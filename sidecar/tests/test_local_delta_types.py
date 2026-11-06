"""A local data project's Delta writes (localdata/delta.py): the edges the write tests leave out. Statements
that can't be a Delta write, AS OF clauses that are wrong, every column type Fabric can't read and what it
becomes, and which appends would change a value without saying so (EA-001)."""

from __future__ import annotations

import pyarrow as pa
import pytest
import sqlglot

from fabric_desktop.localdata import delta as d


def is_delta(schema, table):
    return table == "sales"


# --------------------------------------------------------------------------- statements

def test_a_create_using_delta_that_isnt_create_as_select_says_how_to_write_it():
    with pytest.raises(ValueError, match="CREATE TABLE name USING DELTA"):
        d.parse("CREATE TABLE sales (id INT) USING DELTA", is_delta)


def test_a_create_as_something_that_isnt_rows_is_refused():
    with pytest.raises(ValueError, match="needs a query for its rows"):
        d._rows("DELETE FROM x")


def test_an_insert_with_a_column_list_into_a_delta_table_is_refused_and_any_other_insert_is_duckdbs():
    with pytest.raises(ValueError, match="a column list isn't supported"):
        d.parse("INSERT INTO sales (id) SELECT 1", is_delta)
    assert d.parse("INSERT INTO other SELECT 1", is_delta) is None
    assert d.parse("-- note\nUPDATE sales SET id = 1", is_delta) is None


def test_a_parsed_spark_create_and_insert_become_writes():
    tree = sqlglot.parse_one("CREATE OR REPLACE TABLE s.sales USING DELTA PARTITIONED BY (region) AS SELECT 1 AS id, 'e' AS region",
                             read="spark")
    w = d.parse_tree(tree, is_delta)
    assert (w.schema, w.table, w.mode, w.replace, w.partition_by) == ("s", "sales", "overwrite", True, ["region"])
    insert = d.parse_tree(sqlglot.parse_one("INSERT OVERWRITE sales SELECT 1", read="spark"), is_delta)
    assert insert.mode == "overwrite" and insert.by_position


def test_a_parsed_statement_that_isnt_a_delta_write_is_none_or_refused():
    def spark(sql):
        return sqlglot.parse_one(sql, read="spark")
    assert d.parse_tree(spark("CREATE TABLE t AS SELECT 1"), is_delta) is None  # not USING DELTA
    assert d.parse_tree(spark("INSERT INTO other SELECT 1"), is_delta) is None
    assert d.parse_tree(spark("INSERT INTO other (id) SELECT 1"), is_delta) is None
    assert d.parse_tree(spark("SELECT 1"), is_delta) is None
    with pytest.raises(ValueError, match="column list isn't supported"):
        d.parse_tree(spark("INSERT INTO sales (id) SELECT 1"), is_delta)
    with pytest.raises(ValueError, match="AS SELECT"):
        d.parse_tree(spark("CREATE TABLE sales (id INT) USING DELTA"), is_delta)


# --------------------------------------------------------------------------- reading as of a version or time

def test_sql_without_as_of_comes_back_as_written():
    assert d.rewrite_as_of("SELECT 1") == ("SELECT 1", [])


@pytest.mark.parametrize("sql, message", [
    ("SELECT * FROM sales VERSION AS OF 'x'", "takes a version number"),
    ("SELECT * FROM sales TIMESTAMP AS OF 5", "takes a date or time in quotes"),
    ("SELECT * FROM sales AT (VERSION => ", "Could not read the query's AT"),
])
def test_a_wrong_as_of_says_what_it_takes(sql, message):
    with pytest.raises(ValueError, match=message):
        d.rewrite_as_of(sql)


def test_an_as_of_time_is_utc_unless_it_says_otherwise():
    assert d.as_of_time("2026-10-01").tzinfo is not None
    assert d.as_of_time("2026-10-01T14:30:00+02:00").utcoffset().total_seconds() == 7200
    with pytest.raises(ValueError, match="isn't a date or time"):
        d.as_of_time("yesterday")


# --------------------------------------------------------------------------- column types Fabric reads

