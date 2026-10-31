"""Governance's platform notices: requests and shortcut health reach Teams through the outbox."""

import pytest

from fabric_desktop import app_settings
from fabric_desktop import governance_notices as notices
from fabric_desktop import platform_notify_service as pns
from fabric_desktop.service import Service
from fabric_desktop.workloads.platform_notify import validate

HOOK = "https://contoso.webhook.office.com/webhookb2/abc/IncomingWebhook/def/ghi"
PRODUCTS = [{"id": "shop/c360", "owner": "ana@contoso.com; Sales team", "domain_id": "sales"}]


def req(**extra):
    return {"id": "r1", "kind": "access", "target": "shop/c360", "reason": "Because I need the Q3 numbers",
            "detail": {"note": "secret plan"}, "requested_by": "lee@contoso.com", "requested_at": "2026-10-21T10:00:00+00:00",
            "status": "waiting", "decided_at": ""} | extra


def test_a_waiting_request_goes_to_its_deciders_without_its_reason():
    event = validate(notices.request_waiting(req(), PRODUCTS))
    assert event["to"] == {"people": ["ana@contoso.com"], "roles": ["platform_admin"]}  # names that aren't UPNs drop
    assert event["domain"] == "sales" and event["title"] == "Access to shop/c360" and event["dedupe"] == "request.waiting:r1"
    assert "Q3" not in str(event) and "secret plan" not in str(event)


def test_a_decision_goes_to_whoever_asked_and_a_withdrawal_to_the_deciders():
    decided = validate(notices.request_closed(req(status="declined", decided_at="2026-10-21T11:00:00+00:00"), PRODUCTS))
    assert decided["kind"] == "request.decided" and decided["to"] == {"people": ["lee@contoso.com"], "roles": []}
    assert decided["title"] == "Access to shop/c360 (declined)"
    anon = validate(notices.request_closed(req(status="approved", requested_by=""), PRODUCTS))
    assert anon["to"] == {"people": [], "roles": ["requester"]}
    gone = validate(notices.request_closed(req(status="withdrawn"), PRODUCTS))
    assert gone["kind"] == "request.withdrawn" and gone["to"]["roles"] == ["platform_admin"]
    assert notices.request_closed(req(), PRODUCTS) is None  # still waiting: nothing to say


@pytest.mark.parametrize("kind, made, left", [
    ("workspace", {"workspace_id": "w1", "admins": ["lee@contoso.com"]}, "; the workspace it made is still in Fabric"),
    ("workspace_access", {"assigned": True, "change": "gave", "from": ""}, "; the role it gave stays in the workspace"),
    ("workspace_access", {"assigned": True, "change": "raised", "from": "Viewer"},
     "; the role it raised stays in the workspace"),
    ("workspace_access", {"assigned": True, "change": "same", "from": "Member"},
     "; the role may be from it: check before removing"),
    ("workspace_access", {"assigned": True}, "; the role may be from it: check before removing"),
    ("workspace_access", {"assigned": True, "change": "kept", "from": "Admin"}, ""),
    ("source", {"copy_job_id": "j1", "copy_job_name": "CJ_Landing_crm", "schedule_id": "s1"},
     "; its Copy job is still in the landing workspace, on its schedule"),
    ("domain", {"domain_id": "hr", "fabric_domain_id": "f-hr"}, "; the domain it made is still there"),
    ("shortcut", {"shortcut_id": "lh-1|Tables/dbo/x"}, "; the shortcut it made is still in OneLake"),
    ("shortcut", {"shortcut_id": "lh-1|Tables/dbo/x", "found": True}, "; the shortcut may be from it: check before removing"),
    ("workspace", {}, ""),
], ids=["workspace", "gave", "raised", "same", "older", "kept", "copy job", "domain", "shortcut", "found shortcut", "nothing"])
def test_a_closed_request_whose_approval_made_something_says_it_is_still_there_without_names(kind, made, left):
    r = req(kind=kind, target="Sales Prod", detail={"made": made, "principal": {"email": "kim@contoso.com"}})
    gone = validate(notices.request_closed(r | {"status": "withdrawn"}, PRODUCTS))
    assert gone["title"] == f"{notices._title(r)}{left}"
    declined = validate(notices.request_closed(r | {"status": "declined"}, PRODUCTS))
    assert declined["title"] == f"{notices._title(r)} (declined){left}"
    approved = validate(notices.request_closed(r | {"status": "approved"}, PRODUCTS))
    assert approved["title"] == f"{notices._title(r)} (approved)"  # approved: what it made is what was asked
    for event in (gone, declined):
        assert "contoso" not in str(event["title"]) and "CJ_Landing" not in str(event)
        assert "kim@" not in str(event) and "lee@" not in str(event["title"])


