"""The DataFrame API gives Spark's decimal result types and values, as Spark SQL on this PC does
(EA-004 part 3, #1682): decimal + - * /, sum and avg in select, withColumn, agg, selectExpr and F.expr."""

from __future__ import annotations

from decimal import Decimal

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from fabric_desktop import lakehouse, onelake
from fabric_desktop import publish as publish_mod
from fabric_desktop.notebook import Kernel
from fabric_desktop.spark_decimals import spark_decimal_columns

FRAME = (
    "from pyspark.sql import functions as F\n"
    "from decimal import Decimal\n"
    "from pyspark.sql.types import StructType, StructField, StringType, DecimalType, DoubleType, IntegerType\n"
    "s = StructType([StructField('k', StringType()), StructField('a', DecimalType(10, 2)),\n"
    "                StructField('b', DecimalType(10, 2)), StructField('d', DoubleType()), StructField('i', IntegerType())])\n"
    "df = spark.createDataFrame([('x', Decimal('2.00'), Decimal('3.00'), 1.5, 2),\n"
    "                            ('x', Decimal('99999999.99'), Decimal('0.00'), 2.5, 3)], s)\n"
)


def run(code):
    out = Kernel().run(FRAME + code)
    return out["status"], "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])


def test_division_gives_sparks_type_and_value():
    status, text = run("q = df.select((F.col('a') / F.col('b')).alias('q'))\n"
                       "print(q.dtypes)\nprint([r.q for r in q.collect()])")
    assert status == "ok", text
    types, values = text.strip().splitlines()
    assert types == "[('q', 'decimal(23,13)')]"
    assert values == "[Decimal('0.6666666666667'), None]"  # rounded half up; dividing by 0 is NULL


def test_avg_and_sum_in_a_grouped_agg():
    status, text = run("r = df.groupBy('k').agg(F.avg('a'), F.sum('a'))\nprint(r.dtypes)\nprint(tuple(r.collect()[0]))")
    assert status == "ok", text
    types, row = text.strip().splitlines()
    assert types == "[('k', 'string'), ('avg(a)', 'decimal(14,6)'), ('sum(a)', 'decimal(20,2)')]"
    assert row == "('x', Decimal('50000000.995000'), Decimal('100000001.99'))"


def test_agg_without_a_group_and_a_select_of_an_aggregate():
    status, text = run("print(df.agg(F.avg('a')).dtypes, df.select(F.sum('a')).dtypes)")
    assert status == "ok", text
    assert "[('avg(a)', 'decimal(14,6)')] [('sum(a)', 'decimal(20,2)')]" in text


def test_expr_multiplication_doesnt_overflow():
    status, text = run("r = df.select(F.expr('a * a').alias('m'), F.expr('a * b'))\n"
                       "print(r.dtypes)\nprint([r.m for r in r.collect()])")
    assert status == "ok", text
    types, values = text.strip().splitlines()
    assert types == "[('m', 'decimal(21,4)'), ('(a * b)', 'decimal(21,4)')]"
    assert values == "[Decimal('4.0000'), Decimal('9999999998000000.0001')]"


def test_with_column_with_columns_and_select_expr():
    status, text = run("print(df.withColumn('q', F.col('a') / F.col('b')).dtypes[-1])\n"
                       "print(df.withColumns({'t': F.col('a') + F.col('b'), 'm': F.col('a') * F.col('i')}).dtypes[-2:])\n"
                       "print(df.selectExpr('a - b AS diff', 'avg(a) OVER () AS w').dtypes)")
    assert status == "ok", text
    one, two, three = text.strip().splitlines()
    assert one == "('q', 'decimal(23,13)')"
    assert two == "[('t', 'decimal(11,2)'), ('m', 'decimal(21,2)')]"  # an INT column is decimal(10,0)
    assert three == "[('diff', 'decimal(11,2)'), ('w', 'decimal(14,6)')]"


def test_double_and_integer_expressions_are_unchanged():
    status, text = run("print(df.select(F.col('d') * 2, F.col('i') + 1, F.col('d') / F.col('i'), F.col('a') * F.col('d')).dtypes)\n"
                       "print(df.agg(F.avg('d'), F.sum('i')).dtypes)")
    assert status == "ok", text
    scalars, aggregates = text.replace('"', "").strip().splitlines()
    assert scalars == "[('(d * 2)', 'double'), ('(i + 1)', 'int'), ('(d / nullif(i, 0))', 'double'), ('(a * d)', 'double')]"
    assert aggregates == "[('avg(d)', 'double'), ('sum(i)', 'hugeint')]"  # DuckDB's own, as before


def test_column_names_stay_as_they_were():
    status, text = run("print(df.select(F.col('a') * F.col('b'), F.col('a') / F.col('b'), 'k', F.col('a')).columns)")
    assert status == "ok", text
    assert text.strip() == "['(a * b)', '(a / \"nullif\"(b, 0))', 'k', 'a']"


def test_a_star_next_to_a_decimal_column():
    status, text = run("print(df.select('*', F.col('a') * F.col('b')).dtypes)")
    assert status == "ok", text
    assert text.strip().endswith("('i', 'int'), ('(a * b)', 'decimal(21,4)')]")


def test_an_expression_that_cant_be_typed_keeps_duckdbs_types(caplog):
    with caplog.at_level("INFO", logger="fabric_desktop.spark_decimals"):
        assert spark_decimal_columns(["(a / unknown_fn(b))"], [("a", "DECIMAL(10,2)"), ("b", "DECIMAL(10,2)")]) == [None]
        assert spark_decimal_columns(["(a / b"], [("a", "DECIMAL(10,2)"), ("b", "DECIMAL(10,2)")]) == [None]
    assert "not applied" in caplog.text


def test_a_frame_without_decimals_is_never_parsed():
    assert spark_decimal_columns(["(a / b"], [("a", "DOUBLE"), ("b", "INTEGER")]) == [None]


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "Tables"
    monkeypatch.setattr(onelake.TableRef, "uri", property(lambda self: (root / self.schema / self.table).as_posix()))
    monkeypatch.setattr(publish_mod, "storage_options", lambda token: {})
    monkeypatch.setattr(lakehouse, "storage_options", lambda token: {})
    lakehouse.attach(lakehouse.Attached("ws1", "lh1", "Sales", lambda: "tok", tmp_path))
    write_deltalake(str(root / "dbo" / "seed"), pa.table({"id": [1]}))
    yield root
    lakehouse.attach(None)


def test_save_as_table_writes_sparks_decimal_type(lake):
    status, text = run("df.select((F.col('a') / F.col('b')).alias('q')).write.mode('overwrite').saveAsTable('dbo.q')")
    assert status == "ok", text
    table = DeltaTable(str(lake / "dbo" / "q")).to_pyarrow_table()
    assert table.schema.field("q").type == pa.decimal128(23, 13)
    assert table.column("q").to_pylist() == [Decimal("0.6666666666667"), None]
