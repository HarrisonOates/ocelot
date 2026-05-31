# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.

import re
from collections import defaultdict
import sys
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='htnorder_debug.log',
    filemode='w'
)

# These are utilities to extract info from HTN traces and HDDL files.

def parse_htn_trace(trace_content):
    """
    Parses the HTN output trace to extract primitive actions and the full
    task decomposition hierarchy, including task names and parameters.
    """
    lines = [line.strip() for line in trace_content.split('\n') if line.strip()]

    try:
        trace_start_index = lines.index('==>') + 1
        root_line_index = next(i for i, line in enumerate(lines) if line.startswith('root'))
    except (ValueError, StopIteration):
        raise ValueError("Invalid trace format: '==>' or 'root' line not found.")
    
    primitive_actions = {}
    for i in range(trace_start_index, root_line_index):
        parts = lines[i].split()
        task_id = int(parts[0])
        primitive_actions[task_id] = {'name': parts[1], 'params': parts[2:]}

    hierarchy = defaultdict(dict)
    decompositions = {}

    root_line = lines[root_line_index].split()
    hierarchy['root']['children'] = [int(x) for x in root_line[1:]]

    for i in range(root_line_index + 1, len(lines)):
        line = lines[i]
        parent_part, children_part = [part.strip() for part in line.split('->')]
        
        parent_info = parent_part.split()
        parent_id = int(parent_info[0])
        
        children_info = children_part.split()
        method_name = children_info[0]
        children_ids = [int(x) for x in children_info[1:]]
        
        hierarchy[parent_id]['method'] = method_name
        hierarchy[parent_id]['children'] = children_ids
        
        # Store the full definition of the abstract task
        decompositions[parent_id] = {'name': parent_info[1], 'params': parent_info[2:]}

    return primitive_actions, hierarchy, decompositions

def parse_hddl(hddl_content):
    """
    A HDDL parser
    """
    # Remove comments first
    hddl_content = re.sub(r';.*', '', hddl_content)
    
    # Pad parentheses with spaces to ensure they are treated as separate tokens
    tokenized_string = hddl_content.replace('(', ' ( ').replace(')', ' ) ').split()
    
    def build_list(tokens):
        if not tokens:
            raise SyntaxError("Unexpected EOF while parsing")
        token = tokens.pop(0)
        if token == '(':
            new_list = []
            while tokens[0] != ')':
                new_list.append(build_list(tokens))
            tokens.pop(0) # Pop off ')'
            return new_list
        elif token == ')':
            raise SyntaxError("Unexpected ')'")
        else:
            return token

    return build_list(tokenized_string)


