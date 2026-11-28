"""A NUL character (U+0000) in a string literal, fuzzed in Snowflake SQL, Spark SQL and Databricks SQL, and
Spark's url_decode.

DuckDB 1.x reads a query's text only up to a NUL, so a literal holding one, written as '\\0' or as the
character itself, stopped a valid query with "unterminated quoted string". Each dialect reads '\\0' as
U+0000 (Snowflake's escape sequences; Spark's and Databricks' unescapeSQLString), and the value must come
back whole. A NUL must never end the literal and let the rest of the text run as SQL: names can't hold one
here, and are refused by name; comments lose it.

Spark's url_decode was a template that wrote its argument five times, so each level of nesting grew the
query fivefold, and refused %+1, which Spark 3.5's java.net.URLDecoder reads as byte 1. It is now the same
function Databricks SQL uses (databricks_bytes.url_decode).

Seeded and deterministic (the standard library's random). Cells run through a DuckDB engine session, as in
tests/test_snowflake_databricks_sql_fuzz.py.
"""

from __future__ import annotations

import random
import time

import duckdb
import pytest

from fabric_desktop import sql
from fabric_desktop.engines import registry
from fabric_desktop.engines.contract import CellRequest, SessionConfig

DIALECTS = ["snowflake", "spark", "databricks"]
SLOW = 5.0


@pytest.fixture(scope="module")
def session():
    return registry.adapter("local-duckdb").open_session(SessionConfig("nul"))


def _cell(session, code: str, dialect: str):
    """The cell's result: its rows, or ("error", exception name, message)."""
    result = session.execute(CellRequest(code=code, language="sql", dialect=dialect))
    out = result.outputs[-1]
    if result.status != "ok":
        return ("error", out["ename"], out["evalue"])
    return out.get("rows")


def _quoted(text: str, dialect: str) -> str:
    """``text`` as a string literal of ``dialect``, its NULs written as the character itself."""
    if dialect == "snowflake":
        return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


# ---- found by hand, then fuzzed


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("literal, want", [
    ("'\\0'", "\x00"),
    ("'a\\0b'", "a\x00b"),
    ("'a\x00b'", "a\x00b"),
    ("'\x00\x00'", "\x00\x00"),
    ("'x\\0'", "x\x00"),
])
def test_a_nul_in_a_string_comes_back_whole(session, dialect, literal, want):
    # DuckDB 1.x: "Parser Error: unterminated quoted string at or near 'a"
    got = _cell(session, f"SELECT {literal} AS c, length({literal}) AS n", dialect)
    assert got == [[want, len(want)]], got


@pytest.mark.parametrize("dialect", DIALECTS)
def test_an_unnamed_column_of_a_nul_string_runs(session, dialect):
    got = _cell(session, "SELECT 'a\\0b'", dialect)
    assert got == [["a\x00b"]], got


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_nul_never_ends_a_literal_early(session, dialect):
    # Text after the NUL stays inside the string: nothing after it runs as SQL
    session.execute(CellRequest(code="CREATE OR REPLACE TABLE kept AS SELECT 1 AS k", language="sql"))
    tail = "'; DROP TABLE kept; SELECT '"
    got = _cell(session, f"SELECT {_quoted(chr(0) + tail, dialect)} AS c", dialect)
    assert got == [["\x00" + tail]], got
    got = _cell(session, f"DELETE FROM kept WHERE 'x\\0' = 'x' OR 1 = 0 {chr(0)} OR 1 = 1", dialect)
    assert isinstance(got, tuple), got  # an unquoted NUL in a statement is refused, not cut off there
    assert _cell(session, "SELECT k FROM kept", "duckdb") == [[1]]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("query", [
    "DELETE FROM guard\x00 WHERE k = 2",
    "DELETE FROM guard WHERE k\x00 = 2",
    "UPDATE guard SET k = 9 WHERE k\x00 = 2",
    "SELECT 1; DELETE FROM guard WHERE k\x00 IN (SELECT 2)",
    "DELETE FROM guard WHERE k = 2 OR ab\x00s(k) = 1",
])
def test_a_nul_outside_quotes_never_cuts_a_statement_short(session, dialect, query):
    # DuckDB 1.x reads only up to a NUL; the statement must be refused whole, and guard keeps its rows
    session.execute(CellRequest(code="CREATE OR REPLACE TABLE guard AS SELECT * FROM (VALUES (1), (2)) t(k)",
                                language="sql"))
    got = _cell(session, query, dialect)
    assert isinstance(got, tuple) and "NUL character" in got[2], got
    assert _cell(session, "SELECT k FROM guard ORDER BY k", "duckdb") == [[1], [2]]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_name_holding_a_nul_is_refused_by_name(session, dialect):
    quote = '"' if dialect == "snowflake" else "`"
    got = _cell(session, f"SELECT 1 AS {quote}a{chr(0)}b{quote}", dialect)
    assert isinstance(got, tuple) and "NUL character" in got[2], got


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_comment_holding_a_nul_doesnt_stop_the_query(session, dialect):
    assert _cell(session, f"SELECT 1 AS a /* x{chr(0)}y */", dialect) == [[1]]
    assert _cell(session, f"SELECT 1 AS a -- x{chr(0)}y\n", dialect) == [[1]]


