"""Notebook debugger soak (#2362 hardening, round 3): a thousand pauses and steps in one cell, in
both kernels, leave no trace hook, thread or memory growth behind; a run of cells mixing Stop,
Continue and a restart leaves nothing paused; and a poll waits for an ended process once only."""

from __future__ import annotations

import gc
import threading
import time
import tracemalloc
from collections import Counter

import pytest

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel


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


def threads_and_trace(k):
    """The kernel's lasting threads, by name, and whether a trace hook is left, read inside the kernel.
    The cell's own thread and the host's per-request threads (kernel_host names them request-...,
    and one handles each poll and start) come and go with every call, so they aren't counted."""
    k.start("import sys, threading\n"
            "me = threading.current_thread()\n"
            "print(sys.gettrace() is None, '|'.join(sorted(t.name for t in threading.enumerate()\n"
            "    if t is not me and not t.name.startswith('request-'))))")
    snap = wait(k)
    assert snap["status"] == "ok", snap
    untraced, _, names = snap["outputs"][-1]["text"].strip().partition(" ")
    # A Counter, not a set: a leaked thread whose name repeats one already running still counts.
    return Counter(n for n in names.split("|") if n), untraced == "True"


def assert_no_threads_left(k, before, seconds=10):
    """An interrupted cell's thread ends as its exception unwinds, which takes a moment on a busy
    runner: wait for that, within a deadline, then name any thread that stayed."""
    deadline = time.monotonic() + seconds
    while True:
        after, untraced = threads_and_trace(k)
        assert untraced
        if not after - before or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    assert not after - before, f"threads left behind: {sorted((after - before).elements())}"


PAUSES = 1000


def test_a_thousand_breakpoint_hits_leave_nothing_behind(kernel):
    threads_and_trace(kernel)  # warm up: the kernel's own threads have started
    before, _ = threads_and_trace(kernel)
    kernel.start(f"total = 0\nfor i in range({PAUSES}):\n    total += i\nend = total", breakpoints=[3])
    for i in range(PAUSES):
        p = wait(kernel)["paused"]
        assert p["line"] == 3, (i, p)
        if i % 250 == 0:
            assert {v["name"]: v["value"] for v in p["variables"]}["i"] == str(i)
        kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok" and snap.get("paused") is None
    assert_no_threads_left(kernel, before)


def test_a_thousand_steps_leave_nothing_behind(kernel):
    threads_and_trace(kernel)
    before, _ = threads_and_trace(kernel)
    kernel.start("def f(n):\n    return n + 1\nx = 0\nwhile x < 10**9:\n    x = f(x)", step=True)
    lines = []
    for _ in range(PAUSES):
        lines.append(wait(kernel)["paused"]["line"])
        kernel.debug("step")
    assert set(lines) == {1, 3, 4, 5, 2}  # into f and back, over and over
    assert kernel.interrupt()["interrupted"] is True
    assert wait(kernel)["status"] == "cancelled"
    assert_no_threads_left(kernel, before)


def test_repeated_pauses_in_the_engine_kernel_dont_grow_memory():
    k = Kernel()

    def round_trip():
        k.start("a = 1\nb = 2\nc = 3", breakpoints=[2, 3])
        for line in (2, 3):
            assert wait(k)["paused"]["line"] == line
            k.debug("continue")
        assert wait(k)["status"] == "ok"

    for _ in range(20):
        round_trip()  # warm up caches, history and the kernel's own state
    gc.collect()
    tracemalloc.start()
    try:
        start, _ = tracemalloc.get_traced_memory()
        for _ in range(200):
            round_trip()
        gc.collect()
        end, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # A per-run leak of a frame, a debugger or a pause snapshot is a kilobyte or more: 200 runs would
    # pass 300 KB. The kernel's cell history grows by a little per run (about 30 KB in all), which fits.
    assert end - start < 300_000, end - start


