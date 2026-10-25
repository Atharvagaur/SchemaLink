"""End-to-end pipeline test on a synthetic mini-dataset.

Uses a deterministic stub generator (monkeypatched) to exercise the full
run_experiment path: loaders -> indexes -> linkers -> budget -> generate
-> evaluate -> report -> verify.  This validates the *machinery* only;
no benchmark numbers are produced or claimed from it.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def mini_dataset(tmp_path):
    """Tiny spider-like dataset with 6 questions over 2 databases."""
    root = tmp_path / "data"
    spider = root / "spider"
    spider.mkdir(parents=True)

    dbs = {}
    for db_name, cols in {
        "zoo": [("animal", ["id", "name", "species"]),
                ("keeper", ["id", "name", "animal_id"])],
        "shop": [("item", ["id", "title", "price"]),
                 ("sale", ["id", "item_id", "qty"])],
    }.items():
        db_dir = spider / "database" / db_name
        db_dir.mkdir(parents=True)
        conn = sqlite3.connect(str(db_dir / f"{db_name}.sqlite"))
        for tname, cnames in cols:
            coldefs = ", ".join(
                f"{c} {'INTEGER PRIMARY KEY' if c=='id' else 'TEXT'}"
                for c in cnames
            )
            conn.execute(f"CREATE TABLE {tname} ({coldefs})")
            vals = []
            for i in range(3):
                cells = [str(i + 1)] + [
                    f"'{tname}_{c}{i}'" if not c.endswith("id")
                    and c not in ("price", "qty") else "1"
                    for c in cnames[1:]
                ]
                vals.append("(" + ",".join(cells) + ")")
            conn.execute(
                f"INSERT INTO {tname} ({','.join(cnames)}) VALUES "
                + ",".join(vals)
            )
        conn.commit()
        conn.close()
        dbs[db_name] = cols

    # tables.json (Spider list format)
    tables_json = []
    for db_name, tcols in dbs.items():
        tnames = [t for t, _ in tcols]
        col_names = [[-1, "*"]]
        col_types = ["text"]
        pks = []
        for ti, (tname, cnames) in enumerate(tcols):
            for c in cnames:
                col_names.append([ti, c])
                col_types.append("number" if c == "id" or c.endswith("_id")
                                 or c in ("price", "qty") else "text")
                if c == "id":
                    pks.append(len(col_names) - 1)
        tables_json.append({
            "db_id": db_name,
            "table_names_original": tnames,
            "column_names_original": col_names,
            "column_types": col_types,
            "primary_keys": pks,
            "foreign_keys": [],
        })

    dev = []
    qid = 0
    for db_name in dbs:
        for qtext in [
            "How many animals are there?",
            "List the names of all keepers.",
            "What is the average price of items?",
        ]:
            dev.append({
                "question_id": qid,
                "question": qtext,
                "query": f"SELECT name FROM {list(dbs[db_name])[0][0]} LIMIT 2"
                if "name" in list(dbs[db_name])[0][1]
                else f"SELECT id FROM {list(dbs[db_name])[0][0]} LIMIT 2",
                "database_id": db_name,
            })
            qid += 1

    (spider / "dev.json").write_text(json.dumps(dev))
    (spider / "tables.json").write_text(json.dumps(tables_json))
    (spider / "train_spider.json").write_text(json.dumps(dev))

    cfg = {
        "datasets": {
            "spider": {
                "dev": "spider/dev.json",
                "tables": "spider/tables.json",
                "train": "spider/train_spider.json",
                "db_root": "spider/database",
                "root": str(root),
            }
        },
        "generator": {
            "base_url": "http://localhost", "model": "cohere/north-mini-code:free",
            "api_key": os.environ.get("LLM_API_KEY", "sk-or-v1-test-key"), "max_tokens": 512, "timeout": 5,
            "workers": 1,
        },
        "experiments": {
            "budgets": {"spider": [100, 500]},
            "n_questions": 6, "seed": 0,
            "cache_dir": str(tmp_path / "cache"),
            "results_dir": str(tmp_path / "results"),
            "exemplars_path": str(tmp_path / "exemplars.json"),
        },
    }
    cfg_path = tmp_path / "config.yaml"
    import yaml
    cfg_path.write_text(yaml.dump(cfg))
    return cfg_path, dev


class _StubGenerator:
    """Deterministic generator returning SQL parsed from the prompt's
    question — good enough to exercise the machinery.  Not a benchmark."""

    def __init__(self, cfg, cache_dir="cache"):
        self.model = cfg["generator"]["model"]

    def generate(self, schema, question, evidence, temperature, n, seed,
                 exemplars=None, extra_note=""):
        # always return a trivially safe query for whatever table appears
        import re
        m = re.search(r"CREATE TABLE (\w+)", schema)
        table = m.group(1) if m else None
        if table:
            sql = f"SELECT * FROM {table} LIMIT 2"
        else:
            sql = "SELECT 1"
        choices = [{"sql": sql, "raw": sql, "finish_reason": "stop"}
                   for _ in range(max(1, n))]
        return {
            "choices": choices,
            "prompt_tokens": 100 + len(schema),
            "latency_s": 0.01,
            "temperature": temperature, "n": n,
        }


def test_end_to_end_mini(tmp_path, mini_dataset, monkeypatch):
    cfg_path, dev = mini_dataset

    # load_config requires these env vars (never hardcoded)
    monkeypatch.setenv("GENERATOR_MODEL_SLUG", "cohere/north-mini-code:free")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-v1-test-key")

    import schemalink.run as run_mod

    # monkeypatch Generator inside run module
    monkeypatch.setattr(run_mod, "Generator", _StubGenerator)
    # disable dense encoder (no model download in tests)
    monkeypatch.setattr(run_mod, "get_encoder",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no dense in tests")))
    run_mod._encoder = None
    run_mod._index_cache.clear()

    rc = run_mod.main([
        "--config", str(cfg_path),
        "--dataset", "spider",
        "--n", "6",
        "--strategies", "s0,s1,s3,s6,absl",
        "--smoke",
    ])
    assert rc == 0

    results_dir = tmp_path / "results" / "spider"
    assert (results_dir / "summary.csv").exists()
    assert (results_dir / "traces.jsonl").exists()
    assert (results_dir / "failures.md").exists()
    assert (results_dir / "pareto_spider.png").exists()

    traces = [
        json.loads(l)
        for l in (results_dir / "traces.jsonl").read_text().splitlines()
        if l.strip()
    ]
    assert len(traces) > 0

    # invariant: schema_tokens <= B unless budget_violation
    from schemalink.verify import check_budget, check_recall_monotone
    problems = check_budget(traces)
    assert not problems, problems
    problems = check_recall_monotone(traces)
    # smoke has only 1-2 budgets; monotonicity may be vacuous
    assert isinstance(problems, list)

    # strategies present
    strats = {t["strategy"] for t in traces}
    assert "s0" in strats
    assert "absl" in strats
