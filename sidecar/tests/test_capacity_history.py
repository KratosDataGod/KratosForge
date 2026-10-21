"""Capacity settings history (item 30): fdcap_ snapshots, changes and reads in the central lakehouse."""

from __future__ import annotations

import json

import pytest
from fabric_desktop import audit, server
from fabric_desktop import capacity_history as ch
from fabric_desktop import tenant_history as th
from fabric_desktop.capacity_history_service import METHODS
from fabric_desktop.fabric_api import FabricError
from fabric_desktop.optimize import schema
from test_tenant_history import ME, svc  # noqa: F401 - the central lakehouse fixture


def cap(cid="CAP-1", sku="F64", state="Active", name="Prod", region="West Europe"):
    return {"id": cid, "displayName": name, "sku": sku, "region": region, "state": state}


@pytest.fixture
def caps(svc, monkeypatch):  # noqa: F811
    svc.capacities = [cap(), cap("cap-2", "F8", name="Dev")]
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_capacities", lambda client: svc.capacities)
    return svc


def test_registered_with_the_tenant_columns_and_audited():
    assert METHODS <= server.METHODS and audit.audited("capacity_settings_read")
    assert schema.FAMILIES["fdcap_"] is ch.FAMILY
    tables = ch.FAMILY.migrations[0].tables
    assert tables["snapshot"] == th.SNAPSHOT and tables["change"] == th.CHANGE and tables["read"] == th.READ


def test_first_read_asks_then_records_each_setting_of_each_capacity(caps):
    with pytest.raises(PermissionError, match="Confirm to continue"):
        caps.capacity_settings_read()
    out = caps.capacity_settings_read(approve=True)
    assert out["outcome"] == "read" and out["first_read"] and out["capacities"] == 2 and len(out["changes"]) == 8
    assert all(c["old_json"] is None and c["changed_after"] is None for c in out["changes"])
    snap = caps.lake.rows("fdcap_snapshot").to_dicts()
    assert {r["scope_id"] for r in snap} == {"cap-1", "cap-2"} and {r["read_by"] for r in snap} == {ME}
    assert all(r["installation_id"] and r["source"] == "GET /v1/capacities" for r in snap)
    reads = caps.lake.rows("fdcap_read").to_dicts()
    assert sorted(r["scope_id"] for r in reads) == ["cap-1", "cap-2"] and {r["outcome"] for r in reads} == {"read"}
    assert not caps.lake.has("fdten_snapshot")  # the other family's tables aren't touched


def test_a_resize_is_observed_between_two_reads_and_shown_in_the_history(caps):
    first = caps.capacity_settings_read(approve=True)["read_at"]
    caps.capacities = [cap(sku="F128"), cap("cap-2", "F8", name="Dev")]
    out = caps.capacity_settings_read()
    [c] = out["changes"]
    assert (c["scope_id"], c["setting"], c["changed_after"], c["changed_by_time"]) == ("cap-1", "sku", first, out["read_at"])
    assert json.loads(c["old_json"]) == "F64" and json.loads(c["new_json"]) == "F128"
    history = caps.tenant_settings_history()
    assert history["changes"][0]["setting"] == "sku" and history["changes"][0]["scope_type"] == "capacity"
    assert history["last_read"]["capacity"] == out["read_at"]


def test_a_quiet_read_adds_only_read_rows(caps):
    caps.capacity_settings_read(approve=True)
    caps.capacities.reverse()
    assert caps.capacity_settings_read()["changes"] == []
    assert len(caps.lake.rows("fdcap_snapshot")) == 8 and len(caps.lake.rows("fdcap_read")) == 4


def test_a_capacity_the_reader_cant_see_isnt_called_changed(caps):
    caps.capacity_settings_read(approve=True)
    caps.capacities = [cap()]  # another person, who sees only one capacity
    out = caps.capacity_settings_read()
    assert out["changes"] == [] and out["capacities"] == 1
    caps.capacities = [cap(state="Paused"), cap("cap-2", "F8", name="Dev")]
    [c] = caps.capacity_settings_read()["changes"]
    assert c["setting"] == "state" and json.loads(c["new_json"]) == "Paused"


def test_a_field_missing_from_one_response_isnt_recorded_as_removed(caps):
    caps.capacity_settings_read(approve=True)
    caps.capacities = [{k: v for k, v in cap().items() if k != "region"}, cap("cap-2", "F8", name="Dev")]
    assert caps.capacity_settings_read()["changes"] == []
    caps.capacities = [cap(region="North Europe"), cap("cap-2", "F8", name="Dev")]
    [moved] = caps.capacity_settings_read()["changes"]
    assert moved["setting"] == "region" and json.loads(moved["old_json"]) == "West Europe"


def test_no_capacities_is_a_read_not_a_gap(caps):
    caps.capacities = []
    out = caps.capacity_settings_read(approve=True)
    assert out["outcome"] == "read" and out["capacities"] == 0
    [row] = caps.lake.rows("fdcap_read").to_dicts()
    assert row["scope_id"] is None and row["outcome"] == "read"


