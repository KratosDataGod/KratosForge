"""Spark 3.5's sequence on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

Counting down, dates with and without a step, and a step in months (start + i * step, as Spark gives it): the
queries in tools/spark_value_cases/sequence.txt, each answered by Spark 3.5.3 in
tests/fixtures/spark_3_5_3_sequence.json. What still differs is listed in docs/sql-compatibility.md."""

from __future__ import annotations

import datetime as dt

import pytest
from spark_values import check, load

from fabric_desktop import sql
from fabric_desktop.notebook import Kernel

REFUSED: list = []


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_sequence_answers_as_spark_3_5(spark):
    check(spark, load("sequence"), REFUSED)


def test_sequence_of_columns_is_typed_from_the_bound_tables(spark):
    got = spark.sql("SELECT sequence(d, DATE'2024-04-30', INTERVAL 1 MONTH) AS s, sequence(a, b) AS n "
                    "FROM (SELECT DATE'2024-01-31' AS d, 3 AS a, 1 AS b)").collect()[0]
    assert got[0] == [dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 3, 31), dt.date(2024, 4, 30)]
    assert got[1] == [3, 2, 1]


def test_sql_run_gives_the_same_sequences():
    assert sql.run("SELECT sequence(5, 1) AS s").to_pylist() == [{"s": [5, 4, 3, 2, 1]}]
    assert sql.run("SELECT sequence(d, DATE'2024-01-03') AS s FROM (SELECT DATE'2024-01-05' AS d)").to_pylist() == [
        {"s": [dt.date(2024, 1, 5), dt.date(2024, 1, 4), dt.date(2024, 1, 3)]}]


def test_a_sequence_of_whole_number_literals_is_written_without_the_typed_pass():
    # so spark.sql and sql.run skip typing the statement for it
    statement = sql.parse("SELECT sequence(1, 5)", "spark")[0]
    assert "kf_complex" not in {k for n in statement.walk() for k in n.meta}
    assert "GENERATE_SERIES(1, 5, CASE WHEN 1 <= 5 THEN 1 ELSE -1 END)" in sql.duckdb_sql(statement)


def test_timestamps_step_by_months_from_start():
    # Spark 3.5's rule (start + i * step) as for dates above; this case itself wasn't run in Spark
    got = sql.run("SELECT sequence(TIMESTAMP'2024-01-31 10:00:00', TIMESTAMP'2024-05-01 00:00:00', INTERVAL 1 MONTH)"
                  " AS s").to_pylist()[0]["s"]
    assert [t.replace(tzinfo=None) for t in got] == [dt.datetime(2024, m, d, 10) for m, d in ((1, 31), (2, 29), (3, 31), (4, 30))]


WRONG_WAY = ("The step of sequence goes the wrong way from start to stop, so nothing ran. Spark stops on it too "
             "(illegal sequence boundaries). Use a positive step when start is less than stop, or a negative step "
             "when start is more than stop.")


@pytest.mark.parametrize("query", [
    "SELECT sequence(1, 5, -1)", "SELECT sequence(5, 1, 1)", "SELECT sequence(1, 5, 0)",
    "SELECT sequence(DATE'2024-01-05', DATE'2024-01-01', INTERVAL 1 MONTH)",
    "SELECT sequence(DATE'2024-01-01', DATE'2024-01-05', INTERVAL '-1' DAY)",
    "SELECT sequence(DATE'2024-01-01', DATE'2024-01-05', INTERVAL 0 DAY)",
])
def test_a_step_the_wrong_way_stops_before_anything_runs_for_literals(spark, query):
    from fabric_desktop import spark_complex
    assert spark_complex.WRONG_WAY == WRONG_WAY
    with pytest.raises(ValueError) as caught:
        spark.sql(query).collect()
    assert str(caught.value) == WRONG_WAY


@pytest.mark.parametrize("query", [
    "SELECT sequence(a, b, c) FROM (SELECT 1 AS a, 5 AS b, -1 AS c)",
    "SELECT sequence(a, b, c) FROM (SELECT 1 AS a, 5 AS b, 0 AS c)",
    "SELECT sequence(d, DATE'2024-01-01', INTERVAL 1 MONTH) FROM (SELECT DATE'2024-03-01' AS d)",
    "SELECT sequence(d, DATE'2024-01-09', INTERVAL '-1' DAY) FROM (SELECT DATE'2024-01-01' AS d)",
])
def test_a_step_the_wrong_way_stops_when_it_runs_for_columns(spark, query):
    with pytest.raises(Exception, match=r"The step of sequence goes the wrong way from start to stop, so nothing ran"):
        spark.sql(query).collect()
    with pytest.raises(Exception, match=r"The step of sequence goes the wrong way"):
        sql.run(query)


@pytest.mark.parametrize("query, want", [
    ("SELECT sequence(1, 1, 0)", [1]),
    ("SELECT sequence(a, b, c) FROM (SELECT 3 AS a, 3 AS b, 0 AS c)", [3]),
    ("SELECT sequence(DATE'2024-01-01', DATE'2024-01-01', INTERVAL 0 DAY)", [dt.date(2024, 1, 1)]),
    ("SELECT sequence(d, DATE'2024-03-01', INTERVAL 0 MONTH) FROM (SELECT DATE'2024-03-01' AS d)", [dt.date(2024, 3, 1)]),
    ("SELECT sequence(a, b, c) FROM (SELECT 1 AS a, 7 AS b, 3 AS c)", [1, 4, 7]),
    ("SELECT sequence(d, DATE'2024-01-01', INTERVAL -1 MONTH) FROM (SELECT DATE'2024-03-01' AS d)",
     [dt.date(2024, 3, 1), dt.date(2024, 2, 1), dt.date(2024, 1, 1)]),
])
def test_a_step_of_zero_with_start_equal_to_stop_gives_start(spark, query, want):
    assert spark.sql(query).collect()[0][0] == want
