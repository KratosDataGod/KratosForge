"""Notebook debugger robustness (#2362, round 2): malformed debugger input is refused by name and
never takes the kernel down; a kernel that dies or a cell that hangs while paused ends cleanly in
time with no process left behind; stepping through code that raises, and through nested
functions, always reports the right stack."""

from __future__ import annotations

import io
import json
import time

import pytest

from fabric_desktop import debugger, kernel_host
from fabric_desktop.kernel_proc import KernelGone, ProcessKernel
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


def stack(p):
    return [(f["function"], f["line"]) for f in p["stack"]]


# ---------- 1. malformed input is refused by name ----------

@pytest.mark.parametrize("lines", [
    "3", 3, {"line": 3}, [0], [-1], ["3"], [3.0], [True], [None], [[3]], list(range(debugger.MAX_BREAKPOINTS + 1)),
])
def test_bad_breakpoints_are_refused_by_name(lines):
    with pytest.raises(ValueError, match="[Bb]reakpoint"):
        debugger.check_breakpoints(lines)


def test_good_breakpoints_are_cleaned():
    assert debugger.check_breakpoints(None) == []
    assert debugger.check_breakpoints([5, 1, 5, 10**9]) == [1, 5, 10**9]


@pytest.mark.parametrize("action, expression, message", [
    (None, None, "action is text"),
    (3, None, "action is text"),
    (["step"], None, "action is text"),
    ({"a": 1}, None, "action is text"),
    ("jump", None, "Unknown debugger action"),
    ("", None, "Unknown debugger action"),
    ("eval", None, "Type an expression"),
    ("eval", "   ", "Type an expression"),
    ("eval", 42, "Type an expression"),
    ("eval", ["x"], "Type an expression"),
    ("eval", "x" * (debugger.MAX_EXPRESSION + 1), "at most"),
])
def test_bad_commands_are_refused_by_name_and_the_pause_stays(kernel, action, expression, message):
    kernel.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    with pytest.raises((ValueError, RuntimeError), match=message):
        kernel.debug(action, expression)
    assert wait(kernel)["paused"]["line"] == 2  # still paused, still answering
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


@pytest.mark.parametrize("expression", ["1 +", "x = 1", "def f(): pass", "(" * 500, "\x00"])
def test_expressions_that_dont_compile_are_refused_and_the_pause_stays(kernel, expression):
    kernel.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    with pytest.raises((SyntaxError, ValueError, RuntimeError)):
        kernel.debug("eval", expression)
    assert kernel.debug("eval", "a + 1")["value"] == "2"
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_commands_when_nothing_is_paused_are_refused(kernel):
    with pytest.raises(RuntimeError):
        kernel.debug("continue")
    kernel.start("x = 1")
    wait(kernel)
    for action in ("continue", "step", "eval", 7):
        with pytest.raises(RuntimeError):
            kernel.debug(action, "x")


class _Recorder:
    def __init__(self):
        self.sent = []
        self.answered = []

    def send(self, message):
        self.sent.append(message)

    def answer(self, message):
        self.answered.append(message)


class _Host:
    def __init__(self):
        self.handled = []

    def handle(self, message):
        self.handled.append(message)


@pytest.mark.parametrize("line, why", [
    ('{"id": "p1", "op": "debug", "args": {"action": "st', "not JSON"),
    ("not json at all", "not JSON"),
    ("[" * 100_000 + "]" * 100_000, "nested too deep"),
    ('[1, 2, 3]', "a JSON list"),
    ('"step"', "a JSON str"),
    ("42", "a JSON int"),
    ("null", "a JSON NoneType"),
    ('{"result": 1}', "an answer without an id"),
    ('{"id": 7, "result": 1}', "an answer without an id"),
    ('{"op": 5, "id": "p2"}', "op isn't text"),
    ('{"op": "debug", "id": "p3", "args": [1, 2]}', "args aren't an object"),
])
def test_the_kernel_process_skips_malformed_lines_by_name(line, why, capsys):
    host, channel = _Host(), _Recorder()
    assert why in kernel_host.dispatch(host, channel, line)
    assert host.handled == [] and channel.answered == []
    assert why in capsys.readouterr().err


def test_a_malformed_request_with_an_id_gets_its_refusal_as_the_answer():
    host, channel = _Host(), _Recorder()
    kernel_host.dispatch(host, channel, '{"op": "debug", "id": "p3", "args": "step"}')
    assert channel.sent == [{"id": "p3", "error": {"type": "ValueError",
                                                   "message": "Malformed request: a request whose op isn't text or whose args aren't an object"}}]


def test_an_oversized_line_is_skipped_without_reading_it_as_json(monkeypatch):
    monkeypatch.setattr(kernel_host, "MAX_LINE", 100)
    host, channel = _Host(), _Recorder()
    assert "longer than" in kernel_host.dispatch(host, channel, json.dumps({"id": "p1", "result": "x" * 200}))
    assert channel.answered == []


def test_good_lines_still_go_through():
    host, channel = _Host(), _Recorder()
    assert kernel_host.dispatch(host, channel, "   ") is None
    assert kernel_host.dispatch(host, channel, '{"id": "t1", "result": "tok"}') is None
    assert channel.answered == [{"id": "t1", "result": "tok"}]
    assert kernel_host.dispatch(host, channel, '{"id": "p1", "op": "ping", "args": {}}') is None
    deadline = time.monotonic() + 5
    while not host.handled and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host.handled == [{"id": "p1", "op": "ping", "args": {}}]


