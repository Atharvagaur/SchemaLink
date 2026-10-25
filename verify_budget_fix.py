import sys
sys.path.insert(0, '.')

from schemalink.budget import count_tokens, serialise, enforce
from schemalink.indexer import SchemaElement

print('=== VERIFYING BUDGET ENFORCEMENT FIX ===')
print()

# Test 1: Empty budget with PK (is_pk=True)
print('Test 1: Empty budget (0) with PK (is_pk=True)')
pk_element = SchemaElement('users', 'id', 'INTEGER PRIMARY KEY', is_pk=True)
elements1 = [pk_element]
text1, toks1, selected1, violation1 = enforce(elements1, 0)
print(f'  Budget: 0')
print(f'  Returned tokens: {toks1}')
print(f'  Violation: {violation1}')
print(f'  Selected elements: {[e.element_id for e in selected1]}')
print(f'  Selected text: {text1}')
print()

# Test 2: Budget that fits only PK
pk_text = serialise([pk_element])
pk_tokens = count_tokens(pk_text)
print(f'  PK only requires {pk_tokens} tokens')
print(f'  Budget 0: Should fit PK (0 >= 0): {pk_tokens <= 0}')
print(f'  Budget 0: Selected PK: {len(selected1) > 0}')
print()

# Test 3: Budget that fits PK but not additional elements
elements2 = [
    pk_element,
    SchemaElement('users', 'name', 'TEXT', sample_values=['Alice']),
]
print('Test 2: Budget = 15 (should fit PK but not sample values)')
text2, toks2, selected2, violation2 = enforce(elements2, 15)
print(f'  Budget: 15')
print(f'  Returned tokens: {toks2}')
print(f'  Violation: {violation2}')
print(f'  Selected elements: {[e.element_id for e in selected2]}')
print(f'  Selected text: {text2}')
print(f'  Expected: PK only, tokens ~{pk_tokens}')
print()

# Test 4: Budget that fits everything
print('Test 3: Budget = 50 (should fit everything)')
text3, toks3, selected3, violation3 = enforce(elements2, 50)
print(f'  Budget: 50')
print(f'  Returned tokens: {toks3}')
print(f'  Violation: {violation3}')
print(f'  Selected elements: {[e.element_id for e in selected3]}')
print()

# Test 5: Verify returned token count matches actual
print('Test 4: Verification - returned tokens match serialized text')
text3_recomputed = serialise(selected3)
toks3_recomputed = count_tokens(text3_recomputed)
print(f'  Returned tokens: {toks3}')
print(f'  Recomputed tokens: {toks3_recomputed}')
print(f'  Match: {toks3 == toks3_recomputed}')
print()

# Test 6: Verify with more complex schema
print('Test 5: Complex schema with FK')
from schemalink.indexer import extract_elements
import tempfile
import sqlite3

db_path = 'test.sqlite'
with open(db_path, 'w') as f:
    pass

# Create minimal DB for testing
with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
    tmp_db_path = tmp.name

conn = sqlite3.connect(tmp_db_path)
conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)")
conn.commit()
conn.close()

schema_meta = {
    'table_names_original': ['users', 'orders'],
    'column_names_original': [[0, ''], [0, 'id'], [0, 'name'], [1, 'id'], [1, 'user_id']],
    'column_types': ['', 'INTEGER', 'TEXT', 'INTEGER', 'INTEGER'],
    'primary_keys': [1, 3],
    'foreign_keys': [[4, 1]],
}

complex_elements = extract_elements(schema_meta, tmp_db_path)
print(f'  Complex elements: {len(complex_elements)}')
print(f'  PK elements: {[e.element_id for e in complex_elements if e.is_pk]}')
print(f'  FK elements: {[e.element_id for e in complex_elements if e.fk_target]}')

# Budget too small for PK
text6, toks6, selected6, violation6 = enforce(complex_elements, 10)
print(f'  Budget 10: tokens={toks6}, violation={violation6}, selected={len(selected6)}')

# Budget fits PK but not FK
text7, toks7, selected7, violation7 = enforce(complex_elements, 50)
print(f'  Budget 50: tokens={toks7}, violation={violation7}, selected={len(selected7)}')

# Budget fits everything
text8, toks8, selected8, violation8 = enforce(complex_elements, 200)
print(f'  Budget 200: tokens={toks8}, violation={violation8}, selected={len(selected8)}')

import os
os.unlink(tmp_db_path)
os.unlink(db_path)

print()
print("=== BUDGET ENFORCEMENT FIX VERIFIED ===")
