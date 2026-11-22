# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.
"""Write OCELOT's final plan as an HDDL 1.1 partial-order plan certificate.

Inputs:

* the ORIGINAL (lifted) domain and problem,
* the cleaned PANDA-format plan (``<base>.actual``) from ``pandaPIparser -c``,
  which carries the decomposition in source-model names, and
* the solved MaxSAT encoding (``<base>.wcnf.map`` + ``<base>.sol``), which
  carries the reordered plan: the ``Order(a, b)`` relation and the solved
  ``Start(a, t)`` layers.

Output: a certificate in the format checked by the Bell validator.

Decomposition and parameters
----------------------------
``.actual`` names every compound occurrence, its task arguments, the source
method name and its children, but not the method's own parameter list. For
every occurrence the method's head is unified with the task arguments, its
declared subtasks are matched (by backtracking unification, independent of the
order the children are printed in) against the children, and any method
parameter still unbound after that (one that appears only in the method's
precondition) is chosen by enumerating typed objects until the precondition
holds in some state of the method's legal window in the engine's own
sequential execution (``.actual`` lists primitives in execution order).
Compiled pandaPI artefacts (``__method_precondition_*``, ``__noop*``,
``__top*`` ...) are dropped; they never appear as actions.

Method-precondition guards
--------------------------
``Decomposition`` is shared with the reorderer (htnpop.py), which builds it
before solving from the same inputs. Each applied method whose ground
precondition has a non-static literal gets a guard: a zero-duration step that
consumes those literals (so the encoding gives it unthreatened causal support),
follows everything the hierarchy orders before the method's task and the checks
of its ancestors and earlier tasks, and precedes everything in the task's
subtree. Guards that are provably redundant are pruned (see
``Decomposition._prune_guards``). Guards are never emitted.

Partial order
-------------
The order is built over actions and guards as the union of

1. the ``Order`` relation the MaxSAT reorderer chose (it is transitively
   closed by the encoding and contains every causal link
   ``Support(a, p, b) -> Order(a, b)`` and every threat resolution
   ``Order(d, a) | Order(b, d)``, for guards as for actions),
2. every ordering the decomposition imposes (recomputed here from the source
   methods, including orderings that pass through empty methods), and
3. for ``--semantics parallel`` only, an edge between every pair of actions
   with interfering effects, oriented by their solved start layers,

then projected onto real actions (keeping orderings through a guard) and
transitively reduced. Every edge is checked against the solved layers: an
action precedes only steps in strictly later layers, a guard only steps in the
same or later layers. So the union is acyclic, and it is a restriction of the
POCL order 1, every one of whose linearizations executes under the encoding's
action model, with each guard placed right after its last predecessor. With an
optimal MaxSAT solution the projection's longest chain is exactly the reported
makespan: no edge can make it longer than the layer count, and an
as-soon-as-possible schedule of the same relation is itself a feasible MaxSAT
assignment, so it cannot be shorter. Both properties are asserted, not assumed.
"""

import argparse
import itertools
import json
import os
import re
import sys
from collections import defaultdict

import networkx as nx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


MAX_BINDING_CANDIDATES = 1_000_000


class CertificateError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# S-expressions
# ---------------------------------------------------------------------------

def parse_sexpr(text):
    text = re.sub(r';[^\n]*', '', text).lower()
    tokens = re.findall(r'\(|\)|[^\s()]+', text)
    stack = [[]]
    for tok in tokens:
        if tok == '(':
            stack.append([])
        elif tok == ')':
            if len(stack) == 1:
                raise CertificateError('unbalanced parentheses')
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok)
    if len(stack) != 1:
        raise CertificateError('unbalanced parentheses')
    return stack[0]


def keyword_fields(items):
    """Turn [':a', x, ':b', y, ...] into {':a': x, ':b': y}."""
    out = {}
    i = 0
    while i < len(items):
        if isinstance(items[i], str) and items[i].startswith(':'):
            out[items[i]] = items[i + 1] if i + 1 < len(items) else None
            i += 2
        else:
            i += 1
    return out


def typed_list(items):
    """Parse '?x ?y - t ?z' into [('?x','t'), ('?y','t'), ('?z','object')]."""
    out, pending = [], []
    i = 0
    while i < len(items):
        tok = items[i]
        if tok == '-':
            typ = items[i + 1]
            if isinstance(typ, list):  # (either a b): keep the first for lookup
                typ = typ[1] if len(typ) > 1 else 'object'
            out.extend((v, typ) for v in pending)
            pending = []
            i += 2
        else:
            pending.append(tok)
            i += 1
    out.extend((v, 'object') for v in pending)
    return out


# ---------------------------------------------------------------------------
# Domain / problem model
# ---------------------------------------------------------------------------

class Schema:
    def __init__(self, name, params):
        self.name = name
        self.params = params  # [(var, type)]


