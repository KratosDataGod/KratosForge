"""WR-122: Spark 3.5 functions with no value check (spark_coverage.VARYING), checked by behaviour on this PC.

monotonically_increasing_id's numbers depend on how Spark splits the rows, so what's checked is what Spark
promises: unique, increasing, a bigint, never null. raise_error stops a query only when a row reaches it.
input_file_name, its block functions, spark_partition_id, java_method and reflect are refused by name, never
answered with another value. The fixed answers of these functions are cases in
tools/spark_value_cases/functions.txt, for Spark to answer."""

from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def _ids(rows) -> list[int]:
    return [r[0] for r in rows]


def test_monotonically_increasing_id_is_unique_and_increasing_over_many_rows(spark):
    rows = spark.sql("SELECT monotonically_increasing_id() AS m, id FROM range(500000)").collect()
    ids = _ids(rows)
    assert len(set(ids)) == len(ids) == 500000
    assert all(a < b for a, b in zip(ids, ids[1:], strict=False))
    assert all(isinstance(i, int) and i >= 0 for i in ids)


def test_monotonically_increasing_id_is_a_bigint_never_null(spark):
    assert spark.sql("SELECT typeof(monotonically_increasing_id()), monotonically_increasing_id() IS NULL"
                     ).collect()[0][:] == ("bigint", False)


def test_monotonically_increasing_id_on_a_dataframe_is_unique_and_increasing(spark):
    from pyspark.sql import functions as F  # this PC's Spark, once the kernel has started
    ids = _ids(spark.range(200000).select(F.monotonically_increasing_id()).collect())
    assert len(set(ids)) == len(ids) and all(a < b for a, b in zip(ids, ids[1:], strict=False))


def test_monotonically_increasing_id_keeps_its_values_when_filtered_outside_its_subquery(spark):
    query = "SELECT m FROM (SELECT monotonically_increasing_id() AS m FROM range(10)) WHERE m > 6"
    kept = _ids(spark.sql(query).collect())
    assert len(kept) == 3 and kept == sorted(kept)
    assert set(kept) <= set(_ids(spark.sql("SELECT monotonically_increasing_id() FROM range(10)").collect()))


def test_monotonically_increasing_id_in_a_where_clause_is_refused_by_name(spark):
    with pytest.raises(ValueError, match=r"monotonically_increasing_id\(\) can't run on this PC.*nothing ran"):
        spark.sql("SELECT * FROM range(5) WHERE monotonically_increasing_id() > 2").collect()


def test_raise_error_stops_the_query_with_its_message(spark):
    with pytest.raises(Exception, match="custom error message"):
        spark.sql("SELECT raise_error('custom error message')").collect()
    with pytest.raises(Exception, match="big"):
        spark.sql("SELECT if(id > 1, raise_error('big'), id) FROM range(3)").collect()


def test_raise_error_stops_nothing_when_no_row_reaches_it(spark):
    assert spark.sql("SELECT CASE WHEN 1 = 0 THEN raise_error('x') ELSE 1 END").collect()[0][0] == 1
    assert _ids(spark.sql("SELECT if(id > 100, raise_error('big'), id) FROM range(3)").collect()) == [0, 1, 2]


@pytest.mark.parametrize("call", ["input_file_name()", "input_file_block_start()", "input_file_block_length()"])
def test_the_input_file_functions_are_refused_by_name_never_answered(spark, call):
    with pytest.raises(ValueError, match=r"can't run on this PC.*doesn't track the file each row was read from"):
        spark.sql(f"SELECT {call} FROM range(2)").collect()


def test_input_file_name_on_a_dataframe_is_refused_by_name(spark, tmp_path):
    from pyspark.sql import functions as F
    spark.range(3).write.mode("overwrite").parquet(str(tmp_path / "p"))
    with pytest.raises(NotImplementedError, match=r"input_file_name\(\) names the file each row was read from"):
        spark.read.parquet(str(tmp_path / "p")).select(F.input_file_name()).collect()


def test_spark_partition_id_is_refused_by_name(spark):
    with pytest.raises(ValueError, match=r"spark_partition_id\(\) can't run on this PC.*no partition number"):
        spark.sql("SELECT spark_partition_id() FROM range(3)").collect()


@pytest.mark.parametrize("call", ["java_method('java.lang.Math', 'abs', -5)", "reflect('java.lang.String', 'valueOf', 42)"])
def test_java_reflection_is_refused_by_name_even_for_a_fixed_method(spark, call):
    with pytest.raises(ValueError, match=r"can't run on this PC.*this PC runs no Java"):
        spark.sql(f"SELECT {call}").collect()
