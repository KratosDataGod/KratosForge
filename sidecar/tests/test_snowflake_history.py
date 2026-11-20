"""Your recent Snowflake queries (SF-005), against a fake of the documented SQL API: nothing is sent
without an authorization for the account and warehouse (it uses credits), the window and the rows are
bounded, each query gives its id, status, run time, warehouse and rows, and its text is redacted and cut."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_history as sh
from fabric_desktop.engines import snowflake_remote as sr
from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.contract import Unsupported

HOST = "https://myorg-myaccount.snowflakecomputing.com"
TOKEN = "tok-" + "abc123" * 4
CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "ANALYST",
        "database": "SALES", "schema": "RAW", "warehouse": "DEV_WH", "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret
TARGET = "myorg-myaccount/DEV_WH"
COLUMNS = ["QUERY_ID", "QUERY_TEXT", "QUERY_TYPE", "EXECUTION_STATUS", "ERROR_CODE", "ERROR_MESSAGE", "WAREHOUSE_NAME",
           "WAREHOUSE_SIZE", "ROLE_NAME", "DATABASE_NAME", "SCHEMA_NAME", "START_TIME", "END_TIME",
           "TOTAL_ELAPSED_TIME", "ROWS_PRODUCED", "BYTES_SCANNED"]
# A credential-shaped string, built up so no scanner takes it for real.
SECRET_SQL = "CREATE USER x PASSWORD = '" + "Hunter2" + "-not-real'"


def query(qid: str, text: str = "SELECT 1", status: str = "SUCCESS", **kw: Any) -> list[Any]:
    row = {"QUERY_ID": qid, "QUERY_TEXT": text, "QUERY_TYPE": "SELECT", "EXECUTION_STATUS": status,
           "ERROR_CODE": None, "ERROR_MESSAGE": None, "WAREHOUSE_NAME": "DEV_WH", "WAREHOUSE_SIZE": "X-Small",
           "ROLE_NAME": "ANALYST", "DATABASE_NAME": "SALES", "SCHEMA_NAME": "RAW",
           "START_TIME": "1616173619.000000000", "END_TIME": "1616173620.250000000",
           "TOTAL_ELAPSED_TIME": "1250", "ROWS_PRODUCED": "1", "BYTES_SCANNED": "0"} | kw
    return [row[c] for c in COLUMNS]


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


class FakeSnowflake:
    """The SQL API: an async-free submit answers 202 with a handle, a look gives the first partition,
    ``?partition=N`` later ones. Records every request."""

    def __init__(self, partitions: list[list[list[Any]]] | None = None, status: int = 0, fail: str = ""):
        self.partitions = partitions if partitions is not None else [[query("q-1"), query("q-2")]]
        self.status, self.fail = status, fail
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        assert url.startswith(HOST)
        path = url[len(HOST):]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body})
        if self.status:
            return Resp(self.status, {"code": "390144", "message": "Token is invalid: " + TOKEN, "sqlState": "08001"})
        if method == "POST":
            return Resp(202, {"statementHandle": "01b2-0001"})
        m = re.fullmatch(r"/api/v2/statements/([0-9a-f-]+)(?:\?partition=(\d+))?", path)
        assert m, path
        if self.fail:
            return Resp(422, {"code": "002003", "sqlState": "02000", "message": self.fail})
        if m[2] is not None:
            return Resp(200, {"data": self.partitions[int(m[2])]})
        return Resp(200, {"resultSetMetaData": {"rowType": [{"name": c} for c in COLUMNS],
                                                "partitionInfo": [{"rowCount": len(p)} for p in self.partitions]},
                          "data": self.partitions[0], "statementHandle": m[1]})

    def submitted(self) -> list[dict[str, Any]]:
        return [c["body"] for c in self.calls if c["method"] == "POST"]


def client(fake: FakeSnowflake) -> sr.Client:
    return sr.Client(sa.TokenProvider("myorg-myaccount", "pat", lambda: (TOKEN, None)), http=fake, sleep=lambda s: None)


def ok(target: str = TARGET) -> auth.RemoteAuthorization:
    return auth.confirmed(sw.SNOWFLAKE_SQL.id, target, "me")


def history(fake: FakeSnowflake, authorization: Any = None, conn: dict[str, Any] | None = None, **kw: Any):
    return sh.query_history(conn or CONN, ok() if authorization is None else authorization, client=client(fake), **kw)


def test_nothing_is_sent_without_an_authorization_for_this_account_and_warehouse():
    fake = FakeSnowflake()
    with pytest.raises(RemoteNotAuthorized):
        sh.query_history(CONN, None, client=client(fake))
    with pytest.raises(RemoteNotAuthorized, match="not snowflake-sql on myorg-myaccount/DEV_WH"):
        history(fake, ok("myorg-myaccount/OTHER_WH"))
    expired = auth.confirmed(sw.SNOWFLAKE_SQL.id, TARGET, "me", now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
    with pytest.raises(RemoteNotAuthorized, match="expired"):
        history(fake, expired)
    with pytest.raises(Unsupported, match="Pick a warehouse"):
        history(fake, conn=CONN | {"warehouse": ""})
    with pytest.raises(Unsupported, match="Set a database"):
        history(fake, conn=CONN | {"database": ""})
    assert fake.calls == []


def test_the_query_names_the_window_and_limit_and_runs_on_the_approved_warehouse():
    fake = FakeSnowflake()
    out = history(fake, hours=6, limit=50)
    [body] = fake.submitted()
    sql = body["statement"]
    assert "FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(" in sql
    assert "END_TIME_RANGE_START => DATEADD('hour', -6, CURRENT_TIMESTAMP())" in sql and "RESULT_LIMIT => 50" in sql
    assert "USER_NAME" not in sql and "ACCOUNT_USAGE" not in sql and "END_TIME_RANGE_END" not in sql
    assert body["warehouse"] == "DEV_WH" and body["role"] == "ANALYST" and body["database"] == "SALES"
    assert body["parameters"] == {"MULTI_STATEMENT_COUNT": "1"}
    assert sw.read_only_statements(sql) == [sql]
    assert out["warehouse"] == "DEV_WH" and out["hours"] == 6 and "uses Snowflake credits" in out["note"]


def test_each_query_gives_status_duration_warehouse_rows_and_id():
    fake = FakeSnowflake([[query("q-1", status="FAILED_WITH_ERROR", ERROR_CODE="2003",
                                 ERROR_MESSAGE="Object 'X' does not exist.\nOr not authorized."),
                           query("q-2", status="RUNNING", END_TIME=None, TOTAL_ELAPSED_TIME="900", ROWS_PRODUCED=None)]])
    first, second = history(fake)["queries"]
    assert first == {"query_id": "q-1", "status": "failed_with_error", "query_type": "SELECT", "text": "SELECT 1",
                     "text_cut": False, "error_code": "2003", "error": "Object 'X' does not exist. Or not authorized.",
                     "warehouse": "DEV_WH", "warehouse_size": "X-Small", "role": "ANALYST", "database": "SALES",
                     "schema": "RAW", "started": "2021-03-19T17:06:59.000Z", "ended": "2021-03-19T17:07:00.250Z",
                     "duration_ms": 1250, "rows": 1, "bytes_scanned": 0}
    assert second["status"] == "running" and second["ended"] == "" and second["rows"] is None


def test_the_text_is_redacted_before_it_is_cut():
    fake = FakeSnowflake([[query("q-1", text=SECRET_SQL), query("q-2", text="SELECT " + "x, " * 400)]])
    first, second = history(fake, text_chars=100)["queries"]
    assert "Hunter2" not in first["text"] and "[removed]" in first["text"]
    assert len(second["text"]) == 100 and second["text_cut"] is True
    assert "LEFT(query_text, 4000)" in fake.submitted()[0]["statement"]


def test_the_reading_query_is_left_out_and_later_partitions_are_read_up_to_the_limit():
    fake = FakeSnowflake([[query("01b2-0001"), query("q-1")], [query("q-2"), query("q-3")], [query("q-4")]])
    out = history(fake, limit=2)
    assert [q["query_id"] for q in out["queries"]] == ["q-1", "q-2"]
    assert [c["path"] for c in fake.calls if "partition=" in c["path"]] == ["/api/v2/statements/01b2-0001?partition=1"]


@pytest.mark.parametrize("kw, what", [
    (dict(hours=0), "time window"), (dict(hours=sh.MAX_HOURS + 1), "time window"),
    (dict(limit=0), "number of queries"), (dict(limit=sh.MAX_ROWS + 1), "number of queries"),
    (dict(text_chars=sh.TEXT_MAX + 1), "characters"), (dict(hours=True), "time window"),
])
def test_the_window_rows_and_text_are_bounded_before_any_request(kw, what):
    fake = FakeSnowflake()
    with pytest.raises(ValueError, match=f"{what}.*Ask for a number in that range"):
        history(fake, **kw)
    assert fake.calls == []


def test_the_window_stays_inside_snowflakes_seven_days():
    assert sh.MAX_HOURS < 7 * 24


def test_a_refused_request_keeps_snowflakes_code_and_never_the_token():
    fake = FakeSnowflake(status=401)
    with pytest.raises(sr.SnowflakeError) as err:
        history(fake)
    assert "HTTP 401" in str(err.value) and TOKEN not in str(err.value)
    fake = FakeSnowflake(fail="Function QUERY_HISTORY_BY_USER needs a database in use.")
    with pytest.raises(sr.SnowflakeError, match="code 002003"):
        history(fake)
