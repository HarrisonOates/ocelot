"""HDDL 1.1 partial-order plan certificates (scripts/certificate.py).

The end-to-end cases run the whole pipeline, which writes `<base>.hddl` and
validates it with Bell, and then assert that Bell accepted it and that Bell's
makespan (longest primitive chain) equals OCELOT's reported final makespan.
They are marked `e2e` and need the planner binaries; Bell-dependent assertions
are skipped when no Bell binary is found (see run_planner.resolve_bell_path).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from scripts import certificate as cert_mod  # noqa: E402
from scripts.run_planner import (  # noqa: E402
    resolve_bell_path, resolve_tool_paths, run_pipeline, validate_certificate)

ACYCLIC = REPO / "domains" / "partial-order-acyclic"
BELL = resolve_bell_path()
MEM_MB = 2500

needs_bell = pytest.mark.skipif(BELL is None, reason="Bell validator not found")


def _tools():
    try:
        return resolve_tool_paths(REPO, None, None, None)
    except FileNotFoundError:
        return None


needs_binaries = pytest.mark.skipif(_tools() is None,
                                    reason="planner binaries not built -- run ./build.sh")


# ---------------------------------------------------------------------------
# Inline instances
# ---------------------------------------------------------------------------

# Two robots. Covers: genuinely parallel actions (r1 and r2 work at once), method
# preconditions (m-goto-move, m-report), an empty method with a precondition
# (m-goto-here), method parameters absent from the task head (?r of m-clean is
# bound only through the subtasks; ?r of m-report appears ONLY in the
# precondition, so it must be found by search), and negative preconditions.
CLEANERS_DOMAIN = """
(define (domain cleaners)
  (:requirements :typing :hierarchy :method-preconditions :negative-preconditions)
  (:types robot room - object)
  (:predicates (at ?r - robot ?x - room) (connected ?x ?y - room)
               (clean ?x - room) (reported ?x - room))
  (:task clean-room :parameters (?x - room))
  (:task goto :parameters (?r - robot ?to - room))
  (:task report :parameters (?x - room))
  (:method m-goto-here
    :parameters (?r - robot ?to - room)
    :task (goto ?r ?to)
    :precondition (at ?r ?to)
    :subtasks ())
  (:method m-goto-move
    :parameters (?r - robot ?from ?to - room)
    :task (goto ?r ?to)
    :precondition (and (at ?r ?from) (connected ?from ?to) (not (at ?r ?to)))
    :ordered-subtasks (move ?r ?from ?to))
  (:method m-clean
    :parameters (?x - room ?r - robot)
    :task (clean-room ?x)
    :ordered-subtasks (and (g (goto ?r ?x)) (w (wipe ?r ?x))))
  (:method m-report
    :parameters (?x - room ?r - robot)
    :task (report ?x)
    :precondition (and (clean ?x) (at ?r ?x))
    :subtasks (note ?x))
  (:action move
    :parameters (?r - robot ?from ?to - room)
    :precondition (and (at ?r ?from) (connected ?from ?to))
    :effect (and (not (at ?r ?from)) (at ?r ?to)))
  (:action wipe
    :parameters (?r - robot ?x - room)
    :precondition (at ?r ?x)
    :effect (clean ?x))
  (:action note
    :parameters (?x - room)
    :precondition ()
    :effect (reported ?x)))
"""

# r1 cleans a (already there: empty goto) and c (must move away from a, so the
# empty method's guard needs a placement before the move); r2 moves to d,
# cleans it, and d is then reported.
CLEANERS_PROBLEM = """
(define (problem two-robots)
  (:domain cleaners)
  (:objects r1 r2 - robot a b c d - room)
  (:htn
    :subtasks (and (t1 (clean-room a)) (t2 (clean-room d)) (t3 (report d))
                   (t4 (clean-room c)))
    :ordering (and (< t2 t3)))
  (:init (at r1 a) (at r2 b) (connected a c) (connected b d)))
"""

# The same instance under the IPC makespan objective, which Bell's default
# `ipc2027` profile requires before it accepts a partial order.
CLEANERS_DOMAIN_MAKESPAN = CLEANERS_DOMAIN.replace(
    "(:requirements :typing", "(:requirements :makespan :typing")
CLEANERS_PROBLEM_MAKESPAN = CLEANERS_PROBLEM.rstrip()[:-1] + "\n  (:metric minimize (makespan)))\n"

# The prepare task sets `ready` and then preps; `use` must come after prepare
# and its method requires `ready`; `spoil` (unordered) clears it. No ACTION
# needs `ready`, so the MaxSAT reorderer (which does not model method
# preconditions) may leave clear-ready unordered, which permits a
# linearization where m-use's guard can never be placed.
GUARD_DOMAIN = """
(define (domain guard)
  (:requirements :hierarchy :method-preconditions)
  (:predicates (ready) (prepped) (worked) (spoiled))
  (:task prepare :parameters ())
  (:task use :parameters ())
  (:task spoil :parameters ())
  (:method m-prepare :parameters () :task (prepare)
    :ordered-subtasks (and (set-ready) (prep)))
  (:method m-use :parameters () :task (use) :precondition (ready)
    :subtasks (work))
  (:method m-spoil :parameters () :task (spoil) :subtasks (clear-ready))
  (:action set-ready :parameters () :precondition () :effect (ready))
  (:action prep :parameters () :precondition () :effect (prepped))
  (:action work :parameters () :precondition () :effect (worked))
  (:action clear-ready :parameters () :precondition ()
    :effect (and (not (ready)) (spoiled))))
