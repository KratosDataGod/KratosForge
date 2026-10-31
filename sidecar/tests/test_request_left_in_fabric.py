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
    assert out["next"] == [f"{DEV} keeps the Contributor role in Sales Prod that approving this request gave: remove "
                           "it in the workspace's Manage access in Fabric if it isn't wanted"]
    assert len(svc.api.calls) == calls
    assert [r["role"] for r in svc.api.roles[WS] if r["principal"]["id"] == DEV] == ["Contributor"]
    [row] = audited(svc, "withdrew workspace_access")
    assert row["detail"]["left"] == {"assigned": True, "change": "gave"}


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
    assert left == {"workspace": {"workspace_id": "w1"}, "workspace_access": {"assigned": True},  # no change recorded
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
        "The group Sales Engineers has the Contributor role or a higher one in Sales Prod: they had it before this "
        "request, or approving it gave it. Check which before removing it in the workspace's Manage access in Fabric"]


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


def half_given(svc, who, monkeypatch, have):
    """A workspace access approval whose role step ran but whose decision wasn't recorded."""
    if have:
        svc.api.roles[WS] = [{"id": "r1", "role": have, "principal": {
            "id": DEV, "type": "User", "userDetails": {"userPrincipalName": DEV}}}]
    as_user(who, DEV)
    req = svc.platform_request("workspace_access", "", "", {"workspace_id": WS, "workspace_name": "Sales Prod",
                                                             "role": "Contributor"})
    as_user(who, ADMIN)
    fail_platform_once(monkeypatch, "decide_request", "before")
    with pytest.raises(Exception, match="decide_request"):
        svc.platform_decide(req["id"], True)
    return req


@pytest.mark.parametrize("have, change, line", [
    (None, "gave", f"{DEV} keeps the Contributor role in Sales Prod that approving this request gave: remove it in "
                   "the workspace's Manage access in Fabric if it isn't wanted"),
    ("Viewer", "raised", f"{DEV} keeps the Contributor role in Sales Prod that approving this request raised from "
                         "Viewer: set it back in the workspace's Manage access in Fabric if it isn't wanted"),
    ("Contributor", "same", f"{DEV} has the Contributor role in Sales Prod: they had it before this request, or "
                            "approving it gave it. Check which before removing it in the workspace's Manage access in Fabric"),
    ("Admin", "kept", None),
], ids=["gave", "raised", "same", "kept"])
def test_declining_after_a_failed_decision_says_exactly_what_changed(svc, who, monkeypatch, have, change, line):
    req = half_given(svc, who, monkeypatch, have)
    made = svc.platform_requests("waiting")[0]["detail"]["made"]
    assert made == {"assigned": True, "change": change, "from": have or ""}
    out = svc.platform_decide(req["id"], False)
    assert out.get("next") == ([line] if line else None)
    [row] = audited(svc, "declined workspace_access")
    if change == "kept":  # a higher role they had: approving changed nothing, so nothing was left
        assert "left" not in row["detail"]
    else:
        assert row["detail"]["left"] == {"assigned": True, "change": change, **({"from": have} if have else {})}
    [recorded] = audited(svc, "made for workspace_access request")
    assert recorded["detail"]["made"]["change"] == change


def test_a_retry_after_the_role_was_given_still_records_that_it_was_given(svc, who, monkeypatch):
    req = half_given(svc, who, monkeypatch, None)
    out = svc.platform_decide(req["id"], True)  # finds Contributor, the role this approval gave
    assert out["status"] == "approved" and out["detail"]["made"]["change"] == "gave"


def test_a_request_recorded_before_the_change_was_kept_says_to_check_before_removing():
    old = {"kind": "workspace_access", "detail": {"role": "Member", "workspace_name": "Sales Prod",
                                                  "principal": {"email": DEV}, "made": {"assigned": True}}}
    assert left_in_fabric(old) == [f"{DEV} has the Member role or a higher one in Sales Prod: they had it before "
                                   "this request, or approving it gave it. Check which before removing it in the "
                                   "workspace's Manage access in Fabric"]


def test_withdrawing_a_half_made_request_tells_the_approvers_on_teams(svc, who, monkeypatch, tmp_path):
    """The person who withdraws it usually can't delete a workspace; the approvers can (#1261)."""
    from fabric_desktop import app_settings
    from fabric_desktop import platform_notify_service as pns
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = []
    monkeypatch.setattr(pns, "post_teams", lambda url, title, lines: posts.append((title, lines)))
    req = half_made_workspace(svc, who)
    svc.platform_notify_save([{"name": "default", "url": "https://contoso.webhook.office.com/webhookb2/a/IncomingWebhook/b/c"}])
    posts.clear()
    as_user(who, DEV)
    svc.platform_withdraw(req["id"])
    assert [p[0] for p in posts] == ["Withdrawn: New workspace Sales Forecasting - Dev; the workspace it made is still "
                                     "in Fabric"]
    assert "Lee" not in str(posts) and "Forecast models" not in str(posts)  # no admins' names, no reason
