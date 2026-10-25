"""S3: Hybrid RRF of S1 (BM25) and S2 (dense).

score(c) = sum over rankings of 1/(60 + rank), ranks start at 1.
"""

from typing import List

from schemalink.indexer import DBIndex, SchemaElement
from schemalink.linkers.s1 import link_s1
from schemalink.linkers.s2 import link_s2

RRF_K = 60


def rrf_fuse(rankings: List[List[SchemaElement]], k: int = RRF_K) -> List[SchemaElement]:
    """Reciprocal-rank fusion of multiple rankings of the same elements.

    Identical rankings fuse to the same ordering.
    """
    scores: dict = {}
    seen: dict = {}
    for ranking in rankings:
        for pos, e in enumerate(ranking, start=1):
            eid = e.element_id
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (k + pos)
            if eid not in seen:
                seen[eid] = e

    fused = sorted(seen.values(),
                   key=lambda e: (-scores[e.element_id], e.element_id))
    for i, e in enumerate(fused):
        e.rank = i
        e.score = float(scores[e.element_id])
    return fused


def link_s3(elements: List[SchemaElement], question: str, evidence: str = "",
            db_index: DBIndex = None, query_emb=None) -> List[SchemaElement]:
    r1 = link_s1(elements, question, evidence, db_index)
    r2 = link_s2(elements, question, evidence, db_index, query_emb)
    return rrf_fuse([r1, r2])
