"""Regressions for continuous fact availability in the shared search tracker.

These tiny instances have directly established POCL optima. Check the final
MaxSAT result, not just the engine estimate: choosing the wrong root method
cannot be repaired by reordering its primitives afterwards.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from scripts.run_planner import resolve_tool_paths, run_pipeline


HEURISTICS = [
    pytest.param("zero", id="h0"),
    pytest.param("rc2(prefixMakespan)", id="replay"),
    pytest.param("rc2(prefixMakespan),rc2(ff)", id="replay-ff"),
    pytest.param("rc2(prefixMakespan),rc2(lmc)", id="replay-lmc"),
    pytest.param("rc2(prefixMakespanFast)", id="fast"),
    pytest.param("rc2(prefixMakespanFast),rc2(ff)", id="fast-ff"),
    pytest.param("rc2(prefixMakespanFast),rc2(lmc)", id="fast-lmc"),
]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not all((REPO / path).is_file() for path in (
            "pandaPIparser/pandaPIparser",
            "pandaPIgrounder/pandaPIgrounder",
            "pandaPIengine/build/pandaPIengine",
        )),
        reason="planner binaries not built -- run ./build.sh",
    ),
]


def solve(tmp_path, domain_text, initial, heuristic):
    domain = tmp_path / "domain.hddl"
    problem = tmp_path / "problem.hddl"
    base = tmp_path / "out"
    domain.write_text(domain_text)
    problem.write_text(
        "(define (problem p) (:domain readd) "
        "(:htn :parameters () :subtasks (and (t (root)))) "
        f"(:init {initial}))\n"
    )
    tools = resolve_tool_paths(REPO, engine=None, parser=None, grounder=None)
    result = run_pipeline(domain, problem, base, tools, heuristic, "makespan", 1)

    # A successful encoding alone is not a plan-validity check. Verify the
    # returned decomposition and executable primitive sequence against HDDL.
    verified = subprocess.run(
        [str(tools["parser"]), "-v", str(domain), str(problem), f"{base}.actual"],
        capture_output=True, text=True, timeout=30,
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", verified.stdout + verified.stderr)
    assert verified.returncode == 0, output
    assert "Plan verification result: true" in output, output
    return result


@pytest.mark.parametrize("heuristic", HEURISTICS)
def test_readd_selects_better_final_decomposition(tmp_path, heuristic):
    """Three readers/re-adders fit at time 0; their deleter fits at time 1.

    A one-layer plan is impossible because deletion threatens every reader's
    initial support. The other root method is an explicit three-action chain,
    so the global optimum is exactly 2. Keeping the deleter in the first method
    prevents the front end from simplifying the initially true facts away.
    """
    result = solve(tmp_path, """
(define (domain readd)
  (:requirements :hierarchy :typing :negative-preconditions)
  (:predicates (p0) (p1) (p2) (r) (s))
  (:task root :parameters ())
  (:method concurrent :parameters () :task (root)
    :subtasks (and (t0 (a0)) (t1 (a1)) (t2 (a2)) (td (d))))
  (:method chain :parameters () :task (root)
    :ordered-subtasks (and (x) (y) (z)))
  (:action a0 :parameters () :precondition (p0) :effect (and (p1) (p2)))
  (:action a1 :parameters () :precondition (p1) :effect (and (p0) (p2)))
  (:action a2 :parameters () :precondition (p2) :effect (and (p0) (p1)))
  (:action d :parameters () :precondition (and)
    :effect (and (not (p0)) (not (p1)) (not (p2))))
  (:action x :parameters () :precondition (and) :effect (r))
  (:action y :parameters () :precondition (r) :effect (s))
  (:action z :parameters () :precondition (s) :effect (and)))
""", "(p0) (p1) (p2)", heuristic)
    assert result["makespan"] == 2, result
    assert result["engine_makespan"] == 2, result


@pytest.mark.parametrize("heuristic", HEURISTICS)
def test_delete_readd_starts_a_new_truth_interval(tmp_path, heuristic):
    """The reader cannot retain support from the initial truth interval.

    HTN ordering puts the delete before the reader. Restore requires the
    delete's q effect, and the reader then needs the restored p. These causal
    links force delete -> restore -> reader, hence optimum 3, even though only
    delete -> reader is explicitly ordered in the HTN.
    """
    result = solve(tmp_path, """
(define (domain readd)
  (:requirements :hierarchy :typing :negative-preconditions)
  (:predicates (p) (q))
  (:task root :parameters ())
  (:method reset :parameters () :task (root)
    :subtasks (and (td (delete)) (ts (restore)) (tr (reader)))
    :ordering (and (< td tr)))
  (:action delete :parameters () :precondition (and)
    :effect (and (q) (not (p))))
  (:action restore :parameters () :precondition (q) :effect (p))
  (:action reader :parameters () :precondition (p) :effect (and)))
""", "(p)", heuristic)
    assert result["makespan"] == 3, result
    assert result["engine_makespan"] == 3, result
