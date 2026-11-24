"""localdata/tsql.py paths the uncapped mutation run (batch 223, docs/quality/mutation-testing.md) showed no test
checked. Each is a translation, checked as text or by the refusal it gives; nothing here runs a database."""

from __future__ import annotations

import datetime as dt

import pytest

from fabric_desktop.localdata.tsql import translate

UTC = dt.timezone.utc
COLUMNS = {"dbo": {"t": {"a": "INTEGER"}}, "sales": {"u": {"a": "INTEGER"}}}


def ago(days: float) -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


# ---- time travel: 30 days of history by default, 120 at most


def as_of(days: float) -> list[str]:
    return translate(f"SELECT a FROM dbo.t OPTION (FOR TIMESTAMP AS OF '{ago(days)}')", COLUMNS)[1]


def test_a_time_travel_read_past_30_days_says_a_warehouse_may_not_keep_it():
    assert as_of(29.9) == []
    notes = as_of(30.1)
    assert len(notes) == 1 and "is more than 30 days ago" in notes[0] and "(to at most 120)" in notes[0]


def test_a_time_travel_read_past_120_days_is_refused():
    assert len(as_of(119.9)) == 1
    with pytest.raises(ValueError, match=r"is more than 120 days ago, .*\(30 days by default, at most 120\)"):
        as_of(120.1)


# ---- UPDATE x ... FROM dbo.t AS x: the x is the alias, not a table


@pytest.mark.parametrize("sql", ["UPDATE u SET a = 1 FROM dbo.t AS u", "DELETE u FROM dbo.t AS u"])
def test_a_change_through_its_froms_alias_isnt_read_as_a_table_of_that_name(sql):
    assert translate(sql, COLUMNS)[0][0].startswith(sql.split()[0] + " ")


@pytest.mark.parametrize("sql", ["UPDATE u SET a = 1", "UPDATE u SET a = 1 FROM dbo.t AS y",
                                 "DELETE u FROM dbo.t AS y"])
def test_a_name_that_is_no_alias_there_is_looked_for_in_dbo(sql):
    with pytest.raises(ValueError, match="Invalid object name 'u'.*Write sales.u"):
        translate(sql, COLUMNS)


# ---- views, #temp tables, CONVERT to text, and decimal arithmetic near 38 digits


def test_a_views_names_without_a_schema_are_bound_to_dbo_when_it_is_made():
    out = translate("CREATE VIEW v AS WITH c AS (SELECT a FROM t) SELECT a FROM c JOIN #tmp ON 1 = 1", COLUMNS)[0]
    assert out == ['CREATE VIEW v AS WITH c AS (SELECT a FROM dbo.t) SELECT a FROM c JOIN "#tmp" ON 1 = 1']


def test_a_hash_table_is_made_as_a_temporary_table():
    assert translate("CREATE TABLE #t (a INT)")[0] == ['CREATE TEMPORARY TABLE "#t" (a INT)']


def test_a_number_converted_to_text_too_long_for_its_length_is_an_error():
    out = translate("SELECT CONVERT(VARCHAR(2), 123, 0)")[0][0]
    assert "ERROR('The number doesn''t fit in VARCHAR(2).')" in out


def test_only_try_cast_turns_a_text_that_doesnt_fit_into_null():
    assert "TRY_CAST" not in translate("SELECT CAST(a AS VARCHAR(5)) FROM dbo.t", COLUMNS)[0][0]
    assert "TRY_CAST" in translate("SELECT TRY_CAST(a AS VARCHAR(5)) FROM dbo.t", COLUMNS)[0][0]


def test_decimal_arithmetic_is_exact_only_while_a_hugeints_38_digits_hold_it():
    wide = {"dbo": {"t": {"a": "DECIMAL(38,10)", "b": "DECIMAL(20,2)", "c": "DECIMAL(38,0)", "d": "DECIMAL(10,9)"}}}
    assert "< 1e25 THEN CAST((a) * (b) AS DECIMAL(38, 6))" in translate("SELECT a * b FROM dbo.t", wide)[0][0]
    assert "< 1e28 THEN CAST((c) + (d) AS DECIMAL(38, 0))" in translate("SELECT c + d FROM dbo.t", wide)[0][0]


# ---- CHAR, REPLICATE and LEFT at their edges, run on DuckDB

import duckdb  # noqa: E402


