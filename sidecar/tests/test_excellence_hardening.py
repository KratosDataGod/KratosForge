"""Hardening of the Fabric Excellence Assessment engine: seeded property tests of the scoring invariants,
hostile evidence fed to every adapter and inspector, and junk parameters on the engine call.

Each property runs on a fixed seed, so a failure reproduces exactly. No Fabric call is made."""

from __future__ import annotations

import base64
import copy
import json
import math
import random
import re

import pytest

from fabric_desktop.excellence import (adapters, assess, catalog, catalog_architecture, catalog_capacity, controls as ct,
                                       evidence as ev, inspect_items, inspect_models, profiles as pf, scoring)
from fabric_desktop.service import Service

AT = "2026-11-20T00:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
CAPID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DIMS = list(ct.DIMENSIONS)
KINDS = ("pass", "fail", "not_applicable", "not_evaluated", "error")
BASES = ("measured", "verified_configuration", "inferred")


def _deep(n: int) -> list:
    x: list = []
    for _ in range(n):
        x = [x]
    return x


HUGE = "N" * 200_000
CONTROL_CHARS = "\x00\x1b[31m\u202e\u200b\ufeff\ud800 e\u0301 \U0001F600"
JUNK = [None, True, False, 0, -1, -10**40, 10**40, 1.5, float("nan"), float("inf"), -float("inf"), 1e308, "", " ",
        HUGE, CONTROL_CHARS, [], {}, [None], {"a": None}, [[[[]]]], {"x": {"y": {"z": [1, "a", None]}}},
        _deep(3000), ("t", 1), b"bytes", object()]


def finite_scores(block: dict) -> None:
    for k in ("score", "assured", "coverage"):
        v = block.get(k)
        assert v is None or (isinstance(v, float) and math.isfinite(v) and 0 <= v <= 100), (k, v)
    if block.get("score") == 100.0:
        assert block["coverage"] == 100.0, block


def all_scores(result: dict) -> None:
    s = result["scores"]
    finite_scores(s["overall"])
    for d in s["dimensions"].values():
        finite_scores(d)
    for x in list(result.get("workspace_scores", {}).values()) + list(result.get("capacity_scores", {}).values()):
        finite_scores(x)
    json.dumps(result, allow_nan=False)


# ---- 1. scoring invariants


def outcome(cid: str, dim: str, result: str, sev: str, basis: str, subject: str) -> dict:
    return {"control_id": cid, "rule": f"{cid}@1.0.0", "dimension": dim, "severity": sev, "outcome": result,
            "basis": basis if result in ("pass", "fail") else "insufficient", "subject": {"id": subject},
            "evidence_ids": ["ev-1"] if result in ("pass", "fail") else []}


def random_outcomes(rnd: random.Random) -> list[dict]:
    out = []
    for c in range(rnd.randint(0, 25)):
        dim, sev = rnd.choice(DIMS), rnd.choice(ct.SEVERITIES)
        for s in range(rnd.choice((1, 1, 1, 2, 5, 40))):
            out.append(outcome(f"C{c:02d}", dim, rnd.choice(KINDS), sev, rnd.choice(BASES), f"s{s}"))
    return out


SEEDS = range(400)


@pytest.mark.parametrize("profile", list(pf.BUILT_IN))
def test_scores_stay_in_range_and_100_needs_full_coverage(profile):
    p = pf.get(profile)
    for seed in SEEDS:
        outs = random_outcomes(random.Random(seed))
        s = scoring.score(outs, p)
        finite_scores(s["overall"])
        for d in s["dimensions"].values():
            finite_scores(d)
        if any(o["outcome"] == "fail" and o["severity"] == "critical" for o in outs):
            assert s["critical_risk"] is True, seed
        else:
            assert s["critical_risk"] is False, seed


def test_a_score_just_under_100_with_gaps_doesnt_round_up_to_100():
    """9,999 of 10,000 items pass and another control isn't evaluated: the raw score is 99.99, which used to
    round to 100.0 although coverage isn't full."""
    outs = [outcome("A", "security", "pass", "critical", "verified_configuration", f"i{i}") for i in range(9999)]
    outs.append(outcome("A", "security", "fail", "critical", "verified_configuration", "i9999"))
    outs.append(outcome("B", "security", "not_evaluated", "low", "verified_configuration", "ws"))
    s = scoring.score(outs, pf.get())
    for block in (s["dimensions"]["security"], s["overall"]):
        assert block["coverage"] < 100 and block["score"] < 100, block


def test_coverage_just_under_full_isnt_shown_as_100():
    outs = [outcome("A", "security", "pass", "critical", "verified_configuration", f"i{i}") for i in range(9999)]
    outs.append(outcome("A", "security", "not_evaluated", "critical", "verified_configuration", "i9999"))
    s = scoring.score(outs, pf.get())
    for block in (s["dimensions"]["security"], s["overall"]):
        assert block["coverage"] < 100 and block["score"] < 100, block


def test_scores_dont_depend_on_outcome_order():
    for seed in range(150):
        rnd = random.Random(seed)
        outs = random_outcomes(rnd)
        shuffled = outs[:]
        rnd.shuffle(shuffled)
        assert scoring.score(shuffled, pf.get()) == scoring.score(outs, pf.get()), seed


def test_a_new_not_evaluated_control_never_raises_the_score_and_never_the_assured_score():
    for seed in range(300):
        rnd = random.Random(seed)
        outs = random_outcomes(rnd)
        before = scoring.score(outs, pf.get())
        extra = outcome("Z99", rnd.choice(DIMS), rnd.choice(("not_evaluated", "error")), rnd.choice(ct.SEVERITIES),
                        "insufficient", "ws")
        after = scoring.score(outs + [extra], pf.get())
        for b, a in [(before["overall"], after["overall"])] + [
                (before["dimensions"][d], after["dimensions"][d]) for d in DIMS]:
            if b["score"] is not None and a["score"] is not None:
                assert a["score"] <= b["score"], seed
            if b["assured"] is not None and a["assured"] is not None:
                assert a["assured"] <= b["assured"] + 1e-9, seed


