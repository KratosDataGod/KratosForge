"""replaceWhere in an Academy PySpark exercise (academy/pyspark_shim.py): an overwrite replaces only the
rows that match the condition, every row written must match it, and a condition this PC can't check
writes nothing. The same rules as the notebook runner (spark_local.py, tests/test_delta_write_integrity.py),
from the shared spark_replace_where module."""

from __future__ import annotations

import os
import subprocess
import sys

import deltalake
import pytest

from duckdb.experimental.spark.sql import functions as F

from fabric_desktop.academy import learner_pkg, pyspark_lab, pyspark_shim

SETUP = ("CREATE SCHEMA silver; CREATE TABLE silver.sales AS SELECT * FROM (VALUES "
         "(DATE '2026-03-01', 1, 10e0), (DATE '2026-03-02', 2, 20e0), (DATE '2026-03-03', 3, 30e0)) t(day, id, amount)")


@pytest.fixture
def spark(tmp_path):
    pyspark_lab.prepare(tmp_path, SETUP)
    return pyspark_shim.AcademySession(tmp_path)


def _rows(spark):
    t = deltalake.DeltaTable(str(pyspark_shim.table_path(spark.work, "silver.sales")))
    return sorted((str(r["day"]), r["id"], r["amount"]) for r in t.to_pyarrow_table().to_pylist())


def _writer(spark, df):
    """``df.write`` as the learner's process gets it (pyspark_shim.session sets it on every DataFrame)."""
    return pyspark_shim.Writer(df, spark.work)


def _day(spark, day, rows):
    return spark.createDataFrame([(day, i, a) for i, a in rows], ["day", "id", "amount"]) \
        .withColumn("day", F.col("day").cast("date"))


def test_replace_where_replaces_only_the_matching_day_and_is_safe_to_repeat(spark):
    for _ in range(2):
        (_writer(spark, _day(spark, "2026-03-02", [(2, 21.0), (4, 40.0)])).mode("overwrite")
         .option("replaceWhere", "day = '2026-03-02'").saveAsTable("silver.sales"))
    assert _rows(spark) == [("2026-03-01", 1, 10.0), ("2026-03-02", 2, 21.0), ("2026-03-02", 4, 40.0),
                            ("2026-03-03", 3, 30.0)]


def test_a_plain_overwrite_still_replaces_the_whole_table(spark):
    _writer(spark, _day(spark, "2026-03-02", [(2, 21.0)])).mode("overwrite").saveAsTable("silver.sales")
    assert _rows(spark) == [("2026-03-02", 2, 21.0)]


def test_a_row_outside_the_condition_stops_the_write_and_changes_nothing(spark):
    before = _rows(spark)
    with pytest.raises(ValueError, match="DELTA_REPLACE_WHERE_MISMATCH"):
        (_writer(spark, _day(spark, "2026-03-04", [(9, 1.0)])).mode("overwrite")
         .option("replaceWhere", "day = '2026-03-02'").saveAsTable("silver.sales"))
    assert _rows(spark) == before


def test_replace_where_on_an_append_is_ignored_with_a_note(spark, capsys):
    (_writer(spark, _day(spark, "2026-03-04", [(9, 1.0)])).mode("append")
     .option("replaceWhere", "day = '2026-03-02'").saveAsTable("silver.sales"))
    assert "only applies to an overwrite" in capsys.readouterr().out
    assert len(_rows(spark)) == 4


def test_a_new_table_gets_every_row(spark):
    (_writer(spark, _day(spark, "2026-03-02", [(2, 21.0)])).mode("overwrite")
     .option("replaceWhere", "day = '2026-03-02'").saveAsTable("silver.new_sales"))
    t = deltalake.DeltaTable(str(pyspark_shim.table_path(spark.work, "silver.new_sales")))
    assert t.to_pyarrow_table().num_rows == 1


@pytest.mark.parametrize(("condition", "reason"), [
    ("day = '2026-03-02'; DROP TABLE x", "single condition"),
    ("day IN (SELECT day FROM other)", "no subquery"),
    ("region = 'EU'", "has no column region"),
    ("day = ", "isn't a condition"),
])
def test_a_condition_this_pc_cant_honor_is_refused_and_writes_nothing(spark, condition, reason):
    before = _rows(spark)
    with pytest.raises(ValueError, match=reason):
        (_writer(spark, _day(spark, "2026-03-02", [(2, 21.0)])).mode("overwrite")
         .option("replaceWhere", condition).saveAsTable("silver.sales"))
    assert _rows(spark) == before


def test_the_schema_options_still_read_true_and_false_text(spark):
    df = _day(spark, "2026-03-05", [(5, 5.0)]).withColumn("note", F.lit("x"))
    with pytest.raises(Exception):  # noqa: B017 - delta-rs's own schema error; "false" must not merge
        _writer(spark, df).mode("append").option("mergeSchema", "false").saveAsTable("silver.sales")
    _writer(spark, df).mode("append").option("mergeSchema", "true").saveAsTable("silver.sales")
    t = deltalake.DeltaTable(str(pyspark_shim.table_path(spark.work, "silver.sales")))
    assert "note" in t.schema().to_arrow().names


PROBE = """
import os, sys
sys.path.insert(0, sys.argv[1])
from kf_learner import pyspark_shim
spark = pyspark_shim.session(sys.argv[2])
from pyspark.sql import functions as F
df = spark.read.table("silver.sales").where(F.col("id") == 2).withColumn("amount", F.col("amount") * 2)
df.write.mode("overwrite").option("replaceWhere", "day = '2026-03-02'").saveAsTable("silver.sales")
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("fabric_desktop", "msal", "requests", "azure"))
print(repr(bad))
sys.stdout.flush(); os._exit(0)
"""


def test_the_learner_package_runs_replace_where_without_the_engine(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    pyspark_lab.prepare(work, SETUP)
    pkg = learner_pkg.build(tmp_path / "pkg")
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "TEMP", "TMP", "PATH")}
    out = subprocess.run([sys.executable, "-I", "-c", PROBE, str(pkg), str(work)], cwd=work, env=env,
                         capture_output=True, text=True, timeout=120, check=True).stdout
    assert eval(out.strip().splitlines()[-1]) == []  # noqa: S307 - our own probe's repr
    t = deltalake.DeltaTable(str(pyspark_shim.table_path(work, "silver.sales")))
    assert sorted(r["amount"] for r in t.to_pyarrow_table().to_pylist()) == [10.0, 30.0, 40.0]
