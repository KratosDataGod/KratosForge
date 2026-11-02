"""The Databricks SQL warehouse engine against fakes of the documented REST APIs and the Databricks
CLI: nothing reaches a workspace. Tokens come from references and never appear in an error."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines import registry
from fabric_desktop.engines.capabilities import CAPABILITIES, check_cell
from fabric_desktop.engines.contract import CellRequest, SessionClosed, SessionConfig, Unsupported

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
PAT = "dapi" + "0123456789abcdef" * 2
TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "c2lnbmF0dXJl" * 3
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m"},
        "catalog": "main", "schema": "sales", "compute": {"warehouse_id": "wh1"}}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()

    def json(self):
        return self._body


class FakeWorkspace:
    """Answers the calls the engine makes, recording each; statements finish after ``polls`` polls."""

    def __init__(self, polls=0, fail=None, rows=None, columns=None, total=None, refuse=None):
        self.calls = []
        self.polls = polls
        self.fail = fail
        self.rows = rows if rows is not None else [["1", "a"], ["2", None]]
        self.columns = columns if columns is not None else [{"name": "id", "type_name": "INT"},
                                                            {"name": "name", "type_name": "STRING"}]
        self.total = total
        self.refuse = refuse
        self.cancelled = []
        self.n = 0
        self.on_poll = None

    def _done(self, sid, statement):
        if self.fail:
            return {"statement_id": sid, "status": {"state": "FAILED", "error": {"message": self.fail}}}
        if sid in self.cancelled:
            return {"statement_id": sid, "status": {"state": "CANCELED"}}
        if not statement.upper().startswith(("SELECT", "EXPLAIN", "SHOW")):
            return {"statement_id": sid, "status": {"state": "SUCCEEDED"}, "manifest": {"schema": {"columns": []}}}
        rows = [["== Physical Plan =="], ["Scan main.sales.orders"]] if statement.upper().startswith("EXPLAIN") \
            else self.rows
        cols = [{"name": "plan", "type_name": "STRING"}] if statement.upper().startswith("EXPLAIN") else self.columns
        return {"statement_id": sid, "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": {"columns": cols}, "total_row_count": self.total or len(rows)},
                "result": {"chunk_index": 0, "data_array": rows}}

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        assert url.startswith(HOST) and timeout
        path = url[len(HOST):]
        self.calls.append((method, path, headers, json, params))
        if self.refuse:
            return Resp(self.refuse, {"error_code": "PERMISSION_DENIED", "message": "User lacks CAN USE " + PAT})
        if path == "/api/2.0/sql/warehouses/wh1":
            return Resp(200, {"id": "wh1", "name": "Shared", "state": "STOPPED"})
        if path == "/api/2.0/sql/statements" and method == "POST":
            self.n += 1
            sid = f"s{self.n}"
            self.statements = getattr(self, "statements", {})
            self.statements[sid] = [json["statement"], self.polls]
            if self.polls:
                return Resp(200, {"statement_id": sid, "status": {"state": "PENDING"}})
            return Resp(200, self._done(sid, json["statement"]))
        if path.startswith("/api/2.0/sql/statements/") and path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[-2])
            return Resp(200, {})
        if path.startswith("/api/2.0/sql/statements/"):
            sid = path.split("/")[-1]
            if self.on_poll:
                self.on_poll()
            entry = self.statements[sid]
            entry[1] -= 1
            if entry[1] > 0 and sid not in self.cancelled:
                return Resp(200, {"statement_id": sid, "status": {"state": "RUNNING"}})
            return Resp(200, self._done(sid, entry[0]))
        if path == "/api/2.1/unity-catalog/catalogs":
            if not (params or {}).get("page_token"):
                return Resp(200, {"catalogs": [{"name": "main"}], "next_page_token": "p2"})
            return Resp(200, {"catalogs": [{"name": "samples"}]})
        if path == "/api/2.1/unity-catalog/schemas":
            return Resp(200, {"schemas": [{"name": "sales", "catalog_name": params["catalog_name"]}]})
        if path == "/api/2.1/unity-catalog/tables":
            return Resp(200, {"tables": [{"name": "orders", "catalog_name": "main", "schema_name": "sales",
                                          "table_type": "MANAGED"},
                                         {"name": "v_orders", "catalog_name": "main", "schema_name": "sales",
                                          "table_type": "VIEW"}]})
        return Resp(404, {"message": "no such path"})


def tokens():
    return dba.TokenProvider(HOST, lambda: (TOKEN, None), "test")


def adapter(ws):
    return dbsql.DatabricksSqlAdapter(client_for=lambda conn: dbsql.Client(tokens(), ws, sleep=lambda s: None), sleep=lambda s: None)


def open_session(ws, **options):
    ok = auth.confirmed("databricks-sql", "wh1", "chris")
    return adapter(ws).open_session(SessionConfig("nb", options={"connection": CONN, **options}), ok)


# ---------------------------------------------------------------- descriptor


def test_the_engine_is_registered_as_preview_remote_and_billed_in_dbu():
    d = registry.get("databricks-sql")
    assert d.adapter and d.remote and d.preview and d.billing == "databricks_dbu"
    assert isinstance(registry.adapter("databricks-sql"), dbsql.DatabricksSqlAdapter)
    assert set(d.as_dict()["capabilities"]) == set(CAPABILITIES)
    assert all(s.evidence != "live_verified" for s in d.capabilities.values())
    assert any("not yet against a live workspace" in x for x in d.limitations)
    assert check_cell(d, "sql", "databricks")["ok"] and not check_cell(d, "python")["ok"]
    assert "system billing" in d.capability("cost").note


# ---------------------------------------------------------------- authorization


def test_nothing_is_contacted_without_an_authorization_for_this_warehouse():
    ws = FakeWorkspace()
    with pytest.raises(auth.RemoteNotAuthorized, match="may be billed"):
        adapter(ws).open_session(SessionConfig("nb", options={"connection": CONN}))
    with pytest.raises(auth.RemoteNotAuthorized, match="not databricks-sql on wh1"):
        adapter(ws).open_session(SessionConfig("nb", options={"connection": CONN}),
                                 auth.confirmed("databricks-sql", "wh2", "chris"))
    assert ws.calls == []


def test_a_connection_without_a_warehouse_asks_for_one():
    with pytest.raises(Unsupported, match="Pick a SQL warehouse"):
        adapter(FakeWorkspace()).open_session(SessionConfig("nb", options={"connection": {**CONN, "compute": {}}}))


def test_opening_reads_the_warehouse_but_never_starts_it():
    ws = FakeWorkspace()
    s = open_session(ws)
    assert s.warehouse_name == "Shared" and s.warehouse_state == "STOPPED"
    assert [(m, p) for m, p, *_ in ws.calls] == [("GET", "/api/2.0/sql/warehouses/wh1")]
    assert ws.calls[0][2] == {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------- execute


def test_a_select_returns_typed_preview_rows_and_the_exact_total():
    ws = FakeWorkspace(total=5000)
    s = open_session(ws)
    seen = []
    r = s.execute(CellRequest("SELECT id, name FROM orders", "sql", "databricks", preview_rows=1), seen.append)
    assert r.status == "ok" and r.rows == 5000 and r.engine == "databricks-sql"
    out = r.outputs[0]
    assert out["total_rows"] == 5000 and seen == [out]
    body = ws.calls[-1][3]
    assert body == {"warehouse_id": "wh1", "statement": "SELECT id, name FROM orders", "wait_timeout": "10s",
                    "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY",
                    "catalog": "main", "schema": "sales"}


def test_values_are_typed_where_safe_and_kept_as_text_otherwise():
    t = dbsql.to_table({"schema": {"columns": [
        {"name": "n", "type_name": "LONG"}, {"name": "ok", "type_name": "BOOLEAN"},
        {"name": "d", "type_name": "DATE"}, {"name": "x", "type_name": "INT"}, {"name": "m", "type_name": "DECIMAL"}]}},
        [["1", "true", "2026-10-26", "oops", "1.50"], [None, "false", None, "2", "2.25"]])
    assert t.column("n").to_pylist() == [1, None] and t.column("ok").to_pylist() == [True, False]
    assert str(t.column("d").type) == "date32[day]" and t.column("x").to_pylist() == ["oops", "2"]
    assert t.column("m").to_pylist() == ["1.50", "2.25"]


def test_statements_in_a_cell_run_in_order_and_semicolons_in_strings_dont_split():
    assert dbsql.split_statements("SELECT ';' AS a; -- x;\nUSE main;\n\nSELECT 2") == \
        ["SELECT ';' AS a", "-- x;\nUSE main", "SELECT 2"]
    ws = FakeWorkspace()
    s = open_session(ws)
    r = s.execute(CellRequest("CREATE TABLE t AS SELECT 1; SELECT * FROM t", "sql"))
    sent = [c[3]["statement"] for c in ws.calls if c[1] == "/api/2.0/sql/statements"]
    assert sent == ["CREATE TABLE t AS SELECT 1", "SELECT * FROM t"] and r.rows == 2


def test_a_statement_with_no_rows_says_done_on_the_warehouse():
    r = open_session(FakeWorkspace()).execute(CellRequest("CREATE TABLE t (id INT)", "sql"))
    assert r.status == "ok" and r.rows is None and "on Shared" in r.outputs[0]["text"]


def test_long_statements_are_polled_until_they_finish():
    ws = FakeWorkspace(polls=3)
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "ok"
    assert sum(1 for m, p, *_ in ws.calls if m == "GET" and p.startswith("/api/2.0/sql/statements/")) == 3


def _drop_on_poll(ws, refuse_cancel=False):
    def drop():
        raise ConnectionError("network down " + PAT)
    ws.on_poll = drop
    if refuse_cancel:
        _flaky(ws, [403], lambda m, p: p.endswith("/cancel"))


def test_a_failed_poll_stops_the_statement_on_the_warehouse():
    ws = FakeWorkspace(polls=3)
    _drop_on_poll(ws)
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and ws.cancelled == ["s1"]
    assert "Couldn't reach" in r.outputs[0]["evalue"] and "was stopped on Shared" in r.outputs[0]["evalue"]
    assert PAT not in json.dumps(r.as_dict())


def test_when_the_cancel_fails_too_the_error_says_where_to_stop_it():
    ws = FakeWorkspace(polls=3)
    _drop_on_poll(ws, refuse_cancel=True)
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    evalue = r.outputs[0]["evalue"]
    assert r.status == "error" and evalue.startswith("Couldn't reach") and "may still be running on Shared" in evalue
    assert "query history" in evalue and PAT not in json.dumps(r.as_dict())


def test_a_failed_submit_sends_no_cancel():
    ws = FakeWorkspace()
    s = open_session(ws)
    ws.refuse = 503
    r = s.execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and not any(p.endswith("/cancel") for _, p, *_ in ws.calls)


def test_a_failed_statement_is_the_cells_error_without_a_token():
    ws = FakeWorkspace(fail="[TABLE_OR_VIEW_NOT_FOUND] nope; token " + PAT)
    r = open_session(ws).execute(CellRequest("SELECT * FROM nope", "sql"))
    assert r.status == "error" and "TABLE_OR_VIEW_NOT_FOUND" in r.outputs[0]["evalue"]
    assert PAT not in json.dumps(r.as_dict())


def test_refusals_say_what_to_check_and_never_repeat_a_token():
    with pytest.raises(dbsql.DatabricksError, match="HTTP 403") as e:
        open_session(FakeWorkspace(refuse=403))
    assert "CAN USE" in str(e.value) and PAT not in str(e.value) and TOKEN not in str(e.value)


def test_other_languages_and_dialects_are_refused():
    s = open_session(FakeWorkspace())
    with pytest.raises(Unsupported, match="SQL cells only"):
        s.execute(CellRequest("print(1)", "python"))
    with pytest.raises(Unsupported, match="not warehouse"):
        s.execute(CellRequest("SELECT TOP 1 1", "sql", "warehouse"))


def test_cancel_stops_the_statement_on_the_warehouse():
    ws = FakeWorkspace(polls=5)
    s = open_session(ws)
    ws.on_poll = lambda: ws.on_poll and (setattr(ws, "on_poll", None) or s.cancel())
    r = s.execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "cancelled" and ws.cancelled == ["s1"]
    assert s.cancel() is False  # nothing running


def test_close_ends_the_session_and_later_cells_are_refused():
    s = open_session(FakeWorkspace())
    s.close()
    s.close()
    with pytest.raises(SessionClosed):
        s.execute(CellRequest("SELECT 1", "sql"))


# ---------------------------------------------------------------- plan and catalog


def test_plan_runs_explain_for_one_statement():
    ws = FakeWorkspace()
    s = open_session(ws)
    assert "Physical Plan" in s.plan("SELECT * FROM orders")
    assert ws.calls[-1][3]["statement"] == "EXPLAIN SELECT * FROM orders"
    with pytest.raises(Unsupported, match="one statement"):
        s.plan("SELECT 1; SELECT 2")


def test_catalog_lists_unity_catalog_without_touching_the_warehouse():
    ws = FakeWorkspace()
    s = open_session(ws)
    assert [(t["name"], t["type"]) for t in s.catalog()] == [("orders", "table"), ("v_orders", "view")]
    s2 = open_session(ws, schema="")
    s2.schema_name = ""
    assert s2.catalog() == [{"catalog": "main", "schema": "sales", "name": "", "type": "schema"}]
    s2.catalog_name = ""
    assert [c["catalog"] for c in s2.catalog()] == ["main", "samples"]
    assert not any(p == "/api/2.0/sql/statements" for _, p, *_ in ws.calls)


# ---------------------------------------------------------------- tokens


def test_hosts_must_be_azure_workspaces_before_any_token_is_fetched():
    for host in ("https://evil.example.com", "http://adb-1.2.azuredatabricks.net", ""):
        with pytest.raises(dba.DatabricksAuthError):
            dba.provider_for({**CONN, "host": host}, run=lambda *a, **k: pytest.fail("ran"))


def test_u2m_uses_the_databricks_cli_and_caches_until_near_expiry(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    runs = []

    def run(args, **kw):
        runs.append(args)
        assert kw["timeout"] and kw["capture_output"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"access_token": TOKEN, "token_type": "Bearer", "expiry": "2030-01-01T00:00:00Z"}), stderr="")

    p = dba.provider_for(CONN, run=run, http=object())
    assert p.token() == TOKEN and p.token() == TOKEN and len(runs) == 1
    assert runs[0][1:] == ["auth", "token", "--host", HOST]


def test_cli_failures_say_how_to_sign_in_and_never_quote_output(monkeypatch):
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    p = dba.provider_for(CONN, run=lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=TOKEN, stderr="Error: refresh token is invalid " + PAT), http=object())
    with pytest.raises(dba.DatabricksAuthError, match="databricks auth login --host") as e:
        p.token()
    assert TOKEN not in str(e.value) and PAT not in str(e.value)
    p = dba.provider_for(CONN, run=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)),
                         http=object())
    with pytest.raises(dba.DatabricksAuthError, match="within a minute"):
        p.token()
    monkeypatch.setattr(dba, "find_cli", lambda: None)
    with pytest.raises(dba.DatabricksAuthError, match="isn't installed"):
        dba.provider_for(CONN, http=object()).token()


def test_cli_profiles_use_what_the_profile_holds(tmp_path, monkeypatch):
    cfg = tmp_path / ".databrickscfg"
    cfg.write_text(f"[pat]\nhost = {HOST}\ntoken = {PAT}\n\n[sp]\nhost = {HOST}/\nclient_id = abc\n"
                   f"client_secret = shh\n\n[cli]\nhost = {HOST}\nauth_type = databricks-cli\n\n"
                   "[other]\nhost = https://adb-9.9.azuredatabricks.net\n")
    conn = {"platform": "databricks", "host": HOST}
    p = dba.provider_for(conn | {"credential": {"method": "cli-profile", "profile": "pat"}}, cfg_path=cfg, http=object())
    assert p.method == "pat" and p.token() == PAT

    class Http:
        def post(self, url, data, auth, timeout):
            assert url == f"{HOST}/oidc/v1/token" and auth == ("abc", "shh") and data["grant_type"] == "client_credentials"
            return Resp(200, {"access_token": TOKEN, "expires_in": 3600})

    p = dba.provider_for(conn | {"credential": {"method": "cli-profile", "profile": "sp"}}, cfg_path=cfg, http=Http())
    assert p.method == "oauth-m2m" and p.token() == TOKEN
    monkeypatch.setattr(dba, "find_cli", lambda: "/usr/bin/databricks")
    seen = []
    p = dba.provider_for(conn | {"credential": {"method": "cli-profile", "profile": "cli"}}, cfg_path=cfg, http=object(),
                         run=lambda args, **k: seen.append(args) or SimpleNamespace(
                             returncode=0, stdout=json.dumps({"access_token": TOKEN}), stderr=""))
    assert p.token() == TOKEN and seen[0][-2:] == ["--profile", "cli"]
    with pytest.raises(dba.DatabricksAuthError, match="another workspace"):
        dba.provider_for(conn | {"credential": {"method": "cli-profile", "profile": "other"}}, cfg_path=cfg, http=object())
    with pytest.raises(dba.DatabricksAuthError, match="no profile named"):
        dba.provider_for(conn | {"credential": {"method": "cli-profile", "profile": "nope"}}, cfg_path=cfg, http=object())


def test_m2m_reads_the_secret_from_its_reference_only():
    conn = {**CONN, "credential": {"method": "oauth-m2m", "client_id": "abc", "secret": "env:DBX_SECRET"}}

    class Http:
        def post(self, url, data, auth, timeout):
            return Resp(200 if auth == ("abc", "s3cret") else 401, {"access_token": TOKEN})

    assert dba.provider_for(conn, http=Http(), environ={"DBX_SECRET": "s3cret"}).token() == TOKEN
    with pytest.raises(dba.DatabricksAuthError, match="DBX_SECRET isn't set"):
        dba.provider_for(conn, http=Http(), environ={}).token()
    with pytest.raises(dba.DatabricksAuthError, match="HTTP 401") as e:
        dba.provider_for(conn, http=Http(), environ={"DBX_SECRET": "wrong"}).token()
    assert "wrong" not in str(e.value)
    with pytest.raises(dba.DatabricksAuthError, match="must be a reference"):
        dba.provider_for({**conn, "credential": {**conn["credential"], "secret": "s3cret"}}, http=Http())


def test_a_connection_holding_a_token_is_refused():
    with pytest.raises(dba.DatabricksAuthError, match="token"):
        dba.provider_for({**CONN, "description": PAT}, http=object())


def test_credman_is_windows_only_here():
    import sys
    if sys.platform == "win32":
        pytest.skip("reads the real Credential Manager on Windows")
    with pytest.raises(dba.DatabricksAuthError, match="on Windows only"):
        dba.resolve_secret("credman:KratosForge/dbx")


def test_the_cli_is_found_only_in_absolute_path_folders(tmp_path, monkeypatch):
    import os
    import sys
    name = "databricks.exe" if sys.platform == "win32" else "databricks"
    planted = tmp_path / "here"
    planted.mkdir()
    (planted / name).write_text("")
    (planted / name).chmod(0o755)
    real = tmp_path / "bin"
    real.mkdir()
    monkeypatch.chdir(planted)
    env = {"PATH": os.pathsep.join(["", ".", str(real)]), "PATHEXT": ".EXE"}
    assert dba.find_cli(env) is None
    (real / name).write_text("")
    (real / name).chmod(0o755)
    assert dba.find_cli(env) == str(real / name)


def test_on_windows_only_databricks_exe_in_a_drive_folder_counts(tmp_path, monkeypatch):
    import sys
    scripts, both = tmp_path / "scripts", tmp_path / "both"
    for folder, names in ((scripts, ("databricks.cmd", "databricks.bat")), (both, ("databricks.cmd", "databricks.exe"))):
        folder.mkdir()
        for name in names:
            (folder / name).write_text("")
            (folder / name).chmod(0o755)
    # Without a drive letter, as a network share or \\?\ path has none.
    windows = sys.platform == "win32"
    no_drive = "\\\\?\\" + str(both) if windows else str(both)
    monkeypatch.setattr(dba.sys, "platform", "win32")
    env = {"PATHEXT": ".CMD;.BAT;.EXE"}
    assert dba.find_cli({**env, "PATH": no_drive}) is None
    assert dba._DRIVE.match("C:\\Program Files\\Databricks") and not dba._DRIVE.match("\\\\server\\share")
    if windows:
        assert dba.find_cli({**env, "PATH": str(scripts)}) is None
        assert dba.find_cli({**env, "PATH": str(both)}) == str(both / "databricks.exe")


@pytest.mark.parametrize("bad", ["../../api/2.0/clusters/delete", "wh1/x", "wh 1", "", "a" * 65])
def test_ids_are_checked_before_they_reach_a_url(bad):
    ws = FakeWorkspace()
    client = dbsql.Client(tokens(), ws, sleep=lambda s: None)
    for call in (client.warehouse, client.status, client.cancel):
        with pytest.raises(dbsql.DatabricksError, match="isn't a Databricks"):
            call(bad)
    assert ws.calls == []


def test_a_statement_running_too_long_is_cancelled_and_reported():
    ws = FakeWorkspace(polls=1000)
    now = [0.0]
    s = dbsql.DatabricksSqlSession(SessionConfig("nb"), dbsql.Client(tokens(), ws, sleep=lambda s: None), "wh1",
                                   sleep=lambda sec: now.__setitem__(0, now[0] + 60), clock=lambda: now[0],
                                   max_seconds=300)
    r = s.execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and "after 5 minutes" in r.outputs[0]["evalue"] and ws.cancelled == ["s1"]


def test_the_engine_says_cells_can_change_data_without_another_confirm():
    assert any("no further confirm" in x for x in registry.get("databricks-sql").limitations)


@pytest.mark.parametrize("status,path,code,hint", [
    (403, "/api/2.0/sql/warehouses/wh1", "", "CAN USE on the SQL warehouse."),
    (404, "/api/2.0/sql/warehouses/wh1", "", "Check the SQL warehouse ID"),
    (404, "/api/2.0/sql/statements/s1", "", "run the cell again"),
    (403, "/api/2.1/clusters/list", "", "CAN ATTACH TO"),
    (404, "/api/2.1/clusters/get", "", "Check the cluster ID"),
    (404, "/api/2.1/unity-catalog/metastore_summary", "", "no Unity Catalog metastore assigned"),
    (404, "/api/2.1/unity-catalog/catalogs", "METASTORE_DOES_NOT_EXIST", "no Unity Catalog metastore assigned"),
    (403, "/api/2.1/unity-catalog/tables", "", "USE CATALOG and USE SCHEMA"),
    (403, "/api/2.0/preview/scim/v2/Me", "", "its own user details"),
    (401, "/api/2.1/clusters/list", "", "Sign in again."),
    (429, "/api/2.0/sql/statements", "", "busy"),
    (403, "/api/2.0/other", "", "Ask a workspace admin"),
    (500, "/api/2.0/sql/warehouses", "", ""),
])
def test_each_api_says_what_to_check_when_it_refuses(status, path, code, hint):
    got = dbsql.refusal_hint(status, path, code)
    assert (hint in got) if hint else got == ""


def test_a_refusal_names_the_api_that_refused_and_never_a_token():
    resp = Resp(404, {"error_code": "NOT_FOUND", "message": "nothing here for " + PAT})
    msg = dbsql.Client._refusal(resp, "/api/2.1/clusters/get")
    assert msg.startswith("Databricks refused the request (HTTP 404).") and msg.endswith("Check the cluster ID and the workspace.")
    assert PAT not in msg and "warehouse" not in msg

# ---------------------------------------------------------------- retries (KF-REMOTE-RETRY, #1189)


def _flaky(ws, statuses, when):
    """The next calls matching ``when(method, path)`` answer ``statuses`` before the fake's own answer."""
    real, left = ws.request, list(statuses)

    def request(method, url, **kw):
        if left and when(method, url[len(HOST):]):
            ws.calls.append((method, url[len(HOST):], None, None, None))
            return Resp(left.pop(0), {"message": "busy " + PAT})
        return real(method, url, **kw)
    ws.request = request


