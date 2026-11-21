"""Snowflake warehouse credits (SF-006), against a fake of the documented SQL API: nothing is sent without
an authorization for the account and warehouse (it uses credits), the window and rows are bounded, a role
that can't read ACCOUNT_USAGE is refused with the grant it needs, the result is credits only with the
view's documented latency, and no message carries the token."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import pytest

from fabric_desktop.engines import authorization as auth
from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_credits as sc
from fabric_desktop.engines import snowflake_remote as sr
from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.contract import Unsupported

HOST = "https://myorg-myaccount.snowflakecomputing.com"
TOKEN = "tok-" + "abc123" * 4
CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "ANALYST",
        "database": "SALES", "schema": "RAW", "warehouse": "DEV_WH", "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret
TARGET = "myorg-myaccount/DEV_WH"
COLUMNS = ["USAGE_DAY", "WAREHOUSE_NAME", "CREDITS_COMPUTE", "CREDITS_CLOUD_SERVICES", "CREDITS_USED"]
DENIED = "SQL compilation error:\nDatabase 'SNOWFLAKE' does not exist or not authorized."


def day(date: str, wh: str, compute: Any, cloud: Any, used: Any = None) -> list[Any]:
    if used is None:
        used = str(float(compute) + float(cloud))
    return [date, wh, compute, cloud, used]


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


class FakeSnowflake:
    """The SQL API: a submit answers 202 with a handle, a look gives the first partition, ``?partition=N``
    later ones, or a refusal with Snowflake's code and message. Records every request."""

    def __init__(self, partitions: list[list[list[Any]]] | None = None, status: int = 0,
                 fail: tuple[str, str] | None = None):
        self.partitions = partitions if partitions is not None else [[
            day("2026-11-21", "DEV_WH", "1.5", "0.02"), day("2026-11-21", "ETL_WH", "4.25", "0.1"),
            day("2026-11-20", "DEV_WH", "2", "0.03")]]
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
            code, message = self.fail
            return Resp(422, {"code": code, "sqlState": "02000", "message": message + " " + TOKEN})
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


def credits(fake: FakeSnowflake, authorization: Any = None, conn: dict[str, Any] | None = None, **kw: Any):
    return sc.warehouse_credits(conn or CONN, ok() if authorization is None else authorization, client=client(fake), **kw)