def test_garbage_sent_to_a_paused_kernel_process_leaves_it_paused_and_answering():
    k = ProcessKernel()
    try:
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        proc = k._proc
        for line in ('{"id": "p1", "op": "debu', "[1,2]", "[" * 50_000, '{"result": 3}', '{"op": 1}'):
            proc.stdin.write(line + "\n")
        proc.stdin.flush()
        assert k.debug("eval", "a + 41")["value"] == "42"
        k.debug("continue")
        assert wait(k)["status"] == "ok"
        assert proc.poll() is None  # the same process, never restarted
    finally:
        k.close()


# ---------- 3. a kernel that dies, or a cell that hangs, while paused ----------

def test_a_kernel_process_killed_while_paused_ends_the_session_in_time():
    k = ProcessKernel()
    try:
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        proc = k._proc
        proc.kill()
        proc.wait(10)
        started = time.monotonic()
        with pytest.raises(KernelGone):
            k.debug("continue")
        snap = k.poll(-1, 0.3)
        assert snap["status"] != "running" and not snap.get("paused")
        assert time.monotonic() - started < 10
        # A new cell starts a fresh process, which debugs as before.
        k.start("x = 1\ny = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        assert k._proc is not proc
        k.debug("continue")
        assert wait(k)["status"] == "ok"
    finally:
        k.close()
    assert k._proc is None or k._proc.poll() is not None


def test_closing_a_kernel_process_while_paused_leaves_no_process_behind():
    k = ProcessKernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    proc = k._proc
    started = time.monotonic()
    k.close()
    assert proc.wait(15) is not None
    assert time.monotonic() - started < 15


def test_a_cell_that_hangs_after_continue_is_stopped_in_time(kernel):
    kernel.start("a = 1\nimport time\nwhile True:\n    time.sleep(0.05)", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    time.sleep(0.5)
    assert kernel.poll(-1, 0.1)["status"] == "running"
    started = time.monotonic()
    assert kernel.interrupt()["interrupted"] is True
    snap = wait(kernel, timeout=10)
    assert snap["status"] == "cancelled"
    assert time.monotonic() - started < 10
    kernel.start("print(a)")
    assert wait(kernel)["status"] == "ok"


# ---------- 4. code that raises, and nested functions ----------

RAISES = """def inner(n):
    m = n - 1
    return 10 // m
def outer(n):
    v = inner(n)
    return v
total = outer(1)
after = 1"""


def test_stepping_into_code_that_raises_reports_each_frame_then_the_error(kernel):
    kernel.start(RAISES, step=True)
    assert wait(kernel)["paused"]["line"] == 1
    seen = []
    for _ in range(12):
        kernel.debug("step")
        snap = wait(kernel)
        if not snap.get("paused"):
            break
        seen.append(stack(snap["paused"]))
    assert snap["status"] == "error"
    assert seen[-1] == [("(cell)", 7), ("outer", 5), ("inner", 3)]  # the last pause is on the raising line
    assert [("(cell)", 7), ("outer", 5), ("inner", 2)] in seen
    assert [("(cell)", 7), ("outer", 5)] in seen
    error = next(o for o in snap["outputs"] if o.get("type") == "error")
    assert error["ename"] == "ZeroDivisionError"
    # Nothing stays paused, and the next cell runs untraced.
    kernel.start("import sys\nprint(sys.gettrace() is None, 'after' in dir())")
    out = wait(kernel)
    assert out["status"] == "ok" and out["outputs"][-1]["text"].strip() == "True False"


def test_an_exception_caught_inside_the_cell_keeps_stepping_with_the_right_stack(kernel):
    code = """def risky():
    raise ValueError("no")
def safe():
    try:
        risky()
    except ValueError:
        r = "caught"
    return r
x = safe()
y = x"""
    kernel.start(code, breakpoints=[7])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("safe", 7)]
    kernel.debug("next")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("safe", 8)]
    kernel.debug("out")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 10)]
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_nested_and_recursive_functions_report_every_level(kernel):
    code = """def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)
def wrap():
    def helper(k):
        return fact(k)
    return helper(3)
v = wrap()"""
    kernel.start(code, breakpoints=[3])
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("wrap", 8), ("helper", 7), ("fact", 4), ("fact", 4), ("fact", 3)]
    assert {r["name"]: r["value"] for r in p["variables"]}["n"] == "1"
    kernel.debug("out")
    p = wait(kernel)["paused"]
    assert stack(p) == [("(cell)", 9), ("wrap", 8), ("helper", 7), ("fact", 4), ("fact", 4)]
    kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok"


def test_a_generator_and_a_comprehension_in_the_cell_step_with_the_right_stack(kernel):
    code = """def gen():
    for i in range(2):
        yield i * 10
vals = [v + 1 for v in gen()]
done = vals"""
    kernel.start(code, breakpoints=[3])
    p = wait(kernel)["paused"]
    names = [f for f, _ in stack(p)]
    assert names[0] == "(cell)" and names[-1] == "gen" and p["line"] == 3
    kernel.debug("continue")
    p = wait(kernel)["paused"]  # the generator's second item
    assert p["line"] == 3 and {r["name"]: r["value"] for r in p["variables"]}["i"] == "1"
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_the_notice_of_a_skipped_line_never_holds_its_text(capsys):
    kernel_host.dispatch(_Host(), _Recorder(), "secret-cell-code " * 600)
    err = capsys.readouterr().err
    assert "secret-cell-code" not in err and "10199 characters" in err and len(err) < 200
    kernel_host.dispatch(_Host(), _Recorder(), json.dumps({"op": "debug", "args": ["cell_value = 'private-cell-text'"]}))
    err = capsys.readouterr().err
    assert "private-cell-text" not in err and "op 'debug'" in err


def test_channel_answer_is_never_given_a_non_message():
    # The main loop's old path: an answer without an id raised KeyError there and ended the process.
    ch = kernel_host.Channel(io.StringIO(), io.StringIO())
    with pytest.raises(KeyError):
        ch.answer({"result": 1})
