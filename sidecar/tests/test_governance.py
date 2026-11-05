"""GOV-001: the governance policy registry on the platform (central/policy.py), on the SQLite platform."""

import json
import random
import sqlite3
import sys
import types
from contextlib import closing

import pytest

from fabric_desktop.central import core, policy, udf
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.central.governance_service import local_assignments
from fabric_desktop.service import Service

ADMIN, OWNER, DEV = "ana@contoso.com", "omar@contoso.com", "dev@contoso.com"

NAMING = {"id": "table-names", "name": "Table names are snake_case", "category": "naming", "owner": "Data office",
          "description": "Lowercase letters, digits and underscores.", "severity": "medium", "enforcement": "warn",
          "remediation": "Rename the table, e.g. CustomerOrders to customer_orders.", "rule": "table_name_pattern",
          "params": {"max_length": {"value": 60, "stricter": "lower"},
                     "pattern": {"value": "^[a-z][a-z0-9_]*$", "stricter": "fixed"}}}
OWNERSHIP = {"id": "owner-required", "name": "Every table has an owner", "category": "ownership", "severity": "high",
             "enforcement": "block", "mandatory": True, "rule": "entity_owner",
             "params": {"approvers": {"value": ["steward"], "stricter": "superset"}}}


@pytest.fixture
def who():
    return {"user": ADMIN}


@pytest.fixture
def platform(tmp_path, who):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: who["user"])
    p.call("save_domain", domain={"name": "Sales", "owners": OWNER})
    p.call("save_domain", domain={"name": "EMEA", "parent_id": "sales"})
    p.call("save_domain", domain={"name": "Finance"})
    p.call("register_project", project={"name": "crm", "domain": "sales", "subdomain": "emea"})
    p.call("register_project", project={"name": "orders", "domain": "sales"})
    p.call("register_project", project={"name": "ledger", "domain": "finance"})
    return p


def published(platform, definition):
    platform.call("create_policy", policy=definition)
    return platform.call("publish_policy", id=definition["id"], note="first version")


# --------------------------------------------------------------------------- creation and publication


def test_an_admin_creates_and_publishes_a_versioned_policy(platform):
    draft = platform.call("create_policy", policy=NAMING)
    assert draft["status"] == "draft" and draft["draft_version"] == 1 and draft["current_version"] is None
    assert draft["draft"]["definition"]["params"]["max_length"] == {"value": 60, "stricter": "lower", "description": ""}
    out = platform.call("publish_policy", id="table-names", note="first version")
    assert out["status"] == "published" and out["current_version"] == 1 and out["draft"] is None
    v1 = out["current"]
    assert v1["published_by"] == ADMIN and v1["published_at"] and v1["intact"]
    assert v1["hash"] == policy.definition_hash(v1["definition"])
    history = [(e["action"], e["version"], e["by_user"]) for e in out["history"]]
    assert history == [("published", "1", ADMIN), ("created", "1", ADMIN)]
    assert all(e["at"] for e in out["history"])
    assert any(a["action"] == "policy published" for a in platform.call("audit"))


def test_policy_definitions_are_checked(platform):
    with pytest.raises(core.PlatformError, match="category must be one of"):
        platform.call("create_policy", policy={"name": "x", "category": "vibes"})
    with pytest.raises(core.PlatformError, match="expire after"):
        platform.call("create_policy", policy={"name": "x", "category": "naming", "effective_from": "2027-01-01",
                                               "expires_on": "2026-01-01"})
    with pytest.raises(core.PlatformError, match="is a number"):
        platform.call("create_policy", policy={"name": "x", "category": "naming",
                                               "params": {"n": {"value": "ten", "stricter": "higher"}}})
    with pytest.raises(core.PlatformError, match="enforcement must be"):
        platform.call("create_policy", policy={"name": "x", "category": "naming", "enforcement": "off"})
    platform.call("create_policy", policy=NAMING)
    with pytest.raises(core.PlatformError, match="already a policy"):
        platform.call("create_policy", policy=NAMING)


def test_every_category_from_the_spec_is_offered(platform):
    assert set(platform.call("policy_options")["categories"]) == {
        "naming", "schema", "quality", "classification", "ownership", "contract", "deployment"}


