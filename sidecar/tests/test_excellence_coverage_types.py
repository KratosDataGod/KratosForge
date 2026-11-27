"""Coverage per item type (FEA-306): every item type the workspace holds is reported as inspected,
not inspected (with a plain reason) or unsupported; an unknown type never crashes and never passes;
the summary is deterministic, names are cut and lists are capped."""

from __future__ import annotations

import base64
import dataclasses
import json
import random

import pytest

from fabric_desktop import opportunities_service
from fabric_desktop.excellence import adapters, assess, catalog, controls as ct, coverage as cv, evidence as ev
from fabric_desktop.service import Service

AT = "2026-11-21T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
WS = {"id": WSID, "displayName": "Sales", "description": "Sales reporting"}
REF = ({"title": "Fabric items", "url": "https://learn.microsoft.com/fabric/fundamentals/microsoft-fabric-overview"},)
TEMPLATE = {"why": "w", "solution": "s", "steps": ["a"], "benefit": "b", "effort": "low", "risk": "low",
            "validation": "v", "rollback": "r"}
NEW_TYPES = ["CopyJob", "Dataflow", "Eventstream", "KQLDatabase"]


def item(i: str, kind: str, name: str | None = None) -> dict:
    return {"id": i, "type": kind, "displayName": name if name is not None else i}


def run(items: list[dict] | None, registry: ct.Registry | None = None, extra: list[dict] | None = None) -> dict:
    records = adapters.workspace(WS, items, collected_at=AT) + (extra or [])
    return assess.assess(records, registry or catalog.registry(), scope={"workspaces": [{"id": WSID, "name": "Sales"}]},
                         assessed_at=AT)


def by_type(snap: dict) -> dict:
    return {t["item_type"]: t for t in snap["type_coverage"]["types"]}


def kql_control(fn=None) -> ct.Control:
    """A stand-in control for a type the catalog doesn't cover yet, known only by the type it covers."""
    def check(ctx: ct.Context) -> dict:
        r = ctx.one("item.kqldatabase")
        return ct.failed([r["id"]], "bad") if r["payload"].get("bad") else ct.passed([r["id"]], "ok")
    return ct.Control(id="FEA-PERF-901", version="1.0.0", dimension="performance", title="KQL database check",
                      severity="medium", requirement="kratosforge_recommendation", scope="item",
                      item_types=("KQLDatabase",), evidence_kinds=("item.kqldatabase",), evaluate=fn or check,
                      remediation=TEMPLATE, references=REF)


def kql_rec(i: str, confidence: str = "verified_configuration", payload: dict | None = None) -> dict:
    subj = ev.subject("item", i, i, workspace_id=WSID, item_type="KQLDatabase")
    return ev.record("item.kqldatabase", subj, source="test", confidence=confidence, collected_at=AT,
                     payload=payload if payload is not None else {"bad": False})


# ---- pass cases


def test_every_type_the_workspace_holds_is_listed_with_counts_and_state():
    items = [item(f"w{i}", "KQLDatabase") for i in range(2)] + [item("s", "Eventstream"), item("d", "Dataflow"),
                                                               item("c", "CopyJob")]
    snap = run(items)
    got = by_type(snap)
    assert sorted(got) == NEW_TYPES
    for t in NEW_TYPES:
        row = got[t]
        assert row["state"] == "unsupported" and row["reason"] == cv.UNSUPPORTED
        assert row["inspected"] == 0 and row["not_inspected"] == 0 and row["controls"] == []
    assert got["KQLDatabase"]["items"] == got["KQLDatabase"]["unsupported"] == 2
    assert snap["type_coverage"]["totals"] == {"types": 4, "items": 5, "inspected": 0, "not_inspected": 0,
                                               "unsupported": 5}
    assert snap["type_coverage"]["note"] is None and snap["type_coverage"]["unlisted_workspaces"] == []


