"""Inline examples checked by test_docs_examples.py: see docexamples.py. Not a test module."""

from __future__ import annotations

from docexamples import Claim, Statement

P = "sql-compatibility.md"


def _nb(expr: str, **kw) -> Claim:
    """A notebook's spark.sql on this PC (the "on this PC" Spark sections)."""
    return Claim(P, expr, "notebook", **kw)


def _ts(expr: str, **kw) -> Claim:
    """A local data project's T-SQL (the "Local T-SQL" sections)."""
    return Claim(P, expr, "local-tsql", **kw)


def _column_type(select: str, table: str, column: str = "v") -> dict:
    """A query that makes ``table`` from ``select`` and reads its column's type back, as T-SQL writes it."""
    return dict(setup=(f"{select} INTO dbo.{table}",),
                query=("SELECT CASE WHEN DATA_TYPE = 'INTEGER' THEN 'int' "
                       "WHEN DATA_TYPE LIKE 'DECIMAL%' THEN CONCAT('decimal(', NUMERIC_PRECISION, ',', NUMERIC_SCALE, ')') "
                       f"ELSE DATA_TYPE END FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table}' "
                       f"AND COLUMN_NAME = '{column}'"),
                note="DuckDB names T-SQL's int INTEGER")


CLAIMS: list[Claim] = [
    # A date or time as text with no style (WR-707)
    _ts("CONVERT(VARCHAR(10), CAST('2024-02-29 13:04' AS DATETIME2), 120)"),
    _ts("CAST(CAST('2024-02-29 13:04' AS DATETIME) AS VARCHAR(11))"),
    # Spark's date and time functions on this PC
    _nb("DATE'2024-01-31' + INTERVAL 1 MONTH"),
    _nb("unix_seconds(TIMESTAMP'2024-05-17 13:45:12.9')"),
    # Double rounding and text-to-number comparison (#3086), math at its edges (WR-116, #3120)
    Claim("sql-compatibility.md", "round(0.285D, 2)", "notebook"),
    Claim("sql-compatibility.md", "round(1.005D, 2)", "notebook"),
    Claim("sql-compatibility.md", "'1.5' = 1", "notebook"),
    Claim("sql-compatibility.md", "'1.55' = 1.6", "notebook"),
    Claim("sql-compatibility.md", "'1.0' IN (1, 2)", "notebook"),
    Claim("sql-compatibility.md", "0 = ''", "local-tsql", query="SELECT CASE WHEN 0 = '' THEN 1 ELSE 0 END = 1"),
    Claim("sql-compatibility.md", "CAST('.5' AS INT)", "notebook"),
    Claim("sql-compatibility.md", "factorial(3.7)", "notebook"),
    Claim("sql-compatibility.md", "cot(0)", "notebook", want="inf", note="Python's float('inf') renders as inf"),
    Claim("sql-compatibility.md", "atan2(0.0D, -0.0D)", "notebook"),
    Claim("sql-compatibility.md", "-2147483648", "notebook", typeof=True),
    Claim("sql-compatibility.md", "try_subtract(-2147483648, 1)", "notebook"),
    Claim("sql-compatibility.md", "typeof(raise_error(...))", "notebook", query="SELECT typeof(raise_error('x'))"),
    # Whole-number arithmetic wraps as Spark's does with ANSI off (WR-116, #3031)
    Claim("sql-compatibility.md", "2147483647 + 1", "notebook"),
    Claim("sql-compatibility.md", "CAST(100 AS TINYINT) + CAST(100 AS TINYINT)", "notebook"),
    # Array and string functions at their edges (#3069)
    Claim("sql-compatibility.md", "substring_index('aaa', 'aa', -1)", "notebook"),
    Claim("sql-compatibility.md", "upper('ß')", "notebook"),
    Claim("sql-compatibility.md", "lower('İ')", "notebook"),
    # ANSI mode on this PC: Spark SQL here, ANSI off
    _nb("CAST(2147483648 AS INT)"),
    _nb("CAST(1e10 AS INT)"),
    # Decimal results on this PC: a notebook's spark.sql (the DuckDB engine choice gives the same, local_sql.py)
    _nb("CAST(1 AS DECIMAL(10,2)) / 3", typeof=True),
    _nb("CAST(1 AS DECIMAL(10,2)) / 3", want="0.333333"),
    # Dates and times on this PC
    _nb("add_months(DATE'2019-02-28', 1)"),
    # Aggregates on this PC: the grand-total row of a ROLLUP over a NULL in the data
    _nb("grouping(a)", query="SELECT grouping(a) AS g FROM VALUES (CAST(NULL AS INT), 1) AS t(a, b) "
                             "GROUP BY a, b WITH ROLLUP ORDER BY g DESC LIMIT 1"),
    # Regular expressions and text on this PC
    _nb("regexp_count('abc', '')"),
    # Regular expressions as Java and the source dialects read them (WR-257)
    _nb("regexp_instr('xab', 'a*')"),
    _nb("regexp_instr('abc', '')"),
    _nb("regexp_instr('bcx', '(b)(c)', 2)"),
    _nb("regexp_instr('😀a', 'a')"),
    _nb("regexp_substr('xab', 'a*')"),
    _nb("regexp_extract_all('abab', '(a)\\|(b)', 2)", want="true",
        query="SELECT regexp_extract_all('abab', '(a)|(b)', 2) = array('', 'b', '', 'b')",
        note="the page escapes | for its table, and writes the array as ['', 'b', '', 'b']"),
    Claim("sql-compatibility.md", "REGEXP_LIKE('a' + CHAR(10) + 'b', '^b$', 'm')", "local-tsql",
          query="SELECT CASE WHEN REGEXP_LIKE('a' + CHAR(10) + 'b', '^b$', 'm') THEN 1 ELSE 0 END = 1"),
    _nb("chr(321)"),
    # Differential tests: fixed cases
    _nb("'24-02-29'", query="SELECT to_date('24-02-29')"),
    _nb("CAST(ceil(1.0E3D) AS STRING)"),
    # Local T-SQL ordering and window functions
    _ts("4.00", query="SELECT TOP 1 PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY x) OVER () "
                      "FROM (VALUES (1.25), (2.50), (4.00)) AS t(x)"),
    # Local T-SQL dates and times
    _ts("DATEADD(month, 1, '2024-01-31')"),
    _ts("DATEADD(year, 1, '2024-02-29')"),
    _ts("DATEDIFF(year, '2023-12-31', '2024-01-01')"),
    _ts("DATEDIFF(week, Saturday, Sunday)", query="SELECT DATEDIFF(week, '2024-03-02', '2024-03-03')"),
    _ts("EOMONTH('2024-01-31', 1)"),
    _ts("datetime2(6)", query="SELECT CAST(CAST('2024-02-29 13:04:05' AS datetime2(6)) AS varchar(30))"),
    # Local T-SQL formatting
    _ts("FORMAT(1234.5, 'N')"),
    _ts("FORMAT(255, 'X4')"),
    _ts("FORMAT(-1, 'X')"),
    _ts("FORMAT(1234.5, 'C')"),
    _ts("PARSE('1.234,5' AS DECIMAL(10, 2) USING 'de-DE')"),
    _ts("STR(1234.5)"),
    # Local T-SQL text
    _ts("'a' = 'a  '"),
    _ts("'a' = 'A'"),
    _ts("'abc' LIKE 'abc '"),
    _ts("LEN('ab  ')"),
    _ts("DATALENGTH('ab  ')"),
    _ts("'é'", query="SELECT DATALENGTH('é')"),
    _ts("CONCAT('a', 1.50)"),
    _ts("N'a' + N'b'"),
    _ts("DATALENGTH(N'ab')"),
    _ts("DATALENGTH(N'a' + N'b')"),
    # Local T-SQL tables: CREATE TABLE AS SELECT / SELECT INTO keep the select's types
    _ts("1", **_column_type("SELECT 1 AS v", "t_int")),
    # Local T-SQL decimals
    _ts("decimal(38,10) * decimal(38,10)",
        **_column_type("SELECT CAST(1 AS DECIMAL(38,10)) * CAST(1 AS DECIMAL(38,10)) AS v", "t_mul")),
    _ts("-10.25 / 3"),
    _ts("-10.25 / CAST(3 AS INT)"),
    _ts("1.5", **_column_type("SELECT 1.5 AS v", "t_lit")),
    _ts("1.0 / 3"),
    # Local T-SQL arithmetic and conversion
    _ts("-7 / 2"),
    _ts("-7 % 2"),
    _ts("NULL / 0"),
    _ts("'1' + 1"),
    _ts("'' + 1"),
    _ts("'1' + '1'"),
    _ts("ISNULL(CAST(NULL AS VARCHAR(2)), 'abcd')"),
    _ts("ISNULL(int_column, 2.7)", query="SELECT ISNULL(int_column, 2.7) FROM (VALUES (CAST(NULL AS INT))) AS t(int_column)"),
    _ts("ROUND(150.75, 0)"),
    _ts("ROUND(748.58, -1)"),
    # Local T-SQL JSON
    _ts("ISJSON('1')"),
    _ts("ISJSON('\"text\"', SCALAR)"),
    _ts("JSON_ARRAY('a', 1, NULL)"),
    _ts("JSON_ARRAY(1, JSON_OBJECT('k': 'v'))"),
    _ts("1.50", query="SELECT JSON_VALUE('{\"a\": 1.50}', '$.a')", note="JSON_VALUE's number"),
    _ts("1.50", query="SELECT JSON_ARRAY(1.50)", want="[1.5]", note="JSON_ARRAY's decimal"),
    _ts("1e2", query="SELECT JSON_VALUE('{\"a\": 1e2}', '$.a')"),
    _ts("9223372036854775807 + 1"),  # WR-247: a whole number past an int is a decimal of its digits
    _ts("ABS(-2147483648)"),
    _ts("CONCAT('a', NULL)"),
    _ts("CONCAT(NULL, NULL)"),
    _ts("CONCAT_WS('-', 'a', NULL, 'b')"),
    _ts("'a ' BETWEEN 'a' AND 'a'", query="SELECT CASE WHEN 'a ' BETWEEN 'a' AND 'a' THEN 1 ELSE 0 END = 1"),
    _ts("'a '", query="SELECT CASE WHEN 'a ' IN (SELECT 'a') THEN 1 ELSE 0 END", want="1",
        note="WR-246: IN (SELECT ...) ignores trailing spaces"),
    _ts("NULLIF('a ', 'a')"),
    _ts("UPPER('é')"),
]

# (page, expression) -> why it isn't run here
ILLUSTRATIVE: dict[tuple[str, str], str] = {
    (P, "SELECT *"): "a fragment: a view made with SELECT *, not a statement",
    (P, "SELECT 2147483647 + 1"): "an error example: it stops with Msg 8115 (test_tsql_arithmetic_errors)",
    (P, "regexp_replace('aaa', 'a*', 'X')"): "Spark's answer; this PC refuses that pattern by name (WR-257)",
}

# Inline `SELECT ...` statements run as written (see Statement)
STATEMENTS: list[Statement] = [
    Statement(P, "SELECT * FROM t", "local-tsql", setup=("CREATE TABLE dbo.t (a INT)",),
              note="a name without a schema reads dbo.t"),
]
