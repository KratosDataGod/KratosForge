"""Mutation batch 121: central/home.py (docs/quality/mutation-testing.md). The survivors were the
fallbacks for fields a product, incident or domain leaves out, the order of incidents, domains and stale
requests, which products count as linked or depended on, how many asks and stale requests are shown,
and the day a request becomes stale."""

from __future__ import annotations

import datetime as dt

from fabric_desktop.central import home
from tests.test_mesh_home import INCIDENT, PRODUCTS, _product

NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)


def test_an_incident_s_missing_fields_and_the_order_incidents_come_in():
    bare = {"id": "bare", "env": "prod", "state": "open", "workload": INCIDENT["workload"], "name": "By name"}
    titled = dict(INCIDENT, id="titled", priority="P3", name="Not this", owner="ana", number="INC-7")
    later = dict(INCIDENT, id="later", state="acknowledged", priority="P1")
    reach = {i["id"]: i for i in home.incident_reach(PRODUCTS, [later, bare, titled, INCIDENT])}
    assert list(reach) == ["i1", "titled", "bare", "later"]  # open first, then by priority (none is last)
    assert {k: reach["bare"][k] for k in ("title", "priority", "owner", "opened_at", "number")} == {
        "title": "By name", "priority": "", "owner": "", "opened_at": "", "number": ""}
    assert (reach["titled"]["title"], reach["titled"]["owner"], reach["titled"]["number"]) == ("Orders late", "ana", "INC-7")
    assert reach["titled"]["opened_at"] == INCIDENT["opened_at"]


def test_my_products_missing_fields_and_a_deprecation_with_or_without_a_date():
    products = [{"id": "sales/a", "project": "sales", "owner": "me", "status": "deprecated"},
                dict(_product("sales/b", owner="me"), status="deprecated", deprecated_on="2026-12-01")]
    own = {p["id"]: p for p in home.team("me", None, products, [], None, None, [])["products"]}
    assert own["sales/a"]["name"] == "sales/a" and own["sales/a"]["version"] == ""
    assert own["sales/a"]["needs"][-1] == "Deprecated, stops soon"
    assert own["sales/b"]["needs"][-1] == "Deprecated, stops 2026-12-01"


def test_what_i_depend_on_is_what_my_project_reads_or_i_follow_and_not_my_own():
    products = [_product("ops/read", owner="x", consumers=[("sales", "daily")]),
                _product("ops/other", owner="x", consumers=[("hr", "daily")]),
                {"id": "ops/bare", "project": "ops", "owner": "x", "status": "deprecated"},
                {"id": "ops/plain", "project": "ops", "owner": "x", "status": "published"},
                dict(_product("ops/versioned", owner="x"), version="3"),
                _product("sales/mine", owner="x")]
    follows = ["ops/bare", "ops/plain", "ops/versioned", "sales/mine", "gone/product"]
    deps = {d["id"]: d for d in home.team("me", {"name": "sales"}, products, [], follows, None, [])["depends"]}
    assert list(deps) == ["ops/bare", "ops/plain", "ops/read", "ops/versioned"]
    assert deps["ops/read"]["how"] == "linked" and deps["ops/plain"]["how"] == "following"
    assert (deps["ops/bare"]["name"], deps["ops/bare"]["state"]) == ("ops/bare", "Deprecated, stops soon")
    assert deps["ops/plain"]["state"] == "Version 1" and deps["ops/versioned"]["state"] == "Version 3"


def test_only_my_ten_newest_asks_are_shown():
    asks = [{"id": f"r{i:02d}", "mine": True, "requested_at": f"2026-10-{i + 1:02d}"} for i in range(11)]
    out = home.team("me", None, [], asks, None, None, [])
    assert [r["id"] for r in out["asks"]] == [f"r{i:02d}" for i in range(10, 0, -1)]


def test_owner_gaps_count_only_the_products_with_no_owner():
    gaps = [{"problems": ["No owner"]}, {"problems": ["No second owner to cover"]}, {"problems": []}]
    assert home.oncall([], None, None, {"gaps": gaps})["owner_gaps"] == {"no_owner": 1, "total": 3}


def _waiting(days):
    return {"id": f"d{days}", "status": "waiting", "requested_at": (NOW - dt.timedelta(days=days)).isoformat()}


def test_a_request_is_stale_after_five_days_and_the_five_oldest_are_shown():
    assert home.STALE_DAYS == 5
    requests = [_waiting(d) for d in (5, 6, 7, 8, 9, 10, 11, 12)] + [{"id": "no-time", "status": "waiting"}]
    out = home.org([], [], requests, None, [], NOW)
    assert out["totals"]["stale"] == 7 and out["stale_days"] == 5
    assert [r["id"] for r in out["stale"]] == ["d12", "d11", "d10", "d9", "d8"]


def test_domains_sort_by_name_and_count_incidents_and_second_owners_per_product():
    domains = [{"id": "a", "name": "Zeta"}, {"id": "b", "name": "Alpha"}, {"id": "c"}]
    products = [_product("p/one", owner="x, y", domain="a", ports=[("daily", "silver.orders", "prod")]),
                _product("p/two", owner="x, y", domain="a"), _product("p/three", owner="x, y", domain="a")]
    reach = [{"state": "open", "products": [{"id": "p/one"}]}]
    out = home.org(domains, products, [], None, reach, NOW)
    assert [d["id"] for d in out["domains"]] == ["b", "c", "a"]
    zeta = out["domains"][-1]
    assert zeta["incidents"] == 1 and zeta["two_owners"] == 3
    assert zeta["needs"] == ["1 product with an incident upstream"]
