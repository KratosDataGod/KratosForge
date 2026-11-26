"""The first excellence controls and the read-only engine call (FEA-105): every control has a pass, a
fail and a not-evaluated case, unreadable reads never pass, and the call only reads Fabric."""

from __future__ import annotations

import json

import pytest

from fabric_desktop import audit, server
from fabric_desktop.excellence import (adapters, assess, catalog, catalog_architecture, catalog_artifacts, catalog_capacity,
                                       catalog_devops, catalog_models, catalog_performance, catalog_warehouse,
                                       controls as ct, evidence as ev,
                                       profiles)
from fabric_desktop.service import Service

AT = "2026-11-18T02:00:00+00:00"
WSID = "11111111-2222-3333-4444-555555555555"
CAP = "cccccccc-2222-3333-4444-555555555555"
WS = {"id": WSID, "displayName": "Sales", "capacityId": CAP, "description": "Sales reporting"}


def role(r: str, kind: str = "Group", name: str = "g", guest: bool = False, pid: str | None = None) -> dict:
    upn = "x_other.com#EXT#@t.example" if guest else "x@t.example"
    return {"role": r, "principal": {"id": pid or f"{name}-{r}", "type": kind, "displayName": name,
                                     "userDetails": {"userPrincipalName": upn}}}


def outcome_of(snap: dict, cid: str, subject_id: str | None = None) -> dict:
    got = [o for o in snap["outcomes"] if o["control_id"] == cid and (subject_id is None or o["subject"]["id"] == subject_id)]
    assert len(got) == 1, got
    return got[0]


def run(assignments=None, *, ws=WS, items=None, capacities=None, opt=None) -> dict:
    records = (adapters.workspace(ws, items, collected_at=AT) + adapters.roles(ws, assignments, collected_at=AT)
               + adapters.capacity(ws.get("capacityId") or "", capacities, collected_at=AT)
               + adapters.optimization(opt or {}, collected_at=AT))
    return assess.assess(records, catalog.registry(), assessed_at=AT,
                         scope={"workspaces": [{"id": ws["id"]}], "capacities": [ws["capacityId"]] if ws.get("capacityId") else []})


def test_the_catalog_registers_and_every_control_is_complete():
    r = catalog.registry()
    assert len(r) == (len(catalog.CONTROLS) + len(catalog_architecture.CONTROLS) + len(catalog_artifacts.CONTROLS)
                      + len(catalog_capacity.CONTROLS) + len(catalog_models.CONTROLS) + len(catalog_devops.CONTROLS)
                      + len(catalog_performance.CONTROLS) + len(catalog_warehouse.CONTROLS))
    assert len(catalog.CONTROLS) >= 10
    for c in r.controls():
        assert ct.problems(c) == []
        assert all(ref["url"].startswith("https://") for ref in c.references)
        assert "microsoft" not in c.title.lower()


# ---- security


@pytest.mark.parametrize("cid, failing, passing", [
    ("FEA-SEC-001", [role("Admin", name=f"a{i}") for i in range(5)], [role("Admin", name=f"a{i}") for i in range(4)]),
    ("FEA-SEC-002", [role("Contributor", "User", "Partner", guest=True)], [role("Viewer", "User", "Partner", guest=True)]),
    ("FEA-SEC-003", [role("Admin", "ServicePrincipal", "sync app")], [role("Contributor", "ServicePrincipal", "sync app")]),
    ("FEA-SEC-004", [role("Member", "User", f"p{i}") for i in range(6)], [role("Member", "User", f"p{i}") for i in range(5)]),
])
def test_security_controls_pass_fail_and_not_evaluated(cid, failing, passing):
    bad = outcome_of(run(failing), cid)
    assert bad["outcome"] == "fail" and bad["basis"] == "verified_configuration"
    assert outcome_of(run(passing), cid)["outcome"] == "pass"
    unread = outcome_of(run(None), cid)
    assert unread["outcome"] == "not_evaluated" and unread["needs"] == "workspace.roles"


