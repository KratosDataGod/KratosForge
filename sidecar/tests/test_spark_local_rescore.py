"""Mutation batch 237: spark_local's survivors on today's main. A call is split into statements at its
semicolons only, and reset() closes the session it replaces."""
from __future__ import annotations

from fabric_desktop import spark_local as sl


def test_a_call_that_sets_the_time_zone_is_split_at_its_semicolons_only():
    pieces = sl._session_setting_pieces("SET TIME ZONE 'UTC'; SELECT 1 AS a, 2 AS b")
    assert [p.strip() for p in pieces] == ["SET TIME ZONE 'UTC'", "SELECT 1 AS a, 2 AS b"]


def test_a_single_statement_isnt_split():
    assert sl._session_setting_pieces("SET TIME ZONE 'UTC'") == []


class _Conn:
    closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self):
        self.conn = _Conn()


def test_reset_closes_the_session_it_replaces(monkeypatch):
    old = _Session()
    monkeypatch.setattr(sl, "_session", old)
    sl.reset()
    assert old.conn.closed and sl._session is None


def test_a_type_string_that_is_a_whole_struct_is_that_struct():
    df = sl.session().createDataFrame([(1, "x")], "struct<a:int,b:string>")
    assert df.dtypes == [("a", "int"), ("b", "string")]
    assert [tuple(r) for r in df.collect()] == [(1, "x")]


def test_pyspark_isnt_aliased_when_real_pyspark_can_be_imported(monkeypatch):
    import importlib.util
    import sys
    for name in [n for n in sys.modules if n == "pyspark" or n.startswith("pyspark.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object() if name == "pyspark" else None)
    sl._alias_pyspark()
    assert "pyspark" not in sys.modules


def test_a_schema_is_kept_in_a_target_of_a_lakehouse_with_schemas(monkeypatch):
    import sqlglot
    from types import SimpleNamespace

    from fabric_desktop import lakehouse
    monkeypatch.setattr(lakehouse, "_attached", SimpleNamespace(name="Sales", schemas_enabled=lambda: True))
    tree = sqlglot.parse_one("DELETE FROM sales.orders WHERE id = 1", read="spark")
    sl._in_attached(tree)
    assert (tree.this.db, tree.this.name) == ("sales", "orders")
    tree = sqlglot.parse_one("DELETE FROM dbo.orders WHERE id = 1", read="spark")
    sl._in_attached(tree)
    assert (tree.this.db, tree.this.name) == ("dbo", "orders")


def test_ints_beside_a_value_spark_infer_leaves_to_duckdb_are_still_bigint():
    import datetime
    df = sl.session().createDataFrame([(1, datetime.datetime(2026, 1, 2))], ["a", "t"])
    assert df.dtypes[0] == ("a", "bigint")


def test_named_columns_holding_a_tuple_are_inferred_as_pyspark_does():
    df = sl.session().createDataFrame([(1, (2, "x"))], ["a", "s"])
    assert df.dtypes == [("a", "bigint"), ("s", "struct<_1:bigint,_2:string>")]


def test_a_text_files_one_column_takes_the_schemas_name(tmp_path):
    from duckdb.experimental.spark.sql.types import StringType, StructField, StructType
    path = tmp_path / "x.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")
    df = sl.session().read.schema(StructType([StructField("line", StringType())])).text(str(path))
    assert df.columns == ["line"] and [r.line for r in df.collect()] == ["hello", "world"]


def test_named_columns_holding_a_dict_are_a_map_as_pyspark_infers():
    df = sl.session().createDataFrame([(1, {"k": 2})], ["a", "b"])
    assert df.dtypes == [("a", "bigint"), ("b", "map<string,bigint>")]
    assert [tuple(r) for r in df.collect()] == [(1, {"k": 2})]
