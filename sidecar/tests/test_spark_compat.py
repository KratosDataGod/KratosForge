"""PySpark calls added on top of DuckDB's Spark API (spark_compat.py), checked against Spark's answers."""

import importlib

import pytest

from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


@pytest.fixture
def F():
    return importlib.import_module("pyspark.sql.functions")


@pytest.fixture
def df(spark):
    return spark.createDataFrame([(1, "EU", 10.0), (2, "US", 5.0), (3, "EU", 7.5), (4, None, None)],
                                 ["id", "region", "amount"])


def rows(frame):
    return [tuple(r) for r in frame.collect()]


def test_window_functions_with_partition_order_and_frames(spark, F, df):
    from pyspark.sql.window import Window
    w = Window.partitionBy("region").orderBy(F.col("amount").desc())
    out = df.where("region IS NOT NULL").select(
        "id", F.row_number().over(w).alias("rn"), F.rank().over(w).alias("rk"),
        F.lag("amount").over(w).alias("prev"), F.lead("amount", 1, 0.0).over(w).alias("next"),
        F.sum("amount").over(Window.partitionBy("region")).alias("total"),
        F.sum("amount").over(Window.orderBy("id").rowsBetween(Window.unboundedPreceding, Window.currentRow)).alias("run"))
    assert rows(out.orderBy("id")) == [(1, 1, 1, None, 7.5, 17.5, 10.0), (2, 1, 1, None, 0.0, 5.0, 15.0),
                                       (3, 2, 2, 10.0, 0.0, 17.5, 22.5)]
    from pyspark.sql import Window as W2
    assert W2 is Window


def test_count_star_agg_and_dict_agg(F, df):
    assert rows(df.groupBy("region").agg(F.count("*").alias("n")).orderBy("region")) == [(None, 1), ("EU", 2), ("US", 1)]
    assert rows(df.agg(F.max("amount"), F.count("*"))) == [(10.0, 4)]
    assert rows(df.agg({"amount": "sum"})) == [(22.5,)]
    assert df.agg({"amount": "sum"}).columns == ["sum(amount)"]
    with pytest.raises(ValueError, match="agg doesn't know"):
        df.agg({"amount": "median_ish"})


def test_select_expr_reads_spark_sql(df):
    out = df.selectExpr("id * 2 AS d", "upper(region) AS r", "CAST(amount AS STRING) s")
    assert out.columns == ["d", "r", "s"] and rows(out.orderBy("d"))[0] == (2, "EU", "10.0")


def test_fillna_and_na_follow_spark_types(df):
    assert rows(df.fillna(0).orderBy("id"))[-1] == (4, None, 0.0)  # numbers fill only number columns
    assert rows(df.fillna("n/a").orderBy("id"))[-1] == (4, "n/a", None)
    assert rows(df.fillna({"region": "?", "amount": 1}).orderBy("id"))[-1] == (4, "?", 1.0)
    assert rows(df.na.fill(0, subset=["id"]).orderBy("id"))[-1] == (4, None, None)
    assert df.na.drop().count() == 3 and df.dropna(how="all", subset=["region", "amount"]).count() == 3
    assert df.dropna(thresh=2).count() == 3
    assert rows(df.na.replace("EU", "Europe", "region").orderBy("id"))[0] == (1, "Europe", 10.0)


def test_pivot_with_and_without_values(F, df):
    out = df.where("region IS NOT NULL").groupBy("id").pivot("region").sum("amount")
    assert out.columns == ["id", "EU", "US"] and sorted(rows(out)) == [(1, 10.0, None), (2, None, 5.0), (3, 7.5, None)]
    out = df.groupBy().pivot("region", ["EU"]).agg(F.sum("amount"))
    assert rows(out) == [(17.5,)] and out.columns == ["EU"]
    out = df.groupBy("region").pivot("id", [1, 2]).agg(F.sum("amount").alias("t"), F.count("*").alias("n"))
    assert out.columns == ["region", "1_t", "1_n", "2_t", "2_n"]


