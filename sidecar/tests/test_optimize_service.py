"""Optimize's app calls: create, connect, switch, disconnect, and the IT policy switch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import app_settings, audit, server
from fabric_desktop.optimize import setup
from fabric_desktop.optimize.lakehouse import LocalLakehouse
from fabric_desktop.service import Service


class FakeFabric:
    def __init__(self):
        self.lakehouses: dict[str, list[dict]] = {"w1": [{"id": "lh-sales", "displayName": "Sales"}]}
        self.created: list[str] = []

    def list_workspaces(self):
        return [{"id": "w1", "displayName": "Platform"}]

    def list_lakehouses(self, workspace_id):
        return self.lakehouses.get(workspace_id, [])

    def find_lakehouse(self, workspace_id, name):
        return next((lh for lh in self.list_lakehouses(workspace_id) if lh["displayName"] == name), None)

    def create_lakehouse(self, workspace_id, name):
        lh = {"id": f"lh-{len(self.created) + 1}", "displayName": name}
        self.lakehouses.setdefault(workspace_id, []).append(lh)
        self.created.append(name)
        return lh


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "policy_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(app_settings, "user_path", lambda: tmp_path / "user.json")
    s = Service(root=tmp_path / "app")
    fabric = FakeFabric()
    monkeypatch.setattr(Service, "fabric", property(lambda self: fabric))
    lakes = tmp_path / "onelake"
    monkeypatch.setattr(Service, "_optimize_lake",
                        lambda self, ws, lh, schemas=None: LocalLakehouse(lakes / ws / lh, schemas is not False))
    s.fake = fabric
    s.lakes = lakes
    return s


def test_calls_are_registered_and_audited_as_they_should_be():
    from fabric_desktop.optimize_service import METHODS
    assert METHODS <= server.METHODS
    assert not audit.audited("optimize_status") and not audit.audited("optimize_inspect")
    assert not audit.audited("optimize_discover")
    assert not audit.audited("optimize_findings") and not audit.audited("optimize_recommendations")
    assert audit.audited("optimize_set_recommendation")
    assert audit.audited("optimize_create") and audit.audited("optimize_connect") and audit.audited("optimize_disconnect")


def test_create_initializes_and_connects(svc):
    out = svc.optimize_create("w1", workspace_name="Platform")
    assert out["connected"] and out["created"] and out["access"]["write"]
    assert svc.fake.created == ["FabricDesktop_Central"]
    conn = svc.optimize_status()["active"]
    assert conn["lakehouse_name"] == "FabricDesktop_Central" and conn["access"] == "write"
    assert conn["schema_version"] == 1
    assert svc.optimize_inspect("w1", conn["lakehouse_id"])["state"] == setup.READY


def test_create_that_fails_part_way_says_how_to_finish(svc, monkeypatch):
    monkeypatch.setattr(setup, "initialize", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network down")))
    with pytest.raises(RuntimeError, match="Connect to it to finish"):
        svc.optimize_create("w1")


def test_create_refuses_a_name_already_used(svc):
    with pytest.raises(ValueError, match="already has a lakehouse called Sales"):
        svc.optimize_create("w1", name="Sales")
    assert svc.fake.created == []


def test_connect_to_a_lakehouse_with_other_tables_asks_first(svc):
    out = svc.optimize_connect("w1", "lh-sales")
    assert out == {"connected": False, "needs_confirmation": True, "inspection": out["inspection"]}
    assert out["inspection"]["state"] == setup.EMPTY
    assert svc.optimize_status()["connections"] == []
    assert svc.optimize_connect("w1", "lh-sales", initialize=True)["connected"]


def test_second_installation_connects_to_the_same_lakehouse(svc, tmp_path, monkeypatch):
    lh = svc.optimize_create("w1")["connection"]["lakehouse_id"]
    other = Service(root=tmp_path / "other-pc")
    out = other.optimize_connect("w1", lh, lakehouse_name="FabricDesktop_Central")
    assert out["connected"] and out["access"]["write"]
    lake = LocalLakehouse(svc.lakes / "w1" / lh)
    assert len(lake.rows("fdopt_installations")) == 2
    assert len(lake.rows("fdopt_schema_version")) == 1


def test_discover_marks_optimization_lakehouses(svc):
    svc.optimize_create("w1")
    found = svc.optimize_discover("w1")
    assert [(f["lakehouse_name"], f["optimize"]) for f in found] == [("FabricDesktop_Central", True), ("Sales", False)]


def test_disconnect_keeps_the_lakehouse(svc):
    lh = svc.optimize_create("w1")["connection"]["lakehouse_id"]
    lake = LocalLakehouse(svc.lakes / "w1" / lh)
    rows = len(lake.rows("fdopt_installations"))
    assert svc.optimize_disconnect(lh)["active"] is None
    assert setup.inspect(lake)["state"] == setup.READY and len(lake.rows("fdopt_installations")) == rows
    assert svc.optimize_connect("w1", lh)["connected"]  # reconnect: nothing new to create


def test_policy_can_turn_it_off(svc, tmp_path):
    (tmp_path / "policy.json").write_text(json.dumps({"allow_optimize_lakehouse": False}))
    assert svc.optimize_status()["allowed"] is False
    with pytest.raises(PermissionError, match="allow_optimize_lakehouse"):
        svc.optimize_create("w1")
    with pytest.raises(PermissionError):
        svc.optimize_connect("w1", "lh-sales", initialize=True)
    assert svc.fake.created == [] and not Path(svc.lakes).exists()
    assert app_settings.policy_problems() == []


def test_installation_id_reset_and_label(svc):
    first = svc.optimize_status()["installation_id"]
    assert svc.optimize_new_installation_id()["installation_id"] != first
    assert svc.optimize_set_label("  Build server ")["label"] == "Build server"


def test_shared_results_are_scoped_to_workspaces_the_person_can_open(svc):
    from fabric_desktop.optimize.shared import Shared
    with pytest.raises(ValueError, match="Connect this PC"):
        svc.optimize_findings()
    conn = svc.optimize_create("w1")["connection"]
    me = Shared(LocalLakehouse(svc.lakes / "w1" / conn["lakehouse_id"]), svc.optimize_installation)
    rec = {"rule_id": "small_files", "target": "dbo.t", "kind": "optimize"}
    me.record_analysis({"run_id": "r1"}, [], [
        {"workspace_id": "w1", "item_id": "i1", "rule_id": "small_files", "target": "dbo.t", "severity": "high"},
        {"workspace_id": "w9", "item_id": "i9", "rule_id": "small_files", "target": "dbo.t", "severity": "high"}],
        [rec | {"workspace_id": "w1", "item_id": "i1"}, rec | {"workspace_id": "w9", "item_id": "i9"}])
    assert [f["artifact_key"] for f in svc.optimize_findings()] == ["w1/i1"]
    recs = svc.optimize_recommendations()
    assert len(recs) == 1
    out = svc.optimize_set_recommendation(recs[0]["recommendation_id"], "dismissed", "Not ours")
    assert out["state"] == "dismissed"
    assert svc.optimize_recommendations() == [] and len(svc.optimize_recommendations(include_closed=True)) == 1


def test_read_only_connection_can_read_but_not_change_results(svc):
    svc.optimize_create("w1")
    reg = svc.optimize_registry
    conn = reg.active() | {"access": "read"}
    reg.add(conn)
    assert svc.optimize_findings() == []
    with pytest.raises(PermissionError, match="read-only"):
        svc.optimize_set_recommendation("nope", "dismissed")


def test_other_features_reach_the_central_lakehouse_for_their_own_tables(svc, monkeypatch):
    import polars as pl

    from fabric_desktop.optimize import schema
    fam = schema.Family("fdtst_", "Test", [schema.Migration(1, "Create", {
        "event": {"what": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})])
    monkeypatch.setitem(schema.FAMILIES, fam.prefix, fam)
    assert svc.central_lakehouse() is None
    with pytest.raises(ValueError, match="central lakehouse"):
        svc.central_family(fam)
    svc.optimize_create("w1", workspace_name="Platform")
    conn = svc.central_lakehouse()
    assert conn["lakehouse_name"] == "FabricDesktop_Central" and conn["access"] == "write"
    with pytest.raises(PermissionError, match="Confirm"):
        svc.central_family(fam)  # adding a family's tables asks first
    lake, me = svc.central_family(fam, approve=True)
    setup.write(lake, me, "event", [{"record_id": "e1", "what": "opened"}], family=fam)
    assert lake.rows("fdtst_event")["what"].to_list() == ["opened"]
    svc.optimize_registry.add(dict(conn, access="read"))
    with pytest.raises(PermissionError, match="read"):
        svc.central_family(fam)


def xray_result(partial=False):
    where = {"workspace_id": "w1", "workspace_name": "Platform", "lakehouse_id": "lh-sales", "lakehouse_name": "Sales"}
    return {"scanned": "2026-10-20T09:00:00+00:00", "partial": partial,
            "lakehouse_rows": [where | {"tables": 1, "bytes": 10, "files": 900, "issues": 1}],
            "findings": [
                where | {"schema": "dbo", "table": "orders", "kind": "small_files", "severity": "high",
                         "title": "dbo.orders has 900 small files", "detail": "d", "action": "optimize",
                         "files": 900, "bytes": 10},
                {"kind": "access", "severity": "medium", "title": "Guest Ana has access", "workspace_id": "w1",
                 "person": "Ana", "action": "access"},
                {"kind": "cost", "severity": "low", "title": "nb cost", "notebook": "nb", "cost": 1.0}]}


def test_xray_findings_reach_optimize_without_people_or_cost():
    from fabric_desktop import xray
    out = xray.optimize_records(xray_result(partial=True))
    assert out["run"]["status"] == "partial" and out["run"]["run_id"] == "xray-2026-10-20T09:00:00+00:00"
    assert out["artifacts"] == [{"workspace_id": "w1", "item_id": "lh-sales", "item_type": "Lakehouse",
                                 "display_name": "Sales"}]
    [finding] = out["findings"]  # the guest's name and this PC's cost estimate stay on this PC
    assert finding["rule_id"] == "xray.small_files" and finding["target"] == "dbo.orders"
    assert json.loads(finding["evidence"]) == {"files": 900, "bytes": 10}
    assert [r["kind"] for r in out["recommendations"]] == ["optimize"]


def test_an_xray_scan_is_added_to_the_connected_central_lakehouse(svc):
    assert svc._xray_share(xray_result()) is None  # not connected: nothing is written anywhere
    svc.optimize_create("w1", workspace_name="Platform")
    shared = svc._xray_share(xray_result())
    assert shared["findings"] == 1 and "error" not in shared
    assert svc._xray_share(xray_result())["findings"] == 1  # the same scan again adds nothing new
    [found] = svc.optimize_findings()
    assert found["title"] == "dbo.orders has 900 small files" and found["occurrences"] == 1
    assert [r["kind"] for r in svc.optimize_recommendations()] == ["optimize"]
    conn = svc.central_lakehouse()
    svc.optimize_registry.add(dict(conn, access="read"))
    assert svc._xray_share(xray_result()) is None  # read-only: X-ray doesn't try
