"""Spark's decimal result types on this PC (spark_decimals.py): a sum whose whole part outgrows its type,
a timestamp moved by whole days staying a timestamp, and the round of a double in a DataFrame of whole
numbers, ceil and floor, whole-number literals and round names (strict xfails pin real bugs)."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import spark_decimal_types


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.fixture
def conn():
    c = duckdb.connect()
    # DECIMAL(38,5): Spark's sum is DECIMAL(38,5) too, with 33 whole digits.
    c.execute("CREATE TEMP VIEW big AS SELECT CAST('600000000000000000000000000000000' AS DECIMAL(38,5)) AS v "
              "FROM range(2)")
    c.execute("CREATE TEMP VIEW fits AS SELECT CAST('400000000000000000000000000000000' AS DECIMAL(38,5)) AS v "
              "FROM range(2)")
    yield c
    c.close()


def _rows(conn, query, ansi=False):
    rel = conn.sql(sql.duckdb_sql(spark_decimal_types(sql.parse(query)[0], conn, ansi)))
    return rel.types, rel.fetchall()


DUCKDB2 = not duckdb.__version__.startswith("1.")


@pytest.mark.xfail(DUCKDB2, strict=True, reason="bug on DuckDB 2: its own sum of a DECIMAL(38,5) past 38 digits "
                                                 "raises 'Overflow in SUM of DECIMAL', where Spark (ANSI off) gives NULL")
def test_a_sum_with_more_whole_digits_than_its_type_holds_is_null(conn):
    kinds, rows = _rows(conn, "SELECT sum(v) AS s FROM big")  # 34 whole digits
    assert [str(k) for k in kinds] == ["DECIMAL(38,5)"]
    assert rows == [(None,)]
    _, rows = _rows(conn, "SELECT sum(v) AS s FROM fits")  # 33 whole digits: fits
    assert str(rows[0][0]) == "800000000000000000000000000000000.00000"


def test_a_sum_is_checked_against_its_whole_digits_not_its_precision():
    """DECIMAL(20,2) holds 18 whole digits: 19 is NULL with ANSI off and Spark's error with it on."""
    from fabric_desktop.spark_decimals import SUM_OVERFLOW, _summed

    def run(value, ansi):
        text = f"SELECT {_summed('v', (20, 2), ansi)} AS s FROM (SELECT CAST('{value}' AS DECIMAL(38,2)) AS v)"
        return duckdb.sql(sql.duckdb_sql(sqlglot.parse_one(text, read="duckdb"))).fetchall()[0][0]

    assert str(run("999999999999999999.99", True)) == "999999999999999999.99"
    assert run("1000000000000000000.00", False) is None
    with pytest.raises(duckdb.Error, match=r"Overflow in sum of decimals"):
        run("1000000000000000000.00", True)
    with pytest.raises(duckdb.Error) as got:
        run("-1000000000000000000.00", True)
    assert SUM_OVERFLOW in str(got.value)


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("kernel")
    mp.setenv("LOCALAPPDATA", str(home))
    mp.setenv("HOME", str(home))
    yield Kernel().namespace["spark"]
    mp.undo()


def test_a_timestamp_moved_by_whole_days_keeps_its_time(spark):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_ts AS SELECT TIMESTAMP'2024-01-01 10:30:00' AS ts")
    row = spark.sql("SELECT ts + INTERVAL 1 DAY AS later, ts - INTERVAL 2 DAYS AS earlier FROM kf_ts").collect()[0]
    assert (row.later.day, row.later.hour, row.later.minute) == (2, 10, 30)
    assert (row.earlier.month, row.earlier.day, row.earlier.hour) == (12, 30, 10)


@pytest.mark.xfail(strict=True, reason="bug: in the DataFrame API, round() of a double is DuckDB's (0.28), not Spark's "
                                       "(0.29), when the frame has no DOUBLE or DECIMAL column (spark_decimal_columns "
                                       "returns early on the frame's column types)")
