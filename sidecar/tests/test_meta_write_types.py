"""Metadata loads and column types delta-rs can't write (#954): a plain message naming the table and
column before anything is written, and Fabric-safe types where Local data widens them."""

import datetime as dt

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable

from fabric_desktop.meta.store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path, "dev")


def folder(store, table):
    from pathlib import Path
    return Path(store.uri("LH_Silver", "dbo", table))


@pytest.mark.parametrize("frame, message", [
    (pl.DataFrame({"id": [1], "c": [dt.time(1, 2)]}), "Table dbo.t: Column c is a TIME"),
    (pl.DataFrame({"id": [1], "c": [dt.timedelta(seconds=3)]}), "Table dbo.t: Column c is an INTERVAL"),
    (pl.DataFrame({"id": [1], "c": [None]}), "Table dbo.t: Column c is NULL in every row"),
])
def test_a_column_delta_cant_hold_stops_the_write_with_a_plain_message(store, frame, message):
    with pytest.raises(ValueError, match=message):
        store.write("LH_Silver", "dbo", "t", frame)
    assert not folder(store, "t").exists()
    s = pa.schema([("c", pa.time64("us"))])
    with pytest.raises(ValueError, match="Column c is a TIME"):
        store.write_stream("LH_Silver", "dbo", "s", pa.RecordBatchReader.from_batches(s, []))
    assert not folder(store, "s").exists()


def test_unsigned_counts_and_float16_are_widened(store):
    frame = pl.DataFrame({"region": ["EU", "EU", "US"]}).group_by("region").len().sort("region")  # len is UInt32
    assert frame.schema["len"] == pl.UInt32
    store.write("LH_Silver", "dbo", "t", frame)
    table = DeltaTable(str(folder(store, "t")))
    assert pa.schema(table.schema().to_arrow()).field("len").type == pa.int64()
    assert table.to_pyarrow_table().sort_by("region").column("len").to_pylist() == [2, 1]
    s = pa.schema([("h", pa.float16())])
    store.write_stream("LH_Silver", "dbo", "s", pa.RecordBatchReader.from_batches(
        s, [pa.record_batch([pa.array([1.5], pa.float16())], schema=s)]))
    assert pa.schema(DeltaTable(str(folder(store, "s"))).schema().to_arrow()).field("h").type == pa.float32()


def test_an_all_null_column_appended_takes_the_tables_type(store):
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [1], "c": ["x"]}))
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [2], "c": [None]}), mode="append")
    rows = sorted(DeltaTable(str(folder(store, "t"))).to_pyarrow_table().to_pylist(), key=lambda r: r["id"])
    assert rows == [{"id": 1, "c": "x"}, {"id": 2, "c": None}]


def test_types_that_write_today_are_left_as_they_are(store):
    frame = pl.DataFrame({"i": pl.Series([1], dtype=pl.Int32), "s": ["a"], "d": [dt.date(2026, 1, 1)],
                          "ts": pl.Series([dt.datetime(2026, 1, 1)]).dt.replace_time_zone("UTC")})
    store.write("LH_Silver", "dbo", "t", frame)
    types = {f.name: f.type for f in pa.schema(DeltaTable(str(folder(store, "t"))).schema().to_arrow())}
    assert types["i"] == pa.int32() and types["d"] == pa.date32()


def test_merges_check_types_like_an_append(store):
    store.write("LH_Silver", "dbo", "t", pl.DataFrame({"id": [1], "n": [1]}))
    big = pl.DataFrame({"id": [1, 2], "n": [1, 2], "u": pl.Series([3_000_000_000, 1], dtype=pl.UInt32)})
    store.upsert("LH_Silver", "dbo", "t", big, ["id"])
    table = DeltaTable(str(folder(store, "t")))
    assert pa.schema(table.schema().to_arrow()).field("u").type == pa.int64()  # not narrowed to INT
    assert sorted(r["u"] for r in table.to_pyarrow_table().to_pylist()) == [1, 3_000_000_000]
    version = table.version()
    with pytest.raises(ValueError, match="Table dbo.t: Column c is a TIME"):
        store.upsert("LH_Silver", "dbo", "t", pl.DataFrame({"id": [3], "n": [3], "c": [dt.time(1, 2)]}), ["id"])
    with pytest.raises(ValueError, match="Table dbo.t: Column c is a TIME"):
        store.merge_insert("LH_Silver", "dbo", "t", pl.DataFrame({"id": [3], "n": [3], "c": [dt.time(1, 2)]}), ["id"])
    assert DeltaTable(str(folder(store, "t"))).version() == version
    with pytest.raises(ValueError, match="Table dbo.k: Column c is a TIME"):
        store.merge_insert("LH_Silver", "dbo", "k", pl.DataFrame({"id": [1], "c": [dt.time(1, 2)]}), ["id"])
    assert not folder(store, "k").exists()
    store.merge_insert("LH_Silver", "dbo", "k", pl.DataFrame({"id": pl.Series([1], dtype=pl.UInt32)}), ["id"])
    assert pa.schema(DeltaTable(str(folder(store, "k"))).schema().to_arrow()).field("id").type == pa.int64()
