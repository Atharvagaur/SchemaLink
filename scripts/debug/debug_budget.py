import sys
sys.path.insert(0, '.')

from schemalink.budget import count_tokens, serialise, enforce
from schemalink.indexer import SchemaElement, extract_elements

print('=== DETAILED BUDGET ANALYSIS ===')
print()

# Create a database with sample structure
db_path = 'test.sqlite'
with open(db_path, 'w') as f:
    pass

# Create a schema with a PK and FK
schema_meta = {
    'table_names_original': ['users', 'orders'],
    'column_names_original': [[0, ''], [0, 'id'], [0, 'name'], [1, 'id'], [1, 'user_id']],
    'column_types': ['', 'INTEGER PRIMARY KEY', 'TEXT', 'INTEGER PRIMARY KEY', 'INTEGER'],
    'primary_keys': [1, 3],
    'foreign_keys': [[4, 1]],
}

# Extract elements
import tempfile
with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
    tmp_db_path = tmp.name

# Create minimal DB for testing
import sqlite3
conn = sqlite3.connect(tmp_db_path)
conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)")
conn.commit()
conn.close()

elements = extract_elements(schema_meta, tmp_db_path)
print(f'Total elements: {len(elements)}')
for e in elements:
    print(f'  {e.element_id}: table={e.table}, column={e.column}, pk={e.is_pk}, fk={e.fk_target}')
print()

# Test 1: Budget exactly matching PK
pk_elements = [e for e in elements if e.is_pk]
print('Test 1: Budget exactly matching PK cost')
text1, toks1, selected1, violation1 = enforce(elements, toks1)
print(f'Budget: {toks1}')
print(f'Returned tokens: {toks1}')
print(f'Violation: {violation1}')
print(f'Selected: {[e.element_id for e in selected1]}')
print()

# Test 2: Check if the 'If nothing fits' logic is being executed
print('Test 2: Debug - checking if logic is executed')
print(f'Length of elements: {len(elements)}')
print(f'Tables in table_order: {list(set(e.table for e in elements))}')
print()

# Let's add some debug to the budget.py function
print('=== MANUAL DEBUG ===')
print('For budget = 0:')

# Create a simple elements list for debugging
simple_elements = [
    SchemaElement('test', 'id', 'INTEGER PRIMARY KEY'),
]

print(f'Elements: {len(simple_elements)}')
for e in simple_elements:
    print(f'  {e.element_id}: pk={e.is_pk}')

print(f'Budget: 0')
text_debug, toks_debug, selected_debug, violation_debug = enforce(simple_elements, 0)
print(f'Result: tokens={toks_debug}, violation={violation_debug}, selected={len(selected_debug)}')
print()

# The expected behavior:
# 1. table_order = ['test']
# 2. table_elems['test'] = [SchemaElement('test', 'id', 'INTEGER PRIMARY KEY')]
# 3. selected = [], violation = False
# 4. Loop: tname = 'test'
# 5. trial_elems = _grow_with_mandatory([], [{'test': [SchemaElement(...)]}, 'test', SchemaElement(...)])
# 6. text = serialise([SchemaElement('test', 'id', 'INTEGER PRIMARY KEY')])
# 7. toks = count_tokens(text) = 11
# 8. toks (11) > budget (0), so go to else block
# 9. mandatory_only = []
# 10. Loop through table_elems['test']
# 11. e = SchemaElement('test', 'id', 'INTEGER PRIMARY KEY')
# 12. e.is_table or e.is_pk or e.fk_target = True
# 13. trial2 = [] + [SchemaElement(...)]
# 14. trial2 = _ensure_mandatory([SchemaElement(...)], ...)
# 15. t2 = serialise([SchemaElement(...)])
# 16. toks2 = count_tokens(t2) = 11
# 17. toks2 (11) > budget (0), so break
# 18. added_mandatory = False
# 19. else: violation = True
# 20. The if not selected and table_order check never runs because we're still inside the loop
# 21. End of loop
# 22. If not selected and table_order: # Should be true but never runs
# 23. Actually wait, this should run AFTER the loop ends
# 24. But the "if not selected and table_order" block is inside the loop, not outside!

print('CONCLUSION: The "if not selected and table_order" block is INSIDE the for loop, not outside!')
print('This means it only runs ONCE per table, not once after the loop ends.')
print('When we break out of the loop at line 183, we never reach the rest of the loop!')
print()
print('The fix is to make sure the "if not selected and table_order" block is OUTSIDE the for loop.')
