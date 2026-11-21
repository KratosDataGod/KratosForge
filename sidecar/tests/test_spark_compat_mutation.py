"""PySpark calls in spark_compat that mutation testing (batch 8, docs/quality/mutation-testing.md) showed
no test reached or checked: bool_or, bin, split with a limit, format_string and printf, seeded rand,
first/last, log, Window.rangeBetween, Column.substr, the bitwise methods, an infinite literal, the
functions that cast a number argument to text, grouping() under a plain groupBy, an interval in
show(). Expected values are what Spark gives for the same data."""

from __future__ import annotations

import importlib

import pytest

from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture(scope="module")
def F(spark):
    return importlib.import_module("pyspark.sql.functions")


@pytest.fixture(scope="module")
def W(spark):
    return importlib.import_module("pyspark.sql.window").Window


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "EU", 10.0, True), (2, "US", None, False), (3, "EU", 7.5, None)],
                                 ["id", "region", "amount", "flag"])


def rows(frame):
    return [tuple(r) for r in frame.collect()]


# ---- aggregates

def test_bool_or_and_some(df, F):
    got = df.agg(F.bool_or("flag"), F.some("flag"), F.bool_and("flag"), F.every("flag"))
    assert rows(got) == [(True, True, False, False)]
    assert got.columns == ["bool_or(flag)", "some(flag)", "bool_and(flag)", "every(flag)"]


def test_first_and_last_with_and_without_nulls(spark, F):
    frame = spark.createDataFrame([(1, None), (2, 5.0), (3, None)], "id int, x double").orderBy("id")
    assert rows(frame.agg(F.first("x"), F.last("x"))) == [(None, None)]
    assert rows(frame.agg(F.first("x", True), F.last("x", ignorenulls=True))) == [(5.0, 5.0)]


def test_percentile_approx_notes_only_an_accuracy_it_ignores(df, F, capsys):
    assert rows(df.agg(F.percentile_approx("id", 0.5))) == [(2,)]
    assert "accuracy is ignored" not in capsys.readouterr().out
    df.agg(F.percentile_approx("id", 0.5, 100)).collect()
    assert "accuracy is ignored" in capsys.readouterr().out


def test_grouping_under_a_plain_group_by_is_refused_as_in_spark(df, F):
    with pytest.raises(Exception, match="can only be used with GroupingSets/Cube/Rollup"):
        df.groupBy("region").agg(F.grouping("region"))
    with pytest.raises(Exception, match="can only be used with GroupingSets/Cube/Rollup"):
        df.groupBy("region").agg(F.grouping_id())
    assert sorted(rows(df.groupBy("region").agg(F.count("*"))), key=str) == [("EU", 2), ("US", 1)]


# ---- scalar functions

def test_bin_is_64_bit_twos_complement(df, F):
    assert rows(df.limit(1).select(F.bin(F.lit(-1)), F.bin(F.lit(5.7)), F.bin("id"))) == [("1" * 64, "101", "1")]


@pytest.mark.parametrize(("pattern", "limit", "expected"), [
    (",", 2, ["a", "b,c"]),
    (",", 1, ["a,b,c"]),
    (",", 3, ["a", "b", "c"]),
    (",", -1, ["a", "b", "c"]),
    ("", 1, ["a"]),
    ("", 2, ["a", "b"]),
    ("", -1, ["a", "b", "c"]),
])
def test_split_with_a_limit(df, F, pattern, limit, expected):
    text = "a,b,c" if pattern else "abc"
    assert rows(df.limit(1).select(F.split(F.lit(text), pattern, limit))) == [(expected,)]


def test_format_string_and_printf(df, F):
    assert rows(df.orderBy("id").select(F.format_string("%d-%s", "id", "region"))) == [("1-EU",), ("2-US",), ("3-EU",)]
    assert rows(df.orderBy("id").select(F.printf(F.lit("%d!"), "id"))) == [("1!",), ("2!",), ("3!",)]
    assert rows(df.orderBy("id").select(F.format_string("%s", "amount"))) == [("10.0",), ("null",), ("7.5",)]
    with pytest.raises(TypeError, match="not a column"):
        F.printf(F.col("region"), "id")


def test_rand_with_a_seed_repeats_and_stays_in_0_1(df, F):
    got = rows(df.orderBy("id").select(F.rand(1).alias("a"), F.rand(1).alias("b"), F.rand(2).alias("c")))
    assert all(a == b and 0 <= a < 1 and 0 <= c < 1 for a, b, c in got)
    assert [a for a, _, _ in got] != [c for _, _, c in got]
    assert len({a for a, _, _ in got}) == 3


