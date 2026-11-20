"""Snowflake object listing (SF-004), against a fake of the documented SQL API: no statement sends a
warehouse, each runs as the connection's role, every list is capped and paged with SHOW's LIMIT ... FROM,
names are quoted and literals escaped, and a name the role can't see says what to ask for."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from fabric_desktop.engines import snowflake_auth as sa
from fabric_desktop.engines import snowflake_objects as so
from fabric_desktop.engines import snowflake_remote as sr

HOST = "https://myorg-myaccount.snowflakecomputing.com"
TOKEN = "tok-" + "abc123" * 4
CONN = {"platform": "snowflake", "account": "myorg-myaccount", "user": "jdoe", "role": "ANALYST",
        "database": "SALES", "schema": "RAW", "warehouse": "DEV_WH", "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret


class Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        return self._body


def answer(columns: list[str], *partitions: list[list[Any]]) -> dict[str, Any]:
    parts = list(partitions) or [[]]
    return {"resultSetMetaData": {"rowType": [{"name": c} for c in columns],
                                  "partitionInfo": [{"rowCount": len(p)} for p in parts]},
            "data": parts[0], "partitions": parts}


def names(*items: str, column: str = "name") -> dict[str, Any]:
    return answer([column], [[n] for n in items])


class FakeSnowflake:
    """The SQL API: a submit answers at once (200), ``?partition=N`` gives later partitions. Answers by
    statement prefix (longest first); SHOW ... LIMIT n FROM 'x' is served from ``tables`` as Snowflake
    documents it, rows sorted by name, including the cursor's own row. Records every request."""

    def __init__(self, replies: dict[str, Any] | None = None, status: int = 0):
        self.replies = replies or {}
        self.status = status
        self.calls: list[dict[str, Any]] = []
        self.answers: dict[str, dict[str, Any]] = {}

    def serve(self, sql: str) -> dict[str, Any]:
        for prefix in sorted(self.replies, key=len, reverse=True):
            if sql.startswith(prefix):
                reply = self.replies[prefix]
                return reply(sql) if callable(reply) else reply
        raise AssertionError(f"no reply for {sql}")

    def request(self, method: str, url: str, headers: dict[str, str], timeout: float, data: str | None = None, **_: Any):
        assert url.startswith(HOST)
        path = url[len(HOST):]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body})
        if self.status:
            return Resp(self.status, {"code": "390144", "message": "Token is invalid: " + TOKEN, "sqlState": "08001"})
        if method == "POST":
            handle = f"01b2-{len(self.answers):04x}"
            reply = self.serve(body["statement"])
            if reply.get("refused"):
                return Resp(422, {"code": "002003", "message": reply["refused"], "sqlState": "02000"})
            self.answers[handle] = reply
            return Resp(200, {k: v for k, v in reply.items() if k != "partitions"} | {"statementHandle": handle})
        m = re.fullmatch(r"/api/v2/statements/([0-9a-f-]+)\?partition=(\d+)", path)
        assert m, path
        return Resp(200, {"data": self.answers[m[1]]["partitions"][int(m[2])]})

    def statements(self) -> list[str]:
        return [c["body"]["statement"] for c in self.calls if c["method"] == "POST"]


def show(all_names: list[str]):
    """SHOW ... [STARTS WITH 'p'] LIMIT n [FROM 'c'] over ``all_names``, as documented: sorted, FROM's row included."""
    def reply(sql: str) -> dict[str, Any]:
        found = sorted(all_names)
        prefix = re.search(r"STARTS WITH '((?:[^']|'')*)'", sql)
        if prefix:
            found = [n for n in found if n.startswith(prefix[1].replace("''", "'"))]
        cursor = re.search(r"FROM '((?:[^']|'')*)'", sql)
        if cursor:
            start = next((i for i, n in enumerate(found) if n.startswith(cursor[1].replace("''", "'"))), len(found))
            found = found[start:]
        limit = int(re.search(r"LIMIT (\d+)", sql)[1])
        return names(*found[:limit])
    return reply


def browser(fake: FakeSnowflake, conn: dict[str, Any] | None = None) -> so.Browser:
    provider = sa.TokenProvider("myorg-myaccount", "pat", lambda: (TOKEN, None))
    return so.Browser(conn or CONN, sr.Client(provider, http=fake, sleep=lambda s: None))


DBS = [f"DB_{i:03d}" for i in range(7)]


