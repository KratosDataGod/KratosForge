"""spark_complex's remaining paths: _once_arg looking past other parts, to_json refused when nothing can be
typed (or its argument is NULL), a LATERAL VIEW of a map naming three columns, and the map forms' guards
when the node isn't where they expect it."""
from __future__ import annotations

import duckdb
import pytest
from sqlglot import exp

from fabric_desktop import spark_complex as sc
from fabric_desktop import spark_json, sql


@pytest.fixture
def conn():
    with duckdb.connect() as con:
        con.execute("CREATE TABLE c AS SELECT * FROM (VALUES (1, MAP {'a': 1, 'b': 2}, [1, 2, NULL])) t(id, m, arr)")
        yield con


def typed(conn, query):
    return sql.duckdb_sql(sc.spark_complex_types(sql.parse(query)[0], conn))


def test_once_arg_finds_a_later_part_and_none_for_a_part_it_doesnt_hold():
    text = sc._once("{a} + {v}", a=exp.Rand(), v=exp.column("x"))
    new = sc._parse(text)
    assert sc._sql(sc._once_arg(new, "v")) == "x"
    assert isinstance(sc._once_arg(new, "a"), exp.Rand)
    assert sc._once_arg(new, "zz") is None
    assert sc._once_arg(sc._parse(sc._once("{a} + 1", a=exp.column("x")))) is None   # written directly


def test_to_json_is_refused_when_the_statement_cant_be_typed():
    with pytest.raises(spark_json.Refused, match="couldn't be worked out"):
        sc.spark_complex_types(sql.parse("SELECT to_json(named_struct('a', 1)) AS j")[0], object())


def test_a_statement_that_cant_be_typed_without_to_json_keeps_its_array_form():
    out = sc.spark_complex_types(sql.parse("SELECT size(arr) AS n FROM c")[0], object())
    assert "ARRAY_LENGTH(arr)" in sql.duckdb_sql(out)
    assert not any(k in n.meta for n in out.walk() for k in ("kf_complex", "kf_complex_arg", "kf_complex_id"))


def test_to_json_of_null_is_refused_by_name(conn):
    with pytest.raises(spark_json.Refused, match="to_json"):
        typed(conn, "SELECT to_json(NULL) AS j")
    assert typed(conn, "SELECT to_json(named_struct('a', 1)) AS j").startswith("SELECT (LIST_TRANSFORM(")


def test_a_lateral_view_of_a_map_naming_three_columns_is_left_as_written(conn):
    text = typed(conn, "SELECT id, k FROM c LATERAL VIEW explode(m) t AS k, v, w")
    assert text == "SELECT id, k FROM c CROSS JOIN UNNEST(m) AS t(k, v, w)"


def test_explode_map_outside_a_select_list_is_left_alone():
    node = exp.Explode(this=exp.column("m"))
    holder = exp.Tuple(expressions=[node])
    sc._explode_map(node, "m")
    assert holder.expressions == [node]


def test_lateral_map_without_a_select_around_it_is_left_alone():
    lateral = exp.Lateral(this=exp.Explode(this=exp.column("m")),
                          alias=exp.TableAlias(this=exp.to_identifier("t"),
                                               columns=[exp.to_identifier("k"), exp.to_identifier("v")]))
    sc._lateral_map(lateral, exp.column("m"))
    assert lateral.parent is None and isinstance(lateral.this, exp.Explode)
