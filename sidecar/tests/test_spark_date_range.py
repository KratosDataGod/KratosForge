"""Spark SQL date arithmetic past the year 9999, and ALTER TABLE ... DROP COLUMN of one column.

Checked on Spark 3.5.3 (local[1]): date_add(DATE'9999-12-31', 1) is +10000-01-01, make_date(10000, 1, 1) is
+10000-01-01 and year() of it is 10000, add_months(DATE'9999-12-31', 1000000) is +93333-04-30. Past DuckDB's range
(dates to 5881580-07-10, timestamps to 294247-01-10) Spark answers with a wrapped value or stops with its own
overflow error; here the cell stops with a plain message instead of DuckDB's "Date out of range".
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from fabric_desktop import notebook, sql
from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_dates_past_9999_answer_as_in_spark(spark) -> None:
    row = spark.sql("SELECT year(date_add(DATE'9999-12-31', 1)) AS y, datediff(date_add(DATE'9999-12-31', 1), "
                    "DATE'9999-12-31') AS d, date_add(DATE'9999-12-31', 1) > DATE'9999-12-31' AS later, "
                    "year(make_date(10000, 1, 1)) AS m, month(add_months(DATE'9999-12-31', 1000000)) AS a").collect()[0]
    assert (row.y, row.d, row.later, row.m, row.a) == (10000, 1, True, 10000, 4)
    assert spark.sql("SELECT make_date(2024, 13, 1) AS d").collect()[0].d is None  # Spark: NULL
    assert spark.sql("SELECT date_add(DATE'2024-01-01', 1) AS d").collect()[0].d == dt.date(2024, 1, 2)


@pytest.mark.parametrize("query", [
    "SELECT date_add(DATE'9999-12-31', 2147483647)",
    "SELECT make_date(6000000, 1, 1)",
    "SELECT add_months(DATE'9999-12-31', 2147483647)",
    "SELECT date_from_unix_date(2147483647)",
    "SELECT timestampadd(YEAR, 300000, TIMESTAMP'2000-06-01 00:00:00')",
    "SELECT DATE'5881580-07-10' + INTERVAL 1 DAY",
])
def test_past_the_engines_range_the_cell_says_so_in_plain_words(spark, query: str) -> None:
    with pytest.raises(duckdb.Error) as err:
        spark.sql(query).collect()
    text = notebook.plain_error(err.value)
    assert text.startswith("A date or time in this query is past what the local engine holds")
    assert "so the query stopped. Check the date arithmetic" in text and "run the cell in Fabric." in text


def test_the_plain_message_is_what_a_cell_shows() -> None:
    out = Kernel().run("spark.sql(\"SELECT date_add(DATE'9999-12-31', 2147483647) AS d\").collect()")
    (error,) = out["outputs"]
    assert error["type"] == "error" and error["evalue"].startswith("A date or time in this query is past")


def test_another_error_with_the_same_words_isnt_reworded() -> None:
    assert notebook.plain_error(ValueError("Date out of range")) == "Date out of range"


@pytest.mark.parametrize("statement", ["ALTER TABLE t DROP COLUMN b", "ALTER TABLE t DROP COLUMN IF EXISTS b",
                                       "ALTER TABLE t DROP COLUMNS (a, b)"])
def test_drop_column_reads_as_one_alter_on_every_sqlglot_in_range(statement: str) -> None:
    # sqlglot 30.22 read the one-column form as a Command the session refused; pyproject's floor is 30.23 (#3095),
    # and CI runs this on both ends of the range.
    from sqlglot import exp
    (tree,) = sql.parse(statement)
    assert isinstance(tree, exp.Alter) and isinstance(tree.args["actions"][0], exp.Drop)
