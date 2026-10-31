"""Declining or withdrawing a request whose approval made something in Fabric before a later step
failed (#1195). Nothing in Fabric is deleted; the answer says what is still there, and the audit row
records it. Fabric and the platform are faked, as in test_request_retries; no tenant is called."""

import json

import pytest

from fabric_desktop.central import requests_service
from fabric_desktop.central.requests_service import left_in_fabric
from fabric_desktop.service import Service
from test_request_retries import FabricDomains, fail_once, fail_platform_once
from test_requests_core_four import ADMIN, DEV, WS, _same_day, as_user, ask_workspace, svc, who  # noqa: F401


def audited(svc, action):
    rows = [e for e in svc._catalog().call("audit", limit=50) if e["action"] == action]
    return [dict(e, detail=json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]) for e in rows]


def half_made_workspace(svc, who):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    as_user(who, ADMIN)
    svc.api.add_role_assignment = fail_once(svc.api, "add_role_assignment", "before")
    with pytest.raises(requests_service.StepError):
        svc.platform_decide(req["id"], True)
    assert len(svc.api.workspaces) == 1
    return req


def test_declining_a_request_whose_workspace_was_made_says_it_is_still_in_fabric(svc, who):
    req = half_made_workspace(svc, who)
    calls = len(svc.api.calls)
    out = svc.platform_decide(req["id"], False, "Not this quarter")
    assert out["status"] == "declined"
    assert out["next"] == ["The workspace Sales Forecasting - Dev, made by this request, is still in Fabric on its "
                           "capacity: delete it there if it isn't wanted"]
    assert len(svc.api.calls) == calls and len(svc.api.workspaces) == 1  # nothing in Fabric changed
    [row] = audited(svc, "declined workspace")
    assert row["detail"]["left"] == {"workspace_id": svc.api.workspaces[0]["id"]}


def test_withdrawing_a_request_whose_role_was_given_says_the_role_stays(svc, who, monkeypatch):
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "workspace_name": "Sales Prod",
                                                             "role": "Contributor"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "before")
    with pytest.raises(Exception, match="decide_request"):
        svc.platform_decide(req["id"], True)
    as_user(who, DEV)
    calls = len(svc.api.calls)
    out = svc.platform_withdraw(req["id"])
    assert out["status"] == "withdrawn"
    [line] = out["next"]
    assert line.startswith(f"{DEV} keeps the Contributor role (or the higher one they had) in Sales Prod")
    assert "Manage access" in line
    assert len(svc.api.calls) == calls
    assert [r["role"] for r in svc.api.roles[WS] if r["principal"]["id"] == DEV] == ["Contributor"]
    [row] = audited(svc, "withdrew workspace_access")
    assert row["detail"]["left"] == {"assigned": True}


@pytest.mark.parametrize("close", ["decline", "withdraw"])
def test_a_request_with_nothing_made_closes_exactly_as_before(svc, who, close):
    as_user(who, DEV)
    req = ask_workspace(svc, domain_id="")
    if close == "decline":
        as_user(who, ADMIN)
        out = svc.platform_decide(req["id"], False)
    else:
        out = svc.platform_withdraw(req["id"])
    assert "next" not in out and svc.api.workspaces == []
    [row] = audited(svc, "declined workspace" if close == "decline" else "withdrew workspace")
    assert "left" not in row["detail"]


def test_the_audit_row_keeps_only_the_ids_of_what_was_made():
    """Admins are recorded by sign-in name, and a workspace's capacity or a Copy job's landing workspace
    is where things went, not something made, so none of them is in the row."""
    from fabric_desktop.central.core import _left
    made = {"workspace_id": "w1", "capacity_id": "c1", "admins": ["lee@contoso.com"], "copy_job_id": "j1",
            "copy_job_name": "CJ_Landing_crm", "schedule_id": "s1", "assigned": True, "domain_id": "hr",
            "fabric_domain_id": "f-hr"}
    left = {kind: _left({"kind": kind, "detail": {"made": made}})
            for kind in ("workspace", "workspace_access", "source", "domain", "key_table")}
    assert left == {"workspace": {"workspace_id": "w1"}, "workspace_access": {"assigned": True},
                    "source": {"copy_job_id": "j1", "schedule_id": "s1"},
                    "domain": {"domain_id": "hr", "fabric_domain_id": "f-hr"}, "key_table": {}}


def test_a_copy_job_left_behind_says_its_schedule_still_runs():
    req = {"kind": "source", "target": "crm", "detail": {"copy": {}, "made": {
        "copy_job_id": "j1", "workspace_id": "w1", "copy_job_name": "CJ_Landing_crm", "schedule_id": "s1"}}}
    assert left_in_fabric(req) == ["The Copy job CJ_Landing_crm, made by this request, is still in the landing "
                                   "workspace and its daily schedule still runs it: delete it there if it isn't wanted"]
    del req["detail"]["made"]["schedule_id"]
    assert "schedule" not in left_in_fabric(req)[0]


@pytest.mark.parametrize("kind", ["workspace", "workspace_access", "source", "domain", "key_table"])
def test_nothing_made_says_nothing(kind):
    assert left_in_fabric({"kind": kind, "detail": {"made": {}}}) == []
    assert left_in_fabric({"kind": kind, "detail": {}}) == []


def test_the_sentences_match_the_decline_confirm():
    """platform.js leftInFabric says the same before the click (ui.test.cjs checks these strings too)."""
    assert left_in_fabric({"kind": "workspace_access", "detail": {
        "role": "Contributor", "workspace_name": "Sales Prod", "principal": {"type": "Group", "name": "Sales Engineers"},
        "made": {"assigned": True}}}) == [
        "The group Sales Engineers keeps the Contributor role (or the higher one they had) in Sales Prod from approving "
        "this request: remove it in the workspace's Manage access in Fabric if it isn't wanted"]


@pytest.mark.parametrize("parent, in_fabric", [("", True), ("ops", False)], ids=["in fabric", "platform only"])
def test_declining_a_domain_request_whose_domain_was_saved_says_where_it_is(svc, who, monkeypatch, parent, in_fabric):
    fabric = FabricDomains()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fabric)
    svc.platform_save_domain({"name": "Ops"})
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR", "parent_id": parent})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "before")
    with pytest.raises(Exception, match="decide_request"):
        svc.platform_decide(req["id"], True)
    created = list(fabric.created)
    out = svc.platform_decide(req["id"], False)
    where = "in Fabric and the platform: delete it in Fabric's admin portal, then on Data mesh > Domains," \
        if in_fabric else "in the platform: delete it on Data mesh > Domains"
    assert out["next"] == [f"The domain HR, made by this request, is still {where} if it isn't wanted"]
    assert fabric.created == created == ([("HR", "")] if in_fabric else [])  # nothing more in Fabric
    assert "hr" in {d["id"] for d in svc.platform_domains()}  # and nothing deleted
    [row] = audited(svc, "declined domain")
    assert row["detail"]["left"] == ({"domain_id": "hr", "fabric_domain_id": "f-hr"} if in_fabric else {"domain_id": "hr"})


def test_a_domain_approval_records_what_it_made(svc, who, monkeypatch):
    fabric = FabricDomains()
    monkeypatch.setattr(Service, "_fabric_admin", lambda self, write=False: fabric)
    as_user(who, DEV)
    req = svc.platform_request("domain", "HR", "", {"name": "HR"})
    as_user(who, ADMIN)
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["detail"]["made"] == {"domain_id": "hr", "fabric_domain_id": "f-hr"}
