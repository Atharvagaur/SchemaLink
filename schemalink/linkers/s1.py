"""S1: BM25 ranking.  Table score = max element score; tables first,
then columns within tables."""

from typing import List

import numpy as np

from schemalink.indexer import DBIndex, SchemaElement


def link_s1(elements: List[SchemaElement], question: str, evidence: str = "",
            db_index: DBIndex = None) -> List[SchemaElement]:
    query = f"{question} {evidence}".strip()
    if db_index is None or db_index.bm25 is None:
        # degenerate: keep order
        for i, e in enumerate(elements):
            e.rank = i
            e.score = 0.0
        return list(elements)

    scores = db_index.bm25_scores(query)

    # Table score = max element score of its columns (and itself)
    table_score: dict = {}
    for i, e in enumerate(elements):
        table_score[e.table] = max(table_score.get(e.table, -np.inf), scores[i])

    # Rank tables first (by table score desc), then columns within tables
    # (by column score desc).  Each element keeps its score.
    tables = [e for e in elements if e.is_table]
    cols = [e for e in elements if not e.is_table]

    tables.sort(key=lambda e: (-table_score.get(e.table, 0.0), e.table))
    # order columns by their table rank, then by column score desc
    table_order = {t.table: i for i, t in enumerate(tables)}
    cols.sort(key=lambda e: (
        table_order.get(e.table, 1e9),
        -(scores[elements.index(e)] if e in elements else 0.0),
        e.column,
    ))

    ranked = tables + cols
    for i, e in enumerate(ranked):
        e.rank = i
        idx = elements.index(e)
        e.score = float(scores[idx])
    return ranked
