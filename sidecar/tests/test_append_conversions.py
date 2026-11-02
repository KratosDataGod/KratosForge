"""EA-001: a write into an existing table never changes values to fit its column types."""

import datetime as dt
import decimal

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import publish
from fabric_desktop.localdata.delta import conversions

from test_ea_write_integrity import Ref


@pytest.mark.parametrize("src, dst", [
    (pa.float64(), pa.int32()), (pa.float64(), pa.int64()), (pa.float64(), pa.float32()),
    (pa.decimal128(10, 2), pa.int64()), (pa.float64(), pa.decimal128(10, 2)),
    (pa.timestamp("us", "UTC"), pa.date32()), (pa.string(), pa.int64()), (pa.int64(), pa.string()),
    (pa.bool_(), pa.int32()), (pa.int32(), pa.bool_()), (pa.date32(), pa.string()),
    (pa.list_(pa.float64()), pa.list_(pa.int64())),
    (pa.struct([("a", pa.float64())]), pa.struct([("a", pa.int32())])),
    # A float holds whole numbers exactly only up to its mantissa: 16777217 becomes 16777216 in a
    # float, 2**53 + 1 becomes 2**53 in a double. A decimal rounds in either.
    (pa.int64(), pa.float32()), (pa.int32(), pa.float32()), (pa.int64(), pa.float64()),
    (pa.decimal128(38, 18), pa.float64()), (pa.decimal128(10, 2), pa.float32()),
])
def test_a_type_that_would_change_values_is_a_conversion(src, dst):
    assert conversions(pa.schema([("C", src)]), pa.schema([("c", dst)])) == [("C", src, dst)]


@pytest.mark.parametrize("src, dst", [
    (pa.int32(), pa.int64()), (pa.int64(), pa.int32()),  # delta-rs stops a whole number that doesn't fit
    (pa.int8(), pa.float32()), (pa.int16(), pa.float32()), (pa.int16(), pa.float64()), (pa.int32(), pa.float64()),
    (pa.float32(), pa.float64()), (pa.int64(), pa.decimal128(18, 0)), (pa.date32(), pa.timestamp("us", "UTC")),
    (pa.timestamp("ns", "UTC"), pa.timestamp("us", "UTC")), (pa.large_string(), pa.string()),
    (pa.null(), pa.int32()), (pa.list_(pa.int32()), pa.list_(pa.int64())),
])
def test_a_type_that_keeps_every_value_is_fine(src, dst):
    assert conversions(pa.schema([("c", src)]), pa.schema([("c", dst)])) == []


def test_a_new_column_is_a_schema_change_not_a_conversion():
    assert conversions(pa.schema([("new", pa.float64())]), pa.schema([("c", pa.int32())])) == []


def test_publish_refuses_the_append_names_the_column_and_commits_nothing(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"id": pa.array([1], pa.int32()), "qty": pa.array([3], pa.int32())}), ref, "overwrite", "tok")
    with pytest.raises(ValueError, match=r"qty is double here but int in the table.*Nothing was written"):
        publish.publish(pa.table({"id": pa.array([2], pa.int32()), "qty": [2.75]}), ref, "append", "tok")
    assert DeltaTable(ref.uri).version() == 0


def test_a_stream_is_checked_before_any_batch_is_read(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"d": [dt.date(2026, 1, 1)]}), ref, "overwrite", "tok")
    schema = pa.schema([("d", pa.timestamp("us", "UTC"))])
    read = []

    def batches():
        read.append(1)
        yield pa.record_batch({"d": [dt.datetime(2026, 1, 2, 13, 45, tzinfo=dt.timezone.utc)]}, schema=schema)
    with pytest.raises(ValueError, match="d is timestamp here but date in the table"):
        publish.publish(pa.RecordBatchReader.from_batches(schema, batches()), ref, "append", "tok")
    assert read == [] and DeltaTable(ref.uri).version() == 0


def test_an_overwrite_that_keeps_the_schema_is_checked_and_one_that_replaces_it_is_not(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"n": pa.array([1], pa.int64())}), ref, "overwrite", "tok")
    with pytest.raises(ValueError, match="n is string here but bigint"):
        publish.publish(pa.table({"n": ["12"]}), ref, "overwrite", "tok", schema_mode=None)
    publish.publish(pa.table({"n": ["12"]}), ref, "overwrite", "tok")  # "auto": the schema is replaced
    assert DeltaTable(ref.uri).to_pyarrow_table().column("n").to_pylist() == ["12"]


def test_a_decimal_into_a_whole_number_column_is_refused(tmp_path):
    uri = (tmp_path / "t").as_posix()
    write_deltalake(uri, pa.table({"n": pa.array([1], pa.int64())}))
    with pytest.raises(ValueError, match="decimal"):
        publish.publish(pa.table({"n": pa.array([decimal.Decimal("19.99")], pa.decimal128(4, 2))}), Ref(uri), "append", "tok")
    assert DeltaTable(uri).to_pyarrow_table().column("n").to_pylist() == [1]


