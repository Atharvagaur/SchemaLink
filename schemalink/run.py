"""CLI: python -m schemalink.run --config config.yaml --dataset spider|bird
[--n 150] [--strategies s0,s1,s2,s3,s6,absl] [--smoke]

Budget sweep + ABSL cascade + report generation.
"""

import argparse
import copy
import json
import logging
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from schemalink.budget import count_tokens, enforce, serialise
from schemalink.config import load_config
from schemalink.difficulty import (
    compute_features,
    make_labels,
    predict_tier,
    tier_budgets,
    train_classifier,
)
from schemalink.evaluator import (
    check_ex,
    compute_schema_recall_precision,
    extract_gold_columns,
    get_gold_rows,
)
from schemalink.generator import (
    Generator,
    load_exemplars,
    make_exemplars,
    save_exemplars,
)
from schemalink.indexer import (
    DBIndex,
    extract_elements,
    get_encoder,
)
from schemalink.linkers import link_s0, link_s1, link_s2, link_s3
from schemalink.linkers.absl import run_absl
from schemalink.linkers.s6 import link_s6
from schemalink.loaders import load_dev, load_train
from schemalink.report import generate_report

logger = logging.getLogger("schemalink")

STRATEGIES = ["s0", "s1", "s2", "s3", "s6", "absl"]


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)


# ---------------------------------------------------------------------------
# Per-database index cache
# ---------------------------------------------------------------------------

_index_cache: Dict[str, DBIndex] = {}
_encoder = None
_index_cache_dir = "cache"


