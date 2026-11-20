"""WR-227: local T-SQL's table-valued forms as a Fabric Warehouse has them, or refused by name.

GENERATE_SERIES, STRING_SPLIT, CROSS and OUTER APPLY, VALUES in FROM, and TOP with PERCENT and WITH TIES, as
Microsoft's T-SQL reference for Warehouse in Microsoft Fabric describes them. None of them may end in DuckDB's
binder error or answer differently without saying so.
"""

from __future__ import annotations

import decimal

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    data = LocalData(tmp_path_factory.mktemp("forms"))
    e = data.engine(data.create("Forms")["id"])
    e.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a,b'), (2, 'c'), (3, NULL), (4, '')) v(id, s)")
    yield e
    data.close_all()


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


def refused(engine, sql, says):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    text = str(caught.value)
    assert says in text and "Binder Error" not in text and "Catalog Error" not in text, text


# ---- GENERATE_SERIES

@pytest.mark.parametrize("sql, expected", [
    ("SELECT value FROM GENERATE_SERIES(1, 5)", [1, 2, 3, 4, 5]),
    ("SELECT value FROM GENERATE_SERIES(1, 10, 3)", [1, 4, 7, 10]),
    ("SELECT value FROM GENERATE_SERIES(1, 10, 4)", [1, 5, 9]),  # stops before it passes stop
    ("SELECT value FROM GENERATE_SERIES(5, 1)", [5, 4, 3, 2, 1]),  # start above stop: the step is -1
    ("SELECT value FROM GENERATE_SERIES(5, 1, -2)", [5, 3, 1]),
    ("SELECT value FROM GENERATE_SERIES(-3, 3, 2)", [-3, -1, 1, 3]),
    ("SELECT value FROM GENERATE_SERIES(1, 1)", [1]),
    ("SELECT value FROM GENERATE_SERIES(1, 5, -1)", []),  # a step pointing away: no rows
    ("SELECT value FROM GENERATE_SERIES(5, 1, 1)", []),
    ("SELECT g.value FROM GENERATE_SERIES(1, 3) AS g", [1, 2, 3]),
])
def test_generate_series_counts_as_a_warehouse_does_in_a_column_called_value(engine, sql, expected):
    assert [r[0] for r in rows(engine, sql + " ORDER BY value" if "5, 1" not in sql else sql)] == (
        expected if "5, 1" in sql else sorted(expected))


def test_generate_series_of_whole_numbers_is_int_and_of_decimals_is_exact(engine):
    whole = engine.execute("SELECT value FROM GENERATE_SERIES(1, 3)", dialect="tsql")
    assert whole["columns"] == [{"name": "value", "type": "int32"}]
    tenths = [decimal.Decimal(v) for [v] in rows(engine, "SELECT value FROM GENERATE_SERIES(0.0, 0.3, 0.1)")]
    assert tenths == [decimal.Decimal(x) for x in ("0.0", "0.1", "0.2", "0.3")]  # 0.3 / 0.1 is 2.999... in a double
    down = [decimal.Decimal(v) for [v] in rows(engine, "SELECT value FROM GENERATE_SERIES(0.3, 0.0, -0.1)")]
    assert down == [decimal.Decimal(x) for x in ("0.3", "0.2", "0.1", "0.0")]
    quarters = rows(engine, "SELECT value FROM GENERATE_SERIES(1.00, 2.00, 0.25)")
    assert [decimal.Decimal(v) for [v] in quarters] == [decimal.Decimal(x) for x in ("1.00", "1.25", "1.50", "1.75", "2.00")]
    assert rows(engine, "SELECT count(*), min(value), max(value) FROM GENERATE_SERIES(1, 1000000)") == [[1000000, 1, 1000000]]


def test_generate_series_in_cross_and_outer_apply_reads_each_row(engine):
    assert rows(engine, "SELECT t.id, g.value FROM t CROSS APPLY GENERATE_SERIES(1, t.id) AS g "
                        "WHERE t.id <= 3 ORDER BY t.id, g.value") == [[1, 1], [2, 1], [2, 2], [3, 1], [3, 2], [3, 3]]
    assert rows(engine, "SELECT t.id, g.value FROM t CROSS APPLY GENERATE_SERIES(t.id, 1) AS g "
                        "WHERE t.id = 3 ORDER BY g.value DESC") == [[3, 3], [3, 2], [3, 1]]
    assert rows(engine, "SELECT t.id, g.value FROM t OUTER APPLY GENERATE_SERIES(1, 2, CASE WHEN t.id = 1 THEN -1 "
                        "ELSE 1 END) AS g WHERE t.id <= 2 ORDER BY t.id, g.value") == [[1, None], [2, 1], [2, 2]]


