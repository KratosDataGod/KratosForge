"""Notebook table writes and column types delta-rs can't write (#953): each gets the type Fabric
reads, as a Local data project writes it, or a plain message; every other type is left as it is."""

import datetime as dt
import decimal
import threading

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: default)
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    yield root
    lakehouse.attach(None)


def schema(lake, table):
    return pa.schema(DeltaTable(str(lake / "dbo" / table)).schema().to_arrow())


@pytest.mark.parametrize("column, written", [
    (pa.array([200], pa.uint8()), pa.int16()),
    (pa.array([4_000_000_000], pa.uint32()), pa.int64()),
    (pa.array([2**63 + 5], pa.uint64()), pa.decimal128(20, 0)),
    (pa.array([1.5], pa.float16()), pa.float32()),
    (pa.array([[1.5]], pa.list_(pa.float16())), pa.list_(pa.float32())),
])
def test_types_delta_rs_refuses_are_written_as_fabric_reads_them(lake, column, written):
    lakehouse.write_table(pa.table({"id": [1], "c": column}), "dbo.t")
    assert schema(lake, "t").field("c").type == written
    assert DeltaTable(str(lake / "dbo" / "t")).to_pyarrow_table().column("c").to_pylist() == \
        column.cast(written).to_pylist()


@pytest.mark.parametrize("column, message", [
    (pa.array([dt.time(1, 2)], pa.time64("us")), "is a TIME"),
    (pa.array([dt.timedelta(seconds=3)], pa.duration("us")), "is an INTERVAL"),
    (pa.array([(1, 2, 3)], pa.month_day_nano_interval()), "is an INTERVAL"),
    (pa.array([decimal.Decimal("1.5")], pa.decimal256(40, 2)), "Delta holds up to 38"),
    (pa.array([None], pa.null()), "NULL in every row"),
])
def test_types_fabric_cant_hold_get_a_plain_message_and_nothing_is_written(lake, column, message):
    with pytest.raises(ValueError, match=message):
        lakehouse.write_table(pa.table({"id": [1], "c": column}), "dbo.t")
    assert not (lake / "dbo" / "t" / "_delta_log").exists()


def test_an_all_null_column_appended_takes_the_tables_type_and_doesnt_hang(lake):
    """delta-rs hangs on an append with a NULL-typed column (deltalake 1.6.6), so it's cast first."""
    lakehouse.write_table(pa.table({"id": [1], "c": ["x"]}), "dbo.t")
    done: list = []
    worker = threading.Thread(daemon=True, target=lambda: done.append(
        lakehouse.write_table(pa.table({"id": [2], "c": pa.array([None], pa.null())}), "dbo.t", mode="append")))
    worker.start()
    worker.join(30)
    assert done, "the append didn't finish"
    assert sorted(DeltaTable(str(lake / "dbo" / "t")).to_pyarrow_table().to_pylist(), key=lambda r: r["id"]) == \
        [{"id": 1, "c": "x"}, {"id": 2, "c": None}]
    with pytest.raises(ValueError, match="NULL in every row"):  # a new column has no type to take
        lakehouse.write_table(pa.table({"id": [3], "n": pa.array([None], pa.null())}), "dbo.t", mode="append",
                              schema_mode="merge")


def test_every_type_that_writes_today_is_left_as_it_is(lake, tmp_path):
    data = pa.table({"i8": pa.array([1], pa.int8()), "i32": pa.array([1], pa.int32()),
                     "s": pa.array(["a"], pa.large_string()), "ts": pa.array([1], pa.timestamp("us", tz="UTC")),
                     "d": pa.array([dt.date(2026, 1, 1)], pa.date32()), "b": [True],
                     "dec": pa.array([decimal.Decimal("1.50")], pa.decimal128(10, 2)),
                     "st": pa.array([{"x": 1}]), "l": pa.array([[1]])})
    lakehouse.write_table(data, "dbo.t")
    write_deltalake(str(tmp_path / "direct"), data)
    assert schema(lake, "t") == pa.schema(DeltaTable(str(tmp_path / "direct")).schema().to_arrow())


def test_a_stream_is_made_safe_too(lake):
    s = pa.schema([("id", pa.int64()), ("u", pa.uint32())])
    reader = pa.RecordBatchReader.from_batches(s, [pa.record_batch([pa.array([i]), pa.array([i], pa.uint32())], schema=s)
                                                   for i in range(3)])
    lakehouse.write_table(reader, "dbo.t")
    assert schema(lake, "t").field("u").type == pa.int64()
    assert DeltaTable(str(lake / "dbo" / "t")).to_pyarrow_table().num_rows == 3


def test_a_timestamp_without_a_time_zone_gets_a_plain_message(lake):
    """#966: delta-rs would write timestampNtz (reader 3), which Fabric can't read."""
    import pandas as pd
    import polars as pl
    naive = dt.datetime(2026, 1, 1, 12)
    frames = [pl.DataFrame({"id": [1], "at": [naive]}), pd.DataFrame({"id": [1], "at": [pd.Timestamp(naive)]}),
              pa.table({"id": [1], "s": pa.array([{"at": naive}])})]
    for frame, column in zip(frames, ["at", "at", "s.at"]):
        with pytest.raises(ValueError, match=f"Column {column} holds timestamps without a time zone") as err:
            lakehouse.write_table(frame, "dbo.t")
        assert "replace_time_zone" in str(err.value) and "Nothing was written" in str(err.value)
    assert not (lake / "dbo" / "t" / "_delta_log").exists()
    lakehouse.write_table(pl.DataFrame({"at": [naive]}).with_columns(pl.col("at").dt.replace_time_zone("UTC")), "dbo.t")
    assert schema(lake, "t").field("at").type == pa.timestamp("us", tz="UTC")
