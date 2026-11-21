"""The Variables pane hides a value getSecret read wherever it sits: inside a list, a dict, a nested
value, a longer text or an object's repr, as well as on its own. Such a row (and an item of an opened
variable) shows as hidden and can't be opened. A preview cut short in the middle of one hides the
start of it too. The rows a paired editor reads (kernel_variables) are the same rows. Both kernels."""

from __future__ import annotations

import json
import time

import pytest
from test_prove import call  # noqa: F401

from fabric_desktop import http_session, secret_redaction
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.notebook import Kernel, _preview
from fabric_desktop.service import Service

# Built, not written out, so no secret scanner takes the test for a leak.
VAULT_VALUE = "kf-" + "q7Z" * 6

# getSecret returns VAULT_VALUE without a Key Vault: answered in the kernel's own process.
FAKE_VAULT = ("import fabric_desktop.http_session as _h\n"
              "class _Resp:\n"
              "    status_code = 200\n"
              f"    def json(self): return {{'value': {VAULT_VALUE!r}}}\n"
              "class _Session:\n"
              "    def get(self, *a, **k): return _Resp()\n"
              "_h.session = lambda: _Session()\n"
              "s = notebookutils.credentials.getSecret('kv', 'app-key')\n"
              "del _h, _Resp, _Session\n")

CELL = ("class Conn:\n"
        "    def __init__(self, k): self.k = k\n"
        "    def __repr__(self): return f'Conn(key={self.k})'\n"
        "in_list = [1, s, 3]\n"
        "in_dict = {'url': 'https://x', 'auth': s}\n"
        "nested = {'a': [{'b': ('x', s)}]}\n"
        "longer = 'Bearer ' + s + ' for the api'\n"
        "conn = Conn(s)\n"
        "cut = 'x' * 70 + s\n"
        "plain = 'nothing to hide'\n"
        "numbers = list(range(5))\n")

HOLDING = ["s", "in_list", "in_dict", "nested", "longer", "conn", "cut"]


@pytest.fixture(params=["engine", "process"])
def kernel(request):
    original = http_session.session  # the engine kernel's FAKE_VAULT replaces it in this process
    k = Kernel(token=lambda audience: "tok") if request.param == "engine" else ProcessKernel(token=lambda audience: "tok")
    yield k
    http_session.session = original
    if request.param == "process":
        k.close()


def run(k, code):
    assert k.run(code)["status"] == "ok"


def no_secret(what):
    text = json.dumps(what)
    assert VAULT_VALUE not in text, text
    for k in range(secret_redaction.MIN_CHARS, len(VAULT_VALUE)):
        assert VAULT_VALUE[:k] + "..." not in text, text  # nor the start of one at a cut


def test_rows_holding_a_secret_are_hidden_and_closed(kernel):
    run(kernel, FAKE_VAULT)
    run(kernel, CELL)
    rows = {r["name"]: r for r in kernel.variables()}
    no_secret(rows)
    for name in HOLDING:
        assert rows[name]["value"] == "hidden", rows[name]
        assert rows[name]["expandable"] is False, rows[name]
    assert rows["plain"]["value"] == "'nothing to hide'"
    assert rows["numbers"]["value"] == "[0, 1, 2, 3, 4]" and rows["numbers"]["expandable"] is True


def test_opening_a_variable_that_holds_a_secret_shows_it_hidden(kernel):
    run(kernel, FAKE_VAULT)
    run(kernel, CELL + "mixed = ['ok', s, ['deep', s], 7]\n")
    for name in HOLDING + ["mixed"]:
        detail = kernel.variable_detail(name)
        no_secret(detail)
        assert detail.get("hidden") is True, detail
    # A long list whose secret is past the preview's first items: the list opens, and the item that
    # holds it is hidden while the others show.
    run(kernel, "late = [str(i) for i in range(150)]\nlate[25] = s\n")
    detail = kernel.variable_detail("late")
    no_secret(detail)
    values = [item["value"] for item in detail["items"]]
    assert values[25] == "hidden" and values[24] == "'24'" and values.count("hidden") == 1


def test_without_secrets_rows_read_as_before(kernel):
    run(kernel, "a = [1, 2]\nb = 'text'\n")
    rows = {r["name"]: r for r in kernel.variables()}
    assert rows["a"]["value"] == "[1, 2]" and rows["b"]["value"] == "'text'"


def test_the_debugger_pause_shows_the_same_rows_hidden(kernel):
    run(kernel, FAKE_VAULT)
    kernel.start(CELL + "stop_here = 1", breakpoints=[CELL.count("\n") + 1])
    deadline = time.monotonic() + 20
    snap = kernel.poll(-1, 0.3)
    while not snap.get("paused") and time.monotonic() < deadline:
        snap = kernel.poll(-1, 0.3)
    rows = {r["name"]: r for r in snap["paused"]["variables"]}
    no_secret(snap)
    for name in HOLDING:
        assert rows[name]["value"] == "hidden", rows[name]
    kernel.debug("continue")


# ---------- the preview helpers ----------

def test_a_secret_is_replaced_before_the_preview_is_cut():
    replaced = lambda text: secret_redaction.redact_values(text, [VAULT_VALUE])  # noqa: E731
    text = _preview("x" * 70 + VAULT_VALUE, replaced)
    assert VAULT_VALUE[:4] not in text
    big = [str(i) for i in range(150)]
    big[3] = VAULT_VALUE
    assert VAULT_VALUE not in _preview(big, replaced) and "[REDACTED]" in _preview(big, replaced)


@pytest.mark.parametrize("shown", range(secret_redaction.MIN_CHARS, len(VAULT_VALUE)))
def test_a_cut_inside_a_secret_hides_its_start(shown):
    text = "abc " + VAULT_VALUE[:shown] + "..."
    assert secret_redaction.mask_cut(text, [VAULT_VALUE]) == "abc [REDACTED]..."
    assert secret_redaction.mask_cut(text + "]", [VAULT_VALUE]) == "abc [REDACTED]...]"


def test_a_short_or_unrelated_cut_is_left_alone():
    assert secret_redaction.mask_cut("abc kf-...", [VAULT_VALUE]) == "abc kf-..."  # under MIN_CHARS
    assert secret_redaction.mask_cut("abc zzzzz...", [VAULT_VALUE]) == "abc zzzzz..."
    assert secret_redaction.mask_cut("no cut here", [VAULT_VALUE]) == "no cut here"


# ---------- the rows a paired editor reads ----------

@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    original = http_session.session
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(s, "_token_for", lambda audience: "tok", raising=False)  # getSecret asks for one; no sign-in here
    yield s
    http_session.session = original
    for k in s.sessions.kernels():
        close = getattr(k, "close", None)
        if close:
            close()


@pytest.mark.parametrize("own_process", [False, True])
def test_the_rows_a_paired_editor_reads_hide_it_too(svc, own_process):
    if own_process:
        call(svc, "save_app_settings", changes={"kernel_process": True})
    for code in (FAKE_VAULT, CELL):
        call(svc, "start_cell", code=code, notebook="Keys")
        deadline = time.monotonic() + 30
        snap = call(svc, "poll_cell", version=-1, wait=0.5, notebook="Keys")
        while snap["status"] == "running" and time.monotonic() < deadline:
            snap = call(svc, "poll_cell", version=-1, wait=0.5, notebook="Keys")
        assert snap["status"] == "ok", snap
    rows = {r["name"]: r for r in call(svc, "kernel_variables", notebook="Keys")}
    no_secret(rows)
    for name in HOLDING:
        assert rows[name]["value"] == "hidden", rows[name]
