"""Capacity and FinOps controls from imported Capacity Metrics exports (FEA-202): each control has a pass,
a fail and a not-evaluated case, no import is never a pass, a field the export lacks is never a pass,
and the demo stays labelled and makes no Fabric call."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from fabric_desktop import usage
from fabric_desktop.excellence import adapters, assess, catalog, catalog_capacity as cc
from fabric_desktop.service import Service
from fabric_desktop.usage.store import Store

AT = "2026-11-18T02:00:00+00:00"
CAP = "cccccccc-2222-3333-4444-555555555555"
WSID = "11111111-2222-3333-4444-555555555555"
WS = "Sales"
DAY = 86400.0


def cap_state(sku: str = "F64", state: str = "Active") -> list[dict]:
    return [{"id": CAP, "displayName": "Prod F64", "sku": sku, "state": state, "region": "West Europe"}]


def op(n: int, item: str, cu: float, start: str, *, op_name: str | None = "Notebook Run", status: str | None = "Success",
       end: str | None = None, workspace: str = WS) -> dict:
    return {"key": f"k{n}", "workspace": workspace, "item": item, "operation": op_name, "status": status, "start": start,
            "end": end, "cu": {"value": cu}}


def ops_import(rows: list[dict], id_: str = "u-0000000000000001") -> dict:
    return {"id": id_, "provider": "metrics_app", "layout": "operations", "file_name": "ops.csv", "rows": rows}


def items_import(cu: float, days: float = 14, workspace: str = WS, id_: str = "u-0000000000000002") -> dict:
    start = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=days)
    return {"id": id_, "provider": "metrics_app", "layout": "items", "file_name": "items.csv",
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "rows": [{"key": "i1", "workspace": workspace, "item": "nb", "cu": {"value": cu}}]}


def refreshes(item: str, n: int, day: str = "2026-10-31", start_key: int = 0) -> list[dict]:
    return [op(start_key + i, item, 100.0, f"{day}T{i % 24:02d}:{(i // 24) * 30:02d}:00+00:00",
               op_name="Dataset On-Demand Refresh") for i in range(n)]


SPAN = [op(900, "nb", 10.0, "2026-10-30T00:00:00+00:00"), op(901, "nb", 10.0, "2026-11-01T00:00:00+00:00")]


def run(imports, capacities=None) -> dict:
    caps = cap_state() if capacities is None else capacities
    records = adapters.capacity(CAP, caps, collected_at=AT) + cc.usage(CAP, "Prod F64", WS, imports, collected_at=AT)
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [], "capacities": [CAP]})


def outcome(snap: dict, cid: str) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid]
    assert len(got) == 1, got
    return got[0]


def test_the_capacity_controls_are_complete_and_registered():
    r = catalog.registry()
    for c in cc.CONTROLS:
        assert r.get(c.id) is c and c.scope == "capacity" and c.dimension == "capacity_finops"
        assert c.references and all(ref["url"].startswith("https://learn.microsoft.com/") for ref in c.references)
        assert "Severity " + c.severity in (c.evaluate.__doc__ or "")
        assert "money saved" not in c.remediation["benefit"].lower().replace("not money saved", "")


# ---- FEA-CAP-002 failed operations


@pytest.mark.parametrize("failed_cu, expect", [(5.0, "pass"), (20.0, "fail")])
def test_failed_operations_pass_and_fail(failed_cu, expect):
    rows = [op(1, "nb", 95.0, "2026-10-31T01:00:00+00:00"),
            op(2, "pl", failed_cu, "2026-10-31T02:00:00+00:00", status="Failure")]
    o = outcome(run([ops_import(rows)]), "FEA-CAP-002")
    assert o["outcome"] == expect and o["basis"] == "measured"
    if expect == "fail":
        assert "capacity freed" in o["benefit"].lower() and "estimated" in o["benefit"]
        assert "isn't money saved" in o["benefit"]


def test_failed_operations_without_a_status_column_are_not_evaluated():
    rows = [op(1, "nb", 95.0, "2026-10-31T01:00:00+00:00", status=None)]
    o = outcome(run([ops_import(rows)]), "FEA-CAP-002")
    assert o["outcome"] == "not_evaluated" and "Status" in o["needs"]


def test_failed_operations_from_item_totals_only_are_not_evaluated():
    o = outcome(run([items_import(1000.0)]), "FEA-CAP-002")
    assert o["outcome"] == "not_evaluated" and "Timepoint" in o["needs"]


# ---- FEA-CAP-003 refreshes


def test_refreshes_pass_and_fail():
    bad = outcome(run([ops_import(refreshes("Sales model", 25) + SPAN)]), "FEA-CAP-003")
    assert bad["outcome"] == "fail" and "Sales model (25 on 2026-10-31)" in bad["text"]
    assert "in one day, 1 time:" in bad["text"]
    assert "isn't money saved" in bad["benefit"]
    ok = outcome(run([ops_import(refreshes("Sales model", 24) + SPAN)]), "FEA-CAP-003")
    assert ok["outcome"] == "pass" and ok["basis"] == "measured"


def test_refreshes_counted_per_day_not_over_the_window():
    rows = refreshes("m", 20, "2026-10-30") + refreshes("m", 20, "2026-10-31", start_key=100)
    assert outcome(run([ops_import(rows)]), "FEA-CAP-003")["outcome"] == "pass"


def test_refreshes_in_less_than_a_day_cant_pass_but_can_fail():
    short = outcome(run([ops_import(refreshes("m", 3))]), "FEA-CAP-003")
    assert short["outcome"] == "not_evaluated" and "a day" in short["needs"]
    assert outcome(run([ops_import(refreshes("m", 30))]), "FEA-CAP-003")["outcome"] == "fail"


def test_refreshes_without_an_operation_column_are_not_evaluated():
    rows = [op(1, "nb", 1.0, "2026-10-30T00:00:00+00:00", op_name=None),
            op(2, "nb", 1.0, "2026-11-01T00:00:00+00:00", op_name=None)]
    o = outcome(run([ops_import(rows)]), "FEA-CAP-003")
    assert o["outcome"] == "not_evaluated" and "Operation" in o["needs"]


# ---- FEA-CAP-004 utilization


def test_utilization_pass_and_fail():
    available = 64 * 14 * DAY
    bad = outcome(run([items_import(0.05 * available)]), "FEA-CAP-004")
    assert bad["outcome"] == "fail" and bad["basis"] == "measured" and "5.0%" in bad["text"]
    assert len(bad["evidence_ids"]) == 2 and "Money is saved only when" in bad["benefit"] and "planner" not in bad["benefit"]
    ok = outcome(run([items_import(0.2 * available)]), "FEA-CAP-004")
    assert ok["outcome"] == "pass"


def test_utilization_needs_a_week_and_item_totals():
    short = outcome(run([items_import(1.0, days=3)]), "FEA-CAP-004")
    assert short["outcome"] == "not_evaluated" and "7 days" in short["needs"]
    ops_only = outcome(run([ops_import(SPAN)]), "FEA-CAP-004")
    assert ops_only["outcome"] == "not_evaluated" and "Compute" in ops_only["needs"]


@pytest.mark.parametrize("sku, state", [("FT1", "Active"), ("P1", "Active"), ("A2", "Active"), ("F64", "Paused")])
def test_utilization_doesnt_apply_to_trial_other_skus_or_paused(sku, state):
    assert outcome(run([items_import(1.0)], cap_state(sku, state)), "FEA-CAP-004")["outcome"] == "not_applicable"


# ---- no import, no match, no capacity


@pytest.mark.parametrize("imports", [None, [], [items_import(1.0, workspace="Another workspace")],
                                     [{"id": "u-0000000000000003", "provider": "cost_management", "layout": "cost_details",
                                       "rows": [{"workspace": WS, "cu": {"value": 1.0}}]}]])
def test_no_matching_import_is_not_evaluated_and_asks_for_one(imports):
    snap = run(imports)
    for cid in ("FEA-CAP-002", "FEA-CAP-003", "FEA-CAP-004"):
        o = outcome(snap, cid)
        assert o["outcome"] == "not_evaluated" and o["needs"] == cc.NEEDS, (cid, o)
    assert not [r for r in snap["evidence"] if r["kind"] == cc.KIND]


def test_an_invisible_capacity_is_not_evaluated_even_with_an_import():
    snap = run([items_import(1.0)], capacities=[])
    assert all(outcome(snap, c.id)["outcome"] == "not_evaluated" for c in cc.CONTROLS)


def test_usage_evidence_is_measured_local_and_names_its_files():
    rec = cc.usage(CAP, "Prod F64", "sales", [items_import(1.0)], collected_at=AT)[0]
    assert rec["confidence"] == "measured" and rec["kind"] == "capacity.usage" and "no Fabric call" in rec["source"]
    assert rec["payload"]["imports"][0]["file_name"] == "items.csv" and rec["subject"]["id"] == CAP


def test_a_tampered_usage_record_cant_decide():
    records = adapters.capacity(CAP, cap_state(), collected_at=AT) + cc.usage(CAP, None, WS, [items_import(1.0)],
                                                                                collected_at=AT)
    records[-1]["payload"]["items"][0]["cu_seconds"] = 1e12
    snap = assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [], "capacities": [CAP]})
    assert outcome(snap, "FEA-CAP-004")["outcome"] == "not_evaluated" and snap["evidence_rejected"]


def test_rows_read_by_the_real_importer_decide():
    text = "Workspace,Item kind,Item name,Operation,Start,End,Status,Total CU (s)\n" + "".join(
        f"Sales,SemanticModel,Model,Dataset On-Demand Refresh,2026-10-31 {h:02d}:{m:02d}:00,"
        f"2026-10-31 {h:02d}:{m + 1:02d}:00,Success,10\n" for h in range(24) for m in (0, 30))
    text += "Sales,Notebook,nb,Notebook Run,2026-11-01 08:00:00,2026-11-01 09:00:00,Failure,500\n"
    got = usage.read(text, "ops.csv", {"tz": "UTC", "date_order": "ymd"}, now=dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc))
    assert got["ready"], got["problem"]
    doc = {"id": "u-0000000000000009", "provider": "metrics_app", "layout": got["layout"]["id"], "file_name": "ops.csv",
           "rows": got["rows"]}
    snap = run([doc])
    assert outcome(snap, "FEA-CAP-003")["outcome"] == "fail"
    assert outcome(snap, "FEA-CAP-002")["outcome"] == "fail"


# ---- the engine call


class FakeFabric:
    def __init__(self):
        self.calls: list[tuple] = []

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return {"id": ws, "displayName": WS, "capacityId": CAP, "description": "d"}

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return []

    def list_role_assignments(self, ws):
        self.calls.append(("list_role_assignments", ws))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return cap_state()


@pytest.fixture
def make(tmp_path: Path, monkeypatch):
    def build():
        fake = FakeFabric()
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def _save(root: Path, doc: dict) -> None:
    folder = Store(root).folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc['id']}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_call_reads_the_local_imports(make):
    s = make()
    r = s.excellence_assess(workspace_id=WSID)
    assert outcome(r, "FEA-CAP-004")["outcome"] == "not_evaluated"
    note = [x for x in r["reads"] if x["read"] == "imported Capacity Metrics exports"][0]
    assert note["ok"] is True and "Import one" in note["problem"]
    _save(s.root, items_import(0.01 * 64 * 14 * DAY, id_="u-00000000000000aa") | {"imported_at": AT})
    r = s.excellence_assess(workspace_id=WSID)
    assert outcome(r, "FEA-CAP-004")["outcome"] == "fail"
    assert {c[0] for c in s.fake.calls} <= {"get_workspace", "list_items", "list_role_assignments", "list_capacities"}


def test_an_unreadable_import_store_is_reported_not_raised(make, monkeypatch):
    s = make()
    monkeypatch.setattr(Store, "all", lambda self: (_ for _ in ()).throw(OSError("disk token=abc.def.ghi")))
    r = s.excellence_assess(workspace_id=WSID)
    note = [x for x in r["reads"] if x["read"] == "imported Capacity Metrics exports"][0]
    assert note["ok"] is False and "abc.def.ghi" not in json.dumps(r)
    assert outcome(r, "FEA-CAP-002")["outcome"] == "not_evaluated"


def test_the_demo_shows_capacity_controls_labelled_and_without_fabric(make):
    s = make()
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and r["fixture"] is True
    assert outcome(r, "FEA-CAP-002")["outcome"] == "pass"
    assert outcome(r, "FEA-CAP-003")["outcome"] == "fail" and outcome(r, "FEA-CAP-004")["outcome"] == "fail"
    rec = [e for e in r["evidence"] if e["kind"] == cc.KIND][0]
    assert all("demonstration fixture" in i["file_name"] for i in rec["payload"]["imports"])
    assert all(f["fixture"] for f in r["findings"])
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert again["scores"] == r["scores"]


def test_refreshes_over_count_every_item_day_not_only_the_top_ones():
    rows = [r for i in range(25) for r in refreshes(f"m{i:02d}", 25, start_key=i * 100)] + SPAN
    bad = outcome(run([ops_import(rows)]), "FEA-CAP-003")
    assert bad["outcome"] == "fail" and "in one day, 25 times:" in bad["text"]


def test_overlapping_compute_exports_count_their_days_and_cu_once():
    two = [items_import(1.0, days=4, id_="u-0000000000000002"), items_import(1.0, days=4, id_="u-0000000000000003")]
    assert outcome(run([*two]), "FEA-CAP-004")["outcome"] == "not_evaluated"  # 4 days, not 8
    big = 64 * 14 * 86400 * 0.06  # 6% of an F64 over 14 days in each export
    both = [items_import(big, days=14, id_="u-0000000000000002"), items_import(big, days=14, id_="u-0000000000000003")]
    bad = outcome(run(both), "FEA-CAP-004")
    assert bad["outcome"] == "fail" and "6.0%" in bad["text"] and "from the imported rows" in bad["benefit"]
