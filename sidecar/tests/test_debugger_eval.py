"""Notebook debugger (#2362 hardening, round 5): Evaluate and watches in a paused frame never take
the kernel down or wedge the pause. An expression that exits, raises a BaseException, hangs,
changes state, calls a function with a breakpoint in it or returns a huge value answers in time,
keeps the pause where it was, and Continue or Stop still ends the cell."""

from __future__ import annotations

import threading
import time

import pytest

from fabric_desktop import debugger
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

CODE = ("items = [1, 2]\n"
        "def grow(n):\n"
        "    items.append(n)\n"
        "    return len(items)\n"
        "a = 1\n"
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


def ended_in_time(k, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.2)
        if snap["status"] != "running":
            assert not snap.get("paused"), snap
            return snap
    raise AssertionError(f"the cell still ran {seconds} s after it was stopped")


def alive(k):
    """The kernel (and its process) still answers, and the next cell runs untraced."""
    k.start("import sys\nprint(sys.gettrace() is None)")
    snap = wait(k)
    assert snap["status"] == "ok", snap
    assert snap["outputs"][-1]["text"].strip() == "True"


def continue_finishes(k):
    k.debug("continue")
    snap = wait(k)
    assert snap["status"] == "ok" and not snap.get("paused"), snap
    return snap


BASE_EXCEPTIONS = [
    ("exit()", "SystemExit"),
    ("__import__('sys').exit(3)", "SystemExit: 3"),
    ("quit()", "SystemExit"),
    ("(_ for _ in ()).throw(KeyboardInterrupt('typed'))", "KeyboardInterrupt: typed"),
    ("(_ for _ in ()).throw(GeneratorExit)", "GeneratorExit"),
    ("(_ for _ in ()).throw(type('Boom', (BaseException,), {})('x'))", "Boom: x"),
]


@pytest.mark.parametrize("expr, error", BASE_EXCEPTIONS)
def test_an_expression_that_exits_or_raises_a_base_exception_is_an_error_and_the_pause_stays(kernel, expr, error):
    proc = getattr(kernel, "_proc", None)
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    answer = kernel.debug("eval", expr)
    assert answer["error"].startswith(error), answer
    p = wait(kernel)["paused"]
    assert p["line"] == 5 and p["evals"][-1]["error"] == answer["error"]
    assert kernel.debug("eval", "len(items)")["value"] == "2"
    continue_finishes(kernel)
    if proc is not None:
        assert kernel._proc is proc and proc.poll() is None  # the same process, still running
    alive(kernel)


@pytest.mark.parametrize("hang", ["sum(1 for _ in iter(int, 1))", "[0 for _ in iter(int, 1)][0]"])
def test_a_hanging_expression_answers_pending_and_stop_ends_the_cell(kernel, hang):
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    started = time.monotonic()
    assert kernel.debug("eval", hang) == {"pending": True}
    assert time.monotonic() - started < debugger.EVAL_SECONDS + 3
    assert kernel.interrupt()["interrupted"] is True
    assert ended_in_time(kernel)["status"] == "cancelled"
    with pytest.raises(RuntimeError):
        kernel.debug("continue")  # nothing is paused
    alive(kernel)


def test_stop_during_a_hanging_expression_before_it_answers(kernel):
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    t = threading.Thread(target=lambda: _quietly(kernel.debug, "eval", "sum(1 for _ in iter(int, 1))"), daemon=True)
    t.start()
    time.sleep(0.5)
    assert kernel.interrupt()["interrupted"] is True
    assert ended_in_time(kernel)["status"] == "cancelled"
    t.join(debugger.EVAL_SECONDS + 3)
    alive(kernel)


def _quietly(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - the answer may come after the cell ended
        pass


def test_an_expression_that_changes_state_runs_as_notebook_code_and_the_pause_stays(kernel):
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    assert kernel.debug("eval", "(c := 41) + 1")["value"] == "42"
    assert kernel.debug("eval", "grow(3)")["value"] == "3"
    p = wait(kernel)["paused"]
    assert p["line"] == 5 and p["stack"] == [{"function": "(cell)", "line": 5}]
    assert kernel.debug("eval", "items")["value"] == "[1, 2, 3]"
    kernel.debug("next")
    assert wait(kernel)["paused"]["line"] == 6
    continue_finishes(kernel)


# Run in the paused cell (in its kernel process too): every pause from here on is counted, by line, in _pauses.
COUNT_PAUSES = ('exec("import fabric_desktop.debugger as _d\\n_pauses = []\\n_real = _d.Debugger._pause\\n'
                'def _counted(self, frame, reason):\\n    _pauses.append(frame.f_lineno)\\n'
                '    return _real(self, frame, reason)\\n_d.Debugger._pause = _counted")')


def test_an_expression_calling_a_function_with_a_breakpoint_does_not_pause_again(kernel, monkeypatch):
    monkeypatch.setattr(debugger.Debugger, "_pause", debugger.Debugger._pause)  # put back after, if counted here
    kernel.start(CODE, breakpoints=[3, 5])
    assert wait(kernel)["paused"]["line"] == 5
    assert "error" not in kernel.debug("eval", COUNT_PAUSES)
    # Counted, not timed (a clock read around it failed on busy CI runners): a pause at line 3 inside the
    # expression would hold it until a command came, so it would answer pending after EVAL_SECONDS; a 5 s
    # limit was how that showed. The pauses are now counted: a pause inside it would take the next command
    # (the read of _pauses) at line 3, so it shows there as well as in the answer.
    answer = kernel.debug("eval", "grow(7)")
    assert kernel.debug("eval", "_pauses")["value"] == "[]"  # no pause since the one at line 5
    assert answer["value"] == "3"  # ran through line 3 without stopping
    p = wait(kernel)["paused"]
    assert p["line"] == 5 and len(p["stack"]) == 1
    kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok", snap


def test_a_watch_calling_a_function_with_a_breakpoint_does_not_pause_again(kernel):
    kernel.start(CODE + "\nc = grow(9)\nd = 4", breakpoints=[3, 5, 8], watches=["grow(0)"])
    p = wait(kernel)["paused"]
    assert p["line"] == 5 and p["watches"][0]["value"] == "3"
    kernel.debug("continue")
    p = wait(kernel)["paused"]
    assert p["line"] == 3 and [f["function"] for f in p["stack"]] == ["(cell)", "grow"]  # the cell's own call
    kernel.debug("continue")
    assert wait(kernel)["paused"]["line"] == 8
    continue_finishes(kernel)


@pytest.mark.parametrize("expr", ["list(range(10_000_000))", "'x' * 50_000_000", "{i: i for i in range(2_000_000)}"])
def test_a_huge_value_answers_small_and_quick(kernel, expr):
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    # Waited on, not timed: the debugger itself waits EVAL_SECONDS for the answer and says pending after
    # that, so an answer that isn't pending came within the bound (a clock read around it failed on busy CI runners).
    answer = kernel.debug("eval", expr)
    assert "pending" not in answer, answer
    assert len(answer["value"]) <= 80 and len(repr(answer)) < 1000, answer
    assert len(repr(wait(kernel)["paused"])) < 5000
    continue_finishes(kernel)


@pytest.mark.parametrize("watch, error", [
    ("exit()", "SystemExit"),
    ("(_ for _ in ()).throw(KeyboardInterrupt)", "KeyboardInterrupt"),
    ("(_ for _ in ()).throw(GeneratorExit)", "GeneratorExit"),
    ("1/0", "ZeroDivisionError"),
])
def test_a_watch_that_exits_or_raises_is_an_error_on_each_pause(kernel, watch, error):
    proc = getattr(kernel, "_proc", None)
    kernel.start(CODE, breakpoints=[5, 6], watches=[watch, "len(items)"])
    for line in (5, 6):
        p = wait(kernel)["paused"]
        assert p["line"] == line
        assert p["watches"][0]["error"].startswith(error), p["watches"]
        assert p["watches"][1]["value"] == "2"
        kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok" and not snap.get("paused"), snap
    if not watch.endswith("(KeyboardInterrupt)"):  # after the cell, a KeyboardInterrupt is a Cancel of the rest
        after = snap["watches"]
        assert after[0]["error"].startswith(error) and after[1]["value"] == "2", after
    if proc is not None:
        assert kernel._proc is proc and proc.poll() is None
    alive(kernel)


def test_a_hanging_watch_is_ended_by_stop(kernel):
    kernel.start(CODE, breakpoints=[5], watches=["sum(1 for _ in iter(int, 1))"])
    time.sleep(1.0)  # the cell's thread is now inside the watch, before the pause shows
    assert kernel.interrupt()["interrupted"] is True
    assert ended_in_time(kernel)["status"] == "cancelled"
    alive(kernel)


def test_many_evaluations_in_one_pause_keep_the_snapshot_capped(kernel):
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    for i in range(200):
        assert kernel.debug("eval", f"len(items) + {i}")["value"] == str(2 + i)
    p = wait(kernel)["paused"]
    assert len(p["evals"]) == debugger.MAX_EVALS
    assert p["evals"][-1]["expr"] == "len(items) + 199" and p["line"] == 5
    continue_finishes(kernel)
