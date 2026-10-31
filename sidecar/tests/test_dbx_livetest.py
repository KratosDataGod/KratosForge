"""The Databricks live test (KF-DBX-LIVE) against fakes: the real SQL warehouse engine over a fake
workspace, and a fake Databricks Connect session. Nothing reaches Databricks here; the live run is
a person's, on a workspace they set aside for tests."""

from __future__ import annotations

import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from fabric_desktop import cli
from fabric_desktop import dbx_livetest as dl
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.authorization import RemoteAuthorization
from fabric_desktop.engines.contract import CellResult

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "c2lnbmF0dXJl" * 3
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m"}}
TAG = [{"key": dl.MARKER, "value": ""}]


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class Workspace:
    """The REST calls the harness and the SQL engine make. A slow statement runs until cancelled."""

    def __init__(self, warehouse=None, cluster=None, slow_finishes=False):
        self.calls = []
        self.warehouse = {"id": "wh1", "name": "kf tests", "state": "STOPPED", "cluster_size": "2X-Small",
                          "enable_serverless_compute": True, "auto_stop_mins": 5, "tags": {"custom_tags": TAG}}
        self.warehouse.update(warehouse or {})
        self.cluster = {"cluster_id": "0123-456789-abcdefgh", "cluster_name": "kf tests", "state": "TERMINATED",
                        "spark_version": "16.4.x-scala2.12", "node_type_id": "Standard_D4ds_v5", "num_workers": 0,
                        "autotermination_minutes": 10, "custom_tags": {dl.MARKER: ""}}
        self.cluster.update(cluster or {})
        self.slow_finishes = slow_finishes
        self.statements = {}
        self.cancelled = set()

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append((method, path))
        if headers["Authorization"] != f"Bearer {TOKEN}":
            return Resp(401, {"error_code": "UNAUTHENTICATED", "message": "Invalid access token."})
        if path == "/api/2.0/sql/warehouses/wh1":
            return Resp(200, self.warehouse)
        if path.startswith("/api/2.0/sql/warehouses/") and path.endswith("/stop"):
            return Resp(200, {})
        if path.startswith("/api/2.0/sql/warehouses/"):
            return Resp(404, {"error_code": "RESOURCE_DOES_NOT_EXIST", "message": "No such warehouse."})
        if path == "/api/2.1/clusters/get":
            return Resp(200, self.cluster)
        if path == "/api/2.1/clusters/delete":
            return Resp(200, {})
        if path == "/api/2.1/unity-catalog/catalogs":
            return Resp(200, {"catalogs": [{"name": "main"}, {"name": "samples"}]})
        if path == "/api/2.0/sql/statements" and method == "POST":
            sid = f"s{len(self.statements) + 1}"
            self.statements[sid] = json["statement"]
            return Resp(200, self._state(sid))
        if path.endswith("/cancel"):
            self.cancelled.add(path.split("/")[-2])
            return Resp(200, {})
        if path.startswith("/api/2.0/sql/statements/"):
            return Resp(200, self._state(path.split("/")[-1]))
        return Resp(404, {"message": "no such path"})

    def _state(self, sid):
        statement = self.statements[sid]
        if sid in self.cancelled:
            return {"statement_id": sid, "status": {"state": "CANCELED"}}
        if "kf_live_test_missing_" in statement:
            return {"statement_id": sid, "status": {"state": "FAILED", "error": {
                "message": "[TABLE_OR_VIEW_NOT_FOUND] The table or view `kf_live_test_missing_r1` cannot be found."}}}
        if statement == dl.SLOW_SQL and not self.slow_finishes:
            return {"statement_id": sid, "status": {"state": "RUNNING"}}
        rows = [[str(i), str(i * 2)] for i in range(5)]
        return {"statement_id": sid, "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": {"columns": [{"name": "id", "type_name": "BIGINT"},
                                                    {"name": "twice", "type_name": "BIGINT"}]},
                             "total_row_count": 5},
                "result": {"data_array": rows}}


