"""A local data project's engine (localdata/engine.py): the remaining edges. Helpers read on their own
(struct types, DML targets, script and column-limit errors, the CSV sniffer, a stopped connection), and a
real project on local disk: an empty stream, Delta folders that clash with other names, renames that
aren't mapped, Stop with nothing to stop, and a dry run's copy where links can't be made."""

from __future__ import annotations

import duckdb
import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng


@pytest.fixture
def project(tmp_path):
    """A project with its own Delta table dbo.sales and a registered folder holding orders.parquet."""
    local = LocalData(tmp_path / "app" / "local-data")
    e = local.engine(local.create("Cov")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id")
    files = tmp_path / "files"
    files.mkdir()
    pl.DataFrame({"id": [1, 2]}).write_parquet(files / "orders.parquet")
    local.add_source(e.project.id, str(files))
    return e


# ---- helpers on their own


def test_a_map_holding_a_struct_counts_as_a_struct():
    assert eng._has_struct(pa.map_(pa.string(), pa.struct([("a", pa.int64())]))) is True
    assert eng._has_struct(pa.map_(pa.string(), pa.int64())) is False


def test_a_dml_target_is_none_when_it_cant_be_read_or_names_a_catalog():
    assert eng._dml_target("UPDATE (((") is None
    assert eng._dml_target("UPDATE lake.dbo.t SET a = 1") is None
    assert eng._dml_target("UPDATE dbo.t SET a = 1") == ("dbo", "t")


def test_a_script_error_gets_a_full_stop_and_an_error_that_needs_more_stays_as_it_was():
    out = eng._in_script(ValueError("boom"), 1, 3, "insert into")
    assert str(out) == ("Statement 2 of 3 (INSERT) failed: boom. Statement 1 ran, and what it changed is kept; "
                        "the last statement didn't run.")

    class Picky(Exception):
        def __init__(self, a, b):
            super().__init__(f"{a} {b}")
    picky = Picky("x", "y")
    assert eng._in_script(picky, 0, 1, "select") is picky


def test_the_column_limit_leaves_what_duckdb_will_refuse_itself():
    with duckdb.connect() as con:
        assert eng._within_column_limit(con, "CREATE TABLE (((") is None
        assert eng._within_column_limit(con, "CREATE TABLE t AS SELECT * FROM no_such_table") is None
        assert eng._within_column_limit(con, "ALTER TABLE no_such_table ADD COLUMN b INT") is None
        cols = ", ".join(f"c{i} INT" for i in range(1025))
        with pytest.raises(Exception, match="1024|1,024"):
            eng._within_column_limit(con, f"CREATE TABLE wide ({cols})")
        assert con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'wide'").fetchone() == (0,)


def test_a_file_the_sniffer_cant_read_has_no_lines_above_its_rows(tmp_path):
    with duckdb.connect() as con:
        assert eng._lines_above_the_rows(con, str(tmp_path / "missing.csv")) == 0


class Con:
    """A connection whose cursor or query fails the given way."""

    def __init__(self, cursor_fails=None, execute_fails=None):
        self.cursor_fails, self.execute_fails, self.closed = cursor_fails, execute_fails, 0

    def cursor(self):
        if self.cursor_fails:
            raise self.cursor_fails
        return self

    def execute(self, *a):
        if self.execute_fails:
            raise self.execute_fails
        return self

    def close(self):
        self.closed += 1


def test_a_connection_duckdb_invalidated_is_seen_as_such():
    assert eng._invalidated(Con(cursor_fails=duckdb.FatalException("invalidated"))) is True
    con = Con(execute_fails=duckdb.FatalException("invalidated"))
    assert eng._invalidated(con) is True and con.closed == 1


def test_releasing_waits_out_a_late_interrupt_and_leaves_a_broken_connection(monkeypatch):
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)
    calls = []

    def through(con, query):
        calls.append(query)
        if len(calls) == 1:
            raise duckdb.InterruptException("interrupted")
    monkeypatch.setattr(eng, "run_through", through)
    eng._release(object())
    assert calls == ["SELECT 1", "SELECT 1"]

    def broken(con, query):
        calls.append("broken")
        raise duckdb.ConnectionException("closed")
    monkeypatch.setattr(eng, "run_through", broken)
    eng._release(object())
    assert calls[-1] == "broken" and calls.count("broken") == 1

    def always(con, query):
        calls.append("again")
        raise duckdb.InterruptException("interrupted")
    monkeypatch.setattr(eng, "run_through", always)
    eng._release(object())  # gives up after three tries
    assert calls.count("again") == 3


