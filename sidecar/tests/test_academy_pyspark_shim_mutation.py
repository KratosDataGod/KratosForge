"""academy/pyspark_shim.py paths that its first mutation run (batch 430, docs/quality/mutation/batch-430.md)
showed no test checked. Everything here runs in the test's own process on Delta tables in ``tmp_path``: no
learner process starts and nothing reaches the network. ``session()`` changes DuckDB's DataFrame class and
``sys.modules`` for the whole process, so the tests that call it put both back afterwards."""

from __future__ import annotations

import sys

import deltalake
import duckdb
import pyarrow as pa
import pytest
from duckdb.experimental.spark.sql import dataframe as _dataframe

from fabric_desktop.academy import notebookutils_lab, pyspark_delta, pyspark_lab, pyspark_shim as ps
from fabric_desktop.duckdb_rows import duckdb_major

SETUP = "CREATE SCHEMA silver; CREATE TABLE silver.t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(id, s)"


@pytest.fixture
def spark(tmp_path):
    pyspark_lab.prepare(tmp_path, SETUP)
    return ps.AcademySession(tmp_path)


def _rows(work, name):
    return sorted(tuple(r.values()) for r in deltalake.DeltaTable(str(ps.table_path(work, name))).to_pyarrow_table().to_pylist())


def _df(spark, rows, cols=("id", "s")):
    return spark.createDataFrame(rows, list(cols))


# ---- table_path

def test_table_path_is_lowercase_under_tables(tmp_path):
    assert ps.table_path(tmp_path, "Silver.Orders") == (tmp_path / "tables" / "silver" / "orders").resolve()
    assert ps.table_path(tmp_path, "orders") == (tmp_path / "tables" / "orders").resolve()


def test_table_path_name_parts_are_up_to_63_characters(tmp_path):
    ok = "a" * 63
    assert ps.table_path(tmp_path, f"{ok}.{ok}").name == ok
    for name in ("a" * 64, f"s.{'a' * 64}", None, 3, "_x.y_1"):
        if name == "_x.y_1":
            assert ps.table_path(tmp_path, name).name == "y_1"
            continue
        with pytest.raises(ValueError) as err:
            ps.table_path(tmp_path, name)
        assert str(err.value) == f"{name!r} isn't a table name like silver.orders"


def test_table_path_refuses_a_tables_folder_that_is_a_link(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    try:
        (tmp_path / "tables").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ValueError) as err:
        ps.table_path(tmp_path, "orders")
    assert str(err.value) == "'orders' goes through a link; exercise tables are plain folders"


def test_table_path_refuses_a_table_folder_that_is_a_link(tmp_path):
    (tmp_path / "tables" / "silver").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    try:
        (tmp_path / "tables" / "silver" / "orders").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ValueError, match="goes through a link"):
        ps.table_path(tmp_path, "silver.orders")


def test_table_path_a_plain_folder_on_the_way_is_fine(tmp_path):
    (tmp_path / "tables" / "silver" / "orders").mkdir(parents=True)
    assert ps.table_path(tmp_path, "silver.orders") == (tmp_path / "tables" / "silver" / "orders").resolve()


# ---- Writer options

def test_mode_names(spark):
    w = ps.Writer(None, spark.work)
    assert w._mode == "error"
    for given, mode in (("OVERWRITE", "overwrite"), ("Append", "append"), ("ignore", "ignore"),
                        ("errorIfExists", "error"), ("default", "error"), ("error", "error")):
        assert w.mode(given) is w and w._mode == mode
    with pytest.raises(ValueError) as err:
        w.mode("upsert")
    assert str(err.value) == "Unknown save mode 'upsert'; use overwrite, append, ignore or error"