def test_dates_and_times(spark, F):
    d = spark.createDataFrame([("2026-01-31 13:45:00",)], ["ts"]).select(F.to_timestamp("ts").alias("ts"))
    out = d.select(F.date_format("ts", "yyyy-MM-dd HH:mm").alias("f"), F.date_add("ts", 1).alias("a"),
                   F.datediff(F.lit("2026-02-10"), "ts").alias("dd"), F.trunc("ts", "MM").alias("m"))
    f, a, dd, m = out.first()
    assert (f, str(a), dd, str(m)[:10]) == ("2026-01-31 13:45", "2026-02-01", 10, "2026-01-01")
    now = spark.createDataFrame([(1,)], ["x"]).select(F.current_timestamp().alias("t")).first()[0]
    assert now.tzinfo is None


def test_arrays_and_distincts(spark, F):
    a = spark.createDataFrame([(1, [1, 2]), (2, []), (3, None)], ["id", "xs"])
    assert sorted(rows(a.select("id", F.explode("xs").alias("x")))) == [(1, 1), (1, 2)]
    assert sorted(rows(a.select("id", F.explode_outer("xs").alias("x"))), key=str) == [(1, 1), (1, 2), (2, None), (3, None)]
    assert rows(a.where("id = 1").select(F.size("xs"))) == [(2,)]
    b = spark.createDataFrame([("a", 1), ("a", 1), ("b", None)], ["k", "v"])
    assert rows(b.select(F.countDistinct("k"), F.count_distinct("k", "v"))) == [(2, 1)]
    assert sorted(b.groupBy("k").agg(F.collect_set("v").alias("s")).collect()[0]["s"] or []) in ([1], [])
    assert rows(b.select(F.max_by("k", "v"))) == [("a",)]
    ids = [r[0] for r in b.select(F.monotonically_increasing_id()).collect()]
    assert len(set(ids)) == 3


def test_format_number_rounds_half_even_and_ignored_arguments_say_so(spark, F, capsys):
    n = spark.createDataFrame([(2.345,), (0.125,), (1234567.5,)], ["x"])
    assert rows(n.select(F.format_number("x", 2))) == [("2.34",), ("0.12",), ("1,234,567.50",)]
    assert rows(n.limit(1).select(F.format_number("x", 0))) == [("2",)]
    F.percentile_approx("x", 0.5, 100)
    F.to_json("x", {"timestampFormat": "yyyy"})
    out = capsys.readouterr().out
    assert "accuracy is ignored" in out and "timestampFormat" in out


def test_describe_sample_subtract_unpivot_and_layout_calls(spark, df):
    d = {r[0]: r[1:] for r in df.describe("amount").collect()}
    assert d["count"] == ("3",) and d["max"] == ("10.0",) and d["min"] == ("5.0",)
    assert df.sample(0.0, 1).count() == 0 and df.sample(fraction=1.0).count() == 4
    with pytest.raises(NotImplementedError):
        df.sample(True, 0.5)
    other = spark.createDataFrame([(1, "EU", 10.0)], ["id", "region", "amount"])
    assert df.subtract(other).count() == 3
    long = df.where("id = 1").unpivot("id", ["region"], "k", "val")
    assert rows(long) == [(1, "region", "EU")]
    assert df.repartition(8).coalesce(1).persist().count() == 4


def test_self_join_works(df):
    assert df.join(df, "id").count() == 4
    assert df.crossJoin(df).count() == 16


def test_a_fabric_style_cell_runs_in_the_notebook_kernel():
    from fabric_desktop.notebook import Kernel
    out = Kernel().run(
        "from pyspark.sql import functions as F\n"
        "from pyspark.sql.window import Window\n"
        "df = spark.createDataFrame([(1, 'a', 3.0), (2, 'a', 1.0), (3, 'b', 2.0)], ['id', 'k', 'v'])\n"
        "w = Window.partitionBy('k').orderBy(F.col('v').desc())\n"
        "df.withColumn('rn', F.row_number().over(w)).filter('rn = 1').selectExpr('id', 'v * 10 AS v10').orderBy('id')")
    assert out["status"] == "ok", out
    assert out["outputs"][-1]["rows"] == [[1, 30.0], [3, 20.0]]


def test_spark_conf_keeps_settings_and_the_time_zone_changes_duckdb():
    spark = spark_local.session()  # the kernel test above restarted the session
    spark.conf.set("spark.sql.shuffle.partitions", 8)
    assert spark.conf.get("spark.sql.shuffle.partitions") == "8"
    spark.conf.set("spark.sql.parquet.vorder.enabled", True)
    assert spark.conf.get("spark.sql.parquet.vorder.enabled") == "true"
    assert spark.conf.get("not.set", None) is None
    with pytest.raises(KeyError):
        spark.conf.get("not.set")
    spark.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        assert spark.sql("SELECT current_setting('TimeZone') AS tz").first()[0] == "America/New_York"
    finally:
        spark.conf.unset("spark.sql.session.timeZone")
    assert spark.sql("SELECT current_setting('TimeZone') AS tz").first()[0] == "UTC"
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"