def _submits(ws):
    return sum(1 for m, p, *_ in ws.calls if m == "POST" and p == "/api/2.0/sql/statements")


def test_throttled_and_briefly_unavailable_polls_are_tried_again():
    ws = FakeWorkspace(polls=2)
    _flaky(ws, [429, 503], lambda m, p: m == "GET" and p.startswith("/api/2.0/sql/statements/"))
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "ok" and ws.cancelled == []


def test_a_submit_is_resent_after_429_but_never_after_a_5xx():
    ws = FakeWorkspace()
    s = open_session(ws)
    _flaky(ws, [429], lambda m, p: m == "POST" and p == "/api/2.0/sql/statements")
    assert s.execute(CellRequest("SELECT 1", "sql")).status == "ok" and ws.n == 1 and _submits(ws) == 2
    ws = FakeWorkspace()
    s = open_session(ws)
    _flaky(ws, [503], lambda m, p: m == "POST" and p == "/api/2.0/sql/statements")
    r = s.execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "error" and _submits(ws) == 1 and "Tried" not in r.outputs[0]["evalue"]


def test_after_the_last_try_the_error_says_it_was_retried_without_a_token():
    ws = FakeWorkspace()
    _flaky(ws, [503] * 4, lambda m, p: p == "/api/2.0/sql/warehouses/wh1")
    with pytest.raises(dbsql.DatabricksError) as err:
        open_session(ws)
    assert str(err.value).startswith("Databricks refused the request (HTTP 503).")
    assert str(err.value).endswith("Tried 4 times.") and PAT not in str(err.value)