def test_format_option_options_and_partition_by(spark):
    w = ps.Writer(None, spark.work)
    assert w.format("DELTA") is w and w.partitionBy("a", "b") is w
    with pytest.raises(ValueError) as err:
        w.format("parquet")
    assert str(err.value) == "Exercises write Delta tables: use format('delta') or leave it out"
    assert w.option("MergeSchema", "TRUE") is w and w._options == {"mergeschema": "TRUE"}
    assert w.options(overwriteSchema=1, replaceWhere="id = 1") is w
    assert w._options == {"mergeschema": "TRUE", "overwriteschema": 1, "replacewhere": "id = 1"}
    assert w._flag("mergeschema") and w._flag("overwriteschema") and not w._flag("replacewhere")
    assert not w._flag("missing")
    for value in ("false", "0", "yes", False):
        assert not w.option("x", value)._flag("x")
    assert w.option("x", True)._flag("x")


# ---- saveAsTable

def test_a_new_table_is_written_in_any_mode(spark):
    for n, mode in enumerate(("error", "overwrite", "append", "ignore")):
        ps.Writer(_df(spark, [(n, "x")]), spark.work).mode(mode).saveAsTable(f"silver.new{n}")
        assert _rows(spark.work, f"silver.new{n}") == [(n, "x")]


def test_an_existing_table_in_error_mode_is_refused(spark):
    with pytest.raises(ValueError) as err:
        ps.Writer(_df(spark, [(9, "z")]), spark.work).saveAsTable("silver.t")
    assert str(err.value) == "Table silver.t already exists; use .mode('overwrite') or .mode('append')"
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "b")]


def test_ignore_leaves_an_existing_table(spark):
    ps.Writer(_df(spark, [(9, "z")]), spark.work).mode("ignore").saveAsTable("silver.t")
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "b")]
    assert deltalake.DeltaTable(str(ps.table_path(spark.work, "silver.t"))).version() == 0


def test_append_and_overwrite(spark):
    ps.Writer(_df(spark, [(3, "c")]), spark.work).mode("append").saveAsTable("silver.t")
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "b"), (3, "c")]
    ps.Writer(_df(spark, [(4, "d")]), spark.work).mode("overwrite").saveAsTable("silver.t")
    assert _rows(spark.work, "silver.t") == [(4, "d")]


def test_a_new_column_needs_merge_schema_on_append(spark):
    extra = _df(spark, [(3, "c", 1.5)], ("id", "s", "x"))
    with pytest.raises(Exception, match="(?i)schema"):
        ps.Writer(extra, spark.work).mode("append").saveAsTable("silver.t")
    with pytest.raises(Exception, match="(?i)schema"):
        ps.Writer(extra, spark.work).mode("append").option("overwriteSchema", "true").saveAsTable("silver.t")
    ps.Writer(extra, spark.work).mode("append").option("mergeSchema", "true").saveAsTable("silver.t")
    assert _rows(spark.work, "silver.t") == [(1, "a", None), (2, "b", None), (3, "c", 1.5)]


def test_a_new_schema_needs_overwrite_schema_on_overwrite(spark):
    other = _df(spark, [(7.5,)], ("y",))
    with pytest.raises(Exception, match="(?i)schema"):
        ps.Writer(other, spark.work).mode("overwrite").saveAsTable("silver.t")
    with pytest.raises(Exception, match="(?i)schema"):
        ps.Writer(other, spark.work).mode("overwrite").option("mergeSchema", "true").saveAsTable("silver.t")
    ps.Writer(other, spark.work).mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver.t")
    assert _rows(spark.work, "silver.t") == [(7.5,)]


def test_replace_where_on_an_append_says_so_and_appends(spark, capsys):
    ps.Writer(_df(spark, [(5, "e")]), spark.work).mode("append").option("replaceWhere", "id = 9").saveAsTable("silver.t")
    assert capsys.readouterr().out == "Note: replaceWhere only applies to an overwrite; this append adds the rows, as Delta does.\n"
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "b"), (5, "e")]


def test_replace_where_on_a_new_table_writes_every_row_quietly(spark, capsys):
    ps.Writer(_df(spark, [(5, "e"), (6, "f")]), spark.work).mode("overwrite").option(
        "replaceWhere", "id = 5").saveAsTable("silver.fresh")
    assert capsys.readouterr().out == ""
    assert _rows(spark.work, "silver.fresh") == [(5, "e"), (6, "f")]


