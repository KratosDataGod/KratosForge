"""Approving a domain request again keeps the Fabric domain that request already made (#1042).

Approving a domain request creates it in Fabric with this request's marker in its description, then
saves it in the platform. If the answer is lost (a timeout, or the platform's save failing), the
next approval meets Fabric's 409, finds the domain carrying exactly this request's marker, and keeps
it. A look-alike (same name with no marker, another request's marker, the marker in the name, or
another parent) still gets "Choose Import from Fabric". Fabric and the platform are faked; no
tenant is called."""

import pytest

from fabric_desktop.central import requests_service
from fabric_desktop.service import Service
from test_request_retries import FabricDomains, fail_once, fail_platform_once
from test_requests_core_four import ADMIN, DEV, as_user, svc, who  # noqa: F401

KEPT = "HR already existed in Fabric from this request, so it was kept"


@pytest.fixture
def fabric(monkeypatch):
    f = FabricDomains()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: f)
    return f


def ask(svc, who, name="HR", **detail):
    as_user(who, DEV)
    req = svc.platform_request("domain", name, "", {"name": name} | detail)
    as_user(who, ADMIN)
    return req


def marker(req):
    return f"(KratosForge request {req['id']})"


def waiting(svc):
    return [r["id"] for r in svc.platform_requests("waiting")]


def test_the_domain_made_in_fabric_carries_its_request_s_marker(svc, who, fabric):
    req = ask(svc, who, description="People data")
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and "next" not in out
    assert fabric.domains[0]["description"] == f"People data {marker(req)}"
    # the platform keeps the description as asked, without the marker
    assert [d["description"] for d in svc.platform_domains() if d["name"] == "HR"] == ["People data"]


def test_a_domain_whose_answer_was_lost_is_kept_on_the_next_approval(svc, who, fabric):
    first = svc.platform_decide(ask(svc, who, name="Legal")["id"], True)  # a first-time success, to compare
    req = ask(svc, who)
    fabric.create_admin_domain = fail_once(fabric, "create_admin_domain", "after")
    with pytest.raises(requests_service.StepError, match="Create the domain HR in Fabric failed"):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]]
    out = svc.platform_decide(req["id"], True)
    assert out.pop("next") == [KEPT]
    assert set(out) == set(first) and out["status"] == first["status"] == "approved" and out["made"] == first["made"]
    assert [n for n, _ in fabric.created] == ["Legal", "HR"]  # Fabric holds one HR
    assert {d["name"]: d["fabric_domain"] for d in svc.platform_domains()}["HR"] == "f-hr"
    assert waiting(svc) == []


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_domain_the_platform_failed_to_save_is_kept_on_the_next_approval(svc, who, fabric, monkeypatch, when):
    req = ask(svc, who)
    fail_platform_once(monkeypatch, "save_domain", "before")
    with pytest.raises(requests_service.StepError, match="created in Fabric but not saved in the platform"):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]]
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT]
    assert fabric.created == [("HR", "")]


def test_a_subdomain_whose_answer_was_lost_is_kept_under_its_parent(svc, who, fabric):
    svc.platform_decide(ask(svc, who, name="Ops")["id"], True)
    req = ask(svc, who, parent_id="ops")
    fabric.create_admin_domain = fail_once(fabric, "create_admin_domain", "after")
    with pytest.raises(requests_service.StepError):
        svc.platform_decide(req["id"], True)
    assert svc.platform_decide(req["id"], True)["next"] == [KEPT]
    assert fabric.created == [("Ops", ""), ("HR", "f-ops")]


def _look_alike(fabric, svc, req, domain):
    fabric.domains.append({"id": "f-other", "displayName": "HR", "description": "", "parentDomainId": ""} | domain)
    before = [dict(d) for d in fabric.domains]
    with pytest.raises(requests_service.StepError, match="Fabric already has a domain called HR. Choose Import from Fabric"):
        svc.platform_decide(req["id"], True)
    assert req["id"] in waiting(svc)
    assert fabric.domains == before and fabric.created == []
    assert "HR" not in {d["name"] for d in svc.platform_domains()}


def test_a_domain_of_the_same_name_without_a_marker_is_not_kept(svc, who, fabric):
    req = ask(svc, who)
    _look_alike(fabric, svc, req, {"description": "Made by hand"})


def test_a_domain_with_another_request_s_marker_is_not_kept(svc, who, fabric):
    other = ask(svc, who, name="Finance")
    req = ask(svc, who)
    _look_alike(fabric, svc, req, {"description": marker(other)})


@pytest.mark.parametrize("near", ["(KratosForge request {id}0)", "(KratosForge request {head})", "KratosForge request {id}"])
def test_a_marker_that_only_looks_like_this_request_s_is_not_kept(svc, who, fabric, near):
    req = ask(svc, who)
    _look_alike(fabric, svc, req, {"description": near.format(id=req["id"], head=req["id"][:-1])})


def test_a_domain_with_the_marker_in_its_name_is_not_kept(svc, who, fabric):
    req = ask(svc, who)
    # Fabric's 409 is for HR; the domain carrying the marker text is named differently
    fabric.domains.append({"id": "f-x", "displayName": f"HR {marker(req)}", "description": "", "parentDomainId": ""})
    _look_alike(fabric, svc, req, {"description": ""})


def test_a_domain_with_the_marker_under_another_parent_is_not_kept(svc, who, fabric):
    req = ask(svc, who)
    _look_alike(fabric, svc, req, {"description": marker(req), "parentDomainId": "f-somewhere-else"})


def test_new_domain_in_the_app_never_keeps_a_fabric_domain(svc, who, fabric):
    req = ask(svc, who)
    fabric.domains.append({"id": "f-hr", "displayName": "HR", "description": marker(req), "parentDomainId": ""})
    with pytest.raises(ValueError, match="Choose Import from Fabric"):
        svc.platform_create_domain({"name": "HR"})
    assert "HR" not in {d["name"] for d in svc.platform_domains()}
