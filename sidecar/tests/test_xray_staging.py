"""Leftover Dataflow Gen2 staging lakehouses in the Estate X-ray: found by the scan, checked again
live before Remove, and the newest one in a workspace never offered. Fabric and OneLake are faked."""

import base64
import datetime as dt

import pytest

from fabric_desktop import app_settings, xray
import fabric_desktop.xray_service as xs
from fabric_desktop.service import Service

OLD, MID, NEW = ("StagingLakehouseForDataflows_20260926144305", "StagingLakehouseForDataflows_20260926152759",
                 "StagingLakehouseForDataflows_20260926153358")


def _lh(ws, lid, name):
    return {"workspace_id": ws, "workspace_name": ws.upper(), "lakehouse_id": lid, "lakehouse_name": name}


def test_staging_names_and_when_they_were_made():
    assert xray.is_staging(NEW) and xray.is_staging("DataflowsStagingLakehouse")
    assert not xray.is_staging("StagingLakehouseForDataflows_2026") and not xray.is_staging("LH_StagingLakehouseForDataflows_20260926153358")
    assert xray.staging_created(OLD) == dt.datetime(2026, 9, 26, 14, 43, 5, tzinfo=dt.timezone.utc)
    assert xray.staging_created("DataflowsStagingLakehouse") is None
    assert xray.staging_created("StagingLakehouseForDataflows_20261399999999") is None  # not a real time


def test_older_staging_lakehouses_are_findings_and_the_newest_is_kept():
    found = xray.staging_findings([_lh("w1", "c", NEW), _lh("w1", "a", OLD), _lh("w1", "b", MID),
                                   _lh("w1", "x", "DataflowsStagingLakehouse"), _lh("w2", "d", OLD)])
    assert [(f["workspace_id"], f["lakehouse_name"]) for f in found] == [("w1", OLD), ("w1", MID)]  # w2 has only one
    future = xray.staging_findings([_lh("w1", "c", NEW), _lh("w1", "z", "StagingLakehouseForDataflows_29990101000000")])
    assert future == []  # a name dated ahead neither counts as newest nor is offered
    assert all(f["kind"] == "staging" and f["action"] == "remove_staging" and f["kept"] == NEW and f["kept_id"] == "c"
               for f in found)


class Fabric:
    lakehouses = {"ws": [{"id": "a", "displayName": OLD}, {"id": "b", "displayName": MID}, {"id": "c", "displayName": NEW},
                         {"id": "g", "displayName": "LH_Gold"}]}

    def __init__(self, deleted):
        self.deleted = deleted

    def list_workspaces(self):
        return [{"id": "ws", "displayName": "Sales"}]

    def list_lakehouses(self, ws):
        return self.lakehouses[ws]

    def list_role_assignments(self, ws):
        return []

    def delete_lakehouse(self, ws, lid):
        self.deleted.append((ws, lid))

    # Dataflows: df1 refreshed yesterday and names nothing; tests change FLOWS to try the rest.
    flows = None

    def list_items(self, ws):
        return [{"id": k, "type": "Dataflow", "displayName": v["name"]} for k, v in (self.flows or {}).items()] + [
            {"id": "g", "type": "Lakehouse", "displayName": "LH_Gold"}]

    def get_item_definition(self, ws, item):
        text = self.flows[item].get("definition", '{"queries": []}')
        if text is None:
            raise RuntimeError("legacy dataflow")
        return {"definition": {"parts": [{"path": "queryMetadata.json", "payload": base64.b64encode(text.encode()).decode(),
                                          "payloadType": "InlineBase64"}]}}

    def list_job_instances(self, ws, item):
        return self.flows[item].get("jobs", [])


def test_scan_reads_no_staging_tables_and_lists_the_leftovers():
    read = []

    def files(ws, lh, directory):
        read.append(lh)
        return []
    out = xray.Scanner(Fabric([]), files, lambda *a: {}).scan()
    assert set(read) == {"g"}  # only LH_Gold's folders were listed
    assert [f["kind"] for f in out["findings"]] == ["staging", "staging", "empty"]
    assert out["lakehouses"] == 4


@pytest.fixture
def svc(tmp_path, monkeypatch):
    s = Service(root=tmp_path / "app")
    deleted = []
    now = dt.datetime.now(dt.timezone.utc)
    ago = lambda days: (now - dt.timedelta(days=days)).isoformat(timespec="seconds")
    contents = {"a": [{"path": "Files/x.parquet", "is_dir": False, "size": 1000, "modified": ago(40)},
                      {"path": "Files/sub", "is_dir": True, "size": 0, "modified": ago(45)}],
                "b": [{"path": "Tables/t/part.parquet", "is_dir": False, "size": 10, "modified": ago(3)}],
                "c": []}
    broken = set()

    class Files:
        def __init__(self, _t):
            pass

        def list(self, ws, lh, directory, recursive=False):
            if lh in broken:
                raise OSError("OneLake didn't answer")
            assert recursive
            return [e for e in contents[lh] if e["path"].startswith(directory)]
    monkeypatch.setattr(xs, "FileStore", Files)
    flows = {"df1": {"name": "Sales load", "jobs": [{"status": "Completed", "startTimeUtc": ago(1)},
                                                   {"status": "Failed", "startTimeUtc": ago(0)}]}}
    monkeypatch.setattr(Fabric, "flows", flows)
    monkeypatch.setattr(xs, "FabricClient", lambda _t: Fabric(deleted))
    monkeypatch.setattr(Service, "auth", property(lambda self: type("A", (), {"storage_token": lambda _s: "s",
                                                                             "fabric_token": lambda _s: "f"})()), raising=False)
    s.flows, s.ago = flows, ago
    return s, deleted, broken