def test_policies_can_be_searched_and_filtered(platform):
    published(platform, NAMING)
    platform.call("create_policy", policy=OWNERSHIP)
    assert [p["id"] for p in platform.call("list_policies", query="SNAKE")] == ["table-names"]
    assert [p["id"] for p in platform.call("list_policies", category="ownership")] == ["owner-required"]
    assert [p["id"] for p in platform.call("list_policies", status="draft")] == ["owner-required"]


# --------------------------------------------------------------------------- versions never change


def test_published_versions_are_immutable(platform):
    published(platform, NAMING)
    v1 = platform.call("policy_version", id="table-names", version=1)
    changed = dict(NAMING, severity="high")
    out = platform.call("save_policy_draft", id="table-names", policy=changed, note="stricter")
    assert out["draft_version"] == 2 and out["current_version"] == 1
    assert platform.call("policy_version", id="table-names", version=1) == v1
    platform.call("publish_policy", id="table-names")
    with pytest.raises(core.PlatformError, match="no draft to publish"):
        platform.call("publish_policy", id="table-names")
    out = platform.call("get_policy", id="table-names")
    assert [v["version"] for v in out["versions"]] == [1, 2]
    assert out["versions"][0]["definition"]["severity"] == "medium"
    assert out["versions"][1]["definition"]["severity"] == "high" and out["current_version"] == 2


def test_a_published_version_changed_behind_the_platforms_back_shows_as_not_intact(platform, tmp_path):
    published(platform, NAMING)
    conn = sqlite3.connect(tmp_path / "platform.sqlite")
    conn.execute('UPDATE "catalog__policy_version" SET definition = replace(definition, \'"warn"\', \'"advisory"\')')
    conn.commit()
    conn.close()
    assert platform.call("policy_version", id="table-names", version=1)["intact"] is False
    assert platform.call("resolve_policies", project="crm")["policies"] == []  # not assigned yet
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    assert platform.call("resolve_policies", project="crm")["policies"][0]["intact"] is False


def test_drafts_can_be_discarded_and_never_published_policies_go_with_them(platform):
    platform.call("create_policy", policy=OWNERSHIP)
    assert platform.call("discard_policy_draft", id="owner-required") == {"id": "owner-required", "deleted": True}
    published(platform, NAMING)
    platform.call("save_policy_draft", id="table-names", policy=NAMING)
    out = platform.call("discard_policy_draft", id="table-names")
    assert out["draft"] is None and out["current_version"] == 1 and len(out["versions"]) == 1


def test_deprecated_policies_still_apply_and_retired_ones_stop(platform):
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    platform.call("set_policy_status", id="table-names", status="deprecated", note="replaced by naming v2")
    got = platform.call("resolve_policies", project="crm")
    assert got["policies"][0]["deprecated"] is True
    with pytest.raises(core.PlatformError, match="Say why"):
        platform.call("set_policy_status", id="table-names", status="retired")
    platform.call("set_policy_status", id="table-names", status="retired", note="no longer needed")
    got = platform.call("resolve_policies", project="crm")
    assert got["policies"] == [] and "was retired" in got["inactive"][0]["reason"]
    with pytest.raises(core.PlatformError, match="retired"):
        platform.call("save_policy_draft", id="table-names", policy=NAMING)
    with pytest.raises(core.PlatformError, match="retired"):
        platform.call("set_policy_status", id="table-names", status="published")
    out = platform.call("get_policy", id="table-names")
    assert out["current"]["definition"]["name"] == NAMING["name"]  # still readable
    assert [e["action"] for e in out["history"]][:2] == ["retired", "deprecated"]


def test_dates_decide_when_a_policy_is_in_force(platform):
    published(platform, dict(NAMING, effective_from="2027-01-01", expires_on="2028-01-01"))
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    early = platform.call("resolve_policies", project="crm", today="2026-12-31")
    assert early["policies"] == [] and "takes effect on 2027-01-01" in early["inactive"][0]["reason"]
    assert platform.call("resolve_policies", project="crm", today="2027-06-01")["policies"]
    late = platform.call("resolve_policies", project="crm", today="2028-01-01")
    assert "expired on 2028-01-01" in late["inactive"][0]["reason"]


# --------------------------------------------------------------------------- inheritance and precedence


