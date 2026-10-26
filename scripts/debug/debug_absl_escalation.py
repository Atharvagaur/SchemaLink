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

# Mock generator that returns invalid SQL at lower tiers
class MockGeneratorEscalation:
    def __init__(self):
        self.calls = []
        self.n_calls = 0
    
    def generate(self, schema, question, evidence, temperature, n, seed,
                 exemplars=None, extra_note=""):
        self.n_calls += 1
        toks = 100 + len(schema)
        self.calls.append({"n": n, "temp": temperature, "extra": extra_note, "schema": schema})
        
        # Generate SQL based on tier (schema size)
        # We'll return invalid SQL for lower tiers to test escalation
        if len(schema) < 100:  # Low budget tier
            sql = "SELECT * FROM nonexistent_table_xyz"
        elif len(schema) < 200:  # Medium budget tier
            sql = "SELECT name FROM singer WHERE age > 100"  # Always returns empty
        else:  # High budget tier
            sql = "SELECT name FROM singer"
        
        choices = [{"sql": sql, "raw": "", "finish_reason": "stop"}
                   for _ in range(n)]
        return {"choices": choices, "prompt_tokens": toks,
                "latency_s": 0.01, "temperature": temperature, "n": n}

# Test the escalation logic
print("=== Testing ABSL Escalation ===")
print()

gen = MockGeneratorEscalation()

result = run_absl(
    question="How many singers over 25 are there?",
    evidence="",
    elements=elements,
    db_index=None,
    query_emb=None,
    db_path=db_path,
    generator=gen,
    tiers=[50, 150, 300],  # These budgets will progressively truncate the schema
    start_tier=0,
    flags={"use_self_consistency": False,
           "use_predictor": False,
           "sig_exec": True,  # Enable exec failure checking
           "sig_nonempty": True,  # Enable empty result checking
           "sig_agree": True,  # Enable agreement checking
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

# Check if tier records contain the correct information
print()
print("=== ANALYSIS ===")
print()
print("Expected behavior:")
print("  - Tier 1 should fail (nonexistent table)")
print("  - Tier 2 should fail (returns empty result)")
print("  - Tier 3 should pass (valid SQL that returns rows)")
print("  - Final SQL should be from Tier 3")
print()

print("Actual behavior:")
print(f"  - Final SQL: {result['sql']}")
print(f"  - Final tier: {result['tier']} (should be 3)")
print(f"  - Number of calls: {len(gen.calls)} (should be 3)")
print(f"  - Escalations: {len(result['escalations'])} (should be 2)")
print()

# Check if tier records show the progression
if len(result['tier_records']) >= 3:
    print("Tier records show progression:")
    for i, record in enumerate(result['tier_records']):
        print(f"  Tier {record['tier']}: passed={record['passed']}, sql='{record['sql']}'")
else:
    print(f"ERROR: Only {len(result['tier_records'])} tier records found, expected at least 3")

# Clean up
import os
os.unlink(db_path)
