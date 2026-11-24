"""Breakpoints and stepping: what the first mutation run of debugger.py left alive (batch 347)."""

from __future__ import annotations

import inspect
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import debugger, notebook
from fabric_desktop.notebook import Kernel


class Frame:
    """A stand-in frame: compared and hashed by identity, as a real frame is (SimpleNamespace isn't hashable)."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


def code(name, flags=inspect.CO_OPTIMIZED):
    return SimpleNamespace(co_name=name, co_flags=flags)


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
    frame = Frame(f_code=code("f"), f_locals={f"v{i:03d}": i for i in range(n)})
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
    inner = Frame(f_code=code("f"), f_locals={"__x": 1, "a": 2, "kernel_thing": 3})
    assert [r["name"] for r in d._variables(inner)[0]] == ["a", "kernel_thing"]
    top = Frame(f_code=code("<module>", 0), f_globals={"__x": 1, "a": 2, "kernel_thing": 3})  # the cell's namespace
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
    return Frame(f_code=compile("pass", "lib.py", "exec").replace(co_name="f"), f_globals={}, f_locals=dict(names), f_lineno=3,
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


# ---- what #3233 and #3243 added (batch 347, run again on them)

def test_past_the_describe_deadline_a_variable_is_listed_by_name_and_type_only(monkeypatch):
    monkeypatch.setattr(debugger, "DESCRIBE_SECONDS", 0.0)
    seen = []
    d = debugger.Debugger([], False, lambda name, value: seen.append(name) or describe(name, value))
    rows, total = d._variables(Frame(f_code=code("f"), f_locals={"a": 1}))
    assert rows == [{"name": "a", "type": "int", "size": "", "value": "?", "memory": None, "expandable": False}]
    assert total == 1 and seen == []


def test_a_last_expression_on_the_line_before_it_is_not_a_second_stop():
    k = Kernel()
    k.start("y = 2; y", breakpoints=[1])
    assert waiting(k)["paused"]["line"] == 1
    k.debug("continue")
    snap = waiting(k)
    assert snap["status"] == "ok" and snap["paused"] is None


def test_a_pause_in_a_class_body_shows_the_class_s_own_names():
    k = Kernel()
    k.start("class C:\n    a = 1\n    b = 2\nz = 0", breakpoints=[3])
    paused = waiting(k)["paused"]
    assert [v["name"] for v in paused["variables"]] == ["a"]
    assert k.debug("eval", "a + 1")["value"] == "2"
    k.debug("continue")
    waiting(k)


def test_breakpoint_in_the_cell_pauses_on_its_line():
    k = Kernel()
    k.start("a = 1\nbreakpoint()\nb = 2\nc = 3", breakpoints=[4])
    paused = waiting(k)["paused"]
    assert (paused["line"], paused["reason"]) == (2, "breakpoint")
    k.debug("continue")
    assert waiting(k)["paused"]["line"] == 4
    k.debug("continue")
    assert waiting(k)["status"] == "ok"


def test_breakpoint_here_is_refused_outside_the_cell_or_while_paused():
    d = debugger.Debugger([], False, describe)
    cell = compile("x = 1", notebook._CELL_FILE, "exec")
    d.add(cell)
    assert d.pause_here(Frame(f_code=compile("pass", "lib.py", "exec"))) is False
    d.paused = {}
    assert d.pause_here(Frame(f_code=cell)) is False


def test_a_cell_that_turns_tracing_off_is_told_the_debugger_lost_track():
    k = Kernel()
    k.start("import sys\nsys.settrace(None)\nb = 2", breakpoints=[3])
    snap = waiting(k)
    assert snap["status"] == "ok"
    assert any("The debugger lost track of this cell partway" in str(o.get("text")) for o in snap["outputs"])
    k.start("a = 1\nb = 2", breakpoints=[2])
    waiting(k)
    k.debug("continue")
    assert not any("lost track" in str(o.get("text")) for o in waiting(k)["outputs"])


def test_a_breakpoint_on_a_blank_line_pauses_at_the_next_code_and_one_past_the_end_says_so():
    k = Kernel()
    k.start("a = 1\n\nb = 2\n# end", breakpoints=[2, 4])
    assert waiting(k)["paused"]["line"] == 3
    k.debug("continue")
    snap = waiting(k)
    assert snap["status"] == "ok"
    assert [o["text"] for o in snap["outputs"]] == [
        ("The breakpoint on line 4 can't pause, because no code runs from there to the end of the cell. "
         "Move it to a line with code.")]


@pytest.mark.parametrize("action", ["step", "next"])
def test_a_step_from_a_comprehension_line_goes_on_to_the_next_line(action):
    k = Kernel()
    k.start("xs = [i for i in range(3)]\ny = 1", breakpoints=[], step=True)
    assert waiting(k)["paused"]["line"] == 1
    k.debug(action)
    assert waiting(k)["paused"]["line"] == 2  # not line 1 again for each item
    k.debug("continue")
    assert waiting(k)["status"] == "ok"


def test_a_generator_resumed_on_the_same_cell_line_is_a_new_place_to_stop():
    k = Kernel()
    k.start("def gen():\n    for i in range(3): yield i\nit = gen()\na = next(it)\nb = [next(it), next(it)]\nc = 1",
            breakpoints=[5])
    assert waiting(k)["paused"]["line"] == 5
    k.debug("step")
    assert [f["function"] for f in waiting(k)["paused"]["stack"]] == ["(cell)", "gen"]
    k.debug("next")  # the generator yields, and the cell's line resumes it at once: the step ended when it left
    paused = waiting(k)["paused"]
    assert (paused["line"], [f["function"] for f in paused["stack"]]) == (2, ["(cell)", "gen"])
    k.debug("next")
    assert waiting(k)["paused"]["line"] == 6
    k.debug("continue")
    assert waiting(k)["status"] == "ok"
