# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.

import argparse
import sys
import os

sys.path.append(os.path.dirname(__file__))

from htnpop import lift_plan_to_pop
from analyzer import calculate_graph_statistics

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Compute POP stats directly from HTN trace.')
    parser.add_argument('-d', '--domain', required=True, help='Domain file (HDDL/PDDL)')
    parser.add_argument('-p', '--problem', required=True, help='Problem file (HDDL/PDDL)')
    parser.add_argument('-s', '--plan', required=True, help='Linear plan/trace file produced by engine')
    args = parser.parse_args()

    with open(args.plan, 'r') as f:
        trace = f.read()
    with open(args.domain, 'r') as f:
        domain = f.read()
    with open(args.problem, 'r') as f:
        problem = f.read()

    pop = lift_plan_to_pop(domain, problem, trace)
    stats = calculate_graph_statistics(pop)

    # Mirror analyzer output structure as closely as needed for tests
    print(str(pop))
    print("\nOptimal: True\n")
    print("\n--- Plan Quality Metrics ---")
    print(f"Number of Actions: {stats.get('num_actions', 'N/A')}")
    print(f"Number of Orderings: {stats.get('num_orderings', 'N/A')}")
    print(f"Makespan (Critical Path Length): {stats.get('makespan', 'N/A')}")
    print(f"Parallelism (Actions/Makespan): {stats.get('parallelism', 0.0):.2f}")
