import sys
sys.path.insert(0, '.')

from schemalink.budget import count_tokens, serialise, enforce
from schemalink.indexer import SchemaElement

# Test if the tokenizer is consistent
elements = [
    SchemaElement('users', 'id', 'INTEGER PRIMARY KEY'),
    SchemaElement('users', 'name', 'TEXT'),
    SchemaElement('posts', 'id', 'INTEGER PRIMARY KEY'),
    SchemaElement('posts', 'user_id', 'INTEGER'),
]

text = serialise(elements)
token_count = count_tokens(text)
print('Test 1: Token count consistency')
print(f'Serialized schema length: {len(text)}')
print(f'Token count: {token_count}')
print()

# Test with matched values
elements2 = [
    SchemaElement('users', 'id', 'INTEGER PRIMARY KEY'),
    SchemaElement('users', 'name', 'TEXT'),
]
text2 = serialise(elements2, {'users.id': '123'})
print('Test 2: Matched values annotation')
print(f'Serialized with matched value: {text2}')
print(f'Token count: {count_tokens(text2)}')
print()

# Test budget enforcement
ranked = elements
text3, toks3, selected, violation = enforce(ranked, 50)
print('Test 3: Budget enforcement')
print(f'Budget: 50, Actual tokens: {toks3}, Violation: {violation}')
print(f'Exceed budget: {toks3 > 50}')
print()

# Test exact fit
text4, toks4, selected4, violation4 = enforce(ranked, toks3)
print('Test 4: Exact fit budget')
print(f'Budget: {toks3}, Actual tokens: {toks4}, Violation: {violation4}')
print(f'Exceed budget: {toks4 > toks3}')
print()

# Test PK/FK retention
from schemalink.indexer import extract_elements
db_path = 'D:\DBMS\Project\schemalink\tests\test.sqlite'
elements_with_fk = extract_elements(
    {'table_names_original': ['a', 'b'],
     'column_names_original': [[0, ''], [0, 'id'], [1, 'id'], [1, 'a_id']],
     'column_types': ['', 'INTEGER', 'INTEGER', 'INTEGER'],
     'primary_keys': [1, 2],
     'foreign_keys': [[2, 1]]},
    db_path
)
print('Test 5: PK/FK retention')
for e in elements_with_fk:
    print(f'  {e.element_id}: table={e.table}, column={e.column}, pk={e.is_pk}, fk={e.fk_target}')
text5, toks5, selected5, violation5 = enforce(elements_with_fk, 100)
print(f'Budget: 100, Tokens: {toks5}, Violation: {violation5}')
print()