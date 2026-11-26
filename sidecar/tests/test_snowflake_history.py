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


def test_a_credential_split_at_snowflakes_cut_never_shifts_into_the_shown_text():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnopqrstuvwxyz123456 "  # pragma: allowlist secret
    split = "AKIAABCDEF"  # the start of an access key, cut by Snowflake, too short to be recognised
    text = (jwt * 40 + "x" * 4000)[:sh._FETCH - len(split)] + split  # what LEFT(query_text, 4000) gives
    assert len(text) == sh._FETCH and len(sh.redact.secrets(text)) < sh.TEXT_MAX  # redaction would pull it in
    fake = FakeSnowflake([[query("q-1", text=text)]])
    shown = history(fake, text_chars=sh.TEXT_MAX)["queries"][0]
    assert "AKIA" not in shown["text"] and "eyJ" not in shown["text"] and shown["text_cut"] is True


def test_a_credential_split_at_the_cut_of_an_error_message_is_never_shown():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIx" + "Q" * 150 + "In0.abcdefghijklmnopqrstuvwxyz123456 "  # pragma: allowlist secret
    error = (jwt * 60)[:sh._FETCH - 15] + "AKIAIOSFODNN7EX"  # pragma: allowlist secret
    assert len(error) == sh._FETCH
    fake = FakeSnowflake([[query("q-1", status="FAILED_WITH_ERROR", ERROR_MESSAGE=error)]])
    shown = history(fake)["queries"][0]["error"]
    assert "AKIA" not in shown


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


