"""Snowflake's SQL API client: the paths the other tests leave out (coverage pass 20). The app's own HTTP
session by default, a network failure, a refusal that isn't JSON, cancel's own refusals, roles that
aren't a JSON list, and a connection that names no role."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr
from tests.test_snowflake_remote import FakeSnowflake, Resp, answer, client

HOST = "https://myorg-myaccount.snowflakecomputing.com"


def provider() -> sa.TokenProvider:
    return sa.TokenProvider("myorg-myaccount", "pat", lambda: ("tok-abc", None))


def test_without_an_http_object_the_client_uses_the_apps_shared_session(monkeypatch):
    from fabric_desktop import http_session

    shared = object()
    monkeypatch.setattr(http_session, "session", lambda: shared)
    c = sr.Client(provider())
    assert c._http is shared
    assert c.host == HOST


def test_a_network_failure_names_the_host_and_never_the_token():
    class Down:
        def request(self, *a, **kw):
            raise ConnectionError("tok-abc could not connect")

    c = sr.Client(provider(), http=Down(), sleep=lambda s: None)
    with pytest.raises(sr.SnowflakeError) as caught:
        c.statement("SELECT 1")
    text = str(caught.value)
    assert text.startswith(f"Couldn't reach {HOST} (ConnectionError).")
    assert "Check this PC's network" in text
    assert "tok-abc" not in text


def test_a_refusal_that_isnt_json_still_gives_the_status_and_hint():
    fake = FakeSnowflake({"SELECT": lambda b: Resp(403, "<html>blocked</html>")})
    with pytest.raises(sr.SnowflakeError) as caught:
        client(fake).statement("SELECT 1")
    assert caught.value.status == 403 and caught.value.code == "" and caught.value.sql_state == ""
    assert str(caught.value).startswith("Snowflake refused the request (HTTP 403). A network policy may block")


def test_a_refusal_whose_json_isnt_an_object_has_no_code():
    fake = FakeSnowflake({"SELECT": lambda b: Resp(404, ["not", "an", "object"])})
    with pytest.raises(sr.SnowflakeError) as caught:
        client(fake).statement("SELECT 1")
    assert str(caught.value) == ("Snowflake refused the request (HTTP 404). "
                                 "Check the account identifier in the connection.")


def test_cancel_refuses_a_handle_that_isnt_one_without_sending_anything():
    fake = FakeSnowflake()
    with pytest.raises(sr.SnowflakeError, match="Not a Snowflake statement handle"):
        client(fake).cancel("../statements")
    with pytest.raises(sr.SnowflakeError, match="Not a Snowflake statement handle"):
        client(fake).cancel("")
    assert fake.calls == []


@pytest.mark.parametrize("status", [200, 422])
def test_cancel_accepts_done_or_already_finished(status):
    fake = FakeSnowflake()
    fake.request = lambda method, url, **kw: (fake.calls.append(url), Resp(status, {}))[1]
    client(fake).cancel("01b2-aa")
    assert fake.calls == [HOST + "/api/v2/statements/01b2-aa/cancel"]


def test_a_refused_cancel_is_an_error_with_snowflakes_code():
    fake = FakeSnowflake()
    fake.request = lambda method, url, **kw: Resp(500, {"code": "000605", "message": "Internal error"})
    with pytest.raises(sr.SnowflakeError) as caught:
        client(fake).cancel("01b2-aa")
    assert caught.value.status == 500 and caught.value.code == "000605"
    assert str(caught.value) == "Snowflake refused the request (HTTP 500, code 000605). Internal error"


@pytest.mark.parametrize("roles", ["[not json", json.dumps({"PUBLIC": 1}), None])
def test_available_roles_that_arent_a_json_list_are_none(roles):
    me = answer(["KF_ACCOUNT", "KF_USER", "KF_ROLE", "KF_ROLES"], [["AB12345", "JDOE", "PUBLIC", roles]])
    fake = FakeSnowflake({"SELECT CURRENT_ACCOUNT": me})
    out = sr.check_connection({"account": "myorg-myaccount", "role": "PUBLIC"}, client(fake))
    assert out["identity"]["available_roles"] == []
    assert out["problems"] == [{"field": "role", "message": "Role PUBLIC isn't granted to JDOE.",
                                "fix": "Ask a Snowflake admin to grant the role, or pick one of your available roles."}]


def test_a_connection_without_a_role_checks_as_the_default_role_and_sends_none():
    fake = FakeSnowflake({"SHOW WAREHOUSES": answer(["name", "state", "size"], [["DEV_WH", "STARTED", ""]])})
    out = sr.check_connection({"account": "myorg-myaccount", "warehouse": "DEV_WH"}, client(fake))
    assert out["ok"]
    assert [c["field"] for c in out["checks"]] == ["sign-in", "warehouse"]
    assert out["checks"][1]["detail"] == ("Warehouse DEV_WH is visible to your default role (started). "
                                          "It wasn't resumed. Running queries on it uses credits.")
    assert all("role" not in call["body"] for call in fake.calls)
