"""Unity Catalog volumes on this PC (Preview, databricks_volumes): SQL statements, LIST and read_files in a Databricks SQL
cell, dbutils.fs and %fs, spark.read and df.write on /Volumes paths, and the path checks that keep each one inside
its volume's folder. Each test has a volume root of its own (LOCALAPPDATA in tmp_path)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fabric_desktop import databricks_volumes as volumes
from fabric_desktop.engines.contract import CellRequest, SessionConfig
from fabric_desktop.engines.local_sql import DuckDBSession
from fabric_desktop.notebook import Kernel


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


def _ok(answer):
    status, out = answer
    assert status == "ok", out
    return out


def _error(answer):
    status, out = answer
    assert status == "error", out
    return out["evalue"]


def _rows(answer):
    out = _ok(answer)
    return out["columns"], out["rows"]


# ---- where volumes live

def test_volumes_live_in_the_app_data_folder_under_main_schema_volume(here):
    from fabric_desktop.service import data_dir
    assert volumes.root() == data_dir() / "volumes"
    assert volumes.create("default", "Landing", "raw files")
    assert (here / "default" / "landing").is_dir()  # Unity Catalog stores names as lowercase
    info = volumes.describe("default", "landing")
    assert (info.path, info.folder, info.volume_type, info.comment, info.owner, info.items) == \
        ("/Volumes/main/default/landing", str(here / "default" / "landing"), "MANAGED", "raw files", "ana", 0)


def test_the_volume_root_in_onedrive_is_refused_with_where_to_keep_it(here, monkeypatch):
    from fabric_desktop import known_folders
    monkeypatch.setattr(known_folders, "onedrive_root", lambda p: Path("C:/Users/ana/OneDrive"))
    with pytest.raises(ValueError, match="is in OneDrive .* so a volume's files can't be kept there"):
        volumes.create("default", "v")


# ---- paths: syntax, catalogs, and nothing outside the volume

@pytest.mark.parametrize("path, parts", [
    ("/Volumes/main/default/v/a/b.csv", ("default", "v", ("a", "b.csv"))),
    ("dbfs:/Volumes/workspace/Sales/v/", ("sales", "v", ())),
    ("/Volumes/hive_metastore/default/v//x", ("default", "v", ("x",))),
])
def test_a_volume_path_names_main_its_schema_and_volume(path, parts):
    assert tuple(volumes.parse(path)) == parts


@pytest.mark.parametrize("path, says", [
    ("/Volumes/prod/default/v/x", "prod is a Unity Catalog catalog"),
    ("/Volumes/main/main/v/x", "already uses the name main"),
    ("/Volumes/main/default", "stops above a volume"),
    ("/Volumes/main/default/v/../w/x", "holds '..'"),
    ("/Volumes/main/default/v/./x", "holds '.'"),
    ("/Volumes/main/default/v/a\\..\\..\\x", "backslash"),
    ("/Volumes/main/default/v/C:/Windows", "holds ':'"),
    ("/Volumes/main/default/v/x.csv:stream", "holds ':'"),
    ("/Volumes/main/default/v/x.", "drops a final dot"),
    ("/Volumes/main/default/v/NUL.txt", "device names"),
    ("/Volumes/main/default/con/x", "device name"),
    ("/Volumes/main/default/v/a\x00b", "NUL"),
])
def test_a_path_that_could_lead_elsewhere_is_refused_by_name(path, says):
    with pytest.raises(ValueError, match=says.replace("(", r"\(").replace(".", r"\.")):
        volumes.parse(path)


def test_a_link_out_of_a_volume_is_refused(here, tmp_path):
    volumes.create("default", "v")
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "k.txt").write_text("key")
    try:
        os.symlink(outside, here / "default" / "v" / "out", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    with pytest.raises(ValueError, match="leads outside its volume's folder"):
        volumes.local_path("/Volumes/main/default/v/out/k.txt")
    with pytest.raises(ValueError, match=r"holds a link or junction \(out\)"):
        volumes.check_tree(here / "default" / "v", "/Volumes/main/default/v")
    assert [f.name for f in volumes.files("/Volumes/main/default/v")] == ["out"]  # listed, never read through


def test_a_volume_folder_that_is_a_link_isnt_used(here, tmp_path):
    (here / "default").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        os.symlink(elsewhere, here / "default" / "v", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make a symbolic link")
    with pytest.raises(ValueError, match="is a link"):
        volumes.local_path("/Volumes/main/default/v/x")
    assert volumes.volumes() == []


def test_a_volume_that_isnt_there_says_so(here):
    with pytest.raises(ValueError, match=r"The volume main\.default\.nope cannot be found"):
        volumes.local_path("/Volumes/main/default/nope/x")


def test_uc_path_writes_a_local_path_back_as_its_volume_path(here):
    volumes.create("default", "v")
    assert volumes.uc_path(here / "default" / "v" / "a" / "b.csv") == "dbfs:/Volumes/main/default/v/a/b.csv"
    assert volumes.uc_path(here / "default" / "v", scheme=False) == "/Volumes/main/default/v"
    assert volumes.uc_path(here.parent / "x") is None


@pytest.mark.parametrize("name, says", [("a.b", "'.'"), ("a b", "a space"), ("a|b", "'|'"), ("", "needs a name"),
                                        ("x" * 256, "longer than the 255")])
def test_a_name_unity_catalog_or_windows_cant_hold_is_refused(name, says):
    with pytest.raises(ValueError, match=says):
        volumes.check_name("volume", name)


# ---- SQL: the statements

def test_create_show_describe_and_drop_answer_with_databricks_columns(sql, here):
    _ok(sql("CREATE VOLUME landing COMMENT 'raw files'"))
    assert "[VOLUME_ALREADY_EXISTS]" in _error(sql("CREATE VOLUME main.default.landing"))
    _ok(sql("CREATE VOLUME IF NOT EXISTS workspace.default.landing"))
    _ok(sql("CREATE VOLUME exports"))
    assert _rows(sql("SHOW VOLUMES")) == (["database", "volumeName"], [["default", "exports"], ["default", "landing"]])
    columns, rows = _rows(sql("DESCRIBE VOLUME main.default.landing"))
    assert columns == ["name", "catalog", "database", "owner", "storageLocation", "volumeType", "comment"]
    assert rows == [["landing", "main", "default", "ana", "file:" + str(here / "default" / "landing").replace("\\", "/"),
                     "MANAGED", "raw files"]]
    (here / "default" / "exports" / "x.csv").write_text("a\n1\n")
    _ok(sql("DROP VOLUME exports"))
    assert not (here / "default" / "exports").exists()
    assert "The volume main.default.exports cannot be found" in _error(sql("DROP VOLUME exports"))
    _ok(sql("DROP VOLUME IF EXISTS exports"))
    assert _rows(sql("SHOW VOLUMES"))[1] == [["default", "landing"]]


def test_show_volumes_like_reads_star_and_bar_as_databricks_does(sql):
    for name in ("another_volume", "foo_volume", "Alpha"):
        _ok(sql(f"CREATE VOLUME {name}"))
    assert _rows(sql("SHOW VOLUMES LIKE 'a*'"))[1] == [["default", "alpha"], ["default", "another_volume"]]
    assert _rows(sql("SHOW VOLUMES 'foo*|ALPHA'"))[1] == [["default", "alpha"], ["default", "foo_volume"]]
    assert _rows(sql("SHOW VOLUMES LIKE ' zz '"))[1] == []


def test_a_volume_in_another_schema_needs_the_schema(sql):
    assert _error(sql("CREATE VOLUME sales.landing")).startswith("[SCHEMA_NOT_FOUND] The schema `main`.`sales`")
    assert _error(sql("SHOW VOLUMES IN sales")).startswith("[SCHEMA_NOT_FOUND]")
    _ok(sql("CREATE SCHEMA sales; CREATE VOLUME main.sales.landing"))
    assert _rows(sql("SHOW VOLUMES IN sales"))[1] == [["sales", "landing"]]
    assert _rows(sql("USE SCHEMA sales; SHOW VOLUMES"))[1] == [["sales", "landing"]]


def test_a_new_session_sees_the_volumes_of_a_schema_it_lacks(sql, here):
    volumes.create("finance", "raw")
    assert _rows(sql("SHOW VOLUMES FROM main.finance"))[1] == [["finance", "raw"]]
    _ok(sql("CREATE VOLUME finance.archive"))


def test_alter_volume_renames_and_comment_on_volume_sets_the_comment(sql, here):
    _ok(sql("CREATE VOLUME v COMMENT 'one'"))
    (here / "default" / "v" / "f.txt").write_text("x")
    _ok(sql("ALTER VOLUME v RENAME TO w; COMMENT ON VOLUME w IS 'two'"))
    assert (here / "default" / "w" / "f.txt").exists() and not (here / "default" / "v").exists()
    assert _rows(sql("DESCRIBE VOLUME w"))[1][0][-1] == "two"
    _ok(sql("CREATE VOLUME v"))
    assert "[VOLUME_ALREADY_EXISTS]" in _error(sql("ALTER VOLUME v RENAME TO w"))
    _ok(sql("CREATE SCHEMA s"))
    assert "the schema must not change" in _error(sql("ALTER VOLUME v RENAME TO s.v"))
    _ok(sql("COMMENT ON VOLUME w IS NULL"))
    assert _rows(sql("DESCRIBE VOLUME w"))[1][0][-1] is None


@pytest.mark.parametrize("statement, says", [
    ("CREATE EXTERNAL VOLUME e LOCATION 's3://bucket/x'", "CREATE EXTERNAL VOLUME can't run on this PC"),
    ("CREATE VOLUME e LOCATION 'abfss://c@a.dfs.core.windows.net/x'", "CREATE VOLUME ... LOCATION can't run"),
    ("ALTER VOLUME v SET OWNER TO `bob@contoso.com`", "SET OWNER TO can't run"),
    ("ALTER VOLUME v OWNER TO bob", "OWNER TO can't run"),
    ("ALTER VOLUME v SET TAGS ('a' = 'b')", "SET TAGS can't run"),
    ("ALTER VOLUME v UNSET TAGS ('a')", "UNSET TAGS can't run"),
    ("UNDROP VOLUME v", "UNDROP VOLUME can't run"),
    ("PUT '/tmp/x.csv' INTO '/Volumes/main/default/v/x.csv'", "PUT INTO, GET and REMOVE run only through"),
    ("REMOVE '/Volumes/main/default/v/x.csv'", "Databricks stops it too"),
    ("CREATE VOLUME prod.default.v", "prod is a Unity Catalog catalog"),
    ("CREATE VOLUME main.v", "already uses the name main"),
    ("DROP VOLUME a.b.c.d", "[PARSE_SYNTAX_ERROR]"),
    ("LIST 's3://bucket/raw'", "lists cloud storage, which this PC never reaches"),
    ("LIST '/Volumes/main/default/v' WITH (CREDENTIAL c)", "WITH (CREDENTIAL ...) can't run"),
    ("LIST '/Volumes/main/default/v' LIMIT 0", "LIMIT takes a whole number"),
])
def test_what_this_pc_cant_do_is_refused_by_name_before_anything_runs(sql, here, statement, says):
    _ok(sql("CREATE VOLUME v"))
    assert says in _error(sql(f"CREATE VOLUME marker; {statement}"))
    assert not (here / "default" / "marker").exists()  # refused before the cell ran


def test_statements_run_in_their_place_in_the_cell(sql):
    out = _error(sql("CREATE VOLUME a; CREATE VOLUME a"))
    assert "[VOLUME_ALREADY_EXISTS]" in out
    assert _rows(sql("SHOW VOLUMES"))[1] == [["default", "a"]]  # the first one ran, once


def test_volume_words_in_strings_and_comments_are_left_alone(sql):
    assert _rows(sql("SELECT 'DROP VOLUME x' AS a; -- LIST '/Volumes/main/default/v'\nSELECT 2 AS b"))[1] == [[2]]


# ---- SQL: LIST and read_files

def test_list_answers_path_name_size_and_modification_time(sql, here):
    _ok(sql("CREATE VOLUME v"))
    folder = here / "default" / "v"
    (folder / "b.csv").write_bytes(b"a,b\n1,x\n")  # 8 bytes on Windows too
    (folder / "sub").mkdir()
    columns, rows = _rows(sql("LIST '/Volumes/main/default/v'"))
    assert columns == ["path", "name", "size", "modification_time"]
    assert [r[:3] for r in rows] == [["dbfs:/Volumes/main/default/v/sub", "sub", 0],
                                     ["dbfs:/Volumes/main/default/v/b.csv", "b.csv", 8]]
    assert rows[1][3] == int((folder / "b.csv").stat().st_mtime * 1000)
    assert len(_rows(sql("LIST 'dbfs:/Volumes/main/default/v/' LIMIT 1"))[1]) == 1
    assert "has no /Volumes/main/default/v/nope" in _error(sql("LIST '/Volumes/main/default/v/nope'"))


def test_read_files_reads_a_csv_json_or_parquet_file_or_folder_in_a_volume(sql, here):
    _ok(sql("CREATE VOLUME v"))
    folder = here / "default" / "v"
    (folder / "x.csv").write_text("id,name\n1,a\n2,b\n")
    columns, rows = _rows(sql("SELECT * FROM read_files('/Volumes/main/default/v/x.csv', format => 'csv')"))
    assert columns == ["id", "name", "_rescued_data"] and rows == [[1, "a", None], [2, "b", None]]
    assert _rows(sql("SELECT name FROM read_files('/Volumes/main/default/v/x.csv', schemaEvolutionMode => 'none') "
                     "AS t WHERE t.id = 2"))[1] == [["b"]]
    assert _rows(sql("SELECT * FROM read_files('/Volumes/main/default/v/x.csv', header => false, "
                     "inferColumnTypes => false, schemaEvolutionMode => 'none')"))[1][0] == ["id", "name"]
    (folder / "j").mkdir()
    (folder / "j" / "a.json").write_text('{"k": 1}\n{"k": 2}\n')
    (folder / "j" / "_SUCCESS").write_text("")
    assert _rows(sql("SELECT k FROM read_files('/Volumes/main/default/v/j', format => 'json') ORDER BY k"))[1] == [[1], [2]]
    _ok(sql("CREATE TABLE t AS SELECT 7 AS n"))
    import duckdb
    duckdb.sql("SELECT 7 AS n").write_parquet(str(folder / "p.parquet"))
    assert _rows(sql("SELECT n FROM parquet.`/Volumes/main/default/v/p.parquet`"))[1] == [[7]]
    # format.`path` reads as Spark's reader does by default: no header, every CSV column a string
    assert _rows(sql("SELECT * FROM csv.`/Volumes/main/default/v/x.csv`")) == \
        (["_c0", "_c1"], [["id", "name"], ["1", "a"], ["2", "b"]])


@pytest.mark.parametrize("call, says", [
    ("read_files('/Volumes/main/default/v/x.csv', format => 'csv', pathGlobFilter => '*.csv')",
     "read_files option pathGlobFilter can't be honoured"),
    ("read_files('/Volumes/main/default/v/x.csv', format => 'xml')", "format 'xml' can't run on this PC"),
    ("read_files('abfss://c@a.dfs.core.windows.net/x.csv')", "reads cloud storage, which this PC never reaches"),
    ("read_files('/Volumes/main/default/v/../../../x.csv')", "holds '..'"),
    ("read_files('/Volumes/main/default/v/none.csv')", "[PATH_NOT_FOUND]"),
    ("read_files('/Volumes/other/default/v/x.csv')", "other is a Unity Catalog catalog"),
])
def test_read_files_refuses_what_it_cant_read_as_databricks_does(sql, here, call, says):
    _ok(sql("CREATE VOLUME v"))
    (here / "default" / "v" / "x.csv").write_text("a\n1\n")
    assert says in _error(sql(f"SELECT * FROM {call}"))


def test_read_files_reads_only_the_files_it_names(sql, here, tmp_path):
    """The files are read on a connection that may open those files and nothing else; the session's own
    connection still can't read any file."""
    _ok(sql("CREATE VOLUME v"))
    (here / "default" / "v" / "x.csv").write_text("a\n1\n")
    (tmp_path / "other.csv").write_text("a\n2\n")
    assert _error(sql(f"SELECT * FROM read_csv('{(tmp_path / 'other.csv').as_posix()}')"))
    assert _rows(sql("SELECT a FROM read_files('/Volumes/main/default/v/x.csv')"))[1] == [[1]]


