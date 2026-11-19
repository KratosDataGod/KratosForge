"""Structured streaming on this PC (spark_stream.py): the writer's keyword forms (toTable and start with
outputMode, format and queryName), partitionBy, mergeSchema passed to the table, a single-file source, a
checkpoint that belongs to another source, and a plan that can't be explained. All on local disk."""

from __future__ import annotations

import types

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from deltalake import DeltaTable

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


def test_to_table_with_output_mode_query_name_partition_by_and_merge_schema(k, lake, tmp_path):  # noqa: F811
    code = ("from pyspark.sql import functions as F\n"
            "q = (spark.readStream.table('dbo.bronze').withColumn('extra', F.lit('x')).writeStream"
            f".option('checkpointLocation', {str(tmp_path / 'cp')!r}).partitionBy('id')"
            ".toTable('dbo.silver', outputMode='append', queryName='load', mergeSchema='true'))\n"
            "print(q.name, q.lastProgress['numInputRows'])\n")
    status, text = outputs(k, code)
    assert status == "ok" and "Stream load batch 0: 2 rows" in text and "load 2" in text, text
    rows = DeltaTable(str(lake / "dbo" / "silver")).to_pyarrow_table().to_pylist()
    assert sorted((r["id"], r["extra"]) for r in rows) == [(1, "x"), (2, "x")]


def test_start_with_format_output_mode_and_query_name_keywords(k, tmp_path):
    pq.write_table(pa.table({"id": [1, 2, 3]}), tmp_path / "one.parquet")
    code = (f"src = spark.readStream.parquet({str(tmp_path / 'one.parquet')!r})\n"   # one file, not a folder
            "q = src.writeStream.start(format='memory', outputMode='append', queryName='seen')\n"
            "print(spark.sql('SELECT count(*) AS n FROM seen').first().n, q.name)\n")
    status, text = outputs(k, code)
    assert status == "ok" and text.strip().endswith("3 seen"), text


def test_a_checkpoint_of_another_source_is_refused(k, tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        pq.write_table(pa.table({"id": [1]}), tmp_path / name / "one.parquet")
    cp = str(tmp_path / "cp")
    first = (f"spark.readStream.parquet({str(tmp_path / 'a')!r}).writeStream.format('console')"
             f".option('checkpointLocation', {cp!r}).start()\n")
    assert outputs(k, first)[0] == "ok"
    status, text = outputs(k, first.replace(str(tmp_path / "a"), str(tmp_path / "b")))
    assert status == "error" and "belongs to another source" in text and "Use a new checkpointLocation" in text, text


def test_sources_of_a_plan_that_cant_be_explained_falls_back_to_the_sql(monkeypatch):
    src = object()
    monkeypatch.setattr(spark_stream, "_SOURCES", {"kf_stream_view_1": src, "kf_other": object()})

    class Relation:
        def explain(self):
            raise RuntimeError("no plan")

        def sql_query(self):
            return "SELECT * FROM kf_stream_view_1"

    assert spark_stream._sources_of(types.SimpleNamespace(relation=Relation())) == [src]
