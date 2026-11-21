"""The shared connection result (PAR-003): each platform's own check, against that platform's existing
fakes, answers who, as what, what it may do, and each problem with a next step, in one shape. A refused
or expired credential names the platform's own reason and never carries the token."""

from __future__ import annotations

import dataclasses
import json

import pytest
from fake_http import Reply, session
from test_databricks_discovery import HOST as DBX_HOST
from test_databricks_discovery import TOKEN as DBX_TOKEN
from test_databricks_discovery import FakeWorkspace
from test_databricks_discovery import Resp as DbxResp
from test_snowflake_remote import CONN as SF_CONN
from test_snowflake_remote import FakeSnowflake, full_fake
from test_snowflake_remote import Resp as SfResp

from fabric_desktop import auth, connection_check as cc
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr
from fabric_desktop.fabric_api import FabricClient

# Shaped like a real token, so redaction is what keeps it out, not luck.
SF_TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".eyJ" + "pc3MiOiJTRiJ9" + "." + "c2ln" * 6
FABRIC_TOKEN = "eyJ" + "0eXAiOiJKV1QifQ" + ".eyJ" + "hdWQiOiJmYWJyaWMifQ" + "." + "ZmFi" * 6
SHARED = {"schema", "platform", "ok", "signed_in", "identity", "scope", "can", "problems", "native"}


def _assert_no_secret(out: dict, *secrets: str) -> None:
    text = json.dumps(out)
    for secret in secrets:
        assert secret not in text


def _assert_every_problem_has_a_next_step(out: dict) -> None:
    for p in out["problems"]:
        assert p["reason"] and p["fix"].endswith(".")
        assert ":" not in p["fix"] and ";" not in p["fix"]


# --- Snowflake ----------------------------------------------------------------------------------------

def sf_client(fake: FakeSnowflake, token: str = SF_TOKEN) -> sr.Client:
    return sr.Client(sa.TokenProvider("myorg-myaccount", "pat", lambda: (token, None)), http=fake, sleep=lambda s: None)


def test_snowflake_success_maps_identity_scope_and_keeps_native_fields():
    out = cc.check_snowflake(SF_CONN, sf_client(full_fake())).as_dict()
    assert set(out) == SHARED and out["platform"] == "snowflake" and out["schema"] == cc.SCHEMA
    assert out["ok"] and out["signed_in"] and out["problems"] == []
    assert out["identity"] == "JDOE" and out["scope"] == "role ANALYST"
    assert any("Database SALES" in c for c in out["can"]) and any("Warehouse DEV_WH" in c for c in out["can"])
    native = out["native"]
    assert (native["account"], native["role"], native["warehouse"]) == ("AB12345", "analyst", "DEV_WH")
    assert native["available_roles"] == ["PUBLIC", "ANALYST"] and native["default_role"] == "PUBLIC"
    assert "credential" not in json.dumps(native) and "env:X" not in json.dumps(out)


def test_snowflake_role_not_granted_runs_as_the_default_role_and_says_so():
    out = cc.check_snowflake(SF_CONN | {"role": "SYSADMIN"}, sf_client(full_fake())).as_dict()
    assert out["scope"] == "default role PUBLIC" and not out["ok"]
    [role] = [p for p in out["problems"] if p["field"] == "role"]
    assert "isn't granted" in role["reason"] and "grant the role" in role["fix"]


def test_snowflake_expired_token_names_snowflakes_reason_and_a_next_step_without_the_token():
    fake = FakeSnowflake()
    fake.request = lambda method, url, **kw: SfResp(401, {
        "code": "390114", "message": f"Authentication token has expired. Token {SF_TOKEN}", "sqlState": "08001"})
    out = cc.check_snowflake(SF_CONN, sf_client(fake)).as_dict()
    assert not out["signed_in"] and not out["ok"] and out["identity"] == "" and out["scope"] == ""
    [p] = out["problems"]
    assert p["field"] == "sign-in" and p["code"] == "390114"
    assert "Authentication token has expired" in p["reason"] and "Settings > Connections" in p["fix"]
    _assert_every_problem_has_a_next_step(out)
    _assert_no_secret(out, SF_TOKEN)


def test_snowflake_refused_key_pair_is_its_own_reason():
    out = cc.check_snowflake(SF_CONN, sf_client(FakeSnowflake(status=401))).as_dict()
    [p] = out["problems"]
    assert p["code"] == "390144" and "JWT token is invalid" in p["reason"]
    _assert_no_secret(out, SF_TOKEN)


# --- Databricks ---------------------------------------------------------------------------------------

def dbx_client(ws, fetch=lambda: (DBX_TOKEN, None), sign_in_again: str = "") -> dbsql.Client:
    return dbsql.Client(dba.TokenProvider(DBX_HOST, fetch, "test", sign_in_again=sign_in_again), ws)