def test_a_cancel_is_tried_again_after_a_gateway_error():
    ws = FakeWorkspace(polls=3)
    _flaky(ws, [502], lambda m, p: p.endswith("/cancel"))
    _drop_on_poll(ws)
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert "was stopped on Shared" in r.outputs[0]["evalue"] and ws.cancelled == ["s1"]


def test_one_dropped_poll_neither_fails_the_cell_nor_stops_the_statement():
    ws = FakeWorkspace(polls=2)
    drops = [ConnectionError("reset")]

    def drop_once():
        if drops:
            raise drops.pop()
    ws.on_poll = drop_once
    r = open_session(ws).execute(CellRequest("SELECT 1", "sql"))
    assert r.status == "ok" and ws.cancelled == []


# ---------------------------------------------------------------- whole results (KF-DBX-CHUNKS, #1167)


class ChunkedWorkspace(FakeWorkspace):
    """Results split into ``chunks`` (lists of rows); the statement's reply carries chunk 0 and each
    chunk names the next, as the INLINE disposition does. ``row_limit`` is applied as the API does."""

    def __init__(self, chunks, columns, refuse_chunk=None, loop=False):
        super().__init__(rows=[r for c in chunks for r in c], columns=columns)
        self.chunks = chunks
        self.refuse_chunk = refuse_chunk
        self.loop = loop
        self.limits = []

    def _page(self, n, limit):
        before = sum(len(c) for c in self.chunks[:n])
        rows = self.chunks[n][:max(0, limit - before)] if limit else self.chunks[n]
        more = n + 1 < len(self.chunks) and (not limit or before + len(self.chunks[n]) < limit)
        page = {"chunk_index": n, "data_array": rows}
        if more or self.loop:
            page["next_chunk_index"] = 0 if self.loop else n + 1
        return page

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        if "/result/chunks/" in path:
            self.calls.append((method, path, headers, json, params))
            if self.refuse_chunk:
                return Resp(self.refuse_chunk, {"error_code": "PERMISSION_DENIED", "message": "no " + PAT})
            return Resp(200, self._page(int(path.rsplit("/", 1)[1]), self.limit))
        if path == "/api/2.0/sql/statements" and method == "POST":
            self.limits.append(json.get("row_limit"))
            self.limit = json.get("row_limit") or 0
        resp = super().request(method, url, headers, timeout, json, params)
        body = resp.json()
        if (body.get("manifest") or {}).get("schema", {}).get("columns"):
            body["result"] = self._page(0, self.limit)
            body["manifest"]["total_chunk_count"] = len(self.chunks)
            resp = Resp(200, body)
        return resp


