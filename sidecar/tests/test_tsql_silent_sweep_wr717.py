"""WR-717 to WR-722: the second round of local T-SQL answers that differed from a Fabric Warehouse, or stopped in
other words than T-SQL's. Each answer is from T-SQL's reference on Microsoft Learn.

- WR-717: a VARCHAR(n) column keeps its length in the types the translator reads, so ISNULL(column, ...) and
  LAG/LEAD(column, n, ...) cut a longer value to n, as functions/isnull-transact-sql ("can be truncated") and
  functions/lag-transact-sql ("The data type of the specified scalar_expression") say. A Delta table's text is
  varchar(8000), as a lakehouse's SQL analytics endpoint reads it.
- WR-718: CAST and TRY_CAST of '' to float, real, bit or a date or time type are refused by name: T-SQL's reference
  documents only '' to a decimal (an error, kept here).
- WR-719: DATEFROMPARTS of a date that isn't there is 289, DATEADD past 9999-12-31 is 517, and LEFT or RIGHT of a
  negative length is 536 (Database Engine events and errors). DuckDB made year 10000 a date.
- WR-720: a number where REPLACE, TRIM, UPPER and the other text functions take text is written as text, as T-SQL
  converts it (REPLACE(12.5, '.', ',') is '12,5'), and STR's decimals past 16 are 16.
- WR-721: CONCAT and CONCAT_WS with too few values stop, as T-SQL's pages say, and CONCAT_WS with a NULL separator
  is refused by name.
- WR-722: + - * / % with a bit are refused by name: language-elements/add-transact-sql leaves bit out.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Silent sweep 2")["id"])
    e.execute("CREATE TABLE dbo.t (id INT, i INT, d DECIMAL(10, 2), f FLOAT, s VARCHAR(5), c CHAR(4), "
              "dt DATE)", dialect="tsql")
    e.execute("INSERT INTO dbo.t VALUES (1, -7, -7.25, -2.5, 'ab', 'x', '9999-12-15'), "
              "(2, 7, 2.50, 2.5, NULL, NULL, '2024-01-31')", dialect="tsql")
    e.execute("CREATE TABLE dbo.dt USING DELTA AS SELECT * FROM dbo.t", dialect="spark")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def one(engine, sql):
    return rows(engine, sql)[0][0]


# ---------- WR-717: a text column's length

def test_isnull_of_a_sized_column_cuts_its_replacement(engine):
    got = rows(engine, "SELECT ISNULL(s, 'abcdefgh'), ISNULL(c, 'vwxyz'), COALESCE(s, 'abcdefgh') FROM dbo.t "
                       "ORDER BY id")
    # COALESCE takes the longer type. c is char(4), so its 'x' is 'x   ' (WR-723)
    assert got == [["ab", "x   ", "ab"], ["abcde", "vwxy", "abcdefgh"]]


def test_lag_and_lead_of_a_sized_column_cut_their_default(engine):
    got = rows(engine, "SELECT LAG(s, 1, 'abcdefgh') OVER (ORDER BY id), LEAD(s, 1, 'zzzzzzz') OVER (ORDER BY id) "
                       "FROM dbo.t ORDER BY id")
    assert got == [["abcde", None], ["ab", "zzzzz"]]


def test_a_number_meets_a_sized_column_as_cast_does(engine):
    assert rows(engine, "SELECT ISNULL(s, i) FROM dbo.t ORDER BY id") == [["ab"], ["7"]]
    assert rows(engine, "SELECT ISNULL(s, 123456) FROM dbo.t ORDER BY id") == [["ab"], ["*"]]


def test_a_delta_tables_text_is_varchar_8000(engine):
    long = "y" * 30
    assert rows(engine, f"SELECT ISNULL(s, '{long}') FROM dbo.dt ORDER BY id") == [["ab"], [long]]


# ---------- WR-718: '' to a type T-SQL's reference gives no answer for

@pytest.mark.parametrize("kind", ["FLOAT", "REAL", "BIT", "DATE", "TIME", "DATETIME2"])
@pytest.mark.parametrize("call", ["CAST('' AS {k})", "TRY_CAST(' ' AS {k})", "TRY_CONVERT({k}, '')"])
def test_empty_text_to_an_undocumented_type_is_refused(engine, call, kind):
    with pytest.raises(ValueError, match=r"of empty text \(''\) to \w+ isn't mapped yet") as err:
        engine.execute(f"SELECT {call.format(k=kind)} AS v", dialect="tsql")
    assert "Nothing ran" in str(err.value)


def test_an_empty_text_value_stops_when_it_is_read(engine):
    with pytest.raises(Exception, match=r"of empty text \(''\) to float isn't mapped yet"):
        engine.execute("SELECT TRY_CAST(v AS FLOAT) FROM (VALUES ('1.5'), ('')) AS t(v)", dialect="tsql")
    assert rows(engine, "SELECT TRY_CAST(v AS FLOAT) FROM (VALUES ('1.5'), ('x'), (NULL)) AS t(v)") == [
        [1.5], [None], [None]]


def test_empty_text_to_a_number_keeps_its_documented_answers(engine):
    assert one(engine, "SELECT CAST('' AS INT)") == 0
    with pytest.raises(Exception, match="Msg 8114"):  # cast-and-convert: '' to numeric or decimal is an error
        engine.execute("SELECT CAST('' AS DECIMAL(5, 2))", dialect="tsql")


# ---------- WR-719: T-SQL's numbered errors for dates and lengths

@pytest.mark.parametrize("sql", ["SELECT DATEFROMPARTS(2024, 2, 30)", "SELECT DATEFROMPARTS(10000, 1, 1)",
                                 "SELECT DATEFROMPARTS(0, 1, 1)", "SELECT DATEFROMPARTS(2024, 13, 1)",
                                 "SELECT DATEFROMPARTS(y, 2, 30) FROM (VALUES (2024)) AS v(y)"])
def test_datefromparts_of_a_date_that_isnt_there_is_289(engine, sql):
    with pytest.raises(Exception, match="Msg 289, Level 16: Cannot construct data type date, some of the arguments "
                                        "have values which are not valid."):
        engine.execute(sql, dialect="tsql")


def test_datefromparts_still_makes_dates_and_nulls(engine):
    assert str(one(engine, "SELECT DATEFROMPARTS(2024, 2, 29)")) == "2024-02-29"
    assert one(engine, "SELECT DATEFROMPARTS(2024, NULL, 1)") is None


@pytest.mark.parametrize("sql, kind", [
    ("SELECT DATEADD(day, 1, CAST('9999-12-31' AS DATE))", "date"),
    ("SELECT DATEADD(day, -1, CAST('0001-01-01' AS DATE))", "date"),
    ("SELECT DATEADD(year, 1, CAST('9999-06-01' AS DATETIME2))", "datetime2"),
    ("SELECT COUNT(*) FROM dbo.t WHERE DATEADD(month, 1, dt) > '2000-01-01'", "date"),
    ("SELECT COUNT(*) FROM dbo.dt WHERE DATEADD(month, 1, dt) > '2000-01-01'", "date"),
])
def test_dateadd_past_the_last_day_is_517(engine, sql, kind):
    with pytest.raises(Exception, match=f"Msg 517, Level 16: Adding a value to a '{kind}' column caused an overflow"):
        engine.execute(sql, dialect="tsql")


def test_eomonth_past_the_last_day_stops_in_its_own_words(engine):
    with pytest.raises(Exception, match="EOMONTH's result is past 9999-12-31"):
        engine.execute("SELECT EOMONTH(CAST('9999-12-15' AS DATE), 1)", dialect="tsql")


@pytest.mark.parametrize("sql, name", [("SELECT LEFT('abc', -1)", "LEFT"), ("SELECT RIGHT('abc', -1)", "RIGHT"),
                                       ("SELECT LEFT(s, i) FROM dbo.t WHERE id = 1", "LEFT")])
def test_left_and_right_of_a_negative_length_are_536(engine, sql, name):
    with pytest.raises(Exception, match=f"Msg 536, Level 16: Invalid length parameter passed to the {name} function"):
        engine.execute(sql, dialect="tsql")


# ---------- WR-720: a number as text in the text functions

@pytest.mark.parametrize("sql, want", [
    ("SELECT REPLACE(12.5, '.', ',')", "12,5"), ("SELECT REPLACE(100, '0', 'x')", "1xx"),
    ("SELECT LTRIM(5)", "5"), ("SELECT RTRIM(2.50)", "2.50"), ("SELECT TRIM(2.50)", "2.50"),
    ("SELECT TRANSLATE(12.5, '.', ',')", "12,5"), ("SELECT STUFF(12345, 2, 1, 'x')", "1x345"),
    ("SELECT SUBSTRING(12.5, 1, 2)", "12"), ("SELECT REPLICATE(1.5, 2)", "1.51.5"),
    ("SELECT CHARINDEX('.', 12.5)", 3), ("SELECT STR(12345.678, 10, 20)", "12345.6780"),
    ("SELECT STR(1.5, 10, 20)", "1.50000000"),
])
def test_a_number_in_a_text_function_is_its_text(engine, sql, want):
    assert one(engine, sql) == want


@pytest.mark.parametrize("table", ["dbo.t", "dbo.dt"])
def test_a_number_column_in_a_text_function_is_its_text(engine, table):
    assert rows(engine, f"SELECT REPLACE(d, '.', ','), LOWER(f), REPLACE(i, '7', 'x') FROM {table} ORDER BY id") == [
        ["-7,25", "-2.5", "-x"], ["2,50", "2.5", "x"]]


# ---------- WR-721: CONCAT and CONCAT_WS take at least two values

@pytest.mark.parametrize("sql, name", [("SELECT CONCAT('a')", "CONCAT"), ("SELECT CONCAT_WS(',', 'a')", "CONCAT_WS")])
def test_concat_of_one_value_stops(engine, sql, name):
    with pytest.raises(ValueError, match=f"{name} takes"):
        engine.execute(sql, dialect="tsql")


def test_concat_ws_with_a_null_separator_is_refused(engine):
    with pytest.raises(ValueError, match="CONCAT_WS with a NULL separator isn't mapped yet"):
        engine.execute("SELECT CONCAT_WS(NULL, 'a', 'b')", dialect="tsql")
    with pytest.raises(Exception, match="CONCAT_WS with a NULL separator isn't mapped yet"):
        engine.execute("SELECT CONCAT_WS(s, 'a', 'b') FROM dbo.t", dialect="tsql")  # s is NULL in row 2
    assert rows(engine, "SELECT CONCAT_WS(s, 'a', 'b') FROM dbo.t WHERE s IS NOT NULL") == [["aabb"]]
    assert one(engine, "SELECT CONCAT_WS(',', NULL, NULL)") == ""  # concat-ws: all NULL gives an empty string


# ---------- WR-722: arithmetic on a bit

@pytest.mark.parametrize("sql", ["SELECT CAST(1 AS BIT) + 1", "SELECT CAST(1 AS BIT) + CAST(1 AS BIT)",
                                 "SELECT 5 * CAST(1 AS BIT)", "SELECT CAST(1 AS BIT) - 1", "SELECT 4 % CAST(1 AS BIT)"])
def test_arithmetic_on_a_bit_is_refused(engine, sql):
    with pytest.raises(ValueError, match="with a bit value isn't mapped yet"):
        engine.execute(sql, dialect="tsql")


def test_a_bit_cast_to_int_adds(engine):
    assert one(engine, "SELECT CAST(CAST(1 AS BIT) AS INT) + 1") == 2
