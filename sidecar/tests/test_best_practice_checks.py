"""Best practices: checks and templates (Preview). Policy templates in the platform's catalog, the check
registry (governance_checks.py), Check now on the open project, and a data product's checklist."""

import datetime as dt
import json

import pytest
import requests

from fabric_desktop import audit, governance_checks as gc, server
from fabric_desktop.central import core, policy, udf
from fabric_desktop.central.checks_service import last_load
from fabric_desktop.central.client import FabricPlatform, LocalPlatform
from fabric_desktop.service import Service

ADMIN, OWNER, DEV = "ana@contoso.com", "omar@contoso.com", "dev@contoso.com"
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def who():
    return {"user": ADMIN}


@pytest.fixture
def platform(tmp_path, who):
    p = LocalPlatform(tmp_path / "platform.sqlite", lambda: who["user"])
    p.call("save_domain", domain={"name": "Sales", "owners": OWNER})
    return p


def audited(platform, action):
    return [a for a in platform.call("audit") if a["action"] == action]


# --------------------------------------------------------------------------- templates


def test_built_in_templates_are_listed_read_only(platform):
    out = platform.call("list_policy_templates")
    builtin = [t for t in out["templates"] if t["builtin"]]
    assert [(t["id"], t["category"]) for t in builtin] == [
        ("table-names", "naming"), ("product-owner", "ownership"), ("sensitive-columns-labelled", "classification"),
        ("data-contract", "contract")]
    assert builtin[0]["name"] == "Table names" and "dim_ or fact_" in builtin[0]["description"]
    assert builtin[0]["definition"]["params"]["pattern"]["stricter"] == "fixed"
    assert builtin[3]["definition"]["params"]["within_hours"] == {"value": 24, "stricter": "lower",
                                                                   "description": "Hours since the last successful load"}
    # Every built-in names rules the registry has, and is a valid policy definition.
    for t in builtin:
        assert policy.clean_definition(t["definition"]) == t["definition"]
        assert all(gc.find_rule(r) for r in gc.rule_ids(t["definition"]["rule"]))
        assert not t["can_edit"]
    with pytest.raises(core.PlatformError, match="built-in template"):
        platform.call("delete_policy_template", id="table-names")
    with pytest.raises(core.PlatformError, match="built-in template"):
        platform.call("save_policy_template", id="data-contract", template=dict(builtin[3]["definition"]))


def test_user_templates_are_added_edited_and_deleted_with_an_audit(platform):
    t = platform.call("save_policy_template", template={"id": "no-pii-in-dev", "name": "No PII in Dev",
                                                        "category": "classification", "rule": "classification.sensitive_columns"})
    assert t["id"] == "no-pii-in-dev" and not t["builtin"] and t["created_by"] == ADMIN and t["can_edit"]
    assert audited(platform, "policy template created")[0]["target"] == "no-pii-in-dev"
    with pytest.raises(core.PlatformError, match="already a template"):
        platform.call("save_policy_template", template={"id": "no-pii-in-dev", "name": "x", "category": "naming"})
    t = platform.call("save_policy_template", id="no-pii-in-dev",
                      template={"name": "No PII in Dev", "category": "classification", "severity": "high"})
    assert t["definition"]["severity"] == "high"
    assert audited(platform, "policy template edited")
    yours = [x for x in platform.call("list_policy_templates")["templates"] if not x["builtin"]]
    assert [x["id"] for x in yours] == ["no-pii-in-dev"]
    assert platform.call("delete_policy_template", id="no-pii-in-dev") == {"id": "no-pii-in-dev", "deleted": True}
    assert audited(platform, "policy template deleted")
    assert all(x["builtin"] for x in platform.call("list_policy_templates")["templates"])


