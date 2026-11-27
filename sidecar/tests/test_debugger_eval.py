"""Notebook debugger (#2362 hardening, round 5): Evaluate and watches in a paused frame never take
the kernel down or wedge the pause. An expression that exits, raises a BaseException, hangs,
changes state, calls a function with a breakpoint in it or returns a huge value answers in time,
keeps the pause where it was, and Continue or Stop still ends the cell."""

from __future__ import annotations

import sys
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


def test_an_expression_asked_behind_a_hanging_one_answers_pending_at_once(kernel):
    # The engine's debug lane takes evals one at a time: each one waiting EVAL_SECONDS would add up
    # past the shell's time limit, which ends the engine.
    kernel.start(CODE, breakpoints=[5])
    assert wait(kernel)["paused"]["line"] == 5
    assert kernel.debug("eval", "sum(1 for _ in iter(int, 1))") == {"pending": True}
    started = time.monotonic()
    assert kernel.debug("eval", "len(items)") == {"pending": True}
    assert time.monotonic() - started < 2
    assert kernel.interrupt()["interrupted"] is True
    assert ended_in_time(kernel)["status"] == "cancelled"
    alive(kernel)


def test_each_pause_has_its_own_seq_and_an_expression_keeps_it(kernel):
    # debug_cell and poll_cell are on separate engine lanes, so a poll can show the pause a step led
    # to before the step's answer, or the pause it left after: the app tells them apart by seq, also
    # when a loop pauses on the same line again.
    kernel.start("for i in range(2):\n    j = i", breakpoints=[2])
    first = wait(kernel)["paused"]
    assert first["line"] == 2 and first["seq"] == 1
    assert kernel.debug("eval", "i")["value"] == "0"
    assert wait(kernel)["paused"]["seq"] == 1
    kernel.debug("continue")
    again = wait(kernel)["paused"]
    assert again["line"] == 2 and again["seq"] == 2
    continue_finishes(kernel)
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


LOCALS = ("def f():\n"
          "    q = 1\n"
          "    def bump():\n"
          "        nonlocal q\n"
          "        q += 10\n"
          "    r = 2\n"  # 6
          "    return q, r\n"
          "print(*f())")


def test_a_function_local_an_expression_assigns_stays_after_more_expressions(kernel):
    # Before 3.13 each read of a function's f_locals refreshes a snapshot from the frame, which undid an
    # assignment as soon as a later expression (or a watch on the next pause) read it again.
    kernel.start(LOCALS, breakpoints=[6], watches=["q"])
    p = wait(kernel)["paused"]
    assert p["line"] == 6 and p["watches"][0]["value"] == "1"
    assert kernel.debug("eval", "(q := 5)")["value"] == "5"
    assert kernel.debug("eval", "q + 1")["value"] == "6"
    assert "error" in kernel.debug("eval", "(q := 7) / 0")  # assigned before it raised: kept too
    assert kernel.debug("eval", "q")["value"] == "7"
    kernel.debug("next")
    p = wait(kernel)["paused"]
    assert p["line"] == 7 and p["watches"][0]["value"] == "7"
    assert "".join(o.get("text", "") for o in continue_finishes(kernel)["outputs"]) == "7 2\n"


def test_a_function_local_a_called_function_changes_stays(kernel):
    # The other side of the same snapshot: written back as the pause ends, it must not undo a nonlocal
    # that a function the expression called changed in the frame.
    kernel.start(LOCALS, breakpoints=[6])
    assert wait(kernel)["paused"]["line"] == 6
    kernel.debug("eval", "bump()")
    assert kernel.debug("eval", "q")["value"] == "11"
    kernel.debug("eval", "bump()")
    assert "".join(o.get("text", "") for o in continue_finishes(kernel)["outputs"]) == "21 2\n"


def test_a_nonlocal_a_called_function_changes_after_an_expression_assigned_it_stays(kernel):
    # Both at once: an earlier expression's assignment must not be put back over the frame's newer value.
    # Before 3.13 the assignment reaches the frame only as the pause ends, so bump() still adds to 1 there.
    kernel.start(LOCALS, breakpoints=[6])
    assert wait(kernel)["paused"]["line"] == 6
    assert kernel.debug("eval", "(q := 5)")["value"] == "5"
    kernel.debug("eval", "bump()")
    assert kernel.debug("eval", "q")["value"] == ("11" if sys.version_info < (3, 13) else "15")
    assert kernel.debug("eval", "(q := 3)")["value"] == "3"  # and a later assignment still holds
    assert "".join(o.get("text", "") for o in continue_finishes(kernel)["outputs"]) == "3 2\n"


def test_a_function_local_a_watch_assigns_stays(kernel):
    kernel.start(LOCALS, breakpoints=[6], watches=["(q := 8)"])
    p = wait(kernel)["paused"]
    assert p["line"] == 6 and p["watches"][0]["value"] == "8"
    assert kernel.debug("eval", "q")["value"] == "8"
    assert "".join(o.get("text", "") for o in continue_finishes(kernel)["outputs"]) == "8 2\n"


def test_an_expression_calling_a_function_with_a_breakpoint_does_not_pause_again(kernel):
    kernel.start(CODE, breakpoints=[3, 5])
    assert wait(kernel)["paused"]["line"] == 5
    started = time.monotonic()
    assert kernel.debug("eval", "grow(7)")["value"] == "3"  # ran through line 3 without stopping
    assert time.monotonic() - started < 5
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
    started = time.monotonic()
    answer = kernel.debug("eval", expr)
    assert time.monotonic() - started < debugger.EVAL_SECONDS
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
