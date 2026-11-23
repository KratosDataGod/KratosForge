"""Mutation tests for the debugger (debugger.py): its limits, each exactly at the edge, on real frames
paused directly rather than under sys.settrace. Found by tools/mutate.py."""

import threading
import types

import pytest

from fabric_desktop import debugger as dbg


def describe(name, value):
    return {"name": name, "type": type(value).__name__, "value": repr(value)}


def _frame_with(n):
    """A finished frame of a cell function with ``n`` variables, and a debugger tracing that cell."""
    body = "".join(f"    v{k:03} = {k}\n" for k in range(n))
    code = compile(f"import sys\ndef f():\n{body}    return sys._getframe()\nF = f()\n", dbg.CELL_FILE, "exec")
    ns = {}
    exec(code, ns)  # noqa: S102 - the test's own cell
    changes = []
    d = dbg.Debugger([], True, describe, on_change=changes.append)
    d.add(code)
    return d, ns["F"], changes


def test_a_hundred_breakpoints_are_taken():
    assert dbg.check_breakpoints(list(range(dbg.MAX_BREAKPOINTS, 0, -1))) == list(range(1, dbg.MAX_BREAKPOINTS + 1))


def test_an_expression_of_the_longest_length_is_evaluated(monkeypatch):
    d = dbg.Debugger([], False, describe)
    d.paused = {"line": 1}
    monkeypatch.setattr(dbg, "EVAL_SECONDS", 0.0)
    assert d.command("eval", "1" * dbg.MAX_EXPRESSION) == {"pending": True}
    assert d.commands.get_nowait()[0] == "eval"


@pytest.mark.parametrize("n", [dbg.MAX_LOCALS, dbg.MAX_LOCALS + 1])
def test_a_pause_shows_up_to_the_most_variables_and_counts_the_rest(n):
    d, frame, changes = _frame_with(n)
    d.commands.put(("continue", None))
    d._pause(frame, "step")
    shown = changes[0]["variables"]
    assert len(shown) == dbg.MAX_LOCALS == 200 and shown[-1]["name"] == "v199"
    assert changes[0].get("variables_total") == (n if n > dbg.MAX_LOCALS else None)


def test_step_into_names_at_most_five_calls_it_ran_over():
    code = compile("def callee():\n    pass\n", "lib.py", "exec").co_consts[0]
    d = dbg.Debugger([], True, describe)
    for k in range(dbg.MAX_SKIPPED + 2):
        d._note_skipped(types.SimpleNamespace(f_code=code, f_globals={"__name__": f"m{k}"}))
    assert d.skipped == [f"m{k}.callee" for k in range(5)]


def test_a_pause_keeps_the_last_twenty_evaluations():
    d, frame, changes = _frame_with(1)
    for k in range(21):
        d.commands.put(("eval", (f"v000 + {k}", {"done": threading.Event()})))
    d.commands.put(("continue", None))
    d._pause(frame, "step")
    assert [e["value"] for e in changes[0]["evals"]] == [str(k) for k in range(1, 21)]
