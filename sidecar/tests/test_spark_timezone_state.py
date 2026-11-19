"""WR-120 follow-up: a session's time zone state goes with that session, and SQL sets it as spark.conf does.

The cast and date_trunc passes (spark_time_zone.fix) run for the session the SQL is for, and only while its
own zone shifts: never for a UTC session because another session, or one already gone, was in America/Chicago.
SET TIME ZONE, SET spark.sql.session.timeZone and RESET in spark.sql change the same state spark.conf.set
and spark.conf.unset do (the conf value, current_timezone(), and the earlier instant of a repeated hour)."""

from __future__ import annotations

import gc
import weakref

import pytest
import sqlglot

from fabric_desktop import spark_local, spark_time_zone, sql
from fabric_desktop.notebook import Kernel

KEY = "spark.sql.session.timeZone"
# 01:30 on the day America/Chicago falls back is two instants; Spark 3.5 reads it as the earlier (CDT) one.
FALL_BACK = "SELECT current_timezone() AS z, CAST(CAST('2024-11-03 01:30:00' AS TIMESTAMP) AS LONG) AS s"
EARLIER, LATER = 1730615400, 1730619000


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def _shift_passes() -> bool:
    written = sql.duckdb_sql(sql.spark_fixes(sqlglot.parse_one("SELECT CAST(y AS TIMESTAMP), date_trunc('HOUR', y)",
                                                               read="spark")))
    return "kf_w" in written or "kf_d" in written


class _Session:
    """Stands in for a session: spark_time_zone keeps its state on whatever object it is given."""


def test_a_session_at_a_reused_id_doesnt_inherit_a_gone_sessions_zone(monkeypatch):
    # every session at the same id, as a later one is when CPython reuses a gone session's memory
    monkeypatch.setattr(spark_time_zone, "id", lambda session: 1, raising=False)
    monkeypatch.setattr(spark_local, "_session", None)
    gone = _Session()
    spark_time_zone.session_zone(gone, "America/Chicago")
    with spark_time_zone.for_session(gone):
        assert _shift_passes()
    alive = weakref.ref(gone)
    del gone
    gc.collect()
    assert alive() is None  # nothing in spark_time_zone keeps it
    later = _Session()
    with spark_time_zone.for_session(later):
        assert spark_time_zone.current_gaps() == () and not _shift_passes()
    monkeypatch.setattr(spark_local, "_session", later)
    assert not _shift_passes()


def test_a_utc_session_never_runs_the_shift_passes_after_a_chicago_session_is_gone(monkeypatch):
    chicago = spark_local.LocalSparkSession()
    monkeypatch.setattr(spark_local, "_session", chicago)
    try:
        chicago.conf.set(KEY, "America/Chicago")
        assert _shift_passes()
        assert chicago.sql(FALL_BACK).collect()[0]["s"] == EARLIER
    finally:
        chicago.conn.close()
    utc = spark_local.LocalSparkSession()
    monkeypatch.setattr(spark_local, "_session", utc)
    del chicago
    gc.collect()
    try:
        assert not _shift_passes()
        assert utc.sql(FALL_BACK).collect()[0] == ("UTC", 1730597400)
    finally:
        utc.conn.close()


def test_only_the_current_sessions_zone_counts_not_any_other_sessions(monkeypatch):
    chicago, utc = _Session(), _Session()
    spark_time_zone.session_zone(chicago, "America/Chicago")
    spark_time_zone.session_zone(utc, None)
    monkeypatch.setattr(spark_local, "_session", chicago)  # the active session shifts; the SQL is for utc
    with spark_time_zone.for_session(utc):
        assert not _shift_passes()
    assert _shift_passes()
    spark_time_zone.session_zone(chicago, "UTC")
    assert not _shift_passes()


def _answers(spark) -> tuple[str | None, str, int]:
    row = spark.sql(FALL_BACK).collect()[0]
    return spark.conf.get(KEY), row["z"], row["s"]


@pytest.mark.parametrize("statement", ["SET TIME ZONE 'America/Chicago'", 'SET TIME ZONE "America/Chicago"',
                                       "set time zone 'America/Chicago';",
                                       "SET spark.sql.session.timeZone = America/Chicago",
                                       "SET spark.sql.session.timeZone = 'America/Chicago'"])
def test_sql_set_time_zone_answers_as_spark_conf_set_does(spark, statement):
    try:
        spark.conf.set(KEY, "America/Chicago")
        by_conf = _answers(spark)
        spark.conf.unset(KEY)
        assert _answers(spark) == ("UTC", "UTC", 1730597400)
        assert spark.sql(statement).collect() == [(KEY, "America/Chicago")]
        assert _answers(spark) == by_conf == ("America/Chicago", "America/Chicago", EARLIER)
    finally:
        spark.conf.unset(KEY)