def test_round_of_a_double_in_a_frame_of_whole_numbers_is_sparks(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it

    frame = spark.createDataFrame([(1,)], "n int")
    got = frame.select(F.round(F.lit(0.285).cast("double"), 2).alias("r")).collect()[0].r
    assert float(got) == 0.29


@pytest.mark.xfail(not DUCKDB2, strict=True,
                   reason="bug on DuckDB 1.5: in the DataFrame API, F.lit(0.285) is a DOUBLE in Spark, but next to a "
                          "DOUBLE column its round() comes back as a Decimal, read as a SQL decimal literal")
def test_round_of_a_float_literal_stays_a_double(spark):
    from pyspark.sql import functions as F  # noqa: N812 - PySpark's own name for it

    frame = spark.createDataFrame([(1.0,)], "x double")
    got = frame.select(F.round(F.lit(0.285), 2).alias("r")).collect()[0].r
    assert isinstance(got, float)
    assert got == 0.29


def test_ceil_and_floor_of_a_double_round_up_and_down(spark):
    row = spark.sql("SELECT ceil(1.5D) AS c, floor(1.5D) AS f, ceil(-1.5D) AS nc, floor(-1.5D) AS nf").collect()[0]
    assert tuple(row) == (2, 1, -1, -2)
    assert all(type(v) is int for v in row)


def test_a_date_moved_by_whole_days_stays_a_date(spark):
    import datetime as dt

    spark.sql("CREATE OR REPLACE TEMP VIEW kf_day AS SELECT DATE'2024-01-01' AS d")
    row = spark.sql("SELECT d + INTERVAL 1 DAY AS later, d - INTERVAL 1 DAY AS earlier FROM kf_day").collect()[0]
    assert type(row.later) is dt.date and row.later == dt.date(2024, 1, 2)
    assert type(row.earlier) is dt.date and row.earlier == dt.date(2023, 12, 31)


def test_ceil_of_a_double_is_a_bigint_operand(spark):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_dbl AS SELECT 1.5D AS f")
    row = spark.sql("SELECT typeof(ceil(f) * 1.5) AS t, ceil(f) * 1.5 AS v FROM kf_dbl").collect()[0]
    assert row.t == "decimal(23,1)"  # BIGINT is DECIMAL(20,0): 20 + 2 + 1 digits
    assert str(row.v) == "3.0"


def test_round_of_a_double_to_twenty_places_runs(spark):
    row = spark.sql("SELECT round(0.285D, 20) AS v, round(0.285D, -20) AS w").collect()[0]
    assert (row.v, row.w) == (0.285, 0.0)


def test_a_refused_round_in_a_case_is_named_round(spark):
    from fabric_desktop.spark_decimals import Refused

    with pytest.raises(Refused, match=r"^round\(\) of a double to 25 places"):
        spark.sql("SELECT CASE WHEN false THEN 1D ELSE round(0.285D, 25) END AS v").collect()


def test_a_literal_past_32_bits_is_a_bigint_and_does_not_wrap(spark):
    row = spark.sql("SELECT 2147483648 + 1 AS big, 2147483647 + 1 AS wrapped").collect()[0]
    assert (row.big, row.wrapped) == (2147483649, -2147483648)


def test_the_smallest_bigint_literal_wraps_as_a_bigint(spark):
    row = spark.sql("SELECT -9223372036854775808 - 1 AS v").collect()[0]
    assert row.v == 9223372036854775807


def test_an_expression_text_holding_two_columns_is_left_alone():
    from fabric_desktop.spark_decimals import spark_decimal_columns

    columns = [("a", "DECIMAL(10,2)"), ("b", "INTEGER")]
    assert spark_decimal_columns(["a * 2, b"], columns) == [None]
    assert spark_decimal_columns(["a * 2"], columns)[0] is not None


@pytest.mark.xfail(strict=True, reason="bug: round() of a numeric string fails in DuckDB's binder here; Spark casts the "
                                       "string to a double and gives 0.29")
def test_round_of_a_numeric_string_is_sparks(spark):
    assert float(spark.sql("SELECT round('0.285', 2) AS v").collect()[0].v) == 0.29
