"""SQL-002: the compatibility analyzer, its rules and profiles, and the fixtures that pin its behavior."""

import hashlib
import json
import logging
import socket
from pathlib import Path

import pytest

from fabric_desktop import server, sql
from fabric_desktop.sqlcompat import PROFILES, RULE_VERSION, RULES, analyze, profiles, report_json
from fabric_desktop.sqlcompat import analyzer

FIXTURES = Path(__file__).parent / "fixtures" / "sql_compat_v1.jsonl"
LINES = [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip()]
META, CASES = LINES[0]["meta"], LINES[1:]


def test_fixtures_match_this_rule_version_and_cover_every_target():
    # Changing a rule means moving RULE_VERSION on and reviewing these expectations again.
    assert META["rule_version"] == RULE_VERSION
    assert len(CASES) >= 100
    assert len({c["id"] for c in CASES}) == len(CASES)
    assert {c["target"] for c in CASES} == set(PROFILES)
    assert sum(c["kind"] == "false_positive" for c in CASES) >= 20
    assert {c["expect"] for c in CASES} >= {"known_problems", "review", "unverified", "no_known_problems", "not_checked"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture(case):
    report = analyze(case["sql"], case["target"])
    assert report["outcome"]["status"] == case["expect"]
    assert sorted({d["rule"] for d in report["diagnostics"]}) == case["rules"]
    if "at" in case:
        first = report["diagnostics"][0]
        assert {"rule": first["rule"], "line": first["line"], "col": first["col"]} == case["at"]


def test_every_rule_has_a_source_confidence_and_valid_targets():
    for rule in RULES.values():
        assert rule.targets and set(rule.targets) <= set(PROFILES), rule.id
        assert rule.confidence in ("high", "medium", "low"), rule.id
        for target in rule.targets:
            assert rule.level(target) in analyzer.SEVERITIES, rule.id
        assert analyzer.doc(rule.doc).startswith("https://"), rule.id
    covered = {r for c in CASES for r in c["rules"]} | {"DDL-009"}  # 1,025 columns: tested below
    assert covered == set(RULES)


def test_a_diagnostic_carries_everything_the_editor_and_panel_show():
    report = analyze("SELECT a,\n  b\nFROM dbo.t\nLIMIT 5", "warehouse")
    (d,) = report["diagnostics"]
    assert d["severity"] == "error" and d["rule"] == "SYN-001" and d["target"] == "warehouse"
    assert (d["line"], d["col"], d["end_line"], d["end_col"]) == (4, 1, 4, 8)
    assert (d["from"], d["to"]) == (25, 32) and d["statement"] == 1
    assert d["message"] and d["action"] and d["doc"].startswith("https://learn.microsoft.com/") and d["confidence"] == "high"
    assert report["target"] == {"id": "warehouse", "label": "Fabric Warehouse (T-SQL)", "engine": "Fabric Data Warehouse",
                                "runs_here": False}
    assert report["rule_version"] == RULE_VERSION and report["schema"] == "sql-compat-report@1"
    assert report["counts"] == {"error": 1, "warning": 0, "information": 0, "unverified": 0}


def test_the_report_has_no_score_and_never_claims_compatibility():
    for case in CASES:
        report = analyze(case["sql"], case["target"])
        text = json.dumps(report).lower()
        assert "%" not in report["outcome"]["text"] and "percent" not in text and "score\"" not in text
        assert "compatible" not in report["outcome"]["text"].lower()
    clean = analyze("SELECT TOP (5) id FROM dbo.t", "warehouse")
    assert clean["outcome"]["status"] == "no_known_problems"
    assert "isn't proof" in clean["outcome"]["text"]
    assert any("Static analysis" in n for n in clean["notes"]) and any("warning" in n for n in clean["notes"])


def test_the_sql_is_never_changed_and_the_export_says_which_sql_it_was():
    query = "SELECT TOP 5 [a] FROM t"
    report = analyze(query, "spark")
    assert "sql" not in report  # the analyzer reports; it never hands back a rewritten query
    exported = json.loads(report_json(report, query))
    assert exported["sql"] == query and exported["sql_sha256"] == hashlib.sha256(query.encode()).hexdigest()


def test_secrets_in_the_sql_never_reach_a_diagnostic_or_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    query = "CREATE LOGIN app WITH PASSWORD = 'Hunter2!Secret';\nSELECT FROM WHERE 'Hunter2!Secret'"
    report = analyze(query, "warehouse")
    assert {d["rule"] for d in report["diagnostics"]} == {"DDL-015", "SYN-007"}
    assert "Hunter2" not in json.dumps(report)
    assert "Hunter2" not in caplog.text  # sqlglot's own "unsupported syntax" warning quotes the SQL


def test_checking_runs_nothing_and_calls_nothing(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the analyzer must not connect or run anything")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    import duckdb
    monkeypatch.setattr(duckdb, "connect", refuse)
    for case in CASES[:40]:
        analyze(case["sql"], case["target"])


def test_too_much_sql_is_not_checked_and_long_lists_are_cut():
    big = analyze("SELECT 1;" * (analyzer.MAX_QUERY // 9 + 1), "spark")
    assert big["outcome"]["status"] == "not_checked" and big["statements"] == 0
    many = analyze(";\n".join("SELECT * FROM t WHERE a = NULL" for _ in range(analyzer.MAX_DIAGNOSTICS + 5)), "spark")
    assert len(many["diagnostics"]) == analyzer.MAX_DIAGNOSTICS and many["truncated"]
    assert many["counts"]["warning"] == analyzer.MAX_DIAGNOSTICS + 5


def test_a_warehouse_table_has_at_most_1024_columns():
    cols = ", ".join(f"c{i} int" for i in range(1025))
    report = analyze(f"CREATE TABLE dbo.wide ({cols})", "warehouse")
    assert [d["rule"] for d in report["diagnostics"]] == ["DDL-009"]
    assert "1,025" in report["diagnostics"][0]["message"]
    assert not analyze(f"CREATE TABLE dbo.wide ({', '.join(f'c{i} int' for i in range(1024))})", "warehouse")["diagnostics"]


def test_messages_name_the_engine_and_rules_apply_only_to_their_targets():
    for target in PROFILES:
        report = analyze("SELECT * FROM t WHERE a = NULL", target)
        assert [d["rule"] for d in report["diagnostics"] if d["severity"] != "information"] == ["SEM-001"]
    tsql = analyze("SELECT NVL(a, 0) FROM t", "sqldb")["diagnostics"][0]
    assert tsql["message"] == "Fabric SQL database doesn't have NVL." and tsql["action"].startswith("Use ISNULL")


def test_profiles_list_what_each_engine_supports():
    listed = profiles()
    assert [p["id"] for p in listed] == ["spark", "warehouse", "endpoint", "sqldb", "duckdb"]
    for p in listed:
        for key in ("label", "engine", "statements", "unsupported_types", "identifiers", "transactions", "ddl_dml",
                    "semantic_differences", "confidence", "docs", "rule_version", "checked_on"):
            assert key in p, (p["id"], key)
        assert all(d["url"].startswith("https://") for d in p["docs"])
    warehouse = next(p for p in listed if p["id"] == "warehouse")
    assert warehouse["unsupported_types"]["money, smallmoney"].startswith("decimal")
    assert next(p for p in listed if p["id"] == "endpoint")["statements"]["INSERT, UPDATE, DELETE, MERGE, TRUNCATE"].startswith("not supported")


def test_the_endpoint_is_checked_but_never_run_here():
    with pytest.raises(ValueError, match="can't run on this PC"):
        sql.parse("SELECT 1", "endpoint")


def test_the_page_can_check_list_and_export(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("time.strftime", lambda *_a, **_k: "2026-10-17 120000")
    service = server.Service(client_id="x", root=tmp_path)
    reply = server.handle(service, json.dumps({"id": 1, "method": "sql_analyze",
                                               "params": {"query": "SELECT * FROM t LIMIT 1", "target": "warehouse"}}))
    assert reply["result"]["outcome"]["status"] == "known_problems"
    listed = server.handle(service, '{"id": 2, "method": "sql_compat_profiles"}')["result"]
    assert listed["rule_version"] == RULE_VERSION and len(listed["rules"]) == len(RULES)
    params = {"query": "SELECT 1 AS x", "target": "spark", "name": "Sales/../q"}
    first = server.handle(service, json.dumps({"id": 3, "method": "sql_compat_export", "params": params}))["result"]
    second = server.handle(service, json.dumps({"id": 4, "method": "sql_compat_export", "params": params}))["result"]
    assert Path(first["path"]).name == "Salesq compatibility spark 2026-10-17 120000.json"
    assert Path(second["path"]).name == "Salesq compatibility spark 2026-10-17 120000 (2).json"  # never overwritten
    assert Path(first["path"]).parent == tmp_path / "Downloads"
    saved = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
    assert saved["sql"] == "SELECT 1 AS x" and saved["outcome"]["status"] == "no_known_problems"
    bad = server.handle(service, '{"id": 5, "method": "sql_analyze", "params": {"query": "x", "target": "oracle"}}')
    assert "Unknown target" in bad["error"]


def test_checking_isnt_audited_and_an_export_is_audited_without_the_sql():
    from fabric_desktop import audit
    assert not audit.audited("sql_analyze") and not audit.audited("sql_compat_profiles")
    assert audit.audited("sql_compat_export")
    assert audit.target({"query": "SELECT secret FROM t", "target": "spark", "name": "Q"}) == {
        "query": "(given)", "target": "spark", "name": "Q"}
