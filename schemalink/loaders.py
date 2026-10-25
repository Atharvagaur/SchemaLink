"""Dataset loaders for Spider and BIRD."""

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _check_file(path: Path, what: str, download_hint: str) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {what}: {path}\n  Download: {download_hint}"
        )


def _load_json(path: Path, what: str, hint: str) -> Any:
    _check_file(path, what, hint)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_SPIDER_HINT = (
    "Spider: https://yale-lily.github.io/spider  "
    "(dev.json, tables.json, train_spider.json, database/)"
)
_BIRD_HINT = (
    "BIRD: https://bird-benchmark.github.io/  "
    "(dev.json, dev_tables.json, dev_databases/, train/)"
)


def check_dataset(dataset: str, cfg: Dict[str, Any]) -> Path:
    """Verify dev-side files exist; raise with download instructions.
    The train split is checked separately in load_train (dev must load
    without it)."""
    ds = cfg["datasets"][dataset]
    root = Path(ds.get("root", "."))
    if dataset == "spider":
        _check_file(root / ds["dev"], "Spider dev.json", _SPIDER_HINT)
        _check_file(root / ds["tables"], "Spider tables.json", _SPIDER_HINT)
        db_root = root / ds["db_root"]
        if not db_root.exists():
            raise FileNotFoundError(
                f"Missing Spider database dir: {db_root}\n  Download: {_SPIDER_HINT}"
            )
    else:
        _check_file(root / ds["dev"], "BIRD dev.json", _BIRD_HINT)
        _check_file(root / ds["tables"], "BIRD dev_tables.json", _BIRD_HINT)
        db_root = root / ds["db_root"]
        if not db_root.exists():
            raise FileNotFoundError(
                f"Missing BIRD dev_databases dir: {db_root}\n  Download: {_BIRD_HINT}"
            )
    return root


def _db_path(root: Path, ds: Dict[str, Any], db_id: str) -> Optional[Path]:
    """Return sqlite path for db_id or None."""
    db_root = root / ds["db_root"]
    # Spider: database/<db>/<db>.sqlite ; BIRD: dev_databases/<db>/<db>.sqlite
    p = db_root / db_id / f"{db_id}.sqlite"
    return p if p.exists() else None


def _table_lookup(tables_json: Any) -> Dict[str, Any]:
    """Normalise tables json into {db_id: schema_dict}."""
    if isinstance(tables_json, dict):
        return tables_json
    # Spider tables.json is a list of schema entries
    out = {}
    for entry in tables_json:
        db_id = entry.get("db_id", "")
        out[db_id] = entry
    return out


def load_dev(
    dataset: str,
    cfg: Dict[str, Any],
    n: int = 150,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """Load n random dev questions (seeded). Skips questions whose db file is missing."""
    ds = cfg["datasets"][dataset]
    root = check_dataset(dataset, cfg)
    dev = _load_json(root / ds["dev"], f"{dataset} dev", _SPIDER_HINT if dataset == "spider" else _BIRD_HINT)
    tables_raw = _load_json(root / ds["tables"], f"{dataset} tables",
                            _SPIDER_HINT if dataset == "spider" else _BIRD_HINT)
    tables = _table_lookup(tables_raw)

    rng = random.Random(seed)
    idx = list(range(len(dev)))
    rng.shuffle(idx)
    idx = idx[:n] if n and n < len(idx) else idx

    questions = []
    for i in idx:
        item = dev[i]
        db_id = item.get("database_id", item.get("db_id", ""))
        db_path = _db_path(root, ds, db_id)
        if db_path is None:
            continue
        qid = str(item.get("question_id", i))
        questions.append({
            "qid": f"{dataset}_{qid}",
            "question": item.get("question", ""),
            "evidence": item.get("evidence", ""),
            "gold_sql": item.get("query", ""),
            "db_id": db_id,
            "db_path": str(db_path),
            "dataset": dataset,
            "schema": tables.get(db_id),
        })
    return questions


def load_train(
    dataset: str,
    cfg: Dict[str, Any],
    n: int = 400,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """Load train split (for few-shot exemplars and tier labels only)."""
    ds = cfg["datasets"][dataset]
    root = check_dataset(dataset, cfg)
    train_path = root / ds["train"]
    if dataset == "spider":
        train = _load_json(train_path, "Spider train", _SPIDER_HINT)
    else:
        # BIRD train may be json or jsonl
        try:
            train = _load_json(train_path, "BIRD train", _BIRD_HINT)
        except json.JSONDecodeError:
            train = []
            with open(train_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        train.append(json.loads(line))
    if not isinstance(train, list):
        train = [train]

    rng = random.Random(seed)
    idx = list(range(len(train)))
    rng.shuffle(idx)
    idx = idx[:n]

    tables_raw = _load_json(root / ds["tables"], "tables",
                            _SPIDER_HINT if dataset == "spider" else _BIRD_HINT)
    tables = _table_lookup(tables_raw)

    out = []
    for i in idx:
        item = train[i]
        db_id = item.get("database_id", item.get("db_id", ""))
        db_path = _db_path(root, ds, db_id)
        if db_path is None:
            continue
        out.append({
            "qid": f"{dataset}_train_{i}",
            "question": item.get("question", ""),
            "evidence": item.get("evidence", ""),
            "gold_sql": item.get("query", item.get("sql", "")),
            "db_id": db_id,
            "db_path": str(db_path),
            "dataset": dataset,
            "schema": tables.get(db_id),
        })
    return out
