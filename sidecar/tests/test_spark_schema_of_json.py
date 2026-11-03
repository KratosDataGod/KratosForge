"""schema_of_json worked out on this PC as Databricks infers it with default options (#1447)."""

from __future__ import annotations

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks


def answer(query: str, dialect: str = "databricks"):
    statements = _databricks(query) if dialect == "databricks" else sql.parse(query, "spark")
    return duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()[0]


@pytest.mark.parametrize("dialect", ["databricks", "spark"])
def test_the_docs_example(dialect):
    assert answer("""SELECT schema_of_json('[{"col":0}]')""", dialect) == "ARRAY<STRUCT<`col`: BIGINT>>"


@pytest.mark.parametrize("text, want", [
    # fields sorted by name; null and "" are STRING; an empty object field is dropped; [] is ARRAY<STRING>
    ('{"b":[1,2.5],"a":{"x":null,"y":"s"},"c":[],"d":{},"e":[1,"x"],"f":true,"g":""}',
     "STRUCT<`a`: STRUCT<`x`: STRING, `y`: STRING>, `b`: ARRAY<DOUBLE>, `c`: ARRAY<STRING>, `e`: ARRAY<STRING>, "
     "`f`: BOOLEAN, `g`: STRING>"),
    ('[{"a":1},{"b":2.0,"a":1.5}]', "ARRAY<STRUCT<`a`: DOUBLE, `b`: DOUBLE>>"),   # array elements merge
    ('[{"a":{"x":1}},{"a":{"y":"s"}}]', "ARRAY<STRUCT<`a`: STRUCT<`x`: BIGINT, `y`: STRING>>>"),
    ("[[1],[2.5,null]]", "ARRAY<ARRAY<DOUBLE>>"),
    ("[1, [2]]", "ARRAY<STRING>"),
    ("1", "BIGINT"),
    ("1e2", "DOUBLE"),
    ("null", "STRING"),
    ("{}", "STRUCT<>"),
    ('{"a":{}}', "STRUCT<>"),
    ("[{}]", "ARRAY<STRUCT<>>"),
    ("[]", "ARRAY<STRING>"),
    ('{"a`b":1}', "STRUCT<`a``b`: BIGINT>"),
])
def test_inference_follows_sparks_rules(text, want):
    assert sql.schema_of_json(text) == want


def test_from_json_reads_the_schema_it_gives():
    assert answer("""SELECT from_json('[{"col":5}]', schema_of_json('[{"col":0}]'))""") == [{"col": 5}]
    assert answer("""SELECT from_json('{"a`b":1}', schema_of_json('{"a`b":1}'))""") == {"a`b": 1}


@pytest.mark.parametrize("query, match", [
    ("""SELECT schema_of_json('{"a":123456789012345678901}')""", "too big for BIGINT"),
    ("SELECT schema_of_json('{')", "isn't JSON"),
    ("""SELECT schema_of_json('{"a":1, "a":2}')""", "repeats a key"),
    ("""SELECT schema_of_json('{"a":1}', map('allowNumericLeadingZeros', 'true'))""", "options"),
    ("SELECT schema_of_json(concat('{', '}'))", "written out as text"),
])
def test_what_cant_be_worked_out_the_same_is_refused_plainly(query, match):
    with pytest.raises(ValueError, match=match):
        _databricks(query)