def run(sql: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        return con.execute(translate(sql)[0][0]).fetchall()
    finally:
        con.close()


def stops_with(sql: str, message: str) -> None:
    """``sql`` stops with T-SQL's ``message``, when it is translated or when it runs."""
    with pytest.raises((ValueError, duckdb.Error), match=message):
        run(sql)


def test_char_of_a_column_is_null_outside_0_to_255_and_cuts_a_fraction():
    assert run("SELECT CHAR(a) FROM (VALUES (65), (300), (66.7)) AS v(a)") == [("A",), (None,), ("B",)]
    assert run("SELECT CHAR(a) FROM (VALUES ('66')) AS v(a)") == [("B",)]


def test_char_of_text_that_isnt_a_whole_number_is_t_sqls_int_conversion_error():
    with pytest.raises(duckdb.Error, match=r"Conversion failed when converting the varchar value '6\.5' to data type int\."):
        run("SELECT CHAR(a) FROM (VALUES ('6.5')) AS v(a)")
    with pytest.raises(ValueError, match=r"Conversion failed when converting the varchar value '6\.5' to data type int\."):
        translate("SELECT CHAR('6.5')")


def test_a_replicate_count_that_isnt_a_whole_number_is_t_sqls_bigint_conversion_error():
    with pytest.raises(ValueError, match=r"Error converting data type varchar to bigint\. T-SQL gives the same error"):
        translate("SELECT REPLICATE('a', '6.5')")


def test_a_left_longer_than_any_text_is_cut_to_the_longest_text():
    assert translate("SELECT LEFT('abc', 3000000000)")[0] == ["SELECT LEFT('abc', 2147483647)"]


@pytest.mark.parametrize("sql", [
    "UPDATE dbo.t SET a = (SELECT a FROM u) FROM dbo.t AS u",  # the subquery's u is a table, not the FROM's alias
    "MERGE dbo.t AS u USING (SELECT a FROM u) AS s ON u.a = s.a WHEN MATCHED THEN DELETE;",
])
def test_only_the_target_after_update_or_delete_can_name_an_alias(sql):
    with pytest.raises(ValueError, match="Invalid object name 'u'.*Write sales.u"):
        translate(sql, COLUMNS)


# ---- decimal types: integers widened to T-SQL's precision, and an aggregate's own cast


def run_on_t(sql: str, columns: dict[str, str], rows: str) -> list[tuple]:
    con = duckdb.connect()
    try:
        con.execute("CREATE SCHEMA dbo")
        con.execute(f"CREATE TABLE dbo.t ({', '.join(f'{c} {k}' for c, k in columns.items())})")
        con.execute(f"INSERT INTO dbo.t VALUES {rows}")
        return con.execute(translate(sql, {"dbo": {"t": columns}})[0][0]).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("kind, product", [("TINYINT", "DECIMAL(14, 2)"), ("UTINYINT", "DECIMAL(14, 2)"),
                                           ("SMALLINT", "DECIMAL(16, 2)")])
def test_a_small_integer_times_a_decimal_widens_to_t_sqls_precision(kind, product):
    out = translate("SELECT x * n FROM dbo.t", {"dbo": {"t": {"x": "DECIMAL(10,2)", "n": kind}}})[0][0]
    assert f"AS {product})" in out


def test_an_aggregate_cast_to_int_divides_as_an_int():
    assert run_on_t("SELECT CAST(SUM(x) AS INT) / 3 FROM dbo.t", {"x": "DECIMAL(10,2)"}, "(4.50), (5.50)") == [(3,)]


def test_style_121_always_shows_the_fraction_and_126_only_when_there_is_one():
    plain = "CAST('2026-01-02 03:04:05' AS DATETIME2)"
    assert run(f"SELECT CONVERT(VARCHAR(30), {plain}, 121)")[0][0].startswith("2026-01-02 03:04:05.000")
    assert run(f"SELECT CONVERT(VARCHAR(30), {plain}, 126)") == [("2026-01-02T03:04:05",)]


# ---- ORDER BY text keys ignore trailing spaces, as T-SQL compares text (all but the last key)

TEXT = {"dbo": {"t": {"a": "INTEGER", "s": "VARCHAR"}}}


@pytest.mark.parametrize("sql, order", [
    ("SELECT s AS x, a FROM dbo.t ORDER BY x, a", "ORDER BY RTRIM(s, ' ') NULLS FIRST, a NULLS FIRST"),
    ("SELECT s, a FROM dbo.t ORDER BY 1, 2", "ORDER BY RTRIM(s, ' ') NULLS FIRST, 2 NULLS FIRST"),
    ("SELECT t.s AS s, a FROM dbo.t ORDER BY s, a", "ORDER BY RTRIM(t.s, ' ') NULLS FIRST, a NULLS FIRST"),
    # left alone: a window's result, and a SELECT DISTINCT (DuckDB orders it only by its columns)
    ("SELECT FIRST_VALUE(s) OVER (ORDER BY a) AS f, a FROM dbo.t ORDER BY f, a", "ORDER BY f NULLS FIRST, a NULLS FIRST"),
    ("SELECT DISTINCT s, a FROM dbo.t ORDER BY s, a", "ORDER BY s NULLS FIRST, a NULLS FIRST"),
])
def test_an_order_by_text_key_is_ordered_without_trailing_spaces(sql, order):
    assert translate(sql, TEXT)[0][0].endswith(order)


def test_a_json_path_that_is_a_number_is_refused_as_not_written_out_as_text():
    with pytest.raises(ValueError, match="JSON_VALUE with a path that isn't written out as text isn't mapped yet"):
        translate("SELECT JSON_VALUE('{\"a\": 1}', 5)")


def test_char_of_a_literal_is_worked_out_here():
    assert translate("SELECT CHAR(65)")[0] == ["SELECT CHR(65)"]
    assert translate("SELECT CHAR(300)")[0] == ["SELECT CAST(NULL AS TEXT)"]
    stops_with("SELECT CHAR(99999999999)", "Arithmetic overflow error converting expression to data type int")


def test_upper_keeps_the_sharp_s_as_t_sql_does():
    assert run("SELECT UPPER('straße')") == [("STRAßE",)]


def test_a_count_of_thousands_of_digits_is_t_sqls_overflow_not_pythons_limit():
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type bigint"):
        translate(f"SELECT REPLICATE('a', {'9' * 5000})")


def test_what_a_script_ends_inside():
    from fabric_desktop.localdata import tsql
    assert tsql._left_open("'abc") == "a string"
    assert tsql._left_open("SELECT 1 -- c\nSELECT 'x") == "a string"
    assert tsql._left_open("SELECT 1 -- c") is None


def test_an_update_from_a_subquery_has_no_alias_for_its_target():
    with pytest.raises(ValueError, match="Invalid object name 'u'"):
        translate("UPDATE u SET a = 1 FROM (SELECT 1 AS a) AS s", COLUMNS)


def test_a_table_made_from_a_query_isnt_a_view_so_its_names_arent_bound_to_dbo():
    assert translate("CREATE TABLE dbo.n AS SELECT a FROM t", COLUMNS)[0] == ["CREATE TABLE dbo.n AS SELECT a FROM t"]


def test_isnull_of_an_integer_and_an_integer_is_left_as_coalesce_with_no_cast():
    assert translate("SELECT ISNULL(a, 2) FROM t", COLUMNS)[0] == ["SELECT COALESCE(a, 2) FROM t"]


def test_a_wide_decimal_product_that_fits_is_worked_out_exactly_with_no_floating_point_note():
    out = translate("SELECT w * w FROM t", {"dbo": {"t": {"w": "DECIMAL(38,2)"}}})
    assert out == (["SELECT CAST(CAST(w AS DECIMAL(38, 2)) * w AS DECIMAL(38, 4)) FROM t"], [])


@pytest.mark.xfail(strict=True, reason=(
    "Known bug: a comparison against ALL (SELECT ...) or a non-= ANY (SELECT ...) isn't taken through RTRIM. "
    "sqlglot gives the ALL/ANY a Select, not a Subquery, so the padded-comparison branch never matches, and "
    "'a ' > ALL (SELECT 'a') is true locally where T-SQL, ignoring trailing spaces, says false"))
def test_a_comparison_against_all_of_a_subquery_ignores_trailing_spaces_as_t_sql_does():
    columns, rows = {"s": "VARCHAR(10)"}, "('a')"
    assert run_on_t("SELECT 1 WHERE 'a ' > ALL (SELECT s FROM dbo.t)", columns, rows) == []
    assert run_on_t("SELECT 1 WHERE 'a ' = ALL (SELECT s FROM dbo.t)", columns, rows) == [(1,)]


def test_degrees_of_an_integer_is_cut_toward_zero_not_rounded():
    assert run_on_t("SELECT DEGREES(a) FROM dbo.t", {"a": "INTEGER"}, "(2)") == [(114,)]


def test_a_datetime2_with_one_digit_of_a_second_shows_that_digit_as_text():
    assert run("SELECT CAST(CAST('2024-02-29 13:04:05.1' AS DATETIME2(1)) AS VARCHAR(30))") == [("2024-02-29 13:04:05.1",)]


def test_a_product_one_digit_past_38_is_given_38_digits():
    columns = {"dbo": {"t": {"x": "DECIMAL(19,2)", "y": "DECIMAL(19,2)"}}}
    assert translate("SELECT x * y FROM dbo.t", columns)[0] == ["SELECT CAST(CAST(x AS DECIMAL(38, 2)) * y AS DECIMAL(38, 4)) FROM dbo.t"]


def test_text_in_a_subquery_of_integers_has_no_note_about_unknown_types():
    columns = {"dbo": {"t": {"s": "VARCHAR(5)", "n": "INTEGER"}}}
    assert translate("SELECT 1 FROM dbo.t WHERE s IN (SELECT n FROM dbo.t)", columns) == (
        ["SELECT 1 FROM dbo.t WHERE s IN (SELECT n FROM dbo.t)"], [])


def test_an_n_string_in_a_function_other_than_datalength_is_plain_text():
    assert translate("SELECT QUOTENAME(N'a')")[0] == [
        "SELECT CASE WHEN LENGTH('a') > 128 THEN NULL ELSE '[' || REPLACE('a', ']', ']]') || ']' END"]


def test_a_replicate_count_with_a_fraction_is_cut_toward_zero_here():
    assert translate("SELECT REPLICATE('ab', 1.7)")[0] == ["SELECT REPEAT('ab', 1)"]


def test_regexp_count_is_an_int_so_a_division_of_it_is_an_integer_division():
    assert translate("SELECT REGEXP_COUNT('abab', 'a') / 2")[0][0].endswith(" AS INT) // 2")
    assert run("SELECT REGEXP_COUNT('abab', 'a') / 2") == [(1,)]


def test_an_order_by_key_with_a_table_is_the_tables_column_not_an_output_alias():
    columns = {"dbo": {"t": {"s": "VARCHAR(5)", "a": "INTEGER"}}}
    assert translate("SELECT a AS s, s AS x FROM dbo.t ORDER BY t.s, x", columns)[0] == [
        "SELECT a AS s, s AS x FROM dbo.t ORDER BY RTRIM(t.s, ' ') NULLS FIRST, x NULLS FIRST"]


def test_text_in_a_list_with_an_int_and_a_decimal_is_left_to_duckdb():
    columns = {"dbo": {"t": {"s": "VARCHAR(5)", "n": "INTEGER", "d": "DECIMAL(5,1)"}}}
    assert translate("SELECT 1 FROM dbo.t WHERE s IN (n, d)", columns)[0] == ["SELECT 1 FROM dbo.t WHERE s IN (n, d)"]


def test_a_bare_name_in_a_deletes_subquery_is_a_table_even_when_the_target_has_that_alias():
    with pytest.raises(ValueError, match="Invalid object name 'u'.*Write sales.u"):
        translate("DELETE u FROM dbo.t AS u WHERE EXISTS (SELECT 1 FROM u)", COLUMNS)


def test_an_insert_with_a_column_list_into_a_bare_name_is_looked_for_in_dbo():
    with pytest.raises(ValueError, match="Invalid object name 'u'.*Write sales.u"):
        translate("INSERT INTO u (a) VALUES (1)", COLUMNS)


def test_a_security_word_after_a_dot_is_a_column_not_a_statement():
    assert translate("SELECT t.revoke FROM dbo.t AS t")[0] == ["SELECT t.revoke FROM dbo.t AS t"]


def test_space_of_a_bigint_past_an_int_is_t_sqls_int_overflow():
    # checked in the text, not run: a mutant that drops the check would build a 3 GB string
    out = translate("SELECT SPACE(n) FROM dbo.t", {"dbo": {"t": {"n": "BIGINT"}}})[0][0]
    assert "Arithmetic overflow error converting expression to data type int" in out


def test_counts_just_past_an_int_and_a_bigint_overflow():
    stops_with("SELECT SPACE(2147483648)", "Arithmetic overflow error converting expression to data type int")
    with pytest.raises(ValueError, match="Arithmetic overflow error converting expression to data type bigint"):
        translate("SELECT REPLICATE('a', 9223372036854775808)")


def test_ascii_and_unicode_of_empty_text_are_null():
    assert run("SELECT ASCII(''), UNICODE('')") == [(None, None)]
    assert run("SELECT ASCII('A')") == [(65,)]


def test_upper_of_a_number_is_its_text():
    assert run("SELECT UPPER(5)") == [("5",)]


def test_charindex_of_a_plain_string_from_the_start_is_left_as_strpos():
    assert translate("SELECT CHARINDEX('b', 'abc')")[0] == ["SELECT STRPOS('abc', 'b')"]


def test_a_quote_ending_a_line_comment_opens_no_string():
    from fabric_desktop.localdata import tsql
    assert tsql._left_open("SELECT 1 -- it's\nSELECT 2") is None
    assert tsql._left_open("SELECT 1 -- '\nSELECT 2") is None


def test_the_column_list_is_read_without_regard_to_case():
    assert translate("SELECT n / 2 FROM t", {"DBO": {"T": {"N": "INTEGER"}}}) == (["SELECT n // 2 FROM t"], [])


def test_isnull_with_a_first_argument_of_a_sized_date_type_leaves_the_second_alone():
    assert translate("SELECT ISNULL(CAST(NULL AS DATETIME2(3)), '2024-01-02 03:04:05.6789')")[0] == [
        "SELECT COALESCE(CAST(NULL AS TIMESTAMP(3)), '2024-01-02 03:04:05.6789')"]


def test_a_decimal_written_with_only_a_precision_has_scale_0_in_a_product():
    columns = {"dbo": {"t": {"d": "DECIMAL(5)"}}}
    assert translate("SELECT d * d FROM dbo.t", columns)[0] == ["SELECT CAST(d * d AS DECIMAL(11, 0)) FROM dbo.t"]


def test_patindex_of_a_number_is_refused_as_not_written_out_as_text():
    with pytest.raises(ValueError, match="PATINDEX needs its pattern written out as text"):
        translate("SELECT PATINDEX(5, 'a5')")


def test_an_integer_between_integers_has_no_note():
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    assert translate("SELECT 1 FROM dbo.t WHERE n BETWEEN 1 AND 2", columns) == (
        ["SELECT 1 FROM dbo.t WHERE n BETWEEN 1 AND 2"], [])


def test_grouping_on_a_text_column_named_rtrim_still_has_the_grouping_note():
    columns = {"dbo": {"t": {"rtrim": "VARCHAR(5)"}}}
    notes = translate("SELECT rtrim FROM dbo.t GROUP BY rtrim", columns)[1]
    assert len(notes) == 1 and notes[0].startswith("T-SQL counts text that differs only in trailing spaces")


def test_a_subquerys_alias_in_an_updates_from_isnt_its_target():
    with pytest.raises(ValueError, match="Invalid object name 'u'.*Write sales.u"):
        translate("UPDATE u SET a = 1 FROM dbo.t AS x JOIN (SELECT 1 AS a) AS u ON 1 = 1", COLUMNS)


def test_a_delete_with_a_join_says_it_couldnt_tell_a_divisions_types():
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    out, notes = translate("DELETE t FROM dbo.t AS t JOIN dbo.t AS v ON t.n = v.n WHERE t.n / 2 = 1", columns)
    assert out == ["DELETE FROM dbo.t AS t USING dbo.t AS v WHERE (t.n = v.n) AND (t.n / 2 = 1)"]
    assert notes == [("Couldn't tell the types in t.n / 2: it divides as DuckDB does, and T-SQL divides two "
                      "integers as integers.")]


def test_a_four_part_name_is_told_its_own_schema():
    with pytest.raises(ValueError, match=r"four-part names \(srv.db.sales.u\).*Write sales.u for this project's table"):
        translate("SELECT * FROM srv.db.sales.u")


def test_a_sized_column_of_a_table_with_no_schema_names_the_table_alone_in_its_error():
    assert "truncated in table ''x'', column ''s''." in translate("CREATE TABLE x (s VARCHAR(3))")[0][0]


def test_a_like_pattern_that_is_a_number_has_the_like_note():
    notes = translate("SELECT 1 WHERE 'a5' LIKE 5")[1]
    assert len(notes) == 1 and notes[0].startswith("A LIKE pattern that isn't written in the query")


def test_a_wide_product_whose_size_is_past_the_room_is_worked_out_in_floating_point():
    columns = {"x": "DECIMAL(38,10)", "y": "DECIMAL(10,2)"}
    out = translate("SELECT x * y FROM dbo.t", {"dbo": {"t": columns}})[0][0]
    assert "ABS(CAST((x) AS DOUBLE)) * ABS(CAST((y) AS DOUBLE)) < 1e25" in out
    [(value,)] = run_on_t("SELECT x * y FROM dbo.t", columns, "(100000000000000000000, 1000000)")
    assert abs(value - 10 ** 26) < 10 ** 12  # floating point: about the first 15 digits


def test_regexp_likes_s_flag_is_moved_into_the_pattern():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)"}}}
    assert translate("SELECT 1 FROM dbo.t WHERE REGEXP_LIKE(s, 'a.b', 's')", columns)[0] == [
        "SELECT 1 FROM dbo.t WHERE REGEXP_MATCHES(s, '(?s)a.b')"]


