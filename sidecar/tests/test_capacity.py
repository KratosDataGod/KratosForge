"""FINOPS-002: each environment's capacity and whether it can run work, read only."""

from __future__ import annotations

import datetime as dt
import json

import pytest
import requests

from fabric_desktop import capacity
from fabric_desktop import estate_service as estate_mod
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.service import Service

NOW = dt.datetime(2026, 10, 17, 12, tzinfo=dt.UTC)
CAPS = [{"id": "CAP-1", "displayName": "Contoso F64", "sku": "F64", "region": "West Europe", "state": "Active"},
        {"id": "cap-2", "displayName": "Trial", "sku": "FT1", "region": "East US", "state": "Inactive"}]
WS = {"w1": {"id": "w1", "displayName": "Sales Dev", "capacityId": "cap-1", "capacityRegion": "West Europe",
             "capacityAssignmentProgress": "Completed"},
      "w2": {"id": "w2", "displayName": "Sales Test", "capacityId": "cap-2"},
      "w3": {"id": "w3", "displayName": "Sales Prod", "capacityId": "cap-hidden"},
      "w4": {"id": "w4", "displayName": "No capacity"}}


def get_ws(ws_id):
    if ws_id == "gone":
        raise FabricError("GET failed with 404", "WorkspaceNotFound", 404)
    if ws_id == "locked":
        raise FabricError("GET failed with 403", "InsufficientPrivileges", 403)
    return WS[ws_id]


def envs(**ids):
    return {env: {"workspace_id": ws} for env, ws in ids.items()}


def by_env(doc):
    return {e["env"]: e for e in doc["environments"]}


def test_capacities_show_only_what_fabric_returned():
    doc = capacity.observe(envs(dev="w1", test="w2", prod="w3"), get_ws, lambda: CAPS, NOW)
    e = by_env(doc)
    assert e["dev"]["access"] == "ok" and e["dev"]["status"] == "running"
    assert e["dev"]["capacity"] == {"id": "CAP-1", "name": "Contoso F64", "sku": "F64", "region": "West Europe",
                                    "state": "Active", "cu": 64}
    assert e["dev"]["region"] == "West Europe" and e["dev"]["assignment"] == "Completed"
    assert e["dev"]["observed_at"] == "2026-10-17T12:00:00+00:00" and e["dev"]["source"].startswith("Fabric REST API")
    # Running isn't the same as having room: utilization is unknown, never 0.
    assert "busy" in e["dev"]["status_text"]
    assert e["dev"]["utilization"]["classification"] == "unknown" and e["dev"]["utilization"]["value"] is None
    # Fields Fabric didn't send stay out: test's workspace has no region of its own.
    assert e["test"]["status"] == "not_running" and "paused" in e["test"]["status_text"]
    assert "Runs on this PC still work" in e["test"]["status_text"] and e["test"]["assignment"] is None
    assert doc["partial"] is True  # prod's capacity isn't visible


def test_a_capacity_you_cant_list_is_not_visible_not_missing():
    e = by_env(capacity.observe(envs(prod="w3"), get_ws, lambda: CAPS, NOW))["prod"]
    assert e["access"] == "capacity_not_visible" and e["capacity_id"] == "cap-hidden" and e["capacity"] is None
    assert "administer or may assign" in e["action"] and e["status"] == "unknown"


def test_permission_problems_are_told_apart_from_missing_workspaces():
    e = by_env(capacity.observe(envs(dev="gone", test="locked", prod=None), get_ws, lambda: CAPS, NOW))
    assert e["dev"]["access"] == "workspace_not_found" and "Assign another workspace" in e["dev"]["action"]
    assert e["test"]["access"] == "workspace_no_access" and "access" in e["test"]["action"]
    assert e["prod"]["access"] == "no_workspace" and e["prod"]["observed_at"] is None


def test_no_capacity_scope_still_shows_the_workspace_capacity_id():
    def no_scope():
        raise PermissionError("Capacity.Read.All not granted")
    e = by_env(capacity.observe(envs(dev="w1"), get_ws, no_scope, NOW))["dev"]
    assert e["access"] == "scope_not_granted" and e["capacity_id"] == "cap-1"
    assert "Capacity.Read.All" in e["action"] and "permissions.md" in e["action"]
    forbidden = by_env(capacity.observe(envs(dev="w1"), get_ws,
                                        lambda: (_ for _ in ()).throw(FabricError("403", None, 403)), NOW))["dev"]
    assert forbidden["access"] == "scope_not_granted"


def test_a_workspace_without_a_capacity_is_explained():
    e = by_env(capacity.observe(envs(dev="w4"), get_ws, lambda: CAPS, NOW))["dev"]
    assert e["access"] == "no_capacity" and "License info" in e["action"] and "this PC don't" in e["action"]


def test_signed_out_reads_nothing():
    doc = capacity.observe(envs(dev="w1"), None, None, NOW)
    assert by_env(doc)["dev"]["access"] == "signed_out"


def test_unknown_state_and_sku_are_not_guessed():
    caps = [{"id": "cap-1", "displayName": "X", "sku": "A1", "state": "Provisioning"}]
    e = by_env(capacity.observe(envs(dev="w1"), get_ws, lambda: caps, NOW))["dev"]
    assert e["status"] == "unknown" and "Provisioning" in e["status_text"] and "cu" not in e["capacity"]
    assert "region" not in e["capacity"] and e["region"] == "West Europe"  # from the workspace


