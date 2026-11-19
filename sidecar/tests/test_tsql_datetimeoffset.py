"""WR-241: local T-SQL datetimeoffset, AT TIME ZONE, SWITCHOFFSET and TODATETIMEOFFSET give a Fabric Warehouse's
answers, or are refused by name.

Each answer is from Microsoft Learn's T-SQL reference (AT TIME ZONE, SWITCHOFFSET, TODATETIMEOFFSET, DATEPART and
datetimeoffset, read 2026-11-18), or follows from its rules: a value is compared by its UTC time, converting one to
date, time or datetime2 keeps its local date and time, and AT TIME ZONE gives a time skipped when the clocks go
forward the offset after the change and a time that happens twice the offset before it. The 2026 changes are
those of US and EU law: Pacific time goes to -07:00 at 02:00 on 8 March and back to -08:00 at 02:00 on 1 November;
central Europe goes to +02:00 at 02:00 on 29 March and back to +01:00 at 03:00 on 25 October.
"""

from __future__ import annotations

import pytest

from fabric_desktop.localdata import LocalData, tsql
from fabric_desktop.localdata.tsql_offsets import NOTE, ZONES

_NOT_YET = "This T-SQL can't be run on the local engine: "
PST = "'Pacific Standard Time'"
CET = "'Central European Standard Time'"
UTC = "'UTC'"
LA = "'America/Los_Angeles'"
FIJI = "'Fiji Standard Time'"


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    local = LocalData(tmp_path_factory.mktemp("offsets") / "app" / "local-data")
    e = local.engine(local.create("Offsets")["id"])
    e.execute("CREATE TABLE dbo.ev AS SELECT * FROM (VALUES (1, TIMESTAMP '2026-11-01 08:45:00'), "
              "(2, TIMESTAMP '2026-11-01 09:15:00'), (3, TIMESTAMP '2026-11-01 09:45:00'), (4, NULL)) t(id, utc)")
    return e


def value(engine, sql):
    rows = engine.execute(f"SELECT {sql} AS v", dialect="tsql")["rows"]
    return rows[0][0]


def text(sql, n=34):
    return f"CAST({sql} AS VARCHAR({n}))"


def dto(literal, n=None):
    return f"CAST('{literal}' AS DATETIMEOFFSET{f'({n})' if n is not None else ''})"


def at(local, zone, n=0):
    return f"CAST('{local}' AS DATETIME2({n})) AT TIME ZONE {zone}"