# ---- dbutils.fs and %fs

def test_dbutils_fs_works_in_a_volume_and_lists_its_volume_paths(here, tmp_path):
    volumes.create("default", "v")
    src = tmp_path / "local.csv"
    src.write_text("a,b\n")
    k = Kernel()
    out = k.run("dbutils.fs.mkdirs('/Volumes/main/default/v/raw')\n"
                "dbutils.fs.put('/Volumes/main/default/v/raw/hello.txt', 'Hello, Databricks!', True)\n"
                f"dbutils.fs.cp('file:{src.as_posix()}', 'dbfs:/Volumes/main/default/v/raw/')\n"
                "dbutils.fs.cp('/Volumes/main/default/v/raw/hello.txt', '/Volumes/main/default/v/new.txt')\n"
                "dbutils.fs.mv('/Volumes/main/default/v/new.txt', '/Volumes/main/default/v/moved.txt')\n"
                "print(dbutils.fs.head('/Volumes/main/default/v/moved.txt', 5))\n"
                "[(f.path, f.name, f.size, f.isDir()) for f in dbutils.fs.ls('/Volumes/main/default/v/')]")
    assert out["status"] == "ok", out
    assert "Wrote 18 bytes." in out["outputs"][0]["text"] and "[Truncated to first 5 bytes]\nHello" in out["outputs"][0]["text"]
    assert eval(out["outputs"][-1]["text"]) == [  # noqa: S307 - the kernel's repr
        ("dbfs:/Volumes/main/default/v/raw/", "raw/", 0, True),
        ("dbfs:/Volumes/main/default/v/moved.txt", "moved.txt", 18, False)]
    assert sorted(p.name for p in (here / "default" / "v" / "raw").iterdir()) == ["hello.txt", "local.csv"]
    assert k.run("dbutils.fs.rm('/Volumes/main/default/v/raw', True)")["outputs"][-1]["text"] == "True"
    assert not (here / "default" / "v" / "raw").exists()
    assert k.run("dbutils.fs.rm('/Volumes/main/default/v/raw')")["outputs"][-1]["text"] == "False"