@pytest.fixture
def live():
    """The session as it is now: a test above resets it, which closes the module's one."""
    spark_local._alias_pyspark()
    return spark_local.session()


def test_na_drop_describe_sample_and_unpivot_leave_a_temp_view_called_t_alone(live):
    live.createDataFrame([(1, "a", 2.5), (2, None, None)], ["id", "v", "x"]).createOrReplaceTempView("t")
    view = live.table("t")
    assert view.na.drop().count() == 1
    assert len(view.describe().collect()) == 5
    assert view.sample(1.0, 1).count() == 2
    assert rows(view.unpivot("id", ["x"], "k", "val")) == [(1, "x", 2.5), (2, "x", None)]  # Spark keeps nulls
    assert live.table("t").count() == 2  # the view still reads its own rows


def test_two_na_drop_frames_keep_their_own_rows(live):
    a = live.createDataFrame([(1, "a"), (2, None)], ["id", "v"]).na.drop()
    b = live.createDataFrame([(9, "z")], ["id", "v"]).na.drop()
    assert (rows(a), rows(b)) == ([(1, "a")], [(9, "z")])


def test_create_dataframe_reads_a_ddl_schema_string(live):
    import datetime
    import decimal
    df = live.createDataFrame([(1, "x", decimal.Decimal("1.5"), [1, 2], datetime.date(2026, 1, 2))],
                               "id int, name string, amount decimal(10,2), tags array<int>, day date")
    assert df.dtypes == [("id", "int"), ("name", "string"), ("amount", "decimal(10,2)"),
                         ("tags", "array<int>"), ("day", "date")]
    assert live.createDataFrame([(5,)], "n: bigint").dtypes == [("n", "bigint")]
    assert live.createDataFrame([(5,)], "`my col` long").columns == ["my col"]
    with pytest.raises(ValueError, match="isn't supported on this PC"):
        live.createDataFrame([(1,)], "s geometry")


def test_ddl_schema_reads_structs_comments_and_not_null(live):
    df = live.createDataFrame([((1, ["x"]), 5)],
                              "s STRUCT<a: INT, b: ARRAY<STRING>> COMMENT 'nested, kept', id bigint NOT NULL COMMENT \"key\"")
    assert df.dtypes == [("s", "struct<a:int,b:array<string>>"), ("id", "bigint")]
    [(s, n)] = rows(df)  # a struct comes back as a Row, as Spark's collect() gives it
    assert type(s).__name__ == "Row" and s.asDict() == {"a": 1, "b": ["x"]} and n == 5
    assert live.createDataFrame([({"k": (1,)},)], "m map<string, struct<x:int>>").dtypes == [("m", "map<string,struct<x:int>>")]
    assert live.createDataFrame([(1, None)], "id int NOT NULL, name string").count() == 1
    with pytest.raises(ValueError, match="field id: This field is not nullable, but got None"):
        live.createDataFrame([(1, "a"), (None, "b")], "id int NOT NULL, name string")
    with pytest.raises(ValueError, match="field s.a: This field is not nullable"):
        live.createDataFrame([((None, "x"),)], "s struct<a int NOT NULL, b string>")


def test_an_empty_struct_says_so_instead_of_duckdbs_error(live):
    for schema in ("s struct<>", "m map<string, struct< >>", "a array<STRUCT<>>"):
        with pytest.raises(ValueError, match=r"An empty struct<> isn't supported on this PC"):
            live.createDataFrame([], schema)


def test_a_struct_type_with_a_non_nullable_field_is_checked_too(live):
    from pyspark.sql.types import IntegerType, StructField, StructType
    schema = StructType([StructField("id", IntegerType(), False)])
    with pytest.raises(ValueError, match="not nullable"):
        live.createDataFrame([{"id": None}], schema)


