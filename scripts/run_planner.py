#!/usr/bin/env python3
# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.
"""Ocelot — PANDA planning pipeline runner."""

import argparse
import os
import re
import resource
import subprocess
import sys
from pathlib import Path

PANDA_ROOT = Path(__file__).parent.parent

_DEFAULT_TOOLS = {
    "engine":   PANDA_ROOT / "pandaPIengine" / "build" / "pandaPIengine",
    "parser":   PANDA_ROOT / "pandaPIparser" / "pandaPIparser",
    "grounder": PANDA_ROOT / "pandaPIgrounder" / "pandaPIgrounder",
}


def _memory_limit_preexec(limit_bytes: int):
    def fn():
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    return fn


def resolve_tool_paths(panda_root: Path, engine, parser, grounder) -> dict:
    defaults = {
        "engine":   panda_root / "pandaPIengine" / "build" / "pandaPIengine",
        "parser":   panda_root / "pandaPIparser" / "pandaPIparser",
        "grounder": panda_root / "pandaPIgrounder" / "pandaPIgrounder",
    }
    overrides = {"engine": engine, "parser": parser, "grounder": grounder}
    paths = {}
    for name, override in overrides.items():
        p = Path(override) if override else defaults[name]
        if not p.exists():
            raise FileNotFoundError(f"{name} binary not found at {p}")
        paths[name] = p
    return paths


def detect_mode(args: list) -> str:
    if len(args) == 2 and Path(args[0]).is_dir():
        return "batch"
    if len(args) == 3 and not Path(args[0]).is_dir():
        return "single"
    print("Usage: ocelot domain.hddl problem.hddl output_basename [OPTIONS]")
    print("       ocelot domain_dir/ output_dir/ [OPTIONS]")
    sys.exit(1)


def parse_timing_log(log_path: Path) -> dict:
    """Parse a timing CSV log. Returns dict of step_name -> {wall_s, rss_kb}."""
    result = {}
    for line in log_path.read_text().splitlines():
        parts = line.strip().split(",")
        if len(parts) == 3:
            name, wall, rss = parts
            result[name] = {"wall_s": float(wall), "rss_kb": int(rss)}
    return result


def parse_engine_output(stdout: str) -> dict:
    """Extract nodes, nodes/sec, and makespan from engine stdout."""
    nodes = None
    nodes_per_sec = None
    makespan = None

    m = re.search(r"Generated (\d+) search nodes", stdout)
    if m:
        nodes = int(m.group(1))

    m = re.search(r"Generated (\d+) nodes per second", stdout)
    if m:
        nodes_per_sec = int(m.group(1))

    m = re.search(r"Plan makespan: (\d+)", stdout)
    if m:
        makespan = int(m.group(1))

    return {"nodes": nodes, "nodes_per_sec": nodes_per_sec, "makespan": makespan}


def parse_analyzer_output(stdout: str) -> dict:
    """Extract the final, POP-derived makespan and action count from analyzer.py's output.

    This is the actual optimized/verified plan quality (from the solved MaxSAT
    schedule), as opposed to the engine's own internal search g-value parsed by
    parse_engine_output -- the two can differ and only this one is the real
    answer to report to a user.
    """
    makespan = None
    num_actions = None
    optimal = None

    m = re.search(r"Makespan \(Critical Path Length\):\s*(\d+)", stdout)
    if m:
        makespan = int(m.group(1))

    m = re.search(r"Number of Actions:\s*(\d+)", stdout)
    if m:
        num_actions = int(m.group(1))

    m = re.search(r"Optimal:\s*(True|False)", stdout)
    if m:
        optimal = (m.group(1) == "True")

    return {"makespan": makespan, "num_actions": num_actions, "optimal": optimal}


def run_step(step_name: str, cmd: list, log_path: Path, stdout_file: Path = None,
             cwd: Path = None, memory_limit_mb: int = None) -> subprocess.CompletedProcess:
    """Run cmd timed with /usr/bin/time, appending to log_path."""
    preexec = _memory_limit_preexec(memory_limit_mb * 1024 * 1024) if memory_limit_mb else None
    time_cmd = [
        "/usr/bin/time", "-f", f"{step_name},%e,%M", "-o", str(log_path), "-a", "--",
    ] + [str(c) for c in cmd]
    if stdout_file:
        with open(stdout_file, "w") as fout:
            result = subprocess.run(time_cmd, stdout=fout, stderr=subprocess.PIPE,
                                    text=True, cwd=cwd, preexec_fn=preexec)
    else:
        result = subprocess.run(time_cmd, capture_output=True, text=True, cwd=cwd,
                                preexec_fn=preexec)
    return result