class Method(Schema):
    def __init__(self, name, params, task, precondition, subtasks, ordering):
        super().__init__(name, params)
        self.task = task                # (name, [terms])
        self.precondition = precondition
        self.subtasks = subtasks        # [(sid, name, [terms])] in declaration order
        self.ordering = ordering        # [(sid, sid)]


def parse_subtask_list(node):
    """Parse a task-network task list into [(sid or None, name, [terms])]."""
    if not node:
        return []
    entries = node[1:] if node[0] == 'and' else [node]
    out = []
    for e in entries:
        if not isinstance(e, list) or not e:
            continue
        if len(e) == 2 and isinstance(e[1], list):
            out.append((e[0], e[1][0], list(e[1][1:])))
        else:
            out.append((None, e[0], list(e[1:])))
    return out


def parse_ordering(node):
    if not node:
        return []
    entries = node[1:] if node[0] == 'and' else [node]
    out = []
    for e in entries:
        if not isinstance(e, list) or len(e) < 3:
            continue
        if e[0] == '<':
            out.extend(zip(e[1:-1], e[2:]))
        elif e[0] == '>':
            out.extend((b, a) for a, b in zip(e[1:-1], e[2:]))
    return out


def parse_network(fields):
    """Parse a method/problem task network: returns (subtasks, ordering)."""
    ordered = False
    node = None
    for key in (':ordered-subtasks', ':ordered-tasks'):
        if key in fields:
            node, ordered = fields[key], True
    for key in (':subtasks', ':tasks'):
        if key in fields:
            node = fields[key]
    subtasks = parse_subtask_list(node)
    # Give anonymous subtasks a synthetic id so orderings can refer to them.
    subtasks = [(sid if sid is not None else f'__anon{i}', n, a)
                for i, (sid, n, a) in enumerate(subtasks)]
    if ordered:
        ordering = [(subtasks[i][0], subtasks[i + 1][0]) for i in range(len(subtasks) - 1)]
    else:
        ordering = parse_ordering(fields.get(':ordering'))
    return subtasks, ordering