def test_a_run_of_cells_mixing_continue_stop_and_restart_leaves_nothing_paused(kernel):
    for round_ in range(3):
        for i in range(10):
            kernel.start(f"v{i} = {i}\nw{i} = v{i} + 1", breakpoints=[2], step=i % 2 == 0)
            p = wait(kernel)["paused"]
            assert p["line"] == (1 if i % 2 == 0 else 2)
            if i == 4:
                assert kernel.interrupt()["interrupted"] is True
                assert wait(kernel)["status"] == "cancelled"
            elif i == 7:
                kernel.restart()
                assert kernel.poll(-1, 0.3).get("paused") is None
            else:
                kernel.debug("continue")
                if i % 2 == 0:
                    assert wait(kernel)["paused"]["line"] == 2
                    kernel.debug("continue")
                assert wait(kernel)["status"] == "ok"
        _, untraced = threads_and_trace(kernel)
        assert untraced, round_
        with pytest.raises(RuntimeError):
            kernel.debug("continue")


# ---------- a poll waits for an ended process once ----------

class _Ended:
    """Stands in for an ended Popen whose reader never marks it gone (a tree that keeps the pipe open)."""

    def poll(self):
        return 1


def test_a_poll_waits_for_an_ended_process_once_and_holds_it_weakly():
    k = ProcessKernel()
    ended = _Ended()
    k._proc = ended
    started = time.monotonic()
    k._await_gone(0.3)
    assert time.monotonic() - started >= 0.3
    started = time.monotonic()
    k._await_gone(0.3)
    assert time.monotonic() - started < 0.1  # the same process: no second wait
    other = _Ended()
    k._proc = other
    started = time.monotonic()
    k._await_gone(0.3)
    assert time.monotonic() - started >= 0.3  # a later process gets its own
    k._proc = None
    del ended, other
    gc.collect()
    assert k._awaited is not None and k._awaited() is None  # not kept alive for the check


def test_a_thread_that_stays_is_named(kernel):
    # The check above has teeth: a cell that leaves a thread running fails it, by the thread's name.
    before, _ = threads_and_trace(kernel)
    kernel.start("import threading, time\nstay = threading.Event()\n"
                 "threading.Thread(target=stay.wait, name='left-behind', daemon=True).start()")
    assert wait(kernel)["status"] == "ok"
    with pytest.raises(AssertionError, match="left-behind"):
        assert_no_threads_left(kernel, before, seconds=0.5)
    kernel.start("stay.set()")
    assert wait(kernel)["status"] == "ok"
    # One that takes the name of a thread already running is counted too.
    before, _ = threads_and_trace(kernel)
    kernel.start("import threading\nstay2 = threading.Event()\n"
                 "threading.Thread(target=stay2.wait, name='MainThread', daemon=True).start()")
    assert wait(kernel)["status"] == "ok"
    with pytest.raises(AssertionError, match="MainThread"):
        assert_no_threads_left(kernel, before, seconds=0.5)
    kernel.start("stay2.set()")
    assert wait(kernel)["status"] == "ok"


# ---------- a finished cell is free at once ----------

def test_a_cell_is_marked_done_with_its_final_status_never_after():
    # A poll that sees a final status must never find the run still running: the next cell would be
    # refused ("A cell is already running") in the gap. done is set while the status is held.
    from fabric_desktop import notebook

    seen = []

    class Done(threading.Event):
        def set(self):
            seen.append((run.lock.locked(), run.status))
            super().set()

    run = notebook._Run(1)
    run.done = Done()
    run.finish("ok")
    assert seen == [(True, "ok")]



def test_a_finished_cell_never_reports_a_pause():
    # Cancel resends its interrupt while the cell evaluates an expression; one can land in the
    # debugger's clean-up before it clears the pause. The run must still not report a pause.
    from fabric_desktop import notebook

    run = notebook._Run(1)
    run.debugger = object()
    run.set_paused({"line": 5, "reason": "breakpoint", "stack": [], "variables": []})
    assert run.snapshot()["paused"]["line"] == 5
    run.finish("cancelled")
    snap = run.snapshot()
    assert snap["status"] == "cancelled" and snap["paused"] is None
