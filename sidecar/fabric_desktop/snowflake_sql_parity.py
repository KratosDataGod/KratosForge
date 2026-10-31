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
from dataclasses import dataclass
from typing import Any

from .engines.databricks_sql_parity import same

_FN = "https://docs.snowflake.com/en/sql-reference/functions/"
D = dt.date
_V = "(SELECT column1 AS x FROM VALUES ({}))"


def _values(*rows: Any) -> str:
    return _V.format("), (".join(str(r) for r in rows))


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
    _c("/ (by zero)", "Numeric", "SELECT 1/0", "Division by zero error", "differs",
       "Snowflake stops with a division-by-zero error; here it gives infinity (SFD-003). Use DIV0.",
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
    _c("ARRAY_SIZE", "Semi-structured", "SELECT array_size(array_construct(1, 2, 3))", 3),
    _c("ARRAY_CONTAINS", "Semi-structured", "SELECT array_contains(2::variant, array_construct(1, 2))", True),
    _c("ARRAY_TO_STRING", "Semi-structured", "SELECT array_to_string(array_construct('a', 'b'), ',')", "a,b"),
    _c("ARRAY_SLICE", "Semi-structured", "SELECT array_slice(array_construct(1, 2, 3, 4), 1, 3)", [2, 3]),
    _c("ARRAY_CAT", "Semi-structured", "SELECT array_cat(array_construct(1), array_construct(2))", [1, 2]),
    _c("OBJECT_KEYS", "Semi-structured", "SELECT object_keys(parse_json('{\"a\": 1, \"b\": 2}'))", ["a", "b"]),
    _c("IS_NULL_VALUE", "Semi-structured", "SELECT is_null_value(parse_json('null'))", True),
    _c("TYPEOF", "Semi-structured", "SELECT typeof(parse_json('1'))", "INTEGER", "differs",
       "It names DuckDB's type (JSON), not Snowflake's VARIANT type (SFD-007)."),
    _c("LISTAGG", "Aggregate", f"SELECT listagg(x, ',') WITHIN GROUP (ORDER BY x) FROM {_values(repr('b'), repr('a'))}",
       "a,b"),
    _c("ARRAY_AGG", "Aggregate", f"SELECT array_agg(x) WITHIN GROUP (ORDER BY x) FROM {_values(2, 1)}", [1, 2]),
    _c("COUNT_IF", "Aggregate", f"SELECT count_if(x > 1) FROM {_values(1, 2, 3)}", 2),
    _c("MEDIAN", "Aggregate", f"SELECT median(x) FROM {_values(1, 2, 3, 4)}", 2.5),
    _c("BOOLOR_AGG", "Aggregate", f"SELECT boolor_agg(x) FROM {_values('TRUE', 'FALSE')}", True),
    _c("ANY_VALUE", "Aggregate", f"SELECT any_value(x) FROM {_values(1)}", 1),
    _c("APPROX_COUNT_DISTINCT", "Aggregate", f"SELECT approx_count_distinct(x) FROM {_values(1, 1, 2)}", 2),
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
        row = con.execute(translation.duckdb_sql).fetchone()
    except duckdb.Error as exc:
        return "error", f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    value = row[0] if row else None
    return ("same" if same(value, case.expected) else "differs"), value


def table_markdown() -> str:
    """The docs text: the functions that don't answer the same, as a table, then the ones that do."""
    words = {"differs": "Different result", "error": "Doesn't run", "untranslated": "Runs only in Snowflake"}
    rows = [f"Of {len(CASES)} checks on commonly used Snowflake functions, against the values Snowflake's SQL "
            "reference documents (no Snowflake account was used), these don't answer the same way on this PC.", "",
            "| Function | Group | On this PC | What to know |", "| --- | --- | --- | --- |"]
    rows += [f"| `{c.function}` | {c.group} | {words[c.outcome]} | {c.note} |" for c in CASES if c.outcome != "same"]
    same_ = [f"`{c.function}`" for c in CASES if c.outcome == "same"]
    return "\n".join(rows) + f"\n\nThese {len(same_)} checks answer as Snowflake documents: " + ", ".join(same_) + "."