# (T-SQL expression, the warehouse's answer)
CASES = [
    # literals, as text: yyyy-mm-dd hh:mi:ss[.nnnnnnn] {+|-}hh:mi
    (text(dto("2024-01-01 10:00 +01:00")), "2024-01-01 10:00:00.0000000 +01:00"),
    (text(dto("2007-05-08 12:35:29.123456 +12:15", 7)), "2007-05-08 12:35:29.1234560 +12:15"),
    (text(dto("2007-05-08 12:35:29.123456+12:15", 7)), "2007-05-08 12:35:29.1234560 +12:15"),
    (text(dto("2024-07-03 00:00:00")), "2024-07-03 00:00:00.0000000 +00:00"),
    (text(dto("2024-07-03")), "2024-07-03 00:00:00.0000000 +00:00"),
    (text(dto("2024-07-03T08:00:00Z")), "2024-07-03 08:00:00.0000000 +00:00"),
    (text(dto("2024-07-03 08:00:00 -05:30", 0)), "2024-07-03 08:00:00 -05:30"),
    (text(dto("2024-07-03 08:00:00.12 -05:30", 3)), "2024-07-03 08:00:00.120 -05:30"),
    ("TRY_CAST('2024-07-03 +01:00' AS DATETIMEOFFSET)", None),  # a date with an offset and no time
    # converting one keeps its local date and time (datetimeoffset page, examples 2 and 3)
    (f"CAST({dto('2025-12-10 12:32:10 +01:00', 4)} AS DATE)", "2025-12-10"),
    (f"CAST({dto('2025-12-10 12:32:10.1237 +01:00', 4)} AS TIME(3))", "12:32:10.124000"),
    (f"CAST({dto('2025-12-10 23:30:00 -08:00')} AS DATETIME2)", "2025-12-10 23:30:00"),
    (f"CAST({dto('2025-12-10 23:30:00.5 -08:00')} AS DATETIME2(6))", "2025-12-10 23:30:00.500000"),
    # SWITCHOFFSET and TODATETIMEOFFSET (their pages' examples)
    (text(f"SWITCHOFFSET({dto('1998-09-20 7:45:50.71345 -5:00')}, '-08:00')"), "1998-09-20 04:45:50.7134500 -08:00"),
    (text("TODATETIMEOFFSET(CAST('2019-04-22 16:23:51.766666' AS DATETIME2), '-07:00')"),
     "2019-04-22 16:23:51.7666660 -07:00"),
    (text("TODATETIMEOFFSET(CAST('2019-04-22 11:39:21.698681' AS DATETIME2), -120)"),
     "2019-04-22 11:39:21.6986810 -02:00"),
    (text("TODATETIMEOFFSET(CAST('2019-04-22 11:39:29.033930' AS DATETIME2), '+13:00')"),
     "2019-04-22 11:39:29.0339300 +13:00"),
    (text("SWITCHOFFSET(CAST('2024-06-01 12:00' AS DATETIME2), 90)"), "2024-06-01 13:30:00.0000000 +01:30"),
    # across the date line: the same moment is the next day at +14:00 and the day before at -12:00
    (text(f"SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00')"), "2025-01-01 13:30:00.0000000 +14:00"),
    (text(f"SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '-12:00')"), "2024-12-31 11:30:00.0000000 -12:00"),
    (f"DATEPART(day, SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00'))", 1),
    (f"DATEPART(year, SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00'))", 2025),
    (f"YEAR(SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00'))", 2025),
    (f"MONTH(SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00'))", 1),
    (f"DAY(SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '-12:00'))", 31),
    (f"DATEPART(day, SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '-12:00'))", 31),
    ((f"CASE WHEN SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00') = "
      f"SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '-12:00') THEN 1 ELSE 0 END"), 1),
    (f"CAST(SWITCHOFFSET({dto('2024-12-31 23:30:00 +00:00')}, '+14:00') AS DATE)", "2025-01-01"),
    # DATEPART and DATENAME read the local date and time; TZOFFSET is the offset in minutes (DATEPART page)
    ("DATEPART(tzoffset, '2007-05-10  00:00:01.1234567 +05:10')", 310),
    (f"DATEPART(tzoffset, {dto('2007-10-30 12:15:32.123456 +05:10')})", 310),
    (f"DATEPART(tz, {dto('2007-10-30 12:15:32 -03:30')})", -210),
    (f"DATEPART(hour, {dto('2007-10-30 12:15:32.123456 +05:10')})", 12),
    (f"DATEPART(minute, {dto('2007-10-30 12:15:32.123456 +05:10')})", 15),
    ("DATEPART(tzoffset, CAST('2007-10-30 12:15:32' AS DATETIME2))", 0),
    (f"DATEPART(iso_week, {dto('2024-12-30 01:00 +14:00')})", 1),  # Monday locally; Sunday the 29th in UTC
    (f"DATEPART(weekday, {dto('2024-12-30 01:00 +14:00')})", 2),
    (f"DATENAME(tzoffset, {dto('2007-10-30 12:15:32 +05:10')})", "+05:10"),
    (f"DATENAME(tz, {dto('2007-10-30 12:15:32 -00:30')})", "-00:30"),
    ("DATENAME(tzoffset, CAST('2007-10-30 12:15:32' AS DATETIME2))", "+00:00"),
    (f"DATENAME(month, {dto('2024-12-31 23:30 -12:00')})", "December"),
    (f"DATENAME(weekday, {dto('2024-12-30 01:00 +14:00')})", "Monday"),
    ("DATENAME(hour, CAST('2024-03-05 07:08:09' AS DATETIME2))", "7"),
    ("DATENAME(day, CAST('2024-03-05' AS DATE))", "5"),
    ("DATENAME(quarter, CAST('2024-03-05' AS DATE))", "1"),
    ("DATENAME(dayofyear, CAST('2024-03-05' AS DATE))", "65"),
    ("DATENAME(year, CAST('2024-03-05' AS DATE))", "2024"),
    # compared by UTC time (datetimeoffset page: "compared, sorted, and indexed ... as in UTC")
    (f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} = {dto('2024-01-01 09:00 +00:00')} THEN 1 ELSE 0 END", 1),
    (f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} > {dto('2024-01-01 09:30 +00:00')} THEN 1 ELSE 0 END", 0),
    (f"CASE WHEN {dto('2024-01-01 10:00 -01:00')} > {dto('2024-01-01 10:30 +00:00')} THEN 1 ELSE 0 END", 1),
    (f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} <> {dto('2024-01-01 10:00 +00:00')} THEN 1 ELSE 0 END", 1),
    (f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} = '2024-01-01 09:00:00 +00:00' THEN 1 ELSE 0 END", 1),
    (f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} = CAST('2024-01-01 09:00' AS DATETIME2) THEN 1 ELSE 0 END", 1),
    ((f"CASE WHEN {dto('2024-01-01 10:00 +01:00')} BETWEEN '2024-01-01 08:59:00 +00:00' AND "
      f"{dto('2024-01-01 04:01 -05:00')} THEN 1 ELSE 0 END"), 1),
    # AT TIME ZONE: Microsoft's examples
    (text(at("2022-03-27T01:01:00", CET), 40), "2022-03-27 01:01:00 +01:00"),
    (text(at("2022-03-27T02:01:00", CET), 40), "2022-03-27 03:01:00 +02:00"),
    (text(at("2022-03-27T03:01:00", CET), 40), "2022-03-27 03:01:00 +02:00"),
    (text(at("2022-10-30T01:01:00", CET), 40), "2022-10-30 01:01:00 +02:00"),
    (text(at("2022-10-30T02:00:00", CET), 40), "2022-10-30 02:00:00 +02:00"),
    (text(at("2022-10-30T03:01:00", CET), 40), "2022-10-30 03:01:00 +01:00"),
    (text(f"CAST('2022-05-30' AS DATETIME) AT TIME ZONE {PST}"), "2022-05-30 00:00:00.000 -07:00"),
    (text(f"CAST('2022-05-30' AS DATETIME) AT TIME ZONE {PST} AT TIME ZONE {CET}"), "2022-05-30 09:00:00.000 +02:00"),
    (text(f"{at('2024-01-01 12:00', UTC)} AT TIME ZONE {PST}"), "2024-01-01 04:00:00 -08:00"),
    (text(f"{at('2024-05-01 12:00', UTC)} AT TIME ZONE {PST}"), "2024-05-01 05:00:00 -07:00"),
    (text(f"{at('2024-12-01 12:00', UTC)} AT TIME ZONE {PST}"), "2024-12-01 04:00:00 -08:00"),
    (f"CAST(CAST('2024-07-03 00:00:00' AS DATETIMEOFFSET) AT TIME ZONE {PST} AS DATETIME2)", "2024-07-02 17:00:00"),
    # Pacific time, 2026: forward on 8 March, back on 1 November
    (text(at("2026-03-08 01:59:59", PST)), "2026-03-08 01:59:59 -08:00"),
    (text(at("2026-03-08 02:00:00", PST)), "2026-03-08 03:00:00 -07:00"),
    (text(at("2026-03-08 02:30:00", PST)), "2026-03-08 03:30:00 -07:00"),
    (text(at("2026-03-08 03:00:00", PST)), "2026-03-08 03:00:00 -07:00"),
    (text(at("2026-11-01 00:59:00", PST)), "2026-11-01 00:59:00 -07:00"),
    (text(at("2026-11-01 01:00:00", PST)), "2026-11-01 01:00:00 -07:00"),
    (text(at("2026-11-01 01:30:00", PST)), "2026-11-01 01:30:00 -07:00"),
    (text(at("2026-11-01 02:00:00", PST)), "2026-11-01 02:00:00 -08:00"),
    (text(f"{dto('2026-11-01 08:30:00 +00:00', 0)} AT TIME ZONE {PST}"), "2026-11-01 01:30:00 -07:00"),
    (text(f"{dto('2026-11-01 09:30:00 +00:00', 0)} AT TIME ZONE {PST}"), "2026-11-01 01:30:00 -08:00"),
    (text(f"{dto('2026-03-08 10:00:00 +00:00', 0)} AT TIME ZONE {PST}"), "2026-03-08 03:00:00 -07:00"),
    (f"CASE WHEN {at('2026-11-01 01:30:00', PST)} = {dto('2026-11-01 08:30:00 +00:00')} THEN 1 ELSE 0 END", 1),
    (f"DATEPART(tzoffset, {at('2026-11-01 01:30:00', PST)})", -420),
    (f"DATEPART(tzoffset, {at('2026-11-01 02:30:00', PST)})", -480),
    # central Europe, 2026: forward on 29 March, back on 25 October
    (text(at("2026-03-29 01:59:00", CET)), "2026-03-29 01:59:00 +01:00"),
    (text(at("2026-03-29 02:30:00", CET)), "2026-03-29 03:30:00 +02:00"),
    (text(at("2026-10-25 02:30:00", CET)), "2026-10-25 02:30:00 +02:00"),
    (text(at("2026-10-25 03:00:00", CET)), "2026-10-25 03:00:00 +01:00"),
    (text(f"{dto('2026-10-25 01:30:00 +00:00', 0)} AT TIME ZONE {CET}"), "2026-10-25 02:30:00 +01:00"),
    (text(f"{dto('2026-10-25 00:30:00 +00:00', 0)} AT TIME ZONE {CET}"), "2026-10-25 02:30:00 +02:00"),
    (text(at("2026-03-29 02:30:00", "'W. Europe Standard Time'")), "2026-03-29 03:30:00 +02:00"),
    (text(at("2026-10-25 01:30:00", "'GMT Standard Time'")), "2026-10-25 01:30:00 +01:00"),
    # other zones, across the date line
    (text(f"{dto('2026-12-31 20:00:00 +00:00', 0)} AT TIME ZONE 'New Zealand Standard Time'"),
     "2027-01-01 09:00:00 +13:00"),
    (text(f"{dto('2026-12-31 20:00:00 +00:00', 0)} AT TIME ZONE 'Hawaiian Standard Time'"),
     "2026-12-31 10:00:00 -10:00"),
    (text(f"{dto('2026-06-30 20:00:00 +00:00', 0)} AT TIME ZONE 'India Standard Time'"), "2026-07-01 01:30:00 +05:30"),
    (text(at("2026-04-05 02:30:00", "'AUS Eastern Standard Time'")), "2026-04-05 02:30:00 +11:00"),
    (text(at("1990-06-01 12:00:00", UTC)), "1990-06-01 12:00:00 +00:00"),
    ("CAST(NULL AS DATETIME2) AT TIME ZONE 'Pacific Standard Time'", None),
    (f"CASE WHEN CAST(NULL AS DATETIME2) AT TIME ZONE {PST} IS NULL THEN 1 ELSE 0 END", 1),
    (f"CASE WHEN {at('2026-01-01', PST)} IS NOT NULL THEN 1 ELSE 0 END", 1),
]