def test_a_control_naming_a_type_is_enough_to_cover_it():
    reg = catalog.registry()
    reg.register(kql_control())
    snap = run([item("w1", "KQLDatabase"), item("w2", "KQLDatabase"), item("s", "Eventstream")], reg,
               extra=[kql_rec("w1"), kql_rec("w2", payload={"bad": True})])
    w = by_type(snap)["KQLDatabase"]
    assert w["state"] == "inspected" and w["inspected"] == 2
    assert w["controls"] == w["decided_by"] == ["FEA-PERF-901"] and w["evidence_kinds"] == ["item.kqldatabase"]
    assert by_type(snap)["Eventstream"]["state"] == "unsupported"
    rows = {r["item"]["id"]: r for r in snap["item_coverage"]}
    assert rows["w2"]["state"] == "inspected" and rows["w2"]["controls"] == ["FEA-PERF-901"]
    assert rows["w2"]["evidence_kinds"] == ["item.kqldatabase"]


def test_the_catalogs_own_types_are_covered_by_their_declared_controls():
    covers = cv.covering(catalog.registry())
    for t in ("Notebook", "DataPipeline", "Lakehouse", "SemanticModel", "Warehouse", "SQLEndpoint"):
        assert covers[t] and covers[t] == sorted(covers[t])
    assert not set(NEW_TYPES) & set(covers)


# ---- edge cases


@pytest.mark.parametrize("kind", ["FabricThingFromTheFuture", "warehouse", "  ", "x" * 1000, "Notebook ", 42])
def test_unknown_types_never_crash_and_never_pass(kind):
    snap = run([item("u", kind)])  # type: ignore[arg-type]
    rows = snap["type_coverage"]["types"]
    assert len(rows) == 1 and rows[0]["state"] == "unsupported" and rows[0]["inspected"] == 0
    assert len(rows[0]["item_type"]) <= cv.MAX_TEXT
    assert snap["item_coverage"][0]["state"] == "unsupported"
    assert not [o for o in snap["outcomes"] if o["subject"]["id"] == "u"]
    json.dumps(snap)


def test_malformed_inventory_payloads_dont_crash():
    subj = ev.subject("workspace", WSID, "Sales")
    for payload in ([1, 2], {"items": "abc"}, {"items": {"a": 1}}, {"items": [None, 3, {"id": "x"}, {"type": "T"}]}):
        r = ev.record("workspace.inventory", subj, source="test", confidence="verified_configuration",
                      collected_at=AT, payload=payload)
        snap = assess.assess([r], catalog.registry(), scope={"workspaces": [WSID]}, assessed_at=AT)
        assert snap["type_coverage"]["types"] == [] and snap["type_coverage"]["totals"]["items"] == 0


@pytest.mark.parametrize("payload", [[1, 2], {"items": "abc"}, {"items": {"a": 1}}, {"items": None}, {}])
def test_an_inventory_without_a_list_of_items_is_unlisted_not_empty(payload):
    """A malformed inventory says nothing about the workspace's items: it must not read as an empty
    workspace (no types, no note, item controls not applicable)."""
    subj = ev.subject("workspace", WSID, "Sales")
    r = ev.record("workspace.inventory", subj, source="test", confidence="verified_configuration",
                  collected_at=AT, payload=payload)
    snap = assess.assess([r], catalog.registry(), scope={"workspaces": [WSID]}, assessed_at=AT)
    tc = snap["type_coverage"]
    assert tc["note"] == cv.NOT_LISTED and [w["id"] for w in tc["unlisted_workspaces"]] == [WSID]
    item_ids = {c.id for c in catalog.registry().controls() if c.scope == "item"}
    assert not [o for o in snap["outcomes"] if o["control_id"] in item_ids and o["outcome"] == "not_applicable"]


