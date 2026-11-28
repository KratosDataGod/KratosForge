"""WR-711 to WR-716: queries that ran on the local T-SQL engine and gave another value than a Fabric Warehouse.

Each answer is from T-SQL's reference on Microsoft Learn, and each runs on a database table and on one of the
project's Delta tables:

- WR-711: a rewrite that copies its argument (CEILING, FLOOR, ROUND, POWER, DEGREES, a division, a CAST) ran before
  the rewrite inside it, so ``CEILING(7 / 2)`` divided as decimals (4), and ``CAST(CAST(2.7 AS INT) AS ...)``
  rounded. They now run inside out, and an integer result is typed as one for the rewrite around it.
- WR-712: LAG and LEAD have their value's type (functions/lag-transact-sql, "Return types"), so a decimal default for
  an integer value is cut toward zero: 1 for 1.5, as example C gives. DuckDB rounded it to 2.
- WR-713: LAG and LEAD with IGNORE NULLS and an offset other than 1 are refused by name: example C goes back that
  many rows and then on to the nearest value that isn't NULL, where DuckDB counts only the rows that aren't NULL.
- WR-714: a decimal or float where T-SQL takes a whole number is cut toward zero (CHOOSE's index, DATEADD's number,
  SUBSTRING's start, LAG's offset); TOP and NTILE of a fraction are refused by name.
- WR-715: SIGN has its argument's type (functions/sign-transact-sql), so ``SIGN(2.50) / 2`` is 0.5, not 0.
- WR-716: an int too long for the text it's cast to is ``*`` (functions/cast-and-convert-transact-sql).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Silent sweep")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, i INT, d DECIMAL(10, 2), f FLOAT, s VARCHAR(20))", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, -7, -7.25, -2.5, 'ab  '), (2, 7, 2.50, 2.5, NULL), "
              "(3, NULL, NULL, NULL, 'x')", dialect="tsql")
    e.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM dbo.t", dialect="spark")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def one(engine, sql):
    return rows(engine, sql)[0][0]


TABLES = ["dbo.t", "dbo.dt"]


# ---------- WR-711: the rewrite inside runs first

@pytest.mark.parametrize("table", TABLES)
def test_ceiling_and_floor_of_an_integer_division_divide_as_integers(engine, table):
    # divide-transact-sql: an integer divided by an integer is an integer, cut toward zero; then
    # ceiling-transact-sql: int in, int out. Here CEILING(7 / 2) was 4 and FLOOR(-7 / 2) was -4.
    got = rows(engine, f"SELECT CEILING(i / 2), FLOOR(i / 2), ROUND(i / 2, 0), POWER(i / 2, 2), DEGREES(i / 2), "
                       f"CEILING(i % 4 / 2) FROM {table} ORDER BY id")
    assert got == [[-3, -3, -3, 9, -171, -1], [3, 3, 3, 9, 171, 1], [None] * 6]


@pytest.mark.parametrize("sql, want", [
    ("SELECT CEILING(7 / 2)", 3), ("SELECT FLOOR(-7 / 2)", -3), ("SELECT POWER(7 / 2, 2)", 9),
    ("SELECT CEILING(7 / 2) / 2", 1), ("SELECT CEILING(CEILING(7 / 2) / 2)", 1),
    ("SELECT CAST(ROUND(7 / 2, 0) AS INT)", 3), ("SELECT 'x' + CAST(FLOOR(-7 / 2) AS VARCHAR(10))", "x-3"),
    ("SELECT CAST(CAST(2.7 AS INT) AS VARCHAR(5))", "2"), ("SELECT CEILING(CAST(2.5 AS INT))", 2),
])
def test_a_rewrite_around_another_keeps_the_inner_one(engine, sql, want):
    assert one(engine, sql) == want


@pytest.mark.parametrize("table", TABLES)
def test_a_division_of_an_integer_division_stays_whole(engine, table):
    # (i / 2) / id was -3.5 and 1.75 here: the outer division copied the inner one before it was made whole.
    assert rows(engine, f"SELECT i / 2 / id, (i / 2) % id FROM {table} ORDER BY id") == [[-3, 0], [1, 1],
                                                                                          [None, None]]


def test_count_divided_into_pages(engine):
    assert one(engine, "SELECT CEILING(COUNT(*) / 2) FROM dbo.t") == 1  # 3 / 2 is 1 in T-SQL; it was 2


def test_a_ceiling_of_a_division_by_zero_is_tsqls_error(engine):
    with pytest.raises(Exception, match="Msg 8134"):
        engine.execute("SELECT CEILING(i / 0) FROM dbo.t WHERE id = 1", dialect="tsql")


def test_text_of_a_random_whole_number_reads_it_once(engine):
    for _ in range(20):
        value = one(engine, "SELECT CAST(CAST(RAND() * 10 AS INT) AS VARCHAR(5))")
        assert value in [str(n) for n in range(10)]


# ---------- WR-712: LAG and LEAD have their value's type

@pytest.mark.parametrize("table", TABLES)
def test_a_decimal_default_for_an_integer_value_is_cut(engine, table):
    got = rows(engine, f"SELECT LAG(i, 1, 1.5) OVER (ORDER BY id), LEAD(i, 5, 2.9) OVER (ORDER BY id), "
                       f"LEAD(i, 1, -2.9) OVER (ORDER BY id), LAG(i, 1, f) OVER (ORDER BY id) FROM {table} "
                       "ORDER BY id")
    assert got == [[1, 2, 7, -2], [-7, 2, None, -7], [7, 2, -2, 7]]


def test_microsofts_example_c_default(engine):
    # functions/lag-transact-sql, example C: the first row's default is -c / 2.0 = 1.5, an int column shows 1.
    got = rows(engine, "SELECT a, LAG(2 * c, 1, -c / 2.0) OVER (ORDER BY a) FROM (VALUES (1, -3), (2, 4)) "
                       "AS t(a, c) ORDER BY a")
    assert got == [[1, 1], [2, -6]]


def test_a_default_of_another_kind_keeps_its_rules(engine):
    assert one(engine, "SELECT LAG(d, 1, 1.255) OVER (ORDER BY id) FROM dbo.t ORDER BY id") == "1.26"
    assert one(engine, "SELECT LAG(i, 1, '12') OVER (ORDER BY id) FROM dbo.t ORDER BY id") == 12
    with pytest.raises(Exception, match="Msg 245"):
        engine.execute("SELECT LAG(i, 1, '2.5') OVER (ORDER BY id) FROM dbo.t", dialect="tsql")


# ---------- WR-713: IGNORE NULLS with another offset than 1

def test_ignore_nulls_with_an_offset_past_1_is_refused(engine):
    with pytest.raises(ValueError, match="IGNORE NULLS with an offset other than 1") as err:
        engine.execute("SELECT LAG(i, 2) IGNORE NULLS OVER (ORDER BY id) FROM dbo.t", dialect="tsql")
    assert "LAG" in str(err.value) and "Nothing ran" in str(err.value)
    with pytest.raises(ValueError, match="LEAD ... IGNORE NULLS"):
        engine.execute("SELECT LEAD(i, id) IGNORE NULLS OVER (ORDER BY id) FROM dbo.dt", dialect="tsql")


def test_ignore_nulls_with_an_offset_of_1_runs_as_microsofts_example_d(engine):
    values = "(1, 8), (2, 9), (3, NULL), (4, 10), (5, NULL), (6, NULL), (7, 11)"
    got = rows(engine, "SELECT a, LAG(b) IGNORE NULLS OVER (ORDER BY a), LEAD(b, 1) IGNORE NULLS OVER (ORDER BY a) "
                       f"FROM (VALUES {values}) AS t(a, b) ORDER BY a")
    assert [r[1:] for r in got] == [[None, 9], [8, 10], [9, 10], [9, 11], [10, 11], [10, 11], [10, None]]


# ---------- WR-714: whole-number arguments

@pytest.mark.parametrize("sql, want", [
    ("SELECT CHOOSE(1.9, 'a', 'b')", "a"), ("SELECT CHOOSE(2.1, 'a', 'b')", "b"),
    ("SELECT SUBSTRING('abcdef', 2.9, 2)", "bc"), ("SELECT SUBSTRING('abcdef', 2, 2.9)", "bc"),
    ("SELECT STUFF('abcdef', 2.9, 1, 'x')", "axcdef"), ("SELECT CHARINDEX('c', 'abcabc', 3.9)", 3),
    ("SELECT CONVERT(VARCHAR(10), DATEADD(day, 1.9, CAST('2024-01-01' AS DATE)), 23)", "2024-01-02"),
    ("SELECT CONVERT(VARCHAR(10), DATEADD(day, -1.9, CAST('2024-01-01' AS DATE)), 23)", "2023-12-31"),
    ("SELECT CONVERT(VARCHAR(10), EOMONTH(CAST('2024-01-31' AS DATE), 1.9), 23)", "2024-02-29"),
    ("SELECT CONVERT(VARCHAR(10), DATEFROMPARTS(2024, 1.9, 1), 23)", "2024-01-01"),
    ("SELECT ROUND(2.567, 1.9)", "2.600"),
])
def test_a_fraction_where_tsql_takes_a_whole_number_is_cut(engine, sql, want):
    assert one(engine, sql) == want


@pytest.mark.parametrize("table", TABLES)
def test_a_fractional_lag_offset_is_cut(engine, table):
    assert rows(engine, f"SELECT LAG(id, 1.9) OVER (ORDER BY id) FROM {table} ORDER BY id") == [[None], [1], [2]]
    assert rows(engine, f"SELECT CHOOSE(f + 3, 'a', 'b', 'c', 'd', 'e', 'f') FROM {table} ORDER BY id") == [
        [None], ["e"], [None]]  # 0.5 is cut to 0 (NULL), 5.5 to 5


@pytest.mark.parametrize("sql, name", [
    ("SELECT TOP (2.5) id FROM dbo.t ORDER BY id", "TOP"),
    ("SELECT NTILE(2.5) OVER (ORDER BY id) FROM dbo.t", "NTILE"),
])
def test_top_and_ntile_of_a_fraction_are_refused(engine, sql, name):
    with pytest.raises(ValueError, match=f"{name} with a number that isn't whole"):
        engine.execute(sql, dialect="tsql")


def test_top_percent_still_takes_a_fraction(engine):
    assert rows(engine, "SELECT TOP (50.5) PERCENT id FROM dbo.t ORDER BY id") == [[1], [2]]


# ---------- WR-715: SIGN has its argument's type

@pytest.mark.parametrize("table", TABLES)
def test_sign_keeps_its_arguments_type(engine, table):
    got = rows(engine, f"SELECT SIGN(d), SIGN(d) / 2, SIGN(f) / 2, SIGN(i) / 2 FROM {table} ORDER BY id")
    assert got == [["-1.00", "-0.500000", -0.5, 0], ["1.00", "0.500000", 0.5, 0],
                   [None] * 4]


def test_sign_of_written_numbers(engine):
    assert rows(engine, "SELECT SIGN(-2.50), SIGN(CAST(5 AS FLOAT)) / 2, CONCAT(SIGN(2.50), ''), "
                        "SIGN(CAST(-3 AS BIGINT))") == [["-1.00", 0.5, "1.00", -1]]


# ---------- WR-716: an int too long for its text is '*'

@pytest.mark.parametrize("sql, want", [
    ("SELECT CAST(12345 AS VARCHAR(3))", "*"), ("SELECT CAST(123 AS VARCHAR(3))", "123"),
    ("SELECT CAST(-12 AS VARCHAR(2))", "*"), ("SELECT CONVERT(VARCHAR(2), 100)", "*"),
    ("SELECT CAST(12345 AS CHAR(3))", "*  "), ("SELECT CAST(CAST(12345 AS SMALLINT) AS VARCHAR(3))", "*"),
    ("SELECT ISNULL(CAST(NULL AS VARCHAR(2)), 12345)", "*"),
])
def test_an_int_too_long_for_its_text_is_a_star(engine, sql, want):
    assert one(engine, sql) == want


@pytest.mark.parametrize("table", TABLES)
def test_an_int_column_too_long_for_its_text_is_a_star(engine, table):
    assert rows(engine, f"SELECT CAST(i AS VARCHAR(1)) FROM {table} ORDER BY id") == [["*"], ["7"], [None]]


@pytest.mark.parametrize("sql", ["SELECT CAST(123.45 AS VARCHAR(3))", "SELECT CAST(CAST(12345 AS BIGINT) AS VARCHAR(3))",
                                 "SELECT CAST(CAST(12345 AS FLOAT) AS VARCHAR(3))"])
def test_other_numbers_too_long_for_their_text_still_stop(engine, sql):
    with pytest.raises(Exception, match="doesn't fit in VARCHAR"):
        engine.execute(sql, dialect="tsql")