def test_enterprise_policies_reach_every_project_and_lower_scopes_tighten(platform):
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    platform.call("save_assignment", policy="table-names", scope="domain", target="sales",
                  settings={"enforcement": "block", "params": {"max_length": 40}})
    crm = platform.call("resolve_policies", project="crm")["policies"][0]
    assert (crm["enforcement"], crm["params"]["max_length"], crm["introduced_at"]) == ("block", 40, "enterprise")
    assert [(s["scope"], s["change"]) for s in crm["steps"]] == [
        ("enterprise", "introduced"), ("domain", "enforcement"), ("domain", "max_length")]
    ledger = platform.call("resolve_policies", project="ledger")["policies"][0]
    assert (ledger["enforcement"], ledger["params"]["max_length"]) == ("warn", 60)


def test_multiple_projects_reference_one_published_policy(platform):
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="domain", target="sales")
    reach = platform.call("policy_applicability", id="table-names")
    assert [(p["project"], p["version"]) for p in reach["projects"]] == [("crm", 1), ("orders", 1)]
    assert reach["projects"][0]["via"] == ["domain sales"]


def test_lower_scopes_may_introduce_new_policies(platform):
    published(platform, OWNERSHIP)
    local = [{"policy": "owner-required", "scope": "entity", "target": "crm:silver.customer", "settings": {}}]
    entity = platform.call("resolve_policies", project="crm", entity="silver.customer", local=local)
    assert entity["policies"][0]["introduced_at"] == "entity crm:silver.customer"
    assert platform.call("resolve_policies", project="crm", local=local)["policies"] == []


def test_mandatory_enterprise_policies_cannot_be_weakened(platform, who):
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="enterprise",
                  settings={"mandatory": True, "enforcement": "block"})
    # Saved to the platform: refused.
    who["user"] = OWNER
    with pytest.raises(core.PlatformError, match="mandatory from enterprise"):
        platform.call("save_assignment", policy="table-names", scope="domain", target="sales",
                      settings={"enforcement": "advisory"})
    with pytest.raises(core.PlatformError, match="only stricter"):
        platform.call("save_assignment", policy="table-names", scope="domain", target="sales",
                      settings={"params": {"max_length": 80}})
    with pytest.raises(core.PlatformError, match="mandatory"):
        platform.call("save_assignment", policy="table-names", scope="domain", target="emea",
                      settings={"mandatory": False})
    # Tightening is fine.
    platform.call("save_assignment", policy="table-names", scope="domain", target="sales",
                  settings={"params": {"max_length": 30}, "severity": "high"})
    # From a project's own file: ignored, and the conflict is reported.
    local = [{"policy": "table-names", "scope": "project", "target": "crm", "origin": "governance.toml",
              "settings": {"enforcement": "off", "params": {"max_length": 50, "pattern": ".*"}, "severity": "critical"}}]
    got = platform.call("resolve_policies", project="crm", local=local)
    p = got["policies"][0]
    assert (p["enforcement"], p["params"]["max_length"], p["params"]["pattern"]) == ("block", 30, "^[a-z][a-z0-9_]*$")
    assert p["severity"] == "critical"  # stricter: accepted
    assert sorted(c["message"].split(": ")[1].split(" can't")[0] for c in got["conflicts"]) == [
        "enforcement", "max_length", "pattern"]
    assert all(c["origin"] == "governance.toml" for c in got["conflicts"])
    ignored = [s for s in p["steps"] if s["outcome"] == "ignored"]
    assert len(ignored) == 3 and all("mandatory from enterprise:" in s["reason"] for s in ignored)


def test_non_mandatory_policies_can_be_relaxed_or_switched_off_below(platform):
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    local = [{"policy": "table-names", "scope": "project", "target": "crm", "settings": {"enforcement": "off"}}]
    got = platform.call("resolve_policies", project="crm", local=local)
    assert got["policies"] == [] and "switched off here" in got["inactive"][0]["reason"] and not got["conflicts"]


def test_mandatory_from_the_definition_locks_where_it_is_introduced(platform):
    published(platform, OWNERSHIP)
    platform.call("save_assignment", policy="owner-required", scope="domain", target="sales")
    local = [{"policy": "owner-required", "scope": "project", "target": "orders",
              "settings": {"params": {"approvers": []}}}]
    got = platform.call("resolve_policies", project="orders", local=local)
    assert got["policies"][0]["params"]["approvers"] == ["steward"]
    assert "mandatory from domain sales" in got["conflicts"][0]["message"]
    local[0]["settings"]["params"]["approvers"] = ["steward", "owner"]
    assert platform.call("resolve_policies", project="orders", local=local)["policies"][0]["params"]["approvers"] == [
        "steward", "owner"]


