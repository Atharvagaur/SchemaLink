"""Step 2 tests: serialisation determinism, budget enforcement, PK/FK."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemalink.budget import count_tokens, enforce, serialise
from schemalink.indexer import SchemaElement, extract_elements


def _mk_schema():
    """Two tables with PK/FK relationships."""
    return [
        SchemaElement(table="singer", column="", dtype="TABLE"),
        SchemaElement(table="singer", column="singer_id", dtype="integer",
                      is_pk=True, sample_values=["1", "2", "3"]),
        SchemaElement(table="singer", column="name", dtype="text",
                      sample_values=["Alice", "Bob"]),
        SchemaElement(table="singer", column="age", dtype="integer",
                      sample_values=["30", "25"]),
        SchemaElement(table="concert", column="", dtype="TABLE"),
        SchemaElement(table="concert", column="concert_id", dtype="integer",
                      is_pk=True),
        SchemaElement(table="concert", column="singer_id", dtype="integer",
                      fk_target="singer.singer_id"),
        SchemaElement(table="concert", column="title", dtype="text",
                      sample_values=["Jazz Night", "Rock Fest"]),
    ]


class TestSerialisation:
    def test_deterministic(self):
        els = _mk_schema()
        a = serialise(els)
        b = serialise(list(els))
        assert a == b

    def test_contains_pk_marker(self):
        text = serialise(_mk_schema())
        assert "PRIMARY KEY" in text
        assert "singer_id integer PRIMARY KEY" in text

    def test_contains_fk_when_both_ends(self):
        text = serialise(_mk_schema())
        assert "FOREIGN KEY" in text
        assert "REFERENCES singer(singer_id)" in text

    def test_no_fk_when_target_missing(self):
        els = [e for e in _mk_schema() if e.table != "singer"]
        text = serialise(els)
        # singer table not included -> FK line should be absent
        assert "FOREIGN KEY" not in text

    def test_sample_comment(self):
        text = serialise(_mk_schema())
        assert "-- e.g." in text
        assert "Alice" in text

    def test_matched_value_annotation(self):
        els = _mk_schema()
        text = serialise(els, matched_values={"singer.name": "Alice"})
        assert "-- matched value: 'Alice'" in text


class TestBudget:
    def test_tokens_within_budget(self):
        els = _mk_schema()
        for B in [50, 100, 200, 500, 1000]:
            text, toks, selected, violation = enforce(els, B)
            assert toks == count_tokens(text)
            if not violation:
                assert toks <= B, f"B={B} but {toks} tokens"

    def test_pk_fk_kept_together(self):
        els = _mk_schema()
        for B in [100, 200, 500]:
            text, toks, selected, violation = enforce(els, B)
            sel_tables = {e.table for e in selected}
            sel_ids = {e.element_id for e in selected}
            # if a table is present, its PK must be present
            for t in sel_tables:
                has_pk = any(
                    e.table == t and e.is_pk
                    for e in selected
                )
                # only check when the table record is there and B is large enough
                if f"T:{t}" in sel_ids or any(
                    e.table == t for e in selected if not e.is_table
                ):
                    # PK should be kept if the table has any column selected
                    if any(e.table == t and not e.is_table for e in selected):
                        assert has_pk, f"table {t} selected without PK at B={B}"

    def test_pk_fk_together_violation_case(self):
        """When B is tiny, violation=True but still returns something."""
        els = _mk_schema()
        text, toks, selected, violation = enforce(els, 5)
        assert violation is True
        assert len(selected) >= 1

    def test_inf_budget_returns_everything(self):
        els = _mk_schema()
        text, toks, selected, violation = enforce(els, float("inf"))
        assert violation is False
        assert len(selected) == len(els)

    def test_deterministic_enforcement(self):
        els = _mk_schema()
        r1 = enforce(els, 300)
        r2 = enforce(list(els), 300)
        assert r1[0] == r2[0]
        assert r1[1] == r2[1]
        assert r1[3] == r2[3]


class TestExtractElements:
    def test_extract_from_metadata(self, tmp_path):
        import sqlite3
        db = tmp_path / "d.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE t1 (id INTEGER PRIMARY KEY, nm TEXT, val REAL);
            CREATE TABLE t2 (id INTEGER PRIMARY KEY, t1_id INTEGER,
                             FOREIGN KEY (t1_id) REFERENCES t1(id));
            INSERT INTO t1 VALUES (1, 'alpha', 1.5), (2, 'beta', 2.5),
                                  (3, 'gamma', 3.5), (4, 'delta', 4.5),
                                  (5, 'epsilon', 5.5), (6, 'zeta', 6.5);
            INSERT INTO t2 VALUES (1, 1), (2, 2);
        """)
        conn.commit()
        conn.close()

        meta = {
            "table_names_original": ["t1", "t2"],
            "column_names_original": [
                [-1, "*"],
                [0, "id"], [0, "nm"], [0, "val"],
                [1, "id"], [1, "t1_id"],
            ],
            "column_types": ["text", "number", "text", "number", "number", "number"],
            "primary_keys": [1, 4],
            "foreign_keys": [[5, 1]],
        }
        els = extract_elements(meta, str(db))
        tables = [e for e in els if e.is_table]
        assert {t.table for t in tables} == {"t1", "t2"}
        pks = [e for e in els if e.is_pk]
        assert len(pks) == 2
        fks = [e for e in els if e.fk_target]
        assert len(fks) == 1
        assert fks[0].fk_target == "t1.id"
        # sample values collected
        nm = next(e for e in els if e.column == "nm")
        assert "alpha" in nm.sample_values
        assert len(nm.sample_values) <= 5
        assert all(len(v) <= 40 for v in nm.sample_values)


