"""Snowflake SQL rewrites that the main tests leave out: commands KratosForge doesn't know, FLATTEN with a
second positional argument, NEXT_DAY and TIME_SLICE with computed arguments, epoch dates past DuckDB's
range, MERGE from an unnamed source, events in an UPDATE or beside a subquery's star, a statement sqlglot
can't qualify, divisions inside IFF, IDENTIFIER after a SET of a non-string, and the small helpers'
edges. Translation only; nothing runs."""

from __future__ import annotations

import pytest
from sqlglot import exp

from fabric_desktop import snowflake_sql as s


def one(code):
    t = s.translate(code)
    (st,) = t.statements
    return st


def ids(st):
    return [n.id for n in st.notes]


# ---------------------------------------------------------------- refusals


def test_a_snowflake_command_with_no_rule_is_refused_by_its_first_word():
    st = one("EXECUTE TASK t")
    assert st.verdict == "not_translatable" and ids(st) == ["SFN-099"]
    assert st.notes[0].reason == "EXECUTE is a Snowflake command KratosForge doesn't rewrite."


def test_a_change_event_outside_a_select_is_refused():
    st = one("UPDATE t SET n = CONDITIONAL_CHANGE_EVENT(x) OVER (ORDER BY y)")
    assert st.verdict == "not_translatable" and ids(st) == ["SFX-018"]
    assert "only in a SELECT" in st.notes[0].reason


def test_arrays_to_object_needs_two_arrays():
    st = one("SELECT ARRAYS_TO_OBJECT(a)")
    assert st.verdict == "not_translatable" and "key array and a value array" in st.notes[0].reason


# ---------------------------------------------------------------- rewrites


def test_flatten_takes_its_first_positional_argument_as_the_input():
    sql = one("SELECT * FROM TABLE(FLATTEN(INPUT => x, y))").duckdb_sql
    assert "JSON_EACH(CAST(x AS JSON))" in sql and "CAST(y" not in sql


def test_next_day_and_time_slice_with_computed_arguments_are_left_to_sqlglot():
    assert "STARTS_WITH(UPPER(x), 'MO')" in one("SELECT NEXT_DAY(d, x)").duckdb_sql
    assert one("SELECT TIME_SLICE(t, n, 'HOUR')").duckdb_sql == "SELECT TIME_BUCKET(INTERVAL (n) HOUR, t)"


def test_epoch_dates_past_duckdbs_range_stop_loudly_except_try_to_date():
    sql = one("SELECT TO_DATE('99999999999999999999999'), TRY_TO_DATE('99999999999999999999999')").duckdb_sql
    assert sql == "SELECT TO_DATE('99999999999999999999999'), CAST(NULL AS DATE)"


def test_to_timestamp_of_a_negated_or_bracketed_number_is_seconds_in_utc():
    sql = one("SELECT TO_TIMESTAMP(-(1)), TO_TIMESTAMP((40 * 365))").duckdb_sql
    assert sql.count("AT TIME ZONE 'UTC' AS TIMESTAMP)") == 2


def test_timestamp_from_parts_with_nanoseconds_adds_microseconds():
    sql = one("SELECT TIMESTAMP_FROM_PARTS(2024, 1, 1, 0, 0, 0, 500)").duckdb_sql
    assert "INTERVAL (CAST(500 AS BIGINT) // 1000) MICROSECOND" in sql


def test_merge_from_an_unnamed_subquery_gets_no_duplicate_check():
    st = one("MERGE INTO t USING (SELECT 1 AS id) ON t.id = id WHEN MATCHED THEN UPDATE SET x = 1")
    assert st.duckdb_sql == "MERGE INTO t USING (SELECT 1 AS id) ON t.id = id WHEN MATCHED THEN UPDATE SET x = 1"


def test_array_size_outside_a_select_is_rewritten_without_a_name():
    sql = one("UPDATE t SET n = ARRAY_SIZE(c)").duckdb_sql
    assert sql.startswith("UPDATE t SET n = CASE WHEN JSON_TYPE(") and " AS " not in sql.split("END")[-1]


