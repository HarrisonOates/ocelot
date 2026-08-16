# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.

import re
import sys
import os
import json
import argparse
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import htnorder

import tarskilite as tl
import encoder as encoder
import pop as pop_module
from pop import POP
import networkx as nx

class Action:
    """A simple class to hold the components of a grounded STRIPS action, with unique instance id."""
    def __init__(self, name: str, pres: set, adds: set, dels: set, instance_id=None):
        self.name = name
        self.pres = pres
        self.adds = adds
        self.dels = dels
        self.instance_id = instance_id  # Unique per occurrence

    def __repr__(self):
        return f"{self.name}#{self.instance_id}" if self.instance_id is not None else self.name

    def __str__(self):
        return self.__repr__()

    def __hash__(self):
        # Use instance_id to distinguish occurrences
        return hash((self.name, self.instance_id))

    def __eq__(self, other):
        return (
            isinstance(other, Action)
            and self.name == other.name
            and self.instance_id == other.instance_id
        )

def lift_plan_to_pop(domain_content: str, problem_content: str, trace_content: str) -> POP:
    """
    The main function to generate a complete POP from HTN files.

    This function first builds a causally-sound POP from the linear action trace,
    then augments it with the mandatory ordering constraints from the HTN hierarchy.
    """

    # --- Part 1: Parse all necessary data ---
    print("Parsing files...")
    grounded_actions_map = htnorder.get_grounded_actions_from_trace(domain_content, trace_content)
    # Includes the closed-world `-predicate` facts that compiled-away negative
    # preconditions depend on, so those preconditions have a producer.
    initial_state = htnorder.get_initial_state_with_negatives(problem_content, grounded_actions_map)
    htn_constraints = htnorder.find_implied_constraints(trace_content, domain_content, problem_content)
    
    # The trace gives us a linear sequence of action IDs. We must process them in this order.
    linear_trace_ids = sorted(grounded_actions_map.keys())

    # --- Part 2: Build the causally-correct POP based on the linear trace ---
    print("Building POP from linear trace and causal analysis...")
    pop = POP()
    
    # Create a map from action ID to our Action object
    id_to_action_obj = {}


    # Create and add the special 'init' action
    init_action = Action("init", set(), initial_state, set(), instance_id="init")
    pop.add_action(init_action)
    pop.init = init_action

    # Create Action objects for every action in the trace
    plan_actions = []

    for idx, action_id in enumerate(linear_trace_ids):
        details = grounded_actions_map[action_id]
        full_name = f"({details['name']} {' '.join(details['parameters'])})"
        # Use a unique instance_id for each occurrence (e.g., index in plan or action_id)
        act_obj = Action(
            name=full_name,
            pres={tuple(p) for p in details['preconditions']},
            adds={tuple(a) for a in details['add_effects']},
            dels={tuple(d) for d in details['delete_effects']},
            instance_id=action_id
        )

        pop.add_action(act_obj)
        id_to_action_obj[action_id] = act_obj
        plan_actions.append(act_obj)

    # Create and add the special 'goal' action
    # Assume goal is empty as HTN problems don't require a specific goal state
    goal_action = Action("goal", set(), set(), set(), instance_id="goal")
    pop.add_action(goal_action)
    pop.goal = goal_action
    print(pop.network.nodes)
    

    # The full linear sequence of action objects, including init and goal
    linear_plan_objects = [init_action] + [id_to_action_obj[aid] for aid in linear_trace_ids] + [goal_action]

    # Safeguard: check for None in linear_plan_objects
    if any(a is None for a in linear_plan_objects):
        print("WARNING: None action found in linear_plan_objects!", file=sys.stderr)
        for idx, a in enumerate(linear_plan_objects):
            if a is None:
                print(f"  None at index {idx} (action_id: {linear_trace_ids[idx-1] if 0 < idx-1 < len(linear_trace_ids) else 'init/goal'})", file=sys.stderr)


    # Build adder/deleter dictionaries
    all_fluents = initial_state.union(*[a.pres | a.adds | a.dels for a in linear_plan_objects if a is not None])
    adders = {f: set() for f in all_fluents}
    for i, action in enumerate(linear_plan_objects):
        if action is None:
            print(f"WARNING: Skipping None action at index {i} in adder analysis", file=sys.stderr)
            continue
        for f in action.adds:
            adders[f].add(i) # Store index in linear plan


    # Perform causal link analysis based on the linear order
    for i, consumer in enumerate(linear_plan_objects):
        if consumer is None:
            print(f"WARNING: Skipping None consumer at index {i} in causal link analysis", file=sys.stderr)
            continue
        if i == 0:
            continue # Skip init action
        for p in consumer.pres:
            # Find the LAST producer of p that comes before the consumer in the linear plan
            last_producer_idx = -1
            for producer_idx in adders.get(p, set()):
                if producer_idx < i and producer_idx > last_producer_idx:
                    last_producer_idx = producer_idx
            if last_producer_idx == -1:
                # This should not happen in a valid plan trace.
                print(f"FATAL: No producer found for precondition '{p}' of action '{consumer}'", file=sys.stderr)
                continue
            producer = linear_plan_objects[last_producer_idx]
            if producer is None:
                print(f"WARNING: Producer is None at index {last_producer_idx} for precondition '{p}' of consumer '{consumer}'", file=sys.stderr)
                continue
            pop.link_actions(producer, p, consumer)

    # --- Part 3: Augment the POP with the mandatory HTN ordering constraints ---
    print(f"Augmenting POP with {len(htn_constraints)} mandatory HTN constraints...")
    htn_order_edges = set()
    for act_id1, act_id2 in htn_constraints:
        if act_id1 in id_to_action_obj and act_id2 in id_to_action_obj:
            action1 = id_to_action_obj[act_id1]
            action2 = id_to_action_obj[act_id2]
            # Add the link if it doesn't already exist to avoid clutter
            if not pop.network.has_edge(action1, action2):
                pop.link_actions(action1, "htn_order", action2)
                htn_order_edges.add((action1, action2))

    # htn_order constraints are reconstructed (in htnorder.find_implied_constraints)
    # from the HTN's own declared structure, including a fallback that infers a
    # dependency from a single unambiguous fact producer when a subtask resolves
    # to zero primitives. That fallback can conflict with a causal link derived
    # independently above (both routing through the same shared action from
    # opposite directions), producing a cycle. Causal links are the ground truth
    # here (they come directly from the actions' own preconditions/effects), so
    # if combining the two produces a cycle, drop the minimal set of htn_order
    # edges responsible rather than leave the POP unsatisfiable.
    if htn_order_edges:
        removed = 0
        while True:
            try:
                cycle = nx.find_cycle(pop.network)
            except nx.NetworkXNoCycle:
                break
            edge_to_drop = next(((u, v) for u, v in cycle if (u, v) in htn_order_edges), None)
            if edge_to_drop is None:
                print("WARNING: cycle found with no htn_order edge to drop", file=sys.stderr)
                break
            pop.unlink_actions(edge_to_drop[0], "htn_order", edge_to_drop[1])
            htn_order_edges.discard(edge_to_drop)
            removed += 1
        if removed:
            print(f"Removed {removed} htn_order edge(s) to break cycle(s) with causal links.")

    # --- Part 4: Finalize the graph ---
    print("Finalizing graph and connecting all nodes to init and goal...")
    
    for act in plan_actions:
        if act is None:
            print("WARNING: Skipping None action in plan_actions during finalization", file=sys.stderr)
            continue
        if not nx.has_path(pop.network, init_action, act):
            pop.link_actions(init_action, "finalize_init", act)
        pop.link_actions(act, "finalize_goal", goal_action)
    pop.transitivly_reduce()
    assert nx.is_directed_acyclic_graph(pop.network), "CRITICAL ERROR: The final POP contains a cycle!"
    
    pop.A = set(linear_plan_objects)
    pop.F = all_fluents
    return pop


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Generate a POP from an HTN trace and encode it.")
    parser.add_argument('trace_file', help="The HTN trace or plan file.")
    parser.add_argument('domain_file', help="The PDDL domain file.")
    parser.add_argument('problem_file', help="The PDDL problem file.")
    parser.add_argument('-o', '--output', default='output.wcnf', help="Name for the output WCNF file.")
    parser.add_argument('--semantics', choices=('pocl', 'parallel'), default='pocl',
                        help='Scheduling semantics: POCL (default) or strict parallel')
    
    args = parser.parse_args()

    try:
        with open(args.trace_file, 'r') as f: trace_data = f.read()
        with open(args.domain_file, 'r') as f: domain_data = f.read()
        with open(args.problem_file, 'r') as f: problem_data = f.read()
            
        print("--- All files loaded successfully ---")
        
        pop = lift_plan_to_pop(domain_data, problem_data, trace_data)
        
        print("\n--- POP Generation Complete ---")
        print(pop)

        #dot_file_path = "htn_pop_graph.dot"
        #with open(dot_file_path, 'w') as f:
        #    f.write(pop.dot(compact=False))
        
        #print(f"\nGraph saved to '{dot_file_path}'.")
        
        print("\n--- Encoding POP to WCNF format ---")
        
        encoder_args = argparse.Namespace(
            deorder=False,
            allact=True,
            serial=False,
            semantics=args.semantics,
            output=args.output
        )
        
        # Call the encoder function
        encoder.encode_POP(pop, encoder_args)
        
        print(f"\nEncoding complete.")

    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
