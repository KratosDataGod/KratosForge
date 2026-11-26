"""Mutation batch 303: Spark's to_json and typeof names on this PC (spark_json.py): nested arrays, maps and
structs, a decimal given only its precision, the scale where Java's E form starts, options that are
neither both lists nor all text, and typeof's default decimal."""
from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop import spark_json as sj
from fabric_desktop.notebook import Kernel

T = exp.DataType


@pytest.fixture(scope="module")
def spark():
    return Kernel().namespace["spark"]


def one(spark, select: str):
    return spark.sql(f"SELECT {select} AS j").collect()[0][0]


@pytest.mark.parametrize("value, written", [
    ("array(array(1, 2), array(3))", "[[1,2],[3]]"),
    ("array(array(array(1)))", "[[[1]]]"),
    ("named_struct('a', array(1, 2))", '{"a":[1,2]}'),
    ("map('k', array(1))", '{"k":[1]}'),
    ("array(map('k', array(1)))", '[{"k":[1]}]'),
])
def test_to_json_writes_nested_values_inside_each_other(spark, value, written):
    assert one(spark, f"to_json({value})") == written


def test_a_decimal_given_only_its_precision_has_scale_zero(spark):
    assert one(spark, "to_json(named_struct('a', CAST(2 AS DECIMAL(10))))") == '{"a":2}'


def test_a_decimal_of_scale_seven_switches_to_e_form_below_one_millionth(spark):
    assert one(spark, "to_json(named_struct('a', CAST(0.0000001 AS DECIMAL(8,7))))") == '{"a":1E-7}'


def _node(keys, values):
    return exp.JSONFormat(this=exp.column("x"), options=exp.VarMap(keys=keys, values=values))


def test_options_need_both_names_and_values_as_lists():
    with pytest.raises(sj.Refused, match="must be written out"):
        sj.options_of(_node(exp.column("names"), exp.Array(expressions=[exp.Literal.string("false")])))


def test_option_values_must_be_text_not_numbers():
    with pytest.raises(sj.Refused, match="must be written out"):
        sj.options_of(_node(exp.Array(expressions=[exp.Literal.string("ignoreNullFields")]),
                            exp.Array(expressions=[exp.Literal.number(1)])))


def test_more_option_names_than_values_never_pass():
    with pytest.raises(ValueError):
        sj.options_of(_node(exp.Array(expressions=[exp.Literal.string("ignoreNullFields"),
                                                   exp.Literal.string("dateFormat")]),
                            exp.Array(expressions=[exp.Literal.string("false")])))


def test_typeof_names_a_decimal_without_its_size_as_spark_does():
    assert sj.type_name(T.build("STRUCT<a DECIMAL>")) == "struct<a:decimal(10,0)>"
    assert sj.type_name(T.build("STRUCT<a DECIMAL(5)>")) == "struct<a:decimal(5,0)>"
    assert sj.type_name(T.build("STRUCT<a DECIMAL(5, 2)>")) == "struct<a:decimal(5,2)>"
