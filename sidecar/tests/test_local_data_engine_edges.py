"""A local data project's engine (localdata/engine.py), the paths the main tests don't reach: asks it
refuses before anything runs, names that are already taken, Delta tables it can't read here, and the
small helpers that keep a bad file or value from failing a whole listing. All on local disk."""

from __future__ import annotations

import duckdb
import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import engine as eng


@pytest.fixture
def local(tmp_path):
    return LocalData(tmp_path / "app" / "local-data")


@pytest.fixture
def engine(local, tmp_path):
    """A project with its own Delta table dbo.sales and a registered folder holding orders.parquet."""
    e = local.engine(local.create("Edges")["id"])
    e.execute("CREATE TABLE sales USING DELTA AS SELECT 1 AS id")
    files = tmp_path / "files"
    files.mkdir()
    pl.DataFrame({"id": [1, 2]}).write_parquet(files / "orders.parquet")
    local.add_source(e.project.id, str(files))
    e.files = files
    return e


# ---- asks refused before anything runs


@pytest.mark.parametrize("call, says", [
    (lambda e: e.execute("SELECT 1", dialect="kql"), "dialect must be one of"),
    (lambda e: e.execute("   "), "The query is empty"),
    (lambda e: e.execute("SELECT FROM WHERE (", dialect="spark"), "Could not read the query as Spark SQL"),
    (lambda e: e.write_delta("x", "SELECT 1 AS a", mode="upsert"), "mode must be create, append or overwrite"),
    (lambda e: e.write_delta("x", "SELECT 1 AS a", schema_mode="drop"), "schema_mode must be merge or overwrite"),
    (lambda e: e.write_delta("x", "SELECT 1 AS a", partition_by=["b"]), "Can't partition by b"),
    (lambda e: e.delta_history("nope"), "isn't a Delta table in Edges"),
    (lambda e: e.delta_history("orders"), r"isn't a Delta table \(parquet\)"),
    (lambda e: e.set_override("dbo.orders", {"id": "INT; DROP TABLE x"}), "is not a column type"),
])
def test_asks_the_engine_cant_do_are_refused_with_why(engine, call, says):
    with pytest.raises(ValueError, match=says):
        call(engine)
    assert not (engine.tables_dir / "dbo" / "x").exists()


@pytest.mark.parametrize("call", [
    lambda e: e.refresh("nowhere"),
    lambda e: e.remove_source("nowhere"),
    lambda e: e.set_override("dbo.nope", {"id": "INT"}),
])
def test_a_folder_or_file_the_project_doesnt_have_is_a_key_error(engine, call):
    with pytest.raises(KeyError, match="nowhere|nope"):
        call(engine)


def test_a_type_override_can_be_cleared(engine):
    assert engine.set_override("dbo.orders", {"id": "VARCHAR"})["columns"] == [{"name": "id", "type": "VARCHAR"}]
    assert engine.set_override("dbo.orders", {})["columns"] == [{"name": "id", "type": "BIGINT"}]
    assert engine.project.overrides == {}


# ---- names already taken


def test_a_delta_table_isnt_written_over_a_file_from_a_folder(engine):
    with pytest.raises(ValueError, match="dbo.orders is a file from .*Pick another name"):
        engine.write_delta("orders", "SELECT 1 AS id", mode="append")


@pytest.mark.parametrize("name, says", [
    ("sales", "one of the project's Delta tables"),
    ("orders", "read from "),
])
def test_a_table_isnt_made_over_a_delta_table_or_a_file(engine, name, says):
    with pytest.raises(ValueError, match=f"dbo.{name} is {says}.*Pick another name"):
        engine.make_table(name, "SELECT 1 AS id")


def test_a_folder_with_other_files_isnt_written_into(engine):
    junk = engine.tables_dir / "dbo" / "junk"
    junk.mkdir(parents=True)
    (junk / "notes.txt").write_text("keep me")
    with pytest.raises(ValueError, match="already has files that aren't a Delta table"):
        engine.write_delta("junk", "SELECT 1 AS a")
    assert (junk / "notes.txt").read_text() == "keep me"


def test_a_file_named_like_a_project_delta_table_is_left_out_and_says_so(local, engine, tmp_path):
    more = tmp_path / "more"
    more.mkdir()
    (more / "sales.csv").write_text("id\n9\n")
    issues = local.add_source(engine.project.id, str(more))["issues"]
    assert {"path": str(more / "sales.csv"),
            "problem": "dbo.sales is one of this project's Delta tables. Set a different name for one of them."} in issues
    assert engine.execute("SELECT id FROM sales")["rows"] == [[1]]  # the project's table is still the one read


# ---- Delta tables this engine can't read


def test_a_delta_table_with_column_mapping_isnt_queryable_here(tmp_path):
    folder = str(tmp_path / "mapped")
    write_deltalake(folder, pa.table({"a": [1]}), configuration={"delta.columnMapping.mode": "name"})
    with pytest.raises(ValueError, match="uses column mapping, which a local read can't apply yet"):
        eng.delta_select(folder)


def test_a_timestamp_before_a_tables_first_version_says_when_that_was(engine):
    with pytest.raises(ValueError, match=r"has no version as of 1990-01-01T00:00:00 \(UTC\): its first is from"):
        eng.delta_select(str(engine.tables_dir / "dbo" / "sales"), timestamp="1990-01-01T00:00:00")


# ---- small helpers


def test_a_windows_file_uri_becomes_a_drive_path():
    assert eng._local_path("file:///C:/data/part-0.parquet") == "C:/data/part-0.parquet"
    assert eng._local_path("file:///home/me/part-0.parquet") == "/home/me/part-0.parquet"


def test_a_table_with_no_columns_selects_nothing():
    assert eng._duckdb_types(pa.schema([])) == {}
    assert eng._empty_select(pa.schema([])) == "SELECT NULL WHERE false"


def test_sizes_and_numbers_that_cant_be_read_dont_fail_a_listing(tmp_path):
    assert eng._size(str(tmp_path / "gone.parquet")) == 0
    assert eng._int("many") is None and eng._int(None) is None and eng._int("7") == 7


def test_sql_that_cant_be_split_or_parsed_says_so():
    con = duckdb.connect()
    with pytest.raises(ValueError, match="Could not split the script"):
        eng._split_script("SELECT 'unclosed")
    assert eng._bound(con, "SELEC $x", {"x": 1}) is None  # DuckDB reports it when the statement runs
    with pytest.raises(ValueError):
        eng._only_reads(con, "SELEC 1")
    with pytest.raises(ValueError):
        eng._only_reads(con, "SELECT 1; SELECT 2")


def test_only_table_folders_inside_tables_are_ever_removed(tmp_path):
    tables = tmp_path / "Tables"
    (tables / "dbo" / "t").mkdir(parents=True)
    with pytest.raises(ValueError, match="isn't a table folder of this project"):
        eng._remove_tree(tables / "dbo", tables)
    eng._remove_tree(tables / "dbo" / "gone", tables)  # already gone: nothing to do
    eng._remove_tree(tables / "dbo" / "t", tables)
    assert not (tables / "dbo" / "t").exists() and (tables / "dbo").is_dir()
