"""Structured streaming on this PC (spark_stream.py), the paths the main tests don't reach: the
reader's and writer's other formats and options, the console, memory and folder sinks, the asks
that run only in Fabric, and a new batch whose columns don't fit the stream. All on local disk."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import write_deltalake

from fabric_desktop import spark_stream
from fabric_desktop.notebook import Kernel
from tests.test_spark_stream import lake  # noqa: F401 - a fixture


def outputs(kernel, code):
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


@pytest.fixture
def k(lake):  # noqa: F811
    return Kernel()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    pq.write_table(pa.table({"id": [1, 2], "name": ["a", "b"]}), d / "one.parquet")
    return d


def stream(folder, fmt="parquet"):
    return f"src = spark.readStream.format({fmt!r}).load({str(folder)!r})\n"


# ---- sinks on this PC


def test_a_memory_sink_makes_its_view_then_adds_to_it(k, folder, tmp_path):
    code = stream(folder) + "q = src.writeStream.format('memory').queryName('seen').option('checkpointLocation', " \
        f"{str(tmp_path / 'cp')!r}).start()\nprint(spark.sql('SELECT count(*) AS n FROM seen').first().n)\n"
    status, text = outputs(k, code)
    assert status == "ok" and text.strip().endswith("2"), text
    pq.write_table(pa.table({"id": [3], "name": ["c"]}), folder / "two.parquet")
    status, text = outputs(k, code)
    assert status == "ok" and text.strip().endswith("3"), text


def test_a_console_sink_prints_each_batch(k, folder):
    status, text = outputs(k, stream(folder) + "src.writeStream.format('console').start()\n")
    assert status == "ok" and "Batch: 0" in text and "batch 0: 2 rows" in text, text


@pytest.mark.parametrize("fmt", ["parquet", "csv", "json"])
def test_a_folder_sink_writes_each_batch_as_a_file(k, folder, tmp_path, fmt):
    out = tmp_path / f"out-{fmt}"
    status, text = outputs(k, stream(folder) + f"src.writeStream.format({fmt!r}).option('checkpointLocation', "
                                               f"{str(tmp_path / ('cp-' + fmt))!r}).start({str(out)!r})\n")
    assert status == "ok", text
    assert [p.suffix for p in out.iterdir()] == [f".{fmt}"]


def test_foreach_batch_gets_each_batch_and_its_id(k, folder, tmp_path):
    code = stream(folder) + ("got = []\nsrc.writeStream.foreachBatch(lambda df, i: got.append((i, df.count())))"
                             f".option('checkpointLocation', {str(tmp_path / 'cp')!r}).start()\nprint(got)\n")
    status, text = outputs(k, code)
    assert status == "ok" and "[(0, 2)]" in text, text


# ---- the reader's other ways in


def test_a_single_file_csv_json_and_text_stream(k, tmp_path):
    (tmp_path / "rows.csv").write_text("id,name\n1,a\n")
    (tmp_path / "rows.json").write_text('{"id": 1}\n')
    (tmp_path / "rows.txt").write_text("hello\n")
    code = (f"a = spark.readStream.options(header=True).csv({str(tmp_path / 'rows.csv')!r})\n"
            f"b = spark.readStream.json({str(tmp_path / 'rows.json')!r})\n"
            f"c = spark.readStream.text({str(tmp_path / 'rows.txt')!r})\n"
            "print(a.columns, b.columns, c.columns, a.isStreaming)\n")
    status, text = outputs(k, code)
    assert status == "ok" and "['id', 'name'] ['id'] ['value'] True" in text, text


def test_a_delta_folder_on_this_pc_streams_by_path(k, tmp_path):
    write_deltalake(str(tmp_path / "events"), pa.table({"id": [1, 2]}))
    code = (f"src = spark.readStream.format('delta').load({str(tmp_path / 'events')!r})\n"
            "src.writeStream.format('console').start()\n")
    status, text = outputs(k, code)
    assert status == "ok" and "batch 0: 2 rows" in text, text


def test_a_lakehouse_tables_path_streams_its_table(k, lake):  # noqa: F811
    status, text = outputs(k, "print(spark.readStream.format('delta').load('Tables/dbo/bronze').columns)\n")
    assert status == "ok" and "['id', 'amount']" in text, text


def test_max_files_per_trigger_is_noted_as_ignored_and_processing_time_runs_once(k, folder, tmp_path):
    code = ("from pyspark.sql.types import StructType, StructField, LongType, StringType\n"
            "src = spark.readStream.option('maxFilesPerTrigger', 1).schema(StructType([StructField('id', LongType()), "
            f"StructField('name', StringType())])).parquet({str(folder)!r})\n"
            f"src.writeStream.trigger(processingTime='10 seconds').format('console').start()\n")
    status, text = outputs(k, code)
    assert status == "ok" and "maxFilesPerTrigger is ignored" in text and "runs as availableNow" in text, text


# ---- what runs only in Fabric, or can't run at all


@pytest.mark.parametrize("code, says", [
    ("spark.readStream.load()", "readStream.load needs a path"),
    ("spark.readStream.format('kafka').load('topic')", "Streaming from 'kafka' runs in Fabric"),
    ("spark.readStream.option('startingVersion', 3).table('dbo.bronze')", "startingversion runs in Fabric"),
    ("SRC.writeStream.outputMode('everything')", "Unknown output mode 'everything'"),
    ("SRC.writeStream.outputMode('complete')", 'outputMode("complete") runs in Fabric'),
    ("SRC.writeStream.trigger(continuous='1 second')", "Continuous streams run in Fabric"),
    ("SRC.writeStream.foreach(print)", "writeStream.foreach (a row at a time) runs in Fabric"),
    ("SRC.writeStream.format('memory').start()", "A memory sink needs queryName"),
    ("SRC.writeStream.format('memory').queryName('bad name').start()", "can't be a view name"),
    ("SRC.writeStream.start('C:/elsewhere')", "a stream writes to a lakehouse table"),
    ("SRC.writeStream.format('kafka').start('topic')", "Streaming into 'kafka' at topic runs in Fabric"),
    ("SRC.writeStream.toTable('dbo.x', format='parquet')", "Lakehouse tables are Delta tables"),
    ("SRC.writeStream.toTable('dbo.x')", 'Set option("checkpointLocation", ...)'),
    ("spark.range(3).writeStream.format('console').start()", "writeStream is for DataFrames read with"),
])
def test_asks_a_stream_on_this_pc_cant_do_say_why(k, folder, code, says):
    status, text = outputs(k, stream(folder) + code.replace("SRC", "src") + "\n")
    assert status == "error" and says in text, text


def test_a_folder_with_no_files_yet_needs_a_schema(k, tmp_path):
    (tmp_path / "empty").mkdir()
    status, text = outputs(k, f"spark.readStream.parquet({str(tmp_path / 'empty')!r})\n")
    assert status == "error" and "has no parquet files yet" in text, text


# ---- a batch that doesn't fit the stream


def test_a_batch_takes_the_streams_columns_and_refuses_a_type_it_cant_read():
    want = pa.schema([("id", pa.int64()), ("name", pa.string())])
    fitted = spark_stream._conform(pa.table({"id": pa.array([1], pa.int32())}), want, "src")
    assert fitted.schema == want and fitted.column("name").to_pylist() == [None]
    with pytest.raises(ValueError, match="has column id as string, but the stream reads it as int64"):
        spark_stream._conform(pa.table({"id": ["x"], "name": ["a"]}), want, "src")


def test_a_query_that_already_ran_reports_it_stopped(capsys):
    q = spark_stream.StreamingQuery("orders", [])
    assert q.lastProgress is None and q.status["message"] == "Stopped" and q.exception() is None
    assert q.awaitTermination(5) is True and q.processAllAvailable() is None and q.stop() is None
    q.explain()
    assert "trigger(availableNow=True)" in capsys.readouterr().out


def test_numbers_that_cant_be_read_are_none():
    assert spark_stream._int("many") is None and spark_stream._int(None) is None and spark_stream._int("4") == 4
