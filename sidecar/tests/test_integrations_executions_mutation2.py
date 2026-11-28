"""integrations/executions.py: a stop checks the kernel before it waits. Batch 219 first counted the mutant
that swaps ``and`` for ``or`` in that loop (line 383) as killed when it timed out on a loaded machine;
re-run alone it passed, because it only waited out the grace period before seeing the kernel was idle
(docs/quality/mutation/batch-219.md). The work is a stand-in; no kernel runs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_cells
from fabric_desktop.integrations import executions as ex


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


class Kernel:
    """Stops the run from inside its first cell; reports running once, then idle."""

    def __init__(self, r):
        self.r, self.calls, self.polls = r, [], ["running", "idle"]

    def start(self, code):
        self.r.on_cancel()
        return {"status": "ok"}

    def interrupt(self):
        self.calls.append("interrupt")

    def poll(self):
        status = self.polls.pop(0) if self.polls else "idle"
        self.calls.append(f"poll {status}")
        return {"status": status}

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


def test_a_stop_sleeps_only_while_the_kernel_is_running(tmp_path, monkeypatch):
    mgr = ex.ExecutionManager(SimpleNamespace(root=tmp_path, _attach=lambda ref: None), folder=tmp_path / "ex")
    r = ex.Execution("python", "cl_1", "a run", {"workspace_id": "ws"}, 30.0)
    kernel = Kernel(r)
    mgr._kernel_factory = lambda: kernel
    monkeypatch.setattr(notebook_cells, "wait", lambda k, snap: ({"status": "ok"}, []))
    monkeypatch.setattr(ex, "STOP_GRACE_SECONDS", 0.5)
    sleeps = []

    class Clock:   # this module's time only, so another thread's sleep isn't counted
        def __getattr__(self, name):
            return getattr(time, name)

        def sleep(self, s):
            sleeps.append(s)
    monkeypatch.setattr(ex, "time", Clock())
    mgr.run_cells(r, ["x = 1"], None)
    assert kernel.calls == ["interrupt", "poll running", "poll idle", "stop", "close"]
    assert sleeps == [0.1]