def test_replace_where_with_overwrite_schema_keeps_the_schema(spark, capsys):
    ps.Writer(_df(spark, [(2, "B")]), spark.work).mode("overwrite").option("replaceWhere", "id = 2").option(
        "overwriteSchema", "true").saveAsTable("silver.t")
    assert capsys.readouterr().out == ("Note: overwriteSchema doesn't apply with replaceWhere; the table keeps its "
                                       "schema, as in Delta.\n")
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "B")]


def test_replace_where_without_overwrite_schema_is_quiet(spark, capsys):
    ps.Writer(_df(spark, [(2, "B")]), spark.work).mode("overwrite").option("replaceWhere", "id = 2").saveAsTable("silver.t")
    assert capsys.readouterr().out == ""
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "B")]


def test_replace_where_a_row_outside_says_mismatch(spark):
    with pytest.raises(ValueError, match="DELTA_REPLACE_WHERE_MISMATCH"):
        ps.Writer(_df(spark, [(3, "c")]), spark.work).mode("overwrite").option("replaceWhere", "id = 2").saveAsTable(
            "silver.t")
    assert _rows(spark.work, "silver.t") == [(1, "a"), (2, "b")]


def test_replace_where_a_write_that_fails_its_check_is_the_mismatch(spark, monkeypatch):
    monkeypatch.setattr(ps.spark_replace_where, "check_rows", lambda *a: None)
    with pytest.raises(ValueError, match="DELTA_REPLACE_WHERE_MISMATCH"):
        ps.Writer(_df(spark, [(3, "c")]), spark.work).mode("overwrite").option("replaceWhere", "id = 2").saveAsTable(
            "silver.t")


def test_replace_where_another_delta_error_is_raised_as_it_is(spark, monkeypatch):
    from deltalake.exceptions import DeltaError

    def fail(*a, **k):
        raise DeltaError("disk on fire")
    monkeypatch.setattr(ps.deltalake, "write_deltalake", fail)
    with pytest.raises(DeltaError, match="^disk on fire$"):
        ps.Writer(_df(spark, [(2, "c")]), spark.work).mode("overwrite").option("replaceWhere", "id = 2").saveAsTable(
            "silver.t")


def test_save_as_table_passes_plain_paths_and_the_modes(spark, monkeypatch):
    calls = []
    monkeypatch.setattr(ps.deltalake, "write_deltalake", lambda *a, **k: calls.append((a, k)))
    for mode, opt in (("append", None), ("overwrite", None), ("ignore", None), ("error", None),
                      ("append", "mergeSchema"), ("overwrite", "overwriteSchema")):
        w = ps.Writer(_df(spark, [(1, "a")]), spark.work).mode(mode)
        if opt:
            w.option(opt, "1")
        w.saveAsTable(f"silver.{mode}{opt or ''}")
    got = [(a[0], k["mode"], k["schema_mode"]) for a, k in calls]
    assert got == [(str(ps.table_path(spark.work, "silver.append")), "append", None),
                   (str(ps.table_path(spark.work, "silver.overwrite")), "overwrite", None),
                   (str(ps.table_path(spark.work, "silver.ignore")), "overwrite", None),
                   (str(ps.table_path(spark.work, "silver.error")), "overwrite", None),
                   (str(ps.table_path(spark.work, "silver.appendmergeSchema")), "append", "merge"),
                   (str(ps.table_path(spark.work, "silver.overwriteoverwriteSchema")), "overwrite", "overwrite")]
    assert all(isinstance(a[0], str) and a[0].startswith(str((spark.work / "tables").resolve())) for a, _ in calls)
    assert all(isinstance(a[1], pa.Table) for a, _ in calls)


# ---- Reader and the session

def test_reader_format_and_a_missing_table(spark):
    r = spark.read
    assert isinstance(r, ps.Reader) and r.format("Delta") is r
    with pytest.raises(ValueError) as err:
        r.format("csv")
    assert str(err.value) == "Exercises read Delta tables: use spark.read.table(name)"
    with pytest.raises(ValueError) as err:
        spark.table("silver.nope")
    assert str(err.value) == "There's no table silver.nope"
    (spark.work / "tables" / "silver" / "empty").mkdir()
    with pytest.raises(ValueError, match="There's no table silver.empty"):
        spark.read.table("silver.empty")