def get_problem_constraints(problem_hddl, hierarchy, decompositions):
    """
    Extracts top-level ordering constraints from the problem file's :htn block.
    """
    try:
        parsed_problem = parse_hddl(problem_hddl)
        htn_block = next((item for item in parsed_problem if isinstance(item, list) and item and item[0] == ':htn'), None)
        if not htn_block:
            logging.warning("Could not find :htn block in problem file.")
            return []

        # --- Convert the flat key-value list into a dictionary for easy access ---
        htn_dict = {}
        i = 1 # Start after ':htn'
        while i + 1 < len(htn_block):
            key = htn_block[i]
            value = htn_block[i+1]
            if isinstance(key, str) and key.startswith(':'):
                htn_dict[key] = value
            i += 2
        logging.debug(f"Parsed :htn block into dictionary: {htn_dict.keys()}")

        # --- 1. Extract Task Definitions from the problem file ---
        subtasks_keywords = [':subtasks', ':tasks', ':ordered-subtasks', ':ordered-tasks']
        subtasks_block_content = None
        for key in subtasks_keywords:
            if key in htn_dict:
                subtasks_block_content = htn_dict[key]
                logging.debug(f"Successfully found subtasks block using key '{key}'")
                break
        
        if not subtasks_block_content:
            logging.error("CRITICAL: Could not find any valid subtasks keyword (e.g., :subtasks) in the :htn block dictionary.")
            return []

        # The content is the value from the dictionary
        task_defs_list = subtasks_block_content
        if isinstance(task_defs_list, list) and task_defs_list and task_defs_list[0] == 'and':
            task_defs = task_defs_list[1:]
        else:
            task_defs = [task_defs_list]
        
        problem_task_defs = {}
        for i, task_def in enumerate(task_defs):
            if isinstance(task_def, list) and len(task_def) > 0:
                # Check if this uses labeled tasks (task0 (get_soil_data waypoint2))
                # or unlabeled tasks (rows n0)
                if len(task_def) > 1 and isinstance(task_def[1], list):
                    # Labeled format: (task0 (rows n0))
                    task_name = task_def[0]
                    task_details = task_def[1]
                    problem_task_defs[task_name] = {'name': task_details[0], 'params': task_details[1:]}
                else:
                    # Unlabeled format: (rows n0)
                    # Generate a synthetic label based on position since we need unique identifiers
                    task_name = f"__task{i}"
                    problem_task_defs[task_name] = {'name': task_def[0], 'params': task_def[1:]}
        logging.debug(f"Found problem task definitions: {problem_task_defs}")

        # --- 2. Map Problem Task Names to Trace Task IDs ---
        task_name_to_id = {}
        for trace_task_id in hierarchy['root']['children']:
            if trace_task_id in decompositions:
                trace_task_def = decompositions[trace_task_id]
                for prob_task_name, prob_task_def in problem_task_defs.items():
                    if prob_task_def['name'] == trace_task_def['name'] and prob_task_def['params'] == trace_task_def['params']:
                        task_name_to_id[prob_task_name] = trace_task_id
                        logging.debug(f"Matched problem task '{prob_task_name}' to trace task ID {trace_task_id}")
                        break
        
        if not task_name_to_id:
            logging.warning("Could not match any problem tasks to trace tasks. Cannot process orderings.")
            return []

        # --- 3. Extract and Resolve Ordering Constraints ---
        if ':ordering' not in htn_dict:
            logging.info("No :ordering block found in :htn block.")
            return []

        constraints = []
        ordering_list = htn_dict[':ordering']
        if isinstance(ordering_list, list) and ordering_list and ordering_list[0] == 'and':
            ordering_defs = ordering_list[1:]
        else:
            ordering_defs = [ordering_list]

        for const in ordering_defs:
            if isinstance(const, list) and len(const) > 2 and const[0] == '<':
                task1_name, task2_name = const[1], const[2]
                if task1_name in task_name_to_id and task2_name in task_name_to_id:
                    task1_id = task_name_to_id[task1_name]
                    task2_id = task_name_to_id[task2_name]
                    constraints.append((task1_id, task2_id))
                    logging.debug(f"Resolved ordering: '{task1_name}' < '{task2_name}' as task IDs {task1_id} < {task2_id}")
                else:
                    logging.warning(f"Could not resolve ordering '{task1_name}' < '{task2_name}'. One or both names not matched to a trace ID.")
        
        return constraints

    except Exception as e:
        logging.error(f"An unexpected error occurred in get_problem_constraints: {e}", exc_info=True)
        return []

