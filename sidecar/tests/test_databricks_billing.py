"""Databricks DBUs from system.billing.usage (Optimize > Cost, Preview): nothing runs without a
confirm for the warehouse, the query is fixed text with a whole number of days, rows are DBUs only,
and the last read stays on this PC per workspace."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest

from fabric_desktop import audit, connprofiles, server
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_billing as bill
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.contract import CellResult

from test_databricks_discovery import HOST, TOKEN, FakeWorkspace

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "wh-run", "serverless": False, "warning": ""}]}
TABLE = pa.table({"job_id": ["1", None, None, None], "pipeline_id": [None, "pl-1", None, None],
                  "warehouse_id": [None, None, "wh-run", None], "cluster_id": [None, None, None, None],
                  "sku_name": ["PREMIUM_JOBS_COMPUTE", "PREMIUM_DLT", "PREMIUM_SQL_PRO", "X"],
                  "dbu": [22.4, 11.2, 51.0, None]})


class Session:
    def __init__(self, log, result):
        self.log, self.result = log, result

    def execute(self, request):
        self.log.append(("execute", request.code, request.result_rows))
        return self.result

    def close(self):
        self.log.append(("close",))


class Adapter:
    def __init__(self, result=None):
        self.log = []
        self.result = result or CellResult("ok", [], 2.5, 4, "databricks-sql", TABLE)

    def open_session(self, config, authorization=None):
        self.log.append(("open", config.options["warehouse_id"], authorization))
        return Session(self.log, self.result)


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, adapter=None):
        self._tmp = tmp_path
        self.adapter = adapter or Adapter()
        self.ws = FakeWorkspace()

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")

    def _billing_root(self):
        return self._tmp / "billing"

    def _discovery_client(self, connection):
        return dbsql.Client(dba.TokenProvider(HOST, lambda: (TOKEN, None), "test"), self.ws)

    def _engine_adapter(self, engine_id):
        assert engine_id == "databricks-sql"
        return self.adapter


class WarehouseWs(FakeWorkspace):
    def request(self, method, url, **kw):
        if url.endswith("/api/2.0/sql/warehouses/wh-run"):
            self.calls.append((method, url, {}))
            from test_databricks_discovery import Resp, WAREHOUSES
            return Resp(body=WAREHOUSES[0])
        return super().request(method, url, **kw)


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)


def test_the_calls_are_rpc_methods_and_only_the_last_read_skips_the_audit():
    assert {"databricks_billing", "databricks_billing_last"} <= es.METHODS <= server.METHODS
    assert "databricks_billing_last" in audit.READ_ONLY and "databricks_billing" not in audit.READ_ONLY


def test_query_is_fixed_text_with_whole_days_only():
    assert "INTERVAL" not in bill.query(7) and "date_sub(current_date(), 7)" in bill.query(7)
    for bad in (0, 2, 365, -1):
        with pytest.raises(ValueError):
            bill.query(bad)


def test_rows_are_dbus_by_what_used_them_and_a_missing_figure_is_dropped():
    rows = bill.rows_from(TABLE)
    assert rows == [{"kind": "job", "id": "1", "sku": "PREMIUM_JOBS_COMPUTE", "dbu": 22.4},
                    {"kind": "pipeline", "id": "pl-1", "sku": "PREMIUM_DLT", "dbu": 11.2},
                    {"kind": "warehouse", "id": "wh-run", "sku": "PREMIUM_SQL_PRO", "dbu": 51.0}]


def test_without_confirm_nothing_runs_and_the_warehouse_state_is_read_with_a_get(tmp_path):
    svc = Svc(tmp_path)
    svc.ws = WarehouseWs()
    out = svc.databricks_billing("cli:dev", days=7)
    assert out["needs_confirm"] and out["warehouse"]["name"] == "Shared SQL" and out["warehouse"]["state"] == "RUNNING"
    assert svc.adapter.log == [] and {m for m, _, _ in svc.ws.calls} == {"GET"}


def test_confirmed_runs_once_on_that_warehouse_closes_and_keeps_the_result(tmp_path):
    svc = Svc(tmp_path)
    out = svc.databricks_billing("cli:dev", days=7, confirmed=True)
    assert out["ok"] and out["total_dbu"] == 84.6 and len(out["rows"]) == 3
    kinds = [e[0] for e in svc.adapter.log]
    assert kinds == ["open", "execute", "close"]
    _, warehouse, authorization = svc.adapter.log[0]
    assert warehouse == "wh-run" and authorization.target == "wh-run" and authorization.reason == "confirmed"
    assert svc.adapter.log[1][1] == bill.query(7)
    assert svc.databricks_billing_last("cli:dev")["total_dbu"] == 84.6


def test_the_real_adapter_refuses_without_an_authorization():
    with pytest.raises(RemoteNotAuthorized):
        bill.read(dbsql.DatabricksSqlAdapter(client_for=lambda c: None), {"host": HOST, "compute": {}}, "wh-run", 7, None)


def test_a_failed_query_says_why_without_the_token_and_keeps_nothing(tmp_path):
    failed = CellResult("error", [{"type": "error", "ename": "DatabricksError", "evalue": f"PERMISSION_DENIED on system.billing ({TOKEN})"}],
                        1.0, None, "databricks-sql")
    svc = Svc(tmp_path, Adapter(failed))
    out = svc.databricks_billing("cli:dev", confirmed=True)
    assert not out["ok"] and "PERMISSION_DENIED" in out["error"] and TOKEN not in json.dumps(out)
    assert svc.adapter.log[-1] == ("close",) and svc.databricks_billing_last("cli:dev") == {}


@pytest.mark.parametrize("kw, why", [({"warehouse_id": "x;drop"}, "warehouse id"), ({"days": 3}, "days")])
def test_billing_refuses_odd_input_before_anything(tmp_path, kw, why):
    svc = Svc(tmp_path)
    with pytest.raises(ValueError, match=why):
        svc.databricks_billing("cli:dev", confirmed=True, **kw)
    assert svc.adapter.log == []


def _profile(svc, policy):
    out = svc.engine_connection_save(profile_name="P", databricks_cli="dev", warehouse_id="wh-run")
    data = svc._profiles().load(out["profile"])
    data["execution"] |= policy
    svc._profiles().save(data)
    return out["key"]


def test_a_never_profile_is_refused_before_anything_even_when_confirmed(tmp_path):
    svc = Svc(tmp_path)
    svc.ws = WarehouseWs()
    key = _profile(svc, {"remote_compute": "never"})
    for confirmed in (False, True):
        out = svc.databricks_billing(key, confirmed=confirmed)
        assert out["refused"] and not out["ok"] and "doesn't allow remote compute" in out["error"]
    assert svc.adapter.log == [] and svc.ws.calls == []


def test_a_policy_profile_that_allows_the_engine_runs_under_the_policy(tmp_path):
    svc = Svc(tmp_path)
    key = _profile(svc, {"remote_compute": "policy", "allowed_remote_engines": ["databricks-sql"]})
    out = svc.databricks_billing(key)
    assert out["ok"] and svc.adapter.log[0][2].reason == "policy"


def test_a_policy_profile_without_the_engine_still_asks(tmp_path):
    svc = Svc(tmp_path)
    svc.ws = WarehouseWs()
    key = _profile(svc, {"remote_compute": "policy", "allowed_remote_engines": []})
    assert svc.databricks_billing(key)["needs_confirm"] and svc.adapter.log == []
