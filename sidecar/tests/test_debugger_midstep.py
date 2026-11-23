"""Notebook debugger (#2362 hardening, round 4): Stop, a restart or the kernel process dying while
a step is running (between pauses, not at one) ends the cell in time, leaves nothing paused or
traced, and the next cell runs as usual."""

from __future__ import annotations

import time

import pytest

from fabric_desktop.kernel_proc import KernelGone, ProcessKernel
from fabric_desktop.notebook import Kernel

# Line 6 runs for up to 60 s in the cell's own (traced) code, so a step over it is running, not paused.
SLOW = ("import time\n"
        "a = 1\n"
        "def spin(seconds):\n"
        "    started = time.monotonic()\n"
        "    while time.monotonic() - started < seconds: pass\n"
        "spin(60)\n"
        "b = 2")


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    k = Kernel() if request.param == "engine" else ProcessKernel()
    yield k
    if request.param == "process":
        k.close()


def wait(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def running_a_step(k, action):
    """Pause on the slow line, start ``action`` over it, and check the step is running, not paused.
    "into-out" steps into spin() first, then Step out runs the rest of it."""
    k.start(SLOW, breakpoints=[6])
    assert wait(k)["paused"]["line"] == 6
    if action == "into-out":
        k.debug("step")
        p = wait(k)["paused"]
        assert [f["function"] for f in p["stack"]] == ["(cell)", "spin"]
        action = "out"
    k.debug(action)
    time.sleep(0.5)
    snap = k.poll(-1, 0.1)
    assert snap["status"] == "running" and not snap.get("paused"), snap


def ended_in_time(k, seconds=10):
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        snap = k.poll(-1, 0.2)
        if snap["status"] != "running":
            assert not snap.get("paused"), snap
            return snap
    raise AssertionError(f"the cell still ran {seconds} s after it was stopped")


def runs_untraced(k, expect):
    k.start("import sys\nprint(sys.gettrace() is None, 'a' in dir(), 'b' in dir())")
    snap = wait(k)
    assert snap["status"] == "ok", snap
    assert snap["outputs"][-1]["text"].strip() == expect


@pytest.mark.parametrize("action", ["next", "into-out", "out", "continue"])
def test_stop_while_a_step_runs_ends_the_cell_and_keeps_its_variables(kernel, action):
    running_a_step(kernel, action)
    assert kernel.interrupt()["interrupted"] is True
    assert ended_in_time(kernel)["status"] == "cancelled"
    with pytest.raises(RuntimeError):
        kernel.debug("continue")  # nothing is paused
    runs_untraced(kernel, "True True False")  # a was set before the stop, b never was
    # And the debugger works again.
    kernel.start("x = 1\ny = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_a_restart_while_a_step_runs_ends_the_cell(kernel):
    running_a_step(kernel, "next")
    kernel.restart()
    snap = kernel.poll(-1, 0.3)
    assert snap["status"] != "running" and not snap.get("paused"), snap
    runs_untraced(kernel, "True False False")  # a fresh kernel


def test_the_engine_kernel_thread_of_a_running_step_ends_on_restart():
    k = Kernel()
    running_a_step(k, "next")
    run = k._current
    k.restart()
    assert run.done.wait(10) and not run.thread.is_alive()


def test_the_kernel_process_dying_while_a_step_runs_ends_the_session_in_time():
    k = ProcessKernel()
    try:
        running_a_step(k, "next")
        proc = k._proc
        started = time.monotonic()
        proc.kill()
        snap = ended_in_time(k)
        assert snap["status"] == "error"
        assert any("stopped" in (o.get("evalue") or "") for o in snap["outputs"] if o.get("type") == "error"), snap
        assert time.monotonic() - started < 10
        assert proc.wait(5) is not None
        with pytest.raises((KernelGone, RuntimeError)):
            k.debug("continue")
        # The next cell starts a fresh process, which debugs as before.
        k.start("x = 1\ny = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        assert k._proc is not proc
        k.debug("continue")
        assert wait(k)["status"] == "ok"
    finally:
        k.close()


def test_closing_the_kernel_process_while_a_step_runs_leaves_no_process():
    k = ProcessKernel()
    running_a_step(k, "next")
    proc = k._proc
    started = time.monotonic()
    k.close()
    assert proc.wait(15) is not None
    assert time.monotonic() - started < 15
