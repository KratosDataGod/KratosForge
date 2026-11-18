"""WR-229: T-SQL's FORMAT with a culture or a custom number format, and PARSE and TRY_PARSE, on the local engine.

Each answer is .NET's, as a warehouse's FORMAT and PARSE use it. What .NET's own versions write differently (the
'P' and negative 'C' formats, a culture's abbreviated names and AM/PM, a negative number that rounds to zero) is
refused by name, as is any culture but en-US, en-GB and de-DE. Nothing reaches Fabric.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("formatting") / "app" / "local-data")
    e = local.engine(local.create("Formatting")["id"])
    e.execute("CREATE TABLE dbo.n AS SELECT * FROM (VALUES (1, 1234.5), (2, -0.25), (3, NULL)) v(id, x)")
    e.execute("CREATE TABLE dbo.s AS SELECT * FROM (VALUES (1, '1.234,5'), (2, 'abc'), (3, NULL), (4, ' 7 ')) v(id, t)")
    return e


def one(engine, sql):
    return engine.execute(f"SELECT {sql} AS v", dialect="tsql")["rows"][0][0]


def refused(engine, sql):
    with pytest.raises(Exception) as caught:
        engine.execute(f"SELECT {sql} AS v", dialect="tsql")
    assert "on the local engine" in str(caught.value), str(caught.value)
    return str(caught.value)


DAY = "CAST('2024-03-05 14:07:09' AS DATETIME2)"

# (T-SQL expression, .NET's answer)
FORMATS = [
    # custom number formats
    ("FORMAT(1234567.891, '#,##0.00')", "1,234,567.89"),
    ("FORMAT(1234567.891, '#,#')", "1,234,568"),
    ("FORMAT(1234.5, '0.00')", "1234.50"),
    ("FORMAT(5, '000')", "005"),
    ("FORMAT(1234.5678, '0.##')", "1234.57"),
    ("FORMAT(12.3456, '0.0#0')", "12.346"),
    ("FORMAT(0.5, '#.##')", ".5"),
    ("FORMAT(0, '#.##')", ""),
    ("FORMAT(0, '0.##')", "0"),
    ("FORMAT(1.25, '0.0')", "1.3"),
    ("FORMAT(0.25, '0.0%')", "25.0%"),
    ("FORMAT(0.25, '%0')", "%25"),
    ("FORMAT(123456789, '000-00-0000')", "123-45-6789"),
    ("FORMAT(1234567890, '000-00-0000')", "1234-56-7890"),
    ("FORMAT(5, '000-00-0000')", "000-00-0005"),
    ("FORMAT(-5, '0')", "-5"),
    ("FORMAT(-5, '$0.00')", "-$5.00"),
    ("FORMAT(-5.5, '$#,##0.00;($#,##0.00)')", "($5.50)"),
    ("FORMAT(5.5, '$#,##0.00;($#,##0.00)')", "$5.50"),
    ("FORMAT(12, '00.00 \"kg\"')", "12.00 kg"),
    ("FORMAT(12, '0\\#')", "12#"),
    ("FORMAT(12, '0'' units''')", "12 units"),
    # standard number formats
    ("FORMAT(255, 'X')", "FF"),
    ("FORMAT(255, 'x4')", "00ff"),
    ("FORMAT(-1, 'X')", "FFFFFFFF"),
    ("FORMAT(CAST(-1 AS SMALLINT), 'X')", "FFFF"),
    ("FORMAT(CAST(-1 AS BIGINT), 'x')", "ffffffffffffffff"),
    ("FORMAT(CAST(200 AS TINYINT), 'X')", "C8"),
    ("FORMAT(1234.5, 'C')", "$1,234.50"),
    ("FORMAT(1234.5, 'C0', 'en-US')", "$1,235"),
    ("FORMAT(3000000000, 'N0')", "3,000,000,000"),
    # cultures
    ("FORMAT(1234.5, 'N', 'de-DE')", "1.234,50"),
    ("FORMAT(1234.5, 'N0', 'de-DE')", "1.235"),
    ("FORMAT(1234.5, 'F1', 'de-DE')", "1234,5"),
    ("FORMAT(1234, 'D6', 'de-DE')", "001234"),
    ("FORMAT(1234567.891, '#,##0.00', 'de-DE')", "1.234.567,89"),
    ("FORMAT(1234.5, 'N', 'en-GB')", "1,234.50"),
    ("FORMAT(1234.5, 'N', 'EN-gb')", "1,234.50"),
    (f"FORMAT({DAY}, 'd', 'en-GB')", "05/03/2024"),
    (f"FORMAT({DAY}, 'g', 'en-GB')", "05/03/2024 14:07"),
    (f"FORMAT({DAY}, 'T', 'en-GB')", "14:07:09"),
    (f"FORMAT({DAY}, 'dddd d MMMM yyyy', 'en-GB')", "Tuesday 5 March 2024"),
    (f"FORMAT({DAY}, 'd', 'de-DE')", "05.03.2024"),
    (f"FORMAT({DAY}, 'G', 'de-DE')", "05.03.2024 14:07:09"),
    (f"FORMAT({DAY}, 'dd/MM/yyyy', 'de-DE')", "05.03.2024"),
    (f"FORMAT({DAY}, 'dddd, d. MMMM yyyy', 'de-DE')", "Dienstag, 5. März 2024"),
    (f"FORMAT({DAY}, 'R', 'de-DE')", "Tue, 05 Mar 2024 14:07:09 GMT"),
    (f"FORMAT({DAY}, 's', 'de-DE')", "2024-03-05T14:07:09"),
    (f"FORMAT({DAY}, 'dd/MM/yyyy')", "05/03/2024"),
]


@pytest.mark.parametrize("sql, want", FORMATS, ids=[s for s, _ in FORMATS])
def test_format_gives_dotnets_answer(engine, sql, want):
    assert one(engine, sql) == want


def test_format_reads_each_row_and_keeps_null(engine):
    r = engine.execute("SELECT FORMAT(x, '#,##0.0;(#,##0.0)', 'de-DE') AS f FROM dbo.n ORDER BY id", dialect="tsql")
    assert r["rows"] == [["1.234,5"], ["(0,3)"], [None]]


@pytest.mark.parametrize("sql, says", [
    ("FORMAT(1234.5, 'N', 'fr-FR')", "the culture 'fr-FR' isn't carried yet"),
    ("FORMAT(1234.5, 'N', 'xx')", "'en-US' (a warehouse's default), 'en-GB' and 'de-DE'"),
    ("FORMAT(0.1234, 'P')", "'P' (percent)"),
    ("FORMAT(1234.5, 'C', 'de-DE')", "'C' (currency) outside 'en-US'"),
    ("FORMAT(1234.5, 'E2')", "number format 'E2'"),
    ("FORMAT(1234.5, 'G')", "number format 'G'"),
    ("FORMAT(1234.5, 'N123')", "at most two digits"),
    ("FORMAT(1.5, 'X')", "'X' format is for integers"),
    ("FORMAT(3000000000, 'X')", "'X' format is for integers"),
    ("FORMAT(12, '0;0;0')", "third section"),
    ("FORMAT(12, '0,')", "away from the digits"),
    ("FORMAT(12, '0E0')", "scientific notation"),
    ("FORMAT(12, '0.0‰')", "per mille"),
    ("FORMAT(12, '0%%')", "more than one %"),
    ("FORMAT(12, '0.0x0')", "other than 0 or #"),
    ("FORMAT(12, '#-0')", "0s only"),
    ("FORMAT(12, '00,000')", "more than one 0"),
    ("FORMAT(12, 'abc')", "no 0 or #"),
    ("FORMAT(12, '0''x')", "isn't closed"),
    ("FORMAT(12, '0\\')", "ends with a backslash"),
    (f"FORMAT({DAY}, 'D', 'de-DE')", "date format 'D' in 'de-DE'"),
    (f"FORMAT({DAY}, 'dd MMM yyyy', 'de-DE')", "abbreviated month name (MMM)"),
    (f"FORMAT({DAY}, 'ddd', 'en-GB')", "abbreviated day name (ddd)"),
    (f"FORMAT({DAY}, 'hh:mm tt', 'en-GB')", "AM/PM (t)"),
    ("FORMAT(1.5, 'N', CAST('de-DE' AS VARCHAR(5)))", "written in the query"),
])
def test_format_that_differs_between_dotnet_versions_or_isnt_carried_is_refused(engine, sql, says):
    assert says in refused(engine, sql)


@pytest.mark.parametrize("sql", ["FORMAT(-1234.5, 'C')", "FORMAT(-0.001, '0.00')", "FORMAT(-0.001, '0.00;(0.00)')",
                                 "FORMAT(-0.001, 'N2')", "FORMAT(-0.004, 'F2')", "FORMAT(-0.4, 'N0')",
                                 "FORMAT(-0.001, 'N2', 'de-DE')"])
def test_a_negative_number_dotnet_versions_write_differently_stops_by_name(engine, sql):
    refused(engine, sql)


@pytest.mark.parametrize("sql, expected", [("FORMAT(-0.006, 'N2')", "-0.01"), ("FORMAT(-0.5, 'N0')", "-1"),
                                           ("FORMAT(0.0, 'F2')", "0.00"), ("FORMAT(-0.004, 'F3')", "-0.004")])
def test_a_negative_number_that_doesnt_round_to_zero_keeps_its_sign(engine, sql, expected):
    assert one(engine, sql) == expected


PARSES = [
    ("PARSE('345.98' AS DECIMAL(10,2))", "345.98"),
    ("PARSE('345,98' AS DECIMAL(10,2) USING 'de-DE')", "345.98"),
    ("PARSE('1.234,5' AS DECIMAL(10,2) USING 'de-DE')", "1234.50"),
    ("PARSE('1,234.5' AS DECIMAL(10,2) USING 'en-GB')", "1234.50"),
    ("PARSE(' -1,234 ' AS INT)", -1234),
    ("PARSE('+12' AS BIGINT)", 12),
    ("PARSE('.5' AS FLOAT)", 0.5),
    ("PARSE('5.' AS FLOAT)", 5.0),
    ("PARSE('12' AS INT) + 1", 13),
    ("TRY_PARSE('12' AS INT)", 12),
    ("TRY_PARSE('abc' AS INT)", None),
    ("TRY_PARSE('' AS INT)", None),
    ("TRY_PARSE(NULL AS INT)", None),
    ("TRY_PARSE('99999999999' AS INT)", None),
]


@pytest.mark.parametrize("sql, want", PARSES, ids=[s for s, _ in PARSES])
def test_parse_reads_a_number_in_the_culture(engine, sql, want):
    got = one(engine, sql)
    assert (str(got) if isinstance(want, str) else got) == want


def test_parse_reads_each_row(engine):
    r = engine.execute("SELECT TRY_PARSE(t AS DECIMAL(10,2) USING 'de-DE') AS v FROM dbo.s ORDER BY id", dialect="tsql")
    assert [[None if v is None else str(v)] for (v,) in r["rows"]] == [["1234.50"], [None], [None], ["7.00"]]


def test_parse_of_text_that_isnt_a_number_stops_with_tsqls_error(engine):
    with pytest.raises(Exception, match="Error converting string value 'abc' into data type int using culture"):
        one(engine, "PARSE('abc' AS INT)")


@pytest.mark.parametrize("sql, says", [
    ("TRY_PARSE('12.5' AS INT)", "TRY_PARSE of '12.5' isn't carried yet"),
    ("TRY_PARSE('$12' AS INT)", "TRY_PARSE of '$12'"),
    ("TRY_PARSE('12-' AS INT)", "TRY_PARSE of '12-'"),
    ("TRY_PARSE('(12)' AS INT)", "TRY_PARSE of '(12)'"),
    ("TRY_PARSE('1.5e3' AS FLOAT)", "TRY_PARSE of '1.5e3'"),
    ("TRY_PARSE('Infinity' AS FLOAT)", "TRY_PARSE of 'Infinity'"),
    ("PARSE('1,2' AS INT)", "PARSE of '1,2'"),
    ("PARSE('1.234,5' AS DECIMAL(10,2))", "PARSE of '1.234,5'"),
])
def test_parse_of_text_dotnet_may_read_another_way_stops_by_name(engine, sql, says):
    assert says in refused(engine, sql)


@pytest.mark.parametrize("sql, says", [
    ("PARSE('Monday, 13 December 2010' AS DATETIME2 USING 'en-US')", "PARSE to a date or time"),
    ("TRY_PARSE('13/12/2010' AS DATE USING 'en-GB')", "TRY_PARSE to a date or time"),
    ("PARSE('€345,98' AS MONEY USING 'de-DE')", "MONEY or SMALLMONEY"),
    ("PARSE('1' AS BIT)", "BIT isn't one"),
    ("PARSE('12' AS INT USING 'fr-FR')", "the culture 'fr-FR'"),
])
def test_parse_that_isnt_carried_is_refused_by_name(engine, sql, says):
    assert says in refused(engine, sql)