def test_a_change_event_keeps_another_windows_order_and_a_subquerys_own_star():
    sql = one("SELECT *, (SELECT MAX(z) FROM (SELECT * FROM u)) AS m, "
              "CONDITIONAL_CHANGE_EVENT(SUM(z) OVER (PARTITION BY q)) OVER (ORDER BY y) FROM t").duckdb_sql
    assert sql.startswith("SELECT * EXCLUDE (__sf_place0, __sf_flag0), (SELECT MAX(z) FROM (SELECT * FROM u)) AS m")
    assert "SUM(z) OVER (PARTITION BY q)" in sql


def test_a_bracket_in_a_statement_sqlglot_cant_qualify_is_still_flagged():
    st = one("SELECT a[0] FROM t GROUP BY 5")
    assert st.verdict == "difference" and "SFD-009" in ids(st)


def test_divisions_inside_iff_skip_the_check_only_where_the_divisor_is_tested_for_zero():
    checked = "ERROR('Division by zero')"
    assert one("SELECT IFF(b = 0, 0, a / b) FROM t").duckdb_sql == "SELECT CASE WHEN b = 0 THEN 0 ELSE a / b END FROM t"
    for code in ("SELECT IFF(b = 1, 0, a / b) FROM t", "SELECT IFF(b = 0 AND c, 0, a / b) FROM t",
                 "SELECT IFF(c, 0, a / b) FROM t"):
        assert checked in one(code).duckdb_sql, code
    # b = 0 OR c: a zero divisor never reaches the division here, so either form gives the same rows.
    assert one("SELECT IFF(b = 0 OR c, 0, a / b) FROM t").verdict == "difference"


def test_identifier_after_a_set_of_a_number_is_not_a_name():
    t = s.translate("SET v = 'tbl';\nSET v = 1;\nSELECT * FROM IDENTIFIER($v) AS q")
    assert t.statements[2].verdict == "not_translatable"
    t = s.translate("SET v = 'tbl';\nSELECT * FROM IDENTIFIER($v) AS q")
    assert t.statements[1].duckdb_sql == "SELECT * FROM tbl AS q"


def test_typeof_of_paths_into_written_json():
    sql = one('SELECT TYPEOF(PARSE_JSON(\'{"a": 123456789012345678901234567890123456789}\'):a), '
              'TYPEOF(PARSE_JSON(\'{"a": {"b": 1}}\'):a), TYPEOF(PARSE_JSON(\'{"a": [1]}\'):a)').duckdb_sql
    assert sql == "SELECT 'DOUBLE', 'OBJECT', 'ARRAY'"


# ---------------------------------------------------------------- helpers


def test_scalar_kind_of_casts():
    assert s._scalar_kind(exp.cast(exp.Literal.number(1), "VARCHAR")) == "text"
    assert s._scalar_kind(exp.cast(exp.Literal.string("1"), "INT")) == "number"
    assert s._scalar_kind(exp.cast(exp.Literal.string("1"), "DATE")) == "other"
    assert s._scalar_kind(exp.Neg(this=exp.Literal.number(1))) == "number"


def test_strftime_refuses_an_unclosed_quote_and_unknown_letters():
    assert s._strftime('YYYY "at') is None
    assert s._strftime('YYYY "%x"') == "%Y %%x"
    assert s._strftime("YYYY ?") is None


def test_soundex_p123_fails_loudly_when_soundexs_template_changes(monkeypatch):
    from fabric_desktop import sql
    assert "kf_i = 1 OR" in s._soundex_p123_template()
    monkeypatch.setitem(sql._ANON_TEMPLATES, "SOUNDEX", "something else")
    with pytest.raises(RuntimeError, match="SOUNDEX_P123"):
        s._soundex_p123_template()


def test_binary_valued_reads_casts_and_functions():
    assert s._binary_valued(exp.Paren(this=exp.cast(exp.column("x"), "BINARY")))
    assert not s._binary_valued(exp.cast(exp.column("x"), "VARCHAR"))
    assert s._binary_valued(exp.Unhex(this=exp.column("x")))
    assert not s._binary_valued(exp.column("x"))


