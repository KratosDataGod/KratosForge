"""SQL best practices for T-SQL: read on this PC, never run."""

import pytest

from fabric_desktop.sqlcompat import practices as p


def ids(query, **kw):
    return [f["rule_id"] for f in p.check(query, **kw)["findings"]]


@pytest.mark.parametrize("query, expected", [
    ("CREATE VIEW dbo.v AS SELECT * FROM dbo.t", ["SQ001"]),
    ("CREATE PROCEDURE dbo.p AS BEGIN SELECT DISTINCT * FROM dbo.t END", ["SQ001"]),
    ("SELECT * FROM dbo.t", []),  # an ad hoc look is fine
    ("SELECT COUNT(*) FROM dbo.t", []),
    ("INSERT INTO dbo.a SELECT * FROM dbo.b", ["SQ010"]),
    ("SELECT a FROM dbo.t WHERE YEAR(order_date) = 2026", ["SQ002"]),
    ("SELECT a FROM dbo.t WHERE DATEPART(year, d) = 2026", ["SQ002"]),
    ("SELECT a FROM dbo.t WHERE d >= '2026-01-01' AND CAST(@x AS int) = 1", []),
    ("SELECT UPPER(a) FROM dbo.t WHERE a = 'x'", []),
    ("CREATE TABLE dbo.t (a varchar(max), b varchar(50))", ["SQ003"]),
    ("SELECT a INTO dbo.n FROM dbo.t", ["SQ004"]),
    ("SELECT a INTO #tmp FROM dbo.t", []),
    ("SELECT a FROM sales", ["SQ005"]),
    ("WITH x AS (SELECT 1 AS a) SELECT a FROM x", []),
    ("UPDATE d SET d.a = 1 FROM dbo.dim AS d WHERE d.b = 2", []),
    ("UPDATE STATISTICS dbo.t", []),
    ("SELECT a FROM #tmp", []),
    ("SELECT a FROM dbo.a CROSS JOIN dbo.b", ["SQ006"]),
    ("SELECT a FROM dbo.a JOIN dbo.b WHERE 1 = 1", ["SQ006"]),
    ("SELECT a FROM dbo.a JOIN dbo.b ON a.id = b.id", []),
    ("DECLARE c CURSOR FOR SELECT a FROM dbo.t", ["SQ007"]),
    ("WHILE @i < 10 BEGIN SET @i = @i + 1 END", ["SQ007"]),
    ("CREATE VIEW dbo.v AS SELECT TOP 10 a FROM dbo.t ORDER BY a", ["SQ008"]),
    ("SELECT TOP 5 a FROM dbo.t", ["SQ008"]),
    ("SELECT TOP (5) a FROM dbo.t ORDER BY a", []),
    ("SELECT a, ROW_NUMBER() OVER (ORDER BY a) AS n FROM dbo.t", []),
    ("SELECT DISTINCT a FROM dbo.t GROUP BY a", ["SQ009"]),
    # SQ012: NOT IN over a subquery returns nothing once the subquery returns a NULL
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT b FROM dbo.u)", ["SQ012"]),
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT b FROM dbo.u WHERE b IS NOT NULL)", []),
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT u.b FROM dbo.u AS u WHERE u.b IS NOT NULL)", []),
    ("SELECT a FROM dbo.t WHERE a NOT IN (SELECT b FROM dbo.u WHERE c IS NOT NULL)", ["SQ012"]),  # b can still be NULL
    ("SELECT a FROM dbo.t WHERE a NOT IN (1, 2)", []),
    ("SELECT a FROM dbo.t WHERE a IN (SELECT b FROM dbo.u)", []),
    ("SELECT a FROM dbo.t t WHERE NOT EXISTS (SELECT 1 FROM dbo.u u WHERE u.b = t.a)", []),
    # SQ015: a subquery in the SELECT list that reads the outer row
    ("SELECT o.id, (SELECT MAX(p.amount) FROM dbo.payments p WHERE p.order_id = o.id) AS paid FROM dbo.orders o",
     ["SQ015"]),
    ("SELECT o.id, (SELECT COUNT(*) FROM dbo.payments) AS total FROM dbo.orders o", []),
    ("SELECT o.id, (SELECT dbo.fn_rate(1)) AS rate FROM dbo.orders o", []),  # a schema's function, not an outer alias
    ("SELECT o.id, (SELECT MAX(dbo.payments.amount) FROM dbo.payments) AS top_paid FROM dbo.orders o", []),
    ("SELECT a FROM dbo.t WHERE a IN (SELECT u.b FROM dbo.u u WHERE u.c = t.c)", []),  # not in the SELECT list
    # SQ016: a leading wildcard in WHERE reads every row
    ("SELECT a FROM dbo.t WHERE name LIKE '%son'", ["SQ016"]),
    ("SELECT a FROM dbo.t WHERE (name LIKE '%son' OR b = 1)", ["SQ016"]),
    ("SELECT a FROM dbo.t WHERE name LIKE N'%son'", ["SQ016"]),
    ("SELECT a FROM dbo.t WHERE name LIKE 'Jo%'", []),
    ("SELECT CASE WHEN name LIKE '%son' THEN 1 END AS s FROM dbo.t", []),
    ("SELECT a FROM dbo.t AS x JOIN dbo.u AS y ON y.n LIKE '%' + x.n", []),
    # DM002: ROW_NUMBER() written to a key column renumbers it on every load
    ("INSERT INTO dbo.dim_customer (customer_key, customer_id) "
     "SELECT ROW_NUMBER() OVER (ORDER BY customer_id) AS customer_key, customer_id FROM stg.customer", ["DM002"]),
    ("CREATE TABLE dbo.dim_product AS SELECT ROW_NUMBER() OVER (ORDER BY id) product_sk, id FROM stg.product",
     ["DM002"]),
    ("SELECT ROW_NUMBER() OVER (ORDER BY id) AS customer_key, id FROM stg.customer", []),  # only looked at
    ("INSERT INTO dbo.t (rn, id) SELECT ROW_NUMBER() OVER (PARTITION BY id ORDER BY d) AS rn, id FROM dbo.s", []),
    # WH001: a TRUNCATE and a reload of the same table
    ("TRUNCATE TABLE dbo.sales; INSERT INTO dbo.sales (id) SELECT id FROM stg.sales", ["WH001"]),
    ("TRUNCATE TABLE dbo.sales; INSERT INTO dbo.other (id) SELECT id FROM stg.sales", []),
    ("TRUNCATE TABLE stg.sales", []),
    # SE001: a password in the script
    ("CREATE LOGIN etl WITH PASSWORD = 'Summer2026!'", ["SE001"]),
    ("CREATE DATABASE SCOPED CREDENTIAL c WITH IDENTITY = 'u', SECRET = '<from Key Vault>'", []),
])
def test_rules(query, expected):
    assert ids(query) == expected