def test_only_admins_add_templates_unless_domain_owners_are_let_in(platform, who):
    template = {"id": "owner-tpl", "name": "Owners", "category": "ownership"}
    platform.call("save_policy_template", template={"id": "admins-tpl", "name": "Admins", "category": "naming"})
    who["user"] = DEV
    assert platform.call("list_policy_templates")["can_add"] is False
    with pytest.raises(core.PlatformError, match="Only platform admins can add or change policy templates"):
        platform.call("save_policy_template", template=template)
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("delete_policy_template", id="admins-tpl")
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("set_template_authors", authors="domain_owners")
    who["user"] = OWNER
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("save_policy_template", template=template)
    who["user"] = ADMIN
    assert platform.call("set_template_authors", authors="domain_owners")["authors"] == "domain_owners"
    assert audited(platform, "policy template authors")
    who["user"] = OWNER
    assert platform.call("list_policy_templates")["can_add"] is True
    assert platform.call("save_policy_template", template=template)["created_by"] == OWNER
    with pytest.raises(core.PlatformError, match="only the templates they added"):
        platform.call("delete_policy_template", id="admins-tpl")
    who["user"] = DEV  # owns no domain: still refused
    with pytest.raises(core.PlatformError, match="Only platform admins"):
        platform.call("save_policy_template", template={"id": "dev-tpl", "name": "Dev", "category": "naming"})
    who["user"] = OWNER
    assert platform.call("delete_policy_template", id="owner-tpl")["deleted"]


def test_use_makes_a_policy_draft_and_save_as_template_copies_a_policy(platform, who):
    p = platform.call("use_policy_template", template="data-contract", id="sales-contract", name="Sales contract")
    assert p["id"] == "sales-contract" and p["status"] == "draft" and p["draft_version"] == 1
    d = p["draft"]["definition"]
    assert d["name"] == "Sales contract" and d["rule"] == "contract.freshness, contract.row_count, contract.terms_linked"
    assert d["params"]["within_hours"]["value"] == 24 and p["draft"]["note"] == "From template data-contract"
    assert any(a["action"] == "policy created" for a in platform.call("audit"))
    platform.call("publish_policy", id="sales-contract")
    platform.call("save_policy_draft", id="sales-contract", policy=dict(d, description="changed in draft"))
    t = platform.call("save_policy_as_template", policy="sales-contract")
    assert t["id"] == "sales-contract-template" and t["definition"]["params"] == d["params"]
    assert t["definition"]["description"] == d["description"], "the current (published) definition, not the draft"
    assert audited(platform, "policy template created")[0]["target"] == "sales-contract-template"
    again = platform.call("use_policy_template", template="sales-contract-template", id="contract-two")
    assert again["draft"]["definition"]["name"] == "Sales contract"
    who["user"] = DEV
    with pytest.raises(core.PlatformError, match="Only platform admins can create governance policies"):
        platform.call("use_policy_template", template="table-names", id="x")


def test_templates_live_in_their_own_table_and_have_udf_functions(tmp_path):
    p = LocalPlatform(tmp_path / "p.sqlite", lambda: ADMIN)
    assert p.call("ensure_schema")["created"] == []
    source = udf.function_app()
    for op in ("list_policy_templates", "save_policy_template", "delete_policy_template", "use_policy_template",
               "save_policy_as_template", "set_template_authors", "ensure_template_schema"):
        assert op in core.OPERATIONS and f"def {op}(" in source


# --------------------------------------------------------------------------- the registry


def test_the_registry_contract():
    ids = {r["id"] for r in gc.all_rules()}
    assert {"naming.table_name", "ownership.product_owner", "classification.sensitive_columns", "contract.freshness",
            "contract.row_count", "contract.terms_linked", "contract.description", "contract.endorsed"} <= ids
    for r in gc.all_rules():
        assert all(r[k] for k in gc.RULE_FIELDS) and r["severity"] in gc.FINDING_SEVERITIES
        assert r["runs_in"] in gc.RUNS_IN and isinstance(r.get("default_thresholds", {}), dict)
    with pytest.raises(ValueError, match="needs name"):
        gc.register("bad", lambda: [{"id": "bad.x", "kind": "entity", "severity": "Error", "category": "naming",
                                     "description": "d"}], lambda i, s: [])
    with pytest.raises(ValueError, match="runs_in"):
        gc.register("bad", lambda: [], lambda i, s: [], runs_in="cloud")
    assert gc.rule_ids("contract.freshness, contract.row_count  naming.table_name") == [
        "contract.freshness", "contract.row_count", "naming.table_name"]
    s = gc.settings_for(gc.find_rule("contract.freshness")[1], "high", {"within_hours": 6, "enabled": True})
    assert s == {"enabled": True, "severity": "Error", "thresholds": {"within_hours": 6}}
    f = gc.naming_check({"kind": "entity", "id": "gold.orders", "layer": "gold", "table": "orders"},
                        {"naming.table_name": gc.settings_for(gc.NAMING_RULES[0], "medium", {})})[0]
    assert set(f) >= {"rule_id", "severity", "rule", "category", "object_type", "object", "description", "url"}
    assert f["rule_id"] == "naming.table_name" and f["rule"] == "Table names" and f["object_type"] == "entity"