def test_offline_stops_after_the_first_environment_and_keeps_the_last_good_reading():
    calls = []

    def offline(ws_id):
        calls.append(ws_id)
        raise requests.ConnectionError("no network")
    good = capacity.observe(envs(dev="w1", test="w2"), get_ws, lambda: CAPS, NOW - dt.timedelta(hours=3))
    fresh = capacity.observe(envs(dev="w1", test="w2"), offline, lambda: CAPS, NOW)
    assert calls == ["w1"]  # test isn't tried again once Fabric is unreachable
    assert {e["access"] for e in fresh["environments"]} == {"offline"}
    merged = by_env(capacity.merge_cached(fresh, good, NOW))
    assert merged["dev"]["access"] == "ok" and merged["dev"]["cached"] and merged["dev"]["stale"]
    assert merged["dev"]["age_seconds"] == 3 * 3600 and merged["dev"]["read_error"] == "Couldn't reach Fabric"
    assert "carries on" in merged["dev"]["action"]


def test_a_cached_reading_for_another_workspace_isnt_reused():
    good = capacity.observe(envs(dev="w1"), get_ws, lambda: CAPS, NOW)
    fresh = capacity.observe(envs(dev="w2"), None, None, NOW)
    assert by_env(capacity.merge_cached(fresh, good, NOW))["dev"]["access"] == "signed_out"


def test_paging_is_followed():
    from test_fabric_api import Resp
    pages = [Resp(200, {"value": [CAPS[0]], "continuationUri": "https://api.fabric.microsoft.com/v1/capacities?ct=2"}),
             Resp(200, {"value": [CAPS[1]]})]

    class Session:
        def request(self, method, url, **kw):
            return pages.pop(0)
    from fabric_desktop.fabric_api import FabricClient
    caps = FabricClient(lambda: "t", session=Session()).list_capacities()
    assert [c["id"] for c in caps] == ["CAP-1", "cap-2"]


def test_fabric_errors_keep_their_status_code():
    from test_fabric_api import Resp

    from fabric_desktop.fabric_api import _error
    err = _error("GET", "/workspaces/x", Resp(404, {"errorCode": "WorkspaceNotFound"}))
    assert err.status == 404 and capacity.classify_error(err) == "workspace_not_found"
    assert capacity.classify_error(_error("GET", "/x", Resp(403, {}))) == "workspace_no_access"
    assert capacity.classify_error(RuntimeError("boom")) == "error"


# ---- through the service

class FakeAuth:
    def __init__(self, scopes):
        self.scopes = scopes

    def silent_token(self, scopes):
        return "token" if any(s in self.scopes for s in scopes) else None


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    s.meta_init(str(tmp_path / "proj"), "3")
    s.meta_set_environment("dev", workspace_id="w1", workspace_name="Sales Dev")
    s.meta_set_environment("test", workspace_id="w2", workspace_name="Sales Test")
    calls = {"ws": 0, "caps": 0}

    class Client:
        def __init__(self, token):
            pass

        def get_workspace(self, ws_id):
            calls["ws"] += 1
            return get_ws(ws_id)

        def list_capacities(self):
            calls["caps"] += 1
            return CAPS
    monkeypatch.setattr(estate_mod, "FabricClient", Client)
    s.calls = calls
    return s


def test_service_reads_once_an_hour_and_never_prompts(svc):
    from fabric_desktop.auth import CAPACITY_SCOPES, FABRIC_SCOPES
    svc._auth = FakeAuth(FABRIC_SCOPES + CAPACITY_SCOPES)
    doc = svc.capacity_health()
    e = by_env(doc)
    assert e["dev"]["status"] == "running" and e["test"]["status"] == "not_running" and e["prod"]["access"] == "no_workspace"
    assert svc.calls == {"ws": 2, "caps": 1}
    svc.capacity_health()
    assert svc.calls == {"ws": 2, "caps": 1}  # cached within the hour
    svc.capacity_health(refresh=True)
    assert svc.calls == {"ws": 4, "caps": 2}
    saved = json.loads((svc.root / "capacity_observations.json").read_text())
    assert by_env(saved)["dev"]["capacity"]["sku"] == "F64"
    # The cost view uses the same reading for each environment's share of its capacity.
    report = svc.cost_report(7)
    assert by_env(report["capacities"])["dev"]["capacity"]["name"] == "Contoso F64"


def test_service_without_capacity_consent_says_so(svc):
    from fabric_desktop.auth import FABRIC_SCOPES
    svc._auth = FakeAuth(FABRIC_SCOPES)
    e = by_env(svc.capacity_health(refresh=True))
    assert e["dev"]["access"] == "scope_not_granted" and e["dev"]["capacity_id"] == "cap-1" and svc.calls["caps"] == 0


def test_service_signed_out_works_offline(svc):
    svc._auth = FakeAuth([])
    e = by_env(svc.capacity_health(refresh=True))
    assert e["dev"]["access"] == "signed_out" and svc.calls == {"ws": 0, "caps": 0}
    report = svc.cost_report(7)  # the cost view carries on without Fabric
    assert by_env(report["capacities"])["dev"]["access"] == "signed_out" and report["by_env"] == []
