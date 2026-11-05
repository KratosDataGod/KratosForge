"""Monitor > Databricks (Preview, engines_service.py): databricks_runs and databricks_run read job runs
and Lakeflow updates for a connection key through the same client as discovery, GETs only."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, connprofiles, server
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql

import test_databricks_jobs as tj
from test_databricks_discovery import HOST, TOKEN, Resp

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "", "serverless": False, "warning": ""}]}


class Workspace(tj.FakeWorkspace):
    me = "dev@contoso.com"

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        if url.endswith("/scim/v2/Me"):
            self.calls.append((method, url[len(HOST):], {}))
            return Resp(body={"userName": self.me}) if self.me else Resp(500, {"message": "down"})
        return super().request(method, url, headers=headers, timeout=timeout, json=json, params=params)


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, ws=None):
        self._tmp = tmp_path
        self.ws = ws or Workspace()

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")

    def _discovery_client(self, connection):
        return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), self.ws)


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)
    monkeypatch.setattr(tj.dj.time, "time", lambda: tj.NOW)


def test_both_calls_are_read_only_rpc_methods():
    assert {"databricks_runs", "databricks_run"} <= es.METHODS <= server.METHODS
    assert {"databricks_runs", "databricks_run"} <= audit.READ_ONLY


def test_runs_are_mine_by_default_and_everyones_when_asked(tmp_path):
    svc = Svc(tmp_path)
    mine = svc.databricks_runs("cli:dev")
    assert mine["user"] == "dev@contoso.com" and {r["name"] for r in mine["rows"]} == {"nightly_sales", "orders_cdc"}
    everyone = svc.databricks_runs("cli:dev", mine=False)
    assert len(everyone["rows"]) == 3 and everyone["user"] == ""
    assert {m for m, _, _ in svc.ws.calls} == {"GET"}


def test_runs_say_when_the_user_cant_be_read(tmp_path):
    ws = Workspace()
    ws.me = ""
    out = Svc(tmp_path, ws).databricks_runs("cli:dev")
    assert out["rows"] == [] and out["error"].startswith("Couldn't read who you're signed in as")


def test_runs_say_when_sign_in_fails(tmp_path):
    class NoSignIn(Svc):
        def _discovery_client(self, connection):
            raise dba.DatabricksAuthError(f"Run databricks auth login ({TOKEN})")
    out = NoSignIn(tmp_path).databricks_runs("cli:dev")
    assert not out["signed_in"] and TOKEN not in out["error"]


def test_run_detail_is_redacted(tmp_path):
    out = Svc(tmp_path).databricks_run("cli:dev", "11")
    assert out["tasks"][1]["key"] == "gold_kpis" and TOKEN not in json.dumps(out)