def test_a_plugged_in_checker_carries_cell_and_line_through():
    rules = [{"id": "nb.print", "kind": "notebook", "severity": "Info", "category": "quality", "name": "No print",
              "description": "Use logging."}]

    def check(item, settings):
        s = settings.get("nb.print")
        if not s:
            return None
        return [gc.finding(rules[0], s, "notebook", item["id"], "print() in a cell", cell=2, line=7)]

    gc.register("notebook-test", lambda: rules, check)
    try:
        run = gc.run_rule("nb.print", [{"kind": "notebook", "id": "etl"}, {"kind": "entity", "id": "x"}],
                          gc.settings_for(rules[0], "low", {}))
        assert run["checked"] == 1 and run["findings"][0]["cell"] == 2 and run["findings"][0]["line"] == 7
        assert run["findings"][0]["severity"] == "Info"
    finally:
        gc.unregister("notebook-test")


def test_severity_maps_to_policy_severity():
    assert gc.SEVERITY_OF == {"critical": "Error", "high": "Error", "medium": "Warning", "low": "Info"}
    assert gc.LEVEL_OF == {"Error": "high", "Warning": "medium", "Info": "low"}
    rule = gc.find_rule("naming.table_name")[1]
    for level, sev in (("high", "Error"), ("medium", "Warning"), ("low", "Info")):
        assert gc.settings_for(rule, level, {})["severity"] == sev
    assert gc.settings_for(rule, "high", {"severity": "low"})["severity"] == "Info", "a severity param overrides"
    assert gc.settings_for(rule, "high", {"enabled": False})["enabled"] is False


def test_fabric_only_checkers_are_never_run_here():
    called = []
    rules = [{"id": "fab.vorder", "kind": "entity", "severity": "Warning", "category": "quality", "name": "V-Order",
              "description": "Tables are V-Ordered."}]
    gc.register("fabric-test", lambda: rules, lambda i, s: called.append(i) or [], runs_in="fabric")
    try:
        items = [{"kind": "entity", "id": "gold.dim_x"}]
        settings = gc.settings_for(rules[0], "medium", {})
        assert gc.run_rule("fab.vorder", items, settings) == {"rule_id": "fab.vorder", "name": "V-Order", "runs_in": "fabric",
                                                              "not_run": True, "last": None}
        state = gc.store_fabric_findings({}, "crm", "fab.vorder", [{"object": "gold.dim_x"}], at="2026-10-20T08:00:00+00:00")
        run = gc.run_rule("fab.vorder", items, settings, state["fabric"]["crm"])
        assert run["last"] == {"at": "2026-10-20T08:00:00+00:00", "findings": [{"object": "gold.dim_x"}]}
        assert called == []
    finally:
        gc.unregister("fabric-test")


# --------------------------------------------------------------------------- each built-in checker


def on(rule_id, **params):
    return {rule_id: gc.settings_for(gc.find_rule(rule_id)[1], "medium", params)}


def entity(name, **kw):
    layer, table = name.split(".", 1)
    return {"kind": "entity", "id": name, "layer": layer, "table": table, "owner": "me", "classification": "", "columns": []} | kw


def test_naming_pass_fail_and_unknown():
    s = on("naming.table_name")
    assert gc.naming_check(entity("gold.dim_customer"), s) == []
    assert gc.naming_check(entity("silver.Customers"), s) is None, "only gold tables by default"
    fail = gc.naming_check(entity("gold.customer_orders"), s)[0]
    assert fail["fix"] == {"action": "open_entity", "label": "Open the entity", "entity": "gold.customer_orders"}
    assert "doesn't match" in fail["description"] and fail["severity"] == "Warning"
    long = gc.naming_check(entity("gold.dim_customer"), on("naming.table_name", max_length=8))
    assert [f["description"] for f in long] == ["dim_customer is 12 characters; at most 8."]
    unknown = gc.naming_check(entity("gold.dim_x"), on("naming.table_name", pattern="(["))[0]
    assert unknown["outcome"] == "unknown"


