"""Mutation batch 530: git/ops.py's timeout message, at a limit where whole minutes and a 61-second
minute give different counts. As in test_git_ops_mutation.py, a small Python program stands in for git,
and a stand-in for the awake-time timer reaches the limit as soon as git starts."""

from __future__ import annotations

import sys
import time

from fabric_desktop.git import cli, ops


class _TimeUpAtOnce:
    def __init__(self, seconds, fn):
        self.seconds, self.fn = seconds, fn

    def start(self):
        self.fn()

    def cancel(self):
        pass


def _wait(o, op_id, timeout=20):
    end = time.monotonic() + timeout
    while o.status(op_id)["status"] == "running":
        assert time.monotonic() < end, "the operation never finished"
        time.sleep(0.02)
    return o.status(op_id)


def test_a_three_minute_limit_is_said_as_three_minutes(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "command", lambda cwd, args: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setattr(cli, "NETWORK_TIMEOUT", 180)
    monkeypatch.setattr(ops, "AwakeTimer", _TimeUpAtOnce)
    o = ops.Operations()
    op = _wait(o, o._start("fetch", None, ["fetch"], cwd=tmp_path)["id"])
    assert op["error_kind"] == "timeout"
    assert "longer than 3 minutes and was stopped" in op["message"]