def test_log_with_and_without_a_base(df, F):
    got = rows(df.orderBy("id").select(F.log("id"), F.log(2, "id"), F.log(F.lit(0))))
    assert [round(a, 6) for a, _, _ in got] == [0.0, 0.693147, 1.098612]
    assert [round(b, 6) for _, b, _ in got] == [0.0, 1.0, 1.584963]
    assert all(c is None for _, _, c in got)


@pytest.mark.parametrize("name", ["upper", "lower", "trim", "ltrim", "rtrim", "initcap"])
def test_text_functions_read_a_number_as_text(df, F, name):
    assert rows(df.limit(1).select(getattr(F, name)(F.lit(14)))) == [("14",)]


def test_more_functions_read_a_number_as_text(df, F):
    got = df.limit(1).select(F.ascii(F.lit(7)), F.length(F.lit(123)), F.translate(F.lit(123), "1", "x"),
                             F.lpad(F.lit(5), 3, "0"), F.rpad(F.lit(5), 3, "0"), F.repeat(F.lit(5), 2),
                             F.instr(F.lit(123), "2"), F.locate("2", F.lit(123)),
                             F.regexp_replace(F.lit(123), "2", "x"))
    assert rows(got) == [(55, 3, "x23", "005", "500", "55", 2, 2, "1x3")]


def test_left_and_right_read_a_number_as_text(df, F):
    assert rows(df.limit(1).select(F.left(F.lit(123), F.lit(2)), F.right(F.lit(123), F.lit(2)))) == [("12", "23")]


def test_split_reads_a_number_as_text(df, F):
    assert rows(df.limit(1).select(F.split(F.lit(1020), "0"))) == [(["1", "2", ""],)]


def test_expr_names_as_spark_names(df, F):
    assert df.select(F.expr("id + 1")).columns == ["(id + 1)"]
    assert df.select(F.expr("id")).columns == ["id"]
    assert df.select(F.expr("*")).columns == ["id", "region", "amount", "flag"]


# ---- windows

def test_window_range_between(df, F, W):
    got = df.select("id", F.sum("id").over(W.orderBy("id").rangeBetween(-1, 0)).alias("s")).orderBy("id")
    assert rows(got) == [(1, 1), (2, 3), (3, 5)]


# ---- Column methods

def test_substr_with_ints_or_columns(df, F):
    assert rows(df.orderBy("id").select(F.col("region").substr(2, 1))) == [("U",), ("S",), ("U",)]
    got = df.orderBy("id").select(F.col("region").substr(F.lit(1), F.lit(1)))
    assert rows(got) == [("E",), ("U",), ("E",)]
    assert got.columns == ["substring(region, 1, 1)"]


@pytest.mark.parametrize(("start", "length"), [(1, None), (True, 1), ("1", 1)])
def test_substr_refuses_mixed_arguments(df, F, start, length):
    with pytest.raises(TypeError, match="must both be ints or both be Columns"):
        F.col("region").substr(start, F.lit(1) if length is None else length)


def test_bitwise_methods(df, F):
    got = df.orderBy("id").select(F.col("id").bitwiseAND(3), F.col("id").bitwiseOR(4), F.col("id").bitwiseXOR(1))
    assert rows(got) == [(1, 5, 0), (2, 6, 3), (3, 7, 2)]
    assert got.columns == ["(id & 3)", "(id | 4)", "(id ^ 1)"]


def test_an_infinite_literal_compares(df, F):
    got = df.orderBy("id").select(F.col("amount") > float("-inf"), F.col("amount") < float("inf"))
    assert rows(got) == [(True, True), (None, None), (True, True)]


def test_get_field_of_a_map_and_a_struct(spark, F):
    m = spark.createDataFrame([({"a": 1},)], "m map<string,int>").select(F.col("m").getField("a"))
    assert rows(m) == [(1,)]
    assert m.columns == ["m.a"]
    s = spark.createDataFrame([((1, 2),)], "s struct<a:int,b:int>").select(F.col("s").getField("b"))
    assert rows(s) == [(2,)]
    with pytest.raises(TypeError, match="takes a field name"):
        F.col("s").getField(1)


# ---- show

