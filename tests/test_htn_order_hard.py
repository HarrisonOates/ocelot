"""HTN orderings must stay hard constraints in the MaxSAT reorderer.

htnpop seeds causal links from the engine's sequential trace and then adds the
orderings the hierarchy requires. The encoder only turns `htn_order`-tagged
edges into hard clauses. When a required ordering coincided with a seeded
causal link, it was never tagged, so the solver could switch the consumer to
another supporter and drop an ordering the decomposition requires.
"""
import re
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

# Causal links force the trace c, d, a, b, so b's seeded supporter of p is a:
# the same pair as the hierarchy's (< a b). c also adds p, so without the hard
# ordering the solver can support b from c and run it at time 1.
DOMAIN = """
(define (domain ord) (:requirements :hierarchy)
  (:predicates (p) (q) (r))
  (:task root :parameters ())
  (:method m :parameters () :task (root)
    :subtasks (and (tc (c)) (td (d)) (ta (a)) (tb (b)))
    :ordering (and (< ta tb)))
  (:action c :parameters () :precondition (and) :effect (and (p) (r)))
  (:action d :parameters () :precondition (and (r)) :effect (and (q)))
  (:action a :parameters () :precondition (and (q)) :effect (and (p)))
  (:action b :parameters () :precondition (and (p)) :effect (and)))
"""

PROBLEM = """
(define (problem o) (:domain ord)
  (:htn :parameters () :subtasks (and (t (root))))
  (:init))
"""


def layers(path: Path) -> dict:
    """Map action name -> solved layer from an analyzer .layers file."""
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"\s*\d+\s+(\S+)\s+\[(\d+)\]", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


@pytest.mark.parametrize("semantics", ["pocl", "parallel"])
def test_htn_ordering_on_a_seeded_causal_link_stays_hard(tmp_path, semantics):
    domain, problem, base = tmp_path / "domain.hddl", tmp_path / "problem.hddl", tmp_path / "out"
    domain.write_text(DOMAIN)
    problem.write_text(PROBLEM)
    tools = resolve_tool_paths(REPO, engine=None, parser=None, grounder=None)
    result = run_pipeline(domain, problem, base, tools, "zero", "makespan", 1, semantics=semantics)

    solved = layers(Path(f"{base}.layers"))
    assert solved["a"] < solved["b"], solved
    assert result["makespan"] == 4, result
