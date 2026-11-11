"""Try the fix: the shapes a fix is never offered for, and the Compare pair's edges. Text only."""

from __future__ import annotations

import pytest

from fabric_desktop.learn import fixes

from test_learn_fixes import CATALOG, _fixes, _texts


def test_only_sql_text_is_read():
    with pytest.raises(TypeError, match="query must be SQL text"):
        fixes.propose(None, "warehouse", [], CATALOG)


def test_findings_that_point_nowhere_or_at_no_token_are_skipped():
    sql = "SELECT 1 FROM sales"
    findings = [{"rule_id": "SQ005", "line": 9, "col": 1}, {"rule_id": "SQ005", "line": 1, "col": "x"},
                {"rule_id": "SQ005", "line": 1, "col": 2}, "not a finding", {"rule_id": "XX1", "line": 1, "col": 1}]
    assert fixes.propose(sql, "warehouse", findings, CATALOG)["fixes"] == []
    assert fixes.propose(sql, "warehouse", "not a list", None) == {"fixes": [], "needs": []}


def test_sq005_is_only_offered_on_a_name():
    sql = "SELECT 1 FROM sales"
    assert fixes.propose(sql, "warehouse", [{"rule_id": "SQ005", "line": 1, "col": 1}], CATALOG)["fixes"] == []


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM dbo.sales WHERE YEAR(order_date + 1) = 2026",       # not just a column inside YEAR()
    "SELECT 1 FROM db.dbo.sales WHERE YEAR(order_date) = 2026",        # a three-part name
])
def test_year_is_left_alone_when_it_isnt_a_plain_column_of_a_known_table(sql):
    assert _texts(sql) == []


def test_an_alias_after_as_still_resolves_the_table():
    assert _texts("SELECT 1 FROM dbo.sales AS s WHERE YEAR(s.order_date) = 2026") == [
        "(s.order_date >= '2026-01-01' AND s.order_date < '2027-01-01')"]


def test_compare_reads_a_view_query_and_stops_at_the_semicolon():
    sql = "CREATE VIEW v AS SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026;"
    [fix] = [f for f in _fixes(sql)["fixes"] if f["rule_id"] == "SQ002"]
    assert fix["compare"]["before"] == "SELECT 1 FROM dbo.sales WHERE YEAR(order_date) = 2026"
    assert fix["compare"]["after"].endswith("order_date < '2027-01-01')")


def test_a_view_whose_body_isnt_a_query_has_no_compare():
    from fabric_desktop.sqlcompat.analyzer import split
    from fabric_desktop.sqlcompat.profiles import profile

    [stmt], _ = split("CREATE VIEW v", profile("warehouse")["id"])
    assert fixes._reads("CREATE VIEW v", stmt, {"start": 0, "end": 1}) is None


def test_a_fix_outside_the_read_has_no_compare_and_an_empty_statement_has_none():
    from fabric_desktop.sqlcompat.analyzer import split
    from fabric_desktop.sqlcompat.profiles import profile

    sql = "SELECT 1 FROM dbo.sales"
    [stmt], _ = split(sql, profile("warehouse")["id"])
    assert fixes._reads(sql, stmt, {"start": 100, "end": 101}) is None

    class Empty:
        toks: list = []

    assert fixes._reads("", Empty(), {"start": 0, "end": 0}) is None
