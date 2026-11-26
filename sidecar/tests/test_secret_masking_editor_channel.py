"""Secret values are shown masked in every view: what the editor channel sends a paired VS Code window
(the Variables rows from GET /v1/variables, and a cell's printed output and errors from /v1/run), with
the notebook's session in the engine and in its own process, for a few hundred seeded values
(secret_masking_cases.py) that hold a value read with getSecret or dbutils.secrets.get.

Neither the secret, its bytes as printed, nor any part of it secret_redaction.MIN_CHARS long shows in
the JSON the window reads, and each reply still carries its rows and its masked output."""

from __future__ import annotations

import json
import random

import pytest
from secret_masking_cases import PRELUDE, cases, fake_vault, make_secret, shown
from test_editor_channel import Window
from test_prove import call

from fabric_desktop import http_session
from fabric_desktop.service import Service

SEEDS = {False: 4241, True: 4242}
VALUES = 200
PRINTED = 60
ERRORS = 8


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    original = http_session.session  # the engine kernel's fake vault replaces it in this process
    s = Service(root=tmp_path / "app")
    monkeypatch.setattr(s, "_token_for", lambda audience: "tok", raising=False)  # getSecret asks for one; no sign-in here
    yield s
    http_session.session = original
    for k in s.sessions.kernels():
        close = getattr(k, "close", None)
        if close:
            close()


def texts(reply):
    """The reply as the window parses it."""
    return json.loads(json.dumps(reply))


@pytest.mark.parametrize("own_process", [False, True])
def test_what_a_paired_window_reads_shows_every_secret_masked(svc, own_process):
    if own_process:
        call(svc, "save_app_settings", changes={"kernel_process": True})
    svc.save_notebook("Keys", [{"type": "code", "source": "x = 1"}])
    call(svc, "editor_set_enabled", enabled=True)
    try:
        window = Window(svc)
        window.pair(svc)
        path = svc.notebook_file_path("Keys")["path"]
        seed = SEEDS[own_process]
        rnd = random.Random(seed)
        vault = {f"app-key-{i}": make_secret(rnd, accent=i == 1) for i in range(4)}
        secrets = list(vault.values())
        names = [f"{p}{i}" for p in "sd" for i in range(len(vault))]
        generated = cases(seed, names, VALUES)
        assert window.run(path, fake_vault(vault, dbutils=True))["status"] == "ok"
        snap = window.run(path, PRELUDE + "".join(f"v{i:03d} = {e}\n" for i, (_, e) in enumerate(generated)))
        assert snap["status"] == "ok", [o.get("evalue") for o in snap.get("outputs") or []]

        status, reply = window.call("GET", f"/v1/variables?path={_q(path)}")
        assert status == 200 and reply["ok"]
        rows = {r["name"]: r for r in texts(reply)["result"]}
        problems = [(n, f) for n, f in ((n, shown(r, secrets)) for n, r in rows.items()) if f]
        assert problems == []
        assert {f"v{i:03d}" for i in range(VALUES)} <= set(rows)
        assert all(r["value"] for r in rows.values())
        assert sum(rows[f"v{i:03d}"]["value"] == "hidden" for i in range(VALUES)) >= VALUES * 0.6

        # Printed output: a value dbutils.secrets read is masked in what a cell prints, as on Databricks.
        held = [i for i, (_, e) in enumerate(generated) if any(f"d{j}" in e for j in range(len(vault)))]
        picked = rnd.sample(held, min(PRINTED, len(held)))
        code = "".join(f"print(v{i:03d})\nprint(repr(v{i:03d}))\n" for i in picked)
        snap = texts(window.run(path, code))
        assert snap["status"] == "ok" and shown(snap, secrets) is None
        printed = "".join(o.get("text", "") for o in snap["outputs"])
        assert printed.count("[REDACTED]") >= len(picked), printed.count("[REDACTED]")

        # Errors: the value in an exception's args, and in a chained exception's text.
        for i in rnd.sample(held, ERRORS):
            code = (f"try:\n    raise KeyError('missing', v{i:03d})\n"
                    f"except KeyError as inner:\n    raise RuntimeError('while reading: ' + str(v{i:03d})) from inner")
            snap = texts(window.run(path, code))
            assert snap["status"] == "error" and shown(snap, secrets) is None, i
            [error] = [o for o in snap["outputs"] if o.get("type") == "error"]
            assert "[REDACTED]" in error["evalue"] and error["traceback"]
    finally:
        call(svc, "editor_set_enabled", enabled=False)


def _q(text):
    from urllib.parse import quote
    return quote(text, safe="")
