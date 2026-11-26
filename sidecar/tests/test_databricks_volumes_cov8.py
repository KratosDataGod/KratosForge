"""databricks_volumes edges: the refusals and answers the main tests don't reach (paths above a volume, links the
link check misses, a volume's meta file, the SQL statement reader's syntax errors, the functions the statements
call, and read_files' option and argument refusals). Each test has a volume root of its own (LOCALAPPDATA in
tmp_path)."""

from __future__ import annotations

import json
import os

import duckdb
import pytest
import sqlglot

from fabric_desktop import databricks_volumes as volumes
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.engines.local_sql import DuckDBSession


@pytest.fixture
def here(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERNAME", "ana")
    return tmp_path / "appdata" / "FabricDesktop" / "volumes" / "main"


@pytest.fixture
def sql(here):
    session = DuckDBSession(SessionConfig("nb"))

    def run(code):
        result = session.execute(CellRequest(code, language="sql", dialect="databricks"))
        return result.status, result.outputs[-1]
    yield run
    session.close()


def _error(answer):
    status, out = answer
    assert status == "error", out
    return out["evalue"]


def _ok(answer):
    status, out = answer
    assert status == "ok", out
    return out


# ---- paths and folders

@pytest.mark.parametrize("path", ["/Volumes", "dbfs:/Volumes", "/Volumes/main/default"])
def test_a_path_that_stops_above_a_volume_is_refused(path):
    with pytest.raises(ValueError, match="stops above a volume"):
        volumes.parse(path)


def test_a_schema_folder_link_the_link_check_misses_is_still_refused_by_its_real_location(here, tmp_path,
                                                                                          monkeypatch):
    """A junction on a Python without os.path.isjunction isn't seen as a link; its real location still is."""
    outside = tmp_path / "elsewhere"
    (outside / "v").mkdir(parents=True)
    here.mkdir(parents=True)
    os.symlink(outside, here / "default")
    monkeypatch.setattr(volumes, "_is_link", lambda _p: False)
    with pytest.raises(ValueError, match="leads outside"):
        volumes.volume_folder("default", "v")


def test_uc_path_of_a_schema_folder_is_none(here):
    volumes.create("default", "v")
    assert volumes.uc_path(here / "default") is None
    assert volumes.uc_path(here) is None
    assert volumes.uc_path(here / "default" / "v" / "a.csv", scheme=False) == "/Volumes/main/default/v/a.csv"


def test_a_volume_whose_folder_went_away_counts_no_items_and_keeps_its_creation_time(here):
    here.joinpath("default").mkdir(parents=True)
    (here / "default" / ".gone.volume.json").write_text(json.dumps({"created": 1234, "comment": 5}))
    info = volumes._info("default", "gone", here / "default" / "gone")
    assert (info.items, info.created, info.comment, info.owner) == (0, 1234, None, "ana")


def test_a_volume_without_a_creation_time_uses_its_folder_time(here):
    (here / "default" / "v").mkdir(parents=True)
    (here / "default" / ".v.volume.json").write_text("[1, 2]")  # not a dict: read as empty
    info = volumes.describe("default", "v")
    assert info.created == int((here / "default" / "v").stat().st_mtime * 1000)
    assert info.comment is None


def test_schema_has_volumes_only_for_a_real_schema_folder_holding_one(here, tmp_path):
    assert not volumes.schema_has_volumes("sales")
    (here / "sales").mkdir(parents=True)
    (here / "sales" / "notes.txt").write_text("x")
    assert not volumes.schema_has_volumes("sales")
    volumes.create("sales", "v")
    assert volumes.schema_has_volumes("sales")
    (tmp_path / "out" / "w").mkdir(parents=True)
    os.symlink(tmp_path / "out", here / "linked")
    assert not volumes.schema_has_volumes("linked")


def test_no_volumes_before_any_is_made_and_a_missing_or_linked_schema_lists_none(here, tmp_path):
    assert volumes.volumes() == []
    volumes.create("default", "v")
    assert volumes.volumes("nope") == []
    (tmp_path / "out" / "w").mkdir(parents=True)
    os.symlink(tmp_path / "out", here / "linked")
    assert [v.name for v in volumes.volumes()] == ["v"]
    assert volumes.volumes("linked") == []


# ---- changes

def test_create_if_not_exists_leaves_a_volume_that_is_there(here):
    assert volumes.create("default", "v", "first")
    assert volumes.create("default", "V", "second", if_not_exists=True) is False
    assert volumes.describe("default", "v").comment == "first"
    with pytest.raises(ValueError, match=r"\[VOLUME_ALREADY_EXISTS\]"):
        volumes.create("default", "v")


def test_create_refuses_the_schema_main():
    with pytest.raises(ValueError, match="already uses the name main"):
        volumes.create("main", "v")


def test_create_refuses_when_the_catalog_folder_is_a_link(here, tmp_path):
    (tmp_path / "out").mkdir()
    here.parent.mkdir(parents=True)
    os.symlink(tmp_path / "out", here)
    with pytest.raises(ValueError, match="is a link, so no volume was made there"):
        volumes.create("default", "v")
    assert not (tmp_path / "out" / "default").exists()


def test_drop_if_exists_of_a_missing_volume_is_false_and_without_it_says_not_found(here):
    assert volumes.drop("default", "nope", if_exists=True) is False
    with pytest.raises(ValueError, match="cannot be found"):
        volumes.drop("default", "nope")


def test_drop_if_exists_still_refuses_a_linked_volume(here, tmp_path):
    (tmp_path / "out").mkdir()
    (here / "default").mkdir(parents=True)
    os.symlink(tmp_path / "out", here / "default" / "v")
    with pytest.raises(ValueError, match="is a link"):
        volumes.drop("default", "v", if_exists=True)
    assert (tmp_path / "out").is_dir()


def test_rename_onto_a_volume_that_is_there_is_refused(here):
    volumes.create("default", "a")
    volumes.create("default", "b")
    with pytest.raises(ValueError, match=r"\[VOLUME_ALREADY_EXISTS\] The volume main.default.b"):
        volumes.rename("default", "a", "B")
    assert (here / "default" / "a").is_dir()


def test_rename_a_volume_without_a_meta_file_moves_only_its_folder(here):
    (here / "default" / "a").mkdir(parents=True)
    volumes.rename("default", "a", "c")
    assert (here / "default" / "c").is_dir() and not (here / "default" / "a").exists()
    assert not (here / "default" / ".c.volume.json").exists()


def test_files_of_a_file_lists_the_file_itself(here):
    volumes.create("default", "v")
    (here / "default" / "v" / "a.csv").write_bytes(b"12345")
    [entry] = volumes.files("/Volumes/main/default/v/a.csv")
    assert (entry.path, entry.name, entry.size, entry.is_dir) == ("dbfs:/Volumes/main/default/v/a.csv", "a.csv", 5,
                                                                  False)
    with pytest.raises(FileNotFoundError, match="doesn't exist"):
        volumes.files("/Volumes/main/default/v/b.csv")


# ---- the statement reader

@pytest.mark.parametrize("code", ["SELECT 'abc", 'SELECT "abc', "SELECT `abc", "COMMENT ON VOLUME v IS 'x"])
def test_a_cell_that_cant_be_read_is_left_for_sqlglot(code):
    assert volumes.prepare(code) == (code, {})


def test_empty_statements_are_skipped_and_a_cell_with_no_volume_statement_is_unchanged():
    assert volumes.prepare(";; SELECT 1;") == (";; SELECT 1;", {})
    code, written = volumes.prepare(";;DROP VOLUME v;;")
    assert len(written) == 1 and code.startswith(";;SELECT '__kf_volume_statement_0_")


def test_adjacent_strings_are_joined_as_spark_joins_them():
    _code, written = volumes.prepare("COMMENT ON VOLUME v IS 'a' \"b\"\n'c'")
    [statements] = written.values()
    assert "'abc'" in statements[0]


def test_a_long_statement_is_shortened_where_it_is_named():
    long = "x" * 100
    with pytest.raises(ValueError, match=r"DROP VOLUME v x{63}\.\.\.: unexpected text"):
        volumes.prepare(f"DROP VOLUME v {long}")


@pytest.mark.parametrize("code, says", [
    ("CREATE VOLUME IF EXISTS v", "expected NOT, near 'EXISTS'"),
    ("CREATE VOLUME v FOO", "expected LOCATION or COMMENT, near 'FOO'"),
    ("CREATE VOLUME v COMMENT 5", "expected a string, near '5'"),
    ("DROP VOLUME", "expected a name, near 'the end of the statement'"),
    ("DROP VOLUME v extra", "unexpected text, near 'extra'"),
    ("DROP VOLUME a.b.c.d", "a volume is named as volume, schema.volume or catalog.schema.volume"),
    ("ALTER VOLUME v BAR", "expected RENAME TO, OWNER TO, SET TAGS or UNSET TAGS"),
    ("ALTER VOLUME v SET foo", "expected RENAME TO, OWNER TO, SET TAGS or UNSET TAGS"),
    ("ALTER VOLUME v UNSET foo", "expected RENAME TO, OWNER TO, SET TAGS or UNSET TAGS"),
    ("SHOW VOLUMES IN a.b.c", "a schema is named as schema or catalog.schema"),
    ("SHOW VOLUMES LIKE '('", "the pattern '(' isn't a regular expression"),
    ("LIST '/Volumes/main/default/v' LIMIT 0", "LIMIT takes a whole number of 1 or more"),
    ("LIST '/Volumes/main/default/v' LIMIT", "LIMIT takes a whole number of 1 or more"),
])
def test_a_volume_statement_databricks_cant_read_is_a_syntax_error(code, says):
    with pytest.raises(ValueError, match=r"\[PARSE_SYNTAX_ERROR\]") as caught:
        volumes.prepare(code)
    assert says in str(caught.value)


@pytest.mark.parametrize("code, says", [
    ("SHOW VOLUMES IN other.s", "other is a Unity Catalog catalog"),
    ("SHOW VOLUMES IN system.s", "system is a catalog of this PC's own engine"),
    ("SHOW VOLUMES FROM workspace.main", "already uses the name main"),
    ("ALTER VOLUME v SET OWNER TO bob", "SET OWNER TO can't run on this PC"),
    ("ALTER VOLUME v OWNER TO bob", "OWNER TO can't run on this PC"),
    ("ALTER VOLUME v SET TAGS ('a' = 'b')", "this PC keeps no tags on a volume"),
    ("ALTER VOLUME v UNSET TAGS ('a')", "this PC keeps no tags on a volume"),
])
def test_what_show_and_alter_cant_do_here_is_refused_by_name(code, says):
    with pytest.raises(ValueError, match="can't run on this PC") as caught:
        volumes.prepare(code)
    assert says in str(caught.value)


def test_show_volumes_in_a_schema_checks_it_and_filters_by_pattern(sql, here):
    _ok(sql("CREATE SCHEMA sales"))
    _ok(sql("CREATE VOLUME sales.raw"))
    _ok(sql("CREATE VOLUME sales.refined"))
    out = _ok(sql("SHOW VOLUMES IN main.sales LIKE ' r*d '"))
    assert out["rows"] == [["sales", "refined"]]
    assert "[SCHEMA_NOT_FOUND] The schema `main`.`nope`" in _error(sql("SHOW VOLUMES IN nope"))


# ---- the functions the statements call

def test_answer_runs_a_change_once_per_token_and_says_what_went_wrong(here):
    volumes._done.clear()
    assert volumes._answer("create", "t1", "default", "v", "c", False) == ""
    assert volumes._answer("create", "t1", "default", "v", "c", False) == ""  # the same change: not run again
    assert "[VOLUME_ALREADY_EXISTS]" in volumes._answer("create", "t2", "default", "v", None, False)
    assert volumes._answer("exists", "", "default", "v", None, False) == ""
    assert "cannot be found" in volumes._answer("exists", "", "default", "w", None, False)
    assert volumes._answer("comment", "t3", "default", "v", "new", False) == ""
    assert volumes.describe("default", "v").comment == "new"
    assert volumes._answer("rename", "t4", "default", "v", "w", False) == ""
    assert volumes._answer("other", "t5", "default", "w", None, False) == ""  # an op with nothing to do
    assert volumes._answer("drop", "t6", "default", "w", None, False) == ""
    assert volumes._answer("drop", "t7", "default", "w", None, True) == ""
    assert "a schema needs a name" in volumes._answer("drop", "", None, "w", None, None)
    assert "t5" in volumes._done and "" not in volumes._done


def test_answer_for_a_schema_says_whether_it_holds_volumes(here):
    assert volumes._answer("schema", "", "default", "", None, False) == ""
    assert volumes._answer("schema", "", "sales", "", None, False) == ""
    volumes.create("sales", "v")
    assert volumes._answer("schema", "", "Sales", "", None, False) == "x"


def test_answer_forgets_old_changes_past_a_thousand(here, monkeypatch):
    monkeypatch.setattr(volumes, "_done", {f"k{i}": "" for i in range(1001)})
    assert "cannot be found" in volumes._answer("exists", "new", "default", "v", None, False)
    assert volumes._done == {"new": volumes._done["new"]}


def test_an_os_error_is_answered_by_its_type_when_it_has_no_text(here, monkeypatch):
    def fail(*_a):
        raise OSError()
    monkeypatch.setattr(volumes, "create", fail)
    assert volumes._answer("create", "", "default", "v", None, False) == "OSError"


def test_answer_list_says_what_a_list_cant_read(here):
    volumes.create("default", "v")
    assert volumes._answer_list("list", "", "", "", "/Volumes/main/default/v", False) == ""
    assert "has no /Volumes/main/default/v/x" in volumes._answer_list("list", "", "", "", "/Volumes/main/default/v/x",
                                                                      False)
    assert "cannot be found" in volumes._answer_list("list", "", "", "", "/Volumes/main/default/w", False)
    assert "isn't a /Volumes path" in volumes._answer_list("list", "", "", "", None, False)


def test_show_describe_and_list_rows(here):
    volumes.create("default", "raw", "c")
    volumes.create("default", "refined")
    (here / "default" / "raw" / "a.csv").write_text("x")
    assert volumes._show_rows("default", None) == [{"database": "default", "volumeName": "raw"},
                                                    {"database": "default", "volumeName": "refined"}]
    assert volumes._show_rows("default", "RAW|nope") == [{"database": "default", "volumeName": "raw"}]
    [row] = volumes._describe_rows("default", "raw")
    assert (row["name"], row["database"], row["owner"], row["volumeType"], row["comment"]) == \
        ("raw", "default", "ana", "MANAGED", "c")
    assert volumes._describe_rows("default", "nope") == []
    assert volumes._describe_rows(None, "raw") == []
    [f] = volumes._list_rows("/Volumes/main/default/raw", -1)
    assert (f["name"], f["size"]) == ("a.csv", 1)
    assert volumes._list_rows("/Volumes/main/default/raw", None) == [f]
    assert volumes._list_rows("/Volumes/main/default/raw/none", 1) == []
    assert volumes._list_rows(None, 1) == []


# ---- read_files

def _write(here, name, text):
    volumes.create("default", "v", if_not_exists=True)
    target = here / "default" / "v" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def test_read_table_refuses_a_header_that_isnt_true_or_false(here):
    _write(here, "x.csv", "a\n1\n")
    with pytest.raises(ValueError, match="option header takes true or false, not 'yes'"):
        volumes.read_table("/Volumes/main/default/v/x.csv", {"header": "yes"})


def test_read_table_refuses_a_folder_with_no_data_files(here):
    _write(here, "d/_SUCCESS", "")
    with pytest.raises(ValueError, match=r"\[UNABLE_TO_INFER_SCHEMA\]"):
        volumes.read_table("/Volumes/main/default/v/d", {})


@pytest.mark.parametrize("names", [["a.csv", "b.json"], ["a.txt"]])
def test_read_table_without_a_format_refuses_files_it_cant_tell(here, names):
    for n in names:
        _write(here, f"d/{n}", "a\n1\n")
    with pytest.raises(ValueError, match="can't tell the format"):
        volumes.read_table("/Volumes/main/default/v/d", {})


def test_read_table_refuses_a_schema_evolution_mode_it_cant_honour(here):
    _write(here, "x.csv", "a\n1\n")
    with pytest.raises(ValueError, match="schemaEvolutionMode 'rescue' can't be honoured"):
        volumes.read_table("/Volumes/main/default/v/x.csv", {"schemaEvolutionMode": "rescue"})


def test_read_table_reads_a_folder_of_csv_files_with_another_separator_and_json_lines_multiline(here):
    _write(here, "d/a.csv", "k;n\n1;x\n")
    _write(here, "d/b.csv", "k;n\n2;y\n")
    table = volumes.read_table("/Volumes/main/default/v/d", {"sep": ";", "schemaEvolutionMode": "none"})
    assert sorted(table.to_pylist(), key=lambda r: r["k"]) == [{"k": 1, "n": "x"}, {"k": 2, "n": "y"}]
    _write(here, "m.json", '[{"k": 1}, {"k": 2}]')
    table = volumes.read_table("/Volumes/main/default/v/m.json", {"multiLine": "true"})
    assert table.column_names == ["k", volumes.RESCUED] and table.column("k").to_pylist() == [1, 2]


def _reads(here, query):
    statements = sqlglot.parse(query, read="databricks")
    with duckdb.connect() as con:
        registered = []
        out = volumes.reads(statements, con, registered)
        return out, registered


def test_reads_takes_a_boolean_option_and_keeps_the_alias(here):
    _write(here, "x.csv", "a,b\n1,2\n")
    [statement], registered = _reads(here, "SELECT * FROM read_files('/Volumes/main/default/v/x.csv', "
                                           "header => true, inferColumnTypes => false) AS t")
    assert len(registered) == 1 and f"{registered[0]} AS t" in statement.sql()


@pytest.mark.parametrize("query, says", [
    ("SELECT * FROM read_files('/Volumes/main/default/v/x.csv', header => 1 + 1)",
     "read_files option header must be a constant on this PC"),
    ("SELECT * FROM read_files('/Volumes/main/default/v/x.csv', '/Volumes/main/default/v/y.csv')",
     "takes one path, a string, then name => value options"),
    ("SELECT * FROM read_files(42)", "takes one path, a string"),
    ("SELECT * FROM read_files(format => 'csv')", "read_files() can't run on this PC"),
    ("SELECT read_files('/Volumes/main/default/v/x.csv')", "read_files is a table function: write it after FROM"),
])
def test_reads_refuses_a_read_files_call_it_cant_read(here, query, says):
    _write(here, "x.csv", "a\n1\n")
    with pytest.raises(ValueError) as caught:
        _reads(here, query)
    assert says in str(caught.value)


def test_a_format_table_with_a_catalog_or_a_path_outside_volumes_is_left_alone(here):
    [statement], registered = _reads(here, "SELECT * FROM c.csv.`/Volumes/main/default/v/x.csv`, csv.`/tmp/x.csv`")
    assert registered == [] and "/Volumes/main/default/v/x.csv" in statement.sql()
