"""PySpark on this PC (spark_local.py) at the edges the rest of the suite leaves untaken: writes to a
file in the way, writes refused before anything is written, spark.sql refusals by statement, schema
strings that can't be read, and the Delta table builders' small refusals. Tables are real local Delta
tables (the lake fixture of test_spark_writer.py)."""

from __future__ import annotations

import datetime

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import spark_local as sl
from tests.test_spark_writer import lake, rows, run  # noqa: F401 - lake is a fixture


def test_the_overwrite_predicate_writes_values_as_delta_sql():
    assert sl._sql_literal(True) == "true" and sl._sql_literal(False) == "false"
    assert (
        sl._sql_literal(3) == "3"
        and sl._sql_literal(2.5) == "2.5"
        and sl._sql_literal("O'Neil") == "'O''Neil'"
    )


def test_a_dynamic_overwrite_by_two_partition_columns_replaces_only_those_partitions(
    lake,
):  # noqa: F811
    write_deltalake(
        str(lake / "dbo" / "two"),
        pa.table(
            {"id": [1, 2, 3], "region": ["EU", "US", "EU"], "live": [True, True, False]}
        ),
        partition_by=["region", "live"],
    )
    status, text = run(
        "df2 = spark.createDataFrame([(9, 'EU', True)], ['id', 'region', 'live'])\n"
        "df2.write.option('partitionOverwriteMode', 'dynamic').mode('overwrite').saveAsTable('dbo.two')"
    )
    assert status == "ok", text
    got = sorted(
        DeltaTable(str(lake / "dbo" / "two")).to_pyarrow_table().to_pylist(),
        key=lambda r: r["id"],
    )
    assert got == [
        {"id": 2, "region": "US", "live": True},
        {"id": 3, "region": "EU", "live": False},
        {"id": 9, "region": "EU", "live": True},
    ]


def test_a_dynamic_overwrite_without_the_partition_column_is_refused(lake):  # noqa: F811
    write_deltalake(
        str(lake / "dbo" / "parts"),
        pa.table({"id": [1], "region": ["EU"]}),
        partition_by=["region"],
    )
    status, text = run(
        "spark.createDataFrame([(9,)], ['id']).write.option('partitionOverwriteMode', 'dynamic')"
        ".mode('overwrite').saveAsTable('dbo.parts')"
    )
    assert (
        status == "error"
        and "is partitioned by region, and the data has no region" in text
    )
    assert DeltaTable(str(lake / "dbo" / "parts")).version() == 0


def test_an_overwrite_replaces_a_file_in_the_way(lake, tmp_path):  # noqa: F811
    out = tmp_path / "out.json"
    out.write_text("old")
    status, text = run(f"df.write.mode('overwrite').json({str(out)!r})")
    assert status == "ok", text
    assert out.is_dir() and any(p.suffix == ".json" for p in out.iterdir())


def test_an_overwrite_of_a_new_folder_makes_it(lake, tmp_path):  # noqa: F811
    out = tmp_path / "fresh"
    status, text = run(f"df.write.mode('overwrite').parquet({str(out)!r})")
    assert status == "ok", text
    assert out.is_dir()


def test_a_file_write_partitioned_by_a_column_the_data_lacks_is_refused(lake, tmp_path):  # noqa: F811
    status, text = run(f"df.write.partitionBy('day').parquet({str(tmp_path / 'p')!r})")
    assert (
        status == "error"
        and "partitionBy column day isn't in the data, which has id, region" in text
    )
    assert not (tmp_path / "p").exists()


def test_an_unknown_file_format_is_refused(lake, tmp_path):  # noqa: F811
    status, text = run(f"df.write.format('avro').save({str(tmp_path / 'a')!r})")
    assert (
        status == "error"
        and "Can't write format 'avro'" in text
        and "Use parquet, csv, json or text." in text
    )


def test_a_csv_separator_given_as_an_argument_is_used(lake, tmp_path):  # noqa: F811
    out = tmp_path / "sep"
    status, text = run(f"df.write.csv({str(out)!r}, sep='|', header=True)")
    assert status == "ok", text
    written = "".join(p.read_text() for p in out.iterdir() if p.suffix == ".csv")
    assert "id|region" in written and "9|EU" in written


