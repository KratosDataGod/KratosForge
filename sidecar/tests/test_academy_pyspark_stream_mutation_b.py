"""Mutation tests for academy/pyspark_stream.py (batch 455): readStream and writeStream in this process
with a stand-in spark session (a workspace folder and a DuckDB connection), so no learner code runs.
The tables are the test's own, written with delta-rs."""

from __future__ import annotations

import os
import types

import deltalake
import duckdb
import pyarrow as pa
import pytest
from duckdb.experimental.spark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType

from fabric_desktop.academy import pyspark_shim
from fabric_desktop.academy import pyspark_stream as ps

SCHEMA = StructType([StructField("id", IntegerType()), StructField("amount", DoubleType())])


@pytest.fixture
def spark(tmp_path):
    con = duckdb.connect()
    path = pyspark_shim.table_path(tmp_path, "bronze.orders")
    path.parent.mkdir(parents=True)
    deltalake.write_deltalake(str(path), pa.table({"id": [1, 2, 3], "amount": [10, 20, 5]}))
    yield types.SimpleNamespace(work=tmp_path, conn=con)
    con.close()


def _files(spark, **files):
    for rel, text in files.items():
        p = spark.work / "Files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _read(spark):
    return ps.StreamReader(spark)


def _write(df, spark):
    return ps.StreamWriter(df, spark)


def _table(spark, name):
    return sorted(deltalake.DeltaTable(str(pyspark_shim.table_path(spark.work, name))).to_pyarrow_table().to_pylist(),
                  key=str)


def _to(spark, df, name="bronze.out", cp="Files/cp/a", **kw):
    return _write(df, spark).option("checkpointLocation", cp).toTable(name, **kw)


# ---- small helpers


def test_flag_is_true_only_for_true_or_its_text():
    assert ps._flag(True) and ps._flag(" TRUE ") and not ps._flag(False) and not ps._flag("yes")


def test_quote_doubles_quotes():
    assert ps._quote('a"b') == '"a""b"'


def test_a_path_outside_the_work_folder_is_refused(tmp_path):
    with pytest.raises(PermissionError, match="That path isn't in this exercise."):
        ps._inside(tmp_path / "w", [str(tmp_path / "elsewhere"), "x"])


def test_a_link_on_the_way_is_refused(tmp_path):
    work = tmp_path / "w"
    (work / "Files").mkdir(parents=True)
    os.symlink(tmp_path, work / "Files" / "l")
    with pytest.raises(PermissionError, match="l is a link"):
        ps._inside(work, ["Files", "l", "x"])


def test_conform_keeps_a_batch_already_in_the_streams_columns():
    data = pa.table({"a": pa.array([1], pa.int64())})
    assert ps._conform(data, None, "s") is data
    assert ps._conform(data, data.schema, "s") is data


def test_conform_casts_and_fills_missing_columns():
    want = pa.schema([("a", pa.int64()), ("z", pa.string())])
    got = ps._conform(pa.table({"a": pa.array([1, 2], pa.int32())}), want, "s")
    assert got.schema == want and got.to_pylist() == [{"a": 1, "z": None}, {"a": 2, "z": None}]


def test_conform_refuses_a_column_that_wont_cast():
    want = pa.schema([("a", pa.int64())])
    with pytest.raises(ValueError, match="New data in s has column a as string"):
        ps._conform(pa.table({"a": ["x"]}), want, "s")


# ---- the query


def test_a_finished_query_reports_stopped_and_awaits_at_once():
    q = ps.StreamingQuery("q", [])
    assert q.status == {"message": "Stopped", "isDataAvailable": False, "isTriggerActive": False}
    assert q.awaitTermination() is True and q.awaitTermination(5) is True and q.lastProgress is None


# ---- the reader


def test_reader_and_writer_methods_chain(spark):
    r = _read(spark)
    assert r.format("csv") is r and r.option("a", 1) is r and r.options(b=2) is r and r.schema(SCHEMA) is r
    w = _write(None, spark)
    assert (w.format("delta") is w and w.option("a", 1) is w and w.options(b=2) is w
            and w.outputMode("append") is w and w.queryName("q") is w and w.partitionBy("a") is w
            and w.trigger(availableNow=True) is w and w.foreachBatch(print) is w)


