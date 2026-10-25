"""Read-only SQLite execution, EX checking, schema-recall label extraction."""

import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# SQLite execution (read-only, 30 s timeout)
# ---------------------------------------------------------------------------

def execute_sql(db_path: str, sql: str, timeout_s: float = 30.0) -> Tuple[bool, Any, Optional[str], float]:
    """Execute sql read-only. Returns (ok, rows|None, error|None, latency_s)."""
    uri = f"file:{db_path}?mode=ro"
    start = time.time()
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_s)
        conn.text_factory = lambda b: b.decode("utf-8", "replace")
        conn.row_factory = None

        elapsed = [0.0]

        def _progress():
            if time.time() - start > timeout_s:
                return 1  # abort
            return 0

        conn.set_progress_handler(_progress, 10000)
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        conn.close()
        return True, rows, None, time.time() - start
    except sqlite3.OperationalError as e:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        msg = str(e)
        if "interrupted" in msg.lower() or time.time() - start >= timeout_s:
            return False, None, f"timeout: {msg}", time.time() - start
        return False, None, msg, time.time() - start
    except Exception as e:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        return False, None, str(e), time.time() - start


def get_gold_rows(db_path: str, gold_sql: str, timeout_s: float = 30.0) -> Tuple[Any, Optional[str]]:
    """Execute gold SQL; returns (rows, error)."""
    ok, rows, err, _ = execute_sql(db_path, gold_sql, timeout_s)
    return (rows if ok else None), err


# ---------------------------------------------------------------------------
# Result normalisation and EX comparison
# ---------------------------------------------------------------------------

def _norm_cell(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 6)
    return v


def _norm_row(row: Sequence[Any]) -> Tuple:
    return tuple(_norm_cell(c) for c in row)


def _has_order_by(sql: str) -> bool:
    """Rough check for ORDER BY at top level (not inside a subquery only)."""
    lower = sql.lower()
    return bool(re.search(r"\border\s+by\b", lower))


def results_equal(
    pred_rows: Sequence[Sequence[Any]],
    gold_rows: Sequence[Sequence[Any]],
    ordered: bool,
) -> bool:
    """Multiset (or sequence) comparison after normalisation."""
    if pred_rows is None or gold_rows is None:
        return False
    p = [_norm_row(r) for r in pred_rows]
    g = [_norm_row(r) for r in gold_rows]
    if len(p) != len(g):
        return False
    if ordered:
        return p == g
    # multiset comparison
    from collections import Counter
    return Counter(map(tuple, p)) == Counter(map(tuple, g))


def check_ex(db_path: str, pred_sql: str, gold_sql: str, timeout_s: float = 30.0) -> Tuple[bool, str]:
    """Execution accuracy check.

    Returns (is_correct, error_type) where error_type is one of
    'correct', 'exec_error', 'timeout', 'empty_result', 'wrong_result'.
    """
    # gold result
    gold_ok, gold_rows, gold_err, _ = execute_sql(db_path, gold_sql, timeout_s)
    if not gold_ok:
        return False, "gold_error"

    pred_ok, pred_rows, pred_err, _ = execute_sql(db_path, pred_sql, timeout_s)
    if not pred_ok:
        if pred_err and "timeout" in pred_err:
            return False, "timeout"
        return False, "exec_error"

    if not gold_rows and not pred_rows:
        return True, "correct"
    if pred_rows is not None and len(pred_rows) == 0 and gold_rows and len(gold_rows) > 0:
        return False, "empty_result"

    ordered = _has_order_by(gold_sql)
    if results_equal(pred_rows, gold_rows, ordered):
        return True, "correct"
    return False, "wrong_result"


def result_signature(rows: Optional[Sequence[Sequence[Any]]], ordered: bool = False) -> Optional[Tuple]:
    """Hashable signature of a result set for majority voting."""
    if rows is None:
        return None
    norm = [_norm_row(r) for r in rows]
    if not ordered:
        norm = sorted(norm)
    return tuple(norm)


# ---------------------------------------------------------------------------
# sqlglot label extraction (schema recall / precision)
# ---------------------------------------------------------------------------