def test_an_empty_workspace_has_no_types_and_no_note():
    snap = run([])
    tc = snap["type_coverage"]
    assert tc["types"] == [] and tc["totals"] == {"types": 0, "items": 0, "inspected": 0, "not_inspected": 0,
                                                  "unsupported": 0}
    assert tc["note"] is None and tc["unlisted_workspaces"] == []
    item_outcomes = [o for o in snap["outcomes"] if o["control_id"] in
                     {c.id for c in catalog.registry().controls() if c.scope == "item"}]
    assert item_outcomes and all(o["outcome"] == "not_applicable" for o in item_outcomes)


def test_a_failed_listing_is_reported_not_left_out():
    snap = run(None)
    tc = snap["type_coverage"]
    assert tc["types"] == [] and tc["note"] == cv.NOT_LISTED
    assert tc["unlisted_workspaces"] == [{"id": WSID, "name": "Sales"}] and tc["more_unlisted"] == 0
    assert snap["item_coverage"] == []


def test_a_failed_item_read_gives_its_plain_reason_and_the_type_is_not_inspected():
    reg = catalog.registry()
    reg.register(kql_control())
    snap = run([item("w1", "KQLDatabase"), item("w2", "KQLDatabase")], reg,
               extra=[kql_rec("w1"), kql_rec("w2", "insufficient",
                                                         {"read": False, "problem": "Couldn't be read (403)."})])
    w = by_type(snap)["KQLDatabase"]
    assert w["state"] == "not_inspected" and (w["inspected"], w["not_inspected"]) == (1, 1)
    assert w["reasons"][0]["items"] == 1 and w["reasons"][0]["reason"].startswith("Couldn't be read (403).")
    row = {r["item"]["id"]: r for r in snap["item_coverage"]}["w2"]
    assert row["state"] == "not_inspected" and row["controls"] == ["FEA-PERF-901"]


def test_a_covered_item_with_no_evidence_says_what_is_missing():
    reg = catalog.registry()
    reg.register(kql_control())
    snap = run([item("w1", "KQLDatabase")], reg)
    w = by_type(snap)["KQLDatabase"]
    assert w["state"] == "not_inspected" and "item.kqldatabase" in w["reasons"][0]["reason"]


def test_a_broken_control_is_not_inspected_never_inspected():
    reg = catalog.registry()
    reg.register(kql_control(fn=lambda ctx: 1 / 0))
    snap = run([item("w1", "KQLDatabase")], reg, extra=[kql_rec("w1")])
    w = by_type(snap)["KQLDatabase"]
    assert w["state"] == "not_inspected" and "ZeroDivisionError" in w["reasons"][0]["reason"]


def test_a_control_that_applies_to_no_item_is_not_inspected():
    reg = catalog.registry()
    reg.register(dataclasses.replace(kql_control(), applies=lambda ctx: False))
    snap = run([item("w1", "KQLDatabase")], reg, extra=[kql_rec("w1")])
    w = by_type(snap)["KQLDatabase"]
    assert w["state"] == "not_inspected" and w["reasons"][0]["reason"].startswith("No control applies to it.")


def test_names_are_cut_and_lists_are_capped():
    long = "N" * 1000
    items = [item(f"t{i:03}", f"Type{i:03}", long) for i in range(3)]
    snap = run(items)
    assert all(len(r["item"]["name"]) == cv.MAX_TEXT for r in snap["item_coverage"])
    inv = [r for r in snap["evidence"] if r["kind"] == "workspace.inventory"][0]
    assert all(len(i["name"]) <= cv.MAX_TEXT for i in inv["payload"]["items"])
    reg = catalog.registry()
    reg.register(kql_control())
    n = cv.MAX_REASONS + 5
    extra = [kql_rec(f"w{i}", "insufficient", {"problem": f"reason {i:02} " + "x" * 500}) for i in range(n)]
    snap = run([item(f"w{i}", "KQLDatabase") for i in range(n)], reg, extra=extra)
    w = by_type(snap)["KQLDatabase"]
    assert len(w["reasons"]) == cv.MAX_REASONS and w["more_reasons"] == 5
    assert all(len(r["reason"]) <= cv.MAX_TEXT for r in w["reasons"])