def test_max_files_per_trigger_is_noted_and_otherwise_nothing_is(spark, capsys):
    _files(spark, **{"landing/a.csv": "id,amount\n1,10\n"})
    _read(spark).option("header", "true").csv("Files/landing")
    assert "maxFilesPerTrigger" not in capsys.readouterr().out
    _read(spark).option("header", "true").option("maxFilesPerTrigger", 1).csv("Files/landing")
    assert "maxFilesPerTrigger doesn't apply here" in capsys.readouterr().out


def test_csv_without_a_schema_keeps_the_one_set_before(spark):
    _files(spark, **{"landing/a.csv": "1,10\n"})
    df = _read(spark).schema(SCHEMA).csv("Files/landing")
    assert df.columns == ["id", "amount"]
    _to(spark, df)
    assert _table(spark, "bronze.out") == [{"id": 1, "amount": 10.0}]


def test_csv_without_any_schema_reads_its_header(spark):
    _files(spark, **{"landing/a.csv": "x,y\n1,2\n"})
    assert _read(spark).option("header", "true").csv("Files/landing").columns == ["x", "y"]


def test_parquet_streams_a_folder_of_parquet_files(spark):
    folder = spark.work / "Files" / "pq"
    folder.mkdir(parents=True)
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"k": [1, 2]}), str(folder / "a.parquet"))
    df = _read(spark).parquet("Files/pq")
    assert df.columns == ["k"]
    q = _to(spark, df)
    assert q.lastProgress["numInputRows"] == 2


def test_json_with_a_schema_casts_and_fills_its_columns(spark):
    _files(spark, **{"landing/a.json": '{"id": 1, "extra": "x"}\n{"id": 2, "extra": "y"}\n'})
    schema = StructType([StructField("id", IntegerType()), StructField("note", StringType())])
    df = _read(spark).json("Files/landing", schema=schema)
    assert df.columns == ["id", "note"]
    _to(spark, df)
    assert _table(spark, "bronze.out") == [{"id": 1, "note": None}, {"id": 2, "note": None}]


def test_an_empty_folder_with_a_schema_takes_its_columns_from_the_schema(spark, capsys):
    (spark.work / "Files" / "empty").mkdir(parents=True)
    df = _read(spark).schema(SCHEMA).csv("Files/empty")
    assert df.columns == ["id", "amount"]
    _to(spark, df)
    assert "Stream: nothing new (no new files)." in capsys.readouterr().out


def test_a_missing_folder_with_a_schema_has_nothing_new(spark, capsys):
    df = _read(spark).schema(SCHEMA).csv("Files/not_yet")
    _to(spark, df)
    assert "Stream: nothing new (no new files)." in capsys.readouterr().out


def test_only_files_of_the_format_are_read(spark):
    _files(spark, **{"landing/a.csv": "id,amount\n1,10\n", "landing/notes.txt": "id,amount\n9,9\n"})
    q = _to(spark, _read(spark).option("header", "true").csv("Files/landing"))
    assert q.lastProgress["numInputRows"] == 1


def test_two_new_files_are_one_batch(spark):
    _files(spark, **{"landing/a.csv": "id,amount\n1,10\n", "landing/b.csv": "id,amount\n2,20\n"})
    q = _to(spark, _read(spark).option("header", "true").csv("Files/landing"))
    assert q.lastProgress["numInputRows"] == 2
    assert sorted(r["id"] for r in _table(spark, "bronze.out")) == [1, 2]


def test_a_file_key_is_its_path_in_the_folder_or_its_name(spark):
    _files(spark, **{"landing/sub/b.csv": "id,amount\n2,20\n", "one/a.csv": "id,amount\n1,10\n"})
    q = _to(spark, _read(spark).option("header", "true").csv("Files/landing"))
    assert q.lastProgress["sources"][0]["endOffset"]["files"][0].startswith("sub/b.csv|")
    q = _to(spark, _read(spark).option("header", "true").csv("Files/one/a.csv"), cp="Files/cp/b")
    assert q.lastProgress["sources"][0]["endOffset"]["files"][0].startswith("a.csv|")


@pytest.mark.parametrize("path", ["Files/x", "Tables/a/b/c/d", "Tables"])
def test_a_delta_load_must_name_a_table(spark, path):
    with pytest.raises(ValueError, match="a Delta stream reads a table"):
        _read(spark).load(path)


