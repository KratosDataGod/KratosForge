"""Spark SQL's regular expressions where Java and RE2 still answered differently on this PC (WR-257).

Each expected value is what Apache Spark 3.5.3 gave for the same expression (local mode, UTC, ANSI off, as
Fabric runs Spark SQL), run on 2026-11-20 for this change; none was run on a Fabric cluster. Before the fix each
query here gave another answer on this PC with no error, or answered where Spark stops. What can't be matched
is refused by name, and nothing runs.
"""

from __future__ import annotations

import pytest

from fabric_desktop import sql
from fabric_desktop.duckdb_rows import duckdb_major


def one(expression: str):
    return next(iter(sql.run("SELECT " + expression).to_pylist()[0].values()))


@pytest.mark.parametrize(("expression", "spark"), [
    # A group that took no part in a match is '' in Spark's array, as in regexp_extract (it was NULL here)
    ("regexp_extract_all('abab', '(a)|(b)', 2)", ["", "b", "", "b"]),
    ("regexp_extract_all('a1b22c333', '(\\\\d)(\\\\d)?', 2)", ["", "2", "3", ""]),
    ("regexp_extract_all(s, '(a)|(b)', 2) FROM (SELECT 'abab' AS s)", ["", "b", "", "b"]),
    ("regexp_extract_all('abab', '(a)|(b)', 0)", ["a", "b", "a", "b"]),
    ("regexp_extract_all(CAST(NULL AS STRING), '(a)', 1)", None),
    # regexp_substr is NullIf(regexp_extract(str, regexp, 0), ''): an empty match is NULL (it was '')
    ("regexp_substr(s, p) FROM (SELECT 'xab' AS s, 'a*' AS p)", None),
    ("regexp_substr('abc', 'b')", "b"),
    ("regexp_substr(NULL, 'a')", None),
    # regexp_instr: where the first match starts, an empty one too (it was skipped); idx is a group that Spark only
    # checks (it was read as a start position); counted in UTF-16 units, as a Java String is
    ("regexp_instr('bcx', '(b)(c)', 2)", 1),
    ("regexp_instr('bcbc', '(b)(c)', 2)", 1),
    ("regexp_instr('abc', '')", 1),
    ("regexp_instr('😀a', 'a')", 3),
    ("regexp_instr('é😀a😀b', 'b')", 7),
    ("regexp_instr('aBc', '(?i)b')", 2),
    ("regexp_instr('a' || chr(10) || 'b', '(?m)^b')", 3),
    ("regexp_instr('ab', 'x')", 0),
    ("regexp_instr('abc', 'c', 0)", 3),
    ("regexp_instr(NULL, 'a')", None),
    ("regexp_instr('abc', CAST(NULL AS STRING))", None),
    ("regexp_instr(s, p) FROM (SELECT 'xyz' AS s, 'y' AS p)", 2),
    ("regexp_instr(s, p) FROM (SELECT 'xyz' AS s, '' AS p)", 1),
    ("typeof(regexp_instr('abc', 'c'))", "int"),
    ("regexp_instr('abcabc', 'c') + 1", 4),
    # Java's 😀 is one character; \é is é; \x{61} keeps its braces (a + after it is a repeat)
    ("rlike('😀', '^\\\\uD83D\\\\uDE00$')", True),
    ("rlike('x😀', 'x[\\\\uD83D\\\\uDE00]')", True),
    ("rlike('aé', 'a\\\\é')", True),
    ("rlike('aa', '\\\\x{61}+')", True),
    ("rlike('aa', '^\\\\x{61}{2}$')", True),
    ("rlike('{\"a\":1}', '\\\\{\"a\"')", True),
    ("rlike('a}', 'a}')", True),
    # empty matches that Java and RE2 take alike still run
    ("regexp_replace('ab', '^', '>')", ">ab"),
    ("regexp_replace('ab cd', '\\\\b', '|')", "|ab| |cd|"),
    ("regexp_replace('ab', '$', '!')", "ab!"),
    ("regexp_replace('a' || chr(10) || 'b', '(?m)^', '>')", ">a\n>b"),
    ("regexp_replace('abc', '', '-')", "-a-b-c-"),
    ("regexp_replace('aaa', 'a*', '')", ""),
    ("regexp_replace('a b', '\\\\s*', '')", "ab"),
    ("regexp_replace('  ab', '^\\\\s*', 'X')", "Xab"),
    ("regexp_count('aaa', 'a*')", 2),
    ("regexp_count('ab', '^a*')", 1),
    ("regexp_extract_all('aaa', 'a*', 0)", ["aaa", ""]),
])
def test_answers_as_spark(expression: str, spark) -> None:
    assert one(expression) == spark, expression


