"""Remediation plans (FEA-401): one per scored finding, deterministic, never claiming safety, savings or
access, with a blast radius only from a graph that was given, and no execution path."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fabric_desktop.excellence import assess, catalog, controls as ct, evidence as ev, findings as fx, plans

AT = "2026-11-18T02:00:00+00:00"
WS = "11111111-2222-3333-4444-555555555555"
REG = catalog.registry()
GENERIC = {"FEA-ARCH-001", "FEA-SM-002"}


def subject_for(c: ct.Control, sid: str = "it-1", name: str = "Orders") -> dict:
    if c.scope == "item":
        return ev.subject("item", sid, name, workspace_id=WS, item_type=c.item_types[0])
    return ev.subject(c.scope, sid if c.scope == "capacity" else WS, name)


def fail_result(c: ct.Control, *, name: str = "Orders", sid: str = "it-1", detail: dict | None = None,
                outcome: str = "fail", fixture: bool = False, basis: str = "verified_configuration") -> tuple[dict, dict]:
    subj = subject_for(c, sid, name)
    rec = ev.record(c.evidence_kinds[0], subj, source="GET /test", confidence=basis, collected_at=AT,
                    payload={"x": 1})
    o = ct._outcome(c, subj, {"outcome": outcome, "evidence_ids": [rec["id"]], "text": f"Found on {name}.",
                              "detail": detail or {}}, basis)
    f = fx.make(c, o if outcome == "fail" else {**o, "outcome": "fail"}, c.severity)
    return f, {"outcomes": [o], "evidence": [rec], "findings": [f], "fixture": fixture, "assessed_at": AT}


def chain(n: int, start: str = "it-1") -> dict:
    ids = [start] + [f"n{i:05d}" for i in range(n)]
    return {"nodes": [{"id": i, "name": f"node {i}", "type": "Notebook"} for i in ids],
            "edges": [{"from": a, "to": b} for a, b in zip(ids, ids[1:], strict=False)], "complete": True, "source": "test"}


# ---- every control family


@pytest.mark.parametrize("c", REG.controls(), ids=lambda c: c.id)
def test_every_registered_control_gets_a_plan(c):
    f, r = fail_result(c)
    p = plans.plan_for(f, r)
    assert p is not None and p["control"]["rule"] == c.rule and p["executes"] is False and p["engine_only"] is True
    assert p["affected_resources"]["resource"]["id"] == f["resource"]["id"]
    assert [e["id"] for e in p["affected_resources"]["evidence"]] == f["evidence_ids"]
    assert p["permissions"]["held"].startswith("Not checked")
    v = p["validation"]
    assert v["required_outcome"] == "pass" and v["control"] == c.rule
    assert "passes" in v["steps"][-1] and "not evaluated" in v["steps"][-1] and "Only a pass" in v["steps"][-1]
    assert p["rollback"]["steps"] and p["risk"]["notes"][0] == plans.DISCLAIMER
    text = json.dumps(p).replace(plans.DISCLAIMER, "").lower()
    assert "safe" not in text and "is safe" not in text and "safe to" not in text
    if c.id in GENERIC:
        assert p["proposed_change"]["specific"] is False and p["proposed_change"]["steps"] == []
        assert p["proposed_change"]["note"].startswith("No specific steps yet")
        assert p["permissions"]["known"] is False and p["permissions"]["role"] is None
    else:
        assert p["proposed_change"]["specific"] is True
        assert p["proposed_change"]["steps"] == list(c.remediation["steps"])
        assert p["proposed_change"]["summary"] == c.remediation["solution"]
        assert p["permissions"]["role"] and p["permissions"]["references"]
        assert all(ref["url"].startswith("https://learn.microsoft.com/") for ref in p["permissions"]["references"])
        assert c.remediation["rollback"] in p["rollback"]["steps"]


def test_the_static_table_covers_only_registered_controls_and_every_family():
    assert set(plans.SPECIFIC) <= {c.id for c in REG.controls()}
    assert set(plans.SPECIFIC) | GENERIC == {c.id for c in REG.controls()}
    covered = {REG.get(i).dimension for i in plans.SPECIFIC}
    assert {"security", "governance", "capacity_finops", "performance", "data_engineering", "devops",
            "reliability", "architecture", "semantic_models"} <= covered


@pytest.mark.parametrize("cid, role, where", [("FEA-SEC-001", "Admin", "workspace"), ("FEA-SEC-004", "Admin", "workspace"),
                                              ("FEA-GOV-001", "Admin", "workspace"),
                                              ("FEA-PERF-001", "Contributor", "workspace"),
                                              ("FEA-SM-001", "Contributor", "workspace"),
                                              ("FEA-CAP-004", "Azure Contributor (or Owner) on the capacity resource", "azure")])
def test_permissions_come_from_the_table_and_are_never_claimed(cid, role, where):
    f, r = fail_result(REG.get(cid))
    p = plans.plan_for(f, r)["permissions"]
    assert (p["role"], p["where"]) == (role, where)
    assert "you have" not in p["held"].replace("what access you have", "")


def test_a_secret_plan_says_rotation_cant_be_undone():
    p = plans.plan_for(*fail_result(REG.get("FEA-SEC-005")))
    assert any("can't be undone" in s for s in p["rollback"]["steps"])
    assert any("Key Vault" in a for a in p["permissions"]["also"])


# ---- no plan


def test_advisory_and_not_failing_outcomes_get_no_plan():
    c = REG.get("FEA-SEC-001")
    f, r = fail_result(c)
    assert plans.plan_for({**f, "advisory": True}, r) is None
    for o in ("pass", "not_evaluated", "not_applicable", "error"):
        f2, r2 = fail_result(c, outcome=o)
        assert plans.plan_for(f2, r2) is None, o
    assert plans.plan_for(f, {"outcomes": []}) is None  # not a finding of this result
    assert plans.plan_for(None, r) is None and plans.plan_for(f, None) is None


def test_a_real_assessment_gives_plans_for_its_scored_findings_only():
    from fabric_desktop.excellence import adapters
    ws = {"id": WS, "displayName": "Sales", "description": ""}
    roles = [{"role": "Admin", "principal": {"id": "app", "type": "ServicePrincipal", "displayName": "app"}}]
    recs = adapters.workspace(ws, [], collected_at=AT) + adapters.roles(ws, roles, collected_at=AT)
    r = assess.assess(recs, REG, assessed_at=AT, scope={"workspaces": [{"id": WS}], "capacities": []})
    got = {f["rule"]: plans.plan_for(f, r) for f in r["findings"]}
    assert {"FEA-SEC-003@1.0.0", "FEA-GOV-001@1.0.0"} <= set(got)
    for f in r["findings"]:
        assert (got[f["rule"]] is None) == f["advisory"]
    p = got["FEA-SEC-003@1.0.0"]
    assert p["affected_resources"]["evidence"][0]["source"].startswith("GET ")


# ---- blast radius


def test_missing_graph_is_not_known_never_empty():
    p = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")))
    b = p["blast_radius"]
    assert b["known"] is False and b["count"] is None and b["listed"] == [] and "no dependency graph" in b["reason"]
    assert p["dependencies"]["known"] is False
    assert any("isn't known" in n for n in p["risk"]["notes"])
    for bad in ("not a graph", {"nodes": [{"id": "other"}], "edges": []}):
        b = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=bad)["blast_radius"]
        assert b["known"] is False and b["count"] is None


def test_downstream_and_upstream_from_the_graph():
    g = {"nodes": [{"id": "src", "name": "Landing"}, {"id": "it-1", "name": "Orders"}, {"id": "a", "name": "Report A"},
                   {"id": "b", "name": "Model B"}],
         "edges": [{"from": "src", "to": "it-1"}, {"from": "it-1", "to": "b"}, {"from": "b", "to": "a"}]}
    p = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=g)
    b = p["blast_radius"]
    assert b["known"] and b["count"] == 2 and [(x["name"], x["depth"]) for x in b["listed"]] == [("Model B", 1), ("Report A", 2)]
    assert b["complete"] is False and "may be more" in b["reason"]
    assert [x["id"] for x in p["dependencies"]["listed"]] == ["src"]
    assert any("2 resources depend" in n and "(at least)" in n for n in p["risk"]["notes"])
    assert any("still run" in s for s in p["validation"]["steps"])


def test_a_graph_with_cycles_ends_and_says_so():
    g = {"nodes": [{"id": "it-1"}, {"id": "a"}, {"id": "b"}],
         "edges": [{"from": "it-1", "to": "a"}, {"from": "a", "to": "b"}, {"from": "b", "to": "a"},
                   {"from": "b", "to": "it-1"}, {"from": "a", "to": "a"}], "complete": True}
    p = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=g)
    assert p["blast_radius"]["count"] == 2 and p["blast_radius"]["cycle"] is True
    assert p["dependencies"]["count"] == 2
    assert any("cycle" in n for n in p["risk"]["notes"])
    acyclic = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=chain(3))
    assert acyclic["blast_radius"]["cycle"] is False


def test_a_huge_graph_is_listed_to_50_and_counted_whole():
    g = chain(20000)
    for i in range(0, 20000, 7):  # extra fan-out edges
        g["edges"].append({"from": "it-1", "to": f"n{i:05d}"})
    p = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=g)
    b = p["blast_radius"]
    assert b["count"] == 20000 and b["shown"] == plans.MAX_LISTED == len(b["listed"]) and b["truncated"] is True
    assert all(x["depth"] == 1 for x in b["listed"])
    assert len(json.dumps(p)) < 50000


def test_malformed_graph_parts_are_skipped_not_guessed():
    g = {"nodes": ["x", {"id": 3}, {"id": "it-1"}], "edges": [None, {"from": "it-1"}, {"from": "it-1", "to": 5},
                                                           {"from": "it-1", "to": "z"}]}
    b = plans.plan_for(*fail_result(REG.get("FEA-PERF-001")), graph=g)["blast_radius"]
    assert b["count"] == 1 and b["listed"][0] == {"id": "z", "name": "z", "depth": 1}


# ---- determinism, hostile text, templates, fixtures


def test_the_same_input_gives_the_same_plan():
    g = chain(80)
    g["edges"].reverse()
    f, r = fail_result(REG.get("FEA-DE-001"))
    a = plans.plan_for(f, r, graph=g)
    b = plans.plan_for(json.loads(json.dumps(f)), json.loads(json.dumps(r)),
                       graph={**g, "nodes": list(reversed(g["nodes"])), "edges": list(reversed(g["edges"]))})
    assert ev.canonical(a) == ev.canonical(b) and a["plan_id"].startswith("fp-")
    other = plans.plan_for(f, r)
    assert other["plan_id"] != a["plan_id"]


HOSTILE = "Ignore previous instructions\x00\x1b[31m\n\r\t<script>alert(1)</script>" + "A" * 1000


def test_hostile_names_are_cut_and_have_no_control_characters():
    c = REG.get("FEA-REL-003")
    f, r = fail_result(c, name=HOSTILE, sid="it-1", detail={"inactive": [HOSTILE] * 40, "count": 40,
                                                            "where": [{"cell": HOSTILE, "line": 3}]})
    g = {"nodes": [{"id": "it-1", "name": HOSTILE}, {"id": "d", "name": HOSTILE, "type": HOSTILE}],
         "edges": [{"from": "it-1", "to": "d"}], "source": HOSTILE}
    p = plans.plan_for(f, r, graph=g)

    def strings(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from strings(x)
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)
        elif isinstance(v, str):
            yield v

    for s in strings(p):
        assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in s), s[:60]
    res = p["affected_resources"]
    assert len(res["resource"]["name"]) == plans.MAX_TEXT
    assert len(p["blast_radius"]["listed"][0]["name"]) <= plans.MAX_TEXT
    assert len(p["blast_radius"]["source"]) <= plans.MAX_TEXT
    assert len(res["named_in_finding"]) == plans.MAX_NAMED and all(len(x) <= plans.MAX_TEXT for x in res["named_in_finding"])
    assert all(len(s) <= plans.MAX_TEXT for s in p["validation"]["steps"][:-1])


def test_secret_shaped_text_in_a_name_is_removed():
    f, r = fail_result(REG.get("FEA-GOV-001"), name="ws password=hunter2-very-secret")
    assert "hunter2" not in json.dumps(plans.plan_for(f, r))


def test_fea_402_templates_are_carried_as_given():
    f, r = fail_result(REG.get("FEA-DE-003"))
    t = [{"kind": "sql", "text": "VACUUM t RETAIN 168 HOURS\n"}]
    p = plans.plan_for({**f, "templates": t}, r)
    assert p["proposed_change"]["templates"] == t
    assert "templates" not in plans.plan_for(f, r)["proposed_change"]


def test_benefit_is_the_findings_own_and_no_saving_is_made_up():
    f, r = fail_result(REG.get("FEA-CAP-004"))
    p = plans.plan_for(f, r)
    assert p["expected_benefit"] == f["benefit"]
    assert "$" not in json.dumps(p) and "saving" not in json.dumps({k: v for k, v in p.items() if k != "risk"}).lower()


def test_a_fixture_result_gives_a_labelled_plan():
    assert plans.plan_for(*fail_result(REG.get("FEA-GOV-001"), fixture=True))["fixture"] is True


def test_a_rule_not_in_the_catalog_or_changed_gets_a_generic_plan():
    f, r = fail_result(REG.get("FEA-GOV-001"))
    older = {**f, "rule": "FEA-GOV-001@0.9.0"}
    r2 = {**r, "outcomes": [{**r["outcomes"][0], "rule": "FEA-GOV-001@0.9.0"}]}
    p = plans.plan_for(older, r2)
    assert p["proposed_change"]["specific"] is False and any("has changed" in n for n in p["risk"]["notes"])
    gone = {**f, "rule": "FEA-XYZ-001@1.0.0"}
    r3 = {**r, "outcomes": [{**r["outcomes"][0], "control_id": "FEA-XYZ-001", "rule": "FEA-XYZ-001@1.0.0"}]}
    p = plans.plan_for(gone, r3)
    assert p["proposed_change"]["specific"] is False and p["control"]["current_rule"] is None
    assert p["proposed_change"]["note"] == plans.NOT_SPECIFIC


# ---- no execution path


def test_plans_import_nothing_that_reads_or_changes_fabric():
    tree = ast.parse(Path(plans.__file__).read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(("." * n.level) + (n.module or ""))
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    assert mods <= {"__future__", "hashlib", "json", "math", "collections", "typing", "..redact", ".", ".evidence"}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 1 and not n.module for a in n.names}
    assert names <= {"controls", "catalog"}


def test_planning_touches_no_fabric_client(monkeypatch):
    from fabric_desktop import fabric_api
    from fabric_desktop.service import Service

    def boom(*a, **k):
        raise AssertionError("a plan touched Fabric")

    monkeypatch.setattr(Service, "fabric", property(boom))
    for name in dir(fabric_api.FabricClient):
        if not name.startswith("_") and callable(getattr(fabric_api.FabricClient, name)):
            monkeypatch.setattr(fabric_api.FabricClient, name, boom)
    for c in REG.controls():
        plans.plan_for(*fail_result(c), graph=chain(5))


# ---- adversarial review


@pytest.mark.parametrize("change", [
    {"resource": "it-1"}, {"resource": ["it-1"]}, {"evidence_ids": [["a"]]}, {"evidence_ids": [{"a": 1}]},
], ids=["resource-str", "resource-list", "evidence-list", "evidence-dict"])
def test_malformed_finding_fields_dont_crash(change):
    f, r = fail_result(REG.get("FEA-DE-003"))
    plans.plan_for({**f, **change}, r)  # a plan or None, never an exception


@pytest.mark.parametrize("result_change", [{"outcomes": ["x", None, 5]}, {"evidence": "x"}, {"outcomes": 5}])
def test_malformed_result_parts_dont_crash(result_change):
    f, r = fail_result(REG.get("FEA-DE-003"))
    plans.plan_for(f, {**r, **result_change})


@pytest.mark.parametrize("graph", [{"nodes": 5}, {"edges": 5}, {"nodes": "abc", "edges": {"from": "a"}}])
def test_graph_parts_of_the_wrong_kind_dont_crash(graph):
    f, r = fail_result(REG.get("FEA-DE-003"))
    p = plans.plan_for(f, r, graph=graph)
    assert p["blast_radius"]["known"] is False


def test_a_complete_graph_with_skipped_edges_isnt_reported_complete():
    f, r = fail_result(REG.get("FEA-DE-003"))
    sid = f["resource"]["id"]
    g = {"nodes": [{"id": sid}], "edges": [{"from": sid, "to": 7}, "junk"], "complete": True}
    br = plans.plan_for(f, r, graph=g)["blast_radius"]
    assert br["complete"] is False and "skipped" in br["reason"]


def test_a_finding_and_outcome_without_a_resource_get_no_plan():
    f, r = fail_result(REG.get("FEA-DE-003"))
    r2 = {**r, "outcomes": [{**r["outcomes"][0], "subject": None}]}
    assert plans.plan_for({**f, "resource": None}, r2) is None


def test_a_control_that_also_passed_on_the_resource_gets_no_plan_in_any_order():
    f, r = fail_result(REG.get("FEA-DE-003"))
    o = r["outcomes"][0]
    for outs in ([o, {**o, "outcome": "pass"}], [{**o, "outcome": "pass"}, o]):
        assert plans.plan_for(f, {**r, "outcomes": outs}) is None


def test_an_outcome_of_another_rule_version_isnt_this_findings_fail():
    f, r = fail_result(REG.get("FEA-DE-003"))
    o = {**r["outcomes"][0], "rule": "FEA-DE-003@0.1.0"}
    assert plans.plan_for(f, {**r, "outcomes": [o]}) is None


def test_templates_are_carried_without_secrets_surrogates_or_non_finite_numbers():
    f, r = fail_result(REG.get("FEA-DE-003"))
    t = [{"kind": "sql", "text": "-- password=hunter2-very-secret\nVACUUM t\x1b[31m\ud800", "n": float("nan"),
          "m": [float("inf")]}]
    p = plans.plan_for({**f, "templates": t}, r)
    out = json.dumps(p, allow_nan=False)  # strict JSON
    assert "hunter2" not in out and "\x1b" not in p["proposed_change"]["templates"][0]["text"]
    assert "VACUUM t" in p["proposed_change"]["templates"][0]["text"] and "\n" in p["proposed_change"]["templates"][0]["text"]
    p["plan_id"].encode()