def test_a_user_without_a_sign_in_name_isnt_assumed_not_to_be_a_guest():
    nameless = {"role": "Contributor", "principal": {"id": "u1", "type": "User", "displayName": "Someone"}}
    o = outcome_of(run([nameless]), "FEA-SEC-002")
    assert o["outcome"] == "not_evaluated" and "Someone" in o["text"]
    both = outcome_of(run([nameless, role("Member", "User", "Partner", guest=True)]), "FEA-SEC-002")
    assert both["outcome"] == "fail"
    group = {"role": "Member", "principal": {"id": "g1", "type": "Group", "displayName": "Team"}}
    assert outcome_of(run([group]), "FEA-SEC-002")["outcome"] == "pass"
    assert "directly assigned" in outcome_of(run([group]), "FEA-SEC-002")["text"]
    assert "directly assigned" in outcome_of(run([group]), "FEA-SEC-004")["text"]


def test_role_evidence_drops_email_addresses_and_names_who():
    recs = adapters.roles(WS, [role("Contributor", "User", "Partner", guest=True)], collected_at=AT)
    assert "#EXT#" not in json.dumps(recs) and recs[0]["payload"]["assignments"][0]["guest"] is True
    snap = run([role("Contributor", "User", "Partner", guest=True)])
    f = [f for f in snap["findings"] if f["rule"].startswith("FEA-SEC-002")][0]
    assert "Partner" in f["discovered"] and f["severity"] == "high"


def test_many_names_are_shortened():
    snap = run([role("Admin", name=f"admin{i}") for i in range(9)])
    assert "and 4 more" in outcome_of(snap, "FEA-SEC-001")["text"]


# ---- governance and capacity


def test_description_control():
    assert outcome_of(run([]), "FEA-GOV-001")["outcome"] == "pass"
    assert outcome_of(run([], ws=WS | {"description": " "}), "FEA-GOV-001")["outcome"] == "fail"


def test_capacity_control_and_invisible_capacity():
    active = [{"id": CAP.upper(), "displayName": "F64", "sku": "F64", "state": "Active"}]
    assert outcome_of(run([], capacities=active), "FEA-CAP-001")["outcome"] == "pass"
    paused = [active[0] | {"state": "Paused"}]
    o = outcome_of(run([], capacities=paused), "FEA-CAP-001")
    assert o["outcome"] == "fail" and "Paused" in o["text"]
    for caps in (None, [{"id": "other", "state": "Active"}]):
        assert outcome_of(run([], capacities=caps), "FEA-CAP-001")["outcome"] == "not_evaluated"
    no_cap = run([], ws={k: v for k, v in WS.items() if k != "capacityId"})
    assert not [o for o in no_cap["outcomes"] if o["control_id"] == "FEA-CAP-001"]


# ---- optimization-engine evidence


def workload(item: str, kind: str = "Notebook", definition: str = "read", scheduled: int | None = 5) -> dict:
    return {"item_id": item, "type": kind, "name": item, "workspace_id": WSID, "definition": definition,
            "problem": None if definition == "read" else "403",
            "runs": {"read": scheduled is not None, "scheduled": scheduled}}


def opp(rule: str, *items: str, title: str = "t") -> dict:
    return {"id": f"opp-{rule}-{'-'.join(items)}", "rule": rule, "title": title, "confidence": "proven",
            "artifacts": [{"item_id": i, "role": "change"} for i in items]}


def opt_result(workloads: list[dict], opps: list[dict]) -> dict:
    return {"workspaces": [{"id": WSID}], "workloads": workloads, "opportunities": opps}


def items_of(workloads: list[dict]) -> list[dict]:
    return [{"id": w["item_id"], "type": w["type"], "displayName": w["name"]} for w in workloads]


@pytest.mark.parametrize("cid, kind, rule", [
    ("FEA-PERF-001", "Notebook", "OE-DUP-NB"), ("FEA-PERF-002", "DataPipeline", "OE-DUP-COPY"),
    ("FEA-PERF-002", "DataPipeline", "OE-REPEAT-EXTRACT"), ("FEA-DE-001", "Notebook", "OE-FULL-REFRESH"),
    ("FEA-DE-001", "DataPipeline", "OE-FULL-REFRESH"), ("FEA-DE-002", "Notebook", "OE-DUP-NB-PARAM"),
])
def test_item_controls_pass_fail_and_not_evaluated(cid, kind, rule):
    ws = [workload("bad", kind), workload("good", kind)]
    snap = run([], items=items_of(ws), opt=opt_result(ws, [opp(rule, "bad")]))
    assert outcome_of(snap, cid, "bad")["outcome"] == "fail"
    assert outcome_of(snap, cid, "good")["outcome"] == "pass"
    ws.append(workload("unread", kind, definition="not_read"))
    snap = run([], items=items_of(ws), opt=opt_result(ws, [opp(rule, "bad")]))
    assert outcome_of(snap, cid, "bad")["outcome"] == "fail"
    assert outcome_of(snap, cid, "unread")["outcome"] == "not_evaluated"
    cov = {c["item"]["id"]: c["state"] for c in snap["item_coverage"]}
    assert cov["bad"] == "inspected" and cov["unread"] == "not_inspected"