class ConnectSession:
    """A Databricks Connect session: a long cell runs until cancel interrupts it."""

    client = "16.4.23"

    def __init__(self, count="5"):
        self.count = count
        self.closed = False
        self.proc = None
        self._running = threading.Event()
        self._stop = threading.Event()
        self.ran = []

    def execute(self, request, on_output=None):
        self.ran.append(request.code)
        if request.code == dl.SLOW_CELL:
            self._running.set()
            self._stop.wait(10)
            self._running.clear()
            return CellResult("error", [{"type": "error", "ename": "SparkConnectGrpcException",
                                         "evalue": "[OPERATION_CANCELED] Operation has been cancelled."}], 1.0, None,
                              "databricks-connect")
        return CellResult("ok", [{"type": "text", "text": self.count}], 0.5, None, "databricks-connect")

    def cancel(self):
        if not self._running.is_set():
            return False
        self._stop.set()
        return True

    def close(self):
        self.closed = True


class ConnectAdapter:
    def __init__(self, session=None, fail=None):
        self.session = session or ConnectSession()
        self.fail = fail
        self.authorizations = []

    def open_session(self, config, authorization=None):
        self.authorizations.append(authorization)
        if self.fail:
            raise RuntimeError(self.fail)
        return self.session


def make(tmp_path, ws=None, cluster="", connect=None, **kw):
    ws = ws or Workspace()
    tokens = dba.TokenProvider(HOST, lambda: (TOKEN, None), "databricks-cli")
    client = dbsql.Client(tokens, ws)
    bad = dbsql.Client(dba.TokenProvider(HOST, lambda: ("not-a-token", None), "test"), ws)
    adapter = dbsql.DatabricksSqlAdapter(client_for=lambda conn: dbsql.Client(tokens, ws), sleep=lambda s: None)
    test = dl.DatabricksLiveTest(CONN, "wh1", tokens, client, adapter, tmp_path, cluster_id=cluster,
                                 connect_adapter=connect, bad_token_client=bad, run_id="r1",
                                 sleep=lambda s: time.sleep(0.01), **kw)
    return test, ws


def statuses(result):
    return {c["id"]: c["status"] for c in result["checks"]}


def test_a_full_run_passes_every_check_and_records_no_token(tmp_path):
    connect = ConnectAdapter()
    test, ws = make(tmp_path, cluster="0123-456789-abcdefgh", connect=connect)
    result = test.run()
    assert result["outcome"] == "passed", result["checks"]
    assert set(statuses(result)) == {"auth.token", "dbx.warehouse", "dbx.cluster", "sql.open", "uc.catalogs",
                                     "sql.query", "sql.error", "sql.cancel", "sql.close", "connect.open",
                                     "connect.cell", "connect.cancel", "connect.close", "errors.not_found",
                                     "errors.bad_token"}
    assert set(statuses(result).values()) == {"passed"}
    assert ws.cancelled  # the slow statement was cancelled on the warehouse
    # the engines got the person's confirmation for exactly this warehouse and cluster
    auth = connect.authorizations[0]
    assert isinstance(auth, RemoteAuthorization) and (auth.engine, auth.target) == \
        ("databricks-connect", "0123-456789-abcdefgh")
    files = (tmp_path / "r1" / "result.json").read_text() + (tmp_path / "r1" / "summary.md").read_text()
    assert TOKEN not in files and "eyJ" not in files
    saved = json.loads((tmp_path / "r1" / "result.json").read_text())
    assert saved["schema"] == dl.SCHEMA and saved["warehouse"]["auto_stop_mins"] == 5
    # it created nothing and stopped nothing
    assert not [c for c in ws.calls if c[1].endswith("/stop") or c[1] == "/api/2.1/clusters/delete"]
    assert saved["left_running"] == [{"kind": "warehouse", "id": "wh1"},
                                     {"kind": "cluster", "id": "0123-456789-abcdefgh"}]


@pytest.mark.parametrize("change, why", [
    ({"tags": {"custom_tags": []}}, "has no fabric-desktop-live-test tag"),
    ({"name": "Sales PROD"}, "looks like production"),
    ({"auto_stop_mins": 0}, "auto-stop turned off"),
])
def test_an_unmarked_production_or_never_stopping_warehouse_is_refused_before_anything_runs(tmp_path, change, why):
    test, ws = make(tmp_path, Workspace(warehouse=change))
    result = test.run()
    assert result["outcome"] == "refused" and why in " ".join(result["refused"])
    assert not [c for c in ws.calls if "statements" in c[1]]
    assert list(statuses(result)) == ["auth.token", "dbx.warehouse"]


