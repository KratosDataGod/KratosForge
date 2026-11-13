"""PySpark writes and reads on this PC (spark_local.py), the paths the main tests don't reach: each save
mode against a table that is or isn't there, writes that are refused before anything is written, and
reads by format, version and path. Tables are real local Delta tables, as in test_spark_writer.py."""

from __future__ import annotations

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from tests.test_spark_writer import ORIGINAL, lake, rows, run  # noqa: F401 - lake is a fixture


# ---- save modes


def test_error_mode_refuses_a_table_that_exists_and_makes_one_that_doesnt(lake):  # noqa: F811
    status, text = run("df.write.mode('error').saveAsTable('dbo.sales')")
    assert status == "error" and "Table dbo.sales already exists" in text and rows(lake) == ORIGINAL
    status, _ = run("df.write.mode('errorifexists').saveAsTable('dbo.fresh')")
    assert status == "ok" and rows(lake, "fresh") == [{"id": 9, "region": "EU"}]


def test_ignore_mode_leaves_a_table_that_exists(lake):  # noqa: F811
    status, _ = run("df.write.mode('ignore').saveAsTable('dbo.sales')")
    assert status == "ok" and rows(lake) == ORIGINAL
    assert DeltaTable(str(lake / "dbo" / "sales")).version() == 0  # nothing committed


def test_an_unknown_save_mode_is_refused(lake):  # noqa: F811
    status, text = run("df.write.mode('upsert').saveAsTable('dbo.sales')")
    assert status == "error" and "Unknown save mode 'upsert'" in text and rows(lake) == ORIGINAL


def test_overwrite_schema_on_an_append_says_it_keeps_the_schema(lake):  # noqa: F811
    status, text = run("df.write.mode('append').option('overwriteSchema', 'true').saveAsTable('dbo.sales')")
    assert status == "ok" and "overwriteSchema only applies to an overwrite" in text
    assert len(rows(lake)) == 4


def test_save_as_table_takes_its_mode_and_partitions_as_arguments(lake):  # noqa: F811
    status, text = run("df.write.saveAsTable('dbo.byregion', mode='overwrite', partitionBy='region')")
    assert status == "ok", text
    assert rows(lake, "byregion") == [{"id": 9, "region": "EU"}]


def test_a_table_in_another_format_is_refused(lake):  # noqa: F811
    status, text = run("df.write.saveAsTable('dbo.sales', format='parquet', mode='overwrite')")
    assert status == "error" and "Lakehouse tables are Delta tables" in text and rows(lake) == ORIGINAL


def test_files_in_the_lakehouse_are_written_in_fabric_not_here(lake):  # noqa: F811
    status, text = run("df.write.save('Files/out', format='parquet', mode='overwrite', partitionBy='region')")
    assert status == "error" and "Writing files to Files/out runs in Fabric" in text


def test_a_csv_header_given_as_an_argument_is_kept(lake, tmp_path):  # noqa: F811
    out = tmp_path / "out.csv"
    status, text = run(f"df.write.csv({str(out)!r}, header=False)")
    assert status == "ok", text
    assert out.read_text().strip() == "9,EU"


# ---- reads


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    write_deltalake(str(d / "delta"), pa.table({"id": [1]}))
    write_deltalake(str(d / "delta"), pa.table({"id": [2]}), mode="append")
    (d / "notes.txt").write_text("first line\nsecond line\n")
    (d / "semi.csv").write_text("id;name\n1;a\n")
    return d


def test_a_delta_folder_on_this_pc_reads_now_and_as_of_a_version(folder):
    path = str(folder / "delta")
    status, text = run(f"print(sorted(r.id for r in spark.read.format('delta').load({path!r}).collect()))\n"
                       f"print(spark.read.format('delta').option('versionAsOf', 0).load({path!r}).count())")
    assert status == "ok" and text.split() == ["[1,", "2]", "1"], text


def test_a_lakehouse_table_reads_as_of_a_version(lake):  # noqa: F811
    run("df.write.mode('append').saveAsTable('dbo.sales')")
    status, text = run("print(spark.read.option('versionAsOf', 0).table('dbo.sales').count())")
    assert status == "ok" and text.strip() == "3", text


def test_text_and_csv_reads_by_format(folder):
    status, text = run(f"print([r.value for r in spark.read.format('text').load({str(folder / 'notes.txt')!r}).collect()])\n"
                       f"print(spark.read.format('csv').load({str(folder / 'semi.csv')!r}).count() >= 1)\n"
                       f"print(spark.read.csv({str(folder / 'semi.csv')!r}, header=True, sep=';').columns)")
    assert status == "ok", text
    assert text.splitlines() == ["['first line', 'second line']", "True", "['id', 'name']"]


@pytest.mark.parametrize("code, says", [
    ("spark.read.format('xml').load({p!r})", "Format 'xml' isn't supported on this PC"),
    ("spark.read.parquet()", "Give parquet a path"),
    ("spark.sql('RESTORE TABLE dbo.sales TO VERSION AS OF 0')", "RESTORE rewrites a table to an older version"),
])
def test_reads_this_pc_cant_do_say_why(folder, code, says):
    status, text = run(code.format(p=str(folder / "notes.txt")))
    assert status == "error" and says in text


def test_sql_fills_in_its_keyword_arguments():
    status, text = run("print(spark.sql('SELECT {n} AS n', n=7).collect()[0].n)")
    assert status == "ok" and text.strip() == "7"

