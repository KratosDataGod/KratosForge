"""The Engine picker service (engines_service.py), the paths the main tests don't reach: where it keeps
its files, damaged profiles and unknown connections, billing that can't sign in, a session that
fails to open or is still opening, a cell that is still running or fails, and the risk check's other
lines. Databricks is a fake of the documented APIs; nothing reaches a workspace."""

from __future__ import annotations

import json
import threading

import pytest

from fabric_desktop import app_settings, connprofiles
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.contract import CellResult

import test_databricks_billing as billing
from test_databricks_sql import HOST, PAT, TOKEN, Resp
from test_engines_service import CLI, Signer, Svc, run, wait_ready

PROFILES = {"path": "x", "found": True, "profiles": CLI["profiles"] + [
    {"name": "bare", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "",
     "serverless": False, "warning": ""}]}
SF = {"path": "y", "found": True, "default": "sf", "connections": [
    {"name": "sf", "account": "myorg-acct", "user": "dev", "role": "", "warehouse": "WH", "database": "SALES",
     "schema": "PUBLIC", "authenticator": "", "private_key_file": "", "method": "oauth-local", "usable": True,
     "warnings": []}]}


@pytest.fixture(autouse=True)
def clis(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: PROFILES)
    monkeypatch.setattr(connprofiles, "snowflake_cli_connections", lambda folder=None: SF)


def _broken_profile(svc):
    (svc._profiles().root / "broken.json").write_text(json.dumps({"id": "broken", "name": "Broken", "connections": "x"}))


# ---- where it keeps things, and connections by key

def test_profiles_billing_and_sync_pairs_live_in_the_apps_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    svc = es.EnginesMixin()
    root = tmp_path / "appdata" / "FabricDesktop"
    assert svc._profiles().root == root / "profiles" and (root / "profiles").is_dir()
    assert svc._billing_root() == root / "databricks-billing"
    assert svc._sync_pairs().path == root / "databricks-sync" / "pairs.json"


def test_connections_leave_out_damaged_profiles_and_other_platforms(tmp_path):
    svc = Svc(tmp_path)
    saved = svc.engine_connection_save(profile_name="Snow", snowflake_cli="sf")
    _broken_profile(svc)
    keys = [c["key"] for c in svc.engine_connections()["connections"]]
    assert keys == ["cli:dev", "cli:bare"]  # the Snowflake connection and the damaged profile aren't Databricks rows
    profiles = {p["id"]: p for p in svc.engine_profiles()["profiles"]}
    assert profiles["broken"]["problems"] and profiles["broken"]["connections"] == []
    assert profiles["broken"]["name"] == "Broken" and profiles["broken"]["kind"] == ""
    assert profiles[saved["profile"]]["connections"][0]["platform"] == "snowflake"


@pytest.mark.parametrize("key", ["profile:only-two", "elsewhere:p:c", "anything"])
def test_a_key_that_isnt_from_the_list_is_refused(tmp_path, key):
    with pytest.raises(ValueError, match="Pick a connection from the list"):
        Svc(tmp_path)._connection(key)


def test_a_profile_without_that_connection_says_so(tmp_path):
    svc = Svc(tmp_path)
    saved = svc.engine_connection_save(profile_name="P", databricks_cli="dev", warehouse_id="wh1")
    with pytest.raises(ValueError, match="'P' has no connection 'gone'"):
        svc._connection(f"profile:{saved['profile']}:gone")


def test_save_refuses_an_unknown_snowflake_connection_and_a_nameless_profile(tmp_path):
    svc = Svc(tmp_path)
    with pytest.raises(ValueError, match="no Snowflake CLI connection 'nope'"):
        svc.engine_connection_save(profile_name="P", snowflake_cli="nope")
    with pytest.raises(ValueError, match="Name the new profile"):
        svc.engine_connection_save(profile_name="  ", databricks_cli="dev")
    assert [p["id"] for p in svc._profiles().list()] == []


def test_discovery_signs_in_with_the_connections_own_credential(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(dba, "provider_for", lambda conn: seen.append(conn) or "tokens")
    monkeypatch.setattr(dbsql, "Client", lambda tokens: ("client", tokens))
    conn = {"host": HOST, "credential": {"method": "cli-profile", "profile": "dev"}}
    assert es.EnginesMixin._discovery_client(Svc(tmp_path), conn) == ("client", "tokens")
    assert seen == [conn]


# ---- billing

def test_billing_needs_a_warehouse(tmp_path, monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: billing.CLI)
    svc = billing.Svc(tmp_path)
    key = svc.engine_connection_save(profile_name="P", databricks_cli="dev")["key"]  # names no warehouse
    with pytest.raises(ValueError, match="Pick a SQL warehouse"):
        svc.databricks_billing(key, confirmed=True)
    assert svc.adapter.log == []


def test_billing_that_cant_sign_in_says_why_without_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: billing.CLI)
    svc = billing.Svc(tmp_path)

    def refused(conn):
        raise dba.DatabricksAuthError(f"Sign in again with the Databricks CLI ({PAT}).")
    svc._discovery_client = refused
    out = svc.databricks_billing("cli:dev")
    assert out["needs_confirm"] and out["warehouse"] == {"id": "wh-run"}
    assert "Sign in again" in out["error"] and PAT not in out["error"]

    class Refusing(billing.Adapter):
        def open_session(self, config, authorization=None):
            refused(config)
    svc.adapter = Refusing()
    out = svc.databricks_billing("cli:dev", confirmed=True)
    assert out["ok"] is False and "Sign in again" in out["error"] and PAT not in out["error"]
    assert svc.databricks_billing_last("cli:dev") == {}


