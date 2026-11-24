"""T-SQL on this PC at more of its exact edges (localdata/tsql.py's boundary mutants, re-scored on main
after batch 155): a count at the top of bigint's range, and a whole number of exactly 2^31, the first ten
digits long that T-SQL reads as a decimal rather than an int."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    return local.engine(local.create("TSQL")["id"])


def one(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"][0]


def test_a_count_at_the_top_of_bigint_is_in_range(engine):
    assert one(engine, "SELECT LEFT('abc', 9223372036854775807) AS v") == ["abc"]


def test_2_to_the_31_is_a_decimal_as_in_t_sql(engine):
    # numeric(10, 0) % int is numeric: the row gives it as text, as every decimal here
    assert one(engine, "SELECT 2147483648 % 7 AS q") == ["2"]
    assert one(engine, "SELECT 2147483647 % 7 AS q") == [1]


def test_char_gives_a_character_from_0_to_255_and_null_outside(engine):
    assert one(engine, "SELECT CHAR(0) AS c") == ["\x00"]
    assert one(engine, "SELECT CHAR(255) AS c") == ["ÿ"]
    assert one(engine, "SELECT CHAR(256) AS c") == [None]
    assert one(engine, "SELECT CHAR(-1) AS c") == [None]


def test_one_added_column_is_added(engine):
    engine.execute("CREATE TABLE dbo.t (name VARCHAR(10), qty INT)", dialect="tsql")
    engine.execute("ALTER TABLE dbo.t ADD n INT", dialect="tsql")
    engine.execute("INSERT INTO dbo.t VALUES ('a', 1, 7)", dialect="tsql")
    assert one(engine, "SELECT n FROM dbo.t") == [7]


def test_order_by_a_position_after_a_star_sorts_by_that_column(engine):
    # Fixed here: sqlglot's qualifier stopped with an AssertionError ("* is not Alias") for ORDER BY 1
    engine.execute("CREATE TABLE dbo.t (name VARCHAR(10), qty INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.t VALUES ('b', 1), ('a', 2)", dialect="tsql")
    assert engine.execute("SELECT * FROM dbo.t ORDER BY 1", dialect="tsql")["rows"] == [["a", 2], ["b", 1]]
    assert engine.execute("SELECT t.* FROM dbo.t AS t ORDER BY 1 DESC", dialect="tsql")["rows"] == [["b", 1], ["a", 2]]
