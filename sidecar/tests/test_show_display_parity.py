"""WR-133: ``df.show()`` and ``display(df)`` on this PC against Fabric (Apache Spark 3.5).

Each expected ``show`` text is what Spark 3.5's Dataset.showString builds for the same data: cells
cast with ToPrettyString (null is ``NULL`` since SPARK-43063 in 3.5.0; it was ``null`` before),
a cell past ``truncate`` cut to ``truncate - 3`` characters plus ``...`` (just cut below 4), cells
right-aligned when ``truncate > 0`` and left-aligned when it is 0, widths counted as Java counts
(UTF-16 units, East Asian wide characters two), and the ``only showing top N rows`` footer when
more rows exist. PySpark's ``show`` prints that text, so one more newline ends it."""

from __future__ import annotations

import pytest

from fabric_desktop import notebook
from fabric_desktop import spark_compat as SC
from fabric_desktop import spark_local


@pytest.fixture(scope="module")
def spark():
    spark_local._alias_pyspark()
    return spark_local.session()


def test_default_show_is_20_rows_and_cuts_cells_at_20_characters(spark):
    df = spark.createDataFrame([(i, "x" * (17 + i)) for i in range(25)], ["i", "s"])
    text = SC.show_string(df)
    lines = text.splitlines()
    assert lines[:3] == ["+---+--------------------+", "|  i|                   s|", "+---+--------------------+"]
    assert lines[3] == "|  0|   xxxxxxxxxxxxxxxxx|"            # 17 characters: kept
    assert lines[6] == "|  3|xxxxxxxxxxxxxxxxxxxx|"            # exactly 20: kept
    assert lines[7] == "|  4|xxxxxxxxxxxxxxxxx...|"            # 21: 17 and "..."
    assert len(lines) == 3 + 20 + 2
    assert lines[-2:] == ["+---+--------------------+", "only showing top 20 rows"]
    assert text.endswith("rows\n")


def test_truncate_k_uses_that_width_and_right_aligns(spark):
    df = spark.createDataFrame([("abcdefghij", "ab")], ["long", "s"])
    assert SC.show_string(df, truncate=6) == (
        "+------+---+\n"
        "|  long|  s|\n"
        "+------+---+\n"
        "|abc...| ab|\n"
        "+------+---+\n")
    # Below 4 a cell is just cut; a column name is never cut.
    assert SC.show_string(df, truncate=3) == (
        "+----+---+\n"
        "|long|  s|\n"
        "+----+---+\n"
        "| abc| ab|\n"
        "+----+---+\n")


def test_truncate_false_keeps_whole_cells_left_aligned(spark):
    df = spark.createDataFrame([("abcdefghijklmnopqrstuvwxyz", 1)], ["s", "n"])
    assert SC.show_string(df, truncate=False) == (
        "+--------------------------+---+\n"
        "|s                         |n  |\n"
        "+--------------------------+---+\n"
        "|abcdefghijklmnopqrstuvwxyz|1  |\n"
        "+--------------------------+---+\n")


def test_truncate_goes_through_int_as_pyspark_does(spark):
    df = spark.createDataFrame([("abcdefghij",)], ["s"])
    assert SC.show_string(df, truncate="6") == SC.show_string(df, truncate=6.9) == SC.show_string(df, truncate=6)
    with pytest.raises(TypeError, match="`truncate` should be a bool, got str"):
        SC.show_string(df, truncate="six")
    with pytest.raises(TypeError, match="`vertical` should be a bool"):  # PySpark checks vertical first
        SC.show_string(df, truncate="six", vertical=1)


def test_footer_says_row_for_one_and_rows_otherwise(spark):
    df = spark.range(3)
    assert SC.show_string(df, 1) == "+---+\n| id|\n+---+\n|  0|\n+---+\nonly showing top 1 row\n"
    assert SC.show_string(df, 2).endswith("+---+\nonly showing top 2 rows\n")
    assert SC.show_string(df, 3) == "+---+\n| id|\n+---+\n|  0|\n|  1|\n|  2|\n+---+\n"   # no more: no footer
    assert SC.show_string(df, -1) == "+---+\n| id|\n+---+\n+---+\nonly showing top 0 rows\n"  # n.max(0)