def test_a_confirmed_billing_read_names_the_signed_in_person(tmp_path, monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: billing.CLI)
    svc = billing.Svc(tmp_path)
    svc._auth = Signer()
    assert svc.databricks_billing("cli:dev", confirmed=True)["ok"]
    _, _, authorization = svc.adapter.log[0]
    assert authorization.by == "chris@contoso.com"


# ---- sync

def test_pull_skips_a_file_that_didnt_change_in_databricks_and_keeps_the_state(tmp_path, monkeypatch):
    from test_databricks_sync_service import REMOTE
    from test_databricks_sync_service import Svc as SyncSvc
    (tmp_path / "nb").mkdir()
    (tmp_path / "nb" / "load_orders.py").write_text("# Databricks notebook source\nprint(1)\n")
    svc = SyncSvc(tmp_path)
    pid = svc.databricks_sync_pair_save("sales-etl", "cli:dev", str(tmp_path / "nb"), REMOTE)["id"]
    svc.databricks_sync_push(pid, ["load_orders.py"])
    out = svc.databricks_sync_pull(pid, ["load_orders.py"])
    assert out["done"] == [] and out["skipped"][0]["file"] == "load_orders.py" and "files" not in out
    assert svc.databricks_sync_pairs()["pairs"][0]["synced"] == 1
    assert (tmp_path / "nb" / "load_orders.py").read_text().endswith("print(1)\n")


# ---- before choosing: targets and the risk check

class _Http:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        self.calls.append((method, url[len(HOST):]))
        if url.endswith("/api/2.0/sql/warehouses/wh1"):
            return Resp(200, {"name": "Shared", "state": "RUNNING", "auto_stop_mins": 10})
        if "/clusters/get" in url:
            return Resp(200, {"cluster_name": "", "state": "TERMINATED", "autotermination_minutes": 0})
        return Resp(404, {"message": "no such path"})


