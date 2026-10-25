"""Step 6 tests: AUBC, bootstrap, taxonomy, report outputs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemalink.report import (
    aubc,
    bes,
    bootstrap_ci,
    compute_aubc_per_strategy,
    error_taxonomy,
    execution_accuracy,
    generate_report,
    read_traces,
    summarise,
    write_traces,
)


def _trace(qid, strategy, budget, ex, **kw):
    d = {
        "qid": qid, "dataset": "spider", "strategy": strategy,
        "budget": budget, "tier": None, "escalations": [],
        "schema_tokens": budget // 2 if budget else 0,
        "prompt_tokens_total": 1000, "latency_s": 0.5,
        "selected_elements": [], "pred_sql": "SELECT 1",
        "gold_sql": "SELECT 1", "ex": ex, "error": "",
        "budget_violation": False,
    }
    d.update(kw)
    return d


class TestAUBC:
    def test_flat_perfect(self):
        assert aubc([(250, 1.0), (4000, 1.0)]) == pytest.approx(1.0)

    def test_flat_zero(self):
        assert aubc([(250, 0.0), (4000, 0.0)]) == pytest.approx(0.0)

    def test_monotone_between_0_1(self):
        v = aubc([(250, 0.2), (500, 0.4), (1000, 0.5), (4000, 0.7)])
        assert 0.0 <= v <= 1.0
        assert v > 0.2 and v < 0.7

    def test_single_point(self):
        assert aubc([(1000, 0.6)]) == pytest.approx(0.6)

    def test_higher_is_better(self):
        low = aubc([(250, 0.1), (4000, 0.3)])
        high = aubc([(250, 0.5), (4000, 0.9)])
        assert high > low


class TestBES:
    def test_basic(self):
        assert bes(0.5, 1000) == pytest.approx(0.5)

    def test_zero_tokens(self):
        assert bes(0.5, 0) == 0.0


class TestBootstrap:
    def test_ci_contains_point_estimate(self):
        traces = [_trace(f"q{i}", "s1", 500, i % 2 == 0) for i in range(50)]
        ex = execution_accuracy(traces)
        lo, hi = bootstrap_ci(traces)
        assert lo <= ex <= hi
        assert 0 <= lo <= 1 and 0 <= hi <= 1

    def test_deterministic_seed(self):
        traces = [_trace(f"q{i}", "s1", 500, True) for i in range(20)]
        a = bootstrap_ci(traces, seed=0)
        b = bootstrap_ci(traces, seed=0)
        assert a == b


class TestTaxonomy:
    def test_categories(self):
        traces = [
            _trace("a", "s1", 500, False, schema_recall=0.5),
            _trace("b", "s1", 500, False, error="exec_error: no such table"),
            _trace("c", "s1", 500, False, error="empty_result"),
            _trace("d", "s1", 500, False, schema_recall=1.0),
            _trace("e", "s1", 500, True, schema_recall=1.0),
        ]
        tax = error_taxonomy(traces)
        assert tax["s1"]["missing_column"] == 1
        assert tax["s1"]["exec_error"] == 1
        assert tax["s1"]["empty_result"] == 1
        assert tax["s1"]["wrong_result_with_full_recall"] == 1
        assert tax["s1"]["correct"] == 1


class TestSummarise:
    def test_grouping(self):
        traces = (
            [_trace(f"s1_{i}", "s1", 500, i % 2 == 0) for i in range(10)]
            + [_trace(f"s1_{i}", "s1", 1000, True) for i in range(10)]
        )
        rows = summarise(traces)
        assert len(rows) == 2
        r500 = next(r for r in rows if r["budget"] == 500)
        assert r500["ex"] == pytest.approx(0.5)
        assert r500["n"] == 10

    def test_aubc_per_strategy(self):
        traces = (
            [_trace(f"a{i}", "s1", 250, False) for i in range(5)]
            + [_trace(f"b{i}", "s1", 4000, True) for i in range(5)]
            + [_trace(f"c{i}", "s0", None, True) for i in range(5)]
            + [_trace(f"d{i}", "absl", None, True) for i in range(5)]
            + [_trace(f"e{i}", "absl_cap", 4000, True) for i in range(5)]
        )
        a = compute_aubc_per_strategy(traces)
        assert "s1" in a
        assert "s0" not in a
        assert "absl" not in a
        assert "absl_cap" not in a
        assert 0 <= a["s1"] <= 1


class TestReportOutputs:
    def test_full_report(self, tmp_path):
        traces = (
            [_trace(f"s1_{i}", "s1", b, i % 2 == 0)
             for b in (250, 500) for i in range(6)]
            + [_trace(f"s3_{i}", "s3", b, True)
               for b in (250, 500) for i in range(6)]
        )
        out = tmp_path / "results"
        summary = generate_report(traces, out)

        assert (out / "summary.csv").exists()
        assert (out / "traces.jsonl").exists()
        assert (out / "failures.md").exists()
        assert (out / "pareto_spider.png").exists()

        rt = read_traces(out / "traces.jsonl")
        assert len(rt) == len(traces)
        assert "qid" in rt[0] and "ex" in rt[0]
        assert "aubc" in summary
        assert "s1" in summary["aubc"]
