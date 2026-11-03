"""Approving a shortcut request again finishes it when Fabric already has that shortcut (#1052).

Approving makes the shortcut in Fabric first, as the approver, then records it and approves. If the
answer to the create is lost (a timeout), or the approval after it fails, the next approval meets
Fabric's 409. A shortcut at that place pointing exactly where the request asks counts as made; one
pointing anywhere else keeps the request waiting. Fabric and the platform are faked; no tenant is
called."""

import pytest
import requests

from fabric_desktop import shortcuts
from fabric_desktop.central import core
from fabric_desktop.central.client import LocalPlatform
from fabric_desktop.fabric_api import FabricError
from test_shortcut_center import Auth, FakeApi, svc  # noqa: F401

SRC = {"type": "oneLake", "workspace_id": "ws-dev2", "item_id": "lh-gold-dev", "path": "Tables/dbo/supplier"}
WANTED = {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/supplier"}}
KEPT = "supplier was already in LH_Silver, pointing where this request asks, so it was kept"


@pytest.fixture
def api(svc):
    """Fabric's answer to a second shortcut at the same place: a 409, as the real API gives it."""
    real = svc.api.create_shortcut

    def create(ws, item, path, name, target):
        try:
            return real(ws, item, path, name, target)
        except RuntimeError as exc:
            if str(exc).startswith("409"):
                raise FabricError("A shortcut with that name already exists", code="ItemAlreadyExists", status=409) from exc
            raise
    svc.api.create_shortcut = create
    return svc.api


def ask(svc):
    return svc.shortcut_request("ws-dev", "lh-1", "Tables/dbo", "supplier", SRC, "Need suppliers", "LH_Silver", "Sales Dev")


def suppliers(api):
    return [s for s in api.items["lh-1"] if s["name"] == "supplier"]


def added(svc):
    return [c for c in svc.shortcut_changes()["changes"] if c["kind"] == "added" and c["name"] == "supplier"]


def waiting(svc):
    return [r["id"] for r in svc.platform_requests("waiting")]


def test_a_shortcut_whose_create_answer_was_lost_is_kept_on_the_next_approval(svc, api):
    req = ask(svc)
    real, state = api.create_shortcut, {"armed": True}

    def times_out(*args):
        real(*args)
        if state.pop("armed", False):
            raise requests.Timeout("create_shortcut timed out")
    api.create_shortcut = times_out
    with pytest.raises(requests.Timeout):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]] and len(suppliers(api)) == 1 and added(svc) == []
    out = svc.platform_decide(req["id"], True, "ok")
    assert out["status"] == "approved" and out["next"] == [KEPT]
    assert len(suppliers(api)) == 1
    assert [c["note"] for c in added(svc)] == ["Request from someone: Need suppliers"]  # recorded once, by the retry


def test_a_shortcut_whose_approval_failed_after_it_was_made_is_kept_with_one_history_entry(svc, api, monkeypatch):
    req = ask(svc)
    real, seen = LocalPlatform.call, {"n": 0}

    def call(self, name, **kwargs):
        if name == "decide_request":
            seen["n"] += 1
            if seen["n"] == 1:
                raise requests.HTTPError("UDF_Platform couldn't be reached to decide_request: Fabric answered 503")
        return real(self, name, **kwargs)
    monkeypatch.setattr(LocalPlatform, "call", call)
    with pytest.raises(requests.HTTPError):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]] and len(added(svc)) == 1
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT]
    assert len(suppliers(api)) == 1 and len(added(svc)) == 1


@pytest.mark.parametrize("there", [
    {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/customer"}},  # another table
    {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-other", "path": "Tables/dbo/supplier"}},  # another lakehouse
    {"oneLake": {"workspaceId": "ws-prod", "itemId": "lh-gold-dev", "path": "Tables/dbo/supplier"}},  # another workspace
    {"amazonS3": {"location": "https://bucket.s3.amazonaws.com", "subpath": "/Tables/dbo/supplier", "connectionId": "c1"}},
    {},  # a target Fabric didn't say
    {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/SUPPLIER"}},  # another case
], ids=["table", "lakehouse", "workspace", "s3", "none", "case"])
def test_a_shortcut_pointing_anywhere_else_keeps_the_request_waiting(svc, api, there):
    req = ask(svc)
    api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": there})
    before = [dict(s) for s in api.items["lh-1"]]
    with pytest.raises(core.PlatformError, match="already has a shortcut Tables/dbo/supplier, pointing at .*not where "
                                                 "this request asks"):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]] and api.items["lh-1"] == before and added(svc) == []


def test_a_409_with_nothing_there_is_raised_as_fabric_gave_it(svc, api):
    req = ask(svc)

    def conflict(*args):
        raise FabricError("Conflict", code="ItemAlreadyExists", status=409)
    api.create_shortcut = conflict
    with pytest.raises(FabricError, match="Conflict"):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]]


def test_another_fabric_error_is_raised_as_today(svc, api):
    req = ask(svc)

    def refused(*args):
        raise FabricError("Forbidden", status=403)
    api.create_shortcut = refused
    api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": WANTED})  # even with it there
    with pytest.raises(FabricError, match="Forbidden"):
        svc.platform_decide(req["id"], True)
    assert waiting(svc) == [req["id"]]


