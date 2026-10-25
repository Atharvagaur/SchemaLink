import sys
sys.path.insert(0, '.')

from schemalink.budget import count_tokens, serialise, enforce
from schemalink.indexer import SchemaElement

print('=== DEBUGGING THE BUDGET ALGORITHM ===')
print()

# Create a simple test case
pk_element = SchemaElement('users', 'id', 'INTEGER PRIMARY KEY')
print(f'PK element: {pk_element.element_id}, PK: {pk_element.is_pk}')
print()

# Let's manually trace through the algorithm
ranked = [pk_element]
budget = 0

print(f'Ranked elements: {[e.element_id for e in ranked]}')
print(f'Budget: {budget}')
print()

# Step 1: Group by table
table_order = []
table_elems = {}
for e in ranked:
    if e.table not in table_elems:
        table_elems[e.table] = []
        table_order.append(e.table)
    table_elems[e.table].append(e)

print(f'table_order: {table_order}')
print(f'table_elems keys: {list(table_elems.keys())}')
print(f'table_elems[users]: {[e.element_id for e in table_elems[table_order[0]]]}')
print()

# Step 2: Initialize selected and violation
selected = []
violation = False
print(f'Initial selected: {selected}')
print(f'Initial violation: {violation}')
print()

# Step 3: Try each table
for tname in table_order:
    print(f'Processing table: {tname}')
    print(f'  table_elems[{tname}]: {[e.element_id for e in table_elems[tname]]}')
    
    # Calculate trial_elems with mandatory columns
    from schemalink.budget import _grow_with_mandatory
    trial_elems = _grow_with_mandatory(selected, table_elems, table_order, tname, None)
    print(f'  trial_elems: {[e.element_id for e in trial_elems]}')
    
    text = serialise(trial_elems, None)
    toks = count_tokens(text)
    print(f'  trial text: {text}')
    print(f'  trial tokens: {toks}')
    
    if toks <= budget:
        print(f'  ✓ Fits in budget ({budget})')
        selected = trial_elems
        print(f'  ✓ Selected: {[e.element_id for e in selected]}')
    else:
        print(f'  X Exceeds budget ({budget})')
        
        # Try to add mandatory columns one by one
        mandatory_only = list(selected)
        added_mandatory = False
        for e in table_elems[tname]:
            if e.is_table or e.is_pk or e.fk_target:
                trial2 = mandatory_only + [e]
                from schemalink.budget import _ensure_mandatory
                trial2 = _ensure_mandatory(trial2, table_elems, None)
                t2 = serialise(trial2, None)
                toks2 = count_tokens(t2)
                print(f'    Trying mandatory element {e.element_id}: tokens={toks2}')
                
                if toks2 <= budget:
                    mandatory_only = trial2
                    added_mandatory = True
                    print(f'    ✓ Fits mandatory element')
                else:
                    print(f'    X Exceeds budget, stopping')
                    break
        
        if added_mandatory:
            selected = _ensure_mandatory(mandatory_only, table_elems, None)
            print(f'  ✓ Added mandatory elements: {[e.element_id for e in selected]}')
            
            # Try to add remaining ranked columns one by one
            for e in table_elems[tname]:
                if e in selected:
                    continue
                trial3 = selected + [e]
                t3 = serialise(trial3, None)
                toks3 = count_tokens(t3)
                print(f'    Trying additional element {e.element_id}: tokens={toks3}')
                
                if toks3 <= budget:
                    selected = trial3
                    print(f'    ✓ Added element')
                else:
                    print(f'    X Exceeds budget, stopping')
                    break
        else:
            print(f'  X Cannot add mandatory elements, continuing to next table')
            # Don't break - continue to next table
            pass

print()
print(f'After loop processing: selected={[e.element_id for e in selected]}')
print(f'After loop processing: violation={violation}')
print()

# Step 4: If nothing fits at all
if not selected and table_order:
    print('Executing "If nothing fits at all" block')
    top = table_order[0]
    mandatory = [e for e in table_elems[top]
                 if e.is_table or e.is_pk or e.fk_target]
    print(f'  top table: {top}')
    print(f'  mandatory elements: {[e.element_id for e in mandatory]}')
    
    text = serialise(mandatory, None)
    if count_tokens(text) > budget:
        violation = True
    selected = mandatory
    print(f'  After mandatory selection: selected={[e.element_id for e in selected]}')
    print(f'  After mandatory selection: violation={violation}')
else:
    print(f'Skipping "If nothing fits at all" block: selected={[e.element_id for e in selected]}')

print()

# Step 5: Final check
if not selected:
    violation = True
    print(f'Final check: selected is empty, setting violation={violation}')

print(f'Final result: selected={[e.element_id for e in selected]}, violation={violation}')
print()

# Now run the actual enforce function to compare
print('=== RUNNING ACTUAL ENFORCE FUNCTION ===')
text_result, toks_result, selected_result, violation_result = enforce(ranked, budget)
print(f'Result: tokens={toks_result}, violation={violation_result}')
print(f'Result selected: {[e.element_id for e in selected_result]}')
print(f'Result text: {text_result}')
print()
print(f'Manual trace matches enforce: {violation == violation_result and len(selected) == len(selected_result)}')
