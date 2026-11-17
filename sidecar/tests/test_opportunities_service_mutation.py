"""Mutation-killing tests for opportunities_service.py (batch 81): the read limits and the days
bound, the read budget at its edges, unreadable workspaces, items and histories, which workspaces a
capacity covers and in what order, and keeping and sharing assessments."""

from __future__ import annotations

import types

import pytest
from test_opportunities import LOAD, WS, _daily, _nb

from fabric_desktop import opportunities_service as ops
from fabric_desktop.opportunities import share
from fabric_desktop.service import Service

CAP = "cccccccc-2222-3333-4444-555555555555"


class Fabric:
    """Fabric reads only; ``items`` per workspace, and which reads fail."""

    def __init__(self, items=None, workspaces=None, got=None, fail=()):
        self.calls: list[tuple] = []
        self.items = items if items is not None else [{"id": "a", "type": "Notebook", "displayName": "a"}]
        self.workspaces = workspaces or [WS]
        self.got = got
        self.fail = set(fail)

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return self.got if self.got is not None else {"id": ws, "displayName": "Sales", "capacityId": "cap-1"}

    def list_workspaces(self):
        self.calls.append(("list_workspaces",))
        return self.workspaces

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        if "items" in self.fail:
            raise RuntimeError("503 Service Unavailable")
        return self.items

    def get_item_definition(self, ws, item, fmt=None):
        self.calls.append(("get_item_definition", ws, item, fmt))
        return _nb(LOAD)

    def list_job_instances(self, ws, item):
        self.calls.append(("list_job_instances", ws, item))
        if "jobs" in self.fail:
            raise RuntimeError("429 Too Many Requests")
        return _daily(2, 10)


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    holder = {}
    monkeypatch.setattr(Service, "fabric", property(lambda self: holder["fake"]))

    def make_(**kw):
        holder["fake"] = Fabric(**kw)
        s = Service(root=tmp_path / "app")
        s.fake = holder["fake"]
        return s
    return make_


def _nbs(n):
    return [{"id": f"n{i:03d}", "type": "Notebook", "displayName": f"n{i}"} for i in range(n)]


def _ws(i, name=None):
    return {"id": f"{i:08d}-2222-3333-4444-555555555555", "displayName": name or f"w{i:02d}", "capacityId": CAP}


# ---------------------------------------------------------------- limits and days


def test_the_limits_are_as_documented():
    assert (ops.MAX_WORKSPACES, ops.MAX_ITEMS, ops.WORKERS, ops.MAX_CALLS, ops.KEPT) == (25, 300, 4, 1500, 5)
    assert (ops.MIN_DAYS, ops.MAX_DAYS) == (1, 90)


def test_a_capacity_reads_at_most_25_workspaces_and_says_how_many_were_left_out(make):
    s = make(items=[], workspaces=[_ws(i) for i in range(26)])
    r = s.optimization_assess(capacity_id=CAP)
    assert len(r["workspaces"]) == 25 and r["scope"]["workspaces_left_out"] == 1
    s = make(items=[], workspaces=[_ws(i) for i in range(2)])
    assert s.optimization_assess(capacity_id=CAP)["scope"]["workspaces_left_out"] == 0


def test_a_capacitys_workspaces_are_read_in_name_order_ignoring_case(make):
    s = make(items=[], workspaces=[_ws(1, "beta"), _ws(2, "Alpha"), _ws(3, "gamma")])
    r = s.optimization_assess(capacity_id=CAP)
    assert [w["name"] for w in r["workspaces"]] == ["Alpha", "beta", "gamma"]


def test_one_workspace_keeps_the_id_name_and_capacity_fabric_gave(make):
    other = "ABCDEF01-2222-3333-4444-555555555555"
    s = make(items=[], got={"id": other, "displayName": "Sales", "capacityId": "cap-9"})
    r = s.optimization_assess(workspace_id=WS["id"])
    assert r["workspaces"][0] | {"problem": None} == {"id": other, "name": "Sales", "capacity_id": "cap-9", "problem": None}
    assert r["scope"]["workspaces_left_out"] == 0
    s = make(items=[], got={})
    assert s.optimization_assess(workspace_id=WS["id"])["workspaces"][0]["id"] == WS["id"]


@pytest.mark.parametrize("asked,used", [(0, 1), (1, 1), (90, 90), (91, 90)])
def test_days_are_kept_between_1_and_90(make, asked, used):
    assert make(items=[]).optimization_assess(workspace_id=WS["id"], days=asked)["days"] == used


def test_days_default_to_30_and_the_demo_uses_30(make):
    s = make(items=[])
    assert s.optimization_assess(workspace_id=WS["id"])["days"] == 30
    assert s.optimization_assess(demo=True)["days"] == 30


def test_past_300_items_the_rest_are_listed_as_not_read(make):
    s = make(items=_nbs(301))
    r = s.optimization_assess(workspace_id=WS["id"])
    assert r["workspaces"][0]["problem"] == "1 item(s) past the first 300 weren't read."
    assert len([c for c in s.fake.calls if c[0] == "get_item_definition"]) == 300