def test_epoch_date_reads_whole_numbers_by_size_and_refuses_the_rest():
    import datetime as dt
    assert s._epoch_date("abc") is None
    assert s._epoch_date("86400") == dt.date(1970, 1, 2)
    assert s._epoch_date("86400000") == dt.date(1972, 9, 27)          # still seconds
    assert s._epoch_date("31536000000") == dt.date(1971, 1, 1)       # milliseconds from here
    assert s._epoch_date("-99999999999999999999999") is None   # past what a date holds


def test_with_value_replaces_only_its_own_argument():
    tree = s._with_value("__sf_v + other + t.__sf_arg", exp.column("x"))
    names = sorted(c.sql() for c in tree.find_all(exp.Column))
    assert names == ["other", "t.__sf_arg", "x"]


def test_json_type_names():
    num = s._JsonNumber
    assert s._json_type_name(num("1" * 39)) == "DOUBLE"
    assert s._json_type_name(num("1.5")) == "DECIMAL" and s._json_type_name(num("-12")) == "INTEGER"
    assert s._json_type_name(num("1e3")) == "DOUBLE"
    assert s._json_type_name({"a": 1}) == "OBJECT" and s._json_type_name([1]) == "ARRAY"
    assert s._json_type_name(None) == "NULL_VALUE" and s._json_type_name(True) == "BOOLEAN"


def test_a_path_step_that_isnt_a_key_or_index_isnt_known():
    path = exp.JSONExtract(this=exp.ParseJSON(this=exp.Literal.string("[1, 2]")),
                           expression=exp.JSONPath(expressions=[exp.JSONPathRoot(), exp.JSONPathWildcard()]))
    assert s._typeof_of_literal_path(path) is s._NOT_KNOWN


def test_nonzero_number_of_text_that_isnt_a_number():
    assert s._nonzero_number(exp.Literal.number("abc")) is False
    assert s._nonzero_number(exp.Literal.number("0.0")) is False
    assert s._nonzero_number(exp.Literal.number("2")) is True
    assert s._nonzero_number(exp.Literal.string("2")) is False


@pytest.mark.parametrize(("code", "verdict", "want"), [
    ("SELECT CAST(DATEADD('day', 1, CAST(d AS DATE)) AS DATE) FROM t", None, "SELECT CAST(CAST(d AS DATE) + INTERVAL"),
    ("SELECT TO_CHAR(CURRENT_DATE, 'YYYY Q')", "not_translatable", "SFX-002"),
    ("SELECT {'a': 1, 2}", "translated", "SELECT {'a': 1, '_1': 2}"),
    ("SELECT ARRAY_INSERT(a, p, 1)", "not_translatable", "SFX-000"),
    ("SELECT $1 FROM t", "translated", 'SELECT #1 AS "$1" FROM t'),
    ("SELECT REGEXP_SUBSTR(s, x, 1, 1, 'e')", "translated", "REGEXP_EXTRACT(s, REGEXP_REPLACE(x, '^$', 'a^a', 'g'))"),
    ("SELECT TIMESTAMP_TZ_FROM_PARTS(2024, 1, 1, 0, 0, 0, 0, 'UTC')", "not_translatable", "SFX-000"),
    ("SELECT BITAND_AGG(DISTINCT x) FROM t", "translated", "SELECT BIT_AND(DISTINCT x) FROM t"),
    ("SELECT CAST(APPROX_PERCENTILE(x, 0.5) AS DOUBLE) FROM t", "difference",
     "SELECT CAST(APPROX_QUANTILE(x, 0.5) AS DOUBLE) FROM t"),
    ("SELECT TO_DATE(1)", "translated", None),
])
def test_rewrites_left_as_they_are_or_refused(code, verdict, want):
    st = one(code)
    if verdict:
        assert st.verdict == verdict
    if want and st.verdict == "not_translatable":
        assert ids(st) == [want]
    elif want:
        assert want in st.duckdb_sql
    if code.startswith("SELECT CAST(DATEADD"):
        assert st.duckdb_sql.count("AS DATE)") == 2   # not cast to DATE a second time


def test_a_set_of_several_variables_at_once_names_none():
    t = s.translate("SET (a, b) = ('x', 'y');\nSELECT * FROM IDENTIFIER($a)")
    assert t.statements[1].verdict == "not_translatable"