@pytest.mark.parametrize("expression", [
    "regexp_replace('aaa', 'a*', 'X')",          # Spark: 'XX'
    "regexp_replace('a,b', '[^,]*', 'X')",       # Spark: 'XX,XX'
    "regexp_replace('ab  ', '\\\\s*$', 'X')",    # Spark: 'abXX'
    "regexp_replace('abab', 'b|$', 'X')",        # Spark: 'aXaXX'
    "regexp_replace(s, 'a?', r) FROM (SELECT 'aaa' AS s, 'X' AS r)",  # Spark: 'XXXX'
])
def test_an_empty_match_after_another_match_is_refused_in_regexp_replace(expression: str) -> None:
    """Java replaces the empty match where the match before it ended; RE2 skips it, so these gave one X fewer."""
    with pytest.raises(ValueError, match="regexp_replace can't run on this PC with a pattern that can match empty "
                                         "text right after another match .*nothing ran"):
        one(expression)


@pytest.mark.parametrize(("expression", "spark"), [
    ("regexp_count('ab', '\\\\b')", 2), ("regexp_count('ab cd', '\\\\b')", 4), ("regexp_count('ab', '$')", 1),
    ("regexp_count('a' || chr(10) || 'b', '(?m)^')", 2), ("regexp_count('abab', 'b|$')", 3),
    ("regexp_extract_all('ab', '$|a', 0)", ["a", ""]), ("regexp_extract_all('ab', '\\\\b', 0)", ["", ""]),
])
def test_empty_matches_at_an_anchor_count_once(expression: str, spark) -> None:
    """DuckDB 1.x counted some of these twice (regexp_count('ab', '\\b') was 3), so there they are refused;
    DuckDB 2.0 counts them as Java does."""
    if duckdb_major() >= 2:
        assert one(expression) == spark, expression
    else:
        with pytest.raises(ValueError, match="at an anchor or word boundary .*nothing ran"):
            one(expression)


@pytest.mark.parametrize(("expression", "message"), [
    ("rlike('a{', 'a{')", "a { that isn't a repeat"),             # Spark: INVALID_PARAMETER_VALUE.PATTERN
    ("rlike('a{,3}', 'a{,3}')", "a { that isn't a repeat"),
    ("rlike('{', '{')", "a { that isn't a repeat"),
    ("rlike('a', '\\\\uD83D')", "half of a UTF-16 pair"),         # Spark: false; RE2 has no lone half
    ("regexp_instr('abc', '(b)', 2)", "asks for group 2, and the pattern has 1 group"),
])
def test_refused_by_name(expression: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        one(expression)


def test_the_dataframe_api_refuses_an_empty_match_replace() -> None:
    from fabric_desktop.spark_strings import java_regex_function
    calls = []
    replace = java_regex_function("regexp_replace", lambda *a: calls.append(a), 1, replacement_at=2)
    replace("s", "a*", "")
    with pytest.raises(ValueError, match="right after another match"):
        replace("s", "a*", "X")
    assert calls == [("s", "a*", "")]


def test_regexp_instr_reads_its_arguments_once() -> None:
    """Nested regexp_instr grows by a constant per level, not by a factor."""
    def size(depth: int) -> int:
        text = "s"
        for _ in range(depth):
            text = f"CAST(regexp_instr({text}, 'a') AS STRING)"
        return len(sql.duckdb_sql(sql.parse(f"SELECT {text} FROM (SELECT 'a' AS s)")[0]))
    assert size(4) - size(3) <= size(2) - size(1) + 200
