"""The debugger's own logic (debugger.py), called directly on real frames rather than under
sys.settrace, so each decision is checked on its own: which breakpoints are accepted, which frames
are traced and which calls Step into reports as skipped, where next, out and a return stop, what a
pause shows (variables, watches, evaluations, skipped calls), and how commands reach a paused cell."""

from __future__ import annotations

import sys
import threading

import pytest

from fabric_desktop import debugger as dbg

CELL = """\
import math
X = 5
Big = int
__hidden = 1
kernel_own = 2
TOP = sys._getframe()
def inner(n):
    m = n * 2
    return sys._getframe()
F = inner(3)
"""


def describe(name, value):
    return {"name": name, "type": type(value).__name__, "value": repr(value)}


def _cell(**kw):
    """A debugger with the cell compiled and run: its top frame and inner()'s frame, both finished."""
    code = compile(CELL, dbg.CELL_FILE, "exec")
    ns = {"sys": sys}
    exec(code, ns)  # noqa: S102 - the test's own cell
    d = dbg.Debugger([], False, describe, **kw)
    d.add(code)
    return d, ns["TOP"], ns["F"], ns


# ---- breakpoints


def test_breakpoints_are_whole_line_numbers_sorted_once():
    assert dbg.check_breakpoints(None) == []
    assert dbg.check_breakpoints([5, 2, 5]) == [2, 5]
    for bad in ("3", [1] * (dbg.MAX_BREAKPOINTS + 1)):
        with pytest.raises(ValueError, match="Breakpoints must be a list of at most 100"):
            dbg.check_breakpoints(bad)
    for bad in ([0], [True], [1.5]):
        with pytest.raises(ValueError, match="A breakpoint must be a line number, 1 or more."):
            dbg.check_breakpoints(bad)


# ---- what is traced


def test_the_cell_its_functions_are_traced_and_nothing_else():
    d, top, inner, ns = _cell()
    assert top.f_code in d.codes and ns["inner"].__code__ in d.codes
    assert d.trace(top, "call", None) == d._line
    assert d.trace(sys._getframe(), "call", None) is None
    assert d.skipped == []  # not stepping: nothing to report


def _called_from_cell(fn_src, filename, globals_):
    """A finished frame of a function compiled from ``fn_src``, called from a frame of the cell."""
    code = compile(fn_src, filename, "exec")
    exec(code, globals_)  # noqa: S102 - the test's own code
    return globals_["callee"]()


def test_step_into_reports_the_calls_it_runs_over_by_name_at_most_five(monkeypatch):
    d, _top, _inner, ns = _cell()
    d.mode = "step"
    caller = compile("def run(f):\n    return f()\n", dbg.CELL_FILE, "exec")
    g = {"sys": sys}
    exec(caller, g)  # noqa: S102
    d.add(caller)

    def frame_of(src, filename, glb):
        exec(compile(src, filename, "exec"), glb)  # noqa: S102
        return g["run"](glb["callee"])

    src = "import sys\ndef callee():\n    return sys._getframe()\n"
    other_cell = frame_of(src, dbg.CELL_FILE, {"__name__": "x"})
    library = frame_of(src, "lib.py", {"__name__": "pkg.mod"})
    nameless = frame_of(src, "lib.py", {"__name__": ""})
    for f in (other_cell, library, nameless):
        assert f.f_back is not None and f.f_back.f_code in d.codes
        assert d.trace(f, "call", None) is None
    d.trace(library, "call", None)  # the same call twice is named once
    assert d.skipped == ["callee (defined in another cell)", "pkg.mod.callee", "callee"]
    for k in range(5):
        d._note_skipped(frame_of(src, "lib.py", {"__name__": f"m{k}"}))
    assert len(d.skipped) == dbg.MAX_SKIPPED


# ---- where a step stops


def test_next_out_and_continue_stop_where_they_should():
    d, top, inner, _ns = _cell()
    assert d._depth(top) == 1
    d.depth = 2
    d.mode = "next"
    assert d._stops(top) is True  # back in the caller
    d.mode = "out"
    assert d._stops(top) is True
    d.depth = 1
    assert d._stops(top) is False
    d.mode = "continue"
    assert d._stops(top) is False
    d.lines = {top.f_lineno}
    assert d._stops(top) is True
    d.mode = "step"
    d.lines = set()
    assert d._stops(top) is True


