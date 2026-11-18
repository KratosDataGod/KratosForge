"""Mutation batch 92: csv_cells.py (docs/quality/mutation-testing.md). Dictionary columns with nothing to
protect, and with risky values no row uses, and a string_view batch that needs no protecting but still
goes to the writer as plain strings."""

from __future__ import annotations

import pyarrow as pa

from fabric_desktop import csv_cells


def _dict(values: list[str], indices: list[int]) -> pa.DictionaryArray:
    return pa.DictionaryArray.from_arrays(pa.array(indices, pa.int32()), pa.array(values))


def test_a_dictionary_column_with_nothing_to_protect_is_left_alone():
    column = _dict(["a", "b"], [0, 1, 1])
    batch = pa.RecordBatch.from_arrays([column], names=["kind"])
    got, n = csv_cells.protect_batch(batch)
    assert n == 0 and got is batch
    table, n = csv_cells.protect_table(pa.table({"kind": column}))
    assert n == 0 and table.column("kind").to_pylist() == ["a", "b", "b"]


def test_a_risky_dictionary_value_no_row_uses_counts_no_cell():
    column = _dict(["=1+2", "ok"], [1, 1])
    batch = pa.RecordBatch.from_arrays([column], names=["kind"])
    got, n = csv_cells.protect_batch(batch)
    assert n == 0
    assert got.column(0).to_pylist() == ["ok", "ok"]  # what the writer writes: no row needed protecting


def test_a_risky_dictionary_value_is_counted_once_per_row_that_uses_it():
    batch = pa.RecordBatch.from_arrays([_dict(["=1+2", "ok"], [0, 1, 0])], names=["kind"])
    got, n = csv_cells.protect_batch(batch)
    assert n == 2 and got.column(0).to_pylist() == ["'=1+2", "ok", "'=1+2"]


def test_a_string_view_batch_with_nothing_to_protect_is_written_as_strings():
    batch = pa.RecordBatch.from_arrays([pa.array(["a", "b"], pa.string_view())], names=["name"])
    got, n = csv_cells.protect_batch(batch)
    assert n == 0
    assert got.schema.field("name").type == pa.string()
    assert got.column(0).to_pylist() == ["a", "b"]


def test_a_plain_batch_with_nothing_to_protect_is_the_same_batch():
    batch = pa.RecordBatch.from_arrays([pa.array(["a"]), pa.array([-5])], names=["s", "n"])
    got, n = csv_cells.protect_batch(batch)
    assert n == 0 and got is batch


def test_a_string_view_table_with_nothing_to_protect_is_written_as_strings():
    table = pa.table({"name": pa.array(["a", "b"], pa.string_view()), "n": pa.array([1, 2])})
    got, n = csv_cells.protect_table(table)
    assert n == 0
    assert got.schema.field("name").type == pa.string() and got.schema.field("n").type == pa.int64()
    assert got.column("name").to_pylist() == ["a", "b"]