def test_a_not_evaluated_item_of_an_existing_control_never_raises_the_assured_score():
    for seed in range(300):
        rnd = random.Random(seed)
        outs = random_outcomes(rnd)
        if not outs:
            continue
        o = rnd.choice(outs)
        more = outs + [{**o, "outcome": "not_evaluated", "basis": "insufficient", "evidence_ids": [],
                        "subject": {"id": "new-item"}}]
        b, a = scoring.score(outs, pf.get()), scoring.score(more, pf.get())
        if b["overall"]["assured"] is not None:
            assert a["overall"]["assured"] <= b["overall"]["assured"], seed


# ---- 1b. whole assessments: order, determinism, rescoring


def _service(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
    return Service(root=tmp_path / "app")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        s = _service(tmp_path_factory.mktemp("demo"), mp, object())
        return s.excellence_assess(demo=True)
    finally:
        mp.undo()


def _strip(snap: dict) -> dict:
    return {k: snap[k] for k in ("scores", "workspace_scores", "capacity_scores", "findings", "outcomes",
                                 "item_coverage", "fingerprint", "evidence_used", "counts")}


def test_the_demo_is_well_formed_and_rescores_exactly(demo):
    all_scores(demo)
    again = assess.rescore(json.loads(json.dumps(demo)), catalog.registry())
    assert _strip(again) == _strip(demo)


def test_shuffled_and_duplicated_evidence_gives_the_same_assessment(demo):
    base = assess.rescore(demo, catalog.registry())
    for seed in range(8):
        rnd = random.Random(seed)
        recs = copy.deepcopy(demo["evidence"])
        recs += rnd.sample(recs, k=len(recs) // 3)
        rnd.shuffle(recs)
        again = assess.rescore({**demo, "evidence": recs}, catalog.registry())
        assert _strip(again) == _strip(base), seed


def _with_second(rec: dict, payload: dict, **change) -> dict:
    """Another record of the same kind about the same subject, as a second read would give."""
    return ev.record(rec["kind"], rec["subject"], source=change.get("source", rec["source"] + " (again)"),
                     confidence=change.get("confidence", rec["confidence"]), collected_at=rec["collected_at"],
                     payload=payload)


def test_two_records_of_one_kind_decide_the_same_whatever_their_order(demo):
    roles = next(r for r in demo["evidence"] if r["kind"] == "workspace.roles")
    second = _with_second(roles, {"assignments": []})
    a = assess.rescore({**demo, "evidence": [roles, second] + demo["evidence"]}, catalog.registry())
    b = assess.rescore({**demo, "evidence": [second, roles] + demo["evidence"]}, catalog.registry())
    assert _strip(a) == _strip(b)


def test_same_id_with_a_different_confidence_is_order_independent(demo):
    """The evidence id leaves out confidence and time, so two records can share an id; which one is
    kept mustn't depend on the order they came in."""
    info = next(r for r in demo["evidence"] if r["kind"] == "workspace.info")
    weak = ev.record(info["kind"], info["subject"], source=info["source"], confidence="insufficient",
                     collected_at=info["collected_at"], payload=info["payload"])
    assert weak["id"] == info["id"]
    rest = [r for r in demo["evidence"] if r["id"] != info["id"]]
    a = assess.rescore({**demo, "evidence": [info, weak] + rest}, catalog.registry())
    b = assess.rescore({**demo, "evidence": [weak, info] + rest}, catalog.registry())
    assert _strip(a) == _strip(b)


@pytest.mark.parametrize("profile", list(pf.BUILT_IN))
def test_rescoring_with_each_profile_is_stable(demo, profile):
    p = pf.get(profile)
    once = assess.rescore(demo, catalog.registry(), p)
    twice = assess.rescore(json.loads(json.dumps(once)), catalog.registry())
    assert _strip(once) == _strip(twice)
    all_scores(once)


# ---- tampered snapshots and profiles


@pytest.mark.parametrize("field,value", [
    ("subject", "ws"), ("subject", None), ("subject", {"id": ["x"], "type": "workspace"}),
    ("subject", {"id": "x", "type": "nonsense"}), ("kind", ["workspace.roles"]), ("kind", 7), ("id", ["x"]),
    ("id", None), ("confidence", ["measured"]), ("source", 5), ("collected_at", None)])
def test_a_malformed_stored_record_is_rejected_not_a_crash(demo, field, value):
    recs = copy.deepcopy(demo["evidence"])
    recs[0][field] = value
    again = assess.rescore({**demo, "evidence": recs}, catalog.registry())
    assert len(again["evidence_rejected"]) == 1
    all_scores(again)


@pytest.mark.parametrize("payload", [[], "x", 5, None, {"items": 5}, {"items": "abc"}, {"items": [5, None, {"id": []}]}])
def test_a_junk_inventory_payload_is_not_a_crash(demo, payload):
    recs = [r for r in demo["evidence"] if r["kind"] != "workspace.inventory"]
    inv = next(r for r in demo["evidence"] if r["kind"] == "workspace.inventory")
    recs.append(ev.record(inv["kind"], inv["subject"], source=inv["source"], confidence=inv["confidence"],
                          collected_at=AT, payload=payload))
    again = assess.rescore({**demo, "evidence": recs}, catalog.registry())
    all_scores(again)
    assert not any(o["outcome"] == "pass" and o["subject"]["type"] == "item" for o in again["outcomes"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "10", None])
def test_a_stored_profile_with_non_finite_or_non_numeric_weights_is_refused(demo, bad):
    p = copy.deepcopy(demo["profile"])
    p["weights"]["security"] = bad
    with pytest.raises(ValueError):
        pf.from_dict(p)
    p = copy.deepcopy(demo["profile"])
    p["severity_weights"]["high"] = bad
    with pytest.raises(ValueError):
        pf.from_dict(p)
    p = copy.deepcopy(demo["profile"])
    p["rated_coverage"] = bad
    with pytest.raises(ValueError):
        pf.from_dict(p)


# ---- 2. hostile evidence: adapters and inspectors


def _records_ok(recs: list) -> None:
    assert isinstance(recs, list)
    for r in recs:
        assert ev.problems(r) == [], r
        assert len(r["subject"]["name"]) <= ev.MAX_NAME
        assert len(ev.canonical(r)) < 100_000, (r["kind"], len(ev.canonical(r)))
        json.dumps(r, allow_nan=False, default=str)


def _call(fn, *args, **kw):
    """The adapter's records, or None when it refused the input by raising (the service then lists a read
    problem). It must never hang or return something that isn't valid evidence."""
    try:
        got = fn(*args, **kw)
    except Exception:  # noqa: BLE001 - refusing junk is allowed; excellence_assess catches it
        return None
    _records_ok(got)
    return got


def _shapes(rnd: random.Random, keys: tuple[str, ...]) -> list:
    out = list(JUNK)
    for _ in range(30):
        out.append({k: rnd.choice(JUNK) for k in keys if rnd.random() < 0.8})
        out.append([{k: rnd.choice(JUNK) for k in keys} for _ in range(rnd.randint(0, 3))] + rnd.sample(JUNK, 2))
    return out


def test_workspace_adapter_with_junk():
    rnd = random.Random(1)
    for ws in _shapes(rnd, ("id", "displayName", "description", "capacityId")):
        for items in _shapes(rnd, ("id", "type", "displayName"))[:20] + [None]:
            if not isinstance(ws, dict):
                continue
            ws = {**ws, "id": WSID}
            _call(adapters.workspace, ws, items, collected_at=AT)


@pytest.mark.parametrize("desc", [{"a": 1}, ["x"], 5, True, float("nan"), b"x"])
def test_a_description_that_isnt_text_doesnt_pass_the_description_control(desc):
    recs = adapters.workspace({"id": WSID, "displayName": "W", "description": desc}, [], collected_at=AT)
    snap = assess.assess(recs, catalog.registry(), scope={"workspaces": [WSID]}, assessed_at=AT)
    o = next(o for o in snap["outcomes"] if o["control_id"] == "FEA-GOV-001")
    assert o["outcome"] != "pass"


def test_names_and_descriptions_are_capped():
    recs = adapters.workspace({"id": WSID, "displayName": HUGE, "description": HUGE},
                              [{"id": f"i{i}", "type": "Notebook", "displayName": HUGE} for i in range(3)],
                              collected_at=AT)
    recs += adapters.roles({"id": WSID}, [{"role": "Admin", "principal": {"id": "p", "type": "User", "displayName": HUGE}}],
                           collected_at=AT)
    recs += catalog_architecture.evidence({"id": WSID, "displayName": HUGE},
                                          [{"id": "l1", "type": "Lakehouse", "displayName": HUGE}], {}, {},
                                          collected_at=AT)
    _records_ok(recs)


@pytest.mark.parametrize("assignments", [{}, {"a": 1}, "Admin", 5, [None], ["x"], [{"role": "Admin", "principal": "p"}],
                                         [{"role": ["Admin"], "principal": {"type": "User"}}],
                                         [{"role": "Admin", "principal": {"type": ["User"]}}],
                                         [{"role": "Admin", "principal": {"type": "User", "userDetails": "x"}}],
                                         [{"principal": {"type": "User"}}]])
def test_junk_role_assignments_never_pass_a_role_control(assignments):
    recs = _call(adapters.roles, {"id": WSID}, assignments, collected_at=AT) or []
    snap = assess.assess(recs, catalog.registry(), scope={"workspaces": [WSID]}, assessed_at=AT)
    roles_controls = [o for o in snap["outcomes"] if o["control_id"].startswith("FEA-SEC-00")
                      and o["subject"]["type"] == "workspace" and o["control_id"] <= "FEA-SEC-004"]
    assert roles_controls and all(o["outcome"] != "pass" for o in roles_controls)


def test_role_and_capacity_adapters_with_junk():
    rnd = random.Random(2)
    for a in _shapes(rnd, ("role", "principal")):
        _call(adapters.roles, {"id": WSID}, a, collected_at=AT)
    for v in _shapes(rnd, ("id", "displayName", "state", "sku", "region")):
        _call(adapters.capacity, CAPID, v, collected_at=AT)
        if isinstance(v, list):
            _call(adapters.capacity, CAPID, [{**x, "id": CAPID} for x in v if isinstance(x, dict)], collected_at=AT)


@pytest.mark.parametrize("state", [None, 5, ["Active"], {"Active": 1}, "", HUGE])
def test_a_junk_capacity_state_never_passes(state):
    recs = _call(adapters.capacity, CAPID, [{"id": CAPID, "state": state, "sku": "F64"}], collected_at=AT) or []
    snap = assess.assess(recs, catalog.registry(), scope={"capacities": [CAPID]}, assessed_at=AT)
    assert all(o["outcome"] != "pass" for o in snap["outcomes"] if o["control_id"] == "FEA-CAP-001")


def _workload(rnd: random.Random) -> dict:
    w = {"item_id": rnd.choice(["n1", "n2", "p1", 5, None, HUGE]), "type": rnd.choice(["Notebook", "DataPipeline", 5, None]),
         "workspace_id": WSID, "name": rnd.choice(JUNK), "definition": rnd.choice(["read", "not_read", None, 5]),
         "runs": rnd.choice(JUNK + [{"read": True, "scheduled": 3}]), "problem": rnd.choice(JUNK),
         "inspected": rnd.choice(JUNK + [{"read": True, "rules": 5}]), "default_lakehouse": rnd.choice(JUNK)}
    return {k: v for k, v in w.items() if rnd.random() < 0.9}


def test_optimization_inspection_and_binding_adapters_with_junk():
    rnd = random.Random(3)
    for _ in range(300):
        opt = {"workloads": [_workload(rnd) for _ in range(rnd.randint(0, 4))] + rnd.sample(JUNK, 1),
               "opportunities": rnd.choice([JUNK, [{"artifacts": rnd.choice(JUNK), "rule": rnd.choice(JUNK)}]])}
        _call(adapters.optimization, opt, collected_at=AT)
        _call(adapters.inspection, opt, collected_at=AT)
        _call(catalog_architecture.notebook_bindings, opt, collected_at=AT)
    for v in JUNK:
        _call(adapters.optimization, {"workloads": v, "opportunities": v}, collected_at=AT)
        _call(adapters.inspection, {"workloads": v}, collected_at=AT)


def test_models_adapter_with_junk():
    rnd = random.Random(4)
    for _ in range(200):
        reads = [{"item_id": rnd.choice(["m1", HUGE, 5]), "name": rnd.choice(JUNK), "workspace_id": WSID,
                  "inspected": rnd.choice(JUNK + [{"read": True}, {"read": True, "error": HUGE}])}
                 for _ in range(rnd.randint(0, 3))]
        _call(adapters.models, reads, collected_at=AT)


def test_architecture_evidence_with_junk():
    rnd = random.Random(5)
    for _ in range(200):
        items = rnd.choice([None, [{"id": rnd.choice(["l1", 5, HUGE]), "type": "Lakehouse",
                                    "displayName": rnd.choice(JUNK)} for _ in range(3)]])
        read = {"l1": rnd.choice(JUNK + [{"properties": rnd.choice(JUNK)},
                                         {"properties": {"defaultSchema": rnd.choice(JUNK)}}])}
        _call(catalog_architecture.evidence, {"id": WSID, "displayName": rnd.choice(JUNK)}, items, {}, read,
              collected_at=AT)


@pytest.mark.parametrize("schema", [["dbo"], {"a": 1}, 5, True, float("nan")])
def test_a_junk_default_schema_doesnt_pass_the_schema_control(schema):
    recs = catalog_architecture.lakehouse_properties(WSID, [{"id": "l1", "displayName": "LH"}],
                                                     {"l1": {"properties": {"defaultSchema": schema}}}, collected_at=AT)
    assert all(r["payload"]["schemas_enabled"] is False for r in recs)


def _usage_row(rnd: random.Random) -> dict:
    return {"workspace": rnd.choice(["Sales", "Sales", 5, None]), "item": rnd.choice(JUNK),
            "operation": rnd.choice(["Refresh", None, 5, HUGE]), "status": rnd.choice(["Failure", "Success", None, 5]),
            "start": rnd.choice(["2026-11-01T00:00:00Z", "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00",
                                 "junk", None, 5]),
            "end": rnd.choice(["2026-11-02T00:00:00Z", None, "x"]),
            "cu": rnd.choice([{"value": v} for v in JUNK[:14]] + JUNK[:3])}


def test_capacity_usage_with_junk_never_gives_nan_or_inf():
    rnd = random.Random(6)
    reg = catalog.registry()
    cap_state = adapters.capacity(CAPID, [{"id": CAPID, "state": "Active", "sku": "F2"}], collected_at=AT)
    for _ in range(150):
        imports = [{"id": rnd.choice(["u1", 5, None]), "provider": "metrics_app",
                    "layout": rnd.choice(["items", "operations", None]),
                    "period": rnd.choice(JUNK[:5] + [{"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"}]),
                    "rows": [_usage_row(rnd) for _ in range(rnd.randint(0, 6))]} for _ in range(rnd.randint(1, 3))]
        recs = _call(catalog_capacity.usage, CAPID, rnd.choice(JUNK[:3] + ["F2"]), "Sales", imports, collected_at=AT)
        if recs is None:
            continue
        snap = assess.assess(recs + cap_state, reg, scope={"capacities": [CAPID]}, assessed_at=AT)
        all_scores(snap)


@pytest.mark.parametrize("cu", [float("inf"), float("nan"), 1e308, -1.0])
def test_non_finite_or_absurd_cu_is_ignored_not_scored(cu):
    rows = [{"workspace": "Sales", "item": "x", "status": "Success", "operation": "Run",
             "start": "2026-11-01T00:00:00Z", "cu": {"value": cu}},
            {"workspace": "Sales", "item": "y", "status": "Success", "operation": "Run",
             "start": "2026-11-01T00:00:00Z", "cu": {"value": cu}}]
    items = [{"workspace": "Sales", "item": "x", "cu": {"value": cu}}, {"workspace": "Sales", "item": "y", "cu": {"value": cu}}]
    imports = [{"id": "u1", "provider": "metrics_app", "layout": "operations", "rows": rows},
               {"id": "u2", "provider": "metrics_app", "layout": "items", "rows": items,
                "period": {"start": "2026-10-01T00:00:00Z", "end": "2026-11-01T00:00:00Z"}}]
    recs = catalog_capacity.usage(CAPID, "F2", "Sales", imports, collected_at=AT)
    recs += adapters.capacity(CAPID, [{"id": CAPID, "state": "Active", "sku": "F2"}], collected_at=AT)
    snap = assess.assess(recs, catalog.registry(), scope={"capacities": [CAPID]}, assessed_at=AT)
    all_scores(snap)
    by = {o["control_id"]: o for o in snap["outcomes"]}
    for cid in ("FEA-CAP-002", "FEA-CAP-004"):
        assert by[cid]["outcome"] not in ("pass", "fail"), (cid, by[cid]["text"])


def test_inspect_items_never_raises_and_caps_names():
    rnd = random.Random(7)
    for v in JUNK:
        assert isinstance(inspect_items.inspect(v), dict)
        for kind in ("Notebook", "DataPipeline"):
            got = inspect_items.inspect({"type": kind, "definition": "read", "cells": v, "activities": v, "runs": v})
            assert isinstance(got, dict)
    acts = [{"name": HUGE + str(i), "inactive": True, "type_properties": {"workspaceId": WSID}} for i in range(3)]
    acts += [{"name": rnd.choice(JUNK), "inactive": rnd.choice(JUNK), "type_properties": rnd.choice(JUNK)} for _ in range(40)]
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": acts})
    assert got.get("error") or all(len(n) <= ev.MAX_NAME for k in ("inactive", "fixed_workspace", "state_unknown",
                                                                     "properties_unknown") for n in got[k])
    assert len(ev.canonical(got)) < 20_000
    got = inspect_items.inspect({"type": "Notebook", "definition": "read", "cells": [HUGE, CONTROL_CHARS, None, 5],
                                 "runs": {"read": True, "scheduled": True}})
    assert got["read"] is True and len(ev.canonical(got)) < 20_000


def _bim(rnd: random.Random) -> dict:
    def pick():
        return rnd.choice(JUNK)
    tables = [{"name": rnd.choice([pick(), "T", HUGE]), "columns": rnd.choice([pick(), [{"name": pick(), "dataType": pick()}]]),
               "partitions": rnd.choice([pick(), [{"mode": "directLake", "source": pick()}]])} for _ in range(2)]
    rels = [{"fromTable": rnd.choice(["T", pick()]), "fromColumn": pick(), "toTable": rnd.choice(["T", pick()]),
             "toColumn": pick(), "crossFilteringBehavior": rnd.choice(["bothDirections", pick()]),
             "isActive": pick()} for _ in range(2)]
    roles = [{"name": pick(), "tablePermissions": rnd.choice([pick(), [{"name": pick(), "filterExpression": pick()}]])}]
    model = {"tables": rnd.choice([tables, pick()]), "relationships": rnd.choice([rels, pick()]),
             "roles": rnd.choice([roles, pick()]), "expressions": pick(), "directLakeBehavior": pick()}
    return {"model": model}


def test_inspect_models_never_raises():
    rnd = random.Random(8)
    for v in JUNK:
        assert isinstance(inspect_models.inspect(v), dict)
        assert isinstance(inspect_models.inspect({"model": v}), dict)
    for _ in range(400):
        got = inspect_models.inspect(_bim(rnd))
        assert isinstance(got, dict) and got["read"] is True
        json.dumps(got, default=str)
        assert len(ev.canonical(got)) < 60_000


def test_evidence_records_take_any_text():
    for name in (CONTROL_CHARS, "\ud800", "\udfff x", HUGE):
        r = ev.record("workspace.info", ev.subject("workspace", WSID, name), source="s", confidence="measured",
                      collected_at=AT, payload={"description": name, name[:10]: [name]})
        assert ev.problems(r) == [] and len(r["subject"]["name"]) <= ev.MAX_NAME
    assert ev.subject("workspace", WSID, {"x": 1})["name"] == "{'x': 1}"


# ---- 2b. the engine call with a hostile Fabric


class Hostile:
    """A Fabric client whose every answer is drawn from junk, with a fixed seed."""

    def __init__(self, seed: int):
        self.rnd = random.Random(seed)

    def _junk(self, *shaped):
        return self.rnd.choice(list(shaped) + JUNK)

    def get_workspace(self, w):
        return self._junk({"id": w, "displayName": self._junk("Sales"), "description": self._junk("d"),
                           "capacityId": self._junk(CAPID)},
                          {"id": self._junk(w), "displayName": HUGE, "capacityId": CAPID.upper()})

    def list_items(self, w):
        return self._junk([{"id": self._junk("l1"), "type": t, "displayName": self._junk("LH_Bronze")}
                           for t in ("Lakehouse", "Notebook", "SemanticModel", "DataPipeline")])

    def get_item_definition(self, w, item, fmt=None):
        payload = base64.b64encode(json.dumps(self._junk({"model": self._junk({"tables": []})}),
                                              default=str).encode()).decode()
        return self._junk({"definition": {"parts": self._junk([{"path": "model.bim", "payload": payload},
                                                               {"path": self._junk("x"), "payload": self._junk("")}])}})

    def list_job_instances(self, w, item):
        return self._junk([{"status": self._junk("Completed"), "invokeType": self._junk("Scheduled")}])

    def list_role_assignments(self, w):
        return self._junk([{"role": self._junk("Admin"), "principal": self._junk({"id": "p", "type": "Group"})}])

    def list_capacities(self):
        return self._junk([{"id": self._junk(CAPID), "state": self._junk("Active"), "sku": self._junk("F64")}])

    def get_lakehouse(self, w, lh):
        return self._junk({"properties": self._junk({"defaultSchema": self._junk("dbo")})})


def test_a_hostile_fabric_never_crashes_the_call_or_gives_bad_scores(tmp_path, monkeypatch):
    holder: dict = {}
    monkeypatch.setattr(Service, "fabric", property(lambda self: holder["f"]))
    s = Service(root=tmp_path / "app")
    for seed in range(40):
        holder["f"] = Hostile(seed)
        r = s.excellence_assess(workspace_id=WSID)
        all_scores(r)
        assert r["scope"]["workspaces"][0]["id"] == WSID
        for rec in r["evidence"]:
            assert len(rec["subject"]["name"]) <= ev.MAX_NAME
        assert all(isinstance(x["read"], str) for x in r["reads"])


def test_an_evidence_builder_that_raises_is_listed_as_a_read_problem(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("/home/someone/secret/path.py line 3")
    monkeypatch.setattr(adapters, "roles", boom)
    s = _service(tmp_path, monkeypatch, object())
    r = s.excellence_assess(demo=True)
    bad = [x for x in r["reads"] if not x["ok"]]
    assert bad and "role assignments" in bad[0]["read"] and "/home/someone" not in json.dumps(r)
    assert all(o["outcome"] != "pass" for o in r["outcomes"] if o["control_id"] == "FEA-SEC-001")


# ---- 3. the engine call with junk parameters


@pytest.fixture
def service(tmp_path, monkeypatch):
    class Unused:
        def __getattr__(self, name):
            raise AssertionError(f"no Fabric call expected, got {name}")
    return _service(tmp_path, monkeypatch, Unused())


@pytest.mark.parametrize("params,message", [
    ({"workspace_id": ""}, "workspace"), ({"workspace_id": "not-a-guid"}, "workspace"),
    ({"workspace_id": "../../etc/passwd"}, "workspace"), ({"workspace_id": WSID + "x"}, "workspace"),
    ({"workspace_id": HUGE}, "workspace"), ({"workspace_id": CONTROL_CHARS}, "workspace"),
    ({"workspace_id": None}, "workspace"),
    ({"workspace_id": WSID, "days": 0}, "days"), ({"workspace_id": WSID, "days": -5}, "days"),
    ({"workspace_id": WSID, "days": 91}, "days"), ({"workspace_id": WSID, "days": 10**40}, "days"),
    ({"workspace_id": WSID, "days": True}, "days"), ({"workspace_id": WSID, "days": "30"}, "days"),
    ({"workspace_id": WSID, "days": float("nan")}, "days"), ({"workspace_id": WSID, "days": 7.5}, "days"),
    ({"workspace_id": WSID, "profile": "nope"}, "profile"), ({"workspace_id": WSID, "profile": HUGE}, "profile"),
    ({"workspace_id": WSID, "profile": ["baseline"]}, "profile"), ({"demo": True, "profile": "nope"}, "profile"),
    ({"demo": True, "workspace_id": WSID}, "demonstration"), ({"demo": True, "days": 7}, "demonstration"),
    ({"demo": "false"}, "demo"), ({"demo": 1}, "demo"),
])
def test_junk_parameters_give_a_clear_value_error(service, params, message):
    with pytest.raises(ValueError) as got:
        service.excellence_assess(**params)
    text = str(got.value)
    assert message in text.lower() and len(text) < 300, text
    assert "Traceback" not in text and "/home/" not in text and "fabric_desktop" not in text and ".py" not in text


def test_good_parameters_still_work(service):
    r = service.excellence_assess(demo=True, profile="enterprise_security")
    assert r["fixture"] is True and r["profile"]["id"] == "enterprise_security"
    r = service.excellence_assess(demo=True, days=30)
    assert r["fixture"] is True



def test_a_lakehouse_id_that_isnt_id_shaped_never_reaches_a_request_path(tmp_path, monkeypatch):
    bad = "../../capacities/x?y=1"
    calls: list = []

    class Fake:
        def get_workspace(self, w):
            return {"id": w, "displayName": "Sales"}

        def list_items(self, w):
            return [{"id": bad, "type": "Lakehouse", "displayName": HUGE},
                    {"id": "f1f1f1f1-0000-4000-8000-000000000301", "type": "Lakehouse", "displayName": "LH_Bronze"}]

        def list_role_assignments(self, w):
            return []

        def list_capacities(self):
            return []

        def get_lakehouse(self, w, lh):
            calls.append(lh)
            return {"properties": {"defaultSchema": "dbo"}}

    r = _service(tmp_path, monkeypatch, Fake()).excellence_assess(workspace_id=WSID)
    assert calls == ["f1f1f1f1-0000-4000-8000-000000000301"]
    read = next(x for x in r["reads"] if x["read"] == "lakehouse properties")
    assert read["ok"] is False and len(read["problem"]) < 2000
    assert all(o["outcome"] != "pass" for o in r["outcomes"] if o["subject"]["id"] == bad)


# ---- 4. malformed records: hashing, the bundle, and the controls that read them


ORDINARY = {"b": [1, 2.5, None, True], "a": {"z": "é \U0001F600", "y": {2: "x", 10: "y"}}, "c": "line\nbreak"}


def test_the_hash_format_is_unchanged_for_ordinary_evidence():
    """Pinned before canonical learned mixed keys: a snapshot of valid evidence keeps its ids and fingerprint."""
    assert ev.canonical(ORDINARY) == '{"a":{"y":{"2":"x","10":"y"},"z":"é 😀"},"b":[1,2.5,null,true],"c":"line\\nbreak"}'
    assert ev.content_hash(ORDINARY) == "4b07b137b0de92e42927e894d5bf747d7895e2f1f4b52d3230ce882345e52a21"  # pragma: allowlist secret (a pinned SHA-256)
    r = ev.record("workspace.roles", ev.subject("workspace", "w1", "Sales"), source="GET x", confidence="measured",
                  collected_at=AT, payload=ORDINARY)
    assert r["id"] == "ev-f43ee3ca45302a5e"
    assert assess.fx.finding_id("FEA-SEC-001", "w1") == "fx-29bc190972e32aae"
    rnd = random.Random(11)
    for _ in range(200):
        v = {str(rnd.randint(0, 50)): rnd.choice([1, "x", None, [1, {"k": 2}], {"n": {"m": 1.5}}]) for _ in range(6)}
        assert ev.canonical(v) == json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@pytest.mark.parametrize("value", [
    {1: "a", "b": 2}, {"b": 2, 1: "a"}, {"x": [{None: 1, "y": 2, 3.5: 3, True: 4}]}, {1: "a", "1": "b"}, {("t", 1): 1, "a": 2},
    "\ud800", {"\udfff": ["\ud800 x"]}, {1: "\ud800", "k": {2: "x", "z": "y"}}])
def test_mixed_keys_and_lone_surrogates_hash_deterministically(value):
    once = ev.content_hash(value)
    assert re.fullmatch(r"[0-9a-f]{64}", once)
    assert ev.content_hash(copy.deepcopy(value)) == once
    if isinstance(value, dict):  # the same keys in another order hash the same
        assert ev.content_hash(dict(reversed(list(value.items())))) == once
    ev.canonical(value)


def test_mixed_keys_hash_as_their_json_round_trip_and_stay_distinct():
    mixed = {1: "a", "b": 2}
    assert ev.content_hash(mixed) == ev.content_hash(json.loads(json.dumps(mixed)))
    assert ev.content_hash(mixed) != ev.content_hash({"b": 2})
    assert ev.content_hash({1: "a", "1": "b"}) != ev.content_hash({1: "b", "1": "a"})


def test_a_record_with_mixed_keys_is_made_and_kept():
    r = ev.record("workspace.info", ev.subject("workspace", WSID), source="s", confidence="measured", collected_at=AT,
                  payload={"description": "d", 1: "x"})
    assert ev.problems(r) == [] and len(ev.Bundle([r]).records) == 1


@pytest.mark.parametrize("subj", ["ws", None, 5, ["workspace"]])
def test_a_record_with_a_subject_that_isnt_an_object_is_a_value_error(subj):
    with pytest.raises(ValueError):
        ev.record("workspace.info", subj, source="s", confidence="measured", collected_at=AT, payload={})


@pytest.mark.parametrize("records", [None, "abc", 5, {"id": "x"}])
def test_a_bundle_of_something_that_isnt_a_list_is_empty(records):
    b = ev.Bundle(records)
    assert b.records == [] and b.ids() == set()


def _mutate(rec: dict, how: str) -> None:
    if how == "payload mixed keys":
        rec["payload"] = {1: "a", "b": 2}
    elif how == "payload mixed keys, hash matching":
        rec["payload"] = {**(rec["payload"] if isinstance(rec["payload"], dict) else {}), 1: "a"}
        rec["hash"] = ev.content_hash(rec["payload"])
    elif how == "subject mixed keys":
        rec["subject"][1] = "x"
    elif how == "record mixed keys":
        rec[5] = "x"
    elif how == "id lone surrogate":
        rec["id"] = "ev-\ud800"
    elif how == "subject id lone surrogate":
        rec["subject"]["id"] = "\ud800"
    elif how == "subject name too deep":
        rec["subject"]["name"] = _deep(5000)
    elif how == "hash not text":
        rec["hash"] = {1: 2, "a": 3}


@pytest.mark.parametrize("how", ["payload mixed keys", "payload mixed keys, hash matching", "subject mixed keys",
                                 "record mixed keys", "id lone surrogate", "subject id lone surrogate",
                                 "subject name too deep", "hash not text"])
def test_a_malformed_record_never_crashes_the_assessment(demo, how):
    for i in range(len(demo["evidence"])):
        recs = copy.deepcopy(demo["evidence"])
        _mutate(recs[i], how)
        again = assess.rescore({**demo, "evidence": recs}, catalog.registry())
        all_scores(again)
        ev.Bundle(list(reversed(recs)))  # and in any order


def _junk_payloads(payload: dict) -> list:
    out: list = [[], "x", 5, None, {}]
    for k in payload:
        out.append({kk: v for kk, v in payload.items() if kk != k})
        for junk in (None, 5, "x", [5], {"a": 1}):
            if not isinstance(payload[k], type(junk)) or isinstance(junk, list):
                out.append({**payload, k: junk})
    return out


def test_a_malformed_payload_never_passes_fails_or_errors_a_control(demo):
    """Every kind's payload with a key missing, or of the wrong type: each control that read the record either
    decides exactly as it did on the good record (the key isn't one it reads) or is not evaluated. Junk never
    turns into a pass, a finding or an error, and never a crash."""
    reg = catalog.registry()
    base = assess.rescore(demo, reg)
    for rec in demo["evidence"]:
        if rec["confidence"] == "insufficient" or not isinstance(rec["payload"], dict):
            continue
        before = {(o["control_id"], o["subject"]["id"]): o for o in base["outcomes"]
                  if rec["id"] in o["evidence_ids"] or rec["id"] in (o.get("advisory") or {}).get("evidence_ids", [])}
        assert before, rec["kind"]
        for payload in _junk_payloads(rec["payload"]):
            bad = ev.record(rec["kind"], rec["subject"], source=rec["source"], confidence=rec["confidence"],
                            collected_at=rec["collected_at"], payload=payload)
            recs = [r for r in demo["evidence"] if r["id"] != rec["id"]] + [bad]
            again = assess.rescore({**demo, "evidence": recs}, reg)
            all_scores(again)
            for o in again["outcomes"]:
                was = before.get((o["control_id"], o["subject"]["id"]))
                if was is None:
                    continue
                same = (o["outcome"], o["text"]) == (was["outcome"], was["text"])
                assert same or o["outcome"] in ("not_evaluated", "not_applicable"), (
                    rec["kind"], payload, o["control_id"], o["outcome"], o["text"])
                assert o["outcome"] != "error", (rec["kind"], payload, o["control_id"], o["text"])


# ---- 5. pipeline activities without a name


def test_unnamed_activities_are_marked_and_never_collide():
    acts = [{"type": "Copy", "inactive": True, "type_properties": {}},
            {"type": "Copy", "inactive": False, "type_properties": {"workspaceId": WSID}},
            {"name": "", "type": "Copy", "inactive": True, "type_properties": {}},
            {"name": "   ", "inactive": True, "type_properties": {}},
            {"name": "Copy", "type": "Copy", "inactive": True, "type_properties": {}},
            {"name": None, "type": None},
            {"name": {"x": 1}, "type": ["Copy"], "inactive": False, "type_properties": {"workspaceId": WSID}}]
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": acts})
    assert got["activities"] == 7 and got["inactive_count"] == 4 and got["fixed_workspace_count"] == 2
    assert len(set(got["inactive"])) == 4 and "Copy" in got["inactive"]
    assert len(set(got["fixed_workspace"])) == 2
    unnamed = [n for k in ("inactive", "fixed_workspace", "state_unknown", "properties_unknown") for n in got[k]
               if n != "Copy"]
    assert unnamed and all(n.startswith("(unnamed ") for n in unnamed), unnamed
    named = {n for k in ("inactive", "fixed_workspace", "state_unknown", "properties_unknown") for n in got[k]}
    assert len(named) == 7  # one name per activity, none shared
    assert "(unnamed Copy activity 1)" in got["inactive"] and "(unnamed Copy activity 2)" in got["fixed_workspace"]


def test_an_unnamed_activity_doesnt_take_a_real_activitys_name():
    acts = [{"name": "(unnamed Copy activity 2)", "inactive": True, "type_properties": {}},
            {"type": "Copy", "inactive": True, "type_properties": {}}]
    got = inspect_items.inspect({"type": "DataPipeline", "definition": "read", "activities": acts})
    assert got["inactive_count"] == 2 and len(set(got["inactive"])) == 2


# ---- 6. the payload shapes of FEA-302's pipeline policy keys and FEA-305's Git evidence


def test_the_property_test_covers_the_git_and_pipeline_policy_evidence(demo):
    """test_a_malformed_payload_never_passes_fails_or_errors_a_control walks every demo record: the demo has
    a workspace.git record with a read status and a pipeline inspection with the retry/timeout keys."""
    kinds = {r["kind"]: r for r in demo["evidence"] if r["confidence"] != "insufficient"}
    assert isinstance(kinds["workspace.git"]["payload"]["status"], dict)
    assert any(r["kind"] == "item.inspection" and r["payload"].get("kind") == "pipeline"
               and isinstance(r["payload"].get("calls_out"), int) for r in demo["evidence"])


def _pipeline_record(demo):
    return next(r for r in demo["evidence"] if r["kind"] == "item.inspection"
                and r["payload"].get("kind") == "pipeline" and isinstance(r["payload"].get("calls_out"), int))


@pytest.mark.parametrize("change", [
    {"no_retry_count": 0, "no_retry": ["Copy orders"]}, {"no_timeout_count": 0, "no_timeout": ["Copy orders"]},
    {"no_retry_count": 2, "no_retry": []}, {"no_retry_count": -1}, {"calls_out": True}, {"failure_path": 1},
    {"no_retry_count": 99, "no_retry": ["a"]}, {"retry_unknown": "Copy orders"}, {"failure_path_unknown": None}])
def test_an_inconsistent_pipeline_policy_payload_never_passes(demo, change):
    rec = _pipeline_record(demo)
    bad = ev.record(rec["kind"], rec["subject"], source=rec["source"], confidence=rec["confidence"],
                    collected_at=rec["collected_at"], payload={**rec["payload"], **change})
    again = assess.rescore({**demo, "evidence": [r for r in demo["evidence"] if r["id"] != rec["id"]] + [bad]},
                           catalog.registry())
    mine = [o for o in again["outcomes"] if o["control_id"] in ("FEA-REL-004", "FEA-REL-005", "FEA-REL-006")
            and o["subject"]["id"] == rec["subject"]["id"]]
    assert mine and all(o["outcome"] == "not_evaluated" for o in mine), mine


def test_a_pipeline_inspection_from_an_older_engine_still_fits_and_leaves_the_policy_controls_not_evaluated(demo):
    rec = _pipeline_record(demo)
    old = {k: v for k, v in rec["payload"].items() if k in catalog._PIPELINE}
    assert catalog.PAYLOADS["item.inspection"](old)
    again = assess.rescore({**demo, "evidence": [r for r in demo["evidence"] if r["id"] != rec["id"]] + [
        ev.record(rec["kind"], rec["subject"], source=rec["source"], confidence=rec["confidence"],
                  collected_at=rec["collected_at"], payload=old)]}, catalog.registry())
    by = {o["control_id"]: o["outcome"] for o in again["outcomes"] if o["subject"]["id"] == rec["subject"]["id"]}
    assert by["FEA-REL-004"] == by["FEA-REL-005"] == by["FEA-REL-006"] == "not_evaluated"
    assert by["FEA-REL-003"] in ("pass", "fail")  # the keys it reads are still there


@pytest.mark.parametrize("payload", [
    {"state": "Synced", "provider": "GitHub", "branch": "main", "status": None},
    {"state": "ConnectedAndInitialized", "provider": "GitHub", "branch": "main", "status": {"uncommitted": 0}},
    {"state": "ConnectedAndInitialized", "provider": "GitHub", "branch": "main",
     "status": {"uncommitted": -1, "incoming": 0, "conflicts": 0}},
    {"state": "ConnectedAndInitialized", "provider": "GitHub", "branch": "main",
     "status": {"uncommitted": True, "incoming": 0, "conflicts": 0}}])
def test_a_malformed_git_payload_never_passes(demo, payload):
    rec = next(r for r in demo["evidence"] if r["kind"] == "workspace.git")
    bad = ev.record(rec["kind"], rec["subject"], source=rec["source"], confidence=rec["confidence"],
                    collected_at=rec["collected_at"], payload=payload)
    again = assess.rescore({**demo, "evidence": [r for r in demo["evidence"] if r["id"] != rec["id"]] + [bad]},
                           catalog.registry())
    mine = [o for o in again["outcomes"] if o["control_id"] in ("FEA-DEVOPS-003", "FEA-DEVOPS-004", "FEA-DEVOPS-005")]
    assert len(mine) == 3 and all(o["outcome"] == "not_evaluated" for o in mine), mine