@pytest.mark.parametrize("given, fabric", [
    (pa.uint8(), pa.int16()),
    (pa.uint64(), pa.decimal128(20, 0)),
    (pa.float16(), pa.float32()),
    (pa.large_string(), pa.string()),
    (pa.large_binary(), pa.binary()),
    (pa.binary(4), pa.binary()),
    (pa.dictionary(pa.int32(), pa.large_string()), pa.string()),
    (pa.decimal128(10, 2), pa.decimal128(10, 2)),
    (pa.large_list(pa.uint16()), pa.list_(pa.int32())),
    (pa.map_(pa.large_string(), pa.uint32()), pa.map_(pa.string(), pa.int64())),
    (pa.timestamp("ns"), pa.timestamp("us", tz="UTC")),
])
def test_each_type_fabric_cant_read_becomes_one_it_can(given, fabric):
    assert d._fabric_type(given, "c") == fabric


@pytest.mark.parametrize("given, message", [
    (pa.decimal256(40, 2), "40 digits"),
    (pa.null(), "NULL in every row"),
    (pa.time64("us"), "TIME"),
    (pa.duration("s"), "INTERVAL"),
    (pa.month_day_nano_interval(), "INTERVAL"),
])
def test_a_type_delta_cant_hold_says_how_to_cast_it(given, message):
    with pytest.raises(ValueError, match=message):
        d._fabric_type(given, "c")


def test_no_columns_or_bad_names_are_refused_and_names_can_be_left_unchecked():
    with pytest.raises(ValueError, match="no columns"):
        d.fabric_schema(pa.schema([]))
    with pytest.raises(ValueError, match="differ only in case"):
        d.fabric_schema(pa.schema([("Id", pa.int32()), ("id", pa.int32())]))
    assert d.fabric_schema(pa.schema([("a b", pa.uint8())]), names=False).field(0).type == pa.int16()


@pytest.mark.parametrize("t, writable", [
    (pa.dictionary(pa.int32(), pa.string()), True),
    (pa.dictionary(pa.int32(), pa.uint8()), False),
    (pa.map_(pa.string(), pa.int64()), True),
    (pa.map_(pa.string(), pa.uint8()), False),
    (pa.struct([("a", pa.int32()), ("b", pa.time32("s"))]), False),
    (pa.list_(pa.float16()), False),
    (pa.decimal256(39, 0), False),
])
def test_what_delta_rs_writes_as_it_is(t, writable):
    assert d.delta_writable(t) is writable


def test_naive_timestamps_are_found_inside_dictionaries_and_maps():
    schema = pa.schema([("m", pa.map_(pa.timestamp("us"), pa.timestamp("us", tz="UTC"))),
                        ("dict", pa.dictionary(pa.int8(), pa.string()))])
    assert d.naive_timestamps(schema) == ["m{key}"]


# --------------------------------------------------------------------------- appends that would change a value

@pytest.mark.parametrize("src, dst, keeps", [
    (pa.int32(), pa.decimal128(20, 0), True),
    (pa.int16(), pa.float32(), True),
    (pa.int32(), pa.float32(), False),
    (pa.int32(), pa.float64(), True),
    (pa.int64(), pa.float64(), False),
    (pa.int32(), pa.string(), False),
    (pa.float64(), pa.float32(), False),
    (pa.decimal128(10, 2), pa.decimal128(12, 2), True),
    (pa.decimal128(10, 2), pa.float64(), False),
    (pa.date32(), pa.timestamp("us", tz="UTC"), True),
    (pa.timestamp("us"), pa.date32(), False),
    (pa.large_string(), pa.string(), True),
    (pa.string(), pa.int32(), False),
    (pa.large_binary(), pa.binary(), True),
    (pa.binary(), pa.string(), False),
    (pa.bool_(), pa.bool_(), True),
    (pa.bool_(), pa.int8(), False),
    (pa.list_(pa.int32()), pa.large_list(pa.int64()), True),
    (pa.list_(pa.float64()), pa.list_(pa.int64()), False),
    (pa.map_(pa.string(), pa.int32()), pa.map_(pa.string(), pa.int64()), True),
    (pa.map_(pa.string(), pa.float64()), pa.map_(pa.string(), pa.int32()), False),
    (pa.struct([("A", pa.int32()), ("new", pa.string())]), pa.struct([("a", pa.int64())]), True),
    (pa.struct([("a", pa.float64())]), pa.struct([("a", pa.int32())]), False),
    (pa.struct([("a", pa.int32())]), pa.string(), False),
    (pa.null(), pa.int32(), True),
    (pa.time32("s"), pa.time32("s"), True),
    (pa.time32("s"), pa.time64("us"), False),
])
def test_an_append_keeps_every_value_only_where_the_tables_type_holds_it(src, dst, keeps):
    assert d._keeps_values(src, dst) is keeps


