"""The access cache (fdacc_) in the central lakehouse: refresh writes only what changed, a failed
read keeps the last good snapshot marked stale, and people see only workspaces they can open."""

from __future__ import annotations

import pytest

from fabric_desktop import access_cache, app_settings, audit, server
from fabric_desktop.optimize import schema
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service


def assignment(pid, name, role, kind="User", upn=""):
    return {"id": f"a-{pid}", "role": role, "principal": {"id": pid, "displayName": name, "type": kind,
                                                         "userDetails": {"userPrincipalName": upn or f"{pid}@kratosbi.com"}}}


class FakeFabric:
    def __init__(self):
        self.workspaces = [{"id": "w1", "displayName": "Sales"}, {"id": "w2", "displayName": "HR"},
                           {"id": "plat", "displayName": "Platform"}]
        self.roles = {"w1": [assignment("u1", "Ann", "Admin"), assignment("g1", "Sales readers", "Viewer", "Group")],
                      "w2": [assignment("u2", "Bo", "Member", upn="bo_x.com#EXT#@kratosbi.com")], "plat": []}
        self.lakehouses = {"w1": [{"id": "lh1", "displayName": "LH_Sales"}], "w2": [], "plat": []}
        self.fail: set[str] = set()

    def list_workspaces(self):
        return self.workspaces

    def list_role_assignments(self, ws):
        if ws in self.fail:
            raise RuntimeError("403 Forbidden")
        return self.roles[ws]

    def list_lakehouses(self, ws):
        return self.lakehouses.get(ws, [])

    def find_lakehouse(self, ws, name):
        return next((lh for lh in self.list_lakehouses(ws) if lh["displayName"] == name), None)

    def create_lakehouse(self, ws, name):
        lh = {"id": "lh-central", "displayName": name}
        self.lakehouses.setdefault(ws, []).append(lh)
        return lh


class FakeOneLake:
    def __init__(self):
        self.roles = {"lh1": [{"name": "DefaultReader", "decisionRules": [{"effect": "Permit", "permission": [
            {"attributeName": "Path", "attributeValueIncludedIn": ["*"]}]}],
            "members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"]}]}}]}
        self.calls = 0

    def list_data_access_roles(self, ws, item):
        self.calls += 1
        if item == "broken":
            raise RuntimeError("429 Too many requests")
        return self.roles.get(item, [])


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    s = Service(root=tmp_path / "app")
    fabric, onelake = FakeFabric(), FakeOneLake()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    monkeypatch.setattr(Service, "_scoped", lambda self, scopes: onelake)
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    s.optimize_create("plat")
    s.fake, s.onelake = fabric, onelake
    return s


def lake(svc):
    conn = svc.central_lakehouse()
    return svc._optimize_lake(conn["workspace_id"], conn["lakehouse_id"])


def test_calls_are_registered_and_the_read_is_not_audited():
    from fabric_desktop.access_cache_service import METHODS
    assert METHODS <= server.METHODS
    assert not audit.audited("access_cache") and audit.audited("access_cache_refresh")
    assert schema.FAMILIES["fdacc_"] is access_cache.FAMILY


def test_first_refresh_asks_before_setting_up_the_tables(svc):
    before = svc.access_cache()
    assert before["ready"] is False and before["workspaces"] == []
    with pytest.raises(PermissionError, match="Confirm to continue"):
        svc.access_cache_refresh()
    assert not lake(svc).has("fdacc_scans")
    out = svc.access_cache_refresh(approve=True)
    assert out["changed"] == out["checked"] and out["problems"] == 0


def test_cache_shows_people_and_lakehouse_roles_without_emails(svc):
    svc.access_cache_refresh(approve=True)
    got = {w["workspace_id"]: w for w in svc.access_cache()["workspaces"]}
    sales = got["w1"]
    assert [p["name"] for p in sales["people"]] == ["Ann", "Sales readers"]
    assert sales["summary"] == {"people": 1, "groups": 1, "apps": 0, "guests": 0}
    assert got["w2"]["summary"]["guests"] == 1
    assert sales["lakehouses"][0]["roles"][0] == {"name": "DefaultReader", "paths": ["*"], "entra": [],
                                                   "item_members": ["ReadAll"]}
    assert "kratosbi.com" not in repr(lake(svc).rows("fdacc_workspace_roles").to_dicts())


def test_refresh_writes_only_what_changed(svc):
    svc.access_cache_refresh(approve=True)
    rows_before = lake(svc).rows("fdacc_workspace_roles").height
    again = svc.access_cache_refresh()
    assert again["changed"] == 0 and lake(svc).rows("fdacc_workspace_roles").height == rows_before
    svc.fake.roles["w1"] = svc.fake.roles["w1"][:1]
    third = svc.access_cache_refresh()
    assert third["changed"] == 1
    sales = next(w for w in svc.access_cache()["workspaces"] if w["workspace_id"] == "w1")
    assert [p["name"] for p in sales["people"]] == ["Ann"] and sales["changed_at"] == third["at"]


def test_a_failed_read_keeps_the_last_snapshot_marked_stale(svc):
    svc.access_cache_refresh(approve=True)
    svc.fake.fail.add("w1")
    svc.fake.lakehouses["w1"].append({"id": "broken", "displayName": "LH_Broken"})
    out = svc.access_cache_refresh()
    assert out["problems"] == 2
    sales = next(w for w in svc.access_cache()["workspaces"] if w["workspace_id"] == "w1")
    assert sales["stale"] and "403" in sales["problem"] and [p["name"] for p in sales["people"]] == ["Ann", "Sales readers"]
    broken = next(lh for lh in sales["lakehouses"] if lh["item_id"] == "broken")
    assert broken["known"] is False and broken["stale"] and broken["roles"] == []


def test_people_see_only_workspaces_they_can_open(svc):
    svc.access_cache_refresh(approve=True)
    svc.fake.workspaces = [w for w in svc.fake.workspaces if w["id"] != "w2"]
    assert "w2" not in {w["workspace_id"] for w in svc.access_cache()["workspaces"]}


def test_a_deleted_lakehouse_drops_out_and_one_workspace_can_be_refreshed(svc):
    svc.access_cache_refresh(approve=True)
    svc.fake.lakehouses["w1"] = []
    out = svc.access_cache_refresh(workspace_ids=["w1"])
    assert out["checked"] == 2  # the workspace and its gone lakehouse
    sales = next(w for w in svc.access_cache()["workspaces"] if w["workspace_id"] == "w1")
    assert sales["lakehouses"] == []


def test_refresh_needs_write_access_to_the_central_lakehouse(svc):
    svc.access_cache_refresh(approve=True)
    svc.optimize_registry.add(svc.central_lakehouse() | {"access": "read"})
    with pytest.raises(PermissionError, match="read"):
        svc.access_cache_refresh()
    assert svc.access_cache()["ready"] is True  # reading still works
