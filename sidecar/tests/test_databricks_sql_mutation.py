"""Databricks SQL engine paths that mutation testing (batch 13, docs/quality/mutation-testing.md) showed
no test checked: the two-hour limit and its message, the sign-in retry, Cancel and the retry wait,
whole results of one row, chunk sizes and offsets, and the session options. Fakes only."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import databricks_auth as dba
from fabric_desktop.engines import databricks_sql as dbsql
from fabric_desktop.engines.contract import CellRequest, SessionConfig

HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
TOKEN_A = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "QUFBQUFB" * 3
TOKEN_B = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "QkJCQkJC" * 3
CONN = {"platform": "databricks", "host": HOST, "credential": {"method": "oauth-u2m"},
        "catalog": "main", "schema": "sales", "compute": {"warehouse_id": "wh1"}}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = json.dumps(self._body).encode()
        self.headers = {}

    def json(self):
        return self._body


class Http:
    """Answers from a script: each call takes the next answer for its path (the last one repeats)."""

    def __init__(self, answers):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, json=None, params=None):
        path = url[len(HOST):]
        self.calls.append({"method": method, "path": path, "token": (headers or {}).get("Authorization", ""),
                           "timeout": timeout, "json": json})
        queue = self.answers.get(path) or [Resp(404, {"message": "no such path"})]
        return queue.pop(0) if len(queue) > 1 else queue[0]


WAREHOUSE = Resp(200, {"id": "wh1", "name": "Shared", "state": "RUNNING"})


def client(http, provider=None):
    return dbsql.Client(provider or dba.TokenProvider(HOST, lambda: (TOKEN_A, None), "test"), http, sleep=lambda s: None)


def session(http, **kw):
    return dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), client(http), "wh1", sleep=lambda s: None, **kw)


def done(rows=None, **result):
    rows = rows if rows is not None else [["1"]]
    return {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": "n", "type_name": "INT"}]}, "total_row_count": len(rows)},
            "result": {"data_array": rows, **result}}


class Clock:
    def __init__(self, start, step):
        self.now, self.step = start, step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def test_a_statement_is_followed_for_two_hours_then_stopped():
    assert dbsql.MAX_SECONDS == 7200
    pending = Resp(200, {"statement_id": "s1", "status": {"state": "RUNNING"}})
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [pending],
                 "/api/2.0/sql/statements/s1": [pending], "/api/2.0/sql/statements/s1/cancel": [Resp(200, {})]})
    s = dbsql.DatabricksSqlSession(SessionConfig("nb", options={}), client(http), "wh1", sleep=lambda s: None,
                                   clock=Clock(50_000.0, 600.0))
    out = s.execute(CellRequest("SELECT 1", "sql"))
    assert out.status == "error"
    assert out.outputs[0]["evalue"].startswith("The statement was still running after 120 minutes.")
    polls = [c for c in http.calls if c["path"] == "/api/2.0/sql/statements/s1"]
    assert len(polls) == 12   # one poll every ten minutes of the fake clock, then the limit


def test_the_time_limit_counts_from_when_the_statement_was_sent_and_says_its_minutes():
    pending = Resp(200, {"statement_id": "s1", "status": {"state": "PENDING"}})
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [pending],
                 "/api/2.0/sql/statements/s1": [pending, pending, Resp(200, done())],
                 "/api/2.0/sql/statements/s1/cancel": [Resp(200, {})]})
    ok = session(http, clock=Clock(1_000_000.0, 1.0), max_seconds=3600)
    assert ok.execute(CellRequest("SELECT 1", "sql")).status == "ok"
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [pending],
                 "/api/2.0/sql/statements/s1": [pending], "/api/2.0/sql/statements/s1/cancel": [Resp(200, {})]})
    late = session(http, clock=Clock(0.0, 1801.0), max_seconds=3600)
    evalue = late.execute(CellRequest("SELECT 1", "sql")).outputs[0]["evalue"]
    assert evalue.startswith("The statement was still waiting to start after 60 minutes")
    assert evalue.endswith("The statement was stopped on Shared.")


def test_an_error_cell_reports_its_own_running_time():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, {"statement_id": "s1", "status": {
                     "state": "FAILED", "error": {"message": "Table not found"}}})]})
    out = session(http).execute(CellRequest("SELECT * FROM nope", "sql"))
    assert out.status == "error" and 0 <= out.seconds < 60


def test_a_plain_cell_sends_no_row_limit_and_a_whole_result_of_one_row_asks_for_two():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [Resp(200, done())]})
    s = session(http)
    assert s.execute(CellRequest("SELECT 1", "sql")).status == "ok"
    submits = [c["json"] for c in http.calls if c["path"] == "/api/2.0/sql/statements"]
    assert "row_limit" not in submits[0]
    whole = s.execute(CellRequest("SELECT 1", "sql", result_rows=1))
    submits = [c["json"] for c in http.calls if c["path"] == "/api/2.0/sql/statements"]
    assert submits[1]["row_limit"] == 2
    assert whole.table is not None and whole.table.to_pylist() == [{"n": 1}]


def test_a_whole_results_preview_starts_at_its_first_row():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, done([["1"], ["2"], ["3"]]))]})
    out = session(http).execute(CellRequest("SELECT n", "sql", result_rows=10, preview_rows=2))
    table = [o for o in out.outputs if o["type"] == "table"][0]
    assert table["rows"] == [[1], [2]]


def test_a_first_chunk_that_says_it_starts_at_row_zero_is_read():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE],
                 "/api/2.0/sql/statements": [Resp(200, done([["7"]], row_offset=0, row_count=1))]})
    out = session(http).execute(CellRequest("SELECT n", "sql"))
    assert out.status == "ok" and out.outputs[0]["rows"] == [[7]]


def test_a_whole_result_of_exactly_the_size_limit_is_kept(monkeypatch):
    rows = [["1"], ["2"]]
    monkeypatch.setattr(dbsql, "RESULT_BYTES", len(json.dumps(rows)), raising=False)
    import fabric_desktop.engines.local_sql as local_sql
    s = session(Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE]}))
    assert len(s._all_rows(done(rows), 10)) == 2
    monkeypatch.setattr(dbsql, "RESULT_BYTES", len(json.dumps(rows)) - 1, raising=False)
    with pytest.raises(local_sql.ResultTooLarge):
        s._all_rows(done(rows), 10)


def test_a_result_chunk_that_isnt_an_object_is_refused_by_name():
    with pytest.raises(dbsql.DatabricksError, match="a result chunk this engine can't read"):
        dbsql._result_rows(["1"], 0)


def test_a_refused_token_is_tried_once_more_with_a_new_one():
    issued = [TOKEN_A, TOKEN_B]
    provider = dba.TokenProvider(HOST, lambda: (issued[0], None), "test")
    real_drop = provider.drop

    def drop(token):
        real_drop(token)
        if issued[0] == token and len(issued) > 1:
            issued.pop(0)
    provider.drop = drop
    http = Http({"/api/2.0/sql/warehouses/wh1": [Resp(401, {"message": "expired"}), WAREHOUSE]})
    assert client(http, provider).warehouse("wh1")["name"] == "Shared"
    assert [c["token"] for c in http.calls] == [f"Bearer {TOKEN_A}", f"Bearer {TOKEN_B}"]


def test_a_sign_in_that_cant_be_renewed_is_a_401_error():
    provider = dba.TokenProvider(HOST, lambda: (TOKEN_A, None), "test")
    provider.sign_in_again = "Run databricks auth login again."
    http = Http({"/api/2.0/sql/warehouses/wh1": [Resp(401, {"message": "expired"})]})
    with pytest.raises(dbsql.DatabricksError) as caught:
        client(http, provider).warehouse("wh1")
    assert caught.value.status == 401 and "Run databricks auth login again." in str(caught.value)


def test_requests_wait_a_minute_at_most_for_the_workspace():
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE]})
    client(http).warehouse("wh1")
    assert http.calls[0]["timeout"] == 60


def test_cancel_is_tried_again_after_cancel_was_pressed_and_other_calls_are_not():
    busy = Resp(429, {"message": "busy"})
    http = Http({"/api/2.0/sql/statements/s1/cancel": [busy, Resp(200, {})],
                 "/api/2.0/sql/statements/s1": [busy, Resp(200, done())]})
    c = client(http)
    c.stopped = lambda: True
    c.cancel("s1")
    assert [x["path"] for x in http.calls] == ["/api/2.0/sql/statements/s1/cancel"] * 2
    with pytest.raises(dbsql.DatabricksError, match="HTTP 429"):
        c.status("s1")
    assert len([x for x in http.calls if x["path"] == "/api/2.0/sql/statements/s1"]) == 1


def test_the_adapter_describes_this_engine_and_a_cells_catalog_and_schema_win():
    adapter = dbsql.DatabricksSqlAdapter(client_for=lambda conn: client(Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE]})),
                                         sleep=lambda s: None)
    assert adapter.describe() is dbsql.DATABRICKS_SQL
    ok = auth.confirmed("databricks-sql", "wh1", "chris")
    s = adapter.open_session(SessionConfig("nb", options={"connection": CONN, "catalog": "dev", "schema": ""}), ok)
    assert (s.catalog_name, s.schema_name) == ("dev", "sales")


def test_a_new_token_that_is_refused_too_is_forgotten():
    fetched = []
    tokens = iter([TOKEN_A, TOKEN_B, "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "Q0NDQ0ND" * 3])

    def fetch():
        fetched.append(next(tokens))
        return fetched[-1], None
    provider = dba.TokenProvider(HOST, fetch, "test")
    http = Http({"/api/2.0/sql/warehouses/wh1": [Resp(401, {"message": "expired"}), Resp(401, {"message": "expired"}),
                                                 WAREHOUSE]})
    c = client(http, provider)
    with pytest.raises(dbsql.DatabricksError, match="HTTP 401"):
        c.warehouse("wh1")
    assert c.warehouse("wh1")["name"] == "Shared"
    assert [x["token"] for x in http.calls] == [f"Bearer {t}" for t in fetched] and len(fetched) == 3


def test_plan_asks_the_warehouse_for_every_line_of_the_plan():
    explain = {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
               "manifest": {"schema": {"columns": [{"name": "plan", "type_name": "STRING"}]}},
               "result": {"data_array": [["== Physical Plan =="], ["Scan main.sales.orders"]]}}
    http = Http({"/api/2.0/sql/warehouses/wh1": [WAREHOUSE], "/api/2.0/sql/statements": [Resp(200, explain)]})
    assert session(http).plan("SELECT * FROM orders") == "== Physical Plan ==\nScan main.sales.orders"
    (submit,) = [c["json"] for c in http.calls if c["path"] == "/api/2.0/sql/statements"]
    assert submit["statement"] == "EXPLAIN SELECT * FROM orders" and "row_limit" not in submit
