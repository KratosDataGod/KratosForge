"""Notebook debugger (#2362 hardening, round 6): Python's own language edge cases. Comprehensions
(inlined on 3.12), class bodies, decorators and lambdas, match, try/finally, with, exceptions,
generators, recursion errors, breakpoint() and pdb, cells that set their own trace hook, exec and
eval, del, breakpoints on lines without code, very long lines and non-ASCII names step and pause
on the right line with the right stack and values, never change what the cell computes, and leave
no trace hook behind."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

SIDECAR = Path(__file__).resolve().parents[1]


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


def paused(k):
    snap = wait(k)
    assert snap.get("paused"), snap
    return snap["paused"]


def stack(p):
    return [(f["function"], f["line"]) for f in p["stack"]]


def variables(p):
    return {v["name"]: v["value"] for v in p["variables"]}


def text(snap):
    return "".join(o.get("text", "") for o in snap["outputs"] if o.get("type") in ("text", "stream"))


def finished(k, timeout=20):
    snap = wait(k, timeout)
    assert not snap.get("paused"), snap
    assert snap["status"] == "ok", snap
    return snap


def run_out(k, limit=50):
    """Continue until the cell ends; the pauses it made on the way, as (line, stack) pairs."""
    seen = []
    snap = wait(k)
    while snap.get("paused"):
        assert len(seen) < limit, seen
        seen.append((snap["paused"]["line"], stack(snap["paused"])))
        k.debug("continue")
        snap = wait(k)
    return seen, snap


def untraced(k):
    """The next plain cell runs with no trace hook on its thread and none set for new threads."""
    k.start("import sys, threading\nprint(sys.gettrace() is None, threading.gettrace() is None)")
    assert text(finished(k)).strip() == "True True"


# ---------- comprehensions (inlined into their scope on 3.12) ----------

COMPREHENSIONS = ("xs = [1, 2, 3]\n"
                  "sq = [x * x for x in xs]\n"  # 2
                  "d = {x: -x for x in xs}\n"  # 3
                  "s = {x for x in xs if x > 1}\n"  # 4
                  "done = len(sq) + len(d) + len(s)")  # 5


def test_step_over_a_comprehension_goes_to_the_next_line(kernel):
    kernel.start(COMPREHENSIONS, step=True)
    lines = [paused(kernel)["line"]]
    for _ in range(4):
        kernel.debug("next")
        lines.append(paused(kernel)["line"])
    assert lines == [1, 2, 3, 4, 5]
    kernel.debug("next")
    finished(kernel)
    untraced(kernel)


def test_step_into_a_comprehension_without_calls_goes_to_the_next_line(kernel):
    kernel.start(COMPREHENSIONS, breakpoints=[1])
    assert paused(kernel)["line"] == 1
    for line in (2, 3, 4):
        kernel.debug("step")
        assert paused(kernel)["line"] == line
    kernel.debug("continue")
    finished(kernel)


def test_step_into_a_comprehension_calling_a_cell_function_stops_in_it_each_time(kernel):
    code = ("def double(n):\n"
            "    return n * 2\n"  # 2
            "out = [double(i) for i in range(2)]\n"  # 3
            "end = out")  # 4
    kernel.start(code, breakpoints=[3])
    assert paused(kernel)["line"] == 3
    seen = []
    for _ in range(4):
        kernel.debug("step")
        p = paused(kernel)
        seen.append(stack(p))
    assert seen == [[("(cell)", 3), ("double", 2)], [("(cell)", 3)], [("(cell)", 3), ("double", 2)], [("(cell)", 3)]]
    kernel.debug("step")
    assert paused(kernel)["line"] == 4
    kernel.debug("continue")
    finished(kernel)


MULTILINE = {
    "cell": ("x = 10\n"
             "xs = [1, 2]\n"
             "sq = [x\n"  # 3: the loop variable is bound here
             "      + 1 for x in xs]\n"
             "print(sq, x)"),
    "class": ("class K:\n"
              "    x = 10\n"
              "    xs = [1, 2]\n"
              "    sq = [x\n"  # 4
              "          + 1 for x in xs]\n"
              "print(K.sq, K.x)"),
    "function": ("def f():\n"
                 "    x = 10\n"
                 "    xs = [1, 2]\n"
                 "    sq = [x\n"  # 4
                 "          + 1 for x in xs]\n"
                 "    return sq, x\n"
                 "print(*f())"),
}


@pytest.mark.parametrize("where", list(MULTILINE))
def test_pausing_inside_a_comprehension_never_changes_what_it_computes(kernel, where):
    code = MULTILINE[where]
    line = 3 if where == "cell" else 4
    kernel.start(code, breakpoints=[line])
    seen, snap = run_out(kernel)
    assert seen and all(n == line for n, _ in seen)
    assert snap["status"] == "ok", snap
    assert text(snap) == "[2, 3] 10\n"  # no RuntimeWarning, and x was never None or the outer 10 mid-loop
    untraced(kernel)


@pytest.mark.parametrize("where", list(MULTILINE))
def test_stepping_through_a_comprehension_never_changes_what_it_computes(kernel, where):
    kernel.start(MULTILINE[where], step=True)
    paused(kernel)
    for _ in range(40):
        kernel.debug("step")
        snap = wait(kernel)
        if not snap.get("paused"):
            break
    assert snap["status"] == "ok" and text(snap) == "[2, 3] 10\n", snap


def test_evaluating_in_a_cell_paused_inside_a_comprehension_keeps_its_values(kernel):
    kernel.start(MULTILINE["cell"], breakpoints=[3])
    paused(kernel)
    kernel.debug("continue")  # now inside the comprehension, its x bound
    assert paused(kernel)["line"] == 3
    assert kernel.debug("eval", "xs")["value"] == "[1, 2]"
    assert kernel.debug("eval", "x")["value"] == "10"  # the cell's own x: the comprehension's isn't shown on 3.12
    seen, snap = run_out(kernel)
    assert text(snap) == "[2, 3] 10\n", snap


# ---------- class bodies, decorators, lambdas, match ----------

def test_a_class_body_pauses_with_its_own_frame_and_steps_back_to_the_cell(kernel):
    code = ("class C:\n"
            "    a = 1\n"
            "    b = a + 1\n"  # 3
            "    def m(self):\n"
            "        return self.b\n"
            "c = C().m()")  # 6
    kernel.start(code, breakpoints=[3])
    p = paused(kernel)
    assert stack(p) == [("(cell)", 1), ("C", 3)] and variables(p) == {"a": "1"}
    kernel.debug("next")
    p = paused(kernel)
    assert stack(p) == [("(cell)", 1), ("C", 4)] and variables(p)["b"] == "2"
    kernel.debug("next")
    assert stack(paused(kernel)) == [("(cell)", 6)]
    kernel.debug("continue")
    finished(kernel)


def test_decorated_functions_and_lambdas_show_in_the_stack(kernel):
    code = ("def deco(f):\n"
            "    return f\n"
            "@deco\n"
            "def g(x):\n"
            "    return x + 1\n"  # 5
            "h = lambda y: g(y) * 2\n"
            "r = h(3)")
    kernel.start(code, breakpoints=[5])
    p = paused(kernel)
    assert stack(p) == [("(cell)", 7), ("<lambda>", 6), ("g", 5)] and variables(p) == {"x": "3"}
    kernel.debug("out")
    assert stack(paused(kernel)) == [("(cell)", 7), ("<lambda>", 6)]  # the lambda has no line left: it pauses as it returns
    kernel.debug("continue")
    finished(kernel)


def test_match_steps_case_by_case_and_shows_the_bound_names(kernel):
    code = ("def f(v):\n"
            "    match v:\n"  # 2
            "        case [a, b]:\n"
            "            return a + b\n"
            "        case {'k': k}:\n"  # 5
            "            return k\n"  # 6
            "        case _:\n"
            "            return 0\n"
            "r = f({'k': 5})")
    kernel.start(code, breakpoints=[2])
    lines = [paused(kernel)["line"]]
    for _ in range(3):
        kernel.debug("next")
        p = paused(kernel)
        lines.append(p["line"])
    assert lines == [2, 3, 5, 6] and variables(p)["k"] == "5"
    kernel.debug("continue")
    finished(kernel)


# ---------- try/finally, with, exceptions, generators ----------

def test_a_finally_that_breaks_out_of_a_return_steps_in_order(kernel):
    code = ("def f():\n"
            "    for i in range(3):\n"
            "        try:\n"
            "            if i == 1:\n"
            "                return i\n"  # 5
            "        finally:\n"
            "            if i == 1:\n"  # 7
            "                break\n"  # 8
            "    return 'broke'\n"  # 9
            "r = f()\n"
            "print(r)")  # 11
    kernel.start(code, breakpoints=[5])
    lines = [paused(kernel)["line"]]
    for _ in range(4):
        kernel.debug("next")
        lines.append(paused(kernel)["line"])
    assert lines == [5, 7, 8, 9, 11]
    kernel.debug("continue")
    assert text(finished(kernel)) == "broke\n"


def test_a_with_block_steps_into_the_cells_exit(kernel):
    code = ("class W:\n"
            "    def __enter__(self):\n"
            "        return self\n"
            "    def __exit__(self, *exc):\n"
            "        return True\n"  # 5
            "with W() as w:\n"
            "    1 / 0\n"  # 7
            "after = 1")  # 8
    kernel.start(code, breakpoints=[7])
    assert paused(kernel)["line"] == 7
    stacks = []
    for _ in range(3):
        kernel.debug("step")
        stacks.append(stack(paused(kernel)))
    assert stacks == [[("(cell)", 6)], [("(cell)", 6), ("__exit__", 5)], [("(cell)", 8)]]
    kernel.debug("continue")
    finished(kernel)


def test_stepping_from_a_raise_goes_to_the_except_that_catches_it(kernel):
    code = ("def g():\n"
            "    raise ValueError('v')\n"  # 2
            "def f():\n"
            "    try:\n"
            "        g()\n"
            "    finally:\n"
            "        cleanup = 1\n"  # 7
            "try:\n"
            "    f()\n"
            "except ValueError as e:\n"  # 10
            "    caught = str(e)\n"  # 11
            "z = 1")
    kernel.start(code, breakpoints=[2])
    assert stack(paused(kernel)) == [("(cell)", 9), ("f", 5), ("g", 2)]
    stacks = []
    for _ in range(3):
        kernel.debug("step")
        stacks.append(stack(paused(kernel)))
    assert stacks == [[("(cell)", 9), ("f", 7)], [("(cell)", 10)], [("(cell)", 11)]]
    kernel.debug("continue")
    finished(kernel)


def test_a_generators_throw_and_close_pause_in_its_handlers(kernel):
    code = ("def gen():\n"
            "    try:\n"
            "        yield 1\n"
            "        yield 2\n"
            "    except KeyError:\n"
            "        yield 'k'\n"  # 6
            "    finally:\n"
            "        closing = 1\n"  # 8
            "g = gen()\n"
            "a = next(g)\n"
            "b = g.throw(KeyError)\n"  # 11
            "g.close()\n"  # 12
            "end = b")
    kernel.start(code, breakpoints=[6, 8])
    seen, snap = run_out(kernel)
    assert seen == [(6, [("(cell)", 11), ("gen", 6)]), (8, [("(cell)", 12), ("gen", 8)])]
    assert snap["status"] == "ok"
    untraced(kernel)


# ---------- recursion errors, breakpoint() and the cell's own trace hooks ----------

LOST = "The debugger lost track of this cell partway"


def test_a_recursion_error_says_the_debugger_stopped_instead_of_skipping_breakpoints_silently(kernel):
    code = ("def down(n):\n"
            "    return down(n + 1)\n"
            "try:\n"
            "    down(0)\n"
            "except RecursionError:\n"
            "    hit = 1\n"
            "after = 2")  # 7: Python turned the trace hook off at the RecursionError
    kernel.start(code, breakpoints=[7])
    snap = finished(kernel)
    assert LOST in text(snap) and "RecursionError" in text(snap)
    untraced(kernel)
    kernel.start("a = 1\nb = 2", breakpoints=[2])  # the next cell debugs as usual
    assert paused(kernel)["line"] == 2
    kernel.debug("continue")
    assert LOST not in text(finished(kernel))


@pytest.mark.parametrize("hook", ["sys.settrace(None)", "sys.settrace(lambda *a: None)"], ids=["off", "own"])
def test_a_cell_that_replaces_the_trace_hook_is_told_why_it_stopped_pausing(kernel, hook):
    kernel.start(f"import sys\n{hook}\ndef f():\n    return 1\nx = f()", breakpoints=[4])
    seen, snap = run_out(kernel)
    assert seen == [] and snap["status"] == "ok"  # the line in f, called after, never paused
    assert LOST in text(snap) and "sys.settrace" in text(snap)
    untraced(kernel)


def test_a_cell_that_sets_and_restores_its_own_trace_hook_keeps_pausing(kernel):
    code = ("import sys\n"
            "old = sys.gettrace()\n"
            "sys.settrace(lambda *a: None)\n"
            "x = 1\n"  # 4
            "sys.settrace(old)\n"
            "y = 2")  # 6
    kernel.start(code, breakpoints=[4, 6])
    seen, snap = run_out(kernel)
    assert [n for n, _ in seen] == [4, 6] and snap["status"] == "ok"
    assert LOST not in text(snap)
    untraced(kernel)


def test_a_profiler_the_cell_sets_does_not_stop_the_debugger(kernel):
    code = ("import sys\n"
            "calls = []\n"
            "sys.setprofile(lambda f, e, a: None)\n"
            "x = 1\n"  # 4
            "sys.setprofile(None)\n"
            "y = 2")  # 6
    kernel.start(code, breakpoints=[4, 6])
    seen, snap = run_out(kernel)
    assert [n for n, _ in seen] == [4, 6] and snap["status"] == "ok"
    kernel.start("import sys\nprint(sys.getprofile() is None)")
    assert text(finished(kernel)).strip() == "True"


def test_breakpoint_in_the_cell_pauses_there_under_the_debugger(kernel):
    code = ("def f(n):\n"
            "    m = n + 1\n"
            "    breakpoint()\n"  # 3
            "    return m\n"  # 4
            "x = f(1)\n"
            "y = 2")  # 6
    kernel.start(code, breakpoints=[6])
    p = paused(kernel)
    assert p["line"] == 3 and p["reason"] == "breakpoint" and stack(p) == [("(cell)", 5), ("f", 3)]
    assert variables(p) == {"m": "2", "n": "1"} and "skipped" not in p
    assert kernel.debug("eval", "m * 10")["value"] == "20"
    kernel.debug("next")
    assert paused(kernel)["line"] == 4
    kernel.debug("continue")
    assert paused(kernel)["line"] == 6
    kernel.debug("continue")
    snap = finished(kernel)
    assert "Pdb" not in text(snap)
    untraced(kernel)


def test_breakpoint_in_an_expression_evaluated_while_paused_doesnt_pause_again(kernel):
    # Evaluate runs on the paused cell's thread: a breakpoint() in it must answer, not nest a pause.
    code = ("def f():\n"
            "    breakpoint()\n"
            "    return 7\n"
            "y = 1")  # 4
    kernel.start(code, breakpoints=[4])
    assert paused(kernel)["line"] == 4
    started = time.monotonic()
    assert kernel.debug("eval", "f()")["value"] == "7"
    assert time.monotonic() - started < 5
    p = paused(kernel)
    assert p["line"] == 4 and len(p["stack"]) == 1
    kernel.debug("continue")
    assert finished(kernel)["status"] == "ok"
    untraced(kernel)


def test_breakpoint_in_a_cell_without_the_debugger_carries_on_with_a_note(kernel):
    kernel.start("x = 1\nbreakpoint()\nprint(x + 1)")
    snap = finished(kernel)
    assert "breakpoint() pauses only when the cell runs with the debugger" in text(snap) and text(snap).endswith("2\n") and "Pdb" not in text(snap)
    untraced(kernel)


def test_breakpoint_in_a_capture_cell_carries_on_with_a_note(kernel):
    # %%capture runs the cell under a run of its own, which looked like a watch's: breakpoint() went to pdb.
    kernel.start("%%capture c\nx = 1\nbreakpoint()\nprint(x + 1)")
    snap = finished(kernel)
    assert snap["status"] == "ok" and "Pdb" not in text(snap), snap
    kernel.start("print(c.stdout)")
    shown = text(finished(kernel))
    assert "breakpoint() pauses only when the cell runs with the debugger" in shown and "2\n" in shown
    untraced(kernel)


def test_breakpoint_in_a_capture_cell_pauses_there_under_the_debugger(kernel):
    kernel.start("%%capture c\nx = 1\nbreakpoint()\ny = x + 1\nprint(y)", breakpoints=[5])
    p = paused(kernel)
    assert p["line"] == 3 and p["reason"] == "breakpoint", p
    kernel.debug("continue")
    assert paused(kernel)["line"] == 5
    kernel.debug("continue")
    snap = finished(kernel)
    assert snap["status"] == "ok" and "Pdb" not in text(snap)
    untraced(kernel)


def test_a_debugged_cell_puts_back_the_trace_hook_its_thread_had(kernel):
    # A coverage or profiling tool's hook, set for new threads, is the cell thread's again after a
    # debugged cell (a watch after the cell sees it), not None.
    if not isinstance(kernel, Kernel):
        pytest.skip("the hook is set in this process; the process kernel's cell threads are its own")

    def tool(frame, event, arg):
        return None

    threading.settrace(tool)
    try:
        kernel.start("x = 1\ny = 2", breakpoints=[2], watches=["__import__('sys').gettrace().__name__"])
        assert paused(kernel)["line"] == 2
        kernel.debug("continue")
        snap = finished(kernel)
    finally:
        threading.settrace(None)  # type: ignore[arg-type]
    assert snap["watches"][0]["value"] == "'tool'", snap["watches"]


def test_pdb_in_a_kernel_process_ends_the_cell_instead_of_waiting_for_input(kernel):
    if isinstance(kernel, Kernel):
        pytest.skip("pytest's own stdin stands in for the engine's here; the engine's own is checked below")
    kernel.start("import pdb\nx = 1\npdb.set_trace()\ny = 2", breakpoints=[4])
    snap = wait(kernel)
    assert not snap.get("paused") and snap["status"] in ("ok", "error"), snap
    untraced(kernel)


ENGINE_STDIN = textwrap.dedent("""
    import sys
    from fabric_desktop import server
    report = sys.argv[1]
    def serve(service, stdin, stdout):
        first = stdin.readline()
        with open(report, "w", encoding="utf-8") as f:
            import os
            f.write(repr((sys.stdin.read(), sys.__stdin__.read(), os.read(0, 100), first)))
    server.serve = serve
    server.Service = lambda: type("S", (), {"policy_record": lambda self: None})()
    server._start_integration_http = lambda service: None
    server._refresh_integration_launcher = lambda: None
    server.main()
