"""The Snowflake SQL warehouse engine (SF-003), against a fake of the documented SQL API only: nothing is
sent without an authorization, write statements are refused before any request, results are typed from
rowType and read partition by partition, Cancel cancels on Snowflake, and errors keep Snowflake's code
but never the token."""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Any

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import registry
from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_remote as sr
from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.contract import CellRequest, SessionConfig, Unsupported

HOST = "https://myorg-myaccount.snowflakecomputing.com"
# A key-pair token as Snowflake takes one (header, claims, signature), built up so no scanner takes it for real.
TOKEN = "eyJ" + "hbGciOiJSUzI1NiJ9" + ".e30." + "c2lnbmF0dXJl" * 3
CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "ANALYST", "database": "SALES",
        "schema": "RAW", "warehouse": "DEV_WH", "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret
TARGET = "myorg-myaccount/DEV_WH"
CONFIG = SessionConfig("nb", options={"connection": CONN})


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


def result(row_type: list[dict[str, Any]], partitions: list[list[list[Any]]]) -> dict[str, Any]:
    """A ResultSet as the SQL API sends it, with every partition (the fake serves later ones on request)."""
    info = [{"rowCount": len(p), "uncompressedSize": len(json.dumps(p))} for p in partitions]
    return {"resultSetMetaData": {"numRows": sum(len(p) for p in partitions), "format": "jsonv2", "rowType": row_type,
                                  "partitionInfo": info}, "data": partitions[0], "partitions": partitions}


def col(name: str, kind: str, **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, "nullable": True} | kw


ORDERS = result([col("ID", "fixed", precision=38, scale=0), col("NAME", "text")],
                [[["1", "a"], ["2", "b"]], [["3", None]]])
TYPES = result([col("N", "fixed", precision=10, scale=0), col("AMT", "fixed", precision=10, scale=2),
                col("R", "real"), col("OK", "boolean"), col("D", "date"), col("TS", "timestamp_ntz", scale=9),
                col("LTZ", "timestamp_ltz", scale=9), col("TZ", "timestamp_tz", scale=9), col("T", "time", scale=9),
                col("V", "variant"), col("B", "binary")],
               [[["7", "12.50", "1.5", "true", "18262", "1616173619.000000000", "0.500000000",
                  "1616173619.000000000 1500", "82919.000000000", '{"a": 1}', "0AFF"]]])


class FakeSnowflake:
    """The SQL API: async submits get a handle, a look gives the ResultSet's first partition, ``?partition=N``
    gives later ones, cancel answers 200. Answers by statement prefix; records every request."""

    def __init__(self, replies: dict[str, Any] | None = None, fail: str = "", status: int = 0):
        self.replies = {"SELECT * FROM NO_SUCH": None, "EXPLAIN": result([col("content", "text")], [[["GlobalStats:"],
                        ["  TableScan ORDERS"]]]),
                        "SHOW OBJECTS": result([col("name", "text"), col("kind", "text"), col("database_name", "text"),
                                                col("schema_name", "text")], [[["ORDERS", "TABLE", "SALES", "RAW"],
                                                                              ["V_ORDERS", "VIEW", "SALES", "RAW"]]]),
                        "SELECT": ORDERS} | (replies or {})
        self.fail, self.status, self.hold = fail, status, False
        self.calls: list[dict[str, Any]] = []
        self.bodies: dict[str, dict[str, Any]] = {}
        self.on_look = lambda: None

    def reply(self, sql: str) -> Any:
        return next(r for prefix, r in self.replies.items() if sql.upper().startswith(prefix))

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        assert url.startswith(HOST)
        path = url[len(HOST):]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "headers": headers, "body": body})
        if self.status:
            return Resp(self.status, {"code": "390144", "message": "JWT token is invalid: " + TOKEN, "sqlState": "08001"})
        if method == "POST" and path.startswith("/api/v2/statements?"):
            handle = f"01b2-{len(self.bodies):04x}"
            self.bodies[handle] = body
            return Resp(202, {"statementHandle": handle})
        m = re.fullmatch(r"/api/v2/statements/([0-9a-f-]+)(/cancel)?(?:\?partition=(\d+))?", path)
        assert m, path
        if m[2]:
            return Resp(200, {"statementHandle": m[1]})
        self.on_look()
        if self.hold:
            return Resp(202, {"statementHandle": m[1]})
        answer = self.reply(self.bodies[m[1]]["statement"])
        if answer is None or self.fail:
            return Resp(422, {"code": "002003", "sqlState": "02000",
                              "message": self.fail or "Object 'NO_SUCH' does not exist or not authorized."})
        if m[3] is not None:
            return Resp(200, {"data": answer["partitions"][int(m[3])]})
        return Resp(200, {k: v for k, v in answer.items() if k != "partitions"} | {"statementHandle": m[1]})

    def sent(self, method: str = "POST") -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and not c["path"].endswith("/cancel")]


