"""Unit tests for scripts/htnorder.py -- HTN ordering semantics and the
compilation of negative preconditions into complementary facts.

Fast: no planner binaries required.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import htnorder

REPO = Path(__file__).parent.parent
MEDICAL = REPO / "domains" / "partial-order-acyclic" / "Medical"


# --------------------------------------------------------------------------
# negative precondition compilation
# --------------------------------------------------------------------------

def test_negate_fact_is_an_involution():
    f = ("busy", "nurse_0")
    assert htnorder.negate_fact(f) == ("-busy", "nurse_0")
    assert htnorder.negate_fact(htnorder.negate_fact(f)) == f


def test_is_negative_fact():
    assert htnorder.is_negative_fact(("-busy", "n0"))
    assert not htnorder.is_negative_fact(("busy", "n0"))


def test_only_negatively_used_predicates_get_a_complement():
    """A predicate never used under `not` must not gain a complementary fact,
    otherwise the fluent space doubles and spurious threats appear."""
    domain = (MEDICAL / "domain.hddl").read_text()
    negated = htnorder.get_negatively_used_predicates(domain)
    # `busy` is negated by do_clean_patient et al; `patient-clean` by m0_clean_patient
    assert negated == {"busy", "patient-clean"}
    # these are only ever used positively
    assert "tools-ready" not in negated
    assert "patient-ready" not in negated
    assert "operated" not in negated


def test_negative_preconditions_are_compiled_not_dropped():
    """`(not (busy ?d))` must survive grounding as a positive requirement on the
    complementary fact -- dropping it silently removes resource exclusivity."""
    domain = (MEDICAL / "domain.hddl").read_text()
    actions = htnorder.get_classical_problem_components(domain)["actions"]

    do_op = actions["do_operation"]
    assert ("-busy", "?d") in do_op["preconditions"]
    assert ("tools-ready", "?t") in do_op["preconditions"]
    # nothing should survive as a raw ('not', ...) term
    assert not any(p[0] == "not" for p in do_op["preconditions"])


def test_complementary_facts_are_maintained_by_effects():
    """Adding `busy` must delete `-busy`, and deleting `busy` must add `-busy`,
    or the complement goes stale and causal links break."""
    domain = (MEDICAL / "domain.hddl").read_text()
    actions = htnorder.get_classical_problem_components(domain)["actions"]

    clean = actions["do_clean_patient"]
    assert ("busy", "?n") in clean["add_effects"]
    assert ("-busy", "?n") in clean["delete_effects"]

    liberate = actions["liberate"]
    assert ("busy", "?a") in liberate["delete_effects"]
    assert ("-busy", "?a") in liberate["add_effects"]


def test_positive_only_predicate_has_no_complement_effects():
    """`tools-ready` is never negated, so do_operation must not invent
    `-tools-ready` bookkeeping for it."""
    domain = (MEDICAL / "domain.hddl").read_text()
    actions = htnorder.get_classical_problem_components(domain)["actions"]
    do_op = actions["do_operation"]
    assert ("tools-ready", "?t") in do_op["delete_effects"]
    assert ("-tools-ready", "?t") not in do_op["add_effects"]


def test_initial_state_supplies_closed_world_complements():
    """Under the closed-world assumption `-busy[n]` holds initially iff
    `busy[n]` does not -- otherwise negative preconditions have no producer."""
    domain = (MEDICAL / "domain.hddl").read_text()
    problem = (MEDICAL / "pfile1.hddl").read_text()

    positive = {tuple(l) for l in htnorder.get_initial_state(problem)}
    # nobody is busy in the initial state of any Medical problem
    assert not any(f[0] == "busy" for f in positive)

    grounded = {
        0: {
            "preconditions": {("-busy", "nurse_0")},
            "add_effects": {("busy", "nurse_0")},
            "delete_effects": {("-busy", "nurse_0")},
        }
    }
    state = htnorder.get_initial_state_with_negatives(problem, grounded)
    assert ("-busy", "nurse_0") in state
    assert ("busy", "nurse_0") not in state
    assert positive <= state, "positive literals must be preserved"


def test_complement_absent_when_positive_form_holds_initially():
    problem = "(define (problem p) (:domain d) (:init (busy nurse_0)) )"
    grounded = {0: {"preconditions": {("-busy", "nurse_0")},
                    "add_effects": set(), "delete_effects": set()}}
    state = htnorder.get_initial_state_with_negatives(problem, grounded)
    assert ("busy", "nurse_0") in state
    assert ("-busy", "nurse_0") not in state


# --------------------------------------------------------------------------
# HTN ordering semantics
# --------------------------------------------------------------------------

TRACE_UNORDERED_SIBLINGS = """==>
1 do_clean_patient patient_0 nurse_0
2 liberate nurse_0
3 do_operation patient_0 doctor_0 toolset_0
4 liberate doctor_0
root 10
10 operate patient_0 -> m0_operate 11 12 13 14 15
11 prepare_tools toolset_0 -> m0_prepare_tools 20 21
12 prepare_patient patient_0 -> m0_prepare_patient 22 23
13 clean_patient patient_0 -> m0_clean_patient 1 2
14 perform_operation patient_0 doctor_0 toolset_0 -> m0_perform_operation 3 4
15 clean_patient patient_0 -> m0_clean_patient 30 31
"""


def test_ordering_is_a_full_cross_product_over_descendants():
    """`(< task12 task2)` between two compound subtasks means every primitive
    descendant of task12 precedes every primitive descendant of task2 -- so
    `liberate` (an unordered sibling of do_clean_patient) is included."""
    domain = (MEDICAL / "domain.hddl").read_text()
    problem = (MEDICAL / "pfile1.hddl").read_text()
    constraints = set(htnorder.find_implied_constraints(
        TRACE_UNORDERED_SIBLINGS, domain, problem))

    # clean_patient (13) -> {1 do_clean_patient, 2 liberate}
    # perform_operation (14) -> {3 do_operation, 4 liberate}
    for pred in (1, 2):
        for succ in (3, 4):
            assert (pred, succ) in constraints, \
                f"missing cross-product edge {pred} < {succ}"


def test_get_all_primitive_subtasks_recurses():
    prim, hier, _ = htnorder.parse_htn_trace(TRACE_UNORDERED_SIBLINGS)
    assert htnorder.get_all_primitive_subtasks(13, hier, prim) == {1, 2}
    # the root task collects every primitive below it
    assert {1, 2, 3, 4} <= htnorder.get_all_primitive_subtasks(10, hier, prim)


def test_constraints_are_acyclic():
    """The emitted constraint set must be a DAG; the encoder turns each edge
    into a hard Order() clause, so a cycle makes the WCNF unsatisfiable."""
    import networkx as nx
    domain = (MEDICAL / "domain.hddl").read_text()
    problem = (MEDICAL / "pfile1.hddl").read_text()
    edges = htnorder.find_implied_constraints(TRACE_UNORDERED_SIBLINGS, domain, problem)
    g = nx.DiGraph()
    g.add_edges_from(edges)
    assert nx.is_directed_acyclic_graph(g)