TYPED = [{"name": "id", "type_name": "INT"}, {"name": "amount", "type_name": "DECIMAL", "type_precision": 10,
                                               "type_scale": 2},
         {"name": "at", "type_name": "TIMESTAMP"}, {"name": "day", "type_name": "DATE"},
         {"name": "ok", "type_name": "BOOLEAN"}, {"name": "tags", "type_name": "ARRAY", "type_text": "ARRAY<STRING>"}]


def _typed_rows(start, n):
    return [[str(i), f"{i}.50", f"2024-03-10T0{i % 10}:45:10.000Z", "2024-03-10", "true" if i % 2 else "false",
             '["a"]'] for i in range(start, start + n)]


def test_a_whole_result_reads_every_chunk_in_order_and_types_the_values():
    ws = ChunkedWorkspace([_typed_rows(0, 3), _typed_rows(3, 3), _typed_rows(6, 2)], TYPED)
    s = open_session(ws)
    r = s.execute(CellRequest("USE main; SELECT * FROM t", "sql", preview_rows=2, result_rows=100))
    assert r.status == "ok" and r.rows == 8 and r.table.num_rows == 8
    assert r.table.column("id").to_pylist() == list(range(8))
    assert str(r.table.schema.field("amount").type) == "decimal128(10, 2)"
    assert str(r.table.schema.field("at").type) == "timestamp[us]"   # UTC, no zone, as Fabric Spark keeps it
    assert r.table.column("at")[1].as_py().hour == 1
    assert r.table.column("ok").to_pylist()[:2] == [False, True]
    # The first statement runs as before; the last asks for one row more than allowed.
    assert ws.limits == [None, 101]
    assert [p for _, p, *_ in ws.calls if "/result/chunks/" in p] == \
        ["/api/2.0/sql/statements/s2/result/chunks/1", "/api/2.0/sql/statements/s2/result/chunks/2"]
    assert r.outputs[0] == {"type": "stream", "text": "Kept as text: tags (array<string>).\n"}
    assert r.outputs[1]["total_rows"] == 8 and len(r.outputs[1]["rows"]) == 2