def run_search_step(engine: Path, sas: Path, original: Path, log_path: Path,
                    heuristic: str, g_value: str, weight: int,
                    memory_limit_mb: int = None) -> str:
    """Run the engine, write stdout to original file, return stdout for parsing."""
    preexec = _memory_limit_preexec(memory_limit_mb * 1024 * 1024) if memory_limit_mb else None
    cmd = [str(engine), "-g", g_value, "--astarweight", str(weight), "-H", heuristic, str(sas)]
    time_cmd = [
        "/usr/bin/time", "-f", "InitialEngine,%e,%M", "-o", str(log_path), "-a", "--",
    ] + cmd
    result = subprocess.run(time_cmd, capture_output=True, text=True, preexec_fn=preexec)
    original.write_text(result.stdout)
    return result.stdout


def run_pipeline(domain: Path, problem: Path, basename: Path,
                 tools: dict, heuristic: str, g_value: str, weight: int,
                 memory_limit_mb: int = None,
                 semantics: str = "pocl") -> dict:
    """
    Run all 7 pipeline steps for one problem.
    Returns dict with nodes, nodes_per_sec, makespan, search_s, total_s.
    Raises RuntimeError on step failure.
    """
    scripts_dir = Path(__file__).resolve().parent
    domain = domain.resolve()
    problem = problem.resolve()
    basename = basename.resolve()
    basename.parent.mkdir(parents=True, exist_ok=True)
    log = basename.parent / (basename.name + ".log")
    log.unlink(missing_ok=True)

    htn        = Path(str(basename) + ".htn")
    sas        = Path(str(basename) + ".sas")
    orig       = Path(str(basename) + ".original")
    act        = Path(str(basename) + ".actual")
    layers     = Path(str(basename) + ".layers")
    wcnf       = Path(str(basename) + ".wcnf")
    sol        = Path(str(basename) + ".sol")
    stats_file = Path(str(basename) + ".stats")

    ml = memory_limit_mb

    r = run_step("Parsing", [tools["parser"], domain, problem, htn], log, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Parsing failed:\n{r.stderr.strip()}")

    r = run_step("Grounding", [tools["grounder"], htn, sas], log, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Grounding failed:\n{r.stderr.strip()}")

    engine_stdout = run_search_step(tools["engine"], sas, orig, log, heuristic, g_value, weight,
                                    memory_limit_mb=ml)
    engine_stats = parse_engine_output(engine_stdout)
    if engine_stats["makespan"] is None:
        raise RuntimeError("Search found no solution")

    r = run_step("Cleaning", [tools["parser"], "-c", orig, act], log, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Cleaning failed:\n{r.stderr.strip()}")

    r = run_step("Encoding", [sys.executable, scripts_dir / "htnpop.py",
                               act, domain, problem, "-o", wcnf,
                               "--semantics", semantics], log, cwd=scripts_dir,
                 memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Encoding failed:\n{r.stderr.strip()}")

    r = run_step("Solving", [sys.executable, "-m", "pysat.examples.rc2", "-vv", wcnf],
                 log, stdout_file=sol, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Solving failed:\n{r.stderr.strip()}")

    r = run_step("Analysis", [sys.executable, scripts_dir / "analyzer.py",
                               "--map", str(wcnf) + ".map",
                               "--rc2out", sol, "--show-popstats",
                               "--actual", act, "--layers", layers],
                 log, stdout_file=stats_file, cwd=scripts_dir, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Analysis failed:\n{r.stderr.strip()}")

    analyzer_stats = parse_analyzer_output(stats_file.read_text())
    if analyzer_stats["makespan"] is None:
        raise RuntimeError(f"Could not parse final makespan from {stats_file}")

    timing = parse_timing_log(log)
    total = sum(v["wall_s"] for v in timing.values())
    search_s = timing.get("InitialEngine", {}).get("wall_s", 0.0)

    return {
        "nodes": engine_stats["nodes"],
        "nodes_per_sec": engine_stats["nodes_per_sec"],
        "makespan": analyzer_stats["makespan"],
        "engine_makespan": engine_stats["makespan"],
        "search_s": search_s,
        "total_s": total,
    }


def print_summary(results: list):
    """Print a formatted summary table to stdout."""
    header = f"{'problem':<12} {'nodes':>8} {'nodes/s':>9} {'search(s)':>10} {'total(s)':>9} {'makespan':>9}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        name = r["problem"]
        if r.get("failed"):
            print(f"{name:<12} {'FAILED':>8}")
        else:
            nodes_s = str(r['nodes']) if r['nodes'] is not None else "N/A"
            nps_s   = str(r['nodes_per_sec']) if r['nodes_per_sec'] is not None else "N/A"
            print(
                f"{name:<12} "
                f"{nodes_s:>8} "
                f"{nps_s:>9} "
                f"{r['search_s']:>10.2f} "
                f"{r['total_s']:>9.2f} "
                f"{r['makespan']:>9}"
            )


def run_batch(domain_dir: Path, output_dir: Path,
              tools: dict, heuristic: str, g_value: str, weight: int,
              memory_limit_mb: int = None,
              semantics: str = "pocl"):
    """Discover pfile*.hddl in domain_dir and run the pipeline for each."""
    domain_hddl = domain_dir / "domain.hddl"
    if not domain_hddl.exists():
        print(f"ERROR: no domain.hddl found in {domain_dir}", file=sys.stderr)
        sys.exit(1)

    problems = sorted(domain_dir.glob("pfile*.hddl"))
    if not problems:
        print(f"ERROR: no pfile*.hddl found in {domain_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for prob in problems:
        name = prob.stem
        basename = output_dir / name
        print(f"Running {name}...", flush=True)
        try:
            stats = run_pipeline(domain_hddl, prob, basename, tools, heuristic, g_value, weight,
                                 semantics=semantics,
                                 memory_limit_mb=memory_limit_mb)
            results.append({"problem": name, **stats})
            print(f"  makespan={stats['makespan']}  nodes={stats['nodes']}  total={stats['total_s']:.2f}s")
        except RuntimeError as e:
            print(f"  WARNING: {name} failed: {e}", file=sys.stderr)
            results.append({"problem": name, "failed": True})

    print_summary(results)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ocelot",
        description="Run the PANDA partial-order planning pipeline.",
    )
    p.add_argument("args", nargs="+", metavar="ARG",
                   help="domain.hddl problem.hddl output  OR  domain_dir/ output_dir/")
    p.add_argument("--heuristic", default="rc2(prefixMakespanFast),rc2(ff)",
                   help="Heuristic string passed to -H (default: rc2(prefixMakespanFast),rc2(ff))")
    p.add_argument("--g-value", default="makespan",
                   help="G-value mode passed to -g (default: makespan)")
    p.add_argument("--weight", type=int, default=1,
                   help="A* weight (default: 1)")
    p.add_argument("--semantics", choices=("pocl", "parallel"), default="pocl",
                   help="MaxSAT scheduling semantics (default: pocl)")
    p.add_argument("--memory-limit", type=int, default=8192, metavar="MB",
                   help="Per-process virtual memory limit in MB (default: 8192). "
                        "Set to 0 to disable.")
    p.add_argument("--engine",   default=None, help="Override engine binary path")
    p.add_argument("--parser",   default=None, help="Override parser binary path")
    p.add_argument("--grounder", default=None, help="Override grounder binary path")
    return p


def main():
    parser = build_arg_parser()
    ns = parser.parse_args()

    tools = resolve_tool_paths(PANDA_ROOT, ns.engine, ns.parser, ns.grounder)
    mode  = detect_mode(ns.args)
    mem   = ns.memory_limit or None

    if mode == "single":
        domain, problem, basename = Path(ns.args[0]), Path(ns.args[1]), Path(ns.args[2])
        try:
            result = run_pipeline(domain, problem, basename, tools,
                                  ns.heuristic, ns.g_value, ns.weight,
                                  semantics=ns.semantics,
                                  memory_limit_mb=mem)
            print(f"Done. makespan={result['makespan']}  "
                  f"nodes={result['nodes']}  total={result['total_s']:.2f}s")
        except RuntimeError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        run_batch(Path(ns.args[0]), Path(ns.args[1]), tools,
                  ns.heuristic, ns.g_value, ns.weight, semantics=ns.semantics,
                  memory_limit_mb=mem)
