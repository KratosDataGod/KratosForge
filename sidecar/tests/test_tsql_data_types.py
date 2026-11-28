"""WR-223: data types as Microsoft's "Data types in Fabric Data Warehouse" lists them. A table or view that would
keep a type a warehouse table can't hold is refused by name with Microsoft's alternative; a query can still use
those types where the local engine gives T-SQL's value (datetime rounded to 1/300 of a second), and the types it
has no form of are refused by name. Each supported type keeps its exact value through a local Delta table.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata.tsql import translate

UNSUPPORTED = [  # (as written, the name in the message, words of the alternative)
    ("MONEY", "money", "decimal(19, 4)"), ("SMALLMONEY", "smallmoney", "decimal(10, 4)"),
    ("DATETIME", "datetime", "datetime2(6)"), ("SMALLDATETIME", "smalldatetime", "datetime2(0)"),
    ("DATETIMEOFFSET", "datetimeoffset", "AT TIME ZONE"), ("DATETIMEOFFSET(3)", "datetimeoffset", "datetime2(6)"),
    ("NCHAR(10)", "nchar", "char"), ("NVARCHAR(50)", "nvarchar", "varchar"), ("NVARCHAR(MAX)", "nvarchar", "varchar"),
    ("TEXT", "text", "varchar(max)"), ("NTEXT", "ntext", "varchar(max)"), ("IMAGE", "image", "varbinary(max)"),
    ("TINYINT", "tinyint", "smallint"), ("GEOGRAPHY", "geography", "well-known"), ("GEOMETRY", "geometry", "well-known"),
    ("JSON", "json", "varchar(max)"), ("XML", "xml", "varchar(max)"), ("SQL_VARIANT", "sql_variant", "one type"),
    ("ROWVERSION", "rowversion", "bigint"), ("TIMESTAMP", "rowversion", "bigint"),
    ("HIERARCHYID", "hierarchyid", "path"), ("VECTOR(3)", "vector", "varbinary"),
    ("dbo.Phone", "dbo.phone", "user-defined"), ("SYSNAME", "sysname", "varchar(128)"),
]
PERSISTED = [
    "CREATE TABLE dbo.t (id INT, c {t})",
    "CREATE TABLE dbo.t (id INT NOT NULL, c {t} NULL)",
    "ALTER TABLE dbo.t ADD c {t}",
    "ALTER TABLE dbo.t ALTER COLUMN c {t}",
    "SELECT CAST(NULL AS {t}) AS c INTO dbo.copy",
    "CREATE TABLE dbo.c AS SELECT CAST(NULL AS {t}) AS c",
    "CREATE VIEW dbo.v AS SELECT CAST(NULL AS {t}) AS c",
    "CREATE VIEW dbo.v AS SELECT 1 AS id UNION ALL SELECT CONVERT({t}, NULL) AS c",
]


@pytest.mark.parametrize("written, name, alternative", UNSUPPORTED)
@pytest.mark.parametrize("statement", PERSISTED)
def test_a_table_or_view_of_an_unsupported_type_is_refused_with_microsofts_alternative(statement, written, name,
                                                                                       alternative):
    with pytest.raises(ValueError) as err:
        translate(statement.format(t=written))
    message = str(err.value)
    if name in ("ntext",):  # sqlglot reads ntext as text: one message for both
        name = "text or ntext"
    assert f"The data type {name}" in message and "isn't supported in a Fabric Warehouse table or view" in message
    assert "(column c)" in message and alternative in message


@pytest.mark.parametrize("written", ["DATETIME2", "DATETIME2(7)", "TIME", "TIME(7)", "datetime2"])
@pytest.mark.parametrize("statement", PERSISTED[:6])
def test_seven_digits_of_a_second_are_refused_in_a_table(statement, written):
    with pytest.raises(ValueError, match=r"keeps at most 6 \(column c\)") as err:
        translate(statement.format(t=written))
    assert f"{written.split('(')[0].lower()}(6)" in str(err.value)


@pytest.mark.parametrize("written", [
    "BIT", "SMALLINT", "INT", "BIGINT", "DECIMAL(38, 10)", "NUMERIC(5, 2)", "DECIMAL", "FLOAT", "FLOAT(24)", "REAL",
    "DATE", "TIME(6)", "TIME(0)", "DATETIME2(6)", "DATETIME2(3)", "DATETIME2(0)", "CHAR(10)", "VARCHAR(8000)",
    "VARCHAR(MAX)", "VARBINARY(8000)", "VARBINARY(MAX)", "UNIQUEIDENTIFIER",
])
def test_each_supported_type_makes_a_table(written):
    translate(f"CREATE TABLE dbo.t (id INT, c {written})")
    translate(f"CREATE TABLE dbo.c AS SELECT CAST(NULL AS {written}) AS c")


@pytest.mark.parametrize("written", ["XML", "GEOGRAPHY", "GEOMETRY", "JSON", "SQL_VARIANT", "ROWVERSION",
                                     "HIERARCHYID", "DATETIMEOFFSET", "DATETIMEOFFSET(7)", "VECTOR(3)", "dbo.Phone"])
@pytest.mark.parametrize("statement", ["SELECT CAST(NULL AS {t}) AS c", "SELECT TRY_CONVERT({t}, NULL) AS c",
                                       "CREATE TABLE #t (c {t})", "SELECT CAST(NULL AS {t}) AS c INTO #t"])
def test_a_type_with_no_local_form_is_refused_by_name_in_a_query(statement, written):
    with pytest.raises(ValueError) as err:
        translate(statement.format(t=written))
    message = str(err.value)
    assert message.startswith("This T-SQL can't be run on the local engine: the data type ")
    assert "isn't mapped yet. The local engine has no form of it" in message and "Fabric Warehouse" in message


@pytest.mark.parametrize("sql", ["SELECT CAST(1 AS MONEY) AS c", "CREATE TABLE #t (c SMALLMONEY)"])
def test_money_in_a_query_is_still_refused_by_name(sql):
    with pytest.raises(ValueError, match="MONEY and SMALLMONEY aren't carried yet"):
        translate(sql)


@pytest.mark.parametrize("sql, message", [
    ("SELECT CAST('a' AS VARCHAR(8001)) AS c", ("The size (8001) given to the type 'varchar' exceeds the maximum "
                                               "allowed for any data type (8000).")),
    ("SELECT CAST(0x01 AS VARBINARY(9000)) AS c", ("The size (9000) given to the type 'varbinary' exceeds the "
                                                  "maximum allowed for any data type (8000).")),
    ("SELECT CAST(N'a' AS NVARCHAR(4001)) AS c", ("The size (4001) given to the type 'nvarchar' exceeds the maximum "
                                                 "allowed for any data type (4000).")),
    ("CREATE TABLE dbo.t (c VARCHAR(8001))", ("The size (8001) given to the column 'c' exceeds the maximum allowed "
                                             "for any data type (8000).")),
    ("CREATE TABLE dbo.t (c CHAR(0))", "Length or precision specification 0 is invalid."),
    ("SELECT CAST(1 AS DECIMAL(39, 2)) AS c", "decimal(39) has more digits than T-SQL's decimal holds"),
    ("SELECT CAST(1 AS DECIMAL(5, 6)) AS c", "has a scale larger than its precision"),
    ("SELECT CAST('10:00' AS TIME(8)) AS c", "Specified scale 8 is invalid."),
    ("SELECT CAST('2024-01-01' AS DATETIME2(9)) AS c", "Specified scale 9 is invalid."),
])
def test_a_size_tsql_doesnt_take_stops(sql, message):
    with pytest.raises(ValueError) as err:
        translate(sql)
    assert message in str(err.value)


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("app") / "local-data")
    e = local.engine(local.create("Data types")["id"])
    yield e
    local.close_all()


def rows(engine, sql):
    return [list(r) for r in engine.execute(sql, dialect="tsql")["rows"]]


def one(engine, sql):
    return rows(engine, sql)[0][0]


@pytest.mark.parametrize("value, written, expected", [
    # datetime is kept in steps of 1/300 of a second, shown as .000, .003 and .007 (Microsoft Learn, "datetime").
    ("2024-01-01 10:00:00.001", "DATETIME", "2024-01-01 10:00:00"),
    ("2024-01-01 10:00:00.002", "DATETIME", "2024-01-01 10:00:00.003000"),
    ("2024-01-01 10:00:00.004", "DATETIME", "2024-01-01 10:00:00.003000"),
    ("2024-01-01 10:00:00.005", "DATETIME", "2024-01-01 10:00:00.007000"),
    ("2024-01-01 10:00:00.008", "DATETIME", "2024-01-01 10:00:00.007000"),
    ("2024-01-01 10:00:00.009", "DATETIME", "2024-01-01 10:00:00.010000"),
    ("2024-01-01 23:59:59.999", "DATETIME", "2024-01-02 00:00:00"),
    ("1900-01-01", "DATETIME", "1900-01-01 00:00:00"),
    ("1753-01-01", "DATETIME", "1753-01-01 00:00:00"),
    ("9999-12-31 23:59:59.997", "DATETIME", "9999-12-31 23:59:59.997000"),
    # smalldatetime: 29.998 seconds or less rounds down to the minute, 29.999 or more up.
    ("2024-01-01 10:00:29.998", "SMALLDATETIME", "2024-01-01 10:00:00"),
    ("2024-01-01 10:00:29.999", "SMALLDATETIME", "2024-01-01 10:01:00"),
    ("2024-01-01 23:59:59", "SMALLDATETIME", "2024-01-02 00:00:00"),
    ("2079-06-06 23:59", "SMALLDATETIME", "2079-06-06 23:59:00"),
])
def test_datetime_in_a_query_rounds_as_tsql(engine, value, written, expected):
    assert str(one(engine, f"SELECT CAST('{value}' AS {written}) AS c")) == expected
    assert str(one(engine, f"SELECT CONVERT({written}, '{value}') AS c")) == expected
    assert str(one(engine, f"SELECT CAST(CAST('{value}' AS DATETIME2(3)) AS {written}) AS c")) == expected


def test_datetime_before_1970_rounds_from_midnight(engine):
    assert str(one(engine, "SELECT CAST(CAST('1969-12-31 23:59:59.9989' AS DATETIME2) AS DATETIME) AS c")) == \
        "1970-01-01 00:00:00"
    assert str(one(engine, "SELECT CAST(CAST('1960-05-05 10:00:00.0049' AS DATETIME2) AS DATETIME) AS c")) == \
        "1960-05-05 10:00:00.003000"


@pytest.mark.parametrize("value, written, source", [
    ("1752-12-31", "DATETIME", "varchar"), ("9999-12-31 23:59:59.999", "DATETIME", "varchar"),
    ("1899-12-31", "SMALLDATETIME", "varchar"), ("2079-06-07", "SMALLDATETIME", "varchar"),
])
def test_datetime_out_of_range_is_tsqls_error_or_null(engine, value, written, source):
    with pytest.raises(Exception, match=f"The conversion of a {source} data type to a {written.lower()} data type "
                                        "resulted in an out-of-range value."):
        engine.execute(f"SELECT CAST('{value}' AS {written}) AS c", dialect="tsql")
    assert one(engine, f"SELECT TRY_CAST('{value}' AS {written}) AS c") is None
    assert one(engine, f"SELECT TRY_CONVERT({written}, '{value}') AS c") is None


def test_more_than_three_digits_of_a_second_isnt_a_datetime(engine):
    with pytest.raises(Exception, match="Conversion failed when converting date and/or time from character string"):
        engine.execute("SELECT CAST('2024-01-01 10:00:00.1234' AS DATETIME) AS c", dialect="tsql")
    assert one(engine, "SELECT TRY_CAST('2024-01-01 10:00:00.1234' AS DATETIME) AS c") is None


@pytest.mark.parametrize("sql, expected", [
    ("SELECT CONVERT(VARCHAR(30), CAST('2024-01-01 10:00:00.002' AS DATETIME), 121) AS c", "2024-01-01 10:00:00.003"),
    ("SELECT CONVERT(VARCHAR(30), CAST('2024-01-01 10:00:00.002' AS DATETIME), 120) AS c", "2024-01-01 10:00:00"),
    ("SELECT CONVERT(VARCHAR(30), CAST('2024-01-01 10:00:00.002' AS DATETIME), 126) AS c", "2024-01-01T10:00:00.003"),
    ("SELECT CONVERT(VARCHAR(8), CAST('2024-01-01 23:59:59.999' AS DATETIME), 112) AS c", "20240102"),
    ("SELECT CONVERT(DATE, CAST('2024-01-01 23:59:59.999' AS DATETIME)) AS c", "2024-01-02"),
    ("SELECT CONVERT(VARCHAR(30), CAST('2024-01-01 10:00:29.999' AS SMALLDATETIME), 120) AS c", "2024-01-01 10:01:00"),
    ("SELECT CONVERT(VARCHAR(30), CONVERT(DATETIME, '2024-02-29 10:00:00.002', 121), 121) AS c",
     "2024-02-29 10:00:00.003"),
    # 29.998 is .997 as a datetime, which then rounds down to the minute; 29.999 is 30.000, which rounds up.
    ("SELECT CAST(CAST('2024-01-01 10:00:29.998' AS DATETIME) AS SMALLDATETIME) AS c", "2024-01-01 10:00:00"),
    ("SELECT CAST(CAST('2024-01-01 10:00:29.999' AS DATETIME) AS SMALLDATETIME) AS c", "2024-01-01 10:01:00"),
    ("SELECT CONVERT(VARCHAR(30), CAST('2024-01-01 10:00:00' AS DATETIME), 126) AS c", "2024-01-01T10:00:00"),
    ("SELECT CAST(CAST(CAST('2024-01-01 10:00:00.0049' AS DATETIME2) AS DATETIME) AS DATETIME) AS c",
     "2024-01-01 10:00:00.003000"),
    ("SELECT TRY_CAST(TRY_CAST('2024-01-01 10:00:00.002' AS DATETIME) AS SMALLDATETIME) AS c", "2024-01-01 10:00:00"),
])
def test_a_rounded_datetime_inside_another_conversion_keeps_its_rounding(engine, sql, expected):
    assert str(one(engine, sql)) == expected


def test_the_rounding_adds_no_note():
    _, notes = translate("SELECT CAST('2024-01-01 10:00:00.002' AS DATETIME) AS a, "
                         "DATEADD(day, 1, CONVERT(DATETIME, '02/29/2024', 101)) AS b")
    assert notes == []


def test_types_with_the_same_values_here_work_in_a_query(engine):
    got = rows(engine, "SELECT CAST(N'é€' AS NVARCHAR(10)) AS a, CAST('ab' AS NCHAR(4)) AS b, CAST('x' AS NTEXT) AS c, "
                       "CAST(255 AS TINYINT) AS d, CAST(0x0AFF AS IMAGE) AS e, CAST('t' AS SYSNAME) AS f")[0]
    assert got[:4] == ["é€", "ab  ", "x", 255] and got[5] == "t"
    with pytest.raises(Exception, match="out of range"):  # tinyint is 0 to 255, as in T-SQL
        engine.execute("SELECT CAST(256 AS TINYINT) AS c", dialect="tsql")


def test_a_temp_table_takes_them_with_a_note(engine):
    _, notes = translate("CREATE TABLE #t (a NVARCHAR(10), b TINYINT)")
    assert any("#temp tables lists only the types its tables hold" in n and "nvarchar, tinyint" in n
               and "smallint for tinyint" in n and "for nvarchar" in n for n in notes)
    assert rows(engine, "CREATE TABLE #n (a NVARCHAR(10), b TINYINT);\nINSERT INTO #n VALUES (N'é', 255);\n"
                        "SELECT a, b FROM #n") == [["é", 255]]


@pytest.mark.parametrize("written", ["DATETIME", "SMALLDATETIME"])
def test_a_temp_table_of_datetime_is_refused_since_stored_values_arent_rounded(written):
    with pytest.raises(ValueError, match=f"a {written.lower()} column in a #temp table isn't mapped yet. Here a stored value isn't rounded"):
        translate(f"CREATE TABLE #t (c {written})")


# ---------- a round trip through a local Delta table keeps each supported type's exact value

COLUMNS = ("b BIT, s SMALLINT, i INT, g BIGINT, d DECIMAL(38, 10), n NUMERIC(5, 2), small DECIMAL(9, 9), f FLOAT, "
           "r REAL, dt DATE, t6 DATETIME2(6), t3 DATETIME2(3), t0 DATETIME2(0), c CHAR(5), v VARCHAR(10), "
           "vm VARCHAR(MAX), vb VARBINARY(8), vbm VARBINARY(MAX), u UNIQUEIDENTIFIER")
VALUES = [
    ("1, -32768, -2147483648, -9223372036854775808, -1234567890123456789012345678.0123456789, -999.99, -0.999999999, "
     "-1.7976931348623157E308, CAST(-3.4028235E38 AS REAL), '0001-01-01', '0001-01-01 00:00:00.000001', "
     "'1900-01-01 00:00:00.001', '1753-01-01', 'a', '', '', 0x00, 0x01, '00000000-0000-0000-0000-000000000000'"),
    ("0, 32767, 2147483647, 9223372036854775807, 1234567890123456789012345678.0123456789, 999.99, 0.000000001, "
     "2.2250738585072014E-308, CAST(1.17549435E-38 AS REAL), '9999-12-31', '9999-12-31 23:59:59.999999', "
     "'2024-02-29 12:34:56.789', '2024-02-29 12:34:56', 'abcde', 'é€😀', REPLICATE(CAST('é' AS VARCHAR(MAX)), 9000), "
     "0x00FF10AB00FF10AB, 0xDEADBEEF, '6F9619FF-8B86-D011-B42D-00C04FC964FF'"),
    ("NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
     "NULL"),
]
READ = ("SELECT b, s, i, g, d, n, small, f, r, dt, t6, t3, t0, RTRIM(c) AS c, v, vm, LEN(vm) AS vm_len, "
        "DATALENGTH(vm) AS vm_bytes, vb, vbm, u FROM {t} ORDER BY g")


@pytest.fixture(scope="module")
def trip(engine):
    engine.execute(f"CREATE TABLE dbo.typed ({COLUMNS})", dialect="tsql")
    for value in VALUES:
        engine.execute(f"INSERT INTO dbo.typed VALUES ({value})", dialect="tsql")
    engine.execute("CREATE TABLE dbo.typed_delta USING DELTA AS SELECT * FROM dbo.typed", dialect="spark")
    return engine


def test_each_supported_type_keeps_its_value_through_a_delta_table(trip):
    before = trip.execute(READ.format(t="dbo.typed"), dialect="tsql")
    after = trip.execute(READ.format(t="dbo.typed_delta"), dialect="tsql")
    assert after["rows"] == before["rows"]
    assert len(after["rows"]) == 3
    nulls, low, high = (dict(zip([c["name"] for c in after["columns"]], r, strict=True)) for r in after["rows"])
    assert str(high["d"]) == "1234567890123456789012345678.0123456789" and str(low["small"]) == "-0.999999999"
    assert str(high["t6"]) == "9999-12-31 23:59:59.999999" and str(low["t6"]) == "0001-01-01 00:00:00.000001"
    assert high["vm_len"] == 9000 and high["vm_bytes"] == 18000 and high["v"] == "é€😀"
    assert str(high["u"]).upper() == "6F9619FF-8B86-D011-B42D-00C04FC964FF"
    assert {k: v for k, v in nulls.items() if v is not None} == {}  # LEN and DATALENGTH of NULL are NULL


def test_precision_and_scale_are_kept(trip):
    def kinds(table):
        return {c["name"]: c["type"] for c in trip.execute(f"SELECT d, n, small, r, f FROM {table}",
                                                           dialect="tsql")["columns"]}
    assert kinds("dbo.typed_delta") == kinds("dbo.typed")
    assert kinds("dbo.typed_delta")["d"] == "decimal128(38, 10)" and kinds("dbo.typed_delta")["n"] == "decimal128(5, 2)"


def test_a_delta_timestamp_reads_as_datetime2_without_a_zone(trip):
    r = trip.execute("SELECT t6, DATEPART(hour, t6) AS h, CONVERT(VARCHAR(30), t6, 121) AS s FROM dbo.typed_delta "
                     "WHERE g > 0", dialect="tsql")
    assert "tz" not in r["columns"][0]["type"]
    assert [str(x) for x in r["rows"][0]] == ["9999-12-31 23:59:59.999999", "23", "9999-12-31 23:59:59.999999"]
    joined = rows(trip, "SELECT COUNT(*) FROM dbo.typed_delta AS a JOIN dbo.typed AS b ON a.t6 = b.t6")
    assert joined == [[2]]


def test_lengths_are_still_held_after_the_round_trip(trip):
    with pytest.raises(Exception, match="String or binary data would be truncated"):
        trip.execute("INSERT INTO dbo.typed (v) VALUES ('abcdefghijk')", dialect="tsql")
