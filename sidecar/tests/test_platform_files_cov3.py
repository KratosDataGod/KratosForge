"""Platform repository files (git/platform_files.py): unreadable policies and templates, compare's gaps,
and the deploy plan's remaining branches. Pure functions on in-memory records."""

from __future__ import annotations

from fabric_desktop.git import platform_files as pf

DEF = {"name": "Table names", "category": "naming"}


def deployed(**raw):
    return pf.from_platform({"key_tables": {"prod": []}} | raw)


def plan(git, dep, remove=(), envs=None):
    return pf.plan({"problems": []} | git, envs or {"prod": dep}, "prod", set(remove), "note")


def raw_policy(pid, *, status="published", definition=DEF, assign=(), **extra):
    return {"id": pid, "status": status, "current": {"definition": definition}, "assign": list(assign)} | extra


def why(out):
    return {b["id"]: b["why"] for b in out["blocked"]}


# --------------------------------------------------------------------------- from_platform


def test_policies_and_templates_the_app_cant_read_are_skipped_with_why():
    out = pf.from_platform({"policies": [raw_policy("bad", definition={"category": "naming"}), raw_policy("ok")],
                            "policy_templates": [{"id": "t-bad", "definition": {"name": "T"}},
                                                 {"id": "t-ok", "definition": DEF}]})
    assert list(out["policies"]) == ["ok"] and list(out["policy_templates"]) == ["t-ok"]
    skipped = {s["id"]: s["why"] for s in out["skipped"]}
    assert skipped["bad"] == "can't be read: A policy needs a name"
    assert skipped["t-bad"].startswith("can't be read: ")
    assert skipped.keys() == {"bad", "t-bad"}


# --------------------------------------------------------------------------- compare


def test_changes_of_values_that_arent_tables_compare_whole():
    assert pf._changes([1, 2], [1, 3]) == [{"field": "", "git": [1, 2], "deployed": [1, 3]}]
    assert pf._changes("x", "x") == []


def test_compare_leaves_out_kinds_git_doesnt_hold_and_treats_missing_deployed_kinds_as_empty():
    rec = pf.policy_record(raw_policy("p1"))
    items = pf.compare({"admins": ["ana@contoso.com"], "policies": {"p1": rec}},
                       {"admins": None, "policies": None, "domains": {"d": {}}})
    assert items == [
        {"kind": "admins", "id": "admins.toml", "state": "changed",
         "changes": [{"field": "admins", "git": ["ana@contoso.com"], "deployed": []}]},
        {"kind": "policies", "id": "p1", "state": "only_git", "changes": []},
    ]


# --------------------------------------------------------------------------- plan: domains


def test_a_fabric_domain_keeps_fabrics_name_and_parent_when_its_own_fields_change():
    dep = deployed(domains=[{"id": "fab", "name": "Fabric Name", "parent_id": "top", "fabric_domain_id": "f-1",
                             "description": "old"}, {"id": "top", "name": "Top"}])
    git = {"domains": {"fab": pf.domain_record({"id": "fab", "fabric_domain_id": "f-1", "description": "new"}),
                       "top": pf.domain_record({"id": "top", "name": "Top"})}}
    out = plan(git, dep)
    assert out["blocked"] == []
    assert [s["args"] for s in out["steps"]] == [{"domain": {
        "id": "fab", "name": "Fabric Name", "parent_id": "top", "description": "new", "owners": "",
        "fabric_domain": "f-1"}}]
    assert out["steps"][0]["action"] == "update"


def test_a_domain_linked_to_another_fabric_domain_is_blocked():
    dep = deployed(domains=[{"id": "fab", "name": "F", "fabric_domain_id": "f-1"}])
    out = plan({"domains": {"fab": pf.domain_record({"id": "fab", "fabric_domain_id": "f-2"})}}, dep)
    assert out["steps"] == [] and why(out) == {"fab": "is linked to a different Fabric domain in the platform."}


# --------------------------------------------------------------------------- plan: zones and sources


def test_a_new_zone_in_a_domain_nobody_has_is_blocked():
    dep = deployed(landing_zones=[{"id": "shared", "name": "Shared"}])
    git = {"landing_zones": {"shared": pf.zone_record({"id": "shared", "name": "Shared"}),
                             "hr": pf.zone_record({"id": "hr", "name": "HR", "domain": "people"})}}
    out = plan(git, dep)
    assert out["steps"] == [] and why(out) == {"hr": "is in domain people, which isn't in Git or the platform."}


def test_a_source_id_that_isnt_a_whole_number_above_zero_stops_the_deploy():
    git = {"sources": {"crm": pf.source_record({"code": "CRM", "source_id": "first"}),
                       "erp": pf.source_record({"code": "ERP", "source_id": 0})}}
    out = plan(git, deployed())
    assert out["stops"] == ["sources/crm.toml: source_id is a whole number above 0.",
                            "sources/erp.toml: source_id is a whole number above 0."]
    assert out["steps"] == []


