"""Capacity scores (FEA-503): pooled outcomes of capacity-relevant controls scored with the assessment's own
formulas, never an average of workspace scores; workspaces and capacities nobody assessed lower coverage
instead of disappearing; nothing is invented and versions are never mixed."""

from __future__ import annotations

import copy
import json
import random

import pytest

from fabric_desktop.excellence import assess, controls as ct, evidence as ev, portfolio, profiles as pf, scoring

REF = ({"title": "Scale your Fabric capacity", "url": "https://learn.microsoft.com/fabric/enterprise/scale-capacity"},)
TEMPLATE = {"why": "w", "solution": "s", "steps": ["a"], "benefit": "b", "effort": "low", "risk": "low",
            "validation": "v", "rollback": "r"}
CAP = "cap-1"


def _admins(ctx: ct.Context) -> dict:
    r = ctx.one("workspace.roles")
    return ct.failed([r["id"]], "Too many.") if r["payload"]["admins"] > 3 else ct.passed([r["id"]], "Fine.")


def _code(ctx: ct.Context) -> dict:
    r = ctx.one("notebook.code")
    return ct.failed([r["id"]], "Overwrites.") if "overwrite" in r["payload"]["text"] else ct.passed([r["id"]], "Merges.")


def _state(ctx: ct.Context) -> dict:
    r = ctx.one("capacity.state")
    return ct.passed([r["id"]], "Active.") if r["payload"]["state"] == "Active" else ct.failed([r["id"]], "Paused.")


def registry(cap_version: str = "1.0.0") -> ct.Registry:
    r = ct.Registry()
    common = {"requirement": "kratosforge_recommendation", "remediation": TEMPLATE, "references": REF}
    r.register(ct.Control(id="FEA-SEC-001", version="1.0.0", dimension="security", title="Too many admins",
                          severity="high", scope="workspace", evidence_kinds=("workspace.roles",), evaluate=_admins,
                          **common))
    r.register(ct.Control(id="FEA-PERF-001", version="1.0.0", dimension="performance", title="Full overwrite",
                          severity="medium", scope="item", item_types=("Notebook",), evidence_kinds=("notebook.code",),
                          evaluate=_code, **common))
    r.register(ct.Control(id="FEA-CAP-001", version=cap_version, dimension="capacity_finops", title="Capacity paused",
                          severity="medium", scope="capacity", evidence_kinds=("capacity.state",), evaluate=_state,
                          **common))
    return r


def rec(kind: str, subj: dict, payload: dict) -> dict:
    return ev.record(kind, subj, source="test", confidence="verified_configuration",
                     collected_at="2026-11-01T00:00:00+00:00", payload=payload)


def ws_snap(ws: str, notebooks: dict, at: str = "01", *, admins: int = 9, cap_state: str | None = "Active",
            reg: ct.Registry | None = None, profile: pf.Profile | None = None) -> dict:
    w = ev.subject("workspace", ws)
    records = [rec("workspace.roles", w, {"admins": admins}),
               rec("workspace.inventory", w, {"items": [{"id": i, "type": "Notebook", "name": i} for i in notebooks]})]
    records += [rec("notebook.code", ev.subject("item", i, workspace_id=ws, item_type="Notebook"), {"text": t})
                for i, t in notebooks.items()]
    scope: dict = {"workspaces": [{"id": ws, "name": ws.upper()}]}
    if cap_state is not None:
        records.append(rec("capacity.state", ev.subject("capacity", CAP), {"state": cap_state}))
        scope["capacities"] = [CAP]
    return assess.assess(records, reg or registry(), scope=scope, assessed_at=f"2026-11-{at}T09:00:00+00:00",
                         profile=profile)


def score(snaps: list, ws: list, **kw) -> dict:
    return portfolio.capacity_score(snaps, kw.pop("reg", None) or registry(), capacity_id=CAP, workspace_ids=ws, **kw)


def test_pooled_outcomes_not_an_average_of_workspace_scores():
    a = ws_snap("ws-a", {"a1": "overwrite"})
    b = ws_snap("ws-b", {"b1": "merge", "b2": "merge", "b3": "merge"})
    got = score([a, b], ["ws-a", "ws-b"])
    perf = got["scores"]["dimensions"]["performance"]
    # per workspace: 0 and 100, an average would say 50; pooled, 3 of the 4 notebooks pass
    assert a["scores"]["dimensions"]["performance"]["score"] == 0.0
    assert b["scores"]["dimensions"]["performance"]["score"] == 100.0
    assert perf["score"] == 75.0 and perf["coverage"] == 100.0
    # the same formulas over the same outcomes
    assert got["scores"] == scoring.score(got["outcomes"], pf.get())
    # security isn't capacity-relevant; the capacity control is counted once, not once per snapshot
    assert {o["control_id"] for o in got["outcomes"]} == {"FEA-PERF-001", "FEA-CAP-001"}
    assert sum(1 for o in got["outcomes"] if o["control_id"] == "FEA-CAP-001") == 1
    assert got["evidence"] == {"workspaces_listed": 2, "workspaces_assessed": 2, "capacity_assessed": True, "gaps": 0,
                               "oldest": "2026-11-01T09:00:00+00:00", "newest": "2026-11-01T09:00:00+00:00"}
    assert "not an average" in got["method"]
    json.dumps(got)


