"""Mutation batch 475: tests for the debugger mutants (debugger.py, and the debugger parts of
notebook.py) that the other debugger tests let through (docs/quality/mutation/batch-475.md)."""

from __future__ import annotations

import sys
import threading
import time
import types

from fabric_desktop import debugger, notebook
from fabric_desktop.notebook import Kernel


def wait(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def row(name, value):
    return {"name": name, "value": repr(value)}


# ---------- the limits are the ones the docs and the app state ----------

def test_the_limits_are_the_documented_ones():
    # The app's panel and its messages state these numbers (app/ui/debugging.js dbgBpLimit, the
    # Evaluate box's 500 characters), so a change here is a change the app has to make too.
    assert debugger.MAX_BREAKPOINTS == 100
    assert debugger.MAX_LOCALS == 200
    assert debugger.MAX_SKIPPED == 5
    assert debugger.MAX_EVALS == 20
    assert debugger.MAX_EXPRESSION == 2000
    assert debugger.EVAL_SECONDS == 10.0
    assert debugger.DESCRIBE_SECONDS == 3.0


# ---------- command and stop answer as documented ----------

def test_resuming_answers_with_the_action():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    assert k.debug("continue") == {"resumed": "continue"}
    assert wait(k)["status"] == "ok"


def test_stop_says_whether_the_cell_was_paused_and_only_then_queues_a_stop():
    d = debugger.Debugger([], False, row)
    assert d.stop() is False
    assert d.stopping is True and d.commands.empty()
    d = debugger.Debugger([], False, row)
    d.paused = {"line": 1}
    assert d.stop() is True
    assert d.commands.get_nowait() == ("stop", None)


def test_pause_here_is_true_once_it_has_paused_in_the_cells_code():
    d = debugger.Debugger([], False, row)
    code = compile("import sys\nframe = sys._getframe()\n", notebook._CELL_FILE, "exec")
    d.add(code)
    ns: dict = {}
    exec(code, ns)  # noqa: S102 - the test's own code
    answers = []
    t = threading.Thread(target=lambda: answers.append(d.pause_here(ns["frame"])))
    t.start()
    deadline = time.monotonic() + 10
    while d.paused is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert d.paused is not None and d.paused["reason"] == "breakpoint"
    d.commands.put(("continue", None))
    t.join(10)
    assert answers == [True]
    assert d.pause_here(sys._getframe()) is False  # not the cell's code


# ---------- breakpoint() outside a cell's thread ----------

def test_breakpoint_off_a_cells_thread_goes_to_the_hook_that_was_there(monkeypatch):
    calls = []
    monkeypatch.setattr(notebook._breakpoint_hook, "previous", lambda *a, **kw: calls.append((a, kw)) or "pdb")
    assert notebook._breakpoint_hook(1, x=2) == "pdb"  # this thread runs no cell
    assert calls == [((1,), {"x": 2})]


# ---------- reading a value with no time left ----------

def test_a_read_with_no_time_left_gives_the_fallback_without_reading(monkeypatch):
    # No timer at all for no time left: with one that never fires, a read would still run.
    monkeypatch.setattr(notebook.threading, "Timer", lambda *a: (_ for _ in ()).throw(AssertionError("a timer")))
    read = []
    assert notebook._bounded(lambda: read.append(1) or "read", lambda: "fallback", 0) == "fallback"
    assert notebook._bounded(lambda: read.append(1) or "read", lambda: "fallback", -1) == "fallback"
    assert read == []


def test_an_opened_variable_past_its_time_lists_its_items_as_unread(monkeypatch):
    class Thing:
        def __repr__(self):
            return "thing"
    k = Kernel()
    k.namespace["things"] = [Thing(), Thing()]
    monkeypatch.setattr(notebook, "DESCRIBE_TOTAL_SECONDS", 0)
    detail = k.variable_detail("things")
    assert detail["items"] == [{"key": "0", "type": "Thing", "size": "", "value": "?"},
                               {"key": "1", "type": "Thing", "size": "", "value": "?"}]
    assert detail["total"] == 2


# ---------- a guest leaving the shared session ----------

def test_forgetting_a_guest_in_a_kernel_that_ran_nothing_says_there_was_nothing():
    assert Kernel().forget_guest("A") == {"forgotten": False}


def test_forgetting_another_guest_leaves_the_paused_cell_paused():
    k = Kernel()
    with k.as_guest("A"):
        k.run("kept = 1")
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
    assert k.forget_guest("B") == {"forgotten": False}
    with k.as_guest("A"):
        assert wait(k)["paused"]["line"] == 2
    assert k.forget_guest("A") == {"forgotten": True}
    assert k._current.done.wait(10)
    assert k._current.status == "cancelled"


def test_a_command_for_a_cell_that_isnt_paused_is_refused_and_not_kept_for_later():
    d = debugger.Debugger([], False, row)
    for action in ("continue", "step", "eval"):
        try:
            d.command(action, "1")
        except RuntimeError as exc:
            assert str(exc) == "The cell isn't paused."
        else:
            raise AssertionError("a command for a cell that isn't paused was taken")
    assert d.commands.empty()


class _AsyncExcRecorder:
    """Stands in for ctypes.pythonapi: records what _bounded would raise in the reading thread."""

    def __init__(self):
        self.sent = []

    def PyThreadState_SetAsyncExc(self, ident, exc):  # noqa: N802 - the C API's name
        self.sent.append((ident.value, None if exc is None else exc.value))
        return 1


def test_a_read_that_ends_as_its_timer_fires_takes_back_the_pending_interrupt(monkeypatch):
    fake = _AsyncExcRecorder()
    monkeypatch.setattr(notebook.ctypes, "pythonapi", fake)
    me = threading.get_ident()
    # The timer fires while the read is still going (the stand-in raises nothing), and the read then
    # ends: the interrupt it sent must be taken back, or it would land in whatever runs next.
    assert notebook._bounded(lambda: time.sleep(0.3) or "read", lambda: "fallback", 0.01) == "read"
    assert fake.sent == [(me, notebook._TooSlow), (me, None)]


def test_a_quick_read_sends_nothing_to_its_thread(monkeypatch):
    fake = _AsyncExcRecorder()
    monkeypatch.setattr(notebook.ctypes, "pythonapi", fake)
    # Nothing to take back: clearing anyway would drop a Cancel that arrived meanwhile.
    assert notebook._bounded(lambda: "read", lambda: "fallback", 5) == "read"
    assert fake.sent == []


def test_variables_past_the_describe_time_are_listed_unread_and_never_expandable(monkeypatch):
    monkeypatch.setattr(debugger, "DESCRIBE_SECONDS", 0)
    # A clock that stands still: the time is up at the deadline itself, not only past it.
    monkeypatch.setattr(debugger, "time", type("Clock", (), {"monotonic": staticmethod(lambda: 100.0)}))
    d = debugger.Debugger([], False, row)
    code = compile("import sys\nitems = [1, 2]\nframe = sys._getframe()\n", "<cell>", "exec")
    d.add(code)
    ns: dict = {}
    exec(code, ns)  # noqa: S102 - the test's own code
    rows, total = d._variables(ns["frame"])
    assert {"name": "items", "type": "list", "size": "", "value": "?", "memory": None, "expandable": False} in rows


def test_a_class_body_that_has_run_isnt_kept_by_the_debugger():
    # Each class body's namespace is held while it runs (Debugger.trace), and let go as it returns,
    # so a cell that defines classes in a loop doesn't keep every one of them until it ends.
    k = Kernel()
    k.start("for i in range(5):\n    class C:\n        v = i\nx = 1", breakpoints=[4])
    assert wait(k)["paused"]["line"] == 4
    held = list(k._current.debugger._scopes)
    assert [f.f_code.co_name for f in held] == ["<module>"]
    k.debug("continue")
    assert wait(k)["status"] == "ok"


# ---------- a guest renamed ----------

def test_renaming_a_guest_that_has_variables_but_ran_no_cell_moves_them():
    k = Kernel()
    with k.as_guest("G"):
        k.variables()  # its namespace, before any cell
    assert k.rename_guest("G", "G2") == {"renamed": True}
    assert "G2" in k._guests and "G" not in k._guests


def test_renaming_a_guest_moves_its_last_cell_even_when_the_new_name_has_variables():
    k = Kernel()
    with k.as_guest("G"):
        assert k.run("a = 1")["status"] == "ok"
    with k.as_guest("G2"):
        k.variables()
    assert k.rename_guest("G", "G2") == {"renamed": True}  # the cell moved, the variables couldn't
    assert "G" in k._guests and k._last_runs["G2"].guest == "G2" and "G" not in k._last_runs


# ---------- Cancel while paused ----------

def test_cancel_while_paused_answers_that_the_cell_stopped():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    assert k.interrupt() == {"interrupted": True, "stopped": True}
    assert wait(k)["status"] == "cancelled"


def test_a_timer_that_fires_after_the_read_ended_sends_nothing(monkeypatch):
    fake = _AsyncExcRecorder()
    monkeypatch.setattr(notebook.ctypes, "pythonapi", fake)
    timers = []

    class LateTimer:
        """A timer whose cancel comes too late: it fires once the read has already ended."""

        def __init__(self, seconds, fn):
            self.fn, self.daemon = fn, False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(notebook.threading, "Timer", LateTimer)
    assert notebook._bounded(lambda: "read", lambda: "fallback", 5) == "read"
    timers[0].fn()
    assert fake.sent == []  # disarmed as the read ended: nothing lands in what runs next


# ---------- what Step into names when it steps over a call ----------

def test_a_skipped_call_whose_module_name_isnt_text_is_named_by_its_function():
    d = debugger.Debugger([], True, row)
    cell = compile("x = 1", notebook._CELL_FILE, "exec")
    d.add(cell)

    class CellFrame:
        f_code = cell

    class Frame:
        def __init__(self, module):
            self.f_code = compile("pass", "lib.py", "exec").replace(co_name="work", co_qualname="Lib.work")
            self.f_back = CellFrame()
            self.f_globals = {"__name__": module}

    for module in (7, "", None):
        d.skipped = []
        d.trace(Frame(module), "call", None)
        assert d.skipped == ["work"]
    d.skipped = []
    d.trace(Frame("lib"), "call", None)
    assert d.skipped == ["lib.Lib.work"]


# ---------- a one-line loop that calls the cell's own function ----------

def test_step_over_a_one_line_loop_calling_a_cell_function_runs_the_whole_loop():
    # The function returning on each turn must not make the loop's own line a new place to stop.
    k = Kernel()
    k.start("def f(n):\n    return n\ntotal = 0\nfor i in range(3): total += f(i)\nend = total", step=True)
    for line in (1, 3, 4):  # no breakpoint on the loop's line, which would stop on every turn
        assert wait(k)["paused"]["line"] == line
        if line != 4:
            k.debug("next")
    k.debug("next")
    p = wait(k)["paused"]
    assert p["line"] == 5
    assert {v["name"]: v["value"] for v in p["variables"]}["total"] == "3"
    k.debug("continue")
    assert wait(k)["status"] == "ok"


# ---------- Stop's interrupt in an expression ----------

def test_once_stop_is_asked_for_an_expressions_interrupt_ends_the_cell_instead_of_being_its_answer():
    d = debugger.Debugger([], False, row)
    frame = sys._getframe()
    assert d._evaluate(frame, "1/0")["error"] == "ZeroDivisionError: division by zero"
    d.stopping = True
    try:
        d._evaluate(frame, "(_ for _ in ()).throw(KeyboardInterrupt)")
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("Stop's interrupt was shown as the expression's answer")


def test_a_pause_reached_after_stop_was_asked_for_ends_at_once():
    # Cancel came while the cell ran (stop() found nothing paused), and the cell caught the interrupt
    # and ran on to a breakpoint: that pause ends the cell without waiting for a command.
    d = debugger.Debugger([], False, row)
    assert d.stop() is False
    raised = []

    def pause():
        try:
            d._pause(sys._getframe(), "breakpoint")
        except debugger.Stopped:
            raised.append(True)
    t = threading.Thread(target=pause, daemon=True)
    t.start()
    t.join(5)
    if t.is_alive():
        d.commands.put(("stop", None))
        t.join(5)
        raise AssertionError("the pause waited for a command after Stop")
    assert raised == [True] and d.paused is None


# ---------- why a pause happened ----------

def test_a_step_that_lands_on_a_breakpoint_is_a_step_and_continue_to_one_is_a_breakpoint():
    k = Kernel()
    k.start("a = 1\nb = 2\nc = 3\nd = 4", breakpoints=[2, 4], step=True)
    p = wait(k)["paused"]
    assert (p["line"], p["reason"]) == (1, "step")
    k.debug("next")
    p = wait(k)["paused"]
    assert (p["line"], p["reason"]) == (2, "step")  # where the step took it, not a breakpoint hit
    k.debug("continue")
    p = wait(k)["paused"]
    assert (p["line"], p["reason"]) == (4, "breakpoint")
    k.debug("continue")
    assert wait(k)["status"] == "ok"


def test_a_function_a_step_started_in_lets_go_of_its_variables_when_it_returns():
    # The step remembers where it started (to not stop on that line again); once that frame
    # returns it is let go, so its variables are freed as they would be without the debugger.
    k = Kernel()
    code = ("freed = []\nclass Obj:\n    def __del__(self):\n        freed.append(True)\n"
            "def f():\n    o = Obj()\n    return 1\nf()\nx = 1")
    k.start(code, breakpoints=[7, 9])
    assert wait(k)["paused"]["line"] == 7
    k.debug("continue")
    assert wait(k)["paused"]["line"] == 9
    assert k.debug("eval", "freed")["value"] == "[True]"
    k.debug("continue")
    assert wait(k)["status"] == "ok"


# ---------- breakpoints from the app ----------

def test_exactly_the_most_breakpoints_are_taken_and_one_more_is_refused():
    assert debugger.check_breakpoints(list(range(1, 101))) == list(range(1, 101))
    try:
        debugger.check_breakpoints(list(range(1, 102)))
    except ValueError as exc:
        assert str(exc) == "Breakpoints must be a list of at most 100 line numbers."
    else:
        raise AssertionError("101 breakpoints were taken")


def test_an_expression_of_exactly_the_most_characters_is_evaluated_and_one_more_is_refused():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    longest = "a" + " " * (debugger.MAX_EXPRESSION - 1)
    assert k.debug("eval", longest)["value"] == "1"
    try:
        k.debug("eval", longest + " ")
    except ValueError as exc:
        assert str(exc) == "An expression to evaluate is at most 2000 characters."
    else:
        raise AssertionError("an expression past the limit was evaluated")
    k.debug("continue")
    assert wait(k)["status"] == "ok"


# ---------- the trace's yes/no helpers answer True or False ----------

def test_the_trace_helpers_answer_false_not_nothing():
    # Their callers only test them for truth, so these pin the helpers' own answers.
    frame = sys._getframe()
    assert debugger._suspends(frame) is False  # not a generator or coroutine
    d = debugger.Debugger([], False, row)
    assert d._stops(frame) is False  # Continue, and no breakpoint here
    assert d._stops_on_return(frame) is False  # Continue never stops on a return
    at = types.SimpleNamespace(f_lineno=5)
    d.mode, d._from = "step", (at, 5)
    assert d._stops(at) is False  # still the line the step started on
