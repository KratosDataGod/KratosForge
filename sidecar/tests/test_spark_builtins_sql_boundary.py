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


def test_typeof_a_shift_of_an_int_column_in_a_sql_cell_is_int():
    # typeof's argument is the shift itself: the shift's own argument decides its type
    assert sql.run("SELECT typeof(shiftleft(CAST(1 AS TINYINT), 1)) a, typeof(shiftrightunsigned(i, 1)) b, "
                   "typeof(shiftleft(l, 1)) c FROM VALUES (CAST(1 AS INT), CAST(1 AS BIGINT)) v(i, l)"
                   ).to_pylist() == [{"a": "int", "b": "int", "c": "bigint"}]


def test_a_negative_literal_past_pythons_digit_limit_reads_as_spark_reads_it():
    # Spark reads a literal past decimal(38) as a double: -1e5000 is -inf, and cast to an int its smallest
    nines = "9" * 5000
    got = sql.run(f"SELECT -{nines} a, CAST(-{nines} AS INT) b, CAST(-{nines} AS TINYINT) c").to_pylist()
    assert got == [{"a": float("-inf"), "b": -2147483648, "c": 0}]  # Java: (byte) Integer.MIN_VALUE is 0


def test_a_rank_cast_to_a_smaller_type_wraps_as_spark_does_with_ansi_off():
    got = sql.run("SELECT id, CAST(row_number() OVER (ORDER BY id) AS TINYINT) v, "
                  "CAST(rank() OVER (ORDER BY id) AS SMALLINT) w FROM range(40000) ORDER BY id").to_pylist()
    assert (got[127]["v"], got[299]["v"], got[39999]["w"]) == (-128, 44, -25536)


def test_a_bad_timestamp_literal_stops_as_spark_does_and_a_good_one_reads():
    import pytest
    with pytest.raises(Exception, match="out of range"):
        sql.run("SELECT TIMESTAMP '2026-13-01 00:00:00' v")
    assert sql.run("SELECT CAST('2026-13-01' AS TIMESTAMP) v, to_timestamp('2026-13-01') w").to_pylist() == [
        {"v": None, "w": None}]  # a cast or to_timestamp of text that isn't a timestamp is NULL with ANSI off
    assert str(sql.run("SELECT TIMESTAMP '2026-01-01T10:00:00Z' v").to_pylist()[0]["v"]) == "2026-01-01 10:00:00+00:00"


def test_a_shift_of_a_shift_of_an_int_is_an_int_and_wraps_at_each_step():
    # Java: (3 << 20) << 12 overflows an int to 0; (3 << 31) >>> 1 is 1073741824
    table = sql.run("SELECT shiftleft(shiftleft(shiftleft(i, 10), 10), 12) a, shiftrightunsigned(shiftleft(i, 31), 1) b, "
                    "typeof(shiftleft(shiftleft(i, 1), 1)) c, typeof(shiftleft(shiftleft(l, 1), 1)) d, "
                    "shiftleft(shiftright(l, 1), 1) e FROM VALUES (CAST(3 AS INT), CAST(3 AS BIGINT)) v(i, l)")
    assert [str(f.type) for f in table.schema][:2] == ["int32", "int32"]
    assert table.to_pylist() == [{"a": 0, "b": 1073741824, "c": "int", "d": "bigint", "e": 2}]


def test_a_shift_of_an_int_is_an_int_in_a_local_data_project_s_spark_sql():
    from fabric_desktop.engines import local_sql
    from fabric_desktop.engines.contract import CellRequest, SessionConfig
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    result = session.execute(CellRequest("SELECT typeof(shiftleft(i, 1)) t, shiftleft(i, 31) v "
                                         "FROM VALUES (CAST(3 AS INT)) v(i)", "sql", "spark"))
    assert result.status == "ok" and result.outputs[-1]["rows"] == [["int", -2147483648]]
