"""Optimize's app calls: create, connect, switch, disconnect, and the IT policy switch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fabric_desktop import app_settings, audit, server
from fabric_desktop.fabric_api import FabricError
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


def test_central_restrict_keeps_a_family_from_default_reader_after_asking(svc, monkeypatch):
    import polars as pl

    from fabric_desktop.optimize import schema
    fam = schema.Family("fdtst_", "Access cache", [schema.Migration(1, "Create", {
        "who": {"name": pl.String}, "schema_version": schema.TABLES_V1["schema_version"]})], schema="access")
    monkeypatch.setitem(schema.FAMILIES, fam.prefix, fam)
    everyone = {"name": "DefaultReader", "decisionRules": [{"effect": "Permit", "permission": [
        {"attributeName": "Path", "attributeValueIncludedIn": ["*"]},
        {"attributeName": "Action", "attributeValueIncludedIn": ["Read"]}]}],
        "members": {"fabricItemMembers": [{"itemAccess": ["ReadAll"]}]}}
    store = {"roles": [everyone]}
    puts = []
    store["etag"] = '"v1"'

    def put(self, ws, lh, r, etag=None):
        if etag != store["etag"]:
            raise FabricError("PUT failed with 412", "PreconditionFailed", 412)
        puts.append(r)
        # Fabric returns each role with an id and eTag of its own, and a new list ETag
        store.update(roles=[dict(x, id=x.get("id", f"id-{i}"), eTag='"r"') for i, x in enumerate(r)],
                     etag=f'"v{len(puts) + 1}"')
    api = type("Api", (), {"list_data_access_roles_tagged": lambda self, ws, lh: (list(store["roles"]), store["etag"]),
                           "put_data_access_roles": put})()
    monkeypatch.setattr(Service, "_scoped", lambda self, scopes: api, raising=False)
    monkeypatch.setattr(svc.auth, "signed_in_tenant", lambda: "tenant", raising=False)
    svc.optimize_create("w1", workspace_name="Platform")
    lake, _me = svc.central_family(fam, approve=True)
    with pytest.raises(PermissionError, match="DefaultReader no longer reads /Tables/access.*later.*Confirm") as asked:
        svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"])
    assert asked.value.changes[1] == "New role AccessCacheReaders reads /Tables/access"
    assert puts == []  # asking changes nothing
    with pytest.raises(PermissionError, match="changed since you looked"):  # not what the person was shown
        svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"], approve=True, shown=["something else"])
    store["etag"], stale = '"v0"', store["etag"]
    api.list_data_access_roles_tagged = lambda ws, lh: (list(store["roles"]), stale)  # changed after this read
    with pytest.raises(PermissionError, match="changed since you looked"):
        svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"], approve=True)
    del api.list_data_access_roles_tagged
    assert puts == []
    out = svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"], approve=True, shown=asked.value.changes)
    assert out["changed"] and out["default_reader_paths"] == ["/Tables/dbo", "/Files"]
    assert [r["name"] for r in puts[0]] == ["DefaultReader", "AccessCacheReaders"]
    audit = lake.rows("fdopt_audit").filter(pl.col("action") == "restrict")
    assert audit["target"].to_list() == ["/Tables/access role AccessCacheReaders"]
    assert svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"])["changed"] is False  # nothing left to do
    conn = svc.central_lakehouse()
    svc.optimize_registry.add(dict(conn, schemas=False))
    with pytest.raises(ValueError, match="no schemas"):
        svc.central_restrict(fam, "AccessCacheReaders", ["obj-1"], approve=True)