def test_these_frames_leave_no_view_behind(live):
    def views():
        return live.conn.execute("SELECT count(*) FROM duckdb_views() WHERE NOT internal").fetchone()[0]
    before = views()
    for i in range(50):
        df = live.createDataFrame([(i, None, 1.5), (i + 1, "b", None)], ["id", "v", "x"])
        df.dropna().count()
        df.na.drop(how="all").count()
        df.describe().count()
        df.sample(0.5, 7).count()
        df.unpivot("id", ["x"], "k", "val").count()
    assert views() == before


def test_unpivot_keeps_row_order_and_sample_with_a_seed_repeats(live):
    df = live.createDataFrame([(1, 10, 11), (2, 20, None)], ["id", "a", "b"])
    assert rows(df.unpivot("id", ["a", "b"], "k", "v")) == [(1, "a", 10), (1, "b", 11), (2, "a", 20), (2, "b", None)]
    big = live.range(10000)
    once, again = rows(big.sample(0.1, 42)), rows(big.sample(0.1, 42))
    assert once == again and 800 < len(once) < 1200
    assert big.sample(0.0).count() == 0 and big.sample(1.0, 3).count() == 10000


def test_not_null_follows_pyspark_errors_verify_schema_and_arrays_of_structs(live):
    from duckdb.experimental.spark.errors import PySparkValueError
    with pytest.raises(PySparkValueError, match=r"^\[FIELD_NOT_NULLABLE_WITH_NAME\] field id: This field is not nullable"):
        live.createDataFrame([(None,)], "id int NOT NULL")
    assert live.createDataFrame([(None,)], "id int NOT NULL", verifySchema=False).count() == 1
    schema = "items array<struct<sku: string NOT NULL, qty int>>"
    assert live.createDataFrame([([("a", 1), None],)], schema).count() == 1
    with pytest.raises(PySparkValueError, match=r"field items.element.sku"):
        live.createDataFrame([([("a", 1), (None, 2)],)], schema)
    with pytest.raises(PySparkValueError, match=r"field m.value.x"):
        live.createDataFrame([({"k": (None,)},)], "m map<string, struct<x: int NOT NULL>>")


def test_not_null_covers_array_elements_and_map_values_and_skips_schemas_without_one(live, monkeypatch):
    from duckdb.experimental.spark.errors import PySparkValueError
    from pyspark.sql.types import ArrayType, IntegerType, MapType, StringType, StructField, StructType
    tags = StructType([StructField("tags", ArrayType(StringType(), containsNull=False))])
    assert live.createDataFrame([(["a", "b"],), (None,)], tags).count() == 2  # the array itself may be null
    with pytest.raises(PySparkValueError, match=r"field tags.element: This field is not nullable"):
        live.createDataFrame([(["a", None],)], tags)
    scores = StructType([StructField("m", MapType(StringType(), IntegerType(), valueContainsNull=False))])
    with pytest.raises(PySparkValueError, match=r"field m.value:"):
        live.createDataFrame([({"k": None},)], scores)
    walked = []
    monkeypatch.setattr(spark_local, "_not_nullable", lambda where: walked.append(where) or ValueError(where))
    plain = StructType([StructField("id", IntegerType()), StructField("tags", ArrayType(StringType()))])
    assert not spark_local._refuses_null(plain)
    assert live.createDataFrame([(None, [None])], plain).count() == 1 and walked == []


def test_sample_dropna_and_unpivot_refuse_what_spark_refuses(live):
    df = live.createDataFrame([(1, None, 1.5), (2, "b", None)], ["id", "v", "x"])
    for fraction in (1.5, -0.1):
        with pytest.raises(ValueError, match=r"must be on interval \[0, 1\]"):
            df.sample(fraction)
    with pytest.raises(TypeError, match="needs a fraction"):
        df.sample()
    assert df.sample(False, 1.0, 3).count() == 2 and df.sample(0.0).count() == 0
    with pytest.raises(ValueError, match=r"how \('some'\) should be 'any' or 'all'"):
        df.dropna(how="some")
    assert df.dropna(how=None).count() == df.dropna(how="all").count() == 2  # PySpark reads None as "all"
    with pytest.raises(ValueError, match="UNPIVOT_REQUIRES_VALUE_COLUMNS"):
        df.unpivot("id", [], "k", "val")
    assert df.unpivot("id", None, "k", "val").count() == 4  # None: every column that isn't an id


