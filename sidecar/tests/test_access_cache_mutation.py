"""The access cache (access_cache.py), pinned where mutation testing found gaps: snapshot ids, the
names a scan keeps, and how view() builds each workspace from the scans. Pure functions only: no
lakehouse, no Fabric."""

from __future__ import annotations

import hashlib
import json

import polars as pl

from fabric_desktop import access_cache as ac
from fabric_desktop.access_cache import LAKEHOUSE, WORKSPACE


def scan(kind, ws, at, item="", state="ok", snapshot="", ws_name="", item_name="", problem=""):
    return {"kind": kind, "workspace_id": ws, "workspace_name": ws_name, "item_id": item, "item_name": item_name,
            "snapshot_id": snapshot, "state": state, "problem": problem, "observed_at": at}


def person(snapshot, pid, name, kind="User", guest=False, role="Viewer"):
    return {"snapshot_id": snapshot, "workspace_id": "w1", "principal_id": pid, "principal_name": name,
            "principal_type": kind, "guest": guest, "role": role}


def test_no_scans_means_no_snapshots():
    assert ac.latest_snapshots(None) == {}
    assert ac.latest_snapshots(pl.DataFrame(schema={"state": pl.String})) == {}


def test_a_snapshot_id_is_24_hex_characters_of_the_rows_sha256():
    rows = [{"principal_id": "u1", "role": "Admin"}]
    body = json.dumps([WORKSPACE, "w1", "", sorted(json.dumps(r, sort_keys=True) for r in rows)])
    assert ac._fingerprint(WORKSPACE, "w1", "", rows) == hashlib.sha256(body.encode()).hexdigest()[:24]
    assert len(ac._fingerprint(WORKSPACE, "w1", "", rows)) == 24


def test_a_snapshot_id_ignores_the_order_of_keys_and_rows():
    a = [{"principal_id": "u1", "role": "Admin"}, {"principal_id": "u2", "role": "Viewer"}]
    b = [{"role": "Viewer", "principal_id": "u2"}, {"role": "Admin", "principal_id": "u1"}]
    assert ac._fingerprint(WORKSPACE, "w1", "", a) == ac._fingerprint(WORKSPACE, "w1", "", b)


def test_a_scan_keeps_the_workspace_and_lakehouse_names_or_blanks():
    r = ac.Refresh({}, "2026-11-16T10:00:00Z", "pc")
    r.add(LAKEHOUSE, {"id": "w1", "name": "Sales"}, {"id": "lh1", "name": "LH_Sales"}, [])
    r.add(WORKSPACE, {"id": "w2"}, None, None, problem="403")
    assert [(s["workspace_name"], s["item_name"]) for s in r.scans] == [("Sales", "LH_Sales"), ("", "")]


def test_a_workspace_seen_only_through_a_lakehouse_is_neither_stale_nor_known():
    scans = pl.DataFrame([scan(LAKEHOUSE, "w1", "t1", item="lh1", ws_name="Sales", item_name="LH")])
    [w] = ac.view(scans, None, None, {"w1"})
    assert w["stale"] is False and w["known"] is False and w["checked_at"] is None and w["people"] == []
    assert [lh["item_id"] for lh in w["lakehouses"]] == ["lh1"]


def test_the_workspace_scans_name_wins_unless_it_is_blank():
    scans = pl.DataFrame([scan(LAKEHOUSE, "w1", "t1", item="lh1", ws_name="Old"),
                          scan(WORKSPACE, "w1", "t2", ws_name="New", snapshot="s1"),
                          scan(LAKEHOUSE, "w2", "t1", item="lh2", ws_name="Kept"),
                          scan(WORKSPACE, "w2", "t2", ws_name="", snapshot="s2")])
    assert [w["workspace_name"] for w in ac.view(scans, None, None, {"w1", "w2"})] == ["Kept", "New"]


def test_the_summary_counts_people_groups_apps_and_guests():
    scans = pl.DataFrame([scan(WORKSPACE, "w1", "t1", snapshot="s1", ws_name="Sales")])
    people = pl.DataFrame([person("s1", "u1", "Ann"), person("s1", "u2", "Bo", guest=True), person("s1", "u3", "Cy"),
                           person("s1", "g1", "Readers", "Group"), person("s1", "a1", "Robot", "ServicePrincipal")])
    [w] = ac.view(scans, people, None, {"w1"})
    assert w["summary"] == {"people": 3, "groups": 1, "apps": 1, "guests": 1}


def test_a_lakehouse_roles_lists_are_read_back_from_json():
    scans = pl.DataFrame([scan(LAKEHOUSE, "w1", "t1", item="lh1", snapshot="s1", item_name="LH")])
    roles = pl.DataFrame([{"snapshot_id": "s1", "workspace_id": "w1", "item_id": "lh1", "role_name": "Readers",
                           "paths": '["Tables/a"]', "entra": '["oid-1"]', "item_members": '["Read"]'},
                          {"snapshot_id": "s1", "workspace_id": "w1", "item_id": "lh1", "role_name": "Empty",
                           "paths": "", "entra": "", "item_members": ""}])
    [w] = ac.view(scans, None, roles, {"w1"})
    assert w["lakehouses"][0]["roles"] == [
        {"name": "Empty", "paths": [], "entra": [], "item_members": []},
        {"name": "Readers", "paths": ["Tables/a"], "entra": ["oid-1"], "item_members": ["Read"]}]
