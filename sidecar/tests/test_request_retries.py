"""Approving a request that makes something in Fabric, when a call fails partway (#1039).

Each step of an approval (a Fabric call, or the platform recording progress or the decision) is
failed once, two ways: before it does anything (a 429 or a refused call), and after it has done its
work but before the answer arrives (a timeout). Either way the request is still waiting, and
approving it again finishes it without making anything twice. Fabric and the platform are faked
here, as in test_requests_core_four; no tenant is called."""

import pytest
import requests

from fabric_desktop.central import core, requests_service
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service
from test_requests_core_four import ADMIN, CAP, DEV, GROUP, WS, _same_day, as_user, ask_workspace, svc, who  # noqa: F401

LEE = "lee@contoso.com"


def fail_once(obj, name, when):
    """``obj.name`` fails on its next call: ``before`` doing anything, or ``after`` doing it."""
    real = getattr(obj, name)
    state = {"armed": True}

    def call(*args, **kwargs):
        if not state["armed"]:
            return real(*args, **kwargs)
        state["armed"] = False
        if when == "before":
            raise FabricError(f"{name} failed with 429", status=429)
        real(*args, **kwargs)
        raise requests.Timeout(f"{name} timed out")

    return call


def fail_platform_once(monkeypatch, op, when, nth=1):
    """The platform's ``op`` fails on its nth call."""
    real, seen = LocalPlatform.call, {"n": 0}

    def call(self, name, **kwargs):
        if name == op:
            seen["n"] += 1
            if seen["n"] == nth:
                if when == "before":
                    raise requests.HTTPError(f"UDF_Platform couldn't be reached to {op}: Fabric answered 429")
                real(self, name, **kwargs)
                raise requests.Timeout(f"{op} timed out")
        return real(self, name, **kwargs)

    monkeypatch.setattr(LocalPlatform, "call", call)


def approve_until_done(svc, rid):
    """Approve, as an approver would after an error: the first try fails, the second finishes."""
    with pytest.raises((requests_service.StepError, requests.RequestException)):
        svc.platform_decide(rid, True)
    assert [r["id"] for r in svc.platform_requests("waiting")] == [rid], "still waiting after the failure"
    return svc.platform_decide(rid, True)


def made_once(svc):
    calls = [c[0] for c in svc.api.calls]
    assert calls.count("create_workspace") == 1 and len(svc.api.workspaces) == 1
    ws = svc.api.workspaces[0]["id"]
    admins = [r["principal"]["id"] for r in svc.api.roles[ws] if r["role"] == "Admin"]
    assert sorted(admins) == sorted(["me", GROUP, LEE])  # the creator, then each admin asked for, once


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", ["create_workspace", "add_role_assignment", "add_by_email"])
def test_a_workspace_approval_failing_at_a_fabric_step_finishes_on_retry_making_each_thing_once(svc, who, step, when):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    setattr(svc.api, step, fail_once(svc.api, step, when))
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    made_once(svc)


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_retry_failing_to_put_the_workspace_back_on_its_capacity_finishes_on_the_next(svc, who, when):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    svc.api.add_role_assignment = fail_once(svc.api, "add_role_assignment", "before")
    with pytest.raises(requests_service.StepError):
        svc.platform_decide(req["id"], True)
    svc.api.workspaces[0]["capacityId"] = "moved"  # someone moved it before the retry
    svc.api.assign_to_capacity = fail_once(svc.api, "assign_to_capacity", when)
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    assert svc.api.workspaces[0]["capacityId"] == CAP
    made_once(svc)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("nth", [1, 2, 3], ids=["workspace id", "first admin", "second admin"])
def test_a_workspace_approval_failing_to_record_progress_finishes_on_retry_making_each_thing_once(
        svc, who, monkeypatch, nth, when):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "record_request_progress", when, nth)
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    made_once(svc)


def test_a_workspace_approval_whose_decision_is_not_recorded_stays_waiting_and_finishes_on_retry(svc, who, monkeypatch):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "before")
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    made_once(svc)