@pytest.mark.parametrize("src, dst, ok", [
    (pa.int32(), pa.float64(), True),
    (pa.date32(), pa.timestamp("us"), True),
    (pa.string(), pa.string(), True),
    (pa.binary(), pa.binary(), True),
    (pa.list_(pa.int32()), pa.list_(pa.int64()), True),
    (pa.list_(pa.string()), pa.list_(pa.int64()), False),
    (pa.map_(pa.string(), pa.int32()), pa.map_(pa.string(), pa.float64()), True),
    (pa.struct([("a", pa.int32())]), pa.struct([("a", pa.int64())]), True),
    (pa.struct([("a", pa.int32())]), pa.struct([("b", pa.int32())]), False),
    (pa.string(), pa.int32(), False),
])
def test_rows_fit_a_column_of_the_same_kind(src, dst, ok):
    assert d._assignable(src, dst) is ok


def test_what_doesnt_fit_the_table_is_named():
    source = pa.schema([("id", pa.int32()), ("note", pa.string()), ("extra", pa.int8())])
    target = pa.schema([("id", pa.int64()), ("note", pa.int32()), ("gone", pa.string())])
    assert d.fits(source, target) == ["gone is missing", "note is string, the table has int32", "extra is new"]


# --------------------------------------------------------------------------- rows made to the table's types

def test_rows_are_matched_by_name_or_position_and_a_value_that_doesnt_fit_stops_them():
    target = pa.schema([("id", pa.int16()), ("name", pa.string())])
    by_name = pa.RecordBatch.from_pydict({"name": ["a"], "id": pa.array([1], pa.int64())})
    out = d.Conform([by_name], target).reader.read_all()
    assert out.schema == target and out.to_pylist() == [{"id": 1, "name": "a"}]
    by_position = pa.RecordBatch.from_pydict({"x": pa.array([2], pa.int64()), "y": ["b"]})
    assert d.Conform([by_position], target, by_position=True).reader.read_all().to_pylist() == [{"id": 2, "name": "b"}]
    same = pa.RecordBatch.from_pydict({"id": pa.array([3], pa.int16()), "name": ["c"]}, schema=target)
    assert d.Conform([same], target).reader.read_all().num_rows == 1
    too_big = pa.RecordBatch.from_pydict({"id": pa.array([70000], pa.int64()), "name": ["d"]})
    conform = d.Conform([too_big], target)
    with pytest.raises(ValueError, match="column id doesn't fit"):
        conform.reader.read_all()
    assert "column id" in conform.error


def test_only_columns_delta_rs_cant_write_change_and_an_all_null_column_takes_the_tables_type():
    plain = pa.schema([("id", pa.int64()), ("ts", pa.timestamp("ns"))])
    assert d.delta_safe(plain) is plain  # written today as it is: nothing changes
    asked = []

    def existing():
        asked.append(1)
        return {"note": pa.string()}
    schema = pa.schema([("id", pa.uint8()), ("note", pa.null()), ("Other", pa.null())], metadata={b"k": b"v"})
    with pytest.raises(ValueError, match="Other is NULL in every row"):
        d.delta_safe(schema, existing)
    out = d.delta_safe(pa.schema([("id", pa.uint8()), ("Note", pa.null()), ("x", pa.int32())], metadata={b"k": b"v"}),
                       existing)
    assert [f.type for f in out] == [pa.int16(), pa.string(), pa.int32()] and out.metadata == {b"k": b"v"}
    assert len(asked) == 2  # once a write, however many columns are NULL


def test_naive_timestamps_inside_a_struct_are_named_by_their_path():
    schema = pa.schema([("s", pa.struct([("at", pa.timestamp("us")), ("n", pa.int32())]))])
    assert d.naive_timestamps(schema) == ["s.at"]


def test_a_table_read_at_something_other_than_a_version_or_time_is_refused():
    tree = sqlglot.parse_one("SELECT * FROM sales AT (STREAM => 'x')", read="snowflake")
    with pytest.raises(ValueError, match="as of a VERSION or a TIMESTAMP"):
        d.rewrite_tree(tree)
