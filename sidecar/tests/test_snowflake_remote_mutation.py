"""engines/snowflake_remote.py held to its exact numbers and words: the 30 second statement timeout and
60 second request timeout, which requests are sent again after a 503 (never a submit), a cancel that is
sent even after Cancel was pressed, how long a cancelled statement ran, and the cuts on Snowflake's
error fields. A scripted fake stands in for the SQL API; nothing reaches Snowflake."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr

HOST = "https://myorg-myaccount.snowflakecomputing.com"
HANDLE = "01b2c3d4-0000-1111-2222-333344445555"


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


class Script:
    """Answers each request with the next response given; records every request."""

    def __init__(self, *answers: Resp):
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, headers, timeout, data=None, **_):
        self.calls.append({"method": method, "path": url[len(HOST):], "timeout": timeout,
                           "body": json.loads(data) if data else None})
        return self.answers.pop(0)


def client(http: Script, clock=None) -> sr.Client:
    provider = sa.TokenProvider("myorg-myaccount", "pat", lambda: ("tok-abc", None))  # pragma: allowlist secret (a fake token for the test)
    kwargs = {"clock": clock} if clock else {}
    return sr.Client(provider, http=http, sleep=lambda s: None, **kwargs)


ME = {"resultSetMetaData": {"rowType": [{"name": c} for c in ("KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES")]},
      "data": [["AB12345", "JDOE", "PUBLIC", json.dumps(["PUBLIC"])]]}


def test_a_statement_waits_30_seconds_and_each_request_60_and_isnt_asynchronous_unless_asked():
    http = Script(Resp(200, {"data": []}))
    client(http).statement("SELECT 1")
    (call,) = http.calls
    assert sr.STATEMENT_TIMEOUT == 30 and call["body"]["timeout"] == 30
    assert call["timeout"] == 60
    assert call["path"].startswith("/api/v2/statements?requestId=") and "async" not in call["path"]


def test_a_submit_that_gets_a_503_is_never_sent_again():
    http = Script(Resp(503, {"message": "busy"}), Resp(200, {"data": []}))
    with pytest.raises(sr.SnowflakeError) as caught:
        client(http).statement("SELECT 1")
    assert caught.value.status == 503 and len(http.calls) == 1


def test_a_look_at_a_running_statement_is_sent_again_after_a_503():
    http = Script(Resp(202, {"statementHandle": HANDLE}), Resp(503, {}), Resp(200, {"data": [["1"]]}))
    assert client(http).statement("SELECT 1")["data"] == [["1"]]
    assert [c["method"] for c in http.calls] == ["POST", "GET", "GET"]


def test_a_partition_is_sent_again_after_a_503():
    http = Script(Resp(503, {}), Resp(200, {"data": [["2"]]}))
    assert client(http).partition(HANDLE, 1) == {"data": [["2"]]}
    assert [c["path"] for c in http.calls] == [f"/api/v2/statements/{HANDLE}?partition=1"] * 2


def test_a_partition_refused_or_with_a_bad_handle_raises():
    with pytest.raises(sr.SnowflakeError, match="^Not a Snowflake statement handle.$"):
        client(Script()).partition("bad handle", 1)
    with pytest.raises(sr.SnowflakeError) as caught:
        client(Script(Resp(404, {"message": "gone"}))).partition(HANDLE, 1)
    assert caught.value.status == 404


def test_a_cancel_is_sent_again_after_a_503_even_once_cancel_was_pressed():
    http = Script(Resp(503, {}), Resp(200, {}))
    c = client(http)
    c.stopped = lambda: True
    c.cancel(HANDLE)
    assert [c["path"] for c in http.calls] == [f"/api/v2/statements/{HANDLE}/cancel"] * 2


@pytest.mark.parametrize("max_wait, took", [(119.0, "119 seconds"), (120.0, "2 minutes"), (150.0, "2 minutes"),
                                            (61.0, "61 seconds")])
def test_a_statement_still_running_is_cancelled_and_says_how_long_it_ran(max_wait, took):
    now = [0.0]

    def clock():
        now[0] += 7.0
        return now[0]

    http = Script()

    def request(method, url, headers, timeout, data=None, **_):
        http.calls.append({"method": method, "path": url[len(HOST):]})
        return Resp(200, {}) if url.endswith("/cancel") else Resp(202, {"statementHandle": HANDLE})

    http.request = request
    with pytest.raises(sr.SnowflakeError) as caught:
        client(http, clock=clock).statement("SELECT 1", max_wait=max_wait)
    assert str(caught.value) == (f"The statement was still running after {took}, so it was cancelled. "
                                 "Narrow the query, or run it in Snowsight.")
    assert http.calls[-1]["path"] == f"/api/v2/statements/{HANDLE}/cancel"


def test_an_error_has_no_status_code_or_state_unless_given():
    err = sr.SnowflakeError("x")
    assert (err.status, err.code, err.sql_state) == (0, "", "")


def test_a_refusal_keeps_300_characters_of_the_message_with_the_cut_12_of_the_code_and_8_of_the_state():
    body = {"message": "m" * 400, "code": "C" * 20, "sqlState": "S" * 20}
    with pytest.raises(sr.SnowflakeError) as caught:
        client(Script(Resp(422, body))).statement("SELECT 1")
    err = caught.value
    assert (err.code, err.sql_state) == ("C" * 12, "S" * 8)
    assert str(err) == f"Snowflake refused the request (HTTP 422, code {'C' * 12}). {'m' * 299}…"


@pytest.mark.parametrize("status, hint", [
    (408, "The statement took too long and Snowflake stopped it. Check again in a minute."),
    (429, "Snowflake is limiting requests, so try again in a minute."),
    (409, ""), (430, ""),
])
def test_the_hints_for_a_timeout_and_a_throttle(status, hint):
    assert sr._hint(status) == hint


class Fake:
    """The connection check's statements, answered by their start."""

    def __init__(self, replies):
        self.replies = replies

    def statement(self, sql, role=""):
        for prefix, reply in self.replies.items():
            if sql.startswith(prefix):
                return reply
        return ME


def table(columns, data):
    return {"resultSetMetaData": {"rowType": [{"name": c} for c in columns]}, "data": data}


CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe"}


def test_a_check_names_the_account_and_who_signed_in():
    out = sr.check_connection(CONN, Fake({}))
    assert out["account"] == "myorg-myaccount"
    assert out["checks"][0] == {"field": "sign-in", "ok": True, "detail": "Signed in as JDOE."}


def test_a_found_schema_is_visible_and_a_missing_warehouse_fails():
    out = sr.check_connection({**CONN, "database": "SALES", "schema": "raw", "warehouse": "NOPE"}, Fake({
        "SHOW DATABASES": table(["name"], [["SALES"]]),
        "SHOW SCHEMAS": table(["name"], [["RAW"]]),
        "SHOW WAREHOUSES": table(["name"], []),
    }))
    checks = {c["field"]: c for c in out["checks"]}
    assert checks["schema"] == {"field": "schema", "ok": True, "detail": "Schema RAW is visible to your default role."}
    assert checks["warehouse"] == {"field": "warehouse", "ok": False,
                                   "detail": "Warehouse NOPE doesn't exist, or your default role can't use it."}
    assert out["ok"] is False and [p["field"] for p in out["problems"]] == ["warehouse"]
