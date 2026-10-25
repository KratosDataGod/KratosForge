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
    assert [r["id"] for r in listed] == [f"SQ{n:03}" for n in range(1, 11)]
    assert all(r["kind"] == "sql" and r["severity"] in p.SEVERITIES for r in listed)
