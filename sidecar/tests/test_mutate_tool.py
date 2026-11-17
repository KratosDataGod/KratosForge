"""tools/mutate.py, the mutation testing tool (docs/quality/mutation-testing.md): each mutant's tests
run with no bytecode cache, so a mutant can never run an earlier mutant's cached code (#2836)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "mutate.py"


def _load():
    spec = importlib.util.spec_from_file_location("mutate_tool_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Proc:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        return self.code


def test_each_mutant_runs_with_no_bytecode_cache(monkeypatch, tmp_path):
    mutate = _load()
    seen = {}

    def popen(args, **kw):
        seen.update(kw)
        return _Proc(1)

    monkeypatch.setattr(mutate.subprocess, "Popen", popen)
    assert mutate._run_tests(tmp_path, ["tests/test_x.py"]) == "killed"
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["env"]["PYTHONPATH"] == str(tmp_path)
    assert seen["cwd"] == tmp_path


def test_a_passing_run_is_a_survivor(monkeypatch, tmp_path):
    mutate = _load()
    monkeypatch.setattr(mutate.subprocess, "Popen", lambda args, **kw: _Proc(0))
    assert mutate._run_tests(tmp_path, ["tests/test_x.py"]) == "survived"


def _tiny_sidecar(tmp_path):
    root = tmp_path / "sidecar"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    return root


def test_the_run_stops_when_the_tests_fail_on_the_unmutated_copy(monkeypatch, tmp_path):
    # A test that reads docs/ or app/ fails in the sidecar-only copy whatever the mutant, so every
    # mutant would count as killed: a false 100%.
    mutate = _load()
    monkeypatch.setattr(mutate, "SIDECAR", _tiny_sidecar(tmp_path))
    calls = []
    monkeypatch.setattr(mutate, "_run_tests", lambda work, tests: calls.append(tests) or "killed")
    out = tmp_path / "out.json"
    try:
        mutate.main(["pkg/m.py", "--out", str(out), "--", "tests/test_m.py"])
    except SystemExit as stop:
        assert "fail on the unmutated module" in str(stop.code)
    else:
        raise AssertionError("the run went on although the tests fail on the unmutated code")
    assert calls == [["tests/test_m.py"]]
    assert not out.exists()


def test_a_baseline_timeout_also_stops_the_run(monkeypatch, tmp_path):
    mutate = _load()
    monkeypatch.setattr(mutate, "SIDECAR", _tiny_sidecar(tmp_path))
    monkeypatch.setattr(mutate, "_run_tests", lambda work, tests: "timeout")
    try:
        mutate.main(["pkg/m.py", "--out", str(tmp_path / "out.json"), "--", "tests/test_m.py"])
    except SystemExit as stop:
        assert "time out on the unmutated module" in str(stop.code)
    else:
        raise AssertionError("the run went on although the tests time out on the unmutated code")


def test_the_baseline_runs_on_the_unmutated_module_before_any_mutant(monkeypatch, tmp_path):
    mutate = _load()
    monkeypatch.setattr(mutate, "SIDECAR", _tiny_sidecar(tmp_path))
    seen = []

    def run(work, tests):
        seen.append((work / "pkg" / "m.py").read_text(encoding="utf-8"))
        return "survived" if len(seen) == 1 else "killed"

    monkeypatch.setattr(mutate, "_run_tests", run)
    out = tmp_path / "out.json"
    assert mutate.main(["pkg/m.py", "--out", str(out), "--", "tests/test_m.py"]) == 0
    assert seen[0] == "def f(x):\n    return x + 1\n"
    assert len(seen) > 1 and all(text != seen[0] for text in seen[1:])
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["run"] == len(seen) - 1 and summary["killed"] == summary["run"]