"""
GUARD_PROBLEM = """
(define (problem p) (:domain guard)
  (:htn :subtasks (and (t1 (prepare)) (t2 (use)) (t3 (spoil))) :ordering (and (< t1 t2)))
  (:init))
"""


def _write(tmp_path, domain_text, problem_text):
    d, p = tmp_path / "domain.hddl", tmp_path / "problem.hddl"
    d.write_text(domain_text)
    p.write_text(problem_text)
    return d, p


def _run(domain, problem, out, semantics="pocl", plan_output=None):
    return run_pipeline(domain, problem, out, _tools(), "rc2(prefixMakespanFast),rc2(ff)",
                        "makespan", 1, memory_limit_mb=MEM_MB, semantics=semantics,
                        plan_output=plan_output, bell=BELL, bell_timeout=300)


def _bell(domain, problem, plan, profile="hddl-1.1"):
    proc = subprocess.run([str(BELL), "--json", "--profile", profile,
                           str(domain), str(problem), str(plan)],
                          capture_output=True, text=True, timeout=300)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _orderings(cert_text):
    return re.findall(r"\(< ([^\s()]+) ([^\s()]+)\)", cert_text)


def _assert_certified(result, domain, problem, profile="hddl-1.1"):
    cert = Path(result["certificate"])
    assert cert.exists()
    text = cert.read_text()
    assert "__" not in text, "pandaPI compilation artefacts leaked into the certificate"
    assert result["bell"] == {"status": "valid", "makespan": result["makespan"],
                              "profile": profile}
    # Independent re-check, not relying on the pipeline's own parsing.
    report = _bell(domain, problem, cert, profile)
    assert report["status"] == "valid", report
    assert report["metrics"]["makespan"] == result["makespan"]
    return text


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@needs_binaries
@needs_bell
@pytest.mark.parametrize("semantics", ["pocl", "parallel"])
def test_inline_partial_order_certificate(tmp_path, semantics):
    domain, problem = _write(tmp_path, CLEANERS_DOMAIN, CLEANERS_PROBLEM)
    result = _run(domain, problem, tmp_path / "out", semantics)
    text = _assert_certified(result, domain, problem)

    n_actions = len(re.findall(r"\(o\d+ \((?:move|wipe|note) ", text.split("(:htn", 1)[1]))
    assert n_actions == 6
    # Genuinely parallel: fewer layers than actions, and the order is partial.
    assert result["makespan"] == 3 < n_actions
    # The empty method, with its (only) parameters from the task head.
    assert re.search(r"\(goto r1 a\) :method \(m-goto-here r1 a\) :subtasks \(\)\)", text)
    # ?r of m-report occurs only in its precondition: r2 is the robot at d.
    assert ":method (m-report d r2)" in text
    # ?r of m-clean is absent from the task head, bound through the subtasks.
    assert ":method (m-clean c r1)" in text and ":method (m-clean d r2)" in text


@pytest.mark.e2e
@needs_binaries
@needs_bell
def test_makespan_objective_passes_ipc2027_profile(tmp_path):
    domain, problem = _write(tmp_path, CLEANERS_DOMAIN_MAKESPAN, CLEANERS_PROBLEM_MAKESPAN)
    plan = tmp_path / "custom" / "plan.hddl"
    plan.parent.mkdir()
    result = _run(domain, problem, tmp_path / "out", plan_output=plan)
    assert result["certificate"] == plan.resolve()
    _assert_certified(result, domain, problem, profile="ipc2027")


@pytest.mark.e2e
@needs_binaries
@needs_bell
@pytest.mark.parametrize("semantics", ["pocl", "parallel"])
def test_rover_benchmark_certificate(tmp_path, semantics):
    d = ACYCLIC / "Rover"
    result = _run(d / "domain.hddl", d / "pfile01.hddl", tmp_path / "out", semantics)
    text = _assert_certified(result, d / "domain.hddl", d / "pfile01.hddl")
    assert result["makespan"] == 6
    # 10 actions in 6 layers: Bell's longest chain is shorter than the plan.
    assert result["makespan"] < len(re.findall(r"\(o\d+ \(", text.split("(:htn", 1)[1]))
    # Empty methods and a method parameter recovered from the precondition only
    # (?l and ?y of m-send_soil_data, absent from both head and subtasks).
    assert ":method (m-navigate_abs-2 rover0 waypoint3) :subtasks ()" in text
    assert ":method (m-send_soil_data rover0 waypoint2 waypoint2 waypoint0 general)" in text


@pytest.mark.e2e
@needs_binaries
@needs_bell
def test_unprotected_method_precondition_is_never_silently_accepted(tmp_path):
    """Either the certificate is Bell-valid, or the pipeline fails loudly with
    Bell's diagnostics. (The current reorderer does not protect method
    preconditions, so today this fails with `method_precondition`.)"""
    domain, problem = _write(tmp_path, GUARD_DOMAIN, GUARD_PROBLEM)
    try:
        result = _run(domain, problem, tmp_path / "out")
    except RuntimeError as e:
        assert "Bell rejected the plan certificate" in str(e)
        assert "method_precondition" in str(e)
        assert json.loads((tmp_path / "out.bell.json").read_text())["status"] == "invalid_plan"
    else:
        _assert_certified(result, domain, problem)


# ---------------------------------------------------------------------------
# Unit tests (no planner binaries)
# ---------------------------------------------------------------------------

UNIT_DOMAIN = """
(define (domain u)
  (:requirements :hierarchy)
  (:predicates (p) (q))
  (:task t :parameters ())
  (:method m :parameters () :task (t) :ordered-subtasks (and (a) (b)))
  (:action a :parameters () :precondition () :effect (p))
  (:action b :parameters () :precondition () :effect (q)))