def test_a_return_stops_a_step_that_came_back_but_not_the_cell_or_an_exception():
    d, top, inner, _ns = _cell()
    d.mode = "continue"
    assert d._stops_on_return(inner) is False
    d.mode, d.depth = "next", 3
    assert d._stops_on_return(top) is False  # the cell's own frame: a step past its end ends the cell
    assert d._depth(inner) == 2
    assert d._stops_on_return(inner) is True  # back in a function shallower than the last pause
    d.depth = 2
    assert d._stops_on_return(inner) is False
    d.depth = 3
    d._line(inner, "exception", None)
    assert d._stops_on_return(inner) is False  # unwinding, not returning
    d.mode = "continue"
    assert d._line(inner, "line", None) == d._line and d._raising is None


# ---- what a pause shows


def test_a_pause_shows_the_frames_variables_watches_and_what_was_stepped_over(monkeypatch):
    changes = []
    d, top, inner, _ns = _cell(watches=["X + 1", "nope"], redact=lambda t: t.replace("nope", "****"),
                                on_change=changes.append)
    d.skip = {"kernel_own"}
    d.skipped = ["pkg.mod.callee"]
    d.lines = {top.f_lineno}
    d.commands.put(("eval", ("X * 10", {"done": threading.Event()})))
    d.commands.put(("next", None))
    d._line(top, "line", None)
    paused = changes[0]
    assert paused is not None and changes[-1] is None
    assert (paused["line"], paused["reason"]) == (top.f_lineno, "breakpoint")
    assert paused["stack"] == [{"function": "(cell)", "line": top.f_lineno}]
    assert [v["name"] for v in paused["variables"]] == ["F", "TOP", "X"]  # no module, type, function, dunder or kernel name
    assert paused["watches"][0]["value"] == "6"
    assert paused["watches"][1] == {"expr": "nope", "error": "NameError: name '****' is not defined"}
    assert [e["value"] for e in paused["evals"]] == ["50"]
    assert paused["skipped"] == ["pkg.mod.callee"] and d.skipped == []
    assert d.mode == "next" and d.depth == 1 and d.resumed.is_set() and d.paused is None


def test_a_frame_with_many_variables_shows_the_first_by_name_and_counts_them(monkeypatch):
    monkeypatch.setattr(dbg, "MAX_LOCALS", 1)
    changes = []
    d, top, inner, _ns = _cell(on_change=changes.append)
    d.mode = "step"
    d.commands.put(("continue", None))
    d._pause(inner, "step")
    assert [v["name"] for v in changes[0]["variables"]] == ["m"]
    assert changes[0]["variables_total"] == 2
    assert changes[0]["stack"] == [{"function": "(cell)", "line": 10}, {"function": "inner", "line": inner.f_lineno}]


def test_a_step_that_returns_pauses_on_the_return():
    changes = []
    d, top, inner, _ns = _cell(on_change=changes.append)
    d.mode, d.depth = "out", 3
    d.commands.put(("step", None))
    assert d._line(inner, "return", None) == d._line
    assert changes[0]["reason"] == "step" and d.mode == "step" and d.depth == 2


def test_cancel_while_paused_ends_the_cell():
    d, top, _inner, _ns = _cell()
    d.commands.put(("stop", None))
    with pytest.raises(dbg.Stopped):
        d._pause(top, "step")
    assert d.paused is None and d.resumed.is_set()


# ---- commands from the engine


def test_commands_need_a_paused_cell_and_well_formed_input(monkeypatch):
    d, top, _inner, _ns = _cell()
    with pytest.raises(RuntimeError, match="The cell isn't paused."):
        d.command("continue")
    assert d.stop() is False
    d.paused = {"line": 1}
    with pytest.raises(ValueError, match="A debugger action is text"):
        d.command(3)
    with pytest.raises(ValueError, match="Type an expression to evaluate."):
        d.command("eval", "  ")
    with pytest.raises(ValueError, match="at most 2000 characters"):
        d.command("eval", "1" * (dbg.MAX_EXPRESSION + 1))
    with pytest.raises(SyntaxError):
        d.command("eval", "1 +")
    with pytest.raises(ValueError, match="Unknown debugger action 'jump'"):
        d.command("jump")
    monkeypatch.setattr(dbg, "EVAL_SECONDS", 0.0)
    assert d.command("eval", "X") == {"pending": True}  # nobody on the cell's thread answered
    assert d.stop() is True
    assert [a for a, _ in list(d.commands.queue)] == ["eval", "stop"]


def test_commands_reach_a_paused_cell_on_its_own_thread():
    paused = threading.Event()
    d, top, _inner, _ns = _cell(on_change=lambda p: paused.set() if p else None)
    t = threading.Thread(target=d._pause, args=(top, "step"))
    t.start()
    assert paused.wait(10)
    assert d.command("eval", "X + 2")["value"] == "7"
    assert d.command("out") == {"resumed": "out"}
    t.join(10)
    assert not t.is_alive() and d.mode == "out" and d.paused is None
