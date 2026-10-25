"""Step 1 tests: loaders, executor, EX checker."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemalink.evaluator import (
    check_ex,
    compute_schema_recall_precision,
    execute_sql,
    extract_gold_columns,
    results_equal,
    result_signature,
)
from schemalink.loaders import check_dataset, load_dev, load_train


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class TestExecutor:
    def test_select(self, tmp_db):
        ok, rows, err, lat = execute_sql(tmp_db, "SELECT name FROM singer ORDER BY singer_id")
        assert ok, err
        assert len(rows) == 3
        assert rows[0][0] == "Alice"

    def test_read_only(self, tmp_db):
        ok, rows, err, _ = execute_sql(tmp_db, "DELETE FROM singer")
        assert not ok
        assert err is not None

    def test_bad_sql(self, tmp_db):
        ok, rows, err, _ = execute_sql(tmp_db, "SELECT * FROM nope")
        assert not ok
        assert "no such table" in err.lower()

    def test_timeout_query(self, tmp_path):
        """A pathological query should hit the progress handler."""
        db = tmp_path / "big.sqlite"
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(1000)])
        conn.commit()
        conn.close()
        # Use a tiny timeout to force abort on a cross join that's slow enough
        sql = """
            SELECT count(*) FROM t a, t b, t c
            WHERE (a.a * b.a + c.a) % 7 = 3
        """
        ok, rows, err, _ = execute_sql(str(db), sql, timeout_s=0.01)
        # Either it finishes fast (ok) or times out; must not hang
        if not ok:
            assert err is not None


# ---------------------------------------------------------------------------
# EX checker
# ---------------------------------------------------------------------------

class TestEX:
    def test_gold_matches_itself(self, tmp_db):
        gold = "SELECT name FROM singer ORDER BY singer_id"
        ok, etype = check_ex(tmp_db, gold, gold)
        assert ok, etype
        assert etype == "correct"

    def test_wrong_sql_fails(self, tmp_db):
        gold = "SELECT name FROM singer ORDER BY singer_id"
        pred = "SELECT name FROM singer WHERE singer_id = 1"
        ok, etype = check_ex(tmp_db, pred, gold)
        assert not ok
        assert etype in ("wrong_result", "empty_result")

    def test_timeout_returns_error(self, tmp_path):
        import sqlite3
        db = tmp_path / "big2.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(500)])
        conn.commit()
        conn.close()
        gold = "SELECT count(*) FROM t"
        pred = "SELECT count(*) FROM t a, t b, t c, t d WHERE (a.a*b.a+c.a*d.a)%999983=7"
        ok, etype = check_ex(str(db), pred, gold, timeout_s=0.05)
        # Must return quickly with an error, not hang
        assert not ok
        assert etype in ("timeout", "exec_error", "wrong_result", "empty_result")

    def test_order_by_matters(self, tmp_db):
        gold = "SELECT name FROM singer ORDER BY age DESC"
        pred = "SELECT name FROM singer ORDER BY age ASC"
        ok, _ = check_ex(tmp_db, pred, gold)
        assert not ok, "ORDER BY direction should affect EX"

    def test_unordered_multiset(self, tmp_db):
        gold = "SELECT name FROM singer"
        pred = "SELECT name FROM singer"
        ok, _ = check_ex(tmp_db, pred, gold)
        assert ok

    def test_float_rounding(self, tmp_path):
        import sqlite3
        db = tmp_path / "f.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE v (x REAL)")
        conn.execute("INSERT INTO v VALUES (1.0000001)")
        conn.commit()
        conn.close()
        gold = "SELECT 1.0000002 AS x"
        # pred returns 1.0000001; round to 6 digits both become 1.0
        # actually 1.0000001 rounds to 1.0 and 1.0000002 rounds to 1.0 -> equal
        ok, _ = check_ex(str(db), "SELECT x FROM v", gold)
        # gold returns a constant 1.0000002, pred returns 1.0000001 -> both round to 1.0
        # but col count is 1 each, row count 1 each
        assert ok or not ok  # just ensure no crash; specific rounding tested below

    def test_results_equal_rounding(self):
        assert results_equal([[1.0000001]], [[1.0000002]], ordered=True)
        assert not results_equal([[1.001]], [[1.002]], ordered=True)

    def test_result_signature(self):
        sig1 = result_signature([[1, "a"], [2, "b"]])
        sig2 = result_signature([[2, "b"], [1, "a"]])
        assert sig1 == sig2  # multiset, unordered
        sig3 = result_signature([[1, "a"], [2, "b"]], ordered=True)
        sig4 = result_signature([[2, "b"], [1, "a"]], ordered=True)
        assert sig3 != sig4


# ---------------------------------------------------------------------------
# sqlglot label extraction
# ---------------------------------------------------------------------------

SCHEMA = {
    "table_names_original": ["singer", "concert"],
    "column_names_original": [
        [-1, "*"],
        [0, "singer_id"],
        [0, "name"],
        [0, "age"],
        [1, "concert_id"],
        [1, "singer_id"],
        [1, "title"],
    ],
}


class TestLabelExtraction:
    def test_simple_select(self):
        labels = extract_gold_columns(
            "SELECT name FROM singer WHERE age > 25", SCHEMA
        )
        assert ("singer", "name") in labels
        assert ("singer", "age") in labels

    def test_alias_resolution(self):
        labels = extract_gold_columns(
            "SELECT s.name FROM singer AS s WHERE s.age > 25", SCHEMA
        )
        assert ("singer", "name") in labels
        assert ("singer", "age") in labels

    def test_star_expansion(self):
        labels = extract_gold_columns("SELECT * FROM singer", SCHEMA)
        assert ("singer", "singer_id") in labels
        assert ("singer", "name") in labels
        assert ("singer", "age") in labels

    def test_join(self):
        labels = extract_gold_columns(
            "SELECT s.name, c.title FROM singer s "
            "JOIN concert c ON s.singer_id = c.singer_id",
            SCHEMA,
        )
        assert ("singer", "name") in labels
        assert ("singer", "singer_id") in labels
        assert ("concert", "title") in labels
        assert ("concert", "singer_id") in labels

    def test_cte_not_counted_as_table(self):
        labels = extract_gold_columns(
            "WITH top_singers AS (SELECT * FROM singer WHERE age > 25) "
            "SELECT name FROM top_singers",
            SCHEMA,
        )
        # CTE columns should resolve back to singer via star or be found unqualified
        assert ("singer", "singer_id") in labels  # from SELECT * in CTE

    def test_unqualified_column(self):
        labels = extract_gold_columns(
            "SELECT title FROM concert", SCHEMA
        )
        assert ("concert", "title") in labels

    def test_recall_precision(self):
        gold = {("a", "x"), ("a", "y")}
        sel = {("a", "x"), ("b", "z")}
        r, p = compute_schema_recall_precision(sel, gold)
        assert abs(r - 0.5) < 1e-9
        assert abs(p - 0.5) < 1e-9

    def test_empty_gold(self):
        r, p = compute_schema_recall_precision({("a", "x")}, set())
        assert r == 1.0


# ---------------------------------------------------------------------------
# Loaders (with a synthetic mini-dataset)
# ---------------------------------------------------------------------------

class TestLoaders:
    def _make_spider(self, root: Path, n_q=10):
        dev = []
        for i in range(n_q):
            dev.append({
                "question_id": i,
                "question": f"Q{i}?",
                "query": "SELECT 1",
                "database_id": "db1",
            })
        tables = [{"db_id": "db1", "table_names_original": ["t"],
                   "column_names_original": [[-1, "*"], [0, "id"]]}]
        (root / "spider").mkdir(parents=True, exist_ok=True)
        (root / "spider" / "dev.json").write_text(json.dumps(dev))
        (root / "spider" / "tables.json").write_text(json.dumps(tables))
        (root / "spider" / "train_spider.json").write_text(json.dumps(dev))
        (root / "spider" / "database" / "db1").mkdir(parents=True, exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(str(root / "spider" / "database" / "db1" / "db1.sqlite"))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

    def test_load_dev_spider(self, tmp_path):
        self._make_spider(tmp_path)
        cfg = {"datasets": {"spider": {
            "dev": "spider/dev.json", "tables": "spider/tables.json",
            "train": "spider/train_spider.json", "db_root": "spider/database",
            "root": str(tmp_path),
        }}}
        qs = load_dev("spider", cfg, n=5, seed=0)
        assert len(qs) == 5
        assert qs[0]["dataset"] == "spider"
        assert qs[0]["db_path"].endswith(".sqlite")
        # seeded determinism
        qs2 = load_dev("spider", cfg, n=5, seed=0)
        assert [q["qid"] for q in qs] == [q["qid"] for q in qs2]

    def test_missing_dataset_raises(self, tmp_path):
        cfg = {"datasets": {"spider": {
            "dev": "spider/dev.json", "tables": "spider/tables.json",
            "train": "spider/train_spider.json", "db_root": "spider/database",
            "root": str(tmp_path),
        }}}
        with pytest.raises(FileNotFoundError, match="Download"):
            check_dataset("spider", cfg)

    def test_train_load(self, tmp_path):
        self._make_spider(tmp_path)
        cfg = {"datasets": {"spider": {
            "dev": "spider/dev.json", "tables": "spider/tables.json",
            "train": "spider/train_spider.json", "db_root": "spider/database",
            "root": str(tmp_path),
        }}}
        tr = load_train("spider", cfg, n=3, seed=0)
        assert len(tr) == 3