@pytest.fixture(autouse=True)
def fresh_refusals(monkeypatch):
    """Databricks remembers refused tokens for the process: each test starts with none."""
    monkeypatch.setattr(dba, "_REFUSED", {})


def test_databricks_success_maps_identity_scope_and_keeps_the_host():
    out = cc.check_databricks({}, dbx_client(FakeWorkspace())).as_dict()
    assert set(out) == SHARED and out["ok"] and out["signed_in"]
    assert out["identity"] == "dev@contoso.com"
    assert out["scope"] == "workspace " + DBX_HOST.removeprefix("https://")
    assert "See 2 SQL warehouses." in out["can"] and "See 2 catalogs." in out["can"]
    assert any(c.startswith("See 3 clusters") for c in out["can"])
    assert out["native"]["host"] == DBX_HOST and out["native"]["metastore"] == "westeurope"
    assert out["native"]["suggested"]["warehouse_id"] == "wh-run"


def test_databricks_refused_token_names_the_workspaces_reason_and_a_next_step_without_the_token():
    ws = FakeWorkspace(refuse={"/api/2.0/preview/scim/v2/Me": 401})
    out = cc.check_databricks({}, dbx_client(ws)).as_dict()
    assert not out["signed_in"] and not out["ok"] and out["identity"] == "" and out["scope"] == ""
    sign_in = [p for p in out["problems"] if p["field"] == "sign-in"]
    assert [p["field"] for p in out["problems"]] == ["sign-in"] and sign_in[0]["code"] == "HTTP 401"
    assert "Databricks refused the request (HTTP 401)" in sign_in[0]["reason"]
    assert "databricks auth login" in sign_in[0]["fix"]
    assert out["native"]["host"] == DBX_HOST
    _assert_every_problem_has_a_next_step(out)
    _assert_no_secret(out, DBX_TOKEN)


def test_databricks_expired_cli_sign_in_keeps_the_cli_step():
    ws = FakeWorkspace()
    ws.request = lambda method, url, **kw: DbxResp(401, {"message": f"Token expired {DBX_TOKEN}"})
    again = "Run databricks auth login --host " + DBX_HOST + " again."
    out = cc.check_databricks({}, dbx_client(ws, sign_in_again=again)).as_dict()
    sign_in = [p for p in out["problems"] if p["field"] == "sign-in"]
    assert sign_in and "Databricks refused the sign-in (HTTP 401)" in sign_in[0]["reason"] and again in sign_in[0]["reason"]
    _assert_no_secret(out, DBX_TOKEN)


def test_databricks_sign_in_that_cant_be_had_is_one_problem_and_nothing_is_sent():
    def fail():
        raise dba.DatabricksAuthError("The environment variable DBX_PAT isn't set on this PC.")
    ws = FakeWorkspace()
    out = cc.check_databricks({}, dbx_client(ws, fetch=fail)).as_dict()
    assert ws.calls == [] and not out["signed_in"]
    [p] = out["problems"]
    assert p["field"] == "sign-in" and "DBX_PAT isn't set" in p["reason"]


def test_databricks_connection_whose_sign_in_cant_be_built_says_why():
    out = cc.check_databricks({"platform": "databricks", "host": DBX_HOST,
                               "credential": {"method": "pat", "secret": "env:KF_NO_SUCH_VAR"}}).as_dict()  # pragma: allowlist secret
    assert not out["signed_in"] and out["problems"][0]["field"] == "sign-in"
    assert out["native"]["host"] == DBX_HOST


def test_databricks_a_part_refused_is_its_own_problem_and_the_rest_still_count():
    ws = FakeWorkspace(refuse={"/api/2.1/clusters/list": 403})
    out = cc.check_databricks({}, dbx_client(ws)).as_dict()
    assert out["signed_in"] and not out["ok"]
    [p] = out["problems"]
    assert p["field"] == "clusters" and p["code"] == "HTTP 403" and "workspace admin" in p["fix"]
    assert "See 2 SQL warehouses." in out["can"]
    _assert_no_secret(out, DBX_TOKEN)


# --- Fabric -------------------------------------------------------------------------------------------

WS = [{"id": "a", "displayName": "Sales - Dev", "capacityId": "CAP1"}, {"id": "b", "displayName": "My workspace"}]
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"


def fabric_client(token_provider, replies) -> FabricClient:
    http, _ = session(replies)
    return FabricClient(token_provider, session=http, sleep=lambda s: None)


def test_fabric_success_maps_identity_scope_and_keeps_the_tenant():
    client = fabric_client(lambda: FABRIC_TOKEN, [Reply(200, {"value": WS})])
    out = cc.check_fabric("pat@contoso.com", TENANT, client.list_workspaces,
                          lambda: [{"id": "cap1", "state": "Active"}]).as_dict()
    assert set(out) == SHARED and out["ok"] and out["signed_in"]
    assert out["identity"] == "pat@contoso.com" and out["scope"] == "2 Fabric workspaces"
    assert "You can see 2 workspaces." in out["can"]
    assert any("running capacity" in c for c in out["can"])
    assert out["native"] == {"tenant": TENANT, "workspaces": 2}


