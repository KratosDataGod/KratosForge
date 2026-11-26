"""A set operation inside another: the outer one's cast goes into each SELECT of the inner one, so each is
first cast to the inner one's widened type, also where DuckDB would widen it by itself (Spark's
WidenSetOperationTypes widens the inner one first). Inferred from Spark's rules, not run on Spark."""
from __future__ import annotations

import pytest

from fabric_desktop.notebook import Kernel


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    import os
    home = str(tmp_path_factory.mktemp("home"))
    old = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "HOME")}
    os.environ.update(LOCALAPPDATA=home, HOME=home)
    yield Kernel().namespace["spark"]
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.parametrize("query, values", [
    ("SELECT 1e20D AS a UNION ALL SELECT 1 UNION ALL SELECT 'x'", ["1.0E20", "1.0", "x"]),
    ("SELECT TIMESTAMP '2024-01-01 10:00:00' AS a UNION ALL SELECT DATE '2024-02-02' UNION ALL SELECT 'x'",
     ["2024-01-01 10:00:00", "2024-02-02 00:00:00", "x"]),
    ("SELECT CAST(1.5 AS FLOAT) AS a UNION ALL SELECT 2 UNION ALL SELECT 'x'", ["1.5", "2.0", "x"]),
    ("SELECT 1 AS a UNION ALL SELECT 2L UNION ALL SELECT 'x'", ["1", "2", "x"]),
    ("SELECT 1.5 AS a UNION ALL SELECT 2 UNION ALL SELECT 'x'", ["1.5", "2.0", "x"]),
    ("SELECT 'x' AS a UNION ALL SELECT 1 UNION ALL SELECT 2.5D", ["x", "1", "2.5"]),
])
def test_an_inner_set_operations_value_is_widened_before_an_outer_cast_writes_it(spark, query, values):
    df = spark.sql(query)
    assert df.dtypes == [("a", "string")]
    assert [r[0] for r in df.collect()] == values


def test_a_set_operation_with_no_outer_cast_keeps_its_values_and_types(spark):
    df = spark.sql("SELECT 1 AS a, DATE '2024-02-02' AS d UNION ALL SELECT 2.5D, TIMESTAMP '2024-01-01 10:00:00' "
                   "UNION ALL SELECT 3, NULL")
    assert df.dtypes == [("a", "double"), ("d", "timestamp")]
    assert [r.a for r in df.collect()] == [1.0, 2.5, 3.0]


def test_a_nested_intersect_and_except_widen_the_same_way(spark):
    rows = spark.sql("(SELECT 1 AS a INTERSECT SELECT 1e0D) UNION ALL SELECT 'x'").collect()
    assert sorted(r[0] for r in rows) == ["1.0", "x"]
    rows = spark.sql("SELECT 1 AS a EXCEPT SELECT 2e0D UNION ALL SELECT 'x'").collect()
    assert sorted(r[0] for r in rows) == ["1.0", "x"]