def test_spark_table_reads_the_rows(spark):
    assert sorted(tuple(r) for r in spark.table("silver.t").collect()) == [(1, "a"), (2, "b")]


def test_settings():
    got = ps._settings()
    assert got["default_null_order"] == "nulls_first_on_asc_last_on_desc" and got["TimeZone"] == "UTC"
    if duckdb_major() >= 2:
        assert got["disabled_optimizers"] == "type_pushdown" and len(got) == 3
    else:
        assert len(got) == 2


def test_settings_by_major(monkeypatch):
    monkeypatch.setattr(ps, "duckdb_major", lambda: 2)
    assert ps._settings()["disabled_optimizers"] == "type_pushdown"
    monkeypatch.setattr(ps, "duckdb_major", lambda: 1)
    assert "disabled_optimizers" not in ps._settings()


def test_the_context_connects_in_memory_with_the_settings(monkeypatch):
    real, seen = duckdb.connect, []

    def connect(database, config=None):
        seen.append((database, dict(config)))
        return real(database, config=config)
    monkeypatch.setattr(ps.duckdb, "connect", connect)
    ps._Context("local")
    want = ps._settings()
    assert seen[0] == (":memory:", want)
    # Without the ICU extension the first connect is refused and the time zone is left out.
    assert seen[1:] in ([], [(":memory:", {k: v for k, v in want.items() if k != "TimeZone"})])


def test_the_context_without_time_zones_leaves_the_setting_out(monkeypatch):
    real, seen = duckdb.connect, []

    def connect(database, config=None):
        seen.append(dict(config))
        if "TimeZone" in config:
            raise duckdb.InvalidInputException("no ICU")
        return real(database, config=config)
    monkeypatch.setattr(ps.duckdb, "connect", connect)
    ctx = ps._Context("local")
    assert [("TimeZone" in c) for c in seen] == [True, False]
    assert seen[1]["default_null_order"] == "nulls_first_on_asc_last_on_desc"
    assert ctx._connection.execute("SELECT 1").fetchone() == (1,)


def test_read_delta_at_a_version(spark):
    ps.Writer(_df(spark, [(3, "c")]), spark.work).mode("append").saveAsTable("silver.t")
    path = ps.table_path(spark.work, "silver.t")
    assert ps.read_delta(path, 0).num_rows == 2
    assert ps.read_delta(path).num_rows == 3
    assert ps.read_delta(path, 1).num_rows == 3


def test_no_views_reads_plain_string_and_binary():
    t = pa.table({"s": pa.array(["a"], pa.string_view()), "b": pa.array([b"x"], pa.binary_view()),
                  "n": pa.array([1], pa.int64())},
                 schema=pa.schema([pa.field("s", pa.string_view(), False, {"k": "v"}),
                                   pa.field("b", pa.binary_view()), pa.field("n", pa.int64())], {"t": "m"}))
    got = ps._no_views(t)
    assert [f.type for f in got.schema] == [pa.string(), pa.binary(), pa.int64()]
    assert got.schema.field("s").nullable is False and got.schema.field("s").metadata == {b"k": b"v"}
    assert got.schema.field("b").nullable is True
    assert got.schema.metadata == {b"t": b"m"}
    assert got.to_pylist() == [{"s": "a", "b": b"x", "n": 1}]


def test_arrow_falls_back_to_arrow(spark):
    class Rel:
        def arrow(self):
            return "via arrow"

    class DF:
        relation = Rel()
    assert ps._arrow(DF()) == "via arrow"
    assert ps._arrow(_df(spark, [(1, "a")])).num_rows == 1


def test_read_stream_is_the_exercise_stream_reader(spark):
    assert isinstance(spark.readStream, ps.pyspark_stream.StreamReader)


def test_builder_returns_the_session(spark):
    b = ps._Builder(spark)
    assert b.appName("x") is b and b.config("a", "b", k=1) is b and b.getOrCreate() is spark


# ---- session() and namespace(), with what they change put back

