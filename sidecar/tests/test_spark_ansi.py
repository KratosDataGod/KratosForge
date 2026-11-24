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
from decimal import Decimal

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
from decimal import Decimal
print('RESULT' + json.dumps([spark.sql('SELECT ' + q).collect()[0][0] for q in {[q for q, _ in ANSI_OFF]!r}],
                            default=str))
""")
    got = json.loads(text.split("RESULT", 1)[1])
    wrong = [(q, want, g) for (q, want), g in zip(ANSI_OFF, got, strict=True) if g != want]
    assert not wrong, wrong


def test_dataframe_casts_wrap_as_spark(kernel: Kernel) -> None:
    text = run(kernel, """
import json
from decimal import Decimal
from pyspark.sql import functions as F
df = spark.createDataFrame([(3000000000, 1e10, "x")], "b bigint, d double, s string")
r = df.select(F.col("b").cast("int"), F.col("d").cast("int"), F.col("b").cast("tinyint"), F.col("s").cast("int")).collect()[0]
print('RESULT' + json.dumps(list(r)))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [-1294967296, 2147483647, 0, None]


def test_an_overflowing_sum_wraps_as_spark_does(kernel: Kernel) -> None:
    """Spark with ANSI off wraps 2147483647 + 1 to -2147483648, and so does this PC (spark_decimals)."""
    assert "RESULT[-2147483648]" in run(
        kernel,
        "print('RESULT' + str(list(spark.sql('SELECT 2147483647 + 1').collect()[0])))",
    )


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


def test_databricks_sql_gives_null_for_a_null_dividend_over_zero() -> None:
    """Spark's DivModLike returns NULL for a NULL dividend before it checks the divisor, ANSI or not."""
    assert _databricks_rows("SELECT sum(y) / count(*) FROM (SELECT 1 AS x, 2 AS y) t WHERE x > 100") == [(None,)]
    assert _databricks_rows("SELECT NULL / 0, CAST(NULL AS INT) div 0, CAST(NULL AS INT) % 0, "
                            "CAST(NULL AS DOUBLE) / (rand() * 0)") == [(None, None, None, None)]


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


# WR-113: decimal arithmetic on the DuckDB engine (Databricks SQL and Spark SQL) gives Spark's
# DecimalPrecision types, as the Spark SQL path does (WR-105, spark_decimals); Databricks documents the
# same rules (sql-ref-datatype-rules). Expected types are worked out from those rules.

@pytest.fixture
def engine():
    from fabric_desktop.engines import local_sql
    from fabric_desktop.engines.contract import CellRequest, SessionConfig
    session = local_sql.DuckDBSession(SessionConfig("nb"))
    session.execute(CellRequest(code="CREATE TABLE t AS SELECT CAST(1 AS DECIMAL(10,2)) AS a, CAST(3 AS DECIMAL(10,2)) "
                                     "AS b, 7 AS i, CAST(0 AS DECIMAL(10,2)) AS z", language="sql", dialect="duckdb"))

    def run(code: str, dialect: str = "databricks") -> object:
        result = session.execute(CellRequest(code=code, language="sql", dialect=dialect, result_rows=100))
        if result.status != "ok":
            return result.outputs[0]["evalue"]
        return [str(t) for t in result.table.schema.types], result.table.to_pylist()
    yield run
    session.close()


@pytest.mark.parametrize("dialect", ["databricks", "spark"])
def test_decimal_types_on_the_duckdb_engine(engine, dialect: str) -> None:
    types, rows = engine("SELECT a / b, a * b, a + b, a % b, avg(a), sum(a), a div b, round(a / b, 2), "
                         "sum(a) / count(*), a / i FROM t GROUP BY a, b, i", dialect)
    assert types == ["decimal128(23, 13)", "decimal128(21, 4)", "decimal128(11, 2)", "decimal128(10, 2)",
                     "decimal128(14, 6)", "decimal128(20, 2)", "int64", "decimal128(13, 2)", "decimal128(38, 20)",
                     "decimal128(21, 13)"]
    assert str(rows[0]["(a / b)"]) == "0.3333333333333"
    assert engine("SELECT CAST(1 AS DECIMAL(10,2)) / 3 AS x", dialect) == (["decimal128(14, 6)"], [{"x": Decimal("0.333333")}])


def test_a_decimal_division_by_zero_on_the_duckdb_engine(engine) -> None:
    assert "[DIVIDE_BY_ZERO]" in engine("SELECT a / z FROM t")
    assert engine("SELECT a / z FROM t", "spark") == (["decimal128(23, 13)"], [{"(a / z)": None}])


# WR-114: try_divide of decimals is a decimal, typed as / is; a decimal sum (and an average's sum) past its
# type is NULL with ANSI off and stops with Spark's ARITHMETIC_OVERFLOW in Databricks SQL (ANSI on).

@pytest.mark.parametrize("dialect", ["databricks", "spark"])
def test_try_divide_of_decimals_is_a_decimal(engine, dialect: str) -> None:
    types, rows = engine("SELECT try_divide(a, b), try_divide(a, z), try_divide(i, 2), try_divide(i, 0), "
                         "try_divide(a, b) * 2 FROM t", dialect)
    assert types == ["decimal128(23, 13)", "decimal128(23, 13)", "double", "double", "decimal128(25, 13)"]
    assert [list(r.values()) for r in rows] == [[Decimal("0.3333333333333"), None, 3.5, None, Decimal("0.6666666666666")]]


def test_try_divide_in_a_notebook(kernel: Kernel) -> None:
    text = run(kernel, """
import json
df = spark.sql("SELECT try_divide(CAST(1 AS DECIMAL(10,2)), CAST(3 AS DECIMAL(10,2))) AS q, try_divide(1, 0) AS z")
print('RESULT' + json.dumps([df.dtypes, [str(v) for v in df.collect()[0]]]))
""")
    assert json.loads(text.split("RESULT", 1)[1]) == [[["q", "decimal(23,13)"], ["z", "double"]], ["0.3333333333333", "None"]]


@pytest.fixture
def overflowing(engine):
    engine("CREATE TABLE u AS SELECT CAST(6e37 AS DECIMAL(38,0)) AS big UNION ALL SELECT CAST(6e37 AS DECIMAL(38,0))",
           "duckdb")
    return engine


def test_a_decimal_sum_past_its_type(overflowing) -> None:
    assert overflowing("SELECT sum(big), avg(big) FROM u", "spark") == (
        ["decimal128(38, 0)", "decimal128(38, 4)"], [{"sum(big)": None, "avg(big)": None}])
    for query in ("SELECT sum(big) FROM u", "SELECT avg(big) FROM u"):
        assert "[ARITHMETIC_OVERFLOW] Overflow in sum of decimals" in overflowing(query)
    assert overflowing("SELECT sum(big) FROM u WHERE false") == (["decimal128(38, 0)"], [{"sum(big)": None}])
