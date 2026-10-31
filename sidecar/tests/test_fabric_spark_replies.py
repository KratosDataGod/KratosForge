"""Fabric Spark replies that aren't Livy's documented JSON (FS-HARDEN, #1281), against the fake Livy in
test_fabric_spark.py: nothing reaches Fabric. A broken reply is an error that names the HTTP status,
never an empty answer, and a blank cell sends nothing."""

from __future__ import annotations

import pytest

import test_fabric_spark as t
from fabric_desktop.engines import fabric_spark as fs
from fabric_desktop.engines.contract import CellRequest, Unsupported

PAGE = b"<html>Sign in to the proxy " + b"secret-" + b"cookie</html>"


class _Page:
    def __init__(self, status=200, content=PAGE, body=None):
        self.status_code, self.content, self._body = status, content, body

    def json(self):
        if self._body is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _broken(livy, method, path, reply):
    real = livy.request

    def request(m, url, **kw):
        if m == method and url[len(t.BASE):] == path:
            livy.calls.append((m, url[len(t.BASE):], kw.get("json")))
            return reply
        return real(m, url, **kw)
    livy.request = request


def _posts(livy, path):
    return sum(1 for m, p, _ in livy.calls if m == "POST" and p == path)


@pytest.mark.parametrize("reply", [_Page(), _Page(body=["id", "7"])], ids=["not-json", "not-an-object"])
def test_a_create_reply_that_isnt_livys_json_says_a_session_may_exist(reply):
    livy = t.FakeLivy()
    _broken(livy, "POST", "", reply)
    with pytest.raises(fs.FabricSparkError, match="Monitoring hub") as exc:
        t.open_session(livy)
    assert "secret" not in str(exc.value) and livy.deleted == []


def test_a_start_poll_that_isnt_livys_json_ends_the_session_and_names_the_status():
    livy = t.FakeLivy(start_polls=2)
    _broken(livy, "GET", "/7", _Page(status=203))
    with pytest.raises(fs.FabricSparkError, match=r"HTTP 203\)") as exc:
        t.open_session(livy)
    assert "secret" not in str(exc.value) and livy.deleted == ["7"]


@pytest.mark.parametrize("reply, words", [
    (_Page(), "something other than the JSON object"),
    (_Page(body={"state": "waiting"}), "didn't say which statement"),
    (_Page(body={"id": "../7", "state": "waiting"}), "didn't say which statement"),
], ids=["not-json", "no-id", "bad-id"])
def test_a_submit_reply_that_cant_be_followed_is_an_error_before_any_poll(reply, words):
    livy = t.FakeLivy()
    s = t.open_session(livy)
    _broken(livy, "POST", "/7/statements", reply)
    seen = []
    r = s.execute(CellRequest("print('hello')", "pyspark"), seen.append)
    assert r.status == "error" and seen == r.outputs
    evalue = r.outputs[0]["evalue"]
    assert words in evalue and "may still run" in evalue and "secret" not in evalue
    assert not any(m == "GET" and p.startswith("/7/statements/") for m, p, _ in livy.calls)
    assert not s._lock.locked() and s._statement == ""


def test_a_statement_poll_that_isnt_livys_json_stops_the_statement():
    livy = t.FakeLivy(polls=3)
    s = t.open_session(livy)
    _broken(livy, "GET", "/7/statements/0", _Page())
    r = s.execute(CellRequest("print('hello')", "pyspark"))
    assert r.status == "error" and livy.cancelled == ["0"]


def test_an_empty_reply_is_still_fine_where_livy_sends_none():
    livy = t.FakeLivy()
    s = t.open_session(livy)
    _broken(livy, "DELETE", "/7", _Page(content=b""))
    s.close()
    assert s.closed


@pytest.mark.parametrize("code", ["", "   ", "\n\t\n"])
@pytest.mark.parametrize("language, rows", [("pyspark", 0), ("sql", 0), ("sql", 10)])
def test_a_blank_cell_sends_nothing_to_fabric(code, language, rows):
    livy = t.FakeLivy()
    s = t.open_session(livy)
    with pytest.raises(Unsupported, match="nothing was sent"):
        s.execute(CellRequest(code, language, result_rows=rows))
    assert _posts(livy, "/7/statements") == 0 and not s._lock.locked()