def get_db_index(db_id: str, schema_meta: Dict, db_path: str,
                 use_dense: bool = True) -> DBIndex:
    """Build (or load from a persistent disk cache) the per-DB index.

    Cache files are keyed by db_id + hash of schema_meta + whether dense
    embeddings are present, so schema edits or encoder changes rebuild.
    """
    if db_id in _index_cache:
        return _index_cache[db_id]

    import hashlib
    import pickle

    sig = hashlib.sha256(
        json.dumps(schema_meta, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    cdir = Path(_index_cache_dir)
    wanted = "d" if use_dense else "s"
    cfile = cdir / f"index_{db_id}_{sig}_{wanted}.pkl"

    if cfile.exists():
        try:
            idx = pickle.loads(cfile.read_bytes())
            if idx.db_name == db_id and (
                    wanted == "s" or idx.dense_embs is not None):
                logger.debug("index cache hit: %s", cfile)
                _index_cache[db_id] = idx
                return idx
        except Exception as e:
            logger.warning("index cache unreadable (%s): %s", cfile, e)

    els = extract_elements(schema_meta, db_path)
    idx = DBIndex(db_id, els)
    if use_dense:
        global _encoder
        if _encoder is None:
            try:
                _encoder = get_encoder()
            except Exception as e:
                logger.warning("dense encoder unavailable: %s", e)
                _encoder = False
        if _encoder:
            try:
                idx.build_dense(_encoder)
            except Exception as e:
                logger.warning("dense build failed for %s: %s", db_id, e)
    _index_cache[db_id] = idx

    actual = "d" if idx.dense_embs is not None else "s"
    out = cdir / f"index_{db_id}_{sig}_{actual}.pkl"
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pickle.dumps(idx))
        logger.debug("index cache saved: %s", out)
    except Exception as e:
        logger.warning("could not persist index cache: %s", e)
    return idx


def query_embedding(question: str, evidence: str) -> Optional[np.ndarray]:
    if not _encoder:
        return None
    try:
        emb = _encoder([f"{question} {evidence}".strip()])
        return np.asarray(emb[0], dtype=np.float32)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Single question evaluation for a fixed-budget strategy
# ---------------------------------------------------------------------------

def eval_fixed(
    q: Dict[str, Any],
    strategy: str,
    budget: int,
    elements,
    db_index: DBIndex,
    q_emb,
    generator: Generator,
    exemplars,
    gold_rows,
    gold_labels,
    full_schema_text: str,
) -> Dict[str, Any]:
    """Run one (question, strategy, budget) and return a trace record."""
    matched = {}
    if strategy == "s0":
        ranked = link_s0(elements, q["question"], q["evidence"])
        use_budget = float("inf")
    elif strategy == "s1":
        ranked = link_s1(elements, q["question"], q["evidence"], db_index)
        use_budget = budget
    elif strategy == "s2":
        ranked = link_s2(elements, q["question"], q["evidence"], db_index, q_emb)
        use_budget = budget
    elif strategy == "s3":
        ranked = link_s3(elements, q["question"], q["evidence"], db_index, q_emb)
        use_budget = budget
    elif strategy == "s6":
        ranked, matched = link_s6(elements, q["question"], q["evidence"],
                                  db_index, q_emb)
        use_budget = budget
    else:
        raise ValueError(strategy)

    schema_text, schema_tokens, selected, violation = enforce(
        ranked, use_budget, matched_values=matched,
    )

    try:
        res = generator.generate(
            schema=schema_text,
            question=q["question"],
            evidence=q["evidence"],
            temperature=0.0,
            n=1,
            seed=0,
            exemplars=exemplars,
        )
        prompt_tokens = res["prompt_tokens"]
        completion_tokens = res.get("completion_tokens", 0)
        latency = res["latency_s"]
        pred_sql = res["choices"][0]["sql"] if res["choices"] else ""
        error = None
    except Exception as e:
        prompt_tokens = 0
        completion_tokens = 0
        latency = 0.0
        pred_sql = ""
        error = str(e)

    # EX evaluation
    if error is None and gold_rows is not None:
        ok, etype = check_ex(q["db_path"], pred_sql, q["gold_sql"])
        ex = ok
        if not ok:
            error = etype
    else:
        ex = False
        if error is None:
            error = "gold_error"

    # schema recall / precision from selected columns
    sel_cols = {(e.table.lower(), e.column.lower())
                for e in selected if not e.is_table}
    recall, precision = compute_schema_recall_precision(sel_cols, gold_labels)

    full_toks = count_tokens(full_schema_text) if full_schema_text else 1
    compression = (schema_tokens / full_toks) if full_toks else 1.0

    return {
        "qid": q["qid"],
        "dataset": q["dataset"],
        "strategy": strategy,
        "budget": budget if strategy != "s0" else None,
        "tier": None,
        "escalations": [],
        "schema_tokens": schema_tokens,
        "prompt_tokens_total": prompt_tokens,
        "completion_tokens_total": completion_tokens,
        "latency_s": latency,
        "selected_elements": [e.element_id for e in selected],
        "pred_sql": pred_sql,
        "gold_sql": q["gold_sql"],
        "ex": ex,
        "error": error,
        "budget_violation": violation,
        "schema_recall": recall,
        "schema_precision": precision,
        "compression_ratio": compression,
        "question": q["question"],
    }


# ---------------------------------------------------------------------------
# ABSL train-label generation (uses S6 at each tier, train split ONLY)
# ---------------------------------------------------------------------------

def build_absl_classifier(
    train_qs: List[Dict[str, Any]],
    dataset: str,
    generator: Generator,
    exemplars,
    seed: int,
    cap: int = 400,
) -> Tuple[Any, float, List[List[int]]]:
    """Train LightGBM on train split: features -> smallest tier where S6
    gets it right, else top tier.  Returns (model, val_acc, confusion)."""
    tiers = tier_budgets(dataset)
    feats = []
    labels_raw = []
    kept = []

    for i, q in enumerate(train_qs[:cap]):
        schema_meta = q.get("schema")
        if not schema_meta:
            continue
        try:
            idx = get_db_index(q["db_id"], schema_meta, q["db_path"])
        except Exception:
            continue
        q_emb = query_embedding(q["question"], q["evidence"])
        f = compute_features(q["question"], q["evidence"], idx.elements,
                             idx, q_emb)
        feats.append(f)
        kept.append(q)

        gold_rows, gerr = get_gold_rows(q["db_path"], q["gold_sql"])
        correct_at = []
        for B in tiers:
            ranked, matched = link_s6(idx.elements, q["question"],
                                      q["evidence"], idx, q_emb)
            stext, _, _, _ = enforce(ranked, B, matched_values=matched)
            try:
                res = generator.generate(
                    schema=stext, question=q["question"],
                    evidence=q["evidence"],
                    temperature=0.0, n=1, seed=0, exemplars=exemplars,
                )
                sql = res["choices"][0]["sql"] if res["choices"] else ""
                if gold_rows is not None and not gerr:
                    ok, _ = check_ex(q["db_path"], sql, q["gold_sql"])
                else:
                    ok = False
            except Exception:
                ok = False
            correct_at.append(ok)
        labels_raw.append(correct_at)
        if (i + 1) % 25 == 0:
            logger.info("ABSL labels: %d/%d", i + 1, min(cap, len(train_qs)))

    if not feats:
        return None, 0.0, []

    X = np.vstack(feats)
    y = make_labels(kept, labels_raw)
    return train_classifier(X, y, seed=seed)


# ---------------------------------------------------------------------------
# Main experiment
# ---------------------------------------------------------------------------

def run_experiment(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)
    seed = cfg["experiments"]["seed"]
    seed_everything(seed)

    global _index_cache_dir
    _index_cache_dir = cfg["experiments"]["cache_dir"]

    dataset = args.dataset
    budgets = cfg["experiments"]["budgets"][dataset]
    n = args.n
    smoke = args.smoke

    if smoke:
        n = cfg["experiments"].get("smoke_n", 5)
        budgets = budgets[:2]

    strategies = [s.strip().lower() for s in args.strategies.split(",")]
    for s in strategies:
        if s not in STRATEGIES:
            print(f"Unknown strategy: {s}. Choose from {STRATEGIES}")
            return 2

    # -- load data ----------------------------------------------------------
    try:
        dev_qs = load_dev(dataset, cfg, n=n, seed=seed)
    except FileNotFoundError as e:
        print(f"\nDATASET MISSING:\n{e}")
        return 3
    if not dev_qs:
        print("No dev questions loaded (all databases missing?).")
        return 3

    train_qs = []
    try:
        train_qs = load_train(dataset, cfg, n=400, seed=seed)
    except FileNotFoundError:
        logger.warning("train split missing; exemplars/classifier unavailable")

    generator = Generator(cfg, cache_dir=cfg["experiments"]["cache_dir"])

    # -- exemplars (3 fixed, from train only) --------------------------------
    ex_path = Path(cfg["experiments"].get("exemplars_path", "exemplars.json"))
    exemplars = load_exemplars(ex_path)
    if not exemplars and train_qs:
        exemplars = make_exemplars(train_qs, seed=seed, k=3)
        save_exemplars(ex_path, exemplars)
        logger.info("saved %d exemplars to %s", len(exemplars), ex_path)

    # -- ABSL classifier (train only) ---------------------------------------
    ab_clf, ab_acc, ab_cm = None, 0.0, []
    if "absl" in strategies and train_qs and not smoke:
        logger.info("training ABSL tier classifier on train split...")
        ab_clf, ab_acc, ab_cm = build_absl_classifier(
            train_qs, dataset, generator, exemplars, seed,
            cap=400 if not smoke else 10,
        )
        logger.info("ABSL classifier val acc=%.3f cm=%s", ab_acc, ab_cm)

    # -- precompute gold results & labels ------------------------------------
    logger.info("loading %d dev questions, dataset=%s ...", len(dev_qs), dataset)
    for q in dev_qs:
        q["gold_rows"], q["gold_err"] = get_gold_rows(q["db_path"], q["gold_sql"])
        schema_meta = q.get("schema")
        q["elements"] = extract_elements(schema_meta, q["db_path"]) if schema_meta else []
        q["gold_labels"] = extract_gold_columns(q["gold_sql"], schema_meta or {})

    # -- sweep ---------------------------------------------------------------
    traces: List[Dict[str, Any]] = []
    tiers = tier_budgets(dataset)
    idx_map: Dict[str, DBIndex] = {}
    emb_map: Dict[str, Optional[np.ndarray]] = {}

    for q in dev_qs:
        schema_meta = q.get("schema")
        if not schema_meta or not q["elements"]:
            continue
        if q["db_id"] not in idx_map:
            idx_map[q["db_id"]] = get_db_index(
                q["db_id"], schema_meta, q["db_path"])
        emb_map[q["qid"]] = query_embedding(q["question"], q["evidence"])

    # Pre-serialise full schema for compression ratio
    full_texts = {}
    for q in dev_qs:
        key = q["db_id"]
        if key not in full_texts:
            full_texts[key] = serialise(q["elements"])

    for strategy in strategies:
        if strategy == "s0":
            logger.info("== strategy s0 (full schema, budget=inf) ==")
            for qi, q in enumerate(dev_qs):
                if not q["elements"]:
                    continue
                t = eval_fixed(
                    q, "s0", float("inf"), q["elements"],
                    idx_map.get(q["db_id"]), emb_map.get(q["qid"]),
                    generator, exemplars, q.get("gold_rows"),
                    q.get("gold_labels", set()), full_texts.get(q["db_id"], ""),
                )
                traces.append(t)
                if (qi + 1) % 25 == 0:
                    logger.info("  s0 %d/%d", qi + 1, len(dev_qs))
        elif strategy in ("s1", "s2", "s3", "s6"):
            for B in budgets:
                logger.info("== strategy %s budget=%d ==", strategy, B)
                for qi, q in enumerate(dev_qs):
                    if not q["elements"]:
                        continue
                    t = eval_fixed(
                        q, strategy, B, q["elements"],
                        idx_map.get(q["db_id"]), emb_map.get(q["qid"]),
                        generator, exemplars, q.get("gold_rows"),
                        q.get("gold_labels", set()), full_texts.get(q["db_id"], ""),
                    )
                    traces.append(t)
                    if (qi + 1) % 25 == 0:
                        logger.info("  %s/B=%d %d/%d", strategy, B,
                                    qi + 1, len(dev_qs))
        elif strategy == "absl":
            logger.info("== strategy absl (cascade) ==")
            absl_flags = dict(cfg["experiments"].get("absl_flags", {}))
            for qi, q in enumerate(dev_qs):
                if not q["elements"]:
                    continue
                idx = idx_map.get(q["db_id"])
                q_emb = emb_map.get(q["qid"])
                feats = compute_features(q["question"], q["evidence"],
                                         q["elements"], idx, q_emb)
                if ab_clf is not None and absl_flags.get(
                        "use_predictor", True):
                    start = predict_tier(ab_clf, feats, default_tier=0)
                else:
                    start = 0
                result = run_absl(
                    question=q["question"], evidence=q["evidence"],
                    elements=q["elements"], db_index=idx, query_emb=q_emb,
                    db_path=q["db_path"], generator=generator, tiers=tiers,
                    exemplars=exemplars, start_tier=start,
                    flags=absl_flags,
                )

                def _ex_of(sql):
                    if q.get("gold_rows") is not None and not q.get("gold_err"):
                        ok, etype = check_ex(q["db_path"], sql, q["gold_sql"])
                        return ok, (None if ok else etype)
                    return False, "gold_error"

                ex, err = _ex_of(result["sql"])

                sel_cols = {(e.table.lower(), e.column.lower())
                            for e in result["selected_elements"] if not e.is_table}
                recall, precision = compute_schema_recall_precision(
                    sel_cols, q.get("gold_labels", set()))

                base_trace = {
                    "qid": q["qid"], "dataset": dataset,
                    "tier": result["tier"],
                    "escalations": result["escalations"],
                    "prompt_tokens_total": result["prompt_tokens_total"],
                    "completion_tokens_total": result.get(
                        "completion_tokens_total", 0),
                    "latency_s": result["latency_total"],
                    "schema_tokens": result["schema_tokens"],
                    "selected_elements": [e.element_id
                                          for e in result["selected_elements"]],
                    "gold_sql": q["gold_sql"],
                    "budget_violation": result["budget_violation"],
                    "schema_recall": recall,
                    "schema_precision": precision,
                    "start_tier": result["start_tier"],
                    "question": q["question"],
                }
                traces.append({
                    **base_trace, "strategy": "absl", "budget": None,
                    "pred_sql": result["sql"], "ex": ex,
                    "error": err or result.get("error"),
                })

                # ABSL EX under each cap B (max tier allowed).
                # tier_records now holds one entry per tier run (including
                # failed tiers), so picking the largest-budget record with
                # budget <= cap models "what ABSL would return if it could
                # not escalate past this cap".
                for cap in budgets:
                    recs = [r for r in result["tier_records"]
                            if r["budget"] <= cap]
                    if not recs:
                        continue
                    rec = max(recs, key=lambda r: r["budget"])
                    cex, cerr = _ex_of(rec["sql"])
                    traces.append({
                        **base_trace, "strategy": "absl_cap",
                        "budget": cap, "tier": rec["tier"],
                        "schema_tokens": rec["schema_tokens"],
                        "pred_sql": rec["sql"], "ex": cex, "error": cerr,
                    })
                if (qi + 1) % 25 == 0:
                    logger.info("  absl %d/%d", qi + 1, len(dev_qs))

    # -- report ---------------------------------------------------------------
    results_dir = Path(cfg["experiments"]["results_dir"]) / dataset
    gen_cfg = cfg.get("generator", {})
    prices = {
        "input_per_mtok": float(gen_cfg.get("price_input_per_mtok", 0.0) or 0.0),
        "output_per_mtok": float(gen_cfg.get("price_output_per_mtok", 0.0) or 0.0),
    }
    summary = generate_report(traces, results_dir, prices=prices)

    # print first results table
    from schemalink.report import summarise
    rows = summarise(traces, prices=prices)
    print("\n== Results ==")
    hdr = f"{'strat':6} {'B':>6} {'EX':>7} {'CI':>14} {'tok/q':>8} {'BES':>7} {'viol':>5}"
    print(hdr)
    for r in rows:
        ci = f"[{r['ex_ci_lo']},{r['ex_ci_hi']}]"
        print(f"{r['strategy']:6} {str(r['budget']):>6} {r['ex']:>7.3f} "
              f"{ci:>14} {r['avg_prompt_tokens']:>8.0f} {r['bes']:>7.3f} "
              f"{r['budget_violations']:>5}")

    if "absl" in strategies and ab_clf is not None:
        print(f"\nABSL classifier val accuracy: {ab_acc:.3f}")
        print(f"confusion matrix: {ab_cm}")

    print(f"\nOutputs written to {results_dir}/")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="schemalink.run",
        description="Budget-aware schema linking benchmark",
    )
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dataset", choices=["spider", "bird"], required=True)
    p.add_argument("--n", type=int, default=150)
    p.add_argument("--strategies", default="s0,s1,s2,s3,s6,absl")
    p.add_argument("--smoke", action="store_true",
                   help="5 questions, 2 budgets")
    args = p.parse_args(argv)
    try:
        return run_experiment(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
