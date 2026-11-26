"""A value read with dbutils.secrets stays out of every place the notebook's output doesn't cover: the
Variables pane and its detail, hover help, watches, the debugger, a table's later pages, the engine's
and the kernel process's stderr (sidecar.log) and the support bundle. As on Databricks, it shows as
[REDACTED] (secret_redaction.py). No assertion message prints the value."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fabric_desktop import http_session, secret_redaction, server, support_bundle
from fabric_desktop.notebook import Kernel

# Built, so no literal looks like a credential to the secret scan.
VAULT_VALUE = "-".join(["vv", "kept", "in", "vault", "9137"])
SIDECAR = Path(__file__).resolve().parents[1]


@pytest.fixture
def kernel(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"value": VAULT_VALUE}

    monkeypatch.setattr(http_session, "session", lambda: type("S", (), {"get": lambda self, *a, **k: Resp()})())
    k = Kernel(token=lambda audience: "tok")
    out = k.run("pw = dbutils.secrets.get('kv', 'pw')\nraw = dbutils.secrets.getBytes('kv', 'pw')\n"
                "conn = {'user': 'u', 'pw': pw}\ndsn = 'host=x;' + pw\nitems = [pw]")
    assert out["status"] == "ok"
    return k


def _leaks(value) -> bool:
    return VAULT_VALUE in json.dumps(value, default=str)


def test_the_variables_pane_never_shows_a_secret_inside_another_value(kernel):
    rows = {r["name"]: r for r in kernel.variables()}
    assert {"pw", "raw", "conn", "dsn", "items"} <= set(rows)
    leaked = [name for name, row in rows.items() if _leaks(row)]
    assert leaked == [], f"the secret shows in the Variables pane for {leaked}"
    assert rows["raw"]["value"] == "hidden"  # getBytes' value is the secret, as get's is
    # A value that holds the secret (a longer text, a dict) shows as hidden too, and can't be opened.
    for name in ("dsn", "conn"):
        assert rows[name]["value"] == "hidden" and rows[name]["expandable"] is False, rows[name]


@pytest.mark.parametrize("name", ["conn", "items", "raw"])
def test_a_variables_detail_never_shows_a_secret(kernel, name):
    assert not _leaks(kernel.variable_detail(name))


def test_hover_help_never_shows_a_secret(kernel):
    assert not _leaks(kernel.inspect("dsn", 3))


def test_a_watch_never_shows_a_secret(kernel):
    out = kernel.start("x = 1", watches=["dsn", "conn['pw'] + '!'", "1/0 if False else int(dsn)"])
    kernel._current.done.wait()
    snap = kernel.poll()
    assert snap.get("watches") and not _leaks(snap) and not _leaks(out)


def test_a_tables_later_pages_never_show_a_secret(kernel):
    out = kernel.run("import polars as pl\npl.DataFrame({'n': range(500), 's': [pw if i == 450 else 'x' for i in range(500)]})")
    table = out["outputs"][-1]
    assert table["type"] == "table" and not _leaks(out)
    page = kernel.frame_page(table["frame"], offset=400, limit=100)
    assert page["rows"] and not _leaks(page) and "[REDACTED]" in json.dumps(page)
    assert not _leaks(kernel.frame_page(table["frame"], q="vault"))
    assert not _leaks(kernel.frame_profile(table["frame"]))


def test_the_engines_stderr_and_log_lines_never_hold_a_secret():
    secret_redaction.remember(VAULT_VALUE)
    raw = io.StringIO()
    stream = server._RedactedStream(raw)
    print("from a background thread:", VAULT_VALUE, file=stream)
    for part in (VAULT_VALUE[:6], VAULT_VALUE[6:], "\n"):  # written in pieces, redacted whole
        stream.write(part)
    stream.flush()
    assert not _leaks(raw.getvalue()) and raw.getvalue().count("[REDACTED]") == 2
    import logging
    record = logging.LogRecord("fabric_desktop.notebook", logging.INFO, __file__, 1, "failed: %s", (VAULT_VALUE,), None)
    assert not _leaks(server._SafeFormatter("%(message)s").format(record))


def test_the_support_bundle_never_holds_a_secret(tmp_path, monkeypatch):
    secret_redaction.remember(VAULT_VALUE)
    (tmp_path / "sidecar.log").write_text(f"engine: something printed {VAULT_VALUE}\n", encoding="utf-8")
    monkeypatch.setattr(support_bundle, "about", lambda data_dir=None: {})
    files = support_bundle._files(tmp_path, None)
    assert "[REDACTED]" in files["sidecar.log"] and not _leaks(files)


def test_the_overflow_file_never_holds_a_secret_printed_in_pieces(monkeypatch, kernel):
    from fabric_desktop import notebook
    monkeypatch.setattr(notebook, "STREAM_CHARS", 50)
    before = set(notebook._spills)  # another test's overflow files can still be listed in this process
    out = kernel.run("import sys\nprint('x' * 60)\nfor _ in range(5):\n"
                     "    sys.stdout.write(pw[:7])\n    sys.stdout.write(pw[7:])\n    sys.stdout.write(' ')")
    assert out["status"] == "ok" and not _leaks(out)
    spilled = [p for p in notebook._spills - before if p.exists()]
    assert spilled
    text = "".join(p.read_text(encoding="utf-8") for p in spilled)
    assert not _leaks(text) and text.count("[REDACTED]") == 5


def test_the_kernel_process_stderr_never_holds_a_secret():
    """The kernel process writes to the engine's stderr, which is sidecar.log: what a cell's background
    thread prints, or logs, goes there rather than to the cell."""
    code = ("import threading, logging\nfrom fabric_desktop import secret_redaction\n"
            f"v = {VAULT_VALUE!r}\nsecret_redaction.remember(v)\n"
            "t = threading.Thread(target=lambda: (print('bg', v), logging.getLogger('x').warning('log %s', v)))\n"
            "t.start(); t.join()")
    env = dict(os.environ, PYTHONPATH=str(SIDECAR) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.Popen([sys.executable, "-m", "fabric_desktop.kernel_host"], cwd=SIDECAR, env=env, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    try:
        assert json.loads(proc.stdout.readline())["id"] == "ready"
        proc.stdin.write(json.dumps({"id": "1", "op": "start", "args": {"code": code}}) + "\n")
        proc.stdin.flush()
        while True:
            message = json.loads(proc.stdout.readline())
            if message.get("id") == "1":
                break
        proc.stdin.write(json.dumps({"id": "2", "op": "poll", "args": {"wait": 30}}) + "\n")
        proc.stdin.flush()
        while True:
            message = json.loads(proc.stdout.readline())
            if message.get("id") == "2" and message["result"]["status"] != "running":
                break
            if message.get("id") == "2":
                proc.stdin.write(json.dumps({"id": "2", "op": "poll", "args": {"wait": 30}}) + "\n")
                proc.stdin.flush()
        assert message["result"]["status"] == "ok"
        proc.stdin.close()
        err = proc.stderr.read()
        proc.wait(30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert "bg [REDACTED]" in err and "log [REDACTED]" in err and not _leaks(err)


def test_repr_of_dbutils_secrets_and_its_errors_hold_no_value(kernel):
    out = kernel.run("repr(dbutils.secrets)")
    assert out["status"] == "ok" and not _leaks(out)
    out = kernel.run("dbutils.secrets.get('kv', 'bad_name')")
    assert out["status"] == "error" and not _leaks(out)


def test_a_long_write_ending_partway_through_a_secret_holds_the_start_back():
    secret_redaction.remember(VAULT_VALUE)
    raw = io.StringIO()
    stream = server._RedactedStream(raw)
    stream.write("x" * (stream.HOLD + 10) + VAULT_VALUE[:8])  # past HOLD, no newline, half a value
    assert raw.getvalue() and VAULT_VALUE[:8] not in raw.getvalue()
    stream.write(VAULT_VALUE[8:] + " done\n")
    stream.write("tail " + VAULT_VALUE[:5])
    stream.close()  # writes out what it held
    text = raw.getvalue()
    assert not _leaks(text) and "[REDACTED] done\n" in text and text.endswith("tail " + VAULT_VALUE[:5])


def test_a_line_that_cant_start_a_secret_isnt_held():
    secret_redaction.remember(VAULT_VALUE)
    raw = io.StringIO()
    stream = server._RedactedStream(raw)
    stream.write("engine started\n")
    assert raw.getvalue() == "engine started\n"


def test_a_secret_printed_escaped_is_redacted(monkeypatch):
    """print(bytes) and repr(str) escape what isn't printable ASCII, so the value as read never appears."""
    escaped_value = "päss-wörd\n" + "-".join(["vault", "4471"])

    class Resp:
        status_code = 200

        def json(self):
            return {"value": escaped_value}

    monkeypatch.setattr(http_session, "session", lambda: type("S", (), {"get": lambda self, *a, **k: Resp()})())
    k = Kernel(token=lambda audience: "tok")
    out = k.run("b = dbutils.secrets.getBytes('kv', 'pw')\nprint(b)\nprint(repr(dbutils.secrets.get('kv', 'pw')))\nb")
    assert out["status"] == "ok"
    text = json.dumps(out["outputs"])
    forms = [escaped_value, repr(escaped_value)[1:-1], repr(escaped_value.encode())[2:-1]]
    assert not any(f in text or json.dumps(f)[1:-1] in text for f in forms) and text.count("[REDACTED]") == 3


def test_bytes_that_arent_utf8_are_redacted_as_printed():
    data = b"ab" + bytes([0xFF]) + b"cd" + bytes([0xFE]) + b"ef" + bytes([0xFF]) + b"gh"
    secret_redaction.remember(data)
    shown = secret_redaction.redact(f"got {data!r} and {data}")
    assert repr(data)[2:-1] not in shown and shown == "got b'[REDACTED]' and b'[REDACTED]'"


def test_an_empty_folder_goes_without_recurse_but_never_a_file_that_arrived(tmp_path, monkeypatch):
    """rm without recurse removes a folder only while it is empty: a file written into it meanwhile stays."""
    folder = tmp_path / "landing"
    folder.mkdir()
    real_rmdir = os.rmdir

    def file_arrives_first(path, *a, **kw):
        (Path(path) / "late.csv").write_text("new")
        return real_rmdir(path, *a, **kw)

    monkeypatch.setattr(os, "rmdir", file_arrives_first)
    out = Kernel().run(f"dbutils.fs.rm('file:{folder.as_posix()}')")
    assert out["status"] == "error" and out["outputs"][-1]["ename"] == "IsADirectoryError"
    assert (folder / "late.csv").read_text() == "new"
