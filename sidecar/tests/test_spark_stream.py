"""Structured streaming on this PC (spark_stream.py): availableNow runs that pick up only what is new
since the checkpoint, from Delta tables and folders of files."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "bronze"), pa.table({"id": [1, 2], "amount": [10.0, 20.0]}))
    yield root
    lakehouse.attach(None)


def ids(lake, table):
    return sorted(r["id"] for r in DeltaTable(str(lake / "dbo" / table)).to_pyarrow_table().to_pylist())


def run(kernel, code):
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


STREAM = ("from pyspark.sql import functions as F\n"
          "q = (spark.readStream.table('dbo.bronze').withColumn('double', F.col('amount') * 2)\n"
          "     .writeStream.option('checkpointLocation', 'Files/checkpoints/silver')\n"
          "     .trigger(availableNow=True).toTable('dbo.silver'))\n"
          "q.awaitTermination()\n")


def test_a_delta_stream_takes_only_new_commits_each_run(lake, tmp_path):
    k = Kernel()
    assert "batch 0: 2 rows" in run(k, STREAM)
    assert ids(lake, "silver") == [1, 2]
    assert DeltaTable(str(lake / "dbo" / "silver")).to_pyarrow_table().column("double").to_pylist() == [20.0, 40.0]
    assert "nothing new" in run(k, STREAM)
    write_deltalake(str(lake / "dbo" / "bronze"), pa.table({"id": [3], "amount": [30.0]}), mode="append")
    assert "batch 1: 1 row from" in run(k, STREAM)
    assert ids(lake, "silver") == [1, 2, 3]
    # The checkpoint is kept on this PC, apart from Fabric's at the same path.
    assert (tmp_path / "stream-checkpoints" / "lh1" / "Files" / "checkpoints" / "silver").is_dir()
    # A new kernel (a restart) carries on from the checkpoint.
    assert "nothing new" in run(Kernel(), STREAM)
    assert run(k, "print(q.lastProgress['numInputRows'], q.isActive)").strip() == "1 False"


def test_rewritten_source_rows_stop_the_stream_unless_skipped(lake):
    k = Kernel()
    run(k, STREAM)
    write_deltalake(str(lake / "dbo" / "bronze"), pa.table({"id": [7], "amount": [1.0]}), mode="overwrite")
    out = k.run(STREAM)
    assert out["status"] == "error" and "skipChangeCommits" in str(out["outputs"])
    assert ids(lake, "silver") == [1, 2]
    skipping = STREAM.replace("spark.readStream.table", "spark.readStream.option('skipChangeCommits', 'true').table")
    run(k, skipping)
    assert ids(lake, "silver") == [1, 2]  # the rewrite is skipped whole, as in Spark
    DeltaTable(str(lake / "dbo" / "bronze")).update(predicate="id = 7", updates={"amount": "99.0"})
    write_deltalake(str(lake / "dbo" / "bronze"), pa.table({"id": [8], "amount": [8.0]}), mode="append")
    assert "1 row from" in run(k, skipping)
    assert ids(lake, "silver") == [1, 2, 8]


def test_compaction_passes_and_rows_compacted_before_the_run_still_arrive(lake):
    k = Kernel()
    run(k, STREAM)
    bronze = str(lake / "dbo" / "bronze")
    write_deltalake(bronze, pa.table({"id": [3], "amount": [30.0]}), mode="append")
    DeltaTable(bronze).optimize.compact()  # 3 is now inside the compacted file
    write_deltalake(bronze, pa.table({"id": [4], "amount": [40.0]}), mode="append")
    run(k, STREAM)
    assert ids(lake, "silver") == [1, 2, 3, 4]
    DeltaTable(bronze).optimize.compact()
    assert "nothing new" in run(k, STREAM) or ids(lake, "silver") == [1, 2, 3, 4]
    assert ids(lake, "silver") == [1, 2, 3, 4]


def test_a_partitioned_source_keeps_its_partition_column(lake):
    write_deltalake(str(lake / "dbo" / "sales"), pa.table({"id": [1], "region": ["EU"]}), partition_by=["region"])
    code = STREAM.replace("dbo.bronze", "dbo.sales").replace(".withColumn('double', F.col('amount') * 2)", "") \
        .replace("silver", "sales_copy")
    k = Kernel()
    run(k, code)
    write_deltalake(str(lake / "dbo" / "sales"), pa.table({"id": [2], "region": ["US"]}), mode="append",
                    partition_by=["region"])
    run(k, code)
    rows = DeltaTable(str(lake / "dbo" / "sales_copy")).to_pyarrow_table().to_pylist()
    assert sorted((r["id"], r["region"]) for r in rows) == [(1, "EU"), (2, "US")]


def test_a_folder_of_files_streams_new_files_into_foreach_batch_and_memory(tmp_path):
    landing = tmp_path / "landing"
    landing.mkdir()
    (landing / "a.csv").write_text("id,amount\n1,5\n2,6\n")
    code = (f"seen = globals().setdefault('seen', [])\n"
            f"df = spark.readStream.format('csv').option('header', 'true').load({str(landing)!r})\n"
            f"q = (df.writeStream.option('checkpointLocation', {str(tmp_path / 'cp')!r})"
            f".foreachBatch(lambda b, i: seen.append((i, b.count()))).start())\n"
            f"print(df.isStreaming, seen)")
    k = Kernel()
    assert run(k, code).strip().splitlines() == ["Stream batch 0: 2 rows from 1 new file.", "True [(0, 2)]"]
    (landing / "b.csv").write_text("id,amount\n3,7\n")
    assert run(k, code).strip().endswith("True [(0, 2), (1, 1)]")
    assert run(k, code).strip().splitlines() == ["Stream: nothing new (no new files).", "True [(0, 2), (1, 1)]"]
    mem = (f"m = spark.readStream.format('csv').option('header', 'true').load({str(landing)!r})\n"
           "m.writeStream.format('memory').queryName('landed').outputMode('append').start()\n"
           "print(spark.sql('SELECT count(*) AS n FROM landed').collect()[0]['n'])")
    assert run(k, mem).strip().endswith("3")


def test_what_runs_only_in_fabric_says_so(lake):
    k = Kernel()
    for code, words in [
        ("spark.readStream.table('dbo.bronze').writeStream.trigger(continuous='1 second')", "Continuous streams run in Fabric"),
        ("spark.readStream.table('dbo.bronze').writeStream.toTable('dbo.x')", "checkpointLocation"),
        ("spark.createDataFrame([(1,)], ['a']).writeStream.option('checkpointLocation', 'Files/c').toTable('dbo.x')",
         "readStream"),
        ("spark.readStream.format('kafka').load('x')", "runs in Fabric"),
        ("spark.readStream.table('dbo.bronze').writeStream.outputMode('update')", "outputMode"),
        ("spark.readStream.table('dbo.bronze').groupBy().count().writeStream.outputMode('complete')", "runs in Fabric"),
    ]:
        out = k.run(code)
        assert out["status"] == "error" and words in str(out["outputs"]), (code, out)
    text = run(k, STREAM.replace("trigger(availableNow=True)", "trigger(processingTime='10 seconds')"))
    assert "runs as availableNow on this PC" in text


def test_a_source_with_deletion_vectors_is_refused(lake):
    write_deltalake(str(lake / "dbo" / "dv"), pa.table({"id": [1], "amount": [1.0]}),
                    configuration={"delta.enableDeletionVectors": "true"})
    out = Kernel().run(STREAM.replace("dbo.bronze", "dbo.dv"))
    assert out["status"] == "error" and "deletion vectors" in str(out["outputs"])
    assert not (lake / "dbo" / "silver").exists()