@pytest.mark.parametrize("value, src, dst", [
    (16777217, pa.int64(), pa.float32()), (16777217, pa.int32(), pa.float32()),
    (2**53 + 1, pa.int64(), pa.float64()),
])
def test_a_whole_number_a_float_would_round_is_refused(tmp_path, value, src, dst):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"n": pa.array([1.0], dst)}), ref, "overwrite", "tok")
    with pytest.raises(ValueError, match="Nothing was written"):
        publish.publish(pa.table({"n": pa.array([value], src)}), ref, "append", "tok")
    assert DeltaTable(ref.uri).version() == 0


def test_a_decimal_a_double_would_round_is_refused(tmp_path):
    from decimal import Decimal
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"x": [0.5]}), ref, "overwrite", "tok")
    with pytest.raises(ValueError, match="Nothing was written"):
        publish.publish(pa.table({"x": pa.array([Decimal("0.123456789012345678")], pa.decimal128(38, 18))}), ref, "append", "tok")
    assert DeltaTable(ref.uri).version() == 0


def test_an_int_into_a_double_keeps_its_value(tmp_path):
    ref = Ref((tmp_path / "t").as_posix())
    publish.publish(pa.table({"n": [1.0]}), ref, "overwrite", "tok")
    publish.publish(pa.table({"n": pa.array([2**31 - 1], pa.int32())}), ref, "append", "tok")
    assert sorted(DeltaTable(ref.uri).to_pyarrow_table()["n"].to_pylist()) == [1.0, 2**31 - 1]


# ---- Spark's INSERT and insertInto cast their values to the table's types, as Spark does

def _table_with(monkeypatch, schema):
    from fabric_desktop import spark_local

    class Table:
        def schema(self):
            class S:
                def to_arrow(self):
                    return schema
            return S()
    monkeypatch.setattr(spark_local.lakehouse, "open_table", lambda name: Table())
    return spark_local._store_assign


def test_an_insert_casts_a_literal_that_keeps_its_value(monkeypatch):
    from decimal import Decimal
    assign = _table_with(monkeypatch, pa.schema([("id", pa.int64()), ("amount", pa.float64())]))
    out = assign(pa.table({"id": pa.array([4], pa.int32()), "amount": pa.array([Decimal("1.0")], pa.decimal128(2, 1))}), "dbo.orders")
    assert out.schema == pa.schema([("id", pa.int64()), ("amount", pa.float64())]) and out.to_pylist() == [{"id": 4, "amount": 1.0}]


@pytest.mark.parametrize("column, dst", [
    (pa.array([2.75]), pa.int64()), (pa.array([2**53 + 1], pa.int64()), pa.float64()),
    (pa.array([1.1]), pa.float32()),
])
def test_an_insert_that_would_change_a_value_is_refused(monkeypatch, column, dst):
    assign = _table_with(monkeypatch, pa.schema([("n", dst)]))
    with pytest.raises(ValueError, match="Nothing was written"):
        assign(pa.table({"n": column}), "dbo.t")


@pytest.mark.parametrize("column, dst", [
    (pa.array(["5"]), pa.int32()), (pa.array([True]), pa.int32()), (pa.array([1], pa.int32()), pa.bool_()),
    (pa.array(["2020-01-01"]), pa.date32()),
])
def test_an_insert_leaves_what_spark_wont_assign_for_write_table_to_refuse(monkeypatch, column, dst):
    assign = _table_with(monkeypatch, pa.schema([("n", dst)]))
    out = assign(pa.table({"n": column}), "dbo.t")
    assert out.schema.field("n").type == column.type  # not cast
    assert conversions(out.schema, pa.schema([("n", dst)]))  # and write_table stops it


@pytest.mark.parametrize("column, dst, value", [
    (pa.array([7], pa.int32()), pa.string(), "7"), (pa.array([1.5]), pa.float32(), 1.5),
    (pa.array([dt.date(2026, 1, 2)]), pa.timestamp("us", "UTC"), dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)),
])
def test_an_insert_casts_what_spark_assigns(monkeypatch, column, dst, value):
    assign = _table_with(monkeypatch, pa.schema([("n", dst)]))
    out = assign(pa.table({"n": column}), "dbo.t")
    assert out.schema.field("n").type == dst and out.column("n").to_pylist() == [value]


def test_an_insert_refuses_a_decimal_a_double_would_round(monkeypatch):
    from decimal import Decimal
    assign = _table_with(monkeypatch, pa.schema([("x", pa.float64())]))
    with pytest.raises(ValueError, match="Nothing was written"):
        assign(pa.table({"x": pa.array([Decimal("0.123456789012345678")], pa.decimal128(38, 18))}), "dbo.t")
