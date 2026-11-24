"""Inline examples checked by test_docs_examples.py: see docexamples.py. Not a test module."""

from __future__ import annotations

from docexamples import Claim, Statement

# A Delta table with versions 0 to 3 (local-spark setup)
_SALES = ("CREATE TABLE sales USING DELTA AS SELECT * FROM VALUES (1, 10.0), (2, 20.0) AS v(id, amount)",
          "INSERT INTO sales VALUES (3, 30.0)", "INSERT INTO sales VALUES (4, 40.0)", "INSERT INTO sales VALUES (5, 50.0)")

CLAIMS: list[Claim] = [
    # local-data.md: a local data project's T-SQL
    Claim("local-data.md", "1.0 / 3", "local-tsql"),
    Claim("local-data.md", "'Ann  ' = 'Ann'", "local-tsql",
          query="SELECT CASE WHEN 'Ann  ' = 'Ann' THEN 'true' ELSE 'false' END"),
    Claim("local-data.md", "''", "local-tsql", query="SELECT CAST('' AS INT)",
          note="both mentions: '' as an integer, in a cast and in + with a number"),
    Claim("local-data.md", "CAST(a_datetime AS VARCHAR)", "local-tsql",
          query="SELECT CAST(CAST('2024-03-05 13:04:00' AS DATETIME) AS VARCHAR(30))"),  # WR-707: style 0
    # notebooks.md: a notebook's spark.sql on this PC
    Claim("notebooks.md", "to_date('2026-03-02', 'yyyy-MM-dd HH:mm:ss')", "notebook"),
    Claim("notebooks.md", "decimal(10,2) / decimal(10,2)", "notebook", typeof=True,
          query="SELECT typeof(CAST(1 AS DECIMAL(10,2)) / CAST(3 AS DECIMAL(10,2)))"),
    # engines.md: Spark SQL on this PC
    Claim("engines.md", "'at ' || ts", "notebook",
          query="SELECT 'at ' || ts FROM (SELECT TIMESTAMP'2026-01-02 03:04:05' AS ts)"),
    Claim("engines.md", "CAST(3.7 AS INT)", "notebook"),
    Claim("engines.md", "CAST('1.9' AS INT)", "notebook"),
]

# (page, expression) -> why it isn't run here
ILLUSTRATIVE: dict[tuple[str, str], str] = {
    ("notebooks.md", "entry_point...getContext()"): "a Databricks notebook helper's path, not an expression",
    ("notebooks.md", "display(orders)"): "names the data frame a cell displayed, not a value",
    ("notebooks.md", "SELECT * FROM t VERSION AS OF n"): "a pattern: t and n stand for a table and a version",
    ("local-data.md", "SELECT * FROM sales TIMESTAMP AS OF '2026-10-01 14:00'"): "needs a version committed by that time",
    ("local-data.md", "SELECT * FROM sales AT (TIMESTAMP => '2026-10-01 14:00')"): "needs a version committed by that time",
    ("notebooks.md", "SELECT * FROM dbo.sales VERSION AS OF 3"): "reads the attached Fabric lakehouse's table",
    ("notebooks.md", "SELECT * FROM table_changes('dbo.orders', 1, 3)"): "reads the change feed of the attached lakehouse's table",
    ("notebooks.md", "SELECT * FROM sales"): "the SQL page against a Fabric lakehouse's sales table",
}

# Inline `SELECT ...` statements run as written (see Statement)
STATEMENTS: list[Statement] = [
    Statement("local-data.md", "SELECT * FROM sales VERSION AS OF 3", "local-spark",
              setup=_SALES),
    Statement("local-data.md", "SELECT * FROM sales AT (VERSION => 3)", "local-duckdb", setup=_SALES,
              note="setup runs in the statement's dialect: see _rows"),
]