class Model:
    def __init__(self, domain_text, problem_text):
        dom = parse_sexpr(domain_text)
        if len(dom) != 1 or dom[0][0] != 'define':
            raise CertificateError('domain is not a single (define ...) form')
        prob = parse_sexpr(problem_text)
        if len(prob) != 1 or prob[0][0] != 'define':
            raise CertificateError('problem is not a single (define ...) form')
        dom, prob = dom[0], prob[0]

        self.domain_name = dom[1][1]
        self.problem_name = prob[1][1]
        self.requirements = set()
        self.supertypes = {}
        self.object_types = {}
        self.actions = {}
        self.tasks = {}
        self.methods = {}

        for block in dom[2:]:
            head = block[0]
            if head == ':requirements':
                self.requirements.update(block[1:])
            elif head == ':types':
                for t, parent in typed_list(block[1:]):
                    self.supertypes[t] = parent
            elif head == ':constants':
                for o, t in typed_list(block[1:]):
                    self.object_types[o] = t
            elif head == ':action':
                f = keyword_fields(block[2:])
                a = Schema(block[1], typed_list(f.get(':parameters') or []))
                a.precondition = f.get(':precondition')
                a.effect = f.get(':effect')
                self.actions[a.name] = a
            elif head == ':task':
                f = keyword_fields(block[2:])
                self.tasks[block[1]] = Schema(block[1], typed_list(f.get(':parameters') or []))
            elif head == ':method':
                f = keyword_fields(block[2:])
                subtasks, ordering = parse_network(f)
                task = f[':task']
                self.methods[block[1]] = Method(
                    block[1], typed_list(f.get(':parameters') or []),
                    (task[0], list(task[1:])), f.get(':precondition'), subtasks, ordering)

        self.init = set()
        self.goal = None
        self.metric = None
        self.htn_params = []
        self.htn_subtasks = None  # None: the problem has no initial task network
        self.htn_ordering = []
        for block in prob[2:]:
            head = block[0]
            if head == ':objects':
                for o, t in typed_list(block[1:]):
                    self.object_types[o] = t
            elif head == ':init':
                for lit in block[1:]:
                    if isinstance(lit, list) and lit and lit[0] != 'not' and lit[0] != '=':
                        self.init.add(tuple(lit))
            elif head == ':goal':
                self.goal = block[1] if len(block) > 1 else None
            elif head == ':metric':
                self.metric = block[1:]
            elif head == ':htn':
                f = keyword_fields(block[1:])
                self.htn_params = typed_list(f.get(':parameters') or [])
                self.htn_subtasks, self.htn_ordering = parse_network(f)

        self._objects_of = {}

    @property
    def makespan_objective(self):
        return (':makespan' in self.requirements and self.metric is not None
                and len(self.metric) >= 2 and self.metric[0] == 'minimize'
                and self.metric[1] in (['makespan'], 'makespan'))

    def is_subtype(self, t, ancestor):
        seen = set()
        while t is not None and t not in seen:
            if t == ancestor:
                return True
            seen.add(t)
            t = self.supertypes.get(t)
        return ancestor == 'object'

    def objects_of(self, typ):
        if typ not in self._objects_of:
            self._objects_of[typ] = sorted(o for o, t in self.object_types.items()
                                           if self.is_subtype(t, typ))
        return self._objects_of[typ]

    # -- formulas -----------------------------------------------------------

    @staticmethod
    def term(t, binding):
        return binding.get(t, t) if t.startswith('?') else t

    def holds(self, expr, binding, state):
        if not expr:
            return True
        head = expr[0]
        if head == 'and':
            return all(self.holds(e, binding, state) for e in expr[1:])
        if head == 'or':
            return any(self.holds(e, binding, state) for e in expr[1:])
        if head == 'not':
            return not self.holds(expr[1], binding, state)
        if head == 'imply':
            return (not self.holds(expr[1], binding, state)) or self.holds(expr[2], binding, state)
        if head == '=':
            return self.term(expr[1], binding) == self.term(expr[2], binding)
        if head in ('forall', 'exists'):
            vars_ = typed_list(expr[1])
            combos = itertools.product(*[self.objects_of(t) for _, t in vars_])
            test = all if head == 'forall' else any
            return test(self.holds(expr[2], {**binding, **dict(zip((v for v, _ in vars_), c))}, state)
                        for c in combos)
        return tuple([head] + [self.term(a, binding) for a in expr[1:]]) in state

    def effects(self, expr, binding, state, adds, dels):
        if not expr:
            return
        head = expr[0]
        if head == 'and':
            for e in expr[1:]:
                self.effects(e, binding, state, adds, dels)
        elif head == 'not':
            atom = expr[1]
            dels.add(tuple([atom[0]] + [self.term(a, binding) for a in atom[1:]]))
        elif head == 'when':
            if self.holds(expr[1], binding, state):
                self.effects(expr[2], binding, state, adds, dels)
        elif head == 'forall':
            vars_ = typed_list(expr[1])
            for c in itertools.product(*[self.objects_of(t) for _, t in vars_]):
                self.effects(expr[2], {**binding, **dict(zip((v for v, _ in vars_), c))},
                             state, adds, dels)
        elif head in ('increase', 'decrease', 'assign', 'scale-up', 'scale-down'):
            return
        else:
            adds.add(tuple([head] + [self.term(a, binding) for a in expr[1:]]))

    @property
    def static_predicates(self):
        """Predicates no action effect mentions: their truth never changes."""
        if not hasattr(self, '_static'):
            changed = set()

            def scan(e):
                if not isinstance(e, list) or not e:
                    return
                if e[0] in ('and',):
                    for x in e[1:]:
                        scan(x)
                elif e[0] == 'not':
                    scan(e[1])
                elif e[0] == 'when':
                    scan(e[2])
                elif e[0] == 'forall':
                    scan(e[2])
                elif e[0] not in ('increase', 'decrease', 'assign', 'scale-up', 'scale-down'):
                    changed.add(e[0])
            for a in self.actions.values():
                scan(a.effect)
            self._static = changed
        return lambda pred: pred not in self._static

    def entailing_literals(self, expr, binding, state, positive=True):
        """Ground literals, all true in `state`, that together entail `expr`
        (which must hold in `state`). Returns a set of (positive, atom).

        Disjunctions/existentials contribute the first disjunct true in
        `state`, so the result is a sufficient conjunctive condition: if every
        returned literal holds at the method's check point, so does `expr`.
        """
        if not expr:
            return set()
        head = expr[0]
        if head == 'not':
            return self.entailing_literals(expr[1], binding, state, not positive)
        if head == 'imply':
            return self.entailing_literals(['or', ['not', expr[1]], expr[2]],
                                           binding, state, positive)
        if head in ('and', 'or'):
            conj = (head == 'and') == positive
            parts = [self._signed(e, positive) for e in expr[1:]]
            if conj:
                return set().union(*[self.entailing_literals(e, binding, state, True)
                                     for e in parts]) if parts else set()
            for e in parts:
                if self.holds(e, binding, state):
                    return self.entailing_literals(e, binding, state, True)
            raise CertificateError(f'condition {expr} does not hold')
        if head in ('forall', 'exists'):
            vars_ = typed_list(expr[1])
            insts = [{**binding, **dict(zip((v for v, _ in vars_), c))}
                     for c in itertools.product(*[self.objects_of(t) for _, t in vars_])]
            conj = (head == 'forall') == positive
            body = self._signed(expr[2], positive)
            if conj:
                return set().union(*[self.entailing_literals(body, b, state, True)
                                     for b in insts]) if insts else set()
            for b in insts:
                if self.holds(body, b, state):
                    return self.entailing_literals(body, b, state, True)
            raise CertificateError(f'condition {expr} does not hold')
        if head == '=':
            return set()  # static: fixed by the binding
        atom = tuple([head] + [self.term(a, binding) for a in expr[1:]])
        return {(positive, atom)}

    @staticmethod
    def _signed(e, positive):
        return e if positive else ['not', e]

    def apply(self, action, args, state):
        schema = self.actions[action]
        binding = dict(zip((v for v, _ in schema.params), args))
        adds, dels = set(), set()
        self.effects(schema.effect, binding, state, adds, dels)
        return (state - dels) | adds