def adapter(fake: FakeSnowflake) -> sw.SnowflakeSqlAdapter:
    def client_for(conn):
        return sr.Client(sa.TokenProvider(conn["account"], "pat", lambda: (TOKEN, None)), http=fake, sleep=lambda s: None)
    return sw.SnowflakeSqlAdapter(client_for)


def ok() -> auth.RemoteAuthorization:
    return auth.confirmed(sw.SNOWFLAKE_SQL.id, TARGET, "me")


def open_session(fake: FakeSnowflake, **options: Any) -> sw.SnowflakeSqlSession:
    return adapter(fake).open_session(SessionConfig("nb", options={"connection": CONN} | options), ok())


def run(session: sw.SnowflakeSqlSession, code: str, **kw: Any):
    return session.execute(CellRequest(code, "sql", "snowflake", **kw))


# ---------------------------------------------------------------- nothing sent without a say-so


def test_nothing_is_sent_without_an_authorization_for_this_account_and_warehouse():
    fake = FakeSnowflake()
    with pytest.raises(RemoteNotAuthorized):
        adapter(fake).open_session(CONFIG)
    with pytest.raises(RemoteNotAuthorized, match="not snowflake-sql on myorg-myaccount/DEV_WH"):
        adapter(fake).open_session(CONFIG, auth.confirmed(sw.SNOWFLAKE_SQL.id, "other-account/DEV_WH", "me"))
    with pytest.raises(Unsupported, match="Pick a warehouse"):
        adapter(fake).open_session(SessionConfig("nb", options={"connection": CONN | {"warehouse": ""}}), ok())
    assert fake.calls == []


@pytest.mark.parametrize("code, name", [
    ("INSERT INTO t VALUES (1)", "INSERT"), ("select 1; delete from t", "DELETE"),
    ("WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a", "INSERT"), ("CREATE TABLE t AS SELECT 1", "CREATE"),
    ("MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DELETE", "MERGE"), ("CALL p()", "CALL"),
    ("GRANT ROLE a TO USER b", "GRANT"), ("COPY INTO t FROM @s", "COPY"), ("SELECT * INTO t FROM x", "SELECT ... INTO"),
    ("ALTER WAREHOUSE w SUSPEND", "ALTER"), ("TRUNCATE TABLE t", "TRUNCATE"), ("USE ROLE x", "USE"),
    ("SELECT * FROM t FOR UPDATE", "SELECT ... FOR UPDATE"),
])
def test_write_statements_are_refused_by_name_before_any_request(code, name):
    fake = FakeSnowflake()
    s = open_session(fake)
    with pytest.raises(Unsupported, match=re.escape(f"{name} changes data on Snowflake") + ".*Run it in Snowsight"):
        run(s, code)
    with pytest.raises(Unsupported):
        s.plan(code)
    assert fake.calls == []


