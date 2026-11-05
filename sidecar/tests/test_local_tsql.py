"""FD-LDE-004: T-SQL on the local engine, translated to DuckDB SQL with T-SQL's answers."""

from __future__ import annotations

import datetime as dt
import re

import pytest
import duckdb

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import split_batches, translate


@pytest.fixture
def engine(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("TSQL")["id"])
    e.execute("CREATE TABLE dbo.orders AS SELECT * FROM (VALUES (1, 7, 2, 2.5, 'Ann  ', DATE '2026-10-18'), "
              "(2, -7, 2, 4.0, 'Bo', DATE '2026-01-04')) v(id, qty, boxes, price, name, day)")
    return e


def tsql(engine, sql):
    return engine.execute(sql, dialect="tsql")


def test_everyday_tsql_runs_and_gives_tsql_answers(engine):
    r = tsql(engine, "SELECT TOP 1 [id], ISNULL(NULL, qty) AS q, IIF(qty > 0, 'up', 'down') AS way "
                     "FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[1, 7, "up"]] and r["dialect"] == "tsql"
    assert [c["name"] for c in r["columns"]] == ["id", "q", "way"]


def test_integers_divide_and_average_as_integers(engine):
    r = tsql(engine, "SELECT qty / boxes AS per, 7 / 2 AS lit, price / boxes AS dec FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[3, 3, 1.25], [-3, 3, 2.0]]  # truncated toward zero, as T-SQL does
    assert tsql(engine, "SELECT AVG(boxes + id) AS a FROM dbo.orders")["rows"] == [[3]]  # (3 + 4) / 2


def test_an_integer_average_over_a_window_is_an_integer(engine):
    # The whole AVG(...) OVER (...) is cut, not the AVG inside it (that was a parser error).
    r = tsql(engine, "SELECT AVG(id) OVER () AS a, AVG(qty - id) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING "
                     "AND CURRENT ROW) AS b, AVG(price) OVER () AS c FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[1, 6, 3.25], [1, -1, 3.25]]  # 1.5 and -1.5 toward zero; non-integers are not cut


def test_len_weekday_week_and_plus_on_text(engine):
    r = tsql(engine, "SELECT LEN(name) AS n, DATEPART(weekday, day) AS wd, DATEPART(week, day) AS wk, "
                     "name + '!' AS s FROM dbo.orders ORDER BY id")
    # 2026-10-18 and 2026-01-04 are Sundays: weekday 1. 1 January 2026 is a Thursday, so the 4th starts week 2.
    assert r["rows"] == [[3, 1, 43, "Ann  !"], [2, 1, 2, "Bo!"]]


def test_getdate_is_utc_without_a_zone(engine):
    r = tsql(engine, "SELECT GETDATE() AS a, GETUTCDATE() AS b")
    a, b = r["rows"][0]
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    assert r["columns"][0]["type"].upper().startswith("TIMESTAMP") and "TIME ZONE" not in r["columns"][0]["type"].upper()
    for value in (a, b):
        stamp = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
        assert abs((stamp - now).total_seconds()) < 60


def test_temp_tables_and_go_batches(engine):
    engine.execute("CREATE TABLE dbo.t AS SELECT 'ordinary' AS v")
    r = tsql(engine, "SELECT 'temporary' AS v INTO #t\nGO\nSELECT v FROM #t")
    assert r["rows"] == [["temporary"]]
    assert engine.execute("SELECT v FROM dbo.t")["rows"] == [["ordinary"]]  # the ordinary table is untouched


def test_select_into_cross_apply_and_offset_fetch(engine):
    tsql(engine, "SELECT id, qty INTO dbo.copy FROM dbo.orders")
    r = tsql(engine, "SELECT o.id, x.n FROM dbo.copy o CROSS APPLY (SELECT TOP 1 c.qty AS n FROM dbo.copy c "
                     "WHERE c.id = o.id) x ORDER BY o.id OFFSET 1 ROWS FETCH NEXT 1 ROWS ONLY")
    assert r["rows"] == [[2, -7]]


@pytest.mark.parametrize("sql, says", [
    ("DECLARE @x INT = 1", "variables"),
    ("SELECT * FROM dbo.orders WHERE id = @x", "variables (@x)"),
    ("SELECT @@ROWCOUNT", "variables"),
    ("IF 1 = 1 SELECT 1", "IF"),
    ("WHILE 1 = 1 SELECT 1", "WHILE"),
    ("EXEC dbo.p", "EXEC"),
    ("PRINT 'x'", "PRINT"),
    ("BEGIN TRAN", "transactions"),
    ("SELECT id FROM dbo.orders FOR JSON PATH", "can't be run"),
    ("CREATE PROCEDURE p AS SELECT 1", "PROCEDURE"),
])
def test_what_cant_be_carried_is_refused_not_half_translated(engine, sql, says):
    with pytest.raises(ValueError) as err:
        tsql(engine, sql)
    assert says in str(err.value)


def test_session_options_are_dropped(engine):
    assert tsql(engine, "SET NOCOUNT ON; SELECT 1 AS one")["rows"] == [[1]]


def test_a_division_of_unknown_types_is_reported():
    texts, notes = translate("SELECT x / y FROM nowhere")
    assert texts == ["SELECT x / y FROM nowhere"] and "Couldn't tell the types in x / y" in notes[0]


def test_a_cast_to_an_integer_of_an_unknown_type_is_reported():
    texts, notes = translate("SELECT CAST(x AS INT), CAST(y AS BIT), CAST(z AS DECIMAL(5, 0)) FROM nowhere")
    # BIT is a boolean here (DuckDB's BIT is a bit string).
    assert texts == ["SELECT CAST(x AS INT), CAST(y AS BOOLEAN), CAST(z AS DECIMAL(5, 0)) FROM nowhere"]
    assert notes == ["Couldn't tell the type in CAST(x AS INTEGER): it converts as DuckDB does, and T-SQL cuts a "
                     "decimal toward zero and refuses text that isn't a whole number."]
    # A literal's type is known, so it gets no note.
    assert translate("SELECT CAST(3.7 AS INT)")[1] == []


def test_the_result_says_what_fabric_warehouse_would_reject(engine):
    r = tsql(engine, "SELECT TOP 1 id FROM dbo.orders")
    t = r["translation"]
    assert t["from"] == "tsql" and t["notes"] == []
    assert t["fabric_check"]["target"] == "warehouse" and t["fabric_check"]["outcome"]["status"]
    assert "translation" not in engine.execute("SELECT 1")


def test_writes_to_the_projects_delta_tables_work_in_tsql(engine):
    engine.execute("CREATE TABLE dbo.sales USING DELTA AS SELECT 1 AS id")
    r = tsql(engine, "INSERT INTO dbo.sales SELECT 7 / 2")
    assert r["statements"][0]["type"] == "write delta"
    assert engine.execute("SELECT id FROM dbo.sales ORDER BY id")["rows"] == [[1], [3]]


def test_go_splits_only_on_its_own_line():
    batches = split_batches("SELECT 'GO' AS go\ngo\nSELECT 2 -- next\n  GO  \n")
    assert [b.strip() for b in batches] == ["SELECT 'GO' AS go", "SELECT 2 -- next"]


def test_outer_apply_keeps_rows_with_no_match(engine):
    r = tsql(engine, "SELECT o.id, x.n FROM dbo.orders o OUTER APPLY (SELECT TOP 1 c.qty AS n FROM dbo.orders c "
                     "WHERE c.id = o.id AND c.qty > 0) x ORDER BY o.id")
    assert r["rows"] == [[1, 7], [2, None]]


def test_plus_with_a_number_adds_as_numbers(engine):
    r = tsql(engine, "SELECT '1' + 1 AS a, qty + '7' AS b, name + '!' AS c FROM dbo.orders WHERE id = 1")
    assert r["rows"] == [[2, 14, "Ann  !"]]
    with pytest.raises(Exception, match="Conversion failed when converting the text to data type INT"):
        tsql(engine, "SELECT 'x' + 1")  # T-SQL can't convert 'x' to a number either


def test_float_is_eight_bytes_and_real_four_as_in_sql_server(engine):
    """T-SQL FLOAT is FLOAT(53), an 8-byte float; REAL and FLOAT(1) to FLOAT(24) are 4 bytes."""
    r = tsql(engine, "SELECT CAST(0.1 AS FLOAT) AS a, CAST(0.1 AS FLOAT(53)) AS b, CAST(0.1 AS FLOAT(25)) AS c, "
                     "CONVERT(FLOAT, 0.1) AS d, CAST(0.1 AS DOUBLE PRECISION) AS e")
    assert r["rows"] == [[0.1, 0.1, 0.1, 0.1, 0.1]]
    r = tsql(engine, "SELECT CAST(0.1 AS REAL) AS a, CAST(0.1 AS FLOAT(24)) AS b, CAST(0.1 AS FLOAT(1)) AS c")
    assert all(v != 0.1 and abs(v - 0.1) < 1e-7 for v in r["rows"][0])  # 0.1 as a 4-byte float
    tsql(engine, "CREATE TABLE dbo.f (a FLOAT, b REAL, c FLOAT(10)); INSERT INTO dbo.f VALUES (0.1, 0.1, 0.1)")
    assert tsql(engine, "SELECT a FROM dbo.f")["rows"] == [[0.1]]
    for bad in ("FLOAT(1.5)", "FLOAT(0)", "FLOAT(54)"):
        with pytest.raises(ValueError, match=r"FLOAT\(n\) takes a whole number from 1 to 53"):
            translate(f"SELECT CAST(1 AS {bad})")


def test_a_cast_to_an_integer_type_cuts_toward_zero_as_in_tsql(engine):
    """CAST(3.7 AS INT) is 3 in T-SQL (DuckDB rounds it to 4), for every integer type, CONVERT and
    TRY_CAST too, from a decimal column, a literal or a float."""
    r = tsql(engine, "SELECT CAST(3.7 AS INT) AS a, CAST(-3.7 AS BIGINT) AS b, CAST(2.5 AS SMALLINT) AS c, "
                     "CAST(3.9 AS TINYINT) AS d, CONVERT(INT, -2.5) AS e, TRY_CAST(3.7 AS INT) AS f, "
                     "TRY_CONVERT(INT, 3.99) AS g, CAST(CAST(3.7 AS FLOAT) AS INT) AS h, "
                     "CAST(price AS INT) AS i, CAST(price * -1 AS INT) AS j FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[3, -3, 2, 3, -2, 3, 3, 3, 2, -2], [3, -3, 2, 3, -2, 3, 3, 3, 4, -4]]
    # Already whole, or to a type that isn't an integer: unchanged.
    r = tsql(engine, "SELECT CAST(qty AS BIGINT) AS a, CAST(3.7 AS DECIMAL(5, 0)) AS b FROM dbo.orders WHERE id = 1")
    assert r["rows"] == [[7, "4"]]  # decimals come back as text; T-SQL rounds to a DECIMAL with a smaller scale


def test_text_to_an_integer_type_takes_only_a_whole_number_as_in_tsql(engine):
    """CAST('3.7' AS INT) fails in T-SQL (DuckDB gives 4); spaces around a whole number are fine, and
    '' is 0. TRY_CAST and TRY_CONVERT give NULL where CAST fails."""
    r = tsql(engine, "SELECT CAST(' -5 ' AS INT) AS a, CAST('+7' AS BIGINT) AS b, CAST('' AS INT) AS c, "
                     "CAST(CAST(NULL AS VARCHAR) AS INT) AS d, TRY_CAST('3.7' AS INT) AS e, TRY_CONVERT(INT, '1e3') AS f")
    assert r["rows"] == [[-5, 7, 0, None, None, None]]
    for sql in ("SELECT CAST('3.7' AS INT)", "SELECT CONVERT(BIGINT, '1e3')", "SELECT CAST('5 x' AS SMALLINT)"):
        with pytest.raises(Exception, match="Conversion failed"):
            tsql(engine, sql)


def test_text_to_an_integer_reads_its_source_once():
    """A source that changes each time it's read (RAND, NEWID) is read once, not once per check."""
    text = translate("SELECT CAST(CAST(CAST(RAND() * 10 AS INT) AS VARCHAR(5)) AS INT)")[0][0]
    assert text.upper().count("RANDOM()") == 1 and "LIST_TRANSFORM" in text.upper()


def test_text_lengths_cut_and_pad_as_in_tsql(engine):
    r = tsql(engine, "SELECT CAST('abcdef' AS VARCHAR(3)) AS a, CAST('ab' AS CHAR(4)) AS b, "
                     "CAST(REPLICATE('x', 40) AS VARCHAR) AS c, CAST('abcdef' AS VARCHAR(MAX)) AS d")
    assert r["rows"] == [["abc", "ab  ", "x" * 30, "abcdef"]]
    with pytest.raises(Exception, match="doesn't fit in VARCHAR"):
        tsql(engine, "SELECT CAST(12345 AS VARCHAR(2))")


def test_convert_keeps_its_length_and_fails_where_tsql_fails(engine):
    r = tsql(engine, "SELECT CONVERT(VARCHAR(10), day, 120) AS a, CONVERT(INT, '5') AS b, "
                     "TRY_CONVERT(INT, 'x') AS c FROM dbo.orders WHERE id = 1")
    assert r["rows"] == [["2026-10-18", 5, None]]
    with pytest.raises(duckdb.InvalidInputException):
        tsql(engine, "SELECT CONVERT(INT, 'x')")  # an error in T-SQL, not NULL


def test_max_lengths_in_table_definitions(engine):
    r = tsql(engine, "CREATE TABLE #notes (body NVARCHAR(MAX)); INSERT INTO #notes VALUES ('hello'); "
                     "SELECT body FROM #notes")
    assert r["rows"] == [["hello"]]


@pytest.mark.parametrize("start, end, weeks", [
    ("2024-01-06", "2024-01-07", 1),  # Saturday to Sunday crosses one Sunday
    ("2024-01-07", "2024-01-13", 0),  # Sunday to Saturday crosses none
    ("2024-03-05", "2024-03-10", 1),  # Tuesday to Sunday
    ("2024-03-01", "2024-03-31", 5),
    ("2024-03-31", "2024-03-01", -5),
    ("2024-03-05", "2024-03-05", 0),
    ("2024-01-06 23:59:00", "2024-01-07 00:01:00", 1),  # the time of day doesn't count
    ("2023-12-30", "2024-01-01", 1),  # across a year: Sunday 31 December
])
def test_datediff_week_counts_the_sundays_crossed(engine, start, end, weeks):
    for unit in ("week", "wk", "ww"):
        assert tsql(engine, f"SELECT DATEDIFF({unit}, '{start}', '{end}')")["rows"] == [[weeks]]


def test_datediff_week_on_date_columns(engine):
    r = tsql(engine, "SELECT id, DATEDIFF(week, day, DATEADD(day, 7, day)) FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[1, 1], [2, 1]]
    # Sunday 18 October 2026 back to Sunday 4 January 2026: 41 Sundays.
    assert tsql(engine, "SELECT DATEDIFF(week, MAX(day), MIN(day)) FROM dbo.orders")["rows"] == [[-41]]


T = "CAST('2024-03-06 13:45:30.123456' AS DATETIME2)"  # a Wednesday


@pytest.mark.parametrize("sql, want", [
    (f"DATEPART(y, {T})", 66), (f"DATEPART(w, {T})", 4), (f"DATEPART(m, {T})", 3), (f"DATEPART(n, {T})", 45),
    (f"DATEPART(ms, {T})", 123), (f"DATEPART(mcs, {T})", 123456),
    (f"DATEADD(y, 1, {T})", '2024-03-07 13:45:30.123456'),
    (f"DATEADD(w, 1, {T})", '2024-03-07 13:45:30.123456'),
    (f"DATEADD(n, 1, {T})", '2024-03-06 13:46:30.123456'),
    (f"DATEADD(hh, 1, {T})", '2024-03-06 14:45:30.123456'),
    (f"DATEADD(ss, 1, {T})", '2024-03-06 13:45:31.123456'),
    (f"DATEDIFF(y, {T}, '2024-03-08')", 2), (f"DATEDIFF(w, {T}, '2024-03-08')", 2),
    (f"DATEDIFF(mi, {T}, '2024-03-06 14:00')", 15),
    (f"DATETRUNC(m, {T})", '2024-03-01 00:00:00'), (f"DATETRUNC(mm, {T})", '2024-03-01 00:00:00'),
    (f"DATETRUNC(y, {T})", '2024-03-06 00:00:00'), (f"DATETRUNC(yy, {T})", '2024-01-01 00:00:00'),
    (f"DATETRUNC(hh, {T})", '2024-03-06 13:00:00'),
    (f"DATETRUNC(week, {T})", '2024-03-03 00:00:00'),  # Sunday
    (f"DATETRUNC(iso_week, {T})", '2024-03-04 00:00:00'),  # Monday
    ("DATEDIFF(iso_week, '2024-03-10', '2024-03-11')", 1),  # Sunday to Monday
])
def test_date_parts_by_their_tsql_names(engine, sql, want):
    assert tsql(engine, f"SELECT {sql}")["rows"] == [[want]]


def test_dateadd_and_datetrunc_keep_a_date_a_date(engine):
    r = tsql(engine, "SELECT DATEADD(day, 1, day), DATETRUNC(month, day), DATETRUNC(week, day), "
                     "DATEADD(day, 1, DATEADD(month, 1, day)) FROM dbo.orders WHERE id = 1")
    assert r["rows"] == [['2026-10-19', '2026-10-01', '2026-10-18', '2026-11-19']]
    assert [c["type"] for c in r["columns"]] == ["date32[day]"] * 4


@pytest.mark.parametrize("sql, says", [
    ("SELECT DATEADD(hour, 1, day) FROM dbo.orders", "DATEADD doesn't take the date part hour for a date"),
    ("SELECT DATETRUNC(minute, day) FROM dbo.orders", "DATETRUNC doesn't take the date part minute for a date"),
    (f"SELECT DATETRUNC(dw, {T})", "DATETRUNC doesn't take the date part dw"),
    (f"SELECT DATEADD(iso_week, 1, {T})", "DATEADD doesn't take the date part iso_week"),
    (f"SELECT DATEADD(ns, 100, {T})", "kept to the microsecond"),
])
def test_date_parts_tsql_doesnt_take_are_refused(engine, sql, says):
    with pytest.raises(ValueError, match=says):
        tsql(engine, sql)


@pytest.mark.parametrize("value, pattern, matches", [
    ("b1", "[a-c]1", True), ("d1", "[a-c]1", False), ("d1", "[^a-c]1", True), ("b1", "[^a-c]1", False),
    ("50%", "50[%]", True), ("500", "50[%]", False), ("a_c", "a[_]c", True), ("abc", "a[_]c", False),
    ("x.y", "x[.]y", True), ("xzy", "x[.]y", False), ("a\\b", "a[\\]b", True), ("ab", "[ab]%", True),
    ("line\nbreak", "line[^x]%", True), ("[a]", "[[]a]", True), ("-", "[-]", True), ("a(b", "a(b%", True),
])
def test_like_takes_tsql_character_ranges(engine, value, pattern, matches):
    v, p = value.replace("'", "''"), pattern.replace("'", "''")
    assert tsql(engine, f"SELECT CASE WHEN '{v}' LIKE '{p}' THEN 1 ELSE 0 END")["rows"] == [[int(matches)]]
    assert tsql(engine, f"SELECT CASE WHEN '{v}' NOT LIKE '{p}' THEN 1 ELSE 0 END")["rows"] == [[int(not matches)]]


def test_like_with_ranges_keeps_its_escape_character(engine):
    sql = "SELECT CASE WHEN 'a[b' LIKE 'a![[b]' ESCAPE '!' THEN 1 ELSE 0 END"
    assert tsql(engine, sql)["rows"] == [[1]]


def test_a_like_pattern_that_isnt_text_is_reported():
    texts, notes = translate("SELECT 1 FROM dbo.t WHERE a LIKE b")
    assert texts == ["SELECT 1 FROM dbo.t WHERE a LIKE b"]
    assert notes == ["A LIKE pattern that isn't written in the query (such as LIKE other_column) matches as DuckDB's "
                     "LIKE does, which takes no [a-c] character ranges."]


def test_several_like_patterns_that_arent_text_make_one_note():
    assert len(translate("SELECT 1 FROM dbo.t WHERE a LIKE b OR c LIKE d OR e NOT LIKE f")[1]) == 1


@pytest.mark.parametrize("value, pattern, matches", [
    ("b", "[z-a]", False), ("b", "[^z-a]", True), ("y", "[a-cz-x]", False), ("b", "[a-cz-x]", True),
    ("]", "[!]]", True), ("x", "[!]x]", True), ("a", "[!]x]", False), ("-", "[a!-c]", True), ("b", "[a!-c]", False),
])
def test_like_ranges_backwards_and_with_the_escape_character(engine, value, pattern, matches):
    sql = f"SELECT CASE WHEN '{value}' LIKE '{pattern}' ESCAPE '!' THEN 1 ELSE 0 END"
    assert tsql(engine, sql)["rows"] == [[int(matches)]]


def test_set_datefirst_7_is_skipped_and_others_refused(engine):
    assert tsql(engine, "SET DATEFIRST 7\nGO\nSELECT DATEPART(weekday, CAST('2024-03-03' AS DATE))")["rows"] == [[1]]
    with pytest.raises(ValueError, match="doesn't run other DATEFIRST settings"):
        tsql(engine, "SET DATEFIRST 1\nGO\nSELECT 1")


def test_a_like_without_ranges_stays_a_like():
    assert translate("SELECT 1 FROM dbo.t WHERE a LIKE 'a_%'")[0] == ["SELECT 1 FROM dbo.t WHERE a LIKE 'a_%'"]


def test_patindex_finds_where_a_like_pattern_first_matches(engine):
    engine.execute("CREATE TABLE dbo.p AS SELECT * FROM (VALUES (1, 'abcabc'), (2, 'xyz'), (3, ''), (4, NULL), "
                   "(5, 'a1b2')) v(id, s)")
    r = tsql(engine, "SELECT PATINDEX('%b%', s) AS a, PATINDEX('%bc', s) AS b, PATINDEX('ab%', s) AS c, "
                     "PATINDEX('abcabc', s) AS d, PATINDEX('%[0-9]%', s) AS e, PATINDEX('%b_b%', s) AS f, "
                     "PATINDEX('%c%c', s) AS g FROM dbo.p ORDER BY id")
    assert r["rows"] == [[2, 5, 1, 1, 0, 0, 3], [0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0],
                         [None] * 7, [3, 0, 0, 0, 2, 0, 0]]
    # The examples on Microsoft's PATINDEX page.
    r = tsql(engine, "SELECT PATINDEX('%ter%', 'interesting data') AS a, "
                     "PATINDEX('%en_ure%', 'Please ensure the door is locked') AS b, "
                     "PATINDEX('%[^ 0-9A-Za-z]%', 'Please ensure the door is locked!') AS c")
    assert r["rows"] == [[3, 8, 33]]
    with pytest.raises(ValueError, match="PATINDEX needs its pattern written out as text"):
        tsql(engine, "SELECT PATINDEX(s, 'abc') AS x FROM dbo.p")
    # A NULL pattern gives NULL, as in T-SQL, however it is written.
    r = tsql(engine, "SELECT PATINDEX(NULL, 'abc') AS a, PATINDEX(CAST(NULL AS VARCHAR(10)), s) AS b, "
                     "PATINDEX((NULL), s) AS c FROM dbo.p WHERE id = 1")
    assert r["rows"] == [[None, None, None]]


def test_var_varp_stdev_and_stdevp_are_the_sample_s_and_the_population_s(engine):
    import math
    r = tsql(engine, "SELECT VAR(price) AS v, VARP(price) AS vp, STDEV(price) AS s, STDEVP(price) AS sp, "
                     "VAR(DISTINCT boxes) AS vd FROM dbo.orders")
    v, vp, s, sp, vd = r["rows"][0]
    assert (v, vp, vd) == (1.125, 0.5625, None)  # 2.5 and 4.0; boxes has one distinct value
    assert math.isclose(s, math.sqrt(1.125)) and math.isclose(sp, 0.75)
    one = tsql(engine, "SELECT VAR(qty) AS v, VARP(qty) AS vp, STDEV(qty) AS s, STDEVP(qty) AS sp FROM dbo.orders "
                       "WHERE id = 1")
    assert one["rows"] == [[None, 0.0, None, 0.0]]  # one row: no sample spread, a population spread of 0
    w = tsql(engine, "SELECT id, VARP(qty) OVER () AS vp FROM dbo.orders ORDER BY id")
    assert w["rows"] == [[1, 49.0], [2, 49.0]]


def test_substring_stuff_and_replicate_at_the_edges(engine):
    # Each as in T-SQL, not DuckDB: a negative start isn't counted from the end, and so on.
    r = tsql(engine, "SELECT SUBSTRING('abcdef', -1, 3) AS a, SUBSTRING('abcdef', -5, 3) AS b, "
                     "SUBSTRING(name, id - 1, 2) AS c, SUBSTRING('abc', 2, 5) AS d FROM dbo.orders ORDER BY id")
    assert r["rows"] == [["a", "", "A", "bc"], ["a", "", "Bo", "bc"]]
    r = tsql(engine, "SELECT STUFF('abc', 5, 1, 'x') AS a, STUFF('abc', 0, 1, 'x') AS b, STUFF('abc', 2, 1, 'x') AS c, "
                     "STUFF('abc', 2, -1, 'x') AS d, STUFF('abc', 2, 1, NULL) AS e, STUFF('abc', 2, 9, 'x') AS f")
    assert r["rows"] == [[None, None, "axc", None, "ac", "ax"]]
    r = tsql(engine, "SELECT REPLICATE('ab', -1) AS a, REPLICATE('ab', 2) AS b, REPLICATE('ab', qty) AS c "
                     "FROM dbo.orders ORDER BY id")
    assert r["rows"] == [[None, "abab", "ab" * 7], [None, "abab", None]]
    r = tsql(engine, "SELECT SUBSTRING('abc', CAST(NULL AS INT), 2) AS a, SUBSTRING('abc', 0, CAST(NULL AS INT)) AS b, "
                     "SUBSTRING('abc', 1, CAST(NULL AS INT)) AS c, SUBSTRING(CAST(NULL AS VARCHAR(3)), 0, 2) AS d")
    assert r["rows"] == [[None, None, None, None]]
    with pytest.raises(Exception, match="Invalid length parameter passed to the SUBSTRING function"):
        tsql(engine, "SELECT SUBSTRING(name, 1, qty) AS s FROM dbo.orders")


def test_left_right_and_translate_fail_where_tsql_fails(engine):
    # DuckDB drops from the other end for a negative LEFT / RIGHT and drops unpartnered TRANSLATE characters.
    r = tsql(engine, "SELECT LEFT(name, id) AS l, RIGHT(name, id) AS r, LEFT('abc', 0) AS z, "
                     "TRANSLATE(name, 'nB', 'Nb') AS t FROM dbo.orders ORDER BY id")
    assert r["rows"] == [["A", " ", "", "ANN  "], ["Bo", "Bo", "", "bo"]]
    # T-SQL makes the length an integer toward zero first: -0.5 is 0, and 2.7 is 2 (DuckDB rounds).
    r = tsql(engine, "SELECT LEFT('abc', -0.5) AS a, RIGHT('abc', 2.7) AS b, LEFT(name, price) AS c FROM dbo.orders "
                     "ORDER BY id")
    assert r["rows"] == [["", "bc", "An"], ["", "bc", "Bo"]]
    r = tsql(engine, "SELECT LEFT('abcdef', 2.0) AS a, LEFT(name, 1e1) AS b, RIGHT('abcdef', 3.00) AS c FROM dbo.orders "
                     "ORDER BY id")
    assert r["rows"] == [["ab", "Ann  ", "def"], ["ab", "Bo", "def"]]
    for sql in ("SELECT LEFT('abc', -1)", "SELECT RIGHT(name, -2) FROM dbo.orders"):
        with pytest.raises(ValueError, match="Invalid length parameter passed to the (LEFT|RIGHT) function"):
            tsql(engine, sql)
    with pytest.raises(Exception, match="Invalid length parameter passed to the LEFT function"):
        tsql(engine, "SELECT LEFT(name, qty) AS l FROM dbo.orders")  # -7 on the second row
    with pytest.raises(ValueError, match="must contain an equal number of characters"):
        tsql(engine, "SELECT TRANSLATE('abc', 'ab', 'x')")
    with pytest.raises(Exception, match="must contain an equal number of characters"):
        tsql(engine, "SELECT TRANSLATE(name, name, 'x') AS t FROM dbo.orders")

def test_a_float_as_text_has_six_significant_digits(engine):
    # T-SQL's style 0 for float and real: %g, with a three-digit exponent. DuckDB writes every digit.
    engine.execute("CREATE TABLE dbo.f AS SELECT * FROM (VALUES (1, CAST(1 AS DOUBLE) / 3, CAST(0.5 AS REAL)), "
                   "(2, 1234567.0, 0.00001), (3, 100.0, -2.5), (4, NULL, NULL)) v(id, d, r)")
    r = tsql(engine, "SELECT CAST(d AS VARCHAR) AS a, CONVERT(VARCHAR(20), d) AS b, CAST(r AS NVARCHAR(10)) AS c, "
                     "TRY_CAST(d AS VARCHAR(30)) AS t FROM dbo.f ORDER BY id")
    assert r["rows"] == [["0.333333", "0.333333", "0.5", "0.333333"],
                         ["1.23457e+006", "1.23457e+006", "1e-005", "1.23457e+006"],
                         ["100", "100", "-2.5", "100"], [None, None, None, None]]
    assert tsql(engine, "SELECT CAST(1e10 AS VARCHAR) AS a, CAST(price AS VARCHAR) AS p FROM dbo.orders ORDER BY id")[
        "rows"] == [["1e+010", "2.5"], ["1e+010", "4.0"]]  # price is a decimal: unchanged
    with pytest.raises(Exception, match="doesn't fit in VARCHAR"):
        tsql(engine, "SELECT CAST(d AS VARCHAR(5)) AS s FROM dbo.f")  # '0.333333' is 8 characters


def test_convert_styles_on_numbers(engine):
    # sqlglot read every CONVERT style as a date style, so a number went to STRFTIME and failed.
    engine.execute("CREATE TABLE dbo.f AS SELECT CAST(0.5 AS DOUBLE) AS d")
    r = tsql(engine, "SELECT CONVERT(VARCHAR(10), qty, 1) AS q, CONVERT(VARCHAR(10), price, 2) AS p, "
                     "CONVERT(VARCHAR(10), day, 1) AS d FROM dbo.orders ORDER BY id")
    assert r["rows"] == [["7", "2.5", "10/18/26"], ["-7", "4.0", "01/04/26"]]  # integers and decimals ignore it
    assert tsql(engine, "SELECT CONVERT(VARCHAR(10), d, 0) AS d FROM dbo.f")["rows"] == [["0.5"]]
    for style in (1, 2, 3):
        with pytest.raises(ValueError, match=f"CONVERT style {style} for a float isn't carried yet"):
            tsql(engine, f"SELECT CONVERT(VARCHAR(30), d, {style}) AS d FROM dbo.f")


def test_concat_writes_a_float_in_style_0(engine):
    # CONCAT converts a float as CAST to text does in T-SQL; DuckDB wrote '1234567.0' and '0.3333333333333333'.
    engine.execute("CREATE TABLE dbo.f AS SELECT * FROM (VALUES (1, CAST(1234567 AS DOUBLE), CAST(0.5 AS REAL), 'a'), "
                   "(2, NULL, NULL, 'b')) v(id, d, r, s)")
    r = tsql(engine, "SELECT CONCAT(d, 'x') AS a, CONCAT_WS('-', d, r, s) AS b, CONCAT(1e0 / 3, '') AS c "
                     "FROM dbo.f ORDER BY id")
    assert r["rows"] == [["1.23457e+006x", "1.23457e+006-0.5-a", "0.333333"], ["x", "b", "0.333333"]]
    # Integers and decimals are written as before.
    assert tsql(engine, "SELECT CONCAT(qty, '/', price) AS a FROM dbo.orders ORDER BY id")["rows"] == [["7/2.5"],
                                                                                                    ["-7/4.0"]]


def test_concat_of_a_column_whose_type_cant_be_told_says_so():
    texts, notes = translate("SELECT CONCAT(x, 'a') FROM dbo.nowhere", {"dbo": {"t": {"d": "DOUBLE"}}})
    assert texts == ["SELECT CONCAT(x, 'a') FROM dbo.nowhere"]
    assert any("Couldn't tell the type of x in CONCAT(x, 'a')" in n and "6 significant digits" in n for n in notes)


def test_text_added_to_a_number_follows_tsql(engine):
    # The text becomes the number's type. '' is 0 for an integer or a float, where DuckDB refused it, and text
    # for an integer has to be a whole number: DuckDB rounded '1.5' to 2, T-SQL stops.
    engine.execute("CREATE TABLE dbo.f AS SELECT * FROM (VALUES (1, CAST(1.5 AS DOUBLE), ''), (2, NULL, ' 2 ')) v(id, d, s)")
    assert tsql(engine, "SELECT d + '' AS a, '' + 7 AS b, s + 1 AS c, s + d AS e FROM dbo.f ORDER BY id")["rows"] == [
        [1.5, 7, 1, 1.5], [None, 7, 3, None]]
    with pytest.raises(Exception, match="Conversion failed when converting the text to data type INT"):
        tsql(engine, "SELECT '1.5' + 1 AS a")


@pytest.mark.parametrize("sql", ["SELECT CONVERT(VARCHAR(20), CAST(1234.5 AS MONEY), 1) AS x",
                                 "SELECT CAST(qty AS SMALLMONEY) AS x FROM dbo.orders"])
def test_money_is_refused_by_name(engine, sql):
    with pytest.raises(ValueError, match="on the local engine: MONEY and SMALLMONEY aren't carried yet"):
        tsql(engine, sql)


def test_format_of_a_date_follows_dotnets_formats(engine):
    # Expected text: .NET's en-US formats, as FORMAT uses them (UNVERIFIED on a Fabric Warehouse).
    at = "CAST('2024-03-05 14:07:09.123456' AS DATETIME2)"
    cases = {
        "yyyy-MM-dd": "2024-03-05", "d": "3/5/2024", "D": "Tuesday, March 5, 2024",
        "f": "Tuesday, March 5, 2024 2:07 PM", "F": "Tuesday, March 5, 2024 2:07:09 PM", "g": "3/5/2024 2:07 PM",
        "G": "3/5/2024 2:07:09 PM", "M": "March 5", "O": "2024-03-05T14:07:09.1234560",
        "R": "Tue, 05 Mar 2024 14:07:09 GMT", "s": "2024-03-05T14:07:09", "t": "2:07 PM", "T": "2:07:09 PM",
        "u": "2024-03-05 14:07:09Z", "Y": "March 2024", "ddd dd MMM": "Tue 05 Mar", "dddd MMMM": "Tuesday March",
        "hh:mm tt": "02:07 PM", "h t": "2 P", "HH:mm:ss.fff": "14:07:09.123", "%d": "5", "%M": "3",
        "y yy yyy yyyyy": "24 24 2024 02024", "''Q''q \\d": "Qq d", '"yyyy" yyyy': "yyyy 2024",
        "dd/MM/yyyy": "05/03/2024",
    }
    sql = ", ".join(f"FORMAT({at}, '{f}') AS c{i}" for i, f in enumerate(cases))
    assert tsql(engine, f"SELECT {sql}")["rows"][0] == list(cases.values())
    r = tsql(engine, "SELECT FORMAT(day, 'dd MMM yyyy', 'en-US') AS d, FORMAT(CAST(NULL AS DATE), 'yyyy') AS n "
                     "FROM dbo.orders ORDER BY id")
    assert r["rows"] == [["18 Oct 2026", None], ["04 Jan 2026", None]]
    assert tsql(engine, "SELECT FORMAT(CAST('0999-01-02' AS DATE), 'yyyy') AS y")["rows"] == [["0999"]]


def test_format_of_a_number_rounds_half_away_from_zero(engine):
    r = tsql(engine, "SELECT FORMAT(1234.5, 'N2') AS a, FORMAT(-1234.5, 'N0') AS b, FORMAT(-0.004, 'N2') AS c, "
                     "FORMAT(-0.005, 'N2') AS d, FORMAT(1234567, 'N') AS e, FORMAT(2.5, 'F0') AS f, "
                     "FORMAT(-1234.5, 'f3') AS g, FORMAT(-42, 'D5') AS h, FORMAT(123456, 'D3') AS i, FORMAT(42, 'd') AS j")
    assert r["rows"] == [["1,234.50", "-1,235", "0.00", "-0.01", "1,234,567.00", "3", "-1234.500", "-00042",
                          "123456", "42"]]
    r = tsql(engine, "SELECT FORMAT(price, 'N1') AS p, FORMAT(qty, 'N0') AS q, FORMAT(CAST(NULL AS INT), 'N') AS n "
                     "FROM dbo.orders ORDER BY id")
    assert r["rows"] == [["2.5", "7", None], ["4.0", "-7", None]]


@pytest.mark.parametrize("sql, says", [
    ("SELECT FORMAT(1e0, 'N2')", "FORMAT of a FLOAT value"),
    ("SELECT FORMAT(CAST(1 AS MONEY), 'N2')", "MONEY and SMALLMONEY aren't carried yet"),
    ("SELECT FORMAT(CAST('10:00' AS TIME), 'hh')", "FORMAT of a TIME value"),
    ("SELECT FORMAT('2024-01-01', 'yyyy')", "FORMAT of text"),
    ("SELECT FORMAT(5, '0000')", "number format '0000'"),
    ("SELECT FORMAT(5, 'C')", "number format 'C'"),
    ("SELECT FORMAT(1.5, 'D')", "'D' format is for integers"),
    ("SELECT FORMAT(day, 'D', 'de-DE') FROM dbo.orders", "'en-US' culture"),
    ("SELECT FORMAT(day, name) FROM dbo.orders", "text written in the query"),
    ("SELECT FORMAT(day, 'U') FROM dbo.orders", "date format 'U'"),
    ("SELECT FORMAT(day, 'yyyy K') FROM dbo.orders", "K (the time zone)"),
    ("SELECT FORMAT(day, 'ss.FFF') FROM dbo.orders", "F (fractions"),
    ("SELECT FORMAT(day, 'ffffffff') FROM dbo.orders", "at most 7 f's"),
    ("SELECT FORMAT(day, 'yyyy''') FROM dbo.orders", "isn't closed"),
    ("SELECT FORMAT(5)", "takes a value, a format"),
])
def test_format_that_cant_be_carried_is_refused(engine, sql, says):
    with pytest.raises(ValueError, match=f"on the local engine: .*{re.escape(says)}"):
        tsql(engine, sql)


def test_format_of_a_value_whose_type_cant_be_told_is_refused():
    with pytest.raises(ValueError, match="FORMAT needs to know its value's type"):
        translate("SELECT FORMAT(x, 'yyyy') FROM dbo.nowhere")


@pytest.mark.parametrize("sql, want", [
    ("SELECT CASE WHEN 'a' = 'a  ' THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN 'a' <> 'a  ' THEN 1 ELSE 0 END", 0),
    ("SELECT CASE WHEN 'a ' < 'a' THEN 1 ELSE 0 END", 0),
    ("SELECT CASE WHEN 'a' IN ('b', 'a  ') THEN 1 ELSE 0 END", 1),
    ("SELECT CASE WHEN ' a' = 'a' THEN 1 ELSE 0 END", 0),  # only trailing spaces
    ("SELECT CASE WHEN 'a' = 'A' THEN 1 ELSE 0 END", 0),  # still case-sensitive
])
def test_text_compares_without_trailing_spaces(engine, sql, want):
    assert tsql(engine, sql)["rows"] == [[want]]


def test_a_text_column_compares_without_trailing_spaces(engine):
    # 'Ann  ' is stored with two trailing spaces.
    assert tsql(engine, "SELECT id FROM dbo.orders WHERE name = 'Ann'")["rows"] == [[1]]
    assert tsql(engine, "SELECT id FROM dbo.orders WHERE 'Ann ' = name")["rows"] == [[1]]
    assert tsql(engine, "SELECT id FROM dbo.orders WHERE name <> 'Ann' ORDER BY id")["rows"] == [[2]]
    assert tsql(engine, "SELECT id FROM dbo.orders WHERE name IN ('Ann', 'Cy') ORDER BY id")["rows"] == [[1]]
    assert tsql(engine, "SELECT COUNT(*) FROM dbo.orders a JOIN dbo.orders b ON a.name = RTRIM(b.name)")["rows"] == [[2]]


def test_an_equality_on_a_text_column_keeps_a_range_for_skipping_row_groups():
    texts, _ = translate("SELECT id FROM dbo.orders WHERE name = 'Ann '", {"dbo": {"orders": {"id": "INTEGER", "name": "VARCHAR"}}})
    assert texts == ["SELECT id FROM dbo.orders WHERE (name >= 'Ann' AND name <= 'Ann \U0010ffff' AND RTRIM(name) = 'Ann')"]


def test_numbers_compare_as_before_and_unknown_text_comparisons_are_noted():
    texts, notes = translate("SELECT 1 FROM q WHERE n = 3 AND m < 2.5")
    assert texts == ["SELECT 1 FROM q WHERE n = 3 AND m < 2.5"] and notes == []
    texts, notes = translate("SELECT 1 FROM q WHERE a = 'x' OR b = 'y'")
    assert texts == ["SELECT 1 FROM q WHERE a = 'x' OR b = 'y'"]
    assert notes == ["Couldn't tell the types in some comparisons, so they are done as DuckDB does them: T-SQL "
                     "ignores trailing spaces when it compares text ('a' = 'a  '), and here they count."]