def test_dbutils_fs_refuses_the_volume_itself_escapes_and_other_catalogs(here):
    volumes.create("default", "v")
    k = Kernel()
    for code, says in (("dbutils.fs.rm('/Volumes/main/default/v', True)", "is the volume main.default.v itself"),
                       ("dbutils.fs.ls('/Volumes/main/default/v/../../w')", "holds '..'"),
                       ("dbutils.fs.ls('/Volumes/prod/raw/x')", "prod is a Unity Catalog catalog"),
                       ("dbutils.fs.ls('/Volumes/main/raw/x')", "The volume main.raw.x cannot be found"),
                       ("dbutils.fs.put('/Volumes/main/default/v/a\\\\b', 'x')", "backslash")):
        out = k.run(code)
        assert out["status"] == "error" and says in out["outputs"][-1]["evalue"], (code, out)
    assert (here / "default" / "v").is_dir()


def test_fs_magic_works_in_a_volume(here):
    volumes.create("default", "v")
    k = Kernel()
    assert k.run('%fs put /Volumes/main/default/v/a.txt "one,two"')["status"] == "ok"
    listed = k.run("%fs ls /Volumes/main/default/v/")["outputs"][0]
    assert [r[:2] for r in listed["rows"]] == [["a.txt", "dbfs:/Volumes/main/default/v/a.txt"]]
    assert "one,two" in k.run("%fs head /Volumes/main/default/v/a.txt")["outputs"][0]["text"]
    out = k.run("%fs rm -r /Volumes/main/default/v")
    assert out["status"] == "error" and out["outputs"][-1]["evalue"].startswith("%fs rm: ")


