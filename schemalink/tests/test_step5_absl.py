"""Step 5 tests: ABSL cascade with mocked generator/executor."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemalink.indexer import DBIndex, SchemaElement
from schemalink.linkers.absl import run_absl
from schemalink.difficulty import (
    compute_features,
    make_labels,
    predict_tier,
    tier_budgets,
)


def _mk_elements():
    """Match the conftest tmp_db (singer/concert)."""
    return [
        SchemaElement(table="singer", column="", dtype="TABLE"),
        SchemaElement(table="singer", column="singer_id", dtype="integer",
                      is_pk=True),
        SchemaElement(table="singer", column="name", dtype="text",
                      sample_values=["Alice", "Bob"]),
        SchemaElement(table="singer", column="age", dtype="integer"),
    ]


class _MockGeneratorAlwaysBad:
    """Returns n choices of broken SQL; records prompt tokens per call."""

    def __init__(self):
        self.calls = []
        self.tokens_per_call = []

    def generate(self, schema, question, evidence, temperature, n, seed,
                 exemplars=None, extra_note=""):
        # token count proportional to schema+prompt so tiers differ
        toks = 100 + len(schema) + len(extra_note)
        self.calls.append({"n": n, "temp": temperature, "extra": extra_note})
        self.tokens_per_call.append(toks)
        choices = [{"sql": "SELECT * FROM nonexistent_table_xyz",
                    "raw": "", "finish_reason": "stop"} for _ in range(n)]
        return {"choices": choices, "prompt_tokens": toks,
                "latency_s": 0.01, "temperature": temperature, "n": n}


class _MockGeneratorGoodAtTopTier:
    """Always broken SQL but returns empty-result-free valid SQL only at
    the last tier.  Actually returns a query that returns 1 row from a
    real table only when schema contains a marker of the top tier."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.tokens_per_call = []
        self.n_calls = 0

    def generate(self, schema, question, evidence, temperature, n, seed,
                 exemplars=None, extra_note=""):
        self.n_calls += 1
        toks = 100 + len(schema)
        self.tokens_per_call.append(toks)
        # valid SQL returning rows when budget large enough (schema has
        # both name and age), else broken
        if "name" in schema and "age" in schema:
            sql = "SELECT name FROM singer"
        else:
            sql = "SELECT * FROM nonexistent_xyz"
        choices = [{"sql": sql, "raw": "", "finish_reason": "stop"}
                   for _ in range(n)]
        return {"choices": choices, "prompt_tokens": toks,
                "latency_s": 0.01, "temperature": temperature, "n": n}


class TestABSLAlwaysFail:
    def test_stops_at_top_tier(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorAlwaysBad()
        result = run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[100, 200, 400],
            start_tier=0,
            flags={"use_self_consistency": True,
                   "use_predictor": False},
        )
        # 3 tiers, always fails -> escalates through all, ends at top tier
        assert result["tier"] == 3
        assert len(result["escalations"]) == 2  # 1->2 and 2->3
        assert len(gen.calls) == 3  # one call per tier

    def test_tier_records_all_failed(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorAlwaysBad()
        result = run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[100, 200, 400],
            start_tier=0,
            flags={"use_self_consistency": True},
        )
        # one record per tier, none passed
        assert len(result["tier_records"]) == 3
        budgets = [r["budget"] for r in result["tier_records"]]
        assert budgets == [100, 200, 400]
        assert all(not r["passed"] for r in result["tier_records"])
        assert all(r["sql"] for r in result["tier_records"])

    def test_tier_records_good_at_top_tier(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorGoodAtTopTier(str(tmp_db))
        result = run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[10, 50, 2000],
            start_tier=0,
            flags={"use_self_consistency": True},
        )
        # one record per tier tried, increasing budget order
        budgets = [r["budget"] for r in result["tier_records"]]
        assert budgets == sorted(budgets)
        assert len(result["tier_records"]) >= 1
        # only the last tier tried passed; all before it failed
        passed = [r["passed"] for r in result["tier_records"]]
        assert all(not p for p in passed[:-1])
        assert passed[-1] is True
        assert result["tier_records"][-1]["sql"] == "SELECT name FROM singer"

    def test_token_accounting_sums_all_tiers(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorAlwaysBad()
        result = run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[100, 200, 400],
            start_tier=0,
            flags={"use_self_consistency": True},
        )
        expected = sum(gen.tokens_per_call)
        assert result["prompt_tokens_total"] == expected
        # total >= tokens of final-tier call alone
        assert result["prompt_tokens_total"] >= gen.tokens_per_call[-1]

    def test_no_escalation_when_passes(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorGoodAtTopTier(str(tmp_db))
        result = run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[10, 50, 2000],
            start_tier=0,
            flags={"use_self_consistency": True},
        )
        # at tier 0 (B=10) schema may be too small -> fails; at tier 2 (B=2000)
        # full schema included -> passes with 1 row
        assert result["tier"] >= 1
        if result["tier"] == 1:
            assert len(result["escalations"]) == 0

    def test_self_consistency_off_uses_n1_temp0(self, tmp_db):
        els = _mk_elements()
        gen = _MockGeneratorAlwaysBad()
        run_absl(
            question="q", evidence="e", elements=els,
            db_index=None, query_emb=None, db_path=str(tmp_db),
            generator=gen, tiers=[100, 200],
            start_tier=0,
            flags={"use_self_consistency": False},
        )
        assert all(c["n"] == 1 for c in gen.calls)
        assert all(c["temp"] == 0.0 for c in gen.calls)


class TestFeatures:
    def test_features_shape_and_finite(self):
        els = _mk_elements()
        idx = DBIndex("t", els)
        feats = compute_features("How many per average?", "evidence 42",
                                 els, idx, None)
        assert feats.shape == (10,)
        assert np.all(np.isfinite(feats))
        assert feats[0] == 4  # q word count: How many per average
        assert feats[1] >= 2  # 'per', 'average'
        assert feats[3] == 1  # numeral 42

    def test_labels(self):
        tier_correct = [
            [True, False, False],   # correct at tier 0 -> label 0
            [False, True, False],   # correct at tier 1 -> label 1
            [False, False, False],  # never -> label 2
            [False, False, True],   # correct at tier 2 -> label 2
        ]
        y = make_labels([], tier_correct)
        assert list(y) == [0, 1, 2, 2]

    def test_tier_budgets(self):
        assert tier_budgets("spider") == [500, 1500, 4000]
        assert tier_budgets("bird") == [1000, 4000, 16000]

    def test_predict_tier_default_when_no_model(self):
        assert predict_tier(None, np.zeros(10), default_tier=1) == 1