def product(**kw):
    return {"kind": "product", "id": "crm/c360", "name": "c360", "project": "crm", "status": "published", "owner": "ana",
            "steward": "bo", "description": "Customers", "terms": ["Active customer"],
            "last_load": "2026-10-24T08:00:00+00:00", "last_rows": 10, "load_env": "prod",
            "endorsement": [{"item_name": "LH", "endorsement": "Certified"}], "entities": ["gold.dim_customer"]} | kw


def test_ownership_pass_fail_and_unknown():
    s = on("ownership.product_owner")
    assert gc.ownership_check(product(), s) == []
    assert gc.ownership_check(product(status="draft"), s) is None
    fail = gc.ownership_check(product(owner="", steward=""), s)[0]
    assert fail["description"] == "Names no owner or steward." and fail["fix"]["action"] == "edit_product"
    unknown = gc.ownership_check(product(steward=None, steward_reason="Not on this PC."), s)[0]
    assert unknown["outcome"] == "unknown" and unknown["description"] == "Not on this PC."
    assert gc.ownership_check(product(steward=""), on("ownership.product_owner", steward_required=False)) == []


def test_classification_pass_fail_and_unknown():
    s = on("classification.sensitive_columns")
    labelled = entity("silver.patients", columns=[{"name": "ssn", "classification": "restricted", "markers": ["pii"]},
                                                  {"name": "id", "classification": "", "markers": []}])
    assert gc.classification_check(labelled, s) == []
    assert gc.classification_check(entity("silver.x", columns=[{"name": "id", "classification": "", "markers": []}]), s) is None
    bad = entity("silver.patients", columns=[{"name": "diagnosis", "classification": "internal", "markers": ["phi"]}])
    f = gc.classification_check(bad, s)[0]
    assert f["object"] == "silver.patients#diagnosis" and f["description"] == "diagnosis is marked PHI but has internal."
    assert f["fix"] == {"action": "label", "label": "Label it", "entity": "silver.patients", "column": "diagnosis",
                        "labels": ["confidential", "restricted"]}
    assert f["severity"] == "Warning"
    # Unknown: a table-level rule whose layer isn't recorded can't apply, and says nothing rather than pass.
    assert gc.classification_check({"kind": "product"}, s) is None


def test_contract_pass_fail_and_unknown():
    s = {**on("contract.freshness", within_hours=6), **on("contract.row_count"), **on("contract.terms_linked"),
         **on("contract.description"), **on("contract.endorsed")}
    assert gc.contract_check(product(), s, now=NOW) == []
    fails = gc.contract_check(product(last_load="2026-10-23T12:00:00+00:00", last_rows=0, terms=[], description="",
                                      endorsement=[{"item_name": "LH", "endorsement": "None"}]), s, now=NOW)
    assert {f["rule_id"] for f in fails} == {"contract.freshness", "contract.row_count", "contract.terms_linked",
                                             "contract.description", "contract.endorsed"}
    assert next(f for f in fails if f["rule_id"] == "contract.freshness")["description"] == \
        "Last loaded 24 h ago in Prod; the contract says within 6 h."
    unknown = gc.contract_check(product(last_load=None, last_rows=None, load_reason="Never ran.", terms=None,
                                        terms_reason="Not in the catalog.", endorsement=[{"item_name": "LH", "endorsement": None}]),
                                s, now=NOW)
    assert {f["rule_id"]: f["description"] for f in unknown if f.get("outcome") == "unknown"} == {
        "contract.freshness": "Never ran.", "contract.row_count": "Never ran.", "contract.terms_linked": "Not in the catalog.",
        "contract.endorsed": "Unknown: needs a tenant scan."}


def test_last_load_takes_the_highest_environment_where_every_table_loaded():
    runs = [{"entity": "a", "env": "prod", "status": "ok", "started_at": "2026-10-24T10:00:00+00:00", "rows_written": 5},
            {"entity": "a", "env": "dev", "status": "ok", "started_at": "2026-10-24T09:00:00+00:00", "rows_written": 7},
            {"entity": "b", "env": "dev", "status": "ok", "started_at": "2026-10-23T09:00:00+00:00", "rows_written": 3},
            {"entity": "b", "env": "prod", "status": "failed", "started_at": "2026-10-24T11:00:00+00:00", "rows_written": 0}]
    assert last_load(runs, ["a", "b"]) == {"last_load": "2026-10-23T09:00:00+00:00", "load_env": "dev", "last_rows": 3}
    assert last_load(runs, ["a"])["load_env"] == "prod"
    gone = last_load([], ["a"], {"prod": "Sign in to see its runs"})
    assert gone["last_load"] is None and "Prod: Sign in to see its runs." in gone["load_reason"]