def test_unreadable_sql_and_system_functions_are_refused_too():
    fake = FakeSnowflake()
    s = open_session(fake)
    with pytest.raises(Unsupported, match="couldn't read this one.*Check its syntax"):
        run(s, "SELECT FROM WHERE")
    with pytest.raises(Unsupported, match=r"SYSTEM\$CANCEL_ALL_QUERIES is a system function"):
        run(s, "SELECT SYSTEM$CANCEL_ALL_QUERIES(1)")
    with pytest.raises(Unsupported, match="no SQL"):
        run(s, "-- nothing\n;")
    assert fake.calls == []


@pytest.mark.parametrize("code", [
    "SELECT 1 ->> DELETE FROM t", "SHOW GRANTS ->> GRANT ROLE x TO USER y",
    "EXPLAIN USING TEXT SELECT 1 ->> DELETE FROM t", "SHOW TABLES ->> SELECT * FROM $1",
    "DESC TABLE t ->> DELETE FROM t", "EXPLAIN SELECT 1 ->> DELETE FROM t",
])
def test_a_flow_operator_chain_is_refused_before_any_request(code):
    fake = FakeSnowflake()
    s = open_session(fake)
    with pytest.raises(Unsupported, match="chains statements on Snowflake"):
        run(s, code)
    assert fake.calls == []


def test_flow_operator_text_in_strings_and_comments_is_not_a_chain():
    assert sw.read_only_statements("SELECT '->>' AS a -- ->> b") == ["SELECT '->>' AS a -- ->> b"]
    assert sw.read_only_statements("SHOW TABLES LIKE '->>'") == ["SHOW TABLES LIKE '->>'"]
    assert sw.read_only_statements("EXPLAIN USING TEXT SELECT '->>'") == ["EXPLAIN USING TEXT SELECT '->>'"]


def test_read_only_statements_pass_as_written():
    code = ("SELECT $$a;b$$; WITH a AS (SELECT 1) SELECT * FROM a; SELECT 1 UNION ALL SELECT 2; SHOW TABLES; "
            "SHOW GRANTS; DESC TABLE t; EXPLAIN SELECT 1; VALUES (1)")
    assert sw.read_only_statements(code)[0] == "SELECT $$a;b$$" and len(sw.read_only_statements(code)) == 8