@pytest.mark.parametrize("reset", ["RESET spark.sql.session.timeZone", "RESET", "reset;"])
def test_sql_reset_restores_utc_as_spark_conf_unset_does(spark, reset):
    try:
        spark.sql("SET TIME ZONE 'America/Chicago'")
        assert spark.sql(reset).collect() == []
        assert _answers(spark) == ("UTC", "UTC", 1730597400)
        assert spark_time_zone.current_gaps() == () and spark.conn.execute("SELECT current_setting('TimeZone')").fetchall() == [("UTC",)]
    finally:
        spark.conf.unset(KEY)


def test_a_bare_reset_resets_the_time_zone_only_and_keeps_builder_and_other_settings(spark):
    from fabric_desktop.spark_local import LocalSparkSession
    LocalSparkSession.builder.config("spark.sql.adaptive.enabled", "false")  # the builder's, a session default
    try:
        spark.conf.set("spark.sql.shuffle.partitions", "8")
        spark.sql("SET TIME ZONE 'America/Chicago'")
        spark.sql("RESET")
        assert _answers(spark) == ("UTC", "UTC", 1730597400)
        assert spark.conf.get("spark.sql.adaptive.enabled") == "false"
        assert spark.conf.get("spark.sql.shuffle.partitions") == "8"
        spark.sql("RESET spark.sql.shuffle.partitions")  # a key named is put back to its default
        assert spark.conf.get("spark.sql.shuffle.partitions") == "200"
    finally:
        for key in (KEY, "spark.sql.shuffle.partitions", "spark.sql.adaptive.enabled"):
            spark.conf.unset(key)


def test_a_zone_set_in_sql_applies_to_the_statements_after_it_in_the_same_call(spark):
    try:
        got = spark.sql(f"SET TIME ZONE 'America/Chicago'; {FALL_BACK}").collect()
        assert got == [("America/Chicago", EARLIER)]
        assert spark.sql(f"RESET spark.sql.session.timeZone; {FALL_BACK}").collect() == [("UTC", 1730597400)]
    finally:
        spark.conf.unset(KEY)


def test_an_offset_with_minutes_in_sql_is_refused_as_spark_conf_set_refuses_it(spark):
    with pytest.raises(ValueError) as by_conf:
        spark.conf.set(KEY, "+05:30")
    for statement in ("SET TIME ZONE '+05:30'", "SET spark.sql.session.timeZone = +05:30"):
        with pytest.raises(ValueError) as by_sql:
            spark.sql(statement)
        assert str(by_sql.value) == str(by_conf.value)
    assert _answers(spark) == ("UTC", "UTC", 1730597400)


@pytest.mark.parametrize("query", [
    "SET TIME ZONE 'UTC'; VACUUM t RETAIN 0 HOURS",
    "RESET; VACUUM t RETAIN 0 HOURS",
    "SET TIME ZONE 'UTC'; RESTORE TABLE t TO VERSION AS OF 0",
    "SET TIME ZONE 'UTC'; OPTIMIZE t",
    ("SET spark.sql.session.timeZone = 'UTC'; MERGE WITH SCHEMA EVOLUTION INTO t USING s ON t.id = s.id "
     "WHEN MATCHED THEN UPDATE SET *"),
])
def test_a_zone_setting_doesnt_let_maintenance_run_beside_another_statement(spark, query, monkeypatch):
    """OPTIMIZE, VACUUM, RESTORE and MERGE WITH SCHEMA EVOLUTION must be the only statement in the call; a time
    zone statement before one doesn't split the call into pieces that each pass alone."""
    from fabric_desktop import spark_maint

    ran: list = []
    monkeypatch.setattr(spark_maint, "run", lambda spec: ran.append(spec) or (None, ""))
    try:
        with pytest.raises(Exception):  # noqa: B017  # the same stop as without the zone statement
            spark.sql(query)
        assert ran == []
    finally:
        spark.conf.unset(spark_local._TIME_ZONE_KEY)


@pytest.mark.parametrize("change", [
    "DELETE FROM t WHERE id = 1",
    "WITH c AS (SELECT 1 AS id) DELETE FROM t WHERE id IN (SELECT id FROM c)",
    "UPDATE t SET id = 2",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
])
def test_a_zone_setting_beside_a_change_is_refused_before_anything_runs(spark, change):
    try:
        with pytest.raises(ValueError, match="only statement in the call"):
            spark.sql(f"SET TIME ZONE 'America/Chicago'; {change}")
        assert spark.conf.get(spark_local._TIME_ZONE_KEY, "UTC") in ("UTC", "Etc/UTC")  # the SET didn't run
    finally:
        spark.conf.unset(spark_local._TIME_ZONE_KEY)
