#!/usr/bin/env python3
# Copyright (c) 2026 Harrison Oates. MIT License.
# See LICENSE at the project root.
"""Ocelot — PANDA planning pipeline runner."""

import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
from pathlib import Path

PANDA_ROOT = Path(__file__).parent.parent

_DEFAULT_TOOLS = {
    "engine":   PANDA_ROOT / "pandaPIengine" / "build" / "pandaPIengine",
    "parser":   PANDA_ROOT / "pandaPIparser" / "pandaPIparser",
    "grounder": PANDA_ROOT / "pandaPIgrounder" / "pandaPIgrounder",
}


def resolve_bell_path(override=None):
    """Locate the Bell validator: explicit path, $OCELOT_BELL, a sibling
    bell-htn checkout, then `bell` on PATH. Returns None if none exists."""
    if override:
        return Path(override) if Path(override).exists() else None
    candidates = []
    if os.environ.get("OCELOT_BELL"):
        candidates.append(Path(os.environ["OCELOT_BELL"]))
    candidates.append(PANDA_ROOT.parent / "bell-htn" / "target" / "release" / "bell")
    on_path = shutil.which("bell")
    if on_path:
        candidates.append(Path(on_path))
    for c in candidates:
        if c.exists():
            return c
    return None


def problem_declares_makespan(domain: Path, problem: Path) -> bool:
    """True if the instance declares the IPC makespan objective, so Bell's default
    `ipc2027` profile accepts a partial order for it."""
    d = re.sub(r";[^\n]*", "", domain.read_text()).lower()
    p = re.sub(r";[^\n]*", "", problem.read_text()).lower()
    return (re.search(r":requirements[^)]*:makespan", d) is not None
            and re.search(r"\(\s*:metric\s+minimize\s+\(\s*makespan\s*\)", p) is not None)