def test_nullif_of_integers_is_left_alone():
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    assert translate("SELECT NULLIF(n, 1) FROM dbo.t", columns)[0] == ["SELECT NULLIF(n, 1) FROM dbo.t"]


def test_an_order_by_alias_is_matched_by_its_name_not_by_being_an_alias():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)", "a": "INTEGER"}}}
    assert translate("SELECT a AS y, s AS x FROM dbo.t ORDER BY x, a", columns)[0] == [
        "SELECT a AS y, s AS x FROM dbo.t ORDER BY RTRIM(s, ' ') NULLS FIRST, a NULLS FIRST"]


def test_strs_length_runs_from_1_to_8000():
    for length in (0, 8001):
        with pytest.raises(ValueError, match="STR's length has to be a whole number from 1 to 8000"):
            translate(f"SELECT STR(1, {length})")
    assert run("SELECT STR(1, 1)") == [("1",)]
    assert run("SELECT LEN(STR(1, 8000))") == [(8000,)]  # padded on the left


def test_translate_of_numbers_still_checks_the_lists_lengths():
    assert translate("SELECT TRANSLATE('abc', 1, 2)")[0][0].startswith("SELECT CASE WHEN LENGTH(1) <> LENGTH(2)")


def test_a_replicate_count_under_one_is_cut_to_zero():
    assert translate("SELECT REPLICATE('ab', 0.5)")[0] == ["SELECT REPEAT('ab', 0)"]


