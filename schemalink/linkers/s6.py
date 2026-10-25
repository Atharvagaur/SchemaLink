"""S6: value-augmented hybrid ranking.

S3 ranking, but columns with value-index hits (quoted literals, Capitalised
tokens, numerals in question/evidence) come first, ordered by match score.
Matched value is annotated in the serialisation by the caller.
"""

import re
from typing import Dict, List, Tuple

from schemalink.indexer import DBIndex, SchemaElement, _extract_query_values
from schemalink.linkers.s3 import link_s3


def value_hits_for_query(db_index: DBIndex, question: str, evidence: str) -> Dict[str, float]:
    """Return {table.column_lower: best_match_score} using rapidfuzz >= 90."""
    text = f"{question} {evidence}".strip()
    return db_index.value_hits(text) if db_index else {}


def link_s6(elements: List[SchemaElement], question: str, evidence: str = "",
            db_index: DBIndex = None, query_emb=None,
            use_value_index: bool = True) -> Tuple[List[SchemaElement], Dict[str, str]]:
    """Returns (ranked_elements, matched_values) where matched_values maps
    "table.column" lower -> matched query value string (for serialisation)."""
    base = link_s3(elements, question, evidence, db_index, query_emb)

    if not use_value_index or db_index is None:
        return base, {}

    hits = value_hits_for_query(db_index, question, evidence)
    if not hits:
        return base, {}

    # matched_values for serialisation annotation
    matched_values: Dict[str, str] = {}
    text = f"{question} {evidence}".strip()
    qvals = _extract_query_values(text)
    for key, score in hits.items():
        # find the query value that produced this hit
        from rapidfuzz import fuzz
        best_qv = ""
        best_s = -1
        for qv in qvals:
            for vl, orig, _ in db_index.value_index.get(key, []):
                s = fuzz.partial_ratio(qv.lower(), vl)
                if s > best_s:
                    best_s = s
                    best_qv = qv
        matched_values[key] = best_qv

    # Partition: columns with hits first (by score desc), then the rest (S3 order)
    hit_cols = []
    rest = []
    for e in base:
        key = f"{e.table}.{e.column}".lower() if not e.is_table else ""
        if key and key in hits:
            hit_cols.append(e)
        else:
            rest.append(e)
    hit_cols.sort(key=lambda e: (
        -hits[f"{e.table}.{e.column}".lower()], e.element_id))

    ranked = hit_cols + rest
    for i, e in enumerate(ranked):
        e.rank = i
    return ranked, matched_values
