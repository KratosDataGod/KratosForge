"""Spark's regular expressions and string fixes on this PC (spark_strings.py): Java escapes the main tests
leave out (\\R in a class, \\u, \\e, \\c), a flag-only group, a brace that isn't a quantifier, a script
property, a group index past 9, chr with two arguments and an int result already cast to INT."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from fabric_desktop import spark_strings as ss
from fabric_desktop import sql


@pytest.mark.parametrize(("pattern", "re2"), [
    ("\\e", "\\x{1B}"),
    ("\\cA", "\\x{1}"),
    ("\\u00e9", "\\x{00e9}"),
    ("(?u:a)", "(?:a)"),
    ("(?u)a", "a"),
    ("a\\{x", "a\\{x"),  # a bare { that is no repeat stops, as in Spark (test_spark_regex_dialect.py)
    ("\\p{IsLatin}", "\\p{Latin}"),
])
def test_java_escapes_and_groups_as_re2_reads_them(pattern, re2):
    assert ss.java_pattern(pattern, "rlike") == (re2, 0, {})


@pytest.mark.parametrize(("pattern", "says"), [
    ("[\\R]", "'\\R' inside [ ]"),
    ("\\u12", "'\\u' without four hex digits"),
    ("\\c", "'\\c' without a letter"),
])
def test_java_escapes_re2_cant_take_are_refused(pattern, says):
    with pytest.raises(ValueError, match="rlike can't use") as e:
        ss.java_pattern(pattern, "rlike")
    assert says in str(e.value)


def test_the_escapes_match_what_java_matches():
    assert sql.run("SELECT 'x' || chr(27) || 'y' RLIKE 'x\\\\ey' AS a, 'é' RLIKE '\\\\u00e9' AS b").to_pylist() == [
        {"a": True, "b": True}]


def test_a_group_index_past_9_is_refused_even_when_the_pattern_has_it():
    groups = "".join(f"({c})" for c in "abcdefghijkl")
    with pytest.raises(ValueError, match="asks for group 12. On this PC a group index can be 0 to 9"):
        sql.parse(f"SELECT regexp_extract('abcdefghijkl', '{groups}', 12)")


def test_chr_with_two_arguments_is_left_as_written():
    node = sqlglot.parse_one("SELECT CHR(65, 1)", read="spark").expressions[0]
    assert ss._chr(node) is None


def test_an_int_result_already_cast_to_int_isnt_wrapped_again():
    out = ss.spark_text_results(sqlglot.parse_one("SELECT CAST(LENGTH(s) AS INT), LENGTH(s) FROM t", read="spark"))
    first, second = out.expressions
    assert isinstance(first, exp.Cast) and isinstance(first.this, exp.Length)
    assert isinstance(second, exp.Cast) and second.to.is_type(exp.DataType.Type.INT)
