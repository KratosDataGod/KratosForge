"""The adapters that run notebook, SQL and model checks for governance (best_practice_checkers.py): which
items and settings they answer for, and how a finding is named."""

from __future__ import annotations

from fabric_desktop import best_practice_checkers as bpc
from fabric_desktop import governance_checks as gc


def checker(found=()):
    ran = []

    def run(item, settings):
        ran.append(settings)
        return [dict(f) for f in found]

    return bpc._adapter("notebook", lambda: ["NB001", "NB002"], run), ran


def test_an_item_of_another_kind_or_with_none_of_the_rules_is_not_checked():
    check, ran = checker()
    assert check({"kind": "sql", "id": "q"}, {"NB001": {}}) is None
    assert check({"kind": "notebook", "id": "nb"}, {"SQ005": {}}) is None
    assert ran == []


def test_rules_the_policy_turned_off_are_not_run():
    check, ran = checker()
    assert check({"kind": "notebook", "id": "nb"}, {"NB001": {"enabled": False}}) is None
    assert ran == []


def test_settings_other_than_the_checkers_rules_or_not_objects_are_left_out():
    assert bpc._only(["NB001", "NB002"], {"NB001": "on", "SQ005": {"enabled": True}, "NB002": {"severity": "Warning"}}) \
        == {"NB001": {"enabled": False}, "NB002": {"enabled": True, "severity": "Warning"}}


def test_a_finding_is_named_by_its_item_and_points_at_the_docs_when_it_has_no_page():
    check, ran = checker([{"object": "cell 2, line 1", "url": ""}, {"object": "cell 3", "url": "https://learn/x"}])
    found = check({"kind": "notebook", "id": "nb_sales"}, {"NB002": {"severity": "Error"}})
    assert found == [{"object": "nb_sales: cell 2, line 1", "url": gc.DOCS},
                     {"object": "nb_sales: cell 3", "url": "https://learn/x"}]
    assert ran == [{"NB001": {"enabled": False}, "NB002": {"enabled": True, "severity": "Error"}}]


def test_a_sql_item_with_an_unknown_target_is_checked_as_a_warehouse_script():
    settings = {"SQ005": {"enabled": True, "severity": "Warning"}}
    as_warehouse = bpc._sql({"query": "SELECT a FROM sales", "target": "warehouse"}, settings)
    assert bpc._sql({"query": "SELECT a FROM sales", "target": "mainframe"}, settings) == as_warehouse
    assert [f["rule_id"] for f in as_warehouse] == ["SQ005"]
    assert bpc._sql({}, settings) == []
