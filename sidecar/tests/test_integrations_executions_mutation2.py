"""Executions (integrations/executions.py) at their exact deadlines, with a clock the test holds: the two
boundary mutants batch 165 left as timing only. A call answers at its deadline, not one poll later, and
a stop ends a kernel that is still running once its grace is up, without one more look at it."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import notebook_cells
from fabric_desktop.integrations import executions as ex
from tests.test_integrations_executions_mutation import StoppingKernel, ctx, mgr, run  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def clock(monkeypatch, *readings):
    """ex.time with a monotonic clock that reads ``readings`` in turn, then stays on the last one."""
    seen = []

    def monotonic():
        seen.append(1)
        return readings[min(len(seen), len(readings)) - 1]
    monkeypatch.setattr(ex, "time", SimpleNamespace(**(vars(time) | {"monotonic": monotonic, "sleep": lambda s: None})))
    return seen


def test_a_call_answers_at_exactly_its_deadline(mgr, monkeypatch):  # noqa: F811
    release = threading.Event()
    seen = clock(monkeypatch, 0.0, 5.0, 5.0, 5.0, 6.0)
    r = run()
    view = mgr.start(r, lambda _r: release.wait(10) and {"ok": True}, ctx(), wait=5)
    try:
        assert view["state"] == "running"
        assert len(seen) == 2   # the deadline, then one look that finds it reached
    finally:
        release.set()
        r.done.wait(10)


def test_a_stop_ends_a_kernel_still_running_when_its_grace_is_up(mgr, monkeypatch):  # noqa: F811
    clock(monkeypatch, 0.0, ex.STOP_GRACE_SECONDS)
    r = run()
    kernel = StoppingKernel(r)
    mgr._kernel_factory = lambda: kernel
    monkeypatch.setattr(notebook_cells, "wait", lambda k, snap: ({"status": "ok"}, []))
    mgr.run_cells(r, ["x = 1"], None)
    assert kernel.calls == ["interrupt", "stop", "close"]
