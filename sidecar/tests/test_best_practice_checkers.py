"""The notebook and SQL checkers, as governance's Check now runs them."""

from fabric_desktop import best_practice_checkers  # noqa: F401 - registers the checkers
from fabric_desktop import governance_checks as gc


def test_registered_with_their_rules():
    rules = {r["id"]: r for r in gc.all_rules()}
    assert rules["NB003"]["checker"] == "notebook" and rules["NB003"]["runs_in"] == "local"
    assert rules["SQ005"]["checker"] == "sql" and rules["NB004"]["scheduled_only"] is True


def test_a_policy_reports_only_the_rules_it_names():
    nb = {"kind": "notebook", "id": "nb_sales", "sources": ["rows = df.collect()", "%pip install pandas"]}
    rule = gc.find_rule("NB003")[1]
    run = gc.run_rule("NB003", [nb, {"kind": "entity", "id": "e"}], gc.settings_for(rule, "high", {}))
    assert run["checked"] == 1 and run["passed"] == 0
    assert [(f["rule_id"], f["severity"], f["object"]) for f in run["findings"]] == [
        ("NB003", "Error", "nb_sales: cell 1, line 1")]  # NB009 isn't named, so it isn't reported
    clean = gc.run_rule("NB003", [dict(nb, sources=["rows = df.limit(5).collect()"])], gc.settings_for(rule, "", {}))
    assert clean["checked"] == 1 and clean["passed"] == 1 and clean["findings"] == []


def test_sql_items_and_settings_from_policy_params():
    item = {"kind": "sql", "id": "load.sql", "query": "SELECT a FROM sales", "target": "warehouse"}
    rule = gc.find_rule("SQ005")[1]
    run = gc.run_rule("SQ005", [item], gc.settings_for(rule, "", {"severity": "Warning"}))
    assert [(f["severity"], f["object"]) for f in run["findings"]] == [("Warning", "load.sql: statement 1, line 1")]
    off = gc.run_rule("SQ005", [item], gc.settings_for(rule, "", {"enabled": False}))
    assert off.get("disabled") is True


def test_scheduled_only_rules_arent_run_by_check_now():
    run = gc.run_rule("NB008", [{"kind": "notebook", "id": "n", "sources": ["%pip install x==1"]}],
                      gc.settings_for(gc.find_rule("NB008")[1], "", {}))
    assert run.get("not_run") is True