@pytest.mark.parametrize("cid, kind", [("FEA-PERF-001", "Notebook"), ("FEA-PERF-002", "DataPipeline"),
                                       ("FEA-DE-002", "Notebook")])
def test_a_peer_that_wasnt_read_stops_a_cross_item_pass(cid, kind):
    """Its duplicate could be the unread one (read budget ran out, or no access)."""
    ws = [workload("a", kind), workload("partner", kind, definition="not_read"), workload("other", "Lakehouse")]
    snap = run([], items=items_of(ws), opt=opt_result(ws, []))
    o = outcome_of(snap, cid, "a")
    assert o["outcome"] == "not_evaluated" and "partner" in o["text"] and "partner" in o["needs"]
    many = [workload("a", kind)] + [workload(f"p{i}", kind, definition="not_read") for i in range(7)]
    o = outcome_of(run([], items=items_of(many), opt=opt_result(many, [])), cid, "a")
    assert "and 2 more" in o["text"]


def test_full_refresh_is_per_item_and_doesnt_need_its_peers():
    ws = [workload("a"), workload("partner", definition="not_read")]
    assert outcome_of(run([], items=items_of(ws), opt=opt_result(ws, [])), "FEA-DE-001", "a")["outcome"] == "pass"


def test_suspected_duplicates_are_not_evaluated_not_failed():
    ws = [workload("a"), workload("b")]
    snap = run([], items=items_of(ws), opt=opt_result(ws, [opp("OE-DUP-NB-SUSPECT", "a", "b")]))
    o = outcome_of(snap, "FEA-PERF-001", "a")
    assert o["outcome"] == "not_evaluated" and "isn't known" in o["text"]


def test_full_refresh_applies_only_to_scheduled_items_with_history():
    ws = [workload("never", scheduled=0), workload("nohist", scheduled=None)]
    snap = run([], items=items_of(ws), opt=opt_result(ws, []))
    assert outcome_of(snap, "FEA-DE-001", "never")["outcome"] == "not_applicable"
    assert outcome_of(snap, "FEA-DE-001", "nohist")["outcome"] == "not_evaluated"


def test_items_the_catalog_doesnt_cover_are_unsupported_and_no_inventory_is_not_evaluated():
    snap = run([], items=[{"id": "m", "type": "Report", "displayName": "Sales"}], opt=opt_result([], []))
    assert snap["item_coverage"] == [{"item": snap["item_coverage"][0]["item"], "state": "unsupported",
                                      "reason": "No control covers this kind of item yet."}]
    blind = run([], items=None)
    assert outcome_of(blind, "FEA-PERF-001")["outcome"] == "not_evaluated"


def test_optimization_evidence_carries_no_code():
    ws = [dict(workload("a"), cells=["password = 'hunter2'"], activities=[{"x": 1}])]  # pragma: allowlist secret (fake password: the test checks code never enters evidence)
    recs = adapters.optimization(opt_result(ws, []), collected_at=AT)
    assert "hunter2" not in json.dumps(recs) and recs[0]["confidence"] == "verified_configuration"
    assert adapters.optimization(opt_result([workload("x", definition="unsupported")], []), collected_at=AT) == []


def test_baseline_profile_title_doesnt_say_microsoft():
    for p in profiles.BUILT_IN.values():
        assert "microsoft" not in p.title.lower()
    assert profiles.get().title == "Baseline"


# ---- the engine call