def test_reading_the_target_uses_gets_and_never_starts_it(tmp_path, monkeypatch):
    http = _Http()
    real = dbsql.Client
    monkeypatch.setattr(dba, "provider_for", lambda conn: dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"))
    monkeypatch.setattr(dbsql, "Client", lambda tokens: real(tokens, http))
    t = {"target": "wh1", "options": {"connection": {"host": HOST}}}
    read = es.EnginesMixin._engine_read_target
    assert read(Svc(tmp_path), "databricks-sql", t) == {"name": "Shared", "state": "RUNNING", "auto_stop": 10}
    cluster = read(Svc(tmp_path), "databricks-connect", t | {"target": "0101-abc"})
    assert cluster == {"name": "0101-abc", "state": "TERMINATED", "auto_stop": 0}
    assert {m for m, _ in http.calls} == {"GET"}


def test_a_connection_without_compute_for_the_engine_says_which_id_to_add(tmp_path):
    svc = Svc(tmp_path)
    with pytest.raises(ValueError, match="names no SQL warehouse.*warehouse_id"):
        svc.engine_prepare("nb", "databricks-sql", connection="cli:bare")
    with pytest.raises(ValueError, match="names no cluster.*cluster_id"):
        svc.engine_prepare("nb", "databricks-connect", connection="cli:dev")


def test_the_risk_check_names_a_never_profile_auto_stop_and_cells_that_cant_run(tmp_path):
    svc = Svc(tmp_path)
    key = svc.engine_connection_save(profile_name="P", databricks_cli="dev", warehouse_id="wh1")["key"]
    data = svc._profiles().load(key.split(":")[1])
    data["execution"] |= {"remote_compute": "never"}
    svc._profiles().save(data)
    svc._engine_read_target = lambda engine, t: {"name": "Shared", "state": "RUNNING", "auto_stop": 10}
    plan = svc.engine_prepare("nb", "databricks-sql", connection=key, cells=["%%sql\nSELECT 1", "x = 1"])
    texts = [line["text"] for line in plan["risk"]["lines"]]
    assert plan["policy"]["mode"] == "never" and plan["confirm"]
    assert texts[0] == "The profile 'P' doesn't allow remote compute, so the session will be refused."
    assert "It stops by itself after 10 idle minutes." in texts
    assert not any("will start" in t for t in texts)  # running already
    assert "1 cell can't run on Databricks SQL warehouse (remote) or run translated." in texts  # x = 1 is Python
    assert plan["risk"]["outcome"] == "warning"


@pytest.mark.parametrize("allowed", [True, False])
def test_fabric_spark_runs_on_the_notebooks_lakehouse_and_warns_when_policy_refuses(tmp_path, monkeypatch, allowed):
    real = app_settings.value
    monkeypatch.setattr(app_settings, "value", lambda key: allowed if key == "allow_fabric_runs" else real(key))
    plan = Svc(tmp_path).engine_prepare("nb", "fabric-spark", workspace_id="w1", lakehouse_id="lh1")
    assert plan["target"] == {"id": "w1/lh1", "name": "lh1", "host": "", "state": ""}
    assert plan["confirm"] and plan["policy"] == {"mode": "ask", "profile": ""}
    texts = [line["text"] for line in plan["risk"]["lines"]]
    assert "A Spark session uses Fabric capacity from its start until it closes or times out." in texts
    assert any("policy doesn't allow runs in Fabric" in t for t in texts) is (not allowed)
    assert plan["risk"]["outcome"] == ("pass" if allowed else "warning")


# ---- choosing, and running cells

def test_going_back_to_python_with_close_closes_the_session(tmp_path):
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    wait_ready(svc, "nb")
    assert svc.engine_select("nb", "local-python", decision="close") == {"engine": "local-python", "status": "ready"}
    assert svc.engine_state("nb") == {"engine": "local-python", "status": "ready"}
    with pytest.raises(ValueError, match="cells run in the kernel"):
        svc.engine_start_cell("nb", "%%sql\nSELECT 1")


def test_choosing_a_planned_engine_is_refused(tmp_path):
    with pytest.raises(ValueError, match="can't run cells in this version"):
        Svc(tmp_path).engine_select("nb", "local-spark")


def test_a_session_still_opening_or_that_failed_to_open_runs_no_cell(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    mgr = svc._engine_manager()
    gate, entered = threading.Event(), threading.Event()

    def switch(*a, **k):
        entered.set()
        assert gate.wait(5)
        raise RuntimeError(f"couldn't open the session ({PAT})")
    monkeypatch.setattr(mgr, "switch", switch)
    svc.engine_select("nb", "local-duckdb")
    assert entered.wait(5)
    with pytest.raises(ValueError, match="DuckDB.*is still starting"):
        svc.engine_start_cell("nb", "%%sql\nSELECT 1")
    gate.set()
    state = wait_ready(svc, "nb")
    assert state["status"] == "error" and "couldn't open the session" in state["error"] and PAT not in state["error"]
    with pytest.raises(ValueError, match="couldn't open the session"):
        svc.engine_start_cell("nb", "%%sql\nSELECT 1")


def test_one_cell_at_a_time_and_polling_says_how_long_it_has_run(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    assert svc.engine_poll_cell("nb") == {"status": "idle"}
    svc.engine_select("nb", "local-duckdb")
    wait_ready(svc, "nb")
    mgr = svc._engine_manager()
    gate, entered = threading.Event(), threading.Event()

    def execute(notebook, req):
        entered.set()
        assert gate.wait(5)
        return CellResult("ok", [], 0.1, 1, "local-duckdb")
    monkeypatch.setattr(mgr, "execute", execute)
    assert svc.engine_start_cell("nb", "%%sql\nSELECT 1")["status"] == "running"
    assert entered.wait(5)
    with pytest.raises(RuntimeError, match="already running"):
        svc.engine_start_cell("nb", "%%sql\nSELECT 2")
    polled = svc.engine_poll_cell("nb", wait=0)
    assert polled["status"] == "running" and polled["seconds"] >= 0
    assert svc.engine_cancel("nb") == {"cancelled": mgr.cancel("nb")}
    gate.set()
    assert svc.engine_poll_cell("nb", wait=5)["status"] == "ok"
    svc.engine_close("nb")


def test_a_cell_that_raises_shows_the_error_without_secrets(tmp_path, monkeypatch):
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    wait_ready(svc, "nb")
    mgr = svc._engine_manager()

    def execute(notebook, req):
        raise OSError(f"worker went away ({PAT})")
    monkeypatch.setattr(mgr, "execute", execute)
    out = run(svc, "nb", "%%sql\nSELECT 1")
    assert out["status"] == "error" and out["engine"] == "local-duckdb" and out["rows"] is None
    [error] = out["outputs"]
    assert error["ename"] == "OSError" and "worker went away" in error["evalue"] and PAT not in error["evalue"]
    svc.engine_close("nb")
