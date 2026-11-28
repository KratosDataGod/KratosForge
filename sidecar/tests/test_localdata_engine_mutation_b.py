"""Mutation batch 515, a re-score of localdata/engine.py from batches 3 and 152 (docs/quality/mutation-testing.md):
the 1,024-column limit of a T-SQL table, T-SQL errors in SQL Server's words (alone, in a script and while a
result streams), statements the project's own Delta tables refuse, another writer's commits and tables, a
persistent connection given back after RECYCLE_AFTER queries, a change stopped part way, and the words and
numbers an answer carries."""

from __future__ import annotations

import json
import logging
import os
import threading
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng

OVERFLOW = "Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int."


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    return local.engine(local.create("Batch 515")["id"])


@pytest.fixture
def sales(engine):
    engine.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id, 10 AS amount")
    return engine


def _error(engine, sql, dialect="tsql"):
    with pytest.raises(Exception) as caught:
        engine.execute(sql, dialect=dialect)
    return str(caught.value)


def _read_error(engine, sql, dialect="tsql"):
    with pytest.raises(Exception) as caught:
        with engine.execute_reader(sql, dialect=dialect) as stream:
            stream.reader.read_all()
    return caught.value


def _cols(n):
    return ", ".join(f"c{i} INT" for i in range(n))


# ---- the 1,024 columns of a T-SQL table

def test_a_tsql_table_of_1025_columns_is_refused_by_name(engine):
    assert _error(engine, f"CREATE TABLE dbo.w ({_cols(1025)})") == (
        "CREATE TABLE failed because column 'c1024' in table 'w' exceeds the maximum of 1024 columns.")


def test_a_tsql_table_made_from_1025_values_is_refused(engine):
    values = ", ".join(f"{i} AS c{i}" for i in range(1025))
    text = _error(engine, f"CREATE TABLE dbo.v AS SELECT {values}")
    assert "column 'c1024' in table 'v' exceeds the maximum of 1024 columns." in text


def test_a_tsql_table_of_1024_columns_and_a_small_one_are_made(engine):
    engine.execute(f"CREATE TABLE dbo.w ({_cols(1024)})", dialect="tsql")
    engine.execute("CREATE TABLE dbo.s (a INT, b INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.s VALUES (1, 2)", dialect="tsql")
    assert engine.execute("SELECT a, b FROM dbo.s", dialect="tsql")["rows"] == [[1, 2]]
    assert len(engine.execute("SELECT * FROM dbo.w", dialect="tsql")["columns"]) == 1024


# ---- T-SQL errors in SQL Server's words

def test_one_failing_tsql_statement_isnt_named_as_one_of_a_script(engine):
    assert _error(engine, "SELECT * FROM nosuch") == "Msg 208, Level 16: Invalid object name 'nosuch'."


def test_a_script_names_the_failing_statement_and_the_last_one_that_didnt_run(engine):
    text = _error(engine, "SELECT 1 AS a; SELECT * FROM nosuch; SELECT 2 AS b")
    assert text.startswith("Statement 2 of 3 (SELECT) failed: Msg 208, Level 16: Invalid object name 'nosuch'.")
    assert text.endswith("Statement 1 ran, and what it changed is kept; the last statement didn't run.")


def test_a_refused_translation_has_sql_servers_number(engine):
    assert _error(engine, "WITH a AS (SELECT 1 AS x), a AS (SELECT 2 AS x) SELECT * FROM a") == (
        "Msg 239, Level 16: Duplicate common table expression name 'a' was specified.")


@pytest.fixture
def big(engine):
    engine.execute("CREATE TABLE dbo.t (n INT)", dialect="tsql")
    engine.execute("INSERT INTO dbo.t VALUES (0), (2147483647)", dialect="tsql")
    return engine


def test_an_overflow_while_a_tsql_result_streams_is_in_sql_servers_words(big):
    assert str(_read_error(big, "SELECT n + 1 AS m FROM dbo.t")) == OVERFLOW


def test_an_overflow_while_a_scripts_last_result_streams_names_that_statement(big):
    text = str(_read_error(big, "SELECT 1 AS a; SELECT n + 1 AS m FROM dbo.t"))
    assert text == f"Statement 2 of 2 (SELECT) failed: {OVERFLOW} Statement 1 ran, and what it changed is kept."


def test_an_overflow_while_a_duckdb_result_streams_keeps_duckdbs_words(big):
    text = str(_read_error(big, "SELECT n + 1 AS m FROM dbo.t", dialect="duckdb"))
    assert text.startswith("Out of Range Error: Overflow in addition of INT32")


