"""Connections for the Data Factory page (DF-5): what uses each one, per environment, reads only."""

import base64
import json
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import audit, server
from fabric_desktop import fabric_connections_service as fcs
from fabric_desktop import schedule_service
from fabric_desktop.service import Service

SQL = "5a1c7d2e-0000-4000-8000-000000000001"
S3 = "5a1c7d2e-0000-4000-8000-000000000002"
GONE = "5a1c7d2e-0000-4000-8000-0000000000ff"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"


def _definition(*parts):
    return {"definition": {"parts": [{"path": f"p{i}.json", "payloadType": "InlineBase64",
                                      "payload": base64.b64encode(json.dumps(p).encode()).decode()} for i, p in enumerate(parts)]}}


PIPELINE = {"properties": {"activities": [
    {"name": "Copy customers", "type": "Copy", "typeProperties": {
        "source": {"datasetSettings": {"externalReferences": {"connection": SQL.upper()}}}}},
    {"name": "Each", "type": "ForEach", "typeProperties": {"activities": [
        {"name": "Copy events", "type": "Copy", "externalReferences": {"connection": S3}}]}}]}}


class FakeFabric:
    calls: list = []

    def __init__(self, token):
        self.token = token()

    def list_workspaces(self):
        return [{"id": "ws-dev", "displayName": "Sales Dev"}, {"id": "ws-test", "displayName": "Sales Test"},
                {"id": "ws-x", "displayName": "Other"}]

    def list_connections(self):
        assert self.token == "conn-tok"
        return [{"id": SQL, "displayName": "sql-erp-prod", "connectivityType": "OnPremisesGateway", "gatewayId": "gw-1",
                 "privacyLevel": "Organizational",
                 "connectionDetails": {"type": "SQL", "path": f"erp.contoso.local;Password={TOKEN}"},
                 "credentialDetails": {"credentialType": "Basic"}},
                {"id": S3, "displayName": "s3-events", "connectivityType": "ShareableCloud",
                 "connectionDetails": {"type": "AmazonS3", "path": "https://events.s3.amazonaws.com"}},
                {"id": "web", "displayName": "web-fx-rates", "connectionDetails": {"type": "Web"}}]

    def list_items(self, ws):
        return {"ws-dev": [{"id": "pl1", "type": "DataPipeline", "displayName": "pl_customer_load"},
                           {"id": "cj1", "type": "CopyJob", "displayName": "cj_erp_customers"},
                           {"id": "nb1", "type": "Notebook", "displayName": "Load"}],
                "ws-test": [{"id": "pl2", "type": "DataPipeline", "displayName": "pl_customer_load"},
                            {"id": "pl3", "type": "DataPipeline", "displayName": "pl_broken"}],
                "ws-x": []}[ws]

    def get_item_definition(self, ws, item, fmt=None):
        FakeFabric.calls.append(item)
        if item == "pl3":
            raise RuntimeError("403")
        if item == "cj1":
            return _definition({"properties": {"source": {"connectionSettings": {"externalReferences": {"connection": SQL}}}}})
        if item == "pl2":
            return _definition({"properties": {"activities": [{"externalReferences": {"connection": GONE}}]}})
        return _definition(PIPELINE, {"metadata": {"type": "DataPipeline"}})


class FakeAuth:
    def fabric_token(self):
        return "tok"

    def _token(self, scopes):
        assert scopes == fcs.CONNECTION_SCOPES
        return "conn-tok"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    FakeFabric.calls = []
    for mod in (fcs, schedule_service):
        monkeypatch.setattr(mod, "FabricClient", FakeFabric)
    monkeypatch.setattr(Service, "auth", property(lambda self: FakeAuth()))
    s = Service(root=tmp_path / "app")
    envs = {"prod": {}, "test": {"workspace_id": "ws-test"}, "dev": {"workspace_id": "ws-dev"}}
    monkeypatch.setattr(s, "_project", lambda: SimpleNamespace(environments=envs))
    return s


def _settled(svc, **kw):
    out = svc.fabric_connections(**kw)
    for _ in range(200):
        if not out["pending"]:
            return out
        time.sleep(0.02)
        out = svc.fabric_connections()
    raise AssertionError("scan never finished")


def test_each_connection_lists_what_uses_it_in_each_environment(svc):
    out = _settled(svc)
    assert [w["env"] for w in out["workspaces"]] == ["dev", "test"]  # prod has no workspace
    rows = {r["name"]: r for r in out["connections"]}
    assert list(rows) == ["s3-events", "sql-erp-prod", "web-fx-rates"]
    sql = rows["sql-erp-prod"]
    assert [(u["env"], u["item_name"], u["item_type"]) for u in sql["used_by"]] == [
        ("dev", "pl_customer_load", "pipeline"), ("dev", "cj_erp_customers", "copy job")]
    assert (sql["type"], sql["connectivity"], sql["gateway_id"], sql["credential_type"]) == (
        "SQL", "OnPremisesGateway", "gw-1", "Basic")
    assert [u["item_name"] for u in rows["s3-events"]["used_by"]] == ["pl_customer_load"]  # nested in a ForEach
    assert rows["web-fx-rates"]["used_by"] == []
    assert (out["items"], out["missed"], out["skipped"]) == (4, 1, 0)


def test_a_connection_nobody_here_can_use_is_missing(svc):
    out = _settled(svc)
    assert [(m["id"], [u["env"] for u in m["used_by"]]) for m in out["missing"]] == [(GONE, ["test"])]


def test_nothing_secret_comes_back(svc):
    out = _settled(svc)
    text = json.dumps(out)
    assert TOKEN not in text and "Password=" not in text.replace("Password=[removed]", "")


def test_the_scan_is_cached_until_refresh(svc):
    _settled(svc)
    reads = len(FakeFabric.calls)
    _settled(svc)
    assert len(FakeFabric.calls) == reads
    _settled(svc, refresh=True)
    assert len(FakeFabric.calls) == reads * 2


def test_without_environments_it_checks_the_schedules_workspaces(svc, monkeypatch):
    monkeypatch.setattr(svc, "_project", lambda: (_ for _ in ()).throw(FileNotFoundError("no project")))
    svc.set_workspace_mark("ws-dev", "pins", True)
    out = _settled(svc)
    assert [(w["id"], w["env"]) for w in out["workspaces"]] == [("ws-dev", "")]


def test_signed_out_says_so_without_a_scan(svc, monkeypatch):
    class NoAuth(FakeAuth):
        def fabric_token(self):
            raise RuntimeError("Sign in first")
    monkeypatch.setattr(Service, "auth", property(lambda self: NoAuth()))
    out = svc.fabric_connections()
    assert out["pending"] is False and "Sign in first" in out["error"] and FakeFabric.calls == []


def test_connection_ids_walks_every_level():
    assert fcs.connection_ids([{"a": {"externalReferences": {"connection": " X "}}}, {"externalReferences": {}}]) == {"x"}


def test_read_only_and_not_quick():
    assert "fabric_connections" in server.METHODS and "fabric_connections" in audit.READ_ONLY
    assert "fabric_connections" not in server.QUICK