def test_na_functions_and_describe_refuse_unknown_columns_and_uneven_replace_lists(live):
    from duckdb.experimental.spark.errors import AnalysisException
    df = live.createDataFrame([(1, None, 1.5), (2, "b", None)], ["id", "v", "x"])
    for call in (lambda: df.na.fill({"nope": 1}), lambda: df.na.fill(0, ["nope"]), lambda: df.dropna(subset=["nope"]),
                 lambda: df.na.replace("b", "B", ["nope"]), lambda: df.describe("nope")):
        with pytest.raises(AnalysisException, match="UNRESOLVED_COLUMN.*`nope`"):
            call()
    with pytest.raises(ValueError, match="should be of the same length. Got 2 and 1"):
        df.na.replace(["b", "c"], ["B"])
    assert rows(df.na.replace(["b"], ["B"], "v")) == [(1, None, 1.5), (2, "B", None)]
    assert rows(df.na.fill(0, "x")) == [(1, None, 1.5), (2, "b", 0.0)] and df.dropna(subset="v").count() == 1
    # Spark resolves names ignoring case
    assert df.dropna(subset=["V"]).count() == 1 and rows(df.na.fill({"X": 0})) == [(1, None, 1.5), (2, "b", 0.0)]
    assert rows(df.na.fill("z", "V"))[0][1] == "z" and rows(df.na.replace("b", "B", "V"))[1][1] == "B"
    assert df.describe("ID").columns == ["summary", "id"]


def test_dict_agg_names_columns_as_spark_and_works_after_group_by_and_pivot(live):
    from duckdb.experimental.spark.errors import AnalysisException
    df = live.createDataFrame([(1, "a", 2.0), (1, "b", 4.0), (2, "a", 6.0)], ["id", "v", "x"])
    assert df.agg({"x": "mean"}).columns == ["avg(x)"] and rows(df.agg({"x": "mean"})) == [(4.0,)]
    assert df.agg({"*": "count"}).columns == ["count(1)"] and rows(df.agg({"*": "count"})) == [(3,)]
    assert rows(df.groupBy("id").agg({"x": "sum"}).orderBy("id")) == [(1, 6.0), (2, 6.0)]
    assert df.groupBy("id").agg({"x": "sum"}).columns == ["id", "sum(x)"]
    assert df.agg({"ID": "max"}).columns == ["max(id)"]  # Spark resolves names ignoring case
    assert rows(df.groupBy("id").pivot("v").agg({"x": "sum"}).orderBy("id")) == [(1, 2.0, 4.0), (2, 6.0, None)]
    with pytest.raises(AnalysisException, match="UNRESOLVED_COLUMN.*`nope`"):
        df.groupBy("id").agg({"nope": "sum"})


def test_drop_rename_and_drop_duplicates_resolve_names_as_spark_does(live):
    from duckdb.experimental.spark.errors import AnalysisException
    df = live.createDataFrame([(1, "a"), (1, "a"), (2, "b")], ["id", "v"])
    assert df.drop("ID").columns == ["v"] and df.drop("nope").columns == ["id", "v"]
    assert df.drop(df.v).columns == ["id"]
    assert df.withColumnRenamed("nope", "z").columns == ["id", "v"]  # Spark: a no-op
    assert df.withColumnRenamed("ID", "key").columns == ["key", "v"]
    assert df.dropDuplicates(["ID"]).count() == 2 and df.drop_duplicates().count() == 2
    for call in (lambda: df.dropDuplicates(["nope"]), lambda: df.drop_duplicates(["nope"])):
        with pytest.raises(AnalysisException, match="UNRESOLVED_COLUMN.*`nope`"):
            call()


def test_dropping_every_column_says_so_instead_of_duckdbs_binder_error(live):
    df = live.createDataFrame([(1, "a")], ["id", "v"])
    for call in (lambda: df.drop("id", "V"), lambda: df.drop(*df.columns), lambda: df.drop("id", df.v)):
        with pytest.raises(ValueError, match=r"drop would leave no columns \(id, v\)"):
            call()
    assert df.drop("id", "nope").columns == ["v"]


