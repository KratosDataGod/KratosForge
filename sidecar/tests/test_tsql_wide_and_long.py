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
