"""CI's performance check (examples/benchmark/compare.py, run by .github/workflows/perf.yml): a run of the
start-up, notebook-start and SQL benchmark compared with the recorded baseline."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "examples" / "benchmark"


def load():
    spec = importlib.util.spec_from_file_location("bench_compare", BENCH / "compare.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(startup=1000.0, count=1.4, rows=1_000_000):
    s = lambda m: {"median": m, "p95": m, "min": m, "runs": 5}  # noqa: E731
    return {"runs": 5, "warmup": 1, "machine": {"python": "3.12", "system": "Windows", "cpus": 4},
            "startup": s(startup), "notebook first cell": s(700.0), "notebook first spark cell": s(710.0),
            "sql": {"rows": rows, "count": s(count)}}


def statuses(rows):
    return {r["name"]: r["status"] for r in rows}


def test_a_clear_slowdown_is_a_regression_and_a_speedup_passes():
    cmp = load()
    base = cmp.baseline_from(run(), "test runner")
    assert statuses(cmp.compare(run(startup=1600.0), base))["startup"] == "regression"
    assert statuses(cmp.compare(run(startup=400.0), base))["startup"] == "faster"
    assert statuses(cmp.compare(run(startup=1400.0), base))["startup"] == "within range"  # 1.4x: runner noise


def test_a_tiny_query_isnt_a_regression_for_a_few_milliseconds():
    cmp = load()
    base = cmp.baseline_from(run(), "test runner")
    assert statuses(cmp.compare(run(count=14.0), base))["SQL count"] == "within range"  # 10x, but 12.6 ms
    assert statuses(cmp.compare(run(count=40.0), base))["SQL count"] == "regression"


def test_sql_on_a_different_table_size_isnt_compared():
    cmp = load()
    base = cmp.baseline_from(run(), "test runner")
    got = statuses(cmp.compare(run(count=400.0, rows=10), base))
    assert got["SQL count"] == "not comparable" and got["startup"] == "within range"


def test_the_cli_writes_a_summary_and_fails_only_with_gate(tmp_path, monkeypatch, capsys):
    cmp = load()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    (tmp_path / "base.json").write_text(json.dumps(cmp.baseline_from(run(), "windows-latest", "abc123")))
    (tmp_path / "slow.json").write_text(json.dumps(run(startup=2000.0)))
    summary = tmp_path / "summary.md"
    args = [str(tmp_path / "slow.json"), "--baseline", str(tmp_path / "base.json"), "--summary", str(summary)]
    assert cmp.main(args) == 0  # advisory
    out = capsys.readouterr().out
    assert "::warning title=Performance regression::startup: 2,000.0 ms" in out
    assert "| startup | 2,000.0 | 1,000.0 | 2.0x | **regression** |" in summary.read_text()
    assert "Baseline: windows-latest" in summary.read_text()
    assert cmp.main([*args, "--gate"]) == 1
    (tmp_path / "same.json").write_text(json.dumps(run()))
    assert cmp.main([str(tmp_path / "same.json"), "--baseline", str(tmp_path / "base.json"), "--gate"]) == 0


def test_with_no_baseline_yet_every_line_says_so(tmp_path, capsys):
    cmp = load()
    (tmp_path / "r.json").write_text(json.dumps(run()))
    assert cmp.main([str(tmp_path / "r.json"), "--baseline", str(tmp_path / "missing.json"), "--gate"]) == 0
    assert "| startup | 1,000.0 |  |  | no baseline |" in capsys.readouterr().out


def test_writing_a_baseline_needs_the_runner(tmp_path):
    cmp = load()
    (tmp_path / "r.json").write_text(json.dumps(run()))
    with pytest.raises(SystemExit):
        cmp.main([str(tmp_path / "r.json"), "--write-baseline", str(tmp_path / "b.json")])
    assert cmp.main([str(tmp_path / "r.json"), "--write-baseline", str(tmp_path / "b.json"), "--runner", "x"]) == 0
    written = json.loads((tmp_path / "b.json").read_text())
    assert written["runner"] == "x" and written["settings"] == {"runs": 5, "warmup": 1, "rows": 1_000_000}
    assert written["medians"]["SQL count"] == 1.4


def test_the_recorded_baseline_names_its_runner_and_settings():
    path = BENCH / "baseline-windows.json"
    if not path.exists():
        pytest.skip("no Windows baseline recorded yet")
    base = json.loads(path.read_text(encoding="utf-8"))
    assert base["runner"] and base["commit"] and base["settings"] == {"runs": 5, "warmup": 1, "rows": 1_000_000}
    assert set(base["medians"]) >= {"startup", "notebook first cell", "notebook first spark cell", "SQL count"}


def test_the_workflow_runs_the_baseline_settings_and_stays_advisory():
    workflow = (BENCH.parents[1] / ".github" / "workflows" / "perf.yml").read_text(encoding="utf-8")
    assert "--runs 5 --warmup 1 --rows 1000000" in workflow
    assert "windows-latest" in workflow and "baseline-windows.json" in workflow
    assert "--gate" not in workflow.split("jobs:", 1)[1]  # advisory until docs/performance.md says otherwise