def test_replicate_of_a_column_zero_times_is_checked_without_working_out_the_count():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)"}}}
    out = translate("SELECT REPLICATE(s, 0) FROM dbo.t", columns)[0][0]
    assert out.startswith("SELECT CASE WHEN CAST(STRLEN(CAST(s AS TEXT)) AS DOUBLE) * 0 > 8000")


def test_a_negative_count_written_as_text_is_null():
    assert run("SELECT REPLICATE('a', '-5')") == [(None,)]


def test_timefromparts_at_precision_7_drops_the_last_digit_exactly():
    out = translate("SELECT TIMEFROMPARTS(1, 2, 3, 1234560, 7)")[0][0]
    assert "NOT ((123456)) IS NULL" in out and "0.1" not in out
    assert run("SELECT TIMEFROMPARTS(1, 2, 3, 1234560, 7)") == [(dt.time(1, 2, 3, 123456),)]


def test_a_count_of_thirteen_nodes_is_bound_once_and_not_copied():
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    count = "CAST(CAST(CAST(CAST(CAST((n) AS INT) AS INT) AS INT) AS INT) AS INT)"  # 13 nodes, one past _small's 12
    out = translate(f"SELECT SPACE({count}) FROM dbo.t", columns)[0][0]
    assert out.startswith(f"SELECT LIST_TRANSFORM([STRUCT_PACK(a := LIST_TRANSFORM([STRUCT_PACK(a := {count})]")