# ---------------------------------------------------------------------------
# Inputs: cleaned PANDA plan and solved MaxSAT encoding
# ---------------------------------------------------------------------------

def is_artefact(name):
    return name.startswith('__') or name.startswith('_splitting') or 'method_precondition' in name


def parse_actual(text):
    """Parse a cleaned PANDA plan. Returns (primitives, roots, decompositions)."""
    primitives = {}      # id -> (name, [args]) in file order
    roots = []
    decomps = {}         # id -> (task, [args], method, [child ids])
    lines = text.splitlines()
    try:
        start = lines.index('==>')
    except ValueError:
        raise CertificateError('plan has no ==> marker')
    section = 'primitive'
    for line in lines[start + 1:]:
        line = line.strip()
        if not line:
            continue
        if line == '<==':
            break
        if section == 'primitive':
            if line.startswith('root'):
                roots = [int(x) for x in line.split()[1:]]
                section = 'decomp'
                continue
            parts = line.split()
            primitives[int(parts[0])] = (parts[1].lower(), [a.lower() for a in parts[2:]])
        else:
            head, _, tail = line.partition('->')
            hp = head.split()
            tp = tail.split()
            decomps[int(hp[0])] = (hp[1].lower(), [a.lower() for a in hp[2:]],
                                   tp[0].lower(), [int(x) for x in tp[1:]])
    return primitives, roots, decomps


_ID_RE = re.compile(r'#([^#\s]+)$')


def parse_maxsat_solution(map_text, sol_text):
    """Return (orders {(id,id)}, starts {id: t}, optimal) for plan actions
    (integer ids) and method-precondition guards (ids `g<task id>`)."""
    mapping = json.loads(map_text)
    lines = sol_text.splitlines()
    vlines = [x for x in lines if x.startswith('v ')]
    slines = [x for x in lines if x.startswith('s ')]
    if not vlines or not slines:
        raise CertificateError('MaxSAT output has no solution (missing s/v lines)')
    optimal = 'OPTIMUM FOUND' in slines[0]
    true_vars = [x for x in ' '.join(v[2:] for v in vlines).split() if not x.startswith('-')]

    def ident(label):
        m = _ID_RE.search(label.strip())
        if not m:
            raise CertificateError(f'cannot read action id from MaxSAT label {label!r}')
        tok = m.group(1)
        return int(tok) if tok.isdigit() else tok

    orders, starts = set(), {}
    for v in true_vars:
        label = mapping.get(v)
        if label is None:
            continue
        if ' starts at ' in label:
            a, t = label.rsplit(' starts at ', 1)
            starts[ident(a)] = int(t)
        elif ' -> ' in label:
            a, b = label.split(' -> ')
            orders.add((ident(a), ident(b)))
    real = lambda x: isinstance(x, int) or is_guard_id(x)  # actions and guards
    starts = {a: t for a, t in starts.items() if real(a)}
    orders = {(a, b) for a, b in orders if real(a) and real(b)}
    return orders, starts, optimal


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------

def unify(terms, values, binding):
    """Extend binding so that terms match values; None on failure."""
    if len(terms) != len(values):
        return None
    b = dict(binding)
    for t, v in zip(terms, values):
        if t.startswith('?'):
            if b.get(t, v) != v:
                return None
            b[t] = v
        elif t != v:
            return None
    return b


def match_subtasks(subtasks, children, binding):
    """Match declared subtasks (in order) to child calls (any order).

    Returns (binding, [child index per subtask]) or None.
    """
    used = [False] * len(children)

    def rec(i, b):
        if i == len(subtasks):
            return b, []
        _, name, terms = subtasks[i]
        for j, (cname, cargs) in enumerate(children):
            if used[j] or cname != name:
                continue
            nb = unify(terms, cargs, b)
            if nb is None:
                continue
            used[j] = True
            r = rec(i + 1, nb)
            used[j] = False
            if r is not None:
                return r[0], [j] + r[1]
        return None

    if len(subtasks) != len(children):
        return None
    return rec(0, binding)


def transitive_closure(pairs):
    g = nx.DiGraph(list(pairs))
    return set(nx.transitive_closure_dag(g).edges()) if g.number_of_edges() else set()


def guard_id(tid):
    return f'g{tid}'


def is_guard_id(x):
    return isinstance(x, str) and x.startswith('g') and x[1:].isdigit()


class Guard:
    """An applied method's precondition check, as an internal zero-duration
    step of the reordered plan. `literals` is a set of (positive, atom) whose
    conjunction entails the ground precondition; static literals are omitted."""

    def __init__(self, tid, method, args, literals, position):
        self.id = guard_id(tid)
        self.tid = tid
        self.method = method
        self.args = args
        self.literals = literals
        self.position = position  # state index in the engine's sequence

    def __repr__(self):
        return f'Guard({self.id}, {self.method} {" ".join(self.args)})'


