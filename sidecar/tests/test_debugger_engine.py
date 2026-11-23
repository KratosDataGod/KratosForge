"""Notebook debugger (#2362 hardening, round 6): where a paused cell meets the rest of the engine.
Values whose repr, len or hash fail, hang or are slow never hold up or end a pause, nor the Variables
pane; another notebook in a shared session never sees, resumes or stops a paused cell; leaving,
restarting or renaming a guest while it is paused ends or keeps the pause as it says; garbage
collection, widgets, the memory limit, a thread printing, a restart or a close racing a command, and
a second start all leave the kernel answering."""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from fabric_desktop import http_session, kernel_proc
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.kernel_sessions import KernelSessions
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


def in_time(fn, seconds):
    """``fn()``'s answer, or an AssertionError when it takes longer than ``seconds``."""
    out: dict = {}

    def go():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            out["error"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"no answer in {seconds} seconds"
    if "error" in out:
        raise out["error"]
    return out["value"]


def row(paused, name):
    return next(v for v in paused["variables"] if v["name"] == name)


# Classes for values that misbehave when the debugger or the Variables pane reads them.
ODD = ("import time\n"
       "class Spin:\n"
       "    def __repr__(self):\n"
       "        while True: pass\n"
       "class Slow:\n"
       "    def __repr__(self):\n"
       "        time.sleep(1); return 'slow'\n"
       "class NoHash(str):\n"
       "    def __hash__(self): raise RuntimeError('no hash')\n"
       "class SpinLen(list):\n"
       "    def __len__(self):\n"
       "        while True: pass\n"
       "class SpinShape:\n"
       "    @property\n"
       "    def shape(self):\n"
       "        while True: pass\n"
       "class NotText:\n"
       "    def __repr__(self): return 42\n"
       "class Raises:\n"
       "    def __getattr__(self, name): raise ValueError(name)\n"
       "    def __repr__(self): raise ValueError('no repr')\n")


# ---------- 1. values that are hard to describe ----------

def test_a_text_whose_hash_fails_doesnt_end_the_paused_cell(kernel):
    assert kernel.run(ODD)["status"] == "ok"
    kernel.start("h = NoHash('x')\nx = 1\ny = 2", breakpoints=[2])
    p = wait(kernel)["paused"]
    assert p["line"] == 2 and row(p, "h")["type"] == "NoHash"
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"
    names = {v["name"] for v in in_time(kernel.variables, 10)}
    assert {"h", "x", "y"} <= names


@pytest.mark.parametrize("make", ["Spin()", "SpinLen([1])", "SpinShape()"], ids=["repr", "len", "shape"])
def test_a_value_that_never_finishes_describing_doesnt_hold_up_the_pause(kernel, make):
    assert kernel.run(ODD)["status"] == "ok"
    started = time.monotonic()
    kernel.start(f"v = {make}\nx = 1\ny = 2\nz = 3", breakpoints=[3])
    p = wait(kernel, 15)["paused"]
    assert p and p["line"] == 3 and time.monotonic() - started < 10
    assert row(p, "x")["value"] == "1"
    kernel.debug("next")
    assert wait(kernel, 15)["paused"]["line"] == 4  # each pause stays quick
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"
    rows = in_time(kernel.variables, 15)  # the Variables pane answers too
    assert {"v", "x", "y"} <= {r["name"] for r in rows}


def test_many_slow_values_dont_add_up_to_a_slow_pause(kernel):
    assert kernel.run(ODD)["status"] == "ok"
    started = time.monotonic()
    kernel.start("vals = None\n" + "".join(f"s{i} = Slow()\n" for i in range(12)) + "x = 1", breakpoints=[14])
    p = wait(kernel, 20)["paused"]
    assert p["line"] == 14 and time.monotonic() - started < 8  # 12 one-second reprs, read for a few seconds at most
    assert len([v for v in p["variables"] if v["name"].startswith("s")]) == 12  # every one still listed
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_odd_values_show_as_a_question_mark(kernel):
    assert kernel.run(ODD)["status"] == "ok"
    kernel.start("a = NotText()\nb = Raises()\nc = 'x' * 5_000_000\nd = 1", breakpoints=[4])
    p = wait(kernel)["paused"]
    assert row(p, "a")["value"] == "?" and row(p, "b")["value"] == "?"
    assert row(p, "c")["size"] == "5000000" and len(row(p, "c")["value"]) <= 80
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_opening_a_list_of_values_that_never_finish_answers(kernel):
    assert kernel.run(ODD + "items = [Spin(), Spin(), 1]")["status"] == "ok"
    detail = in_time(lambda: kernel.variable_detail("items"), 20)
    assert detail["total"] == 3 and detail["items"][2]["value"] == "1"


# ---------- 2. a shared session: the other notebook never touches the pause ----------

@pytest.fixture(params=["engine", "process"])
def shared(request):
    k = Kernel() if request.param == "engine" else ProcessKernel()
    yield k
    if request.param == "process":
        k.close()


def test_another_notebook_in_the_session_gets_busy_answers_and_leaves_the_pause(shared):
    k = shared
    with k.as_guest("A"):
        k.notebook_name = "A"
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
    with k.as_guest("B"):
        snap = k.poll(-1, 0.2)
        assert snap.get("session_running") and not snap.get("paused") and snap["status"] != "running"
        assert not k.interrupt()["interrupted"]
        with pytest.raises(RuntimeError, match="No cell is running under the debugger"):
            k.debug("continue")
        with pytest.raises(RuntimeError, match="is running a cell in this shared session"):
            k.start("c = 3", breakpoints=[1])
        assert "a" not in {v["name"] for v in k.variables()}
        assert k.variable_detail("a").get("missing")
        with pytest.raises(RuntimeError, match="cell is running"):
            k.ipywidget_event("nope", {"value": 1})
    with k.as_guest("A"):
        assert wait(k)["paused"]["line"] == 2  # still paused where it was
        k.debug("continue")
        assert wait(k)["status"] == "ok"
    with k.as_guest("B"):
        assert k.run("c = 3")["status"] == "ok"


def test_a_guest_that_restarts_or_leaves_while_paused_ends_its_paused_cell(shared):
    k = shared
    with k.as_guest("A"):
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
    k.forget_guest("A")  # GuestKernel.restart, and leaving the shared session
    with k.as_guest("B"):  # the session isn't held up by a cell nobody can reach
        assert k.run("c = 3")["status"] == "ok"


def test_a_guest_renamed_while_paused_keeps_its_pause_and_variables():
    sessions = KernelSessions(lambda process: ProcessKernel(), lambda: True)
    try:
        sessions.session("H")
        sessions.share("G", "H")
        g = sessions.kernel("G")
        g.notebook_name = "G"
        assert g.run("kept = 1")["status"] == "ok"
        g.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(g)["paused"]["line"] == 2
        sessions.rename("G", "G2")
        g2 = sessions.kernel("G2")
        assert wait(g2)["paused"]["line"] == 2
        g2.debug("continue")
        assert wait(g2)["status"] == "ok"
        assert {"kept", "a", "b"} <= {v["name"] for v in g2.variables()}
    finally:
        for k in sessions.kernels():
            k.close()


def test_a_renamed_guest_in_the_engine_kernel_keeps_its_pause():
    k = Kernel()
    with k.as_guest("G"):
        k.run("kept = 1")
        k.start("a = 1\nb = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
    k.rename_guest("G", "G2")
    with k.as_guest("G2"):
        assert wait(k)["paused"]["line"] == 2
        k.debug("continue")
        assert wait(k)["status"] == "ok"
        assert {"kept", "a", "b"} <= {v["name"] for v in k.variables()}
    with k.as_guest("G"):
        assert k.poll()["status"] == "idle"


# ---------- 3. the rest of the engine while paused ----------

def test_del_and_weakref_callbacks_during_a_pause_leave_it_paused(kernel):
    kernel.start("import gc, weakref\n"
                 "seen = []\n"
                 "class Gone:\n"
                 "    def __del__(self): seen.append('del'); print('bye')\n"
                 "class Ref: pass\n"
                 "r = Ref()\n"
                 "w = weakref.ref(r, lambda _ref: seen.append('ref'))\n"
                 "g = Gone(); g.me = g; del g\n"
                 "del r\n"
                 "x = 1\n"
                 "y = 2", breakpoints=[11])
    assert wait(kernel)["paused"]["line"] == 11
    out = kernel.debug("eval", "(gc.collect(), sorted(seen))[1]")
    assert "del" in out["value"] and "ref" in out["value"], out
    assert wait(kernel)["paused"]["line"] == 11
    kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok"  # "bye" shows wherever the collection ran, on whichever thread


@pytest.mark.parametrize("last", ["y = 2; len('a')", "len('b'); len('a')", "y = 2; y"], ids=["assign", "call", "name"])
@pytest.mark.parametrize("action", ["continue", "step"])
def test_continue_from_a_last_line_ending_in_an_expression_doesnt_stop_on_it_again(kernel, last, action):
    # The cell's last expression is compiled apart (so its value shows): it shares the line.
    kernel.start(f"x = 1\n{last}", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug(action)
    snap = wait(kernel)
    assert snap["status"] == "ok" and not snap.get("paused"), snap.get("paused")


def test_a_breakpoint_on_a_last_line_expression_still_stops(kernel):
    kernel.start("x = 1\nx + 1", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    snap = wait(kernel)
    assert snap["status"] == "ok" and snap["outputs"][-1]["text"] == "2"


def test_start_while_paused_is_refused_and_the_pause_stays(kernel):
    kernel.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    with pytest.raises(RuntimeError, match="already running"):
        kernel.start("c = 3")
    with pytest.raises(RuntimeError, match="already running"):
        kernel.start("c = 3", breakpoints=[1])
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"
    assert kernel.run("print(a + b)")["outputs"][-1]["text"].strip() == "3"


def test_a_widget_change_while_paused_is_refused_and_the_pause_stays(kernel):
    kernel.start("a = 1\nb = 2", breakpoints=[2])
    assert wait(kernel)["paused"]["line"] == 2
    with pytest.raises(RuntimeError, match="cell is running"):
        kernel.ipywidget_event("nope", {"value": 1})
    assert wait(kernel)["paused"]["line"] == 2
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_a_thread_printing_while_paused_doesnt_slow_the_debugger(kernel):
    kernel.start("import threading, time\n"
                 "stop = threading.Event()\n"
                 "def chatter():\n"
                 "    n = 0\n"
                 "    while not stop.is_set() and n < 200_000:\n"
                 "        print('.', end='', flush=n % 1000 == 0); n += 1\n"
                 "t = threading.Thread(target=chatter, daemon=True); t.start()\n"
                 "x = 1\n"
                 "stop.set(); t.join(10)", breakpoints=[9])
    assert wait(kernel)["paused"]["line"] == 9
    started = time.monotonic()
    for _ in range(5):
        assert in_time(lambda: kernel.poll(-1, 0.0), 5).get("paused")
    assert in_time(lambda: kernel.debug("eval", "x + 1"), 15)["value"] == "2"
    assert time.monotonic() - started < 15
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


def test_variables_while_paused_read_the_namespace_and_follow_a_delete(kernel):
    kernel.start("x = 1\n"
                 "def f():\n"
                 "    inner = [1, 2]\n"
                 "    return inner\n"
                 "f()\n"
                 "del x\n"
                 "y = 2", breakpoints=[4, 7])
    p = wait(kernel)["paused"]
    assert p["line"] == 4 and row(p, "inner")["expandable"] is False
    assert kernel.variable_detail("inner").get("missing")  # a frame's local isn't the namespace's
    assert "x" in {v["name"] for v in kernel.variables()}
    kernel.debug("continue")
    p = wait(kernel)["paused"]
    assert p["line"] == 7 and "x" not in {v["name"] for v in p["variables"]}
    assert kernel.variable_detail("x").get("missing")
    kernel.debug("continue")
    assert wait(kernel)["status"] == "ok"


VAULT_VALUE = "kf-" + "w4P" * 6  # built, not written out
FAKE_VAULT = ("import fabric_desktop.http_session as _h\n"
              "class _Resp:\n"
              "    status_code = 200\n"
              f"    def json(self): return {{'value': {VAULT_VALUE!r}}}\n"
              "class _Session:\n"
              "    def get(self, *a, **k): return _Resp()\n"
              "_h.session = lambda: _Session()\n"
              "del _h\n")


@pytest.fixture(params=["engine", "process"])
def vault_kernel(request):
    original = http_session.session
    k = Kernel(token=lambda audience: "tok") if request.param == "engine" else ProcessKernel(token=lambda audience: "tok")
    yield k
    http_session.session = original
    if request.param == "process":
        k.close()


def test_a_secret_in_a_nested_frame_stays_hidden(vault_kernel):
    k = vault_kernel
    assert k.run(FAKE_VAULT)["status"] == "ok"
    k.start("def outer():\n"
            "    def inner():\n"
            "        plain = notebookutils.credentials.getSecret('kv', 'app-key')\n"
            "        held = {'k': plain}\n"
            "        return held\n"
            "    return inner()\n"
            "kept = outer()", breakpoints=[5])
    p = wait(k)["paused"]
    assert [f["function"] for f in p["stack"]] == ["(cell)", "outer", "inner"]
    assert row(p, "plain")["value"] == "hidden" and row(p, "held")["value"] == "hidden"
    assert VAULT_VALUE not in json.dumps(p)
    assert VAULT_VALUE not in json.dumps(k.debug("eval", "held"))
    k.debug("continue")
    assert wait(k)["status"] == "ok"
    assert VAULT_VALUE not in json.dumps(k.variables()) + json.dumps(k.variable_detail("kept"))


def test_a_value_too_slow_to_check_for_a_secret_is_hidden(vault_kernel):
    k = vault_kernel
    assert k.run(FAKE_VAULT)["status"] == "ok"
    assert k.run(ODD + "plain = notebookutils.credentials.getSecret('kv', 'app-key')\n"
                       "items = [Spin(), plain]")["status"] == "ok"
    detail = in_time(lambda: k.variable_detail("items"), 20)
    assert detail.get("hidden") and VAULT_VALUE not in json.dumps(detail)


# ---------- 4. the process kernel: limits, restart and close while paused ----------

@pytest.mark.skipif(not sys.platform.startswith(("linux", "win")), reason="memory is read on Windows and Linux")
def test_the_memory_limit_ends_a_paused_cell(monkeypatch):
    monkeypatch.setattr(kernel_proc, "WATCH_SECONDS", 0.1)
    k = ProcessKernel()
    try:
        k.start("block = bytearray(300 * 1024 ** 2)\nx = 1", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        k.memory_limit = (kernel_proc.memory_used(k.pid) or 0) - 100 * 1024 ** 2
        deadline = time.monotonic() + 20
        snap = k.poll(-1, 0.3)
        while snap["status"] == "running" and time.monotonic() < deadline:
            snap = k.poll(-1, 0.3)
        assert snap["status"] == "error" and not snap.get("paused") and k.limit_hit == "memory"
        k.memory_limit = None
        assert k.run("print(1)")["status"] == "ok"
    finally:
        k.close()


@pytest.mark.parametrize("end", ["close", "restart"])
def test_close_or_restart_racing_a_debug_command_answers_it(end):
    k = ProcessKernel()
    try:
        k.start("import time\nx = 1\ny = 2", breakpoints=[2])
        assert wait(k)["paused"]["line"] == 2
        answer: dict = {}

        def evaluate():
            try:
                answer["value"] = k.debug("eval", "time.sleep(30)")
            except Exception as exc:  # noqa: BLE001 - what the caller sees
                answer["error"] = exc
        t = threading.Thread(target=evaluate, daemon=True)
        t.start()
        time.sleep(0.5)
        getattr(k, end)()
        t.join(15)
        assert not t.is_alive(), "the debug command was never answered"
        assert "error" in answer and "stopped" in str(answer["error"])
        snap = k.poll(-1, 0.0)
        assert not snap.get("paused") and snap["status"] != "running"
        if end == "restart":
            assert k.run("print(2)")["status"] == "ok"
    finally:
        k.close()


def test_restart_racing_an_evaluation_in_the_engine_kernel_ends_the_cell():
    k = Kernel()
    k.start("import time\nx = 1\ny = 2", breakpoints=[2])
    assert wait(k)["paused"]["line"] == 2
    t = threading.Thread(target=lambda: k.debug("eval", "next(i for i in iter(int, 1) if i)"), daemon=True)
    t.start()
    time.sleep(0.3)
    in_time(k.restart, 15)
    assert k.poll()["status"] == "cancelled" and sys.gettrace() is None
    t.join(15)
    assert not t.is_alive()
    assert k.run("print(1)")["status"] == "ok"
