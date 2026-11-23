"""WR-265: ISNUMERIC on the local engine, with SQL Server's rules.

The answers come from Microsoft's ISNUMERIC and money pages (the number types, '+', '-', '$' and the currency
symbols) and from the per-character table in Jeff Moden's "Why doesn't ISNUMERIC work correctly?" on
SQLServerCentral (each character alone, before '0', after '0' and between zeros). Text whose answer no source
gives is refused by name instead of guessed."""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

TAB, LF, VT, FF, CR, NBSP = "\t", "\n", "\x0b", "\x0c", "\r", "\xa0"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("ISNUMERIC")["id"])
    yield e
    local.close_all()


def _isnumeric(engine, text: str | None):
    """ISNUMERIC of the text as a row of a table, so the rule is the one DuckDB runs on data, not a folded literal."""
    engine.execute("CREATE OR REPLACE TABLE dbo.isnum_values (v VARCHAR)")
    literal = "NULL" if text is None else "'" + text.replace("'", "''") + "'"
    engine.execute(f"INSERT INTO dbo.isnum_values VALUES ({literal})")
    return engine.execute("SELECT ISNUMERIC(v) FROM dbo.isnum_values", dialect="tsql")["rows"][0][0]


# (text, SQL Server's answer)
TEXT = [
    (None, 0),
    ("", 0),
    (" ", 0),
    ("   ", 0),
    ("0", 1),
    ("12.5", 1),
    ("-10", 1),
    ("+7", 1),
    (".5", 1),
    ("5.", 1),
    ("-.5", 1),
    ("x", 0),
    ("abc", 0),
    ("12a", 0),
    ("0x1A", 0),
    ("NaN", 0),
    ("Infinity", 0),
    ("1..0", 0),
    ("0+", 0),
    ("0$", 0),
    # Currency symbols, alone and before the value
    ("$", 1),
    ("£", 1),
    ("€", 1),
    ("¥", 1),
    ("¢", 1),
    ("¤", 1),
    ("\\", 1),
    ("$1", 1),
    ("$1,000.50", 1),
    ("£5", 1),
    ("€0", 1),
    ("\\0", 1),
    ("₩100", 1),
    ("＄3", 1),
    # Signs, points and commas alone
    ("+", 1),
    ("-", 1),
    (".", 1),
    (",", 1),
    # Thousands separators: anywhere before the point
    ("1,000", 1),
    ("1,2,3,4,5", 1),
    (",1", 1),
    ("0,", 1),
    ("0,,0", 1),
    ("333,444,555", 1),
    # Scientific notation
    ("1e5", 1),
    ("1E5", 1),
    ("1d5", 1),
    ("12D4", 1),
    ("0e+99", 1),
    ("1.5e-3", 1),
    ("-2e2", 1),
    ("e5", 0),
    ("e", 0),
    ("d", 0),
    ("1e", 0),
    ("1ee5", 0),
    # Spaces: around the value, not inside it
    (" 1", 1),
    ("1 ", 1),
    ("  1.5  ", 1),
    ("1 000", 0),
    ("0 0", 0),
    # Tabs and line breaks: before the value; after it they make it 0
    (TAB + "1", 1),
    (LF + "1", 1),
    (CR + "1", 1),
    (CR + LF + "  12", 1),
    ("1" + TAB, 0),
    ("1" + LF, 0),
    ("1" + CR, 0),
    # Vertical tab, form feed and non-breaking space: after the value, not before it
    ("0" + VT, 1),
    ("0" + FF, 1),
    ("0" + NBSP, 1),
    (VT + "0", 0),
    (FF + "0", 0),
    (NBSP + "0", 0),
    # Each alone is 1, as SQL Server reports
    (TAB, 1),
    (LF, 1),
    (VT, 1),
    (FF, 1),
    (CR, 1),
    (NBSP, 1),
]


@pytest.mark.parametrize("text, want", TEXT, ids=[repr(t) for t, _ in TEXT])
def test_isnumeric_of_text_from_a_table(engine, text, want):
    assert _isnumeric(engine, text) == want


LITERALS = [(t, w) for t, w in TEXT if t is not None]


@pytest.mark.parametrize("text, want", LITERALS, ids=[repr(t) for t, _ in LITERALS])
def test_isnumeric_of_a_literal(engine, text, want):
    sql = "SELECT ISNUMERIC('" + text + "')"
    assert engine.execute(sql, dialect="tsql")["rows"][0][0] == want


