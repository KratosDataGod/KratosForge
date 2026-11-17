"""ANSI mode on this PC (WR-112).

Spark SQL here follows ANSI mode off, as Fabric runs it: Runtime 1.3 (Spark 3.5) by Spark's default and
Runtime 2.0 (Spark 4.1) by Microsoft's override (Fabric's Runtime 2.0 migration guide). Databricks SQL
on the DuckDB engine follows ANSI mode on, a SQL warehouse's default (ANSI_MODE) and Databricks Runtime
17.0's. Expected values are Spark 3.5's ANSI compliance page (CAST(2147483648L AS INT) is -2147483648
with ANSI off; overflow is "the same with the corresponding operation in a Java/Scala program") and
Databricks' ANSI_MODE page. None of this was run on Fabric or Databricks: UNVERIFIED live.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from fabric_desktop import spark_api, sql
from fabric_desktop.engines.local_sql import _databricks
from fabric_desktop.notebook import Kernel

ANSI_OFF = [
    ("CAST('abc' AS INT)", None),
    ("CAST('3000000000' AS INT)", None),
    ("CAST('12.7' AS INT)", 12),
    ("CAST(2147483648 AS INT)", -2147483648),
    ("CAST(3000000000 AS INT)", -1294967296),
    ("CAST(-2147483649 AS INT)", 2147483647),
    ("CAST(128 AS TINYINT)", -128),
    ("CAST(-129 AS TINYINT)", 127),
    ("CAST(40000 AS SMALLINT)", -25536),
    ("CAST(CAST(9223372036854775807 AS BIGINT) AS INT)", -1),
    ("CAST(1e10 AS INT)", 2147483647),
    ("CAST(-1e10 AS INT)", -2147483648),
    ("CAST(1e20 AS BIGINT)", 9223372036854775807),
    ("CAST(CAST('NaN' AS DOUBLE) AS INT)", 0),
    ("CAST(1e10 AS SMALLINT)", -1),
    ("CAST(70000.9 AS SMALLINT)", 4464),
    ("CAST(-12.7 AS INT)", -12),
    ("CAST(12345678901.5 AS INT)", -539222987),
    ("CAST(CAST(99999999999999999999 AS DECIMAL(20,0)) AS BIGINT)", 7766279631452241919),
    ("CAST(5 AS INT)", 5),
    ("CAST(true AS INT)", 1),
    ("try_cast(3000000000 AS INT)", None),
    ("1 / 0", None),
    ("1 div 0", None),
    ("5 % 0", None),
    ("CAST('2024-13-45' AS DATE)", None),
    ("CAST('2024-02-30' AS DATE)", None),
    ("CAST('abc' AS TIMESTAMP)", None),
    ("to_date('bad', 'yyyy-MM-dd')", None),
]


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def run(kernel: Kernel, code: str) -> str:
    out = kernel.run(code)
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    return text


def test_spark_sql_answers_with_ansi_mode_off(kernel: Kernel) -> None:
    text = run(kernel, f"""
import json
print('RESULT' + json.dumps([spark.sql('SELECT ' + q).collect()[0][0] for q in {[q for q, _ in ANSI_OFF]!r}],
                            default=str))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, want, g) for (q, want), g in zip(ANSI_OFF, got, strict=True) if g != want]
    assert not wrong, wrong


def test_dataframe_casts_wrap_as_spark(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from pyspark.sql import functions as F
df = spark.createDataFrame([(3000000000, 1e10, "x")], "b bigint, d double, s string")
r = df.select(F.col("b").cast("int"), F.col("d").cast("int"), F.col("b").cast("tinyint"), F.col("s").cast("int")).collect()[0]
print('RESULT' + json.dumps(list(r)))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [-1294967296, 2147483647, 0, None]


def test_an_overflowing_sum_stops_here(kernel: Kernel) -> None:
    """Spark with ANSI off wraps 2147483647 + 1 to -2147483648; this PC stops (documented)."""
    out = kernel.run("spark.sql('SELECT 2147483647 + 1').collect()")
    assert out["status"] == "error"


def test_turning_ansi_mode_on_says_this_pc_keeps_it_off(kernel: Kernel) -> None:
    text = run(kernel, "spark.conf.set('spark.sql.ansi.enabled', 'true')\nprint(spark.conf.get('spark.sql.ansi.enabled'))")
    assert spark_api.ANSI_NOTE in text and text.rstrip().endswith("true")
    assert spark_api.ANSI_NOTE not in run(kernel, "spark.conf.set('spark.sql.ansi.enabled', False)")


def _databricks_rows(query: str) -> list:
    return duckdb.connect().sql(sql.duckdb_sql(_databricks(query)[-1])).fetchall()


@pytest.mark.parametrize("query", ["SELECT 1 / 0", "SELECT 1 div 0", "SELECT 5 % 0", "SELECT 1.5 / 0",
                                   "SELECT 1 / (rand() * 0)", "SELECT CAST(1 AS DOUBLE) / 0"])
def test_databricks_sql_stops_on_a_division_by_zero(query: str) -> None:
    with pytest.raises(duckdb.Error, match=r"\[DIVIDE_BY_ZERO\]"):
        _databricks_rows(query)


def test_databricks_sql_divides_otherwise() -> None:
    assert _databricks_rows("SELECT 7 / 2, 7 div 2, 7 % 2, try_divide(1, 0)") == [(3.5, 3, 1, None)]
    assert _databricks_rows("SELECT avg(x) FROM (SELECT 1 AS x WHERE false)") == [(None,)]


def test_databricks_sql_stops_on_a_failed_cast() -> None:
    with pytest.raises(duckdb.Error, match=r"CAST_INVALID_INPUT"):
        _databricks_rows("SELECT CAST('abc' AS INT)")
    with pytest.raises(duckdb.Error):
        _databricks_rows("SELECT CAST('2024-13-45' AS DATE)")
    with pytest.raises(duckdb.Error):
        _databricks_rows("SELECT 2147483647 + 1")
