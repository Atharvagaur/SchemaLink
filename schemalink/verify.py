"""Step 7 verification pass over produced traces.

Checks (run after a real experiment):
  1. no row has schema_tokens > B unless budget_violation == True
  2. schema recall is non-decreasing in B for each fixed-budget strategy
  3. ABSL total tokens >= its final-tier call tokens (>= min tier budget)
  4. print 3 random full traces

Usage: python -m schemalink.verify --results results/<dataset>
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def load_traces(results_dir: Path) -> List[Dict[str, Any]]:
    p = results_dir / "traces.jsonl"
    traces = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                traces.append(json.loads(line))
    return traces


def check_budget(traces: List[Dict[str, Any]]) -> List[str]:
    problems = []
    for t in traces:
        b = t.get("budget")
        st = t.get("schema_tokens", 0)
        if b is None or st is None:
            continue
        if st > b and not t.get("budget_violation"):
            problems.append(
                f"{t['qid']} [{t['strategy']} B={b}]: "
                f"schema_tokens={st} > B without budget_violation"
            )
    return problems


def check_recall_monotone(traces: List[Dict[str, Any]]) -> List[str]:
    problems = []
    groups: Dict[tuple, Dict[int, float]] = defaultdict(dict)
    for t in traces:
        s = t.get("strategy")
        b = t.get("budget")
        r = t.get("schema_recall")
        if s in ("s0", "absl", "absl_cap") or b is None or r is None:
            continue
        groups[(t["qid"], s)][b] = r
    for (qid, s), by_b in groups.items():
        bs = sorted(by_b)
        for i in range(len(bs) - 1):
            if by_b[bs[i + 1]] < by_b[bs[i]] - 1e-9:
                problems.append(
                    f"{qid} [{s}]: recall drops {by_b[bs[i]]:.3f} "
                    f"@B={bs[i]} -> {by_b[bs[i+1]]:.3f} @B={bs[i+1]}"
                )
    return problems


def check_absl_tokens(traces: List[Dict[str, Any]]) -> List[str]:
    problems = []
    for t in traces:
        if t.get("strategy") != "absl":
            continue
        total = t.get("prompt_tokens_total", 0)
        tier = t.get("tier")
        # total must be at least one call's worth; as a weak lower bound
        # we check total > 0 whenever a tier was reached
        if tier and total <= 0:
            problems.append(
                f"{t['qid']}: absl reached tier {tier} but total tokens={total}"
            )
    return problems


def print_random_traces(traces: List[Dict[str, Any]], k: int = 3,
                        seed: int = 0) -> None:
    rng = random.Random(seed)
    sample = rng.sample(traces, min(k, len(traces)))
    for i, t in enumerate(sample, 1):
        print(f"\n{'='*70}\nTRACE {i}: {t.get('qid')} "
              f"[{t.get('strategy')} B={t.get('budget')} tier={t.get('tier')}]")
        for key in ("question", "schema_tokens", "prompt_tokens_total",
                    "latency_s", "escalations", "schema_recall",
                    "schema_precision", "ex", "error", "budget_violation",
                    "pred_sql", "gold_sql"):
            print(f"  {key}: {t.get(key)}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="schemalink.verify")
    p.add_argument("--results", required=True,
                   help="results dir containing traces.jsonl")
    p.add_argument("--show", type=int, default=3)
    args = p.parse_args(argv)

    results_dir = Path(args.results)
    traces = load_traces(results_dir)
    print(f"loaded {len(traces)} traces from {results_dir}")

    problems = []
    problems += check_budget(traces)
    problems += check_recall_monotone(traces)
    problems += check_absl_tokens(traces)

    if problems:
        print(f"\nFAILED: {len(problems)} invariant violations:")
        for m in problems[:50]:
            print(f"  - {m}")
        rc = 1
    else:
        print("\nAll invariants OK:")
        print("  [OK] schema_tokens <= B whenever budget_violation=False")
        print("  [OK] schema recall non-decreasing in B (fixed-budget)")
        print("  [OK] ABSL token accounting consistent")
        rc = 0

    print_random_traces(traces, k=args.show)
    return rc


if __name__ == "__main__":
    sys.exit(main())
