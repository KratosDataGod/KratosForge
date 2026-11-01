"""from_json with a written-out schema and str_to_map on this PC (#1404)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines.local_sql import _databricks


def answer(query: str, dialect: str = "databricks"):
    statements = _databricks(query) if dialect == "databricks" else sql.parse(query, "spark")
    return duckdb.connect().execute(sql.duckdb_sql(statements[-1])).fetchone()[0]


@pytest.mark.parametrize("dialect", ["databricks", "spark"])
@pytest.mark.parametrize("query, want", [
    ("""SELECT from_json('{"a":1, "b":0.8}', 'a INT, b DOUBLE')""", {"a": 1, "b": 0.8}),
    ("""SELECT from_json('{"a":1}', 'A INT')""", {"A": None}),   # keys match case-sensitively
    ("""SELECT from_json('{"a":1}', 'STRUCT<a: BIGINT, b: STRING>')""", {"a": 1, "b": None}),
    ("""SELECT from_json('[1, 2, 3]', 'ARRAY<INT>')""", [1, 2, 3]),
    ("""SELECT from_json('{"s":{"t":[{"u":1},{"u":2}]}}', 's STRUCT<t: ARRAY<STRUCT<u: BIGINT>>>')""",
     {"s": {"t": [{"u": 1}, {"u": 2}]}}),
    ("SELECT from_json('abc', 'a INT, b STRING')", {"a": None, "b": None}),   # not JSON: a struct of NULLs
    ("SELECT from_json('', 'a INT')", None),
    ("SELECT from_json('   ', 'a INT')", None),
    ("SELECT from_json(NULL, 'a INT')", None),
    ("SELECT from_json('abc', 'ARRAY<INT>')", None),
    ("SELECT str_to_map('a:1,b:2,c:3', ',', ':')", {"a": "1", "b": "2", "c": "3"}),
    ("SELECT str_to_map('a:1,b:2,c:3')", {"a": "1", "b": "2", "c": "3"}),
    ("SELECT str_to_map('a')", {"a": None}),
    ("SELECT str_to_map('')", {"": None}),
    ("SELECT str_to_map(NULL)", None),
    ("SELECT str_to_map('a=1;b', ';', '=')", {"a": "1", "b": None}),
    ("SELECT str_to_map('a:1:2,:x')", {"a": "1:2", "": "x"}),   # splits at the first key-value delimiter
    ("SELECT str_to_map('a1b2c', '[0-9]', 'x')", {"a": None, "b": None, "c": None}),   # delimiters are regexes
])
def test_answers_as_databricks_documents(query, want, dialect):
    assert answer(query, dialect) == want


def test_each_value_is_read_as_sparks_json_reader_reads_it():
    got = answer("""SELECT from_json('{"a":"1", "b":1.5, "c":true, "d":{"x":[1, 2]}, "e":"2024-01-02",
        "f":"2024-01-01T10:00:00+02:00", "g":1.555, "h":3000000000, "i":7, "j":"yes", "k":[1]}',
        'a INT, b INT, c BOOLEAN, d STRING, e DATE, f TIMESTAMP, g DECIMAL(10,2), h INT, i DOUBLE, j BOOLEAN, k STRING')""")
    assert got["a"] is None and got["b"] is None and got["h"] is None and got["j"] is None   # no coercion, no overflow
    assert got["c"] is True and got["d"] == '{"x":[1,2]}' and got["k"] == "[1]"
    assert got["e"] == dt.date(2024, 1, 2)
    assert got["f"] == dt.datetime(2024, 1, 1, 8, 0, tzinfo=dt.timezone.utc)
    assert got["g"] == Decimal("1.56") and got["i"] == 7.0


def test_a_column_and_a_lambda_run():
    con = duckdb.connect()
    rows = con.execute(sql.duckdb_sql(_databricks(
        """SELECT from_json(x, 'a INT').a FROM VALUES ('{"a":5}'), (NULL), ('nope') AS t(x)""")[-1])).fetchall()
    assert [r[0] for r in rows] == [5, None, None]
    assert answer("""SELECT transform(array('{"a":1}', '{"a":2}'), x -> from_json(x, 'a INT').a)""") == [1, 2]


def test_its_argument_is_still_rewritten_and_never_inside_a_lambda():
    # The text argument keeps the other Spark fixes (sort_array puts NULL first) and a column named like the
    # rewrite's own lambda arguments is the person's column.
    assert answer("SELECT str_to_map(array_join(sort_array(array('b:2', 'a:1')), ','))") == {"a": "1", "b": "2"}
    assert answer("""SELECT from_json(fj_t, 'a INT') FROM (SELECT '{"a":3}' AS fj_t)""") == {"a": 3}


@pytest.mark.parametrize("query, match", [
    ("""SELECT from_json('{"a":1}', 'a MAP<STRING, INT>')""", "can't read a MAP"),
    ("""SELECT from_json('{"a":1}', 'a TIMESTAMP_NTZ')""", "can't read a TIMESTAMP_NTZ"),
    ("""SELECT from_json('{"a":1}', 'INT')""", "STRUCT or ARRAY schema"),
    ("""SELECT from_json('{"a":1}', concat('a ', 'INT'))""", "written out as text"),
    ("""SELECT from_json('{"a":1}', 'a INT', map('mode', 'FAILFAST'))""", "options"),
    ("""SELECT from_json('{"a":1}', '`x"y` INT')""", "quote or backslash"),
])
def test_what_cant_run_the_same_is_refused_plainly(query, match):
    with pytest.raises(ValueError, match=match):
        _databricks(query)


def test_a_json_array_with_a_struct_schema_stops_with_a_message():
    with pytest.raises(duckdb.Error, match="ARRAY<STRUCT"):
        answer("""SELECT from_json('[{"a":1}]', 'a INT')""")


def test_a_duplicate_key_stops_as_in_databricks():
    with pytest.raises(duckdb.Error, match="unique"):
        answer("SELECT str_to_map('a:1,a:2')")


def test_strings_spark_reads_into_doubles_decimals_and_whole_numbers_into_timestamps():
    """#1412 review, from Spark's JacksonParser: DOUBLE and FLOAT read NaN, +INF, +Infinity, Infinity,
    -INF and -Infinity (allowNonNumericNumbers, on by default); DECIMAL reads a string with the en-US
    decimal parser (commas dropped), rounding half-up; TIMESTAMP reads a whole number as epoch seconds."""
    got = answer("""SELECT from_json('{"a":"NaN", "b":"+INF", "c":"-Infinity", "d":"Infinity", "e":"-INF",
        "f":"+Infinity", "g":"inf", "h":"1.25", "i":"-1.25", "j":"1,000.5", "k":"x", "l":1700000000, "m":2.5}',
        'a DOUBLE, b DOUBLE, c FLOAT, d DOUBLE, e DOUBLE, f DOUBLE, g DOUBLE, h DECIMAL(5,1), i DECIMAL(5,1),
         j DECIMAL(10,1), k DECIMAL(5,1), l TIMESTAMP, m TIMESTAMP')""")
    assert got["a"] != got["a"]   # NaN
    assert got["b"] == got["d"] == got["f"] == float("inf") and got["c"] == got["e"] == float("-inf")
    assert got["g"] is None and got["k"] is None and got["m"] is None
    assert got["h"] == Decimal("1.3") and got["i"] == Decimal("-1.3") and got["j"] == Decimal("1000.5")
    assert got["l"] == dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("query", [
    """SELECT from_json('{"a":1, "a":2}', 'a INT')""",
    """SELECT from_json('{"s":{"a":1, "a":2}}', 's STRUCT<a: INT>')""",
])
def test_a_repeated_schema_key_stops_rather_than_keeping_the_first(query):
    """Spark keeps the last of a repeated key and json_extract the first, so it stops (#1412 review)."""
    with pytest.raises(duckdb.Error, match="repeats a key of its schema"):
        answer(query)


def test_a_repeated_key_outside_the_schema_still_runs():
    assert answer("""SELECT from_json('{"b":1, "b":2, "a":3}', 'a INT')""") == {"a": 3}
