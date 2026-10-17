"""SQL-001: the SQL query page's dialects and Format SQL (the editor itself is checked in app/tests)."""

import pytest

from fabric_desktop import server, sql, sql_dialects
from fabric_desktop.sql_format import format_sql
from test_notebook import LH, _sql_service

NESTED = """-- Revenue by month
WITH monthly AS (SELECT date_trunc('month', s.order_date) month, sum(s.amount) revenue /* total */
FROM sales s join customers c on c.id=s.cid WHERE c.region != 'EU' and s.id in (select id from returns where kind = 'Late') GROUP BY 1)
select * from monthly order by month limit 12; -- trailing
"""


def test_every_dialect_is_listed_and_only_spark_and_duckdb_run_here():
    assert list(sql_dialects.DIALECTS) == ["spark", "warehouse", "sqldb", "duckdb"]
    assert [d["id"] for d in [sql_dialects.get(k) for k in sql_dialects.DIALECTS] if d["runs_here"]] == ["spark", "duckdb"]
    assert sql_dialects.get(None)["id"] == "spark"
    with pytest.raises(ValueError, match="Unknown SQL dialect"):
        sql_dialects.get("oracle")


def test_format_lays_out_ctes_joins_and_nested_queries():
    out = format_sql(NESTED)
    assert out["changed"] and not out["warning"]
    text = out["sql"]
    assert text.startswith("-- Revenue by month\nWITH monthly AS (\n  SELECT\n")
    assert "\n  JOIN customers AS c\n    ON c.id = s.cid\n" in text
    assert "s.id IN (\n" in text and "      SELECT\n        id\n      FROM returns\n" in text  # nested query indented
    assert "\nLIMIT 12;" in text


def test_format_keeps_comments_identifiers_and_strings():
    text = format_sql(NESTED)["sql"]
    assert "-- Revenue by month" in text and "/* total */" in text and "-- trailing" in text
    assert "'EU'" in text and "'Late'" in text
    assert "s.order_date" in text and "monthly" in text
    quoted = format_sql("select `Order Date`, CustomerName from dbo.`Sales Data` where Region = 'East' -- keep\n")
    assert "`Order Date`" in quoted["sql"] and "CustomerName" in quoted["sql"] and "`Sales Data`" in quoted["sql"]
    assert "-- keep" in quoted["sql"] and "'East'" in quoted["sql"]


def test_format_capitalizes_keywords_and_functions_only():
    text = format_sql("select region, count(*) n from Sales group by region")["sql"]
    assert text == "SELECT\n  region,\n  COUNT(*) AS n\nFROM Sales\nGROUP BY\n  region"


def test_format_is_stable():
    once = format_sql(NESTED)["sql"]
    assert format_sql(once)["sql"] == once


@pytest.mark.parametrize("query, why", [
    ("select ifnull(a, 1) from t", "remove the function 'IFNULL'"),  # sqlglot would rename it to COALESCE
    ("select 'it''s' from t", "change the query's structure"),      # sqlglot reads this as CONCAT('it', 's')
])
def test_format_refuses_a_rewrite_and_leaves_the_sql_alone(query, why):
    out = format_sql(query)
    assert out == {"sql": query, "changed": False, "warning": f"Format SQL left the query as it was, because formatting would {why}."}


def test_format_leaves_sql_it_cant_read_unchanged():
    out = format_sql("select from where")
    assert out["sql"] == "select from where" and not out["changed"]
    assert out["warning"] == ("Format SQL left the query as it was: it couldn't be read as Spark SQL (Lakehouse). "
                              "Expected table name but got 'where' (line 1, column 17).")


def test_format_handles_empty_and_comment_only_sql():
    assert format_sql("") == {"sql": "", "changed": False, "warning": ""}
    assert format_sql("-- just a note\n") == {"sql": "-- just a note\n", "changed": False, "warning": ""}


def test_format_t_sql_keeps_brackets_variables_and_top():
    out = format_sql("select top 5 [Order Id], @cutoff from dbo.Sales -- c\nwhere isnull(x,0)=1 and dateadd(day,1,d) > getdate()",
                     "warehouse")
    assert not out["warning"]
    for part in ("TOP 5", "[Order Id]", "@cutoff", "dbo.Sales -- c", "ISNULL(x, 0)", "DATEADD(DAY, 1, d)", "GETDATE()"):
        assert part in out["sql"]


def test_format_duckdb():
    out = format_sql("select * from read_parquet('x.parquet') where s = 'Ab'", "duckdb")
    assert out["sql"] == "SELECT\n  *\nFROM READ_PARQUET('x.parquet')\nWHERE\n  s = 'Ab'"


def test_duckdb_dialect_runs_duckdb_sql_and_t_sql_does_not_run(tmp_path, monkeypatch):
    service, _ = _sql_service(tmp_path, monkeypatch)
    out = service.run_sql("SELECT Country, count(*)::INT AS n FROM sales GROUP BY ALL ORDER BY n DESC, Country", LH, dialect="duckdb")
    assert out["rows"][0] == ["US", 2]
    with pytest.raises(ValueError, match="Fabric Warehouse \\(T-SQL\\) can't run on this PC"):
        service.run_sql("SELECT TOP 5 * FROM sales", LH, dialect="warehouse")


def test_spark_is_still_the_default_with_the_duckdb_fallback():
    assert sql.run("SELECT 1 AS x").column("x").to_pylist() == [1]
    assert sql.run("SELECT 42::INT AS x").column("x").to_pylist() == [42]  # DuckDB syntax still accepted


def test_the_page_can_call_the_new_methods(tmp_path):
    service = server.Service(client_id="x", root=tmp_path)
    reply = server.handle(service, '{"id": 1, "method": "sql_format", "params": {"query": "select 1 a", "dialect": "spark"}}')
    assert reply == {"id": 1, "result": {"sql": "SELECT\n  1 AS a", "changed": True, "warning": ""}}
    labels = server.handle(service, '{"id": 2, "method": "sql_dialects"}')["result"]
    assert [d["label"] for d in labels] == ["Spark SQL (Lakehouse)", "Fabric Warehouse (T-SQL)", "Fabric SQL database (T-SQL)", "DuckDB (this PC)"]