@pytest.mark.parametrize("sql, says", [
    ("SELECT * FROM GENERATE_SERIES(1, 5, 0)", "GENERATE_SERIES's step can't be 0, as in T-SQL."),
    ("SELECT t.id, g.value FROM t CROSS APPLY GENERATE_SERIES(1, 3, t.id - 2) AS g",
     "GENERATE_SERIES's step can't be 0, as in T-SQL."),
    ("SELECT * FROM GENERATE_SERIES(1, 5.0)", "start and stop of the same type"),
    ("SELECT * FROM GENERATE_SERIES(1e0, 3)", "not float"),
    ("SELECT * FROM GENERATE_SERIES(NULL, 5)", "GENERATE_SERIES with NULL isn't mapped yet"),
    ("SELECT * FROM GENERATE_SERIES(1, 5, NULL)", "GENERATE_SERIES with NULL isn't mapped yet"),
    ("SELECT * FROM GENERATE_SERIES(1.0, 2.0, 0.25)", "more decimal places than start and stop"),
    ("SELECT * FROM GENERATE_SERIES(1, 5, 0.5)", "more decimal places than start and stop"),
    ("SELECT GENERATE_SERIES(1, 3)", "only in FROM or CROSS APPLY"),
])
def test_generate_series_refuses_what_it_doesnt_carry_by_name(engine, sql, says):
    refused(engine, sql, says)


# ---- STRING_SPLIT

def test_string_split_with_enable_ordinal_off_is_the_plain_split(engine):
    plain = sorted(rows(engine, "SELECT value FROM STRING_SPLIT('a,b,,c', ',')"))
    assert plain == [[""], ["a"], ["b"], ["c"]]
    for flag in ("0", "NULL"):
        assert sorted(rows(engine, f"SELECT * FROM STRING_SPLIT('a,b,,c', ',', {flag})")) == plain
    assert rows(engine, "SELECT * FROM STRING_SPLIT(NULL, ',')") == []  # NULL text: an empty table


def test_string_split_in_apply_drops_or_keeps_rows_as_a_warehouse_does(engine):
    assert sorted(rows(engine, "SELECT t.id, s.value FROM t CROSS APPLY STRING_SPLIT(t.s, ',') AS s")) == [
        [1, "a"], [1, "b"], [2, "c"], [4, ""]]
    assert sorted(rows(engine, "SELECT t.id, s.value FROM t OUTER APPLY STRING_SPLIT(t.s, ',') AS s"),
                  key=lambda r: (r[0], r[1] or "")) == [[1, "a"], [1, "b"], [2, "c"], [3, None], [4, ""]]


@pytest.mark.parametrize("sql, says", [
    ("SELECT * FROM STRING_SPLIT('a,b', ',', 1)", "STRING_SPLIT with enable_ordinal 1 isn't mapped yet"),
    ("SELECT * FROM STRING_SPLIT('a,b', ',', 2)", "enable_ordinal must be a constant 0 or 1"),
    ("SELECT t.id FROM t CROSS APPLY STRING_SPLIT('a,b', ',', t.id) AS s", "enable_ordinal must be a constant 0 or 1"),
    ("SELECT * FROM STRING_SPLIT('a,b', NULL)", "STRING_SPLIT with a NULL separator isn't mapped yet"),
    ("SELECT * FROM STRING_SPLIT('a--b', '--')", "a separator of one character"),
])
def test_string_split_refuses_what_it_doesnt_carry_by_name(engine, sql, says):
    refused(engine, sql, says)


# ---- APPLY over a subquery

def test_cross_and_outer_apply_over_a_subquery(engine):
    assert rows(engine, "SELECT t.id, x.n FROM t CROSS APPLY (SELECT t.id * 2 AS n) AS x ORDER BY t.id") == [
        [1, 2], [2, 4], [3, 6], [4, 8]]
    assert rows(engine, "SELECT t.id, x.n FROM t OUTER APPLY (SELECT TOP 1 u.id AS n FROM t AS u WHERE u.id > t.id "
                        "ORDER BY u.id) AS x ORDER BY t.id") == [[1, 2], [2, 3], [3, 4], [4, None]]
    assert rows(engine, "SELECT t.id, x.n FROM t CROSS APPLY (SELECT TOP 1 u.id AS n FROM t AS u WHERE u.id > t.id "
                        "ORDER BY u.id) AS x ORDER BY t.id") == [[1, 2], [2, 3], [3, 4]]


