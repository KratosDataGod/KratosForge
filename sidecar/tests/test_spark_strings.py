"""Spark SQL's regular expressions and string functions on this PC, value-checked against Spark 3.5 (WR-107).

Each expected value is Spark's, ANSI off as Fabric runs it: java.util.regex's syntax
(https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/regex/Pattern.html), Matcher's
replacement syntax, and the functions' documented results (sql-ref-functions-builtin). They run through the
notebook's spark.sql. What still differs is listed in docs/sql-compatibility.md ("Regular expressions and
text on this PC"), and LOC-002 warns wherever a pattern is used. None of this was run on a Fabric cluster:
UNVERIFIED live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import spark_strings, sql
from fabric_desktop.notebook import Kernel
from fabric_desktop.sqlcompat import analyze

DOCS = Path(__file__).resolve().parents[2] / "docs" / "sql-compatibility.md"

_RUN = """
import json
out = []
for c in {cases!r}:
    try:
        r = spark.sql('SELECT ' + c + ' AS v')
        v = r.collect()[0][0]
        out.append([r.dtypes[0][1], None if v is None else (v if isinstance(v, (list, bool, int)) else str(v))])
    except Exception as e:
        out.append(['error', str(e)])
print('RESULT' + json.dumps(out))
"""


@pytest.fixture(scope="module")
def kernel() -> Kernel:
    return Kernel()


def check(kernel: Kernel, table: list[tuple[str, str, object]]) -> None:
    out = kernel.run(_RUN.format(cases=[c for c, _, _ in table]))
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    got = [tuple(x) for x in json.loads(text.split("RESULT", 1)[1])]
    wrong = [(c, (t, v), g) for (c, t, v), g in zip(table, got, strict=True) if g != (t, v)]
    assert not wrong, "\n".join(f"{c}: want {w}, got {g}" for c, w, g in wrong)


# ---- Java's syntax, translated for RE2 (Spark SQL literals: '\\d' is the pattern \d)
PATTERNS = [
    (r"'abc' rlike 'b'", "boolean", True),
    (r"'abc' rlike '^b'", "boolean", False),
    (r"'ABC' rlike '(?i)abc'", "boolean", True),
    (r"'abc' rlike '\\p{Alpha}+$'", "boolean", True),
    (r"'ab1' rlike '^\\p{Alpha}+$'", "boolean", False),
    (r"'a1' rlike '^[\\p{Alpha}\\p{Digit}]+$'", "boolean", True),
    (r"'1' rlike '\\P{Digit}'", "boolean", False),
    (r"'a.c' rlike 'a\\Q.\\Ec'", "boolean", True),
    (r"'abc' rlike 'a\\Q.\\Ec'", "boolean", False),
    (r"concat('a', chr(11), 'b') rlike 'a\\sb'", "boolean", True),    # Java's \s takes the vertical tab
    (r"'a b' rlike 'a\\hb'", "boolean", True),
    (r"concat('a', chr(13), chr(10), 'b') rlike '^a\\Rb$'", "boolean", True),
    (r"'A' rlike '\\u0041'", "boolean", True),
    (r"'A' rlike '\\0101'", "boolean", True),
    (r"'abc' rlike '\\Aabc\\z'", "boolean", True),
    (r"'aaa' rlike '^a{2,3}?$'", "boolean", True),
    (r"regexp_extract('ab12', '(?<n>\\d+)', 1)", "string", "12"),
    (r"regexp_replace('ab12', '(?<n>\\d+)', '<${n}>')", "string", "ab<12>"),
    (r"NULL rlike 'a'", "boolean", None),
    (r"'a' rlike NULL", "boolean", None),
    (r"regexp_like('abc', 'b')", "boolean", True),
    (r"regexp('abc', 'b')", "boolean", True),
]

FUNCTIONS = [
    (r"regexp_extract('100-200', '(\\d+)-(\\d+)', 2)", "string", "200"),
    (r"regexp_extract('100-200', '(\\d+)-(\\d+)')", "string", "100"),
    (r"regexp_extract('100-200', '(\\d+)-(\\d+)', 0)", "string", "100-200"),
    (r"regexp_extract('foo', '(\\d+)', 1)", "string", ""),
    (r"regexp_extract('ab', '(a)(x)?', 2)", "string", ""),
    (r"regexp_extract('100', '(\\d+)', NULL)", "string", None),
    (r"regexp_extract(NULL, '(\\d+)', 1)", "string", None),
    (r"regexp_extract_all('100-200, 300-400', '(\\d+)-(\\d+)', 2)", "array<string>", ["200", "400"]),
    (r"regexp_extract_all('a1b2', '\\d', 0)", "array<string>", ["1", "2"]),
    (r"regexp_replace('100-200', '(\\d+)-(\\d+)', '$2-$1')", "string", "200-100"),
    (r"regexp_replace('100-200', '(\\d+)', '\\1x')", "string", "1x-1x"),       # \ takes the next character
    (r"regexp_replace('abc', 'b', '\\$')", "string", "a$c"),
    (r"regexp_replace('abc', 'b', '\\\\')", "string", "a\\c"),
    (r"regexp_replace('100-200', '(\\d+)', 'num')", "string", "num-num"),
    (r"regexp_replace('abc', '', '-')", "string", "-a-b-c-"),
    (r"regexp_replace('aaa', 'a', 'b', 2)", "string", "abb"),
    (r"regexp_replace('aaa', 'a', 'b', 5)", "string", "aaa"),
    (r"regexp_replace(NULL, 'a', 'b')", "string", None),
    (r"regexp_replace('abc', 'b', NULL)", "string", None),
    (r"regexp_count('ababab', 'ab')", "int", 3),
    (r"regexp_count('abc', '')", "int", 4),
    (r"regexp_count(NULL, 'a')", "int", None),
    (r"regexp_instr('abcabc', 'c')", "int", 3),
    (r"regexp_instr('abc', 'x')", "int", 0),
    (r"regexp_substr('abc123', '\\d+')", "string", "123"),
    (r"regexp_substr('abc', '\\d+')", "string", None),
    (r"split('a,b,,c,,', ',')", "array<string>", ["a", "b", "", "c", "", ""]),
    (r"split('a,b,c', ',', 2)", "array<string>", ["a", "b,c"]),
    (r"split('a,b,c', ',', -1)", "array<string>", ["a", "b", "c"]),
    (r"split('abc', '')", "array<string>", ["a", "b", "c"]),
    (r"split('a1b22c', '\\d+')", "array<string>", ["a", "b", "c"]),
    (r"split('a.b', '.')", "array<string>", ["", "", "", ""]),
    (r"split(NULL, ',')", "array<string>", None),
]

STRINGS = [
    (r"split_part('a,b,c', ',', 2)", "string", "b"),
    (r"split_part('a,b,c', ',', -1)", "string", "c"),
    (r"split_part('a,b,c', ',', 5)", "string", ""),
    (r"split_part('a,b,c', '', 1)", "string", "a,b,c"),
    (r"split_part('a,b,c', '', 2)", "string", ""),
    (r"split_part(NULL, ',', 1)", "string", None),
    (r"left('abc', -1)", "string", ""),
    (r"left('abc', 0)", "string", ""),
    (r"right('abc', -1)", "string", ""),
    (r"right('abc', 5)", "string", "abc"),
    (r"left(NULL, -1)", "string", None),
    (r"rpad('abc', 6, '')", "string", "abc"),
    (r"lpad('abc', 2, '')", "string", "ab"),
    (r"lpad('abc', 5, 'xy')", "string", "xyabc"),
    (r"locate('b', 'abcb', 3)", "int", 4),
    (r"locate('b', 'abc', 0)", "int", 0),
    (r"locate('b', 'abc', NULL)", "int", 0),
    (r"chr(65)", "string", "A"),
    (r"chr(321)", "string", "A"),
    (r"chr(-1)", "string", ""),
    (r"char(66)", "string", "B"),
    (r"length('ab ')", "int", 3),
    (r"char_length(NULL)", "int", None),
    (r"octet_length('é')", "int", 2),
    (r"bit_length('a')", "int", 8),
    (r"instr('abc', 'c')", "int", 3),
    (r"position('b' IN 'abc')", "int", 2),
    (r"levenshtein('kitten', 'sitting')", "int", 3),
    (r"'a_b' like 'a\\_b'", "boolean", True),
    (r"'axb' like 'a\\_b'", "boolean", False),
    (r"'a%' ilike 'A\\%'", "boolean", True),
    (r"'ab' like 'a_'", "boolean", True),
    (r"concat('a', NULL)", "string", None),
    (r"'a' || NULL", "string", None),
    (r"concat('a', 'b')", "string", "ab"),
    (r"concat_ws(',', 'a', NULL, 'b')", "string", "a,b"),
    (r"concat_ws(',', array('a', NULL, 'b'), 'c')", "string", "a,b,c"),
    (r"concat_ws('-', split('x,y', ','))", "string", "x-y"),
    (r"concat_ws(NULL, array('a'))", "string", None),
]


def test_java_patterns_read_as_spark_reads_them(kernel):
    check(kernel, PATTERNS)


def test_regular_expression_functions_give_sparks_values(kernel):
    check(kernel, FUNCTIONS)


def test_string_functions_give_sparks_values(kernel):
    check(kernel, STRINGS)


REFUSED = [
    (r"'abc' rlike 'a(?=b)'", "a lookahead or lookbehind"),
    (r"'abc' rlike '(?<!x)a'", "a lookahead or lookbehind"),
    (r"'abab' rlike '(ab)\\1'", "a backreference"),
    (r"'abab' rlike '(?<x>ab)\\k<x>'", "a backreference"),
    (r"'aaa' rlike 'a*+'", "a possessive quantifier"),
    (r"'aaa' rlike 'a{2}+'", "a possessive quantifier"),
    (r"'abc' rlike '(?>a)bc'", "an atomic group"),
    (r"'abc' rlike 'abc\\Z'", "'\\Z'"),
    (r"'abc' rlike '(?x) a b c'", "the flag 'x'"),
    (r"'abc' rlike '(?U)a'", "the flag 'U'"),
    (r"'abc' rlike '[a[b]]'", "a class inside a class"),
    (r"'abc' rlike '[[:alpha:]]'", "a class inside a class"),
    (r"'abc' rlike '[a-z&&[^b]]'", "a class intersection"),
    (r"'abc' rlike '[a-z&&b]'", "a class intersection"),
    (r"'abc' rlike '\\p{InGreek}'", "\\p{InGreek}"),
    (r"'abc' rlike '\\p{IsAlphabetic}'", "the binary property"),
    (r"regexp_replace('a.b', '\\.', '$')", "refers to no group"),
    (r"regexp_replace('ab', '(a)', '$2')", "refers to group 2, and the pattern has 1 group"),
    (r"regexp_replace('ab', '(a)', '${x}')", "a group the pattern doesn't name"),
    (r"regexp_replace('ab', 'a', 'b\\')", "ends in a \\"),
    (r"regexp_replace('aaa', 'a', 'b', 0)", "position is 0"),
    (r"regexp_extract('100', '(\\d+)', 3)", "asks for group 3, and the pattern has 1 group"),
    (r"regexp_extract('100', '(\\d+)', -1)", "group index is -1"),
    (r"regexp_extract_all('a1', '(\\d)', 2)", "asks for group 2"),
    (r"split_part('a,b', ',', 0)", "index can't be 0"),
]


@pytest.mark.parametrize("case, why", REFUSED)
def test_what_re2_cant_read_is_refused_by_name(case, why):
    with pytest.raises(ValueError) as caught:
        sql.duckdb_sql(sql.parse(f"SELECT {case}")[0])
    assert why in str(caught.value) and "nothing ran" in str(caught.value), str(caught.value)


def test_a_replacement_from_the_data_with_a_dollar_or_backslash_stops_by_name(kernel):
    out = kernel.run("spark.sql(\"SELECT regexp_replace('ab', '(a)', r) AS v FROM VALUES ('$1x') AS t(r)\")"
                     ".collect()")
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "error" and "only a replacement written in the query" in text, text
    check(kernel, [("(SELECT regexp_replace('ab', 'a', r) FROM VALUES ('z') AS t(r))", "string", "zb")])


def test_the_translation_keeps_what_re2_and_java_read_alike():
    for pattern in (r"^a+b*?c{2,3}$", r"[^a-z_]\d\w\b", r"(?i)abc", r"(?s:a.b)", r"(?:a|b)+", r"x\.y"):
        assert spark_strings.java_pattern(pattern, "rlike")[0] == pattern
    assert spark_strings.java_pattern(r"(a)(?:b)(?<n>c)", "rlike")[1:] == (2, {"n": 2})
    assert spark_strings.java_pattern(r"(?u)a", "rlike")[0] == "a"


def test_databricks_sql_reads_java_patterns_too():
    import duckdb
    from fabric_desktop.engines.local_sql import _databricks
    con = duckdb.connect()
    query = r"SELECT regexp_replace('100-200', '(?<a>\\d+)-(\\d+)', '$2-${a}')"
    assert con.execute(sql.duckdb_sql(_databricks(query)[-1])).fetchone()[0] == "200-100"


STILL_DIFFERS = {
    "A pattern taken from the data": "SELECT rlike(s, p) FROM t",
    "`$` without the `m` flag": "SELECT rlike(s, 'a$') FROM t",
    "`.` and": "SELECT rlike(s, 'a.b') FROM t",
    "`(?i)` with letters outside ASCII": "SELECT rlike(s, '(?i)é') FROM t",
    "`\\b` next to a letter outside ASCII": "SELECT rlike(s, '\\\\bé') FROM t",
    "`split` with a pattern that can match no text": "SELECT split(s, 'x*') FROM t",
    "a replacement taken from the data": "SELECT regexp_replace(s, 'a', r) FROM t",
    "A pattern in the DataFrame API given as a column": "",
    "`F.split_part` with an empty delimiter": "",
    "`F.upper` of `'ß'`": "",  # SQL upper('ß') gives Spark's SS since #3050
}


def test_what_still_differs_is_listed_in_the_compatibility_docs():
    text = DOCS.read_text(encoding="utf-8")
    section = text.split("## Regular expressions and text on this PC", 1)[1].split("\n## ", 1)[0]
    for words in STILL_DIFFERS:
        assert words in section, words


@pytest.mark.parametrize("case", [c for c, q in STILL_DIFFERS.items() if "rlike" in q or "regexp" in q
                                  or "split(" in q])
def test_the_static_check_warns_where_a_pattern_is_used(case):
    hits = [d for d in analyze(STILL_DIFFERS[case], "spark")["diagnostics"] if d["rule"] == "LOC-002"]
    assert hits, case


def test_the_dataframe_api_reads_java_patterns_given_as_text(kernel):
    out = kernel.run(r'''
from pyspark.sql import functions as F
df = spark.createDataFrame([("100-200", "a_b")], ["s", "t"])
r = df.select(F.regexp_replace("s", r"(\d+)-(\d+)", "$2-$1").alias("a"), F.regexp_extract("s", r"(?<n>\d+)", 1).alias("b"),
              F.col("s").rlike(r"^\p{Digit}+-").alias("c"), F.length("s").alias("d"), F.col("t").like(r"a\_b").alias("e"),
              F.instr("s", "-").alias("f"))
print("RESULT", r.dtypes, r.collect()[0])
try:
    df.select(F.col("s").rlike("a(?=b)"))
except ValueError as e:
    print("REFUSED", e)
''')
    text = "".join(o.get("text", "") + str(o.get("evalue", "")) for o in out["outputs"])
    assert out["status"] == "ok", text
    assert ("[('a', 'string'), ('b', 'string'), ('c', 'boolean'), ('d', 'int'), ('e', 'boolean'), ('f', 'int')] "
            "Row(a='200-100', b='100', c=True, d=7, e=True, f=4)") in text, text
    assert "REFUSED rlike can't use a lookahead" in text, text
