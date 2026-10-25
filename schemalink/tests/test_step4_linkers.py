"""Step 4 tests: linkers — RRF identity, completeness, determinism."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemalink.indexer import DBIndex, SchemaElement
from schemalink.linkers import link_s0, link_s1, link_s2, link_s3, link_s6
from schemalink.linkers.s3 import rrf_fuse


def _mk(n_tables=3, cols_per=4):
    els = []
    for t in range(n_tables):
        tn = f"tbl{t}"
        els.append(SchemaElement(table=tn, column="", dtype="TABLE"))
        for c in range(cols_per):
            els.append(SchemaElement(
                table=tn, column=f"col{c}", dtype="text",
                is_pk=(c == 0),
                sample_values=[f"v{t}{c}a", f"v{t}{c}b"],
            ))
    return els


def _mk_index(els):
    idx = DBIndex("test", els)
    # fake dense: use a deterministic hash-based embedding
    d = 16
    rng = np.random.RandomState(42)
    emb = rng.randn(len(els), d).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    idx.dense_embs = emb
    return idx


class TestRRF:
    def test_identical_rankings_fuse_to_same(self):
        els = _mk()
        r1 = link_s0(list(els), "q")
        r2 = link_s0(list(els), "q")
        fused = rrf_fuse([r1, r2])
        assert [e.element_id for e in fused] == [e.element_id for e in r1]

    def test_rrf_counts_all(self):
        els = _mk()
        a = link_s0(list(els), "q")
        b = list(reversed(a))
        fused = rrf_fuse([a, b])
        assert len(fused) == len(els)


class TestCompleteness:
    """Each linker returns every element exactly once."""

    @pytest.mark.parametrize("linker", [link_s0, link_s1, link_s3])
    def test_returns_all_unique(self, linker):
        els = _mk()
        idx = _mk_index(els)
        out = linker(list(els), "find col1 in tbl0", "", idx)
        ids = [e.element_id for e in out]
        assert len(ids) == len(set(ids)), "duplicates found"
        assert set(ids) == {e.element_id for e in els}

    def test_s2_returns_all_unique(self):
        els = _mk()
        idx = _mk_index(els)
        q_emb = np.random.RandomState(0).randn(16).astype(np.float32)
        out = link_s2(list(els), "q", "", idx, q_emb)
        ids = [e.element_id for e in out]
        assert len(ids) == len(set(ids))
        assert set(ids) == {e.element_id for e in els}

    def test_s6_returns_all_unique(self):
        els = _mk()
        idx = _mk_index(els)
        q_emb = np.random.RandomState(0).randn(16).astype(np.float32)
        out, matched = link_s6(list(els), "q", "evidence", idx, q_emb)
        ids = [e.element_id for e in out]
        assert len(ids) == len(set(ids))
        assert set(ids) == {e.element_id for e in els}
        assert isinstance(matched, dict)

    def test_s6_value_hits_reorder(self):
        """A column whose sample value appears in the query should rank first."""
        els = [
            SchemaElement(table="a", column="", dtype="TABLE"),
            SchemaElement(table="a", column="id", dtype="int", is_pk=True),
            SchemaElement(table="a", column="city", dtype="text",
                          sample_values=["Philadelphia", "Boston"]),
            SchemaElement(table="a", column="color", dtype="text",
                          sample_values=["red", "blue"]),
        ]
        idx = DBIndex("t", els)
        q_emb = np.zeros(4, dtype=np.float32)
        idx.dense_embs = None  # force BM25-only path via s3 fallback
        out, matched = link_s6(
            els, "What is the city of Philadelphia?", "", idx, q_emb,
        )
        # city column should be first non-table element
        cols = [e for e in out if not e.is_table]
        assert cols[0].column == "city"
        assert matched.get("a.city", "").lower() in ("philadelphia",)


class TestDeterminism:
    @pytest.mark.parametrize("linker", [link_s0, link_s1, link_s3])
    def test_deterministic(self, linker):
        els = _mk()
        idx = _mk_index(els)
        o1 = [e.element_id for e in linker(list(els), "query", "", idx)]
        o2 = [e.element_id for e in linker(list(els), "query", "", idx)]
        assert o1 == o2
