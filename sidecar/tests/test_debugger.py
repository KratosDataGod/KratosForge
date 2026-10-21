"""Breakpoints and stepping in a notebook cell (Prove stage 5, item 44)."""

from __future__ import annotations

import time

import pytest

from fabric_desktop import app_settings
from fabric_desktop.debugger import check_breakpoints
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

CODE = """x = 1
def double(n):
    y = n * 2
    return y
z = double(x)
password = "s3cr3t-value"
w = z + 1
w"""


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    k = Kernel() if request.param == "engine" else ProcessKernel()
    yield k
    if request.param == "process":
        k.close()


def paused(k, timeout=20):
    """The next snapshot that is paused or finished."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def test_a_breakpoint_pauses_in_a_function_and_shows_that_frame(kernel):
    kernel.start(CODE, watches=["x * 100"], breakpoints=[3])
    snap = paused(kernel)
    p = snap["paused"]
    assert snap["status"] == "running"
    assert (p["line"], p["reason"]) == (3, "breakpoint")
    assert p["stack"] == [{"function": "(cell)", "line": 5}, {"function": "double", "line": 3}]
    assert [(v["name"], v["value"]) for v in p["variables"]] == [("n", "1")]
    assert p["watches"][0]["value"] == "100"  # watches are evaluated in the paused frame

    assert kernel.debug("eval", "n + 10")["value"] == "11"
    assert kernel.debug("eval", "1/0")["error"] == "ZeroDivisionError: division by zero"
    assert [e["expr"] for e in paused(kernel)["paused"]["evals"]] == ["n + 10", "1/0"]

    kernel.debug("out")
    p = paused(kernel)["paused"]
    assert (p["line"], p["stack"]) == (6, [{"function": "(cell)", "line": 6}])
    kernel.debug("next")
    p = paused(kernel)["paused"]
    assert p["line"] == 7
    secret = next(v for v in p["variables"] if v["name"] == "password")
    assert secret["value"] == "hidden"
    assert "s3cr3t-value" not in str(p)
    assert "double" not in [v["name"] for v in p["variables"]]  # functions and modules aren't variables

    kernel.debug("continue")
    snap = paused(kernel)
    assert snap["status"] == "ok" and snap.get("paused") is None
    assert snap["outputs"][-1]["text"] == "3"


def test_step_pauses_at_the_first_line_and_steps_into_calls(kernel):
    kernel.start(CODE, step=True)
    assert paused(kernel)["paused"]["line"] == 1
    for line in (2, 5, 3):
        kernel.debug("step")
        assert paused(kernel)["paused"]["line"] == line
    kernel.debug("next")
    assert paused(kernel)["paused"]["line"] == 4
    kernel.debug("continue")
    assert paused(kernel)["status"] == "ok"


def test_cancel_while_paused_keeps_variables(kernel):
    kernel.start("a = 1\nb = 2\nc = 3", breakpoints=[2])
    assert paused(kernel)["paused"]["line"] == 2
    assert kernel.interrupt()["interrupted"] is True
    snap = paused(kernel)
    assert snap["status"] == "cancelled"
    assert "Variables are kept" in snap["outputs"][-1]["text"]
    assert {v["name"] for v in kernel.variables()} >= {"a"}
    kernel.start("print(a)")
    assert paused(kernel)["status"] == "ok"


def test_cancel_ends_a_cell_stuck_in_an_evaluate(kernel):
    import threading
    kernel.start("a = 1\nb = 2", breakpoints=[2])
    assert paused(kernel)["paused"]["line"] == 2
    answer = {}

    def evaluate():
        try:
            answer["r"] = kernel.debug("eval", "sum(1 for _ in iter(int, 1))")
        except Exception as exc:  # noqa: BLE001 - the process kernel may be closed before it answers
            answer["error"] = exc
    t = threading.Thread(target=evaluate, daemon=True)
    t.start()
    time.sleep(1.0)  # the cell's thread is now inside the expression
    assert kernel.interrupt()["interrupted"] is True
    deadline = time.monotonic() + 10
    while (snap := kernel.poll(-1, 0.3))["status"] == "running" and time.monotonic() < deadline:
        pass
    assert snap["status"] == "cancelled" and snap.get("paused") is None
    assert {v["name"] for v in kernel.variables()} >= {"a"}


def test_a_cell_without_breakpoints_is_not_traced(kernel):
    snap = kernel.start("import sys\nprint(sys.gettrace() is None)")
    snap = paused(kernel)
    assert "paused" not in snap
    assert snap["outputs"][0]["text"].strip() == "True"
    with pytest.raises(RuntimeError, match="No cell is running under the debugger"):
        kernel.debug("continue")


@pytest.mark.parametrize("code, line", [
    ("%%pyspark\na = 1\nb = 2\n", 3),
    ("\n%%pyspark\na = 1\nb = 2\n", 4),  # a blank line above the header
    ("%%time\na = 1\nb = 2\n", 3),
    ("%%capture out\na = 1\nb = 2\n", 3),
    ("a = 1\n%env FD_DEBUG_TEST=1\nb = 2\n", 3),  # a line magic on the line above
])
def test_magic_cells_keep_their_line_numbers(kernel, code, line):
    kernel.start(code, breakpoints=[line])
    snap = paused(kernel)
    assert snap["paused"]["line"] == line, snap
    assert {v["name"] for v in snap["paused"]["variables"]} >= {"a"}
    kernel.debug("continue")
    assert paused(kernel)["status"] == "ok"


def test_library_code_and_other_cells_never_stop():
    k = Kernel()
    k.run("def helper():\n    v = 1\n    return v")  # defined by an earlier cell: not this cell's code
    k.start("r = helper()\ns = sorted([3, 1, 2])\nt = 1", breakpoints=[2])
    p = paused(k)["paused"]
    assert p["line"] == 2
    k.debug("step")  # sorted() is a library call: stepping goes to the next line of the cell
    assert paused(k)["paused"]["line"] == 3
    k.debug("continue")
    assert paused(k)["status"] == "ok"


def test_debugger_commands_are_checked():
    k = Kernel()
    k.start("a = 1\nb = 2", breakpoints=[2])
    paused(k)
    with pytest.raises(ValueError, match="Unknown debugger action"):
        k.debug("jump")
    with pytest.raises(ValueError, match="Type an expression"):
        k.debug("eval", " ")
    with pytest.raises(SyntaxError):
        k.debug("eval", "a +")
    k.debug("continue")
    assert paused(k)["status"] == "ok"
    assert check_breakpoints(None) == []
    assert check_breakpoints([3, 1, 3]) == [1, 3]
    for bad in ([0], [True], ["2"], "3", list(range(1, 200))):
        with pytest.raises(ValueError):
            check_breakpoints(bad)


def test_a_debugged_cell_has_no_time_limit():
    k = ProcessKernel()
    k.time_limit = 1
    try:
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert paused(k)["paused"]["line"] == 2
        time.sleep(2.5)  # past the limit while paused
        assert k.poll()["status"] == "running"
        k.debug("continue")
        assert paused(k)["status"] == "ok"
    finally:
        k.close()


def test_the_engine_passes_breakpoints_and_commands(tmp_path, monkeypatch):
    import json
    from fabric_desktop.server import handle
    from fabric_desktop.service import Service
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    s = Service(root=tmp_path / "app")
    send = lambda method, **params: handle(s, json.dumps({"id": 1, "method": method, "params": params}))  # noqa: E731
    reply = send("start_cell", code="a = 1\nb = 2", breakpoints=[2])
    assert "error" not in reply, reply
    assert paused(s.kernel)["paused"]["line"] == 2
    assert send("debug_cell", action="eval", expression="a")["result"]["value"] == "1"
    send("debug_cell", action="continue")
    assert paused(s.kernel)["status"] == "ok"
    assert "error" in send("start_cell", code="1", breakpoints=["x"])
