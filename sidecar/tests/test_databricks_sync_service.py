"""Workspaces > Databricks sync RPCs (engines_service.py): pairs are kept on this PC, compare only
reads, push and pull are audited and keep the last-sync state, and deletes need the second confirm."""

from __future__ import annotations

import pytest

from fabric_desktop import audit, connprofiles, server
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import databricks_workspace_sync as wsync

from test_databricks_discovery import HOST, TOKEN
from test_databricks_workspace_sync import REMOTE, FakeWorkspace

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "", "serverless": False, "warning": ""}]}
READS = {"databricks_sync_pairs", "databricks_sync_compare"}
WRITES = {"databricks_sync_pair_save", "databricks_sync_pair_remove", "databricks_sync_push", "databricks_sync_pull"}


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path):
        self._tmp = tmp_path
        self.ws = FakeWorkspace(exists=False)

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")

    def _sync_pairs(self):
        return wsync.Pairs(self._tmp / "sync" / "pairs.json")

    def _discovery_client(self, connection):
        return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), self.ws)


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)


@pytest.fixture
def svc(tmp_path):
    (tmp_path / "nb").mkdir()
    (tmp_path / "nb" / "load_orders.py").write_text("# Databricks notebook source\nprint(1)\n")
    return Svc(tmp_path)


def test_reads_skip_the_audit_and_writes_are_audited():
    assert READS | WRITES <= es.METHODS <= server.METHODS
    assert READS <= audit.READ_ONLY and not WRITES & audit.READ_ONLY


def test_pair_compare_push_and_the_state_is_kept(svc, tmp_path):
    pid = svc.databricks_sync_pair_save("sales-etl", "cli:dev", str(tmp_path / "nb"), REMOTE)["id"]
    assert svc.ws.calls == []   # pairing reads nothing in Databricks
    with pytest.raises(ValueError, match="already paired"):
        svc.databricks_sync_pair_save("again", "cli:dev", str(tmp_path / "nb"), REMOTE)
    assert svc.databricks_sync_compare(pid)["rows"][0]["action"] == "push"
    assert svc.databricks_sync_push(pid, ["load_orders.py"])["done"][0]["action"] == "pushed"
    assert svc.databricks_sync_pairs()["pairs"][0]["synced"] == 1
    assert svc.databricks_sync_compare(pid)["rows"][0]["action"] == "none"


def test_delete_needs_the_second_confirm_through_the_rpc(svc, tmp_path):
    pid = svc.databricks_sync_pair_save("sales-etl", "cli:dev", str(tmp_path / "nb"), REMOTE)["id"]
    svc.databricks_sync_push(pid, ["load_orders.py"])
    (tmp_path / "nb" / "load_orders.py").unlink()
    with pytest.raises(wsync.SyncError):
        svc.databricks_sync_push(pid, [], deletes=["load_orders.py"])
    assert svc.databricks_sync_push(pid, [], deletes=["load_orders.py"], confirm_deletes=True)["done"][0]["action"] == "deleted"


def test_unknown_connection_and_pair_are_refused(svc, tmp_path):
    with pytest.raises(ValueError):
        svc.databricks_sync_pair_save("x", "cli:gone", str(tmp_path / "nb"), REMOTE)
    with pytest.raises(wsync.SyncError):
        svc.databricks_sync_compare("s-nope")


def test_removing_a_pair_touches_no_file(svc, tmp_path):
    pid = svc.databricks_sync_pair_save("sales-etl", "cli:dev", str(tmp_path / "nb"), REMOTE)["id"]
    svc.databricks_sync_pair_remove(pid)
    assert svc.databricks_sync_pairs()["pairs"] == [] and (tmp_path / "nb" / "load_orders.py").exists() and svc.ws.calls == []