# --------------------------------------------------------------------------- Check now, on the open project


@pytest.fixture
def svc(tmp_path):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "crm"), "3")
    return s


def publish(svc, template, pid, **params):
    svc.governance_template_use(template, id=pid)
    if params:
        p = svc.governance_policy(pid)
        d = p["draft"]["definition"]
        for k, v in params.items():
            d["params"][k]["value"] = v
        svc.governance_save_draft(pid, d)
    svc.governance_publish(pid)
    svc.governance_assign(pid, "enterprise")


def test_check_now_runs_policies_on_this_pc_and_remembers_the_result(svc, tmp_path):
    root = tmp_path / "crm"
    (root / "metadata" / "entities" / "gold.customer_orders.toml").write_text(
        (root / "metadata" / "entities" / "gold.dim_customer.toml").read_text().replace("gold.dim_customer", "gold.customer_orders"))
    svc._project().reload()
    publish(svc, "table-names", "table-names")
    svc.governance_create({"id": "nothing-checks-it", "name": "Unchecked", "category": "schema"})
    svc.governance_publish("nothing-checks-it")
    svc.governance_assign("nothing-checks-it", "enterprise")
    assert svc.governance_last_check()["result"] is None
    out = svc.governance_check_now()
    assert out["project"] == "crm" and out["where"] == "this PC" and out["enforcement"] == "Warn only: nothing is blocked."
    names = next(p for p in out["policies"] if p["id"] == "table-names")
    assert names["status"] == "findings" and [f["object"] for f in names["findings"]] == ["gold.customer_orders"]
    assert names["checked"] == 3 and names["passed"] == 2
    assert out["counts"]["to_look_at"] == 1
    unchecked = next(p for p in out["policies"] if p["id"] == "nothing-checks-it")
    assert unchecked["status"] == "not_checked" and "names no rule" in unchecked["note"]
    saved = json.loads((tmp_path / "app" / "governance_checks.json").read_text())
    assert saved["results"]["crm"]["checked_at"] == out["checked_at"]
    assert svc.governance_last_check()["result"]["counts"] == out["counts"]


def test_check_now_labels_and_exceptions(svc, tmp_path):
    root = tmp_path / "crm"
    path = root / "metadata" / "entities" / "silver.customers.toml"
    col = next(iter(svc._project().entities["silver.customers"].columns))
    path.write_text(path.read_text() + f'\n[catalog.columns.{col}]\ntags = ["PII"]\n')
    svc._project().reload()
    publish(svc, "sensitive-columns-labelled", "labels")
    out = svc.governance_check_now()
    got = next(p for p in out["policies"] if p["id"] == "labels")
    assert got["status"] == "findings" and got["findings"][0]["fix"]["action"] == "label"
    assert got["findings"][0]["severity"] == "Error", "a high policy gives Error"
    svc.mesh_save_about("silver.customers", {"classification": "confidential"}, column=col)
    assert next(p for p in svc.governance_check_now()["policies"] if p["id"] == "labels")["status"] == "pass"
    # An approved exception turns a finding into a note.
    svc.mesh_save_about("silver.customers", {"classification": "internal"}, column=col)
    until = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    req = svc.platform_request("exception", "labels", "Masked copy not ready",
                               {"policy": "labels", "scope": "project", "scope_target": "crm", "until": until})
    svc.platform_decide(req["id"], True)
    out = svc.governance_check_now()
    got = next(p for p in out["policies"] if p["id"] == "labels")
    assert got["status"] == "excepted"
    assert got["findings"][0]["excepted"] == f"Excepted until {until}: Masked copy not ready"
    assert out["counts"]["to_look_at"] == 0 and out["counts"]["excepted"] == 1


