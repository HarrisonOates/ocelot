"""Duplicate pruning in makespan mode must compare the whole temporal state.

Two search nodes can share a logical state and task network while differing in
when facts became true, were last needed, or were last deleted. A node with a
smaller plan makespan so far may still have a worse completion, so pruning on
the scalar makespan alone can discard the optimal plan. A node may only prune
another if it is no later in every component of the timing state.

The instance was found by differential fuzzing (visited list on vs. off).
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))

from scripts.run_planner import resolve_tool_paths, run_pipeline


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

DOMAIN = """
(define (domain dup) (:requirements :hierarchy :negative-preconditions)
  (:predicates (p0) (p1))
  (:task root :parameters ())
  (:task t1 :parameters ())
  (:task t2 :parameters ())
  (:method m_root :parameters () :task (root) :subtasks (and (s0 (t1))))
  (:method m_t1 :parameters () :task (t1)
    :subtasks (and (s0 (t2)) (s1 (a5)) (s2 (a0)) (s3 (a2)))
    :ordering (and (< s0 s3) (< s1 s3) (< s2 s3)))
  (:method m_t2_short :parameters () :task (t2) :subtasks (and (s0 (a5))))
  (:method m_t2_long :parameters () :task (t2) :subtasks (and (s0 (a1)) (s1 (a5))))
  (:action a0 :parameters () :precondition (and (p1)) :effect (and (p1) (p0)))
  (:action a1 :parameters () :precondition (and (p0)) :effect (and (p0)))
  (:action a2 :parameters () :precondition (and) :effect (and (not (p1))))
  (:action a5 :parameters () :precondition (and) :effect (and (p1) (not (p0)))))
"""

PROBLEM = """
(define (problem q) (:domain dup)
  (:htn :parameters () :subtasks (and (r (root))))
  (:init (p0) (p1)))
"""

HEURISTICS = [
    pytest.param("zero", id="h0"),
    pytest.param("rc2(prefixMakespan)", id="replay"),
    pytest.param("rc2(prefixMakespan),rc2(ff)", id="replay-ff"),
    pytest.param("rc2(prefixMakespanFast)", id="fast"),
]


@pytest.mark.parametrize("heuristic", HEURISTICS)
def test_visited_list_keeps_temporally_better_duplicate(tmp_path, heuristic):
    """a5, a5 and a0 fit in layer 0 and a2 in layer 1, so the optimum is 2."""
    domain, problem, base = tmp_path / "domain.hddl", tmp_path / "problem.hddl", tmp_path / "out"
    domain.write_text(DOMAIN)
    problem.write_text(PROBLEM)
    tools = resolve_tool_paths(REPO, engine=None, parser=None, grounder=None)
    result = run_pipeline(domain, problem, base, tools, heuristic, "makespan", 1)

    verified = subprocess.run(
        [str(tools["parser"]), "-v", str(domain), str(problem), f"{base}.actual"],
        capture_output=True, text=True, timeout=30,
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", verified.stdout + verified.stderr)
    assert "Plan verification result: true" in output, output
    assert result["engine_makespan"] == 2, result
    assert result["makespan"] == 2, result
