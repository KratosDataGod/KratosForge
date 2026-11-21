"""Which Snowflake functions answer the same on this PC (DuckDB, after KratosForge's Snowflake rules).

A Snowflake SQL cell runs on this PC through snowflake_sql.translate() and DuckDB. That labels each
statement "translated" when no known difference applies, but a label is not a result. Each case below
is one Snowflake function or operator with a sample call and the value Snowflake documents for it (the
examples and stated semantics in Snowflake's SQL reference, https://docs.snowflake.com/en/sql-reference,
linked per case). Nothing here was run against a Snowflake account. ``outcome`` is what this version of
sqlglot, the rules and DuckDB do with it:

    same          runs on this PC and gives the documented value
    differs       runs, but gives another value or type; the translation labels it a difference (SFD-*)
    error         translates, but DuckDB refuses the translated SQL
    untranslated  the rules say it runs only in Snowflake

tests/test_snowflake_sql_parity.py runs every case again, fails when an outcome changes, and checks that
every "differs" case is labelled a difference by translate(), so no known mismatch reads "translated".
docs/engines.md carries the table (``table_markdown``), checked by the same test.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from dataclasses import dataclass
from typing import Any

from .engines.databricks_sql_parity import same
from .duckdb_rows import first_row

_FN = "https://docs.snowflake.com/en/sql-reference/functions/"
D = dt.date
_V = "(SELECT column1 AS x FROM VALUES ({}))"
# The docs' table1 for CONDITIONAL_CHANGE_EVENT and CONDITIONAL_TRUE_EVENT.
_TABLE1 = ("VALUES ('Alberta', 0), ('Alberta', 0), ('Alberta', 13), ('Alberta', 13), ('Alberta', 14), ('Alberta', 15), "
           "('Alberta', NULL), ('Manitoba', 30) AS t(province, o_col)")
# The docs' march_temps_every_five_mins: march_temps resampled every five minutes, NULL where nothing was observed.
_MARCH = ("VALUES " + ", ".join(f"('{c}', '2025-03-15 {t}'::timestamp, {v})" for c, t, v in (
    ("Big Bear City", "09:45", "NULL"), ("Big Bear City", "09:49", 48), ("Big Bear City", "09:50", "NULL"),
    ("Big Bear City", "09:55", 49), ("Big Bear City", "10:00", "NULL"), ("Big Bear City", "10:05", "NULL"),
    ("Big Bear City", "10:10", 51), ("Big Bear City", "10:15", "NULL"), ("Big Bear City", "10:18", 54),
    ("South Lake Tahoe", "09:50", 44), ("South Lake Tahoe", "09:55", 46), ("South Lake Tahoe", "10:00", "NULL"),
    ("South Lake Tahoe", "10:05", "NULL"), ("South Lake Tahoe", "10:10", 52), ("South Lake Tahoe", "10:15", 54)))
    + " AS m(city, observed, temperature)")
# vartab from Snowflake's IS_<object_type> and AS_<object_type> pages.
_VARTAB = ("(SELECT column1 AS n, parse_json(column2) AS v FROM VALUES (1, 'null'), (2, null), (3, 'true'), "
           "(4, '-17'), (5, '123.12'), (6, '1.912e2'), (7, '\"Om ara pa ca na dhih\"  '), "
           "(8, '[-1, 12, 289, 2188, false,]'), (9, '{ \"x\" : \"abc\", \"y\" : false, \"z\": 10} '))")

# The docs' example tables, as VALUES (WR-305).
_EMPLOYEES = ("VALUES (1001, 10000), (1020, 9000), (1030, 8000), (900, 15000), (2000, NULL) AS t(e, s)")
_BITWISE = "VALUES (15, 1.1), (26, 2.9), (12, 7.1), (14, NULL), (8, NULL), (NULL, 9.1) AS t(k, d)"
_AGGR = "VALUES (1, 10, NULL), (2, 10, 11), (2, 20, 22), (2, 25, NULL), (2, 30, 35) AS t(k, v, v2)"
_AVG = "VALUES (1, 1.1), (1, 10), (2, 2.4), (2, NULL), (3, NULL), (NULL, 9.9) AS t(int_col, d)"
_BASIC = "VALUES (11, 101), (11, 102), (11, NULL), (12, 101), (NULL, 101), (NULL, 102) AS t(i_col, j_col)"
_SAMPLE = ("VALUES ('1', '1'), ('1', '5'), ('1', '3'), ('2', '2'), ('2', NULL), ('3', NULL), (NULL, '7'), (NULL, '1') "
           "AS t(k, d)")
_CORN = "VALUES (1, 'Iowa', 100), (2, 'Iowa', 110), (3, 'Kansas', 120), (4, 'Kansas', 130), (5, 'Iowa', 110) AS t(f, s, b)"


# WR-307: the docs' example tables, as VALUES, or the page's own statements where the check is the statement
_MENU = ("VALUES ('Pretzel', NULL, 3.00), ('Corn Dog', NULL, 4.00), ('Hot Dog', 1.50, 5.00), ('Sandwich', 2.50, 6.00) "
         "AS menu(item_name, item_cost, item_price)")
_SALES = ("VALUES (0, 2010, 1000.00), (0, 2011, 1500.00), (0, 2012, 500.00), (0, 2013, 750.00), (1, 2010, 10000.00), "
          "(1, 2011, 12500.00), (1, 2012, 15000.00), (1, 2013, 20000.00), (2, 2012, 500.00), (2, 2013, 800.00) "
          "AS sales(emp_id, year, revenue)")
_QT = "VALUES (1, 'A', 1), (2, 'A', 2), (3, 'B', 1), (4, 'B', 2) AS qt(i, p, o)"
_LIKE_EX = ("CREATE OR REPLACE TABLE like_ex(name VARCHAR(20)); INSERT INTO like_ex VALUES ('John  Dddoe'), "
            "('John ''alias'' Doe'), ('Joe   Doe'), ('John_down'), ('Joe down'), ('Elaine'), (''), (null);")
_ILIKE_EX = ("CREATE OR REPLACE TABLE ilike_ex(name VARCHAR(20)); INSERT INTO ilike_ex VALUES ('John  Dddoe'), "
             "('Joe   Doe'), ('John_down'), ('Joe down'), (null);")
_LIKE_EXAMPLE = ("CREATE OR REPLACE TABLE like_example(name VARCHAR(20)); INSERT INTO like_example VALUES "
                 "('John  Dddoe'), ('Joe   Doe'), ('John_down'), ('Joe down'), ('Tom   Doe'), ('Tim down'), (null);")
_LIKE_ALL = ("CREATE OR REPLACE TABLE like_all_example(name VARCHAR(20)); INSERT INTO like_all_example VALUES "
             "('John  Dddoe'), ('Joe   Doe'), ('John_do%wn'), ('Joe down'), ('Tom   Doe'), ('Tim down'), (null);")
_CITIES = "VALUES ('San Francisco'), ('San Jose'), ('Santa Clara'), ('Sacramento') AS strings(v)"
_QUARTERLY = ("(SELECT * FROM VALUES (1, 10000, '2023_Q1'), (1, 400, '2023_Q1'), (2, 4500, '2023_Q1'), "
              "(2, 35000, '2023_Q1'), (1, 5000, '2023_Q2'), (1, 3000, '2023_Q2'), (2, 200, '2023_Q2'), "
              "(2, 90500, '2023_Q2'), (1, 6000, '2023_Q3'), (1, 5000, '2023_Q3'), (2, 2500, '2023_Q3'), "
              "(2, 9500, '2023_Q3'), (3, 2700, '2023_Q3'), (1, 8000, '2023_Q4'), (1, 10000, '2023_Q4'), "
              "(2, 800, '2023_Q4'), (2, 4500, '2023_Q4'), (3, 2700, '2023_Q4'), (3, 16000, '2023_Q4'), "
              "(3, 10200, '2023_Q4') AS q(empid, amount, quarter))")
_MONTHLY = ("(SELECT * FROM VALUES (1, 'electronics', 100, 200, 300, 100), (2, 'clothes', 100, 300, 150, 200), "
            "(3, 'cars', 200, 400, 100, 50), (4, 'appliances', 100, NULL, 100, 50) "
            "AS m(empid, dept, jan, feb, mar, apr))")
_MERGE_MULT = (
    "CREATE OR REPLACE TABLE merge_example_mult_target (id INTEGER, val INTEGER, status VARCHAR); "
    "INSERT INTO merge_example_mult_target (id, val, status) VALUES (1, 10, 'Production'), (2, 20, 'Alpha'), "
    "(3, 30, 'Production'); "
    "CREATE OR REPLACE TABLE merge_example_mult_source (id INTEGER, marked VARCHAR, isnewstatus INTEGER, newval INTEGER, "
    "newstatus VARCHAR); "
    "INSERT INTO merge_example_mult_source (id, marked, isnewstatus, newval, newstatus) VALUES "
    "(1, 'Y', 0, 10, 'Production'), (2, 'N', 1, 50, 'Beta'), (3, 'N', 0, 60, 'Deprecated'), (4, 'N', 0, 40, 'Production'); "
    "MERGE INTO merge_example_mult_target USING merge_example_mult_source "
    "ON merge_example_mult_target.id = merge_example_mult_source.id "
    "WHEN MATCHED AND merge_example_mult_source.marked = 'Y' THEN DELETE "
    "WHEN MATCHED AND merge_example_mult_source.isnewstatus = 1 THEN UPDATE SET val = merge_example_mult_source.newval, "
    "status = merge_example_mult_source.newstatus "
    "WHEN MATCHED THEN UPDATE SET val = merge_example_mult_source.newval "
    "WHEN NOT MATCHED THEN INSERT (id, val, status) VALUES (merge_example_mult_source.id, merge_example_mult_source.newval, "
    "merge_example_mult_source.newstatus);")
_ARRAY_APPEND = ("CREATE OR REPLACE TABLE array_append_examples (array_column ARRAY); "
                 "INSERT INTO array_append_examples (array_column) SELECT ARRAY_CONSTRUCT(1, 2, 3);")
_APPEND_FOUR = "UPDATE array_append_examples SET array_column = ARRAY_APPEND(array_column, 4);"
_APPEND_FIVE = "UPDATE array_append_examples SET array_column = ARRAY_APPEND(array_column, 'five');"
_BICYCLES = (
    "CREATE TABLE leased_bicycles (bicycle_id INTEGER, \"customer_id\" INTEGER); "
    "CREATE TABLE returned_bicycles (bicycle_id INTEGER); "
    "INSERT INTO leased_bicycles (bicycle_ID, \"customer_id\") VALUES (101, 1111), (102, 2222), (103, 3333), (104, 4444), "
    "(105, 5555); INSERT INTO returned_bicycles (bicycle_ID) VALUES (102), (104), (110);")

_NEVERMORE = "'nevermore1, nevermore2, nevermore3.'"
_EMPLOYEES_INSERT = (
    "CREATE TABLE employees (first_name VARCHAR, last_name VARCHAR, workphone VARCHAR, city VARCHAR, postal_code VARCHAR); "
    "INSERT INTO employees VALUES ('May', 'Franklin', '1-650-249-5198', 'San Francisco', '94115'), "
    "('Gillian', 'Patterson', '1-650-859-3954', 'San Francisco', '94115'), "
    "('Lysandra', 'Reeves', '1-212-759-3751', 'New York', '10018'), "
    "('Michael', 'Arnett', '1-650-230-8467', 'San Francisco', '94116');")
_CONTRACTORS = (
    "CREATE TABLE contractors (contractor_first VARCHAR, contractor_last VARCHAR, worknum VARCHAR, city VARCHAR, "
    "zip_code VARCHAR); INSERT INTO contractors VALUES ('Bradley', 'Greenbloom', '1-650-445-0676', 'San Francisco', "
    "'94110'), ('Cole', 'Simpson', '1-212-285-8904', 'New York', '10001'), ('Laurel', 'Slater', '1-650-633-4495', "
    "'San Francisco', '94115');")
_DEMO1 = ("CREATE TABLE demo1 (id INTEGER, json_data VARCHAR); "
          "INSERT INTO demo1 SELECT 1, '{\"level_1_key\": \"level_1_value\"}'; "
          "INSERT INTO demo1 SELECT 2, '{\"level_1_key\": {\"level_2_key\": \"level_2_value\"}}'; "
          "INSERT INTO demo1 SELECT 3, '{\"level_1_key\": {\"level_2_key\": [\"zero\", \"one\", \"two\"]}}';")
_TESTTABLE = "CREATE TABLE testtable (i INTEGER); INSERT INTO testtable VALUES (1), (2), (3), (4), (5);"
_T2 = "CREATE TABLE t2 (i INTEGER); INSERT INTO t2 VALUES (1), (2), (3), (4), (5);"


def _values(*rows: Any) -> str:
    return _V.format("), (".join(str(r) for r in rows))


@dataclass(frozen=True)
class Raises:
    """A documented error: the case is "same" when DuckDB stops with a message containing ``text``."""
    text: str


@dataclass(frozen=True)
class Case:
    function: str
    group: str
    sql: str          # a SELECT whose first column of the first row is the answer
    expected: Any     # the value Snowflake documents
    outcome: str      # same | differs | error | untranslated (this version)
    source: str       # the Snowflake reference page for the function
    note: str = ""


def _c(function: str, group: str, sql: str, expected: Any, outcome: str = "same", note: str = "",
       page: str = "") -> Case:
    return Case(function, group, sql, expected, outcome, _FN + (page or function.split(" ")[0].lower()), note)


CASES: tuple[Case, ...] = (
    _c("IFF", "Conditional", "SELECT iff(TRUE, 'a', 'b')", "a"),
    _c("NVL", "Conditional", "SELECT nvl(NULL, 'x')", "x"),
    _c("NVL2 (NULL)", "Conditional", "SELECT nvl2(NULL, 1, 2)", 2),
    _c("NVL2 (value)", "Conditional", "SELECT nvl2('a', 1, 2)", 1),
    _c("IFNULL", "Conditional", "SELECT ifnull(NULL, 1)", 1),
    _c("NULLIF", "Conditional", "SELECT nullif(1, 1)", None),
    _c("ZEROIFNULL", "Conditional", "SELECT zeroifnull(NULL)", 0),
    _c("NULLIFZERO", "Conditional", "SELECT nullifzero(0)", None),
    _c("DIV0", "Conditional", "SELECT div0(1, 0)", 0),
    _c("DIV0NULL", "Conditional", "SELECT div0null(1, 0)", 0),
    _c("DECODE", "Conditional", "SELECT decode(2, 1, 'one', 2, 'two', 'other')", "two"),
    _c("DECODE (NULL matches NULL)", "Conditional", "SELECT decode(NULL, NULL, 'null', 'other')", "null"),
    _c("EQUAL_NULL", "Conditional", "SELECT equal_null(NULL, NULL)", True),
    _c("COALESCE", "Conditional", "SELECT coalesce(NULL, 2, 3)", 2),
    _c("GREATEST", "Conditional", "SELECT greatest(1, 3, 2)", 3),
    _c("GREATEST (any NULL)", "Conditional", "SELECT greatest(1, NULL)", None),
    _c("LEAST (any NULL)", "Conditional", "SELECT least(1, NULL)", None),
    _c("CONCAT (any NULL)", "String", "SELECT concat('a', NULL)", None),
    _c("|| (NULL)", "String", "SELECT 'a' || NULL", None, page="concat"),
    _c("CONCAT_WS (any NULL)", "String", "SELECT concat_ws(',', 'a', NULL)", None),
    _c("SPLIT_PART", "String", "SELECT split_part('a-b-c', '-', 2)", "b"),
    _c("SPLIT_PART (negative part)", "String", "SELECT split_part('a-b-c', '-', -1)", "c"),
    _c("SPLIT_PART (part 0)", "String", "SELECT split_part('a-b-c', '-', 0)", "a"),
    _c("SPLIT", "String", "SELECT split('a,b', ',')", ["a", "b"]),
    _c("STRTOK", "String", "SELECT strtok('a.b.c', '.', 2)", "b"),
    _c("REGEXP_SUBSTR", "String", "SELECT regexp_substr('abc123', '[0-9]+')", "123"),
    _c("REGEXP_COUNT", "String", "SELECT regexp_count('abcabc', 'b')", 2),
    _c("REGEXP_REPLACE (every match)", "String", "SELECT regexp_replace('abcb', 'b', 'x')", "axcx"),
    _c("REGEXP_LIKE (whole string)", "String", "SELECT regexp_like('abc', 'b')", False),
    _c("RLIKE (whole string)", "String", "SELECT rlike('abc', 'a.c')", True),
    _c("LEFT", "String", "SELECT left('abcdef', 3)", "abc"),
    _c("RIGHT", "String", "SELECT right('abcdef', 3)", "def"),
    _c("LENGTH", "String", "SELECT length('abc')", 3),
    _c("LEN", "String", "SELECT len('abc')", 3, page="length"),
    _c("UPPER", "String", "SELECT upper('abc')", "ABC"),
    _c("INITCAP", "String", "SELECT initcap('hello world')", "Hello World"),
    _c("TRIM", "String", "SELECT trim('  a  ')", "a"),
    _c("LTRIM (characters)", "String", "SELECT ltrim('xxabc', 'x')", "abc"),
    _c("LPAD", "String", "SELECT lpad('abc', 5, '*')", "**abc"),
    _c("REPLACE", "String", "SELECT replace('abcabc', 'b', 'x')", "axcaxc"),
    _c("REVERSE", "String", "SELECT reverse('abc')", "cba"),
    _c("REPEAT", "String", "SELECT repeat('ab', 2)", "abab"),
    _c("SPACE", "String", "SELECT space(2) || 'x'", "  x"),
    _c("INSERT", "String", "SELECT insert('abcdef', 2, 3, 'X')", "aXef"),
    _c("CONTAINS", "String", "SELECT contains('abc', 'b')", True),
    _c("STARTSWITH", "String", "SELECT startswith('abc', 'a')", True),
    _c("ENDSWITH", "String", "SELECT endswith('abc', 'c')", True),
    _c("POSITION", "String", "SELECT position('b' IN 'abc')", 2),
    _c("CHARINDEX", "String", "SELECT charindex('b', 'abc')", 2),
    _c("SUBSTR", "String", "SELECT substr('abcdef', 2, 3)", "bcd", page="substr"),
    _c("TRANSLATE", "String", "SELECT translate('abc', 'ab', 'xy')", "xyc"),
    _c("ASCII", "String", "SELECT ascii('A')", 65),
    _c("CHR", "String", "SELECT chr(65)", "A"),
    _c("EDITDISTANCE", "String", "SELECT editdistance('kitten', 'sitting')", 3),
    _c("SOUNDEX", "String", "SELECT soundex('Miller')", "M460"),
    _c("SOUNDEX (same code for similar names)", "String", "SELECT soundex('Rupert')", "R163"),
    _c("SOUNDEX (adjacent same codes)", "String", "SELECT soundex('Tymczak')", "T522"),
    _c("SOUNDEX (first letter's code)", "String", "SELECT soundex('Pfister')", "P236"),
    _c("SOUNDEX (H between same codes)", "String", "SELECT soundex('Ashcraft')", "A261"),
    _c("SOUNDEX (padded with zeros)", "String", "SELECT soundex('Lee')", "L000"),
    _c("TIME", "Date and time", "SELECT time('13:30:00')", dt.time(13, 30), page="to_time"),
    _c("TIME (fraction)", "Date and time", "SELECT time('13:30:00.000')", dt.time(13, 30),
       page="to_time"),
    _c("TIMESTAMPADD (years)", "Date and time", "SELECT timestampadd(year, 2, to_date('2022-05-08'))",
       dt.date(2024, 5, 8), page="timestampadd"),
    _c("TIMESTAMPADD (hours on a date)", "Date and time", "SELECT timestampadd(hour, 2, to_date('2022-04-05'))",
       dt.datetime(2022, 4, 5, 2, 0), page="timestampadd"),
    _c("TIMESTAMPADD (month end)", "Date and time", "SELECT timestampadd(month, 1, '2023-01-31'::date)",
       dt.date(2023, 2, 28), page="timestampadd"),
    _c("TIMESTAMPADD (hours on a time)", "Date and time", "SELECT timestampadd(hour, 3, to_time('05:00:00'))",
       dt.time(8, 0), page="timestampadd"),
    _c("SOUNDEX_P123 (docs example)", "String", "SELECT soundex_p123('Pfister')", "P123"),
    _c("SOUNDEX_P123 (same first two codes)", "String", "SELECT soundex_p123('Lloyd')", "L430", page="soundex_p123"),
    _c("OCTET_LENGTH", "String", "SELECT octet_length('abc')", 3),
    _c("OCTET_LENGTH (two-byte letter)", "String", "SELECT octet_length('Β')", 2, page="octet_length"),
    _c("OCTET_LENGTH (binary)", "String", "SELECT octet_length(X'A1B2')", 2, page="octet_length"),
    _c("TRY_BASE64_DECODE_STRING", "String", "SELECT try_base64_decode_string(base64_encode('HELLO'))", "HELLO"),
    _c("TRY_BASE64_DECODE_STRING (garbage)", "String", "SELECT try_base64_decode_string('127')", None,
       page="try_base64_decode_string"),
    _c("TRY_BASE64_DECODE_BINARY", "String", "SELECT try_base64_decode_binary(base64_encode('HELP'))", b"HELP"),
    _c("TRY_HEX_DECODE_STRING", "String", "SELECT try_hex_decode_string(hex_encode('AaBb'))", "AaBb"),
    _c("TRY_HEX_DECODE_STRING (garbage)", "String", "SELECT try_hex_decode_string('127')", None,
       page="try_hex_decode_string"),
    _c("TRY_HEX_DECODE_BINARY", "String", "SELECT try_hex_decode_binary(hex_encode('ABab'))", b"ABab"),
    _c("MD5_NUMBER_LOWER64", "String", "SELECT md5_number_lower64('Snowflake')", 9203306159527282910),
    _c("MD5_NUMBER_UPPER64", "String", "SELECT md5_number_upper64('Snowflake')", 17145559544104499780),
    _c("WIDTH_BUCKET (docs example)", "Numeric",
       "SELECT listagg(width_bucket(price, 200000, 600000, 4), ',') WITHIN GROUP (ORDER BY d) FROM "
       "(SELECT column1 AS d, column2 AS price FROM VALUES ('2013-08-01', 290000.00), ('2014-02-01', 320000.00), ('2015-04-01', 399999.99), ('2016-04-01', 400000.00), ('2017-04-01', 470000.00), ('2018-04-01', 510000.00))", "1,2,2,3,3,4", page="width_bucket"),
    _c("WIDTH_BUCKET (below min)", "Numeric", "SELECT width_bucket(100, 200000, 600000, 4)", 0, page="width_bucket"),
    _c("WIDTH_BUCKET (at max)", "Numeric", "SELECT width_bucket(600000, 200000, 600000, 4)", 5, page="width_bucket"),
    _c("WIDTH_BUCKET (NULL)", "Numeric", "SELECT width_bucket(NULL, 200000, 600000, 4)", None, page="width_bucket"),
    _c("MD5", "String", "SELECT md5('a')", "0cc175b9c0f1b6a831c399e269772661"),
    _c("SHA2 (256 by default)", "String", "SELECT sha2('a')",
       "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"),
    _c("ABS", "Numeric", "SELECT abs(-3)", 3),
    _c("CEIL", "Numeric", "SELECT ceil(1.2)", 2),
    _c("FLOOR", "Numeric", "SELECT floor(-1.2)", -2),
    _c("ROUND (half away from zero)", "Numeric", "SELECT round(2.5)", 3),
    _c("ROUND (negative half)", "Numeric", "SELECT round(-2.5)", -3),
    _c("TRUNC", "Numeric", "SELECT trunc(2.7)", 2),
    _c("MOD (negative dividend)", "Numeric", "SELECT mod(-7, 3)", -1),
    _c("% (modulo)", "Numeric", "SELECT 7 % 3", 1, page="mod"),
    _c("SQUARE", "Numeric", "SELECT square(3)", 9),
    _c("SQRT", "Numeric", "SELECT sqrt(16)", 4),
    _c("POWER", "Numeric", "SELECT power(2, 3)", 8),
    _c("SIGN", "Numeric", "SELECT sign(-5)", -1),
    _c("BITAND", "Numeric", "SELECT bitand(12, 10)", 8),
    _c("/ (by zero)", "Numeric", "SELECT 1/0", Raises("Division by zero"), page="div0"),
    _c("/ (by a zero column)", "Numeric", f"SELECT 1 / x FROM {_values(1, 0)}", Raises("Division by zero"),
       page="div0"),
    _c("TO_NUMBER (scale 0)", "Conversion", "SELECT to_number('12.3456')", 12, page="to_decimal"),
    _c("TRY_TO_NUMBER", "Conversion", "SELECT try_to_number('12x')", None, page="try_to_decimal"),
    _c("TRY_CAST", "Conversion", "SELECT try_cast('5' AS INT)", 5),
    _c("TO_VARCHAR", "Conversion", "SELECT to_varchar(123)", "123", page="to_char"),
    _c("TO_BOOLEAN", "Conversion", "SELECT to_boolean('yes')", True),
    _c("TO_DATE", "Conversion", "SELECT to_date('2026-10-30')", D(2026, 10, 30)),
    _c("TO_DATE (format)", "Conversion", "SELECT to_date('10/30/2026', 'MM/DD/YYYY')", D(2026, 10, 30)),
    _c("TO_TIME", "Conversion", "SELECT to_time('13:45:00')", dt.time(13, 45)),
    _c("DATE_FROM_PARTS", "Date and time", "SELECT date_from_parts(2026, 10, 30)", D(2026, 10, 30)),
    _c("DATE_TRUNC (DATE)", "Date and time", "SELECT date_trunc('month', '2026-03-15'::date)", D(2026, 3, 1)),
    _c("DATEADD (DATE, month end)", "Date and time", "SELECT dateadd(month, 1, '2026-01-31'::date)", D(2026, 2, 28)),
    _c("ADD_MONTHS (month end)", "Date and time", "SELECT add_months('2026-01-31'::date, 1)", D(2026, 2, 28)),
    _c("LAST_DAY", "Date and time", "SELECT last_day('2026-02-10'::date)", D(2026, 2, 28)),
    _c("DATEDIFF (day)", "Date and time", "SELECT datediff(day, '2026-01-01'::date, '2026-01-31'::date)", 30),
    _c("DATEDIFF (month boundary)", "Date and time",
       "SELECT datediff(month, '2026-01-31'::date, '2026-02-01'::date)", 1),
    _c("TIMESTAMPDIFF (hour)", "Date and time",
       "SELECT timestampdiff(hour, '2026-10-30 10:00'::timestamp, '2026-10-30 13:30'::timestamp)", 3),
    _c("TIMEADD", "Date and time", "SELECT timeadd(hour, 2, '2026-10-30 10:00'::timestamp)",
       dt.datetime(2026, 10, 30, 12, 0)),
    _c("TIME_SLICE", "Date and time", "SELECT time_slice('2026-10-30 10:07'::timestamp, 15, 'minute')",
       dt.datetime(2026, 10, 30, 10, 0)),
    _c("DAYOFWEEK (Sunday is 0)", "Date and time", "SELECT dayofweek('2026-10-30'::date)", 5, page="year"),
    _c("YEAR", "Date and time", "SELECT year('2026-10-30'::date)", 2026),
    _c("HOUR", "Date and time", "SELECT hour('2026-10-30 13:45:00'::timestamp)", 13, page="hour-minute-second"),
    _c("DATE_PART", "Date and time", "SELECT date_part(year, '2026-10-30'::date)", 2026),
    _c("MONTHNAME", "Date and time", "SELECT monthname('2026-10-30'::date)", "Oct"),
    _c("DAYNAME", "Date and time", "SELECT dayname('2026-10-30'::date)", "Fri"),
    _c("PARSE_JSON path", "Semi-structured", "SELECT parse_json('{\"a\": 1}'):a::int", 1),
    # Snowflake's "Querying semi-structured data" page: src:salesperson.name::string is the name, unquoted.
    _c("PARSE_JSON path (cast to a string)", "Semi-structured",
       "SELECT parse_json('{\"salesperson\": {\"name\": \"Frank Beasley\"}}'):salesperson.name::string",
       "Frank Beasley", page="../../user-guide/querying-semistructured"),
    _c("PARSE_JSON path (a number cast to a string)", "Semi-structured",
       "SELECT parse_json('{\"a\": 1.5}'):a::varchar", "1.5", page="../../user-guide/querying-semistructured"),
    _c("TO_VARCHAR (a JSON string)", "Conversion", "SELECT to_varchar(parse_json('{\"a\": \"Pine\"}'):a)", "Pine",
       page="to_char"),
    _c("PARSE_JSON path (brackets cast to a string)", "Semi-structured",
       "SELECT v['salesperson']['name']::string FROM (SELECT parse_json('{\"salesperson\": {\"name\": "
       "\"Frank Beasley\"}}') AS v)", "Frank Beasley", page="../../user-guide/querying-semistructured"),
    # Snowflake's "Considerations for semi-structured data", NULL values: TO_CHAR of a JSON null is NULL.
    _c("TO_VARCHAR (a JSON null)", "Conversion", "SELECT to_char(parse_json('{ \"a\": null }'):a)", None,
       page="../../user-guide/semistructured-considerations"),
    # [i] is 0-based on an ARRAY and on a VARIANT holding one (Snowflake's "Querying semi-structured data").
    _c("PARSE_JSON path ([1] on a stored array)", "Semi-structured",
       "SELECT v[1]::int FROM (SELECT parse_json('[10, 20, 30]') AS v)", 20,
       page="../../user-guide/querying-semistructured"),
    _c("ARRAY_CONSTRUCT ([i] with an index column)", "Semi-structured",
       "SELECT a[i] FROM (SELECT array_construct(10, 20, 30) AS a, 2 AS i)", 30,
       page="../../user-guide/querying-semistructured"),
    _c("ARRAY_SIZE", "Semi-structured", "SELECT array_size(array_construct(1, 2, 3))", 3),
    _c("ARRAY_CONTAINS", "Semi-structured", "SELECT array_contains(2::variant, array_construct(1, 2))", True),
    _c("ARRAY_TO_STRING", "Semi-structured", "SELECT array_to_string(array_construct('a', 'b'), ',')", "a,b"),
    _c("ARRAY_SLICE", "Semi-structured", "SELECT array_slice(array_construct(1, 2, 3, 4), 1, 3)", [2, 3]),
    _c("ARRAY_CAT", "Semi-structured", "SELECT array_cat(array_construct(1), array_construct(2))", [1, 2]),
    _c("OBJECT_KEYS", "Semi-structured", "SELECT object_keys(parse_json('{\"a\": 1, \"b\": 2}'))", ["a", "b"]),
    _c("OBJECT_CONSTRUCT (constant)", "Semi-structured", "SELECT {'b': 2, 'a': 1}::string", '{"a":1,"b":2}',
       page="object_construct"),
    _c("OBJECT_DELETE", "Semi-structured",
       "SELECT object_delete(object_construct('a', 1, 'b', 2, 'c', 3), 'a', 'b')::string", '{"c":3}'),
    _c("OBJECT_DELETE (nested path)", "Semi-structured",
       "SELECT object_delete(ov:employee_contact, 'phone')::string FROM (SELECT object_construct('employee_contact', "
       "object_construct('city', 'San Mateo', 'state', 'CA', 'phone', '800-555-0100')) AS ov)",
       '{"city":"San Mateo","state":"CA"}', page="object_delete"),
    _c("OBJECT_PICK", "Semi-structured",
       "SELECT object_pick(object_construct('a', 1, 'b', 2, 'c', 3), 'a', 'b')::string", '{"a":1,"b":2}'),
    _c("OBJECT_PICK (array of keys)", "Semi-structured",
       "SELECT object_pick(object_construct('a', 1, 'b', 2, 'c', 3), array_construct('a', 'b'))::string",
       '{"a":1,"b":2}', page="object_pick"),
    _c("OBJECT_INSERT (docs example)", "Semi-structured",
       "SELECT object_insert(object_insert(object_insert(object_insert(object_construct('a', 'value1', 'b', "
       "'value2'), 'c', 'value3'), 'd', parse_json('null')), 'e', NULL), 'f', 'null')::string",
       '{"a":"value1","b":"value2","c":"value3","d":null,"f":"null"}', page="object_insert"),
    _c("OBJECT_INSERT (update)", "Semi-structured",
       "SELECT object_insert(object_construct('a', 'value1', 'b', 'value2'), 'b', 'valuex', TRUE)::string",
       '{"a":"value1","b":"valuex"}', page="object_insert"),
    _c("OBJECT_INSERT (existing key)", "Semi-structured",
       "SELECT object_insert(object_construct('a', 'value1'), 'a', 'valuex')", Raises("Duplicate field key"),
       page="object_insert"),
    _c("OBJECT_AGG (docs example)", "Aggregate",
       "SELECT listagg(o::string, ' ') WITHIN GROUP (ORDER BY g) FROM (SELECT g, object_agg(k, v) AS o FROM "
       "(SELECT column1 AS g, column2 AS k, column3 AS v FROM VALUES (0, 'name', 'Joe'::variant), (0, 'age', 21::variant), (1, 'name', 'Sue'::variant), (1, 'zip', 94401::variant)) GROUP BY g)", '{"age":21,"name":"Joe"} {"name":"Sue","zip":94401}', page="object_agg"),
    _c("TO_OBJECT", "Conversion", "SELECT to_object(parse_json('{\"a\":1}'))::string", '{"a":1}'),
    _c("TO_OBJECT (JSON null)", "Conversion", "SELECT to_object(parse_json('null'))", None, page="to_object"),
    _c("ARRAYS_TO_OBJECT", "Semi-structured",
       "SELECT arrays_to_object(['key1', 'key2', 'key3'], [1, 2, 3])::string", '{"key1":1,"key2":2,"key3":3}'),
    _c("ARRAYS_TO_OBJECT (NULL key)", "Semi-structured",
       "SELECT arrays_to_object(['key1', NULL, 'key3'], [1, 2, 3])::string", '{"key1":1,"key3":3}',
       page="arrays_to_object"),
    _c("ARRAYS_TO_OBJECT (NULL value)", "Semi-structured",
       "SELECT arrays_to_object(['key1', 'key2', 'key3'], [1, NULL, 3])::string",
       '{"key1":1,"key2":null,"key3":3}', page="arrays_to_object"),
    _c("ARRAY_EXCEPT", "Semi-structured", "SELECT array_except(['A', 'B'], ['B', 'C'])", ["A"]),
    _c("ARRAY_EXCEPT (copies)", "Semi-structured", "SELECT array_except(['A', 'B', 'B', 'B', 'C'], ['B'])",
       ["A", "B", "B", "C"], page="array_except"),
    _c("ARRAY_EXCEPT (NULLs)", "Semi-structured", "SELECT array_except(['A', NULL, NULL], ['B', NULL])",
       ["A", None], page="array_except"),
    _c("ARRAY_EXCEPT (NULL argument)", "Semi-structured", "SELECT array_except(['A', 'B'], NULL)", None,
       page="array_except"),
    _c("ARRAY_INTERSECTION", "Semi-structured",
       "SELECT array_intersection(array_construct('A', 'B', 'B', 'B', 'C'), array_construct('B', 'B'))",
       ["B", "B"]),
    _c("ARRAY_INTERSECTION (NULLs)", "Semi-structured", "SELECT array_intersection([NULL, 102, NULL], [NULL, NULL, 103])",
       [None, None], page="array_intersection"),
    _c("ARRAY_INTERSECTION (objects in another order)", "Semi-structured",
       "SELECT array_size(array_intersection([{'a': 1, 'b': 2, 'c': 3}], [{'c': 3, 'b': 2, 'a': 1}]))", 1,
       page="array_intersection"),
    _c("FILTER", "Semi-structured", "SELECT filter([1, NULL, 3, 5, NULL], a -> a IS NOT NULL)", [1, 3, 5]),
    _c("FILTER (objects)", "Semi-structured",
       "SELECT array_size(filter([{'name': 'Pat', 'value': 50}, {'name': 'Terry', 'value': 75}, "
       "{'name': 'Dana', 'value': 25}], a -> a:value >= 50))", 2, page="filter"),
    _c("TRANSFORM", "Semi-structured", "SELECT transform([1, 2, 3], a INT -> a * 2)", [2, 4, 6]),
    _c("TRANSFORM (objects)", "Semi-structured",
       "SELECT transform([{'name': 'Pat', 'value': 50}, {'name': 'Terry', 'value': 75}], "
       "c -> c:value || ' is the number')", ["50 is the number", "75 is the number"], page="transform"),
    _c("MAP_CAT", "Semi-structured",
       "SELECT map_size(map_cat({'color': 'red', 'size': 'M'}::MAP(VARCHAR, VARCHAR), "
       "{'currency': 'USD', 'size': 'L'}::MAP(VARCHAR, VARCHAR)))", 3),
    _c("MAP_CAT (the second map wins)", "Semi-structured",
       "SELECT map_cat({'color': 'red', 'size': 'M'}::MAP(VARCHAR, VARCHAR), "
       "{'currency': 'USD', 'size': 'L'}::MAP(VARCHAR, VARCHAR))['size']", "L", page="map_cat"),
    _c("IS_NULL_VALUE", "Semi-structured", "SELECT is_null_value(parse_json('null'))", True),
    # The examples on Snowflake's IS_<object_type> and AS_<object_type> pages: vartab, then multiple_types.
    *(_c(f"{name} (vartab)", "Semi-structured", f"SELECT listagg(n, ',') WITHIN GROUP (ORDER BY n) FROM {_VARTAB} "
         f"WHERE {name.lower()}(v)", rows, page="is") for name, rows in (
        ("IS_NULL_VALUE", "1"), ("IS_BOOLEAN", "3"), ("IS_INTEGER", "4"), ("IS_DOUBLE", "4,5,6"),
        ("IS_REAL", "4,5,6"), ("IS_VARCHAR", "7"), ("IS_CHAR", "7"), ("IS_ARRAY", "8"), ("IS_OBJECT", "9"))),
    _c("IS_DECIMAL (vartab)", "Semi-structured",
       f"SELECT listagg(n, ',') WITHIN GROUP (ORDER BY n) FROM {_VARTAB} WHERE is_decimal(v)", "4,5", "untranslated",
       "Refused by SFN-034 when the query reaches row 5: how 123.12 and 1.912e2 were written isn't kept in a "
       "stored VARIANT here. IS_DECIMAL(PARSE_JSON('1.912e2')) is FALSE, as in Snowflake.", page="is"),
    _c("IS_DECIMAL (PARSE_JSON with an exponent)", "Semi-structured", "SELECT is_decimal(parse_json('1.912e2'))",
       False, page="is"),
    _c("IS_DECIMAL (PARSE_JSON with a fraction)", "Semi-structured", "SELECT is_decimal(parse_json('123.12'))",
       True, page="is"),
    _c("IS_DECIMAL", "Semi-structured", "SELECT is_decimal(to_variant(1.23::decimal(6, 3)))", True),
    _c("IS_DECIMAL (a DOUBLE)", "Semi-structured", "SELECT is_decimal(to_variant(3.21::double))", False),
    _c("IS_DECIMAL (an integer)", "Semi-structured", "SELECT is_decimal(to_variant(15))", True),
    _c("IS_INTEGER (a DECIMAL)", "Semi-structured", "SELECT is_integer(to_variant(1.23::decimal(6, 3)))", False),
    _c("IS_INTEGER (a DOUBLE)", "Semi-structured", "SELECT is_integer(to_variant(3.21::double))", False),
    _c("IS_DOUBLE (a BOOLEAN)", "Semi-structured", "SELECT is_double(to_variant(true))", False),
    _c("IS_DOUBLE (a DECIMAL)", "Semi-structured", "SELECT is_double(to_variant(1.23::decimal(6, 3)))", True),
    _c("IS_REAL", "Semi-structured", "SELECT is_real(to_variant(3.21::double))", True, page="is_double-real"),
    _c("IS_DATE", "Semi-structured", "SELECT is_date(to_variant('2024-01-17'::date))", True, page="is"),
    _c("IS_DATE_VALUE", "Semi-structured", "SELECT is_date_value(parse_json('\"2024-01-17\"'))", False, page="is"),
    _c("IS_TIME", "Semi-structured", "SELECT is_time(to_variant('10:20:30'::time))", True, page="is"),
    _c("IS_TIMESTAMP_NTZ", "Semi-structured", "SELECT is_timestamp_ntz('2024-01-17 19:00:00'::timestamp::variant)",
       True, page="is"),
    _c("IS_BINARY", "Semi-structured", "SELECT is_binary(to_variant(to_binary('41', 'HEX')))", True, page="is"),
    _c("AS_REAL (vartab)", "Semi-structured", f"SELECT avg(as_real(v)) FROM {_VARTAB}", 99.10666666666667, page="as"),
    _c("AS_DOUBLE", "Semi-structured", "SELECT as_double(to_variant(to_double(1.23)))", 1.23, page="as_double-real"),
    _c("AS_ARRAY", "Semi-structured", "SELECT as_array(to_variant(array_construct('Array-like', 'example')))",
       ["Array-like", "example"]),
    _c("AS_BOOLEAN", "Semi-structured", "SELECT as_boolean(to_variant(to_boolean(false)))", False),
    _c("AS_CHAR", "Semi-structured", "SELECT as_char(to_variant('X'))", "X", page="as_char-varchar"),
    _c("AS_VARCHAR", "Semi-structured", "SELECT as_varchar(to_variant('My VARCHAR value'))", "My VARCHAR value",
       page="as_char-varchar"),
    _c("AS_VARCHAR (not a string)", "Semi-structured", "SELECT as_varchar(parse_json('15'))", None,
       page="as_char-varchar"),
    _c("AS_DECIMAL", "Semi-structured", "SELECT as_decimal(to_variant(1.23::decimal(6, 3)), 6, 3)",
       Decimal("1.230"), page="as_decimal-number"),
    _c("AS_NUMBER", "Semi-structured", "SELECT as_number(to_variant(to_number(2.34, 6, 3)), 6, 3)", Decimal("2.340"),
       page="as_decimal-number"),
    _c("AS_DECIMAL (a DOUBLE)", "Semi-structured", "SELECT as_decimal(to_variant(3.21::double))", None,
       page="as_decimal-number"),
    _c("AS_INTEGER", "Semi-structured", "SELECT as_integer(to_variant(15))", 15),
    _c("AS_INTEGER (a DECIMAL)", "Semi-structured", "SELECT as_integer(parse_json('1.5'))", None),
    _c("AS_OBJECT", "Semi-structured", "SELECT object_keys(as_object(parse_json('{\"Tree\": \"Pine\"}')))", ["Tree"]),
    _c("AS_OBJECT (an array)", "Semi-structured", "SELECT as_object(parse_json('[1]'))", None),
    _c("AS_INTEGER (of an AS_OBJECT path)", "Semi-structured",
       "SELECT as_integer(as_object(parse_json('{\"a\": 5}')):a)", 5, page="as_integer"),
    _c("IS_INTEGER (of AS_INTEGER)", "Semi-structured", "SELECT is_integer(to_variant(as_integer(parse_json('5'))))",
       True, page="is_integer"),
    _c("AS_DATE", "Semi-structured", "SELECT as_date(to_variant('2024-01-17'::date))", D(2024, 1, 17), page="as"),
    _c("AS_TIME", "Semi-structured", "SELECT as_time(to_variant('10:20:30'::time))", dt.time(10, 20, 30), page="as"),
    _c("AS_TIMESTAMP_NTZ", "Semi-structured", "SELECT as_timestamp_ntz('2024-01-17 19:00:00'::timestamp::variant)",
       dt.datetime(2024, 1, 17, 19), page="as"),
    _c("AS_BINARY", "Semi-structured", "SELECT as_binary(to_variant(to_binary('41', 'HEX')))", b"A", page="as"),
    _c("TYPEOF", "Semi-structured", "SELECT typeof(parse_json('1'))", "INTEGER"),
    # The examples on Snowflake's TYPEOF page, row by row.
    _c("TYPEOF (JSON null)", "Semi-structured", "SELECT typeof(parse_json('null'))", "NULL_VALUE", page="typeof"),
    _c("TYPEOF (SQL NULL)", "Semi-structured", "SELECT typeof(NULL::variant)", None, page="typeof"),
    _c("TYPEOF (BOOLEAN)", "Semi-structured", "SELECT typeof(parse_json('true'))", "BOOLEAN", page="typeof"),
    _c("TYPEOF (INTEGER)", "Semi-structured", "SELECT typeof(parse_json('-17'))", "INTEGER", page="typeof"),
    _c("TYPEOF (DECIMAL)", "Semi-structured", "SELECT typeof(parse_json('123.12'))", "DECIMAL", page="typeof"),
    _c("TYPEOF (DOUBLE)", "Semi-structured", "SELECT typeof(parse_json('1.912e2'))", "DOUBLE", page="typeof"),
    _c("TYPEOF (VARCHAR)", "Semi-structured", "SELECT typeof(parse_json('\"Om ara pa ca na dhih\"  '))", "VARCHAR",
       page="typeof"),
    _c("TYPEOF (ARRAY)", "Semi-structured", "SELECT typeof(parse_json('[-1, 12, 289, 2188, false,]'))", "ARRAY",
       page="typeof"),
    _c("TYPEOF (OBJECT)", "Semi-structured", "SELECT typeof(parse_json('{ \"x\" : \"abc\", \"y\" : false, \"z\": 10} '))",
       "OBJECT", page="typeof"),
    _c("TYPEOF (VARCHAR cast to VARIANT)", "Semi-structured", "SELECT typeof('check in'::variant)", "VARCHAR",
       page="typeof"),
    _c("TYPEOF (TIMESTAMP cast to VARIANT)", "Semi-structured",
       "SELECT typeof('2024-01-17 19:00:00.000'::timestamp::variant)", "TIMESTAMP_NTZ", page="typeof"),
    _c("TYPEOF (path to an exponent)", "Semi-structured", "SELECT typeof(parse_json('{\"a\": 1.912e2}'):a)", "DOUBLE",
       page="typeof"),
    _c("TYPEOF (path to a fraction)", "Semi-structured", "SELECT typeof(parse_json('{\"a\": [1, 191.2]}'):a[1])",
       "DECIMAL", page="typeof"),
    # is_timestamp's example: SELECT v, TYPEOF(v) over TO_VARIANT of each kind of timestamp, row by row
    _c("TYPEOF (TIMESTAMP_TZ)", "Semi-structured",
       "SELECT typeof(to_variant(to_timestamp_tz('2020-02-24 15:00:00.123 +01:00')))", "TIMESTAMP_TZ",
       page="is_timestamp"),
    _c("TYPEOF (TIMESTAMP_LTZ)", "Semi-structured",
       "SELECT typeof(to_variant(to_timestamp_ltz('2022-02-24 13:00:00.123 +01:00')))", "TIMESTAMP_LTZ",
       page="is_timestamp"),
    _c("TYPEOF (TIMESTAMP_NTZ)", "Semi-structured",
       "SELECT typeof(to_variant(to_timestamp('2023-02-24 12:00:00.456')))", "TIMESTAMP_NTZ", page="is_timestamp"),
    _c("TYPEOF (DATE)", "Semi-structured", "SELECT typeof(to_variant(to_date('2024-02-24')))", "DATE",
       page="is_timestamp"),
    _c("TYPEOF (stored value with an exponent)", "Semi-structured",
       "SELECT typeof(v:a) FROM (SELECT parse_json('{\"a\": 1.912e2}') AS v)", "DOUBLE", "untranslated",
       "Refused by SFN-034 when the query reaches the value: DuckDB doesn't keep how a stored JSON number was "
       "written. TYPEOF of PARSE_JSON, or of a path into PARSE_JSON of text in the query, is exact.", page="typeof"),
    _c("LISTAGG", "Aggregate", f"SELECT listagg(x, ',') WITHIN GROUP (ORDER BY x) FROM {_values(repr('b'), repr('a'))}",
       "a,b"),
    _c("ARRAY_AGG", "Aggregate", f"SELECT array_agg(x) WITHIN GROUP (ORDER BY x) FROM {_values(2, 1)}", [1, 2]),
    _c("COUNT_IF", "Aggregate", f"SELECT count_if(x > 1) FROM {_values(1, 2, 3)}", 2),
    _c("MEDIAN", "Aggregate", f"SELECT median(x) FROM {_values(1, 2, 3, 4)}", 2.5),
    _c("BOOLOR_AGG", "Aggregate", f"SELECT boolor_agg(x) FROM {_values('TRUE', 'FALSE')}", True),
    _c("ANY_VALUE", "Aggregate", f"SELECT any_value(x) FROM {_values(1)}", 1),
    _c("GROUPING (grouping sets, docs example)", "Aggregate",
       "SELECT listagg(concat_ws('/', coalesce(col_x::string, '-'), coalesce(col_y::string, '-'), s::string, gx::string, gy::string, gxy::string), ' ') WITHIN GROUP (ORDER BY col_x NULLS LAST, col_y NULLS LAST) FROM (SELECT col_x, col_y, sum(col_z) AS s, grouping(col_x) AS gx, grouping(col_y) AS gy, grouping(col_x, col_y) AS gxy FROM (SELECT column1 AS col_x, column2 AS col_y, column3 AS col_z FROM VALUES (1, 2, 1), (1, 2, 3), (2, 1, 10), (2, 2, 11), (2, 2, 3)) GROUP BY GROUPING SETS ((col_x), (col_y), ()))",
       "1/-/4/0/1/1 2/-/24/0/1/1 -/1/10/1/0/2 -/2/18/1/0/2 -/-/28/1/1/3", page="grouping"),
    _c("GROUPING (rollup, docs example)", "Aggregate",
       "SELECT listagg(concat_ws('/', coalesce(col_x::string, '-'), coalesce(col_y::string, '-'), s::string, gx::string, gy::string, gxy::string), ' ') WITHIN GROUP (ORDER BY col_x NULLS LAST, col_y NULLS LAST) FROM (SELECT col_x, col_y, sum(col_z) AS s, grouping(col_x) AS gx, grouping(col_y) AS gy, grouping(col_x, col_y) AS gxy FROM (SELECT column1 AS col_x, column2 AS col_y, column3 AS col_z FROM VALUES (1, 2, 1), (1, 2, 3), (2, 1, 10), (2, 2, 11), (2, 2, 3)) GROUP BY ROLLUP (col_x, col_y))",
       "1/2/4/0/0/0 1/-/4/0/1/1 2/1/10/0/0/0 2/2/14/0/0/0 2/-/24/0/1/1 -/-/28/1/1/3", page="grouping"),
    _c("GROUPING_ID (docs example)", "Aggregate",
       "SELECT listagg(concat_ws('/', coalesce(col_x::string, '-'), coalesce(col_y::string, '-'), s::string, gx::string, gy::string, gxy::string), ' ') WITHIN GROUP (ORDER BY col_x NULLS LAST, col_y DESC NULLS FIRST) FROM (SELECT col_x, col_y, sum(col_z) AS s, grouping_id(col_x) AS gx, grouping_id(col_y) AS gy, grouping_id(col_x, col_y) AS gxy FROM (SELECT column1 AS col_x, column2 AS col_y, column3 AS col_z FROM VALUES (1, 2, 1), (1, 2, 3), (2, 1, 10), (2, 2, 11), (2, 2, 3)) GROUP BY GROUPING SETS ((col_x), (col_y), ()))",
       "1/-/4/0/1/1 2/-/24/0/1/1 -/-/28/1/1/3 -/2/18/1/0/2 -/1/10/1/0/2", page="grouping_id"),
    _c("APPROX_COUNT_DISTINCT", "Aggregate", f"SELECT approx_count_distinct(x) FROM {_values(1, 1, 2)}", 2),
    _c("HLL", "Aggregate", f"SELECT hll(x) FROM {_values(1, 1, 2)}", 2),
    _c("HLL (DISTINCT has no effect)", "Aggregate", f"SELECT hll(DISTINCT x) FROM {_values(1, 1, 2)}", 2, page="hll"),
    _c("DATASKETCHES_HLL (docs example)", "Aggregate",
       f"SELECT datasketches_hll(x) FROM {_values(1, 2, 2, 2, 1, 1, 4, 4, 5)}", 4.00000003, "differs",
       "SFD-010: DuckDB's estimate is 4.0, where Snowflake's sketch gives 4.00000003.", page="datasketches_hll"),
    _c("APPROX_PERCENTILE (docs example, 0.1)", "Aggregate",
       f"SELECT approx_percentile(x, 0.1) FROM {_values(*range(11))}", 1.5, "differs",
       "SFD-010: DuckDB's approx_quantile gives 1.0.", page="approx_percentile"),
    _c("APPROX_PERCENTILE (docs example, 0.5)", "Aggregate",
       f"SELECT approx_percentile(x, 0.5) FROM {_values(*range(11))}", 5.5, "differs",
       "SFD-010: DuckDB's approx_quantile gives 5.0.", page="approx_percentile"),
    _c("APPROX_PERCENTILE (docs example, 0.999)", "Aggregate",
       f"SELECT approx_percentile(x, 0.999) FROM {_values(*range(11))}", 10.5, "differs",
       "SFD-010: DuckDB's approx_quantile gives 10.0, the largest value; Snowflake's estimate goes past it.",
       page="approx_percentile"),
    _c("BITMAP_BUCKET_NUMBER", "Numeric", "SELECT bitmap_bucket_number(1)", 1),
    _c("BITMAP_BUCKET_NUMBER (last of bucket 1)", "Numeric", "SELECT bitmap_bucket_number(32768)", 1,
       page="bitmap_bucket_number"),
    _c("BITMAP_BUCKET_NUMBER (first of bucket 2)", "Numeric", "SELECT bitmap_bucket_number(32769)", 2,
       page="bitmap_bucket_number"),
    _c("BITMAP_BIT_POSITION", "Numeric", "SELECT bitmap_bit_position(1)", 0),
    _c("BITMAP_BIT_POSITION (last of bucket 1)", "Numeric", "SELECT bitmap_bit_position(32768)", 32767,
       page="bitmap_bit_position"),
    _c("BITMAP_BIT_POSITION (first of bucket 2)", "Numeric", "SELECT bitmap_bit_position(32769)", 0,
       page="bitmap_bit_position"),
    _c("STDDEV_SAMP", "Aggregate", f"SELECT stddev_samp(x) FROM {_values(1, 3)}", 2 ** 0.5, page="stddev_samp"),
    _c("RATIO_TO_REPORT", "Aggregate", f"SELECT ratio_to_report(x) OVER () FROM {_values(1, 3)} ORDER BY x LIMIT 1",
       0.25),
    _c("RATIO_TO_REPORT (docs example)", "Aggregate",
       f"SELECT ratio_to_report(x) OVER (ORDER BY x) FROM {_values(300, 250, 450, 'NULL')} "
       "ORDER BY x DESC NULLS LAST LIMIT 1", 0.45),
    _c("RATIO_TO_REPORT (null value)", "Aggregate",
       f"SELECT ratio_to_report(x) OVER () FROM {_values(300, 'NULL')} ORDER BY x NULLS FIRST LIMIT 1", None),
    _c("RATIO_TO_REPORT (sum is 0)", "Aggregate", f"SELECT ratio_to_report(x) OVER () FROM {_values(1, -1)} LIMIT 1",
       None),
    _c("RATIO_TO_REPORT (by partition)", "Aggregate",
       "SELECT ratio_to_report(column2) OVER (PARTITION BY column1) FROM VALUES ('a', 2), ('a', 3), ('b', 7) "
       "ORDER BY column1, column2 LIMIT 1", 0.4),
    _c("HAVERSINE", "Geospatial", "SELECT haversine(0, 0, 0, 0)", 0),
    _c("HAVERSINE (docs example)", "Geospatial", "SELECT haversine(40.7127, -74.0059, 34.0500, -118.2500)",
       3936.385096389, page="haversine"),
    _c("LATERAL FLATTEN (docs example)", "Semi-structured",
       "SELECT listagg(key || '|' || path || '|' || coalesce(to_varchar(index), 'NULL'), ',') WITHIN GROUP (ORDER BY "
       "path) FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1, \"b\":[77,88], \"c\": {\"d\":\"X\"}}')))",
       "a|a|NULL,b|b|NULL,c|c|NULL", page="flatten"),
    _c("LATERAL FLATTEN (PATH, docs example)", "Semi-structured",
       "SELECT listagg(coalesce(key, 'NULL') || '|' || path || '|' || index || '|' || value, ',') WITHIN GROUP "
       "(ORDER BY index) FROM TABLE(FLATTEN(INPUT => PARSE_JSON('{\"a\":1, \"b\":[77,88]}'), PATH => 'b'))",
       "NULL|b[0]|0|77,NULL|b[1]|1|88", page="flatten"),
    _c("LATERAL FLATTEN (empty array, docs example)", "Semi-structured",
       "SELECT count(*) FROM TABLE(FLATTEN(INPUT => PARSE_JSON('[]')))", 0, page="flatten"),
    _c("LATERAL FLATTEN (OUTER, docs example)", "Semi-structured",
       "SELECT coalesce(key, 'NULL') || '|' || path || '|' || coalesce(to_varchar(index), 'NULL') || '|' || "
       "coalesce(to_varchar(value), 'NULL') || '|' || to_varchar(this) FROM TABLE(FLATTEN(INPUT => "
       "PARSE_JSON('[]'), OUTER => TRUE))", "NULL||NULL|NULL|[]", page="flatten"),
    _c("LATERAL FLATTEN (nested, docs example)", "Semi-structured",
       "SELECT listagg(p.id || ':' || f1.value:type::string || '=' || f1.value:content::string, ',') WITHIN GROUP "
       "(ORDER BY p.id, f1.index) FROM (SELECT column1 AS id, PARSE_JSON(column2) AS c FROM VALUES "
       "(12712555, '{\"contact\": [{\"business\": [{\"type\": \"phone\", \"content\": \"555-1234\"}, "
       "{\"type\": \"email\", \"content\": \"j.smith@example.com\"}]}]}')) p, LATERAL FLATTEN(INPUT => p.c, "
       "PATH => 'contact') f, LATERAL FLATTEN(INPUT => f.value:business) f1",
       "12712555:phone=555-1234,12712555:email=j.smith@example.com", page="flatten"),
    _c("CONDITIONAL_CHANGE_EVENT (docs example)", "Window",
       f"SELECT listagg(e, ',') WITHIN GROUP (ORDER BY province, o_col) FROM (SELECT province, o_col, "
       f"conditional_change_event(o_col) OVER (PARTITION BY province ORDER BY o_col) AS e FROM {_TABLE1})",
       "0,0,1,1,2,3,3,0"),
    _c("CONDITIONAL_CHANGE_EVENT (of a condition)", "Window",
       f"SELECT listagg(e, ',') WITHIN GROUP (ORDER BY province, o_col) FROM (SELECT province, o_col, "
       f"conditional_change_event(o_col < 15) OVER (PARTITION BY province ORDER BY o_col) AS e FROM {_TABLE1})",
       "0,0,0,0,0,1,1,0"),
    _c("CONDITIONAL_CHANGE_EVENT (power changes)", "Window",
       "SELECT listagg(e, ',') WITHIN GROUP (ORDER BY ts) FROM (SELECT ts, conditional_change_event(voltage = 0) "
       "OVER (ORDER BY ts) AS e FROM VALUES (1, 120), (2, 120), (3, 0), (4, 0), (5, 0), (6, 0), (7, 120) "
       "AS v(ts, voltage))", "0,0,1,1,1,1,2"),
    _c("CONDITIONAL_TRUE_EVENT (docs example)", "Window",
       f"SELECT listagg(e, ',') WITHIN GROUP (ORDER BY province, o_col) FROM (SELECT province, o_col, "
       f"conditional_true_event(o_col) OVER (PARTITION BY province ORDER BY o_col) AS e FROM {_TABLE1})",
       "0,0,1,2,3,4,4,1"),
    _c("CONDITIONAL_TRUE_EVENT (of a condition)", "Window",
       f"SELECT listagg(e, ',') WITHIN GROUP (ORDER BY province, o_col) FROM (SELECT province, o_col, "
       f"conditional_true_event(o_col > 20) OVER (PARTITION BY province ORDER BY o_col) AS e FROM {_TABLE1})",
       "0,0,0,0,0,0,0,1"),
    _c("CONDITIONAL_TRUE_EVENT (of LAG)", "Window",
       "SELECT listagg(e, ',') WITHIN GROUP (ORDER BY p, o) FROM (SELECT p, o, conditional_true_event("
       "lag(o) OVER (PARTITION BY p ORDER BY o) > 1) OVER (PARTITION BY p ORDER BY o) AS e FROM VALUES "
       "(100, 1), (100, 2), (100, 3), (100, 4), (100, 5), (100, 6), (200, 7), (200, 8), (200, 9), (200, 10), "
       "(200, 11), (300, 12), (400, 13) AS v(p, o))", "0,0,1,2,3,4,0,1,2,3,4,0,0"),
    _c("INTERPOLATE_FFILL (docs example)", "Window",
       f"SELECT listagg(coalesce(to_varchar(f), 'NULL'), ',') WITHIN GROUP (ORDER BY city, observed) FROM "
       f"(SELECT city, observed, interpolate_ffill(temperature) OVER (PARTITION BY city ORDER BY observed) AS f "
       f"FROM {_MARCH})", "NULL,48,48,49,49,49,51,51,54,44,46,46,46,52,54", page="interpolate_bfill"),
    _c("INTERPOLATE_BFILL (docs example)", "Window",
       f"SELECT listagg(coalesce(to_varchar(b), 'NULL'), ',') WITHIN GROUP (ORDER BY city, observed) FROM "
       f"(SELECT city, observed, interpolate_bfill(temperature) OVER (PARTITION BY city ORDER BY observed) AS b "
       f"FROM {_MARCH})", "48,48,49,49,51,51,51,54,54,44,46,52,52,52,54", page="interpolate_bfill"),
    _c("INTERPOLATE_LINEAR (between two values)", "Window",
       f"SELECT listagg(to_varchar(round(l)::int), ',') WITHIN GROUP (ORDER BY observed) FROM (SELECT observed, "
       f"interpolate_linear(temperature) OVER (PARTITION BY city ORDER BY observed) AS l FROM {_MARCH} "
       f"WHERE city = 'South Lake Tahoe')", "44,46,48,50,52,54", page="interpolate_bfill"),
    # WR-305: the documented examples for functions the list showed as Not checked.
    _c("BIT_LENGTH", "String", "SELECT bit_length('abc')", 24),
    _c("BIT_LENGTH (two-byte character)", "String", "SELECT bit_length('Δ')", 16, page="bit_length"),
    _c("RPAD", "String", "SELECT rpad('Hi', 10, '_')", "Hi________"),
    _c("RPAD (cut to length)", "String", "SELECT rpad('Twelve Dollars', 10, '$')", "Twelve Dol", page="rpad"),
    _c("RPAD (two-character pad)", "String", "SELECT rpad('123.50', 19, '*_')", "123.50*_*_*_*_*_*_*", page="rpad"),
    _c("RTRIM", "String", "SELECT rtrim('$125.00', '0.')", "$125"),
    _c("RTRIM (spaces)", "String", "SELECT rtrim('Trailing Spaces#  ')", "Trailing Spaces#", page="rtrim"),
    _c("RTRIM (characters)", "String", "SELECT rtrim('Trailing Spaces#  ', '# ')", "Trailing Spaces", page="rtrim"),
    _c("RTRIMMED_LENGTH", "String", "SELECT rtrimmed_length(' ABCD ')", 5),
    _c("STRTOK_TO_ARRAY", "String", "SELECT strtok_to_array('a.b.c', '.')", ["a", "b", "c"]),
    _c("STRTOK_TO_ARRAY (two delimiters)", "String", "SELECT strtok_to_array('user@snowflake.com', '.@')",
       ["user", "snowflake", "com"], page="strtok_to_array"),
    _c("UNICODE", "String", "SELECT unicode('a')", 97),
    _c("UNICODE (snowflake)", "String", "SELECT unicode('❄')", 10052, page="unicode"),
    _c("UNICODE (first character)", "String", "SELECT unicode('cde')", 99, page="unicode"),
    _c("UNICODE (empty)", "String", "SELECT unicode('')", 0, page="unicode"),
    _c("JAROWINKLER_SIMILARITY", "String", "SELECT jarowinkler_similarity('Gute nacht', 'Ich weis nicht')", 56,
       "untranslated", "Refused by SFN-031: Snowflake doesn't document its score, and DuckDB's gives 57 for this pair "
       "from Snowflake's example, 94 for 'Ich weiß nicht' and 'Ich weiss nicht' (97), and 72 for 'święta' and "
       "'swieta' (77). EDITDISTANCE runs here.", page="jarowinkler_similarity"),
    _c("LOWER", "String", "SELECT lower('The Quick Gray Fox')", "the quick gray fox"),
    _c("LOWER (accents)", "String", "SELECT lower('ÄäÖößÜü')", "ääöößüü", page="lower"),
    _c("LOWER (Cyrillic)", "String", "SELECT lower('АаБбВвГгДдЕеЁёЖжЗзИиЙй')", "ааббввггддееёёжжззиийй", page="lower"),
    _c("SUBSTRING", "String", "SELECT substring('testing 1 2 3', 9, 3)", "1 2", page="substr"),
    _c("SUBSTRING (to the end)", "String", "SELECT substring('800-555-0100', 5)", "555-0100", page="substr"),
    _c("BASE64_ENCODE", "String", "SELECT base64_encode('Snowflake')", "U25vd2ZsYWtl"),
    _c("BASE64_ENCODE (HELLO)", "String", "SELECT base64_encode('HELLO')", "SEVMTE8=", page="base64_encode"),
    _c("BASE64_DECODE_STRING", "String", "SELECT base64_decode_string('U25vd2ZsYWtl')", "Snowflake"),
    _c("HEX_ENCODE", "String", "SELECT hex_encode('Snowflake')", "536E6F77666C616B65"),
    _c("HEX_ENCODE (lower case)", "String", "SELECT hex_encode('Snowflake', 0)", "536e6f77666c616b65", page="hex_encode"),
    _c("HEX_DECODE_STRING", "String", "SELECT hex_decode_string('536E6F77666C616B65')", "Snowflake"),
    _c("HEX_DECODE_STRING (lower case)", "String", "SELECT hex_decode_string('536e6f77666c616b65')", "Snowflake",
       page="hex_decode_string"),
    _c("TIME_FROM_PARTS", "Date and time", "SELECT time_from_parts(0, 100, 0)", dt.time(1, 40)),
    _c("TIME_FROM_PARTS (seconds over 59)", "Date and time", "SELECT time_from_parts(12, 0, 12345)", dt.time(15, 25, 45),
       page="time_from_parts"),
    _c("TIMESTAMP_FROM_PARTS", "Date and time", "SELECT timestamp_from_parts(2013, 4, 5, 12, 0, -3600)",
       dt.datetime(2013, 4, 5, 11, 0)),
    _c("TIMESTAMP_NTZ_FROM_PARTS", "Date and time",
       "SELECT timestamp_ntz_from_parts(to_date('2013-04-05'), to_time('12:00:00'))", dt.datetime(2013, 4, 5, 12, 0),
       page="timestamp_from_parts"),
    _c("EXTRACT", "Date and time", "SELECT extract(year FROM to_timestamp('2024-04-10T23:39:20.123-07:00'))", 2024),
    _c("QUARTER", "Date and time", "SELECT quarter('2025-04-11T23:39:20.123-07:00'::timestamp)", 2, page="year"),
    _c("MONTH", "Date and time", "SELECT month('2025-04-11T23:39:20.123-07:00'::timestamp)", 4, page="year"),
    _c("DAY", "Date and time", "SELECT day('2025-04-11T23:39:20.123-07:00'::timestamp)", 11, page="year"),
    _c("DAYOFMONTH", "Date and time", "SELECT dayofmonth('2025-04-11T23:39:20.123-07:00'::timestamp)", 11, page="year"),
    _c("DAYOFYEAR", "Date and time", "SELECT dayofyear('2025-04-11T23:39:20.123-07:00'::timestamp)", 101, page="year"),
    # The default WEEK_OF_YEAR_POLICY (0) and WEEK_START (0): the docs' second WEEK example.
    _c("WEEK", "Date and time", "SELECT week('2016-01-02T23:39:20.123-07:00'::timestamp)", 53, page="year"),
    _c("WEEKISO", "Date and time", "SELECT weekiso('2016-01-02T23:39:20.123-07:00'::timestamp)", 53, page="year"),
    _c("WEEKOFYEAR", "Date and time", "SELECT weekofyear('2016-01-02T23:39:20.123-07:00'::timestamp)", 53, page="year"),
    _c("YEAROFWEEK", "Date and time", "SELECT yearofweek('2016-01-02T23:39:20.123-07:00'::timestamp)", 2015, page="year"),
    _c("YEAROFWEEKISO", "Date and time", "SELECT yearofweekiso('2016-01-02T23:39:20.123-07:00'::timestamp)", 2015,
       page="year"),
    _c("DAYOFWEEKISO", "Date and time", "SELECT dayofweekiso('2025-04-05T23:39:20.123-07:00'::timestamp)", 6, page="year"),
    _c("MINUTE", "Date and time", "SELECT minute('2025-04-08T23:39:20.123-07:00'::timestamp)", 39,
       page="hour-minute-second"),
    _c("SECOND", "Date and time", "SELECT second('2025-04-08T23:39:20.123-07:00'::timestamp)", 20,
       page="hour-minute-second"),
    _c("MONTHS_BETWEEN", "Date and time", "SELECT months_between('2019-03-15'::date, '2019-02-15'::date)", 1),
    _c("MONTHS_BETWEEN (month ends)", "Date and time", "SELECT months_between('2019-03-31'::date, '2019-02-28'::date)", 1,
       page="months_between"),
    _c("MONTHS_BETWEEN (backwards)", "Date and time", "SELECT months_between('2019-02-01'::date, '2019-03-01'::date)", -1,
       page="months_between"),
    _c("TIMEDIFF", "Date and time", "SELECT timediff(year, '2017-01-01', '2019-01-01')", 2),
    _c("TIMEDIFF (months)", "Date and time", "SELECT timediff(month, '2017-01-1', '2017-12-31')", 11, page="timediff"),
    _c("DATE", "Date and time", "SELECT date('2024-05-10')", D(2024, 5, 10), page="to_date"),
    _c("DATE (format)", "Date and time", "SELECT date('2024.05.10', 'YYYY.MM.DD')", D(2024, 5, 10), page="to_date"),
    _c("DATE (month first)", "Date and time", "SELECT date('05/10/2024', 'MM/DD/YYYY')", D(2024, 5, 10), page="to_date"),
    _c("TO_TIMESTAMP (milliseconds)", "Date and time", "SELECT to_timestamp(1000000000, 3)",
       dt.datetime(1970, 1, 12, 13, 46, 40)),
    _c("TO_TIMESTAMP (seconds as text)", "Date and time", "SELECT to_timestamp('31536000')", dt.datetime(1971, 1, 1),
       page="to_timestamp"),
    _c("TO_TIMESTAMP_NTZ", "Date and time", "SELECT to_timestamp_ntz('2024-04-05 01:02:03')",
       dt.datetime(2024, 4, 5, 1, 2, 3), page="to_timestamp"),
    _c("TO_TIMESTAMP_NTZ (seconds)", "Date and time", "SELECT to_timestamp_ntz(40 * 365.25 * 86400)",
       dt.datetime(2010, 1, 1), page="to_timestamp"),
    _c("BOOLAND", "Conditional", "SELECT booland(1, -2)", True),
    _c("BOOLAND (zero and NULL)", "Conditional", "SELECT booland(0, NULL)", False, page="booland"),
    _c("BOOLAND (NULL)", "Conditional", "SELECT booland(NULL, 3)", None, page="booland"),
    _c("BOOLAND (rounded to zero)", "Conditional", "SELECT booland(2, 0.3)", False, page="booland"),
    _c("BOOLNOT", "Conditional", "SELECT boolnot(0)", True),
    _c("BOOLNOT (non-zero)", "Conditional", "SELECT boolnot(10)", False, page="boolnot"),
    _c("BOOLNOT (rounded to zero)", "Conditional", "SELECT boolnot(0.3)", True, page="boolnot"),
    _c("BOOLOR", "Conditional", "SELECT boolor(0, 2)", True),
    _c("BOOLOR (non-zero and NULL)", "Conditional", "SELECT boolor(3, NULL)", True, page="boolor"),
    _c("BOOLOR (NULL)", "Conditional", "SELECT boolor(NULL, 0)", None, page="boolor"),
    _c("BOOLOR (rounded to zero)", "Conditional", "SELECT boolor(0.4, 0.3)", False, page="boolor"),
    _c("BOOLXOR", "Conditional", "SELECT boolxor(2, 0)", True),
    _c("BOOLXOR (both true)", "Conditional", "SELECT boolxor(1, -1)", False, page="boolxor"),
    _c("BOOLXOR (NULL)", "Conditional", "SELECT boolxor(NULL, 3)", None, page="boolxor"),
    _c("BOOLXOR (rounded to zero)", "Conditional", "SELECT boolxor(0, 0.3)", False, page="boolxor"),
    _c("GREATEST_IGNORE_NULLS", "Conditional", "SELECT greatest_ignore_nulls(2, 4, -1, NULL)", 4),
    _c("GREATEST_IGNORE_NULLS (float)", "Conditional", "SELECT greatest_ignore_nulls(1, 2, 3, 4.25)", 4.25,
       page="greatest_ignore_nulls"),
    _c("LEAST_IGNORE_NULLS", "Conditional", "SELECT least_ignore_nulls(2, 4, -1, NULL)", -1),
    _c("LEAST_IGNORE_NULLS (float)", "Conditional", "SELECT least_ignore_nulls(3, 6, NULL, -2.75)", -2.75,
       page="least_ignore_nulls"),
    _c("MAX_BY", "Aggregate", f"SELECT max_by(e, s) FROM {_EMPLOYEES}", 900),
    _c("MAX_BY (top 3)", "Aggregate", f"SELECT max_by(e, s, 3) FROM {_EMPLOYEES}", [900, 1001, 1020], page="max_by"),
    _c("MIN_BY", "Aggregate", f"SELECT min_by(e, s) FROM {_EMPLOYEES}", 1030),
    _c("MIN_BY (bottom 3)", "Aggregate", f"SELECT min_by(e, s, 3) FROM {_EMPLOYEES}", [1030, 1020, 1001], page="min_by"),
    _c("MODE", "Aggregate", "SELECT mode(v) FROM VALUES (10), (10), (10), (10), (20), (21), (20), (20), (25), (30) AS t(v)",
       10),
    _c("MODE (NULL)", "Aggregate", "SELECT mode(v) FROM VALUES (NULL::int) AS t(v)", None, page="mode"),
    _c("PERCENTILE_CONT", "Aggregate",
       "SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY v) FROM VALUES (10), (20) AS t(v)", 12.5),
    _c("PERCENTILE_CONT (four values)", "Aggregate",
       "SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY v) FROM VALUES (10), (20), (25), (30) AS t(v)", 17.5,
       page="percentile_cont"),
    _c("PERCENTILE_DISC", "Aggregate",
       "SELECT percentile_disc(0.25) WITHIN GROUP (ORDER BY v) FROM VALUES (10), (20), (25), (30) AS t(v)", 10),
    _c("PERCENTILE_DISC (five values)", "Aggregate",
       "SELECT percentile_disc(0.25) WITHIN GROUP (ORDER BY v) FROM VALUES (0), (10), (20), (30), (40) AS t(v)", 10,
       page="percentile_disc"),
    _c("BITAND_AGG", "Aggregate", f"SELECT bitand_agg(k) FROM {_BITWISE}", 8),
    _c("BITAND_AGG (decimals)", "Aggregate", f"SELECT bitand_agg(d) FROM {_BITWISE}", 1, page="bitand_agg"),
    _c("BITOR_AGG", "Aggregate", f"SELECT bitor_agg(k) FROM {_BITWISE}", 31),
    _c("BITOR_AGG (decimals)", "Aggregate", f"SELECT bitor_agg(d) FROM {_BITWISE}", 15, page="bitor_agg"),
    _c("BITXOR_AGG", "Aggregate", f"SELECT bitxor_agg(k) FROM {_BITWISE}", 31),
    _c("BITXOR_AGG (decimals)", "Aggregate", f"SELECT bitxor_agg(d) FROM {_BITWISE}", 12, page="bitxor_agg"),
    _c("BOOLAND_AGG", "Aggregate", "SELECT booland_agg(c) FROM VALUES (TRUE), (FALSE), (TRUE), (FALSE) AS t(c)", False),
    _c("BOOLAND_AGG (all true)", "Aggregate", "SELECT booland_agg(c) FROM VALUES (TRUE), (TRUE), (TRUE), (TRUE) AS t(c)",
       True, page="booland_agg"),
    _c("BOOLXOR_AGG", "Aggregate", "SELECT boolxor_agg(c) FROM VALUES (TRUE), (FALSE), (FALSE), (FALSE) AS t(c)", True),
    _c("BOOLXOR_AGG (two true)", "Aggregate", "SELECT boolxor_agg(c) FROM VALUES (TRUE), (FALSE), (TRUE), (FALSE) AS t(c)",
       False, page="boolxor_agg"),
    _c("KURTOSIS", "Aggregate", f"SELECT kurtosis(v) FROM {_AGGR}", -2.32421875),
    _c("KURTOSIS (k)", "Aggregate", f"SELECT kurtosis(k) FROM {_AGGR}", 5, page="kurtosis"),
    _c("SKEW", "Aggregate", f"SELECT round(skew(v), 11) FROM {_AGGR}", 0.05240788515, "differs",
       "Snowflake's reference shows 0.05240788515; the exact sample skewness is 0.05240784322 (SFD-013)."),
    _c("SKEW (k)", "Aggregate", f"SELECT round(skew(k), 9) FROM {_AGGR}", -2.236069766, "differs",
       "Snowflake's reference shows -2.236069766; the exact sample skewness is -2.236067977 (SFD-013).", page="skew"),
    # The docs' aggr table for REGR_*, CORR and COVAR_*: v is y, v2 is x; rows with a NULL don't count.
    _c("REGR_AVGX", "Aggregate", f"SELECT round(regr_avgx(v, v2), 9) FROM {_AGGR}", 22.666666667),
    _c("REGR_SLOPE", "Aggregate", f"SELECT round(regr_slope(v, v2), 9) FROM {_AGGR}", 0.831408776),
    _c("REGR_INTERCEPT", "Aggregate", f"SELECT round(regr_intercept(v, v2), 9) FROM {_AGGR}", 1.154734411),
    _c("REGR_R2", "Aggregate", f"SELECT round(regr_r2(v, v2), 10) FROM {_AGGR}", 0.9976905312),
    _c("CORR", "Aggregate", f"SELECT round(corr(v, v2), 10) FROM {_AGGR}", 0.9988445981),
    _c("COVAR_POP", "Aggregate", f"SELECT covar_pop(v, v2) FROM {_AGGR}", 80),
    _c("COVAR_SAMP", "Aggregate", f"SELECT covar_samp(v, v2) FROM {_AGGR}", 120),
    _c("VAR_POP", "Aggregate", "SELECT var_pop(v) FROM VALUES (10), (20), (25), (30) AS t(v)", 54.6875),
    _c("VAR_POP (one value)", "Aggregate", "SELECT var_pop(v) FROM VALUES (10) AS t(v)", 0, page="var_pop"),
    _c("VAR_SAMP", "Aggregate", "SELECT round(var_samp(v), 10) FROM VALUES (10), (20), (25), (30) AS t(v)", 72.9166666667),
    _c("VAR_SAMP (one value)", "Aggregate", "SELECT var_samp(v) FROM VALUES (10) AS t(v)", None, page="var_samp"),
    _c("STDDEV", "Aggregate", "SELECT stddev(c1) FROM VALUES (6), (10), (14) AS t(c1)", 4),
    _c("AVG", "Aggregate", f"SELECT avg(int_col) FROM {_AVG}", 1.8),
    _c("AVG (decimals)", "Aggregate", f"SELECT avg(d) FROM {_AVG}", 5.85, page="avg"),
    _c("BITNOT", "Bitwise", "SELECT bitnot(1)", -2),
    _c("BITNOT (large)", "Bitwise", "SELECT bitnot(65504)", -65505, page="bitnot"),
    _c("BITOR", "Bitwise", "SELECT bitor(2, 4)", 6),
    _c("BITXOR", "Bitwise", "SELECT bitxor(16, 24)", 8),
    _c("BITSHIFTLEFT", "Bitwise", "SELECT bitshiftleft(16, 1)", 32),
    _c("BITSHIFTRIGHT", "Bitwise", "SELECT bitshiftright(65504, 1)", 32752),
    _c("GETBIT", "Bitwise", "SELECT getbit(11, 3)", 1),
    _c("GETBIT (zero bit)", "Bitwise", "SELECT getbit(11, 2)", 0, page="getbit"),
    _c("GETBIT (high bit)", "Bitwise", "SELECT getbit(11, 100)", 0, page="getbit"),
    _c("MD5_HEX", "Hash", "SELECT md5_hex('Snowflake')", "edf1439075a83a447fb8b630ddc9c8de", page="md5"),
    _c("SHA1", "Hash", "SELECT sha1('Snowflake')", "fda76b0bcc1e87cf259b1d1e3271d76f590fb5dd"),
    _c("SHA1_HEX", "Hash", "SELECT sha1_hex('AbCd0')", "9ddb991863d53b35a52c490db256207c776ab8d8", page="sha1"),
    _c("SHA2_HEX", "Hash", "SELECT sha2_hex('AbCd0')",
       "e1d8ba27889d6782008f495473278c4f071995c5549a976e4d4f93863ce93643", page="sha2"),
    _c("CBRT", "Numeric", "SELECT round(cbrt(2), 8)", 1.25992105),
    _c("CBRT (negative)", "Numeric", "SELECT round(cbrt(-10), 8)", -2.15443469, page="cbrt"),
    _c("FACTORIAL", "Numeric", "SELECT factorial(5)", 120),
    _c("FACTORIAL (0)", "Numeric", "SELECT factorial(0)", 1, page="factorial"),
    _c("FACTORIAL (10)", "Numeric", "SELECT factorial(10)", 3628800, page="factorial"),
    _c("LOG", "Numeric", "SELECT log(2, 8)", 3),
    _c("LOG (fraction)", "Numeric", "SELECT log(2, 0.5)", -1, page="log"),
    _c("LOG (base 10)", "Numeric", "SELECT round(log(10, 20), 9)", 1.301029996, page="log"),
    _c("LN", "Numeric", "SELECT round(ln(10), 9)", 2.302585093),
    _c("LN (1)", "Numeric", "SELECT ln(1)", 0, page="ln"),
    _c("COT", "Numeric", "SELECT round(cot(pi() / 3), 10)", 0.5773502692),
    _c("ATAN2", "Numeric", "SELECT round(atan2(5, 5), 10)", 0.7853981634),
    _c("DEGREES", "Numeric", "SELECT degrees(pi())", 180),
    _c("DEGREES (1)", "Numeric", "SELECT round(degrees(1), 9)", 57.295779513, page="degrees"),
    _c("TRY_TO_BOOLEAN", "Conversion", "SELECT try_to_boolean('True')", True),
    _c("TRY_TO_BOOLEAN (not valid)", "Conversion", "SELECT try_to_boolean('Not valid')", None, page="try_to_boolean"),
    _c("TO_DECIMAL", "Conversion", "SELECT to_decimal('12.3456')", 12),
    _c("TO_DECIMAL (scale)", "Conversion", "SELECT to_decimal('12.3456', 10, 1)", Decimal("12.3"), page="to_decimal"),
    _c("TO_NUMERIC", "Conversion", "SELECT to_numeric('98.76546')", 99, page="to_decimal"),
    _c("TO_NUMERIC (scale)", "Conversion", "SELECT to_numeric('98.76546', 10, 1)", Decimal("98.8"), page="to_decimal"),
    _c("TRY_TO_DATE", "Conversion", "SELECT try_to_date('2024-05-10')", D(2024, 5, 10)),
    _c("TRY_TO_DATE (not a date)", "Conversion", "SELECT try_to_date('Invalid')", None, page="try_to_date"),
    _c("TRY_TO_DATE (format)", "Conversion", "SELECT try_to_date('05/10/2024', 'MM/DD/YYYY')", D(2024, 5, 10),
       page="try_to_date"),
    _c("TRY_TO_DATE (other format)", "Conversion", "SELECT try_to_date('2024-05-10', 'MM/DD/YYYY')", None,
       page="try_to_date"),
    _c("TO_DOUBLE", "Conversion", "SELECT to_double('2.2')", 2.2),
    _c("TO_DOUBLE (decimal)", "Conversion", "SELECT to_double(1.1)", 1.1, page="to_double"),
    _c("CHAR", "String", "SELECT char(97)", "a", page="chr"),
    _c("CHAR (snowflake)", "String", "SELECT char(10052)", "❄", page="chr"),
    _c("COUNT", "Aggregate", f"SELECT count(i_col) FROM {_BASIC}", 4),
    _c("COUNT (star)", "Aggregate", f"SELECT count(*) FROM {_BASIC}", 6, page="count"),
    _c("COUNT (distinct)", "Aggregate", f"SELECT count(DISTINCT j_col) FROM {_BASIC}", 2, page="count"),
    _c("MIN", "Aggregate", f"SELECT min(d) FROM {_SAMPLE}", "1"),
    _c("MAX", "Aggregate", f"SELECT max(d) FROM {_SAMPLE}", "7"),
    _c("SUM", "Aggregate", "SELECT sum(d) FROM VALUES (1.1), (10), (2.2), (NULL), (9) AS t(d)", Decimal("22.3")),
    _c("STDDEV_POP", "Aggregate", "SELECT round(stddev_pop(c1), 9) FROM VALUES (6), (10), (14) AS t(c1)", 3.265986375,
       "differs", "Snowflake's reference shows 3.265986375; the square root of 32/3 is 3.265986324 (SFD-013)."),
    _c("VARIANCE", "Aggregate", "SELECT round(variance(v), 10) FROM VALUES (10), (20), (25), (30) AS t(v)",
       72.9166666667, page="var_samp"),
    _c("REGR_COUNT", "Aggregate", f"SELECT regr_count(v, v2) FROM {_AGGR}", 3),
    _c("REGR_AVGY", "Aggregate", f"SELECT regr_avgy(v, v2) FROM {_AGGR}", 20),
    _c("REGR_SXX", "Aggregate", f"SELECT round(regr_sxx(v, v2), 9) FROM {_AGGR}", 288.666666667),
    _c("REGR_SXY", "Aggregate", f"SELECT regr_sxy(v, v2) FROM {_AGGR}", 240),
    _c("REGR_SYY", "Aggregate", f"SELECT regr_syy(v, v2) FROM {_AGGR}", 200),
    _c("EXP", "Numeric", "SELECT round(exp(1), 9)", 2.718281828),
    _c("EXP (of LN)", "Numeric", "SELECT round(exp(ln(10)), 9)", 10, page="exp"),
    _c("POW", "Numeric", "SELECT pow(2, 3)", 8),
    _c("POW (fraction)", "Numeric", "SELECT round(pow(2, 0.5), 9)", 1.414213562, page="pow"),
    _c("POW (negative)", "Numeric", "SELECT pow(2, -1)", 0.5, page="pow"),
    _c("ACOS", "Numeric", "SELECT round(acos(0.5), 9)", 1.047197551),
    _c("ACOS (1)", "Numeric", "SELECT acos(1)", 0, page="acos"),
    _c("ASIN", "Numeric", "SELECT round(asin(0.5), 10)", 0.5235987756),
    _c("ATAN", "Numeric", "SELECT round(atan(1), 10)", 0.7853981634),
    _c("SIN", "Numeric", "SELECT round(sin(pi() / 3), 10)", 0.8660254038),
    _c("SIN (90 degrees)", "Numeric", "SELECT sin(radians(90))", 1, page="sin"),
    _c("COS", "Numeric", "SELECT round(cos(pi() / 3), 10)", 0.5),
    _c("COS (0)", "Numeric", "SELECT cos(0)", 1, page="cos"),
    _c("TAN", "Numeric", "SELECT round(tan(pi() / 3), 9)", 1.732050808),
    _c("SINH", "Numeric", "SELECT round(sinh(1.5), 9)", 2.129279455),
    _c("COSH", "Numeric", "SELECT round(cosh(1.5), 9)", 2.352409615),
    _c("TANH", "Numeric", "SELECT round(tanh(1.5), 10)", 0.9051482536),
    _c("ASINH", "Numeric", "SELECT round(asinh(2.129279455), 9)", 1.5),
    _c("ACOSH", "Numeric", "SELECT round(acosh(2.352409615), 9)", 1.5),
    _c("ATANH", "Numeric", "SELECT round(atanh(0.9051482536), 9)", 1.5),
    _c("RADIANS", "Numeric", "SELECT round(radians(60), 9)", 1.047197551),
    _c("RADIANS (720)", "Numeric", "SELECT round(radians(720), 9)", 12.566370614, page="radians"),
    _c("PI", "Numeric", "SELECT round(pi(), 9)", 3.141592654),
    _c("TRUNCATE", "Numeric", "SELECT truncate(-975.975)", -975, page="trunc"),
    _c("TRUNCATE (scale)", "Numeric", "SELECT truncate(135.135, 1)", Decimal("135.1"), page="trunc"),
    _c("TRUNCATE (negative scale)", "Numeric", "SELECT truncate(-975.975, -1)", -970, page="trunc"),
    _c("RANK", "Window", f"SELECT list(r ORDER BY b DESC, f) FROM (SELECT f, b, rank() OVER (ORDER BY b DESC) AS r FROM {_CORN})",
       [1, 2, 3, 3, 5]),
    _c("DENSE_RANK", "Window",
       f"SELECT list(r ORDER BY b DESC, f) FROM (SELECT f, b, dense_rank() OVER (ORDER BY b DESC) AS r FROM {_CORN})",
       [1, 2, 3, 3, 4]),
    _c("LAG", "Window", "SELECT list(p ORDER BY c1) FROM (SELECT c1, lag(c2) IGNORE NULLS OVER (ORDER BY c1) AS p "
       "FROM VALUES (1, 5), (2, 4), (3, NULL), (4, 2), (5, NULL), (6, NULL), (7, 6) AS t(c1, c2))",
       [None, 5, 4, 4, 2, 2, 2]),
    _c("LAG (default)", "Window", "SELECT list(d ORDER BY y) FROM (SELECT y, r - lag(r, 1, 0) OVER (ORDER BY y) AS d "
       "FROM VALUES (2010, 1000.00), (2011, 1500.00), (2012, 500.00), (2013, 750.00) AS t(y, r))",
       [1000, 500, -1000, 250], page="lag"),
    _c("NTH_VALUE", "Window", "SELECT list(n ORDER BY a, b) FROM (SELECT a, b, nth_value(b, 2) OVER (PARTITION BY a ORDER BY b) "
       "AS n FROM VALUES (1, 10), (1, 11), (1, 12), (2, 20), (2, 21), (2, 22) AS t(a, b))", [11, 11, 11, 21, 21, 21]),
    # WR-307: the rest, each from its page's own example (or a page that uses it, named by page=)
    _c("MD5_BINARY", "Hash", "SELECT md5_binary('Snowflake')", bytes.fromhex("EDF1439075A83A447FB8B630DDC9C8DE")),
    _c("SHA1_BINARY", "Hash", "SELECT sha1_binary('Snowflake')", bytes.fromhex("FDA76B0BCC1E87CF259B1D1E3271D76F590FB5DD")),
    # the page's table example; its row is 'AbCd0', the row SHA1_BINARY's and SHA2_HEX's pages hash to the same digests
    _c("SHA2_BINARY", "Hash", "SELECT sha2_binary('AbCd0')",
       bytes.fromhex("E1D8BA27889D6782008F495473278C4F071995C5549A976E4D4F93863CE93643")),
    _c("HEX_DECODE_BINARY", "String", "SELECT hex_decode_binary(hex_encode(md5_binary('Snowflake')))",
       bytes.fromhex("EDF1439075A83A447FB8B630DDC9C8DE")),
    _c("TO_BINARY", "Conversion", "SELECT to_binary('SNOW', 'utf-8')", bytes.fromhex("534E4F57")),
    _c("ARRAY_MAX", "Semi-structured", "SELECT array_max([20, 0, NULL, 10, NULL])", 20),
    _c("ARRAY_MIN", "Semi-structured", "SELECT array_min([20, 0, NULL, 10, NULL])", 0),
    _c("ARRAY_REMOVE", "Semi-structured", "SELECT array_size(array_remove([5, 5], 5))", 0),
    _c("MAP_SIZE", "Semi-structured", "SELECT map_size({'a':1,'b':2,'c':3}::MAP(VARCHAR,NUMBER))", 3),
    _c("MAP_CONTAINS_KEY", "Semi-structured",
       "SELECT map_contains_key('k1',{'k1':'v1','k2':'v2','k3':'v3'}::MAP(VARCHAR,VARCHAR))", True),
    _c("MAP_DELETE", "Semi-structured",
       "SELECT to_json(map_delete({'a':1,'b':2,'c':3}::MAP(VARCHAR,NUMBER),'a','b'))", '{"c":3}'),
    _c("VARIANCE_SAMP", "Aggregate", f"SELECT round(variance_samp(v2), 10) FROM {_AGGR} WHERE k = 2", 144.3333333333,
       page="var_samp"),
    _c("VARIANCE_SAMP (one row)", "Aggregate", f"SELECT variance_samp(v) FROM {_AGGR} WHERE k = 1", None,
       page="var_samp"),
    _c("VARIANCE_POP", "Aggregate", f"SELECT round(variance_pop(v), 10) FROM {_AGGR} WHERE k = 2", 54.6875,
       page="var_pop"),
    _c("VARIANCE_POP (one row)", "Aggregate", f"SELECT variance_pop(v) FROM {_AGGR} WHERE k = 1", 0, page="var_pop"),
    _c("FIRST_VALUE (IGNORE NULLS)", "Window", f"SELECT list(f ORDER BY p) FROM (SELECT item_price AS p, "
       f"first_value(item_cost) IGNORE NULLS OVER (ORDER BY item_price) AS f FROM {_MENU})", [1.5, 1.5, 1.5, 1.5],
       page="first_value"),
    _c("FIRST_VALUE (RESPECT NULLS)", "Window", f"SELECT list(f ORDER BY p) FROM (SELECT item_price AS p, "
       f"first_value(item_cost) RESPECT NULLS OVER (ORDER BY item_price) AS f FROM {_MENU})", [None, None, None, None],
       page="first_value"),
    _c("LAST_VALUE", "Window", "SELECT list(l ORDER BY column1, column2) FROM (SELECT column1, column2, "
       "last_value(column2) OVER (PARTITION BY column1 ORDER BY column2) AS l FROM VALUES (1, 10), (1, 11), (1, 12), "
       "(2, 20), (2, 21), (2, 22))", [12, 12, 12, 22, 22, 22]),
    _c("LEAD", "Window", f"SELECT list(d ORDER BY emp_id, year) FROM (SELECT emp_id, year, lead(revenue) OVER "
       f"(PARTITION BY emp_id ORDER BY year) - revenue AS d FROM {_SALES})",
       [500, -1000, 250, None, 2500, 2500, 5000, None, 300, None]),
    _c("LEAD (IGNORE NULLS)", "Window", "SELECT list(n ORDER BY c1) FROM (SELECT c1, lead(c2) IGNORE NULLS OVER "
       "(ORDER BY c1) AS n FROM VALUES (1,5), (2,4), (3,NULL), (4,2), (5,NULL), (6,NULL), (7,6) AS t1(c1, c2))",
       [4, 2, 2, 6, 6, 6, None], page="lead"),
    _c("ROW_NUMBER", "Window", f"SELECT list(r ORDER BY i) FROM (SELECT i, row_number() OVER (PARTITION BY p ORDER BY o) "
       f"AS r FROM {_QT})", [1, 2, 1, 2], page="../constructs/qualify"),
    _c("REGR_VALX", "Aggregate", "SELECT regr_valx(1, 10)", 10),
    _c("REGR_VALX (NULL y)", "Aggregate", "SELECT regr_valx(NULL, 10)", None, page="regr_valx"),
    _c("REGR_VALY", "Aggregate", "SELECT regr_valy(1, 10)", 1),
    _c("REGR_VALY (NULL x)", "Aggregate", "SELECT regr_valy(1, NULL)", None, page="regr_valy"),
    _c("BETWEEN (decimal)", "Operators", "SELECT 'true' WHERE 1.35 BETWEEN 1 AND 2", "true", page="between"),
    _c("BETWEEN (text)", "Operators", "SELECT 'true' WHERE 'the' BETWEEN 'that' AND 'then'", "true", page="between"),
    _c("CASE", "Operators", "SELECT list(result ORDER BY column1) FROM (SELECT column1, CASE WHEN column1=1 THEN 'one' "
       "WHEN column1=2 THEN 'two' ELSE 'other' END AS result FROM (values(1),(2),(3)) v)", ["one", "two", "other"]),
    _c("CASE (no ELSE)", "Operators", "SELECT list(result ORDER BY column1) FROM (SELECT column1, CASE WHEN column1=1 "
       "THEN 'one' WHEN column1=2 THEN 'two' END AS result FROM (values(1),(2),(3)) v)", ["one", "two", None],
       page="case"),
    _c("LIKE", "Operators", f"{_LIKE_EX} SELECT list(name ORDER BY name) FROM like_ex WHERE name LIKE '%Jo%oe%'",
       ["Joe   Doe", "John  Dddoe", "John 'alias' Doe"]),
    _c("LIKE (NOT LIKE '')", "Operators", f"{_LIKE_EX} SELECT list(name ORDER BY name) FROM like_ex WHERE name NOT LIKE ''",
       ["Elaine", "Joe   Doe", "Joe down", "John  Dddoe", "John 'alias' Doe", "John_down"], page="like"),
    _c("LIKE (ESCAPE)", "Operators", f"{_LIKE_EX} SELECT list(name ORDER BY name) FROM like_ex "
       "WHERE name LIKE '%J%h%^_do%' ESCAPE '^'", ["John_down"], page="like"),
    _c("ILIKE", "Operators", f"{_ILIKE_EX} SELECT list(name ORDER BY name) FROM ilike_ex WHERE name ILIKE '%j%h%do%'",
       ["John  Dddoe", "John_down"]),
    _c("ILIKE (ESCAPE)", "Operators", f"{_ILIKE_EX} SELECT list(name ORDER BY name) FROM ilike_ex "
       "WHERE name ILIKE '%j%h%^_do%' ESCAPE '^'", ["John_down"], page="ilike"),
    _c("LIKE ANY", "Operators", f"{_LIKE_EXAMPLE} SELECT list(name ORDER BY name) FROM like_example "
       "WHERE name LIKE ANY ('%Jo%oe%','T%e')", ["Joe   Doe", "John  Dddoe", "Tom   Doe"], page="like_any"),
    _c("LIKE ALL", "Operators", f"{_LIKE_ALL} SELECT list(name ORDER BY name) FROM like_all_example "
       "WHERE name LIKE ALL ('%Jo%oe%','J%e')", ["Joe   Doe", "John  Dddoe"], page="like_all"),
    _c("ILIKE ANY", "Operators", "SELECT list(name ORDER BY name) FROM VALUES ('jane doe'), ('Jane Doe'), ('JANE DOE'), "
       "('John Doe'), ('John Smith') AS t(name) WHERE name ILIKE ANY ('jane%', '%SMITH')",
       ["JANE DOE", "Jane Doe", "John Smith", "jane doe"], page="ilike_any"),
    _c("REGEXP", "Operators", f"SELECT list(v ORDER BY v) FROM {_CITIES} WHERE v REGEXP 'San* [fF].*'", ["San Francisco"]),
    _c("REGEXP (whole string)", "Operators", f"SELECT list(v REGEXP 'San\\\\b.*' ORDER BY v) FROM {_CITIES}",
       [False, True, True, False], page="regexp"),
    _c("IS DISTINCT FROM", "Operators", "SELECT list(x1.i IS DISTINCT FROM x2.i ORDER BY x1.i, x2.i) FROM "
       "(VALUES (1), (2), (NULL)) x1(i), (VALUES (1), (2), (NULL)) x2(i)",
       [False, True, True, True, False, True, True, True, False], page="is-distinct-from"),
    _c(":: (to a decimal)", "Operators", "SELECT '9.8765'::NUMBER(6,5)", Decimal("9.87650"), page="cast"),
    _c("CAST", "Conversion", "SELECT CAST('9.8765' AS NUMBER(5,2))", Decimal("9.88")),
    _c("CAST (number to integer)", "Conversion", "SELECT CAST(1.2345::NUMBER(5,4) AS INTEGER)", 1, page="cast"),
    _c("QUALIFY", "Operators", f"SELECT list(i ORDER BY i) FROM (SELECT i, p, o FROM {_QT} "
       "QUALIFY ROW_NUMBER() OVER (PARTITION BY p ORDER BY o) = 1)", [1, 3], page="../constructs/qualify"),
    _c("PIVOT", "Operators", f"SELECT list(q4 ORDER BY empid) FROM (SELECT * FROM {_QUARTERLY} PIVOT(SUM(amount) FOR "
       "quarter IN ('2023_Q1' AS q1, '2023_Q2' AS q2, '2023_Q3' AS q3, '2023_Q4' AS q4)))", [18000, 5300, 28900],
       page="../constructs/pivot"),
    _c("PIVOT (a missing cell)", "Operators", f"SELECT list(q1 ORDER BY empid) FROM (SELECT * FROM {_QUARTERLY} "
       "PIVOT(SUM(amount) FOR quarter IN ('2023_Q1' AS q1, '2023_Q2' AS q2, '2023_Q3' AS q3, '2023_Q4' AS q4)))",
       [10400, 39500, None], page="../constructs/pivot"),
    _c("UNPIVOT", "Operators", f"SELECT list(sales ORDER BY empid, month) FROM (SELECT * FROM {_MONTHLY} "
       "UNPIVOT (sales FOR month IN (jan, feb, mar, apr))) WHERE empid = 4", [50, 100, 100],
       page="../constructs/unpivot"),
    _c("UNPIVOT (row count)", "Operators", f"SELECT count(*) FROM (SELECT * FROM {_MONTHLY} "
       "UNPIVOT (sales FOR month IN (jan, feb, mar, apr)))", 15, page="../constructs/unpivot"),
    _c("UNPIVOT (INCLUDE NULLS)", "Operators", f"SELECT count(*) FROM (SELECT * FROM {_MONTHLY} "
       "UNPIVOT INCLUDE NULLS (sales FOR month IN (jan, feb, mar, apr)))", 16, page="../constructs/unpivot"),
    _c("GENERATOR", "Operators", "SELECT count(*) FROM TABLE(GENERATOR(ROWCOUNT => 10)) v", 10),
    _c("SEQ4", "Numeric", "SELECT list(s ORDER BY s) FROM (SELECT seq4() AS s FROM TABLE(GENERATOR(ROWCOUNT => 10)) v)",
       [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], page="generator"),
    # in [min, max], as the page says; a generator other than RANDOM() is refused (SFN-021)
    _c("UNIFORM", "Numeric", "SELECT min(u) >= 1 AND max(u) <= 10 FROM (SELECT uniform(1, 10, random()) AS u FROM "
       "TABLE(GENERATOR(ROWCOUNT => 1000)) v)", True),
    _c("WITH (RECURSIVE)", "Statements", "WITH RECURSIVE current_f (current_val, previous_val) AS (SELECT 0, 1 UNION ALL "
       "SELECT current_val + previous_val, current_val FROM current_f WHERE current_val + previous_val < 100) "
       "SELECT list(current_val ORDER BY current_val) FROM current_f",
       [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89], page="../constructs/with"),
    _c("UNION ALL (BY NAME)", "Statements", "SELECT list(a ORDER BY a) FROM (SELECT * FROM VALUES (1, 'one'), (2, 'two'), "
       "(3, 'three') AS t1(a, b) UNION ALL BY NAME SELECT * FROM VALUES ('three', 3), ('four', 4) AS t2(b, a))",
       [1, 2, 3, 3, 4], page="../operators-query"),
    _c("MERGE", "Statements", f"{_MERGE_MULT} SELECT list(status ORDER BY id) FROM merge_example_mult_target",
       ["Beta", "Production", "Production"], page="../sql/merge"),
    _c("MERGE (values)", "Statements", f"{_MERGE_MULT} SELECT list(val ORDER BY id) FROM merge_example_mult_target",
       [50, 60, 40], page="../sql/merge"),
    _c("UPDATE", "Statements", f"{_ARRAY_APPEND} {_APPEND_FOUR} SELECT array_column FROM array_append_examples",
       [1, 2, 3, 4], page="array_append"),
    # the page's 1-to-1 join, over the target and src rows its nondeterministic example prints
    _c("UPDATE FROM", "Statements", "CREATE TABLE target (k INT, v INT); CREATE TABLE src (k INT, v INT); "
       "INSERT INTO target VALUES (0, 10); INSERT INTO src VALUES (0, 11), (0, 12), (0, 13); "
       "UPDATE target SET v = b.v FROM (SELECT k, MIN(v) v FROM src GROUP BY k) b WHERE target.k = b.k; "
       "SELECT list(v) FROM target", [11], page="../sql/update"),
    _c("DELETE", "Statements", f"{_BICYCLES} DELETE FROM leased_bicycles WHERE \"customer_id\" = 5555; "
       "SELECT list(bicycle_id ORDER BY bicycle_id) FROM leased_bicycles", [101, 102, 103, 104], page="../sql/delete"),
    _c("DELETE USING", "Statements", f"{_BICYCLES} DELETE FROM leased_bicycles WHERE \"customer_id\" = 5555; "
       "DELETE FROM leased_bicycles USING returned_bicycles WHERE leased_bicycles.bicycle_ID = returned_bicycles.bicycle_ID; "
       "SELECT list(bicycle_id ORDER BY bicycle_id) FROM leased_bicycles", [101, 103], page="../sql/delete"),
    _c("TRUNCATE TABLE", "Statements", "CREATE OR REPLACE TABLE merge_example_target (id INTEGER, description VARCHAR); "
       "INSERT INTO merge_example_target (id, description) VALUES (10, 'To be updated (this is the old value)'); "
       "TRUNCATE table merge_example_target; SELECT count(*) FROM merge_example_target", 0, page="../sql/merge"),
    # WR-313: statements, each from the page cited (read 2026-11-19)
    # merge: "Perform a basic merge that updates values", which names the target's column in UPDATE SET
    _c("MERGE (UPDATE SET target.column)", "Statements", "CREATE OR REPLACE TABLE merge_example_target (id INTEGER, "
       "description VARCHAR); INSERT INTO merge_example_target (id, description) VALUES "
       "(10, 'To be updated (this is the old value)'); CREATE OR REPLACE TABLE merge_example_source (id INTEGER, "
       "description VARCHAR); INSERT INTO merge_example_source (id, description) VALUES "
       "(10, 'To be updated (this is the new value)'); MERGE INTO merge_example_target USING merge_example_source "
       "ON merge_example_target.id = merge_example_source.id WHEN MATCHED THEN "
       "UPDATE SET merge_example_target.description = merge_example_source.description; "
       "SELECT description FROM merge_example_target", "To be updated (this is the new value)", page="../sql/merge"),
    # merge: "the target gets one copy of the row for each copy in the source" (two source rows, 2 inserted)
    _c("MERGE (NOT MATCHED, source duplicates)", "Statements", "CREATE TABLE target (id INTEGER, v INTEGER); "
       "CREATE TABLE src (id INTEGER, v INTEGER); INSERT INTO src VALUES (50, 1), (50, 2); MERGE INTO target USING "
       "src ON target.id = src.id WHEN NOT MATCHED THEN INSERT (id, v) VALUES (src.id, src.v); "
       "SELECT count(*) FROM target", 2, page="../sql/merge"),
    # merge: AND <case_predicate> "when true, causes the not-matching case to be executed"
    _c("MERGE (NOT MATCHED AND)", "Statements", "CREATE TABLE target (id INTEGER, v INTEGER); "
       "CREATE TABLE src (id INTEGER, v INTEGER); INSERT INTO src VALUES (1, 400), (2, 2000); MERGE INTO target "
       "USING src ON target.id = src.id WHEN NOT MATCHED AND src.v > 1000 THEN INSERT (id, v) VALUES (src.id, src.v); "
       "SELECT list(id) FROM target", [2], page="../sql/merge"),
    # insert-multi-table: the page's INSERT FIRST example, src n1 = 1, 11, 101; t1 gets 101 and 11
    _c("INSERT FIRST", "Statements", "CREATE TABLE src (n1 INTEGER); INSERT INTO src VALUES (1), (11), (101); "
       "CREATE TABLE t1 (n1 INTEGER); CREATE TABLE t2 (n1 INTEGER); INSERT FIRST WHEN n1 > 100 THEN INTO t1 "
       "WHEN n1 > 10 THEN INTO t1 INTO t2 ELSE INTO t2 SELECT n1 FROM src; SELECT list(n1 ORDER BY n1) FROM t1",
       [11, 101], "untranslated", "Refused by SFN-026. Write one INSERT ... SELECT for each table.", page="../sql/insert-multi-table"),
    # identifiers-syntax: unquoted identifiers "are stored and resolved as uppercase characters (e.g. id is stored
    # and resolved as ID)"; CREATE TABLE mytable(c1 INT, c2 INT) makes MYTABLE. A double-quoted one keeps its case.
    _c("CREATE TABLE (stored names)", "Statements", "CREATE TABLE mytable (c1 INT, \"c2\" INT); SELECT "
       "string_agg(table_name || '.' || column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns "
       "WHERE table_name = 'MYTABLE'", "MYTABLE.C1,MYTABLE.c2", page="../identifiers-syntax"),
    _c("CREATE TEMPORARY TABLE (stored names)", "Statements", "CREATE TEMPORARY TABLE two (j INT); SELECT "
       "table_name || '.' || column_name FROM information_schema.columns WHERE table_name = 'TWO'", "TWO.J",
       page="../identifiers-syntax"),
    _c("CREATE VIEW (stored names)", "Statements", "CREATE TABLE t (id INT); CREATE VIEW v AS SELECT id, id AS "
       "id2 FROM t; SELECT string_agg(table_name || '.' || column_name, ',' ORDER BY ordinal_position) FROM "
       "information_schema.columns WHERE table_name = 'V'", "V.ID,V.ID2", page="../identifiers-syntax"),
    _c("CREATE SCHEMA (stored name)", "Statements", "CREATE SCHEMA mine; SELECT schema_name FROM "
       "information_schema.schemata WHERE schema_name = 'MINE'", "MINE", page="../identifiers-syntax"),
    _c("ALTER TABLE ADD COLUMN (stored name)", "Statements", "CREATE TABLE t (id INT); ALTER TABLE t ADD COLUMN "
       "c2 INT; SELECT column_name FROM information_schema.columns WHERE table_name = 'T' AND ordinal_position = 2",
       "C2", page="../identifiers-syntax"),
    _c("ALTER TABLE RENAME (stored name)", "Statements", "CREATE TABLE t (id INT); ALTER TABLE t RENAME TO t2; "
       "SELECT table_name FROM information_schema.tables WHERE table_name = 'T2'", "T2",
       page="../identifiers-syntax"),
    # create-table: LIKE copies "Column names, types, defaults, and constraints"
    _c("CREATE TABLE LIKE", "Statements", "CREATE TABLE t (id INTEGER DEFAULT 7, v INTEGER); CREATE TABLE u LIKE t; "
       "INSERT INTO u (v) VALUES (1); SELECT id FROM u", 7, "untranslated",
       "Refused by SFN-027. Write the column definitions out.", page="../sql/create-table"),
    # create-table: CREATE TABLE mytable_copy (b) AS SELECT * FROM mytable
    _c("CREATE TABLE AS (column list)", "Statements", "CREATE TABLE mytable (amount NUMBER); "
       "INSERT INTO mytable VALUES (1); CREATE TABLE mytable_copy (b) AS SELECT * FROM mytable; "
       "SELECT b FROM mytable_copy", 1, page="../sql/create-table"),
    # create-table: a transient table has "a lower level of data protection"; it answers queries as any table
    _c("CREATE TRANSIENT TABLE", "Statements", "CREATE TRANSIENT TABLE t (a INTEGER) CLUSTER BY (a); "
       "INSERT INTO t VALUES (1), (2); SELECT count(*) FROM t", 2, page="../sql/create-table"),
    # create-table: AUTOINCREMENT START 10 INCREMENT 5 numbers from 10 by 5; NOORDER values aren't always in order
    _c("AUTOINCREMENT", "Statements", "CREATE TABLE t (id INTEGER AUTOINCREMENT START 10 INCREMENT 5, v INTEGER); "
       "INSERT INTO t (v) VALUES (1), (2), (3); SELECT bool_and(id >= 10 AND (id - 10) % 5 = 0) "
       "AND count(DISTINCT id) = 3 FROM t", True, page="../sql/create-table"),
    # constraints-overview: "Snowflake doesn't enforce PRIMARY KEY, UNIQUE, and FOREIGN KEY constraints on
    # standard tables", so a repeated key is kept
    _c("PRIMARY KEY (not enforced)", "Statements", "CREATE TABLE t (id INTEGER PRIMARY KEY, code VARCHAR UNIQUE); "
       "INSERT INTO t VALUES (1, 'a'), (1, 'a'); SELECT count(*) FROM t", 2, page="../constraints-overview"),
    # begin: START TRANSACTION is a synonym for BEGIN; the transaction stays open until COMMIT or ROLLBACK
    _c("START TRANSACTION", "Statements", "CREATE TABLE t (a INTEGER); START TRANSACTION; INSERT INTO t VALUES (1); "
       "ROLLBACK; SELECT count(*) FROM t", 0, page="../sql/begin"),
    # begin: the transaction stays open until COMMIT or ROLLBACK, and COMMIT keeps its rows
    _c("BEGIN (COMMIT)", "Statements", "CREATE TABLE t (a INTEGER); BEGIN; INSERT INTO t VALUES (1); COMMIT; "
       "SELECT count(*) FROM t", 1, page="../sql/begin"),
    # transactions: "DDL statements implicitly commit active transactions", so the INSERT before CREATE stays
    _c("DDL INSIDE BEGIN", "Statements", "CREATE TABLE t (a INTEGER); BEGIN; INSERT INTO t VALUES (1); "
       "CREATE TABLE x (b INTEGER); ROLLBACK; SELECT count(*) FROM t", 1, "untranslated",
       "Refused by SFN-030. Put COMMIT before the CREATE.", page="../transactions"),
    # truncate-table: the page's own TRUNCATE TABLE IF EXISTS example
    _c("TRUNCATE TABLE IF EXISTS", "Statements", "CREATE TABLE temp_test_truncate (i INTEGER); "
       "INSERT INTO temp_test_truncate VALUES (1); TRUNCATE TABLE IF EXISTS temp_test_truncate; "
       "SELECT count(*) FROM temp_test_truncate", 0, "untranslated", "Refused by SFN-028. Leave out IF EXISTS.",
       page="../sql/truncate-table"),
    # alter-table: ADD COLUMN, RENAME COLUMN ... TO and RENAME TO, as its syntax gives them
    _c("ALTER TABLE RENAME (column and table)", "Statements", "CREATE TABLE t1 (a1 INTEGER); ALTER TABLE t1 ADD COLUMN a2 NUMBER; "
       "ALTER TABLE t1 RENAME COLUMN a1 TO b1; ALTER TABLE t1 RENAME TO t2; INSERT INTO t2 (b1, a2) VALUES (1, 2); "
       "SELECT b1 + a2 FROM t2", 3, page="../sql/alter-table"),
    # alter-table: DROP [ COLUMN ] [ IF EXISTS ] <col1_name> [, <col2_name> ... ]
    _c("ALTER TABLE DROP COLUMN A, B", "Statements", "CREATE TABLE t (a INTEGER, b INTEGER, c INTEGER); "
       "ALTER TABLE t DROP COLUMN a, b; SELECT count(*) FROM information_schema.columns WHERE table_name ILIKE 't'",
       1, "untranslated", "Refused by SFN-029. Write one ALTER TABLE for each column.", page="../sql/alter-table"),
    _c("ARRAY_APPEND", "Semi-structured", "SELECT array_append(array_construct(1, 2, 3), 4)", [1, 2, 3, 4]),
    # the page's ARRAY column example: two UPDATEs append 4, then 'five'
    _c("ARRAY_APPEND (ARRAY column)", "Semi-structured", f"{_ARRAY_APPEND} {_APPEND_FOUR} {_APPEND_FIVE} "
       "SELECT array_column FROM array_append_examples", [1, 2, 3, 4, "five"], page="array_append"),
    _c("ARRAY_APPEND (element types)", "Semi-structured", f"{_ARRAY_APPEND} {_APPEND_FOUR} {_APPEND_FIVE} "
       "SELECT ARRAY_CONSTRUCT(TYPEOF(array_column[0]), TYPEOF(array_column[1]), TYPEOF(array_column[2]), "
       "TYPEOF(array_column[3]), TYPEOF(array_column[4])) AS type FROM array_append_examples",
       ["INTEGER", "INTEGER", "INTEGER", "INTEGER", "VARCHAR"], page="array_append"),
    _c("ARRAY_COMPACT", "Semi-structured", "SELECT array_compact(array_construct(10, NULL, 30))", [10, 30]),
    _c("ARRAY_DISTINCT", "Semi-structured", "SELECT array_distinct(['A', 'A', 'B', NULL, NULL])", ["A", "B", None]),
    _c("ARRAY_DISTINCT (NULL)", "Semi-structured", "SELECT array_distinct(NULL)", None, page="array_distinct"),
    _c("ARRAY_FLATTEN", "Semi-structured", "SELECT array_flatten([[1, 2, 3], [4], [5, 6]])", [1, 2, 3, 4, 5, 6]),
    _c("ARRAY_FLATTEN (a NULL element)", "Semi-structured", "SELECT array_flatten([[1, 2, 3], NULL, [5, 6]])", None,
       page="array_flatten"),
    _c("ARRAY_FLATTEN (a NULL inside)", "Semi-structured", "SELECT array_flatten([[1, 2, 3], [NULL], [5, 6]])",
       [1, 2, 3, None, 5, 6], page="array_flatten"),
    _c("ARRAY_GENERATE_RANGE", "Semi-structured", "SELECT array_generate_range(2, 5)", [2, 3, 4]),
    _c("ARRAY_GENERATE_RANGE (step)", "Semi-structured", "SELECT array_generate_range(5, 25, 10)", [5, 15],
       page="array_generate_range"),
    _c("ARRAY_GENERATE_RANGE (negative step)", "Semi-structured", "SELECT array_generate_range(-5, -25, -10)", [-5, -15],
       page="array_generate_range"),
    _c("ARRAY_POSITION", "Semi-structured", "SELECT array_position('hi'::variant, array_construct('hello', 'hi'))", 1),
    _c("ARRAY_POSITION (first)", "Semi-structured",
       "SELECT array_position('hello'::variant, array_construct('hello', 'hi'))", 0, page="array_position"),
    _c("ARRAY_POSITION (missing)", "Semi-structured",
       "SELECT array_position('hello'::variant, array_construct('hola', 'bonjour'))", None, page="array_position"),
    _c("ARRAY_REMOVE_AT", "Semi-structured", "SELECT array_remove_at([2, 5, 7], 0)", [5, 7]),
    _c("ARRAY_REMOVE_AT (from the end)", "Semi-structured", "SELECT array_remove_at([2, 5, 7], -1)", [2, 5],
       page="array_remove_at"),
    _c("ARRAY_REMOVE_AT (past the end)", "Semi-structured", "SELECT array_remove_at([2, 5, 7], 10)", [2, 5, 7],
       page="array_remove_at"),
    _c("ARRAY_REVERSE", "Semi-structured", "SELECT array_reverse([1,2,3,4])", [4, 3, 2, 1]),
    _c("ARRAY_SORT", "Semi-structured", "SELECT array_sort([20, 0, NULL, 10])", [0, 10, 20, None]),
    _c("ARRAY_SORT (descending)", "Semi-structured", "SELECT array_sort([20, 0, NULL, 10], FALSE)", [None, 20, 10, 0],
       page="array_sort"),
    _c("ARRAY_SORT (NULLs first)", "Semi-structured", "SELECT array_sort([20, 0, NULL, 10], TRUE, TRUE)",
       [None, 0, 10, 20], page="array_sort"),
    _c("UUID_STRING (named)", "String", "SELECT uuid_string('fe971b24-9572-4005-b22f-351e9c09274d','foo')",
       "dc0b6f65-fca6-5b4b-9d37-ccc3fde1f3e2", page="uuid_string"),
    _c("TO_JSON", "Semi-structured", "SELECT to_json(parse_json('{\"b\":1,\"a\":2}'))", '{"a":2,"b":1}'),
    _c("PARSE_JSON", "Semi-structured", "SELECT to_json(parse_json('{\"food\":\"bard\"}'))", '{"food":"bard"}',
       page="to_json"),
    _c("TO_ARRAY", "Semi-structured", "SELECT to_array('snowman,snowball,snowcone')", ["snowman,snowball,snowcone"]),
    _c("OBJECT_CONSTRUCT_KEEP_NULL", "Semi-structured",
       "SELECT to_json(object_construct_keep_null('key_1','one','key_2',NULL))", '{"key_1":"one","key_2":null}'),
    _c("GET", "Semi-structured", "SELECT get(object_construct('Ukraine', 'Kyiv'), 'Ukraine')::VARCHAR", "Kyiv"),
    _c("GET_PATH", "Semi-structured",
       "SELECT get_path(parse_json('{\"array2\": [{\"id3\": \"value_b3\"}]}'), 'array2[0].id3')::VARCHAR", "value_b3"),
    _c("STRIP_NULL_VALUE", "Semi-structured", "SELECT strip_null_value(parse_json('{\"c\": null}'):c)", None),
    _c("STRIP_NULL_VALUE (a value)", "Semi-structured", "SELECT strip_null_value(parse_json('{\"c\": \"3\"}'):c)::VARCHAR",
       "3", page="strip_null_value"),
    _c("BASE64_DECODE_BINARY", "String", "SELECT base64_decode_binary('SEVMUA==')", b"HELP"),
    _c("REGEXP_INSTR", "Regular expressions", f"SELECT regexp_instr({_NEVERMORE}, 'nevermore\\\\d')", 1),
    _c("REGEXP_INSTR (start)", "Regular expressions", f"SELECT regexp_instr({_NEVERMORE}, 'nevermore\\\\d', 5)", 13,
       page="regexp_instr"),
    _c("REGEXP_INSTR (occurrence)", "Regular expressions", f"SELECT regexp_instr({_NEVERMORE}, 'nevermore\\\\d', 1, 3)",
       25, page="regexp_instr"),
    _c("REGEXP_INSTR (after the match)", "Regular expressions",
       f"SELECT regexp_instr({_NEVERMORE}, 'nevermore\\\\d', 1, 3, 1)", 35, page="regexp_instr"),
    _c("REGEXP_INSTR (no such occurrence)", "Regular expressions",
       f"SELECT regexp_instr({_NEVERMORE}, 'nevermore', 1, 4)", 0, page="regexp_instr"),
    _c("REGEXP_INSTR (word boundary)", "Regular expressions",
       "SELECT regexp_instr('It was the best of times, it was the worst of times', '\\\\bwas\\\\b', 1, 1)", 4,
       page="regexp_instr"),
    _c("REGEXP_INSTR (POSIX class)", "Regular expressions",
       "SELECT regexp_instr('It was the best of times, it was the worst of times', '[[:alpha:]]{2,}st', 15, 1)", 38,
       page="regexp_instr"),
    _c("REGEXP_SUBSTR_ALL", "Regular expressions", "SELECT regexp_substr_all('a1_a2a3_a4A5a6', 'a[[:digit:]]')",
       ["a1", "a2", "a3", "a4", "a6"]),
    _c("REGEXP_SUBSTR_ALL (start)", "Regular expressions",
       "SELECT regexp_substr_all('a1_a2a3_a4A5a6', 'a[[:digit:]]', 2)", ["a2", "a3", "a4", "a6"],
       page="regexp_substr_all"),
    _c("REGEXP_SUBSTR_ALL (occurrence)", "Regular expressions",
       "SELECT regexp_substr_all('a1_a2a3_a4A5a6', 'a[[:digit:]]', 1, 3)", ["a3", "a4", "a6"],
       page="regexp_substr_all"),
    _c("REGEXP_SUBSTR_ALL (i)", "Regular expressions",
       "SELECT regexp_substr_all('a1_a2a3_a4A5a6', 'a[[:digit:]]', 1, 1, 'i')", ["a1", "a2", "a3", "a4", "A5", "a6"],
       page="regexp_substr_all"),
    _c("REGEXP_SUBSTR_ALL (group)", "Regular expressions",
       "SELECT regexp_substr_all('a1_a2a3_a4A5a6', '(a)([[:digit:]])', 1, 1, 'ie')", ["a", "a", "a", "a", "A", "a"],
       page="regexp_substr_all"),
    _c("REGEXP_SUBSTR (group)", "Regular expressions", "SELECT regexp_substr('It was the best of times, it was the "
       "worst of times.', 'the\\\\W+(\\\\w+)', 1, 2, 'e')", "worst", page="regexp_instr"),
    _c("REGEXP_SUBSTR_ALL (no match)", "Regular expressions", "SELECT regexp_substr_all('a1_a2a3_a4A5a6', 'b')", [],
       page="regexp_substr_all"),
    _c("REGEXP_EXTRACT_ALL", "Regular expressions", "SELECT regexp_extract_all('a1_a2a3_a4A5a6', 'a[[:digit:]]')",
       ["a1", "a2", "a3", "a4", "a6"], page="regexp_substr_all"),
    _c("TIMESTAMP_LTZ_FROM_PARTS", "Date and time", "ALTER SESSION SET TIMEZONE='America/New_York'; "
       "SELECT date_part(epoch_second, timestamp_ltz_from_parts(2013, 4, 5, 12, 00, 00))", 1365177600,
       page="timestamp_from_parts"),
    _c("TIMESTAMP_TZ_FROM_PARTS", "Date and time", "ALTER SESSION SET TIMEZONE='America/New_York'; "
       "SELECT date_part(epoch_second, timestamp_tz_from_parts(2013, 4, 5, 12, 00, 00))", 1365177600,
       page="timestamp_from_parts"),
    _c("CONVERT_TIMEZONE", "Date and time", "SELECT convert_timezone('America/Los_Angeles', 'America/New_York', "
       "'2024-01-01 14:00:00'::TIMESTAMP_NTZ)", dt.datetime(2024, 1, 1, 17, 0)),
    _c("CONVERT_TIMEZONE (to UTC)", "Date and time", "SELECT convert_timezone('Europe/Warsaw', 'UTC', "
       "'2024-01-01 00:00:00'::TIMESTAMP_NTZ)", dt.datetime(2023, 12, 31, 23, 0), page="convert_timezone"),
    _c("TO_CHAR", "Conversion", "SELECT to_char(d, 'yyyy.mm.dd') FROM VALUES ('2024-04-03'::DATE) AS t(d)", "2024.04.03"),
    _c("TRY_TO_BINARY", "Conversion", "SELECT try_to_binary(hex_encode('A B'), 'HEX')", b"A B"),
    _c("TRY_TO_DECIMAL", "Conversion", "SELECT try_to_decimal('345.123')", 345),
    _c("TRY_TO_DECIMAL (scale)", "Conversion", "SELECT try_to_decimal('345.123', 10, 2)", Decimal("345.12"),
       page="try_to_decimal"),
    _c("TRY_TO_DECIMAL (out of range)", "Conversion", "SELECT try_to_decimal('345.123', 4, 2)", None,
       page="try_to_decimal"),
    _c("TRY_TO_DECIMAL (not a number)", "Conversion", "SELECT try_to_decimal('aaa')", None, page="try_to_decimal"),
    _c("TRY_TO_NUMERIC", "Conversion", "SELECT try_to_numeric('345.123', 10, 2)", Decimal("345.12"),
       page="try_to_decimal"),
    _c("TRY_TO_DOUBLE", "Conversion", "SELECT try_to_double('3.1415926')", 3.1415926),
    _c("TRY_TO_DOUBLE (not a number)", "Conversion", "SELECT try_to_double('Invalid')", None, page="try_to_double"),
    _c("TRY_TO_TIME", "Conversion", "SELECT try_to_time('12:30:00')", dt.time(12, 30)),
    _c("TRY_TO_TIME (not a time)", "Conversion", "SELECT try_to_time('Invalid')", None, page="try_to_time"),
    # data-types-datetime's TIMESTAMP_LTZ and TIMESTAMP_TZ examples: a table ts_test(ts) gets '2024-01-01 16:00:00'
    # and '2024-01-02 16:00:00 +00:00' in an America/Los_Angeles session, then SELECT ts, HOUR(ts), and again
    # after ALTER SESSION SET TIMEZONE = 'America/New_York'.
    _c("INSERT (TIMESTAMP_LTZ column)", "Statements", "CREATE OR REPLACE TABLE ts_test(ts TIMESTAMP_LTZ); ALTER SESSION "
       "SET TIMEZONE = 'America/Los_Angeles'; INSERT INTO ts_test VALUES('2024-01-01 16:00:00'); INSERT INTO "
       "ts_test VALUES('2024-01-02 16:00:00 +00:00'); SELECT list(hour(ts) ORDER BY ts) FROM ts_test", [16, 8],
       page="../data-types-datetime"),
    _c("INSERT (TIMESTAMP_LTZ column, another session zone)", "Statements", "CREATE OR REPLACE TABLE ts_test(ts "
       "TIMESTAMP_LTZ); ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; INSERT INTO ts_test VALUES('2024-01-01 "
       "16:00:00'); INSERT INTO ts_test VALUES('2024-01-02 16:00:00 +00:00'); ALTER SESSION SET TIMEZONE = "
       "'America/New_York'; SELECT list(hour(ts) ORDER BY ts) FROM ts_test", [19, 11], page="../data-types-datetime"),
    _c("INSERT (TIMESTAMP_TZ column)", "Statements", "CREATE OR REPLACE TABLE ts_test(ts TIMESTAMP_TZ); ALTER SESSION "
       "SET TIMEZONE = 'America/Los_Angeles'; INSERT INTO ts_test VALUES('2024-01-01 16:00:00'); SELECT hour(ts) "
       "FROM ts_test", 16, page="../data-types-datetime"),
    _c("INSERT (TIMESTAMP_TZ column, text with an offset)", "Statements", "CREATE OR REPLACE TABLE ts_test(ts "
       "TIMESTAMP_TZ); ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; INSERT INTO ts_test VALUES('2024-01-02 "
       "16:00:00 +00:00'); SELECT hour(ts) FROM ts_test", 16, "untranslated",
       "Refused by SFN-013: DuckDB can't keep the +00:00 Snowflake keeps with the value (SFX-032). Text with "
       "another offset and no space stops the query (SFN-033).", page="../data-types-datetime"),
    _c("INSERT (TIMESTAMP_TZ column, another session zone)", "Statements", "CREATE OR REPLACE TABLE ts_test(ts "
       "TIMESTAMP_TZ); ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; INSERT INTO ts_test VALUES('2024-01-01 "
       "16:00:00'); ALTER SESSION SET TIMEZONE = 'America/New_York'; SELECT hour(ts) FROM ts_test", 16, "differs",
       "SFD-002: the value keeps -08:00 in Snowflake; here it is an instant, shown in the session's new zone "
       "(19). In the zone it was written in, it reads as in Snowflake.", page="../data-types-datetime"),
    # TO_TIMESTAMP_TZ's examples (to_timestamp page, in its America/Los_Angeles session): 01:02:03.000 -0700, the
    # session's offset, so its wall clock and its instant (08:02:03 UTC) are both checked.
    _c("TO_TIMESTAMP_TZ", "Conversion", "ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
       "SELECT to_timestamp_tz('2024-04-05 01:02:03')", dt.datetime(2024, 4, 5, 1, 2, 3), page="to_timestamp"),
    _c("TO_TIMESTAMP_TZ (its instant)", "Conversion", "ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
       "SELECT date_part(epoch_second, to_timestamp_tz('2024-04-05 01:02:03'))", 1712304123, page="to_timestamp"),
    _c("TO_TIMESTAMP_TZ (format)", "Conversion", "ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
       "SELECT to_timestamp_tz('04/05/2024 01:02:03', 'mm/dd/yyyy hh24:mi:ss')", dt.datetime(2024, 4, 5, 1, 2, 3),
       page="to_timestamp"),
    _c("TO_TIMESTAMP_TZ (day first)", "Conversion", "ALTER SESSION SET TIMEZONE = 'America/Los_Angeles'; "
       "SELECT date_part(epoch_second, to_timestamp_tz('04/05/2024 01:02:03', 'dd/mm/yyyy hh24:mi:ss'))",
       1714809723, page="to_timestamp"),
    # TRY_TO_TIMESTAMP's page has one example, of TRY_TO_TIMESTAMP; its _TZ form answers it the same way
    _c("TRY_TO_TIMESTAMP_TZ", "Conversion", "SELECT try_to_timestamp_tz('2024-01-15 12:30:00')",
       dt.datetime(2024, 1, 15, 12, 30), page="try_to_timestamp"),
    _c("TRY_TO_TIMESTAMP_TZ (not a timestamp)", "Conversion", "SELECT try_to_timestamp_tz('Invalid')", None,
       page="try_to_timestamp"),
    _c("TRY_TO_TIMESTAMP", "Conversion", "SELECT try_to_timestamp('2024-01-15 12:30:00')", dt.datetime(2024, 1, 15, 12, 30)),
    _c("TRY_TO_TIMESTAMP (not a timestamp)", "Conversion", "SELECT try_to_timestamp('Invalid')", None,
       page="try_to_timestamp"),
    _c("TO_TIMESTAMP (milliseconds in a string)", "Conversion", "SELECT to_timestamp('31536000000')",
       dt.datetime(1971, 1, 1), page="to_timestamp"),
    _c("TO_TIMESTAMP (nanoseconds in a string)", "Conversion", "SELECT to_timestamp('31536000000000000')",
       dt.datetime(1971, 1, 1), page="to_timestamp"),
    _c("TO_TIMESTAMP (scale)", "Conversion", "SELECT to_timestamp(1000000000, 3)", dt.datetime(1970, 1, 12, 13, 46, 40),
       page="to_timestamp"),
    _c("ARRAYS_OVERLAP", "Semi-structured",
       "SELECT arrays_overlap(array_construct('hello', 'aloha'), array_construct('hello', 'hi', 'hey'))", True),
    _c("ARRAYS_OVERLAP (none)", "Semi-structured",
       "SELECT arrays_overlap(array_construct('hello', 'aloha'), array_construct('hola', 'bonjour', 'ciao'))", False,
       page="arrays_overlap"),
    _c("ARRAYS_OVERLAP (NULLs match)", "Semi-structured",
       "SELECT arrays_overlap(ARRAY_CONSTRUCT(1, 2, NULL), ARRAY_CONSTRUCT(3, NULL, 5))", True, page="arrays_overlap"),
    _c("ARRAYS_ZIP", "Semi-structured", "SELECT array_size(arrays_zip([1,2,3],['one'],['I','II']))", 3),
    _c("ARRAYS_ZIP (NULL)", "Semi-structured", "SELECT arrays_zip([1,2,3],NULL,[100,200,300])", None, page="arrays_zip"),
    _c("MAP_KEYS", "Semi-structured", "SELECT map_keys({'a':1,'b':2,'c':3}::MAP(VARCHAR,NUMBER))", ["a", "b", "c"]),
    _c("MAP_PICK", "Semi-structured", "SELECT to_json(map_pick({'a':1,'b':2,'c':3}::MAP(VARCHAR,NUMBER),'a','b'))",
       '{"a":1,"b":2}'),
    _c("MAP_ENTRIES", "Semi-structured", "SELECT array_size(map_entries({'a': 1, 'b': 2}::MAP(VARCHAR, INT)))", 2),
    _c("MAP_ENTRIES (NULL)", "Semi-structured", "SELECT map_entries(NULL::MAP(VARCHAR, INT))", None, page="map_entries"),
    _c("NEXT_DAY", "Date and time", "SELECT next_day('2025-05-06'::DATE, 'Friday')", dt.date(2025, 5, 9)),
    _c("PREVIOUS_DAY", "Date and time", "SELECT previous_day('2025-05-06'::DATE, 'Friday')", dt.date(2025, 5, 2)),
    _c("INSERT INTO", "Statements", f"{_EMPLOYEES_INSERT} SELECT list(first_name ORDER BY first_name) FROM employees",
       ["Gillian", "Lysandra", "May", "Michael"], page="../sql/insert"),
    _c("INSERT INTO (SELECT)", "Statements", f"{_EMPLOYEES_INSERT} {_CONTRACTORS} "
       "INSERT INTO employees(first_name, last_name, workphone, city, postal_code) SELECT contractor_first, "
       "contractor_last, worknum, NULL, zip_code FROM contractors WHERE CONTAINS(worknum,'650'); "
       "SELECT list(first_name ORDER BY first_name) FROM employees WHERE city IS NULL", ["Bradley", "Laurel"],
       page="../sql/insert"),
    # the page's object, holding only the key the call reads
    _c("TO_VARIANT", "Semi-structured", "SELECT get(to_variant(object_construct('temperature', 31.5)), 'temperature')::FLOAT",
       31.5, page="get"),
    _c("CHECK_JSON", "Semi-structured", "SELECT check_json('{\"a\": 1}')", None),
    _c("TRY_PARSE_JSON", "Semi-structured", "SELECT try_parse_json('{\"x\": \"abc\", \"y\": false')", None),
    # TRY_TO_TIMESTAMP's page: its one example calls TRY_TO_TIMESTAMP, which "maps to TRY_TO_TIMESTAMP_NTZ by
    # default" (TIMESTAMP_TYPE_MAPPING's default, TIMESTAMP_NTZ), so these are its values for the _NTZ form
    _c("TRY_TO_TIMESTAMP_NTZ", "Conversion", "SELECT try_to_timestamp_ntz('2024-01-15 12:30:00')",
       dt.datetime(2024, 1, 15, 12, 30), page="try_to_timestamp"),
    _c("TRY_TO_TIMESTAMP_NTZ (not a timestamp)", "Conversion", "SELECT try_to_timestamp_ntz('Invalid')", None,
       page="try_to_timestamp"),
    _c("JSON_EXTRACT_PATH_TEXT", "Semi-structured", f"{_DEMO1} SELECT list(JSON_EXTRACT_PATH_TEXT(json_data, "
       "'level_1_key') ORDER BY id) FROM demo1",
       ["level_1_value", '{"level_2_key":"level_2_value"}', '{"level_2_key":["zero","one","two"]}']),
    _c("JSON_EXTRACT_PATH_TEXT (two levels)", "Semi-structured", f"{_DEMO1} SELECT list(JSON_EXTRACT_PATH_TEXT("
       "json_data, 'level_1_key.level_2_key') ORDER BY id) FROM demo1", [None, "level_2_value", '["zero","one","two"]'],
       page="json_extract_path_text"),
    _c("JSON_EXTRACT_PATH_TEXT (array index)", "Semi-structured", f"{_DEMO1} SELECT list(JSON_EXTRACT_PATH_TEXT("
       "json_data, 'level_1_key.level_2_key[1]') ORDER BY id) FROM demo1", [None, None, "one"],
       page="json_extract_path_text"),
    _c("ARRAY_UNIQUE_AGG", "Semi-structured", "CREATE OR REPLACE TABLE array_unique_agg_test (a INTEGER); "
       "INSERT INTO array_unique_agg_test VALUES (5), (2), (1), (2), (1); "
       "SELECT ARRAY_SIZE(ARRAY_UNIQUE_AGG(a)) AS number_of_distinct_values FROM array_unique_agg_test", 3,
       page="../../user-guide/querying-arrays-for-distinct-counts"),
    # the page's testtable isn't created there; SAMPLE ROW (0) is empty and TABLESAMPLE (100) is every row, as it says
    _c("SAMPLE (ROW 0)", "Operators", f"{_TESTTABLE} SELECT count(*) FROM testtable SAMPLE ROW (0)", 0,
       page="../constructs/sample"),
    _c("SAMPLE (TABLESAMPLE 100)", "Operators", f"{_TESTTABLE} SELECT count(*) FROM testtable TABLESAMPLE (100)", 5,
       page="../constructs/sample"),
    # the page's t2 (SHOW TABLES says 5 rows), then SHOW TABLES LIKE 't2%' read from information_schema: empty
    _c("DROP TABLE", "Statements", f"{_T2} DROP TABLE t2; SELECT count(*) FROM information_schema.tables "
       "WHERE table_name ILIKE 't2%'", 0, page="../sql/drop-table"),
    _c("DROP TABLE (IF EXISTS)", "Statements", f"{_T2} DROP TABLE t2; DROP TABLE IF EXISTS t2; "
       "SELECT count(*) FROM information_schema.tables WHERE table_name ILIKE 't2%'", 0, page="../sql/drop-table"),
)


def run(case: Case, con: Any = None) -> tuple[str, Any]:
    """(outcome, value) for ``case`` on this PC: the value is DuckDB's answer, or the reason it has none."""
    import duckdb

    from . import snowflake_sql

    translation = snowflake_sql.translate(case.sql)
    if not translation.runnable:
        return "untranslated", translation.first_refusal()
    con = con or duckdb.connect()
    try:
        row = first_row(con.execute(translation.duckdb_sql))
    except duckdb.Error as exc:
        if isinstance(case.expected, Raises) and case.expected.text in str(exc):
            return "same", case.expected
        if snowflake_sql.stopped_by_name(exc):  # a refusal for a value only the run can tell (SFN-033, SFN-034)
            return "untranslated", str(exc).splitlines()[0].split("Error: ", 1)[-1]
        return "error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    value = row[0] if row else None
    return ("same" if same(value, case.expected) else "differs"), value


def table_markdown() -> str:
    """The docs text: the functions that don't answer the same, as a table, then the ones that do."""
    words = {"differs": "Different result", "error": "Doesn't run", "untranslated": "Runs only in Snowflake"}
    rows = [(f"Of {len(CASES)} checks on commonly used Snowflake functions, against the values Snowflake's SQL "
            "reference documents (no Snowflake account was used), these don't answer the same way on this PC."), "",
            "| Function | Group | On this PC | What to know |", "| --- | --- | --- | --- |"]
    rows += [f"| `{c.function}` | {c.group} | {words[c.outcome]} | {c.note} |" for c in CASES if c.outcome != "same"]
    same_ = [f"`{c.function}`" for c in CASES if c.outcome == "same"]
    return "\n".join(rows) + f"\n\nThese {len(same_)} checks answer as Snowflake documents: " + ", ".join(same_) + "."
