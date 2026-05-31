# Copyright (c) 2020 Queen's Mu Lab. MIT License.
# Derived from popgen (https://github.com/ai-planning/popgen).
# See NOTICE at the project root for full license text.

import argparse, json

from linearizer import count_linearizations
from pop import POP

import networkx as nx


def get_mapping(map_file):
    with open(map_file) as f:
        mapping = json.load(f)
    return mapping

def print_solution(mapping, output):

    with open(output) as f:
        output = f.readlines()

    varline = [x for x in output if x.startswith('v ')][0]
    values = varline.strip().split(' ')[1:]

    print("\nSolution:")
    for v in values:
        if '-' not in v:
            print("  " + mapping[v])


def extract_pop(mapping, output):

    with open(output) as f:
        output = f.readlines()

    varline = [x for x in output if x.startswith('v ')][0]
    solline = [x for x in output if x.startswith('s ')][0]

    optimal = ('OPTIMUM FOUND' in solline)
    values = varline.strip().split(' ')[1:]

    actions = set()
    orderings = []
    supports = []

    for v in [x for x in values if '-' not in x]:
        if 'in plan' in mapping[v]:
            act = mapping[v].split(' in plan')[0]
            actions.add(act)
        elif ' -> ' in mapping[v]:
            parts = mapping[v].split(' -> ')
            orderings.append((parts[0], parts[1]))
        elif 'supports' in mapping[v]:
            parts = mapping[v].split(' supports ')
            supports.append((parts[0], parts[1].split(' for ')[0], parts[1].split(' for ')[1]))
        else:
            pass # These are auxiliary variables
            # print("Error: Unrecognized mapping line: %s" % mapping[v])

    pop = POP()

    for a in actions:
        pop.add_action(a)

    for (u,v) in orderings:
        pop.link_actions(u,'',v)

    for (a1, p, a2) in supports:
        pop.link_actions(a1,p,a2)

    #for a1 in actions:
    #    for a2 in actions:
    #        if (a1,a2) not in orderings and (a2,a1) not in orderings:
    #            print a1
    #            print a2

    return pop, optimal

def calculate_graph_statistics(pop):
    """
    Takes a POP object, builds a graph, and calculates key metrics.
    Assumes pop object has .actions (a list/set) and .get_links() which
    returns a list of (action1, action2) ordering tuples.
    """
    stats = {}
    
    # --- 1. Build the Graph from the POP object ---
    G = pop.network 
    # Assuming pop.actions is an iterable of action name strings

    # --- 2. Basic Statistics ---
    plan_actions = [a for a in G.nodes() if 'init#' not in a and 'goal#' not in a]
    stats['num_actions'] = len(plan_actions)
    stats['num_orderings'] = G.number_of_edges()

    # --- 3. Makespan & Parallelism Calculation ---
    if not nx.is_directed_acyclic_graph(G):
        stats['makespan'] = 'Error: Plan contains a cycle'
        stats['parallelism'] = 0.0
        return stats

    try:
        # The longest path gives us the critical path of the plan
        critical_path_nodes = nx.dag_longest_path(G)
        
        # Makespan is the number of actual plan actions on this critical path.
        # This correctly excludes init/goal from the time calculation.
        critical_path_actions = [n for n in critical_path_nodes if 'init#' not in n and 'goal#' not in n]
        stats['makespan'] = len(critical_path_actions)
        
        # Parallelism = total actions / makespan. A score of 1.0 is sequential.
        if stats['makespan'] > 0:
            stats['parallelism'] = stats['num_actions'] / stats['makespan']
        else:
            stats['parallelism'] = 0.0
            
    except nx.NetworkXError:
        # This can happen if the graph is not connected (e.g., no path from init to goal)
        stats['makespan'] = 'N/A (disjointed plan)'
        stats['parallelism'] = 0.0
        
    return stats

def do_popstats(mapping, output, show_linears = False):

    pop, optimal = extract_pop(mapping, output)

    if show_linears:
        print("\nLinearizations: %d\n" % count_linearizations(pop))

    print("\n%s\n" % str(pop))
    print("Optimal: %s\n" % str(optimal))

    stats = calculate_graph_statistics(pop)
    print("\n--- Plan Quality Metrics ---")
    print(f"Number of Actions: {stats.get('num_actions', 'N/A')}")
    print(f"Number of Orderings: {stats.get('num_orderings', 'N/A')}")
    print(f"Makespan (Critical Path Length): {stats.get('makespan', 'N/A')}")
    print(f"Parallelism (Actions/Makespan): {stats.get('parallelism', 0.0):.2f}")

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Analyze the output of the solved encoding')

    parser.add_argument('--map', help='The mapping file', required=True)
    parser.add_argument('--rc2out', help='The output from RC2', required=True)

    parser.add_argument('--dot', help='Print the POP as a dot file')
    parser.add_argument('--compactdot', help='Print the POP as a compact dot file')

    parser.add_argument('--print-solution', help='Print the solution', action='store_true')
    parser.add_argument('--show-popstats', help='Show the POP stats', action='store_true')
    parser.add_argument('--count-linearizations', help='Show the number of linearizations', action='store_true')

    args = parser.parse_args()

    if args.print_solution:
        print_solution(get_mapping(args.map), args.rc2out)

    if args.show_popstats:
        do_popstats(get_mapping(args.map), args.rc2out, args.count_linearizations)

    if args.dot:
        pop, _ = extract_pop(get_mapping(args.map), args.rc2out)
        with open(args.dot, 'w') as f:
            f.write(pop.dot())

    if args.compactdot:
        pop, _ = extract_pop(get_mapping(args.map), args.rc2out)
        with open(args.compactdot, 'w') as f:
            f.write(pop.dot(compact=True))

