"""Snowflake SQL on this PC (snowflake_sql.py): what the uncapped mutation run (batch 186,
docs/quality/mutation-testing.md) showed no test checked. Each expected value is what Snowflake gives,
or the note this PC gives where it can't."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import snowflake_sql as sf


def _run(code, setup=()):
    t = sf.translate(code)
    assert t.runnable, t.first_refusal()
    con = duckdb.connect()
    try:
        for s in setup:
            con.execute(s)
        return con.execute(t.duckdb_sql).fetchall()
    finally:
        con.close()


def _notes(code):
    return [n.id for s in sf.translate(code).statements for n in s.notes]


def test_as_decimal_of_a_variant_takes_its_precision_and_scale():
    assert _run("select as_decimal(to_variant(1.25), 10, 0), as_decimal(to_variant(1.25), 10, 1)") == [(Decimal("1"), Decimal("1.3"))]


def test_typeof_of_a_path_into_json_written_in_the_query_names_an_array_and_an_object():
    assert _run("select typeof(parse_json('{\"a\": [1]}'):a), typeof(parse_json('{\"a\": {\"b\": 1}}'):a), "
                "typeof(parse_json('{\"a\": 1.5}'):a)") == [("ARRAY", "OBJECT", "DECIMAL")]


def test_a_lag_over_another_window_inside_conditional_true_event_keeps_its_own_order():
    rows = _run("select a, conditional_true_event(x > lag(x) over (order by b)) over (order by a) "
                "from (select * from values (1, 5, 2), (2, 3, 1), (3, 4, 3) as t(a, x, b)) order by a")
    assert rows == [(1, 1), (2, 1), (3, 1)]


def test_a_snowflake_command_this_pc_doesn_t_rewrite_is_refused_by_name():
    t = sf.translate("EXECUTE TASK my_task")
    assert not t.runnable and _notes("EXECUTE TASK my_task") == ["SFN-099"]


def test_position_in_binary_counts_bytes():
    assert _run("select position(x'02', cast(x'0102' as binary), 1)") == [(2,)]


@pytest.mark.parametrize(("column", "ids"), [
    ("id INT AUTOINCREMENT", [(1,), (2,)]), ("id INT IDENTITY(10, 5)", [(10,), (15,)]),
    ("id INT AUTOINCREMENT START 10", [(10,), (11,)]), ("id INT AUTOINCREMENT INCREMENT 5", [(1,), (6,)])])
def test_an_autoincrement_column_numbers_its_rows_from_1_by_1_unless_told(column, ids):
    code = f"CREATE TABLE t ({column}, v INT); INSERT INTO t (v) VALUES (1), (2); SELECT id FROM t ORDER BY v"
    assert _run(code) == ids


@pytest.mark.parametrize(("start", "says"), [
    ("x", "whole numbers"), ("1.5", "whole numbers"),
    ("99999999999999999999", "fit in a BIGINT"), ("9223372036854775808", "fit in a BIGINT")])
def test_an_autoincrement_start_that_isn_t_a_whole_number_in_range_is_refused(start, says):
    t = sf.translate(f"CREATE TABLE t (id INT IDENTITY({start}, 1))")
    assert not t.runnable and says in t.first_refusal()
    assert sf.translate("CREATE TABLE t (id INT IDENTITY(9223372036854775807, 1))").runnable


@pytest.mark.parametrize("key", ["PRIMARY KEY (a)", "UNIQUE (a)"])
def test_a_table_key_isn_t_enforced_as_in_snowflake(key):
    code = f"CREATE TABLE t (a INT, b INT, {key}); INSERT INTO t VALUES (1, 1), (1, 2); SELECT count(*) FROM t"
    assert _run(code) == [(2,)]


@pytest.mark.parametrize("table", ["a INT PRIMARY KEY, b INT", "a INT, b INT, PRIMARY KEY (a)"])
def test_a_primary_key_column_is_not_null_as_in_snowflake(table):
    with pytest.raises(duckdb.ConstraintException, match="NOT NULL"):
        _run(f"CREATE TABLE t ({table}); INSERT INTO t VALUES (NULL, 1); SELECT count(*) FROM t")


def test_regexp_substr_with_e_takes_group_1_of_a_written_pattern_and_reads_one_from_the_data():
    assert _run("select regexp_substr('ab12', 'b([0-9])', 1, 1, 'e')") == [("1",)]
    assert _run("select regexp_substr(s, p, 1, 1, 'e') from (select 'ab12' as s, '[0-9]+' as p)") == [("12",)]


def test_to_timestamp_ntz_of_a_number_in_brackets_or_negated():
    assert _run("select to_timestamp_ntz((1700000000)), to_timestamp_ntz(-(1700000000))")[0][0].year == 2023
    assert _run("select to_timestamp_ntz(-(1700000000))")[0][0].year == 1916


@pytest.mark.xfail(strict=True, reason=(
    "Bug: TO_TIMESTAMP_NTZ of a number from the data stops here with DuckDB's "
    "'Scalar Function with name to_timestamp_ntz does not exist'; Snowflake reads the number as seconds since "
    "1970, as it does for a number written in the query. The rewrite to TO_TIMESTAMP(...) AT TIME ZONE 'UTC' "
    "is made only for a number written in the query (_written_number)."))
def test_to_timestamp_ntz_of_a_number_column_reads_seconds_since_1970():
    import datetime
    assert _run("select to_timestamp_ntz(x) from (select 1700000000 as x)") == [
        (datetime.datetime(2023, 11, 14, 22, 13, 20),)]


def test_lpad_and_rpad_with_an_empty_pad_cut_or_keep_the_text():
    assert _run("select lpad('abc', 5, ''), rpad('abc', 2, ''), lpad('abc', -1, '')") == [("abc", "ab", "")]


def test_object_agg_with_one_argument_is_refused_by_name():
    assert _notes("select object_agg(k) from (select 'a' as k)") == ["SFX-016"]
    assert _run("select object_agg(k, v) from (select 'a' as k, 1 as v)") == [('{"a":1}',)]


def test_an_event_function_outside_a_select_is_refused_with_its_page():
    code = "create table t (a int, x int); update t set x = conditional_change_event(x) over (order by a)"
    t = sf.translate(code)
    assert not t.runnable and "SFX-018" in _notes(code)
    pages = [sf.translate(c).statements[0].notes[0].source.rsplit("/", 1)[1] for c in (
        "SELECT INTERPOLATE_FFILL(x, y) OVER (ORDER BY o) FROM t",
        "SELECT CONDITIONAL_CHANGE_EVENT(x) OVER (ORDER BY o) FROM t JOIN u ON t.id = u.id")]
    assert pages == ["interpolate_bfill", "conditional_change_event"]


def test_a_division_in_iff_still_stops_on_a_zero_divisor_as_in_snowflake():
    with pytest.raises(duckdb.Error, match="Division by zero"):
        _run("select iff(x = 1, 0, 1 / x) from (select 0 as x)")


@pytest.mark.parametrize(("value", "may_be_date"), [
    ("x", True), ("foo(x)", True), ("cast(x as varchar)", True),
    ("cast(x as timestamp)", False), ("to_timestamp_ntz(x)", False), ("try_to_timestamp(x)", False)])
def test_date_arithmetic_says_a_date_may_come_back_as_a_timestamp_unless_the_value_is_plainly_one(value, may_be_date):
    assert ("SFD-006" in _notes(f"select dateadd(day, 1, {value}) from t")) is may_be_date


@pytest.mark.parametrize(("code", "rule"), [
    ("CREATE DYNAMIC TABLE d TARGET_LAG = '1 minute' WAREHOUSE = w AS SELECT 1", "SFN-003"),
    ("CREATE TABLE d TARGET_LAG = '1 minute' AS SELECT 1", "SFN-003"),
    ("CREATE STAGE s", "SFN-011"), ("CREATE SEQUENCE s", "SFN-011"),
    ("ALTER TABLE a SWAP WITH b", "SFN-029"),
    ("select snowflake.cortex.complete('m', 'x')", "SFN-012"),
    ("select array_construct(1, cast('a' as varchar))", "SFN-025"),
])
def test_what_runs_only_in_snowflake_is_refused_by_its_rule(code, rule):
    t = sf.translate(code)
    assert not t.runnable and _notes(code) == [rule]


def test_a_function_in_another_database_isn_t_taken_for_cortex():
    assert _notes("select db.sch.myfn(1)") == []


def test_a_note_can_t_be_changed():
    import dataclasses
    note = sf.translate("CREATE STAGE s").statements[0].notes[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.id = "x"


def test_a_date_format_letter_duckdb_has_no_code_for_is_refused():
    assert _notes("select to_char(cast('2026-05-01' as date), 'YYYY Q')") == ["SFX-002"]
    assert _run("select to_char(cast('2026-05-01' as date), 'YYYY-MM-DD')") == [("2026-05-01",)]


def test_editdistance_with_a_maximum_from_the_data_or_negative():
    assert _run("select editdistance('kitten', 'sitting'), editdistance('kitten', 'sitting', 2), "
                "editdistance('kitten', 'sitting', -1)") == [(3, 2, 0)]
    assert _run("select editdistance('kitten', 'sitting', -n) from (select -5 as n)") == [(3,)]
    assert _run("select editdistance('kitten', 'sitting', -n) from (select -2 as n)") == [(2,)]
    assert _run("select editdistance('kitten', 'sitting', n) from (select -2 as n)") == [(0,)]


def test_to_date_of_text_holding_seconds_or_milliseconds():
    assert _run("select to_date('1700000000'), to_date('1700000000000')")[0] == (
        __import__("datetime").date(2023, 11, 14),) * 2


def test_sample_methods_map_to_duckdb_ones():
    assert "BERNOULLI" in sf.translate("select 1 from t sample bernoulli (10)").duckdb_sql
    assert "BERNOULLI" in sf.translate("select 1 from t sample row (10)").duckdb_sql
    assert "SYSTEM" in sf.translate("select 1 from t sample system (10)").duckdb_sql


def test_a_cast_of_slashed_text_to_varchar_stays_text():
    assert _run("select cast('03/05/2026' as varchar)") == [("03/05/2026",)]


def test_a_table_key_naming_no_column_is_dropped_like_any_other_key():
    t = sf.translate("create table t (a int, primary key (zz))")
    assert t.runnable
    duckdb.connect().execute(t.duckdb_sql)


def test_lpad_and_rpad_with_no_pad_or_a_written_one_are_left_to_duckdb():
    assert _run("select lpad('abc', 5), rpad('abc', 5, 'xy')") == [("  abc", "abcxy")]


@pytest.mark.xfail(strict=True, reason=(
    "Bug: as above, TO_TIMESTAMP_NTZ of a sum of a written number and a number from the data stops here with "
    "DuckDB's 'Scalar Function with name to_timestamp_ntz does not exist'; Snowflake gives a timestamp."))
def test_to_timestamp_ntz_of_a_written_number_plus_a_column_reads_seconds_since_1970():
    import datetime
    assert _run("select to_timestamp_ntz(1700000000 + x) from (select 0 as x)") == [
        (datetime.datetime(2023, 11, 14, 22, 13, 20),)]


def test_typeof_of_a_json_number_too_long_for_a_decimal_is_double():
    # 40 whole digits, and 38 after the point: neither fits Snowflake's NUMBER(38, s), so each is a DOUBLE
    assert _run("select typeof(parse_json('{\"a\": " + "1234567890" * 4 + "}'):a), "
                "typeof(parse_json('{\"a\": 0." + "1234567890" * 3 + "12345678}'):a), "
                "typeof(parse_json('{\"a\": 12}'):a)") == [("DOUBLE", "DOUBLE", "INTEGER")]


def test_a_division_in_iffs_true_branch_under_a_zero_test_still_stops():
    with pytest.raises(duckdb.Error, match="Division by zero"):
        _run("select iff(b = 0, 1 / b, 0) from (select 0 as b)")


def test_to_date_of_epoch_text_either_side_of_midnight_in_each_unit():
    import datetime
    for digits, day in (("1700006400", 15), ("1700006399", 14)):
        # seconds, then milliseconds, microseconds and nanoseconds: the last digit kept or nines added
        values = [digits, digits + ("000" if day == 15 else "999"), digits + ("000000" if day == 15 else "999999"),
                  digits + ("000000000" if day == 15 else "999999999")]
        sql = "select " + ", ".join(f"to_date('{v}')" for v in values)
        assert _run(sql) == [(datetime.date(2023, 11, day),) * 4]


def test_a_statement_sqlglot_cannot_write_is_refused_with_its_message():
    t = sf.translate("select time()")
    assert not t.runnable
    assert t.statements[0].verdict == "not_translatable"
    assert [n.id for n in t.statements[0].notes] == ["SFX-000"]


def _titles(code):
    return [(n.id, n.title) for s in sf.translate(code).statements for n in s.notes]


def test_a_mixed_array_refusal_names_the_function_written():
    assert _titles("select array_construct(1, 'a')") == [("SFN-025", "ARRAY_CONSTRUCT: text and numbers in one array")]
    assert _titles("select [1, 'a']") == [("SFN-025", "ARRAY_CONSTRUCT: text and numbers in one array")]
    assert _titles("select array_append([1], 'a')") == [("SFN-025", "ARRAY_APPEND: text and numbers in one array")]


def test_to_char_with_a_number_as_its_format_is_a_computed_format():
    assert _titles("select to_char(1.5, 5)") == [("SFX-002", "TO_VARCHAR with a computed format")]


def test_time_slice_with_a_slice_length_from_the_data_is_left_to_sqlglot():
    # only a length written in the query is rewritten here; one from the data still translates and runs
    rows = _run("select time_slice(d, n, 'DAY') from (select cast('2024-01-05' as date) as d, 2 as n)")
    assert len(rows) == 1


def test_regexp_substr_e_with_a_pattern_python_cannot_read_keeps_the_whole_match():
    assert _run(r"select regexp_substr('ab1', '\\p{L}+', 1, 1, 'e')") == [("ab",)]


def test_regexp_substr_m_with_a_pattern_from_the_data_is_multi_line():
    assert _run("select regexp_substr(s, p, 1, 1, 'm') from (select 'x' || chr(10) || 'y' as s, '^y' as p)") == [
        ("y",)]


def test_a_merge_stops_on_a_duplicate_source_row_before_a_conditional_update():
    with pytest.raises(duckdb.Error, match="more than one source row"):
        _run("merge into t using s on t.id = s.id when matched and s.v > 0 then update set v = s.v",
             setup=("create table t (id int, v int)", "create table s (id int, v int)",
                    "insert into t values (1, 0)", "insert into s values (1, 5), (1, 6)"))


def test_pivot_columns_of_numbers_are_named_without_quotes():
    con = duckdb.connect()
    t = sf.translate("select * from (select 1 as k, 10 as v union all select 2, 20) pivot (sum(v) for k in (1, 2))")
    cur = con.execute(t.duckdb_sql)
    assert [d[0] for d in cur.description] == ["1", "2"]
    assert cur.fetchall() == [(10, 20)]


def test_ceil_of_a_32_digit_number_to_7_places_runs():
    # at 7 places TRUNC's step would need 39 digits, so this scale is left to the FLOAT rewrite
    assert len(_run("select ceil(x, 7) from (select 12345678901234567890123456789012::number(38, 0) as x)")) == 1


def test_typeof_of_json_numbers_past_38_digits_in_all_is_double():
    # 20 digits each side of the point, and 39 whole digits: past NUMBER(38, s)
    assert _run("select typeof(parse_json('{\"a\": " + "1234567890" * 2 + "." + "1234567890" * 2 + "}'):a), "
                "typeof(parse_json('{\"a\": " + "1234567890" * 3 + "123456789}'):a)") == [("DOUBLE", "DOUBLE")]


def test_typeof_of_a_json_element_past_the_end_or_of_an_object_is_null():
    assert _run("select typeof(parse_json('{\"a\": [1]}'):a[5]), "
                "typeof(parse_json('{\"a\": {\"b\": 1}}'):a[0])") == [(None, None)]


def test_hll_of_a_one_argument_call_counts_the_calls_values():
    assert _run("select hll(is_integer(to_variant(x))) from (select 1 as x union all select 2 union all select 3)"
                ) == [(1,)]


def test_datasketches_hll_with_three_arguments_and_as_decimal_with_precision_0_are_refused():
    assert _notes("select datasketches_hll(x, 12, 3) from (select 1 as x)") == ["SFX-014"]
    assert _notes("select as_decimal(to_variant(1.5), 0)") == ["SFX-011"]


def test_a_refusal_sqlglot_gives_carries_its_own_message():
    note = sf.translate("select time()").statements[0].notes[0]
    assert "sqlglot can't rewrite it" not in note.reason


def test_an_unknown_command_is_named_by_its_first_word():
    note = sf.translate("execute task t").statements[0].notes[0]
    assert note.id == "SFN-099"
    assert note.reason.startswith("EXECUTE is a Snowflake command")


def test_ddl_after_a_transaction_ends_runs():
    for end in ("commit", "rollback"):
        assert _run(f"begin transaction; {end}; create table t (a int); select count(*) from t") == [(0,)]


def test_identifier_of_a_session_variable_keeps_the_tables_alias():
    assert _run("set t = 'tbl'; select x.a from identifier($t) as x",
                setup=("create table tbl (a int)", "insert into tbl values (7)")) == [(7,)]


def test_alter_table_dropping_one_column_runs():
    assert _run("alter table t drop column a; select * from t",
                setup=("create table t (a int, b int)", "insert into t values (1, 2)")) == [(2,)]


def test_two_to_char_calls_of_a_column_keep_their_own_formats():
    assert _run("select to_char(d, 'YYYY'), to_char(d, 'HH24') from "
                "(select cast('2026-05-01 13:00' as timestamp) as d)") == [("2026", "13")]


def test_seq_of_another_table_beside_flatten_is_that_tables_column():
    assert _run("select t.seq from (select 5 as seq) t, lateral flatten(input => parse_json('[1]')) f") == [(5,)]


def test_try_to_date_of_epoch_text_past_duckdbs_dates_is_null():
    assert _run("select try_to_date('99999999999999999999999')") == [(None,)]


def test_an_identity_step_of_0_is_refused():
    assert _notes("create table t (a int identity(1, 0))") == ["SFX-000"]


def test_as_decimal_with_only_a_precision_has_scale_0():
    assert _run("select as_decimal(to_variant(1.26), 5)") == [(Decimal("1"),)]


def test_to_date_of_epoch_text_at_each_units_first_value():
    # 31536000000 is past the seconds Snowflake reads as seconds, so milliseconds; and so on up
    import datetime
    assert _run("select to_date('31536000000'), to_date('31536000000000'), to_date('31536000000000000')") == [
        (datetime.date(1971, 1, 1),) * 3]


def test_an_identity_start_below_bigints_range_is_refused():
    assert _notes("create table t (a int identity(-9223372036854775809, 1))") == ["SFX-000"]
    assert _notes("create table t (a int identity(-9223372036854775808, 1))") == ["SFD-001"]


def test_a_multi_table_insert_refusal_names_all_or_first():
    assert _titles("insert all into t1 values (1) into t2 values (2) select 1") == [("SFN-026", "INSERT ALL")]
    assert _titles("insert first when 1 > 0 then into t1 values (1) select 1") == [("SFN-026", "INSERT FIRST")]


def test_a_key_column_already_not_null_gets_no_second_not_null():
    sql = sf.translate("create table t (a int not null primary key, b int, primary key (b))").duckdb_sql
    assert sql.upper().count("NOT NULL") == 2


def test_ceil_and_floor_at_6_and_minus_18_places_stay_numbers():
    rows = _run("select ceil(1.2345671, 6), ceil(5, -18), floor(-1.2345671, 6)")
    assert rows == [(Decimal("1.234568"), Decimal("1000000000000000000"), Decimal("-1.234568"))]
    assert all(isinstance(v, Decimal) for v in rows[0])  # 1e18 as a DOUBLE would compare equal


def test_as_decimal_at_precision_1_and_scale_equal_to_precision():
    assert _run("select as_decimal(to_variant(7), 1), as_decimal(to_variant(0.5), 2, 2)") == [
        (Decimal("7"), Decimal("0.50"))]


def test_typeof_of_json_numbers_at_38_digits_and_37_places_is_still_a_number():
    assert _run("select typeof(parse_json('{\"a\": " + "1234567890" * 3 + "12345678}'):a), "
                "typeof(parse_json('{\"a\": 0." + "1234567890" * 3 + "1234567}'):a)") == [("INTEGER", "DECIMAL")]


def test_typeof_of_a_json_element_just_past_the_end_is_null():
    assert _run("select typeof(parse_json('{\"a\": [1]}'):a[1])") == [(None,)]


def test_a_function_this_pc_doesnt_know_keeps_its_digit_text_argument():
    # only TO_DATE, TRY_TO_DATE and DATE read digits as seconds since 1970
    assert _run("select myfn('123')", setup=("create macro myfn(x) as length(x)",)) == [(3,)]


def test_a_merge_from_a_subquery_with_no_name_runs():
    assert _run("merge into tgt using (select 5 as w) on tgt.id = 1 when matched then update set v = w",
                setup=("create table tgt (id int, v int)", "insert into tgt values (1, 0)")) == [(1,)]