def test_a_small_workspace_has_no_problem(make):
    s = make(items=_nbs(2) + [{"id": "l", "type": "Lakehouse", "displayName": "lh"}])
    assert s.optimization_assess(workspace_id=WS["id"])["workspaces"][0]["problem"] is None


def test_reads_run_on_four_workers(make, monkeypatch):
    seen = []
    real = ops.ThreadPoolExecutor

    def pool(max_workers):
        seen.append(max_workers)
        return real(max_workers=max_workers)
    monkeypatch.setattr(ops, "ThreadPoolExecutor", pool)
    make().optimization_assess(workspace_id=WS["id"])
    assert seen == [4]


# ---------------------------------------------------------------- the read budget


def test_an_unspent_budget_reports_1500_calls_and_not_reached(make):
    r = make().optimization_assess(workspace_id=WS["id"])
    b = r["scope"]["budget"]
    assert b == {"max_calls": 1500, "max_seconds": ops.MAX_SECONDS, "calls": 3, "reached": False}
    assert "note" not in r["scope"]


def test_a_budget_with_calls_left_still_stops_at_its_deadline(monkeypatch):
    monkeypatch.setattr(ops, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    b = ops._Budget(5, 0.0)
    assert b.take() is False and b.spent and b.used == 0


def test_a_budget_gives_exactly_its_calls(monkeypatch):
    monkeypatch.setattr(ops, "time", types.SimpleNamespace(monotonic=lambda: 100.0))
    b = ops._Budget(2, 10.0)
    assert [b.take(), b.take(), b.take()] == [True, True, False]
    assert b.used == 2 and b.spent


def test_no_budget_left_for_a_workspace_lists_it_as_not_read(make, monkeypatch):
    monkeypatch.setattr(ops, "MAX_CALLS", 0)
    s = make()
    r = s.optimization_assess(workspace_id=WS["id"])
    assert r["workspaces"][0]["problem"] == "Not read: the assessment's read budget ran out."
    assert [c[0] for c in s.fake.calls] == ["get_workspace"]


def test_no_budget_left_for_definitions_records_each_as_not_read(make, monkeypatch):
    monkeypatch.setattr(ops, "MAX_CALLS", 1)
    s = make(items=_nbs(2))
    r = s.optimization_assess(workspace_id=WS["id"])
    nr = r["coverage"]["not_read"]
    assert {n["item_id"] for n in nr} == {"n000", "n001"}
    assert all("budget" in n["problem"] for n in nr)
    assert r["coverage"]["history_not_read"] == 2


# ---------------------------------------------------------------- unreadable reads


def test_items_that_cant_be_listed_leave_the_workspace_unread(make):
    s = make(fail={"items"})
    r = s.optimization_assess(workspace_id=WS["id"])
    assert r["workspaces"][0]["problem"].startswith("Its items couldn't be listed: ")
    assert "503" in r["workspaces"][0]["problem"]


def test_a_history_that_cant_be_read_is_counted_not_raised(make):
    r = make(items=_nbs(2), fail={"jobs"}).optimization_assess(workspace_id=WS["id"])
    assert r["coverage"]["history_not_read"] == 2 and r["coverage"]["not_read"] == []


# ---------------------------------------------------------------- keeping and sharing


def test_an_assessment_is_kept_under_its_run_id(make):
    s = make()
    r = s.optimization_assess(workspace_id=WS["id"])
    assert r["share_id"] == share.run_id(r) and s._kept_assessments()[r["share_id"]] is r


def test_five_assessments_are_kept_and_a_sixth_drops_the_oldest(make):
    s = make(items=[])
    ids = [s.optimization_assess(workspace_id=WS["id"], days=d)["share_id"] for d in range(1, 6)]
    assert list(s._kept_assessments()) == ids
    sixth = s.optimization_assess(workspace_id=WS["id"], days=6)["share_id"]
    assert list(s._kept_assessments()) == ids[1:] + [sixth]


def test_sharing_rebuilds_the_rows_and_passes_approve_and_confirm_through(make, monkeypatch):
    s = make()
    r = s.optimization_assess(workspace_id=WS["id"])
    got = []

    def fake_share(rows, source, approve=False, confirm=""):
        got.append((rows, source, approve, confirm))
        return {"written": approve}
    monkeypatch.setattr(s, "_optimize_share_analysis", fake_share)
    assert s.optimization_share(r["share_id"]) == {"written": False}
    assert got[0] == (share.analysis_rows(r), "this assessment", False, "")
    assert s.optimization_share(r["share_id"], approve=True, confirm="ok") == {"written": True}
    assert got[1][2:] == (True, "ok")


def test_sharing_an_assessment_that_isnt_kept_says_so(make, monkeypatch):
    s = make()
    monkeypatch.setattr(s, "_optimize_share_analysis", lambda *a, **k: pytest.fail("nothing is shared"))
    with pytest.raises(ValueError, match=r"isn't kept on this PC any more .*5 newer ones"):
        s.optimization_share("oe-0000000000000000")
    s._kept_assessments()["None"] = {"scope": {}}
    with pytest.raises(ValueError, match="isn't kept"):
        s.optimization_share(None)
