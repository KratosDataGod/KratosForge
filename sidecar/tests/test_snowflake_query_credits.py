"""Snowflake credits of tagged queries (Compare platforms, Preview): the read runs only after the same
confirm as Databricks billing, the query is fixed read-only text with checked tags and no user
columns, and every credit figure is worked out (Estimated) or None with its reason, never 0.
Tested against fakes only; no live account."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pyarrow as pa
import pytest

from fabric_desktop import audit, server
from fabric_desktop import engines_service as es
from fabric_desktop.engines import snowflake_query_credits as sqc
from fabric_desktop.engines import snowflake_warehouse as sw
from fabric_desktop.engines.authorization import RemoteNotAuthorized
from fabric_desktop.engines.contract import CellResult

CONN = {"platform": "snowflake", "account": "myorg-acct", "user": "svc", "warehouse": "REPORTING_XS",
        "credential": {"method": "pat", "secret": "env:X"}}  # pragma: allowlist secret
TABLE = pa.table({
    "QUERY_ID": ["q1", "q2", "q3", "q4"], "QUERY_TAG": ["kf:daily-sales"] * 4,
    "WAREHOUSE_NAME": ["REPORTING_XS", "REPORTING_XS", None, "SP_WH"],
    "WAREHOUSE_SIZE": ["X-Small", "Small", None, "Medium"],
    "WAREHOUSE_TYPE": ["STANDARD", "STANDARD", None, "SNOWPARK-OPTIMIZED"],
    "START_TIME": [dt.datetime(2026, 11, 23, 9, tzinfo=dt.timezone.utc)] * 4,
    "EXECUTION_TIME": [972000, Decimal(1800000), 15, 60000], "BYTES_SCANNED": [1e9, None, 0, 5]})


class Session:
    def __init__(self, log, result):
        self.log, self.result = log, result

    def execute(self, request):
        self.log.append(("execute", request.code, request.dialect))
        return self.result

    def close(self):
        self.log.append(("close",))


class Adapter:
    def __init__(self, result=None):
        self.log = []
        self.result = result or CellResult("ok", [], 1.0, 4, "snowflake-sql", TABLE)

    def open_session(self, config, authorization=None):
        self.log.append(("open", config.options["warehouse"], authorization))
        return Session(self.log, self.result)


class Svc(es.EnginesMixin):
    def __init__(self, tmp_path, conn=CONN, policy=None):
        self.tmp, self.conn, self.policy = tmp_path, conn, policy or {}
        self.adapter = Adapter()

    def _connection(self, key):
        return self.conn, self.policy, "P"

    def _snowflake_credits_root(self):
        return self.tmp / "sf"

    def _engine_adapter(self, engine_id):
        assert engine_id == "snowflake-sql"
        return self.adapter


def test_it_is_an_audited_rpc_method():
    assert "snowflake_query_credits" in es.METHODS <= server.METHODS
    assert "snowflake_query_credits" not in audit.READ_ONLY


def test_the_query_is_read_only_fixed_text_with_checked_tags_and_no_user_columns():
    sql = sqc.query(30, ["kf:daily-sales", "kf:daily-sales", "team/etl v2"])
    assert "IN ('kf:daily-sales', 'team/etl v2')" in sql and "DATEADD(day, -30" in sql
    assert sw.read_only_statements(sql) == [sql.strip()] or len(sw.read_only_statements(sql)) == 1
    for word in ("user_name", "role_name", "query_text"):
        assert word not in sql.lower()
    for bad in (["x' OR '1'='1"], ["a;b"], [""], [7], ["t" * 201], [f"t{i}" for i in range(21)]):
        with pytest.raises(ValueError):
            sqc.query(30, bad)
    with pytest.raises(ValueError, match="Link a Snowflake query tag"):
        sqc.query(30, [])
    with pytest.raises(ValueError, match="days"):
        sqc.query(90, ["kf:x"])


def test_rows_are_estimated_credits_or_none_with_the_reason():
    rows = {r["query_id"]: r for r in sqc.rows_from(TABLE)}
    assert rows["q1"]["credits"] == pytest.approx(0.27) and rows["q1"]["credits_per_hour"] == 1.0
    assert rows["q1"]["seconds"] == 972 and rows["q1"]["start"] == "2026-11-23T09:00:00Z"
    assert rows["q2"]["credits"] == pytest.approx(1.0)
    assert rows["q3"]["credits"] is None and "didn't run on a warehouse" in rows["q3"]["reason"]
    assert rows["q4"]["credits"] is None and "snowpark-optimized" in rows["q4"]["reason"]
    assert all("user" not in k for r in rows.values() for k in r)


def test_without_confirm_nothing_runs(tmp_path):
    svc = Svc(tmp_path)
    out = svc.snowflake_query_credits("profile:p:sf", tags=["kf:daily-sales"])
    assert out == {"needs_confirm": True, "warehouse": {"name": "REPORTING_XS", "account": "myorg-acct"}}
    assert svc.adapter.log == []


def test_confirmed_runs_once_on_that_warehouse_under_a_confirmed_authorization_and_keeps_the_result(tmp_path):
    svc = Svc(tmp_path)
    out = svc.snowflake_query_credits("profile:p:sf", tags=["kf:daily-sales"], confirmed=True)
    assert out["ok"] and len(out["queries"]) == 4 and any("60 seconds" in a for a in out["assumptions"])
    assert [e[0] for e in svc.adapter.log] == ["open", "execute", "close"]
    _, warehouse, authorization = svc.adapter.log[0]
    assert warehouse == "REPORTING_XS" and authorization.target == "myorg-acct/REPORTING_XS"
    assert authorization.reason == "confirmed" and authorization.engine == "snowflake-sql"
    assert svc.adapter.log[1][2] == "snowflake"
    assert sqc.last(tmp_path / "sf", "myorg-acct")["queries"][0]["query_id"] == "q1"


def test_the_real_adapter_refuses_without_an_authorization():
    with pytest.raises(RemoteNotAuthorized):
        sqc.read(sw.SnowflakeSqlAdapter(client_for=lambda c: None), CONN, "REPORTING_XS", 30, ["kf:x"], None)


def test_a_never_profile_is_refused_even_when_confirmed(tmp_path):
    svc = Svc(tmp_path, policy={"remote_compute": "never"})
    for confirmed in (False, True):
        out = svc.snowflake_query_credits("profile:p:sf", tags=["kf:x"], confirmed=confirmed)
        assert out["refused"] and "doesn't allow remote compute" in out["error"]
    assert svc.adapter.log == []


def test_a_policy_profile_that_allows_snowflake_sql_runs_under_the_policy(tmp_path):
    svc = Svc(tmp_path, policy={"remote_compute": "policy", "allowed_remote_engines": ["snowflake-sql"]})
    assert svc.snowflake_query_credits("profile:p:sf", tags=["kf:x"])["ok"]
    assert svc.adapter.log[0][2].reason == "policy"


def test_a_failed_read_says_why_and_keeps_nothing(tmp_path):
    svc = Svc(tmp_path)
    svc.adapter = Adapter(CellResult("error", [{"type": "error", "evalue": "Object 'QUERY_HISTORY' does not exist or not authorized."}],
                                     1.0, None, "snowflake-sql"))
    out = svc.snowflake_query_credits("profile:p:sf", tags=["kf:x"], confirmed=True)
    assert not out["ok"] and "not authorized" in out["error"]
    assert sqc.last(tmp_path / "sf", "myorg-acct") is None


@pytest.mark.parametrize("kw, words", [({"warehouse": "x;drop"}, "warehouse name"), ({"days": 3}, "days"),
                                       ({"tags": ["a'b"]}, "query tag")])
def test_odd_input_is_refused_before_anything(tmp_path, kw, words):
    svc = Svc(tmp_path)
    with pytest.raises(ValueError, match=words):
        svc.snowflake_query_credits("profile:p:sf", confirmed=True, **({"tags": ["kf:x"]} | kw))
    assert svc.adapter.log == []


def test_only_snowflake_connections(tmp_path):
    with pytest.raises(ValueError, match="isn't a Snowflake connection"):
        Svc(tmp_path, conn={"platform": "databricks"}).snowflake_query_credits("profile:p:db", tags=["kf:x"])
