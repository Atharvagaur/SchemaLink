"""S0: full schema, no truncation (budget = inf)."""

from typing import List

from schemalink.indexer import SchemaElement


def link_s0(elements: List[SchemaElement], question: str, evidence: str = "",
            db_index=None) -> List[SchemaElement]:
    """Return every element in original order; enforcer uses budget=inf."""
    out = []
    for i, e in enumerate(elements):
        e.rank = i
        e.score = 1.0
        out.append(e)
    return out
