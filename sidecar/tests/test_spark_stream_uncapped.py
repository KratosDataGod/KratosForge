"""spark_stream.py paths that the uncapped mutation run (batch 218, docs/quality/mutation-testing.md)
showed no test checked."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_desktop import spark_stream
from fabric_desktop.notebook import Kernel
from tests.test_spark_stream import lake  # noqa: F401 - a fixture


@pytest.fixture
def k(lake):  # noqa: F811
    return Kernel()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "incoming"
    d.mkdir()
    pq.write_table(pa.table({"id": [1, 2], "name": ["a", "b"]}), d / "one.parquet")
    return d


def outputs(kernel, code):
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    return out["status"], text


def test_a_file_is_known_by_its_path_under_the_folder_or_its_own_name(tmp_path):
    folder = tmp_path / "in"
    (folder / "sub").mkdir(parents=True)
    f = folder / "sub" / "a.csv"
    f.write_text("id\n1\n", encoding="utf-8")
    st = f.stat()
    assert spark_stream._file_key(f, folder) == f"sub/a.csv|{st.st_size}|{st.st_mtime_ns}"
    assert spark_stream._file_key(f, f) == f"a.csv|{st.st_size}|{st.st_mtime_ns}"


def test_a_memory_sink_reports_the_rows_it_wrote(k, folder):
    status, text = outputs(k, f"src = spark.readStream.format('parquet').load({str(folder)!r})\n"
                              "q = src.writeStream.format('memory').queryName('seen').start()\n"
                              "print('rows', q.lastProgress['sink']['numOutputRows'])\n")
    assert status == "ok" and "rows 2" in text, text


def test_a_stream_of_the_change_data_feed_runs_in_fabric(k):
    status, text = outputs(k, "spark.readStream.option('readChangeFeed', 'true').table('dbo.bronze')\n")
    assert status == "error" and "change data feed (readChangeFeed) runs in Fabric" in text, text


def test_a_stream_view_is_named_with_12_hex_digits():
    name = spark_stream._view_name()
    assert name.startswith("__stream_") and len(name) == len("__stream_") + 12
    int(name[len("__stream_"):], 16)