class TestPKNormalization:
    """Primary-key index normalization across dataset formats."""

    def test_spider_flat_list(self):
        from schemalink.indexer import _normalize_pk_indices
        assert _normalize_pk_indices([1, 9]) == {1, 9}

    def test_bird_mixed_int_and_list(self):
        from schemalink.indexer import _normalize_pk_indices
        # BIRD format: flat ints + a composite PK list
        assert _normalize_pk_indices([1, 4, 8, 10, [19, 20]]) == {1, 4, 8, 10, 19, 20}

    def test_list_of_lists(self):
        from schemalink.indexer import _normalize_pk_indices
        assert _normalize_pk_indices([[0], [1], [2]]) == {0, 1, 2}

    def test_composite_pk_preserved(self):
        from schemalink.indexer import _normalize_pk_indices
        # composite key on columns 19 and 20 -> both preserved
        assert _normalize_pk_indices([[19, 20]]) == {19, 20}

    def test_empty_and_none(self):
        from schemalink.indexer import _normalize_pk_indices
        assert _normalize_pk_indices([]) == set()
        assert _normalize_pk_indices(None) == set()


class TestExtractElementsBirdFormat:
    """Schema element extraction using real BIRD-style metadata (mixed PK list)."""

    def test_bird_mixed_pk_no_crash(self, tmp_path):
        import sqlite3
        db = tmp_path / "bird.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT, balance REAL);
            CREATE TABLE tx (id INTEGER PRIMARY KEY, acct_id INTEGER,
                             amount REAL, FOREIGN KEY (acct_id) REFERENCES accounts(id));
            INSERT INTO accounts VALUES (1, 'Alice', 100.0), (2, 'Bob', 200.0);
            INSERT INTO tx VALUES (1, 1, 50.0), (2, 2, 75.0);
        """)
        conn.commit()
        conn.close()

        # BIRD-style: primary_keys mixes ints and a composite-list entry
        meta = {
            "table_names_original": ["accounts", "tx"],
            "column_names_original": [
                [-1, "*"],
                [0, "id"], [0, "name"], [0, "balance"],
                [1, "id"], [1, "acct_id"], [1, "amount"],
            ],
            "column_types": ["text", "integer", "text", "real",
                             "integer", "integer", "real"],
            # BIRD-style: primary_keys mixes ints and a composite-list entry.
            # col indices: 0="*", 1=accounts.id, 2=accounts.name,
            #   3=accounts.balance, 4=tx.id, 5=tx.acct_id, 6=tx.amount
            "primary_keys": [1, [4, 5]],
            "foreign_keys": [[5, 1]],
        }
        # Should not raise TypeError: unhashable type: 'list'
        els = extract_elements(meta, str(db))
        pks = [e for e in els if e.is_pk]
        pk_columns = {(e.table, e.column) for e in pks}
        assert ("accounts", "id") in pk_columns
        assert ("tx", "id") in pk_columns
        assert ("tx", "acct_id") in pk_columns  # composite PK entry preserved
        assert len(pks) == 3
        fks = [e for e in els if e.fk_target]
        assert len(fks) == 1
        assert fks[0].fk_target == "accounts.id"

    def test_spider_format_still_works(self, tmp_path):
        import sqlite3
        db = tmp_path / "spider.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE t1 (id INTEGER PRIMARY KEY, nm TEXT);
            CREATE TABLE t2 (id INTEGER PRIMARY KEY, t1_id INTEGER,
                             FOREIGN KEY (t1_id) REFERENCES t1(id));
            INSERT INTO t1 VALUES (1, 'alpha'), (2, 'beta');
            INSERT INTO t2 VALUES (1, 1), (2, 2);
        """)
        conn.commit()
        conn.close()

        meta = {
            "table_names_original": ["t1", "t2"],
            "column_names_original": [
                [-1, "*"], [0, "id"], [0, "nm"],
                [1, "id"], [1, "t1_id"],
            ],
            "column_types": ["text", "integer", "text", "integer", "integer"],
            "primary_keys": [1, 3],
            "foreign_keys": [[4, 1]],
        }
        els = extract_elements(meta, str(db))
        pks = [e for e in els if e.is_pk]
        assert len(pks) == 2
        fks = [e for e in els if e.fk_target]
        assert len(fks) == 1
        assert fks[0].fk_target == "t1.id"

