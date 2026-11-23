"""Mutation batch 238: spark_complex's survivors on today's main. A shift of an int read as an int
(_int_shift_type), typeof of a shift of a long, to_json refusing an argument it can't type with the reason
and step that say so, and the types a rewrite already gave kept for to_json."""
from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop import spark_complex as sc
from fabric_desktop import spark_json, sql


def shift(arg_type: str | None, *, wrapped: int = 0) -> tuple[exp.Expr, dict]:
    """A shiftleft marked "int_shift" over a column whose type is ``arg_type`` (None: no type known),
    inside ``wrapped`` brackets, and the types _int_shift_type reads."""
    arg = exp.column("i")
    node: exp.Expr = exp.Anonymous(this="shiftleft", expressions=[arg, exp.Literal.number(1)])
    sc._mark(node, "int_shift", arg)
    arg.meta[sc._ID] = 0
    for _ in range(wrapped):
        node = exp.Paren(this=node)
    return node, ({} if arg_type is None else {0: exp.DataType.build(arg_type)})


@pytest.mark.parametrize("kind", ["TINYINT", "SMALLINT", "INT"])
def test_a_shift_of_an_int_is_an_int(kind):
    node, types = shift(kind)
    assert sc._int_shift_type(node, types) == exp.DataType.build("INT")


def test_a_shift_inside_brackets_is_still_found():
    node, types = shift("INT", wrapped=2)
    assert sc._int_shift_type(node, types) == exp.DataType.build("INT")


@pytest.mark.parametrize("kind", ["BIGINT", "DOUBLE"])
def test_a_shift_of_anything_else_is_not_an_int(kind):
    node, types = shift(kind)
    assert sc._int_shift_type(node, types) is None


def test_a_shift_whose_argument_has_no_type_is_not_an_int():
    node, types = shift(None)
    assert sc._int_shift_type(node, types) is None


def test_a_node_that_is_not_a_shift_is_not_an_int():
    arg = exp.column("i")
    node = exp.Paren(this=exp.Anonymous(this="abs", expressions=[arg]))
    arg.meta[sc._ARG] = True
    arg.meta[sc._ID] = 0
    assert sc._int_shift_type(node, {0: exp.DataType.build("INT")}) is None


def test_typeof_a_shift_of_a_long_or_of_null_is_not_int():
    got = sql.run("SELECT typeof(shiftleft(l, 1)) a, typeof(NULL) b FROM VALUES (CAST(1 AS BIGINT)) t(l)")
    assert got.to_pylist() == [{"a": "bigint", "b": "void"}]


def test_to_json_of_null_is_refused_as_untyped_with_its_step():
    with pytest.raises(spark_json.Refused) as caught:
        sql.run("SELECT to_json(NULL) AS j")
    assert spark_json.UNTYPED in str(caught.value) and spark_json.UNTYPED_STEP in str(caught.value)


def test_to_json_keeps_the_type_a_cast_to_string_already_has():
    # The cast is rewritten as a lambda sqlglot can't type; its type is kept for to_json (spark_complex.TYPED)
    got = sql.run("SELECT to_json(named_struct('a', CAST(1.5 AS STRING), 'b', CAST(x AS STRING))) j "
                  "FROM VALUES (CAST(2.5 AS DOUBLE)) t(x)")
    assert got.to_pylist() == [{"j": '{"a":"1.5","b":"2.5"}'}]
    got = sql.run("SELECT to_json(named_struct('a', CAST(x AS STRING))) j "
                  "FROM VALUES (TIMESTAMP '2026-01-02 03:04:05') t(x)")
    assert got.to_pylist() == [{"j": '{"a":"2026-01-02 03:04:05"}'}]


def test_a_shift_of_a_shift_of_an_int_keeps_its_value():
    got = sql.run("SELECT shiftleft(shiftleft(i, 1), 1) a, shiftright(shiftleft(i, 2), 1) b "
                  "FROM VALUES (CAST(3 AS INT)) t(i)")
    assert got.to_pylist() == [{"a": 12, "b": 6}]


@pytest.mark.xfail(strict=True, reason="a shift of a shift of an int is typed bigint on this PC; Spark types it int, "
                                       "as the inner shift is an int (WR-141)")
def test_a_shift_of_a_shift_of_an_int_is_an_int():
    got = sql.run("SELECT shiftleft(shiftleft(i, 1), 1) a, typeof(shiftleft(shiftleft(i, 1), 1)) b "
                  "FROM VALUES (CAST(3 AS INT)) t(i)")
    assert str(got.schema.field("a").type) == "int32" and got.to_pylist() == [{"a": 12, "b": "int"}]


def test_to_json_beside_a_shift_of_an_int_leaves_the_shift_an_int():
    got = sql.run("SELECT to_json(named_struct('a', 1)) j, shiftleft(i, 1) s FROM VALUES (CAST(1 AS INT)) t(i)")
    assert [str(f.type) for f in got.schema] == ["string", "int32"]
    assert got.to_pylist() == [{"j": '{"a":1}', "s": 2}]
