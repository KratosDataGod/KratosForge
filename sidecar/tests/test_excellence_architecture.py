"""Architecture controls (FEA-203): pass, fail and not-evaluated fixtures for every control; name
heuristics are inferred evidence and never scored; the engine call adds only the lakehouse read the
app already makes, and the demo still calls nothing."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.excellence import adapters, assess, catalog, catalog_architecture as arch, controls as ct
from fabric_desktop.excellence_service import FIXTURE_LAKEHOUSES, MAX_LAKEHOUSE_READS
from fabric_desktop.service import Service

AT = "2026-11-18T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"


def ws(name: str = "Sales Prod") -> dict:
    return {"id": WSID, "displayName": name, "description": "d"}


def item(i: str, kind: str = "Lakehouse", name: str | None = None) -> dict:
    return {"id": i, "type": kind, "displayName": name or i}


def notebook(i: str, lakehouse: str | None, definition: str = "read") -> dict:
    return {"item_id": i, "type": "Notebook", "name": i, "workspace_id": WSID, "definition": definition,
            "default_lakehouse": lakehouse, "runs": {"read": False}}


def run(items: list[dict] | None, *, w: dict | None = None, opt: dict | None = None,
        lakehouses: dict | None = None) -> dict:
    w = w or ws()
    opt = opt or {"workloads": []}
    records = adapters.workspace(w, items, collected_at=AT) + arch.evidence(w, items, opt, lakehouses or {},
                                                                           collected_at=AT)
    return assess.assess(records, catalog.registry(), assessed_at=AT, scope={"workspaces": [{"id": WSID}]})


def outcome_of(snap: dict, cid: str, subject_id: str | None = None) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and (subject_id is None or o["subject"]["id"] == subject_id)]
    assert len(got) == 1, got
    return got[0]


def advice(o: dict) -> str:
    """The would-be outcome of an inferred control, which is never scored."""
    assert o["outcome"] == "not_evaluated" and o["basis"] == "inferred"
    return o["advisory"]["outcome"]


def test_architecture_controls_are_registered_and_complete():
    r = catalog.registry()
    ids = [c.id for c in r.controls("architecture") if c.id.startswith("FEA-ARCH-")]
    assert ids == ["FEA-ARCH-001", "FEA-ARCH-002", "FEA-ARCH-003", "FEA-ARCH-004"]
    for c in r.controls("architecture"):
        assert ct.problems(c) == []
        assert any(ref["url"].startswith("https://learn.microsoft.com/") for ref in c.references)
        assert "severity" in (c.evaluate.__doc__ or "").lower()
        assert c.accepts_inferred is False  # no name heuristic is scored


def test_words_split_names_without_false_matches():
    assert {"lh", "bronze"} <= arch.words("LH_Bronze")
    assert {"sales", "gold"} <= arch.words("SalesGold")
    assert "old" not in arch.words("Gold") and "test" not in arch.words("Contest")
    assert arch.environment("Sales Pre-Prod") == ("non_production", ["preprod"])
    assert arch.environment("Sales [PROD]")[0] == "production"
    assert arch.environment("Sales")[0] is None
    assert arch.environment("prod copy of dev")[0] is None  # says both


# ---- FEA-ARCH-001 one lakehouse per medallion layer (inferred)


def test_medallion_pass_fail_and_not_evaluated():
    good = run([item("b", name="LH_Bronze"), item("s", name="lh_silver"), item("g", name="GoldLakehouse")])
    assert advice(outcome_of(good, "FEA-ARCH-001")) == "pass"
    split = run([item("b1", name="bronze_sales"), item("b2", name="bronze_crm"), item("s", name="silver")])
    o = outcome_of(split, "FEA-ARCH-001")
    assert advice(o) == "fail" and "2 lakehouses are named bronze" in o["advisory"]["text"]
    mixed = run([item("x", name="bronze_silver_lh")])
    assert advice(outcome_of(mixed, "FEA-ARCH-001")) == "fail"
    # an advisory fail is a finding marked advisory, and it isn't scored
    f = [f for f in mixed["findings"] if f["rule"].startswith("FEA-ARCH-001")][0]
    assert f["advisory"] is True and mixed["counts"]["findings"] == 0
    assert mixed["scores"]["dimensions"]["architecture"]["controls"]["fail"] == 0
    unnamed = outcome_of(run([item("x", name="sales_lh")]), "FEA-ARCH-001")
    assert unnamed["outcome"] == "not_evaluated" and unnamed["advisory"] is None and "can't be told" in unnamed["text"]
    assert outcome_of(run(None), "FEA-ARCH-001")["outcome"] == "not_evaluated"
    assert outcome_of(run([item("n", "Notebook")]), "FEA-ARCH-001")["outcome"] == "not_applicable"


# ---- FEA-ARCH-002 schema-enabled lakehouses (verified)


def test_schemas_pass_fail_and_not_evaluated():
    items = [item("a"), item("b"), item("c")]
    snap = run(items, lakehouses={"a": {"properties": {"defaultSchema": "dbo"}}, "b": {"properties": {}}})
    ok, bad, unread = (outcome_of(snap, "FEA-ARCH-002", x) for x in "abc")
    assert ok["outcome"] == "pass" and ok["basis"] == "verified_configuration"
    assert bad["outcome"] == "fail" and bad["basis"] == "verified_configuration"
    assert unread["outcome"] == "not_evaluated" and unread["needs"] == "lakehouse.properties"
    cov = {c["item"]["id"]: c["state"] for c in snap["item_coverage"]}
    assert cov == {"a": "inspected", "b": "inspected", "c": "not_inspected"}
    assert [f["rule"].split("@")[0] for f in snap["findings"] if not f["advisory"]] == ["FEA-ARCH-002"]


# ---- FEA-ARCH-003 left-over items in production (inferred)


def test_leftovers_pass_fail_and_not_evaluated():
    clean = [item("l", name="LH_Gold"), item("n", "Notebook", "nb_load_orders")]
    assert advice(outcome_of(run(clean), "FEA-ARCH-003")) == "pass"
    dirty = clean + [item("t", "Notebook", "nb_orders_TEST"), item("c", "Lakehouse", "orders-copy"),
                     item("s", "Lakehouse", "StagingLakehouseForDataflows_20261118")]
    o = outcome_of(run(dirty), "FEA-ARCH-003")
    assert advice(o) == "fail" and "2 items" in o["advisory"]["text"]
    assert "StagingLakehouseForDataflows" not in o["advisory"]["text"]  # Fabric's own staging item
    unsure = outcome_of(run(dirty, w=ws("Sales")), "FEA-ARCH-003")
    assert unsure["outcome"] == "not_evaluated" and unsure["advisory"] is None and "production" in unsure["text"]
    assert outcome_of(run(dirty, w=ws("Sales Dev")), "FEA-ARCH-003")["outcome"] == "not_applicable"
    assert outcome_of(run(None), "FEA-ARCH-003")["outcome"] == "not_evaluated"


def test_an_unread_workspace_name_says_no_environment():
    recs = arch.naming({"id": WSID}, [item("t", "Notebook", "tmp")], collected_at=AT)
    assert recs[0]["payload"]["environment"] is None and recs[0]["confidence"] == "inferred"


# ---- FEA-ARCH-004 notebook default lakehouse outside the workspace (verified)


def test_cross_workspace_binding_pass_fail_and_not_evaluated():
    wl = [notebook("in", "LH1"), notebook("out", "other-ws-lh"), notebook("none", None),
          notebook("unread", None, definition="not_read")]
    items = [item("LH1")] + [item(w["item_id"], "Notebook") for w in wl]
    snap = run(items, opt={"workloads": wl})
    assert outcome_of(snap, "FEA-ARCH-004", "in")["outcome"] == "pass"
    out = outcome_of(snap, "FEA-ARCH-004", "out")
    assert out["outcome"] == "fail" and out["basis"] == "verified_configuration" and "other-ws-lh" in out["text"]
    assert outcome_of(snap, "FEA-ARCH-004", "none")["outcome"] == "not_applicable"
    assert outcome_of(snap, "FEA-ARCH-004", "unread")["outcome"] == "not_evaluated"
    blind = run(None, opt={"workloads": wl})
    assert outcome_of(blind, "FEA-ARCH-004")["outcome"] == "not_evaluated"


def test_binding_evidence_carries_no_code():
    wl = [dict(notebook("a", "LH1"), cells=["password = 'hunter2'"])]  # pragma: allowlist secret (fake: code never enters evidence)
    recs = arch.notebook_bindings({"workloads": wl}, collected_at=AT)
    assert "hunter2" not in json.dumps(recs) and recs[0]["payload"] == {"default_lakehouse": "LH1", "definition": "read"}


# ---- the engine call


class FakeFabric:
    def __init__(self, lakehouses: int = 2, fail: set[str] | None = None):
        self.calls: list[tuple] = []
        self.n, self.fail = lakehouses, fail or set()

    def get_workspace(self, w):
        self.calls.append(("get_workspace", w))
        return {"id": w, "displayName": "Sales Prod", "description": "d"}

    def list_items(self, w):
        self.calls.append(("list_items", w))
        return [item(f"lh{i}", name=f"LH_{i}") for i in range(self.n)]

    def list_role_assignments(self, w):
        self.calls.append(("list_role_assignments", w))
        return []

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        return []

    def get_lakehouse(self, w, lh):
        self.calls.append(("get_lakehouse", w, lh))
        if lh in self.fail:
            raise RuntimeError("403 Forbidden token=abc.def.ghi")
        return {"id": lh, "properties": {"defaultSchema": "dbo"} if lh == "lh0" else {}}


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(**kw):
        fake = FakeFabric(**kw)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def test_the_call_reads_lakehouses_with_the_existing_reader_only(make):
    s = make(fail={"lh1"})
    r = s.excellence_assess(workspace_id=WSID)
    assert {c[0] for c in s.fake.calls} <= {"get_workspace", "list_items", "list_role_assignments", "list_capacities",
                                            "get_lakehouse"}
    assert outcome_of(r, "FEA-ARCH-002", "lh0")["outcome"] == "pass"
    assert outcome_of(r, "FEA-ARCH-002", "lh1")["outcome"] == "not_evaluated"
    read = [x for x in r["reads"] if x["read"] == "lakehouse properties"][0]
    assert read["ok"] is False and "LH_1" in read["problem"] and "abc.def.ghi" not in json.dumps(r)


def test_lakehouse_reads_are_bounded(make):
    s = make(lakehouses=MAX_LAKEHOUSE_READS + 2)
    r = s.excellence_assess(workspace_id=WSID)
    assert sum(1 for c in s.fake.calls if c[0] == "get_lakehouse") == MAX_LAKEHOUSE_READS
    read = [x for x in r["reads"] if x["read"] == "lakehouse properties"][0]
    assert "2 past the first" in read["problem"]
    asked = {c[2] for c in s.fake.calls if c[0] == "get_lakehouse"}
    unasked = [f"lh{i}" for i in range(MAX_LAKEHOUSE_READS + 2) if f"lh{i}" not in asked]
    assert len(unasked) == 2
    for lh in unasked:
        assert outcome_of(r, "FEA-ARCH-002", lh)["outcome"] == "not_evaluated"


def test_the_demo_shows_architecture_without_calling_fabric(make):
    s = make()
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and r["fixture"] is True and r["fixture_note"]
    rules = {f["rule"].split("@")[0] for f in r["findings"] if not f["advisory"]}
    assert {"FEA-ARCH-002", "FEA-ARCH-004"} <= rules and all(f["fixture"] for f in r["findings"])
    assert set(FIXTURE_LAKEHOUSES) <= {o["subject"]["id"] for o in r["outcomes"] if o["control_id"] == "FEA-ARCH-002"}
    assert r["scores"]["dimensions"]["architecture"]["status"] != "not_applicable"
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert again["scores"] == r["scores"]
