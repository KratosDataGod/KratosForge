"""Snowflake's SQL API client and the connection check (SF-002), against a fake of the documented API:
statements are submitted, followed and cancelled as documented, no check sends a warehouse, and each
failed field says why and what to do."""

from __future__ import annotations

import json
from typing import Any

import pytest

from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr

HOST = "https://myorg-myaccount.snowflakecomputing.com"


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def answer(columns: list[str], data: list[list[Any]]) -> dict[str, Any]:
    return {"resultSetMetaData": {"rowType": [{"name": c} for c in columns]}, "data": data}


ME = answer(["KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES"],
            [["AB12345", "JDOE", "PUBLIC", json.dumps(["PUBLIC", "ANALYST"])]])


class FakeSnowflake:
    """Answers by statement text; records every request."""

    def __init__(self, replies: dict[str, Any] | None = None, status: int = 200):
        self.replies = replies or {}
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if self.status != 200:
            return Resp(self.status, {"code": "390144", "message": "JWT token is invalid.", "sqlState": "08001"})
        sql = body["statement"]
        for prefix, reply in self.replies.items():
            if sql.startswith(prefix):
                return reply(body) if callable(reply) else Resp(200, reply)
        return Resp(200, ME)


def client(fake: FakeSnowflake, method: str = "pat") -> sr.Client:
    provider = sa.TokenProvider("myorg-myaccount", method, lambda: ("tok-abc", None))
    return sr.Client(provider, http=fake, sleep=lambda s: None)


CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "analyst",
        "database": "SALES", "schema": "raw", "warehouse": "DEV_WH", "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret


def full_fake() -> FakeSnowflake:
    return FakeSnowflake({
        "SHOW DATABASES": answer(["name"], [["SALES"], ["SALES_ARCHIVE"]]),
        "SHOW SCHEMAS": answer(["name"], [["RAW"]]),
        "SHOW WAREHOUSES": answer(["name", "state", "size"], [["DEV_WH", "SUSPENDED", "X-Small"]]),
    })


def test_a_good_connection_passes_every_field_and_never_sends_a_warehouse():
    fake = full_fake()
    out = sr.check_connection(CONN, client(fake))
    assert out["ok"] and out["signed_in"] and not out["problems"]
    assert [c["field"] for c in out["checks"]] == ["sign-in", "role", "database", "schema", "warehouse"]
    assert out["identity"] == {"account": "AB12345", "user": "JDOE", "default_role": "PUBLIC",
                               "available_roles": ["PUBLIC", "ANALYST"]}
    assert all("warehouse" not in c["body"] for c in fake.calls)  # nothing resumes
    assert all(c["body"].get("role") == "ANALYST" for c in fake.calls[1:])  # SHOW runs as the profile's role
    assert "wasn't resumed" in out["checks"][-1]["detail"] and "(suspended, X-Small)" in out["checks"][-1]["detail"]