class FakeFabric:
    def __init__(self, roles_error: Exception | None = None, caps_error: Exception | None = None):
        self.calls: list[tuple] = []
        self.roles_error, self.caps_error = roles_error, caps_error

    def get_workspace(self, ws):
        self.calls.append(("get_workspace", ws))
        return {"id": ws, "displayName": "Sales", "capacityId": CAP, "description": ""}

    def list_items(self, ws):
        self.calls.append(("list_items", ws))
        return [{"id": "l", "type": "Lakehouse", "displayName": "lh"}]

    def list_role_assignments(self, ws):
        self.calls.append(("list_role_assignments", ws))
        if self.roles_error:
            raise self.roles_error
        return [role("Admin", "ServicePrincipal", "app")]

    def list_capacities(self):
        self.calls.append(("list_capacities",))
        if self.caps_error:
            raise self.caps_error
        return []


@pytest.fixture
def make(tmp_path, monkeypatch):
    def build(**kw):
        fake = FakeFabric(**kw)
        monkeypatch.setattr(Service, "fabric", property(lambda self: fake))
        s = Service(root=tmp_path / "app")
        s.fake = fake
        return s
    return build


def test_the_call_only_reads(make):
    s = make()
    r = s.excellence_assess(workspace_id=WSID)
    assert {c[0] for c in s.fake.calls} <= {"get_workspace", "list_items", "list_role_assignments", "list_capacities"}
    assert outcome_of(r, "FEA-SEC-003")["outcome"] == "fail"
    assert outcome_of(r, "FEA-CAP-001")["outcome"] == "not_evaluated"
    assert any("isn't visible to you" in (x["problem"] or "") for x in r["reads"])
    assert r["fixture"] is False and r["profile"]["id"] == "baseline"
    json.dumps(r)


def test_unreadable_roles_never_pass_and_dont_leak_tokens(make):
    s = make(roles_error=RuntimeError("403 Forbidden token=abc.def.ghi"), caps_error=RuntimeError("boom"))
    r = s.excellence_assess(workspace_id=WSID, profile="enterprise_security")
    for cid in ("FEA-SEC-001", "FEA-SEC-002", "FEA-SEC-003", "FEA-SEC-004"):
        assert outcome_of(r, cid)["outcome"] == "not_evaluated"
    assert r["scores"]["dimensions"]["security"]["status"] == "insufficient_evidence"
    assert "abc.def.ghi" not in json.dumps(r)
    assert [x["ok"] for x in r["reads"] if x["read"] in ("role assignments", "capacities")] == [False, False]


def test_unreadable_workspace_settings_are_reported_not_raised(make):
    s = make()
    s.fake.get_workspace = lambda ws: (_ for _ in ()).throw(RuntimeError("404 token=abc.def.ghi"))
    r = s.excellence_assess(workspace_id=WSID)
    assert outcome_of(r, "FEA-GOV-001")["outcome"] == "not_evaluated"
    assert r["scope"]["capacities"] == []
    w = [x for x in r["reads"] if x["read"] == "workspace settings"][0]
    assert w["ok"] is False and "abc.def.ghi" not in json.dumps(r)


def test_the_demo_makes_no_fabric_call_and_is_labelled(make):
    s = make()
    r = s.excellence_assess(demo=True)
    assert s.fake.calls == [] and r["fixture"] is True and r["fixture_note"]
    assert r["findings"] and all(f["fixture"] for f in r["findings"])
    assert {"FEA-SEC-002", "FEA-SEC-003"} <= {f["rule"].split("@")[0] for f in r["findings"]}
    assert outcome_of(r, "FEA-CAP-001")["outcome"] == "pass"
    again = assess.rescore(json.loads(json.dumps(r)), catalog.registry())
    assert again["scores"] == r["scores"]


@pytest.mark.parametrize("kwargs, message", [({}, "workspace"), ({"workspace_id": "nope"}, "workspace"),
                                             ({"demo": True, "profile": "nope"}, "unknown profile")])
def test_bad_input_is_refused(make, kwargs, message):
    with pytest.raises(ValueError, match=message):
        make().excellence_assess(**kwargs)


def test_the_call_is_a_read_and_not_quick():
    assert "excellence_assess" in server.METHODS and "excellence_assess" in audit.READ_ONLY
    assert "excellence_assess" not in server.QUICK


def test_an_evidence_subject_needs_a_workspace_id():
    with pytest.raises(ValueError):
        adapters.workspace({"displayName": "x"}, [], collected_at=AT)
    assert ev.SCHEMA.endswith("@1")