def test_vertical_layout(spark):
    df = spark.createDataFrame([(1, "Alice", None), (2, "Bob", "x")], "id int, name string, note string")
    assert SC.show_string(df, vertical=True) == (
        "-RECORD 0-----\n"
        " id   | 1     \n"
        " name | Alice \n"
        " note | NULL  \n"
        "-RECORD 1-----\n"
        " id   | 2     \n"
        " name | Bob   \n"
        " note | x     \n")
    assert SC.show_string(df, 1, vertical=True).endswith(" note | NULL  \nonly showing top 1 row\n")
    assert SC.show_string(df.limit(0), vertical=True) == "(0 rows)\n"


def test_null_is_NULL_in_spark_35_at_top_level_and_nested(spark):
    df = spark.sql("select cast(null as int) as n, array(1, null) as a, map('k', cast(null as int)) as m, "
                   "named_struct('x', 1, 'y', cast(null as string)) as st")
    assert SC.show_string(df) == (
        "+----+---------+-----------+---------+\n"
        "|   n|        a|          m|       st|\n"
        "+----+---------+-----------+---------+\n"
        "|NULL|[1, NULL]|{k -> NULL}|{1, NULL}|\n"
        "+----+---------+-----------+---------+\n")


def test_values_render_as_spark_casts_them_for_show(spark):
    df = spark.sql(
        "select true as b, date'2024-01-02' as d, timestamp'2024-01-02 03:04:05.120' as ts, "
        "timestamp'2024-01-02 03:04:05' as ts0, cast(1.10 as decimal(5,2)) as dec, 1.0D as dbl, 1e20D as big, "
        "array(1, 2) as arr, map('a', 1) as m, named_struct('n', 1, 's', 'x') as st, "
        "array(named_struct('n', 1, 's', 'x')) as aos")
    assert SC.show_string(df, truncate=False) == (
        "+----+----------+----------------------+-------------------+----+---+------+------+--------+------+--------+\n"
        "|b   |d         |ts                    |ts0                |dec |dbl|big   |arr   |m       |st    |aos     |\n"
        "+----+----------+----------------------+-------------------+----+---+------+------+--------+------+--------+\n"
        "|true|2024-01-02|2024-01-02 03:04:05.12|2024-01-02 03:04:05|1.10|1.0|1.0E20|[1, 2]|{a -> 1}|{1, x}|[{1, x}]|\n"
        "+----+----------+----------------------+-------------------+----+---+------+------+--------+------+--------+\n")


def test_binary_and_empty_string(spark):
    df = spark.createDataFrame([(b"\x00\x01\xff", "")], "bin binary, e string")
    assert SC.show_string(df) == (
        "+----------+---+\n"
        "|       bin|  e|\n"
        "+----------+---+\n"
        "|[00 01 FF]|   |\n"
        "+----------+---+\n")


def test_widths_and_cuts_count_utf16_units_as_java_does(spark):
    df = spark.createDataFrame([("\U0001F600" * 5,)], ["x"])  # 5 emoji: 10 UTF-16 units in Java
    assert SC.show_string(df) == "+----------+\n|         x|\n+----------+\n|\U0001F600\U0001F600\U0001F600\U0001F600\U0001F600|\n+----------+\n"
    # 22 units > 7: substring(0, 4) is two emoji, then "..."
    df = spark.createDataFrame([("\U0001F600" * 11,)], ["x"])
    assert SC.show_string(df, truncate=7) == "+-------+\n|      x|\n+-------+\n|\U0001F600\U0001F600...|\n+-------+\n"


def test_show_prints_the_text_and_a_newline(spark, capsys):
    spark.range(1).show()
    assert capsys.readouterr().out == "+---+\n| id|\n+---+\n|  0|\n+---+\n\n"


def test_display_keeps_1000_rows_as_fabric_and_nulls_stay_null(spark):
    df = spark.sql("select id, case when id % 2 = 0 then null else id end as maybe from range(1500)")
    out = notebook.display(df)
    assert out["type"] == "table"
    assert len(out["rows"]) == notebook.PREVIEW_ROWS == 1000
    assert out["rows"][0] == [0, None]   # a missing value is JSON null; the app's table marks it "null"
    assert out["rows"][1] == [1, 1]
