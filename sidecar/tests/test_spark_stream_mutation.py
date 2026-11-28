"""spark_stream paths that mutation testing (batch 20, docs/quality/mutation-testing.md) showed no test
checked: what each sink reports as written, the query's progress and status, the batch message,
files a folder stream skips, folder sinks, a Tables/ path sink and two streams joined."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import spark_stream
from tests.test_spark_stream import lake  # noqa: F401 - a fixture
from tests.test_spark_stream_edges import folder, k, outputs, stream  # noqa: F401 - fixtures


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def ok(kernel, code):
    status, text = outputs(kernel, code)
    assert status == "ok", text
    return text


def progress(kernel):
    return eval(ok(kernel, "print(repr(q.lastProgress))").strip())  # noqa: S307 - our own repr of a dict


# ---- what each sink reports


def test_a_table_sink_reports_its_rows_its_name_and_a_short_duration(k, lake):  # noqa: F811
    ok(k, "q = (spark.readStream.table('dbo.bronze').writeStream.option('checkpointLocation', 'Files/cp/t')"
          ".toTable('dbo.silver', queryName='silver_feed'))\n")
    p = progress(k)
    assert p["sink"] == {"description": "delta", "numOutputRows": 2}
    assert p["name"] == "silver_feed" and ok(k, "print(q.name)").strip() == "silver_feed"
    assert 0 <= p["durationMs"]["triggerExecution"] < 60_000


def test_the_trigger_time_is_in_milliseconds(k, lake, monkeypatch):  # noqa: F811
    ticks = iter([100.0, 102.5])
    monkeypatch.setattr(spark_stream, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    ok(k, "q = spark.readStream.table('dbo.bronze').writeStream.option('checkpointLocation', 'Files/cp/d')"
          ".toTable('dbo.silver')\n")
    assert progress(k)["durationMs"] == {"triggerExecution": 2500}


def test_without_a_query_name_the_table_sink_has_none(k, lake):  # noqa: F811
    ok(k, "q = spark.readStream.table('dbo.bronze').writeStream.option('checkpointLocation', 'Files/cp/u')"
          ".toTable('dbo.silver')\n")
    assert progress(k)["name"] is None


def test_a_console_sink_reports_its_rows(k, folder):  # noqa: F811
    ok(k, stream(folder) + "q = src.writeStream.format('console').start()\n")
    assert progress(k)["sink"] == {"description": "console", "numOutputRows": 2}


def test_foreach_batch_reports_no_row_count(k, folder, tmp_path):  # noqa: F811
    ok(k, stream(folder) + f"q = src.writeStream.option('checkpointLocation', {str(tmp_path / 'cp')!r})"
          ".foreachBatch(lambda df, i: None).start()\n")
    assert progress(k)["sink"]["numOutputRows"] is None


def test_a_folder_sink_makes_missing_folders_writes_again_and_names_its_files(k, folder, tmp_path):  # noqa: F811
    out = tmp_path / "out" / "nested" / "parts"
    code = stream(folder) + (f"q = src.writeStream.format('parquet').option('checkpointLocation', "
                             f"{str(tmp_path / 'cp2')!r}).start({str(out)!r})\n")
    ok(k, code)
    assert progress(k)["sink"] == {"description": "parquet", "numOutputRows": 2}
    pq.write_table(pa.table({"id": [3], "name": ["c"]}), folder / "two.parquet")
    ok(k, code)  # the folder is there now
    names = sorted(p.name for p in out.iterdir())
    assert len(names) == 2
    assert all(re.fullmatch(r"part-0000[01]-[0-9a-f]{8}\.parquet", n) for n in names), names


def test_a_delta_sink_at_a_tables_path_writes_that_table(k, lake):  # noqa: F811
    ok(k, "q = (spark.readStream.table('dbo.bronze').writeStream.format('delta')"
          ".option('checkpointLocation', 'Files/cp/p').start('Tables/dbo/by_path'))\n")
    assert sorted(r["id"] for r in DeltaTable(str(lake / "dbo" / "by_path")).to_pyarrow_table().to_pylist()) == [1, 2]


# ---- the run


def test_the_batch_message_names_the_versions_read(k, lake):  # noqa: F811
    code = ("q = spark.readStream.table('dbo.bronze').writeStream.option('checkpointLocation', 'Files/cp/m')"
            ".toTable('dbo.silver')\n")
    assert "from versions 0..0 (1 files)" in ok(k, code)
    write_deltalake(str(lake / "dbo" / "bronze"), pa.table({"id": [3], "amount": [30.0]}), mode="append")
    assert "from versions 1..1 (1 files)" in ok(k, code)


def test_a_folder_stream_reads_only_files_of_its_format(k, folder, tmp_path):  # noqa: F811
    (folder / "README.md").write_text("not data", encoding="utf-8")
    (folder / "nested.parquet").mkdir()  # a folder named like a file
    text = ok(k, stream(folder) + "q = src.writeStream.format('console').start()\n")
    assert "1 new file" in text and "batch 0: 2 rows" in text


def test_two_streams_joined_run_in_fabric(k, lake):  # noqa: F811
    status, text = outputs(k, "a = spark.readStream.table('dbo.bronze')\nb = spark.readStream.table('dbo.bronze')\n"
                              "a.union(b).writeStream.option('checkpointLocation', 'Files/cp/j').toTable('dbo.both')\n")
    assert status == "error" and "Joining two streams runs in Fabric" in text


# ---- the query and the helpers


def test_a_query_reports_its_last_progress_its_error_and_a_stopped_status():
    boom = RuntimeError("boom")
    q = spark_stream.StreamingQuery("orders", [{"batchId": 0}, {"batchId": 1}], boom)
    assert q.lastProgress == {"batchId": 1}
    assert q.exception() is boom
    assert q.status == {"message": "Stopped", "isDataAvailable": False, "isTriggerActive": False}


def test_writer_settings_chain():
    w = spark_stream.StreamWriter(None)
    assert w.queryName("feed") is w and w._name == "feed"
    assert w.options(checkpointLocation="cp", mergeSchema="true") is w
    assert w._options == {"checkpointlocation": "cp", "mergeschema": "true"}


def test_false_is_not_a_set_flag():
    assert spark_stream._flag(False) is False
    assert spark_stream._flag(True) is True and spark_stream._flag(" TRUE ") is True
    assert spark_stream._flag("false") is False and spark_stream._flag(None) is False


def test_an_unpartitioned_file_matches_every_row():
    assert spark_stream._partition({}).equals(pc.scalar(True))