def test_more_rows_than_asked_for_is_an_error_with_no_table():
    ws = ChunkedWorkspace([_typed_rows(0, 3), _typed_rows(3, 3)], TYPED)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=4))
    assert r.status == "error" and r.table is None and r.rows is None
    assert "more than the 4 rows" in r.outputs[0]["evalue"]


def test_a_result_over_the_byte_cap_is_an_error(monkeypatch):
    monkeypatch.setattr(dbsql, "RESULT_BYTES", 300)
    ws = ChunkedWorkspace([_typed_rows(0, 3), _typed_rows(3, 3)], TYPED)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "error" and r.table is None and "larger than" in r.outputs[0]["evalue"]


def test_a_refused_chunk_is_an_error_that_never_shows_the_token():
    ws = ChunkedWorkspace([_typed_rows(0, 2), _typed_rows(2, 2)], TYPED, refuse_chunk=403)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "error" and r.table is None
    assert PAT not in json.dumps(r.as_dict()) and "CAN USE" in r.outputs[0]["evalue"]


def test_chunks_that_go_round_stop_with_an_error():
    ws = ChunkedWorkspace([_typed_rows(0, 1)], TYPED, loop=True)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "error" and "out of order" in r.outputs[0]["evalue"]


def test_empty_chunks_that_never_end_stop_at_the_chunk_cap(monkeypatch):
    monkeypatch.setattr(dbsql, "MAX_CHUNKS", 5)
    ws = ChunkedWorkspace([_typed_rows(0, 1)] + [[] for _ in range(20)], TYPED)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "error" and "more than 5 chunks" in r.outputs[0]["evalue"]
    assert sum(1 for _, p, *_ in ws.calls if "/result/chunks/" in p) == 4


