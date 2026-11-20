"""spark_decimals.py's remaining paths: operands too wide for Spark's rule (DuckDB's type is kept), whole
numbers past BIGINT, avg with FILTER and OVER, round/ceil/floor places that can't be read or don't fit,
a bare expression as the statement, and pmod/bround CASEs whose ELSE isn't what sql.parse writes."""

from __future__ import annotations

import duckdb
import pytest
from sqlglot import exp, parse_one
from sqlglot.schema import MappingSchema

from fabric_desktop import spark_decimals as sd
from fabric_desktop import sql


@pytest.fixture
def conn():
    with duckdb.connect() as c:
        c.execute("CREATE TEMP VIEW t AS SELECT CAST('12345' AS DECIMAL(38,0)) AS big, CAST('1.5' AS DECIMAL(10,5)) AS small, "
                  "CAST('2.25' AS DECIMAL(10,2)) AS x, CAST(-1.5 AS DECIMAL(10,2)) AS y, 7 AS i, 2 AS n, "
                  "CAST('1.123456789' AS DECIMAL(38,30)) AS fine, CAST(5 AS DECIMAL(38,10)) AS wide")
        yield c


def rewrite(conn, expression):
    return sd.spark_decimal_types(sql.parse(f"SELECT {expression} AS v FROM t")[0], conn)


def run(conn, expression):
    rel = conn.sql(sql.duckdb_sql(rewrite(conn, expression)))
    return str(rel.types[0]), rel.fetchall()[0][0]


def unchanged(conn, expression):
    before = sql.duckdb_sql(sql.parse(f"SELECT {expression} AS v FROM t")[0])
    return sql.duckdb_sql(rewrite(conn, expression)) == before


def test_a_remainder_whose_operand_doesnt_fit_38_digits_at_the_scale_keeps_duckdbs_type(conn):
    # decimal(38,0) % decimal(10,5): Spark's scale 5 would need 43 digits for the dividend.
    assert unchanged(conn, "big % small")


def test_a_whole_number_literal_past_bigint_keeps_duckdbs_sum(conn):
    assert unchanged(conn, "i + 99999999999999999999")
    assert run(conn, "i + 99999999999999999999")[1] == 100000000000000000006


def test_avg_with_filter_and_over_keeps_both_and_gives_sparks_type(conn):
    kind, value = run(conn, "avg(x) FILTER (WHERE x > 0) OVER ()")
    assert kind == "DECIMAL(14,6)" and str(value) == "2.250000"
    kind, value = run(conn, "avg(y) FILTER (WHERE y > 0) OVER ()")
    assert kind == "DECIMAL(14,6)" and value is None   # the filter leaves no rows


def test_places_that_arent_a_literal_number_leave_round_as_it_was(conn):
    assert unchanged(conn, "round(x, n)")
    assert unchanged(conn, "round(x, '1')")
    assert sd._places(exp.Literal.string("1")) is None
    assert sd._places(exp.column("n")) is None
    assert sd._places(None) == 0
    assert sd._places(exp.Neg(this=exp.Literal.number(2))) == -2


def test_ceil_and_floor_to_places_that_dont_fit_38_digits_keep_duckdbs_type(conn):
    assert unchanged(conn, "ceil(wide, 5)")       # 38 digits moved left by 5
    assert unchanged(conn, "floor(fine, -10)")    # scale 30 moved right by 10
    assert sd._ceil_floor("CEIL", 38, 10, 5) is None
    assert sd._ceil_floor("FLOOR", 38, 30, -10) is None
    assert sd._ceil_floor("CEIL", 10, 2, 2) == "fj_arg0"
    kind, value = run(conn, "ceil(x, 1)")
    assert kind == "DECIMAL(10,1)" and str(value) == "2.3"


def test_a_bare_expression_as_the_statement_is_replaced_whole():
    st = parse_one("CAST(1.5 AS DECIMAL(10,2)) * CAST(2.5 AS DECIMAL(10,2))", read="duckdb")
    root = sd._retype(st, sd._targets(st), MappingSchema({}))
    assert isinstance(root, exp.Paren)
    with duckdb.connect() as c:
        rel = c.sql(f"SELECT {sql.duckdb_sql(root)}")
        assert str(rel.types[0]) == "DECIMAL(21,4)" and str(rel.fetchall()[0][0]) == "3.7500"
    st = parse_one("2147483647 + 1", read="duckdb")   # outside any SELECT it still wraps as Java does
    root = sd._retype(st, sd._targets(st), MappingSchema({}))
    with duckdb.connect() as c:
        assert c.execute(f"SELECT {sql.duckdb_sql(root)}").fetchone()[0] == -2147483648


def test_rewrap_of_the_node_itself_is_the_replacement():
    node = exp.column("a")
    other = exp.column("b")
    assert sd._rewrap(node, node, other) is other


def _written_case(name):
    from fabric_desktop.sql import SPARK_FUNCTION
    case = exp.Case(ifs=[], default=exp.column("a"))
    case.meta[SPARK_FUNCTION] = name
    return case


def test_a_pmod_or_bround_case_with_an_unexpected_else_has_no_operands_and_isnt_rewritten():
    pmod, bround = _written_case("PMOD"), _written_case("BROUND")
    assert sd._written(pmod) == "PMOD" and sd._operands(pmod) == []
    assert sd._operands(bround) == []
    assert sd._rewrite(pmod, {}, {}, []) is None
    assert sd._rewrite(bround, {}, {}, []) is None


def test_whole_rank_of_a_literal_that_isnt_plain_digits_is_unknown_and_of_a_wide_literal_is_bigint():
    assert sd._whole_rank(exp.Literal.number("1."), ("int", 1)) is None
    assert sd._whole_rank(exp.Neg(this=exp.Literal.number("3000000000")), ("int", 10)) == 20
    assert sd._whole_rank(exp.Paren(this=exp.Literal.number("5")), ("int", 1)) == 10
    assert sd._whole_rank(exp.column("c"), ("int", 7)) is None