def test_the_engine_never_uses_the_local_translator_or_another_engine():
    import inspect
    source = inspect.getsource(sw)
    imports = [line for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
    assert not any("snowflake_sql" in line or "local_sql import" in line and "ResultTooLarge" not in line
                   and "_too" not in line and "_table_output" not in line for line in imports)


# ---------------------------------------------------------------- running and results


def test_a_query_runs_on_the_warehouse_with_the_connection_context_and_previews_rows():
    fake = FakeSnowflake()
    r = run(open_session(fake), "SELECT id, name FROM orders")
    assert r.status == "ok" and r.rows == 3 and r.engine == "snowflake-sql"
    [out] = r.outputs
    assert out["type"] == "table" and out["rows"] == [[1, "a"], [2, "b"], [3, None]] and out["total_rows"] == 3
    [post] = fake.sent()
    assert post["path"].startswith("/api/v2/statements?requestId=") and post["path"].endswith("&async=true")
    assert {k: post["body"][k] for k in ("role", "database", "schema", "warehouse")} == {
        "role": "ANALYST", "database": "SALES", "schema": "RAW", "warehouse": "DEV_WH"}
    assert post["body"]["timeout"] == sw.MAX_SECONDS
    assert post["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert [c["path"] for c in fake.sent("GET")] == ["/api/v2/statements/01b2-0000",
                                                    "/api/v2/statements/01b2-0000?partition=1"]


def test_a_preview_reads_only_the_partitions_it_needs():
    fake = FakeSnowflake()
    r = run(open_session(fake), "SELECT id FROM orders", preview_rows=2)
    assert r.rows == 3 and r.outputs[0]["rows"] == [[1, "a"], [2, "b"]]
    assert not any("partition=" in c["path"] for c in fake.calls)


def test_several_statements_run_in_order_and_the_last_is_shown():
    fake = FakeSnowflake()
    r = run(open_session(fake), "SHOW OBJECTS IN SCHEMA SALES.RAW; SELECT id FROM orders")
    assert r.status == "ok" and r.rows == 3
    assert [c["body"]["statement"] for c in fake.sent()] == ["SHOW OBJECTS IN SCHEMA SALES.RAW", "SELECT id FROM orders"]


def test_values_are_typed_from_row_type():
    fake = FakeSnowflake({"SELECT": TYPES})
    r = run(open_session(fake), "SELECT * FROM t", result_rows=10)
    t = r.table.to_pylist()[0]
    assert t["N"] == 7 and t["AMT"] == Decimal("12.50") and t["R"] == 1.5 and t["OK"] is True
    assert t["D"] == dt.date(2020, 1, 1) and t["TS"] == dt.datetime(2021, 3, 19, 17, 6, 59)
    assert t["LTZ"] == dt.datetime(1970, 1, 1, 0, 0, 0, 500000, tzinfo=dt.timezone.utc)
    assert t["TZ"] == dt.datetime(2021, 3, 19, 17, 6, 59, tzinfo=dt.timezone.utc)   # shown in UTC, offset dropped
    assert t["T"] == dt.time(23, 1, 59) and t["V"] == '{"a": 1}' and t["B"] == "0AFF"
    assert r.outputs[0] == {"type": "stream", "text": "Kept as text: V (variant), B (binary).\n"}


def test_a_value_that_wont_read_as_its_type_keeps_the_column_as_text():
    table, as_text = sw.to_table([col("N", "fixed", precision=10, scale=0), col("D", "date")], [["x", "18262"]])
    assert table.to_pylist() == [{"N": "x", "D": dt.date(2020, 1, 1)}] and as_text == ["N (fixed)"]


def test_a_partition_that_doesnt_match_its_columns_or_count_is_an_error_not_a_short_table():
    bad = result([col("A", "text")], [[["1"]], [["2"]]])
    bad["partitions"][1] = [["2", "extra"]]
    r = run(open_session(FakeSnowflake({"SELECT": bad})), "SELECT a FROM t")
    assert r.status == "error" and "don't match its columns" in r.outputs[-1]["evalue"]
    short = result([col("A", "text")], [[["1"], ["2"]]])
    short["resultSetMetaData"]["partitionInfo"][0]["rowCount"] = 5
    r = run(open_session(FakeSnowflake({"SELECT": short})), "SELECT a FROM t")
    assert r.status == "error" and "holds 5 rows but sent 2" in r.outputs[-1]["evalue"]


# ---------------------------------------------------------------- whole results


def test_a_whole_result_reads_every_partition():
    fake = FakeSnowflake()
    r = run(open_session(fake), "SELECT id, name FROM orders", result_rows=3, preview_rows=1)
    assert r.status == "ok" and r.table.num_rows == 3 and r.outputs[-1]["rows"] == [[1, "a"]]
    assert "table" not in r.as_dict()


def test_a_whole_result_over_the_rows_asked_for_is_an_error_before_reading_more():
    fake = FakeSnowflake()
    r = run(open_session(fake), "SELECT id FROM orders", result_rows=2)
    assert r.status == "error" and r.table is None and "more than the 2 rows asked for" in r.outputs[-1]["evalue"]
    assert not any("partition=" in c["path"] for c in fake.calls)


def test_a_whole_result_over_result_bytes_is_an_error(monkeypatch):
    monkeypatch.setattr(sw, "RESULT_BYTES", 10)
    r = run(open_session(FakeSnowflake()), "SELECT id FROM orders", result_rows=100)
    assert r.status == "error" and r.table is None and "larger than" in r.outputs[-1]["evalue"]


def test_a_whole_result_shorter_than_snowflakes_count_or_with_two_columns_of_one_name_is_an_error():
    short = result([col("A", "text")], [[["1"]]])
    short["resultSetMetaData"]["numRows"] = 4
    r = run(open_session(FakeSnowflake({"SELECT": short})), "SELECT a FROM t", result_rows=10)
    assert r.status == "error" and "has 4 rows but sent 1" in r.outputs[-1]["evalue"]
    twice = result([col("A", "text"), col("A", "text")], [[["1", "2"]]])
    r = run(open_session(FakeSnowflake({"SELECT": twice})), "SELECT a, a FROM t", result_rows=10)
    assert r.status == "error" and "more than one column named A" in r.outputs[-1]["evalue"]


# ---------------------------------------------------------------- cancel and errors


def test_cancel_while_running_cancels_on_snowflake_and_ends_as_cancelled():
    fake = FakeSnowflake()
    fake.hold = True
    s = open_session(fake)
    fake.on_look = lambda: s.cancel()
    r = run(s, "SELECT id FROM orders")
    assert r.status == "cancelled" and r.outputs[-1]["evalue"] == "Cancelled."
    assert fake.calls[-1]["method"] == "POST" and fake.calls[-1]["path"] == "/api/v2/statements/01b2-0000/cancel"
    assert s.cancel() is False   # nothing running any more


def test_a_refused_sign_in_keeps_the_code_and_never_the_token():
    r = run(open_session(FakeSnowflake(status=401)), "SELECT 1")
    assert r.status == "error" and "HTTP 401, code 390144" in r.outputs[-1]["evalue"]
    assert "Check the connection's user name" in r.outputs[-1]["evalue"] and TOKEN not in json.dumps(r.as_dict())


def test_a_sql_error_keeps_snowflakes_code_and_never_the_token():
    r = run(open_session(FakeSnowflake(fail="SQL compilation error: no such table, token " + TOKEN)),
            "SELECT * FROM t")
    assert r.status == "error" and "HTTP 422, code 002003" in r.outputs[-1]["evalue"]
    assert "no such table" in r.outputs[-1]["evalue"] and TOKEN not in json.dumps(r.as_dict())


# ---------------------------------------------------------------- plan, catalog, contract


def test_plan_explains_without_the_warehouse_and_catalog_lists_objects():
    fake = FakeSnowflake()
    s = open_session(fake)
    assert s.plan("SELECT id FROM orders") == "GlobalStats:\n  TableScan ORDERS"
    assert s.catalog() == [{"catalog": "SALES", "schema": "RAW", "name": "ORDERS", "type": "table"},
                           {"catalog": "SALES", "schema": "RAW", "name": "V_ORDERS", "type": "view"}]
    assert [c["body"]["statement"] for c in fake.sent()] == ["EXPLAIN USING TEXT SELECT id FROM orders",
                                                             "SHOW OBJECTS IN SCHEMA SALES.RAW"]
    assert all("warehouse" not in c["body"] for c in fake.sent())
    with pytest.raises(Unsupported, match="tables, not variables"):
        s.variables()


def test_the_engine_is_registered_as_a_read_only_preview_that_uses_credits():
    d = registry.get("snowflake-sql")
    assert d is sw.SNOWFLAKE_SQL and d.adapter and d.preview and d.remote and d.billing == "snowflake_credits"
    assert isinstance(registry.adapter("snowflake-sql"), sw.SnowflakeSqlAdapter)
    assert "Preview: tested against fakes of the documented API, not yet against a live account." in d.limitations[0]
    assert any("resumes it, and a running warehouse uses credits until it suspends" in x for x in d.limitations)
    assert all(s.evidence == "unit_tests" for s in d.capabilities.values() if s.available)


def test_a_closed_session_refuses_cells():
    s = open_session(FakeSnowflake())
    s.close()
    assert s.closed
    with pytest.raises(Exception, match="was closed"):
        s.execute(CellRequest("SELECT 1", "sql"))
