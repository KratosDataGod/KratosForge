"""Data mesh > Home: the team lead's, on-call's and organization's views (central/home.py)."""

import datetime as dt
import types

import pytest

from fabric_desktop.central import home
from fabric_desktop.service import Service

NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)


def _product(pid, owner="olu@contoso.com", status="published", domain="sales", ports=(), consumers=()):
    project, _, name = pid.partition("/")
    return {"id": pid, "project": project, "name": name, "owner": owner, "status": status, "domain_id": domain,
            "version": "2", "ports": [{"port": p, "entity": e, "env": env} for p, e, env in ports],
            "consumers": [{"product_id": pid, "consumer_project": c, "port": port, "updated_by": ""} for c, port in consumers]}


PRODUCTS = [
    _product("sales/orders", owner="olu@contoso.com, ana@contoso.com", ports=[("daily", "silver.orders", "prod")],
             consumers=[("finance", "daily"), ("hr", "daily")]),
    _product("sales/returns", ports=[("daily", "silver.returns", "prod")]),
    _product("sales/wip", status="draft"),
    _product("ops/inventory", owner="bo@contoso.com", domain="ops", consumers=[("sales", "daily")]),
    _product("ops/stores", owner="", domain="ops-retail", status="deprecated"),
]
INCIDENT = {"id": "i1", "env": "prod", "title": "Orders late", "state": "open", "priority": "P2",
            "workload": "meta:sales:silver.orders", "opened_at": "2026-10-27T10:00:00+00:00"}


def test_incident_reach_keeps_incidents_that_reach_a_product_with_its_owners_and_readers():
    other = {"id": "i2", "env": "prod", "title": "Agent offline", "state": "open", "workload": "agent:a1"}
    elsewhere = dict(INCIDENT, id="i3", env="dev")  # no dev port carries silver.orders
    reach = home.incident_reach(PRODUCTS, [other, INCIDENT, elsewhere])
    assert [i["id"] for i in reach] == ["i1"]
    assert reach[0]["products"] == [{"id": "sales/orders", "name": "orders", "owners": ["olu@contoso.com", "ana@contoso.com"]}]
    assert reach[0]["readers"] == ["finance", "hr"] and reach[0]["people"] == 2


def test_team_lists_my_products_with_what_each_needs_requests_on_me_and_what_i_depend_on():
    reach = home.incident_reach(PRODUCTS, [INCIDENT])
    requests = [{"id": "r1", "status": "waiting", "can_decide": True, "mine": False, "requested_at": "2026-10-26"},
                {"id": "r2", "status": "waiting", "can_decide": False, "mine": True, "requested_at": "2026-10-25"},
                {"id": "r3", "status": "approved", "can_decide": False, "mine": True, "requested_at": "2026-10-26"}]
    reviews = {"grants": [{"product_id": "sales/orders", "project": "finance", "due": True},
                          {"product_id": "sales/orders", "project": "hr", "due": False}]}
    out = home.team("Olu@contoso.com", {"name": "sales"}, PRODUCTS, requests, ["ops/stores"], reviews, reach)
    rows = {p["id"]: p for p in out["products"]}
    assert list(rows) == ["sales/orders", "sales/returns"]  # drafts aren't products yet
    assert rows["sales/orders"]["needs"] == ["Incident upstream", "Access review due"]
    assert rows["sales/orders"]["incident"] == "i1" and rows["sales/orders"]["readers"] == ["finance", "hr"]
    assert rows["sales/returns"]["needs"] == ["No second owner"]
    assert [r["id"] for r in out["waiting"]] == ["r1"] and [r["id"] for r in out["asks"]] == ["r3", "r2"]
    assert [(d["id"], d["how"]) for d in out["depends"]] == [("ops/inventory", "linked"), ("ops/stores", "following")]
    assert out["depends"][1]["state"].startswith("Deprecated")
    assert out["reviews_due"] == 1 and out["reviews_known"] and out["follows_known"]


def test_team_without_a_project_shows_the_products_i_own_and_unknown_sources_as_unknown():
    out = home.team("bo@contoso.com", None, PRODUCTS, [], None, None, [])
    assert [p["id"] for p in out["products"]] == ["ops/inventory"] and out["depends"] == []
    assert out["follows_known"] is False and out["reviews_known"] is False