def test_a_decision_recorded_but_not_answered_says_so_on_retry_and_is_recorded_once(svc, who, monkeypatch):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "after")
    with pytest.raises(requests.Timeout):
        svc.platform_decide(req["id"], True)
    with pytest.raises(core.PlatformError, match="That request was already approved"):
        svc.platform_decide(req["id"], True)
    assert [r["status"] for r in svc.platform_requests()] == ["approved"]
    decided = [e for e in svc._catalog().call("audit", limit=50) if e["action"] == "approved workspace"]
    assert len(decided) == 1
    made_once(svc)


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("existing", [None, "Viewer"], ids=["new", "role changed"])
def test_a_workspace_access_approval_failing_at_fabric_gives_the_role_once(svc, who, when, existing):
    as_user(who, DEV)
    if existing:
        svc.api.roles[WS] = [{"id": "d1", "role": existing, "principal": {
            "id": DEV, "type": "User", "userDetails": {"userPrincipalName": DEV}}}]
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    step = "update_role_assignment" if existing else "add_by_email"
    setattr(svc.api, step, fail_once(svc.api, step, when))
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    mine = [r for r in svc.api.roles[WS] if r["principal"]["id"] == DEV]
    assert [r["role"] for r in mine] == ["Contributor"]


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_workspace_access_approval_failing_to_record_progress_gives_the_role_once(svc, who, monkeypatch, when):
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "role": "Contributor"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "record_request_progress", when)
    assert approve_until_done(svc, req["id"])["status"] == "approved"
    assert [r["role"] for r in svc.api.roles[WS] if r["principal"]["id"] == DEV] == ["Contributor"]


class FabricDomains:
    """Fabric's admin domains API, which refuses a second domain of the same name with a 409."""

    def __init__(self):
        self.created, self.domains = [], []

    def create_admin_domain(self, name, description="", parent_id=""):
        if any(d["displayName"].lower() == name.lower() for d in self.domains):
            raise FabricError(f"POST /admin/domains failed with 409: {name} already exists", status=409)
        self.created.append((name, parent_id))
        made = {"id": f"f-{name.lower()}", "displayName": name, "description": description,
                "parentDomainId": parent_id}
        self.domains.append(made)
        return dict(made)

    def list_admin_domains(self):
        return [dict(d) for d in self.domains]


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_domain_approval_failing_at_fabric_never_makes_the_domain_twice(svc, who, monkeypatch, when):
    fabric = FabricDomains()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fabric)
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    fabric.create_admin_domain = fail_once(fabric, "create_admin_domain", when)
    with pytest.raises(requests_service.StepError, match="Create the domain HR in Fabric failed"):
        svc.platform_decide(req["id"], True)
    # Fabric made it but the answer was lost ("after"): approving again finds the domain by this
    # request's marker and keeps it (#1042), so either way Fabric holds one HR.
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    assert fabric.created == [("HR", "")]
    assert [d["fabric_domain"] for d in svc.platform_domains() if d["name"] == "HR"] == ["f-hr"]


# --------------------------------------------------------------------------- the workspace's description (#1069)


def test_a_workspace_with_a_long_reason_keeps_its_marker_and_is_found_again(svc, who, monkeypatch):
    """Fabric takes a workspace description of at most 4000 characters; the marker must survive the cut,
    so approving again after a lost answer finds the workspace."""
    real, state = svc.api.create_workspace, {"armed": True}

    def fabric_limit_then_lost(name, capacity_id="", description=""):
        if len(description) > 4000:
            raise FabricError("400 The description is longer than 4000 characters", status=400)
        out = real(name, capacity_id, description)
        if state.pop("armed", False):
            raise requests.Timeout("create_workspace timed out")
        return out
    monkeypatch.setattr(svc.api, "create_workspace", fabric_limit_then_lost)
    as_user(who, DEV)
    other = ask_workspace(svc, name="Other - Dev")
    req = svc.platform_request("workspace", "Sales Forecasting - Dev",
                               "Forecast models. " * 300 + f"(kratosforge request {other['id']})",
                               {"name": "Sales Forecasting - Dev", "capacity_id": CAP, "capacity_name": "Shared F64",
                                "domain_id": "sales", "admins": [GROUP, "Lee@Contoso.com"]})
    as_user(who, ADMIN)
    with pytest.raises(requests_service.StepError, match="Create the workspace"):
        svc.platform_decide(req["id"], True)
    assert req["id"] in [r["id"] for r in svc.platform_requests("waiting")]
    assert svc.platform_decide(req["id"], True)["status"] == "approved"
    made_once(svc)
    description = svc.api.workspaces[0]["description"]
    assert len(description) <= 4000 and description.endswith(f" (KratosForge request {req['id']})")
    assert other["id"] not in description