# ---- spark.read and df.write

def test_spark_reads_and_writes_a_volume_path(here):
    volumes.create("default", "v")
    (here / "default" / "v" / "data.csv").write_text("id,name\n1,a\n2,b\n")
    k = Kernel()
    out = k.run("df = spark.read.format('csv').option('header', 'true').load('/Volumes/main/default/v/data.csv')\n"
                "df.write.mode('overwrite').parquet('/Volumes/main/default/v/out')\n"
                "back = spark.read.load('dbfs:/Volumes/main/default/v/out')\n"
                "sorted(r.name for r in back.collect())")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["text"] == "['a', 'b']"
    assert any(p.name.startswith("part-00000-") for p in (here / "default" / "v" / "out").iterdir())
    missing = k.run("spark.read.parquet('/Volumes/main/default/v/none')")
    assert "[PATH_NOT_FOUND] Path does not exist: /Volumes/main/default/v/none." in missing["outputs"][-1]["evalue"]
    escaped = k.run("spark.read.parquet('/Volumes/main/default/v/../../x')")
    assert "holds '..'" in escaped["outputs"][-1]["evalue"]


# ---- the notebook import check

def test_an_import_lists_a_volume_path_for_fabric_not_as_unreachable():
    from fabric_desktop import notebook_formats as nbf
    source = "# Databricks notebook source\nx = 1\n\n# COMMAND ----------\n\ndf = spark.read.csv('/Volumes/main/default/v/x.csv')"
    notes = [n for n in nbf.parse_source(source, "nb.py")["notes"] if n["kind"] != "changed"]
    assert [(n["cell"], n["kind"]) for n in notes] == [(2, "fabric")]
    assert "Fabric has no volumes" in notes[0]["what"]
    other = source.replace("/Volumes/main/default/v/x.csv", "dbfs:/mnt/raw/x.csv")
    assert [n["kind"] for n in nbf.parse_source(other, "nb.py")["notes"] if n["kind"] != "changed"] == ["check"]


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters and UNC paths are Windows'")
def test_a_unc_or_drive_path_can_never_come_out_of_a_volume_path_on_windows(here):
    volumes.create("default", "v")
    for path in ("/Volumes/main/default/v///server/share/x", "/Volumes/main/default/v/C:x"):
        try:
            local = volumes.local_path(path)
        except ValueError:
            continue
        assert str(local).startswith(str(here / "default" / "v"))


@pytest.mark.parametrize("name", ["con", "CON.txt", "con .txt", "nul  .csv", "COM1", "lpt9.log", "aux", "CONIN$",
                                  "conout$.txt"])
def test_a_windows_device_name_is_refused_even_with_spaces_before_its_extension(name):
    with pytest.raises(ValueError, match="device name"):
        volumes.parse(f"/Volumes/main/default/v/{name}")
    with pytest.raises(ValueError, match="device name"):
        volumes.check_name("volume", name.split(".")[0].strip())


@pytest.mark.parametrize("name", ["console", "con_x.txt", "nully", "com10", "a con.txt"])
def test_a_name_that_only_starts_like_a_device_is_kept(name):
    assert volumes.parse(f"/Volumes/main/default/v/{name}").uc_path() == f"/Volumes/main/default/v/{name}"
