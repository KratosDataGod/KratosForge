"""The notebook's Engine picker service (engines_service.py): what choosing an engine would do, the
remote confirm, sessions opening in the background, and cells on the chosen engine. DuckDB runs for
real; Databricks is a fake of the documented APIs."""

from __future__ import annotations

import time

import pytest

from fabric_desktop import connprofiles
from fabric_desktop import engines_service as es
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.authorization import RemoteAuthorization
from fabric_desktop.engines.sessions import SwitchNeeded

from test_databricks_sql import HOST, FakeWorkspace, TOKEN

CLI = {"path": "x", "found": True, "profiles": [
    {"name": "dev", "host": HOST, "auth_type": "databricks-cli", "cluster_id": "", "warehouse_id": "wh1",
     "serverless": False, "warning": ""}]}


class Signer:
    def signed_in_user(self):
        return "chris@contoso.com"


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, ws=None):
        self._tmp = tmp_path
        self._auth = Signer()
        self.ws = ws or FakeWorkspace()
        self.auths = []

    def _profiles(self):
        (self._tmp / "profiles").mkdir(exist_ok=True)
        return connprofiles.ProfileStore(self._tmp / "profiles")

    def _engine_adapter(self, engine_id):
        if engine_id == "databricks-sql":
            svc = self

            class Recording(dbsql.DatabricksSqlAdapter):
                def open_session(self, config, authorization=None):
                    svc.auths.append(authorization)
                    return super().open_session(config, authorization)
            tokens = dba.TokenProvider(HOST, lambda: (TOKEN, None), "test")
            return Recording(client_for=lambda conn: dbsql.Client(tokens, self.ws), sleep=lambda s: None)
        return super()._engine_adapter(engine_id)

    def _engine_read_target(self, engine, t):
        info = self.ws.request("GET", f"{HOST}/api/2.0/sql/warehouses/{t['target']}", headers={}, timeout=1).json()
        return {"name": info.get("name"), "state": info.get("state", ""), "auto_stop": info.get("auto_stop_mins")}


@pytest.fixture(autouse=True)
def cli(monkeypatch):
    monkeypatch.setattr(connprofiles, "databricks_cli_profiles", lambda path=None: CLI)


def wait_ready(svc, nb):
    for _ in range(200):
        state = svc.engine_state(nb)
        if state["status"] != "starting":
            return state
        time.sleep(0.02)
    raise AssertionError("never started")


def run(svc, nb, source):
    started = svc.engine_start_cell(nb, source)
    if started["status"] != "running":
        return started
    for _ in range(200):
        out = svc.engine_poll_cell(nb, wait=0.1)
        if out["status"] != "running":
            return out
    raise AssertionError("never finished")


def test_the_catalog_says_what_bills_and_which_engine_is_the_kernel(tmp_path):
    engines = {e["id"]: e for e in Svc(tmp_path).engine_catalog()["engines"]}
    assert engines["local-python"]["kernel"] and not engines["local-duckdb"]["kernel"]
    assert engines["databricks-sql"]["bills"] == "bills DBUs"
    assert engines["fabric-spark"]["bills"] == "uses Fabric capacity"
    assert engines["local-spark"]["adapter"] is False


def test_connections_list_cli_profiles_with_no_secret(tmp_path):
    conns = Svc(tmp_path).engine_connections()["connections"]
    assert conns == [{"key": "cli:dev", "label": "dev (Databricks CLI)", "source": "cli", "host": HOST,
                      "warehouse_id": "wh1", "cluster_id": "", "catalog": "", "schema": "", "remote_compute": "ask",
                      "warning": ""}]


def test_a_local_engine_runs_cells_and_flags_the_ones_it_cant(tmp_path):
    svc = Svc(tmp_path)
    plan = svc.engine_prepare("nb", "local-duckdb", cells=["%%sql\nSELECT 1", "x = 1"])
    assert not plan["confirm"] and plan["target"] is None
    assert [c["index"] for c in plan["cells"]] == [0, 1]  # Spark SQL translated, Python unsupported
    assert plan["cells"][1]["ok"] is False and plan["transition"]["decisions"] == ["close", "keep"]
    assert svc.engine_select("nb", "local-duckdb", decision="keep")["status"] == "starting"
    assert wait_ready(svc, "nb")["status"] == "ready"
    out = run(svc, "nb", "%%sql\nSELECT 41 + 1 AS answer")
    assert out["status"] == "ok" and out["engine"] == "local-duckdb" and out["rows"] == 1
    flagged = run(svc, "nb", "print('hi')")
    assert flagged["status"] == "unsupported" and "doesn't run" in flagged["reason"]
    assert svc.engine_close("nb") == {"engine": "local-python", "status": "ready"}
    assert svc.engine_state("nb")["engine"] == "local-python"