@pytest.fixture
def restore(monkeypatch):
    import duckdb.experimental.spark.sql as spark_sql
    saved = dict(sys.modules)
    keep = {name: _dataframe.DataFrame.__dict__.get(name) for name in ("write", "writeStream", "isStreaming")}
    monkeypatch.setattr(spark_sql, "SparkSession", spark_sql.SparkSession)
    monkeypatch.setattr(pyspark_delta.DeltaTable, "_session", getattr(pyspark_delta.DeltaTable, "_session", None),
                        raising=False)
    monkeypatch.setattr(ps.AcademySession, "builder", ps.AcademySession.__dict__.get("builder"), raising=False)
    monkeypatch.setattr(ps, "_UTILS", None)
    yield
    for name, value in keep.items():
        if value is None:
            if name in _dataframe.DataFrame.__dict__:
                delattr(_dataframe.DataFrame, name)
        else:
            setattr(_dataframe.DataFrame, name, value)
    for name in [n for n in sys.modules if n not in saved]:
        del sys.modules[name]
    sys.modules.update(saved)


def test_session_binds_write_imports_and_delta(tmp_path, restore):
    pyspark_lab.prepare(tmp_path, SETUP)
    spark = ps.session(tmp_path)
    assert isinstance(spark, ps.AcademySession) and spark.work == tmp_path
    df = _df(spark, [(3, "c")])
    assert isinstance(df.write, ps.Writer) and df.write._work == tmp_path
    df.write.mode("append").saveAsTable("silver.t")
    assert _rows(tmp_path, "silver.t") == [(1, "a"), (2, "b"), (3, "c")]
    assert isinstance(df.writeStream, ps.pyspark_stream.StreamWriter)
    assert df.isStreaming is False
    import duckdb.experimental.spark as spark_pkg
    import duckdb.experimental.spark.sql as spark_sql
    import duckdb.experimental.spark.sql.functions as functions
    import duckdb.experimental.spark.sql.types as types
    assert sys.modules["pyspark"] is spark_pkg and sys.modules["pyspark.sql"] is spark_sql
    assert sys.modules["pyspark.sql.functions"] is functions and sys.modules["pyspark.sql.types"] is types
    assert sys.modules["pyspark.sql.window"] is ps.spark_compat.window_module
    assert spark_sql.SparkSession is ps.AcademySession
    assert spark_sql.SparkSession.builder.getOrCreate() is spark
    from delta.tables import DeltaTable
    assert DeltaTable is pyspark_delta.DeltaTable and sys.modules["delta"].tables is sys.modules["delta.tables"]
    assert pyspark_delta.DeltaTable._session is spark
    assert hasattr(spark_sql, "Row") and hasattr(spark_sql, "Window")


def test_namespace_installs_notebookutils_once(tmp_path, restore, monkeypatch):
    spark = ps.AcademySession(tmp_path)
    calls = []
    real = notebookutils_lab.install

    def install(work, ns):
        calls.append(work)
        return real(work, ns)
    monkeypatch.setattr(ps.notebookutils_lab, "install", install)
    first = ps.namespace(spark)
    assert set(first) == {"__name__", "spark", "notebookutils", "mssparkutils"}
    assert first["__name__"] == "__main__" and first["spark"] is spark
    assert first["notebookutils"] is first["mssparkutils"] is sys.modules["notebookutils"]
    second = ps.namespace(spark)
    assert second["notebookutils"] is first["notebookutils"] and calls == [tmp_path]
    assert second is not first and second["spark"] is spark


def test_table_path_a_link_the_first_check_missed_still_cant_leave_tables(tmp_path, monkeypatch):
    """The second guard: were a link not seen on the way (a race, or a link kind the first check doesn't
    know), the resolved path must still be under ``tables/``."""
    (tmp_path / "tables").mkdir()
    (tmp_path / "elsewhere").mkdir()
    try:
        (tmp_path / "tables" / "silver").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    monkeypatch.setattr(type(tmp_path), "is_symlink", lambda self: False)
    monkeypatch.setattr(type(tmp_path), "is_junction", lambda self: False, raising=False)
    with pytest.raises(ValueError) as err:
        ps.table_path(tmp_path, "silver.orders")
    assert str(err.value) == "'silver.orders' isn't a table in this exercise"