class Decomposition:
    """The chosen decomposition of a cleaned PANDA plan, in source-model terms.

    Method bindings (including precondition-only parameters) and method
    precondition guards are computed over the engine's own sequential
    execution: `.actual` lists primitives in execution order (the engine
    prepends each step while walking its solution back from the goal). Both the
    reorderer (htnpop.py, before solving) and the certificate writer (after)
    build this object from the same inputs, so they agree on every binding.
    """

    def __init__(self, model, actual_text):
        self.model = model
        prims, roots, decomps = parse_actual(actual_text)

        # Drop compiled artefacts. A real primitive must be a source action.
        self.prims = {}
        for pid, (name, args) in prims.items():
            if is_artefact(name):
                continue
            if name not in model.actions:
                raise CertificateError(f'plan action {pid} {name} is not a source-domain action')
            self.prims[pid] = (name, args)
        self.decomps = {}
        for tid, (task, args, method, children) in decomps.items():
            if is_artefact(task) or is_artefact(method):
                raise CertificateError(
                    f'decomposition {tid} {task} -> {method} is a pandaPI compilation '
                    f'artefact that does not map back to the source model')
            if task not in model.tasks:
                raise CertificateError(f'task {task} (occurrence {tid}) is not a source task')
            if method not in model.methods:
                raise CertificateError(f'method {method} (occurrence {tid}) is not a source method')
            kept = [c for c in children if c in self.prims or c in decomps]
            self.decomps[tid] = (task, args, method, kept)
        self.roots = [r for r in roots if r in self.prims or r in self.decomps]

        self.sequence = list(self.prims)  # engine execution order
        self.position = {p: i for i, p in enumerate(self.sequence)}
        self.states = [frozenset(model.init)]
        for p in self.sequence:
            name, args = self.prims[p]
            self.states.append(frozenset(model.apply(name, args, self.states[-1])))

        self._build_hierarchy()
        self._build_guards()

    def call(self, oid):
        if oid in self.prims:
            return self.prims[oid]
        task, args, _, _ = self.decomps[oid]
        return task, args

    def _build_hierarchy(self):
        m = self.model
        self.children = {}          # oid -> child oids in source declaration order
        self.sibling_order = {}     # oid -> [(child, child)] (transitively closed)

        # Root network.
        root_calls = [self.call(r) for r in self.roots]
        if m.htn_subtasks is None:
            self.root_params = []
            self.root_children = list(self.roots)
            self.root_order = []
        else:
            res = match_subtasks(m.htn_subtasks, root_calls, {})
            if res is None:
                raise CertificateError(
                    f'root tasks {root_calls} do not match the problem task network')
            binding, idx = res
            self.root_children = [self.roots[j] for j in idx]
            sid_to_oid = {m.htn_subtasks[i][0]: self.root_children[i] for i in range(len(idx))}
            self.root_order = sorted(transitive_closure(
                (sid_to_oid[a], sid_to_oid[b]) for a, b in m.htn_ordering))
            self.root_params = []
            for v, t in m.htn_params:
                if v not in binding:
                    raise CertificateError(f'problem task-network parameter {v} is unbound')
                self.root_params.append(binding[v])

        self.pending_bindings = {}
        for tid, (task, args, mname, kids) in self.decomps.items():
            meth = m.methods[mname]
            b = unify(meth.task[1], args, {})
            if meth.task[0] != task or b is None:
                raise CertificateError(f'method {mname} does not decompose {task} {args}')
            res = match_subtasks(meth.subtasks, [self.call(k) for k in kids], b)
            if res is None:
                raise CertificateError(
                    f'children of {tid} ({task} -> {mname}) do not match its declared subtasks')
            b, idx = res
            ordered = [kids[j] for j in idx]
            self.children[tid] = ordered
            sid_to_oid = {meth.subtasks[i][0]: ordered[i] for i in range(len(idx))}
            self.sibling_order[tid] = sorted(transitive_closure(
                (sid_to_oid[a], sid_to_oid[c]) for a, c in meth.ordering))
            self.pending_bindings[tid] = b

        # Leaves, inherited predecessors/successors.
        self.leaves = {}

        def leaves(oid):
            if oid not in self.leaves:
                if oid in self.prims:
                    self.leaves[oid] = {oid}
                else:
                    self.leaves[oid] = set().union(*[leaves(c) for c in self.children[oid]]) \
                        if self.children[oid] else set()
            return self.leaves[oid]

        for o in list(self.prims) + list(self.decomps):
            leaves(o)
        self.preds, self.succs = {}, {}
        self.parent = {}
        self.inherited = set()
        self.traversal = []         # pre-order, each sibling list in a linear extension
        reached = set()

        def visit(kids, order, preds, succs, parent):
            topo = list(nx.lexicographical_topological_sort(
                nx.DiGraph(order) if order else nx.DiGraph(),
                key=kids.index)) if order else []
            ranked = topo + [k for k in kids if k not in topo]
            for k in ranked:
                if k in reached:
                    raise CertificateError(f'occurrence {k} is introduced twice')
                reached.add(k)
                self.parent[k] = parent
                self.traversal.append(k)
                kp = set(preds).union(*[leaves(a) for a, b in order if b == k])
                ks = set(succs).union(*[leaves(b) for a, b in order if a == k])
                self.preds[k], self.succs[k] = kp, ks
                if k in self.decomps:
                    visit(self.children[k], self.sibling_order[k], kp, ks, k)
            for a, b in order:
                self.inherited.update(itertools.product(leaves(a), leaves(b)))

        visit(self.root_children, self.root_order, set(), set(), None)
        orphans = (set(self.prims) | set(self.decomps)) - reached
        if orphans and self.model.htn_subtasks is not None:
            raise CertificateError(f'occurrences not reachable from the root: {sorted(orphans)}')

    def _sibling_predecessors(self, oid):
        """Occurrences ordered before oid or before one of its ancestors."""
        out = set()
        while oid is not None:
            parent = self.parent.get(oid)
            order = self.root_order if parent is None else self.sibling_order[parent]
            out.update(a for a, b in order if b == oid)
            oid = parent
        return out

    def _sibling_successors(self, oid):
        """Occurrences ordered after oid or after one of its ancestors."""
        out = set()
        while oid is not None:
            parent = self.parent.get(oid)
            order = self.root_order if parent is None else self.sibling_order[parent]
            out.update(b for a, b in order if a == oid)
            oid = parent
        return out

    def _build_guards(self):
        """Bind every method and create a guard for each method application
        whose precondition has a non-static literal."""
        m = self.model
        static = m.static_predicates
        self.method_args = {}
        self.guards = {}            # tid -> Guard
        n = len(self.sequence)
        for tid in [o for o in self.traversal if o in self.decomps]:
            task, args, mname, _ = self.decomps[tid]
            meth = m.methods[mname]
            b = self.pending_bindings[tid]
            unbound = [(v, t) for v, t in meth.params if v not in b]
            names = [v for v, _ in unbound]

            # Legal window of the check in the engine's sequence: after every
            # primitive the hierarchy orders before the task, after the guards
            # of its ancestors and of tasks ordered before it (or an
            # ancestor), and before its own and its successors' primitives.
            earlier_guards = set()
            anc = self.parent.get(tid)
            while anc is not None:
                earlier_guards.add(anc)
                anc = self.parent.get(anc)
            for o in self._sibling_predecessors(tid):
                earlier_guards.update(x for x in self.decomps if self._descends(x, o))
            lo = max([self.position[p] + 1 for p in self.preds[tid]]
                     + [self.guards[g].position for g in earlier_guards if g in self.guards]
                     + [0])
            hi = min((self.position[p] for p in self.leaves[tid] | self.succs[tid]), default=n)

            def find(lo_, hi_):
                combos = itertools.product(*[m.objects_of(t) for _, t in unbound])
                for c in itertools.islice(combos, MAX_BINDING_CANDIDATES):
                    nb = {**b, **dict(zip(names, c))}
                    for i in range(lo_, hi_ + 1):
                        if m.holds(meth.precondition, nb, self.states[i]):
                            return nb, i
                return None

            found = find(lo, hi) if lo <= hi else None
            if found is None:
                found = find(0, n)  # outside the window: the encoding decides
                if found is not None:
                    print(f'warning: precondition of {mname} for occurrence {tid} does not '
                          f'hold in its window of the engine sequence', file=sys.stderr)
            if found is None:
                raise CertificateError(
                    f'no binding of {names} satisfies the precondition of '
                    f'{mname} for occurrence {tid} ({task} {" ".join(args)})')
            nb, pos = found
            self.method_args[tid] = [nb[v] for v, _ in meth.params]
            lits = {(sign, atom) for sign, atom in
                    m.entailing_literals(meth.precondition, nb, self.states[pos])
                    if not static(atom[0])}
            if lits:
                self.guards[tid] = Guard(tid, mname, self.method_args[tid], lits, pos)
        self._prune_guards()

    def _necessary_literals(self, pid):
        """Literals every execution of primitive pid requires: the conjuncts of
        its precondition that are plain (possibly negated) atoms."""
        m = self.model
        name, args = self.prims[pid]
        schema = m.actions[name]
        binding = dict(zip((v for v, _ in schema.params), args))
        out, todo = set(), [schema.precondition]
        while todo:
            e = todo.pop()
            if not e:
                continue
            if e[0] == 'and':
                todo.extend(e[1:])
            elif e[0] == 'not' and isinstance(e[1], list) and e[1] and \
                    e[1][0] not in ('and', 'or', 'not', 'imply', 'forall', 'exists', '='):
                out.add((False, tuple([e[1][0]] + [m.term(a, binding) for a in e[1][1:]])))
            elif e[0] not in ('or', 'not', 'imply', 'forall', 'exists', '='):
                out.add((True, tuple([e[0]] + [m.term(a, binding) for a in e[1:]])))
        return out

    def _prune_guards(self):
        """Drop guards that need no step of their own.

        A guard for task T is redundant if some primitive `a` in T's subtree is
        ordered by the hierarchy before everything else in that subtree
        (including the guards that remain), and every guard literal is a
        conjunct of a's precondition. In every linearization `a` executes, so
        the check placed immediately before `a` holds; that point follows every
        hierarchy predecessor of T and every check that must precede T's (all
        of them precede `a`), and precedes the rest of T's subtree.

        For an empty method (nothing in T's subtree) the same holds with `a`
        the first of T's hierarchy successors: everything ordered before T, or
        checked before it, also precedes every successor of T.
        Processed bottom-up, so descendants' guards are decided first.
        """
        if not self.guards:
            return
        hierarchy = self.hierarchy_constraints()
        for tid in reversed([o for o in self.traversal if o in self.guards]):
            g = guard_id(tid)
            rest = self.elements(tid) - {g}
            if not rest:
                rest = set().union(*[self.elements(o) for o in self._sibling_successors(tid)])
            for a in rest:
                if a not in self.prims:
                    continue
                if all((a, e) in hierarchy for e in rest if e != a) and \
                        self.guards[tid].literals <= self._necessary_literals(a):
                    del self.guards[tid]
                    break

    def _descends(self, x, ancestor):
        while x is not None:
            if x == ancestor:
                return True
            x = self.parent.get(x)
        return False

    def elements(self, oid):
        """Primitive leaves and guards within oid's subtree (guards by id)."""
        if oid in self.prims:
            return {oid}
        out = {guard_id(oid)} if oid in self.guards else set()
        for c in self.children[oid]:
            out |= self.elements(c)
        return out

    def hierarchy_constraints(self):
        """Every ordering the hierarchy imposes among primitives and guards:
        a guard precedes everything else in its subtree, and for siblings
        s1 < s2 everything in s1's subtree precedes everything in s2's."""
        edges = set()
        for tid in self.guards:
            g = guard_id(tid)
            edges.update((g, e) for e in self.elements(tid) if e != g)
        orders = [self.root_order] + [self.sibling_order[t] for t in self.decomps]
        for order in orders:
            for a, b in order:
                edges.update(itertools.product(self.elements(a), self.elements(b)))
        return edges


