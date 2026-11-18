"""WR-118: Spark 3.5's JSON functions on this PC, value-checked against Spark 3.5.3 (see spark_values.py).

get_json_object, json_tuple, from_json in its PERMISSIVE, FAILFAST and DROPMALFORMED modes (with the
corrupt-record column), to_json and schema_of_json: 232 queries in tools/spark_value_cases/json.txt, each
answered by Spark 3.5.3 in tests/fixtures/spark_3_5_3_json.json. What this PC can't answer as Spark does is
refused by name below, and listed in docs/sql-compatibility.md."""

from __future__ import annotations

import pytest
from spark_values import Refused, check, load

from fabric_desktop.notebook import Kernel

REFUSED = [
    Refused("reading options", r"(from_json|schema_of_json)\(.*'allow(Comments|UnquotedFieldNames|NumericLeadingZeros)', 'true'",
            r"option isn't read on this PC", "`allowComments`, `allowUnquotedFieldNames` and `allowNumericLeadingZeros`"),
    Refused("pretty", r"to_json\(.*'pretty'", r"'pretty' option isn't read on this PC", "The `to_json` option `pretty`"),
]


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def test_spark_json_functions_answer_as_spark_3_5_or_are_refused_by_name(spark):
    check(spark, load("json"), REFUSED)


def test_typeof_names_a_struct_only_when_every_field_keeps_its_name():
    from sqlglot import exp

    from fabric_desktop import spark_json
    assert spark_json.type_name(exp.DataType.build("STRUCT<a INT>")) == "struct<a:int>"
    lost = exp.DataType(this=exp.DataType.Type.STRUCT, expressions=[
        exp.ColumnDef(this=exp.to_identifier(""), kind=exp.DataType.build("INT"))], nested=True)
    assert spark_json.type_name(lost) is None


@pytest.mark.parametrize("value, written", [
    ("CAST(0 AS DECIMAL(18,8))", "0E-8"), ("CAST(0.00000012 AS DECIMAL(10,8))", "1.2E-7"),
    ("CAST(-0.000000123 AS DECIMAL(20,12))", "-1.23000E-7"), ("CAST(0.0000012 AS DECIMAL(10,8))", "0.00000120"),
    ("CAST(1.5 AS DECIMAL(10,8))", "1.50000000"), ("CAST(1.5 AS DECIMAL(5,2))", "1.50"),
])
def test_to_json_writes_a_decimal_as_javas_big_decimal_to_string(spark, value, written):
    # Spark 3.5.3 gives these too (the fourth capture in spark_3_5_3_json.json).
    assert spark.sql(f"SELECT to_json(named_struct('a', {value})) AS j").collect()[0][0] == '{"a":' + written + '}'


def test_range_gives_sparks_id_column_unless_the_query_reads_duckdbs_range_column(spark):
    assert spark.sql("SELECT * FROM range(3)").columns == ["id"]
    assert [r[0] for r in spark.sql("SELECT range AS n FROM range(3)").collect()] == [0, 1, 2]
    assert [r[0] for r in spark.sql("SELECT sum(a.range * b.range) AS s FROM range(3) a, range(2) b").collect()] == [3]


def test_a_tables_own_range_column_leaves_range_reading_as_spark(spark):
    spark.sql("CREATE OR REPLACE TEMP VIEW kf_ranges AS SELECT 7 AS range")
    assert spark.sql("SELECT t.range, r.id FROM kf_ranges t, range(2) r ORDER BY r.id").collect() == [(7, 0), (7, 1)]