def test_charindex_from_1_is_from_the_start():
    assert translate("SELECT CHARINDEX('b', 'abc', 1)")[0] == ["SELECT STRPOS('abc', 'b')"]


def test_charindex_from_a_column_below_1_looks_from_the_start():
    assert run_on_t("SELECT CHARINDEX('b', 'abc', n) FROM dbo.t", {"n": "INTEGER"}, "(-5), (3), (NULL)") == [
        (2,), (0,), (None,)]


def test_a_function_duckdb_cant_write_is_refused_by_name():
    with pytest.raises(ValueError, match="This T-SQL can't be run on the local engine: SOUNDEX is not supported in DuckDB"):
        translate("SELECT SOUNDEX('abc')")


@pytest.mark.parametrize("sql", ["CREATE TABLE x (a INT); THROW 50000, 'x', 1",
                                 "SELECT 1 AS [function]; THROW 50000, 'x', 1"])
def test_throw_is_refused_by_name_when_the_batch_makes_no_routine(sql):
    with pytest.raises(ValueError, match="The local engine doesn't run T-SQL THROW yet"):
        translate(sql)


@pytest.mark.xfail(strict=True, reason=(
    "Known gap: SELECT DISTINCT * and a UNION of SELECT * over a text column give no note that 'a' and 'a ' stay "
    "apart here (WR-210). The typed copy is qualified with expand_stars=False, so _grouped_text sees a Star, "
    "whose type isn't text, where SELECT DISTINCT s gets the note"))
@pytest.mark.parametrize("sql", ["SELECT DISTINCT * FROM dbo.t", "SELECT * FROM dbo.t UNION SELECT * FROM dbo.t"])
def test_distinct_over_a_star_with_a_text_column_has_the_grouping_note(sql):
    notes = translate(sql, {"dbo": {"t": {"s": "VARCHAR(20)", "n": "INTEGER"}}})[1]
    assert len(notes) == 1 and notes[0].startswith("T-SQL counts text that differs only in trailing spaces")


def test_a_wide_product_with_scale_7_and_34_whole_digits_is_cut_to_scale_6():
    columns = {"dbo": {"t": {"x": "DECIMAL(31,7)", "y": "DECIMAL(9,0)"}}}
    assert "AS DECIMAL(38, 6))" in translate("SELECT x * y FROM dbo.t", columns)[0][0]


def test_zero_point_is_a_decimal_of_precision_1():
    columns = {"dbo": {"t": {"d": "DECIMAL(5,2)"}}}
    assert translate("SELECT 0. * d FROM dbo.t", columns)[0] == ["SELECT CAST(0. * d AS DECIMAL(7, 2)) FROM dbo.t"]


def test_a_negated_like_class_doesnt_hold_its_own_caret():
    assert run("SELECT 1 WHERE '^' LIKE '[^a]'") == [(1,)]
    assert run("SELECT 1 WHERE 'a' LIKE '[^a]'") == []


@pytest.mark.parametrize("where", ["t.n BETWEEN u.z AND 2", "t.n IN (u.z, 2)"])
def test_an_unknown_side_with_no_text_has_no_padding_note(where):
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    assert translate(f"SELECT 1 FROM dbo.t, dbo.unk AS u WHERE {where}", columns)[1] == []


