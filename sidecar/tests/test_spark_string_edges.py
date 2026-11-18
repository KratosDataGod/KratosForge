"""Spark SQL text functions at the edges the differential test (tests/spark_differential.py) found, each answer
checked against Apache Spark 3.5.3 (local, UTC, ANSI off): a negative substring length, overlay before the
first character and locate on NULL text. levenshtein outside ASCII is in tests/test_spark_differential_fixes.py."""

from __future__ import annotations

import pytest

from fabric_desktop import sql


def one(expression: str):
    return next(iter(sql.run("SELECT " + expression).to_pylist()[0].values()))


@pytest.mark.parametrize(("expression", "spark"), [
    ("substring('Hello World', -1, -6)", ""),
    ("substr('MiXeD', -2, -6)", ""),
    ("substring('abc', 2, -1)", ""),
    ("substr(CAST(NULL AS STRING), 1, -2)", None),
    ("substring('abc', CAST(NULL AS INT), -1)", None),
    ("substring('abc', 2, CAST(NULL AS INT))", None),
    ("substring('Hello World', 2, 3)", "ell"),
    ("substring('abcdef', 0, 3)", "abc"),
    ("substring('abc', -5, 4)", "ab"),
    ("overlay('abc' PLACING ',' FROM 0)", ",abc"),
    ("overlay('MiXeD' PLACING 'ab' FROM -1)", "abMiXeD"),
    ("overlay('a,b,,c' PLACING 'a' FROM -2)", "ac"),
    ("overlay('Spark SQL' PLACING '_' FROM 6)", "Spark_SQL"),
    ("locate('b', CAST(NULL AS STRING), -2)", None),
    ("locate(CAST(NULL AS STRING), 'abcb', CAST(NULL AS INT))", 0),
    ("locate('b', 'abcb', 0)", 0),
    ("locate('b', 'abcb', 3)", 4),
    ("levenshtein('kitten', 'sitting')", 3),
    ("levenshtein(CAST(NULL AS STRING), 'a')", None),
])
def test_answers_as_spark(expression: str, spark) -> None:
    assert one(expression) == spark
