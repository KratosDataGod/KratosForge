"""Mutation batch 363: excellence_service.py (docs/quality/mutation-testing.md). The survivors were the
read limits (50 lakehouses, 30 models) checked only against the module's own constants, the
demonstration fixture's usage rows and hidden key, how unreadable lakehouses and a missing model.bim
are reported, the reads listed when a workspace has no capacity, and the capacity's name in usage."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop import excellence_service as es
from fabric_desktop.excellence import inspect_items
from fabric_desktop.service import Service

WSID = "11111111-2222-3333-4444-555555555555"
CAP = "cccccccc-2222-3333-4444-555555555555"


class FakeFabric:
    def __init__(self, workspace: dict | None = None, capacities: list | None = None, lakehouses: int = 0,
                 fail: set[str] | None = None):
        self.calls: list[tuple] = []
        self.workspace = workspace if workspace is not None else {"id": WSID, "displayName": "Sales", "capacityId": CAP}
        self.capacities = capacities if capacities is not None else []
        self.lakehouses, self.fail = lakehouses, fail or set()

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return dict(self.workspace)

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": f"lh{i}", "type": "Lakehouse", "displayName": f"LH_{i}"} for i in range(self.lakehouses)]

    def list_role_assignments(self, ws):
        return []

    def list_capacities(self):
        return list(self.capacities)

    def get_lakehouse(self, w, lh):
        self.calls.append(("get_lakehouse", w, lh))
        if lh in self.fail:
            raise RuntimeError("403 Forbidden")
        return {"id": lh, "properties": {}}


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(**kw):
        fake = FakeFabric(**kw)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def read(r: dict, name: str) -> dict | None:
    got = [x for x in r["reads"] if x["read"] == name]
    assert len(got) <= 1
    return got[0] if got else None


# ---- the limits, by number


def test_fifty_lakehouses_are_read_and_the_fifty_first_isnt():
    reads: list[dict] = []
    fake = FakeFabric()
    items = [{"id": f"lh{i}", "type": "Lakehouse", "displayName": f"LH_{i}"} for i in range(51)]
    got = es._lakehouses(fake, WSID, items, reads)
    assert len(got) == 50 and "lh50" not in got
    assert reads == [{"read": "lakehouse properties", "ok": False, "problem": "1 past the first 50 weren't read."}]


def test_thirty_models_are_read_and_the_thirty_first_isnt():
    opt = {"workloads": [{"item_id": f"m{i}", "type": "SemanticModel", "name": f"M{i}"} for i in range(31)]}
    rows, entry = es._models(opt, lambda _item: {"model": {"tables": []}})
    assert [r["inspected"].get("read") is False for r in rows].count(True) == 1
    assert rows[30]["inspected"] == {"read": False, "problem": "Not read: only the first 30 semantic models are read "
                                                              "in one assessment."}
    assert entry == {"read": "semantic model definitions", "ok": False,
                     "problem": "1 over the limit of 30 weren't read.", "count": 30}


# ---- lakehouses and models that can't be read


def test_no_lakehouse_means_no_lakehouse_read_is_listed():
    reads: list[dict] = []
    assert es._lakehouses(FakeFabric(), WSID, [{"id": "n", "type": "Notebook"}], reads) == {}
    assert es._lakehouses(FakeFabric(), WSID, None, reads) == {}
    assert reads == []


def test_one_readable_lakehouse_is_an_ok_read():
    reads: list[dict] = []
    es._lakehouses(FakeFabric(), WSID, [{"id": "lh0", "type": "Lakehouse"}], reads)
    assert reads == [{"read": "lakehouse properties", "ok": True, "problem": None}]


def test_three_unreadable_lakehouses_are_all_named_and_a_fourth_is_counted():
    items = [{"id": f"lh{i}", "type": "Lakehouse", "displayName": f"LH_{i}"} for i in range(4)]
    reads: list[dict] = []
    es._lakehouses(FakeFabric(fail={"lh0", "lh1", "lh2"}), WSID, items, reads)
    assert reads[0]["problem"] == ("3 of 4 couldn't be read: LH_0 (RuntimeError: 403 Forbidden); LH_1 (RuntimeError: 403 "
                                   "Forbidden); LH_2 (RuntimeError: 403 Forbidden).")
    reads = []
    es._lakehouses(FakeFabric(fail={"lh0", "lh1", "lh2", "lh3"}), WSID, items, reads)
    assert reads[0]["problem"].endswith("; LH_2 (RuntimeError: 403 Forbidden) and 1 more.")


def test_a_definition_without_model_bim_is_a_value_error_saying_so():
    with pytest.raises(ValueError, match="model.bim"):
        es._model_bim({"definition": {"parts": [{"path": "definition.pbism", "payload": ""}]}})
    bim = base64.b64encode("﻿{\"model\": {}}".encode("utf-8")).decode()
    assert es._model_bim({"definition": {"parts": [{"path": "model.bim", "payload": bim}]}}) == {"model": {}}


def test_a_model_that_cant_be_read_is_marked_not_read():
    def boom(_item):
        raise RuntimeError("404")
    rows, entry = es._models({"workloads": [{"item_id": "m", "type": "SemanticModel"}]}, boom)
    assert rows[0]["inspected"]["read"] is False and rows[0]["inspected"]["problem"].startswith("Couldn't be read")
    assert entry["ok"] is False and entry["problem"] == "1 couldn't be read."


# ---- the engine's inventory


def test_a_workspace_the_engine_couldnt_read_has_no_items():
    assert es._items({"workspaces": [{"id": "w", "problem": "403"}], "workloads": []}) is None
    assert es._items({"workloads": [{"item_id": "i", "type": "Notebook", "name": "n"}]}) == [
        {"id": "i", "type": "Notebook", "displayName": "n"}]


# ---- the demonstration fixture


def test_the_fixtures_usage_rows_are_thirty_hours_of_refreshes_and_three_runs():
    items, ops = es._fixture_usage("Sales")
    assert [r["key"] for r in items["rows"]] == ["fixture:cap:0", "fixture:cap:1", "fixture:cap:2"]
    refreshes = [r for r in ops["rows"] if r["operation"] == "Dataset On-Demand Refresh"]
    assert [r["key"] for r in refreshes] == [f"fixture:cap:{100 + h}" for h in range(30)]
    assert [r["start"] for r in refreshes][23:25] == ["2026-10-31T23:00:00+00:00", "2026-10-31T00:30:00+00:00"]
    assert refreshes[0]["cu"]["period"] == {"start": "2026-10-31T00:00:00+00:00", "end": "2026-10-31T00:00:00+00:00"}
    assert [r["key"] for r in ops["rows"] if r["operation"] != "Dataset On-Demand Refresh"] == [
        "fixture:cap:200", "fixture:cap:201", "fixture:cap:202"]


def test_the_demo_model_hides_one_fact_key_and_shows_the_other(make):
    r = make().excellence_assess(demo=True)
    got = [o for o in r["outcomes"] if o["control_id"] == "FEA-SM-006"]
    assert [o["outcome"] for o in got] == ["fail"]
    assert "1 found: Orders.customer_sk." in json.dumps(got)


def test_the_demo_is_assessed_at_the_fixtures_time_with_its_description_read(make):
    r = make().excellence_assess(demo=True)
    assert r["assessed_at"] == "2026-11-01T00:00:00+00:00"
    assert [o["outcome"] for o in r["outcomes"] if o["control_id"] == "FEA-GOV-001"] == ["pass"]


# ---- the reads of a real assessment


def test_a_workspace_read_without_an_id_keeps_the_one_asked_for(make):
    s = make(workspace={"displayName": "Sales", "capacityId": CAP})
    r = s.excellence_assess(workspace_id=WSID)
    assert [(w["id"], w["name"]) for w in r["scope"]["workspaces"]] == [(WSID, "Sales")]
    for name in ("workspace settings", "role assignments", "capacities"):
        assert read(r, name) == {"read": name, "ok": True, "problem": None}


def test_the_engine_looks_back_thirty_days_unless_asked(make, monkeypatch):
    seen = []
    real = Service._optimization_assess

    def spy(self, *a, **kw):
        seen.append(kw.get("days"))
        return real(self, *a, **kw)
    monkeypatch.setattr(Service, "_optimization_assess", spy)
    s = make()
    s.excellence_assess(workspace_id=WSID)
    s.excellence_assess(workspace_id=WSID, days=7)
    assert seen == [30, 7]


def test_a_workspace_with_no_capacity_lists_no_capacity_or_usage_read(make):
    r = make(workspace={"id": WSID, "displayName": "Sales"}).excellence_assess(workspace_id=WSID)
    assert read(r, "capacity") is None and read(r, "imported Capacity Metrics exports") is None


def test_a_capacity_that_isnt_visible_is_a_failed_read(make):
    r = make().excellence_assess(workspace_id=WSID)
    assert read(r, "capacity")["ok"] is False


def test_usage_names_the_capacity_whatever_the_case_of_its_id(make, monkeypatch):
    usage = type("U", (), {"all": lambda self: es._fixture_usage("Sales")})()
    monkeypatch.setattr(Service, "_usage", property(lambda self: usage))
    s = make(capacities=[{"id": CAP.upper(), "displayName": "Finance F2", "sku": "F2", "state": "Active"}])
    r = s.excellence_assess(workspace_id=WSID)
    usage = [e for e in r["evidence"] if e["kind"] == "capacity.usage"]
    assert [e["subject"]["name"] for e in usage] == ["Finance F2"]
    assert read(r, "imported Capacity Metrics exports") == {"read": "imported Capacity Metrics exports", "ok": True,
                                                              "problem": None}


def test_inspect_items_is_what_the_engine_inspects_with(make, monkeypatch):
    seen = []
    real = Service._optimization_assess

    def spy(self, *a, **kw):
        seen.append(kw.get("inspect"))
        return real(self, *a, **kw)
    monkeypatch.setattr(Service, "_optimization_assess", spy)
    make().excellence_assess(workspace_id=WSID)
    assert seen == [inspect_items.inspect]
