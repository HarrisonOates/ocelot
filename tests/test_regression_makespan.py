"""End-to-end makespan regression tests.

Every expected value below was independently verified with Aries
(`aries-plan -d DOMAIN PROBLEM --optimize makespan`, counting distinct timesteps
in its final plan) as the *proven optimal* makespan for that problem.

Each case asserts three things agree:

  1. the engine's own `Plan makespan:` (its A* g-value, maintained by
     `Model::apply`),
  2. the pipeline's reconstructed `Makespan (Critical Path Length)` (POP +
     MaxSAT), and
  3. the known optimum.

A disagreement between (1) and (2) is the signature of the bug class fixed on
branch `fix-makespan-fringe`: the engine's tracker computing an "exact" g-value
over an incomplete or over-strict constraint set. Three separate defects showed
up exactly this way -- missing threat resolution (engine too low), reader
serialisation (engine too high), and a fact-level rather than link-level
producer/deleter mutex (engine too high). See MAKESPAN_HEURISTIC_INVESTIGATION.md.

Marked `e2e`: needs the C++ binaries built (`./build.sh`). Skipped otherwise.
Run just these with `pytest -m e2e`, or skip them with `pytest -m "not e2e"`.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
ACYCLIC = REPO / "domains" / "partial-order-acyclic"
TESTDOM = REPO / "domains" / "test"

BINARIES = [
    REPO / "pandaPIparser" / "pandaPIparser",
    REPO / "pandaPIengine" / "build" / "pandaPIengine",
]

pytestmark = pytest.mark.e2e


def _grounder() -> Path | None:
    for candidate in (REPO / "scripts" / "pandaPIgrounder",
                      REPO / "pandaPIgrounder" / "pandaPIgrounder"):
        if candidate.exists():
            return candidate
    return None


missing_binaries = pytest.mark.skipif(
    not all(b.exists() for b in BINARIES) or _grounder() is None,
    reason="planner binaries not built -- run ./build.sh",
)


# (domain dir, problem stem, proven optimal makespan, expected action count)
CASES = [
    # The original regression: before the threat-resolution fix the engine
    # claimed 10 for plans that genuinely need 11, and picked a suboptimal plan.
    ("Medical", "pfile5", 10, 20),
    ("Medical", "pfile1", 8, 10),
    ("Medical", "pfile2", 6, 10),
    # Rover exercises zero-primitive method decompositions (m-navigate_abs-2)
    # and was where reader serialisation inflated the engine's value 6 -> 8.
    ("Rover", "pfile01", 6, 12),
    ("Postman", "pfile1", 4, 7),
    ("Barman-BDI", "pfile01", 10, 10),
    ("Oven", "pfile1", 3, 9),
    ("Oven", "pfile2", 3, 12),
    ("Satellite", "1obs-1sat-1mod", 5, 5),
    ("Satellite", "2obs-1sat-1mod", 7, 7),
]


def run_pipeline(domain: Path, problem: Path, out_base: Path, timeout: int = 600):
    """Run the whole ocelot pipeline, returning (engine_makespan, pipeline_makespan, n_actions)."""
    # scripts/run_planner.py has no __main__ guard, so drive main() directly
    # rather than via -m (which would silently do nothing).
    cmd = [sys.executable, "-c",
           "import sys; from scripts.run_planner import main; sys.argv[0]='ocelot'; main()",
           str(domain), str(problem), str(out_base)]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)

    original = Path(f"{out_base}.original")
    stats = Path(f"{out_base}.stats")
    if not original.exists() or not stats.exists():
        pytest.fail(f"pipeline produced no output for {problem.name}\n"
                    f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")

    def grab(pattern, text):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    engine = grab(r"Plan makespan:\s*(\d+)", original.read_text())
    stats_text = stats.read_text()
    pipeline = grab(r"Makespan \(Critical Path Length\):\s*(\d+)", stats_text)
    n_actions = grab(r"Number of Actions:\s*(\d+)", stats_text)
    return engine, pipeline, n_actions


@missing_binaries
@pytest.mark.parametrize("domain_dir,problem,expected,n_actions", CASES,
                         ids=[f"{d}-{p}" for d, p, _, _ in CASES])
def test_makespan_matches_known_optimum(domain_dir, problem, expected, n_actions, tmp_path):
    d = ACYCLIC / domain_dir
    engine, pipeline, actions = run_pipeline(
        d / "domain.hddl", d / f"{problem}.hddl", tmp_path / "out")

    assert engine is not None, "engine reported no plan makespan"
    assert pipeline is not None, "pipeline reported no makespan"

    # The two must agree: they are supposed to be measuring the same thing.
    assert engine == pipeline, (
        f"engine g-value ({engine}) disagrees with the reconstructed POP makespan "
        f"({pipeline}) for {domain_dir}/{problem} -- the engine's tracker and the "
        f"POP encoding have diverged")
    assert pipeline == expected, (
        f"{domain_dir}/{problem} makespan {pipeline}, expected the known optimum {expected}")
    assert actions == n_actions, (
        f"{domain_dir}/{problem} plan has {actions} actions, expected {n_actions}")


@missing_binaries
def test_threat_free_actions_stay_parallel(tmp_path):
    """Two actions in conflict over a fact that nothing requires carry no causal
    link, so there is no threat and no reason to order them: makespan must be 1.

    Guards against replacing link-level threat resolution with a fact-level
    producer/deleter mutex, which reports 2 here. See
    domains/test/pocl-neq-parallel/domain.hddl.
    """
    d = TESTDOM / "pocl-neq-parallel"
    engine, pipeline, actions = run_pipeline(
        d / "domain.hddl", d / "pfile01.hddl", tmp_path / "out")
    assert actions == 2
    assert pipeline == 1, (
        f"expected makespan 1 for two threat-free actions, got {pipeline} -- "
        f"ordering was imposed where no causal link needed protecting")
    assert engine == 1, f"engine reported {engine}, expected 1"
