# Copyright (c) 2020 Queen's Mu Lab. MIT License.
# Derived from popgen (https://github.com/ai-planning/popgen).
# See NOTICE at the project root for full license text.

import argparse, json

from linearizer import count_linearizations
from pop import POP

import networkx as nx


def write_layers(actual_file, starts, layers_file):
    """Write a complete layered counterpart of .actual.

    Primitive action lines receive their solved start layer; root and
    decomposition records are copied unchanged, and the format is explicitly
    terminated with <==.
    """
    if not actual_file:
        raise ValueError('--layers requires --actual')
    start_by_id = {}
    for label, timestep in starts.items():
        marker = ')#'
        if marker not in label:
            continue
        try:
            action_id = int(label.rsplit(marker, 1)[1])
        except ValueError:
            continue
        start_by_id[action_id] = timestep

    lines = []
    in_primitive_plan = False
    in_decomposition = False
    saw_end = False
    with open(actual_file) as f:
        for raw in f:
            line = raw.rstrip('\n')
            if line == '==>':
                in_primitive_plan = True
                lines.append(line)
                continue
            if in_primitive_plan and not in_decomposition:
                if line.startswith('root'):
                    in_decomposition = True
                    lines.append(line)
                    continue
                if line.strip():
                    action_id = int(line.split(None, 1)[0])
                    if action_id not in start_by_id:
                        raise ValueError(f'No solved start layer for .actual action {action_id}')
                    lines.append(f'{line} [{start_by_id[action_id]}]')
                else:
                    lines.append(line)
                continue
            if line == '<==':
                saw_end = True
            lines.append(line)

    if not saw_end:
        lines.append('<==')

    with open(layers_file, 'w') as f:
        f.write('\n'.join(lines) + '\n')


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
    starts = {}

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
        elif ' starts at ' in mapping[v]:
            act, t = mapping[v].split(' starts at ')
            starts[act] = int(t)
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

    return pop, optimal, starts

def calculate_graph_statistics(pop, starts=None):
    """
    Takes a POP object, builds a graph, and calculates key metrics.
    Assumes pop object has .actions (a list/set) and .get_links() which
    returns a list of (action1, action2) ordering tuples.

    If `starts` (a dict of action name -> start timestep, as solved by the
    MaxSAT encoding's Start(a,t) variables) is provided, the makespan is
    computed directly from those solved start times rather than from the
    longest path in the extracted Order graph. The Order graph only contains
    edges the solver was *forced* to set true (by HTN/support/threat
    constraints); it is not guaranteed to contain an edge for every pair of
    actions the solver happened to schedule sequentially, so its longest path
    can understate the makespan the solver actually optimized for.
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
        if starts:
            # Authoritative: the makespan the MaxSAT solver actually optimized.
            real_starts = [t for a, t in starts.items() if 'init#' not in a and 'goal#' not in a]
            stats['makespan'] = (max(real_starts) + 1) if real_starts else 0
        else:
            # Fallback for callers with no solved Start(a,t) assignments
            # (e.g. a pre-solve POP): use the causal/ordering graph's
            # longest path as the best available estimate.
            critical_path_nodes = nx.dag_longest_path(G)
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

def do_popstats(mapping, output, show_linears = False, actual_file=None, layers_file=None):

    pop, optimal, starts = extract_pop(mapping, output)

    if layers_file:
        write_layers(actual_file, starts, layers_file)

    if show_linears:
        print("\nLinearizations: %d\n" % count_linearizations(pop))

    print("\n%s\n" % str(pop))
    print("Optimal: %s\n" % str(optimal))

    stats = calculate_graph_statistics(pop, starts)
    print("\n--- Plan Quality Metrics ---")
    print(f"Number of Actions: {stats.get('num_actions', 'N/A')}")
    print(f"Number of Orderings: {stats.get('num_orderings', 'N/A')}")
    print(f"Makespan (Critical Path Length): {stats.get('makespan', 'N/A')}")
    print(f"Parallelism (Actions/Makespan): {stats.get('parallelism', 0.0):.2f}")

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Analyze the output of the solved encoding')

    parser.add_argument('--map', help='The mapping file', required=True)
    parser.add_argument('--rc2out', help='The output from RC2', required=True)
    parser.add_argument('--actual', help='Cleaned .actual plan used for encoding')
    parser.add_argument('--layers', help='Write solved action layers to this file')

    parser.add_argument('--dot', help='Print the POP as a dot file')
    parser.add_argument('--compactdot', help='Print the POP as a compact dot file')

    parser.add_argument('--print-solution', help='Print the solution', action='store_true')
    parser.add_argument('--show-popstats', help='Show the POP stats', action='store_true')
    parser.add_argument('--count-linearizations', help='Show the number of linearizations', action='store_true')

    args = parser.parse_args()

    if args.print_solution:
        print_solution(get_mapping(args.map), args.rc2out)

    if args.show_popstats:
        do_popstats(get_mapping(args.map), args.rc2out, args.count_linearizations,
                    args.actual, args.layers)

    if args.dot:
        pop, _, _ = extract_pop(get_mapping(args.map), args.rc2out)
        with open(args.dot, 'w') as f:
            f.write(pop.dot())

    if args.compactdot:
        pop, _, _ = extract_pop(get_mapping(args.map), args.rc2out)
        with open(args.compactdot, 'w') as f:
            f.write(pop.dot(compact=True))
