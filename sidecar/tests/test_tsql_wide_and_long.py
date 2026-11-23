"""WR-235: very wide and very long values in local T-SQL stay exact, or stop by name where a Fabric Warehouse differs.

From Microsoft Learn (2026-11-18): a warehouse table has at most 1,024 columns (Tables in Fabric Data Warehouse),
varchar(max) and varbinary(max) hold up to 16 MB (Data types), and REPLICATE of an input that isn't varchar(max)
or nvarchar(max) "truncates the return value at 8,000 bytes" (REPLICATE). No value here is cut silently.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData

MB = 1 << 20


@pytest.fixture
def engine(tmp_path):
    data = LocalData(tmp_path)
    e = data.engine(data.create("Wide")["id"])
    yield e
    data.close_all()


def rows(engine, sql):
    return engine.execute(sql, dialect="tsql")["rows"]


def error(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect="tsql")
    return str(caught.value)


def columns(n, kind="INT"):
    return ", ".join(f"c{i} {kind}" for i in range(n))


def values(n):
    return ", ".join(f"{i} AS c{i}" for i in range(n))


def tables(engine):
    return {r[0] for r in rows(engine, "SELECT table_name FROM INFORMATION_SCHEMA.TABLES")}


# ---- a table's 1,024 columns

def test_a_table_of_1024_columns_is_made_and_keeps_every_value(engine):
    engine.execute(f"CREATE TABLE dbo.w ({columns(1024)})", dialect="tsql")
    engine.execute(f"INSERT INTO dbo.w SELECT {values(1024)}", dialect="tsql")
    result = engine.execute("SELECT * FROM dbo.w", dialect="tsql")
    assert len(result["columns"]) == 1024 and result["rows"] == [list(range(1024))]


@pytest.mark.parametrize("sql, table", [
    (f"CREATE TABLE dbo.w ({columns(1025)})", "w"),
    (f"CREATE TABLE #w ({columns(1025)})", "#w"),
    (f"SELECT {values(1025)} INTO dbo.w", "w"),
    (f"CREATE TABLE dbo.w AS SELECT {values(1025)}", "w"),
])
def test_a_table_of_more_than_1024_columns_stops_with_sql_servers_words_and_isnt_made(engine, sql, table):
    assert error(engine, sql) == (f"CREATE TABLE failed because column 'c1024' in table '{table}' exceeds the "
                                  "maximum of 1024 columns.")
    assert "w" not in tables(engine)


def test_select_star_into_a_table_counts_the_columns_it_reads(engine):
    engine.execute(f"CREATE TABLE dbo.half AS SELECT {values(600)}", dialect="tsql")
    text = error(engine, "SELECT a.*, b.* INTO dbo.both FROM dbo.half a CROSS JOIN dbo.half b")
    assert text.startswith("CREATE TABLE failed because column ") and "'both'" in text, text
    assert "both" not in tables(engine)
    engine.execute("SELECT *, 1 AS one INTO dbo.more FROM dbo.half", dialect="tsql")  # 601: made
    assert len(engine.execute("SELECT * FROM dbo.more", dialect="tsql")["columns"]) == 601


def test_alter_table_add_past_1024_columns_stops_and_adds_nothing(engine):
    engine.execute(f"CREATE TABLE dbo.w ({columns(1020)})", dialect="tsql")
    for i in range(4):  # to 1,024: added
        engine.execute(f"ALTER TABLE dbo.w ADD n{i} INT", dialect="tsql")
    text = error(engine, "ALTER TABLE dbo.w ADD n4 INT")
    assert text == "ALTER TABLE failed because column 'n4' in table 'w' exceeds the maximum of 1024 columns."
    assert len(engine.execute("SELECT * FROM dbo.w", dialect="tsql")["columns"]) == 1024


def test_alter_table_add_of_several_columns_is_refused_by_name_and_adds_nothing(engine):
    engine.execute("CREATE TABLE dbo.t (a INT)", dialect="tsql")
    text = error(engine, "ALTER TABLE dbo.t ADD b INT, c INT")
    assert "ADD of more than one column in one statement isn't mapped yet" in text and "Parser Error" not in text
    assert len(engine.execute("SELECT * FROM dbo.t", dialect="tsql")["columns"]) == 1


def test_a_result_wider_than_a_table_isnt_a_table_and_runs(engine):
    assert len(engine.execute(f"SELECT {values(1500)}", dialect="tsql")["columns"]) == 1500


def test_the_duckdb_dialect_has_no_column_limit(engine):
    engine.execute(f"CREATE TABLE w AS SELECT {values(1100)}", dialect="duckdb")
    assert "w" in tables(engine)


# ---- long values

def test_a_value_of_a_megabyte_comes_back_whole(engine):
    engine.execute("CREATE TABLE dbo.l (v VARCHAR(MAX))", dialect="tsql")
    engine.execute(f"INSERT INTO dbo.l SELECT REPLICATE(CAST('ab' AS VARCHAR(MAX)), {MB // 2})", dialect="tsql")
    [[value]] = rows(engine, "SELECT v FROM dbo.l")
    assert len(value) == MB and value == "ab" * (MB // 2)
    assert rows(engine, "SELECT LEN(v), DATALENGTH(v) FROM dbo.l") == [[MB, MB]]


def test_a_megabyte_of_text_outside_ascii_keeps_every_character(engine):
    [[value]] = rows(engine, f"SELECT REPLICATE(CAST(N'é😀' AS NVARCHAR(MAX)), {MB // 4})")
    assert value == "é😀" * (MB // 4)


@pytest.mark.parametrize("sql, n", [
    ("SELECT LEN(REPLICATE('a', 8000))", 8000),
    ("SELECT LEN(REPLICATE('é', 4000))", 4000),  # 8,000 bytes in UTF-8
    ("SELECT LEN(REPLICATE(N'a', 4000))", 4000),
    ("SELECT LEN(REPLICATE(CAST('a' AS VARCHAR(MAX)), 20000))", 20000),
    ("SELECT LEN(REPLICATE(CONVERT(NVARCHAR(MAX), 'a'), 20000))", 20000),
])
def test_replicate_up_to_its_limit_or_of_a_max_input_is_whole(engine, sql, n):
    assert rows(engine, sql) == [[n]]


@pytest.mark.parametrize("sql, most", [
    ("SELECT REPLICATE('a', 8001)", "8,000 bytes"),
    ("SELECT REPLICATE('é', 4001)", "8,000 bytes"),
    ("SELECT REPLICATE(CAST('a' AS VARCHAR(10)), 9000)", "8,000 bytes"),
    ("SELECT REPLICATE(N'a', 4001)", "4,000 characters"),
    ("SELECT REPLICATE(CAST('a' AS NVARCHAR(10)), 4001)", "4,000 characters"),
])
def test_replicate_past_where_a_warehouse_cuts_it_stops_by_name(engine, sql, most):
    text = error(engine, sql)
    assert f"REPLICATE gives more than {most} here, and a Fabric Warehouse cuts the result there" in text, text
    assert "(MAX)" in text


def test_replicate_of_a_column_is_checked_on_each_row(engine):
    engine.execute("CREATE TABLE dbo.t (s VARCHAR(100))", dialect="tsql")
    engine.execute("INSERT INTO dbo.t VALUES ('a'), ('abc')", dialect="tsql")
    assert rows(engine, "SELECT LEN(REPLICATE(s, 2000)) FROM dbo.t WHERE s = 'a'") == [[2000]]
    assert "8,000 bytes" in error(engine, "SELECT LEN(REPLICATE(s, 3000)) FROM dbo.t")


@pytest.mark.parametrize("sql", ["SELECT REPLICATE(NULL, 3)", "SELECT REPLICATE('x', NULL)",
                                 "SELECT REPLICATE('ab', -1)"])
def test_replicate_keeps_its_null_answers(engine, sql):
    assert rows(engine, sql) == [[None]]


@pytest.mark.parametrize("sql, out", [
    ("SELECT LEN(REPLICATE('ab', 4000.7))", [[8000]]),  # the count is cut toward zero first: 8,000 bytes, whole
    ("SELECT LEN(REPLICATE('ab', 4000.7 - 8001))", [[None]]),  # a negative count is NULL, before any limit
    ("SELECT DATALENGTH(SPACE(2.9)), SPACE(-1)", [[2, None]]),  # LEN would drop trailing spaces
])
def test_replicates_limit_reads_the_count_as_t_sql_takes_it(engine, sql, out):
    assert rows(engine, sql) == out


def test_replicate_one_past_the_limit_after_the_count_is_cut_stops(engine):
    assert "8,000 bytes" in error(engine, "SELECT REPLICATE('ab', 4001)")
    assert "8,000 bytes" in error(engine, "SELECT REPLICATE('ab', 4001.2)")


@pytest.mark.parametrize("sql, out", [
    ("SELECT LEN(REPLICATE(REPLICATE('ab', 100), 40))", [[8000]]),
    ("SELECT REPLICATE(REPLICATE('ab', -1), 3)", [[None]]),  # a negative inner count is NULL inside too
    ("SELECT REPLICATE(REPLICATE('ab', -5000), -1)", [[None]]),  # two negative counts don't make a size
    ("SELECT LEN(REPLICATE(REPLICATE('ab', 2.9), 1.9))", [[4]]),  # each count cut toward zero
    ("SELECT DATALENGTH(REPLICATE(SPACE(2.9), 3))", [[6]]),
    ("SELECT REPLICATE(SPACE(-1), 3)", [[None]]),
    ("SELECT LEN(REPLICATE(REPLICATE(CAST('a' AS VARCHAR(MAX)), 5000), 2))", [[10000]]),  # (MAX) goes outward
])
def test_a_replicate_inside_another_is_rewritten_too(engine, sql, out):
    assert rows(engine, sql) == out


@pytest.mark.parametrize("sql, most", [
    ("SELECT REPLICATE(REPLICATE('ab', 4001), 1)", "8,000 bytes"),  # the inner one is past it
    ("SELECT REPLICATE(REPLICATE('ab', 100), 41)", "8,000 bytes"),  # the outer one is past it
    ("SELECT REPLICATE(REPLICATE(N'a', 100), 41)", "4,000 characters"),  # N'' text inside stays N'' text
])
def test_a_replicate_inside_another_is_checked_at_both(engine, sql, most):
    assert f"REPLICATE gives more than {most}" in error(engine, sql)


@pytest.mark.parametrize("call", [
    "SPACE(LEN(REPLICATE({}, n)))", "REPLICATE({}, n)", "STUFF({}, a, b, d)", "SPACE(LEN({}))",
    "SUBSTRING({}, a, b)", "CHARINDEX(x, c, LEN({}))", "LEFT({}, n)", "TRANSLATE({}, x, y)",
])
def test_nested_string_calls_grow_the_sql_in_step_not_several_times_a_level(call):
    from fabric_desktop.localdata import tsql

    def size(depth):
        inner = "c"
        for _ in range(depth):
            inner = call.format(inner)
        return len(tsql.translate(f"SELECT {inner} AS x FROM t")[0][0])
    four, eight = size(4), size(8)
    assert eight < 3 * four, (four, eight)  # linear: twice the depth, about twice the SQL


def test_a_query_nested_too_deeply_to_translate_stops_by_name():
    from fabric_desktop.localdata import tsql
    with pytest.raises(ValueError, match="nests calls or brackets too deeply"):
        tsql.translate("SELECT " + "SPACE(LEN(REPLICATE(" * 40 + "c" + ", n)))" * 40 + " FROM t")


@pytest.mark.parametrize("sql, out", [
    ("SELECT STUFF((SELECT MAX(s) FROM dbo.t), 2, 1, 'X')", [["aXc"]]),  # a subquery is bound, not in the lambda
    ("SELECT SUBSTRING(s, (SELECT MIN(n) FROM dbo.t), LEN(s) - 1) FROM dbo.t", [["bc"]]),
    ("SELECT SUBSTRING(SUBSTRING(s, 0, 3), 0, 2) FROM dbo.t", [["a"]]),
    ("SELECT CHARINDEX('c', s, CHARINDEX('b', s, 0)) FROM dbo.t", [[3]]),
    ("SELECT REPLICATE(MAX(s), COUNT(*) + 1) FROM dbo.t", [["abcabc"]]),
    ("SELECT LEFT(REPLICATE(s, n), n + 1) FROM dbo.t", [["abc"]]),
    ("SELECT STUFF(STUFF(s, 1, 1, 'Q'), 9, 1, 'Z') FROM dbo.t", [[None]]),
])
def test_a_bound_argument_gives_the_same_answer(engine, sql, out):
    engine.execute("CREATE TABLE dbo.t (s VARCHAR(10), n INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.t VALUES ('abc', 2)", dialect="tsql")
    assert rows(engine, sql) == out


def test_a_column_named_like_the_bound_arguments_keeps_its_own_value(engine):
    engine.execute("CREATE TABLE dbo.k (kf_args INT, s VARCHAR(10))", dialect="tsql")
    engine.execute("INSERT INTO dbo.k VALUES (2, 'abc')", dialect="tsql")
    assert rows(engine, "SELECT SUBSTRING((SELECT MAX(s) FROM dbo.k), kf_args, kf_args) FROM dbo.k") == [["bc"]]


def test_a_large_legitimate_batch_isnt_taken_for_one_nested_too_deeply():
    from fabric_desktop.localdata import tsql
    rows = ", ".join(f"({i}, '{'v' * 1000}', REPLICATE('x', 3), SUBSTRING(s, 0, 2))" for i in range(1000))
    sql = f"INSERT INTO dbo.t (id, s, r, f) SELECT * FROM (VALUES {rows}) v(id, s, r, f)"
    assert len(sql) > 1_000_000
    [text], _notes = tsql.translate(sql)
    assert len(text) < 2 * len(sql)  # far below the backstop, which only a nesting can reach


def test_a_literal_replicate_within_its_limit_needs_no_check_at_run_time():
    from fabric_desktop.localdata import tsql
    assert tsql.translate("SELECT REPLICATE('x', 3), REPLICATE(N'é', 4000)")[0] == \
        ["SELECT REPEAT('x', 3), REPEAT('é', 4000)"]


def test_a_count_from_rand_is_read_once():
    from fabric_desktop.localdata import tsql
    for sql in ("SELECT REPLICATE('ab', RAND() * 6 - 3)", "SELECT SPACE(RAND() * 6 - 3)"):
        assert tsql.translate(sql)[0][0].count("RANDOM()") == 1, sql


@pytest.mark.parametrize("sql, out", [
    ("SELECT REPLICATE('ab', '3')", [["ababab"]]),  # #3007: text is a whole number
    ("SELECT DATALENGTH(SPACE(' 2 '))", [[2]]),
    ("SELECT REPLICATE('ab', s) FROM (VALUES ('2')) v(s)", [["abab"]]),
    ("SELECT LEN(REPLICATE('ab', s)) FROM (VALUES ('4000')) v(s)", [[8000]]),
])
def test_a_text_count_is_a_whole_number_and_still_checked(engine, sql, out):
    assert rows(engine, sql) == out


def test_a_text_count_past_the_limit_stops(engine):
    assert "8,000 bytes" in error(engine, "SELECT REPLICATE('ab', s) FROM (VALUES ('4001')) v(s)")


@pytest.mark.parametrize("call", ["REPLICATE('a', {})", "LEFT('abc', {})", "RIGHT('abc', -{})",
                                  "SUBSTRING('abc', {}, 2)", "SUBSTRING('abc', 1, {})", "STUFF('abc', 1, {}, 'x')",
                                  "CHARINDEX('a', 'abc', {})", "SUBSTRING('abc', -{}, 2)"])
@pytest.mark.parametrize("number", ["9" * 5000, "9223372036854775809", "1e400", "1e30", "99999999999999999999.5"])
def test_a_count_past_bigint_is_t_sqls_overflow_not_pythons(call, number):
    from fabric_desktop.localdata import tsql
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type bigint"):
        tsql.translate("SELECT " + call.format(number))


@pytest.mark.parametrize("number", ["9" * 5000, "2147483648", "1e400", "1e30", "99999999999999999999.5"])
def test_a_space_count_past_int_is_t_sqls_overflow_not_pythons(number):
    # SPACE takes an int (functions/space-transact-sql), so its overflow names int
    from fabric_desktop.localdata import tsql
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type int"):
        tsql.translate(f"SELECT SPACE({number})")


@pytest.mark.parametrize("call", ["SUBSTRING('abc', 1, -9223372036854775808)", "RIGHT('abc', -9223372036854775808)"])
def test_the_lowest_bigint_is_within_bigint(engine, call):
    # -9223372036854775808 is a bigint, so the call's own error comes, not the overflow
    assert "Invalid length parameter" in error(engine, "SELECT " + call)


@pytest.mark.parametrize("sql, out", [
    ("SELECT LEN(REPLICATE('a', 9223372036854775807 - 9223372036854775800))", [[7]]),
    ("SELECT LEN(SPACE(1e1) + 'x')", [[11]]),
    ("SELECT LEFT('abc', 2.9e0)", [["ab"]]),
])
def test_a_count_within_bigint_still_runs(engine, sql, out):
    assert rows(engine, sql) == out