def test_requests_go_to_the_documented_api_with_the_sign_in_headers():
    fake = full_fake()
    sr.check_connection(CONN, client(fake))
    first = fake.calls[0]
    assert first["method"] == "POST" and first["url"].startswith(HOST + "/api/v2/statements?requestId=")
    assert first["headers"]["Authorization"] == "Bearer tok-abc"
    assert first["headers"]["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert first["body"]["timeout"] == sr.STATEMENT_TIMEOUT


def test_a_role_not_granted_fails_by_name_and_shows_run_as_the_default_role():
    fake = full_fake()
    out = sr.check_connection(CONN | {"role": "SYSADMIN"}, client(fake))
    assert not out["ok"]
    assert out["problems"][0]["field"] == "role" and "SYSADMIN isn't granted to JDOE" in out["problems"][0]["message"]
    assert all("role" not in c["body"] for c in fake.calls)
    assert "your default role" in out["checks"][2]["detail"]


def test_a_missing_database_skips_the_schema_and_says_what_to_ask_for():
    fake = full_fake()
    fake.replies["SHOW DATABASES"] = answer(["name"], [])
    out = sr.check_connection(CONN, client(fake))
    fields = {p["field"]: p for p in out["problems"]}
    assert set(fields) == {"database", "schema"}
    assert "can't see it" in fields["database"]["message"] and "USAGE" in fields["database"]["fix"]
    assert not any(c["body"]["statement"].startswith("SHOW SCHEMAS") for c in fake.calls)


def test_like_patterns_and_names_are_escaped():
    fake = full_fake()
    fake.replies["SHOW DATABASES"] = answer(["name"], [["MY_DB'X"]])
    fake.replies["SHOW SCHEMAS"] = answer(["name"], [["RAW"]])
    sr.check_connection(CONN | {"database": "MY_DB'X", "warehouse": ""}, client(fake))
    sqls = [c["body"]["statement"] for c in fake.calls]
    assert "SHOW DATABASES LIKE 'MY_DB''X'" in sqls
    assert sr._literal("a\\b") == "'a\\\\b'"
    assert "SHOW SCHEMAS LIKE 'raw' IN DATABASE \"MY_DB'X\"" in sqls


def test_a_role_matches_exactly_before_ignoring_case():
    fake = FakeSnowflake({"SELECT": answer(["KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES"],
                                           [["A", "U", "PUBLIC", json.dumps(["analyst", "Analyst"])]])})
    out = sr.check_connection({"account": "myorg-myaccount", "role": "Analyst"}, client(fake))
    assert out["checks"][1]["detail"] == "Role Analyst is available to you."


def test_a_sign_in_failing_mid_check_is_a_problem_not_a_crash():
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise sa.SnowflakeAuthError("The environment variable X isn't set on this PC.")
        return "tok", None
    provider = sa.TokenProvider("myorg-myaccount", "keypair", fetch, clock=lambda: 0.0)
    provider._expires = -1.0  # every call after the first makes a new token
    fake = full_fake()
    orig = provider.token

    def token():
        provider._token = "" if calls else provider._token
        return orig()
    provider.token = token
    out = sr.check_connection(CONN, sr.Client(provider, http=fake, sleep=lambda s: None))
    assert out["signed_in"] and not out["ok"]
    assert any(p["field"] == "show" and "isn't set" in p["message"] for p in out["problems"])


def test_a_name_matches_exactly_or_by_case_when_only_one_does():
    rows = [{"name": "Sales"}, {"name": "SALES"}]
    assert sr._find(rows, "SALES") == {"name": "SALES"}
    assert sr._find(rows, "sales") is None  # two differ only by case: no guess
    assert sr._find([{"name": "SALES"}], "sales") == {"name": "SALES"}


def test_a_refused_sign_in_is_one_problem_without_the_token():
    out = sr.check_connection(CONN, client(FakeSnowflake(status=401)))
    assert not out["signed_in"] and not out["ok"]
    [problem] = out["problems"]
    assert problem["field"] == "sign-in" and "HTTP 401, code 390144" in problem["message"]
    assert "Check the connection's user name" in problem["message"] and "tok-abc" not in json.dumps(out)


def test_a_refused_pat_is_not_sent_again():
    fake = FakeSnowflake(status=401)
    c = client(fake)
    with pytest.raises(sr.SnowflakeError):
        c.statement("SELECT 1")
    with pytest.raises(sa.SnowflakeAuthError, match="refused this programmatic access token"):
        c.statement("SELECT 1")
    assert len(fake.calls) == 1


def test_a_sign_in_that_cant_be_made_is_reported_not_raised():
    out = sr.check_connection(CONN | {"credential": {"method": "oauth-local"}})
    assert out["problems"][0]["field"] == "sign-in" and "isn't available" in out["problems"][0]["message"]


def test_a_running_statement_is_followed_until_it_finishes():
    states = iter([Resp(202, {"statementHandle": "01b2-aa"}), Resp(200, answer(["N"], [["1"]]))])
    fake = FakeSnowflake()
    fake.request = lambda method, url, **kw: (fake.calls.append({"method": method, "url": url}), next(states))[1]
    assert sr.rows(client(fake).statement("SELECT 1")) == [{"n": "1"}]
    assert fake.calls[1] == {"method": "GET", "url": HOST + "/api/v2/statements/01b2-aa"}


def test_a_statement_still_running_at_the_limit_is_cancelled():
    fake, now = FakeSnowflake(), [0.0]

    def request(method, url, **kw):
        fake.calls.append({"method": method, "url": url})
        now[0] += 31
        return Resp(200, {}) if url.endswith("/cancel") else Resp(202, {"statementHandle": "01b2-aa"})
    fake.request = request
    c = sr.Client(sa.TokenProvider("myorg-myaccount", "pat", lambda: ("t", None)), http=fake,
                  sleep=lambda s: None, clock=lambda: now[0])
    with pytest.raises(sr.SnowflakeError, match="still running after 60 seconds, so it was cancelled"):
        c.statement("SELECT SYSTEM$WAIT(120)")
    assert fake.calls[-1] == {"method": "POST", "url": HOST + "/api/v2/statements/01b2-aa/cancel"}


def test_an_odd_handle_or_answer_is_an_error_not_a_url():
    fake = FakeSnowflake({"SELECT": lambda b: Resp(202, {"statementHandle": "../../x"})})
    with pytest.raises(sr.SnowflakeError, match="no statement handle"):
        client(fake).statement("SELECT 1")
    fake = FakeSnowflake({"SELECT": lambda b: Resp(200, "<html>")})
    with pytest.raises(sr.SnowflakeError, match="other than a JSON object"):
        client(fake).statement("SELECT 1")
    with pytest.raises(sr.SnowflakeError, match="no rows"):
        sr.rows({"data": "x"})
    with pytest.raises(sr.SnowflakeError, match="don't match its columns"):
        sr.rows(answer(["A", "B"], [["1"]]))


def test_a_sql_error_keeps_snowflakes_code_and_state():
    fake = FakeSnowflake({"SELECT": lambda b: Resp(422, {"code": "002003", "sqlState": "02000",
                                                        "message": "Object 'X' does not exist or not authorized."})})
    with pytest.raises(sr.SnowflakeError) as err:
        client(fake).statement("SELECT * FROM X")
    assert (err.value.status, err.value.code, err.value.sql_state) == (422, "002003", "02000")
    assert "does not exist or not authorized" in str(err.value)


def test_the_service_checks_only_snowflake_connections_and_is_a_read(monkeypatch):
    from fabric_desktop import audit
    from fabric_desktop import engines_service as es

    class Svc(es.EnginesMixin):
        def __init__(self, conn):
            self.conn = conn

        def _connection(self, key):
            return self.conn, {}, "p"

    seen = []
    monkeypatch.setattr(sr, "check_connection", lambda conn: seen.append(conn) or {"ok": True})
    out = Svc(CONN).snowflake_check("profile:p:sf")
    assert seen == [CONN] and out == {"ok": True}
    with pytest.raises(ValueError, match="isn't a Snowflake connection"):
        Svc({"platform": "databricks"}).snowflake_check("profile:p:db")
    assert "snowflake_check" in es.METHODS and "snowflake_check" in audit.READ_ONLY
