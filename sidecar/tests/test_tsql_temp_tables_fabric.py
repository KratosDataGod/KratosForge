"""WR-260: #temp tables as a Fabric Warehouse has them.

Microsoft Learn (Fabric Warehouse, temp tables): session #temp tables are supported, distributed or not, and
only a distributed one can be altered. ``##global`` tables aren't supported, and table variables aren't listed.
On the local engine a T-SQL run is the session: a #temp table lasts for the run and is dropped when it ends.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate


@pytest.fixture()
def local(tmp_path):
    store = LocalData(tmp_path / "app" / "local-data")
    yield store
    store.close_all()


@pytest.fixture()
def engine(local):
    e = local.engine(local.create("Temp")["id"])
    e.execute("CREATE TABLE dbo.t (id INT)", dialect="tsql")
    return e


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def temp_tables(engine):
    return [t["table"] for t in engine.tables() if t["table"].startswith("#")]


# A #temp table lasts for the run

def test_a_temp_table_is_seen_by_every_batch_of_its_run(engine):
    assert rows(engine, "CREATE TABLE #t (a INT);\nINSERT INTO #t VALUES (1), (2)\nGO\n"
                        "SELECT COUNT(*) FROM #t") == [[2]]


def test_a_temp_table_is_gone_after_its_run(engine):
    rows(engine, "SELECT 1 AS a INTO #t; SELECT a FROM #t")
    assert temp_tables(engine) == []
    with pytest.raises(Exception, match=r"Msg 208, Level 16: Invalid object name '#t'\."):
        engine.execute("SELECT a FROM #t", dialect="tsql")


def test_the_next_run_can_make_the_same_temp_table_again(engine):
    for _ in range(2):
        assert rows(engine, "CREATE TABLE #t (a INT); INSERT INTO #t VALUES (7); SELECT a FROM #t") == [[7]]


def test_a_failed_run_drops_its_temp_tables_too(engine):
    with pytest.raises(Exception, match="nothere"):
        engine.execute("CREATE TABLE #t (a INT); SELECT * FROM dbo.nothere", dialect="tsql")
    assert temp_tables(engine) == []


def test_a_streamed_run_drops_its_temp_table_once_read(engine):
    with engine.execute_reader("SELECT 5 AS a INTO #s; SELECT a FROM #s", dialect="tsql") as stream:
        assert stream.read_all().column("a").to_pylist() == [5]
    assert temp_tables(engine) == []


def test_a_project_table_is_kept(engine):
    rows(engine, "INSERT INTO dbo.t VALUES (1); SELECT id INTO #c FROM dbo.t")
    assert rows(engine, "SELECT id FROM dbo.t") == [[1]]


def test_a_distributed_temp_table_can_be_altered(engine):
    assert rows(engine, "CREATE TABLE #t (a INT) WITH (DISTRIBUTION = ROUND_ROBIN);\nGO\nALTER TABLE #t ADD b INT;\n"
                        "INSERT INTO #t VALUES (1, 2);\nSELECT a, b FROM #t") == [[1, 2]]


# Refused by name

@pytest.mark.parametrize("sql", [
    "CREATE TABLE #t (a INT); ALTER TABLE #t ADD b INT",
    "SELECT 1 AS a INTO #t; ALTER TABLE #t ADD b INT",
    "CREATE TABLE #t (a INT)\nGO\nALTER TABLE #t DROP COLUMN a",
])
def test_altering_a_non_distributed_temp_table_is_refused(engine, sql):
    with pytest.raises(ValueError, match=r"ALTER TABLE #t isn't supported in a Fabric Warehouse, because a warehouse "
                                         r"alters only a distributed #temp table"):
        engine.execute(sql, dialect="tsql")
    assert temp_tables(engine) == []


@pytest.mark.parametrize(("sql", "what"), [
    ("DECLARE @t TABLE (a INT)", "DECLARE @t TABLE"),
    ("DECLARE @t AS TABLE (a INT, b INT)", "DECLARE @t TABLE"),
    ("DECLARE @x INT, @rows TABLE (a INT)", "DECLARE @rows TABLE"),
    ("SELECT * FROM @t", "@t"),
    ("INSERT INTO @t VALUES (1)", "@t"),
])
def test_table_variables_are_refused_by_name(sql, what):
    with pytest.raises(ValueError, match=rf"table variables \({what}\) aren't supported in a Fabric Warehouse\. Use a "
                                         r"#temp table"):
        translate(sql)


@pytest.mark.parametrize("sql", ["CREATE VIEW dbo.v AS SELECT a FROM #t",
                                 "CREATE VIEW dbo.v AS SELECT t.id FROM dbo.t JOIN #k ON #k.id = t.id",
                                 "CREATE OR ALTER VIEW dbo.v AS SELECT a FROM #t"])
def test_a_view_on_a_temp_table_is_sql_servers_error_4508(engine, sql):
    with pytest.raises(ValueError, match=r"^Msg 4508, Level 16: Views or functions are not allowed on temporary "
                                         r"tables\. Table names that begin with '#' denote temporary tables\.$"):
        engine.execute(sql, dialect="tsql")


def test_a_temporary_view_is_refused():
    with pytest.raises(ValueError, match=r"a view can't be temporary \(#v\)"):
        translate("CREATE VIEW #v AS SELECT 1 AS a")


@pytest.mark.parametrize(("sql", "written"), [
    ("SELECT * FROM dbo.#t", "dbo.#t"),
    ("CREATE TABLE dbo.#t (a INT)", "dbo.#t"),
    ("INSERT INTO dbo.#t VALUES (1)", "dbo.#t"),
    ("SELECT * FROM tempdb..#t", "tempdb..#t"),
    ("SELECT * FROM tempdb.dbo.#t", "tempdb.dbo.#t"),
])
def test_a_schema_on_a_temp_table_is_refused(sql, written):
    with pytest.raises(ValueError, match=rf"a #temp table takes no schema or database name \({written}\), in a "
                                         r"Fabric Warehouse either\. Write #t alone\."):
        translate(sql)


@pytest.mark.parametrize("sql", ["CREATE TABLE ##g (a INT)", "SELECT * FROM ##g", "INSERT INTO ##g VALUES (1)"])
def test_a_global_temp_table_is_refused(sql):
    with pytest.raises(ValueError, match=r"global temporary tables \(##g\) aren't supported in a Fabric Warehouse"):
        translate(sql)


def test_a_temp_table_is_made_temporary_with_its_name():
    out, _ = translate("CREATE TABLE #t (a INT) WITH (DISTRIBUTION = ROUND_ROBIN); SELECT a FROM #t")
    assert out == ['CREATE TEMPORARY TABLE "#t" (a INT)', 'SELECT a FROM "#t"']
