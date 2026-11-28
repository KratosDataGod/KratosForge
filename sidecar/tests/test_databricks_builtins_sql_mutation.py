"""databricks_builtins_sql.py paths that mutation testing (batch 18, docs/quality/mutation-testing.md) showed no
test checked: argument counts and their messages, constant arguments, and how calls are written."""

from __future__ import annotations

import duckdb
import pytest
from sqlglot import parse_one

from fabric_desktop.databricks_builtins_sql import rewrite


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def written(sql: str) -> str:
    return rewrite(parse_one(sql, read="databricks")).sql("duckdb")


def refused(sql: str) -> str:
    with pytest.raises(ValueError) as caught:
        written(sql)
    return str(caught.value)


@pytest.mark.parametrize("sql, message", [
    ("SELECT to_time()", "to_time() takes 1 or 2 arguments in Databricks, not 0."),
    ("SELECT to_time('1', '2', '3')", "to_time() takes 1 or 2 arguments in Databricks, not 3."),
    ("SELECT unix_nanos()", "unix_nanos() takes 1 argument in Databricks, not 0."),
    ("SELECT unix_nanos(1, 2)", "unix_nanos() takes 1 argument in Databricks, not 2."),
    ("SELECT bitmap_and_agg() FROM t", "bitmap_and_agg() takes 1 argument in Databricks, not 0."),
    ("SELECT bitmap_and_agg(a, b) FROM t", "bitmap_and_agg() takes 1 argument in Databricks, not 2."),
    ("SELECT ip_cidr_contains('a')", "ip_cidr_contains() takes 2 arguments in Databricks, not 1."),
    ("SELECT ip_cidr_contains('a', 'b', 'c')", "ip_cidr_contains() takes 2 arguments in Databricks, not 3."),
    ("SELECT hmac('a')", "hmac() takes 2 or 3 arguments in Databricks, not 1."),
    ("SELECT hmac('a', 'b', 'c', 'd')", "hmac() takes 2 or 3 arguments in Databricks, not 4."),
    ("SELECT vector_norm()", "vector_norm() takes 1 or 2 arguments in Databricks, not 0."),
    ("SELECT time_to_seconds()", "time_to_seconds() takes 1 argument in Databricks, not 0."),
    ("SELECT time_to_seconds(1, 2)", "time_to_seconds() takes 1 argument in Databricks, not 2."),
    ("SELECT is_valid_utf8()", "is_valid_utf8() takes 1 argument in Databricks, not 0."),
    ("SELECT is_valid_utf8('a', 'b')", "is_valid_utf8() takes 1 argument in Databricks, not 2."),
])
def test_a_call_with_the_wrong_number_of_arguments_is_refused_by_name(sql, message):
    assert refused(sql) == message


def test_time_diff_in_microseconds_counts_each_microsecond():
    sql = written("SELECT time_diff(MICROSECOND, TIME'00:00:00', TIME'00:00:01')")
    assert sql.endswith(" // 1")
    assert duckdb.connect().execute(sql).fetchall() == [(1_000_000,)]


def test_to_time_reads_a_column_with_a_constant_format_and_refuses_a_format_from_a_column():
    assert written("SELECT to_time(c, 'HH:mm') FROM t") == "SELECT CAST(STRPTIME(c, '%H:%M') AS TIME) FROM t"
    assert "its format isn't a constant string here" in refused("SELECT to_time('10:00', c) FROM t")


def test_try_make_timestamp_ltz_from_a_date_and_a_time_reads_them_in_the_zone_given():
    assert written("SELECT try_make_timestamp_ltz(d, t, 'UTC') FROM x") == \
        "SELECT TIMEZONE(('UTC'), (TRY(CAST((d) AS DATE) + CAST((t) AS TIME)))) FROM x"
    assert written("SELECT try_make_timestamp_ltz(d, t) FROM x") == \
        "SELECT CAST(TRY(CAST((d) AS DATE) + CAST((t) AS TIME)) AS TIMESTAMPTZ) FROM x"


def test_counter_diff_inside_another_functions_window_is_refused():
    assert refused("SELECT rank() OVER (PARTITION BY counter_diff(x) ORDER BY y) FROM t") == \
        "[WINDOW_FUNCTION_WITHOUT_OVER_CLAUSE] counter_diff() needs OVER (... ORDER BY ...)."


def test_a_function_of_a_persons_own_over_a_window_stays_as_written():
    assert written("SELECT myfunc(x) OVER (ORDER BY y) FROM t") == "SELECT MYFUNC(x) OVER (ORDER BY y NULLS FIRST) FROM t"


def test_bit_reverse_of_a_literal_past_int_stays_bigint():
    assert written("SELECT bit_reverse(2147483647)").startswith("SELECT CAST(__KF_BIT_REVERSE(")
    assert written("SELECT bit_reverse(2147483647)").endswith(" AS INT)")
    assert written("SELECT bit_reverse(2147483648)") == \
        "SELECT __KF_BIT_REVERSE(TYPEOF((2147483648)), (2147483648))"


def test_variant_get_takes_its_path_as_a_string_literal_only():
    assert refused("SELECT variant_get(v, 1, 'int') FROM t") == \
        "variant_get() takes its path as a string literal in Databricks."
    assert refused("SELECT variant_get(v, c, 'int') FROM t") == \
        "variant_get() takes its path as a string literal in Databricks."


def test_variant_explode_in_from_keeps_its_alias():
    sql = written("SELECT * FROM variant_explode(parse_json('[1,2]')) AS t(pos, key, value)")
    assert sql.endswith(") AS t(pos, key, value)")


def test_a_call_inside_another_is_written_too():
    assert written("SELECT zeroifnull(nullifzero(x)) FROM t") == "SELECT (COALESCE(((NULLIF((x), 0))), 0)) FROM t"


def test_a_statement_that_is_itself_the_call_is_written():
    assert rewrite(parse_one("zeroifnull(x)", read="databricks")).sql("duckdb") == "(COALESCE((x), 0))"
