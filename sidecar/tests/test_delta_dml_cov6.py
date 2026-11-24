"""MERGE, UPDATE and DELETE on a Delta table (delta_dml.py), the paths the other tests leave: INSERT VALUES
without a column list, the one-match check on an ON that isn't key equalities and one DuckDB can't run, a
streamed source, values that change to fit a column (from a table and a stream), NaN, and column names
looked up on the wrong side. On Delta tables in tmp_path."""

from __future__ import annotations

import math

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import delta_dml as D


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "customers"
    write_deltalake(str(path), pa.table({"id": [1, 2, 3], "spend": [10.0, 20.0, 30.0]}))
    return DeltaTable(str(path))


def rows(table):
    got = DeltaTable(table.table_uri).to_pyarrow_table().sort_by("id")
    return list(zip(got.column("id").to_pylist(), got.column("spend").to_pylist(), strict=True))


def test_insert_values_without_columns_fills_every_column_in_order(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id "
                     "WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.spend * 2)")
    assert [(w.action, len(w.values), w.sets) for w in change.whens] == [("insert", 2, [])]
    out = D.apply(change, table, pa.table({"id": [3, 4], "spend": [1.0, 5.0]}), ["id", "spend"])
    assert out["rows_inserted"] == 1
    assert rows(table) == [(1, 10.0), (2, 20.0), (3, 30.0), (4, 10.0)]