"""
UNIT_PROBLEM = "(define (problem up) (:domain u) (:htn :subtasks (t)) (:init))"
# Children listed out of declaration order, plus a compiled artefact.
UNIT_ACTUAL = """==>
1 a
2 b
3 __method_precondition_m
root 4
4 t -> m 2 3 1
<==
"""


def _solution(starts, orders=()):
    labels = {1: "(a )#1", 2: "(b )#2"}
    mapping, true = {}, []
    for i, (a, t) in enumerate(starts.items(), start=1):
        mapping[str(i)] = f"{labels[a]} starts at {t}"
        true.append(str(i))
    for j, (a, b) in enumerate(orders, start=100):
        mapping[str(j)] = f"{labels[a]} -> {labels[b]}"
        true.append(str(j))
    mapping["99"] = "init#init -> (a )#1"
    true.append("99")
    return json.dumps(mapping), "s OPTIMUM FOUND\nv " + " ".join(true) + " 0\n"


def test_certificate_matches_children_and_drops_artefacts():
    map_text, sol = _solution({1: 0, 2: 1}, orders=[(1, 2)])
    cert = cert_mod.build_certificate(UNIT_DOMAIN, UNIT_PROBLEM, UNIT_ACTUAL, map_text, sol,
                                      makespan=2)
    text = cert.render()
    assert "__" not in text
    assert "(o4 (t) :method (m) :subtasks (and (o1 (a)) (o2 (b))))" in text
    assert _orderings(text) == [("o1", "o2")]
    assert cert.chain == 2


def test_inherited_ordering_is_added_even_if_solver_omitted_it():
    map_text, sol = _solution({1: 0, 2: 1})
    cert = cert_mod.build_certificate(UNIT_DOMAIN, UNIT_PROBLEM, UNIT_ACTUAL, map_text, sol,
                                      makespan=2)
    assert _orderings(cert.render()) == [("o1", "o2")]


def test_inherited_ordering_contradicting_layers_fails_loudly():
    map_text, sol = _solution({1: 0, 2: 0})
    with pytest.raises(cert_mod.CertificateError, match="inherited from the decomposition"):
        cert_mod.build_certificate(UNIT_DOMAIN, UNIT_PROBLEM, UNIT_ACTUAL, map_text, sol,
                                   makespan=1)


def test_makespan_mismatch_fails_loudly():
    map_text, sol = _solution({1: 0, 2: 1}, orders=[(1, 2)])
    with pytest.raises(cert_mod.CertificateError, match="longest chain"):
        cert_mod.build_certificate(UNIT_DOMAIN, UNIT_PROBLEM, UNIT_ACTUAL, map_text, sol,
                                   makespan=3)


SET_USE_DOMAIN = """
(define (domain su) (:requirements :hierarchy)
  (:predicates (ready))
  (:action set :parameters () :precondition () :effect (ready))
  (:action use :parameters () :precondition (ready) :effect ()))
"""
SET_USE_PROBLEM = "(define (problem sup) (:domain su) (:init))"
SET_USE_PLAN = """(define (plan x) (:domain su) (:problem sup)
  (:decomposition (:root :subtasks (and (s (set)) (u (use)))))
  (:htn :tasks (and (s (set)) (u (use))) :ordering {ordering}))
"""


@needs_bell
@pytest.mark.parametrize("ordering,expected,error", [
    ("(and (< s u))", 2, None),
    ("()", 2, "action_precondition|missing_support"),
    ("(and (< s u))", 1, "Bell reports makespan 2"),
])
def test_validate_certificate_fails_loudly(tmp_path, ordering, expected, error):
    domain, problem = _write(tmp_path, SET_USE_DOMAIN, SET_USE_PROBLEM)
    plan = tmp_path / "plan.hddl"
    plan.write_text(SET_USE_PLAN.format(ordering=ordering))
    args = (BELL, domain, problem, plan, expected, tmp_path / "log", tmp_path / "bell.json")
    if error is None:
        assert validate_certificate(*args)["makespan"] == 2
    else:
        with pytest.raises(RuntimeError, match=error):
            validate_certificate(*args)
