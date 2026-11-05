"""A MERGE stops, with nothing changed, when a value it copies into the table would change to fit the
column's type (EA-001): delta-rs casts quietly, so 2.75 into an INT column became 2. A value the cast
keeps, and a wider type, still merge. Both ways a MERGE runs on this PC: spark.sql and the
DeltaTable.merge builder in a notebook, and the local data project's engine (delta_dml.apply)."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from test_spark_write_types import lake, run  # noqa: F401 - lake is a fixture

from fabric_desktop import delta_dml

T = "dbo/t"


def seed(lake, column):
    write_deltalake(str(lake / T), pa.table({"id": pa.array([1], pa.int64()), "v": column}))


def rows(lake):
    return sorted(DeltaTable(str(lake / T)).to_pyarrow_table().to_pylist(), key=lambda r: r["id"])


def merge(source, whens):
    return (f"spark.sql(\"MERGE INTO dbo.t AS t USING ({source}) AS s ON t.id = s.id {whens}\")")


INSERT_ALL = "WHEN NOT MATCHED THEN INSERT *"
UPDATE_ALL = "WHEN MATCHED THEN UPDATE SET *"
UPDATE_ONE = "WHEN MATCHED THEN UPDATE SET t.v = s.v"
INSERT_ONE = "WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v)"


@pytest.mark.parametrize("have, value, whens, id_", [
    (pa.array([3], pa.int32()), "2.75D", INSERT_ALL, 2),
    (pa.array([3], pa.int32()), "2.75D", UPDATE_ALL, 1),
    (pa.array([3], pa.int32()), "2.75D", UPDATE_ONE, 1),
    (pa.array([3], pa.int32()), "2.75D", INSERT_ONE, 2),
    (pa.array([dt.date(2026, 1, 1)]), "TIMESTAMP'2026-01-02 23:30:00'", INSERT_ALL, 2),
    (pa.array([1], pa.int64()), "'012'", INSERT_ALL, 2),
], ids=["float into int, INSERT *", "UPDATE SET *", "UPDATE SET t.v = s.v", "INSERT (..) VALUES (s.v)",
        "timestamp into date", "text a whole number would change"])
def test_a_merge_that_would_change_a_value_stops(lake, have, value, whens, id_):
    seed(lake, have)
    status, text = run(merge(f"SELECT {id_}L AS id, {value} AS v", whens))
    assert status == "error" and "would change values in v" in text and "Nothing was changed" in text, text
    assert DeltaTable(str(lake / T)).version() == 0


@pytest.mark.parametrize("have, value, want", [
    (pa.array([3], pa.int32()), "2.0D", 2),       # a float the cast keeps
    (pa.array([3], pa.int64()), "CAST(2 AS INT)", 2),  # a wider type
    (pa.array([3.5], pa.float64()), "2.5", 2.5),  # Spark's decimal literal into a double
])
def test_a_merge_that_keeps_every_value_writes(lake, have, value, want):
    seed(lake, have)
    status, text = run(merge(f"SELECT 2L AS id, {value} AS v", INSERT_ALL))
    assert status == "ok", text
    assert rows(lake)[-1] == {"id": 2, "v": want}


def test_a_value_computed_in_the_merge_is_not_checked_here(lake):
    # Only a source column copied as it is is checked; an expression is typed by delta-rs.
    seed(lake, pa.array([3], pa.int32()))
    status, text = run(merge("SELECT 1L AS id, 4L AS n", "WHEN MATCHED THEN UPDATE SET t.v = s.n + 1"))
    assert status == "ok", text
    assert rows(lake) == [{"id": 1, "v": 5}]


def test_the_merge_builder_stops_too(lake):
    seed(lake, pa.array([3], pa.int32()))
    status, text = run("from delta.tables import DeltaTable as D\n"
                       "D.forName(spark, 'dbo.t').alias('t').merge(spark.sql('SELECT 2L AS id, 2.75D AS v').alias('s'), "
                       "'t.id = s.id').whenNotMatchedInsertAll().execute()")
    assert status == "error" and "would change values in v" in text, text
    assert DeltaTable(str(lake / T)).version() == 0


def test_a_stream_source_stops_before_the_commit(tmp_path):
    uri = str(tmp_path / "t")
    write_deltalake(uri, pa.table({"id": pa.array([1], pa.int64()), "v": pa.array([3], pa.int32())}))
    change = delta_dml.parse("MERGE INTO t AS t USING s AS s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")
    data = pa.table({"id": pa.array([2, 3], pa.int64()), "v": [2.0, 2.75]})
    with pytest.raises(Exception, match="would change values in v"):
        delta_dml.apply(change, DeltaTable(uri), pa.RecordBatchReader.from_batches(data.schema, data.to_batches()),
                        ["id", "v"])
    assert DeltaTable(uri).version() == 0
