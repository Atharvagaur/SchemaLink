"""Budget enforcer: serialise a ranked element subset under a token budget B.

Budget applies to the **serialised schema text only** — not to instructions
or the question.  Token counting uses tiktoken ``o200k_base`` as a fixed
proxy tokenizer for all models.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

import tiktoken

from schemalink.indexer import SchemaElement

logger = logging.getLogger(__name__)

_ENC = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def serialise(
    elements: List[SchemaElement],
    matched_values: Optional[Dict[str, str]] = None,
) -> str:
    """Serialise selected elements as CREATE TABLE-style text.

    - types and PK marker
    - FK lines only when both ends are included
    - ``-- e.g. v1, v2`` sample values
    - ``-- matched value: 'X'`` annotation (S6)
    """
    if matched_values is None:
        matched_values = {}

    selected: Dict[str, List[SchemaElement]] = {}
    order: List[str] = []
    selected_ids: Set[str] = set()
    for e in elements:
        if e.table not in selected:
            selected[e.table] = []
            order.append(e.table)
        selected[e.table].append(e)
        selected_ids.add(e.element_id)

    parts: List[str] = []
    for tname in order:
        cols = selected[tname]
        parts.append(f"CREATE TABLE {tname} (")
        col_lines = []
        for c in cols:
            if c.is_table:
                continue
            line = c.serialise_line()
            # matched-value annotation
            mv = matched_values.get(f"{tname}.{c.column}".lower())
            if mv and "-- matched value" not in line:
                line += f"  -- matched value: '{mv}'"
            col_lines.append(line)
            sc = c.sample_comment()
            if sc:
                col_lines.append(sc)

        # FK lines: only when both ends included
        for c in cols:
            if c.is_table or not c.fk_target:
                continue
            tgt = c.fk_target
            # both ends: our column is included (it is, we're iterating them)
            # and target table+column included
            if "." in tgt:
                tt, tc = tgt.split(".", 1)
            else:
                tt, tc = tgt, None
            tgt_included = tt in selected
            if tc:
                tgt_col_included = any(
                    not x.is_table and x.column == tc
                    for x in selected.get(tt, [])
                )
                tgt_included = tgt_included and tgt_col_included
            if tgt_included:
                if tc:
                    col_lines.append(
                        f"    FOREIGN KEY ({c.column}) REFERENCES {tt}({tc})"
                    )
                else:
                    col_lines.append(
                        f"    FOREIGN KEY ({c.column}) REFERENCES {tt}"
                    )
        parts.extend(col_lines)
        parts.append(")")

    return "\n".join(parts)


def enforce(
    ranked: List[SchemaElement],
    budget: int,
    matched_values: Optional[Dict[str, str]] = None,
) -> Tuple[str, int, List[SchemaElement], bool]:
    """Add ranked elements in order, stopping when the next addition would
    exceed ``budget`` tokens (of the serialised subset).

    Always keep PK/FK columns of retained tables.  If the mandatory set alone
    exceeds ``budget``, keep only the top-ranked tables and return
    ``budget_violation=True``.

    Returns (schema_text, token_count, selected_elements, budget_violation).
    """
    if budget == float("inf") or budget is None:
        text = serialise(ranked, matched_values)
        return text, count_tokens(text), list(ranked), False

    # group by table, preserving rank order of first appearance
    table_order: List[str] = []
    table_elems: Dict[str, List[SchemaElement]] = {}
    for e in ranked:
        if e.table not in table_elems:
            table_elems[e.table] = []
            table_order.append(e.table)
        table_elems[e.table].append(e)

    # Mandatory set: PK and FK columns of each table (table record itself too)
    def _mandatory(tname: str) -> List[SchemaElement]:
        out = []
        for e in table_elems[tname]:
            if e.is_table or e.is_pk or e.fk_target:
                out.append(e)
        return out

    # Phase 1: add tables in rank order, including mandatory cols.
    # Stop when adding next table (with its mandatory cols) would exceed B.
    selected: List[SchemaElement] = []
    violation = False

    for tname in table_order:
        cand = list(selected) + table_elems[tname]
        # only mandatory for this trial?  No: try full table first?  Spec says
        # "Add ranked columns in order, counting tokens of the serialised
        # subset, and stop when the next addition would exceed B."
        # So we grow element-by-element, but PK/FK of *retained* tables are
        # always kept.
        trial_elems = _grow_with_mandatory(selected, table_elems, table_order,
                                           tname, matched_values)
        text = serialise(trial_elems, matched_values)
        toks = count_tokens(text)
        if toks <= budget:
            selected = trial_elems
        else:
            # Cannot add this table at all — but maybe we can add its
            # mandatory columns only?  Try that.
            mandatory_only = list(selected)
            added_mandatory = False
            for e in table_elems[tname]:
                if e.is_table or e.is_pk or e.fk_target:
                    trial2 = mandatory_only + [e]
                    # ensure mandatory cols of previously retained tables stay
                    trial2 = _ensure_mandatory(trial2, table_elems, matched_values)
                    t2 = serialise(trial2, matched_values)
                    if count_tokens(t2) <= budget:
                        mandatory_only = trial2
                        added_mandatory = True
                    else:
                        break
            if added_mandatory:
                selected = _ensure_mandatory(
                    mandatory_only, table_elems, matched_values)
                # Now add remaining ranked columns of this table one by one
                for e in table_elems[tname]:
                    if e in selected:
                        continue
                    trial3 = selected + [e]
                    t3 = serialise(trial3, matched_values)
                    if count_tokens(t3) <= budget:
                        selected = trial3
                    else:
                        break
            else:
                # Cannot add this table's mandatory columns either
                # Continue to try next table (don't break yet)
                pass

    # If nothing fits at all: keep only top-ranked table's mandatory cols
    if not selected and table_order:
        top = table_order[0]
        mandatory = [e for e in table_elems[top]
                     if e.is_table or e.is_pk or e.fk_target]
        text = serialise(mandatory, matched_values)
        if count_tokens(text) > budget:
            violation = True
        selected = mandatory

    # Ensure violation is True if no elements selected at all
    if not selected:
        violation = True

    final_text = serialise(selected, matched_values)
    final_toks = count_tokens(final_text)

    # Safety: if over budget even now, trim non-mandatory columns
    if final_toks > budget:
        violation = True
        trimmed = [e for e in selected if e.is_table or e.is_pk or e.fk_target]
        # if even that is over, keep only the single top table's PK
        t2 = serialise(trimmed, matched_values)
        if count_tokens(t2) > budget and table_order:
            top = table_order[0]
            trimmed = [e for e in table_elems[top]
                       if e.is_table or e.is_pk]
            t2 = serialise(trimmed, matched_values)
        selected = trimmed
        final_text = t2
        final_toks = count_tokens(final_text)

    return final_text, final_toks, selected, violation


def _grow_with_mandatory(
    current: List[SchemaElement],
    table_elems: Dict[str, List[SchemaElement]],
    table_order: List[str],
    new_table: str,
    matched_values: Optional[Dict[str, str]],
) -> List[SchemaElement]:
    """current elements + all elements of new_table, with mandatory cols of
    all previously retained tables ensured."""
    out = list(current)
    for e in table_elems[new_table]:
        if e not in out:
            out.append(e)
    return _ensure_mandatory(out, table_elems, matched_values)


def _ensure_mandatory(
    current: List[SchemaElement],
    table_elems: Dict[str, List[SchemaElement]],
    matched_values: Optional[Dict[str, str]],
) -> List[SchemaElement]:
    """For every table present in ``current``, make sure PK/FK cols are there."""
    present_tables = {e.table for e in current}
    out = list(current)
    seen = {e.element_id for e in out}
    for t in present_tables:
        for e in table_elems.get(t, []):
            if (e.is_table or e.is_pk or e.fk_target) and e.element_id not in seen:
                out.append(e)
                seen.add(e.element_id)
    return out