@pytest.mark.parametrize("sql, expected", CASES, ids=[c[0][:90] for c in CASES])
def test_answers_as_a_warehouse_gives_them(engine, sql, expected):
    assert value(engine, sql) == expected


def test_a_returned_column_is_its_utc_time_with_a_note(engine):
    result = engine.execute(f"SELECT {at('2026-11-01 01:30:00', PST)} AS v", dialect="tsql")
    assert result["rows"] == [["2026-11-01 08:30:00+00:00"]]
    assert result["columns"][0]["type"] == "timestamp[us, tz=UTC]"
    assert NOTE in tsql.translate(f"SELECT {at('2026-11-01 01:30:00', PST)} AS v")[1]


def test_rows_sort_and_compare_by_utc_time_across_the_fall_back(engine):
    local = f"utc AT TIME ZONE 'UTC' AT TIME ZONE {PST}"
    shown = f"CAST(CAST({local} AS DATETIMEOFFSET(6)) AS VARCHAR(33))"
    rows = engine.execute(f"SELECT id, {shown} AS v FROM dbo.ev WHERE utc IS NOT NULL "
                          f"ORDER BY {local} DESC", dialect="tsql")["rows"]
    # 08:45 UTC is 01:45 -07:00 and 09:15 UTC is 01:15 -08:00: later in UTC, earlier on the clock
    assert rows == [[3, "2026-11-01 01:45:00.000000 -08:00"], [2, "2026-11-01 01:15:00.000000 -08:00"],
                    [1, "2026-11-01 01:45:00.000000 -07:00"]]
    after = engine.execute(f"SELECT id FROM dbo.ev WHERE {local} > '2026-11-01 01:30:00 -07:00' ORDER BY id",
                           dialect="tsql")["rows"]
    assert after == [[1], [2], [3]]
    later = engine.execute(f"SELECT id FROM dbo.ev WHERE {local} >= CAST('2026-11-01 01:15:00 -08:00' AS "
                           "DATETIMEOFFSET) ORDER BY id", dialect="tsql")["rows"]
    assert later == [[2], [3]]
    hours = engine.execute(f"SELECT id, DATEPART(hour, {local}) AS h, DATEPART(tzoffset, {local}) AS z "
                           "FROM dbo.ev ORDER BY id", dialect="tsql")["rows"]
    assert hours == [[1, 1, -420], [2, 1, -480], [3, 1, -480], [4, None, None]]