def get_domain_constraints(domain_hddl):
    """
    Extracts ordering constraints from each method
    """
    parsed_domain = parse_hddl(domain_hddl)
    
    methods = [item for item in parsed_domain if isinstance(item, list) and item and item[0] == ':method']
    
    method_constraints = {}
    for method in methods:
        method_name = method[1]
        constraints = []
        
        # Safely iterate through the method's keyword-value pairs
        i = 2 # Start after ':method' and its name
        while i + 1 < len(method):
            keyword = method[i]
            value = method[i+1]

            # Handle totally-ordered subtasks
            if keyword in [':ordered-subtasks', ':ordered-tasks']:
                task_list = value[1:] if isinstance(value, list) and value and value[0] == 'and' else [value]
                if len(task_list) > 1:
                    constraints.extend([(j, j + 1) for j in range(len(task_list) - 1)])
                break # Found subtask block for this method

            # Handle partially-ordered subtasks
            elif keyword in [':subtasks', ':tasks']:
                task_list = value[1:] if isinstance(value, list) and value and value[0] == 'and' else [value]
                task_name_map = {task_def[0]: j for j, task_def in enumerate(task_list) if isinstance(task_def, list) and len(task_def) > 1}
                try:
                    order_idx = method.index(':ordering')
                    ordering_block = method[order_idx + 1]
                    ordering_defs = ordering_block[1:] if isinstance(ordering_block, list) and ordering_block and ordering_block[0] == 'and' else [ordering_block]
                    for const in ordering_defs:
                        if isinstance(const, list) and len(const) > 2 and const[0] == '<' and const[1] in task_name_map and const[2] in task_name_map:
                            constraints.append((task_name_map[const[1]], task_name_map[const[2]]))
                except (ValueError, IndexError):
                    pass # No ordering block or malformed
                break # Found subtask block for this method
            
            i += 2

        method_constraints[method_name] = constraints
        
    return method_constraints

def get_all_primitive_subtasks(task_id, hierarchy, primitive_actions):
    """Recursively finds all primitive subtasks of a given task."""
    if task_id in primitive_actions:
        return {task_id}
    if task_id not in hierarchy or not hierarchy[task_id].get('children'):
        return set()
    
    all_subtasks = set()
    for child_id in hierarchy[task_id]['children']:
        all_subtasks.update(get_all_primitive_subtasks(child_id, hierarchy, primitive_actions))
    return all_subtasks

def find_implied_constraints(htn_trace, domain_file, problem_file):
    """Main function to parse files and find all implied ordering constraints."""
    logging.info("--- Starting find_implied_constraints ---")
    primitive_actions, hierarchy, decompositions = parse_htn_trace(htn_trace)
    logging.info(f"Parsed trace. Found {len(primitive_actions)} primitive actions.")
    problem_constraints = get_problem_constraints(problem_file, hierarchy, decompositions)
    logging.info(f"Parsed problem file. Found {len(problem_constraints)} top-level constraints: {problem_constraints}")
    domain_constraints = get_domain_constraints(domain_file)
    logging.info("Parsed domain file for method constraints.")

    
    
    all_constraints = set()

    logging.info("Processing problem-level (top-level) constraints...")
    #Add problem-level constraints
    for task1, task2 in problem_constraints:
        logging.debug(f"Processing constraint: abstract task {task1} -> abstract task {task2}")
        
        subtasks1 = get_all_primitive_subtasks(task1, hierarchy, primitive_actions)
        subtasks2 = get_all_primitive_subtasks(task2, hierarchy, primitive_actions)
        logging.debug(f"  > Primitives for task {task1}: {subtasks1}")
        logging.debug(f"  > Primitives for task {task2}: {subtasks2}")
        if not subtasks1 or not subtasks2:
            logging.warning(f"  > One or both subtask sets are empty. No constraints will be generated for this pair.")

        for s1 in subtasks1:
            for s2 in subtasks2:
                all_constraints.add((s1, s2))
        
        logging.info(f"Generated {len(all_constraints)} constraints from top-level so far.")


    # Add method-level constraints
    logging.info("Processing method-level constraints...")
    method_constraint_count = 0
    skipped_precondition_constraints = 0
    
    for parent_id, decomp in hierarchy.items():
        if parent_id == 'root' or 'method' not in decomp: continue
        
        method_name = decomp['method']
        children = decomp['children']
        
        if method_name in domain_constraints:
            for idx1, idx2 in domain_constraints[method_name]:
                if idx1 < len(children) and idx2 < len(children):
                    child1 = children[idx1]
                    child2 = children[idx2]
                    
                    subtasks1 = get_all_primitive_subtasks(child1, hierarchy, primitive_actions)
                    subtasks2 = get_all_primitive_subtasks(child2, hierarchy, primitive_actions)
                    
                    # Only add constraints for explicitly ordered pairs in the HTN
                    # ALL primitives in subtask1 must come before ALL primitives in subtask2
                    for s1 in subtasks1:
                        for s2 in subtasks2:
                            # Skip constraints involving SHOP-style method precondition tasks
                            # These are tasks with names like "SHOP_methodm_<name>_precondition"
                            # They are compiled preconditions, not real ordering constraints
                            action1_info = primitive_actions.get(s1, {})
                            action2_info = primitive_actions.get(s2, {})
                            action1_name = action1_info.get('name', '') if isinstance(action1_info, dict) else ''
                            action2_name = action2_info.get('name', '') if isinstance(action2_info, dict) else ''
                            
                            if '_precondition' in action1_name or '_precondition' in action2_name:
                                skipped_precondition_constraints += 1
                                logging.debug(f"Skipping precondition constraint: ({s1}, {s2}) with names ({action1_name}, {action2_name})")
                                continue
                            
                            if (s1, s2) not in all_constraints:
                                all_constraints.add((s1, s2))
                                method_constraint_count += 1
    
    logging.info(f"Added {method_constraint_count} new constraints from method decompositions.")
    logging.info(f"Skipped {skipped_precondition_constraints} constraints involving precondition tasks.")

    # NOTE: We do NOT compute transitive closure here!
    # The HTN constraints should only enforce what's explicitly stated in the methods.
    # The MaxSAT encoding will handle transitivity through its Order predicate logic.
    logging.info("Skipping transitive closure to avoid over-constraining the problem.")
    final_constraints = sorted(list(all_constraints))
    logging.info(f"Finished. Total constraints without transitive closure: {len(final_constraints)}")
    logging.debug(f"Final constraint list: {final_constraints}")
    return final_constraints

