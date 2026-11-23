"""WR-700: DDL-012, DEFAULT isn't supported on Fabric Warehouse or the SQL analytics endpoint.

Microsoft Learn's table constraints page says "SQL analytics endpoint and Warehouse don't support default
constraints at this time", and Fabric's INSERT and UPDATE syntax have no DEFAULT. The rule is an error there,
like the other rules for constructs Microsoft documents as unsupported, and doesn't apply to other targets."""

import pytest

from fabric_desktop.sqlcompat import RULES, analyze
from fabric_desktop.sqlcompat.profiles import DOCS


def _found(sql, target):
    return [(d["rule"], sql[d["from"]:d["to"]]) for d in analyze(sql, target)["diagnostics"] if d["rule"] == "DDL-012"]


def test_the_rule_is_a_definite_error_sourced_to_the_table_constraints_page():
    rule = RULES["DDL-012"]
    assert set(rule.targets) == {"warehouse", "endpoint"}
    assert rule.level("warehouse") == rule.level("endpoint") == "error"
    assert rule.confidence == "high"
    assert DOCS[rule.doc][0] == "https://learn.microsoft.com/fabric/data-warehouse/table-constraints"
    # graded like the other definite "not supported" DDL rules
    assert rule.level("warehouse") == RULES["DDL-001"].level("warehouse") == RULES["DDL-002"].level("warehouse")


def test_create_index_keeps_its_own_unverified_rule():
    rule = RULES["DDL-016"]
    assert rule.targets == ("warehouse",) and rule.level("warehouse") == "unverified"
    assert [(d["rule"], d["severity"]) for d in analyze("CREATE INDEX i ON dbo.t (a)", "warehouse")["diagnostics"]] \
        == [("DDL-016", "unverified")]


FOUND = [
    # CREATE TABLE: inline, and the named CONSTRAINT x DEFAULT form
    ("CREATE TABLE dbo.d (a int DEFAULT 0)", [("DDL-012", "DEFAULT")]),
    ("CREATE TABLE dbo.d (a int NOT NULL CONSTRAINT df_a DEFAULT (0), b int)",
     [("DDL-012", "CONSTRAINT df_a DEFAULT")]),
    ("CREATE TABLE dbo.d (a int DEFAULT 0, b varchar(5) DEFAULT 'x')", [("DDL-012", "DEFAULT"), ("DDL-012", "DEFAULT")]),
    # ALTER TABLE ADD: a column with DEFAULT, and a named default constraint (reported once, not also as DDL-007)
    ("ALTER TABLE dbo.d ADD b int NULL DEFAULT 1", [("DDL-012", "DEFAULT")]),
    ("ALTER TABLE dbo.d ADD CONSTRAINT df_b DEFAULT 1 FOR b", [("DDL-012", "CONSTRAINT df_b DEFAULT")]),
    # INSERT and UPDATE that ask for a default
    ("INSERT INTO dbo.d DEFAULT VALUES", [("DDL-012", "DEFAULT VALUES")]),
    ("INSERT INTO dbo.d VALUES (DEFAULT, 1)", [("DDL-012", "DEFAULT")]),
    ("INSERT INTO dbo.d (a, b) VALUES (1, 2), (3, DEFAULT)", [("DDL-012", "DEFAULT")]),
    ("UPDATE dbo.d SET a = DEFAULT WHERE b = 1", [("DDL-012", "DEFAULT")]),
    ("UPDATE d SET a = 1, b = DEFAULT FROM dbo.d AS d WHERE d.c = 2", [("DDL-012", "DEFAULT")]),
    # MERGE's own INSERT and UPDATE
    (("MERGE dbo.d AS t USING dbo.s AS s ON t.a = s.a WHEN MATCHED THEN UPDATE SET b = DEFAULT "
      "WHEN NOT MATCHED THEN INSERT (a, b) VALUES (s.a, DEFAULT);"), [("DDL-012", "DEFAULT"), ("DDL-012", "DEFAULT")]),
    ("MERGE dbo.d AS t USING dbo.s AS s ON t.a = s.a WHEN NOT MATCHED THEN INSERT DEFAULT VALUES;",
     [("DDL-012", "DEFAULT VALUES")]),
    # inside a procedure body
    ("CREATE PROCEDURE dbo.p AS BEGIN INSERT INTO dbo.d DEFAULT VALUES; END", [("DDL-012", "DEFAULT VALUES")]),
    # DEFAULT that isn't a column default
    ("EXEC dbo.p @a = DEFAULT", []),
    ("EXEC dbo.p 1, DEFAULT", []),
    ("SELECT dbo.f(DEFAULT)", []),
    ("INSERT INTO dbo.d VALUES (dbo.f(DEFAULT))", []),
    ("INSERT INTO dbo.d SELECT dbo.f(DEFAULT)", []),
    ("CREATE TABLE dbo.d (a int, b varchar(10) COLLATE database_default)", []),
    ("UPDATE dbo.d SET a = 1 WHERE b = 2", []),
]


@pytest.mark.parametrize("sql,expected", FOUND)
def test_warehouse_marks_each_default(sql, expected):
    assert _found(sql, "warehouse") == expected


@pytest.mark.parametrize("sql", ["CREATE TABLE dbo.d (a int DEFAULT 0)", "INSERT INTO dbo.d DEFAULT VALUES",
                                 "UPDATE dbo.d SET a = DEFAULT WHERE b = 1"])
def test_the_endpoint_marks_it_too_beside_its_read_only_error(sql):
    rules = {d["rule"] for d in analyze(sql, "endpoint")["diagnostics"]}
    assert {"DDL-012", "EP-001"} <= rules


def test_a_default_makes_the_statement_a_known_problem_with_the_source_and_a_fix():
    report = analyze("CREATE TABLE dbo.d (a int DEFAULT 0)", "warehouse")
    assert report["outcome"]["status"] == "known_problems"
    (d,) = report["diagnostics"]
    assert d["severity"] == "error" and d["title"] == "DEFAULT isn't supported"
    assert "default constraints" in d["message"] and d["message"].startswith("Fabric Warehouse doesn't support")
    assert "COALESCE" in d["action"]
    assert d["doc"] == "https://learn.microsoft.com/fabric/data-warehouse/table-constraints"


@pytest.mark.parametrize("target", ["sqldb", "spark", "duckdb"])
@pytest.mark.parametrize("sql", ["CREATE TABLE dbo.d (a int DEFAULT 0)", "CREATE TABLE dbo.d (a int CONSTRAINT df DEFAULT 0)",
                                 "ALTER TABLE dbo.d ADD b int NULL DEFAULT 1", "INSERT INTO d DEFAULT VALUES",
                                 "INSERT INTO d VALUES (DEFAULT)", "UPDATE d SET a = DEFAULT WHERE b = 1"])
def test_other_targets_are_left_as_they_were(target, sql):
    assert _found(sql, target) == []
