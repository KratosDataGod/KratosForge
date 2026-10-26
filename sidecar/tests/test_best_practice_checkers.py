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


def test_check_now_runs_notebook_rules_and_says_when_nothing_is_there_to_check(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    for pid, rule, params in (("collect", "NB003", {}), ("schemas", "SQ005", {}),
                              ("off", "NB003", {"enabled": {"value": False}})):
        svc.governance_create({"id": pid, "name": pid, "category": "quality", "rule": rule, "params": params})
        svc.governance_publish(pid)
        svc.governance_assign(pid, "enterprise")
    svc.notebooks.save("nb_sales", [{"type": "code", "source": "rows = df.collect()"},
                                    {"type": "markdown", "source": "df.collect()"}], None)
    got = {p["id"]: p for p in svc.governance_check_now()["policies"]}
    assert got["collect"]["status"] == "findings" and got["collect"]["checked"] == 1
    assert [f["object"] for f in got["collect"]["findings"]] == ["nb_sales: cell 1, line 1"]
    # Check now has no SQL scripts to give the SQL rules, so the policy isn't shown as passing.
    assert got["schemas"]["status"] == "not_checked" and got["schemas"]["note"] == "Check now found nothing for its sql rules to check."
    # A policy whose parameters turn its rules off isn't shown as passing either.
    assert got["off"]["status"] == "not_checked" and "enabled = false" in got["off"]["note"]