def test_oncall_names_who_to_wake_and_says_when_a_source_could_not_be_read():
    reach = home.incident_reach(PRODUCTS, [INCIDENT])
    landing = {"sources": [{"name": "sap", "late": True}, {"name": "crm", "late": False}], "problem": None}
    shortcuts = [{"name": "a", "health": "broken"}, {"name": "b", "health": "healthy"}]
    coverage = {"gaps": [{"problems": ["No owner"]}, {"problems": ["No second owner to cover"]}]}
    out = home.oncall(reach, landing, shortcuts, coverage)
    assert out["wake"] == [{"product": "orders", "owners": ["olu@contoso.com", "ana@contoso.com"]}]
    assert [s["name"] for s in out["late"]] == ["sap"] and [s["name"] for s in out["broken"]] == ["a"]
    assert out["owner_gaps"] == {"no_owner": 1, "total": 2}
    unknown = home.oncall([], None, None, None)
    assert unknown["late"] is None and unknown["broken"] is None and unknown["owner_gaps"] is None
    assert home.oncall([], None, None, {"available": False, "gaps": []})["owner_gaps"] is None


def test_org_counts_per_top_level_domain_with_subdomains_rolled_up_and_stale_requests():
    domains = [{"id": "sales", "name": "Sales"}, {"id": "ops", "name": "Operations"},
               {"id": "ops-retail", "name": "Retail", "parent_id": "ops"}]
    requests = [{"id": "old", "status": "waiting", "requested_at": "2026-10-10T09:00:00+00:00"},
                {"id": "new", "status": "waiting", "requested_at": "2026-10-26T09:00:00+00:00"}]
    reviews = {"grants": [{"product_id": "sales/orders", "due": True}, {"product_id": "ops/inventory", "due": False}]}
    out = home.org(domains, PRODUCTS, requests, reviews, home.incident_reach(PRODUCTS, [INCIDENT]), NOW)
    rows = {d["id"]: d for d in out["domains"]}
    assert list(rows) == ["ops", "sales"]  # sorted by name; the subdomain counts toward Operations
    assert rows["ops"]["products"] == 2 and rows["ops"]["two_owners"] == 0
    assert rows["sales"] | {"needs": None} == {"id": "sales", "name": "Sales", "products": 2, "readers": 2,
                                               "two_owners": 1, "reviews_due": 1, "incidents": 1, "needs": None}
    assert rows["sales"]["needs"] == ["1 product with an incident upstream", "1 without a second owner", "1 access review due"]
    assert out["totals"] == {"domains": 2, "products": 4, "two_owners": 1, "reviews_due": 1, "grants": 2,
                             "waiting": 2, "stale": 1}
    assert [r["id"] for r in out["stale"]] == ["old"] and out["stale"][0]["days"] == 17
    assert home.org(domains, PRODUCTS, [], None, [], NOW)["totals"]["reviews_due"] is None


# --------------------------------------------------------------------------- the service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    s = Service(root=tmp_path / "app")
    s._auth = types.SimpleNamespace(signed_in_user=lambda: "chris@contoso.com", silent_token=lambda scopes: None,
                                    fabric_token=lambda: None, storage_token=lambda: None)
    return s


def test_mesh_home_works_without_a_project_and_names_what_it_could_not_read(svc, monkeypatch):
    cat = svc._catalog()
    cat.call("register_product", product={"project": "sales", "name": "orders", "owner": "chris@contoso.com",
                                          "access": "public", "status": "published", "domain": "sales"},
             ports=[{"port": "daily", "entity": "silver.orders"}], env="prod")
    team = svc.mesh_home("team")
    assert team["project"] is None and team["incidents_read"] is False
    assert [p["id"] for p in team["team"]["products"]] == ["sales/orders"]
    assert team["team"]["products"][0]["needs"] == ["No second owner"]
    org = svc.mesh_home("org")
    assert org["org"]["totals"]["products"] == 1
    monkeypatch.setattr(svc, "platform_landing", lambda env: (_ for _ in ()).throw(RuntimeError("no landing")))
    oncall = svc.mesh_home("oncall")
    assert oncall["oncall"]["late"] is None and any(p.startswith("Landing zone:") for p in oncall["problems"])
    with pytest.raises(ValueError, match="view is one of"):
        svc.mesh_home("everyone")


def test_mesh_home_with_a_project_reads_its_incidents_and_shows_what_they_reach(svc, tmp_path):
    svc.meta_init(str(tmp_path / "proj"), "3")
    project = svc.mesh_home("team")["project"]["name"]
    cat = svc._catalog()
    cat.call("register_product", product={"project": project, "name": "orders", "owner": "olu@contoso.com",
                                          "access": "public", "status": "published", "domain": "sales"},
             ports=[{"port": "daily", "entity": "silver.orders"}], env="dev")
    svc.incident_report("dev", "Orders late", workload=f"meta:{project}:silver.orders")
    team = svc.mesh_home("team")
    assert team["incidents_read"] and team["team"]["products"][0]["needs"][0] == "Incident upstream"
    oncall = svc.mesh_home("oncall")["oncall"]
    assert oncall["wake"] == [{"product": "orders", "owners": ["olu@contoso.com"]}]
