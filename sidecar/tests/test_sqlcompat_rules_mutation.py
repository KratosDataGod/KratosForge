"""SQL compatibility rules (sqlcompat/rules.py) that mutation testing (batch 11,
docs/quality/mutation-testing.md) showed no test checked: the exact text each finding points at, the
rules for #temp names, columns added by ALTER TABLE, CTEs with a column list, the clock words, and
statements the rules must read to their end without failing."""

import dataclasses

import pytest

from fabric_desktop.sqlcompat import RULES, analyze


def _found(sql, target):
    """(rule, the SQL text the finding points at) for each finding, in report order."""
    return [(d["rule"], sql[d["from"]:d["to"]]) for d in analyze(sql, target)["diagnostics"]]


CASES = [
    # #temp names: one # joined to the name is a session temp table; ## is global; "# t" isn't a name
    ("warehouse", "CREATE TABLE #t (a nvarchar(10))", [("DDL-006", "CREATE TABLE #t"), ("TYP-001", "nvarchar")]),
    ("warehouse", "CREATE TABLE # t (a nvarchar(10))", [("DDL-006", "CREATE TABLE # t")]),
    ("warehouse", "CREATE TABLE #t", [("DDL-006", "CREATE TABLE #t")]),
    ("warehouse", "SELECT * FROM ##g", [("DDL-005", "##g")]),
    # types: the whole type is marked, also when the statement ends right after it
    ("warehouse", "CREATE TABLE t (a varchar(max), b varbinary(max), c datetime2(7))",
     [("TYP-005", "varchar(max)"), ("TYP-005", "varbinary(max)"), ("TYP-003", "datetime2(7)")]),
    ("warehouse", "ALTER TABLE t ADD a varchar(max)", [("TYP-005", "varchar(max)")]),
    # columns added by ALTER TABLE: with or without COLUMN, and each one after a comma
    ("warehouse", "ALTER TABLE t ADD COLUMN a nvarchar(10)", [("TYP-001", "nvarchar")]),
    ("warehouse", "ALTER TABLE t ADD a int, b nvarchar(5)", [("TYP-001", "nvarchar")]),
    ("warehouse", "ALTER TABLE t ADD a int", []),
    ("warehouse", "ALTER TABLE t ADD a int NOT NULL", [("DDL-007", "NOT NULL")]),
    ("warehouse", "ALTER TABLE t ADD CHECK (a > 0)", [("DDL-007", "ADD CHECK")]),
    ("warehouse", "ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0)", [("DDL-007", "ADD CONSTRAINT c CHECK")]),
    # a table made from a query: past WITH (options), and in CONVERT
    ("warehouse", "CREATE TABLE t WITH (DISTRIBUTION = ROUND_ROBIN) AS SELECT CAST(a AS nvarchar(10)) AS a FROM s",
     [("TYP-002", "nvarchar")]),
    ("warehouse", "CREATE TABLE t AS SELECT CONVERT(nvarchar(10), a) AS a FROM s", [("TYP-002", "nvarchar")]),
    ("warehouse", "CREATE TABLE t AS SELECT CAST(a AS nvarchar(10)) AS a FROM s", [("TYP-002", "nvarchar")]),
    ("warehouse", "CREATE TABLE t", []),
    # objects and statements Fabric doesn't support: from the first word to the last
    ("warehouse", "CREATE MATERIALIZED VIEW v AS SELECT 1", [("DDL-001", "CREATE MATERIALIZED VIEW")]),
    ("warehouse", "CREATE TABLE [a/b] (x int)", [("DDL-008", "[a/b]")]),
    ("warehouse", "CREATE STATISTICS s ON t (a, b)", [("DDL-010", "CREATE STATISTICS")]),
    ("warehouse", "CREATE USER u FROM LOGIN l", [("DDL-011", "CREATE USER")]),
    ("warehouse", "CREATE EXTERNAL TABLE e (a int)", [("DDL-013", "CREATE EXTERNAL TABLE")]),
    ("sqldb", "CREATE LOGIN l WITH PASSWORD = 'x'", [("DDL-015", "CREATE LOGIN")]),
    ("warehouse", "CREATE TABLE t (a int, PRIMARY KEY (a))", [("DDL-004", "PRIMARY KEY")]),
    ("warehouse", "SELECT * FROM a INNER HASH JOIN b ON a.x = b.x", [("DML-005", "HASH JOIN")]),
    ("endpoint", "DROP TABLE t", [("EP-001", "DROP TABLE"), ("DES-001", "DROP TABLE")]),
    ("endpoint", "CREATE TABLE t (a int)", [("EP-001", "CREATE TABLE")]),
    # the clock: CURRENT_TIMESTAMP is UTC in T-SQL; CURRENT_DATE isn't flagged for that
    ("warehouse", "SELECT CURRENT_TIMESTAMP, CURRENT_DATE FROM t", [("SEM-005", "CURRENT_TIMESTAMP")]),
    ("warehouse", "SELECT CURRENT_DATE FROM t", []),
    # a date pattern only matters when one is given
    ("spark", "SELECT date_format(d, 'yyyy'), to_date(d) FROM t", [("LOC-003", "date_format")]),
    ("spark", "SELECT from_unixtime(ts, 'yyyy'), from_unixtime(ts) FROM t", [("LOC-003", "from_unixtime")]),
    # ISNULL(x, y) is T-SQL's replace-NULL; Spark's one-argument isnull(x) is a test, so it isn't flagged
    ("spark", "SELECT ISNULL(a, 0), ISNULL(a) FROM t", [("FN-002", "ISNULL")]),
    # GO alone on its line is a batch separator
    ("spark", "SELECT 1\nGO\nSELECT 2", [("SYN-005", "GO")]),
    # an UPDATE whose WHERE comes after a subquery still has a WHERE
    ("warehouse", "UPDATE t SET a = (SELECT max(b) FROM u) WHERE id = 1", []),
    ("warehouse", "UPDATE t SET a = 1", [("DES-003", "UPDATE")]),
    # CTEs: one with a column list that names itself, and the second of two
    ("warehouse", "WITH c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT * FROM c", [("DML-002", "c")]),
    ("warehouse", "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM b) SELECT * FROM b", [("DML-002", "b")]),
    # statements that end early are read to their end without failing
    ("warehouse", "CREATE TABLE t (a nvarchar(10)", [("TYP-001", "nvarchar")]),
    ("warehouse", "WITH a AS (SELECT 1)", [("SYN-007", ")")]),
    ("warehouse", "SELECT * FROM t WITH", [("SYN-007", "WITH")]),
]