# Snowflake's credential parameters, which redact.secrets doesn't know by name: each value goes, in every
# quoting Snowflake accepts, and the parameter's name stays so the statement still reads.
_SF_SECRET_CASES = [
    ("CREATE STAGE s URL='s3://b/' CREDENTIALS=(AWS_KEY_ID='AKID0001' AWS_SECRET_KEY='wJalr/K7MDENG''x')",  # pragma: allowlist secret
     ["AKID0001", "wJalr", "K7MDENG"]),  # pragma: allowlist secret
    ("COPY INTO t FROM @s CREDENTIALS=(AWS_TOKEN='FQoGZXIvYXdz1234')", ["FQoGZXIvYXdz1234"]),  # pragma: allowlist secret
    ("COPY INTO t FROM 'azure://a.blob.core.windows.net/c' CREDENTIALS=(AZURE_SAS_TOKEN='?sv=2020&sig=zzSIG')",  # pragma: allowlist secret
     ["sv=2020", "zzSIG"]),  # pragma: allowlist secret
    ("CREATE STAGE s ENCRYPTION=(TYPE='AWS_CSE' MASTER_KEY='eSxX0jzYfIamtnBKOEOwq80Au6NbSgPH5r4BDDwOaO8=')",  # pragma: allowlist secret
     ["eSxX0jzYfIamtnBKOEOwq80Au6NbSgPH5r4BDDwOaO8"]),  # pragma: allowlist secret
    ("CREATE SECURITY INTEGRATION i TYPE=API_AUTHENTICATION OAUTH_CLIENT_SECRET = 'clientS3cret'", ["clientS3cret"]),  # pragma: allowlist secret
    ("CREATE SECRET s TYPE=OAUTH2 OAUTH_REFRESH_TOKEN='refreshTok9' OAUTH_REFRESH_TOKEN_EXPIRY_TIME='2026-12-01'",  # pragma: allowlist secret
     ["refreshTok9"]),  # pragma: allowlist secret
    ("ALTER USER u SET PRIVATE_KEY = 'MIIEvQIBADANBgkqhkiG9w0BAQEFAASC'", ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"]),  # pragma: allowlist secret
    ("CREATE SECRET s TYPE=GENERIC_STRING SECRET_STRING='sk_live_abc123'", ["sk_live_abc123"]),  # pragma: allowlist secret
    ("CREATE SECRET s TYPE=GENERIC_STRING SECRET_STRING=$$dollar quoted secret$$", ["dollar", "quoted", "secret$"]),  # pragma: allowlist secret  # pragma: allowlist secret
    ("CREATE SECRET s TYPE=PASSWORD USERNAME='svc' PASSWORD=\"dq\"\"pass\"", ["dq", "pass\""]),  # pragma: allowlist secret
    ("ALTER USER bob SET PASSWORD = 'two words' MUST_CHANGE_PASSWORD = TRUE", ["two words"]),  # pragma: allowlist secret
    ("CREATE USER u PASSWORD=bareWord123 DEFAULT_ROLE=r", ["bareWord123"]),  # pragma: allowlist secret
    ("CALL make_user(name => 'u', password => 'arrowPass1')", ["arrowPass1"]),  # pragma: allowlist secret
    ("alter user u set password='lower''case\\'esc'", ["lower", "esc"]),  # pragma: allowlist secret
    ("CREATE USER u PASSWORD='cut by Snowflake's limit and never clos", ["cut by Snowflake"]),  # pragma: allowlist secret
    ("CREATE USER u RSA_PUBLIC_KEY='MIIB' PASSPHRASE='phrase7'", ["phrase7"]),  # pragma: allowlist secret
    ("CREATE SECRET s TYPE=PASSWORD USERNAME='svc' PASSWORD=\"cut off double quote", ["cut off", "double quote"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD /* new */ = 'cmtBefore1'", ["cmtBefore1"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = /* new */ 'cmtAfter2'", ["cmtAfter2"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = -- new\n 'lineCmt3'", ["lineCmt3"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = ( 'paren4' )", ["paren4"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = 'pre5' || 'concat5'", ["pre5", "concat5"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = IDENTIFIER('ident6')", ["ident6"]),  # pragma: allowlist secret
    ("SET pw = 'sessVar7'", ["sessVar7"]),  # pragma: allowlist secret
    ("BEGIN\n  LET password := 'letVar8';\nEND", ["letVar8"]),  # pragma: allowlist secret
    ("SELECT 1; SET db_api_token = 'tokVar9'", ["tokVar9"]),  # pragma: allowlist secret
    ("ALTER USER u SET PASSWORD = 'multi\nline10'", ["multi", "line10"]),  # pragma: allowlist secret
    ("CREATE EXTERNAL FUNCTION f() API_KEY = 'apiKey11'", ["apiKey11"]),  # pragma: allowlist secret
    ("CREATE SECURITY INTEGRATION i TYPE=EXTERNAL_OAUTH EXTERNAL_OAUTH_CLIENT_SECRET='extSecret12'", ["extSecret12"]),  # pragma: allowlist secret
    ("ALTER SESSION SET RSA_PRIVATE_KEY_PASSPHRASE = 'rsaPass13'", ["rsaPass13"]),  # pragma: allowlist secret
]


@pytest.mark.parametrize("sql,gone", _SF_SECRET_CASES, ids=[str(i) for i in range(len(_SF_SECRET_CASES))])
def test_snowflake_credential_parameters_are_removed_from_query_text_and_errors(sql, gone):
    text, _ = sh._cut(sql, 2000)
    row = sh._row({"query_text": sql, "error_message": f"SQL compilation error near {sql}"}, 2000)
    for shown in (text, row["text"], row["error"]):
        assert "[removed]" in shown
        for value in gone:
            assert value not in shown, (value, shown)


def test_what_isnt_a_credential_stays():
    kept = ["SELECT password_hash, token_count FROM t WHERE token_count = 3",  # pragma: allowlist secret
            "CREATE USER u RSA_PUBLIC_KEY='MIIBIjANBgkq' DEFAULT_ROLE=analyst",  # pragma: allowlist secret
            "CREATE SECRET s TYPE=PASSWORD USERNAME='svc'",  # pragma: allowlist secret
            "CREATE STAGE s STORAGE_INTEGRATION=si ENCRYPTION=(TYPE='AWS_SSE_KMS' KMS_KEY_ID='alias/k')",
            "ALTER USER u SET MUST_CHANGE_PASSWORD = TRUE",
            "ALTER USER u\nSET MUST_CHANGE_PASSWORD = TRUE",
            "SET row_count = 5",
            "LET keyword_list := 'a,b'",
            "ALTER USER u SET RSA_PUBLIC_KEY = 'MIIBIjAN'",
            "ALTER ACCOUNT SET PASSWORD_POLICY = db.s.strict_policy"]  # pragma: allowlist secret
    for sql in kept:
        assert sh._cut(sql, 2000)[0] == sql
