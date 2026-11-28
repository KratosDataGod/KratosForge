"""Silent differences in local T-SQL text lengths, round 3.

- WR-723: a CHAR(n) value is padded with spaces to n bytes, as a warehouse stores it (statements/
  set-ansi-padding-transact-sql: char columns are padded "to the length of the column" under ON and OFF alike).
  DATALENGTH gives n, + and CONCAT and CAST(... AS VARCHAR) keep the spaces, LEN drops them. A database table's
  char column, and CAST and CONVERT to CHAR(n) and NCHAR(n), are padded; a Delta table's text isn't (a lakehouse's
  SQL analytics endpoint reads a Delta CHAR(n) as varchar(n), fabric/data-warehouse/data-types).
- WR-724: varchar(n) and char(n) hold n bytes of UTF-8 (data-types/char-and-varchar: "n defines the string length
  in bytes"). A cut to n bytes runs on ASCII text, and text past ASCII that doesn't fit is refused by name, since
  the reference doesn't say how a split character is cut. DATALENGTH of nvarchar and nchar counts two bytes a UTF-16
  code unit. ASCII gives the first UTF-8 byte (functions/ascii-transact-sql, example B: ASCII('æ') = 195,
  UNICODE('æ') = 230), and CHAR of 128 to 255 is refused by name.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Silent sweep 3")["id"])
    e.execute("CREATE TABLE dbo.c (id INT, c CHAR(4), v VARCHAR(4), u VARCHAR(10))", dialect="tsql")
    e.execute("INSERT INTO dbo.c VALUES (1, 'x', 'x', 'æ'), (2, 'é', 'é', '߿'), (3, NULL, NULL, NULL), "
              "(4, 'ab  ', 'ab  ', 'ࠀ'), (5, 'abcd', 'abcd', '😀')", dialect="tsql")
    e.execute("CREATE TABLE dbo.dc USING DELTA AS SELECT id, c FROM dbo.c", dialect="spark")
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def one(engine, sql):
    return rows(engine, sql)[0][0]


# ---------- WR-723: CHAR(n) is padded to n

def test_a_char_column_reads_padded_to_its_length(engine):
    got = rows(engine, "SELECT c, DATALENGTH(c), LEN(c), c + '|', CONCAT(c, '|'), CAST(c AS VARCHAR(10)) + '|' "
                       "FROM dbo.c ORDER BY id")
    assert got == [["x   ", 4, 1, "x   |", "x   |", "x   |"],
                   ["é  ", 4, 1, "é  |", "é  |", "é  |"],  # 'é' is two bytes, and two spaces make four
                   [None, None, None, None, "|", None],
                   ["ab  ", 4, 2, "ab  |", "ab  |", "ab  |"],
                   ["abcd", 4, 4, "abcd|", "abcd|", "abcd|"]]


def test_a_varchar_column_is_not_padded(engine):
    assert rows(engine, "SELECT DATALENGTH(v) FROM dbo.c ORDER BY id") == [[1], [2], [None], [4], [4]]


def test_comparisons_group_by_and_join_keep_their_rows(engine):
    assert one(engine, "SELECT COUNT(*) FROM dbo.c WHERE c = 'x'") == 1
    assert rows(engine, "SELECT c, COUNT(*) FROM dbo.c WHERE id IN (1, 4) GROUP BY c ORDER BY c") == [
        ["ab  ", 1], ["x   ", 1]]
    assert rows(engine, "SELECT a.id FROM dbo.c AS a JOIN dbo.c AS b ON a.c = b.v ORDER BY a.id") == [
        [1], [2], [4], [5]]


def test_select_star_cte_subquery_and_view_read_it_padded(engine):
    assert rows(engine, "SELECT * FROM dbo.c WHERE id = 1") == [[1, "x   ", "x", "æ"]]
    assert one(engine, "WITH q AS (SELECT c FROM dbo.c WHERE id = 1) SELECT DATALENGTH(c) FROM q") == 4
    assert one(engine, "SELECT DATALENGTH(x.c) FROM (SELECT c FROM dbo.c WHERE id = 1) AS x") == 4
    engine.execute("CREATE VIEW dbo.cv AS SELECT id, c FROM dbo.c", dialect="tsql")
    assert one(engine, "SELECT DATALENGTH(c) FROM dbo.cv WHERE id = 1") == 4


def test_a_char_value_copied_to_a_varchar_keeps_its_spaces(engine):
    engine.execute("CREATE TABLE dbo.copy (v VARCHAR(10))", dialect="tsql")
    engine.execute("INSERT INTO dbo.copy SELECT c FROM dbo.c WHERE id = 1", dialect="tsql")
    assert one(engine, "SELECT DATALENGTH(v) FROM dbo.copy") == 4


def test_isnull_of_a_char_column_pads_its_replacement(engine):
    assert one(engine, "SELECT ISNULL(c, 'zz') + '|' FROM dbo.c WHERE id = 3") == "zz  |"


def test_a_delta_tables_text_is_read_as_stored(engine):
    # A lakehouse's SQL analytics endpoint reads a Delta CHAR(n) as varchar(n): no padding.
    assert rows(engine, "SELECT DATALENGTH(c), c + '|' FROM dbo.dc ORDER BY id") == [
        [1, "x|"], [2, "é|"], [None, None], [4, "ab  |"], [4, "abcd|"]]


@pytest.mark.parametrize("expression, want", [
    ("CAST('ab' AS CHAR(4)) + '|'", "ab  |"),
    ("CONVERT(CHAR(4), 'ab') + '|'", "ab  |"),
    ("CAST('é' AS CHAR(3)) + '|'", "é |"),
    ("DATALENGTH(CAST('é' AS CHAR(3)))", 3),
    ("DATALENGTH(CAST('ab   ' AS CHAR(3)))", 3),
    ("CAST('abcdef' AS CHAR(3))", "abc"),
    ("CAST(NULL AS CHAR(3))", None),
    ("CAST('abcdef' AS VARCHAR(3))", "abc"),
    ("CAST('ééé' AS VARCHAR(6))", "ééé"),
])
def test_cast_to_char_and_varchar_cuts_and_pads_by_bytes(engine, expression, want):
    assert one(engine, f"SELECT {expression}") == want


def test_cast_of_a_column_to_char_pads_by_bytes(engine):
    assert rows(engine, "SELECT CAST(v AS CHAR(3)) + '|' FROM dbo.c ORDER BY id") == [
        ["x  |"], ["é |"], [None], ["ab |"], ["abc|"]]


# ---------- WR-724: n is bytes of UTF-8

@pytest.mark.parametrize("expression, n", [
    ("CAST('ééé' AS VARCHAR(5))", 5),
    ("CAST('ééé' AS CHAR(4))", 4),
    ("CONVERT(VARCHAR(1), 'é')", 1),
])
def test_cutting_written_text_past_ascii_is_refused_before_it_runs(engine, expression, n):
    with pytest.raises(ValueError, match=f"cutting text with characters past ASCII to {n} bytes isn't mapped yet"):
        engine.execute(f"SELECT {expression}", dialect="tsql")


def test_cutting_a_column_past_ascii_stops_at_that_value(engine):
    assert rows(engine, "SELECT CAST(u AS VARCHAR(2)) FROM dbo.c WHERE id IN (1, 2, 3) ORDER BY id") == [
        ["æ"], ["߿"], [None]]  # two bytes each: they fit
    with pytest.raises(Exception, match="cutting text with characters past ASCII to 1 bytes isn't mapped yet"):
        engine.execute("SELECT CAST(u AS VARCHAR(1)) FROM dbo.c WHERE id = 1", dialect="tsql")


def test_isnull_replacement_past_ascii_too_long_for_the_column_is_refused(engine):
    with pytest.raises(ValueError, match="past ASCII to 4 bytes"):
        engine.execute("SELECT ISNULL(v, 'ééé') FROM dbo.c WHERE id = 3", dialect="tsql")


def test_the_column_check_counts_bytes(engine):
    with pytest.raises(Exception, match="String or binary data would be truncated"):
        engine.execute("INSERT INTO dbo.c (id, v) VALUES (9, 'ééé')", dialect="tsql")
    assert one(engine, "SELECT COUNT(*) FROM dbo.c WHERE id = 9") == 0


@pytest.mark.parametrize("expression, want", [
    ("DATALENGTH(N'ab')", 4),
    ("DATALENGTH(N'é😀')", 6),  # 😀 is two UTF-16 code units
    ("DATALENGTH('é😀')", 6),   # and four UTF-8 bytes
    ("DATALENGTH(CAST('ab' AS NVARCHAR(4)))", 4),
    ("DATALENGTH(CAST('ab' AS NCHAR(4)))", 8),
    ("DATALENGTH(CAST('abcdef' AS NVARCHAR(4)))", 8),
    ("DATALENGTH(CONVERT(NVARCHAR(10), 'é😀'))", 6),
    ("DATALENGTH(CAST(NULL AS NVARCHAR(4)))", None),
    ("DATALENGTH(CAST('a' AS NCHAR))", 60),  # no length in CAST is 30
])
def test_datalength_of_nvarchar_counts_utf16_bytes(engine, expression, want):
    assert one(engine, f"SELECT {expression}") == want


def test_datalength_of_a_column_cast_to_nvarchar(engine):
    assert rows(engine, "SELECT DATALENGTH(CAST(u AS NVARCHAR(10))) FROM dbo.c ORDER BY id") == [
        [2], [2], [None], [2], [4]]


def test_datalength_of_other_nvarchar_text_is_refused(engine):
    with pytest.raises(ValueError, match="DATALENGTH of an expression with nvarchar in it isn't mapped yet"):
        engine.execute("SELECT DATALENGTH(N'a' + N'b')", dialect="tsql")


def test_cutting_a_character_past_uffff_to_nvarchar_is_refused(engine):
    with pytest.raises(Exception, match="past U\\+FFFF to 1 UTF-16 code units isn't mapped yet"):
        engine.execute("SELECT DATALENGTH(CAST('😀' AS NVARCHAR(1)))", dialect="tsql")


@pytest.mark.parametrize("expression, want", [
    ("ASCII('æ')", 195), ("UNICODE('æ')", 230),  # Microsoft's example B
    ("ASCII('A')", 65), ("ASCII('é')", 195), ("ASCII('😀')", 240), ("ASCII(N'æ')", 195),
    ("ASCII('')", None), ("ASCII(NULL)", None),
])
def test_ascii_is_the_first_utf8_byte(engine, expression, want):
    assert one(engine, f"SELECT {expression}") == want


def test_ascii_of_a_column_is_the_first_utf8_byte(engine):
    # æ U+00E6, ߿ U+07FF (the last two-byte one), ࠀ U+0800 (the first three-byte one), 😀 U+1F600
    assert rows(engine, "SELECT ASCII(u), UNICODE(u), ASCII(c) FROM dbo.c ORDER BY id") == [
        [195, 230, 120], [223, 2047, 195], [None, None, None], [224, 2048, 97], [240, 128512, 97]]


def test_char_runs_to_127_and_is_null_past_255(engine):
    assert rows(engine, "SELECT CHAR(65), CHAR(127), CHAR(256), CHAR(-1)") == [["A", "\x7f", None, None]]
    assert rows(engine, "SELECT CHAR(id * 20) FROM dbo.c WHERE id IN (3, 5) ORDER BY id") == [["<"], ["d"]]


def test_char_of_128_to_255_is_refused(engine):
    with pytest.raises(ValueError, match="CHAR of 200 isn't mapped yet"):
        engine.execute("SELECT CHAR(200)", dialect="tsql")
    with pytest.raises(Exception, match="CHAR of a code from 128 to 255 isn't mapped yet"):
        engine.execute("SELECT CHAR(id * 50) FROM dbo.c WHERE id = 3", dialect="tsql")
