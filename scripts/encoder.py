# Copyright (c) 2020 Queen's Mu Lab. MIT License.
# Derived from popgen (https://github.com/ai-planning/popgen).
# See NOTICE at the project root for full license text.

import argparse, json

from bauhaus import Encoding, proposition, And, Or
from nnf import dimacs

from lifter import lift_POP


class Hashable:
    def __hash__(self):
        return hash(str(self))

    def __eq__(self, __value: object) -> bool:
        return hash(self) == hash(__value)

    def __repr__(self):
        return str(self)

def encode_POP(pop, cmdargs):

    # For sanitization, make sure we close the pop
    pop.transativly_close()

    F = pop.F
    A = pop.A

    init = pop.init
    goal = pop.goal

    print(f"F: {F}")
    print(f"A: {A}")
    print(f"Init: {init}")
    print(f"Goal: {goal}")

    adders = {}
    deleters = {}

    for f in F:
        adders[f] = set([])
        deleters[f] = set([])

    for a in A:
        for f in a.adds:
            adders[f].add(a)
        for f in a.dels:
            deleters[f].add(a)

    E = Encoding()

    @proposition(E)
    class Action(Hashable):
        def _prop_name(self):
            #print(f"Action({self.name})")
            return f"Action({self.name})"
        #_prop_name = "Action"
        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return f"Action({self.name})"

        def __str__(self):
            return f"{self.name} in plan"

        def __hash__(self) -> int:
            return hash(self.name)

    @proposition(E)
    class Order(Hashable):
        def _prop_name(self):
            return f"Order({self.a1}, {self.a2})"
        def __init__(self, a1, a2):
            self.a1 = a1
            self.a2 = a2

        def __repr__(self):
            return f"Order({self.a1}, {self.a2})"

        def __str__(self):
            return f"{self.a1} -> {self.a2}"

    @proposition(E)
    class Support(Hashable):
        def _prop_name(self):
            return f"Support({self.a1}, {self.p}, {self.a2})"
        def __init__(self, a1, p, a2):
            self.a1 = a1
            self.p = p
            self.a2 = a2

        def __repr__(self):
            return f"Support({self.a1}, {self.p}, {self.a2})"

        def __str__(self):
            return f"{self.a1} supports {self.p} for {self.a2}"

    actions = [Action(a) for a in A]
    orders = [Order(a1, a2) for a1 in A for a2 in A]
    supports = [Support(a1, p, a2) for a2 in A for p in a2.pres for a1 in adders[p]]

    horizon = len(A)

    @proposition(E)
    class Start(Hashable):
        def _prop_name(self):
            return f"Start({self.a}, {self.t})"
        def __init__(self, action, timestep):
            self.a = action
            self.t = timestep

        def __repr__(self):
            return f"Start({self.a}, {self.t})"

        def __str__(self):
            return f"{self.a} starts at {self.t}"

    @proposition(E)
    class MakespanGE(Hashable):
        def _prop_name(self):
            return f"MakespanGE({self.t})"
        def __init__(self, timestep):
            self.t = timestep

        def __repr__(self):
            return f"MakespanGE({self.t})"

        def __str__(self):
            return f"makespan >= {self.t}"

    v2a = {action: action.name for action in actions}
    a2v = {action.name: action for action in actions}

    v2o = {order: (order.a1, order.a2) for order in orders}
    o2v = {(order.a1, order.a2): order for order in orders}

    v2s = {support: (support.a1, support.p, support.a2) for support in supports}

    clauses = []

    # Add the antisymmetric ordering constraints
    clauses.extend([~Order(a, a) for a in A])

    # Add the transitivity constraints
    for a1 in A:
        for a2 in A:
            for a3 in A:
                clauses.append((Order(a1, a2) & Order(a2, a3)) >> Order(a1, a3))

    # HTN ordering constraint enforcement

    print("Checking for mandatory 'htn_order' links...")
    htn_link_count = 0
    if hasattr(pop, 'link_reasons'):
        for (a1, a2), reasons in pop.link_reasons.items():
            # Check if 'htn_order' is in the set of reasons for this link
            if 'htn_order' in reasons:
                # If it is, assert that this ordering MUST exist.
                # This becomes a hard clause with top_cost.
                clauses.append(Order(a1, a2))
                htn_link_count += 1
                print(f"  Adding hard constraint for HTN link: {a1} -> {a2}")
        
        if htn_link_count > 0:
            print(f"Successfully added {htn_link_count} mandatory HTN ordering constraints.")
        else:
            print("No 'htn_order' links found to enforce.")
    else:
        print("Warning: pop.link_reasons attribute not found. Skipping HTN constraints.")

    # Add the ordering -> actions constraints
    for a1 in A:
        for a2 in A:
            clauses.append(Order(a1, a2) >> (Action(a1) & Action(a2)))

    start_vars = {(a, t): Start(a, t) for a in A for t in range(horizon)}
    makespan_ge = {t: MakespanGE(t) for t in range(1, horizon + 1)}

    # Link actions with exactly-one start time
    for a in A:
        possible_times = [start_vars[(a, t)] for t in range(horizon)]
        clauses.append(Action(a) >> Or(possible_times))
        for t in range(horizon):
            clauses.append(start_vars[(a, t)] >> Action(a))
        for t1 in range(horizon):
            for t2 in range(t1 + 1, horizon):
                clauses.append((~start_vars[(a, t1)]) | (~start_vars[(a, t2)]))

    # Enforce precedence respecting unit-duration tasks
    for a1 in A:
        for a2 in A:
            if a1 is a2:
                continue
            for t1 in range(horizon):
                for t2 in range(t1 + 1):
                    clauses.append((~Order(a1, a2)) | (~start_vars[(a1, t1)]) | (~start_vars[(a2, t2)]))

    # Makespan monotonicity and linkage to finish times (duration 1)
    for t in range(1, horizon):
        clauses.append(makespan_ge[t + 1] >> makespan_ge[t])
    for a in A:
        for t in range(horizon):
            clauses.append(start_vars[(a, t)] >> makespan_ge[t + 1])

    # Make sure everything comes after the init, and before the goal
    for a in A:
        if a is not init:
            clauses.append(Action(a) >> Order(init, a))
        if a is not goal:
            clauses.append(Action(a) >> Order(a, goal))

    # Ensure that we have a goal and init action.
    clauses.append(Action(init))
    clauses.append(Action(goal))

    # Satisfy all the preconditions
    for a2 in A:
        for p in a2.pres:
            clauses.append(Action(a2) >> Or([Support(a1, p, a2) for a1 in [x for x in adders[p] if x is not a2]]))

    # Create unthreatened support
    for a2 in A:
        for p in a2.pres:
            for a1 in [x for x in adders[p] if x is not a2]:

                # Support implies ordering
                clauses.append(Support(a1, p, a2) >> Order(a1, a2))

                # Forbid threats
                for ad in deleters[p]:
                    if ad not in [a1, a2]:
                        clauses.append(Support(a1, p, a2) >> (~Action(ad) | Order(ad, a1) | Order(a2, ad)))


    if cmdargs.serial:
        for a1 in A:
            for a2 in A:
                if a1 is not a2:
                    clauses.append((Action(a1) & Action(a2)) >> (Order(a1, a2) | Order(a2, a1)))

    if cmdargs.allact:
        for a in A:
            clauses.append(Action(a))

    if cmdargs.deorder:
        for (ai,aj) in pop.get_links():
            clauses.append(~Order(aj, ai))

    cnf = And(clauses).compile().simplify().to_CNF()
    #print(f"CNF: {cnf}")

    var_labels = dict(enumerate(cnf.vars(), start=1))
    var_labels_inverse = {v: k for k, v in var_labels.items()}
    cnf_dimacs = dimacs.dumps(cnf, mode='cnf', var_labels=var_labels_inverse).strip()

    cnflines = cnf_dimacs.split('\n')

    assert "p cnf" in cnflines[0]
    (_, _, nv, nc) = cnflines[0].split()
    num_soft = len(makespan_ge)
    top_cost = num_soft + 1 if num_soft > 0 else 1
    cnflines[0] = f"p wcnf {nv} {int(nc)+num_soft} {top_cost}"

    for i in range(1, len(cnflines)):
        if cnflines[i] != "":
            cnflines[i] = f"{top_cost} {cnflines[i]}"

    for t in range(1, horizon + 1):
        v = var_labels_inverse[makespan_ge[t]]
        cnflines.append(f"1 -{v} 0")

    with open(cmdargs.output, 'w') as f:
        f.write('\n'.join(cnflines))

    with open(cmdargs.output+'.map', 'w') as f:
        f.write(json.dumps({k: str(v) for k, v in var_labels.items()}, indent=4))

    print('')
    print(f"Vars: {nv}")
    print(f"Clauses: {int(nc)+num_soft}")
    print(f"Soft: {num_soft}")
    print(f"Hard: {nc}")
    print(f"Max Weight: {top_cost}")
    print('')

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Generate a wcnf file for a planning problem.')

    parser.add_argument('-d', '--domain', dest='domain', help='Domain file', required=True)

    parser.add_argument('-p', '--problem', dest='problem', help='Problem file', required=True)

    parser.add_argument('-s', '--plan', dest='plan', help='Plan file', required=True)

    parser.add_argument('-o', '--output', dest='output', help='Output file', required=True)

    parser.add_argument('--allact', dest='allact', action='store_true', help='Include all actions in the plan')
    parser.add_argument('--serial', dest='serial', action='store_true', help='Force it to be serial')
    parser.add_argument('--deorder', dest='deorder', action='store_true', help='Force it to be a deordering')

    args = parser.parse_args()
    pop = lift_POP(args.domain, args.problem, args.plan, serialized=True)

    encode_POP(pop, args)