def test_spark_url_decode_reads_a_signed_escape_as_java_does(session):
    # Spark 3.5's url_decode is java.net.URLDecoder: Integer.parseInt("+1", 16) is 1; the template refused it
    assert _cell(session, "SELECT url_decode('%+1%-0') AS a", "spark") == [["\x01\x00"]]
    assert _cell(session, "SELECT url_decode('a+%21') AS a", "spark") == [["a !"]]
    got = _cell(session, "SELECT url_decode('%zz') AS a", "spark")
    assert isinstance(got, tuple) and "CANNOT_DECODE_URL" in got[2], got
    # Java puts U+FFFD in place of bytes that aren't UTF-8; the template refused these
    assert _cell(session, "SELECT url_decode('a%FF') AS a", "spark") == [["a�"]]


def test_nested_spark_url_decode_stays_small():
    # each level wrote its argument five times: 30 levels were 5 ** 30 copies
    start = time.monotonic()
    text = sql.duckdb_sql(sql.parse("SELECT " + "url_decode(" * 30 + "'a%2B'" + ")" * 30, "spark")[0])
    assert len(text) < 2000 and time.monotonic() - start < SLOW


def test_nested_spark_url_decode_runs(session):
    assert _cell(session, "SELECT url_decode(url_decode('a%252B1')) AS a", "spark") == [["a+1"]]
    assert _cell(session, "SELECT " + "url_decode(" * 12 + "'a%2B'" + ")" * 12 + " AS a", "spark") == [["a "]]


# ---- seeded fuzz: random strings holding NULs, quotes and backslashes come back as written


ALPHABET = ["\x00", "\x00", "'", "\\", "a", "é", "😀", " ", ";", "--", "/*", "*/", "\n", '"', "`", "%", "$"]


@pytest.mark.parametrize("dialect, seed", [("snowflake", 20261121), ("spark", 7), ("databricks", 1556)])
def test_random_strings_with_nuls_round_trip(session, dialect, seed):
    r = random.Random(seed)
    for _ in range(60):
        texts = ["".join(r.choice(ALPHABET) for _ in range(r.randint(0, 12))) for _ in range(r.randint(1, 3))]
        columns = ", ".join(f"{_quoted(t, dialect)} AS c{i}" for i, t in enumerate(texts))
        got = _cell(session, f"SELECT {columns}", dialect)
        assert got == [texts], (texts, got)


@pytest.mark.parametrize("dialect, seed", [("snowflake", 3), ("spark", 11), ("databricks", 29)])
def test_nuls_anywhere_in_a_query_run_or_are_refused_by_name(session, dialect, seed):
    # A NUL put anywhere in a query is read, or refused by name; it never cuts the query short, and the
    # table the query could delete from keeps its rows unless the query, read whole, deletes them
    r = random.Random(seed)
    base = "DELETE FROM guard WHERE k = 2 AND s = 'a\\0b' /* c */"
    for _ in range(40):
        session.execute(CellRequest(code="CREATE OR REPLACE TABLE guard AS SELECT * FROM (VALUES (1, 'x'), "
                                    "(2, 'a' || chr(0) || 'b')) t(k, s)", language="sql"))
        at = r.randrange(len(base) + 1)
        query = base[:at] + "\x00" * r.randint(1, 2) + base[at:]
        start = time.monotonic()
        got = _cell(session, query, dialect)
        assert time.monotonic() - start < SLOW
        rows = _cell(session, "SELECT k FROM guard ORDER BY k", "duckdb")
        if isinstance(got, tuple):
            assert got[1] in ("ValueError", "Unsupported") or (
                isinstance(getattr(duckdb, got[1], None), type) and got[1] != "Error"), (query, got)
            assert rows == [[1], [2]], (query, got, rows)
        else:
            assert rows in ([[1]], [[1], [2]]), (query, rows)  # never more than the query, read whole, deletes