def test_check_now_says_unknown_instead_of_passing(svc):
    svc.mesh_save_product({"name": "customer_360", "description": "Customers", "owner": "me", "status": "published",
                           "access": "public", "ports": [{"name": "customers", "entity": "gold.dim_customer"}]})
    publish(svc, "data-contract", "contract")
    publish(svc, "product-owner", "owners")
    out = svc.governance_check_now()
    contract = next(p for p in out["policies"] if p["id"] == "contract")
    assert contract["status"] == "unknown" and contract["passed"] == 0
    reasons = {f["rule_id"]: f["description"] for f in contract["unknown"]}
    assert "No successful load is recorded" in reasons["contract.freshness"]
    assert "isn't in the platform's catalog yet" in reasons["contract.terms_linked"]
    owners = next(p for p in out["policies"] if p["id"] == "owners")
    assert owners["status"] == "findings" and owners["findings"][0]["description"] == "Names no steward."
    svc.mesh_save_product({"name": "customer_360", "description": "Customers", "owner": "me", "steward": "bo",
                           "status": "published", "access": "public",
                           "ports": [{"name": "customers", "entity": "gold.dim_customer"}]})
    assert 'steward = "bo"' in (svc._project().root / "metadata" / "products" / "customer_360.toml").read_text()
    svc.meta_run()
    out = svc.governance_check_now()
    assert next(p for p in out["policies"] if p["id"] == "owners")["status"] == "pass"
    contract = next(p for p in out["policies"] if p["id"] == "contract")
    assert {f["rule_id"] for f in contract["unknown"]} == {"contract.terms_linked"}, "the run log now answers freshness"


class TrustBehindAGateway:
    """The local platform, except that product_trust goes to a UDF_Platform whose gateway answers 502."""

    def __init__(self, platform, content):
        self.platform, self.content = platform, content

    def call(self, op, **kwargs):
        if op != "product_trust":
            return self.platform.call(op, **kwargs)
        resp = requests.Response()
        resp.status_code, resp._content, resp.reason = 502, self.content, "Bad Gateway"
        session = type("Session", (), {"post": lambda self, url, **kw: resp})()
        return FabricPlatform(lambda: "tok", "ws", "fn", session=session).call(op, **kwargs)


@pytest.mark.parametrize("content", [b"<html><title>502 Bad Gateway</title></html>",
                                     b'{"errors": [{"message": "Bad Gateway"}]}'])
def test_check_now_says_unknown_when_a_gateway_answers_502(svc, content):
    """A 502 is the platform not being reached (unknown, never a pass), not "isn't in the catalog yet"."""
    svc.mesh_save_product({"name": "customer_360", "description": "Customers", "owner": "me", "status": "published",
                           "access": "public", "ports": [{"name": "customers", "entity": "gold.dim_customer"}]})
    publish(svc, "data-contract", "contract")
    local = svc._catalog()
    svc._catalog = lambda: TrustBehindAGateway(local, content)
    out = svc.governance_check_now()
    contract = next(p for p in out["policies"] if p["id"] == "contract")
    reasons = {f["rule_id"]: f["description"] for f in contract["unknown"]}
    assert contract["status"] == "unknown"
    assert reasons["contract.terms_linked"] == (
        "The platform couldn't be read: HTTPError: UDF_Platform couldn't be reached to product trust: "
        "Fabric answered 502 Bad Gateway. Try again in a few minutes.")


def test_a_data_products_checklist(svc, tmp_path):
    svc.mesh_save_product({"name": "customer_360", "description": "", "owner": "me", "steward": "bo", "status": "published",
                           "access": "public", "ports": [{"name": "customers", "entity": "gold.dim_customer"}]})
    cat = svc._catalog()
    cat.call("register_product", product={"project": "crm", "name": "customer_360", "owner": "me", "access": "public",
                                          "status": "published"},
             ports=[{"port": "customers", "lakehouse_id": "lh-1", "workspace_id": "ws-1", "lakehouse_name": "LH_Gold"}], env="prod")
    cat.call("register_product", product={"project": "hr", "name": "people", "owner": "", "access": "public",
                                          "status": "published", "description": "Staff"}, ports=[], env="prod")
    out = svc.governance_product_checklist("crm/customer_360")
    assert out["source"] == {"template": "data-contract", "name": "Data contract", "builtin": True}
    marks = {r["label"]: (r["mark"], r["detail"]) for r in out["rows"]}
    assert list(marks) == ["Owner and steward", "Description", "Business terms linked", "Fresh within 24 h", "Endorsement"]
    assert marks["Owner and steward"] == ("pass", "Owner me, steward bo.")
    assert marks["Description"][0] == "fail" and marks["Business terms linked"] == ("fail", "Links no business term.")
    assert marks["Fresh within 24 h"][0] == "unknown"
    assert marks["Endorsement"] == ("unknown", "Unknown: needs a tenant scan.")
    publish(svc, "data-contract", "contract", within_hours=6)
    assert svc.governance_product_checklist("crm/customer_360")["rows"][3]["label"] == "Fresh within 6 h"
    other = {r["label"]: (r["mark"], r["detail"]) for r in svc.governance_product_checklist("hr/people")["rows"]}
    assert other["Owner and steward"][0] == "fail"
    assert other["Fresh within 6 h"] == ("unknown", "Load times are in hr's run log. Open that project on this PC to check them.")
    assert other["Endorsement"] == ("unknown", "Has no item in Fabric yet, so there's no endorsement to read.")