def test_databases_page_through_with_limit_from_and_never_send_a_warehouse():
    fake = FakeSnowflake({"SHOW DATABASES": show(DBS)})
    b = browser(fake)
    seen, after = [], ""
    for _ in range(5):
        page = b.databases(after=after, limit=3)
        seen += [i["name"] for i in page["items"]]
        after = page["next"]
        if not after:
            break
    assert seen == DBS
    sqls = fake.statements()
    assert sqls == ["SHOW DATABASES LIMIT 5", "SHOW DATABASES LIMIT 5 FROM 'DB_002'", "SHOW DATABASES LIMIT 5 FROM 'DB_005'"]
    assert all("warehouse" not in c["body"] for c in fake.calls if c["body"])
    assert all(c["body"]["role"] == "ANALYST" for c in fake.calls if c["body"])
    assert all(c["body"]["parameters"] == {"MULTI_STATEMENT_COUNT": "1"} for c in fake.calls if c["body"])


def test_a_page_whose_cursor_isnt_in_the_answer_still_pages_right():
    def without_cursor(sql: str) -> dict[str, Any]:  # if Snowflake leaves FROM's own row out
        whole = show(DBS)(sql)
        cursor = re.search(r"FROM '([^']*)'", sql)
        if cursor:
            whole["data"] = [r for r in whole["data"] if r[0] != cursor[1]]
            whole["partitions"] = [whole["data"]]
        return whole
    b = browser(FakeSnowflake({"SHOW DATABASES": without_cursor}))
    first = b.databases(limit=3)
    second = b.databases(after=first["next"], limit=3)
    third = b.databases(after=second["next"], limit=3)
    assert [i["name"] for p in (first, second, third) for i in p["items"]] == DBS and third["next"] == ""


def test_a_prefix_uses_starts_with_and_strings_are_escaped():
    fake = FakeSnowflake({"SHOW DATABASES": show(["A'B_1", "A'B_2", "AXB_3"])})
    page = browser(fake).databases(prefix="A'B_", after="A'B_1")
    assert [i["name"] for i in page["items"]] == ["A'B_2"]
    assert fake.statements() == ["SHOW DATABASES STARTS WITH 'A''B_' LIMIT 202 FROM 'A''B_1'"]


def full_fake() -> FakeSnowflake:
    return FakeSnowflake({
        "SHOW DATABASES LIKE": names("SALES", "SALESX"),   # LIKE 'SALES' can't escape _ or %: filtered afterwards
        "SHOW SCHEMAS LIKE": names("RAW"),
        "SHOW SCHEMAS IN": show(["PUBLIC", "RAW", "STAGING"]),
        "SHOW TABLES LIKE": names("ORDERS"),
        "SHOW TABLES IN": lambda sql: answer(["name", "database_name", "schema_name", "kind", "rows", "bytes", "comment"],
                                             [["ORDERS", "SALES", "RAW", "TABLE", "42", "2048", "Line\nbreak"]]),
        "SHOW VIEWS LIKE": names("V_ORDERS"),
        "SHOW VIEWS IN": lambda sql: answer(["name", "database_name", "schema_name", "is_secure", "is_materialized"],
                                            [["V_ORDERS", "SALES", "RAW", "true", "false"]]),
        "SHOW COLUMNS": answer(["table_name", "column_name", "data_type", "null?", "default", "kind", "comment"],
                               [["ORDERS", f"C{i}", json.dumps({"type": "FIXED", "precision": 38, "scale": 0,
                                                                "nullable": i % 2 == 0}), "", "", "COLUMN", ""]
                                for i in range(3)],
                               [["ORDERS", "NAME", json.dumps({"type": "TEXT", "length": 16, "nullable": True}),
                                 "true", "", "COLUMN", "the name"]]),
    })


def test_schemas_tables_and_views_resolve_names_then_quote_them():
    fake = full_fake()
    b = browser(fake)
    schemas = b.schemas("sales")
    assert schemas["database"] == "SALES" and [i["name"] for i in schemas["items"]] == ["PUBLIC", "RAW", "STAGING"]
    tables = b.tables("sales", "raw")
    assert tables["items"] == [{"database": "SALES", "schema": "RAW", "name": "ORDERS", "kind": "TABLE", "rows": 42,
                                "bytes": 2048, "owner": "", "comment": "Line break"}]
    views = b.views("SALES", "RAW")
    assert views["items"][0]["name"] == "V_ORDERS" and views["items"][0]["secure"] is True
    sqls = fake.statements()
    assert "SHOW DATABASES LIKE 'sales' LIMIT 10000" in sqls
    assert "SHOW SCHEMAS LIKE 'raw' IN DATABASE \"SALES\" LIMIT 10000" in sqls
    assert "SHOW SCHEMAS IN DATABASE \"SALES\" LIMIT 202" in sqls
    assert "SHOW TABLES IN SCHEMA \"SALES\".\"RAW\" LIMIT 202" in sqls
    assert "SHOW VIEWS IN SCHEMA \"SALES\".\"RAW\" LIMIT 202" in sqls
    assert all("warehouse" not in c["body"] for c in fake.calls if c["body"])


