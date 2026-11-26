"""Snowflake's SQL API client and connection check (engines/snowflake_remote.py), mutation batch 351:
what is sent (statement timeout, request timeout), which calls are tried again (a submit never on a 5xx;
a look and a cancel are), a cancel that still goes out after the person pressed Cancel, the hints and
the cut of Snowflake's error text, and the wording of the check's results."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr

HANDLE = "01b2c3d4-0000-1111-2222-333344445555"


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


class Scripted:
    """Gives the scripted answers in turn and records each request."""

    def __init__(self, *answers: Any):
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        self.calls.append({"method": method, "url": url, "timeout": timeout, "body": json.loads(data) if data else None})
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def answer(columns: list[str], data: list[list[Any]]) -> dict[str, Any]:
    return {"resultSetMetaData": {"rowType": [{"name": c} for c in columns]}, "data": data}


OK = answer(["X"], [["1"]])


def client(http: Any) -> sr.Client:
    provider = sa.TokenProvider("myorg-myaccount", "pat", lambda: ("tok-abc", None))
    return sr.Client(provider, http=http, sleep=lambda s: None)


def test_a_statement_asks_for_30_seconds_and_each_request_waits_60():
    http = Scripted(Resp(200, OK))
    client(http).statement("SELECT 1")
    assert sr.STATEMENT_TIMEOUT == 30
    assert http.calls[0]["body"]["timeout"] == 30
    assert http.calls[0]["timeout"] == 60


def test_a_submit_that_gets_a_5xx_is_not_sent_again():
    http = Scripted(Resp(503, {"message": "busy"}), Resp(200, OK))
    with pytest.raises(sr.SnowflakeError, match="HTTP 503"):
        client(http).statement("SELECT 1")
    assert len(http.calls) == 1


def test_a_look_that_gets_a_5xx_is_tried_again():
    http = Scripted(Resp(202, {"statementHandle": HANDLE}), Resp(503, {"message": "busy"}), Resp(200, OK))
    assert client(http).statement("SELECT 1") == OK
    assert [c["method"] for c in http.calls] == ["POST", "GET", "GET"]


def test_a_cancel_that_gets_a_5xx_is_tried_again():
    http = Scripted(Resp(503, {"message": "busy"}), Resp(200, {}))
    client(http).cancel(HANDLE)
    assert len(http.calls) == 2


def test_a_cancel_still_goes_out_and_is_tried_again_after_the_person_pressed_cancel():
    http = Scripted(Resp(429, {"message": "slow down"}), Resp(200, {}))
    c = client(http)
    c.stopped = lambda: True
    c.cancel(HANDLE)
    assert [c["url"].endswith("/cancel") for c in http.calls] == [True, True]


def test_a_cancel_of_a_finished_statement_is_fine_and_a_refused_one_raises():
    client(Scripted(Resp(422, {"message": "already finished"}))).cancel(HANDLE)
    with pytest.raises(sr.SnowflakeError, match="HTTP 400"):
        client(Scripted(Resp(400, {"message": "no"}))).cancel(HANDLE)
    with pytest.raises(sr.SnowflakeError, match="Not a Snowflake statement handle"):
        client(Scripted()).cancel("../../x")


def test_a_network_failure_is_the_persons_message():
    http = Scripted(ConnectionError("down"))
    with pytest.raises(sr.SnowflakeError, match=r"Couldn't reach https://myorg-myaccount\.snowflakecomputing\.com \(ConnectionError\)"):
        client(http).statement("SELECT 1")


def test_an_error_made_here_has_no_status_or_code():
    err = sr.SnowflakeError("x")
    assert (err.status, err.code, err.sql_state) == (0, "", "")


@pytest.mark.parametrize("status, hint", [
    (401, "Check the connection's user name"),
    (403, "network policy"),
    (404, "Check the account identifier"),
    (408, "took too long"),
    (429, "Snowflake is limiting requests"),
])
def test_each_refusal_says_what_to_do(status, hint):
    assert hint in sr._hint(status)
    assert hint not in sr._hint(status + 1)


def test_snowflakes_code_and_state_are_cut_to_size():
    body = {"message": "m", "code": "C" * 20, "sqlState": "S" * 20}
    with pytest.raises(sr.SnowflakeError) as raised:
        client(Scripted(Resp(400, body))).statement("SELECT 1")
    err = raised.value
    assert err.code == "C" * 12 and err.sql_state == "S" * 8


ME = answer(["KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES"],
            [["AB12345", "JDOE", "PUBLIC", json.dumps(["PUBLIC", "ANALYST"])]])


class ByStatement:
    def __init__(self, replies: dict[str, Any]):
        self.replies = replies

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        sql = json.loads(data)["statement"]
        for prefix, reply in self.replies.items():
            if sql.startswith(prefix):
                return Resp(200, reply)
        return Resp(200, ME)


CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "analyst",
        "database": "SALES", "schema": "raw", "warehouse": "DEV_WH"}


def test_the_check_keeps_the_account_and_names_who_signed_in_and_what_it_found():
    fake = ByStatement({"SHOW DATABASES": answer(["name"], [["SALES"]]), "SHOW SCHEMAS": answer(["name"], [["RAW"]]),
                        "SHOW WAREHOUSES": answer(["name", "state", "size"], [["DEV_WH", "SUSPENDED", "X-Small"]])})
    out = sr.check_connection(CONN, client(fake))
    assert out["account"] == "myorg-myaccount"
    details = {c["field"]: c["detail"] for c in out["checks"]}
    assert details["sign-in"] == "Signed in as JDOE."
    assert details["schema"] == "Schema RAW is visible to role ANALYST."


def test_a_missing_warehouse_is_a_problem():
    fake = ByStatement({"SHOW DATABASES": answer(["name"], [["SALES"]]), "SHOW SCHEMAS": answer(["name"], [["RAW"]]),
                        "SHOW WAREHOUSES": answer(["name"], [])})
    out = sr.check_connection(CONN, client(fake))
    assert not out["ok"]
    assert [p["field"] for p in out["problems"]] == ["warehouse"]
    assert {c["field"]: c["ok"] for c in out["checks"]}["warehouse"] is False


def test_roles_that_arent_json_are_no_roles_not_a_crash():
    me = answer(["KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES"], [["AB12345", "JDOE", "PUBLIC", "not json ["]])
    fake = ByStatement({"SELECT CURRENT_ACCOUNT()": me})
    out = sr.check_connection({"platform": "snowflake", "account": "myorg-myaccount", "role": "analyst"}, client(fake))
    assert out["identity"]["available_roles"] == []
    assert [p["field"] for p in out["problems"]] == ["role"]