def test_a_simple_case_with_an_arm_that_isnt_text_is_left_alone():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)", "n": "INTEGER"}}}
    sql = "SELECT CASE s WHEN 'a' THEN 1 WHEN n THEN 2 END FROM dbo.t"
    assert translate(sql, columns)[0] == [sql]


def test_an_order_by_number_that_isnt_whole_names_no_column():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)", "a": "INTEGER"}}}
    assert translate("SELECT s, a FROM dbo.t ORDER BY 1.5, a", columns)[0] == [
        "SELECT s, a FROM dbo.t ORDER BY 1.5 NULLS FIRST, a NULLS FIRST"]


def test_strs_decimals_run_to_16():
    with pytest.raises(ValueError, match="decimals"):
        translate("SELECT STR(1, 10, 17)")


def test_substring_from_0_counts_the_place_before_the_text():
    assert run("SELECT SUBSTRING('abc', 0, 2)") == [("a",)]
    assert "GREATEST(0, 1)" in translate("SELECT SUBSTRING('abc', 0, 2)")[0][0]  # worked out, not left to DuckDB


def test_substring_of_length_0_has_no_negative_length_check():
    assert "Invalid length parameter" not in translate("SELECT SUBSTRING('abc', 0, 0)")[0][0]
    assert run("SELECT SUBSTRING('abc', 0, 0)") == [("",)]


def test_char_just_below_an_int_is_t_sqls_int_overflow():
    assert run("SELECT CHAR(-2147483648)") == [(None,)]
    stops_with("SELECT CHAR(-2147483649)", "Arithmetic overflow error converting expression to data type int")


@pytest.mark.xfail(strict=True, reason=(
    "Known bug since WR-247 (#3248): a length below a bigint is now a DECIMAL that DuckDB fails to cast to "
    "INT64 with its own message ('Failed to cast decimal value ... to type INT64'), not T-SQL's overflow error"))
def test_a_length_far_below_a_bigint_is_t_sqls_bigint_overflow():
    stops_with("SELECT LEFT('abc', -10000000000000000000)",
               "Arithmetic overflow error converting expression to data type bigint")


@pytest.mark.xfail(strict=True, reason=(
    "Known bug: at main 293fb38da a negative literal reached the bigint range check as a float "
    "(-9223372036854775809 rounds to -2**63, inside the range), so the answer was LEFT's 'Invalid length' error; "
    "since WR-247 (#3248) it is DuckDB's 'Failed to cast decimal value' error. T-SQL stops converting the value "
    "to a bigint"))
def test_a_length_one_below_a_bigint_is_t_sqls_bigint_overflow():
    stops_with("SELECT LEFT('abc', -9223372036854775809)",
               "Arithmetic overflow error converting expression to data type bigint")


def test_char_of_a_column_keeps_0_and_255_and_nulls_256():
    assert run_on_t("SELECT CHAR(n) FROM dbo.t", {"n": "INTEGER"}, "(0), (255), (256)") == [
        ("\x00",), ("ÿ",), (None,)]


def test_a_datediff_one_past_an_int_is_t_sqls_overflow():
    assert run("SELECT DATEDIFF(second, '1970-01-01', '2038-01-19 03:14:07')") == [(2 ** 31 - 1,)]
    with pytest.raises(duckdb.Error, match="The datediff function resulted in an overflow"):
        run("SELECT DATEDIFF(second, '1970-01-01', '2038-01-19 03:14:08')")


@pytest.mark.parametrize("sql", ["UPDATE dbo.t SET a =", "UPDATE dbo.t SET a +="])
def test_an_updates_set_cut_off_after_its_operator_isnt_read_as_a_session_option(sql):
    with pytest.raises(ValueError, match="Could not read the query as T-SQL"):
        translate(sql)


def test_set_statistics_names_its_kind_when_the_batch_ends_there():
    with pytest.raises(ValueError, match="SET STATISTICS IO isn't mapped yet"):
        translate("SET STATISTICS IO")


def test_what_a_script_ends_inside_with_block_comments():
    from fabric_desktop.localdata import tsql
    assert tsql._left_open("/* a /* b */ c */ SELECT 1") is None
    assert tsql._left_open("/* a /* b */ c") == "a comment"


def test_the_integer_0_is_one_digit_in_a_product():
    columns = {"dbo": {"t": {"d": "DECIMAL(5,2)"}}}
    assert translate("SELECT 0 * d FROM dbo.t", columns)[0] == ["SELECT CAST(0 * d AS DECIMAL(7, 2)) FROM dbo.t"]


def test_an_unchecked_convert_style_lists_the_checked_ones():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)"}}}
    assert translate("SELECT CONVERT(DATETIME2, s, 3) FROM dbo.t", columns)[1] == [
        ("CONVERT style 3 runs as a translation that hasn't been checked against T-SQL's answers; styles 101, 103, "
         "112, 120, 121 and 126 have been. Compare its values in Fabric if they matter.")]


def test_text_between_an_unknown_and_text_has_the_padding_note():
    columns = {"dbo": {"t": {"s": "VARCHAR(20)"}}}
    notes = translate("SELECT 1 FROM dbo.t, dbo.unk AS u WHERE t.s BETWEEN u.z AND 'b'", columns)[1]
    assert len(notes) == 1 and notes[0].startswith("Couldn't tell the types in some comparisons")


def test_text_in_a_list_of_an_int_and_a_float_is_left_to_duckdb():
    columns = {"dbo": {"t": {"s": "VARCHAR(5)", "n": "INTEGER", "f": "FLOAT"}}}
    assert translate("SELECT 1 FROM dbo.t WHERE s IN (n, f)", columns)[0] == ["SELECT 1 FROM dbo.t WHERE s IN (n, f)"]