def test_shortcut_breaks_and_fixes_become_events_and_a_webhook_in_a_detail_is_redacted():
    changes = [{"kind": "broke", "shortcut_id": "lh-1|Tables/dbo/c", "name": "c", "workspace_name": "Sales Dev",
                "lakehouse_name": "LH_Silver", "at": "2026-10-21T10:00:00+00:00", "note": f"OneLake answered 404 via {HOOK}"},
               {"kind": "fixed", "shortcut_id": "lh-1|Tables/dbo/c", "name": "c", "at": "2026-10-21T11:00:00+00:00"},
               {"kind": "repointed", "shortcut_id": "lh-1|Tables/dbo/d"}]
    broken, fixed = [validate(e) for e in notices.shortcut_events(changes)]
    assert broken["kind"] == "shortcut.broken" and broken["title"] == "c in Sales Dev/LH_Silver" and broken["severity"] == "warn"
    assert "webhookb2" not in broken["detail"] and "404" in broken["detail"]
    assert fixed["kind"] == "shortcut.fixed" and fixed["open"] == {"view": "shortcuts", "tab": "health", "target": "lh-1|Tables/dbo/c"}


class Posts(list):
    def __call__(self, url, title, lines):
        self.append((url, title, lines))


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    posts = Posts()
    monkeypatch.setattr(pns, "post_teams", posts)
    s = Service(root=tmp_path / "app")
    s.posts = posts
    return s


def test_requesting_deciding_and_withdrawing_post_once_each_when_a_route_is_set(svc):
    first = svc.platform_request("key_table", "customer", "New customers", {"business_key": "customer_id"})
    assert svc.posts == [] and "problems" not in first  # no route yet: nothing to post, nothing goes wrong
    svc.platform_notify_save([{"name": "default", "url": HOOK}])
    svc.platform_withdraw(first["id"])
    assert [p[1] for p in svc.posts] == ["Withdrawn: New key table customer"]
    out = svc.platform_request("key_table", "supplier", "Suppliers", {"business_key": "supplier_id"})
    svc.platform_notify([notices.request_waiting(out, [])])  # the same wait again isn't posted twice
    svc.platform_decide(out["id"], True, "ok")
    assert [p[1] for p in svc.posts][1:] == ["Waiting on you: New key table supplier",
                                             "Decided: New key table supplier (approved)"]
    assert all("Suppliers" not in " ".join(p[2]) for p in svc.posts)  # the reason stays in the app


def test_a_notice_that_fails_never_undoes_the_request(svc, monkeypatch):
    svc.platform_notify_save([{"name": "default", "url": HOOK}])

    def broken(self, events):
        raise RuntimeError(f"Teams said no to {HOOK}")
    monkeypatch.setattr(Service, "platform_notify", broken)
    out = svc.platform_request("key_table", "customer", "", {"business_key": "customer_id"})
    assert out["status"] == "waiting" and svc.platform_requests("waiting")[0]["id"] == out["id"]
    assert out["problems"][0].startswith("The Teams notice wasn't sent") and "webhookb2" not in out["problems"][0]


def test_a_notice_that_cannot_be_built_never_fails_the_decision(svc, monkeypatch):
    def bad(req, products):
        raise KeyError("owner")
    monkeypatch.setattr(notices, "request_closed", bad)
    out = svc.platform_request("key_table", "customer", "", {"business_key": "customer_id"})
    decided = svc.platform_decide(out["id"], False, "no")
    assert decided["status"] == "declined" and svc.platform_requests("declined")[0]["id"] == out["id"]
    assert decided["problems"][0].startswith("The Teams notice wasn't sent")
