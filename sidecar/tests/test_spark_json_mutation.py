"""spark_json.py's to_json, run on DuckDB here, against what Spark's to_json writes for each type: booleans,
doubles and NaN, dates and timestamps (and their formats), binary, decimals in Java's form, arrays, maps by key
type, structs nested three deep, and NULL fields kept only when asked; run through this PC's spark.sql, as a
cell runs them. Then typeof's names and to_json's options."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop import spark_json as sj

T = exp.DataType


@pytest.fixture(scope="module")
def spark():
    from fabric_desktop.notebook import Kernel

    session = Kernel().namespace["spark"]
    session.conf.set("spark.sql.session.timeZone", "UTC")
    return session


def to_json(spark, value: str, options: str = "") -> str:
    return spark.sql(f"SELECT to_json({value}{', ' + options if options else ''}) AS j").collect()[0][0]


@pytest.mark.parametrize("value, written", [
    ("named_struct('b', true, 'c', false)", '{"b":true,"c":false}'),
    ("named_struct('d', CAST(1e10 AS DOUBLE), 'n', CAST('NaN' AS DOUBLE), 'f', CAST(1.5 AS FLOAT))",
     '{"d":1.0E10,"n":"NaN","f":1.5}'),
    ("named_struct('d', DATE '2024-01-02')", '{"d":"2024-01-02"}'),
    ("named_struct('t', TIMESTAMP_NTZ '2024-01-02 03:04:05.678')", '{"t":"2024-01-02T03:04:05.678"}'),
    ("named_struct('t', TIMESTAMP '2024-01-02 03:04:05.678')", '{"t":"2024-01-02T03:04:05.678Z"}'),
    ("named_struct('b', CAST('hi' AS BINARY))", '{"b":"aGk="}'),
    ("array(array(1, NULL), array(2))", "[[1,null],[2]]"),
    ("map('a', map('x', 1))", '{"a":{"x":1}}'),
    ("map(1, 2, 3, NULL)", '{"1":2,"3":null}'),
    ("map(true, 2)", '{"true":2}'),
    ("map(DATE '1970-01-11', 2)", '{"10":2}'),
    ("named_struct('a', CAST(NULL AS INT), 'b', 1)", '{"b":1}'),
    ("array(named_struct('a', array(1, 2)))", '[{"a":[1,2]}]'),
    ("named_struct('s', named_struct('a', array(1)))", '{"s":{"a":[1]}}'),
    ("named_struct('m', map('k', array(1)))", '{"m":{"k":[1]}}'),
    ("array(map('k', named_struct('a', array(3))))", '[{"k":{"a":[3]}}]'),
    ("named_struct('x', CAST(0.0000001 AS DECIMAL(10,7)))", '{"x":1E-7}'),
    ("named_struct('x', CAST(1.5 AS DECIMAL(38,2)))", '{"x":1.50}'),
    ("named_struct('x', CAST(1.000001 AS DECIMAL(10,6)))", '{"x":1.000001}'),
    ("named_struct('a', array())", '{"a":[]}'),
])
def test_to_json_writes_each_type_as_spark_does(spark, value, written):
    assert to_json(spark, value) == written


def test_null_fields_are_kept_only_when_ignore_null_fields_is_false(spark):
    value = "named_struct('a', CAST(NULL AS INT), 'b', 1)"
    assert to_json(spark, value, "map('ignoreNullFields', 'false')") == '{"a":null,"b":1}'
    assert to_json(spark, value, "map('ignoreNullFields', 'true')") == '{"b":1}'


def test_the_date_and_timestamp_formats_are_read(spark):
    assert to_json(spark, "named_struct('d', DATE '2024-01-02')", "map('dateFormat', 'dd/MM/yyyy')") == '{"d":"02/01/2024"}'
    assert to_json(spark, "named_struct('t', TIMESTAMP '2024-01-02 03:04:05')",
                   "map('timestampFormat', 'yyyy HH')") == '{"t":"2024 03"}'
    assert to_json(spark, "named_struct('t', TIMESTAMP_NTZ '2024-01-02 03:04:05')",
                   "map('timestampNTZFormat', 'MM-dd')") == '{"t":"01-02"}'


def test_a_decimal_with_no_scale_given_has_scale_0():
    sql = sj.to_json_sql("v", T.build("struct<x:decimal(5)>", dialect="spark"), {})
    assert "LIST_TRANSFORM" not in sql and "CAST(v['x'] AS VARCHAR)" in sql


def test_to_json_of_a_plain_value_is_refused():
    with pytest.raises(sj.Refused) as caught:
        sj.to_json_sql("v", T.build("int"), {})
    assert str(caught.value) == ("to_json writes a struct, an array or a map, as in Spark, not a INT, so nothing ran. "
                                 "Wrap the value in struct(...) or array(...).")


def options(*pairs):
    keys = exp.Array(expressions=[exp.Literal.string(k) for k, _ in pairs])
    values = exp.Array(expressions=[v if isinstance(v, exp.Expr) else exp.Literal.string(v) for _, v in pairs])
    return exp.Anonymous(this="to_json", options=exp.Map(keys=keys, values=values))


def node(given):
    return exp.Column(this=exp.to_identifier("x"), options=given)


def test_options_are_read_by_their_name_in_any_case():
    assert sj.options_of(node(None)) == {}
    got = sj.options_of(node(options(("DATEFORMAT", "dd"), ("ignorenullfields", "false")).args["options"]))
    assert got == {"dateFormat": "dd", "ignoreNullFields": "false"}


def test_an_option_this_pc_doesnt_read_is_refused_by_name():
    with pytest.raises(sj.Refused) as caught:
        sj.options_of(node(options(("dateFormat", "dd"), ("pretty", "true")).args["options"]))
    assert str(caught.value) == ("to_json's 'pretty' option isn't read on this PC, so nothing ran. Leave it out, or "
                                 "run the cell in Fabric.")


@pytest.mark.parametrize("given", [
    exp.Map(keys=exp.Array(expressions=[exp.Literal.string("dateFormat")]), values=exp.Literal.string("dd")),
    exp.Map(keys=exp.Literal.string("dateFormat"), values=exp.Array(expressions=[exp.Literal.string("dd")])),
    exp.Map(keys=exp.Array(expressions=[exp.Literal.string("dateFormat")]),
            values=exp.Array(expressions=[exp.Literal.number(1)])),
])
def test_options_not_written_out_as_text_pairs_are_refused(given):
    with pytest.raises(sj.Refused, match=r"^to_json's options must be written out as map\('name', 'value', \.\.\.\)"):
        sj.options_of(node(given))


def test_options_with_more_names_than_values_are_an_error():
    given = exp.Map(keys=exp.Array(expressions=[exp.Literal.string("dateFormat"), exp.Literal.string("x")]),
                    values=exp.Array(expressions=[exp.Literal.string("dd")]))
    with pytest.raises(ValueError, match="zip"):
        sj.options_of(node(given))


@pytest.mark.parametrize("spark_type, name", [
    ("struct<a:decimal(5,2)>", "struct<a:decimal(5,2)>"),
    ("struct<a:decimal(5)>", "struct<a:decimal(5,0)>"),
    ("map<string,decimal>", "map<string,decimal(10,0)>"),
    ("struct<a:int,b:bigint>", "struct<a:int,b:bigint>"),
    ("array<struct<a:boolean>>", "array<struct<a:boolean>>"),
    ("struct<a:array<double>>", "struct<a:array<double>>"),
    ("map<int,array<date>>", "map<int,array<date>>"),
    ("struct<a:array<interval>>", None),
    ("array<int>", None),
])
def test_typeof_names_structs_and_maps_as_spark_does(spark_type, name):
    assert sj.type_name(T.build(spark_type, dialect="spark")) == name
