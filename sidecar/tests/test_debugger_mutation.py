"""Breakpoints and stepping: what the first mutation run of debugger.py left alive (batch 347)."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import debugger, notebook
from fabric_desktop.notebook import Kernel


def describe(name, value):
    return {"name": name, "value": repr(value)}


def waiting(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def test_at_most_100_breakpoints():
    assert debugger.check_breakpoints(list(range(1, 101))) == list(range(1, 101))
    with pytest.raises(ValueError, match="at most 100 line numbers"):
        debugger.check_breakpoints(list(range(1, 102)))


@pytest.mark.parametrize("n, shown", [(200, 200), (201, 200)])
def test_a_frame_shows_its_first_200_variables(n, shown):
    d = debugger.Debugger([], False, describe)
    frame = SimpleNamespace(f_code=SimpleNamespace(co_name="f"), f_locals={f"v{i:03d}": i for i in range(n)})
    rows, total = d._variables(frame)
    assert len(rows) == shown and total == n and rows[0]["name"] == "v000"


def test_at_most_five_stepped_over_names_are_kept():
    d = debugger.Debugger([], True, describe)
    cell = compile("x = 1", notebook._CELL_FILE, "exec")
    d.add(cell)
    back = SimpleNamespace(f_code=cell)
    for i in range(7):
        code = compile("pass", "lib.py", "exec").replace(co_name=f"f{i}", co_qualname=f"f{i}")
        d.trace(SimpleNamespace(f_code=code, f_back=back, f_globals={"__name__": "lib"}), "call", None)
    assert d.skipped == [f"lib.f{i}" for i in range(5)]


def test_an_expression_is_at_most_2000_characters_and_waited_for_10_seconds(monkeypatch):
    waits = []

    class Event:
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

        def set(self):
            pass

    monkeypatch.setattr(debugger, "threading", SimpleNamespace(Event=Event))
    d = debugger.Debugger([], False, describe)
    d.paused = {}
    assert d.command("eval", "1" * 2000) == {"pending": True}
    assert waits == [10.0]
    with pytest.raises(ValueError, match="at most 2000 characters"):
        d.command("eval", "1" * 2001)


def test_a_pause_keeps_the_latest_20_evaluations():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    waiting(k)
    for i in range(21):
        assert k.debug("eval", f"{i} + 0")["value"] == str(i)
    evals = waiting(k)["paused"]["evals"]
    assert len(evals) == 20 and evals[0]["expr"] == "1 + 0" and evals[-1]["expr"] == "20 + 0"
    k.debug("continue")
    waiting(k)


def _plain_frame_suspends():
    return debugger._suspends(sys._getframe())  # asked while the frame runs, at a call, not a return


def test_only_a_generator_or_coroutine_frame_can_be_suspending():
    assert _plain_frame_suspends() is False

    def gen():
        yield 1
        yield 2

    g = gen()
    next(g)
    assert debugger._suspends(g.gi_frame) is True


def test_a_command_says_what_it_resumed():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[1])
    waiting(k)
    assert k.debug("continue") == {"resumed": "continue"}
    assert waiting(k)["status"] == "ok"


def test_commands_and_stop_on_a_debugger_that_isnt_paused():
    d = debugger.Debugger([], False, describe)
    with pytest.raises(RuntimeError, match="The cell isn't paused."):
        d.command("step")
    assert d.stop() is False and d.stopping is True
    d = debugger.Debugger([], False, describe)
    d.paused = {}
    assert d.stop() is True and d.commands.get_nowait() == ("stop", None)


def test_a_generator_suspends_at_each_yield_and_ends_at_its_return():
    def gen():
        yield 1
        yield 2

    seen = []

    def tracer(frame, event, arg):
        if frame.f_code is gen.__code__:
            if event == "return":
                seen.append(debugger._suspends(frame))
            return tracer
        return None

    before = sys.gettrace()  # a coverage tracer, if one runs, is put back
    sys.settrace(tracer)
    try:
        assert list(gen()) == [1, 2]
    finally:
        sys.settrace(before)
    assert seen == [True, True, False]


def test_dunder_names_are_never_variables_and_the_kernels_own_only_at_the_top():
    d = debugger.Debugger([], False, describe)
    d.skip = {"kernel_thing"}
    inner = SimpleNamespace(f_code=SimpleNamespace(co_name="f"), f_locals={"__x": 1, "a": 2, "kernel_thing": 3})
    assert [r["name"] for r in d._variables(inner)[0]] == ["a", "kernel_thing"]
    top = SimpleNamespace(f_code=SimpleNamespace(co_name="<module>"), f_locals={"__x": 1, "a": 2, "kernel_thing": 3})
    assert [r["name"] for r in d._variables(top)[0]] == ["a"]


def test_a_stepped_over_name_uses_its_module_only_when_that_is_text():
    d = debugger.Debugger([], True, describe)
    cell = compile("x = 1", notebook._CELL_FILE, "exec")
    d.add(cell)
    back = SimpleNamespace(f_code=cell)
    for module in ("", 5):
        code = compile("pass", "lib.py", "exec").replace(co_name="f", co_qualname="Q.f")
        d.trace(SimpleNamespace(f_code=code, f_back=back, f_globals={"__name__": module}), "call", None)
    assert d.skipped == ["f"]


def fake_frame(**names):
    return SimpleNamespace(f_code=compile("pass", "lib.py", "exec").replace(co_name="f"), f_globals={}, f_locals=dict(names), f_lineno=3,
                           f_back=None)


def test_an_expression_interrupted_by_stop_ends_the_cell_instead_of_answering():
    d = debugger.Debugger([], False, describe)
    assert d._evaluate(fake_frame(n=1), "1/0")["error"] == "ZeroDivisionError: division by zero"
    d.stopping = True
    with pytest.raises(ZeroDivisionError):
        d._evaluate(fake_frame(n=1), "1/0")


def test_a_pause_after_stop_was_asked_ends_the_cell_at_once():
    changes = []
    d = debugger.Debugger([], False, describe, on_change=changes.append)
    d.stopping = True
    raised = []

    def pause():
        try:
            d._pause(fake_frame(n=1), "step")
        except BaseException as exc:  # noqa: BLE001 - what the pause ended with is the point
            raised.append(exc)

    t = threading.Thread(target=pause, daemon=True)
    t.start()
    t.join(5)
    waited = t.is_alive()
    if waited:
        d.commands.put(("stop", None))  # don't leave it waiting
        t.join(5)
    assert not waited, "the pause waited for a command instead of ending at once"
    assert raised and isinstance(raised[0], debugger.Stopped)
    assert d.paused is None and d.resumed.is_set() and changes[-1] is None


def test_a_step_that_lands_on_a_breakpoint_line_is_a_step():
    k = Kernel()
    k.start("a = 1\nb = 2\nc = 3", breakpoints=[2], step=True)
    assert waiting(k)["paused"]["reason"] == "step"
    k.debug("step")
    p = waiting(k)["paused"]
    assert (p["line"], p["reason"]) == (2, "step")
    k.debug("continue")
    waiting(k)