def test_the_cluster_is_refused_on_the_same_terms(tmp_path):
    connect = ConnectAdapter()
    test, ws = make(tmp_path, Workspace(cluster={"custom_tags": {}, "autotermination_minutes": 0}),
                    cluster="0123-456789-abcdefgh", connect=connect)
    result = test.run()
    assert result["outcome"] == "refused" and len(result["refused"]) == 2
    assert not connect.authorizations and not [c for c in ws.calls if "statements" in c[1]]


def test_a_sign_in_that_fails_stops_the_run(tmp_path):
    test, ws = make(tmp_path)
    test.tokens = dba.TokenProvider(HOST, lambda: ("", None), "databricks-cli")
    result = test.run()
    assert result["outcome"] == "failed" and list(statuses(result)) == ["auth.token"] and not ws.calls


def test_a_statement_that_finishes_before_the_cancel_fails_that_check(tmp_path):
    test, _ = make(tmp_path, Workspace(slow_finishes=True))
    result = test.run()
    assert statuses(result)["sql.cancel"] == "failed"
    assert "before it could be cancelled" in next(c["detail"] for c in result["checks"] if c["id"] == "sql.cancel")


def test_a_wrong_cell_result_fails_and_a_session_that_wont_open_skips_its_checks(tmp_path):
    test, _ = make(tmp_path, cluster="0123-456789-abcdefgh", connect=ConnectAdapter(ConnectSession(count="4")))
    assert statuses(test.run())["connect.cell"] == "failed"
    test, _ = make(tmp_path, cluster="0123-456789-abcdefgh",
                   connect=ConnectAdapter(fail="databricks-connect for Databricks Runtime 16.4 isn't installed"))
    result = test.run()
    s = statuses(result)
    assert s["connect.open"] == "failed" and s["connect.cell"] == s["connect.cancel"] == "skipped"
    assert "connect.close" not in s and s["errors.bad_token"] == "passed"


def test_stop_compute_stops_only_what_was_stopped_when_the_run_began(tmp_path):
    test, ws = make(tmp_path, Workspace(cluster={"state": "RUNNING"}), cluster="0123-456789-abcdefgh",
                    connect=ConnectAdapter(), stop_compute=True)
    result = test.run()
    assert result["outcome"] == "passed"
    assert ("POST", "/api/2.0/sql/warehouses/wh1/stop") in ws.calls
    assert ("POST", "/api/2.1/clusters/delete") not in ws.calls  # someone else had the cluster running
    assert statuses(result)["stop.warehouse"] == "passed" and "stop.cluster" not in statuses(result)
    assert result["left_running"] == []


def test_the_plan_says_what_it_starts_and_that_it_creates_nothing(tmp_path):
    test, _ = make(tmp_path, cluster="0123-456789-abcdefgh", connect=ConnectAdapter())
    plan = "\n".join(test.plan())
    assert "bills DBUs" in plan and "Create nothing in the workspace" in plan and "auto-stop" in plan
    assert "--stop-compute" not in plan and "Leave the warehouse and cluster" in plan


def test_the_command_line_dry_run_signs_in_to_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dba, "provider_for", lambda conn: pytest.fail("signed in during a dry run"))
    def code(*argv):
        with pytest.raises(SystemExit) as exit:
            cli.main(["live-test-databricks", *argv])
        return exit.value.code
    assert code("--profile", "kf-test", "--warehouse", "wh1", "--dry-run", "--out", str(tmp_path)) == 0
    assert "Dry run: nothing was signed in to, started or written." in capsys.readouterr().out
    assert code("--profile", "kf-test", "--dry-run") == 2
    assert code("--profile", "kf-test", "--host", HOST, "--warehouse", "wh1", "--dry-run") == 2

# ---------------------------------------------------------------- this PC first (KF-DBX-PREFLIGHT)


