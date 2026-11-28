"""Silent differences in local T-SQL, round 5: CONVERT's date styles, aggregates and windows.

- WR-731: every CONVERT date style to text is written as Learn's "Date and time styles" table shows it
  (functions/cast-and-convert-transact-sql), or refused by name where the table doesn't settle the style for the
  type; none runs with an "unchecked style" note. Text read with an unchecked style is refused by name.
- WR-732: ROW_NUMBER, RANK, DENSE_RANK, NTILE, LAG, LEAD, FIRST_VALUE, LAST_VALUE, CUME_DIST and PERCENT_RANK need
  ORDER BY in OVER: error 4112 (Database Engine errors 4000 to 4999).
- WR-733: AVG of tinyint, smallint and int is an int, of bigint a bigint (functions/avg-transact-sql).
- WR-734: COUNT is an int, COUNT_BIG a bigint (functions/count-transact-sql).
- WR-735: SUM and AVG of a bit are error 8117 (their pages: "except for the bit data type").
- WR-736: a frame on a ranking function (10752), a frame with no ORDER BY (10756), DISTINCT with OVER (10759).
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Silent sweep 5")["id"])
    e.execute("CREATE TABLE dbo.a (id INT, g INT, x INT, s SMALLINT, b BIGINT, d DECIMAL(10,2), bt BIT)", dialect="tsql")
    e.execute("INSERT INTO dbo.a VALUES (1, 1, 10, 1, 5, 1.50, 1), (2, 1, 10, 2, 6, 2.25, 0), (3, 1, 20, 4, 7, NULL, 1), "
              "(4, 2, NULL, NULL, NULL, 3.00, NULL), (5, 2, 30, 8, 9, 4.10, 0)", dialect="tsql")
    e.execute("CREATE TABLE dbo.big (x INT)", dialect="tsql")
    e.execute("INSERT INTO dbo.big VALUES (2000000000), (2000000000)", dialect="tsql")
    yield e
    local.close_all()


def run(engine, sql):
    return engine.execute(sql, dialect="tsql")


def one(engine, sql):
    return run(engine, sql)["rows"][0][0]


# ---------- WR-731: CONVERT's date styles as Learn's table writes them

DT = "CAST('2024-01-02 14:23:44.5' AS DATETIME)"
SDT = "CAST('2024-01-02 14:23:44' AS SMALLDATETIME)"
DATE = "CAST('2024-01-02' AS DATE)"
DT2 = "CAST('2024-01-02 14:23:44.5' AS DATETIME2)"
TIME = "CAST('14:23:44.5' AS TIME)"


@pytest.mark.parametrize("value, style, want", [
    (DATE, 1, "01/02/24"), (DATE, 2, "24.01.02"), (DATE, 102, "2024.01.02"), (DATE, 3, "02/01/24"),
    (DATE, 4, "02.01.24"), (DATE, 104, "02.01.2024"), (DATE, 5, "02-01-24"), (DATE, 105, "02-01-2024"),
    (DATE, 6, "02 Jan 24"), (DATE, 106, "02 Jan 2024"), (DATE, 7, "Jan 02, 24"), (DATE, 107, "Jan 02, 2024"),
    (DATE, 10, "01-02-24"), (DATE, 110, "01-02-2024"), (DATE, 11, "24/01/02"), (DATE, 111, "2024/01/02"),
    (DATE, 12, "240102"), (DATE, 23, "2024-01-02"),
    (DT, 104, "02.01.2024"), (DT2, 105, "02-01-2024"), (SDT, 111, "2024/01/02"),
    (DT, 8, "14:23:44"), (DT, 24, "14:23:44"), (DT, 108, "14:23:44"), (TIME, 108, "14:23:44"), (DT2, 108, "14:23:44"),
    (DT, 14, "14:23:44:500"), (DT, 114, "14:23:44:500"), (DT, 13, "02 Jan 2024 14:23:44:500"),
    (DT, 113, "02 Jan 2024 14:23:44:500"),
    (SDT, 114, "14:24:00:000"), (SDT, 113, "02 Jan 2024 14:24:00:000"),  # smalldatetime "show zeros"; 44s rounds up
    (DT, 100, "Jan  2 2024  2:23PM"), (DT, 20, "2024-01-02 14:23:44"), (DT, 21, "2024-01-02 14:23:44.500"),
    (DT, 25, "2024-01-02 14:23:44.500"),
    ("CAST('2024-01-02 10:00:00.002' AS DATETIME)", 114, "10:00:00:003"),  # the datetime's own rounding
])
def test_a_style_is_written_as_learns_table_shows(engine, value, style, want):
    r = run(engine, f"SELECT CONVERT(VARCHAR(40), {value}, {style})")
    assert r["rows"] == [[want]]
    assert not (r.get("translation") or {}).get("notes")


@pytest.mark.parametrize("value, style", [
    (TIME, 114), (TIME, 14), (DT2, 114), (DT2, 113), (DATE, 108), (DATE, 8), (TIME, 104), (TIME, 23),
    (DT, 9), (DT, 109), (DT, 22),
])
def test_a_style_the_table_doesnt_settle_for_the_type_is_refused(engine, value, style):
    with pytest.raises(ValueError, match=f"CONVERT style {style} of a .* isn't mapped yet"):
        run(engine, f"SELECT CONVERT(VARCHAR(40), {value}, {style})")


@pytest.mark.parametrize("style", [127, 130, 131])
def test_the_read_only_and_hijri_styles_are_refused(engine, style):
    with pytest.raises(ValueError, match="can't be run on the local engine"):
        run(engine, f"SELECT CONVERT(VARCHAR(40), {DT}, {style})")


@pytest.mark.parametrize("text, style", [("02.01.2024", 104), ("01/02/24", 1), ("Jan 02, 2024", 107),
                                         ("14:23:44", 108)])
def test_text_read_with_an_unchecked_style_is_refused(engine, text, style):
    with pytest.raises(ValueError, match=f"CONVERT of text to a date with style {style} isn't mapped yet"):
        run(engine, f"SELECT CONVERT(DATETIME, '{text}', {style})")


@pytest.mark.parametrize("sql, want", [
    ("SELECT CONVERT(DATE, '01/02/2024', 101)", "2024-01-02"), ("SELECT CONVERT(DATE, '2024-01-02', 21)", "2024-01-02"),
    ("SELECT CONVERT(DATETIME, 'Jan  2 2024  3:04PM', 100)", "2024-01-02 15:04:00"),
])
def test_text_read_with_a_checked_style_or_its_alias_runs(engine, sql, want):
    assert str(one(engine, sql)) == want


# ---------- WR-732, WR-736: OVER as T-SQL takes it

@pytest.mark.parametrize("call, name", [
    ("ROW_NUMBER() OVER ()", "ROW_NUMBER"), ("RANK() OVER (PARTITION BY g)", "RANK"),
    ("DENSE_RANK() OVER ()", "DENSE_RANK"), ("NTILE(2) OVER ()", "NTILE"), ("LAG(x) OVER ()", "LAG"),
    ("LEAD(x) OVER (PARTITION BY g)", "LEAD"), ("FIRST_VALUE(x) OVER ()", "FIRST_VALUE"),
    ("LAST_VALUE(x) OVER ()", "LAST_VALUE"), ("CUME_DIST() OVER ()", "CUME_DIST"),
    ("PERCENT_RANK() OVER ()", "PERCENT_RANK"),
])
def test_a_window_function_without_order_by_is_4112(engine, call, name):
    with pytest.raises(ValueError, match=f"Msg 4112, Level 16: The function '{name}' must have an OVER clause with "
                                         "ORDER BY."):
        run(engine, f"SELECT {call} FROM dbo.a")


def test_an_order_by_in_a_named_window_counts(engine):
    assert run(engine, "SELECT ROW_NUMBER() OVER w FROM dbo.a WINDOW w AS (ORDER BY id)")["rows"] == [
        [1], [2], [3], [4], [5]]
    with pytest.raises(ValueError, match="Msg 4112"):
        run(engine, "SELECT ROW_NUMBER() OVER w FROM dbo.a WINDOW w AS (PARTITION BY g)")


@pytest.mark.parametrize("call, says", [
    ("ROW_NUMBER() OVER (ORDER BY x ROWS UNBOUNDED PRECEDING)",
     "Msg 10752, Level 15: The function 'ROW_NUMBER' may not have a window frame."),
    ("RANK() OVER (ORDER BY x RANGE UNBOUNDED PRECEDING)", "Msg 10752, Level 15: The function 'RANK' may not"),
    ("LAG(x) OVER (ORDER BY x ROWS UNBOUNDED PRECEDING)", "Msg 10752, Level 15: The function 'LAG' may not"),
    ("SUM(x) OVER (ROWS UNBOUNDED PRECEDING)",
     "Msg 10756, Level 15: Window frame with ROWS or RANGE must have an ORDER BY clause."),
    ("COUNT(DISTINCT x) OVER (PARTITION BY g)", "Msg 10759, Level 15: Use of DISTINCT is not allowed with the OVER"),
    ("SUM(DISTINCT x) OVER ()", "Msg 10759, Level 15"),
])
def test_over_clauses_t_sql_rejects(engine, call, says):
    with pytest.raises(ValueError, match=says.replace("(", r"\(").replace(")", r"\)")):
        run(engine, f"SELECT {call} FROM dbo.a")


def test_the_default_frame_with_order_by_is_range(engine):
    got = run(engine, "SELECT id, SUM(x) OVER (ORDER BY x), COUNT(*) OVER (ORDER BY x), "
                      "LAST_VALUE(x) OVER (ORDER BY x), FIRST_VALUE(id) OVER (ORDER BY x, id) FROM dbo.a ORDER BY id")
    assert got["rows"] == [[1, 20, 3, 10, 4], [2, 20, 3, 10, 4], [3, 40, 4, 20, 4], [4, None, 1, None, 4],
                           [5, 70, 5, 30, 4]]


def test_ranks_put_nulls_lowest(engine):
    got = run(engine, "SELECT id, RANK() OVER (ORDER BY x), DENSE_RANK() OVER (ORDER BY x), "
                      "RANK() OVER (ORDER BY x DESC), CUME_DIST() OVER (ORDER BY x), PERCENT_RANK() OVER (ORDER BY x) "
                      "FROM dbo.a ORDER BY id")
    assert got["rows"] == [[1, 2, 2, 3, 0.6, 0.25], [2, 2, 2, 3, 0.6, 0.25], [3, 4, 3, 2, 0.8, 0.75],
                           [4, 1, 1, 5, 0.2, 0.0], [5, 5, 4, 1, 1.0, 1.0]]
    assert [c["type"] for c in got["columns"][1:]] == ["int64", "int64", "int64", "double", "double"]


# ---------- WR-733, WR-734: AVG's and COUNT's types

def test_count_is_an_int_and_count_big_a_bigint(engine):
    r = run(engine, "SELECT COUNT(*), COUNT(x), COUNT(DISTINCT x), COUNT_BIG(*) FROM dbo.a")
    assert r["rows"][0] == [5, 4, 3, 5]
    assert [c["type"] for c in r["columns"]] == ["int32", "int32", "int32", "int64"]
    r = run(engine, "SELECT id, COUNT(*) OVER (), COUNT(x) OVER (PARTITION BY g) FROM dbo.a ORDER BY id")
    assert r["rows"] == [[1, 5, 3], [2, 5, 3], [3, 5, 3], [4, 5, 1], [5, 5, 1]]
    assert [c["type"] for c in r["columns"]][1:] == ["int32", "int32"]
    run(engine, "SELECT COUNT(*) AS n INTO dbo.counted FROM dbo.a")
    assert one(engine, "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'counted'") == "INTEGER"


def test_avg_of_an_integer_keeps_its_type(engine):
    r = run(engine, "SELECT AVG(x), AVG(s), AVG(b) FROM dbo.a WHERE id = 1")
    assert r["rows"] == [[10, 1, 5]]
    assert [c["type"] for c in r["columns"]] == ["int32", "int32", "int64"]
    r = run(engine, "SELECT id, AVG(x) OVER (PARTITION BY g) FROM dbo.a ORDER BY id")
    assert r["rows"] == [[1, 13], [2, 13], [3, 13], [4, 30], [5, 30]] and r["columns"][1]["type"] == "int32"


def test_avg_of_a_decimal_is_decimal_38_max_s_6(engine):
    r = run(engine, "SELECT AVG(d), SUM(d) FROM dbo.a")
    assert r["rows"] == [["2.712500", "10.85"]]
    assert [c["type"] for c in r["columns"]] == ["decimal128(38, 6)", "decimal128(38, 2)"]


def test_avg_of_an_int_whose_sum_is_past_an_int_stops_by_name(engine):
    with pytest.raises(Exception, match="AVG of an int whose sum is past an int's range stops in T-SQL"):
        run(engine, "SELECT AVG(x) FROM dbo.big")
    assert one(engine, "SELECT AVG(CAST(x AS BIGINT)) FROM dbo.big") == 2000000000


# ---------- WR-735: no SUM or AVG of a bit

@pytest.mark.parametrize("call, op", [("SUM(bt)", "sum"), ("AVG(bt)", "avg"), ("SUM(bt) OVER ()", "sum")])
def test_sum_and_avg_of_a_bit_are_8117(engine, call, op):
    with pytest.raises(ValueError, match=f"Msg 8117, Level 16: Operand data type bit is invalid for {op} operator."):
        run(engine, f"SELECT {call} FROM dbo.a")


# ---------- the rest of the sweep, as Learn gives it

def test_stdev_and_var_with_one_row_and_none(engine):
    assert run(engine, "SELECT STDEV(x), STDEVP(x), VAR(x), VARP(x) FROM dbo.a WHERE id = 1")["rows"] == [
        [None, 0.0, None, 0.0]]
    assert run(engine, "SELECT STDEV(x), STDEVP(x), VAR(x), VARP(x) FROM dbo.a WHERE id > 100")["rows"] == [
        [None, None, None, None]]
    r = run(engine, "SELECT VARP(x), VAR(d) FROM dbo.a")
    assert r["rows"][0][0] == 68.75 and [c["type"] for c in r["columns"]] == ["double", "double"]


def test_grouping_sets_put_the_null_rows_first(engine):
    got = run(engine, "SELECT g, x, COUNT(*) FROM dbo.a GROUP BY GROUPING SETS ((g, x), (g), ()) "
                      "ORDER BY g, x, COUNT(*) DESC")
    assert got["rows"] == [[None, None, 5], [1, None, 3], [1, 10, 2], [1, 20, 1], [2, None, 2], [2, None, 1],
                           [2, 30, 1]]


def test_approx_count_distinct_is_a_bigint(engine):
    r = run(engine, "SELECT APPROX_COUNT_DISTINCT(x) FROM dbo.a")
    assert r["rows"] == [[3]] and r["columns"][0]["type"] == "int64"