def test_parameters_named_like_keywords_arent_tables():
    sql = "CREATE OR ALTER PROCEDURE dbo.p\n    @from date,\n    @to date\nAS\nBEGIN\n    SELECT a FROM dbo.t WHERE d >= @from\nEND"
    assert ids(sql) == []


def test_findings_have_position_order_and_settings():
    out = p.check("SELECT a FROM sales;\nCREATE VIEW dbo.v AS SELECT * FROM dbo.t")
    assert [(f["rule_id"], f["statement"], f["line"], f["severity"]) for f in out["findings"]] == [
        ("SQ001", 2, 2, "Warning"), ("SQ005", 1, 1, "Info")]
    assert out["counts"] == {"Error": 0, "Warning": 1, "Info": 1} and out["statements"] == 2
    tuned = p.check("SELECT a FROM sales", settings={"SQ005": {"severity": "Error"}})
    assert tuned["findings"][0]["severity"] == "Error"
    assert ids("SELECT a FROM sales", settings={"SQ005": {"enabled": False}}) == []
    assert ids("SELECT a FROM sales", settings={"SQ005": "off"}) == ["SQ005"]  # not a dict: ignored


def test_only_tsql_targets_and_text():
    assert ids("SELECT a FROM sales", target="endpoint") == ["SQ005"]
    with pytest.raises(ValueError):
        p.check("SELECT 1", target="spark")
    with pytest.raises(TypeError):
        p.check(None)  # type: ignore[arg-type]


def test_rules_list():
    listed = p.rules()
    assert [r["id"] for r in listed] == [f"SQ{n:03}" for n in range(1, 11)] + ["SQ012", "SQ015", "SQ016", "DM002", "WH001",
                                                                                   "SE001"]
    assert all(r["kind"] == "sql" and r["severity"] in p.SEVERITIES for r in listed)


def test_a_secret_is_reported_once_on_its_line_and_masked_nowhere():
    sql = "SELECT a FROM dbo.t;\nEXEC dbo.connect 'Server=x;User Id=u;Password=Hunter2xyz;'"
    out = p.check(sql)
    se = [f for f in out["findings"] if f["rule_id"] == "SE001"]
    assert [(f["line"], f["statement"], f["severity"]) for f in se] == [(2, 2, "Error")]
    assert "Hunter2xyz" not in repr(out)  # the finding never repeats the secret
    assert out["findings"][0]["rule_id"] == "SE001"  # worst first
    assert ids(sql, settings={"SE001": {"enabled": False}}) == []


def test_a_secret_after_a_stray_carriage_return_points_at_its_own_place():
    sql = "SELECT a FROM dbo.t\rSELECT b FROM dbo.u;\nEXEC dbo.go 'Password=Hunter2xyz;'"
    se = [f for f in p.check(sql)["findings"] if f["rule_id"] == "SE001"]
    assert [(f["line"], f["statement"]) for f in se] == [(2, 2)]
