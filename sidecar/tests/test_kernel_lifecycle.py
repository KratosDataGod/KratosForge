"""WR-130: a notebook's kernel process across its lifecycle edges. Restart while a cell runs, an interrupt while
a cell streams output, a kernel that dies mid-cell, and restarts back to back each end with a clear status, leave
no process behind (the kernel's or one its cell started), and leave a kernel the next cell can use."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

import pytest
from test_kernel_process import alive, described, kernel, text, until_done  # noqa: F401 - kernel is a fixture

# A cell that starts a child process, prints its pid, then keeps printing (output streaming) until stopped.
STREAMING_WITH_CHILD = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print('child', child.pid, flush=True)\n"
    "i = 0\n"
    "while True:\n"
    "    i += 1\n"
    "    print('tick', i, flush=True)\n"
    "    time.sleep(0.01)\n")


def _child_pid(k, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in text(k.poll(-1, 0.5)).splitlines():
            if line.startswith("child "):
                return int(line.split()[1])
    raise AssertionError("the cell never printed its child's pid")


def _gone(*pids, timeout=10):
    deadline = time.monotonic() + timeout
    while any(alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(alive(p) for p in pids), [described(p) for p in pids if alive(p)]


def test_restart_while_a_cell_runs_ends_it_and_its_processes_and_starts_fresh(kernel):
    kernel.run("kept = 1")
    kernel.start(STREAMING_WITH_CHILD)
    child, old = _child_pid(kernel), kernel.pid
    kernel.restart()
    _gone(child, old)
    snap = kernel.poll(-1, 0)
    assert snap["status"] != "running"  # nothing is left showing as running
    out = kernel.run("'kept' in dir()")
    assert out["status"] == "ok" and out["outputs"] == [{"type": "text", "text": "False"}]
    assert kernel.pid != old and out["execution_count"] == 1  # a fresh process, counting from 1 again


def test_an_interrupt_while_a_cell_streams_output_cancels_it_and_keeps_the_kernel(kernel):
    kernel.run("kept = 41")
    kernel.start("import time\ni = 0\nwhile True:\n    i += 1\n    print('tick', i, flush=True)\n    time.sleep(0.005)")
    deadline = time.monotonic() + 30
    while "tick 50" not in text(kernel.poll(-1, 0.2)) and time.monotonic() < deadline:
        pass
    pid = kernel.pid
    assert kernel.interrupt()["interrupted"] is True
    snap = until_done(kernel)
    assert snap["status"] == "cancelled", snap
    assert "tick 1\n" in text(snap)  # what it printed before the interrupt is kept
    out = kernel.run("kept + 1")
    assert out["outputs"] == [{"type": "text", "text": "42"}] and kernel.pid == pid


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL; on Windows the crash test in test_kernel_process covers it")
def test_a_kernel_killed_mid_cell_says_so_and_leaves_no_process_behind(kernel):
    kernel.start(STREAMING_WITH_CHILD)
    child, pid = _child_pid(kernel), kernel.pid
    os.kill(pid, signal.SIGKILL)  # dies from outside, as the OS or a crash would end it
    snap = until_done(kernel)
    assert snap["status"] == "error" and text(snap).strip(), snap
    assert "child" in text(snap)  # what it printed before it died is kept
    _gone(child, pid)  # with no Stop or Restart: what its cell started went with it
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]


def test_two_quick_restarts_back_to_back_leave_one_working_kernel(kernel):
    kernel.start(STREAMING_WITH_CHILD)
    child, first = _child_pid(kernel), kernel.pid
    kernel.restart()
    kernel.restart()  # the second finds no process: nothing to end, no error
    _gone(child, first)
    out = kernel.run("import os\nos.getpid()")
    assert out["status"] == "ok" and kernel.pid != first
    assert out["outputs"] == [{"type": "text", "text": str(kernel.pid)}]


def test_restarts_from_two_threads_at_once_leave_one_working_kernel(kernel):
    kernel.start(STREAMING_WITH_CHILD)
    child, first = _child_pid(kernel), kernel.pid
    errors: list[BaseException] = []

    def restart():
        try:
            kernel.restart()
        except BaseException as exc:  # noqa: BLE001 - reported by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=restart) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    _gone(child, first)
    assert kernel.run("1 + 1")["outputs"] == [{"type": "text", "text": "2"}]


def test_restart_right_after_a_cell_starts_runs_no_more_of_it(kernel, tmp_path):
    marker = tmp_path / "ran"
    kernel.start(f"import time\ntime.sleep(1)\nopen({str(marker)!r}, 'w').write('x')")
    kernel.restart()
    time.sleep(2)
    assert not marker.exists()
    assert kernel.poll(-1, 0)["status"] != "running"
