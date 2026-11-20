"""Spark SQL built-ins on this PC at their exact edges (boundary re-score of batch 8, mutation batch 158,
docs/quality/mutation-testing.md). Expected values are what Spark gives for the same query."""

from __future__ import annotations

from fabric_desktop import sql


def one(query: str):
    return next(iter(sql.run(query).to_pylist()[0].values()))


def test_elt_with_one_choice():
    assert one("SELECT elt(1, 'a')") == "a"


def test_shiftleft_of_a_literal_just_past_int_is_a_long():
    assert one("SELECT shiftleft(2147483648, 1)") == 4294967296
    assert one("SELECT shiftleft(2147483647, 1)") == -2  # an int, wrapping as Java's does


def test_window_drops_a_row_with_no_time():
    assert one("SELECT count(*) FROM (SELECT window(t, '1 hour') AS w "
               "FROM VALUES (TIMESTAMP '2026-01-01 00:30:00'), (NULL) AS v(t))") == 1


def test_shiftleft_of_the_smallest_int_literal_stays_an_int():
    assert one("SELECT shiftleft(-2147483648, 1)") == 0  # Spark reads -2147483648 as an int


def test_a_shift_of_an_int_column_is_an_int_and_of_a_long_a_long():
    # Spark's shiftleft, shiftright and shiftrightunsigned of a tinyint, smallint or int is an int (WR-116)
    table = sql.run("SELECT shiftleft(i, 1) a, shiftright(i, 1) b, shiftrightunsigned(i, 1) c, shiftleft(s, 1) d, "
                    "shiftleft(l, 1) e, shiftrightunsigned(l, 1) f FROM VALUES "
                    "(CAST(-5 AS INT), CAST(5 AS SMALLINT), CAST(5 AS BIGINT)) t(i, s, l)")
    assert [str(f.type) for f in table.schema] == ["int32", "int32", "int32", "int32", "int64", "int64"]
    assert table.to_pylist() == [{"a": -10, "b": -3, "c": 2147483645, "d": 10, "e": 10, "f": 2}]


def test_typeof_a_shift_of_an_int_column_in_spark_sql_is_int():
    from fabric_desktop.notebook import Kernel
    out = Kernel().run("print(spark.sql(\"SELECT typeof(shiftleft(i, 1)) t, typeof(shiftleft(l, 1)) u FROM VALUES "
                       "(CAST(1 AS INT), CAST(1 AS BIGINT)) v(i, l)\").collect())")
    text = "".join(o.get("text", "") for o in out["outputs"])
    assert "Row(t='int', u='bigint')" in text, out


def test_a_negative_literal_past_pythons_digit_limit_reads_as_spark_reads_it():
    # Spark reads a literal past decimal(38) as a double: -1e5000 is -inf, and cast to an int its smallest
    nines = "9" * 5000
    got = sql.run(f"SELECT -{nines} a, CAST(-{nines} AS INT) b").to_pylist()
    assert got == [{"a": float("-inf"), "b": -2147483648}]