class Certificate:
    def __init__(self, decomposition, orders, starts, makespan, semantics='pocl',
                 domain_text=None, actual_text=None):
        self.dec = decomposition
        self.model = decomposition.model
        self.prims = decomposition.prims
        self.domain_text, self.actual_text = domain_text, actual_text
        self.semantics = semantics
        self.makespan = makespan

        guard_ids = {guard_id(t) for t in decomposition.guards}
        nodes = set(self.prims) | guard_ids
        missing = nodes - set(starts)
        if missing:
            raise CertificateError(
                f'no solved start layer for plan steps {sorted(map(str, missing))} '
                f'(was the plan encoded with the same method-precondition guards?)')
        self.starts = {p: starts[p] for p in nodes}
        # Reference linearization: by solved layer. Every submitted ordering
        # edge goes to a strictly later layer, so this is one of the
        # certificate's linearizations.
        self.layered = sorted(self.prims, key=lambda p: (self.starts[p], p))
        self.position = {p: i for i, p in enumerate(self.layered)}
        self.order = self._build_order(orders, nodes)

    def call(self, oid):
        return self.dec.call(oid)

    # -- ordering ------------------------------------------------------------

    def _build_order(self, solver_orders, nodes):
        prims = set(self.prims)
        hierarchy = self.dec.hierarchy_constraints()
        edges = {(a, b) for a, b in solver_orders if a in nodes and b in nodes}
        missing_inherited = hierarchy - edges
        edges |= hierarchy

        if self.semantics == 'parallel':
            edges |= self._interference_edges()

        # A guard is a zero-duration check at the start of its layer: its
        # supporters end before that layer (strict), and whatever it precedes
        # may start in the same layer (non-strict). Real actions take a layer.
        dur = lambda x: 0 if is_guard_id(x) else 1
        bad = [(a, b) for a, b in edges if self.starts[b] < self.starts[a] + dur(a)]
        if bad:
            a, b = bad[0]
            why = (' (required by the decomposition but not enforced by the reorderer)'
                   if (a, b) in missing_inherited else '')
            raise CertificateError(
                f'ordering {a} < {b}{why} contradicts the solved layers '
                f'{self.starts[a]}, {self.starts[b]}; {len(bad)} such edge(s)')

        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from(edges)
        if not nx.is_directed_acyclic_graph(g):
            raise CertificateError('submitted ordering is cyclic')
        # Project onto real actions, keeping orderings that pass through a
        # guard. In any linearization of the projection every predecessor of
        # a guard precedes every successor, so the check can be placed right
        # after its last predecessor, where all its supporters have run and
        # no threat it was protected from has.
        closure = nx.transitive_closure_dag(g)
        proj = nx.DiGraph()
        proj.add_nodes_from(prims)
        proj.add_edges_from((a, b) for a, b in closure.edges() if a in prims and b in prims)
        reduced = nx.transitive_reduction(proj)
        chain = nx.dag_longest_path_length(reduced) + 1 if prims else 0
        if self.makespan is not None and chain != self.makespan:
            raise CertificateError(
                f'longest chain of the partial order is {chain}, but the reported '
                f'makespan is {self.makespan}')
        self.chain = chain
        return sorted(reduced.edges(), key=lambda e: (self.position[e[0]], self.position[e[1]]))

    def _interference_edges(self):
        """Pairs whose effects interfere, under the encoder's own action model."""
        import htnorder
        grounded = htnorder.get_grounded_actions_from_trace(self.domain_text, self.actual_text)
        acts = [p for p in self.prims if p in grounded]
        out = set()
        for a, b in itertools.combinations(acts, 2):
            ga, gb = grounded[a], grounded[b]
            if (ga['add_effects'] & gb['delete_effects']) or (gb['add_effects'] & ga['delete_effects']):
                if self.starts[a] < self.starts[b]:
                    out.add((a, b))
                elif self.starts[b] < self.starts[a]:
                    out.add((b, a))
        return out

    # -- output --------------------------------------------------------------

    @staticmethod
    def oid(o):
        return f'o{o}'

    def occ(self, o):
        name, args = self.call(o)
        return f'({self.oid(o)} ({" ".join([name] + list(args))}))'

    @staticmethod
    def task_list(items):
        if not items:
            return '()'
        return '(and ' + ' '.join(items) + ')'

    def render(self):
        m = self.model
        out = [f'(define (plan ocelot)',
               f'  (:domain {m.domain_name})',
               f'  (:problem {m.problem_name})',
               '  (:decomposition']
        root = '    (:root'
        dec = self.dec
        if dec.root_params:
            root += ' :parameters (' + ' '.join(dec.root_params) + ')'
        root += ' :subtasks ' + self.task_list([self.occ(r) for r in dec.root_children]) + ')'
        out.append(root)
        queue = [r for r in dec.root_children if r in dec.decomps]
        while queue:
            t = queue.pop(0)
            task, args, mname, _ = dec.decomps[t]
            kids = dec.children[t]
            out.append(f'    {self.occ(t)[:-1]} :method ({" ".join([mname] + dec.method_args[t])})'
                       f' :subtasks {self.task_list([self.occ(k) for k in kids])})')
            queue.extend(k for k in kids if k in dec.decomps)
        out[-1] += ')'
        tasks = self.task_list([self.occ(p) for p in self.layered])
        ordering = self.task_list([f'(< {self.oid(a)} {self.oid(b)})' for a, b in self.order])
        out.append(f'  (:htn :tasks {tasks}')
        out.append(f'    :ordering {ordering}))')
        return '\n'.join(out) + '\n'


