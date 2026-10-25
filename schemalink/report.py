"""Metrics (EX, AUBC, BES, bootstrap CI), summary CSV, JSONL traces,
Pareto plot, error taxonomy, failures.md."""

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Trace record helpers
# ---------------------------------------------------------------------------

TRACE_FIELDS = [
    "qid", "dataset", "strategy", "budget", "tier", "start_tier",
    "escalations", "schema_tokens", "prompt_tokens_total",
    "completion_tokens_total", "latency_s",
    "selected_elements", "pred_sql", "gold_sql", "ex", "error",
    "budget_violation", "schema_recall", "schema_precision",
    "compression_ratio", "question",
]


def write_traces(path: Path, traces: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for t in traces:
            row = {k: t.get(k) for k in TRACE_FIELDS}
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_traces(path: Path) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------

def execution_accuracy(traces: List[Dict[str, Any]]) -> float:
    if not traces:
        return 0.0
    return sum(1 for t in traces if t.get("ex")) / len(traces)


def bootstrap_ci(traces: List[Dict[str, Any]], n_resamples: int = 1000,
                 seed: int = 0) -> Tuple[float, float]:
    """95% bootstrap CI for EX over questions."""
    if not traces:
        return (0.0, 0.0)
    exs = [1.0 if t.get("ex") else 0.0 for t in traces]
    rng = random.Random(seed)
    n = len(exs)
    stats = []
    for _ in range(n_resamples):
        s = sum(exs[rng.randrange(n)] for _ in range(n)) / n
        stats.append(s)
    stats.sort()
    lo = stats[int(0.025 * n_resamples)]
    hi = stats[min(int(0.975 * n_resamples), n_resamples - 1)]
    return lo, hi


def aubc(points: List[Tuple[int, float]]) -> float:
    """Area under EX vs log2(budget), trapezoid rule, normalised to [0,1].

    points: [(budget, ex), ...] sorted by budget.
    Normalisation: divide by (log2(max_b) - log2(min_b)) and clip EX to [0,1].
    """
    if len(points) < 2:
        return points[0][1] if points else 0.0
    pts = sorted(points, key=lambda p: p[0])
    x = [math.log2(b) for b, _ in pts]
    y = [min(1.0, max(0.0, e)) for _, e in pts]
    area = 0.0
    for i in range(len(x) - 1):
        area += (x[i + 1] - x[i]) * (y[i] + y[i + 1]) / 2
    width = x[-1] - x[0]
    if width <= 0:
        return y[-1]
    return area / width


def bes(ex: float, prompt_tokens_per_query: float) -> float:
    """BES = EX / (prompt tokens per query / 1000)."""
    if prompt_tokens_per_query <= 0:
        return 0.0
    return ex / (prompt_tokens_per_query / 1000.0)


def avg(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def pctl(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------

def cost_per_1k_queries(
    traces: List[Dict[str, Any]],
    prices: Optional[Dict[str, float]] = None,
) -> float:
    """Estimated USD cost per 1,000 queries.

    cost = (sum(prompt_tokens) * input_price + sum(completion_tokens)
            * output_price) / 1e6   [prices are USD per 1M tokens]
    returned per 1,000 queries: total_cost / n_queries * 1000.
    Prices default to 0.0 (metric reported as 0 when unset in config).
    """
    if not traces:
        return 0.0
    prices = prices or {}
    pin = float(prices.get("input_per_mtok", 0.0) or 0.0)
    pout = float(prices.get("output_per_mtok", 0.0) or 0.0)
    sp = sum(t.get("prompt_tokens_total", 0) or 0 for t in traces)
    sc = sum(t.get("completion_tokens_total", 0) or 0 for t in traces)
    total_usd = (sp * pin + sc * pout) / 1_000_000.0
    return total_usd / len(traces) * 1000.0


def summarise(
    traces: List[Dict[str, Any]],
    prices: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Group by (dataset, strategy, budget) and compute metrics."""
    groups: Dict[Tuple, List[Dict]] = defaultdict(list)
    for t in traces:
        key = (t.get("dataset"), t.get("strategy"), t.get("budget"))
        groups[key].append(t)

    rows = []
    for (dataset, strategy, budget), ts in sorted(
        groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2] or 0)
    ):
        ex = execution_accuracy(ts)
        lo, hi = bootstrap_ci(ts)
        ptokens = avg([t.get("prompt_tokens_total", 0) or 0 for t in ts])
        ctokens = avg([t.get("completion_tokens_total", 0) or 0 for t in ts])
        stokens = avg([t.get("schema_tokens", 0) or 0 for t in ts])
        lat = [t.get("latency_s", 0) or 0 for t in ts]
        escal = avg([1.0 if t.get("escalations") else 0.0 for t in ts])
        violations = sum(1 for t in ts if t.get("budget_violation"))
        recs = [t.get("schema_recall") for t in ts if t.get("schema_recall") is not None]
        precs = [t.get("schema_precision") for t in ts if t.get("schema_precision") is not None]
        rows.append({
            "dataset": dataset,
            "strategy": strategy,
            "budget": budget,
            "n": len(ts),
            "ex": round(ex, 4),
            "ex_ci_lo": round(lo, 4),
            "ex_ci_hi": round(hi, 4),
            "avg_schema_tokens": round(stokens, 1),
            "avg_prompt_tokens": round(ptokens, 1),
            "avg_completion_tokens": round(ctokens, 1),
            "cost_per_1k_queries": round(cost_per_1k_queries(ts, prices), 6),
            "bes": round(bes(ex, ptokens), 4),
            "p50_latency_s": round(pctl(lat, 0.5), 3),
            "p95_latency_s": round(pctl(lat, 0.95), 3),
            "escalation_rate": round(escal, 4),
            "budget_violations": violations,
            "avg_schema_recall": round(avg(recs), 4) if recs else "",
            "avg_schema_precision": round(avg(precs), 4) if precs else "",
        })
    return rows


def write_summary_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def compute_aubc_per_strategy(traces: List[Dict[str, Any]]) -> Dict[str, float]:
    """AUBC for fixed-budget strategies S1, S2, S3, S6 only."""
    allowed = {"s1", "s2", "s3", "s6"}
    by_strat: Dict[str, Dict[int, List]] = defaultdict(lambda: defaultdict(list))
    for t in traces:
        s = t.get("strategy", "")
        if s not in allowed or t.get("budget") is None:
            continue
        by_strat[s][t["budget"]].append(t)
    out = {}
    for s, bud_map in by_strat.items():
        pts = []
        for b, ts in sorted(bud_map.items()):
            pts.append((b, execution_accuracy(ts)))
        out[s] = round(aubc(pts), 4)
    return out


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

def error_taxonomy(traces: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """Per strategy counts of failure modes.

    missing_column: schema recall < 1.0 and not ex
    exec_error: pred sql failed to execute and not ex
    empty_result: returned empty and gold non-empty (approx via error field)
    wrong_result_with_full_recall: recall == 1.0 but not ex
    """
    out: Dict[str, Dict[str, int]] = defaultdict(lambda: Counter())
    for t in traces:
        s = t.get("strategy", "?")
        if t.get("ex"):
            out[s]["correct"] += 1
            continue
        err = t.get("error") or ""
        recall = t.get("schema_recall")
        if "exec_error" in err or "timeout" in err:
            out[s]["exec_error"] += 1
        elif "empty_result" in err:
            out[s]["empty_result"] += 1
        elif recall is not None and recall < 1.0:
            out[s]["missing_column"] += 1
        elif recall is not None and recall >= 1.0:
            out[s]["wrong_result_with_full_recall"] += 1
        else:
            out[s]["other"] += 1
    return {k: dict(v) for k, v in out.items()}


def write_failures_md(path: Path, traces: List[Dict[str, Any]],
                      k: int = 30, seed: int = 0) -> None:
    fails = [t for t in traces if not t.get("ex")]
    rng = random.Random(seed)
    if len(fails) > k:
        fails = rng.sample(fails, k)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Failures\n"]
    for t in fails:
        lines.append(f"## {t.get('qid')} [{t.get('strategy')} B={t.get('budget')}]\n")
        lines.append(f"- Q: {t.get('question', '')[:200]}")
        lines.append(f"- gold: `{t.get('gold_sql', '')[:300]}`")
        lines.append(f"- pred: `{t.get('pred_sql', '')[:300]}`")
        lines.append(f"- error: {t.get('error', '')}")
        lines.append(f"- schema_recall: {t.get('schema_recall')}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pareto plot
# ---------------------------------------------------------------------------

def pareto_plot(traces: List[Dict[str, Any]], out_dir: Path) -> List[Path]:
    """One plot per dataset: avg prompt tokens (log x) vs EX."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    by_ds: Dict[str, List[Dict]] = defaultdict(list)
    for t in traces:
        by_ds[t.get("dataset", "?")].append(t)

    paths = []
    for dataset, ts in by_ds.items():
        # aggregate
        groups: Dict[Tuple, List] = defaultdict(list)
        for t in ts:
            groups[(t.get("strategy"), t.get("budget"))].append(t)

        fig, ax = plt.subplots(figsize=(8, 5))
        # fixed-budget strategies: lines
        fixed = defaultdict(list)
        markers_extra = []
        for (s, b), g in groups.items():
            if s is None:
                continue
            ex = execution_accuracy(g)
            pt = avg([t.get("prompt_tokens_total", 0) or 0 for t in g])
            if s in ("s0", "absl"):
                markers_extra.append((s, b, pt, ex))
            elif s != "absl_cap":  # absl_cap reported in summary table only
                fixed[s].append((pt, ex, b))

        for s, pts in sorted(fixed.items()):
            pts.sort()
            ax.plot([p[0] for p in pts], [p[1] for p in pts],
                    marker="o", label=s)
        for s, b, pt, ex in markers_extra:
            mk = "*" if s == "s0" else "D"
            ax.scatter([pt], [ex], marker=mk, s=160,
                       label=f"{s} (B={b})", zorder=5)

        ax.set_xscale("log")
        ax.set_xlabel("Avg prompt tokens per query (log)")
        ax.set_ylabel("Execution accuracy (EX)")
        ax.set_title(f"Pareto: {dataset}")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        out = out_dir / f"pareto_{dataset}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)
        paths.append(out)
    return paths


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------

def generate_report(
    traces: List[Dict[str, Any]],
    results_dir: Path,
    prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Write all outputs; returns summary dict."""
    results_dir.mkdir(parents=True, exist_ok=True)

    write_traces(results_dir / "traces.jsonl", traces)
    rows = summarise(traces, prices=prices)
    write_summary_csv(results_dir / "summary.csv", rows)

    aubcs = compute_aubc_per_strategy(traces)
    tax = error_taxonomy(traces)
    plot_paths = pareto_plot(traces, results_dir)
    write_failures_md(results_dir / "failures.md", traces)

    # ABSL EX under each cap (max tier allowed) is encoded as strategy
    # "absl_cap{B}" if present in traces
    summary = {
        "aubc": aubcs,
        "error_taxonomy": tax,
        "plots": [str(p) for p in plot_paths],
        "n_traces": len(traces),
        "prices_usd_per_mtok": prices or {"input_per_mtok": 0.0,
                                          "output_per_mtok": 0.0},
        "cost_per_1k_queries": round(
            cost_per_1k_queries(traces, prices), 6),
    }
    with open(results_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return summary