def test_a_ticked_source_nobody_reads_is_removed_and_an_unticked_one_is_only_offered():
    dep = deployed(sources=[{"name": "sap", "code": "SAP", "source_id": 1},
                            {"name": "old", "code": "OLD", "source_id": 2}])
    out = plan({"sources": {}}, dep, remove={("sources", "sap")})
    assert [(s["op"], s["args"]) for s in out["steps"]] == [("delete_source", {"name": "sap"})]
    assert {r["id"] for r in out["removable"]} == {"sap", "old"}


# --------------------------------------------------------------------------- plan: keys, glossary, rules


def test_key_tables_that_differ_or_are_only_deployed_are_left_with_a_note():
    dep = pf.from_platform({"key_tables": {"keys": [{"name": "customer", "business_key": "id"},
                                                    {"name": "legacy", "business_key": "code"}]}})
    git = {"key_tables": {"customer": pf.key_table_record({"name": "customer", "business_key": "customer_id"})}}
    out = plan(git, deployed(), envs={"prod": deployed(), "keys": dep})
    assert out["steps"] == []
    assert out["notes"] == [
        ("Key table customer in the shared keys database: its fields differ from Git. A deploy doesn't change an "
         "existing key table."),
        "Key table legacy is in the shared keys database but not in Git. A deploy never removes a key table: its keys stay."]


def test_a_glossary_term_only_deployed_stays_and_changed_shortcut_rules_are_saved():
    dep = deployed(glossary=[{"term": "Churn", "definition": "Gone"}], shortcut_rules={"allow": ["a"]})
    out = plan({"glossary": {}, "shortcut_rules": {"allow": ["a", "b"]}}, dep)
    assert out["notes"] == ["Glossary term 'Churn' isn't in Git. The platform can't remove glossary terms yet, so it stays."]
    assert [(s["op"], s["args"]) for s in out["steps"]] == [("save_shortcut_rules", {"rules": {"allow": ["a", "b"]}})]
    assert plan({"shortcut_rules": {"allow": ["a"]}}, dep)["steps"] == []


def test_a_changed_template_is_saved_under_its_id():
    dep = deployed(policy_templates=[{"id": "ours", "definition": DEF}])
    rec = pf.template_record({"id": "ours", "definition": DEF | {"description": "Now described"}})
    out = plan({"policy_templates": {"ours": rec}}, dep)
    (step,) = out["steps"]
    assert (step["op"], step["action"], step["args"]["id"]) == ("save_policy_template", "update", "ours")
    assert step["args"]["template"]["description"] == "Now described"
    assert plan({"policy_templates": {"ours": pf.template_record({"id": "ours", "definition": DEF})}}, dep)["steps"] == []


# --------------------------------------------------------------------------- plan: policies


def test_a_policy_file_with_a_draft_status_is_blocked():
    rec = pf.policy_record(raw_policy("p1", status="draft"))
    out = plan({"policies": {"p1": rec}}, deployed(policies=[]))
    assert out["steps"] == [] and why(out) == {"p1": "status is published, deprecated or retired."}


def test_a_policy_only_drafted_in_the_platform_is_blocked():
    dep = deployed(policies=[{"id": "p1", "status": "draft", "current": None}])
    out = plan({"policies": {"p1": pf.policy_record(raw_policy("p1"))}}, dep)
    assert out["steps"] == [] and why(out)["p1"].startswith("is only a draft in the platform.")


def test_a_retired_policy_cant_change_but_an_identical_one_is_left_alone():
    dep = deployed(policies=[raw_policy("p1", status="retired")])
    same = pf.policy_record(raw_policy("p1", status="retired"))
    out = plan({"policies": {"p1": same}}, dep)
    assert out["steps"] == [] and out["blocked"] == []
    changed = pf.policy_record(raw_policy("p1", status="retired", definition=DEF | {"description": "new"}))
    out = plan({"policies": {"p1": changed}}, dep)
    assert out["steps"] == [] and why(out) == {"p1": "is retired in the platform, so it can't change."}


def test_assignments_git_drops_are_removed_and_a_new_status_is_set():
    a_old = {"scope": "domain", "target": "sales", "settings": {}}
    a_keep = {"scope": "enterprise", "settings": {}}
    dep = deployed(policies=[raw_policy("p1", assign=[a_old, a_keep])])
    rec = pf.policy_record(raw_policy("p1", status="deprecated", assign=[a_keep]))
    out = plan({"policies": {"p1": rec}}, dep)
    assert [(s["op"], s["what"]) for s in out["steps"]] == [
        ("remove_assignment", "unassign domain sales"), ("set_policy_status", "mark it deprecated")]
    assert out["steps"][0]["args"] == {"policy": "p1", "scope": "domain", "target": "sales"}
    assert out["steps"][1]["args"] == {"id": "p1", "status": "deprecated", "note": "note"}