def get_initial_state(problem_hddl_content: str) -> list:
    """
    Parses a HDDL problem file string and extracts the initial state.

    The initial state is returned as a list of positive literals, where each
    literal is itself a list of strings.
    
    Args:
        problem_hddl_content: A string containing the HDDL problem definition.

    Returns:
        A list of lists representing the positive literals in the initial state.
        Returns an empty list if the :init block is not found or is empty.
    """
    try:
        parsed_problem = parse_hddl(problem_hddl_content)
    except SyntaxError as e:
        print(f"Error parsing HDDL file: {e}", file=sys.stderr)
        return []

    # Safely find the :init block within the parsed structure
    init_block = next((item for item in parsed_problem if isinstance(item, list) and item and item[0] == ':init'), None)

    if not init_block or len(init_block) < 2:
        return []  # No :init block found or it's empty

    initial_state = []
    # The actual predicates start from the second element of the block
    literals = init_block[1:]
    
    for literal in literals:
        # A positive literal is a list, e.g., ['at', 'rover0', 'waypoint3']
        # A negative literal would be ['not', ['at', ...]], which we ignore
        # under the closed-world assumption.
        if isinstance(literal, list) and literal and literal[0] != 'not':
            initial_state.append(literal)
            
    return initial_state

def get_classical_problem_components(domain_pddl_content: str) -> dict:
    """Parses a HDDL domain to extract action schemas."""
    
    def _make_hashable_recursively(item):
        """Recursively converts lists within a structure to tuples."""
        if isinstance(item, list):
            return tuple(_make_hashable_recursively(sub_item) for sub_item in item)
        return item

    try:
        parsed_domain = parse_hddl(domain_pddl_content)
    except SyntaxError:
        return {"actions": {}}

    action_definitions = {}
    action_blocks = [item for item in parsed_domain if isinstance(item, list) and item and item[0] == ':action']

    for block in action_blocks:
        action_name = block[1]
        preconditions, add_effects, delete_effects, parameters = set(), set(), set(), []
        i = 2
        while i + 1 < len(block):
            keyword, value = block[i], block[i+1]
            if keyword == ':parameters':
                parameters = value
            elif keyword == ':precondition':
                literals = value[1:] if isinstance(value, list) and value and value[0] == 'and' else [value]
                preconditions.update(
                    _make_hashable_recursively(lit) for lit in literals if isinstance(lit, list) and lit
                )
            elif keyword == ':effect':
                literals = value[1:] if isinstance(value, list) and value and value[0] == 'and' else [value]
                for lit in literals:
                    if isinstance(lit, list) and lit:
                        # This logic correctly handles 'not' for effects, so we leave it.
                        if lit[0] == 'not' and isinstance(lit[1], list):
                            delete_effects.add(tuple(lit[1]))
                        else:
                            add_effects.add(tuple(lit))
            i += 2
        
        action_definitions[action_name] = {
            "parameters": parameters,
            "preconditions": preconditions,
            "add_effects": add_effects,
            "delete_effects": delete_effects
        }
    return {"actions": action_definitions}


