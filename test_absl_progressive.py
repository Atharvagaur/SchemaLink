import sys
sys.path.insert(0, '.')

from schemalink.linkers.absl import run_absl
from schemalink.indexer import SchemaElement
import tempfile
import sqlite3
from pathlib import Path

# Create a temporary database similar to conftest.py
def create_test_db():
    tmp = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
    db_path = tmp.name
    tmp.close()
    
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE singer (
            singer_id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER
        );
        CREATE TABLE concert (
            concert_id INTEGER PRIMARY KEY,
            singer_id INTEGER,
            title TEXT,
            FOREIGN KEY (singer_id) REFERENCES singer(singer_id)
        );
        INSERT INTO singer VALUES (1, 'Alice', 30), (2, 'Bob', 25), (3, 'Carol', 35);
        INSERT INTO concert VALUES (10, 1, 'Jazz Night'), (11, 2, 'Rock Fest'), (12, 1, 'Pop Show');
    """)
    conn.commit()
    conn.close()
    return db_path

# Create test database
db_path = create_test_db()
print(f"Created test database at: {db_path}")

# Create simple elements that will be progressively truncated
elements = [
    SchemaElement(table="singer", column="", dtype="TABLE"),
    SchemaElement(table="singer", column="singer_id", dtype="integer", is_pk=True),
    SchemaElement(table="singer", column="name", dtype="text", sample_values=["Alice", "Bob"]),
    SchemaElement(table="singer", column="age", dtype="integer", sample_values=["30", "25"]),
]

print(f"Created {len(elements)} elements")
print(f"Elements: {[e.element_id for e in elements]}")
print()

# Mock generator that returns different SQL at each tier based on schema
class MockGeneratorProgressive:
    def __init__(self):
        self.calls = []
    
    def generate(self, schema, question, evidence, temperature, n, seed,
                 exemplars=None, extra_note=""):
        toks = 100 + len(schema)  # Schema length changes as we progress
        self.calls.append({"n": n, "temp": temperature, "extra": extra_note, "schema": schema})
        
        # Generate different SQL based on schema content
        if "name" in schema and "age" in schema:
            sql = "SELECT name, age FROM singer WHERE age > 25"
        elif "name" in schema:
            sql = "SELECT name FROM singer"
        elif "age" in schema:
            sql = "SELECT age FROM singer"
        else:
            sql = "SELECT * FROM singer"
        
        choices = [{"sql": sql, "raw": "", "finish_reason": "stop"}
                   for _ in range(n)]
        return {"choices": choices, "prompt_tokens": toks,
                "latency_s": 0.01, "temperature": temperature, "n": n}

# Test the progressive behavior
print("=== Testing Progressive ABSL ===")
print()

gen = MockGeneratorProgressive()

result = run_absl(
    question="How many singers over 25 are there?",
    evidence="",
    elements=elements,
    db_index=None,
    query_emb=None,
    db_path=db_path,
    generator=gen,
    tiers=[50, 100, 300],  # These budgets will progressively truncate the schema
    start_tier=0,
    flags={"use_self_consistency": False,  # Use n=1 to simplify
           "use_predictor": False,
           "sig_exec": False,  # Don't fail on exec errors
           "sig_nonempty": False,  # Don't fail on empty results
           "sig_agree": False,  # Don't fail on agreement
           "use_value_index": False,
           "use_self_consistency": False},
)

print("Results:")
print(f"  Final SQL: {result['sql']}")
print(f"  Final tier: {result['tier']}")
print(f"  Start tier: {result['start_tier']}")
print(f"  Escalations: {len(result['escalations'])}")
for i, esc in enumerate(result['escalations']):
    print(f"    {i+1}. {esc}")
print(f"  Number of generator calls: {len(gen.calls)}")
print(f"  Prompt tokens total: {result['prompt_tokens_total']}")
print()
print("Per-call details:")
for i, call in enumerate(gen.calls):
    print(f"    Call {i+1}:")
    print(f"      schema length: {len(call['schema'])}")
    print(f"      schema preview: {call['schema'][:100]}...")
print()
print("Tier records:")
for i, record in enumerate(result['tier_records']):
    print(f"    Tier {record['tier']}: passed={record['passed']}, budget={record['budget']}, schema_tokens={record['schema_tokens']}, sql='{record['sql']}'")

# Clean up
import os
os.unlink(db_path)