def build_certificate(domain_text, problem_text, actual_text, map_text, sol_text,
                      makespan=None, semantics='pocl'):
    model = Model(domain_text, problem_text)
    dec = Decomposition(model, actual_text)
    orders, starts, optimal = parse_maxsat_solution(map_text, sol_text)
    if makespan is None:
        real = [t for a, t in starts.items() if isinstance(a, int)]
        makespan = (max(real) + 1) if real else 0
    cert = Certificate(dec, orders, starts, makespan, semantics, domain_text, actual_text)
    cert.optimal = optimal
    return cert


def main(argv=None):
    p = argparse.ArgumentParser(description='Write an HDDL 1.1 partial-order plan certificate.')
    p.add_argument('--domain', required=True)
    p.add_argument('--problem', required=True)
    p.add_argument('--actual', required=True, help='cleaned PANDA plan (pandaPIparser -c)')
    p.add_argument('--map', required=True, help='MaxSAT variable map (.wcnf.map)')
    p.add_argument('--rc2out', required=True, help='RC2 output (.sol)')
    p.add_argument('--semantics', choices=('pocl', 'parallel'), default='pocl')
    p.add_argument('--makespan', type=int, default=None,
                   help='reported makespan the certificate must reproduce')
    p.add_argument('-o', '--output', required=True)
    a = p.parse_args(argv)

    read = lambda path: open(path).read()
    try:
        cert = build_certificate(read(a.domain), read(a.problem), read(a.actual),
                                 read(a.map), read(a.rc2out), a.makespan, a.semantics)
    except CertificateError as e:
        print(f'certificate error: {e}', file=sys.stderr)
        return 1
    with open(a.output, 'w') as f:
        f.write(cert.render())
    print(f'Certificate: {a.output}')
    print(f'Actions: {len(cert.prims)}')
    print(f'Orderings: {len(cert.order)}')
    print(f'Makespan (longest chain): {cert.chain}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