def test_a_streams_own_os_error_comes_out_as_it_is(engine, monkeypatch):
    def broken(result, size):
        def batches():
            raise OSError("the disk went away")
            yield  # pragma: no cover
        return pa.RecordBatchReader.from_batches(pa.schema([("a", pa.int64())]), batches())
    monkeypatch.setattr(eng, "arrow_reader", broken)
    err = _read_error(engine, "SELECT 1 AS a", dialect="duckdb")
    assert isinstance(err, OSError) and "the disk went away" in str(err)


def test_the_log_keeps_the_first_1000_characters_of_duckdbs_words(engine, caplog):
    name = "x" * 1500
    with caplog.at_level(logging.INFO, logger=eng.__name__):
        _error(engine, f"SELECT * FROM {name}")
    said = [r.getMessage().split("DuckDB said: ", 1)[1] for r in caplog.records if "DuckDB said: " in r.getMessage()]
    assert said and len(said[-1]) == 1000 and said[-1].endswith("…")


# ---- the project's own Delta tables

def test_truncate_of_the_projects_own_delta_table_is_refused(sales):
    with pytest.raises(ValueError, match="TRUNCATE TABLE on sales, one of the project's Delta tables, isn't mapped"):
        sales.execute("TRUNCATE TABLE sales")
    assert sales.execute("SELECT id FROM sales")["rows"] == [[1]]


def test_drop_of_the_projects_own_delta_table_is_refused(sales):
    with pytest.raises(ValueError, match="sales is one of the project's Delta tables. Dropping it deletes"):
        sales.execute("DROP TABLE sales")
    assert sales.execute("SELECT id FROM sales")["rows"] == [[1]]


