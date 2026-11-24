"""Spark's to_json and typeof names on this PC (spark_json.py), pinned so a changed type check, format,
option or decimal rule fails a test. Each value is what Spark 3.5 writes for it, run through a notebook's
spark.sql; the refusals and typeof names are checked on the module itself."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop import spark_json as sj
from fabric_desktop.notebook import Kernel

T = exp.DataType


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


CASES = [
    ("to_json(named_struct('a', 1, 'b', 'x', 'c', true, 'd', false))", '{"a":1,"b":"x","c":true,"d":false}'),
    ("to_json(named_struct('a', CAST(1 AS TINYINT), 'b', CAST(2 AS SMALLINT), 'c', CAST(3 AS BIGINT)))",
     '{"a":1,"b":2,"c":3}'),
    ("to_json(named_struct('a', CAST(NULL AS INT), 'b', 2))", '{"b":2}'),
    ("to_json(named_struct('a', CAST(NULL AS INT), 'b', 2), map('ignoreNullFields', 'false'))", '{"a":null,"b":2}'),
    ("to_json(named_struct('a', CAST(NULL AS INT)), map('IGNORENULLFIELDS', ' FALSE '))", '{"a":null}'),
    ("to_json(named_struct('a', CAST('NaN' AS DOUBLE), 'b', CAST(1e10 AS DOUBLE), 'c', CAST(1.5 AS FLOAT)))",
     '{"a":"NaN","b":1.0E10,"c":1.5}'),
    ("to_json(named_struct('a', DATE'2024-01-02', 'b', TIMESTAMP'2024-01-02 03:04:05.678', 'c', CAST('ab' AS BINARY)))",
     '{"a":"2024-01-02","b":"2024-01-02T03:04:05.678Z","c":"YWI="}'),
    ("to_json(named_struct('a', DATE'2024-01-02'), map('dateFormat', 'dd/MM/yyyy'))", '{"a":"02/01/2024"}'),
    ("to_json(named_struct('a', TIMESTAMP'2024-01-02 03:04:05'), map('timestampFormat', 'yyyy'))", '{"a":"2024"}'),
    ("to_json(named_struct('a', CAST(TIMESTAMP'2024-01-02 03:04:05' AS TIMESTAMP_NTZ)))",
     '{"a":"2024-01-02T03:04:05.000"}'),
    ("to_json(named_struct('a', CAST(TIMESTAMP'2024-01-02 03:04:05' AS TIMESTAMP_NTZ)), map('timestampNTZFormat', 'yyyy'))",
     '{"a":"2024"}'),
    ("to_json(array(1, NULL, 3))", "[1,null,3]"),
    ("to_json(array(array(1), array(2, 3)))", "[[1],[2,3]]"),
    ("to_json(map('k', 1, 'j', NULL))", '{"k":1,"j":null}'),
    ("to_json(map(1, 'a'))", '{"1":"a"}'),
    ("to_json(map(true, 'a'))", '{"true":"a"}'),
    ("to_json(map(DATE'1970-01-03', 'a'))", '{"2":"a"}'),
    ("to_json(map(CAST(1.5 AS DECIMAL(3,1)), 'a'))", '{"1.5":"a"}'),
    ("to_json(named_struct('a', array(named_struct('b', map('c', 1)))))", '{"a":[{"b":{"c":1}}]}'),
    ("to_json(named_struct('a', CAST(0.0000012 AS DECIMAL(10,7))))", '{"a":0.0000012}'),
    ("to_json(named_struct('a', CAST(0 AS DECIMAL(10,7))))", '{"a":0E-7}'),
    ("to_json(named_struct('a', CAST(0 AS DECIMAL(10,6))))", '{"a":0.000000}'),
    ("to_json(named_struct('a', CAST(0.00000012 AS DECIMAL(10,8))))", '{"a":1.2E-7}'),
    ("to_json(named_struct('a', CAST(-123 AS DECIMAL(20,12))))", '{"a":-123.000000000000}'),
    ("to_json(named_struct('a', CAST(12 AS DECIMAL(10,0))))", '{"a":12}'),
    ("to_json(CAST(NULL AS STRUCT<a: INT>))", None),
    ("to_json(array())", "[]"),
    ("to_json(named_struct('a', array()))", '{"a":[]}'),
    ("to_json(named_struct('a', CAST(12 AS DECIMAL(5))))", '{"a":12}'),
    ("to_json(map('a', array(1, 2)))", '{"a":[1,2]}'),
    ("to_json(map('a', map('b', array(1))))", '{"a":{"b":[1]}}'),
    ("typeof(named_struct('a', 1, 'b', 'x'))", "struct<a:int,b:string>"),
    ("typeof(map('a', array(1)))", "map<string,array<int>>"),
    ("typeof(array(named_struct('a', CAST(1.5 AS DECIMAL(3,1)))))", "array<struct<a:decimal(3,1)>>"),
    (("typeof(named_struct('a', CAST(1 AS DOUBLE), 'b', CAST(1 AS FLOAT), 'c', DATE'2024-01-01', 'd', true, "
      "'e', CAST('a' AS BINARY)))"), "struct<a:double,b:float,c:date,d:boolean,e:binary>"),
]


@pytest.mark.parametrize("sql, written", CASES)
def test_to_json_and_typeof_write_what_spark_writes(spark, sql, written):
    assert spark.sql(f"SELECT {sql} AS j").collect()[0][0] == written


def test_only_a_struct_array_or_map_is_written():
    for kind in ("int", "string"):
        with pytest.raises(sj.Refused, match=f"not a {kind.upper()}"):
            sj.to_json_sql("x", T.build(kind), {})


def test_a_map_with_other_keys_is_refused_by_name():
    with pytest.raises(sj.Refused, match="map with DOUBLE keys"):
        sj.to_json_sql("x", T.build("map<double, int>", dialect="spark"), {})


@pytest.mark.parametrize("dtype, name", [
    ("decimal", "decimal(10,0)"), ("decimal(5)", "decimal(5,0)"), ("tinyint", "tinyint"), ("smallint", "smallint"),
    ("bigint", "bigint"), ("varchar(3)", "string"),
])
def test_typeof_names_each_field_type(dtype, name):
    assert sj.type_name(T.build(f"struct<a {dtype}>")) == f"struct<a:{name}>"


def test_typeof_names_only_a_type_with_a_struct_or_map_in_it():
    assert sj.type_name(T.build("array<int>")) is None
    assert sj.type_name(T.build("array<array<map<int, int>>>", dialect="spark")) == "array<array<map<int,int>>>"
    assert sj.type_name(T.build("map<int, interval>", dialect="spark")) is None
    assert sj.type_name(T.build("map<interval, int>", dialect="spark")) is None
    assert sj.type_name(T.build("array<struct<a interval>>", dialect="spark")) is None


def _options(sql):
    return sj.options_of(exp.maybe_parse(sql, dialect="spark"))


def test_options_are_read_by_name_whatever_their_case():
    assert _options("to_json(x)") == {}
    assert _options("to_json(x, map('DATEFORMAT', 'yyyy', 'timestampformat', 'MM'))") == {
        "dateFormat": "yyyy", "timestampFormat": "MM"}
    with pytest.raises(sj.Refused, match="'pretty' option isn't read"):
        _options("to_json(x, map('pretty', 'true'))")
    with pytest.raises(sj.Refused, match="written out as map"):
        _options("to_json(x, map('dateFormat', y))")


def test_options_must_be_text_pairs_written_out():
    with pytest.raises(sj.Refused, match="written out as map"):
        _options("to_json(x, map('dateFormat', 1))")
    node = exp.Anonymous(this="to_json")
    node.set("options", exp.VarMap(keys=exp.Array(expressions=[exp.Literal.string("dateFormat")]),
                                   values=exp.Literal.string("yyyy")))
    with pytest.raises(sj.Refused, match="written out as map"):
        sj.options_of(node)
    node.set("options", exp.VarMap(keys=exp.Array(expressions=[exp.Literal.string("dateFormat")] * 2),
                                   values=exp.Array(expressions=[exp.Literal.string("yyyy")])))
    with pytest.raises(ValueError):  # a key without its value
        sj.options_of(node)