def test_insert_values_with_too_few_values_names_the_table_columns(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT VALUES (s.id)")
    with pytest.raises(ValueError, match=r"it has 2 \(id, spend\), but VALUES gives 1"):
        D.apply(change, table, pa.table({"id": [9], "spend": [1.0]}), ["id", "spend"])
    assert table.version() == 0


def test_two_source_rows_for_one_row_are_refused_when_on_isnt_key_equalities(table):
    # ON with an expression: no keys to count, so the check counts the source rows each table row joins.
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id + 0 WHEN MATCHED THEN UPDATE SET spend = s.spend")
    assert D.equi_keys(change) is None
    with pytest.raises(ValueError, match=r"\[DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE\] More than one "
                                         r"source row matches the same row of customers \(2 source rows\)"):
        D.apply(change, table, pa.table({"id": [2, 2], "spend": [1.0, 2.0]}), ["id", "spend"])
    assert table.version() == 0


def test_one_source_row_each_passes_the_check_when_on_isnt_key_equalities(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id + 0 WHEN MATCHED THEN UPDATE SET spend = s.spend")
    out = D.apply(change, table, pa.table({"id": [2], "spend": [7.0]}), ["id", "spend"])
    assert out["rows_updated"] == 1
    assert rows(table)[1] == (2, 7.0)


def test_a_check_duckdb_cant_run_refuses_the_merge(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.code WHEN MATCHED THEN UPDATE SET spend = s.spend")
    source = pa.table({"code": ["not a number"], "spend": [1.0]})
    with pytest.raises(ValueError, match=r"(?s)MERGE INTO customers isn't run here, because this PC couldn't check .* "
                                         r"The check stopped on: .*Write ON as column comparisons"):
        D.apply(change, table, source, ["code", "spend"])
    assert table.version() == 0


def test_a_streamed_source_the_caller_didnt_check_is_refused(table):
    change = D.parse("MERGE INTO customers t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET spend = s.spend")
    stream = pa.RecordBatchReader.from_batches(pa.schema([("id", pa.int64()), ("spend", pa.float64())]), iter([]))
    with pytest.raises(ValueError, match="a streamed source must be checked for DELTA_MULTIPLE_SOURCE_ROW"):
        D.apply(change, table, stream, ["id", "spend"])
    assert table.version() == 0


def test_a_value_that_would_be_cut_to_fit_a_whole_number_column_stops_the_merge(tmp_path):
    path = tmp_path / "orders"
    write_deltalake(str(path), pa.table({"id": [1], "qty": pa.array([1], pa.int64())}))
    table = DeltaTable(str(path))
    change = D.parse("MERGE INTO orders t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET * "
                     "WHEN NOT MATCHED THEN INSERT *")
    with pytest.raises(ValueError, match=r"This MERGE would change values in qty to fit its type in orders \(int64\): "):
        D.apply(change, table, pa.table({"id": [1], "qty": [2.75]}), ["id", "qty"])
    assert table.version() == 0
    # 2.0 comes through the cast and back unchanged, so it goes in.
    out = D.apply(change, table, pa.table({"id": [1], "qty": [2.0]}), ["id", "qty"])
    assert out["rows_updated"] == 1
    assert DeltaTable(str(path)).to_pyarrow_table().column("qty").to_pylist() == [2]


def test_a_streamed_source_is_checked_batch_by_batch(tmp_path):
    path = tmp_path / "orders"
    write_deltalake(str(path), pa.table({"id": [1], "qty": pa.array([1], pa.int64())}))
    table = DeltaTable(str(path))
    change = D.parse("MERGE INTO orders t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT (id, qty) VALUES (s.id, s.qty)")
    schema = pa.schema([("id", pa.int64()), ("qty", pa.float64())])

    def stream(*batches):
        return pa.RecordBatchReader.from_batches(schema, (pa.record_batch(b, schema=schema) for b in batches))

    out = D.apply(change, table, stream({"id": [2], "qty": [3.0]}), ["id", "qty"], one_match_checked=True)
    assert out["rows_inserted"] == 1
    # delta-rs reads the stream inside its MERGE, so the check's error comes back through delta-rs's own.
    with pytest.raises(Exception, match="would change values in qty to fit its type in orders"):
        D.apply(change, DeltaTable(str(path)), stream({"id": [3], "qty": [4.0]}, {"id": [4], "qty": [4.5]}),
                ["id", "qty"], one_match_checked=True)
    got = DeltaTable(str(path)).to_pyarrow_table().sort_by("id")
    assert got.column("id").to_pylist() == [1, 2]


def test_a_double_that_a_float_column_rounds_stops_the_merge_and_nan_doesnt(tmp_path):
    path = tmp_path / "readings"
    write_deltalake(str(path), pa.table({"id": [1], "v": pa.array([0.5], pa.float32())}))
    table = DeltaTable(str(path))
    change = D.parse("MERGE INTO readings t USING s ON t.id = s.id WHEN NOT MATCHED THEN INSERT *")
    out = D.apply(change, table, pa.table({"id": [2, 3, 4], "v": [math.nan, 1.5, None]}), ["id", "v"])
    assert out["rows_inserted"] == 3
    with pytest.raises(ValueError, match=r"would change values in v to fit its type in readings \(float\): 2\.75"):
        D.apply(change, DeltaTable(str(path)), pa.table({"id": [5], "v": [0.1]}), ["id", "v"])
    # A whole number a float column holds exactly goes in.
    out = D.apply(change, DeltaTable(str(path)), pa.table({"id": [6], "v": pa.array([5], pa.int64())}), ["id", "v"])
    assert out["rows_inserted"] == 1


def test_column_lookups_by_side(table):
    merge = D.parse("MERGE INTO customers t USING s ON t.id = s.id WHEN MATCHED THEN DELETE")
    names = D._Names(merge, ["id", "Spend"], ["id", "Total"])
    assert names.column("total") == "Total"          # no qualifier: not the table's, so the source's
    assert names.column("SPEND", "T") == "Spend"
    with pytest.raises(ValueError, match="There is no column total in t. customers has id, Spend; the source has id, Total."):
        names.column("total", "t")                    # the table's alias: the source isn't looked at
    with pytest.raises(ValueError, match="There is no column spend in s."):
        names.column("spend", "s")
    update = D.parse("UPDATE customers SET spend = bonus")
    with pytest.raises(ValueError, match=r"There is no column bonus\. customers has id, spend\.$"):
        D.apply(update, table)
    assert table.version() == 0
