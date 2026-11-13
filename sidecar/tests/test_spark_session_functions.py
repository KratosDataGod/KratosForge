"""Spark's clock, random and session functions on this PC (spark_builtins_sql, sql._spark_session_functions).

current_catalog() and current_database() used to answer about DuckDB's own session ("memory"). They
now answer as in Fabric, or are refused by name. uuid() is a string, as in Spark. randn() and shuffle()
run, and current_timezone() is the session's zone. The Databricks SQL engine keeps DuckDB's session
answers, because its session isn't a Unity Catalog one."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks
from fabric_desktop.notebook import Kernel


def rows(query: str) -> list[tuple]:
    return [tuple(r.values()) for r in sql.run(query).to_pylist()]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_current_catalog_is_spark_catalog_as_in_fabric_not_duckdbs_memory():
    assert rows("SELECT current_catalog()") == [("spark_catalog",)]


@pytest.mark.parametrize("call", ["current_database()", "current_schema()"])
def test_current_database_is_refused_by_name_never_duckdbs_memory(call):
    with pytest.raises(ValueError, match=r"can't run on this PC the way it runs in Fabric.*currentDatabase"):
        sql.run(f"SELECT {call}")


def test_current_timezone_follows_the_session_zone(spark):
    assert spark.sql("SELECT current_timezone()").collect()[0][0] == "UTC"
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        assert spark.sql("SELECT current_timezone()").collect()[0][0] == "America/New_York"
    finally:
        spark.conf.unset("spark.sql.session.timeZone")
    assert spark.sql("SELECT current_timezone()").collect()[0][0] == "UTC"


def test_uuid_is_a_string_that_text_functions_take():
    value, kind, upper = rows("SELECT uuid(), typeof(uuid()), upper(uuid()) RLIKE '^[0-9A-F-]{36}$'")[0]
    assert isinstance(value, str) and len(value) == 36 and kind == "string" and upper is True


def test_randn_is_a_double_spread_around_zero():
    mean, sd = rows("SELECT avg(x), stddev(x) FROM (SELECT randn() AS x FROM range(20000))")[0]
    assert abs(mean) < 0.05 and abs(sd - 1) < 0.05


def test_a_seeded_randn_is_refused_by_name():
    with pytest.raises(ValueError, match=r"randn\(\) can't run on this PC.*leave out the seed"):
        sql.run("SELECT randn(42)")


def test_shuffle_keeps_every_element_and_nulls_and_reads_its_array_once():
    assert rows("SELECT array_sort(shuffle(array(3, NULL, 1, 2))), shuffle(NULL), shuffle(array())") == \
        [([1, 2, 3, None], None, [])]
    # The array is read once, so an array of random values comes back as the same values reordered.
    same = rows("SELECT array_sort(a) = array_sort(shuffle(a)) FROM (SELECT array(rand(), rand(), rand()) AS a)")
    assert same == [(True,)]
    orders = {tuple(rows("SELECT shuffle(array(1, 2, 3, 4, 5, 6))")[0][0]) for _ in range(20)}
    assert len(orders) > 1


def test_databricks_sql_keeps_duckdbs_session_answers():
    con = duckdb.connect()
    [statement] = _databricks("SELECT current_catalog(), uuid()")
    catalog, uid = con.execute(sql.duckdb_sql(statement)).fetchone()
    assert catalog != "spark_catalog" and isinstance(uid, str)