def test_a_wide_quotient_whose_dividend_is_past_the_room_is_worked_out_in_floating_point():
    columns = {"x": "DECIMAL(38,0)", "y": "DECIMAL(10,0)"}
    # 2 * 10**32 is past the 10**32 the exact path can move left by 6 places in a HUGEINT
    [(value,)] = run_on_t("SELECT x / y FROM dbo.t", columns, f"({2 * 10 ** 32}, 10)")
    assert abs(value - 2 * 10 ** 31) < 10 ** 17  # floating point: about the first 15 digits


def test_left_of_a_column_length_of_0_is_empty():
    assert run_on_t("SELECT LEFT('abc', n) FROM dbo.t", {"n": "INTEGER"}, "(0)") == [("",)]


def test_replicate_past_8000_bytes_names_varchar_max():
    with pytest.raises(duckdb.Error, match=r"its input isn't VARCHAR\(MAX\)"):
        run_on_t("SELECT REPLICATE(s, n) FROM dbo.t", {"s": "VARCHAR(10)", "n": "INTEGER"}, "('aaaaaaaaaa', 1000)")


def test_an_empty_comment_inside_a_comment_closes_on_its_own():
    from fabric_desktop.localdata import tsql
    assert tsql._left_open("/* /**/ */ SELECT 1") is None
    assert tsql._left_open("/**/ SELECT 1") is None
    assert tsql._left_open("/* x */'a") == "a string"  # the character right after */ is read


def test_create_schema_authorization_at_the_batchs_end_is_refused_by_name():
    with pytest.raises(ValueError, match=r"CREATE SCHEMA \.\.\. AUTHORIZATION isn't mapped yet"):
        translate("CREATE SCHEMA s AUTHORIZATION")


def test_a_batch_that_ends_in_for_and_holds_the_word_timestamp_is_read_without_an_index_error():
    with pytest.raises(ValueError, match="Could not read the query as T-SQL"):
        translate("SELECT 'TIMESTAMP' FOR")


@pytest.mark.parametrize("name, said", [("v", "dbo.v"), ("sales.v", "sales.v")])
def test_altering_a_view_that_isnt_there_names_it_with_its_schema(name, said):
    with pytest.raises(ValueError, match=f"Invalid object name '{said}': ALTER VIEW changes a view that's there"):
        translate(f"ALTER VIEW {name} AS SELECT 1 AS a", {"dbo": {"t": {"a": "INTEGER"}}})


def test_substring_and_stuff_with_a_column_length_of_0():
    columns = {"n": "INTEGER"}
    assert run_on_t("SELECT SUBSTRING('abc', 1, n), STUFF('abc', 2, n, 'x') FROM dbo.t", columns, "(0)") == [
        ("", "axbc")]


def test_char_of_0_and_256_written_in_the_query():
    assert translate("SELECT CHAR(0), CHAR(256)")[0] == ["SELECT CHR(0), CAST(NULL AS TEXT)"]


def test_a_datediff_one_below_an_int_is_t_sqls_overflow():
    assert run("SELECT DATEDIFF(second, '2038-01-19 03:14:08', '1970-01-01')") == [(-(2 ** 31),)]
    with pytest.raises(duckdb.Error, match="The datediff function resulted in an overflow"):
        run("SELECT DATEDIFF(second, '2038-01-19 03:14:09', '1970-01-01')")


def test_altering_a_view_in_dbo_by_its_bare_name_finds_it():
    assert translate("ALTER VIEW v AS SELECT 1 AS a", {"dbo": {"v": {"a": "INTEGER"}}})[0] == [
        "CREATE OR REPLACE VIEW v AS SELECT 1 AS a"]


def test_a_modulo_by_a_value_of_unknown_type_is_left_to_duckdb():
    columns = {"dbo": {"t": {"n": "INTEGER"}}}
    assert translate("SELECT n % u.z FROM dbo.t, dbo.unk AS u", columns)[0] == [
        "SELECT n % u.z FROM dbo.t, dbo.unk AS u"]


def test_a_decimal_avg_in_a_product_has_scale_6_and_precision_38():
    columns = {"dbo": {"t": {"d": "DECIMAL(10,2)"}}}
    assert translate("SELECT AVG(d) * 2 FROM dbo.t", columns)[0] == [
        "SELECT CAST(CAST(CAST(AVG(d) AS DECIMAL(38, 6)) AS DECIMAL(38, 6)) * 2 AS DECIMAL(38, 6)) FROM dbo.t"]


def test_isnumeric_is_refused_by_name():
    with pytest.raises(ValueError, match="ISNUMERIC isn't mapped yet"):
        translate("SELECT ISNUMERIC('1')")


@pytest.mark.parametrize("sql", ["SELECT QUOTENAME()", "SELECT QUOTENAME('a', '[', 1)"])
def test_quotename_with_no_or_three_arguments_is_left_as_written(sql):
    assert translate(sql)[0] == [sql]


def test_a_go_inside_a_string_says_to_move_it_out_of_the_string():
    from fabric_desktop.localdata.tsql import split_batches
    with pytest.raises(ValueError, match="the GO on line 2 is inside a string.*Move the GO out of the string,"):
        split_batches("SELECT 'a\nGO\n'")


def test_a_variable_named_after_a_security_word_is_refused_as_a_variable():
    with pytest.raises(ValueError, match=r"doesn't run T-SQL variables \(@grant\) yet"):
        translate("SELECT @grant")


def test_str_with_no_arguments_is_left_as_written():
    assert translate("SELECT STR()")[0] == ["SELECT STR()"]


def test_str_with_four_arguments_is_left_as_written():
    assert translate("SELECT STR(1, 2, 3, 4)")[0] == ["SELECT STR(1, 2, 3, 4)"]


