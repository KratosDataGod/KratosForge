"""notebookutils on this PC at its boundaries (mutation batch 162, a boundary re-score of batch 7,
docs/quality/mutation-testing.md): a head of 0 bytes reads nothing, a Key Vault reply of exactly 400 is an
error, a token that expires this second isn't valid, and a load error names up to exactly five files."""

from __future__ import annotations

import base64
import json
import types

import pytest
import requests

import test_notebookutils_fs as t
from fabric_desktop import http_session, lakehouse, notebookutils_local as nu
from tests.test_notebookutils_load_table import files, rows, run  # noqa: F401 - fixtures and helpers
from tests.test_spark_writer import lake  # noqa: F401 - a fixture


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = t.FakeStore({"Files/one.txt": b"hello"})
    monkeypatch.setattr(nu, "_store", lambda: s)
    monkeypatch.setattr(lakehouse, "write_guard", None)
    monkeypatch.setattr(lakehouse, "proof", None)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield s
    lakehouse.attach(None)


def test_a_head_of_zero_bytes_reads_nothing(store):
    fs = nu.build(nu.Host()).fs
    assert fs.head("Files/one.txt", 0) == ""
    assert store.calls == []  # not even the first byte is asked for
    assert fs.head("Files/one.txt", 1) == "h"


class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.headers: dict[str, str] = {}

    def json(self):
        return self._body


def test_a_key_vault_reply_of_exactly_400_is_an_error(monkeypatch):
    reply = _Reply(400, {"error": {"code": "BadParameter", "message": "no"}})
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: reply))
    creds = nu.build(nu.Host(token=lambda audience: "tok")).credentials
    with pytest.raises(requests.HTTPError, match="BadParameter"):
        creds.getSecret("kv1", "db-password")
    ok = _Reply(399, {"value": "v"})
    monkeypatch.setattr(http_session, "session", lambda: types.SimpleNamespace(get=lambda *a, **k: ok))
    assert creds.getSecret("kv1", "db-password") == "v"


def _jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def test_a_token_that_expires_this_second_isnt_valid(monkeypatch):
    monkeypatch.setattr(nu.time, "time", lambda: 1_800_000_000.0)
    creds = nu.build(nu.Host()).credentials
    assert creds.isValidToken(_jwt(1_800_000_000.0)) is False
    assert creds.isValidToken(_jwt(1_800_000_000.5)) is True


@pytest.mark.parametrize("count, more", [(5, False), (6, True)])
def test_a_load_error_names_up_to_five_files_and_says_and_more_after(files, count, more):  # noqa: F811
    folder = files.parent / "Files" / "in"
    for i in range(count):
        (folder / f"note{i}.txt").write_text("x\n", encoding="utf-8")
    before = rows(files, "sales")
    status, text = run("notebookutils.lakehouse.loadTable({'relativePath': 'Files/in', 'pathType': 'Folder', "
                       "'mode': 'Overwrite'}, 'sales')")
    assert status == "error" and "aren't .csv (" in text, text
    assert ("and more" in text) is more, text
    assert rows(files, "sales") == before
