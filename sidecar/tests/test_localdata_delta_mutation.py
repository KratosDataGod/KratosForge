"""localdata/delta.py: names, statement parsing, Fabric-safe types and what an append may take.

These assertions pin each answer exactly; a mutation run found the branches below changeable
without a failing test. A wrong answer here writes a table Fabric can't read, or refuses one it can."""

from __future__ import annotations

import pyarrow as pa
import pytest
import sqlglot

from fabric_desktop.localdata import delta as D


def _delta(schema, table):
    return True


def _not_delta(schema, table):
    return False


# --------------------------------------------------------------------------- names

@pytest.mark.parametrize("raw, name", [
    ('"a""b"', 'a"b'),
    ("`a``b`", "a`b"),
    ('"ab"', "ab"),
    ('"', '"'),              # one quote alone is not a quoted name
    ('"ab`', '"ab`'),        # quotes that don't match are left alone
    ("'ab'", "'ab'"),        # a string literal is not a name
    ("  plain  ", "plain"),
])
def test_unquote(raw, name):
    assert D.unquote(raw) == name


@pytest.mark.parametrize("raw, parts", [
    ("s.t", ("s", "t")),
    ('"my s" . "my t"', ("my s", "my t")),
    ("t", (None, "t")),
    ("a.b.c", (None, "a.b.c")),   # anything else comes back whole, for check_name to refuse
])
def test_split_name(raw, parts):
    assert D.split_name(raw) == parts


@pytest.mark.parametrize("schema, table", [("dbo", "__fd_x"), ("__FD_s", "t"), ("dbo", "1t"), ("dbo", "a b")])
def test_check_name_refuses_reserved_and_bad_names(schema, table):
    with pytest.raises(ValueError, match="can't be a Delta"):
        D.check_name(schema, table)


def test_check_name_takes_a_plain_name():
    assert D.check_name("dbo", "sales_2026") is None


# --------------------------------------------------------------------------- parse (the text path)

def test_parse_insert_overwrite_and_append():
    over = D.parse("INSERT OVERWRITE s.t SELECT 1", _delta)
    assert (over.schema, over.table, over.mode, over.query, over.by_position) == ("s", "t", "overwrite", "SELECT 1", True)
    app = D.parse("insert into t by name select 1;", _delta)
    assert (app.schema, app.mode, app.query, app.by_position) == (None, "append", "select 1", False)
    assert (app.if_not_exists, app.replace, app.partition_by) == (False, False, [])


def test_parse_leaves_an_insert_into_another_table_to_duckdb():
    assert D.parse("INSERT INTO t SELECT 1", _not_delta) is None


def test_parse_refuses_a_create_using_delta_it_cannot_read():
    with pytest.raises(ValueError, match="CREATE TABLE name USING DELTA"):
        D.parse("CREATE TABLE t USING DELTA SELECT 1", _delta)
    # USING DELTA in a statement that isn't a CREATE is not this mistake.
    assert D.parse("SELECT 'USING DELTA'", _delta) is None


def test_parse_create_with_partitions():
    w = D.parse('CREATE OR REPLACE TABLE s.t USING DELTA PARTITIONED BY (a, "b") AS SELECT 1 AS a, 2 AS b', _delta)
    assert (w.mode, w.replace, w.if_not_exists, w.partition_by) == ("overwrite", True, False, ["a", "b"])


def test_rows_must_be_a_query():
    assert D._rows(" SELECT 1 ;; ") == "SELECT 1"
    with pytest.raises(ValueError, match="needs a query"):
        D._rows("DELETE FROM t")


# --------------------------------------------------------------------------- parse_tree (the Spark path)

def _spark(sql):
    return sqlglot.parse_one(sql, read="spark")


def test_parse_tree_create_and_insert():
    w = D.parse_tree(_spark("CREATE TABLE s.t USING DELTA AS SELECT 1 AS a"), _delta)
    assert (w.schema, w.table, w.mode, w.replace) == ("s", "t", "create", False)
    assert "SELECT" in w.query.upper()
    i = D.parse_tree(_spark("INSERT OVERWRITE TABLE t SELECT 1"), _delta)
    assert (i.schema, i.table, i.mode) == (None, "t", "overwrite")
    assert D.parse_tree(_spark("INSERT INTO t SELECT 1"), _not_delta) is None


def test_parse_tree_column_list_only_refused_for_a_delta_table():
    with pytest.raises(ValueError, match="column list"):
        D.parse_tree(_spark("INSERT INTO t (a) SELECT 1"), _delta)
    assert D.parse_tree(_spark("INSERT INTO t (a) SELECT 1"), _not_delta) is None


# --------------------------------------------------------------------------- AS OF

def test_rewrite_as_of_numbers_each_placeholder_from_start_and_keeps_the_alias():
    sql, found = D.rewrite_as_of("SELECT * FROM a AT (VERSION => 1) x JOIN b AT (VERSION => 2) ON true", start=3)
    assert [f.placeholder for f in found] == ["__fd_as_of_3", "__fd_as_of_4"]
    assert [(f.table, f.version) for f in found] == [("a", 1), ("b", 2)]
    assert "__fd_as_of_3 AS x" in sql
    assert "__fd_as_of_4 AS b" in sql   # no alias: the table's own name stays usable


def test_rewrite_as_of_leaves_other_sql_alone():
    assert D.rewrite_as_of("SELECT 1") == ("SELECT 1", [])