@pytest.mark.parametrize("first", [-1_000_000_000, "x"])
def test_the_first_chunk_index_from_the_warehouse_doesnt_steer_the_loop(first):
    ws = ChunkedWorkspace([_typed_rows(0, 1), _typed_rows(1, 1)], TYPED)
    page = ws._page
    ws._page = lambda n, limit: {**page(n, limit), "chunk_index": first} if n == 0 else page(n, limit)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "ok" and r.table.num_rows == 2


def test_a_chunk_index_that_skips_ahead_stops_with_an_error():
    ws = ChunkedWorkspace([_typed_rows(0, 1), _typed_rows(1, 1)], TYPED)
    ws._page, page = (lambda n, limit: {**page(n, limit), "next_chunk_index": 7} if n == 0 else page(n, limit)), ws._page
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql", result_rows=100))
    assert r.status == "error" and "out of order" in r.outputs[0]["evalue"]


def test_a_cell_that_ends_without_a_query_has_no_result_to_compare():
    r = open_session(ChunkedWorkspace([[]], TYPED)).execute(CellRequest("USE main", "sql", result_rows=10))
    assert r.status == "error" and "End the cell with a query" in r.outputs[0]["evalue"]


def test_two_columns_of_one_name_cant_be_compared():
    cols = [{"name": "x", "type_name": "INT"}, {"name": "x", "type_name": "INT"}]
    r = open_session(ChunkedWorkspace([[["1", "2"]]], cols)).execute(
        CellRequest("SELECT 1 AS x, 2 AS x", "sql", result_rows=10))
    assert r.status == "error" and "more than one column named x" in r.outputs[0]["evalue"]


