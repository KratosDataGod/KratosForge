"""Inline examples checked by test_docs_examples.py on the pages added after the first five: see docexamples.py.
Not a test module."""

from __future__ import annotations

from docexamples import Claim, Statement

NB = "notebook-spark-on-this-pc.md"
LT = "live-testing.md"

CLAIMS: list[Claim] = [
    # notebook-spark-on-this-pc.md: a notebook's spark.sql on this PC
    Claim(NB, "current_catalog()", "notebook"),
    Claim(NB, "upper(14)", "notebook"),
    # live-testing.md: 7 / 2 is 3 in T-SQL (a local data project's T-SQL here) and 3.5 in Spark SQL
    Claim(LT, "7 / 2", "local-tsql"),
    Claim(LT, "7 / 2", "notebook", want="3.5", note="the same sentence's Spark SQL value"),
]

# (page, expression) -> why it isn't run here
ILLUSTRATIVE: dict[tuple[str, str], str] = {
    (NB, "cast(i AS string)"): "names the column, not a value: test_spark_sql_names.py checks the name",
    (LT, "spark.range(5).count()"): "runs on a Databricks cluster in the live tests",
    ("learn.md", "YEAR(order_date) = 2026"): "the SQ002 fix's rewrite, not a value: test_learn_fixes.py checks it",
    ("metadata-guide.md", '[[check]] python = "transforms.checks:valid_email"'):
        "a metadata config line naming a check function, not an expression",
    ("notebook-magics.md", "df = %sql ..."): "a publish rewrite, not a value: test_notebook_magics.py checks it",
    ("notebook-magics.md", "SELECT * FROM _sqldf WHERE total > 10"): "reads _sqldf, which an earlier SQL cell sets",
    ("notebook-resources.md", "SELECT * FROM 'builtin/x.csv'"): "a path the resources rewrite finds, not a query run",
    ("notebook-templates.md", "SELECT *"): "a fragment: the columns a template leaves out, not a statement",
    ("warehouse.md", "SELECT TOP (100) *"): "a fragment: what clicking a table runs on the Fabric warehouse",
    ("warehouse.md", ("SELECT TOP (50) * FROM queryinsights.long_running_queries "
                      "ORDER BY median_total_elapsed_time_ms DESC")): "reads the Fabric warehouse's queryinsights views",
    ("warehouse.md", "SELECT TOP (50) * FROM queryinsights.frequently_run_queries ORDER BY number_of_runs DESC"):
        "reads the Fabric warehouse's queryinsights views",
    ("warehouse.md", "SELECT TOP (100) * FROM queryinsights.exec_requests_history ORDER BY start_time DESC"):
        "reads the Fabric warehouse's queryinsights views",
}

# Inline `SELECT ...` statements run as written (see Statement)
STATEMENTS: list[Statement] = []