""")


def test_the_engine_keeps_its_request_stream_from_cells(tmp_path):
    """A cell's pdb (or sys.stdin.read) in the engine's own kernel reads nothing, never the app's requests."""
    report = tmp_path / "report.txt"
    env = {**os.environ, "PYTHONPATH": str(SIDECAR), "LOCALAPPDATA": str(tmp_path / "la"), "HOME": str(tmp_path),
           "FABRIC_DESKTOP_HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "APPDATA": str(tmp_path)}
    done = subprocess.run([sys.executable, "-c", ENGINE_STDIN, str(report)], input='{"id": 1}\n{"id": 2}\n',
                          capture_output=True, text=True, env=env, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    # fd 0 and sys.__stdin__ too: native code or os.read in a cell can't take the app's requests either
    assert report.read_text(encoding="utf-8") == repr(("", "", b"", '{"id": 1}\n'))


# ---------- exec, eval, del ----------

def test_cell_functions_called_from_exec_and_eval_strings_pause_with_the_cells_stack(kernel):
    code = ("def f(n):\n"
            "    return n + 1\n"  # 2
            "r = eval('f(1)')\n"  # 3
            "exec('s = f(2)\\nt = 3')\n"  # 4
            "u = 4")  # 5
    kernel.start(code, breakpoints=[2])
    p = paused(kernel)
    assert stack(p) == [("(cell)", 3), ("f", 2)] and variables(p) == {"n": "1"}
    kernel.debug("step")
    assert stack(paused(kernel)) == [("(cell)", 4)]
    kernel.debug("step")
    assert stack(paused(kernel)) == [("(cell)", 4), ("f", 2)]
    kernel.debug("step")
    p = paused(kernel)
    assert stack(p) == [("(cell)", 5)] and {"r", "s", "t"} <= set(variables(p))
    kernel.debug("continue")
    finished(kernel)


def test_deleted_names_are_gone_from_the_paused_frame(kernel):
    code = ("a = 1\n"
            "def f():\n"
            "    p = 1\n"
            "    q = 2\n"
            "    del p\n"
            "    return q\n"  # 6
            "del a\n"
            "r = f()")  # 8
    kernel.start(code, breakpoints=[6, 8])
    p = paused(kernel)
    assert p["line"] == 8 and "a" not in variables(p)
    kernel.debug("continue")
    p = paused(kernel)
    assert variables(p) == {"q": "2"}
    assert kernel.debug("eval", "p")["error"].startswith("NameError")
    kernel.debug("continue")
    finished(kernel)


# ---------- breakpoints on lines without code ----------

@pytest.mark.parametrize("code, line, stops", [
    ("x = 1\n\ny = 2\nz = 3", 2, 3),  # a blank line
    ("x = 1\n# note\ny = 2", 2, 3),  # a comment
    ("x = 0\nif x:\n    y = 2\nelse:\n    y = 3\nz = 3", 4, 5),  # else:
    ("def f():\n    '''doc'''\n\n    return 1\nr = f()", 3, 4),  # a blank line in a function
], ids=["blank", "comment", "else", "infunc"])
def test_a_breakpoint_on_a_line_without_code_pauses_at_the_next_line_with_code(kernel, code, line, stops):
    kernel.start(code, breakpoints=[line])
    p = paused(kernel)
    assert p["line"] == stops and p["reason"] == "breakpoint"
    kernel.debug("continue")
    finished(kernel)


def test_a_breakpoint_after_the_last_line_with_code_is_named(kernel):
    kernel.start("x = 1\ny = 2\n# the end", breakpoints=[3])
    snap = finished(kernel)
    assert "breakpoint on line 3" in text(snap).lower() and "no code" in text(snap)


# ---------- long lines and non-ASCII names ----------

def test_a_very_long_line_pauses_and_steps_and_shows_its_values_short(kernel):
    long = "v = [" + ", ".join(str(i) for i in range(30_000)) + "]"
    kernel.start(long + "\nw = len(v)\nq = 1", breakpoints=[1])
    p = paused(kernel)
    assert p["line"] == 1 and len(repr(p)) < 5000
    kernel.debug("next")
    p = paused(kernel)
    assert p["line"] == 2 and len(variables(p)["v"]) <= 120
    kernel.debug("continue")
    finished(kernel)


def test_non_ascii_names_show_in_the_stack_variables_and_evaluations(kernel):
    code = ("π = 3.0\n"
            "def größe(länge):\n"
            "    breite = länge * 2\n"
            "    return breite\n"  # 4
            "ergebnis = größe(π)\n"
            "fertig = 'é'")  # 6
    kernel.start(code, breakpoints=[4, 6])
    p = paused(kernel)
    assert stack(p) == [("(cell)", 5), ("größe", 4)] and variables(p) == {"breite": "6.0", "länge": "3.0"}
    assert kernel.debug("eval", "breite + länge")["value"] == "9.0"
    kernel.debug("continue")
    p = paused(kernel)
    assert variables(p)["ergebnis"] == "6.0" and variables(p)["π"] == "3.0"
    kernel.debug("continue")
    finished(kernel)