def test_a_value_that_doesnt_read_as_its_type_stays_text_and_is_named():
    cols = [{"name": "n", "type_name": "INT"}]
    r = open_session(ChunkedWorkspace([[["1"], ["not a number"]]], cols)).execute(
        CellRequest("SELECT n FROM t", "sql", result_rows=10))
    assert r.status == "ok" and r.table.column("n").to_pylist() == ["1", "not a number"]
    assert "Kept as text: n (int)" in r.outputs[0]["text"]


def test_a_normal_cell_still_shows_the_first_chunk_only():
    ws = ChunkedWorkspace([_typed_rows(0, 3), _typed_rows(3, 3)], TYPED)
    r = open_session(ws).execute(CellRequest("SELECT * FROM t", "sql"))
    assert r.status == "ok" and r.table is None and ws.limits == [None]
    assert not any("/result/chunks/" in p for _, p, *_ in ws.calls)


def test_the_result_capability_says_the_rows_are_copied_onto_this_pc():
    note = registry.get("databricks-sql").capability("result")
    assert note.available and "copied onto this PC" in note.note

# ---------------------------------------------------------------- truthful cancels (KF-REMOTE-LIMIT, #1198)


def _limited(ws):
    now = [0.0]
    return dbsql.DatabricksSqlSession(SessionConfig("nb"), dbsql.Client(tokens(), ws, sleep=lambda s: None), "wh1",
                                      sleep=lambda sec: now.__setitem__(0, now[0] + 60), clock=lambda: now[0],
                                      max_seconds=300)