@pytest.mark.parametrize("target, sql, expected", CASES, ids=[f"{c[0]}:{c[1][:40]}" for c in CASES])
def test_each_finding_points_at_the_right_text(target, sql, expected):
    assert _found(sql, target) == expected


def test_more_than_1024_columns_is_one_finding_on_create_table():
    sql = "CREATE TABLE t ({})".format(", ".join(f"c{i} int" for i in range(1025)))
    report = analyze(sql, "warehouse")
    assert _found(sql, "warehouse") == [("DDL-009", "CREATE TABLE")]
    assert "this one has 1,025" in report["diagnostics"][0]["message"]
    sql = "CREATE TABLE t ({})".format(", ".join(f"c{i} int" for i in range(1024)))
    assert _found(sql, "warehouse") == []


def test_a_bad_name_is_quoted_to_64_characters():
    name = "a" * 63 + "/bcdef"
    report = analyze(f"CREATE TABLE [{name}] (x int)", "warehouse")
    (d,) = report["diagnostics"]
    assert d["rule"] == "DDL-008"
    assert d["title"] == f"Name '{name[:64]}' isn't allowed"
    assert name[:65] not in d["title"]


def test_the_rules_cant_be_changed_while_the_app_runs():
    rule = RULES["SYN-001"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.severity = "information"
