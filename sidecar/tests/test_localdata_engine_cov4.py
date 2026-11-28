"""localdata/engine.py: the helpers the engine's own tests don't reach, mostly with fake connections, and the
Tables folder issues a project's Delta tables can have (a folder name that can't be a table, a name a
registered file or a table people made already has). All in tmp_path."""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng


# Pure helpers

def test_a_file_uri_is_a_path_with_a_windows_drive_kept():
    assert eng._local_path("file:///C:/Users/x/part-0.parquet") == "C:/Users/x/part-0.parquet"
    assert eng._local_path("file:///tmp/t/part-0.parquet") == "/tmp/t/part-0.parquet"


@pytest.mark.parametrize("kind, has", [
    (pa.map_(pa.string(), pa.struct([("a", pa.int32())])), True),
    (pa.map_(pa.string(), pa.int32()), False),
    (pa.large_list(pa.struct([("a", pa.int32())])), True),
    (pa.list_(pa.int32(), 2), False),
])
def test_a_struct_is_found_inside_maps_and_lists(kind, has):
    assert eng._has_struct(kind) is has


@pytest.mark.parametrize("text, target", [
    ("UPDATE dbo.t SET a = 1", ("dbo", "t")),
    ("DELETE FROM t", (None, "t")),
    ("UPDATE db.dbo.t SET a = 1", None),  # a catalog: not one of the project's tables
    ("SELECT 1", None),
    ("UPDATE 'unclosed", None),             # can't be read
])
def test_the_table_a_dml_statement_changes(text, target):
    assert eng._dml_target(text) == target


def test_a_version_that_isnt_a_number_has_no_next_commit(tmp_path):
    assert eng._next_commit_exists(str(tmp_path), {"delta": {"version": "3"}}) is False
    assert eng._next_commit_exists(str(tmp_path), {}) is False
    (tmp_path / "_delta_log").mkdir()
    (tmp_path / "_delta_log" / f"{4:020d}.json").write_text("{}")
    assert eng._next_commit_exists(str(tmp_path), {"delta": {"version": 3}}) is True