@pytest.mark.parametrize(("status", "outcome"), [(403, "unreadable"), (500, "failed")])
def test_a_read_that_fails_is_recorded_redacted_and_shown_as_a_gap(caps, monkeypatch, status, outcome):
    caps.capacity_settings_read(approve=True)

    def broken(client):
        raise FabricError(f"GET /capacities failed with {status}: Bearer abcdefghijkl token=abc123", status=status)
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_capacities", broken)
    out = caps.capacity_settings_read()
    assert out["outcome"] == outcome and "abcdefghijkl" not in out["reason"]
    if outcome == "unreadable":
        assert "Capacity.Read.All" in out["reason"]
    reasons = [r["reason"] for r in caps.lake.rows("fdcap_read").to_dicts() if r["outcome"] == outcome]
    assert reasons and "abcdefghijkl" not in reasons[0]
    [gap] = caps.tenant_settings_history()["gaps"]
    assert gap["scope_type"] == "capacity" and gap["outcome"] == outcome
    assert len(caps.lake.rows("fdcap_snapshot")) == 8  # nothing looks changed


def override(cid, *settings):
    return {"id": cid, "tenantSettings": [{"settingName": n, "title": n, "enabled": e, "tenantSettingGroup": "G"}
                                          for n, e in settings]}


@pytest.fixture
def admin(caps, monkeypatch):
    caps.overrides = [override("CAP-1", ("ExportToExcel", False))]
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_capacity_tenant_setting_overrides",
                        lambda client: caps.overrides)
    return caps


def test_overrides_are_read_only_when_asked(admin, monkeypatch):
    def never(client):
        raise AssertionError("asked for Tenant.Read.All without admin=True")
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_capacity_tenant_setting_overrides", never)
    out = admin.capacity_settings_read(approve=True)
    assert out["overrides"] is None and not any(c["setting"].startswith("override:") for c in out["changes"])


def test_an_admin_records_each_capacitys_overrides_and_their_removal(admin):
    first = admin.capacity_settings_read(approve=True, admin=True)
    assert first["overrides"] == "read"
    [o] = [c for c in first["changes"] if c["setting"].startswith("override:")]
    assert (o["scope_id"], o["setting"]) == ("cap-1", "override:ExportToExcel")
    assert json.loads(o["new_json"]) == {"enabled": False}
    snap = [r for r in admin.lake.rows("fdcap_snapshot").to_dicts() if r["setting"] == "override:ExportToExcel"]
    assert snap[0]["source"].endswith("(admin)")
    admin.overrides = [override("cap-1", ("ExportToExcel", True)), override("cap-2", ("Publish", True))]
    changed = admin.capacity_settings_read(admin=True)["changes"]
    assert {(c["scope_id"], c["setting"]) for c in changed} == {("cap-1", "override:ExportToExcel"),
                                                                ("cap-2", "override:Publish")}
    assert admin.capacity_settings_read(admin=True)["changes"] == []
    admin.overrides = [override("cap-2", ("Publish", True))]  # the admin read covers every capacity
    [gone] = admin.capacity_settings_read(admin=True)["changes"]
    assert gone["setting"] == "override:ExportToExcel" and gone["new_json"] is None
    # A read without admin doesn't touch overrides.
    assert admin.capacity_settings_read()["changes"] == []


def test_not_an_admin_is_recorded_and_the_capacities_are_still_read(admin, monkeypatch):
    def denied(client):
        raise FabricError("GET failed with 403: Forbidden Bearer abcdefghijkl", status=403)
    monkeypatch.setattr("fabric_desktop.fabric_api.FabricClient.list_capacity_tenant_setting_overrides", denied)
    out = admin.capacity_settings_read(approve=True, admin=True)
    assert out["outcome"] == "read" and out["overrides"] == "unreadable" and len(out["changes"]) == 8
    row = next(r for r in admin.lake.rows("fdcap_read").to_dicts() if r["source"].endswith("(admin)"))
    assert row["outcome"] == "unreadable" and "Fabric administrator" in row["reason"]


# Fabric's documented response for GET /v1/admin/capacities/delegatedTenantSettingOverrides puts
# the list under "value" (quality's #351 probe): fed through the real client, not a mocked method.
OVERRIDES_SAMPLE = {"value": [{"id": "f51b705f-a409-4d40-9197-c5d5f349e2ef", "tenantSettings": [
    {"settingName": "TenantSettingForCapacityDelegatedSwitch", "title": "t", "enabled": True,
     "tenantSettingGroup": "g", "delegatedFrom": "Tenant"}]}]}


def test_overrides_come_from_the_documented_value_list(monkeypatch):
    from fabric_desktop.fabric_api import FabricClient

    class Resp:
        def json(self):
            return OVERRIDES_SAMPLE

    client = FabricClient(lambda: "token")
    monkeypatch.setattr(client, "_request", lambda method, url, **kw: Resp())
    got = client.list_capacity_tenant_setting_overrides()
    assert [o["id"] for o in got] == ["f51b705f-a409-4d40-9197-c5d5f349e2ef"]
    snaps, changes = ch.compare_overrides(got, [], ME, "2026-10-21T08:00:00+00:00")
    assert [c["setting"] for c in changes] == ["override:TenantSettingForCapacityDelegatedSwitch"]
    assert snaps[0]["scope_id"] == "f51b705f-a409-4d40-9197-c5d5f349e2ef"
