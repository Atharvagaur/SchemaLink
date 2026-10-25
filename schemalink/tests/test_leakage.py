"""Leakage check: gold SQL must only be referenced in evaluator.py and for
train-split tier labels.  Also sqlglot label extraction on alias-heavy
queries (printed for eyeballing)."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "schemalink"

# Files ALLOWED to reference gold SQL (loading, evaluating, reporting only):
# loader loads gold; run passes gold to evaluator + builds train labels;
# generator uses gold for few-shot exemplars (train split only);
# difficulty builds tier labels from train; evaluator does EX checking;
# report carries gold as data in trace/failure output.
ALLOWED = {"evaluator.py", "difficulty.py", "loaders.py", "run.py",
           "generator.py", "report.py"}

# Decision-making files that must NEVER see gold SQL
FORBIDDEN = ["linkers/s0.py", "linkers/s1.py", "linkers/s2.py",
             "linkers/s3.py", "linkers/s6.py", "linkers/absl.py",
             "budget.py", "indexer.py"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_no_gold_sql_in_decision_components():
    """Linkers, budget enforcer and indexer must never see gold SQL —
    gold is only for evaluator (EX), train-split tier labels, loading,
    few-shot exemplars (train) and trace reporting."""
    offenders = []
    for rel in FORBIDDEN:
        p = PKG / rel
        assert p.exists(), f"missing {rel}"
        text = _read(p)
        for kw in ["gold_sql", "gold_rows", "gold_result", "gold_labels"]:
            if kw in text:
                offenders.append(f"{rel}: {kw}")
    assert not offenders, f"gold SQL leaked into decision code: {offenders}"


def test_report_carries_gold_only_as_data():
    """report.py may *carry* gold in trace output but must not use it to
    decide anything (no comparison of pred against gold inside report)."""
    text = _read(PKG / "report.py")
    # it writes gold_sql fields but must never call check_ex or compare
    assert "check_ex" not in text
    assert "results_equal" not in text


def test_no_gold_in_generator_prompt_path():
    """generator.py may carry gold only for exemplar construction
    (train split), never inside the per-question prompt builder."""
    text = _read(PKG / "generator.py")
    # build_messages must not mention gold
    m = re.search(r"def build_messages.*?(?=\ndef |\nclass |\Z)",
                  text, re.DOTALL)
    assert m, "build_messages not found"
    body = m.group(0)
    assert "gold" not in body.lower(), \
        "build_messages must not reference gold SQL"


# ---------------------------------------------------------------------------
# sqlglot label extraction on alias-heavy queries (printed for review)
# ---------------------------------------------------------------------------

BIRD_SCHEMA = {
    "table_names": [
        "employee", "department", "project", "task", "assignment",
        "salary", "location", "manager",
    ],
    "column_names": [
        [-1, "*"],
        [0, "emp_id"], [0, "name"], [0, "dept_id"], [0, "salary_id"],
        [1, "dept_id"], [1, "dept_name"], [1, "location_id"],
        [2, "project_id"], [2, "dept_id"], [2, "project_name"],
        [3, "task_id"], [3, "project_id"], [3, "task_name"],
        [4, "assign_id"], [4, "emp_id"], [4, "task_id"],
        [5, "salary_id"], [5, "amount"], [5, "emp_id"],
        [6, "location_id"], [6, "city"], [6, "country"],
        [7, "mgr_id"], [7, "emp_id"], [7, "dept_id"],
    ],
}

ALIAS_HEAVY_QUERIES = [
    # 1 self-join with aliases
    "SELECT e1.name AS emp_name, e2.name AS manager_name "
    "FROM employee e1 JOIN manager m ON e1.emp_id = m.emp_id "
    "JOIN employee e2 ON m.mgr_id = e2.emp_id WHERE e1.dept_id = 3",

    # 2 triple alias join
    "SELECT a.name, p.project_name, t.task_name "
    "FROM employee a JOIN assignment s ON a.emp_id = s.emp_id "
    "JOIN task t ON s.task_id = t.task_id "
    "JOIN project p ON t.project_id = p.project_id "
    "WHERE p.dept_id = 5",

    # 3 subquery with alias
    "SELECT x.city FROM location x WHERE x.location_id IN "
    "(SELECT d.location_id FROM department d WHERE d.dept_name = 'Sales')",

    # 4 CTE + alias
    "WITH high_paid AS (SELECT emp_id, amount FROM salary sl "
    "WHERE sl.amount > 100000) "
    "SELECT hp.emp_id FROM high_paid hp JOIN employee em "
    "ON hp.emp_id = em.emp_id",

    # 5 nested subquery, two aliases
    "SELECT em.name FROM employee em WHERE em.dept_id = "
    "(SELECT d.dept_id FROM department d WHERE d.location_id = "
    "(SELECT l.location_id FROM location l WHERE l.city = 'Paris'))",

    # 6 aggregation over alias join
    "SELECT d.dept_name, AVG(s.amount) FROM department d "
    "JOIN employee e ON d.dept_id = e.dept_id "
    "JOIN salary s ON e.salary_id = s.salary_id GROUP BY d.dept_name",

    # 7 three-level alias chain
    "SELECT t1.project_name, COUNT(t2.task_id) FROM project t1 "
    "JOIN task t2 ON t1.project_id = t2.project_id "
    "JOIN assignment t3 ON t2.task_id = t3.task_id "
    "GROUP BY t1.project_name",

    # 8 union with aliases
    "SELECT e.name AS label FROM employee e WHERE e.dept_id = 1 "
    "UNION SELECT d.dept_name AS label FROM department d WHERE d.dept_id = 2",

    # 9 correlated subquery with alias
    "SELECT e.name FROM employee e WHERE e.salary_id > "
    "(SELECT s.amount FROM salary s WHERE s.emp_id = e.emp_id) ",

    # 10 star + alias + join
    "SELECT s.*, d.dept_name FROM salary s "
    "JOIN employee e ON s.emp_id = e.emp_id "
    "JOIN department d ON e.dept_id = d.dept_id "
    "WHERE s.amount > 50000",
]


def test_label_extraction_alias_heavy(capsys):
    """Run extraction on 10 alias-heavy queries and PRINT labels so they
    can be eyeballed."""
    from schemalink.evaluator import extract_gold_columns

    all_ok = True
    for i, sql in enumerate(ALIAS_HEAVY_QUERIES, 1):
        labels = extract_gold_columns(sql, BIRD_SCHEMA)
        with capsys.disabled():
            print(f"\n[{i}] {sql}")
            print(f"    -> {sorted(labels)}")
        if not labels:
            all_ok = False

    # sanity: every query should yield at least one label
    assert all_ok, "some queries produced zero labels"


def test_label_extraction_resolves_aliases(capsys):
    """Alias columns must resolve to real table names."""
    from schemalink.evaluator import extract_gold_columns

    sql = ALIAS_HEAVY_QUERIES[0]
    labels = extract_gold_columns(sql, BIRD_SCHEMA)
    tables = {t for t, _ in labels}
    with capsys.disabled():
        print(f"\nAlias test: {sql}")
        print(f"  tables resolved: {sorted(tables)}")
    # no alias names should appear as tables
    assert "e1" not in tables
    assert "e2" not in tables
    assert "m" not in tables
    assert "employee" in tables
    assert "manager" in tables
