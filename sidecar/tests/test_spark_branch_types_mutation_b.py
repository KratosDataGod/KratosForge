"""spark_branch_types: the edges the earlier tests left open (mutation batch 482). Spark's type names in a
refusal, the wider type of a float or a date, an exponent literal, which calls and set operations are typed,
and how a cast column keeps its name. Pure sqlglot here: nothing runs on DuckDB or Spark."""

from __future__ import annotations

import re

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_branch_types as sbt
from fabric_desktop.spark_decimals import Refused


def parse(sql):
    return sqlglot.parse_one(sql, read="spark")


def typed(sql, schema=None):
    statement = sbt.mark(parse(sql))
    return sbt.spark_branch_types(statement, None, schema or MappingSchema({"t": {"x": "int"}}, dialect="spark"))


def test_spark_names_every_whole_number_and_a_decimal():
    assert [sbt._spark_name(("int", n)) for n in (3, 5, 10, 20)] == ["TINYINT", "SMALLINT", "INT", "BIGINT"]
    assert sbt._spark_name(("decimal", 5, 2)) == "DECIMAL(5,2)"


@pytest.mark.parametrize("value, name", [("CAST(1 AS TINYINT)", "TINYINT"), ("CAST(1 AS SMALLINT)", "SMALLINT"),
                                         ("1.5", "DECIMAL(2,1)")])
def test_a_refusal_names_each_type_as_spark_does(value, name):
    with pytest.raises(Refused, match=re.escape(f"({name}, BOOLEAN)")):
        typed(f"SELECT CASE WHEN x = 1 THEN {value} ELSE true END FROM t")


def test_a_float_with_a_whole_number_is_a_float_either_way_round():
    assert sbt.wider(("float",), ("int", 10)) == ("float",)
    assert sbt.wider(("int", 10), ("float",)) == ("float",)


def test_a_date_with_a_timestamp_is_a_timestamp():
    assert sbt.wider(("date",), ("timestamp",)) == ("timestamp",)
    assert sbt.wider(("timestamp",), ("date",)) == ("timestamp",)


def test_a_literal_with_an_exponent_is_a_double():
    assert sbt._kind(exp.Literal.number("1e5"), {}) == ("double",)
    assert sbt._kind(exp.Neg(this=exp.Literal.number("2E3")), {}) == ("double",)


def test_a_side_with_a_star_cant_be_told():
    assert sbt._columns(parse("SELECT *, 1 FROM t"), {}) is None


def test_a_column_of_unknown_type_beside_a_known_one_is_left_alone():
    union = parse("SELECT y UNION ALL SELECT 1")
    sbt._set_types(union, {})
    assert union.meta[sbt._KEY] == [None]
    assert union.sql(dialect="duckdb") == "SELECT y UNION ALL SELECT 1"


def test_only_marked_set_operations_are_typed():
    statement = parse("SELECT 1 AS a UNION ALL SELECT 'x'")  # not marked: another rewrite's own
    assert sbt.spark_branch_types(statement, None, MappingSchema()).sql(dialect="duckdb") == \
        "SELECT 1 AS a UNION ALL SELECT 'x'"
    marked = sbt.spark_branch_types(sbt.mark(parse("SELECT 1 AS a UNION ALL SELECT 'x'")), None, MappingSchema())
    assert "CAST(1 AS TEXT)" in marked.sql(dialect="duckdb")


def test_a_column_the_schema_doesnt_know_doesnt_stop_the_typing():
    out = typed("SELECT CASE WHEN x = 1 THEN 1 ELSE 'a' END AS c, nope FROM t").sql(dialect="duckdb")
    assert "CAST(1 AS TEXT)" in out


def test_a_cast_column_keeps_its_name_quoted():
    select = parse("SELECT `a b` FROM t")
    sbt._cast_column(select, 0, ("int", 10), ("text",))
    assert select.sql(dialect="duckdb") == 'SELECT CAST("a b" AS TEXT) AS "a b" FROM t'


def test_a_statement_that_cant_be_typed_comes_back_as_it_was():
    statement = sbt.mark(parse("SELECT CASE WHEN true THEN 1 ELSE 'a' END"))
    assert sbt.spark_branch_types(statement, None) is statement  # no connection to read a schema from
    assert statement.sql(dialect="duckdb") == "SELECT CASE WHEN TRUE THEN 1 ELSE 'a' END"
