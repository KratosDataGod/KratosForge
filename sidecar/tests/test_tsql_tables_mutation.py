"""Local T-SQL's table-valued forms (localdata/tsql_tables.py, WR-227): what mutation testing (batch 89,
docs/quality/mutation-testing.md) showed no test pinned. GENERATE_SERIES's start and stop kinds through
parentheses and columns, the int and bigint edges of its value column, TOP (n) PERCENT's bounds for a percent
worked out per query and its row count at 100 percent, and the refusals that
sqlglot's parser never hands it."""

from __future__ import annotations

import pytest
from sqlglot import exp, parse_one

from fabric_desktop.localdata import LocalData, tsql_tables


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LOCALAPPDATA", str(home))
        mp.setenv("HOME", str(home))
        data = LocalData(tmp_path_factory.mktemp("tables"))
        e = data.engine(data.create("Tables")["id"])
        e.execute("CREATE TABLE d (n DECIMAL(4, 1))")
        e.execute("INSERT INTO d VALUES (1.0)")
        try:
            yield e
        finally:
            data.close_all()


def run(engine, sql):
    return engine.execute(sql, dialect="tsql")


def refused(engine, sql, says):
    with pytest.raises(Exception) as caught:
        run(engine, sql)
    assert says in str(caught.value), str(caught.value)


# ---- GENERATE_SERIES: the kind of a start or stop

def test_a_whole_start_in_parentheses_is_still_whole_against_a_decimal_stop(engine):
    refused(engine, "SELECT value FROM GENERATE_SERIES((1), 5.0)", "needs start and stop of the same type")


def test_a_column_start_is_no_literal_kind_so_a_decimal_stop_runs(engine):
    # a column named n has no kind of its own: it is neither a whole number nor a decimal literal
    out = run(engine, "SELECT g.value FROM d CROSS APPLY GENERATE_SERIES(d.n, 3.0) AS g")
    assert sorted(float(r[0]) for r in out["rows"]) == [1.0, 2.0, 3.0]


@pytest.mark.parametrize("start, stop, kind", [
    (2147483646, 2147483647, "int32"),     # the largest int still fits
    (2147483647, 2147483648, "int64"),     # one past it is bigint
    (-2147483648, -2147483647, "int32"),   # the smallest int fits
    (-2147483649, -2147483648, "int64"),   # one below it is bigint
])
def test_a_whole_series_is_int_up_to_ints_edges_and_bigint_past_them(engine, start, stop, kind):
    out = run(engine, f"SELECT value FROM GENERATE_SERIES({start}, {stop})")
    assert out["rows"] == [[start], [stop]]
    assert out["columns"][0]["type"] == kind


def test_a_series_without_a_stop_is_refused_by_name():
    # sqlglot's parser stops first on GENERATE_SERIES(5); the rewrite still refuses a tree without a stop
    tree = exp.select("*").from_(exp.Table(this=exp.GenerateSeries(start=exp.Literal.number(5))))
    with pytest.raises(ValueError, match="GENERATE_SERIES takes a start and a stop"):
        tsql_tables.rewrite(tree)


# ---- TOP (n) PERCENT

def test_top_percent_is_refused_on_anything_but_a_select():
    limit = parse_one("SELECT TOP 10 PERCENT a FROM t ORDER BY a", read="tsql").args["limit"]
    union = exp.union(exp.select("a").from_("t"), exp.select("a").from_("u"))
    union.set("limit", limit.copy())
    with pytest.raises(ValueError, match="TOP ... PERCENT runs here only on a SELECT"):
        tsql_tables.rewrite(union)


def test_top_100_percent_keeps_every_row_of_a_long_table(engine):
    out = run(engine, "SELECT TOP 100 PERCENT value FROM GENERATE_SERIES(1, 150) ORDER BY value")
    assert len(out["rows"]) == 150


@pytest.mark.parametrize("share, kept", [("ABS(0.5)", 1), ("ABS(-25)", 1), ("ABS(100)", 4), ("'50'", 2),
                                         ("(ABS(-75))", 3), ("'100'", 4)])
def test_a_percent_worked_out_per_query_inside_0_to_100_runs(engine, share, kept):
    out = run(engine, f"SELECT TOP ({share}) PERCENT value FROM GENERATE_SERIES(1, 4) ORDER BY value")
    assert len(out["rows"]) == kept


@pytest.mark.parametrize("share", ["ABS(-150)", "ABS(100.5)", "ABS(-0.5) - 1"])
def test_a_percent_worked_out_per_query_outside_0_to_100_stops_as_a_warehouse_does(engine, share):
    refused(engine, f"SELECT TOP ({share}) PERCENT value FROM GENERATE_SERIES(1, 4) ORDER BY value",
            "Percent values must be between 0 and 100.")


@pytest.mark.parametrize("share", ["'150'", "(ABS(-150))", "'-1'", "((ABS(-101)))"])
def test_a_percent_in_text_or_extra_parentheses_outside_0_to_100_stops(engine, share):
    refused(engine, f"SELECT TOP ({share}) PERCENT value FROM GENERATE_SERIES(1, 4) ORDER BY value",
            "Percent values must be between 0 and 100.")