def test_versions_can_be_pinned_but_not_rolled_back_under_mandatory(platform):
    published(platform, NAMING)
    platform.call("save_policy_draft", id="table-names", policy=dict(NAMING, remediation="v2 text"))
    platform.call("publish_policy", id="table-names")
    platform.call("save_assignment", policy="table-names", scope="enterprise", settings={"version": 1})
    assert platform.call("resolve_policies", project="crm")["policies"][0]["version"] == 1
    platform.call("save_assignment", policy="table-names", scope="enterprise", settings={"mandatory": True})
    local = [{"policy": "table-names", "scope": "project", "target": "crm", "settings": {"version": 1}}]
    got = platform.call("resolve_policies", project="crm", local=local)
    assert got["policies"][0]["version"] == 2 and "version can't go from 2 to 1" in got["conflicts"][0]["message"]
    with pytest.raises(core.PlatformError, match="isn't a published version"):
        platform.call("save_assignment", policy="table-names", scope="domain", target="sales", settings={"version": 7})
    bad = [{"policy": "table-names", "scope": "project", "target": "crm", "settings": {"version": 9}}]
    assert "no published version 9" in platform.call("resolve_policies", project="crm", local=bad)["conflicts"][0]["message"]


def test_resolution_is_deterministic(platform):
    published(platform, NAMING)
    published(platform, OWNERSHIP)
    platform.call("save_assignment", policy="table-names", scope="enterprise", settings={"mandatory": True})
    platform.call("save_assignment", policy="owner-required", scope="domain", target="emea")
    platform.call("save_assignment", policy="table-names", scope="domain", target="sales", settings={"params": {"max_length": 50}})
    local = [{"policy": "table-names", "scope": "project", "target": "crm", "settings": {"params": {"max_length": 55}}},
             {"policy": "owner-required", "scope": "entity", "target": "crm:gold.sales", "settings": {"severity": "critical"}}]
    snap = platform.call("policy_snapshot")
    chain = [("enterprise", "*"), ("domain", "sales"), ("domain", "emea"), ("project", "crm"), ("entity", "crm:gold.sales")]
    first = policy.resolve(snap, chain, local, today="2026-10-17")
    for seed in range(5):
        shuffled = dict(snap, assignments=random.Random(seed).sample(snap["assignments"], len(snap["assignments"])))
        assert policy.resolve(shuffled, chain, list(reversed(local)), today="2026-10-17") == first
    assert [p["id"] for p in first["policies"]] == ["owner-required", "table-names"]


def test_unknown_or_unpublished_policies_in_a_project_file_are_reported_not_fatal(platform):
    platform.call("create_policy", policy=OWNERSHIP)
    local = [{"policy": "owner-required", "scope": "project", "target": "crm", "settings": {}},
             {"policy": "no-such-policy", "scope": "project", "target": "crm", "settings": {}}]
    got = platform.call("resolve_policies", project="crm", local=local)
    assert got["policies"] == [] and len(got["inactive"]) == 2


def test_assignments_respect_the_scopes_a_policy_allows(platform):
    published(platform, dict(NAMING, scopes=["enterprise", "domain"]))
    with pytest.raises(core.PlatformError, match="enterprise, domain scope only"):
        platform.call("save_assignment", policy="table-names", scope="project", target="crm")
    platform.call("create_policy", policy=OWNERSHIP)
    with pytest.raises(core.PlatformError, match="publish it before"):
        platform.call("save_assignment", policy="owner-required", scope="enterprise")


# --------------------------------------------------------------------------- who may change what