def get_grounded_actions_from_trace(domain_pddl_content: str, trace_content: str) -> dict:
    """
    Grounds the primitive actions found in an HTN trace.

    For each action instance in the trace, it substitutes the parameters from
    the trace into the action's schema (preconditions, effects) from the domain.

    Args:
        domain_pddl_content: The string content of the PDDL domain file.
        trace_content: The string content of the HTN execution trace.

    Returns:
        A dictionary mapping each action ID from the trace to its fully
        grounded definition. E.g.:
        {
            173: {
                'name': 'navigate',
                'parameters': ('rover0', 'waypoint3', 'waypoint1'),
                'preconditions': {('at', 'rover0', 'waypoint3'), ...},
                'add_effects': {('at', 'rover0', 'waypoint1')},
                'delete_effects': {('at', 'rover0', 'waypoint3')}
            }, ...
        }
    """
    
    # Parse the action schemas from the domain file.
    action_schemas = get_classical_problem_components(domain_pddl_content)["actions"]
    
    # Parse the executed primitive actions from the trace.
    executed_actions, _, _ = parse_htn_trace(trace_content)
    
    grounded_actions = {}

    # For each executed action, match and substitute.
    for action_id, trace_info in executed_actions.items():
        action_name = trace_info['name']
        concrete_params = trace_info['params']
        
        # Find the corresponding schema for this action name.
        if action_name not in action_schemas:
            #print(f"Warning: Action '{action_name}' from trace not found in domain. Skipping.", file=sys.stderr)
            continue
            
        schema = action_schemas[action_name]
        
        # Extract only the variable names (e.g., '?x') from the parameter list.
        schema_vars = [p for p in schema['parameters'] if p.startswith('?')]
        
        if len(schema_vars) != len(concrete_params):
            # print(f"Warning: Parameter count mismatch for action '{action_name}'. Skipping.", file=sys.stderr)
            continue
            
        # Create the mapping from variable to concrete object.
        variable_mapping = dict(zip(schema_vars, concrete_params))
        
        def _substitute(predicate_template: tuple) -> tuple:
            """Substitutes variables in a predicate tuple using the mapping."""
            return tuple(variable_mapping.get(term, term) for term in predicate_template)

        # Apply the substitution to generate grounded effects.
        grounded_preconditions = {_substitute(p) for p in schema['preconditions']}
        grounded_add_effects = {_substitute(a) for a in schema['add_effects']}
        grounded_delete_effects = {_substitute(d) for d in schema['delete_effects']}
        
        grounded_actions[action_id] = {
            'name': action_name,
            'parameters': tuple(concrete_params),
            'preconditions': grounded_preconditions,
            'add_effects': grounded_add_effects,
            'delete_effects': grounded_delete_effects
        }
        
    return grounded_actions


if __name__ == "__main__":
    pass