def test_preview_says_what_it_holds_and_writes_nothing(svc):
    s, deleted, _ = svc
    p = s.xray_staging_preview("ws", "a")
    assert p["ok"] and p["why"] == [] and p["kept"] == NEW
    assert (p["files"], p["bytes"]) == (1, 1000) and p["last_write"]
    assert deleted == []


def test_a_recent_write_the_newest_or_an_unreadable_one_is_never_removed(svc):
    s, deleted, broken = svc
    recent = s.xray_staging_preview("ws", "b")
    assert not recent["ok"] and "3 days ago" in recent["why"][0]
    newest = s.xray_staging_preview("ws", "c")
    assert not newest["ok"] and "newest" in newest["why"][0]
    gold = s.xray_staging_preview("ws", "g")
    assert not gold["ok"] and "isn't named like" in gold["why"][0]
    broken.add("a")
    assert not s.xray_staging_preview("ws", "a")["ok"]
    for lid, name in (("b", MID), ("c", NEW), ("g", "LH_Gold"), ("a", OLD)):
        with pytest.raises(ValueError, match="wasn't deleted"):
            s.xray_remove_staging("ws", lid, name)
    assert deleted == []


def test_remove_checks_again_and_deletes_only_that_lakehouse(svc):
    s, deleted, _ = svc
    with pytest.raises(ValueError, match="now called"):
        s.xray_remove_staging("ws", "a", MID)  # the name the person confirmed must still be its name
    with pytest.raises(ValueError, match="isn't in the workspace"):
        s.xray_remove_staging("ws", "zz", OLD)
    assert deleted == []
    s._save_settings(xray_removed={"old": "2026-01-01T00:00:00+00:00"})
    out = s.xray_remove_staging("ws", "a", OLD, "Sales", finding_id="staging|ws|a||||")
    assert list(s._settings()["xray_removed"]) == ["staging|ws|a||||"]  # older than 90 days: dropped
    assert deleted == [("ws", "a")] and out["removed_at"]
    s._xray_file.parent.mkdir(parents=True, exist_ok=True)
    found = {"id": "x", "kind": "staging", "workspace_id": "ws", "lakehouse_id": "a", "severity": "low", "title": "t"}
    annotated = s._xray_annotate({"scanned": "2026-10-26T00:00:00+00:00", "findings": [found]}, refresh=False)
    assert annotated["findings"][0]["fixed"] is True


def test_policy_can_turn_removing_off(svc, monkeypatch):
    s, deleted, _ = svc
    monkeypatch.setattr(app_settings, "value", lambda k: False if k == "allow_staging_cleanup" else True)
    p = s.xray_staging_preview("ws", "a")
    assert not p["ok"] and "allow_staging_cleanup" in p["why"][-1]
    with pytest.raises(ValueError):
        s.xray_remove_staging("ws", "a", OLD)
    assert deleted == []
    assert app_settings.RESTRICTED["allow_staging_cleanup"] is False


def test_every_dataflow_must_show_it_does_not_stage_there(svc):
    s, deleted, _ = svc
    p = s.xray_staging_preview("ws", "a")
    assert p["ok"] and p["dataflows"] == [{"name": "Sales load", "item_id": "df1", "last_refresh": p["dataflows"][0]["last_refresh"],
                                           "names_it": False, "ok": True}]
    cases = {
        "names it": {"name": "Named", "definition": '{"stagingLakehouseId": "A"}', "jobs": [{"status": "Completed", "startTimeUtc": s.ago(1)}]},
        "quarterly": {"name": "Quarterly", "jobs": [{"status": "Completed", "startTimeUtc": s.ago(60)}]},
        "never ran": {"name": "New", "jobs": [{"status": "Failed", "startTimeUtc": s.ago(1)}]},
        "legacy": {"name": "Old", "definition": None},
    }
    for label, flow in cases.items():
        s.flows["df2"] = flow
        p = s.xray_staging_preview("ws", "a")
        assert not p["ok"], label
        assert flow["name"] in p["why"][-1], (label, p["why"])
        with pytest.raises(ValueError, match="wasn't deleted"):
            s.xray_remove_staging("ws", "a", OLD)
    s.flows.clear()
    p = s.xray_staging_preview("ws", "a")
    assert not p["ok"] and "lists no Dataflow Gen2" in p["why"][-1]
    assert deleted == []