def validate_certificate(bell: Path, domain: Path, problem: Path, certificate: Path,
                         expected_makespan: int, log: Path, report_file: Path,
                         profile: str = "auto", timeout_s: int = 600,
                         memory_limit_mb: int = None) -> dict:
    """Run Bell on the certificate. Raise RuntimeError, with Bell's diagnostics,
    unless Bell reports `valid` with exactly `expected_makespan`."""
    if profile == "auto":
        profile = "ipc2027" if problem_declares_makespan(domain, problem) else "hddl-1.1"
    cmd = ["timeout", "-k", "5", str(timeout_s), bell, "--json", "--profile", profile,
           domain, problem, certificate]
    r = run_step("Validation", cmd, log, memory_limit_mb=memory_limit_mb)
    if r.returncode == 124:
        raise RuntimeError(f"Bell did not finish validating {certificate} within {timeout_s}s")
    try:
        report = json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise RuntimeError(f"Bell produced no JSON report (exit {r.returncode}):\n"
                           f"{r.stdout.strip()}\n{r.stderr.strip()}")
    report_file.write_text(json.dumps(report, indent=2) + "\n")
    status = report.get("status")
    makespan = (report.get("metrics") or {}).get("makespan")
    if status != "valid":
        def describe(d):
            text = f"  [{d.get('code')}] {d.get('message')}"
            if d.get("occurrence"):
                text += f" (occurrence {d['occurrence']})"
            loc = d.get("location")
            if loc:
                text += f" at {loc.get('file')}:{loc.get('line')}:{loc.get('column')}"
            if d.get("counterexample"):
                text += f" counterexample: {' -> '.join(d['counterexample'])}"
            return text
        diags = "\n".join(describe(d) for d in report.get("diagnostics", []))
        hint = ""
        if status == "input_error":
            hint = ("\n  (input_error can also mean Bell cannot accept the domain/problem "
                    "files themselves, e.g. an undeclared supertype or a mismatched "
                    ":domain name; fix the instance or rerun with --no-bell)")
        raise RuntimeError(f"Bell rejected the plan certificate {certificate} "
                           f"(status {status}, profile {profile}):\n{diags}{hint}")
    if makespan != expected_makespan:
        raise RuntimeError(f"Bell reports makespan {makespan} for {certificate}, but OCELOT "
                           f"reported {expected_makespan}")
    return {"status": status, "makespan": makespan, "profile": profile}


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
                 semantics: str = "pocl",
                 plan_output: Path = None,
                 bell: Path = None,
                 bell_profile: str = "auto",
                 bell_timeout: int = 600) -> dict:
    """
    Run all pipeline steps for one problem.

    After the MaxSAT analysis, the final plan is written as an HDDL 1.1
    partial-order certificate (`plan_output`, default `<basename>.hddl`) and,
    if a Bell binary is given, validated by Bell; a rejection or a makespan
    differing from the reported one raises RuntimeError.

    Returns dict with nodes, nodes_per_sec, makespan, search_s, total_s,
    certificate and bell (Bell's verdict, or None when not validated).
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
    cert_file  = Path(plan_output).resolve() if plan_output else Path(str(basename) + ".hddl")
    bell_file  = Path(str(basename) + ".bell.json")

    if cert_file in (domain, problem):
        raise RuntimeError(f"refusing to overwrite input file {cert_file} with the plan")

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

    cert_file.unlink(missing_ok=True)
    r = run_step("Certificate", [sys.executable, scripts_dir / "certificate.py",
                                  "--domain", domain, "--problem", problem,
                                  "--actual", act, "--map", str(wcnf) + ".map",
                                  "--rc2out", sol, "--semantics", semantics,
                                  "--makespan", analyzer_stats["makespan"],
                                  "-o", cert_file],
                 log, cwd=scripts_dir, memory_limit_mb=ml)
    if r.returncode != 0:
        raise RuntimeError(f"Writing the plan certificate failed:\n{r.stderr.strip()}")

    bell_result = None
    bell_file.unlink(missing_ok=True)
    if bell is not None:
        bell_result = validate_certificate(bell, domain, problem, cert_file,
                                           analyzer_stats["makespan"], log, bell_file,
                                           profile=bell_profile, timeout_s=bell_timeout,
                                           memory_limit_mb=ml)

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
        "certificate": cert_file,
        "bell": bell_result,
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
              semantics: str = "pocl", **validation):
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
                                 memory_limit_mb=memory_limit_mb, **validation)
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
    p.add_argument("--plan-output", default=None, metavar="PATH",
                   help="Where to write the HDDL 1.1 plan certificate "
                        "(single mode only; default: <output>.hddl)")
    p.add_argument("--bell", default=None, metavar="PATH",
                   help="Bell validator binary used to check the certificate (default: "
                        "$OCELOT_BELL, ../bell-htn/target/release/bell, or `bell` on PATH; "
                        "validation is skipped if none is found)")
    p.add_argument("--no-bell", action="store_true",
                   help="Do not validate the certificate with Bell")
    p.add_argument("--bell-profile", choices=("auto", "ipc2027", "hddl-1.1"), default="auto",
                   help="Bell eligibility profile (default: auto = ipc2027 when the instance "
                        "declares the makespan metric, else hddl-1.1)")
    p.add_argument("--bell-timeout", type=int, default=600, metavar="S",
                   help="Seconds to allow Bell before failing (default: 600)")
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
    bell  = None if ns.no_bell else resolve_bell_path(ns.bell)
    if ns.bell and not ns.no_bell and bell is None:
        print(f"ERROR: Bell binary not found at {ns.bell}", file=sys.stderr)
        sys.exit(1)
    if bell is None and not ns.no_bell:
        print("Note: Bell validator not found; the plan certificate will not be validated.",
              file=sys.stderr)
    validation = {"bell": bell, "bell_profile": ns.bell_profile,
                  "bell_timeout": ns.bell_timeout}

    if mode == "single":
        domain, problem, basename = Path(ns.args[0]), Path(ns.args[1]), Path(ns.args[2])
        try:
            result = run_pipeline(domain, problem, basename, tools,
                                  ns.heuristic, ns.g_value, ns.weight,
                                  semantics=ns.semantics,
                                  memory_limit_mb=mem,
                                  plan_output=ns.plan_output, **validation)
            verdict = (f"bell={result['bell']['status']} ({result['bell']['profile']})"
                       if result["bell"] else "bell=skipped")
            print(f"Done. makespan={result['makespan']}  "
                  f"nodes={result['nodes']}  total={result['total_s']:.2f}s  "
                  f"plan={result['certificate']}  {verdict}")
        except RuntimeError as e:
            print(f"FAILED: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        run_batch(Path(ns.args[0]), Path(ns.args[1]), tools,
                  ns.heuristic, ns.g_value, ns.weight, semantics=ns.semantics,
                  memory_limit_mb=mem, **validation)
