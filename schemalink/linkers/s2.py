"""S2: dense cosine ranking of element embeddings vs question+evidence."""

from typing import List

from schemalink.indexer import DBIndex, SchemaElement


def link_s2(elements: List[SchemaElement], question: str, evidence: str = "",
            db_index: DBIndex = None, query_emb=None) -> List[SchemaElement]:
    """Cosine ranking.  query_emb: pre-computed embedding of question+evidence."""
    if db_index is None or db_index.dense_embs is None or query_emb is None:
        for i, e in enumerate(elements):
            e.rank = i
            e.score = 0.0
        return list(elements)

    scores = db_index.dense_scores(query_emb)
    order = sorted(range(len(elements)), key=lambda i: (-scores[i], elements[i].element_id))
    ranked = []
    for pos, i in enumerate(order):
        e = elements[i]
        e.rank = pos
        e.score = float(scores[i])
        ranked.append(e)
    return ranked
