"""Notebook debugger (#2362 hardening, round 5): a secret never shows while a cell is paused. A value
read with getSecret, or held by a variable whose name looks like a secret, appears nowhere in the
paused snapshot (Variables, watches, evaluations, the call stack, skipped names, the reason) or in
what an evaluation returns, whether it is shown as itself, inside a container or a longer text, in an
object's repr or in an error's message. Both kernels run the same debugger, so each test runs in
both."""

from __future__ import annotations

import json
import time

import pytest

from fabric_desktop import http_session
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel

# Built, not written out, so no secret scanner takes the test for a leak.
VAULT_VALUE = "kf-" + "q7Z" * 6

# Makes notebookutils.credentials.getSecret return VAULT_VALUE without a Key Vault: the request is answered
# in the kernel's own process (the engine's for Kernel, the kernel process for ProcessKernel).
FAKE_VAULT = ("import fabric_desktop.http_session as _h\n"
              "class _Resp:\n"
              "    status_code = 200\n"
              f"    def json(self): return {{'value': {VAULT_VALUE!r}}}\n"
              "class _Session:\n"
              "    def get(self, *a, **k): return _Resp()\n"
              "_h.session = lambda: _Session()\n"
              "s = notebookutils.credentials.getSecret('kv', 'app-key')\n"
              "del _h, _Resp, _Session\n")


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    original = http_session.session  # the engine kernel's FAKE_VAULT replaces it in this process
    k = Kernel(token=lambda audience: "tok") if request.param == "engine" else ProcessKernel(token=lambda audience: "tok")
    yield k
    http_session.session = original
    if request.param == "process":
        k.close()


def wait(k, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = k.poll(-1, 0.3)
        if snap.get("paused") or snap["status"] != "running":
            return snap
    raise AssertionError("the cell neither paused nor finished")


def with_secret(k):
    assert k.run(FAKE_VAULT)["status"] == "ok"


def clean(what, secret=VAULT_VALUE):
    text = json.dumps(what)
    assert secret not in text, text
    return what


def row(p, name):
    return next(v for v in p["variables"] if v["name"] == name)


# Line 13 is in inner(), two frames down from the cell, with the secret as an argument; line 15 is the
# cell's own last line.
CELL = ("class Holder:\n"
        "    def __init__(self, v): self.v = v\n"
        "    def __repr__(self): return f'Holder({self.v})'\n"
        "items = [s, 1]\n"
        "mapping = {'k': s}\n"
        "pair = (s,)\n"
        "joined = 'prefix-' + s\n"
        "fstr = f'<{s}>'\n"
        "holder = Holder(s)\n"
        "def outer(arg):\n"
        "    return inner(arg)\n"
        "def inner(x):\n"
        "    return len(x)\n"
        "outer(s)\n"
        "done = 1")

EXPRESSIONS = ["s", "joined", "s + '!'", "repr(s)", "str([s])", "holder", "mapping", "items", "pair", "fstr",
               "s.upper().lower()", "int(s)", "float(fstr)", "{}[s]", "mapping[s]", "getattr(holder, s)",
               "(_ for _ in ()).throw(ValueError(s))", "1 / 0 if s else 0"]


def test_a_getsecret_value_is_hidden_in_variables_however_it_is_held(kernel):
    with_secret(kernel)
    kernel.start(CELL, breakpoints=[15])
    p = clean(wait(kernel))["paused"]
    assert p["line"] == 15
    names = {v["name"] for v in p["variables"]}
    assert {"s", "items", "mapping", "pair", "joined", "fstr", "holder"} <= names
    # A value that holds one shows as hidden, as in the Variables pane.
    for name in ("s", "items", "mapping", "pair", "joined", "fstr", "holder"):
        assert row(p, name)["value"] == "hidden", name


def test_a_getsecret_value_is_hidden_in_a_nested_frame_and_its_stack(kernel):
    with_secret(kernel)
    kernel.start(CELL, breakpoints=[13])
    p = clean(wait(kernel))["paused"]
    assert [f["function"] for f in p["stack"]] == ["(cell)", "outer", "inner"]
    assert row(p, "x")["value"] == "hidden"
    for expr in ("x", "x + '!'", "repr(x)", "int(x)"):
        clean(kernel.debug("eval", expr))
    assert kernel.debug("eval", "int(x)")["error"] == "ValueError: invalid literal for int() with base 10: '[REDACTED]'"


@pytest.mark.parametrize("where", [13, 15])
def test_evaluations_never_show_a_getsecret_value(kernel, where):
    with_secret(kernel)
    kernel.start(CELL, breakpoints=[where])
    wait(kernel)
    for expr in EXPRESSIONS if where == 15 else ["x", "repr(x)", "int(x)", "[x]", "x[::1]"]:
        result = clean(kernel.debug("eval", expr))
        assert "value" in result or "error" in result, result
    clean(wait(kernel))  # the evaluations kept in the snapshot too


def test_watches_never_show_a_getsecret_value(kernel):
    with_secret(kernel)
    kernel.start(CELL, breakpoints=[15], watches=EXPRESSIONS)
    p = clean(wait(kernel))["paused"]
    assert [w["expr"] for w in p["watches"]] == EXPRESSIONS
    assert next(w for w in p["watches"] if w["expr"] == "s")["value"] == "hidden"


# The value isn't one getSecret read: only the names say it is a secret.
NAMED = ("def make():\n"
         "    return 'kf-' + 'wX' * 7\n"
         "password = make()\n"
         "token = make()\n"
         "api_key = make()\n"
         "client_secret = make()\n"
         "def use(token):\n"
         "    return len(token)\n"
         "use(password)\n"
         "done = 1")
NAMED_VALUE = "kf-" + "wX" * 7


@pytest.mark.parametrize("where", [8, 10])
def test_a_value_hidden_by_its_name_stays_hidden_in_variables_watches_and_evaluations(kernel, where):
    names = ["token"] if where == 8 else ["password", "token", "api_key", "client_secret"]
    kernel.start(NAMED, breakpoints=[where], watches=names)
    p = clean(wait(kernel), NAMED_VALUE)["paused"]
    for name in names:
        assert row(p, name)["value"] == "hidden"
        assert next(w for w in p["watches"] if w["expr"] == name)["value"] == "hidden"
        assert clean(kernel.debug("eval", name), NAMED_VALUE)["value"] == "hidden"
    if where == 8:
        assert [f["function"] for f in p["stack"]] == ["(cell)", "use"]
    clean(wait(kernel), NAMED_VALUE)


def test_skipped_names_and_the_reason_never_carry_a_secret(kernel):
    with_secret(kernel)
    kernel.start("import json\njson.dumps(s)\ndone = 1", step=True)
    assert wait(kernel)["paused"]["line"] == 1
    kernel.debug("next")
    kernel.debug("step")
    p = clean(wait(kernel))["paused"]
    assert p["line"] == 3 and p["skipped"] == ["json.dumps"] and p["reason"] == "step"