def test_many_unlisted_workspaces_are_capped():
    scope = {"workspaces": [f"ws-{i:03}" for i in range(cv.MAX_LIST + 3)]}
    snap = assess.assess([], catalog.registry(), scope=scope, assessed_at=AT)
    tc = snap["type_coverage"]
    assert len(tc["unlisted_workspaces"]) == cv.MAX_LIST and tc["more_unlisted"] == 3


def test_the_summary_is_deterministic_and_sorted():
    kinds = ["KQLDatabase", "Notebook", "copyjob", "CopyJob", "Dataflow", "Eventstream", "Report", "Lakehouse"]
    items = [item(f"i{n:02}", kinds[n % len(kinds)]) for n in range(24)]
    first = run(items)
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)
    again = run(shuffled)
    assert first["type_coverage"] == again["type_coverage"]
    assert json.dumps(first["type_coverage"], sort_keys=True) == json.dumps(again["type_coverage"], sort_keys=True)
    names = [t["item_type"] for t in first["type_coverage"]["types"]]
    assert names == sorted(names, key=lambda x: (x.casefold(), x))
    assert assess.rescore(json.loads(json.dumps(first)), catalog.registry())["type_coverage"] == first["type_coverage"]
    tot = first["type_coverage"]["totals"]
    assert tot["items"] == 24 == tot["inspected"] + tot["not_inspected"] + tot["unsupported"]


# ---- the engine call: the read cap still lists every item


class CapFabric:
    def __init__(self, notebooks: int):
        self.notebooks = notebooks
        self.calls: list[tuple] = []

    def get_workspace(self, ws):
        return {"id": ws, "displayName": "Sales"}

    def list_items(self, ws):
        return [item(f"nb{i}", "Notebook") for i in range(self.notebooks)] + [item("wh", "KQLDatabase"),
                                                                              item("cj", "CopyJob")]

    def get_item_definition(self, ws, it, fmt=None):
        self.calls.append(("get_item_definition", it))
        doc = {"cells": [{"cell_type": "code", "source": "x = 1\n"}], "metadata": {}}
        return {"definition": {"parts": [{"path": "notebook-content.ipynb", "payloadType": "InlineBase64",
                                          "payload": base64.b64encode(json.dumps(doc).encode()).decode()}]}}

    def list_job_instances(self, ws, it):
        return []

    def list_role_assignments(self, ws):
        return []

    def list_capacities(self):
        return []


def test_past_the_read_cap_every_item_is_still_reported(tmp_path, monkeypatch):
    fake = CapFabric(notebooks=3)
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    monkeypatch.setattr(opportunities_service, "MAX_ITEMS", 2)
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    assert sum(1 for c in fake.calls if c[0] == "get_item_definition") == 2
    got = by_type(r)
    assert sorted(got) == ["CopyJob", "KQLDatabase", "Notebook"]
    nb = got["Notebook"]
    assert nb["items"] == 3 and nb["inspected"] == 2 and nb["not_inspected"] == 1 and nb["state"] == "not_inspected"
    assert "only the first 2" in nb["reasons"][0]["reason"]
    assert got["KQLDatabase"]["state"] == got["CopyJob"]["state"] == "unsupported"
    assert r["type_coverage"]["note"] is None


def test_a_failed_listing_through_the_engine_call_is_reported(tmp_path, monkeypatch):
    fake = CapFabric(notebooks=1)

    def boom(ws):
        raise RuntimeError("403")

    fake.list_items = boom  # type: ignore[method-assign]
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    r = Service(root=tmp_path / "app").excellence_assess(workspace_id=WSID)
    assert r["type_coverage"]["types"] == [] and r["type_coverage"]["note"] == cv.NOT_LISTED