def test_a_data_file_outside_the_table_is_refused_with_200_characters_of_its_path(tmp_path):
    t = tmp_path / "t"
    write_deltalake(str(t), pa.table({"id": [1]}))
    rel = "../" + "a" * 300 + ".parquet"
    add = {"add": {"path": rel, "partitionValues": {}, "size": 100, "modificationTime": 0, "dataChange": True}}
    info = {"commitInfo": {"timestamp": 1, "operation": "WRITE"}}
    (t / "_delta_log" / f"{1:020d}.json").write_text(json.dumps(add) + "\n" + json.dumps(info) + "\n",
                                                     encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        eng.delta_select(str(t))
    assert "names a data file outside the table's folder" in str(caught.value)
    assert f"({rel[:200]!r})" in str(caught.value)


def test_a_table_another_writer_makes_is_read_by_the_next_statement(sales):
    assert sales.execute("SELECT id FROM sales")["rows"] == [[1]]
    write_deltalake(str(sales.tables_dir / "dbo" / "made"), pa.table({"n": [5, 6]}))
    assert sales.execute("SELECT sum(n) AS s FROM made")["rows"] == [["11"]]


def test_only_the_table_another_writer_committed_to_is_read_again(sales, monkeypatch):
    sales.execute("CREATE TABLE other USING DELTA AS SELECT 2 AS id")
    sales.execute("SELECT 1")
    write_deltalake(str(sales.tables_dir / "dbo" / "sales"), pa.table({"id": [3], "amount": [30]}), mode="append")
    read_again = []
    register = sales._register_own

    def spy(con, schema, table, folder, save=True):
        read_again.append(table)
        return register(con, schema, table, folder, save)
    monkeypatch.setattr(sales, "_register_own", spy)
    assert sales.execute("SELECT count(*) AS n FROM sales")["rows"] == [[2]]
    assert read_again == ["sales"]


def test_a_commit_that_left_the_logs_time_alone_is_still_seen(sales):
    sales.execute("SELECT 1")
    log = sales.tables_dir / "dbo" / "sales" / "_delta_log"
    before = os.stat(log)
    write_deltalake(str(sales.tables_dir / "dbo" / "sales"), pa.table({"id": [3], "amount": [30]}), mode="append")
    os.utime(log, ns=(before.st_atime_ns, before.st_mtime_ns))  # as a volume with 2-second times can leave it
    assert sales.execute("SELECT count(*) AS n FROM sales")["rows"] == [[2]]


def test_a_writes_profile_check_after_its_commit_says_it_committed(engine, monkeypatch):
    calls = []
    check = eng.delta_profile.check_table

    def spy(table, *args, **kwargs):
        calls.append(kwargs.get("committed", False))
        return check(table, *args, **kwargs)
    monkeypatch.setattr(eng.delta_profile, "check_table", spy)
    engine.write_delta("dbo.made", "SELECT 1 AS id")
    assert calls[-1] is True


def test_a_change_says_how_long_it_took(sales):
    assert 0 <= sales.change_delta("DELETE FROM sales WHERE id = 5")["seconds"] < 60


def test_a_change_stopped_part_way_is_cancelled(sales, monkeypatch):
    def stopped(*args, **kwargs):
        sales.cancel()
        raise RuntimeError("delta-rs stopped")
    monkeypatch.setattr(eng.delta_dml, "apply", stopped)
    with pytest.raises(eng.QueryCancelled):
        sales.execute("DELETE FROM sales WHERE id = 1")


def test_a_tsql_merge_into_an_unqualified_table_refuses_a_row_matched_twice(sales):
    text = _error(sales, "MERGE INTO sales AS t USING (SELECT 1 AS id UNION ALL SELECT 1 AS id) AS s "
                         "ON t.id = s.id WHEN MATCHED THEN UPDATE SET amount = 5;")
    assert text.startswith(eng.TSQL_SAME_ROW_TWICE) and text.endswith("Nothing in dbo.sales was changed.")


# ---- the connection

def test_a_persistent_connection_is_given_back_after_recycle_after_queries(local, tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "RECYCLE_AFTER", 4)
    data = tmp_path / "files"
    data.mkdir()
    (data / "orders.csv").write_text("id,amount\n1,10\n2,20\n", encoding="utf-8")
    pid = local.create("Recycled", storage="persistent")["id"]
    local.add_source(pid, str(data))
    e = local.engine(pid)
    first = e._con
    for _ in range(12):
        assert e.execute("SELECT sum(amount) AS t FROM orders")["rows"] == [["30"]]
    assert e.reopened >= 1 and e._con is not first


def test_the_table_list_gives_back_the_cursor_it_borrowed(sales):
    sales.execute("SELECT 1")
    held, done = threading.Event(), threading.Event()

    def hold():
        with sales._lock:
            held.set()
            done.wait(10)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(10)
    try:
        assert any(x["table"] == "sales" for x in sales.tables())
    finally:
        done.set()
        t.join(10)
    assert sales._borrowed == 0


def test_a_file_put_under_a_made_tables_name_is_an_issue_with_its_path(local, tmp_path):
    pid = local.create("Issue")["id"]
    e = local.engine(pid)
    e.execute("CREATE TABLE dbo.orders AS SELECT 1 AS x")
    src = tmp_path / "src"
    src.mkdir()
    (src / "orders.csv").write_text("a\n1\n", encoding="utf-8")
    issues = local.add_source(pid, str(src))["issues"]
    assert [i["path"] for i in issues] == [str(src / "orders.csv")]


# ---- what an answer carries

def test_a_query_without_a_limit_returns_1000_rows(engine):
    assert len(engine.execute("SELECT * FROM range(1500)")["rows"]) == 1000


def test_each_statement_says_how_long_it_took(engine):
    steps = engine.execute("SELECT 1 AS a; SELECT 2 AS b")["statements"]
    assert [s["statement"] for s in steps] == [1, 2] and all(0 <= s["seconds"] < 60 for s in steps)


def test_an_analyzed_query_names_its_three_slowest_steps(engine):
    out = engine.explain("SELECT a.id, count(*) FROM range(100) a(id) JOIN range(50) b(id) ON a.id = b.id "
                         "WHERE a.id > 3 GROUP BY a.id ORDER BY 1", analyze=True)
    assert len(out["slowest"]) == 3


def test_a_cut_answer_under_the_most_says_to_pass_a_larger_limit():
    assert eng.shown_rows_note(5, 10, True, 5) == (
        "Showing the first 5 of 10 rows. Pass a larger limit (up to 100,000), or run it with sql_start "
        "(target \"local\") to page through and export every row.")
    assert eng.shown_rows_note(eng.MAX_QUERY_ROWS, None, True, eng.MAX_QUERY_ROWS).startswith(
        "Showing the first 100,000 rows; the query returned more. Run it")


def test_the_lines_above_a_header_are_counted_in_words():
    table = SimpleNamespace(schema="dbo", table="notes")
    assert eng._lines_above_problem(1, table).startswith(
        "The first line of this file doesn't look like the rest, so it is probably a title")
    assert eng._lines_above_problem(2, table).startswith(
        "The first 2 lines of this file don't look like the rest, so they are probably a title")


def test_a_statement_after_a_closed_comment_keeps_its_first_letter():
    assert eng._kind("/* a /* nested */ c */SELECT 1") == "select"


def test_spark_sql_dropping_the_projects_own_delta_table_is_refused(sales):
    with pytest.raises(ValueError, match="sales is one of the project's Delta tables. Dropping it deletes"):
        sales.execute("DROP TABLE sales", dialect="spark")
    assert sales.execute("SELECT id FROM sales")["rows"] == [[1]]