@pytest.mark.parametrize("a,b,same", [
    (WANTED, {"oneLake": {"workspaceId": "WS-DEV2", "itemId": "LH-GOLD-DEV", "path": "/Tables/dbo/supplier/"}}, True),
    (WANTED, {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/Supplier"}}, False),
    ({"amazonS3": {"location": "HTTPS://B.S3.amazonaws.com/Finance", "subpath": "/Payroll", "connectionId": "C1"}},
     {"amazonS3": {"location": "https://b.s3.amazonaws.com/Finance", "subpath": "Payroll/", "connectionId": "c1"}}, True),
    ({"amazonS3": {"location": "https://b.s3.amazonaws.com", "subpath": "/Finance/Payroll", "connectionId": "c1"}},
     {"amazonS3": {"location": "https://b.s3.amazonaws.com", "subpath": "/finance/payroll", "connectionId": "c1"}}, False),
    ({"adlsGen2": {"location": "https://acct.dfs.core.windows.net/Data", "subpath": "/x", "connectionId": "c1"}},
     {"adlsGen2": {"location": "https://acct.dfs.core.windows.net/data", "subpath": "/x", "connectionId": "c1"}}, False),
    (WANTED, {"oneLake": {"workspaceId": "ws-dev2", "itemId": "lh-gold-dev", "path": "Tables/dbo/supplier2"}}, False),
    ({"amazonS3": {"location": "https://b.s3.amazonaws.com/", "subpath": "/e", "connectionId": "c1"}},
     {"amazonS3": {"location": "https://b.s3.amazonaws.com", "subpath": "e", "connectionId": "c1"}}, True),
    ({"amazonS3": {"location": "https://b.s3.amazonaws.com", "subpath": "/e", "connectionId": "c1"}},
     {"amazonS3": {"location": "https://b.s3.amazonaws.com", "subpath": "/e", "connectionId": "c2"}}, False),
    ({"amazonS3": {"location": "https://b", "subpath": "/e", "connectionId": "c1"}},
     {"adlsGen2": {"location": "https://b", "subpath": "/e", "connectionId": "c1"}}, False),
    ({}, {}, False),
])
def test_same_target(a, b, same):
    assert shortcuts.same_target(a, b) is same


# --------------------------------------------------------------------------- declined after it was made (#1260)


def fail_decision_once(monkeypatch):
    real, seen = LocalPlatform.call, {"n": 0}

    def call(self, name, **kwargs):
        if name == "decide_request" and kwargs.get("approve"):
            seen["n"] += 1
            if seen["n"] == 1:
                raise requests.HTTPError("UDF_Platform couldn't be reached to decide_request: Fabric answered 503")
        return real(self, name, **kwargs)
    monkeypatch.setattr(LocalPlatform, "call", call)


LEFT = ("The shortcut supplier in LH_Silver, made by this request, is still in OneLake: remove it on "
        "OneLake > Shortcuts if it isn't wanted")


@pytest.mark.parametrize("close", ["decline", "withdraw"])
def test_a_shortcut_request_closed_after_its_shortcut_was_made_says_it_is_still_in_onelake(svc, api, monkeypatch, close):
    req = ask(svc)
    fail_decision_once(monkeypatch)
    with pytest.raises(requests.HTTPError):
        svc.platform_decide(req["id"], True)
    sid = core.shortcut_id("lh-1", "Tables/dbo/supplier")
    assert svc.platform_requests("waiting")[0]["detail"]["made"] == {"shortcut_id": sid}
    calls = [dict(s) for s in api.items["lh-1"]]
    out = svc.platform_decide(req["id"], False) if close == "decline" else svc.platform_withdraw(req["id"])
    assert out["next"] == [LEFT]
    assert api.items["lh-1"] == calls and len(suppliers(api)) == 1  # nothing removed
    action = "declined shortcut" if close == "decline" else "withdrew shortcut"
    [row] = [e for e in svc._catalog().call("audit", limit=50) if e["action"] == action]
    assert sid in row["detail"]


def test_a_retry_after_the_shortcut_was_recorded_keeps_it_and_approves(svc, api, monkeypatch):
    req = ask(svc)
    fail_decision_once(monkeypatch)
    with pytest.raises(requests.HTTPError):
        svc.platform_decide(req["id"], True)
    out = svc.platform_decide(req["id"], True)
    assert out["status"] == "approved" and out["next"] == [KEPT] and len(suppliers(api)) == 1


def test_a_shortcut_request_with_nothing_made_declines_as_before(svc, api):
    req = ask(svc)
    out = svc.platform_decide(req["id"], False)
    assert out["status"] == "declined" and "next" not in out and suppliers(api) == []


FOUND = ("The shortcut supplier in LH_Silver is in OneLake: it was there before this request, or approving it made it. "
         "Check which before removing it on OneLake > Shortcuts")


def test_a_shortcut_that_was_already_there_is_recorded_as_found_and_a_decline_says_to_check(svc, api, monkeypatch):
    """One made by hand before the request, pointing where it asks, may be someone else's: no removal advice."""
    req = ask(svc)
    api.items["lh-1"].append({"name": "supplier", "path": "Tables/dbo", "target": WANTED})
    fail_decision_once(monkeypatch)
    with pytest.raises(requests.HTTPError):
        svc.platform_decide(req["id"], True)
    sid = core.shortcut_id("lh-1", "Tables/dbo/supplier")
    assert svc.platform_requests("waiting")[0]["detail"]["made"] == {"shortcut_id": sid, "found": True}
    out = svc.platform_decide(req["id"], False)
    assert out["next"] == [FOUND] and len(suppliers(api)) == 1
    [row] = [e for e in svc._catalog().call("audit", limit=50) if e["action"] == "declined shortcut"]
    assert '"found": true' in row["detail"]


def test_a_shortcut_this_approval_logged_making_stays_made_by_it_on_a_retry(svc, api, monkeypatch):
    """The decision failed after the shortcut was made and logged: the retry's 409 finds that log, so it's ours."""
    req = ask(svc)
    fail_decision_once(monkeypatch)
    with pytest.raises(requests.HTTPError):
        svc.platform_decide(req["id"], True)
    assert svc._sc_made_before(svc.platform_requests("waiting")[0], "", RuntimeError("409")) is True
