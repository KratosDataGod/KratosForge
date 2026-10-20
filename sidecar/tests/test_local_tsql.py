"""FD-LDE-004: T-SQL on the local engine, translated to DuckDB SQL with T-SQL's answers."""

from __future__ import annotations

import datetime as dt

import pytest

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
    with pytest.raises(Exception):
        tsql(engine, "SELECT 'x' + 1")  # T-SQL can't convert 'x' to a number either


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
    with pytest.raises(Exception):
        tsql(engine, "SELECT CONVERT(INT, 'x')")  # an error in T-SQL, not NULL


def test_max_lengths_in_table_definitions(engine):
    r = tsql(engine, "CREATE TABLE #notes (body NVARCHAR(MAX)); INSERT INTO #notes VALUES ('hello'); "
                     "SELECT body FROM #notes")
    assert r["rows"] == [["hello"]]
