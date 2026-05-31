import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.run_planner import resolve_tool_paths, detect_mode

PANDA_ROOT = Path(__file__).parent.parent


def test_resolve_tool_paths_defaults(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    paths = resolve_tool_paths(PANDA_ROOT, engine=None, parser=None, grounder=None)
    assert paths["engine"] == PANDA_ROOT / "pandaPIengine" / "build" / "pandaPIengine"
    assert paths["parser"] == PANDA_ROOT / "pandaPIparser" / "pandaPIparser"
    assert paths["grounder"] == PANDA_ROOT / "scripts" / "pandaPIgrounder"


def test_resolve_tool_paths_override(tmp_path):
    fake_bin = tmp_path / "myengine"
    fake_bin.touch()
    fake_bin.chmod(0o755)
    paths = resolve_tool_paths(PANDA_ROOT, engine=str(fake_bin), parser=None, grounder=None)
    assert paths["engine"] == fake_bin


def test_resolve_tool_paths_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="engine binary not found"):
        resolve_tool_paths(PANDA_ROOT, engine=str(tmp_path / "missing"), parser=None, grounder=None)


def test_detect_mode_single(tmp_path):
    d = tmp_path / "domain.hddl"
    p = tmp_path / "problem.hddl"
    d.touch(); p.touch()
    assert detect_mode([str(d), str(p), "out"]) == "single"


def test_detect_mode_batch(tmp_path):
    domain_dir = tmp_path / "Barman"
    domain_dir.mkdir()
    assert detect_mode([str(domain_dir), str(tmp_path / "results")]) == "batch"


def test_detect_mode_invalid(tmp_path):
    with pytest.raises(SystemExit):
        detect_mode(["only_one_arg"])


from scripts.run_planner import parse_timing_log, parse_engine_output


def test_parse_timing_log(tmp_path):
    log = tmp_path / "out.log"
    log.write_text(
        "Parsing,0.00,2576\n"
        "Grounding,0.01,2528\n"
        "InitialEngine,0.59,125892\n"
        "Cleaning,0.00,2320\n"
        "Encoding,0.59,80264\n"
        "Solving,0.04,24000\n"
        "Analysis,0.08,35984\n"
    )
    result = parse_timing_log(log)
    assert result["InitialEngine"]["wall_s"] == pytest.approx(0.59)
    assert result["Encoding"]["wall_s"] == pytest.approx(0.59)
    assert sum(v["wall_s"] for v in result.values()) == pytest.approx(1.31)


def test_parse_engine_output():
    stdout = (
        "Heuristic #0 = hhRC2(prefix-makespan-fast;distance;)\n"
        " - type: plan makespan\n"
        "- Generated 111455 search nodes\n"
        "- Generated 207293 nodes per second\n"
        "- Plan makespan: 5\n"
        "==>\n"
        "action1\n"
        "action2\n"
    )
    result = parse_engine_output(stdout)
    assert result["nodes"] == 111455
    assert result["nodes_per_sec"] == 207293
    assert result["makespan"] == 5


def test_parse_engine_output_no_solution():
    stdout = "- Generated 5000 search nodes\n- Generated 10000 nodes per second\n"
    result = parse_engine_output(stdout)
    assert result["nodes"] == 5000
    assert result["makespan"] is None


from scripts.run_planner import print_summary


def test_print_summary_output(capsys):
    results = [
        {"problem": "pfile01", "nodes": 109473, "nodes_per_sec": 186118,
         "search_s": 0.59, "total_s": 0.71, "makespan": 5},
        {"problem": "pfile02", "failed": True},
    ]
    print_summary(results)
    captured = capsys.readouterr().out
    assert "pfile01" in captured
    assert "5" in captured
    assert "FAILED" in captured
    assert "nodes/s" in captured