def test_as_of_time_is_utc_unless_it_says_otherwise():
    assert D.as_of_time("2026-10-01").utcoffset().total_seconds() == 0
    assert D.as_of_time("2026-10-01T00:00:00+02:00").utcoffset().total_seconds() == 7200


# --------------------------------------------------------------------------- Fabric-safe types

def test_fabric_schema_makes_every_column_nullable():
    out = D.fabric_schema(pa.schema([pa.field("a", pa.int32(), nullable=False)]))
    assert out.field("a").nullable is True
    out = D.fabric_schema(pa.schema([pa.field("a b", pa.int32(), nullable=False)]), names=False)
    assert out.field("a b").nullable is True


def test_fabric_schema_refuses_an_empty_column_name():
    with pytest.raises(ValueError, match="column mapping"):
        D.fabric_schema(pa.schema([pa.field("", pa.int32())]))


@pytest.mark.parametrize("precision, ok", [(38, True), (39, False)])
def test_decimal_precision_limit_is_38(precision, ok):
    t = pa.decimal256(precision, 2)
    assert D.delta_writable(t) is ok
    if ok:
        assert D._fabric_type(t, "c") == pa.decimal128(38, 2)
    else:
        with pytest.raises(ValueError, match="up to 38"):
            D._fabric_type(t, "c")


def test_strings_and_binaries_stay_as_they_are():
    # Fabric reads these as they are: the fallbacks for older pyarrow must not turn them into strings.
    assert D._fabric_type(pa.string(), "c") == pa.string()
    assert D._fabric_type(pa.int64(), "c") == pa.int64()
    assert D._fabric_type(pa.large_string(), "c") == pa.string()
    assert D._fabric_type(pa.binary(4), "c") == pa.binary()


def test_delta_safe_gives_an_all_null_column_the_tables_type():
    schema = pa.schema([pa.field("A", pa.null()), pa.field("u", pa.uint8())])
    asked = []

    def existing():
        asked.append(1)
        return {"a": pa.date32()}

    out = D.delta_safe(schema, existing)
    assert out.field("A").type == pa.date32()
    assert out.field("A").nullable is True
    assert out.field("u").type == pa.int16()
    assert asked == [1]
    # With no table to look at, an all-NULL column is fabric_schema's plain error.
    with pytest.raises(ValueError, match="NULL in every row"):
        D.delta_safe(pa.schema([pa.field("A", pa.null())]))


# --------------------------------------------------------------------------- what an append may take

@pytest.mark.parametrize("src, dst, ok", [
    (pa.date32(), pa.timestamp("us", tz="UTC"), True),
    (pa.timestamp("ms"), pa.timestamp("us", tz="UTC"), True),
    (pa.string(), pa.timestamp("us"), False),
    (pa.timestamp("us"), pa.date32(), False),
    (pa.string(), pa.string(), True),
    (pa.binary(), pa.binary(), True),
    (pa.string(), pa.binary(), False),
    (pa.binary(), pa.string(), False),
    (pa.list_(pa.int32()), pa.list_(pa.int64()), True),
    (pa.list_(pa.int32()), pa.list_(pa.string()), False),
    (pa.list_(pa.int32()), pa.int32(), False),
    (pa.map_(pa.string(), pa.int32()), pa.map_(pa.string(), pa.int64()), True),
    (pa.map_(pa.string(), pa.int32()), pa.map_(pa.int32(), pa.int64()), False),
    (pa.map_(pa.string(), pa.int32()), pa.map_(pa.string(), pa.string()), False),
    (pa.struct([("a", pa.int8())]), pa.struct([("a", pa.int64())]), True),
    (pa.struct([("a", pa.int8())]), pa.struct([("b", pa.int64())]), False),
])
def test_assignable(src, dst, ok):
    assert D._assignable(src, dst) is ok


def test_fits_names_each_problem():
    src = pa.schema([("a", pa.string()), ("new", pa.int32())])
    dst = pa.schema([("a", pa.int32()), ("gone", pa.int32())])
    assert D.fits(src, dst) == ["gone is missing", "a is string, the table has int32", "new is new"]


# --------------------------------------------------------------------------- Conform and Counter

def test_conform_by_name_by_default():
    target = pa.schema([("a", pa.int64()), ("b", pa.int64())])
    batch = pa.record_batch([pa.array([2], pa.int32()), pa.array([1], pa.int32())], names=["b", "a"])
    out = D.Conform(iter([batch]), target).reader.read_all()
    assert out.to_pydict() == {"a": [1], "b": [2]}


def test_conform_by_position_refuses_a_short_row():
    target = pa.schema([("a", pa.int64()), ("b", pa.int64())])
    batch = pa.record_batch([pa.array([1], pa.int32())], names=["x"])
    with pytest.raises(ValueError):
        D.Conform(iter([batch]), target, by_position=True).reader.read_all()


def test_counter_starts_at_zero():
    batch = pa.record_batch([pa.array([1, 2, 3])], names=["a"])
    c = D.Counter(pa.RecordBatchReader.from_batches(batch.schema, iter([batch])))
    assert (c.rows, c.bytes) == (0, 0)
    c.reader.read_all()
    assert (c.rows, c.bytes) == (3, batch.nbytes)


def test_parse_tree_asks_about_the_column_list_target_by_its_schema_and_name():
    asked = []

    def is_delta(schema, table):
        asked.append((schema, table))
        return False

    assert D.parse_tree(_spark("INSERT INTO s.t (a) SELECT 1"), is_delta) is None
    assert D.parse_tree(_spark("INSERT INTO t (a) SELECT 1"), is_delta) is None
    assert asked == [("s", "t"), (None, "t")]