def test_findings_are_the_snapshots_own_and_only_relevant_ones():
    a = ws_snap("ws-a", {"a1": "overwrite"}, cap_state="Paused")
    got = score([a], ["ws-a"])
    ids = {f["id"] for f in a["findings"]}
    assert {f["id"] for f in got["findings"]} <= ids
    assert {f["rule"].split("@")[0] for f in got["findings"]} == {"FEA-PERF-001", "FEA-CAP-001"}  # not SEC
    assert got["findings"] == [f for f in a["findings"] if f["id"] in {g["id"] for g in got["findings"]}]


def test_unassessed_workspace_lowers_coverage_and_is_not_healthy():
    a = ws_snap("ws-a", {"a1": "merge"})
    full = score([a], ["ws-a"])
    assert full["scores"]["overall"]["coverage"] == 100.0 and full["scores"]["overall"]["score"] == 100.0
    gaps = score([a], ["ws-a", "ws-b", "ws-c"])
    assert gaps["evidence"]["workspaces_assessed"] == 1 and gaps["evidence"]["gaps"] == 2
    assert [w["state"] for w in gaps["workspaces"]] == ["assessed", "not_assessed", "not_assessed"]
    gap = next(o for o in gaps["outcomes"] if o["subject"]["id"] == "ws-b")
    assert gap["outcome"] == "not_evaluated" and gap["text"] == portfolio.NOT_ASSESSED and gap["evidence_ids"] == []
    o = gaps["scores"]["overall"]
    assert o["coverage"] < 100 and o["score"] == 99.0 and o["assured"] < full["scores"]["overall"]["assured"]
    assert gaps["findings"] == []  # a gap is never a finding


def test_capacity_with_no_evidence_has_no_score_or_rating():
    got = score([], ["ws-a", "ws-b"])
    o = got["scores"]["overall"]
    assert o["score"] is None and o["rating"] is None and o["status"] == "insufficient_evidence" and o["assured"] == 0.0
    assert got["evidence"]["capacity_assessed"] is False and got["evidence"]["oldest"] is None
    assert {x["outcome"] for x in got["outcomes"]} == {"not_evaluated"}
    nothing = score([], [])
    assert nothing["scores"]["overall"]["status"] == "insufficient_evidence"


def test_capacity_not_assessed_is_a_gap():
    a = ws_snap("ws-a", {"a1": "merge"}, cap_state=None)
    got = score([a], ["ws-a"])
    cap = next(o for o in got["outcomes"] if o["control_id"] == "FEA-CAP-001")
    assert cap["outcome"] == "not_evaluated" and cap["text"] == portfolio.CAPACITY_NOT_ASSESSED
    assert got["evidence"]["capacity_assessed"] is False and got["scores"]["overall"]["coverage"] < 100


def test_latest_snapshot_per_workspace_wins():
    old = ws_snap("ws-a", {"a1": "overwrite"}, "01", cap_state="Paused")
    new = ws_snap("ws-a", {"a1": "merge"}, "05")
    got = score([new, old], ["ws-a"])
    assert {o["outcome"] for o in got["outcomes"]} == {"pass"} and got["findings"] == []
    assert got["workspaces"][0]["assessed_at"] == new["assessed_at"]
    # the capacity's latest assessment may come from another workspace's snapshot
    other = ws_snap("ws-b", {"b1": "merge"}, "09", cap_state="Paused")
    got = score([new, old, other], ["ws-a"])
    assert next(o for o in got["outcomes"] if o["control_id"] == "FEA-CAP-001")["outcome"] == "fail"
    assert {o["subject"]["id"] for o in got["outcomes"]} == {"a1", CAP}  # ws-b isn't on the list, so not counted


def test_a_snapshot_missing_a_controls_outcomes_is_a_gap_not_healthy():
    """A snapshot that lists the workspace and capacity but holds none of a relevant control's outcomes
    must not score as fully covered: each missing control is a gap."""
    s = ws_snap("ws-a", {"a1": "overwrite"}, cap_state="Paused")
    s["outcomes"] = [o for o in s["outcomes"] if o["outcome"] != "fail"]
    got = score([s], ["ws-a"])
    assert got["scores"]["overall"]["coverage"] < 100 and got["evidence"]["gaps"] == 2
    assert (got["scores"]["overall"].get("rating") or {}).get("id") != "exemplary"
    assert {(o["control_id"], o["subject"]["id"]) for o in got["outcomes"] if o.get("gap")} == {
        ("FEA-PERF-001", "ws-a"), ("FEA-CAP-001", CAP)}