def test_columns_read_every_partition_and_page_on_this_pc():
    fake = full_fake()
    b = browser(fake)
    first = b.columns("SALES", "RAW", "orders", limit=3)
    assert [c["name"] for c in first["items"]] == ["C0", "C1", "C2"] and first["next"] == 3 and first["total"] == 4
    assert first["items"][1] == {"name": "C1", "type": "FIXED", "precision": 38, "scale": 0, "length": None,
                                 "nullable": False, "default": "", "kind": "COLUMN", "comment": ""}
    second = b.columns("SALES", "RAW", "orders", offset=3, limit=3)
    assert second["items"][0]["name"] == "NAME" and second["items"][0]["length"] == 16 and second["next"] is None
    assert 'SHOW COLUMNS IN TABLE "SALES"."RAW"."ORDERS"' in fake.statements()
    assert any("?partition=1" in c["path"] for c in fake.calls)
    b.columns("SALES", "RAW", "V_ORDERS", view=True)
    assert 'SHOW COLUMNS IN VIEW "SALES"."RAW"."V_ORDERS"' in fake.statements()


def test_a_quote_in_a_name_is_doubled_in_the_identifier():
    fake = FakeSnowflake({"SHOW DATABASES LIKE": names('MY"DB'), "SHOW SCHEMAS IN": show(["S"])})
    browser(fake).schemas('MY"DB')
    assert 'SHOW SCHEMAS IN DATABASE "MY""DB" LIMIT 202' in fake.statements()


@pytest.mark.parametrize("call, message", [
    (lambda b: b.schemas("NOPE"), "Database NOPE doesn't exist, or role ANALYST can't see it. Check the name, or ask for USAGE"),
    (lambda b: b.tables("SALES", "NOPE"), "Schema NOPE doesn't exist in SALES, or role ANALYST can't see it."),
    (lambda b: b.columns("SALES", "RAW", "NOPE"), ("Table NOPE doesn't exist in SALES.RAW, or role ANALYST can't see it. "
                                                   "Check the name, or ask for SELECT on the table")),
])
def test_a_name_the_role_cant_see_says_what_to_ask_for(call, message):
    fake = full_fake()
    fake.replies |= {"SHOW DATABASES LIKE 'NOPE'": names(), "SHOW SCHEMAS LIKE 'NOPE'": names(),
                     "SHOW TABLES LIKE 'NOPE'": names()}
    with pytest.raises(sr.SnowflakeError, match=re.escape(message)):
        call(browser(fake))


def test_wildcards_in_like_are_filtered_to_the_exact_name():
    fake = full_fake()
    fake.replies["SHOW DATABASES LIKE"] = names("MY_DB", "MYXDB")
    fake.replies["SHOW SCHEMAS IN"] = show(["S"])
    assert browser(fake).schemas("MY_DB")["database"] == "MY_DB"
    assert 'SHOW SCHEMAS IN DATABASE "MY_DB" LIMIT 202' in fake.statements()


@pytest.mark.parametrize("limit", [0, so.MAX_PAGE + 1, True, "5"])
def test_a_page_size_outside_the_cap_is_refused_before_any_request(limit):
    fake = FakeSnowflake()
    with pytest.raises(ValueError, match="Ask for a number in that range"):
        browser(fake).databases(limit=limit)
    with pytest.raises(ValueError):
        browser(fake).columns("A", "B", "C", offset=-1)
    assert fake.calls == []


def test_a_refused_request_keeps_snowflakes_code_and_never_the_token():
    fake = FakeSnowflake(status=401)
    with pytest.raises(sr.SnowflakeError) as err:
        browser(fake).databases()
    assert "HTTP 401" in str(err.value) and TOKEN not in str(err.value)


def test_a_sql_error_from_show_keeps_its_code():
    fake = FakeSnowflake({"SHOW DATABASES": {"refused": "Insufficient privileges"}})
    with pytest.raises(sr.SnowflakeError, match="code 002003") as err:
        browser(fake).databases()
    assert err.value.code == "002003"


def test_too_many_partitions_is_an_error_not_a_cut_list():
    big = answer(["name"], *[[["X"]] for _ in range(so.MAX_PARTITIONS + 1)])
    fake = FakeSnowflake({"SHOW DATABASES": big})
    with pytest.raises(sr.SnowflakeError, match="Narrow the list"):
        browser(fake).databases()


def test_without_a_role_shows_run_as_the_default_role():
    fake = FakeSnowflake({"SHOW DATABASES": show(DBS)})
    browser(fake, CONN | {"role": ""}).databases()
    assert all("role" not in c["body"] for c in fake.calls if c["body"])