def test_nothing_is_sent_without_an_authorization_for_this_account_and_warehouse():
    fake = FakeSnowflake()
    with pytest.raises(RemoteNotAuthorized):
        sc.warehouse_credits(CONN, None, client=client(fake))
    with pytest.raises(RemoteNotAuthorized, match="not snowflake-sql on myorg-myaccount/DEV_WH"):
        credits(fake, ok("myorg-myaccount/OTHER_WH"))
    with pytest.raises(RemoteNotAuthorized):
        credits(fake, auth.confirmed("databricks-sql", TARGET, "me"))
    expired = auth.confirmed(sw.SNOWFLAKE_SQL.id, TARGET, "me", now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
    with pytest.raises(RemoteNotAuthorized, match="expired"):
        credits(fake, expired)
    with pytest.raises(Unsupported, match="Pick a warehouse"):
        credits(fake, conn=CONN | {"warehouse": ""})
    assert fake.calls == []


def test_the_confirm_step_says_what_would_run_and_sends_nothing():
    plan = sc.confirm(CONN, days=3, limit=10)
    assert plan["needs_confirm"] is True and plan["engine"] == sw.SNOWFLAKE_SQL.id and plan["target"] == TARGET
    assert plan["statement"] == sc.statement(3, 10) and "uses Snowflake credits" in plan["note"]
    assert "Confirm" in plan["note"]
    with pytest.raises(Unsupported, match="Pick a warehouse"):
        sc.confirm(CONN | {"warehouse": ""})
    with pytest.raises(ValueError, match="number of days"):
        sc.confirm(CONN, days=sc.MAX_DAYS + 1)


def test_the_query_reads_the_metering_view_on_the_approved_warehouse_with_the_window_and_limit():
    fake = FakeSnowflake()
    out = credits(fake, days=14, limit=50)
    [body] = fake.submitted()
    sql = body["statement"]
    assert "FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY" in sql
    assert "DATEADD('day', -13, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE)" in sql and sql.endswith("LIMIT 51")
    assert "credits_used_compute" in sql and "credits_used_cloud_services" in sql
    assert body["warehouse"] == "DEV_WH" and body["role"] == "ANALYST" and "database" not in body
    assert body["parameters"] == {"MULTI_STATEMENT_COUNT": "1"}
    assert sw.read_only_statements(sql) == [sql]
    assert out["warehouse"] == "DEV_WH" and out["days"] == 14 and out["limit"] == 50


@pytest.mark.parametrize("kw, what", [
    (dict(days=0), "number of days"), (dict(days=sc.MAX_DAYS + 1), "number of days"), (dict(days=True), "number of days"),
    (dict(limit=0), "number of rows"), (dict(limit=sc.MAX_ROWS + 1), "number of rows"), (dict(limit="5"), "number of rows"),
])
def test_the_window_and_rows_are_bounded_before_any_request(kw, what):
    fake = FakeSnowflake()
    with pytest.raises(ValueError, match=f"{what}.*Ask for a number in that range"):
        credits(fake, **kw)
    assert fake.calls == []


def test_the_window_is_at_most_31_days():
    assert sc.MAX_DAYS == 31


def test_the_row_cap_keeps_the_newest_rows_and_says_the_list_was_cut():
    rows = [day(f"2026-11-{d:02d}", "DEV_WH", "1", "0") for d in range(21, 15, -1)]
    fake = FakeSnowflake([rows[:3], rows[3:]])
    out = credits(fake, limit=4)
    assert [r["date"] for r in out["rows"]] == ["2026-11-21", "2026-11-20", "2026-11-19", "2026-11-18"]
    assert out["more"] is True and out["totals"]["credits"] == 4.0
    assert "Only the newest 4 rows" in out["note"]
    assert [c["path"] for c in fake.calls if "partition=" in c["path"]] == ["/api/v2/statements/01b2-0001?partition=1"]
    assert credits(FakeSnowflake(), limit=10)["more"] is False


def test_the_result_is_credits_only_with_the_compute_and_cloud_services_split_and_the_documented_latency():
    out = credits(FakeSnowflake())
    assert out["unit"] == "snowflake_credits" and out["source"] == "SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY"
    assert out["rows"][0] == {"date": "2026-11-21", "warehouse": "DEV_WH", "credits": 1.52, "credits_compute": 1.5,
                              "credits_cloud_services": 0.02}
    assert out["totals"] == {"credits": 7.9, "credits_compute": 7.75, "credits_cloud_services": 0.15}
    assert out["latency_hours"] == 3 and out["cloud_services_latency_hours"] == 6
    assert "up to 3 hours" in out["note"] and "up to 6 hours" in out["note"]
    assert "may be more than the credits billed" in out["note"] and "uses Snowflake credits" in out["note"]
    text = json.dumps(out).lower()
    for word in ("savings", "saving", "usd", "eur", "$", "€", "£", "price", "cost", "currency", "dollar"):
        assert word not in text, word


def test_a_figure_that_isnt_a_credit_amount_is_left_out_not_guessed():
    fake = FakeSnowflake([[day("2026-11-21", "DEV_WH", "1", "0"), day("2026-11-21", "BAD_WH", "x", "0", "1"),
                           day("2026-11-20", "NEG_WH", "-1", "0", "-1")]])
    out = credits(fake)
    assert [r["warehouse"] for r in out["rows"]] == ["DEV_WH"] and out["dropped"] == 2
    assert "2 rows had a figure that isn't a credit amount" in out["note"]


@pytest.mark.parametrize("code, message", [
    ("002003", DENIED),
    ("002003", "SQL compilation error:\nSchema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not authorized."),
    ("002003", "Object 'SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY' does not exist or not authorized."),
    ("003001", "Insufficient privileges to operate on schema 'ACCOUNT_USAGE'"),
])
def test_a_role_that_cant_read_account_usage_is_refused_with_the_grant_it_needs(code, message):
    fake = FakeSnowflake(fail=(code, message))
    with pytest.raises(sc.AccountUsageDenied) as err:
        credits(fake)
    text = str(err.value)
    assert text.startswith("Role ANALYST can't read SNOWFLAKE.ACCOUNT_USAGE")
    assert "IMPORTED PRIVILEGES on the SNOWFLAKE database" in text and "SNOWFLAKE.USAGE_VIEWER database role" in text
    assert "to role ANALYST" in text and TOKEN not in text and ":" not in text and ";" not in text
    assert err.value.code == code
    assert len(fake.submitted()) == 1   # refused once, never retried with another role or a grant


def test_the_default_role_is_named_as_such_when_the_connection_sets_none():
    with pytest.raises(sc.AccountUsageDenied, match="^Your default role can't read.*to your default role"):
        credits(FakeSnowflake(fail=("002003", DENIED)), conn=CONN | {"role": ""})


def test_another_refusal_keeps_snowflakes_code_and_isnt_mistaken_for_access():
    fake = FakeSnowflake(fail=("000606", "No active warehouse selected in the current session."))
    with pytest.raises(sr.SnowflakeError) as err:
        credits(fake)
    assert not isinstance(err.value, sc.AccountUsageDenied) and "code 000606" in str(err.value)


def test_no_message_carries_the_token():
    for fake in (FakeSnowflake(status=401), FakeSnowflake(fail=("002003", DENIED)),
                 FakeSnowflake(fail=("100038", "Numeric value 'abc' is not recognized"))):
        with pytest.raises(sr.SnowflakeError) as err:
            credits(fake)
        assert TOKEN not in str(err.value)
    out = credits(FakeSnowflake())
    assert TOKEN not in json.dumps(out) and TOKEN not in json.dumps(sc.confirm(CONN))
