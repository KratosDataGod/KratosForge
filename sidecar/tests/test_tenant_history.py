"""Tenant settings history (item 30): fdten_ snapshots, changes and reads in the central lakehouse."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import app_settings, audit, server, tenant_history as th
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.optimize import schema
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service
from fabric_desktop.tenant_history_service import METHODS

TENANT, ME = "t-1", "oid-1"


def setting(name, enabled=True, groups=(), title=None):
    s = {"settingName": name, "title": title or name, "enabled": enabled, "tenantSettingGroup": "G",
         "canSpecifySecurityGroups": bool(groups)}
    if groups:
        s["enabledSecurityGroups"] = [{"graphId": g, "name": g} for g in groups]
    return s


class FakeFabric:
    lakehouses = {"w1": []}

    def list_workspaces(self):
        return [{"id": "w1", "displayName": "Platform"}]

    def list_lakehouses(self, ws):
        return self.lakehouses.setdefault(ws, [])

    def find_lakehouse(self, ws, name):
        return next((lh for lh in self.list_lakehouses(ws) if lh["displayName"] == name), None)

    def create_lakehouse(self, ws, name):
        lh = {"id": "lh-c", "displayName": name}
        self.list_lakehouses(ws).append(lh)
        return lh


class FakeAuth:
    def signed_in_tenant(self):
        return TENANT

    def signed_in_object_id(self):
        return ME

    def _token(self, scopes):
        return "tok"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    s = Service(root=tmp_path / "app")
    FakeFabric.lakehouses = {"w1": []}
    monkeypatch.setattr(Service, "fabric", property(lambda self: FakeFabric()))
    monkeypatch.setattr(Service, "auth", property(lambda self: FakeAuth()), raising=False)
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    s.optimize_create("w1", workspace_name="Platform")
    s.settings = [setting("ExternalSharing", False), setting("ExportToExcel", True, ["g2", "g1"])]
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_tenant_settings", lambda client: s.settings)
    s.lake = LocalLakehouse(lakes / "w1" / "lh-c")
    return s


def test_registered_read_only_where_it_should_be():
    assert METHODS <= server.METHODS
    assert not audit.audited("tenant_settings_history") and audit.audited("tenant_settings_read")
    assert schema.FAMILIES["fdten_"] is th.FAMILY


def test_first_read_asks_then_records_every_setting(svc):
    with pytest.raises(PermissionError, match="Confirm to continue"):
        svc.tenant_settings_read()
    out = svc.tenant_settings_read(approve=True)
    assert out["outcome"] == "read" and out["first_read"] and out["settings"] == 2
    assert {c["setting"] for c in out["changes"]} == {"ExternalSharing", "ExportToExcel"}
    assert all(c["old_json"] is None and c["changed_after"] is None for c in out["changes"])
    snap = svc.lake.rows("fdten_snapshot").to_dicts()
    assert {r["read_by"] for r in snap} == {ME} and {r["scope_type"] for r in snap} == {"tenant"}
    assert all(r["installation_id"] and r["record_id"] for r in snap)  # provenance on every row


def test_a_quiet_read_adds_only_a_read_row(svc):
    svc.tenant_settings_read(approve=True)
    svc.settings = [setting("ExportToExcel", True, ["g1", "g2"], title="Renamed by Microsoft"),
                    setting("ExternalSharing", False)]  # same values, other order and title
    out = svc.tenant_settings_read()
    assert out["changes"] == []
    assert len(svc.lake.rows("fdten_snapshot")) == 2 and len(svc.lake.rows("fdten_read")) == 2


def test_a_change_is_observed_between_two_reads(svc):
    first = svc.tenant_settings_read(approve=True)["read_at"]
    svc.settings = [setting("ExternalSharing", True), setting("ExportToExcel", True, ["g1", "g2"])]
    out = svc.tenant_settings_read()
    [c] = out["changes"]
    assert c["setting"] == "ExternalSharing" and c["changed_after"] == first and c["changed_by_time"] == out["read_at"]
    assert json.loads(c["old_json"])["enabled"] is False and json.loads(c["new_json"])["enabled"] is True
    history = svc.tenant_settings_history()
    assert history["changes"][0]["setting"] == "ExternalSharing" and history["last_read"]["tenant"] == out["read_at"]
    assert "by" not in history["changes"][0]  # who changed it isn't known


def test_a_setting_that_disappears_and_comes_back(svc):
    svc.tenant_settings_read(approve=True)
    svc.settings = [setting("ExportToExcel", True, ["g1", "g2"])]
    [gone] = svc.tenant_settings_read()["changes"]
    assert gone["setting"] == "ExternalSharing" and gone["new_json"] is None
    assert svc.tenant_settings_read()["changes"] == []
    svc.settings.append(setting("ExternalSharing", False))
    [back] = svc.tenant_settings_read()["changes"]
    assert back["old_json"] is None and json.loads(back["new_json"])["enabled"] is False


def test_not_an_admin_is_recorded_as_unreadable_and_shown_as_a_gap(svc, monkeypatch):
    svc.tenant_settings_read(approve=True)

    def denied(client):
        raise FabricError("GET /admin/tenantsettings failed with 403: Forbidden token=abc123", status=403)
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_tenant_settings", denied)
    out = svc.tenant_settings_read()
    assert out["outcome"] == "unreadable" and "Fabric administrator" in out["reason"]
    [gap] = svc.tenant_settings_history()["gaps"]
    assert gap["outcome"] == "unreadable"
    assert len(svc.lake.rows("fdten_snapshot")) == 2  # nothing looks changed


def test_a_failure_is_redacted(svc, monkeypatch):
    svc.tenant_settings_read(approve=True)

    def broken(client):
        raise FabricError("GET failed with 500: Bearer abcdefghijkl secret", status=500)
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_tenant_settings", broken)
    assert svc.tenant_settings_read()["outcome"] == "failed"
    reasons = [r["reason"] for r in svc.lake.rows("fdten_read").to_dicts() if r["outcome"] == "failed"]
    assert reasons and "abcdefghijkl" not in reasons[0]


def test_history_without_a_central_lakehouse(svc, monkeypatch):
    monkeypatch.setattr(Service, "central_lakehouse", lambda self: None)
    assert svc.tenant_settings_history() == {"connected": False, "changes": [], "last_read": None, "gaps": []}


def test_history_includes_capacity_changes_when_finops_tables_exist(svc):
    import polars as pl
    svc.tenant_settings_read(approve=True)
    cols = schema.PROVENANCE | th.CHANGE
    row = {c: None for c in cols} | {"record_id": "c1", "scope_type": "capacity", "scope_id": "cap1",
                                     "setting": "sku", "changed_by_time": "2999-01-01T00:00:00Z", "new_json": "\"F64\""}
    svc.lake.create("fdcap_change", cols)
    svc.lake.insert_new("fdcap_change", pl.DataFrame([row], schema=cols))
    assert svc.tenant_settings_history()["changes"][0]["scope_type"] == "capacity"
