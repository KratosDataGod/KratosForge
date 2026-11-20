"""Spark's regular expressions and string results on this PC (spark_strings.py): what the uncapped mutation
run (batch 188, docs/quality/mutation-testing.md) showed no test pinned."""

from __future__ import annotations

import pytest

from fabric_desktop import spark_strings as s


def pattern(text: str) -> str:
    return s.java_pattern(text, "rlike")[0]


def refused(text: str) -> str:
    with pytest.raises(ValueError, match="can't use") as caught:
        s.java_pattern(text, "rlike")
    return str(caught.value)


def test_a_plus_at_the_start_isnt_read_as_a_possessive_quantifier():
    # Nothing comes before it to quantify, so it is left for the engine to refuse as Java does
    assert s.java_pattern("+a", "rlike") == ("+a", 0, {})


def run(query: str):
    from fabric_desktop import sql
    return next(iter(sql.run(query).to_pylist()[0].values()))


def written(query: str) -> str:
    from fabric_desktop import sql
    return sql.duckdb_sql(sql.parse(query)[0])


def test_an_index_from_the_data_is_read_when_the_query_runs():
    assert run("SELECT split_part('a,b', ',', x) AS v FROM VALUES (2) t(x)") == "b"
    assert run("SELECT left('abc', x) AS v FROM VALUES (2) t(x)") == "ab"


def test_a_pattern_that_isnt_text_is_left_for_the_engine():
    assert "REGEXP_MATCHES('12', 1)" in written("SELECT '12' rlike 1")


def _duckdb_1():
    import duckdb
    return duckdb.__version__.startswith("1.")


@pytest.mark.xfail(_duckdb_1(), strict=True, reason=(
    "Bug: on DuckDB 1.5, a concatenation with a NULL inside another one, ('a' || NULL) || 'b', comes back typed "
    "INTEGER, not string as in Spark: only a concatenation whose own argument is the NULL is cast to text, and "
    "not when it sits inside another concatenation (spark_text_results)."))
def test_a_concatenation_with_a_null_inside_another_is_still_a_string():
    from fabric_desktop import sql
    for query in ("SELECT ('a' || NULL) || 'b' AS v", "SELECT concat(concat('a', NULL), 'b') AS v"):
        table = sql.run(query)
        assert table.to_pylist() == [{"v": None}] and str(table.schema.field("v").type) == "string", query


def test_concat_ws_without_an_array_keeps_spark_s_text_for_each_value():
    assert run("SELECT concat_ws(',', CAST(1e20 AS DOUBLE), 'a')") == "1.0E20,a"


def test_a_group_past_9_is_refused_on_this_pc_even_when_the_pattern_has_it():
    ten = "(a)" * 10
    with pytest.raises(ValueError, match="asks for group 10. On this PC a group index can be 0 to 9"):
        written(f"SELECT regexp_extract('{'a' * 10}', '{ten}', 10)")
    with pytest.raises(ValueError, match="asks for group 12. On this PC a group index can be 0 to 9"):
        written("SELECT regexp_extract(s, p, 12) FROM VALUES ('a', 'a') t(s, p)")


# ---- Java's pattern syntax as RE2 reads it


def test_a_quantifier_after_a_class_isnt_possessive():
    assert pattern("[ab]+") == "[ab]+"


def test_a_class_ends_at_its_bracket_and_another_can_follow():
    assert pattern("[a][b]") == "[a][b]"


def test_a_bracket_first_in_a_negated_class_is_a_character():
    assert pattern(r"[^]\s]") == r"[^][:space:]]"


def test_a_backslash_at_the_very_end_is_left_as_it_is():
    assert pattern("a\\") == "a\\"


def test_a_property_letter_missing_at_the_end_is_an_empty_name():
    assert pattern("\\p") == "\\p{}"


def test_is_before_a_category_is_dropped_and_is_alone_is_kept():
    assert pattern(r"\p{IsL}") == r"\p{L}"
    assert pattern(r"\p{Is}") == r"\p{Is}"


def test_a_word_boundary_letter_with_a_brace_is_refused_but_other_escapes_take_a_quantifier():
    assert pattern(r"\d{2}") == r"\d{2}"
    assert "'\\b'" in refused(r"\b{g}")


def test_a_line_break_class_inside_brackets_is_refused():
    assert "'\\R' inside [ ]" in refused(r"[\R]")


def test_a_unicode_escape_takes_exactly_four_hex_digits():
    assert pattern("\\" + "u0041b") == r"\x{0041}b"  # Java's \u escape, written out
    assert "'\\u' without four hex digits after it" in refused(r"\u12")


def test_a_control_letter_is_read_once():
    assert pattern(r"\cAb") == r"\x{1}b"


def test_a_comment_group_is_left_for_the_engine():
    assert pattern("(?#x)a") == "(?#x)a"


def test_a_refused_flag_names_the_whole_group():
    assert "the flag 'x' in '(?x)'" in refused("(?x)a")


# ---- regexp_replace's replacement


def test_a_replacement_reads_two_digit_groups_while_the_pattern_has_them():
    assert s.java_replacement("$12", "regexp_replace", 11, {}) == "\\1" + "2"
    assert s.java_replacement("$9", "regexp_replace", 9, {}) == "\\9"
    with pytest.raises(ValueError, match="refers to group 11. On this PC"):
        s.java_replacement("$11", "regexp_replace", 11, {})
    with pytest.raises(ValueError, match="refers to group 10. On this PC a replacement can use groups 0 to 9"):
        s.java_replacement("$10", "regexp_replace", 10, {})


def test_a_missing_group_says_which_groups_there_are():
    with pytest.raises(ValueError, match=r"Use 0 for the whole match, or write \\\$"):
        s.java_replacement("$1", "regexp_replace", 0, {})
    with pytest.raises(ValueError, match="Use a group from 0 to 1, or write"):
        s.java_replacement("$2", "regexp_replace", 1, {})


def test_position_1_is_the_first_character():
    assert "REGEXP_REPLACE" in written("SELECT regexp_replace('aaa', 'a', 'b', 1)")


# ---- regexp_extract's group


def test_regexp_extract_takes_group_1_by_default_and_up_to_9():
    with pytest.raises(ValueError, match="asks for group 1, and the pattern has 0 groups"):
        written("SELECT regexp_extract('abc', 'b')")
    written(f"SELECT regexp_extract('{'a' * 9}', '{'(a)' * 9}', 9)")
    assert run("SELECT regexp_extract('a1', '([a-z])([0-9])')") == "a"


# ---- the DataFrame wrapper


def test_the_wrapper_passes_a_call_that_stops_before_its_replacement_or_group():
    def fn(*args):
        return args

    replace = s.java_regex_function("regexp_replace", fn, 1, replacement_at=2)
    assert replace("x", "a") == ("x", "a")
    extract = s.java_regex_function("regexp_extract", fn, 1, group_at=2)
    assert extract("x", "a") == ("x", "a")


def test_a_plus_after_a_lazy_quantifier_isnt_called_possessive():
    # Java has no possessive form of a lazy quantifier: it stops on a*?+ as a dangling +, and RE2 stops on it
    # too, so it is left for the engine rather than refused here as a possessive quantifier.
    assert pattern("a*?+") == "a*?+"