def test_show_writes_an_interval_as_an_interval(spark, capsys):
    # DuckDB has one interval type, so the fields (DAY TO SECOND) are this PC's, not the literal's
    spark.sql("SELECT INTERVAL '90' MINUTE AS m").show(truncate=False)
    out = capsys.readouterr().out
    assert "INTERVAL '0 01:30:00'" in out
    assert "timedelta" not in out and "1:30:00|" not in out


def test_between_infinite_and_nan_bounds(spark, F):
    frame = spark.createDataFrame([(1, 10.0), (2, 5.0)], ["id", "amount"]).orderBy("id")
    assert rows(frame.select(F.col("amount").between(float("-inf"), float("inf")))) == [(True,), (True,)]
    # NaN is above every other double in Spark, so x <= NaN holds
    assert rows(frame.select(F.col("amount").between(0, float("nan")))) == [(True,), (True,)]


def test_window_range_between_from_the_class(df, F, W):
    got = df.select("id", F.sum("id").over(W.rangeBetween(-1, 0).orderBy("id")).alias("s")).orderBy("id")
    assert rows(got) == [(1, 1), (2, 3), (3, 5)]


def test_struct_field_and_map_key_names(spark, F):
    frame = spark.createDataFrame([((1, 2), {"a": 1})], "s struct<a:int,b:int>, m map<string,int>")
    got = frame.select(F.col("s")["a"], F.col("s").b, F.col("m")["a"])
    assert got.columns == ["s.a", "s.b", "m[a]"]
    assert rows(got) == [(1, 2, 1)]


def test_show_writes_a_map_and_a_struct_as_spark(spark, capsys):
    spark.createDataFrame([((1, 2), {"a": 1})], "s struct<a:int,b:int>, m map<string,int>").show(truncate=False)
    out = capsys.readouterr().out
    assert "|{1, 2}|{a -> 1}|" in out


def test_grouped_avg_and_mean(df, F):
    got = sorted(rows(df.groupBy("region").avg("id")), key=str)
    assert got == [("EU", 2.0), ("US", 2.0)]
    assert df.groupBy("region").mean("id").columns == ["region", "avg(id)"]


def test_dict_agg_counts_a_column_or_rows(df):
    assert df.agg({"amount": "count"}).columns == ["count(amount)"]
    assert rows(df.agg({"amount": "count"})) == [(2,)]
    assert df.agg({"*": "count"}).columns == ["count(1)"]
    assert rows(df.agg({"*": "count"})) == [(3,)]


def test_collect_makes_rows_of_structs_in_an_array(spark):
    got = spark.createDataFrame([([(1, "x")],)], "a array<struct<n:int,s:string>>").collect()
    assert got[0].a[0].n == 1 and got[0].a[0].s == "x"


def test_window_bounds_are_pysparks(W):
    import sys
    assert W.unboundedPreceding == -sys.maxsize - 1
    assert W.unboundedFollowing == sys.maxsize
    assert W.currentRow == 0


def test_the_function_list_is_this_modules_public_calls():
    from fabric_desktop import spark_compat
    extras = {"countDistinct", "sumDistinct", "count", "hash"}
    assert "install" not in spark_compat.FUNCTIONS and "spark_expr" not in spark_compat.FUNCTIONS
    assert all(not n.startswith("_") for n in spark_compat.FUNCTIONS)
    assert all(getattr(f, "__module__", "") == spark_compat.__name__
               for n, f in spark_compat.FUNCTIONS.items() if n not in extras)


def test_install_twice_changes_nothing(df, F):
    from fabric_desktop import spark_compat
    spark_compat.install()
    got = df.limit(1).select(F.upper(F.lit(14)))
    assert got.columns == ["upper(14)"]
    assert rows(got) == [("14",)]


def test_summary_takes_a_percentile_as_a_fraction_of_100(spark):
    got = rows(spark.range(1, 101).summary("100%", "50%"))
    assert got == [("100%", "100"), ("50%", "50")]


def test_drop_refuses_what_isnt_a_name_or_a_column(df):
    with pytest.raises(TypeError, match="drop\\(\\) takes column names or Columns, not int"):
        df.drop(1)


def test_select_star_of_a_struct_with_a_dot_in_its_name(spark, F):
    frame = spark.createDataFrame([((1, 2),)], "`a.b` struct<x:int,y:int>")
    got = frame.select(F.col("`a.b`.*"))
    assert got.columns == ["x", "y"]
    assert rows(got) == [(1, 2)]
    plain = spark.createDataFrame([((1, 2),)], "s struct<x:int,y:int>").select(F.col("s.*"))
    assert plain.columns == ["x", "y"]
