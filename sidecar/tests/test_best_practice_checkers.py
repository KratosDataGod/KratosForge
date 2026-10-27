"""The notebook and SQL checkers, as governance's Check now runs them."""

import json

import pytest

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
    # No SQL scripts on this PC, so the SQL policy isn't shown as passing.
    assert got["schemas"]["status"] == "not_checked" and got["schemas"]["note"] == "Check now found nothing for its sql rules to check."
    # A policy whose parameters turn its rules off isn't shown as passing either.
    assert got["off"]["status"] == "not_checked" and "enabled = false" in got["off"]["note"]


def test_practice_settings_come_from_the_policies_in_force(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    assert svc.governance_practice_settings("notebook") == {"settings": {}, "policies": [], "note": ""}
    for pid, rule, sev, params in (("strict", "NB003, SQ005", "high", {}), ("lax", "NB003", "low", {}),
                                   ("off", "NB009", "medium", {"enabled": {"value": False}})):
        svc.governance_create({"id": pid, "name": pid, "category": "quality", "rule": rule, "severity": sev, "params": params})
        svc.governance_publish(pid)
        svc.governance_assign(pid, "enterprise")
    got = svc.governance_practice_settings("notebook")
    # Two policies name NB003: the worse severity. A policy that turns a rule off turns it off.
    assert got["settings"] == {"NB003": {"enabled": True, "severity": "Error"}, "NB009": {"enabled": False, "severity": "Warning"}}
    assert sorted(got["policies"]) == ["lax", "off", "strict"]
    assert svc.governance_practice_settings("sql")["settings"] == {"SQ005": {"enabled": True, "severity": "Error"}}
    with pytest.raises(ValueError):
        svc.governance_practice_settings("model")


def test_one_policy_turning_a_rule_off_doesnt_beat_one_turning_it_on(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    for pid, params in (("off", {"enabled": {"value": False}}), ("on", {})):
        svc.governance_create({"id": pid, "name": pid, "category": "quality", "rule": "SQ002", "severity": "low", "params": params})
        svc.governance_publish(pid)
        svc.governance_assign(pid, "enterprise")
    assert svc.governance_practice_settings("sql")["settings"] == {"SQ002": {"enabled": True, "severity": "Info"}}


def test_scheduled_only_rules_check_only_scheduled_notebooks():
    nb = {"kind": "notebook", "id": "n", "sources": ["%pip install x==1"]}
    settings = gc.settings_for(gc.find_rule("NB008")[1], "", {})
    run = gc.run_rule("NB008", [nb], settings)
    assert not run.get("not_run") and run["checked"] == 0, "an unscheduled notebook isn't counted as passing"
    run = gc.run_rule("NB008", [nb, dict(nb, id="s", scheduled=True)], settings)
    assert run["checked"] == 1 and [f["object"] for f in run["findings"]] == ["s: cell 1, line 1"]


def test_check_now_checks_saved_queries_and_warehouse_project_files(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    svc.governance_create({"id": "schemas", "name": "schemas", "category": "quality", "rule": "SQ005", "params": {}})
    svc.governance_publish("schemas")
    svc.governance_assign("schemas", "enterprise")
    svc.sql_query_save("mine", "Revenue", "SELECT a FROM sales")
    repo = tmp_path / "sales-platform"
    view = repo / "WH_Gold.Warehouse" / "dbo" / "Views"
    view.mkdir(parents=True)
    (view / "vw_Ok.sql").write_text("CREATE VIEW dbo.vw_Ok AS SELECT a FROM dbo.sales;\n", encoding="utf-8")
    (view / "vw_Bad.sql").write_bytes(b"\xff\xfe not utf-8")
    svc.git_repos_file.write_text(json.dumps({"repos": [{"path": str(repo), "name": "sales-platform"}]}), encoding="utf-8")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.name != "governance_checks.json")
    out = svc.governance_check_now()
    [pol] = out["policies"]
    assert pol["status"] == "findings" and pol["checked"] == 2 and pol["passed"] == 1
    assert [f["object"] for f in pol["findings"]] == ["My queries/Revenue.sql: statement 1, line 1"]
    assert any("WH_Gold/dbo/Views/vw_Bad.sql" in p for p in out["problems"]), "an unreadable script is said, not skipped"
    # Check now only reads the scripts; it writes nothing but its last result.
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.name != "governance_checks.json") == before


def test_check_now_runs_scheduled_only_rules_on_notebooks_a_schedule_runs(tmp_path):
    from fabric_desktop.service import Service

    svc = Service(root=tmp_path / "app")
    svc.meta_init(str(tmp_path / "crm"), "3")
    svc.governance_create({"id": "pip", "name": "pip", "category": "quality", "rule": "NB008", "params": {}})
    svc.governance_publish("pip")
    svc.governance_assign("pip", "enterprise")
    for name in ("nb_load", "nb_adhoc", "nb_paused"):
        svc.notebooks.save(name, [{"type": "code", "source": "%pip install x==1"}], None)
    [pol] = svc.governance_check_now()["policies"]
    assert pol["status"] == "not_checked" and pol["note"] == "Check now found nothing for its scheduled notebook rules to check."
    (tmp_path / "app" / "schedules.json").write_text(json.dumps({"engine": "", "schedules": [
        {"id": "a", "target": {"kind": "notebook", "name": "nb_load"}, "config": {}, "enabled": True},
        {"id": "b", "target": {"kind": "notebook", "name": "nb_paused"}, "config": {}, "enabled": False},
        {"id": "c", "target": {"kind": "meta", "env": "dev"}, "config": {}}]}), encoding="utf-8")
    [pol] = svc.governance_check_now()["policies"]
    assert pol["status"] == "findings" and pol["checked"] == 1
    assert [f["object"] for f in pol["findings"]] == ["nb_load: cell 1, line 1"]
    # A torn schedules file is said and left where it is; the other notebook rules still run.
    (tmp_path / "app" / "schedules.json").write_text("{", encoding="utf-8")
    out = svc.governance_check_now()
    assert out["policies"][0]["status"] == "not_checked" and (tmp_path / "app" / "schedules.json").is_file()
    assert any("schedules couldn't be read" in p for p in out["problems"])
