"""A secret a notebook reads in its own kernel process is masked by the engine too (WR-426).

The kernel process passes each value it remembers to the engine over the pipe between them, so
receipt exports built in the engine show it as [REDACTED]. The value is never written to the log or
to a file on the way.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

from fabric_desktop import receipts, secret_redaction
from fabric_desktop.kernel_proc import ProcessKernel
from fabric_desktop.secret_redaction import REDACTED

# Made-up values, only for these tests.
VALUE = "Zq8-made-up-vault-value-41"  # pragma: allowlist secret (made up)
OTHER = "Mv3-another-made-up-27"  # pragma: allowlist secret (made up)
SIDECAR = str(Path(__file__).resolve().parent.parent)

# Starts kernel_host with Key Vault answered by a stub: the value comes from the environment, so
# no file under tmp_path holds it.
BOOTSTRAP = """\
import os

from fabric_desktop import http_session, kernel_host


class Resp:
    status_code = 200

    def json(self):
        return {"value": os.environ["WR426_MADE_UP_VALUE"]}


class Session:
    def get(self, url, params=None, headers=None, timeout=None):
        return Resp()


http_session.session = lambda: Session()
kernel_host.main()
"""

CELL = """\
import logging, sys, threading
v = dbutils.secrets.get("made-up-vault", "made-up-key")
print(v)
logging.getLogger("wr426").warning("the key is %s", v)
t = threading.Thread(target=lambda: print("thread", v, file=sys.stderr))
t.start(); t.join()
"""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(secret_redaction, "_forward", None, raising=False)
    secret_redaction.forget_all()
    yield
    secret_redaction.forget_all()


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    script = tmp_path / "bootstrap.py"
    script.write_text(BOOTSTRAP, encoding="utf-8")
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))  # the kernel's overflow folder

    def env() -> dict[str, str]:
        return dict(os.environ, PYTHONPATH=SIDECAR, PYTHONUNBUFFERED="1", TMPDIR=str(temp),
                    WR426_MADE_UP_VALUE=VALUE)

    k = ProcessKernel(token=lambda audience: "made-up-token", command=[sys.executable, str(script)], env=env)
    yield k
    k.close()


def _receipt(text: str) -> dict:
    return receipts.new_receipt(
        "proof",
        notebook={"name": "Sales", "parameters": {"key": text}, "code": {"revision": "ab" * 32, "cells": []}},
        verification={"verdict": "match", "text": f"Outputs match ({text})", "level": "rows"},
        limitations=[f"the proof used {text}"],
    )


def _files_holding(root: Path, value: str) -> list[str]:
    needle = value.encode("utf-8")
    return [str(p) for p in root.rglob("*") if p.is_file() and needle in p.read_bytes()]


def test_a_secret_read_in_the_kernel_process_is_masked_in_receipt_exports(kernel, tmp_path, capfd):
    assert not secret_redaction.active()  # the engine itself never read it
    out = kernel.run(CELL)
    assert out["status"] == "ok", out
    assert VALUE not in str(out["outputs"]) and REDACTED in str(out["outputs"])

    body = _receipt(VALUE)
    rid = receipts.receipt_id(body)
    md, page = receipts.to_markdown(body, rid), receipts.to_html(body, rid)
    assert VALUE not in md and VALUE not in page
    assert f"the proof used {REDACTED}" in md and f"the proof used {REDACTED}" in page
    # The receipt itself keeps what it was given; only exports mask.
    assert body["limitations"] == [f"the proof used {VALUE}"]

    (tmp_path / "receipt.md").write_text(md, encoding="utf-8")
    (tmp_path / "receipt.html").write_text(page, encoding="utf-8")
    kernel.close()
    logged = capfd.readouterr()
    assert VALUE not in logged.out and VALUE not in logged.err
    assert "the key is [REDACTED]" in logged.err + str(out["outputs"])
    assert "thread [REDACTED]" in logged.err
    assert _files_holding(tmp_path, VALUE) == []


def test_the_engines_own_log_masks_a_value_the_kernel_process_read(kernel, capfd):
    assert kernel.run('dbutils.secrets.get("made-up-vault", "made-up-key")')["status"] == "ok"
    log = io.StringIO()
    stream = secret_redaction.RedactedStream(log)
    stream.write(f"engine line with {VALUE}\n")
    stream.flush()
    assert log.getvalue() == f"engine line with {REDACTED}\n"
    assert VALUE not in capfd.readouterr().err


def test_a_value_reaches_the_engine_before_the_cell_ends(kernel):
    # The value is sent as the kernel remembers it, ahead of the answer that says the cell is done.
    snap = kernel.run('v = dbutils.secrets.get("made-up-vault", "made-up-key")\nlen(v)')
    assert snap["status"] == "ok"
    assert secret_redaction.redact_all(VALUE) == REDACTED


def test_wire_round_trips_text_and_bytes():
    assert secret_redaction.wire(VALUE) == {"text": VALUE}
    sent = secret_redaction.wire(OTHER.encode("utf-8"))
    assert sent == {"bytes": base64.b64encode(OTHER.encode("utf-8")).decode("ascii")}
    assert secret_redaction.wire(42) is None
    secret_redaction.remember_wire(sent)
    assert secret_redaction.redact_all(f"b'{OTHER}'") == f"b'{REDACTED}'"


@pytest.mark.parametrize("message", [None, "text", [], {}, {"text": 5}, {"bytes": "not base64!"}, {"bytes": 7}])
def test_a_malformed_value_message_is_ignored(message):
    secret_redaction.remember_wire(message)
    assert not secret_redaction.active()


def test_only_newly_remembered_values_are_forwarded():
    sent: list = []
    secret_redaction.forward_to(sent.append)
    secret_redaction.remember(VALUE)
    secret_redaction.remember(VALUE)
    secret_redaction.remember("ab")  # too short to mask, so never sent
    assert sent == [VALUE]


def test_a_forward_that_fails_still_remembers():
    def gone(value):
        raise OSError("the engine is gone")

    secret_redaction.forward_to(gone)
    secret_redaction.remember(VALUE)
    assert secret_redaction.redact(VALUE) == REDACTED


def test_the_engine_reader_remembers_a_value_message_and_answers_nothing():
    class Pipe(io.StringIO):
        written = ""

        def write(self, text):
            Pipe.written += text
            return len(text)

    class Proc:
        stdout = io.StringIO('{"secret": {"text": "%s"}}\n["not", "a", "message"]\n' % VALUE)
        stdin = Pipe()

        def wait(self):
            return 0

        def poll(self):
            return 0

    k = ProcessKernel(command=["unused"])
    proc = Proc()
    k._read(proc)  # type: ignore[arg-type]
    assert secret_redaction.redact(VALUE) == REDACTED
    assert Pipe.written == ""


# ---- limits on what the engine takes from a kernel's process


@pytest.mark.parametrize("value", ["1", "a", "abc", "", b"ab"])
def test_a_value_too_short_to_mask_is_dropped_on_the_wire_as_in_the_kernel(value):
    secret_redaction.remember_wire(secret_redaction.wire(value))
    assert not secret_redaction.active()
    assert secret_redaction.redact_all("1 a abc ab") == "1 a abc ab"


def test_a_value_past_the_length_cap_is_dropped():
    long = "Q" * (secret_redaction.WIRE_CHARS + 1)
    secret_redaction.remember_wire({"text": long})
    secret_redaction.remember_wire(secret_redaction.wire(long.encode()))
    assert not secret_redaction.active()
    at_cap = "Z" + "Q" * (secret_redaction.WIRE_CHARS - 1)
    secret_redaction.remember_wire({"text": at_cap})
    assert secret_redaction.redact(at_cap) == REDACTED


def test_values_past_the_count_cap_are_dropped(monkeypatch):
    monkeypatch.setattr(secret_redaction, "WIRE_VALUES", 3)
    for n in range(5):
        secret_redaction.remember_wire({"text": f"made-up-value-{n}"})
    assert secret_redaction.redact("made-up-value-2") == REDACTED
    assert secret_redaction.redact("made-up-value-3") == "made-up-value-3"
    secret_redaction.remember("made-up-value-4")  # the kernel's own values have no such cap
    assert secret_redaction.redact("made-up-value-4") == REDACTED


def test_bad_base64_is_dropped_without_a_word(capsys):
    secret_redaction.remember_wire({"bytes": "not base64 !!"})
    secret_redaction.remember_wire({"bytes": "A" * (secret_redaction.WIRE_LINE)})
    assert not secret_redaction.active()
    out = capsys.readouterr()
    assert out.out == out.err == ""


def test_the_engine_reader_does_not_decode_a_value_line_past_the_line_cap(monkeypatch):
    seen = []
    monkeypatch.setattr(secret_redaction, "remember_wire", seen.append)
    huge = '{"secret": {"text": "%s"}}\n' % ("Q" * secret_redaction.WIRE_LINE)
    small = '{"secret": {"text": "%s"}}\n' % VALUE

    class Proc:
        stdout = io.StringIO(huge + small)
        stdin = io.StringIO()

        def wait(self):
            return 0

        def poll(self):
            return 0

    ProcessKernel(command=["unused"])._read(Proc())  # type: ignore[arg-type]
    assert seen == [{"text": VALUE}]