@pytest.mark.parametrize("sql, want", [
    ("SELECT ISNUMERIC(12)", 1),
    ("SELECT ISNUMERIC(12.5)", 1),
    ("SELECT ISNUMERIC(-1)", 1),
    ("SELECT ISNUMERIC(1e5)", 1),
    ("SELECT ISNUMERIC(CAST(1 AS BIGINT))", 1),
    ("SELECT ISNUMERIC(CAST(1 AS TINYINT))", 1),
    ("SELECT ISNUMERIC(CAST(1 AS DECIMAL(10, 2)))", 1),
    ("SELECT ISNUMERIC(CAST(1 AS FLOAT))", 1),
    ("SELECT ISNUMERIC(CAST(1 AS REAL))", 1),
    ("SELECT ISNUMERIC(CAST(1 AS BIT))", 1),
    ("SELECT ISNUMERIC(CAST(NULL AS INT))", 0),
    ("SELECT ISNUMERIC(NULL)", 0),
    ("SELECT ISNUMERIC(CAST('2024-01-01' AS DATE))", 0),
    ("SELECT ISNUMERIC(CAST('2024-01-01 10:00' AS DATETIME2))", 0),
    ("SELECT ISNUMERIC(CAST('10:00' AS TIME))", 0),
    ("SELECT ISNUMERIC(N'12')", 1),
    ("SELECT ISNUMERIC(N'£')", 1),
])
def test_isnumeric_by_type(engine, sql, want):
    got = engine.execute(sql, dialect="tsql")["rows"][0][0]
    assert got == want and type(got) is int


@pytest.mark.parametrize("text", [
    TAB + TAB, " " + TAB, TAB + " ", CR + LF,  # blanks with a tab or line break, more than one
    " $", "$ ", "-.", "$.", "+,", ",,", "..", "$-", "-$",  # no digits, and not one character alone
    "$-1", "-$1", "£+5",  # a currency symbol next to a sign
    "- 1", "$ 1", "+ 1e5",  # a space after a sign or currency symbol
    "1.0,0", "1.,5",  # a comma after the decimal point
    "1,000e5", "$1e5",  # a comma or currency symbol with an exponent
    "1e999", "1" * 400, "1e-400",  # too big or too small for a float
])
def test_isnumeric_refuses_text_with_no_documented_answer(engine, text):
    with pytest.raises(Exception, match="on the local engine: ISNUMERIC of this value has no documented answer"):
        _isnumeric(engine, text)


def test_isnumeric_reads_its_argument_once(engine):
    """A call nested in the argument isn't copied into each branch."""
    engine.execute("CREATE OR REPLACE TABLE dbo.isnum_words (w VARCHAR)")
    engine.execute("INSERT INTO dbo.isnum_words VALUES ('1,000'), ('x'), (NULL), ('$')")
    rows = engine.execute("SELECT ISNUMERIC(UPPER(LTRIM(w))) AS n FROM dbo.isnum_words ORDER BY w",
                          dialect="tsql")["rows"]
    assert sorted(r[0] for r in rows) == [0, 0, 1, 1]
    [sql], _ = translate("SELECT ISNUMERIC(UPPER(w)) FROM dbo.isnum_words")
    assert sql.upper().count("UPPER(") == 1


def test_isnumeric_in_a_where_clause(engine):
    engine.execute("CREATE OR REPLACE TABLE dbo.isnum_codes (code VARCHAR)")
    engine.execute("INSERT INTO dbo.isnum_codes VALUES ('98052'), ('K0A 1B0'), ('$'), ('1e5'), ('')")
    rows = engine.execute("SELECT code FROM dbo.isnum_codes WHERE ISNUMERIC(code) <> 1 ORDER BY code",
                          dialect="tsql")["rows"]
    assert rows == [[""], ["K0A 1B0"]]


def test_isnumeric_of_another_type_is_refused_by_name(engine):
    with pytest.raises(Exception, match="on the local engine: ISNUMERIC runs here on text, numbers"):
        engine.execute("SELECT ISNUMERIC(NEWID())", dialect="tsql")


def test_isnumeric_of_isnumeric(engine):
    assert engine.execute("SELECT ISNUMERIC(ISNUMERIC('x'))", dialect="tsql")["rows"] == [[1]]