def test_same_time_snapshots_without_fingerprints_pick_the_same_one_whatever_the_order():
    a, b = ws_snap("ws-a", {"a1": "overwrite"}), ws_snap("ws-a", {"a1": "merge"})
    for s in (a, b):
        s.pop("fingerprint")
    assert json.dumps(score([a, b], ["ws-a"]), sort_keys=True) == json.dumps(score([b, a], ["ws-a"]), sort_keys=True)


def test_deterministic_and_inputs_unchanged():
    snaps = [ws_snap("ws-a", {"a1": "overwrite"}, "01"), ws_snap("ws-b", {"b1": "merge"}, "02"),
             ws_snap("ws-a", {"a1": "merge", "a2": "overwrite"}, "03", cap_state="Paused")]
    before = copy.deepcopy(snaps)
    want = json.dumps(score(snaps, ["ws-a", "ws-b", "ws-c"]), sort_keys=True)
    for seed in range(5):
        s, ws = snaps[:], ["ws-a", "ws-b", "ws-c"]
        random.Random(seed).shuffle(s)
        random.Random(seed).shuffle(ws)
        assert json.dumps(score(s, ws), sort_keys=True) == want
    assert snaps == before


def test_mixed_versions_are_refused():
    a = ws_snap("ws-a", {"a1": "merge"})
    with pytest.raises(ValueError, match="rules differ"):
        score([a], ["ws-a"], reg=registry("1.1.0"))
    other = ws_snap("ws-b", {"b1": "merge"}, profile=pf.get("capacity"))
    with pytest.raises(ValueError, match="another profile"):
        score([a, other], ["ws-a", "ws-b"])
    with pytest.raises(ValueError, match="another profile"):
        score([a], ["ws-a"], profile=pf.get("capacity"))


def _broken(change) -> dict:
    s = ws_snap("ws-a", {"a1": "overwrite"})
    change(s)
    return s


@pytest.mark.parametrize("bad, message", [
    ("nope", "list"),
    ([7], "isn't an object"),
    ([_broken(lambda s: s.update(schema="x"))], "schema"),
    ([_broken(lambda s: s.update(assessed_at="2026-11-01"))], "time zone"),
    ([_broken(lambda s: s["profile"]["weights"].update(security=float("nan")))], "finite"),
    ([_broken(lambda s: s.update(profile={"id": "baseline", "rated_coverage": float("inf")}))], "finite"),
    ([_broken(lambda s: s.update(rule_versions=None))], "rule_versions"),
    ([_broken(lambda s: s.update(scope={"workspaces": ["ws-a"]}))], "scope"),
    ([_broken(lambda s: s["outcomes"][0].update(outcome="ok"))], "outcomes"),
    ([_broken(lambda s: s["outcomes"][0].update(severity="huge"))], "outcomes"),
    ([_broken(lambda s: s["findings"][0].update(severity=None))], "findings"),
])
def test_malformed_snapshots_are_refused(bad, message):
    with pytest.raises(ValueError, match=message):
        score(bad, ["ws-a"])


@pytest.mark.parametrize("kw, message", [
    ({"capacity_id": ""}, "capacity_id"),
    ({"workspace_ids": "ws-a"}, "workspace_ids"),
    ({"workspace_ids": ["ws-a", "ws-a"]}, "distinct"),
    ({"workspace_ids": ["ws-a", ""]}, "distinct"),
    ({"workspace_ids": [f"w{i}" for i in range(portfolio.MAX_WORKSPACES + 1)]}, "at most"),
    ({"dimensions": ("capacity",)}, "unknown dimension"),
])
def test_bad_arguments_are_refused(kw, message):
    args = {"capacity_id": CAP, "workspace_ids": ["ws-a"]} | kw
    with pytest.raises(ValueError, match=message):
        portfolio.capacity_score([], registry(), **args)


def test_too_many_snapshots_are_refused():
    a = ws_snap("ws-a", {"a1": "merge"})
    with pytest.raises(ValueError, match=f"at most {portfolio.MAX_SNAPSHOTS}"):
        score([a] * (portfolio.MAX_SNAPSHOTS + 1), ["ws-a"])


def test_against_the_real_catalog_capacity_controls_are_counted():
    from fabric_desktop.excellence import catalog
    reg = catalog.registry()
    got = portfolio.capacity_score([], reg, capacity_id=CAP, workspace_ids=["ws-a"])
    cap_controls = {c.id for c in reg.controls() if c.scope == "capacity"}
    assert cap_controls <= {o["control_id"] for o in got["outcomes"]}
    assert {o["dimension"] for o in got["outcomes"] if o["subject"]["type"] == "workspace"} <= set(portfolio.DIMENSIONS)