def test_an_update_from_a_join_keeps_only_the_other_tables_in_from():
    columns = {"dbo": {"t": {"a": "INTEGER"}, "u": {"a": "INTEGER"}}}
    assert translate("UPDATE x SET a = 1 FROM dbo.t AS x JOIN dbo.u AS y ON x.a = y.a", columns)[0] == [
        "UPDATE dbo.t AS x SET a = 1 FROM dbo.u AS y WHERE (x.a = y.a)"]


def test_replicate_and_space_of_a_zero_count_column_are_empty_not_null():
    assert run_on_t("SELECT REPLICATE('a', n), SPACE(n) FROM dbo.t", {"n": "INTEGER"}, "(0)") == [("", "")]


def test_set_statistics_followed_by_a_query_names_the_statistics_kind():
    with pytest.raises(ValueError, match="SET STATISTICS TIME isn't mapped yet"):
        translate("SET STATISTICS TIME ON SELECT 1")


@pytest.mark.parametrize("mode", ["PATH", "AUTO"])
def test_for_json_names_its_mode(mode):
    with pytest.raises(ValueError, match=f"FOR JSON {mode} isn't mapped yet"):
        translate(f"SELECT 1 AS a FOR JSON {mode}")


def test_a_timestamp_hint_beside_another_hint_says_to_use_it_alone():
    with pytest.raises(ValueError, match=r"OPTION \(FOR TIMESTAMP AS OF \.\.\.\) takes a time written out in quotes"):
        translate("SELECT 1 OPTION (MAXDOP 1, FOR TIMESTAMP AS OF '2026-11-01')")


def test_char_of_0_and_255_are_characters():
    assert translate("SELECT CHAR(0), CHAR(255)")[0] == ["SELECT CHR(0), CHR(255)"]


def test_an_alter_table_adding_one_column_is_mapped():
    assert translate("ALTER TABLE dbo.t ADD b INT", COLUMNS)[0] == ["ALTER TABLE dbo.t ADD COLUMN b INT"]


def test_charindex_from_position_1_searches_from_the_start():
    columns = {"dbo": {"t": {"s": "VARCHAR(10)"}}}
    assert translate("SELECT CHARINDEX('a', s, 1) FROM dbo.t", columns)[0] == ["SELECT STRPOS(s, 'a') FROM dbo.t"]


def test_a_length_of_the_largest_bigint_is_cut_to_the_longest_text():
    assert translate("SELECT LEFT('abc', 9223372036854775807)")[0] == ["SELECT LEFT('abc', 2147483647)"]


def test_a_decimal_length_of_the_smallest_bigint_is_an_invalid_length_not_an_overflow():
    with pytest.raises(ValueError, match="Invalid length parameter passed to the LEFT function"):
        translate("SELECT LEFT('abc', -9223372036854775808.0)")


def test_replicate_of_a_literal_within_the_limit_needs_no_check():
    assert translate("SELECT REPLICATE('a', 0), REPLICATE('a', 8000)")[0] == [
        "SELECT REPEAT('a', 0), REPEAT('a', 8000)"]


def test_a_fractional_count_below_1_is_cut_to_0_when_translated():
    assert translate("SELECT SPACE(0.5), REPLICATE('a', 0.5)")[0] == [
        "SELECT REPEAT(' ', CAST(0 AS BIGINT)), REPEAT('a', 0)"]


def test_a_decimal_divided_with_a_shift_of_38_places_is_worked_out_in_hugeint():
    # DECIMAL(10,0) / DECIMAL(32,32) is DECIMAL(38,6): the dividend moves 6 - 0 + 32 = 38 places, which a HUGEINT holds
    columns = {"dbo": {"t": {"x": "DECIMAL(10,0)", "y": "DECIMAL(32,32)"}}}
    assert "INT128" in translate("SELECT x / y FROM dbo.t", columns)[0][0]


def test_an_order_by_position_of_a_text_column_before_another_key_is_trimmed():
    columns = {"dbo": {"t": {"s": "VARCHAR(10)", "a": "INTEGER"}}}
    assert "ORDER BY RTRIM(" in translate("SELECT UPPER(s), a FROM dbo.t ORDER BY 1, 2", columns)[0][0]
    assert translate("SELECT s, a FROM dbo.t ORDER BY s, a", columns)[0] == [
        "SELECT s, a FROM dbo.t ORDER BY RTRIM(s, ' ') NULLS FIRST, a NULLS FIRST"]


def test_an_order_by_position_past_the_columns_is_left_for_the_engine_to_refuse():
    columns = {"dbo": {"t": {"s": "VARCHAR(10)"}}}
    assert translate("SELECT s FROM dbo.t ORDER BY 2, 1", columns)[0] == [
        "SELECT s FROM dbo.t ORDER BY 2 NULLS FIRST, 1 NULLS FIRST"]


def test_isjson_names_its_bad_second_argument():
    with pytest.raises(ValueError, match="not foo\\."):
        translate("SELECT ISJSON('[1]', foo)")


def test_isjson_of_text_that_isnt_json_is_0():
    assert run("SELECT ISJSON('x')") == [(0,)]


def test_timefromparts_takes_fractions_of_0_from_a_column():
    assert run_on_t("SELECT TIMEFROMPARTS(1, 2, 3, a, 3) FROM dbo.t", {"a": "INTEGER"}, "(0)") == [(dt.time(1, 2, 3),)]


def test_the_first_of_a_year_that_starts_on_a_sunday_is_in_week_1():
    assert run("SELECT DATEPART(week, '2023-01-01'), DATEPART(week, '2023-01-08')") == [(1, 2)]


def test_adding_a_number_to_a_column_of_unknown_type_has_no_text_note():
    assert translate("SELECT b + 1 FROM dbo.t", COLUMNS)[1] == []
