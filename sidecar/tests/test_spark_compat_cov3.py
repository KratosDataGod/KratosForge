"""PySpark calls added on top of DuckDB's Spark API (spark_compat.py): the paths the main tests leave out.
Each frame is built on this PC's Spark session; the helpers are called directly where no frame reaches them."""

from __future__ import annotations

import importlib
import inspect
import struct

import duckdb
import pytest

from fabric_desktop import spark_compat as sc
from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture
def F(spark):  # pyspark is DuckDB's Spark API once the session has aliased it
    return importlib.import_module("pyspark.sql.functions")


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "a", 2.0), (1, "b", 3.0), (2, "a", 4.0)], ["g", "k", "v"])


def rows(frame):
    return [tuple(r) for r in frame.collect()]


# ---------------------------------------------------------------- through a frame


def test_pivot_avg_min_max_and_count(df):
    pivoted = df.groupBy("g").pivot("k")
    assert rows(pivoted.avg("v").orderBy("g")) == [(1, 2.0, 3.0), (2, 4.0, None)]
    assert rows(pivoted.min("v").orderBy("g")) == [(1, 2.0, 3.0), (2, 4.0, None)]
    assert rows(df.groupBy("g").pivot("k").max("v").orderBy("g")) == [(1, 2.0, 3.0), (2, 4.0, None)]
    assert rows(df.groupBy("g").pivot("k").count().orderBy("g")) == [(1, 1, 1), (2, 1, 0)]


def test_expr_names_drop_a_string_literals_quotes(df, F):
    assert df.select(F.expr("concat('x', k)")).columns == ["concat(x, k)"]
    out = df.select(F.expr("'abc'"))
    assert out.columns == ["abc"] and rows(out)[0] == ("abc",)
    # an IN over a subquery has no name of Spark's own here: it is the SQL with the literal's quotes dropped
    out = df.where("g = 2").select(F.expr("k IN (SELECT 'a' LIMIT 1)"))
    assert out.columns == ["k IN (SELECT a LIMIT 1)"] and rows(out) == [(True,)]


def test_map_concat_of_one_map_flatten_to_json_and_printf(spark, df, F):
    m = spark.sql("SELECT MAP {'a': 1} AS m")
    (value,), = rows(m.select(F.map_concat("m")))
    assert (value.asDict() if hasattr(value, "asDict") else value) == {"a": 1}
    assert rows(df.limit(1).select(F.flatten(F.array(F.array(F.lit(1)), F.array(F.lit(2)))))) == [([1, 2],)]
    assert rows(df.where("g = 2").select(sc.to_json(F.struct("g")))) == [('{"g":2}',)]
    assert rows(df.where("g = 2").select(sc.printf("%d!", F.col("g")))) == [("2!",)]


def test_locate_with_its_text_as_a_keyword_still_runs(df, F):
    out = df.select(F.locate("a", str=F.col("k")))
    assert rows(out) == [(1,), (0,), (1,)]


def test_a_select_error_that_isnt_about_aggregates_is_raised(df):
    with pytest.raises(duckdb.BinderException, match="nope"):
        df.select("nope")


def test_a_field_of_a_column_the_frame_lacks_is_refused_by_the_select(spark, df, F):
    with pytest.raises(duckdb.BinderException, match="nope"):
        df.select(F.col("nope")["x"])
    assert spark.sql("SELECT {'x': 1} AS s").select(F.col("s")["x"]).columns == ["s.x"]


# ---------------------------------------------------------------- helpers


def test_col_sql_keeps_a_star():
    assert sc._col_sql("*") == "*"
    assert sc._col_sql('a"b') == '"a""b"'


def test_typed_forms_forget_the_oldest_past_their_limit(monkeypatch, F):
    monkeypatch.setattr(sc, "_TYPED", {})
    monkeypatch.setattr(sc, "_TYPED_KEPT", 1)
    sc._typed("size", F.col("a"), F.col("a"))
    sc._typed("size", F.col("b"), F.col("b"))
    assert list(sc._TYPED) == [str(F.col("b").expr)]


def test_typed_and_generator_forms_of_a_column_the_frame_cant_read(df):
    assert sc._arg_types(df, ('"nope"',)) is None
    assert sc._typed_sql(df, "size", ('"nope"',)) is None
    assert sc._generator_columns(df, "explode", '"nope"') is None


def test_an_operator_that_gives_back_not_implemented_is_passed_on(F):
    named = sc._spark_named("__add__", lambda self, other: NotImplemented)
    assert named(F.col("g"), object()) is NotImplemented


def test_when_on_a_column_that_isnt_a_case_keeps_its_result(F):
    step = sc._case_step(lambda self, *args: "result", "when")
    assert step(F.col("g"), F.col("g") == 1, F.lit(5)) == "result"


def test_function_named_keeps_a_function_without_a_signature_and_skips_arguments_it_cant_bind(F):
    def odd():
        return None
    odd.__signature__ = 5  # not a Signature: inspect can't read it
    assert sc._function_named("odd", odd) is odd

    def lenient(*args):
        return F.lit(1)
    lenient.__signature__ = inspect.Signature([])
    out = sc._function_named("lenient", lenient)(1, 2)
    assert str(out.expr) == str(F.lit(1).expr)


def test_bound_name_of_an_expression_the_frame_cant_bind(df):
    assert sc._bound_name(df, duckdb.ColumnExpression("nope")) == "nope"


def test_with_columns_given_anything_but_one_dict_passes_it_on(df):
    assert sc._decimal_with_columns(lambda self, *cols: cols)(df, "a", "b") == ("a", "b")


def test_is_grouping_of_a_name():
    assert sc._is_grouping("grouping(x)") is False


@pytest.mark.parametrize(("given", "parts"), [
    ("`a.b.*", None),          # an unclosed backquote
    ("`a`b.*", None),          # a backquoted name not followed by a dot
    ("`a.b`.c.*", ["a.b", "c"]),
    (5, None),
])
def test_star_prefix_edges(given, parts):
    assert sc._star_prefix(given) == parts


def test_fill_takes_only_a_value_of_the_columns_kind():
    assert sc._fillable("varchar", None) is False
    assert sc._fillable("varchar", ["a"]) is False
    assert sc._fillable("varchar", "a") is True


def test_struct_rows_keeps_a_value_that_is_already_a_row():
    from duckdb.experimental.spark.sql import types as T
    from duckdb.experimental.spark.sql.types import Row
    row = Row(a=1)
    assert sc._struct_rows(row, T.StructType([T.StructField("a", T.IntegerType())])) is row


def test_java_float_at_the_largest_float_skips_digits_that_round_past_it():
    biggest = struct.unpack("f", struct.pack("f", 3.4028234663852886e38))[0]
    assert sc._java_float(biggest) == "3.4028235E38"