# --------------------------------------------------------------------------- an older UDF_Platform, and the RPC surface


class OldPlatform:
    def call(self, op, **_):
        raise LookupError(f"404: {op}")


def test_an_older_udf_platform_says_to_finish_or_repair(svc):
    svc._catalog = lambda: OldPlatform()
    for call in (svc.governance_templates, lambda: svc.governance_template_delete("x"),
                 lambda: svc.governance_template_use("table-names"), lambda: svc.governance_template_from_policy("p"),
                 lambda: svc.governance_template_save({"name": "x"}), lambda: svc.governance_template_authors("admins")):
        with pytest.raises(LookupError, match="After an update, a platform admin runs Platform > Settings > Finish or repair once."):
            call()


def test_rpc_methods_are_allowed_and_reads_are_not_audited():
    new = {"governance_templates", "governance_template_save", "governance_template_delete", "governance_template_use",
           "governance_template_from_policy", "governance_template_authors", "governance_check_now",
           "governance_last_check", "governance_product_checklist"}
    assert new <= server.METHODS
    for m in ("governance_templates", "governance_last_check", "governance_product_checklist"):
        assert not audit.audited(m)
    for m in new - {"governance_templates", "governance_last_check", "governance_product_checklist"}:
        assert audit.audited(m)


def test_every_item_must_be_endorsed():
    s = on("contract.endorsed")
    mixed = product(endorsement=[{"item_name": "LH_Gold", "endorsement": "Certified"},
                                 {"item_name": "LH_Silver", "endorsement": "None"}])
    f = gc.contract_check(mixed, s, now=NOW)[0]
    assert f["rule_id"] == "contract.endorsed" and f["description"] == "1 of 2 items aren't endorsed in Fabric: LH_Silver."
    both = product(endorsement=[{"item_name": "A", "endorsement": "Certified"}, {"item_name": "B", "endorsement": "Promoted"}])
    assert gc.contract_check(both, s, now=NOW) == []


def test_a_setting_a_rule_cant_read_is_unknown_and_the_rest_still_run():
    bad = gc.settings_for(gc.find_rule("contract.row_count")[1], "medium", {"min_rows": "ten"})
    run = gc.run_rule("contract.row_count", [product(), product(id="crm/other")], bad)
    assert run["checked"] == 2 and run["findings"] == []
    assert len(run["unknown"]) == 2 and all("couldn't run" in f["description"] for f in run["unknown"])
    assert gc.run_rule("naming.table_name", [entity("gold.dim_customer")],
                       on("naming.table_name")["naming.table_name"])["passed"] == 1


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a|a)+$", r"(?P<g>a+)+$", r"((a+))+$", r"(\w+\w+)+$", r"(a+b?)+$"])
def test_a_pattern_that_backtracks_in_python_still_matches_at_once(pattern):
    import time
    start = time.monotonic()
    f = gc.naming_check(entity("gold." + "a" * 40 + "!"), on("naming.table_name", pattern=pattern))
    assert time.monotonic() - start < 2
    assert len(f) == 1 and "doesn't match" in f[0]["description"] and "outcome" not in f[0]
    assert gc.naming_check(entity("gold." + "a" * 40), on("naming.table_name", pattern=pattern)) == []


def test_patterns_re2_cant_take_are_unknown_and_long_names_arent_matched():
    for pattern in (r"(a)\1", r"(?=dim)\w+"):
        f = gc.naming_check(entity("gold.dim_x"), on("naming.table_name", pattern=pattern))[0]
        assert f["outcome"] == "unknown" and "Lookarounds and backreferences" in f["description"]
    f = gc.naming_check(entity("gold.dim_x"), on("naming.table_name", pattern="x" * 301))[0]
    assert f["outcome"] == "unknown" and "Shorten the pattern" in f["description"]
    long = gc.naming_check(entity("gold." + "x" * 300), on("naming.table_name"))[0]
    assert "too long to check" in long["description"]