def test_unauthorized_changes_are_rejected(platform, who):
    published(platform, NAMING)
    who["user"] = DEV
    for op, kwargs in [("create_policy", {"policy": OWNERSHIP}),
                       ("save_policy_draft", {"id": "table-names", "policy": NAMING}),
                       ("publish_policy", {"id": "table-names"}),
                       ("set_policy_status", {"id": "table-names", "status": "deprecated"}),
                       ("discard_policy_draft", {"id": "table-names"}),
                       ("save_assignment", {"policy": "table-names", "scope": "enterprise"}),
                       ("save_assignment", {"policy": "table-names", "scope": "project", "target": "crm"}),
                       ("save_assignment", {"policy": "table-names", "scope": "domain", "target": "sales"})]:
        with pytest.raises(core.PlatformError, match="Only platform admins"):
            platform.call(op, **kwargs)
    assert platform.call("get_policy", id="table-names")["status"] == "published"  # reading is open to everyone
    assert [e["action"] for e in platform.call("policy_history", id="table-names")] == ["published", "created"]


def test_domain_owners_assign_to_their_own_domains_only(platform, who):
    published(platform, NAMING)
    who["user"] = OWNER
    platform.call("save_assignment", policy="table-names", scope="domain", target="sales")
    platform.call("save_assignment", policy="table-names", scope="domain", target="emea")  # a subdomain of theirs
    with pytest.raises(core.PlatformError, match="owners of finance"):
        platform.call("save_assignment", policy="table-names", scope="domain", target="finance")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("save_assignment", policy="table-names", scope="enterprise")
    history = platform.call("policy_history", id="table-names")
    assert history[0]["by_user"] == OWNER and history[0]["detail"]["target"] == "emea"


def test_the_platform_records_the_signed_in_user_not_what_the_caller_says(platform, who):
    platform.call("create_policy", policy=NAMING, by="someone-else@contoso.com")
    assert platform.call("policy_history", id="table-names")[0]["by_user"] == ADMIN


# --------------------------------------------------------------------------- storage and migration


def test_a_platform_made_before_policies_gets_their_tables(tmp_path):
    path = tmp_path / "old.sqlite"
    p = LocalPlatform(path, lambda: ADMIN)
    p.call("save_domain", domain={"name": "Sales"})
    p.call("ensure_key_table", name="customer")
    conn = sqlite3.connect(path)
    for t in ("policy", "policy_version", "policy_assignment", "policy_event"):
        conn.execute(f'DROP TABLE "catalog__{t}"')
    conn.execute("UPDATE \"config__setting\" SET value = '3' WHERE name = 'schema_version'")
    conn.commit()
    conn.close()
    reopened = LocalPlatform(path, lambda: ADMIN)  # runs ensure_schema, as setup does in Fabric
    with closing(sqlite3.connect(path)) as conn:
        db = core.Db(conn, "sqlite")
        assert db.rows("SELECT value FROM \"config__setting\" WHERE name = 'schema_version'")[0]["value"] == str(core.SCHEMA_VERSION)
        assert all(db.exists("catalog", t) for t in ("policy", "policy_version", "policy_assignment", "policy_event"))
    assert [d["id"] for d in reopened.call("list_domains")] == ["sales"]
    assert reopened.call("key_stats", name="customer")["created"]


def test_policy_operations_create_missing_tables_on_first_use(tmp_path):
    with closing(sqlite3.connect(tmp_path / "x.sqlite")) as conn:
        db = core.Db(conn, "sqlite")
        core.ensure_schema(db)
        for t in ("policy", "policy_version", "policy_assignment", "policy_event"):
            db.run(f'DROP TABLE "catalog__{t}"')
        assert policy.list_policies(db) == []
        assert db.exists("catalog", "policy_event")
        assert core.ensure_schema(db)["created"] == []  # safe to run again


def test_the_function_app_runs_policies_in_their_own_module(monkeypatch):
    """The functions share their operations' names. The platform logic must still call its own
    functions (claim_admin calls whoami; create_policy calls get_policy), not the functions."""
    fake = types.ModuleType("fabric.functions")

    class Functions:
        def connection(self, **_):
            return lambda f: f
        context = connection

        def function(self):
            return lambda f: f

    fake.UserDataFunctions, fake.FabricSqlConnection, fake.UserDataFunctionContext = Functions, object, object
    fake.UserThrownError = RuntimeError
    monkeypatch.setitem(sys.modules, "fabric", types.SimpleNamespace(functions=fake))
    monkeypatch.setitem(sys.modules, "fabric.functions", fake)
    source = udf.function_app()
    for op in core.OPERATIONS:
        assert f"def {op}(" in source
    app: dict = {}
    exec(compile(source, "function_app.py", "exec"), app)
    with closing(sqlite3.connect(":memory:")) as conn:
        db = app["Db"](conn, "sqlite")
        app["OPERATIONS"]["ensure_schema"](db)
        assert app["OPERATIONS"]["claim_admin"](db, ADMIN)["admin"]
        assert app["OPERATIONS"]["create_policy"](db, NAMING, by=ADMIN)["draft_version"] == 1


