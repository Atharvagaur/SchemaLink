"""S7 / ABSL: adaptive budget cascade.

At tier t: S6 ranking truncated to B_t, sample n SQLs at temperature 0.5,
majority vote by execution-result equivalence.  Checks:
  (a) majority SQL executes without error
  (b) majority returns >= 1 row
  (c) agreement fraction of the 3 candidates matching majority >= 0.67
If all pass -> return majority.  If any fail and t < 3 -> escalate to next
tier, appending the failure reason to the prompt.  At t = 3 return best.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from schemalink.budget import count_tokens, enforce, serialise
from schemalink.evaluator import execute_sql, result_signature
from schemalink.generator import Generator
from schemalink.indexer import DBIndex, SchemaElement
from schemalink.linkers.s6 import link_s6

logger = logging.getLogger(__name__)

DEFAULT_FLAGS = {
    "use_predictor": True,
    "sig_exec": True,
    "sig_nonempty": True,
    "sig_agree": True,
    "use_value_index": True,
    "use_self_consistency": True,   # off => n=1, temperature 0
}


def run_absl(
    question: str,
    evidence: str,
    elements: List[SchemaElement],
    db_index: Optional[DBIndex],
    query_emb,
    db_path: str,
    generator: Generator,
    tiers: List[int],
    exemplars: Optional[List[Dict[str, Any]]] = None,
    start_tier: int = 0,
    flags: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Run the ABSL cascade for one question.

    Returns dict with: sql, tier (final), start_tier, escalations (list),
    prompt_tokens_total, latency_total, schema_tokens, budget_violation,
    selected_elements, error, tier_records.

    tier_records: list of what each tier would have returned if capped there
    (including failed tiers). Each entry contains tier, budget, sql,
    passed flag, and schema_tokens. The sql field captures the first choice
    (or vote sql if exec_ok) from that tier, reflecting the generated SQL
    attempt rather than the theoretical "would-be-returned" SQL. This
    provides visibility into the progression of generation attempts and
    helps diagnose why escalation was needed.
    """
    fl = dict(DEFAULT_FLAGS)
    if flags:
        fl.update(flags)

    use_sc = fl["use_self_consistency"]
    n_samples = 3 if use_sc else 1
    temperature = 0.5 if use_sc else 0.0

    escalations: List[str] = []
    total_tokens = 0
    total_comp_tokens = 0
    total_latency = 0.0
    best: Optional[Tuple[str, Any]] = None  # (sql, rows)
    best_tier = start_tier
    final_sql = ""
    final_schema_text = ""
    final_schema_tokens = 0
    final_violation = False
    final_selected: List[SchemaElement] = []
    error: Optional[str] = None
    extra_note = ""
    # per-tier record: what would be returned if capped at this tier
    # Contains tier, budget, sql, passed flag, and schema_tokens.
    # The sql field captures the first choice (or vote sql if exec_ok)
    # from that tier, reflecting the generated SQL attempt rather than
    # the theoretical "would-be-returned" SQL. This provides visibility
    # into the progression of generation attempts and helps diagnose
    # why escalation was needed.
    tier_records: List[Dict[str, Any]] = []

    for t in range(start_tier, len(tiers)):
        budget = tiers[t]
        ranked, matched = link_s6(
            elements, question, evidence, db_index, query_emb,
            use_value_index=fl["use_value_index"],
        )
        schema_text, schema_tokens, selected, violation = enforce(
            ranked, budget, matched_values=matched,
        )
        final_schema_text = schema_text
        final_schema_tokens = schema_tokens
        final_violation = violation
        final_selected = selected
        best_tier = t

        # one generation call producing n_samples choices
        try:
            res = generator.generate(
                schema=schema_text,
                question=question,
                evidence=evidence,
                temperature=temperature,
                n=n_samples,
                seed=42,
                exemplars=exemplars,
                extra_note=extra_note,
            )
        except Exception as e:
            error = str(e)
            logger.warning("ABSL generation failed at tier %d: %s", t, e)
            break

        total_tokens += res["prompt_tokens"]
        total_comp_tokens += res.get("completion_tokens", 0)
        total_latency += res["latency_s"]

        choices = res["choices"]
        # Execute all candidates
        exec_results = []
        for ch in choices:
            ok, rows, err, lat = execute_sql(db_path, ch["sql"], timeout_s=30)
            exec_results.append((ok, rows, err, ch["sql"]))

        # Majority vote by execution-result equivalence
        vote = _majority_vote(exec_results)

        # Determine pass/fail
        reasons = []
        if fl["sig_exec"] and not vote["exec_ok"]:
            reasons.append("majority SQL did not execute without error")
        if fl["sig_nonempty"] and vote["n_rows"] is not None and vote["n_rows"] < 1:
            reasons.append("majority returned 0 rows")
        if fl["sig_agree"] and vote["agreement"] < 0.67:
            reasons.append(f"agreement {vote['agreement']:.2f} < 0.67")

        if not reasons:
            final_sql = vote["sql"]
            tier_records.append({
                "tier": t + 1, "budget": budget, "sql": vote["sql"],
                "passed": True, "schema_tokens": schema_tokens,
            })
            break  # accepted
            # what this tier would have returned if capped here
            # cap_sql is the best SQL that could be returned from this tier:
            #   - vote["sql"] if it passed execution (exec_ok=True)
            #   - best[0] if any tier executed successfully and is remembered
            #   - choices[0]["sql"] as fallback
            # This captures the generated SQL attempt from this tier, not the
            # theoretical "would-be-returned" SQL, providing visibility into
            # what would happen if this tier were the final tier.
            cap_sql = vote["sql"] if vote["exec_ok"] and vote["sql"] else (
                best[0] if best else (choices[0]["sql"] if choices else "")
            )
            tier_records.append({
                "tier": t + 1, "budget": budget, "sql": cap_sql,
                "passed": False, "schema_tokens": schema_tokens,
            })

        # escalation
        if t < len(tiers) - 1:
            escalations.append(
                f"tier {t+1} -> {t+2}: " + "; ".join(reasons)
            )
            extra_note = (
                "Note: a previous attempt failed because "
                + "; ".join(reasons)
                + ". Try a more careful query."
            )
            # remember best-so-far (first that executed)
            if best is None and vote["exec_ok"] and vote["rows"] is not None:
                best = (vote["sql"], vote["rows"])
        else:
            # top tier: return best available
            final_sql = vote["sql"] if vote["sql"] else (
                best[0] if best else (choices[0]["sql"] if choices else "")
            )
            if not vote["exec_ok"] and best:
                final_sql = best[0]
            if not final_sql and choices:
                final_sql = choices[0]["sql"]

    if not final_sql and final_schema_text is not None:
        # generation never ran (e.g. start_tier beyond range)
        final_sql = ""

    return {
        "sql": final_sql,
        "tier": best_tier + 1,          # 1-indexed for reporting
        "start_tier": start_tier + 1,
        "escalations": escalations,
        "prompt_tokens_total": total_tokens,
        "completion_tokens_total": total_comp_tokens,
        "latency_total": total_latency,
        "schema_tokens": final_schema_tokens,
        "budget_violation": final_violation,
        "selected_elements": final_selected,
        "schema_text": final_schema_text,
        "tier_records": tier_records,
        "error": error,
    }


def _majority_vote(exec_results: List[Tuple[bool, Any, Optional[str], str]]) -> Dict[str, Any]:
    """Group by result signature; return majority (sql, rows, n_rows,
    exec_ok, agreement)."""
    from collections import Counter

    # count only candidates that executed successfully for signatures
    sigs = []
    meta = {}
    for ok, rows, err, sql in exec_results:
        if ok:
            sig = result_signature(rows, ordered=False)
            sigs.append(sig)
            if sig not in meta:
                meta[sig] = (sql, rows)
    if not sigs:
        # none executed
        return {
            "sql": exec_results[0][3] if exec_results else "",
            "rows": None,
            "n_rows": None,
            "exec_ok": False,
            "agreement": 0.0,
        }

    counter = Counter(sigs)
    best_sig, best_count = counter.most_common(1)[0]
    sql, rows = meta[best_sig]
    agreement = best_count / len(exec_results)
    n_rows = len(rows) if rows is not None else None
    exec_ok = True
    return {
        "sql": sql, "rows": rows, "n_rows": n_rows,
        "exec_ok": exec_ok, "agreement": agreement,
    }
