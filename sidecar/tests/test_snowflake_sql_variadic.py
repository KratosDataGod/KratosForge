"""Key and value functions given an odd number of arguments (#2505).

sqlglot's build_var_map reads one argument past the end for OBJECT_CONSTRUCT('a', 1, 'b'), VAR_MAP('a')
or Spark's map('a') and stops with an IndexError, not a ParseError. sql_dialects.parse turns that into a
ParseError naming the function, so every SQL reader refuses the query the way it refuses any SQL it
can't read, instead of crashing."""

import pytest
import sqlglot
from sqlglot.errors import ParseError

from fabric_desktop import delta_dml, snowflake_sql, spark_local, sql, sql_dialects, sql_format, sql_portability
from fabric_desktop.engines import local_sql
from fabric_desktop.sqlcompat import analyzer

PAIRS = "needs key and value pairs"


@pytest.mark.parametrize("query,name", [
    ("SELECT OBJECT_CONSTRUCT('a', 1, 'b') FROM t", "OBJECT_CONSTRUCT"),
    ("SELECT OBJECT_CONSTRUCT('a') FROM t", "OBJECT_CONSTRUCT"),
    ("SELECT VAR_MAP('a', 1, 'b') FROM t", "VAR_MAP"),
])
def test_snowflake_refuses_an_odd_number_of_key_and_value_arguments_by_name(query, name):
    with pytest.raises(ValueError, match=f"Could not read the query: {name} {PAIRS}"):
        snowflake_sql.translate(query)


@pytest.mark.parametrize("query", ["SELECT OBJECT_CONSTRUCT('a', 1, 'b', 2) FROM t", "SELECT OBJECT_CONSTRUCT(*) FROM t"])
def test_snowflake_still_reads_pairs_and_a_star(query):
    assert snowflake_sql.translate(query).statements


@pytest.mark.parametrize("dialect", ["spark", "databricks"])
@pytest.mark.parametrize("args", ["'a'", "'a', 1, 'b'"])
def test_the_parse_error_names_spark_and_databricks_map(dialect, args):
    with pytest.raises(ParseError, match=f"MAP {PAIRS}"):
        sql_dialects.parse(f"SELECT map({args})", read=dialect)
    with pytest.raises(ParseError, match=f"MAP {PAIRS}"):
        sql_dialects.parse_one(f"SELECT map({args})", read=dialect)


def test_reading_pairs_is_unchanged():
    assert sql_dialects.parse("SELECT map('a', 1)", read="spark")[0] == sqlglot.parse("SELECT map('a', 1)", read="spark")[0]
    assert sql_dialects.parse_one("SELECT 1", read="spark") == sqlglot.parse_one("SELECT 1", read="spark")


def test_any_other_index_error_is_raised_as_it_is(monkeypatch):
    other = IndexError("some other bug")

    def broken(*args, **kwargs):
        raise other
    monkeypatch.setattr(sqlglot, "parse", broken)
    monkeypatch.setattr(sqlglot, "parse_one", broken)
    with pytest.raises(IndexError) as raised:
        sql_dialects.parse("SELECT 1", read="spark")
    assert raised.value is other
    with pytest.raises(IndexError) as raised:
        sql_dialects.parse_one("SELECT 1", read="spark")
    assert raised.value is other


def test_spark_sql_reports_it_instead_of_reading_the_query_as_duckdb():
    # DuckDB's reader would keep MAP('a', 1) and drop 'b'; the query is refused as written instead
    with pytest.raises(ValueError, match=f"Could not read the query: MAP {PAIRS}"):
        sql.parse("SELECT map('a', 1, 'b')")
    assert sql.parse("SELECT map('a', 1)")[0].sql("duckdb") == "SELECT MAP(['a'], [1])"


def test_databricks_sql_refuses_it():
    with pytest.raises(ValueError, match=f"Could not read the query: MAP {PAIRS}"):
        local_sql._databricks("SELECT map('a')")


@pytest.mark.parametrize("code,dialect", [("SELECT OBJECT_CONSTRUCT('a')", "snowflake"),
                                          ("SELECT map('a')", "databricks"), ("SELECT map('a')", None)])
def test_a_session_check_leaves_it_to_duckdb_without_crashing(code, dialect):
    assert local_sql._refuse_outside(code, dialect) is None


def test_format_sql_leaves_it_as_it_was():
    out = sql_format.format_sql("SELECT map('a', 1, 'b')", "spark")
    assert out["changed"] is False and out["sql"] == "SELECT map('a', 1, 'b')"
    assert f"MAP {PAIRS}" in out["warning"]


@pytest.mark.parametrize("query,source,name", [("SELECT OBJECT_CONSTRUCT('a', 1, 'b')", "snowflake", "OBJECT_CONSTRUCT"),
                                               ("SELECT map('a')", "spark", "MAP")])
def test_portability_says_it_could_not_read_it(query, source, name):
    assert f"{name} {PAIRS}" in sql_portability.portability(query, source)["problem"]


def test_the_compatibility_check_marks_it_unverified():
    report = analyzer.analyze("SELECT map('a', 1, 'b')", "spark")
    assert report["outcome"]["status"] == "unverified"


def test_a_change_statement_with_it_is_refused():
    with pytest.raises(ValueError, match=f"Could not read the statement: MAP {PAIRS}"):
        delta_dml.parse("UPDATE t SET m = map('a')")


def test_spark_sql_refusals_read_past_it():
    assert spark_local.sql_refusal("SELECT map('a', 1, 'b')") is None