def test_the_time_limit_says_the_statement_was_stopped_only_when_it_was():
    ws = FakeWorkspace(polls=1000)
    r = _limited(ws).execute(CellRequest("SELECT 1", "sql"))
    assert "after 5 minutes. The statement was stopped on Shared." in r.outputs[0]["evalue"]
    ws = FakeWorkspace(polls=1000)
    s = _limited(ws)
    _flaky(ws, [403], lambda m, p: p.endswith("/cancel"))
    evalue = s.execute(CellRequest("SELECT 1", "sql")).outputs[0]["evalue"]
    assert "may still be running on Shared" in evalue and "query history" in evalue
    assert "cancelled" not in evalue and "stopped on" not in evalue and PAT not in evalue


def test_a_cancel_pressed_while_sending_that_is_refused_says_the_statement_may_still_run():
    ws = FakeWorkspace(polls=3)
    s = open_session(ws)
    real = ws.request

    def press_cancel_during_submit(method, url, **kw):
        if method == "POST" and url.endswith("/api/2.0/sql/statements"):
            assert s.cancel() is True  # no statement id yet: execute cancels it once submit returns
        return real(method, url, **kw)
    ws.request = press_cancel_during_submit
    _flaky(ws, [403, 403], lambda m, p: p.endswith("/cancel"))
    r = s.execute(CellRequest("SELECT 1", "sql"))
    evalue = r.outputs[0]["evalue"]
    assert r.status == "cancelled" and evalue.startswith("Cancelled. The statement may still be running on Shared")
    assert "HTTP 403" not in evalue and PAT not in json.dumps(r.as_dict())


@pytest.mark.parametrize("rows", [[["1"], ["2", "x"]], [["1", "a", "extra"]], [{"id": "1", "name": "a"}]],
                         ids=["short", "long", "not-a-list"])
def test_a_preview_whose_rows_dont_match_the_columns_is_an_error_never_padded(rows):
    seen = []
    r = open_session(FakeWorkspace(rows=rows)).execute(CellRequest("SELECT id, name FROM t", "sql"),
                                                        on_output=seen.append)
    assert r.status == "error" and r.table is None and seen == r.outputs
    assert "one value per column" in r.outputs[-1]["evalue"]
    with pytest.raises(dbsql.DatabricksError, match="one value per column"):
        dbsql.to_table({"schema": {"columns": [{"name": "id"}, {"name": "name"}]}}, rows)
