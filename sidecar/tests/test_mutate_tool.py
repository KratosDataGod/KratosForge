"""tools/mutate.py, the mutation testing tool (docs/quality/mutation-testing.md): each mutant's tests
run with no bytecode cache, so a mutant can never run an earlier mutant's cached code (#2836)."""

from __future__ import annotations

import importlib.util
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
