"""Governance's notices (governance_notices.py), mutation batch 275: what its first mutation score
found untested. Which product a request is about and its domain, the time a notice keeps, what a
declined request left in Fabric only for its own kind, a title of exactly the limit, and the whole
of a deprecation, an incident's reach and an exception about to end."""

from __future__ import annotations

import hashlib

from fabric_desktop import governance_notices as notices

PRODUCTS = [{"id": "shop/a", "owner": "ana@contoso.com", "domain_id": "sales"},
            {"id": "shop/b", "owner": "bo@contoso.com", "domain_id": "finance"}]


def req(**extra):
    return {"id": "r1", "kind": "access", "target": "shop/b", "detail": {}, "requested_by": "lee@contoso.com",
            "requested_at": "2026-10-21T10:00:00+00:00", "status": "waiting", "decided_at": ""} | extra


def test_a_request_is_about_the_product_it_names_not_the_first_one():
    event = notices.request_waiting(req(), PRODUCTS)
    assert event["to"]["people"] == ["bo@contoso.com"] and event["domain"] == "finance"
    assert event["at"] == "2026-10-21T10:00:00+00:00"  # the time it was asked, not now


def test_a_new_data_product_is_in_the_domain_of_the_product_its_detail_names():
    r = req(kind="data", target="shop/b", detail={"what": "new", "product_id": "shop/a", "owners": "kim@contoso.com"})
    event = notices.request_waiting(r, PRODUCTS)
    assert event["domain"] == "sales" and event["to"]["people"] == ["kim@contoso.com"]


def test_a_shortcut_request_without_a_product_is_about_none():
    event = notices.request_waiting(req(kind="shortcut", target="lh/x", detail={}), PRODUCTS)
    assert event["to"]["people"] == [] and event["domain"] == ""


def test_only_an_exception_for_a_domain_is_in_that_domain():
    r = req(kind="exception", target="pol-1", detail={"scope": "project", "scope_target": "crm"})
    assert notices.request_waiting(r, PRODUCTS)["domain"] == ""
    r = req(kind="exception", target="pol-1", detail={"scope": "domain", "scope_target": "hr"})
    assert notices.request_waiting(r, PRODUCTS)["domain"] == "hr"


def _left(kind, made):
    r = req(kind=kind, target="T", status="declined", detail={"made": made})
    return notices.request_closed(r, PRODUCTS)["title"]


def test_what_a_declined_request_left_is_said_only_for_its_own_kind():
    assert _left("domain", {"copy_job_id": "j1"}) == "New domain T (declined)"
    assert _left("source", {"domain_id": "hr"}) == "New source T (declined)"
    assert _left("domain", {"shortcut_id": "s1"}) == "New domain T (declined)"
    assert _left("domain", {}) == "New domain T (declined)"
    assert _left("shortcut", {}) == "New shortcut T (declined)"
    assert _left("source", {"copy_job_id": "j1"}) == "New source T (declined); its Copy job is still in the landing workspace"


def test_a_title_of_exactly_the_limit_is_kept_whole_and_a_tail_past_it_keeps_no_head():
    head = "h" * (notices.TITLE_MAX - 10)
    assert notices._fit(head, "t" * 10) == head + "t" * 10
    assert notices._fit("abc", "t" * (notices.TITLE_MAX + 5)) == "…" + "t" * (notices.TITLE_MAX + 5)


def test_a_changed_product_and_a_shortcut_keep_their_domain_and_time():
    event = notices.product_changed({"id": "p1", "name": "Orders", "domain_id": "sales"}, ["v2"], ["a@contoso.com"], "prod")
    assert event["domain"] == "sales"
    [broke] = notices.shortcut_events([{"kind": "broke", "shortcut_id": "s1", "at": "2026-10-21T10:00:00+00:00"}])
    assert broke["at"] == "2026-10-21T10:00:00+00:00"


def test_a_deprecation_names_each_reading_project_and_link():
    product = {"id": "shop/a", "deprecated_on": " 2026-12-01 ", "project": "shop", "domain_id": "sales"}
    consumers = [{"consumer_project": "crm", "alias": "orders", "updated_by": "kim@contoso.com"},
                 {"consumer_project": "bi"},
                 {"consumer_project": "shop", "alias": "self"}]  # its own project isn't a reader
    event = notices.product_deprecated(product, consumers)
    assert event["title"] == "shop/a stops on 2026-12-01" and event["domain"] == "sales"
    assert event["detail"].startswith("Read by bi (?), crm (orders). ")
    assert event["to"] == {"people": ["kim@contoso.com"], "roles": ["project:bi", "project:crm"]}
    assert event["dedupe"] == "product.deprecated:shop/a:2026-12-01"


def test_a_deprecation_falls_back_to_the_domain_name_and_platform_admins():
    event = notices.product_deprecated({"id": "p", "domain": "hr"}, [{"consumer_project": "has space"}])
    assert event["domain"] == "hr" and event["title"] == "p" and event["to"]["roles"] == ["platform_admin"]


def test_a_deprecation_read_by_nobody_else_or_without_an_id_says_nothing():
    assert notices.product_deprecated({"id": "p", "project": "shop"}, [{"consumer_project": "shop"}]) is None
    assert notices.product_deprecated({"id": "p", "project": "shop"}, [{"alias": "x"}]) is None
    assert notices.product_deprecated({"project": "shop"}, [{"consumer_project": "crm"}]) is None


def test_an_incidents_reach_is_posted_whole():
    found = {"people": [{"upn": "b@contoso.com"}, {"upn": "a@contoso.com"}],
             "products": [{"name": "Orders", "domain_id": "sales"}, {"name": "Stock", "domain_id": "sales"}]}
    event = notices.incident_impact({"id": "i1", "name": "Late load", "env": "prod"}, found)
    digest = hashlib.sha1(b"a@contoso.com|b@contoso.com", usedforsecurity=False).hexdigest()[:12]
    assert event == {"kind": "incident.impact", "id": "i1", "title": "Late load", "at": event["at"], "env": "prod",
                     "severity": "warn", "domain": "sales", "detail": "Data products affected: Orders, Stock.",
                     "to": {"people": ["a@contoso.com", "b@contoso.com"], "roles": []},
                     "open": {"view": "incident", "tab": "detail", "target": "i1"},
                     "dedupe": f"incident.impact:i1:{digest}"}


def test_an_incident_across_domains_or_without_products_has_neither():
    found = {"people": [], "products": [{"name": "A", "domain_id": "x"}, {"name": "B", "domain_id": "y"}]}
    assert notices.incident_impact({"id": "i1"}, found)["domain"] == ""
    event = notices.incident_impact({"id": "i1", "title": "T"}, {"people": [], "products": []})
    assert event["detail"] == "" and event["title"] == "T" and event["env"] == ""
    assert notices.incident_impact({"id": "i1"}, {"people": [], "products": []})["title"] == "Incident"


def test_an_exception_ending_names_its_place_and_goes_to_its_team():
    exc = {"id": "e1", "policy_id": "pol", "until": "2026-12-01", "scope": "domain", "target": "hr"}
    event = notices.exception_ending(exc, 3)
    assert event["title"] == "Exception to pol for hr ends in 3 days"
    assert event["domain"] == "hr" and event["to"] == {"people": [], "roles": ["domain_owner:hr"]}
    other = notices.exception_ending(exc | {"scope": "workspace", "target": "ok"}, 1)
    assert other["to"] == {"people": [], "roles": ["requester"]} and other["title"].endswith("ends tomorrow")