def test_a_delta_load_of_a_tables_path_reads_that_table(spark):
    assert _read(spark).load("Tables/bronze/orders").columns == ["id", "amount"]


# ---- Delta sources


def test_a_commit_after_the_first_run_is_the_next_batch_and_then_nothing(spark, capsys):
    df = _read(spark).table("bronze.orders")
    first = _to(spark, df)
    assert first.lastProgress["batchId"] == 0
    deltalake.write_deltalake(str(pyspark_shim.table_path(spark.work, "bronze.orders")),
                              pa.table({"id": [4], "amount": [7]}), mode="append")
    second = _to(spark, _read(spark).table("bronze.orders"))
    assert second.lastProgress["batchId"] == 1 and second.lastProgress["numInputRows"] == 1
    capsys.readouterr()
    third = _to(spark, _read(spark).table("bronze.orders"))
    assert third.recentProgress == []
    assert capsys.readouterr().out.strip() == "Stream: nothing new (no new commits after version 1)."


# ---- the writer


def test_a_named_query_says_its_name_and_the_rows_it_wrote(spark, capsys):
    q = _to(spark, _read(spark).table("bronze.orders"), queryName="orders")
    assert q.name == "orders" and q.lastProgress["sink"]["numOutputRows"] == 3
    assert "Stream orders batch 0: 3 rows from version 0 (the table as it is)." in capsys.readouterr().out


def test_an_unnamed_query_says_stream(spark, capsys):
    _to(spark, _read(spark).table("bronze.orders"))
    assert capsys.readouterr().out.startswith("Stream batch 0: 3 rows")


def test_foreach_batch_has_no_output_row_count(spark):
    seen = []
    q = (_write(_read(spark).table("bronze.orders"), spark).foreachBatch(lambda df, i: seen.append(i))
         .option("checkpointLocation", "Files/cp/f").start())
    assert seen == [0] and q.lastProgress["sink"]["numOutputRows"] is None


def test_the_duration_is_in_milliseconds(spark, monkeypatch):
    clock = iter([10.0, 12.5])
    monkeypatch.setattr(ps.time, "monotonic", lambda: next(clock))
    q = _to(spark, _read(spark).table("bronze.orders"))
    assert q.lastProgress["durationMs"] == {"triggerExecution": 2500}


def test_processing_time_is_noted_and_available_now_isnt(spark, capsys):
    _write(_read(spark).table("bronze.orders"), spark).trigger(availableNow=True) \
        .option("checkpointLocation", "Files/cp/a").toTable("bronze.out")
    assert "runs as availableNow" not in capsys.readouterr().out
    _write(_read(spark).table("bronze.orders"), spark).trigger(processingTime="5 seconds") \
        .option("checkpointLocation", "Files/cp/b").toTable("bronze.out2")
    assert "trigger(processingTime='5 seconds') runs as availableNow here" in capsys.readouterr().out


def test_writer_refusals_say_why(spark):
    w = _write(_read(spark).table("bronze.orders"), spark)
    with pytest.raises(NotImplementedError, match="writeStream.foreach"):
        w.foreach(print)
    with pytest.raises(ValueError, match="Unknown output mode 'bogus'"):
        w.outputMode("bogus")
    with pytest.raises(ValueError, match="Exercise tables are Delta tables"):
        w.toTable("bronze.out", format="parquet")
    with pytest.raises(ValueError, match=r'Set option\("checkpointLocation"'):
        w.toTable("bronze.out")
    with pytest.raises(ValueError, match="a stream writes to a table"):
        w.option("checkpointLocation", "Files/cp/x").start("Tables/a/b/c")


def test_start_with_a_format_or_a_tables_path(spark, capsys):
    q = _write(_read(spark).table("bronze.orders"), spark).start(format="console", queryName="c")
    assert q.name == "c" and "Batch: 0" in capsys.readouterr().out
    _write(_read(spark).table("bronze.orders"), spark).option("checkpointLocation", "Files/cp/p") \
        .start("Tables/bronze/copied")
    assert len(_table(spark, "bronze.copied")) == 3


def test_two_streams_in_one_query_are_refused(spark):
    a, b = _read(spark).table("bronze.orders"), _read(spark).table("bronze.orders")
    with pytest.raises(NotImplementedError, match="Joining two streams"):
        _to(spark, a.union(b))