def test_a_commit_conflict_on_a_log_that_cant_be_read_isnt_called_a_conflict(tmp_path):
    from deltalake.exceptions import CommitFailedError
    assert eng._lost_to_another_writer(CommitFailedError("conflict"), tmp_path / "missing", 0) is False


def test_a_leftover_spill_file_that_cant_be_removed_is_logged(tmp_path, monkeypatch):
    (tmp_path / "duckdb_temp_1").write_text("x", encoding="utf-8")
    infos = []
    monkeypatch.setattr(eng._log, "info", lambda *a: infos.append(a))

    def held(self, missing_ok=False):
        raise PermissionError("in use")
    monkeypatch.setattr(type(tmp_path), "unlink", held)
    eng._remove_leftover_spill(tmp_path)
    assert infos[0][0] == "Couldn't remove %s, left from an earlier run: %s" and infos[0][1] == "duckdb_temp_1"


def test_data_files_named_by_uri_are_read_as_paths_on_disk(tmp_path):
    class Table:
        def get_add_actions(self, flatten=False):
            return pa.record_batch({"path": pa.array(["file://" + tmp_path.as_posix() + "/part%201.parquet"], pa.string())})
    assert eng._data_files(Table(), str(tmp_path)) == [(tmp_path / "part 1.parquet").as_posix()]


def test_data_files_named_by_uri_outside_the_table_are_refused(tmp_path):
    class Table:
        def get_add_actions(self, flatten=False):
            return pa.record_batch({"path": pa.array(["file:///data/t/part%201.parquet"], pa.string())})
    with pytest.raises(ValueError, match="outside the table's folder"):
        eng._data_files(Table(), str(tmp_path))


# ---- a project


def test_a_statement_with_no_rows_streams_an_empty_reader(project):
    with project.execute_reader("CREATE TABLE made (a INT)") as stream:
        assert stream.reader.read_all().num_rows == 0


def test_delta_folders_that_clash_with_other_names_are_listed_as_issues(project):
    project.execute("CREATE VIEW mine AS SELECT 1 AS a")
    one = pa.table({"a": [1]})
    for name in ("orders", "mine", "bad name!"):
        write_deltalake(str(project.tables_dir / "dbo" / name), one)
    with project._lock:
        project._refresh_own()
    problems = {i["path"].replace("\\", "/").rsplit("/", 1)[-1]: i["problem"]
                for i in project.project.refreshed[eng.OWN_KEY]["issues"]}
    assert set(problems) == {"orders", "mine", "bad name!"}
    assert "is also a file from" in problems["orders"]
    assert "is already a table or view you made in this project" in problems["mine"]
    assert project.execute("SELECT * FROM dbo.orders")["rows"] == [[1], [2]]  # the file is read, not the folder


def test_a_rename_of_a_registered_folder_or_an_unreadable_one_is_left_to_duckdb_or_refused(project):
    with pytest.raises(ValueError, match="it's read from a registered folder, which keeps its name"):
        project.execute("ALTER TABLE dbo.orders RENAME TO orders2")
    with project._lock:
        con = project._open_locked()
        assert project._refuse_rename(con, "ALTER TABLE ((( RENAME") is None
        assert project._refuse_rename(con, "ALTER TABLE sales ALTER COLUMN id SET DEFAULT 'rename'") is None


def test_stop_with_nothing_running_stops_nothing(project):
    assert project.running == [] and project.cancel() is False
    project._running["q1"] = {"cancelled": False}
    project._running["q2"] = {"cancelled": False}
    project._con = None  # the interrupt thread finds nothing to interrupt and ends
    try:
        assert project.cancel("q2") is True
        assert project._running["q1"]["cancelled"] is False and project._running["q2"]["cancelled"] is True
        assert sorted(project.running) == ["q1", "q2"]
        project._interrupt_until_stopped("q2", seconds=0)  # past its deadline at once
    finally:
        project._running.clear()


def test_a_dry_run_copies_data_files_where_links_cant_be_made(project, monkeypatch):
    def no_links(src, dst):
        raise OSError("links not allowed here")
    monkeypatch.setattr(eng.os, "link", no_links)
    folder = project.tables_dir / "dbo" / "sales"
    with project._shadow(folder) as copy:
        parquet = [p for p in copy.rglob("*.parquet")]
        assert parquet and all(p.stat().st_nlink == 1 for p in parquet)
        assert (copy / "_delta_log").is_dir()
