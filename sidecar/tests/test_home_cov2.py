"""Data mesh > Home's views (central/home.py): a product with no owner or deprecated, a dependency the
project publishes itself, a resolved incident, a request with an unreadable date and a domain whose
products all have two owners."""

import datetime as dt

from fabric_desktop.central import home

NOW = dt.datetime(2026, 11, 18, 12, 0)


def test_team_flags_no_owner_and_deprecated_and_skips_its_own_products_as_dependencies():
    products = [
        {
            "id": "crm/a",
            "name": "A",
            "project": "crm",
            "status": "published",
            "owner": "",
        },
        {
            "id": "crm/b",
            "name": "B",
            "project": "crm",
            "status": "deprecated",
            "owner": "x@c.com, y@c.com",
            "deprecated_on": "2027-01-01",
            "consumers": [{"product_id": "crm/a", "consumer_project": "crm"}],
        },
        {
            "id": "crm/c",
            "name": "C",
            "project": "crm",
            "status": "deprecated",
            "owner": "x@c.com",
        },
    ]
    out = home.team("me@c.com", {"name": "crm"}, products, [], ["crm/c"], None, [])
    needs = {p["id"]: p["needs"] for p in out["products"]}
    assert needs["crm/a"] == ["No owner"]
    assert needs["crm/b"] == ["Deprecated, stops 2027-01-01"]
    assert needs["crm/c"] == ["No second owner", "Deprecated, stops soon"]
    # linked (crm/a) and followed (crm/c), but both are this project's own: not a dependency
    assert out["depends"] == []
    assert out["reviews_known"] is False and out["follows_known"] is True


def test_oncall_doesnt_wake_anyone_for_a_resolved_incident():
    reach = [
        {"id": "i1", "state": "resolved", "products": [{"name": "A", "owners": ["x"]}]},
        {"id": "i2", "state": "open", "products": [{"name": "B", "owners": ["y"]}]},
    ]
    out = home.oncall(reach, None, None, {"available": False})
    assert out["wake"] == [{"product": "B", "owners": ["y"]}]
    assert out["late"] is None and out["broken"] is None and out["owner_gaps"] is None


def test_org_ignores_an_unreadable_request_date_and_a_fully_owned_domain_needs_nothing():
    domains = [{"id": "d1", "name": "Sales"}]
    products = [{"id": "p1", "domain_id": "d1", "status": "published", "owner": "a; b"}]
    requests = [
        {"id": "r1", "status": "waiting", "requested_at": "not a date"},
        {"id": "r2", "status": "waiting", "requested_at": None},
        {
            "id": "r3",
            "status": "waiting",
            "requested_at": (NOW - dt.timedelta(days=9)).isoformat(),
        },
    ]
    out = home.org(domains, products, requests, None, [], NOW)
    assert out["domains"] == [
        {
            "id": "d1",
            "name": "Sales",
            "products": 1,
            "readers": 0,
            "two_owners": 1,
            "reviews_due": None,
            "incidents": 0,
            "needs": [],
        }
    ]
    assert out["totals"]["waiting"] == 3 and out["totals"]["stale"] == 1
    assert [r["id"] for r in out["stale"]] == ["r3"] and out["stale"][0]["days"] == 9


def test_days_of_unreadable_values_is_none():
    assert home._days("2026-13-40", NOW) is None
    assert home._days(None, NOW) is None
    assert home._days("2026-11-15T12:00:00", NOW) == 3
