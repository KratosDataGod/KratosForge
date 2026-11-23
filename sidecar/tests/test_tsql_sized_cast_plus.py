"""WR-266: ``+`` inside a CAST or CONVERT to a sized text type is rewritten as anywhere else.

``CAST(s + 'x' AS VARCHAR(5))`` became ``LEFT(CAST(s + 'x' AS TEXT), 5)``: the cut copied the value before the
``+`` on text became ``||``, so the rewrite landed on a node no longer in the tree and DuckDB couldn't bind the
``+``. T-SQL joins the two strings, then cuts the result to 5 characters. A number still outranks text, NULL with
text is NULL, and a sized cast inside another cast is cut before the outer one reads it.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import tsql


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("sizedplus") / "app" / "local-data")
    e = local.engine(local.create("SizedPlus")["id"])
    e.execute("CREATE TABLE dbo.t AS SELECT * FROM (VALUES ('abc', '12', 1, CAST(NULL AS VARCHAR)), "
              "('de', '7', 2, 'q')) v(s, d, n, z)")
    return e


def one(engine, sql):
    return engine.execute("SELECT " + sql, dialect="tsql")["rows"][0][0]


def column(engine, sql):
    return [r[0] for r in engine.execute(sql, dialect="tsql")["rows"]]


# (T-SQL expression, T-SQL's answer)
CASES = [
 # text + text, joined then cut
 ("CAST('ab' + 'xyz' AS VARCHAR(4))", "abxy"),
 ("CONVERT(VARCHAR(4), 'ab' + 'xyz')", "abxy"),
 ("CAST(N'ab' + N'xyz' AS NVARCHAR(3))", "abx"),
 ("CONVERT(NVARCHAR(3), N'ab' + N'xyz')", "abx"),
 ("CAST('a' + 'b' AS CHAR(4))", "ab  "),
 ("CONVERT(CHAR(3), 'ab' + 'cd')", "abc"),
 ("CONVERT(CHAR(4), 'a' + 'b') + '|'", "ab  |"),
 ("TRY_CAST('ab' + 'cd' AS VARCHAR(3))", "abc"),
 ("CAST('ab' + 'cd' AS VARCHAR(MAX))", "abcd"),
 ("CAST('ab' + 'cd' AS VARCHAR(10))", "abcd"),
 # nested +, and a sized cast inside another
 ("CAST(('a' + 'b') + 'c' AS VARCHAR(2))", "ab"),
 ("CONVERT(VARCHAR(4), 'ab' + 'cd' + 'ef')", "abcd"),
 ("CAST(CAST('ab' + 'cd' AS VARCHAR(3)) + 'z' AS VARCHAR(5))", "abcz"),
 ("CAST('a' + CAST('bcd' AS VARCHAR(2)) AS NVARCHAR(10))", "abc"),
 ("CAST(CAST('ab' + 'cd' AS CHAR(3)) + 'z' AS CHAR(5))", "abcz "),
 ("CAST(CAST('12' + '3' AS VARCHAR(2)) AS INT)", 12),
 ("CAST(CAST('123' AS VARCHAR(2)) AS INT)", 12),
 ("CAST('12' + '3' AS INT)", 123),
 # text + number: the number outranks the text
 ("CAST('1' + 2 AS VARCHAR(3))", "3"),
 ("CONVERT(VARCHAR(3), 2 + '1')", "3"),
 ("CAST('10' + 5 AS NVARCHAR(5))", "15"),
 ("CAST('1' + 2 AS CHAR(3))", "3  "),
 ("CAST(1 + 2 AS VARCHAR(5))", "3"),
 # NULL with text is NULL
 ("CAST(NULL + 'x' AS VARCHAR(2))", None),
 ("CONVERT(NVARCHAR(2), 'x' + NULL)", None),
]


@pytest.mark.parametrize(("sql", "want"), CASES)
def test_plus_inside_sized_cast(engine, sql, want):
    assert one(engine, sql) == want


def test_column_plus_literal_from_a_table(engine):
    assert column(engine, "SELECT CAST(s + 'x' AS VARCHAR(3)) FROM dbo.t ORDER BY n") == ["abc", "dex"]
    assert column(engine, "SELECT CONVERT(NVARCHAR(4), s + '-' + d) FROM dbo.t ORDER BY n") == ["abc-", "de-7"]
    assert column(engine, "SELECT CAST(s + 'x' AS CHAR(4)) FROM dbo.t ORDER BY n") == ["abcx", "dex "]
    assert column(engine, "SELECT CONVERT(VARCHAR(2), s + z) FROM dbo.t ORDER BY n") == [None, "de"]
    assert column(engine, "SELECT CAST(s + CAST(n AS VARCHAR(1)) AS CHAR(5)) FROM dbo.t ORDER BY n") == ["abc1 ", "de2  "]


def test_text_column_plus_number_adds(engine):
    assert column(engine, "SELECT CAST(d + n AS VARCHAR(3)) FROM dbo.t ORDER BY n") == ["13", "9"]
    assert column(engine, "SELECT CONVERT(CHAR(3), n + d) FROM dbo.t ORDER BY n") == ["13 ", "9  "]


def test_number_too_long_is_an_error(engine):
    with pytest.raises(Exception, match="doesn't fit"):
        one(engine, "CAST('100' + 1 AS VARCHAR(2))")


def test_text_not_a_number_is_an_error(engine):
    with pytest.raises(Exception, match="Conversion failed"):
        one(engine, "CAST('x' + 1 AS VARCHAR(3))")


def test_no_plus_left_in_the_translation():
    for sql in ("SELECT CAST(s + 'x' AS VARCHAR(5)) FROM dbo.t", "SELECT CONVERT(NVARCHAR(5), 'a' + ('b' + 'c'))",
                "SELECT CAST(CAST('a' + 'b' AS CHAR(3)) + 'c' AS VARCHAR(2))"):
        out = tsql.translate(sql, {"dbo": {"t": {"s": "VARCHAR"}}})[0][0]
        assert "+" not in out and "||" in out, out


def test_number_cut_reads_its_source_once(engine):
    """A sized cast inside another is now cut, so a number checked for length is bound once: RAND is read once."""
    text = tsql.translate("SELECT CAST(CAST(CAST(RAND() * 10 AS INT) AS VARCHAR(5)) AS INT)")[0][0]
    assert text.upper().count("RANDOM()") == 1
    assert 0 <= one(engine, "CAST(CAST(CAST(RAND() * 10 AS INT) AS VARCHAR(5)) AS INT)") < 10