def test_a_remote_engine_needs_a_confirm_and_gets_one_for_that_warehouse(tmp_path):
    svc = Svc(tmp_path)
    plan = svc.engine_prepare("nb", "databricks-sql", connection="cli:dev", cells=["%%sql\nINSERT INTO t VALUES (1)"])
    assert plan["confirm"] and plan["grant_minutes"] == 60
    assert plan["target"] == {"id": "wh1", "name": "Shared", "host": HOST, "state": "STOPPED"}
    texts = " ".join(line["text"] for line in plan["risk"]["lines"])
    assert "will start and bill DBUs" in texts and "may change data on Shared" in texts
    assert plan["risk"]["outcome"] == "warning"
    assert not [c for c in svc.ws.calls if "statements" in c[1]]  # preparing ran nothing
    with pytest.raises(PermissionError, match="Confirm it first"):
        svc.engine_select("nb", "databricks-sql", connection="cli:dev", decision="keep")
    svc.engine_select("nb", "databricks-sql", connection="cli:dev", decision="keep", confirmed=True)
    state = wait_ready(svc, "nb")
    assert state["status"] == "ready" and state["target"]["id"] == "wh1" and state["reason"] == "confirmed"
    granted = svc.auths[0]
    assert isinstance(granted, RemoteAuthorization) and (granted.engine, granted.target, granted.by) == \
        ("databricks-sql", "wh1", "chris@contoso.com")
    assert run(svc, "nb", "%%sql\nSELECT id, name FROM t")["status"] == "ok"


def test_a_risk_check_that_cant_read_the_warehouse_says_couldnt_run(tmp_path):
    svc = Svc(tmp_path)
    svc._engine_read_target = lambda engine, t: (_ for _ in ()).throw(RuntimeError("HTTP 403 " + TOKEN))
    risk = svc.engine_prepare("nb", "databricks-sql", connection="cli:dev")["risk"]
    line = next(x for x in risk["lines"] if x["outcome"] == "unknown")
    assert line["text"].startswith("Couldn't run: couldn't read the warehouse") and TOKEN not in line["text"]


def test_switching_between_engines_needs_a_decision_and_planned_ones_refuse(tmp_path):
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    wait_ready(svc, "nb")
    with pytest.raises(SwitchNeeded):
        svc.engine_select("nb", "local-sqlite")
    with pytest.raises(SwitchNeeded):
        svc.engine_select("nb", "local-python")
    svc.engine_select("nb", "local-sqlite", decision="close")
    assert wait_ready(svc, "nb")["engine"] == "local-sqlite"
    with pytest.raises(ValueError, match="can't run cells in this version"):
        svc.engine_prepare("nb", "local-spark")
    with pytest.raises(ValueError, match="Pick a Databricks connection first"):
        svc.engine_prepare("nb", "databricks-connect")
    with pytest.raises(ValueError, match="Attach a lakehouse"):
        svc.engine_prepare("nb", "fabric-spark")
    svc.engine_close("nb")


def test_cell_requests_follow_the_cells_magic():
    assert es.cell_request("%%sql\nSELECT 1") .language == "sql"
    assert es.cell_request("%sql\nSELECT 1").code == "SELECT 1"
    assert es.cell_request("df = spark.range(3)").language == "pyspark"
    assert es.cell_request("x = 1").language == "python"


class _Stuck:
    closed = False

    def close(self):
        raise RuntimeError("worker wouldn't stop")


def test_a_session_that_couldnt_close_is_noted_in_the_close_and_switch_results(tmp_path):
    svc = Svc(tmp_path)
    svc.engine_select("nb", "local-duckdb")
    assert wait_ready(svc, "nb")["status"] == "ready"
    mgr = svc._engine_manager()
    mgr._notebooks["nb"].sessions["local-duckdb"] = _Stuck()
    svc.engine_select("nb", "local-sqlite", decision="close")
    state = wait_ready(svc, "nb")
    assert state["status"] == "ready" and state["notes"] == ["Couldn't close the DuckDB (this PC) session."]
    mgr._notebooks["nb"].sessions["local-sqlite"] = _Stuck()
    out = svc.engine_close("nb")
    assert out == {"engine": "local-python", "status": "ready",
                   "notes": ["Couldn't close the SQLite (this PC) session."]}
    assert svc.engine_close("nb") == {"engine": "local-python", "status": "ready"}