def test_fabric_expired_sign_in_names_entras_code_and_a_next_step_without_the_token():
    def expired():
        raise auth.SignInError("AADSTS700082", f"The refresh token has expired due to inactivity. {FABRIC_TOKEN}",
                               "invalid_grant")
    client = fabric_client(expired, [])
    out = cc.check_fabric("pat@contoso.com", TENANT, client.list_workspaces).as_dict()
    assert not out["signed_in"] and not out["ok"] and out["identity"] == "" and out["scope"] == ""
    [p] = out["problems"]
    assert p["field"] == "sign-in" and p["code"] == "AADSTS700082"
    assert "refresh token has expired" in p["reason"] and p["fix"] == cc.FABRIC_ENDED_FIX
    assert out["native"]["tenant"] == TENANT
    _assert_every_problem_has_a_next_step(out)
    _assert_no_secret(out, FABRIC_TOKEN)


def test_fabric_refused_token_names_fabrics_reason_without_the_token():
    replies = [Reply(401, {"errorCode": "TokenExpired", "message": f"Access token has expired. {FABRIC_TOKEN}"})] * 2
    client = fabric_client(lambda: FABRIC_TOKEN, replies)
    out = cc.check_fabric("pat@contoso.com", TENANT, client.list_workspaces).as_dict()
    [p] = out["problems"]
    assert p["field"] == "sign-in" and p["code"] == "HTTP 401 TokenExpired"
    assert "failed with 401" in p["reason"] and p["fix"] == cc.FABRIC_SIGN_IN_FIX
    assert not out["signed_in"]
    _assert_no_secret(out, FABRIC_TOKEN)


def test_fabric_conditional_access_refusal_points_at_the_it_admin():
    def blocked():
        raise auth.SignInError("AADSTS53003", "Access has been blocked by Conditional Access policies.")
    out = cc.check_fabric("pat@contoso.com", TENANT, fabric_client(blocked, []).list_workspaces).as_dict()
    assert out["problems"][0]["fix"] == cc.FABRIC_BLOCKED_FIX


def test_fabric_headless_expiry_keeps_its_own_words():
    def required():
        raise auth.SignInRequired(auth.SIGNED_OUT_HEADLESS)
    out = cc.check_fabric("pat@contoso.com", TENANT, fabric_client(required, []).list_workspaces).as_dict()
    [p] = out["problems"]
    assert p["field"] == "sign-in" and p["reason"] == auth.SIGNED_OUT_HEADLESS


def test_fabric_signed_out_asks_for_sign_in_and_calls_nothing():
    out = cc.check_fabric(None, None, lambda: pytest.fail("no Fabric call when signed out")).as_dict()
    assert not out["signed_in"]
    [p] = out["problems"]
    assert p["field"] == "sign-in" and "Sign in to Fabric" in p["fix"]


def test_fabric_forbidden_is_about_access_not_sign_in():
    client = fabric_client(lambda: FABRIC_TOKEN, [Reply(403, {"errorCode": "InsufficientPrivileges", "message": "No."})])
    out = cc.check_fabric("pat@contoso.com", TENANT, client.list_workspaces).as_dict()
    assert out["signed_in"] and out["problems"][0]["field"] == "workspaces"
    assert out["problems"][0]["fix"] == cc.FABRIC_ACCESS_FIX


# --- The type ----------------------------------------------------------------------------------------

def test_a_problem_without_a_next_step_or_an_unknown_platform_is_refused():
    with pytest.raises(ValueError, match="next step"):
        cc.ConnectionCheck("fabric", False, problems=(cc.Problem("sign-in", "Refused.", ""),))
    with pytest.raises(ValueError, match="unknown platform"):
        cc.ConnectionCheck("bigquery", False)


def test_the_shared_part_is_identity_scope_and_problems_only():
    """Section D item 3 of the parity assessment: roles, warehouses, hosts and tenants stay native."""
    names = {f.name for f in dataclasses.fields(cc.ConnectionCheck)}
    assert names == {"platform", "signed_in", "identity", "scope", "can", "problems", "native"}


def test_a_secret_in_a_native_field_is_removed_on_the_way_out():
    check = cc.ConnectionCheck("snowflake", True, native={"note": f"Bearer {SF_TOKEN}", "token": "x"})
    out = check.as_dict()
    _assert_no_secret(out, SF_TOKEN)
    assert out["native"]["token"] == "[removed]"


def test_databricks_sign_in_lost_mid_check_is_a_sign_in_problem_not_an_access_one():
    ws = FakeWorkspace(refuse={"/api/2.0/sql/warehouses": 401})
    out = cc.check_databricks({}, dbx_client(ws)).as_dict()
    assert not out["signed_in"]
    assert [p["field"] for p in out["problems"]] == ["sign-in"]
    _assert_no_secret(out, DBX_TOKEN)