def test_local_resolution_needs_no_fabric(platform, monkeypatch):
    import requests

    def no_network(*_, **__):
        raise AssertionError("resolving policies must not call Fabric")

    monkeypatch.setattr(requests.Session, "request", no_network)
    published(platform, NAMING)
    platform.call("save_assignment", policy="table-names", scope="enterprise")
    assert platform.call("resolve_policies", project="crm")["policies"][0]["id"] == "table-names"


# --------------------------------------------------------------------------- the open project


def test_governance_toml_is_read_and_its_problems_reported(tmp_path):
    (tmp_path / "governance.toml").write_text(
        '[[policy]]\nid = "table-names"\nenforcement = "block"\n[policy.params]\nmax_length = 40\n\n'
        '[[policy]]\nid = "owner-required"\nentity = "silver.customer"\ncolour = "red"\n\n[[policy]]\nseverity = "high"\n')
    local, problems = local_assignments(tmp_path, "crm")
    assert local[0] == {"policy": "table-names", "scope": "project", "target": "crm", "origin": "governance.toml",
                        "settings": {"enforcement": "block", "params": {"max_length": 40}}}
    assert local[1]["scope"] == "entity" and local[1]["target"] == "crm:silver.customer"
    assert problems == ["governance.toml: owner-required: colour isn't a policy setting",
                        "governance.toml: policy 3 needs an id"]
    (tmp_path / "governance.toml").write_text("[[policy]\n")
    assert local_assignments(tmp_path, "crm")[0] == []
    assert local_assignments(tmp_path / "nowhere", "crm") == ([], [])


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def test_existing_projects_work_without_a_governance_file(svc):
    assert [r["status"] for r in svc.meta_run()] == ["ok"] * 6
    out = svc.governance_resolve()
    assert out["project"] == "crm" and out["policies"] == [] and out["problems"] == []


def test_the_open_project_resolves_with_its_domain_file_and_entity_layer(svc, tmp_path):
    svc.platform_save_domain({"name": "Sales"})
    svc.mesh_set_project({"domain": "sales"})
    svc.governance_create(dict(NAMING, applies_to={"layers": ["gold"]}))
    svc.governance_publish("table-names")
    svc.governance_assign("table-names", "domain", "sales", {"enforcement": "warn"})
    (tmp_path / "crm" / "governance.toml").write_text('[[policy]]\nid = "table-names"\nenforcement = "block"\n')
    entities = svc.meta_project()["entities"]
    silver = next(e["name"] for e in entities if e["name"].startswith("silver."))
    gold = next(e["name"] for e in entities if e["name"].startswith("gold."))
    assert svc.governance_resolve(entity=silver)["policies"] == []
    got = svc.governance_resolve(entity=gold)["policies"][0]
    assert got["enforcement"] == "block" and got["steps"][-1]["origin"] == "governance.toml"
    overview = svc.governance_overview(query="snake")
    assert [p["id"] for p in overview["policies"]] == ["table-names"] and overview["counts"]["published"] == 1
    assert json.dumps(svc.governance_applicability("table-names"))


def test_rpc_methods_are_allowed():
    from fabric_desktop.server import METHODS

    assert {"governance_overview", "governance_policy", "governance_create", "governance_save_draft",
            "governance_publish", "governance_set_status", "governance_assign", "governance_unassign",
            "governance_applicability", "governance_resolve", "governance_discard_draft"} <= METHODS


def test_policy_changes_are_audited_and_reads_are_not():
    from fabric_desktop import audit

    for method in ("governance_create", "governance_save_draft", "governance_discard_draft", "governance_publish",
                   "governance_set_status", "governance_assign", "governance_unassign"):
        assert audit.audited(method)
    for method in ("governance_overview", "governance_policy", "governance_applicability",
                   "governance_resolve"):
        assert not audit.audited(method)