def _fake_cli(tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    exe = folder / ("databricks.exe" if sys.platform == "win32" else "databricks")
    exe.write_text("")
    exe.chmod(0o755)
    return {"PATH": str(folder)}, str(exe)


def _answers(stdout="Databricks CLI v0.230.0\n", code=0):
    calls = []

    def run(args, **kw):
        calls.append(args)
        assert kw.get("timeout") and kw.get("stdin") is not None
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
    return run, calls


def _cfg(tmp_path, text):
    path = tmp_path / ".databrickscfg"
    path.write_text(text)
    return path


PROFILE = {"platform": "databricks", "host": "", "credential": {"method": "cli-profile", "profile": "kf-test"}}


def test_a_ready_pc_passes_every_check_and_only_asks_the_cli_its_version(tmp_path):
    environ, exe = _fake_cli(tmp_path)
    run, calls = _answers()
    cfg = _cfg(tmp_path, f"[kf-test]\nhost = {HOST}\ntoken = " + "dapi" + "0" * 32 + "\n")
    found = dl.pc_checks(PROFILE, environ=environ, cfg_path=cfg, run=run, version=(3, 12, 7))
    assert all(ok for ok, _ in found), found
    assert calls == [[exe, "--version"]]
    text = " ".join(t for _, t in found)
    assert "Python 3.12.7" in text and "0.230.0" in text and HOST in text and "dapi" not in text


@pytest.mark.parametrize("version,stdout,code,cfg,want", [
    ((3, 11, 9), None, 0, None, "needs 3.12 or later"),
    ((3, 12, 0), "Databricks CLI v0.18.0\n", 0, None, "needs 0.205 or later"),
    ((3, 12, 0), "Version 0.17.7\n", 2, None, "old Python databricks-cli"),
    ((3, 12, 0), None, 0, "[other]\nhost = x\n", "no profile named 'kf-test'"),
    ((3, 12, 0), None, 0, "[kf-test]\nhost = https://example.com\n", "Sign in with: databricks auth login"),
])
def test_each_missing_piece_is_named(tmp_path, version, stdout, code, cfg, want):
    environ, _ = _fake_cli(tmp_path)
    run, _ = _answers(stdout or "Databricks CLI v0.230.0\n", code)
    path = _cfg(tmp_path, cfg or f"[kf-test]\nhost = {HOST}\n")
    found = dl.pc_checks(PROFILE, environ=environ, cfg_path=path, run=run, version=version)
    bad = [t for ok, t in found if not ok]
    assert len(bad) == 1 and want in bad[0], found


def test_no_cli_on_path_is_named_and_nothing_is_run(tmp_path):
    run, calls = _answers()
    found = dl.pc_checks(PROFILE, environ={"PATH": str(tmp_path)}, cfg_path=tmp_path / "none", run=run,
                                   version=(3, 12, 0))
    bad = [t for ok, t in found if not ok]
    assert calls == [] and any("isn't on PATH" in t for t in bad) and any("no Databricks CLI configuration" in t
                                                                         for t in bad)


def test_a_host_sign_in_checks_the_address_not_a_profile(tmp_path):
    environ, _ = _fake_cli(tmp_path)
    run, _ = _answers()
    conn = {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m"}}
    assert all(ok for ok, _ in dl.pc_checks(conn, environ=environ, run=run, version=(3, 12, 0)))
    conn["host"] = "https://evil.example.com"
    assert not all(ok for ok, _ in dl.pc_checks(conn, environ=environ, run=run, version=(3, 12, 0)))


def test_the_command_line_refuses_to_sign_in_when_this_pc_isnt_ready(monkeypatch, capsys):
    monkeypatch.setattr(dl, "pc_checks", lambda conn: [(True, "Python ok."), (False, "No CLI.")])
    monkeypatch.setattr(dba, "provider_for", lambda conn: pytest.fail("signed in on a PC that isn't ready"))
    with pytest.raises(SystemExit) as exit:
        cli.main(["live-test-databricks", "--profile", "kf-test", "--warehouse", "wh1", "--yes"])
    out = capsys.readouterr().out
    assert exit.value.code == 2 and "NO  No CLI." in out and "Nothing was signed in to or started." in out