# ---- VALUES in FROM

def test_values_in_from_runs_with_its_columns_named(engine):
    assert rows(engine, "SELECT n, c FROM (VALUES (1, 'a'), (2, 'b')) AS v(n, c) ORDER BY n") == [[1, "a"], [2, "b"]]
    assert rows(engine, "SELECT v.n FROM (VALUES (1), (2)) AS v (n) ORDER BY n") == [[1], [2]]


@pytest.mark.parametrize("sql, says", [
    ("SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v", "No column name was specified for column 1 of 'v'."),
    ("SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v(n)", "No column name was specified for column 2 of 'v'."),
    ("SELECT * FROM (VALUES (1, 'a')) AS v(n, c, d)", "'v' has fewer columns than were specified in the column list."),
    ("SELECT * FROM (VALUES (1), (2))", "needs a name and a name for each column"),
])
def test_values_in_from_without_its_names_is_refused_as_a_warehouse_refuses_it(engine, sql, says):
    refused(engine, sql, says)


def test_insert_values_is_left_alone(engine):
    engine.execute("CREATE TABLE ins (a INT, b VARCHAR)")
    engine.execute("INSERT INTO ins VALUES (1, 'x'), (2, 'y')", dialect="tsql")
    assert rows(engine, "SELECT a, b FROM ins ORDER BY a") == [[1, "x"], [2, "y"]]


# ---- TOP with PERCENT and WITH TIES

@pytest.mark.parametrize("sql, expected", [
    ("SELECT TOP 33 PERCENT id FROM t ORDER BY id", [1, 2]),  # 1.32 rows: rounded up to 2
    ("SELECT TOP 10 PERCENT n FROM (VALUES (1), (2), (3)) AS v(n) ORDER BY n", [1]),  # 0.3 rows: 1
    ("SELECT TOP 50 PERCENT id FROM t ORDER BY id DESC", [4, 3]),
    ("SELECT TOP 0 PERCENT id FROM t ORDER BY id", []),
    ("SELECT TOP 100 PERCENT id FROM t ORDER BY id", [1, 2, 3, 4]),
    ("SELECT TOP (25) PERCENT id FROM t ORDER BY id", [1]),
    ("SELECT TOP 2 PERCENT WITH TIES n FROM (VALUES (1), (1), (2)) AS v(n) ORDER BY n", [1, 1]),
    ("SELECT TOP 2 WITH TIES n FROM (VALUES (1), (2), (2), (3)) AS v(n) ORDER BY n", [1, 2, 2]),
    ("SELECT TOP 40 PERCENT WITH TIES n FROM (VALUES (1), (2), (2), (3), (4)) AS v(n) ORDER BY n", [1, 2, 2]),
])
def test_top_percent_rounds_up_and_with_ties_keeps_the_tied_rows(engine, sql, expected):
    assert [r[0] for r in rows(engine, sql)] == expected


def test_top_percent_counts_the_rows_after_grouping(engine):
    assert rows(engine, "SELECT TOP 50 PERCENT id % 2 AS k, count(*) AS n FROM t GROUP BY id % 2 ORDER BY k") == [[0, 2]]


@pytest.mark.parametrize("sql, says", [
    ("SELECT TOP 101 PERCENT id FROM t ORDER BY id", "Percent values must be between 0 and 100."),
    ("SELECT TOP (-1) PERCENT id FROM t ORDER BY id", "Percent values must be between 0 and 100."),
    ("SELECT TOP 2 WITH TIES n FROM (VALUES (1), (2), (2), (3)) AS v(n)", "only with an ORDER BY"),
    ("SELECT TOP 2 PERCENT WITH TIES n FROM (VALUES (1), (2)) AS v(n)", "needs an ORDER BY"),
    ("SELECT DISTINCT TOP 50 PERCENT id FROM t ORDER BY id", "without DISTINCT"),
])
def test_top_refuses_what_it_doesnt_carry_by_name(engine, sql, says):
    refused(engine, sql, says)