def test_merge_with_another_statement_in_one_call_is_blocked():
    status, reason = sl.sql_refusal(
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE; SELECT 1"
    )
    assert (
        status == "blocked"
        and "MERGE, UPDATE or DELETE with other statements" in reason
    )


def test_drop_table_with_no_name_isnt_refused():
    assert sl.sql_refusal("DROP TABLE IF EXISTS x") == (
        "unknown",
        "drops x (DROP TABLE), which runs here for a temp view and in Fabric for a lakehouse table",
    )


def test_insert_into_a_table_that_isnt_there_is_refused(lake):  # noqa: F811
    status, text = run("spark.sql('INSERT INTO dbo.nope SELECT 1 AS id')")
    assert status == "error" and "Table dbo.nope doesn't exist" in text


def test_insert_with_another_column_count_is_refused(lake):  # noqa: F811
    status, text = run("spark.sql(\"INSERT INTO dbo.sales SELECT 1, 'EU', 3\")")
    assert (
        status == "error"
        and "the query has 3 columns and table dbo.sales has 2" in text
    )
    assert rows(lake) == [
        {"id": 1, "region": "EU"},
        {"id": 2, "region": "US"},
        {"id": 3, "region": "EU"},
    ]


def test_a_text_read_with_a_schema_other_than_one_string_is_refused(lake, tmp_path):  # noqa: F811
    f = tmp_path / "t.txt"
    f.write_text("a\n")
    status, text = run(
        "from pyspark.sql.types import StructType, StructField, IntegerType\n"
        f"spark.read.schema(StructType([StructField('a', IntegerType())])).text({str(f)!r})"
    )
    assert status == "error" and "A text file's schema is one string column" in text


def test_a_schema_string_part_that_cant_be_read_is_refused():
    with pytest.raises(ValueError, match=r"Couldn't read 'id' in the schema 'id'"):
        sl.ddl_schema("id")


def test_restore_to_a_naive_time_reads_it_as_utc(monkeypatch):
    seen = []
    monkeypatch.setattr(
        sl.LocalDeltaTable,
        "_maintain",
        lambda self, spec: seen.append(spec) or "restored",
    )
    assert (
        sl.LocalDeltaTable("sales").restoreToTimestamp(
            datetime.datetime(2026, 1, 2, 3, 4, 5)
        )
        == "restored"
    )
    assert seen == [
        {
            "operation": "restore",
            "timestamp": datetime.datetime(
                2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc
            ),
        }
    ]
    zoned = datetime.datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    sl.LocalDeltaTable("sales").restoreToTimestamp(zoned)
    assert seen[-1]["timestamp"] is zoned


def test_for_path_of_a_lakehouse_table_opens_it_by_name(monkeypatch):
    monkeypatch.setattr(sl.lakehouse, "relative_path", lambda p: "Tables/dbo/sales")
    monkeypatch.setattr(
        sl.LocalDeltaTable,
        "forName",
        classmethod(lambda cls, spark, name: ("named", name)),
    )
    assert sl.LocalDeltaTable.forPath(
        None, "abfss://ws@onelake/lh/Tables/dbo/sales"
    ) == ("named", "dbo.sales")


def test_optimize_where_twice_joins_both_filters_and_zorder_needs_a_column():
    builder = sl.LocalOptimizeBuilder(sl.LocalDeltaTable("sales"))
    assert builder.where("region = 'EU'").where("day = 1") is builder
    assert builder._where == "region = 'EU' AND (day = 1)"
    with pytest.raises(ValueError, match="executeZOrderBy needs at least one column."):
        builder.executeZOrderBy()


def test_the_builder_takes_settings_from_a_conf_object():
    class Conf:
        def getAll(self):  # noqa: N802
            return [("spark.sql.shuffle.partitions", "3")]

    session = sl._Builder().config(conf=Conf()).getOrCreate()
    assert session.conf.get("spark.sql.shuffle.partitions") == "3"