REFUSED = [
    # columns: a warehouse table can't keep one
    ("CREATE TABLE dbo.t (c DATETIMEOFFSET)", "The data type datetimeoffset isn't supported in a Fabric Warehouse"),
    (f"SELECT {at('2026-01-01', PST)} AS c INTO dbo.t2", "The data type datetimeoffset isn't supported"),
    (f"CREATE TABLE dbo.t3 AS SELECT {at('2026-01-01', PST)} AS c", _NOT_YET + "a datetimeoffset value in"),
    (f"INSERT INTO dbo.ev (id, utc) SELECT 5, {at('2026-01-01', PST)}", _NOT_YET + "a datetimeoffset value in"),
    (f"WITH x AS (SELECT {at('2026-01-01', PST)} AS c) SELECT c FROM x", _NOT_YET + "a datetimeoffset value in"),
    # uses not carried yet
    (f"SELECT COALESCE({at('2026-01-01', PST)}, {at('2026-01-02', PST)})", _NOT_YET + "a datetimeoffset value in"),
    (f"SELECT DATEADD(hour, 1, {at('2026-01-01', PST)})", _NOT_YET + "DATEADD of a datetimeoffset"),
    (f"SELECT DATEDIFF(hour, {at('2026-01-01', PST)}, {at('2026-01-02', PST)})", _NOT_YET + "DATEDIFF of"),
    (f"SELECT DATETRUNC(day, {at('2026-01-01', PST)})", _NOT_YET + "DATETRUNC of a datetimeoffset"),
    (f"SELECT EOMONTH({at('2026-01-01', PST)})", _NOT_YET + "EOMONTH of a datetimeoffset"),
    (f"SELECT FORMAT({at('2026-01-01', PST)}, 'yyyy')", _NOT_YET + "a datetimeoffset value in"),
    (f"SELECT CAST({at('2026-01-01', PST)} AS DATETIME)", _NOT_YET + "converting a datetimeoffset to datetime"),
    (f"SELECT CAST({dto('2026-01-01 10:00:00.25 +01:00', 2)} AS DATETIME2(1))", "fewer digits of a second"),
    (f"SELECT CAST({dto('2026-01-01 10:00 +01:00')} AS VARCHAR(20))", "is 34 characters as text"),
    (f"SELECT CAST({dto('2026-01-01 10:00 +01:00')} AS VARCHAR)", "is 34 characters as text"),
    (f"SELECT CAST(utc AT TIME ZONE {PST} AS VARCHAR(34)) FROM dbo.ev", "needs its digits of a second"),
    (f"SELECT CONVERT(VARCHAR(40), {dto('2026-01-01 10:00 +01:00')}, 126)", "with a style isn't mapped yet"),
    ("SELECT CONVERT(DATETIMEOFFSET, '2026-01-01T10:00:00+01:00', 127)", "with a style isn't mapped yet"),
    # text not read here, and the seventh digit
    (f"SELECT {dto('12-10-25 12:32:10 +01:00')}", _NOT_YET + "the text '12-10-25 12:32:10 +01:00' isn't read"),
    (f"SELECT {dto('2026-01-01 10:00:00.1234567 +01:00')}", "the seventh digit of a second"),
    (f"SELECT {dto('2026-01-01 10:00:00.25 +01:00', 1)}", "more digits of a second than datetimeoffset(1)"),
    (f"SELECT {dto('2026-01-01 +01:00')}", "Conversion failed when converting date and/or time"),
    (f"SELECT {dto('2026-02-30 10:00 +01:00')}", "Conversion failed when converting date and/or time"),
    (f"SELECT {dto('2026-01-01 10:00 +15:00')}", "Conversion failed when converting date and/or time"),
    # time zones and offsets
    (f"SELECT {at('2026-01-01', LA)}", _NOT_YET + "the time zone 'America/Los_Angeles' isn't"),
    (f"SELECT {at('2026-01-01', FIJI)}", "AT TIME ZONE takes a Windows time zone name"),
    ("SELECT utc AT TIME ZONE CAST(id AS VARCHAR(10)) FROM dbo.ev", "with a time zone that isn't written"),
    ("SELECT CAST('2026-01-01' AS VARCHAR(10)) AT TIME ZONE 'UTC'", "AT TIME ZONE of text"),
    ("SELECT SWITCHOFFSET(CAST('2026-01-01' AS DATETIME2), '+15:00')", "outside -14:00 to +14:00"),
    ("SELECT TODATETIMEOFFSET(CAST('2026-01-01' AS DATETIME2), 841)", "outside -14:00 to +14:00"),
    ("SELECT TODATETIMEOFFSET(CAST('2026-01-01' AS DATETIME2), '5:00')", "isn't read here"),
    ("SELECT SWITCHOFFSET(CAST('2026-01-01' AS DATETIME2), id) FROM dbo.ev", "an offset that isn't written"),
    ("SELECT SYSDATETIMEOFFSET() AT TIME ZONE 'UTC'", _NOT_YET + "SYSDATETIMEOFFSET isn't mapped yet"),
    ("SELECT DATETIMEOFFSETFROMPARTS(2024, 1, 1, 0, 0, 0, 0, 1, 0, 0)", _NOT_YET + "DATETIMEOFFSETFROMPARTS"),
]


@pytest.mark.parametrize("sql, message", REFUSED, ids=[r[0][:90] for r in REFUSED])
def test_refused_by_name(engine, sql, message):
    with pytest.raises(Exception, match=None) as caught:
        engine.execute(sql, dialect="tsql")
    assert message in str(caught.value)


@pytest.mark.parametrize("local, zone", [("2006-12-31 12:00", PST), ("2038-01-01 00:00", CET),
                                         ("2008-06-01 00:00", "'AUS Eastern Standard Time'")])
def test_a_time_outside_the_zone_years_carried_stops(engine, local, zone):
    with pytest.raises(Exception) as caught:
        engine.execute(f"SELECT {text(at(local, zone))} AS v", dialect="tsql")
    assert "is carried here for times from" in str(caught.value)
    assert "Run it in a Fabric Warehouse for other times." in str(caught.value)


def test_every_zone_name_is_one_the_local_engine_knows(engine):
    for name in ZONES:
        assert value(engine, f"DATEPART(year, {at('2026-07-01 12:00', repr(name))})") == 2026