def extract_gold_columns(gold_sql: str, schema: Dict[str, Any]) -> Set[Tuple[str, str]]:
    """Extract (table, column) labels from gold SQL using sqlglot.

    Resolves aliases and CTEs; '*' expands to all columns of the referenced table.
    schema: dict with 'table_names_original' and 'column_names_original'
            (Spider-style) OR {'tables': {name: [col, ...]}}.
    """
    import sqlglot
    from sqlglot import exp

    # Normalise schema into {table_lower: set(col_lower)}
    tbl_cols: Dict[str, Set[str]] = {}
    if isinstance(schema, dict) and "table_names_original" in schema:
        tnames = schema["table_names_original"]
        cnames = schema["column_names_original"]  # list of [table_idx, col]
        for ti, tname in enumerate(tnames):
            tbl_cols[tname.lower()] = set()
        for entry in cnames:
            ti, cname = entry[0], entry[1]
            if ti >= 0 and ti < len(tnames):
                tbl_cols[tnames[ti].lower()].add(cname.lower())
    elif isinstance(schema, dict) and "tables" in schema:
        for tname, cols in schema["tables"].items():
            tbl_cols[tname.lower()] = {c.lower() for c in cols}
    elif isinstance(schema, dict):
        # column_names / table_names lowercase variants (BIRD dev_tables)
        tnames = schema.get("table_names", schema.get("table_names_original", []))
        cnames = schema.get("column_names", [])
        for ti, tname in enumerate(tnames):
            tbl_cols[tname.lower()] = set()
        for entry in cnames:
            if len(entry) >= 2:
                ti, cname = entry[0], entry[1]
                if ti >= 0 and ti < len(tnames):
                    tbl_cols[tnames[ti].lower()].add(cname.lower())

    labels: Set[Tuple[str, str]] = set()
    try:
        tree = sqlglot.parse_one(gold_sql, dialect="sqlite")
    except Exception:
        return labels

    if tree is None:
        return labels

    # Collect CTE names so we don't attribute their columns to real tables
    cte_names: Set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = cte.alias or ""
        if alias:
            cte_names.add(alias.lower())

    # Build alias -> table mapping from FROM / JOIN
    alias_map: Dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        tname = (table.name or "").lower()
        talias = (table.alias or "").lower()
        if tname and talias and talias != tname:
            alias_map[talias] = tname
        if tname and not talias:
            alias_map[tname] = tname

    def _resolve(scope_name: str) -> Optional[str]:
        """Resolve a table reference (possibly alias) to a real table name."""
        sn = scope_name.lower()
        if sn in cte_names:
            return None  # CTE – not a real table
        if sn in alias_map:
            return alias_map[sn]
        if sn in tbl_cols:
            return sn
        return None

    def _within_aggregate(node) -> bool:
        """True when node sits inside a function/aggregate (e.g. count(*))
        before reaching the enclosing Select."""
        p = node.parent
        while p is not None and not isinstance(p, exp.Select):
            if isinstance(p, exp.Func):
                return True
            p = p.parent
        return False

    for col in tree.find_all(exp.Column):
        # Skip columns inside CTE definitions? sqlglot scopes are complex;
        # we do a best-effort: check if the column's enclosing select has a CTE alias.
        col_table = col.table.lower() if col.table else ""
        col_name = (col.name or "").lower()
        if not col_name or col_name == "*":
            # star expansion (count(*) references no columns; do not expand)
            if _within_aggregate(col):
                continue
            if col_table:
                resolved = _resolve(col_table)
                if resolved and resolved in tbl_cols:
                    for c in tbl_cols[resolved]:
                        labels.add((resolved, c))
            else:
                # unqualified * -> all tables in FROM
                for t in tree.find_all(exp.Table):
                    resolved = _resolve(t.name)
                    if resolved and resolved in tbl_cols:
                        for c in tbl_cols[resolved]:
                            labels.add((resolved, c))
            continue

        if col_table:
            resolved = _resolve(col_table)
            if resolved and resolved in tbl_cols:
                labels.add((resolved, col_name))
            elif resolved is None and col_table not in cte_names:
                # unknown table – still record with resolved name if possible
                pass
        else:
            # unqualified column: search all tables for a match
            found = False
            for tname, cols in tbl_cols.items():
                if col_name in cols:
                    labels.add((tname, col_name))
                    found = True
                    if len(tbl_cols) > 1:
                        # ambiguous; add for all matches to be safe on recall side
                        continue
                    break
            if not found:
                pass

    # Handle standalone Star nodes (SELECT * / t.*)
    for star in tree.find_all(exp.Star):
        # count(*) / sum(*) reference no columns; don't expand those stars
        if _within_aggregate(star):
            continue
        # Walk up to find the enclosing Select
        node = star.parent
        while node is not None and not isinstance(node, exp.Select):
            node = node.parent
        if node is None:
            continue
        sel = node
        # Star's own table qualifier lives on Column("*") sometimes; check parent
        qual = ""
        if isinstance(star.parent, exp.Column):
            qual = (star.parent.table or "").lower()
        if qual:
            resolved = _resolve(qual)
            if resolved and resolved in tbl_cols:
                for c in tbl_cols[resolved]:
                    labels.add((resolved, c))
        else:
            # unqualified * -> expand all tables in this select's FROM/JOIN
            from_tables = list(sel.find_all(exp.Table))
            # also check FROM clause directly
            if not from_tables and sel.args.get("from"):
                from_tables = list(sel.args["from"].find_all(exp.Table))
            for t in from_tables:
                resolved = _resolve(t.name)
                if resolved and resolved in tbl_cols:
                    for c in tbl_cols[resolved]:
                        labels.add((resolved, c))

    return labels


def compute_schema_recall_precision(
    selected: Set[Tuple[str, str]],
    gold_labels: Set[Tuple[str, str]],
) -> Tuple[float, float]:
    """Return (recall, precision). Empty gold => recall 1.0."""
    if not gold_labels:
        return 1.0, 1.0 if not selected else 1.0
    tp = len(selected & gold_labels)
    recall = tp / len(gold_labels) if gold_labels else 1.0
    precision = tp / len(selected) if selected else 0.0
    return recall, precision
