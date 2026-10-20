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


def test_a_setting_a_listed_capacity_stops_returning_has_no_new_value(caps):
    caps.capacity_settings_read(approve=True)
    caps.capacities = [{k: v for k, v in cap().items() if k != "region"}, cap("cap-2", "F8", name="Dev")]
    [gone] = caps.capacity_settings_read()["changes"]
    assert gone["setting"] == "region" and gone["new_json"] is None
    assert caps.capacity_settings_read()["changes"] == []


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
