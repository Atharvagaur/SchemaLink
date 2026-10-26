import sys
sys.path.insert(0, '.')

from schemalink.budget import count_tokens, serialise, enforce
from schemalink.indexer import SchemaElement

# Test the budget enforcement issue
print('=== BUG CONFIRMATION: Budget Enforcement Issue ===')
print()

# Test case: Very small budget but with PK
elements = [
    SchemaElement('users', 'id', 'INTEGER PRIMARY KEY'),
    SchemaElement('users', 'name', 'TEXT', sample_values=['Alice', 'Bob', 'Charlie']),
]

print('Test: Budget = 5 (should fit PK but not the sample values)')
text, toks, selected, violation = enforce(elements, 5)

print(f'Budget: 5')
print(f'Returned tokens: {toks}')
print(f'Violation: {violation}')
print(f'Selected elements: {[e.element_id for e in selected]}')

# Check what should be selected (just the PK)
expected_text = serialise([elements[0]])  # Just the PK
expected_toks = count_tokens(expected_text)

print(f'Expected tokens for PK only: {expected_toks}')
print(f'Expected text: {expected_text}')
print()

# Now test with a reasonable budget
print('Test: Budget = 50 (should fit PK + sample values)')
text2, toks2, selected2, violation2 = enforce(elements, 50)

print(f'Budget: 50')
print(f'Returned tokens: {toks2}')
print(f'Violation: {violation2}')
print(f'Selected elements: {[e.element_id for e in selected2]}')
print(f'Selected text: {text2}')
print()

# Verify the returned tokens match the actual serialized text
text2_recomputed = serialise(selected2)
toks2_recomputed = count_tokens(text2_recomputed)
print(f'Verification:')
print(f'Returned tokens: {toks2}')
print(f'Recomputed tokens: {toks2_recomputed}')
print(f'Match: {toks2 == toks2_recomputed}')
print()

# Test the empty budget case
print('=== CRITICAL ISSUE: Empty Budget Handling ===')
elements3 = [SchemaElement('users', 'id', 'INTEGER PRIMARY KEY')]
text3, toks3, selected3, violation3 = enforce(elements3, 0)

print(f'Budget: 0')
print(f'Returned tokens: {toks3}')
print(f'Violation: {violation3}')
print(f'Selected elements: {[e.element_id for e in selected3]}')

# Even with budget 0, we should at least be able to select the PK
# The PK "id INTEGER PRIMARY KEY" should be minimal enough to fit in 0 tokens
# If it doesn't fit, that's a violation
pk_text = serialise([elements3[0]])
pk_tokens = count_tokens(pk_text)
print(f'PK text: {pk_text}')
print(f'PK tokens: {pk_tokens}')
print(f'Should fit in 0 tokens: {pk_tokens <= 0}')
print(f'Selected: {selected3}')
print(f'Should have selected PK if pk_tokens <= 0: {len(selected3) > 0}')
