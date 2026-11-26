"""git/ops.py: the mutant that test_git_ops_mutation.py and the module's other tests leave alive. On Windows,
taskkill runs with its output captured and a 15-second limit."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from fabric_desktop.git import ops


def test_on_windows_taskkill_runs_quietly_with_a_15_second_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "_WINDOWS", True)
    monkeypatch.setattr(ops, "system32", lambda name: name)
    monkeypatch.setattr(ops, "subprocess", SimpleNamespace(run=lambda cmd, **kw: calls.append((cmd, kw)),
                                                           TimeoutExpired=subprocess.TimeoutExpired))
    proc = SimpleNamespace(pid=7, poll=lambda: None, kill=lambda: None)
    ops._kill(proc)
    assert calls[0][0] == ["taskkill.exe", "/F", "/T", "/PID", "7"]
    assert calls[0][1]["capture_output"] is True and calls[0][1]["timeout"] == 15