class Picky(Exception):
    """An error that needs more than a message to be made."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def test_a_script_error_names_the_statement_and_keeps_an_error_that_cant_be_remade():
    said = eng._in_script(ValueError("Bad value!"), 0, 1, "insert into")
    assert str(said) == "Statement 1 of 1 (INSERT) failed: Bad value! Nothing before it ran."
    said = eng._in_script(ValueError("no table t"), 2, 5, "select")
    assert str(said) == ("Statement 3 of 5 (SELECT) failed: no table t. Statements 1 to 2 ran, and what they changed "
                         "is kept; the 2 after it didn't run.")
    picky = Picky("x", 1)
    assert eng._in_script(picky, 1, 2, "update") is picky
    assert eng._same_kind(picky, "other words") is picky


def test_leading_comments_are_skipped_nested_as_t_sql_nests_them():
    assert eng._without_leading_comments("/* a /* b */ c */ -- x\n  SELECT 1") == "SELECT 1"
    assert eng._without_leading_comments("-- only a comment") == ""
    assert eng._without_leading_comments("/* unclosed /* */") == ""


def test_an_error_that_isnt_duckdbs_is_kept_unless_sql_server_numbers_it():
    plain = ValueError("Something else went wrong")
    assert eng._as_warehouse_says(plain, {"sql": "SELECT 1", "kind": "select"}) is plain


# The column limit, with DESCRIBE failing or nothing to count

def test_the_column_limit_leaves_what_it_cant_count_to_duckdb():
    with duckdb.connect() as con:
        con.execute("CREATE TABLE wide AS SELECT 1 AS a")
        eng._within_column_limit(con, "CREATE TABLE 'unclosed")                      # can't be read
        eng._within_column_limit(con, "CREATE TABLE t AS SELECT * FROM missing")     # DESCRIBE fails
        eng._within_column_limit(con, "ALTER TABLE missing ADD COLUMN b INT")        # DESCRIBE fails
        eng._within_column_limit(con, "CREATE TABLE t2 AS (VALUES (1))")             # nothing to count
        eng._within_column_limit(con, "CREATE TABLE t3")                             # nor here
        eng._within_column_limit(con, "ALTER TABLE wide ADD COLUMN b INT")           # 2 columns
        many = ", ".join(f"c{i} INT" for i in range(eng.MAX_TABLE_COLUMNS))
        con.execute(f"CREATE TABLE full_t ({many})")
        with pytest.raises(ValueError, match=r"ALTER TABLE failed because column 'extra' in table 'full_t' exceeds"):
            eng._within_column_limit(con, "ALTER TABLE full_t ADD COLUMN extra INT")


# Fake connections

class Cursor:
    def __init__(self, fail):
        self.fail, self.closed = fail, False

    def execute(self, sql):
        if self.fail:
            raise duckdb.FatalException("database invalidated")

    def close(self):
        self.closed = True


class Con:
    def __init__(self, cursor_fails=False, select_fails=False, errors=()):
        self.cursor_fails, self.select_fails, self.errors, self.ran = cursor_fails, select_fails, list(errors), []
        self.made = []

    def cursor(self):
        if self.cursor_fails:
            raise duckdb.FatalException("database invalidated")
        self.made.append(Cursor(self.select_fails))
        return self.made[-1]

    def execute(self, sql, *params):
        self.ran.append(sql)
        if self.errors:
            raise self.errors.pop(0)
        return self

    def complete(self):
        pass

    def interrupt(self):
        self.ran.append("interrupt")


def test_an_invalidated_database_is_seen_on_the_cursor_or_its_query():
    assert eng._invalidated(Con(cursor_fails=True)) is True
    con = Con(select_fails=True)
    assert eng._invalidated(con) is True and con.made[0].closed
    con = Con()
    assert eng._invalidated(con) is False and con.made[0].closed


def test_release_retries_an_interrupt_and_stops_on_a_broken_connection(monkeypatch):
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)
    con = Con(errors=[duckdb.InterruptException("late"), duckdb.InterruptException("late")])
    eng._release(con)
    assert con.ran == ["SELECT 1"] * 3
    con = Con(errors=[duckdb.ConnectionException("closed")])
    eng._release(con)
    assert con.ran == ["SELECT 1"]
    con = Con(errors=[duckdb.InterruptException("late")] * 3)
    eng._release(con)  # three tries, then the next query says what's wrong
    assert con.ran == ["SELECT 1"] * 3


def test_a_csv_the_sniffer_cant_read_has_no_lines_above_its_rows(tmp_path):
    with duckdb.connect() as con:
        assert eng._lines_above_the_rows(con, str(tmp_path / "missing.csv")) == 0
        csv = tmp_path / "titled.csv"
        csv.write_text("Sales report\n\na,b\n1,2\n3,4\n5,6\n")
        assert eng._lines_above_the_rows(con, str(csv)) >= 0


# An engine

@pytest.fixture()
def local(tmp_path):
    ld = LocalData(tmp_path / "app" / "local-data")
    yield ld
    ld.close_all()


def test_interrupting_stops_once_nothing_cancelled_runs(local, monkeypatch):
    e = local.engine(local.create("Stop")["id"])
    e.execute("SELECT 1")
    monkeypatch.setattr(eng.time, "sleep", lambda s: e._running.clear())
    e._con = Con()
    e._running["q1"] = {"cancelled": True}
    e._interrupt_until_stopped("q1")
    assert e._con.ran == ["interrupt"]
    e._running["q2"] = {"cancelled": True}
    e._interrupt_until_stopped("q2", seconds=0)  # past its deadline at once: nothing sent
    assert e._con.ran == ["interrupt"]
    e._con = None


def test_delta_folders_that_cant_be_tables_are_listed_as_issues(local, tmp_path):
    pid = local.create("Own issues")["id"]
    data = tmp_path / "data"
    data.mkdir()
    (data / "filed.csv").write_text("x\n1\n")
    local.add_source(pid, str(data))
    e = local.engine(pid)
    e.execute("CREATE TABLE dbo.made AS SELECT 1 AS answer")
    for name in ("made", "filed", "bad name!"):
        write_deltalake(str(e.tables_dir / "dbo" / name), pa.table({"id": [1]}))
    e.refresh()
    issues = {i["path"].replace("\\", "/").rsplit("/", 1)[-1]: i["problem"]
              for i in e.project.refreshed[eng.OWN_KEY]["issues"]}
    assert "already a table or view you made" in issues["made"]
    assert "dbo.filed is also a file from" in issues["filed"] and "Rename one of them" in issues["filed"]
    assert "bad name!" in issues
    assert e.execute("SELECT answer FROM dbo.made")["rows"] == [[1]]  # not put over
    assert e.execute("SELECT x FROM dbo.filed")["rows"] == [[1]]       # the file is read instead


def test_a_rename_of_a_registered_file_is_refused_and_what_isnt_a_rename_is_left(local, tmp_path):
    pid = local.create("Renames")["id"]
    data = tmp_path / "data"
    data.mkdir()
    (data / "filed.csv").write_text("x\n1\n")
    local.add_source(pid, str(data))
    e = local.engine(pid)
    with pytest.raises(ValueError, match="Renaming dbo.filed isn't mapped yet: it's read from a registered folder"):
        e.execute("ALTER TABLE dbo.filed RENAME TO other")
    assert e.execute("SELECT x FROM dbo.filed")["rows"] == [[1]]
    with duckdb.connect() as con:
        e._refuse_rename(con, "ALTER TABLE 'unclosed RENAME TO y")         # can't be read: DuckDB says so
        e._refuse_rename(con, 'ALTER TABLE t ADD COLUMN "rename" INT')   # RENAME only as a column's name
        e._refuse_rename(con, "SELECT 1")