def test_drop_takes_a_column_object_in_any_case(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([(1, "a", 2)], ["id", "v", "x"])
    assert df.drop(F.col("ID")).columns == ["v", "x"] and df.drop(F.col("`id`")).columns == ["v", "x"]
    assert df.drop(df["V"], "X").columns == ["id"]
    assert df.drop(F.col("id") + 1).columns == ["id", "v", "x"]  # not a column: nothing to drop
    with pytest.raises(ValueError, match="drop would leave no columns"):
        df.drop(F.col("ID"), F.col("V"), "x")


def test_summary_takes_statistics_and_adds_quartiles_as_spark_does(live):
    df = live.createDataFrame([(1, "a", 2.0), (2, "b", None), (3, "b", 5.0), (4, "c", 1.0)], ["id", "v", "x"])
    out = rows(df.summary())
    assert [r[0] for r in out] == ["count", "mean", "stddev", "min", "25%", "50%", "75%", "max"]
    assert out[4:7] == [("25%", "1", None, "1.0"), ("50%", "2", None, "2.0"), ("75%", "3", None, "5.0")]
    assert rows(df.summary("count", "count_distinct")) == [("count", "4", "4", "3"), ("count_distinct", "4", "3", "3")]
    with pytest.raises(ValueError, match="id is not a recognised statistic"):
        df.summary("id")  # summary takes statistics; describe takes columns
    assert len(df.select("x").summary("min").columns) == 2


def test_substring_counts_position_zero_as_one_and_concat_is_null_with_a_null(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([("hello", "a", None, 0)], "s string, a string, n string, p int")
    out = df.select(F.substring("s", 0, 2), F.substring("s", -3, 2), F.concat("a", "n"), F.concat("a", "s"))
    assert rows(out) == [("he", "ll", None, "ahello")]
    assert out.columns == ["substring(s, 0, 2)", "substring(s, -3, 2)", "concat(a, n)", "concat(a, s)"]
    assert rows(df.selectExpr("substring(s, 0, 2)", "substr(s, p, 3)", "concat(a, n)")) == [("he", "hel", None)]
    assert rows(df.select(F.expr("substring(s, 0, 3)"))) == [("hel",)]
    assert rows(live.sql("SELECT substring('hello', 0, 2) a, substr('hello', -3) b")) == [("he", "llo")]
    arrays = live.createDataFrame([([1, 2], [3], None)], "a array<int>, b array<int>, c array<int>")
    assert rows(arrays.select(F.concat("a", "b"), F.concat("a", "c"))) == [([1, 2, 3], None)]


def test_initcap_lowers_the_rest_and_concat_names_a_literal_by_its_text(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([("hELLO wORLD-x y_z", "a")], ["s", "a"])
    out = df.select(F.initcap("s"), F.concat("a", F.lit("!")), F.concat(F.lit("it's"), "a"))
    assert rows(out) == [("Hello World-x Y_z", "a!", "it'sa")]
    assert out.columns == ["initcap(s)", "concat(a, !)", "concat(it's, a)"]


def test_unix_timestamp_takes_dates_and_timestamps_and_reads_the_session_time_zone(live):
    import datetime as dt
    import pyspark.sql.functions as F
    df = live.createDataFrame([(dt.date(2024, 3, 10), dt.datetime(2024, 3, 10, 13, 5, 7), "10/03/2024", "junk")],
                              ["d", "ts", "e", "j"])
    out = df.select(F.unix_timestamp("d"), F.unix_timestamp("ts"), F.unix_timestamp("e", "dd/MM/yyyy"), F.unix_timestamp("j"))
    assert rows(out) == [(1710028800, 1710075907, 1710028800, None)]
    assert out.columns[1] == "unix_timestamp(ts, yyyy-MM-dd HH:mm:ss)"
    assert rows(df.selectExpr("unix_timestamp(ts)", "to_unix_timestamp(ts)")) == [(1710075907, 1710075907)]
    query = "SELECT unix_timestamp(timestamp'2024-03-10 13:05:07') a, unix_timestamp('2024-03-10 13:05:07') b"
    assert rows(live.sql(query)) == [(1710075907, 1710075907)]
    live.conf.set("spark.sql.session.timeZone", "America/New_York")
    try:
        assert rows(live.sql(query)) == [(1710090307, 1710090307)]  # 13:05:07 EDT
    finally:
        live.conf.set("spark.sql.session.timeZone", "UTC")


def test_unix_timestamp_reads_fractions_and_quoted_text_and_refuses_what_it_cant_read(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([("2024-03-10 13:05:07.250", "2024-03-10T13:05:07", "Mar 10, 2024")], ["a", "b", "c"])
    out = df.select(F.unix_timestamp("a", "yyyy-MM-dd HH:mm:ss.SSS"), F.unix_timestamp("b", "yyyy-MM-dd'T'HH:mm:ss"),
                    F.unix_timestamp("c", "MMM d, yyyy"))
    assert rows(out) == [(1710075907, 1710075907, 1710028800)]
    assert rows(live.sql("SELECT unix_timestamp('2024-03-10 13:05:07.250', 'yyyy-MM-dd HH:mm:ss.SSS')")) == [(1710075907,)]
    with pytest.raises(ValueError, match="can't read part of its format"):
        df.select(F.unix_timestamp("a", "yyyy-MM-dd Q"))


def test_unix_timestamp_reads_quoted_letters_and_dates_a_time_only_pattern_to_1970(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([("2024-03-10T13:05:07.250Z", "1:05 PM", "at 13", "03-10", "it's 13")],
                              ["iso", "t", "at", "md", "q"])
    out = df.select(F.unix_timestamp("iso", "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"), F.unix_timestamp("t", "h:mm a"),
                    F.unix_timestamp("at", "'at' HH"), F.unix_timestamp("md", "MM-dd"), F.unix_timestamp("q", "'it''s' HH"))
    assert rows(out) == [(1710075907, 47100, 46800, 5875200, 46800)]  # no year: 1970-01-01, as in Spark


def test_group_by_nothing_aggregates_the_frame_and_first_last_skip_nulls(live):
    import pyspark.sql.functions as F
    from pyspark.sql import Window
    df = live.createDataFrame([(1, None, 1), (1, "a", 2), (2, "b", 3), (2, None, 4)], ["k", "v", "o"])
    assert rows(df.groupBy().agg(F.sum("k"), F.max("o"))) == [(6, 4)] and rows(df.groupBy().agg({"k": "sum"})) == [(6,)]
    out = df.groupBy("k").agg(F.first("v", True), F.last("v", ignorenulls=True)).orderBy("k")
    assert rows(out) == [(1, "a", "a"), (2, "b", "b")] and out.columns == ["k", "first(v)", "last(v)"]
    window = Window.partitionBy("k").orderBy("o")
    assert rows(df.select("o", F.last("v", True).over(window)).orderBy("o")) == [(1, None), (2, "a"), (3, "b"), (4, "b")]
    query = ("SELECT k, last(v, true) l, first(v) IGNORE NULLS f, last_value(v, true) lv "
             "FROM VALUES (1, 'a'), (1, NULL) t(k, v) GROUP BY k")
    assert rows(live.sql(query)) == [(1, "a", "a", "a")]
    query = ("SELECT o, last(v, true) OVER (ORDER BY o) l FROM VALUES (1, NULL), (2, 'a'), (3, NULL) t(o, v) ORDER BY o")
    assert rows(live.sql(query)) == [(1, None), (2, "a"), (3, "a")]


def test_slice_takes_a_length_and_add_months_is_a_date_as_in_spark(live):
    import datetime as dt
    import pyspark.sql.functions as F
    df = live.createDataFrame([([3, 1, None, 2], 2, dt.date(2024, 1, 31))], "arr array<int>, n int, d date")
    out = df.select(F.slice("arr", 2, 2), F.slice("arr", -2, 2), F.slice("arr", "n", 1), F.slice("arr", 1, 10))
    assert rows(out) == [([1, None], [None, 2], [1], [3, 1, None, 2])]
    assert out.columns[0] == "slice(arr, 2, 2)"
    query = ("SELECT add_months(date'2024-01-31', 1) a, add_months('2024-01-31', 1) b, "
             "add_months(timestamp'2024-01-31 10:00:00', 1) c")
    assert rows(live.sql(query)) == [(dt.date(2024, 2, 29),) * 3]
    assert rows(df.selectExpr("add_months(d, 1)")) == [(dt.date(2024, 2, 29),)]


def test_slice_refuses_index_0_and_add_months_reads_text_as_in_spark(live):
    import datetime as dt
    import pyspark.sql.functions as F
    df = live.createDataFrame([([3, 1, 2], 0, -1, "2024-01-31", "2024-03-31 10:00:00", "bad")],
                              "arr array<int>, z int, neg int, t string, ts string, junk string")
    for col in (F.slice("arr", 0, 2), F.slice("arr", "z", 1)):
        with pytest.raises(Exception, match="index 0 is invalid"):
            rows(df.select(col))
    with pytest.raises(Exception, match="length must be greater than or equal to 0"):
        rows(df.select(F.slice("arr", 1, "neg")))
    with pytest.raises(Exception, match="index 0 is invalid"):
        rows(live.sql("SELECT slice(array(1, 2), 0, 1)"))
    assert rows(df.select(F.slice("arr", -1, 1))) == [([2],)]
    out = df.select(F.add_months("t", 1), F.add_months("ts", 1), F.add_months("junk", 1))
    assert rows(out) == [(dt.date(2024, 2, 29), dt.date(2024, 4, 30), None)]
    assert rows(df.selectExpr("add_months(t, -1)")) == [(dt.date(2023, 12, 31),)]
    named = df.select(F.add_months("t", 1), F.add_months(F.col("t"), F.lit(2)))
    assert named.columns[0] == "add_months(t, 1)"
    assert rows(named) == [(dt.date(2024, 2, 29), dt.date(2024, 3, 31))]


def test_months_between_counts_time_and_rounds_and_element_at_refuses_index_0_as_in_spark(live):
    import pyspark.sql.functions as F
    df = live.createDataFrame([("2024-03-01 10:00:00", "2024-01-31", [1, 2], 0)],
                              "a string, b string, arr array<int>, z int")
    out = df.select(F.months_between("a", "b"), F.months_between("a", "b", False))
    assert out.columns == ["months_between(a, b, true)", "months_between(a, b, false)"]
    assert rows(out)[0][0] == 1.04569892
    assert abs(rows(out)[0][1] - (2 - 30 / 31 + 10 / 24 / 31)) < 1e-12
    query = ("SELECT months_between('2024-03-31', '2024-02-29') a, months_between('2024-03-15', '2024-01-31') b, "
             "months_between(date'2024-01-01', date'2024-03-15') c, months_between('bad', '2024-01-31') d")
    assert rows(live.sql(query)) == [(1.0, 1.48387097, -2.4516129, None)]
    assert rows(df.select(F.element_at("arr", -1), F.element_at("arr", 2))) == [(2, 2)]
    assert rows(live.sql("SELECT element_at(map(0, 'z'), 0), array(1, 2)[0]")) == [("z", 1)]
    for frame in (df.select(F.element_at("arr", 0)), df.select(F.element_at("arr", F.col("z"))),
                  live.sql("SELECT element_at(array(1, 2), 0)")):
        with pytest.raises(Exception, match="index 0 is invalid"):
            rows(frame)


def test_sql_names_rewritten_functions_as_spark_and_reads_not_null_in_a_struct(live):
    out = live.sql("SELECT months_between('2024-03-01', '2024-01-31'), substring('abc', 0, 2) AS s, "
                   "slice(array(1, 2), 1, 1), upper('a')")
    assert out.columns == ["months_between('2024-03-01', '2024-01-31')", "s", "slice(array(1, 2), 1, 1)", "upper('a')"]
    assert rows(out) == [(1.03225806, "ab", [1], "A")]
    grouped = live.sql("SELECT substring(v, 0, 1), count(*) FROM VALUES ('ab'), ('ac') AS t(v) GROUP BY substring(v, 0, 1)")
    assert rows(grouped) == [("a", 2)]
    live.sql("CREATE OR REPLACE TEMP VIEW nn AS SELECT named_struct('x', 1) AS a")
    assert live.sql("SELECT CAST(a AS struct<x:int not null>) AS a FROM nn").collect()[0][0]["x"] == 1
    with pytest.raises(ValueError, match="empty struct<> isn't supported"):
        live.sql("SELECT CAST(NULL AS struct<>)")


def test_empty_struct_message_only_when_struct_is_the_error():
    from fabric_desktop import sql
    for query in ("SELECT CAST(NULL AS struct<>)", "SELECT CAST(NULL AS array<struct< >>)",
                  "SELECT CAST(NULL AS struct< >)"):
        with pytest.raises(ValueError, match="empty struct<> isn't supported"):
            sql.parse(query)
    for query in ("SELECT 'struct<>' FRM x y z", "SELECT CAST(NULL AS struct<>) FRM x y z"):
        with pytest.raises(ValueError, match="Could not read the query"):
            sql.parse(query)
    assert sql.parse("SELECT 'struct<>' AS s")
