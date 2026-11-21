"""Trends and regressions across assessment snapshots (FEA-603): transitions are classified against the last
decided outcome, a fail that goes unseen is never resolved, a score moved only by coverage says so, and
snapshots that can't be compared are flagged."""

from __future__ import annotations

import copy
import json
import random

import pytest

from fabric_desktop.excellence import assess, controls as ct, evidence as ev, profiles as pf, trends

REF = ({"title": "Roles in workspaces", "url": "https://learn.microsoft.com/fabric/fundamentals/roles-workspaces"},)
TEMPLATE = {"why": "w", "solution": "s", "steps": ["a"], "benefit": "b", "effort": "low", "risk": "low",
            "validation": "v", "rollback": "r"}
WS = {"type": "workspace", "id": "ws-1", "name": "Sales"}


def _admins(ctx: ct.Context) -> dict:
    r = ctx.one("workspace.roles")
    n = r["payload"]["admins"]
    return ct.failed([r["id"]], f"{n} admins.") if n > 3 else ct.passed([r["id"]], f"{n} admins.")


def _code(ctx: ct.Context) -> dict:
    r = ctx.one("notebook.code")
    return ct.failed([r["id"]], "Overwrites.") if "overwrite" in r["payload"]["text"] else ct.passed([r["id"]], "Merges.")


def registry(sec_version: str = "1.0.0") -> ct.Registry:
    r = ct.Registry()
    r.register(ct.Control(id="FEA-SEC-001", version=sec_version, dimension="security", title="Too many admins",
                          severity="high", requirement="kratosforge_recommendation", scope="workspace",
                          evidence_kinds=("workspace.roles",), evaluate=_admins, remediation=TEMPLATE, references=REF))
    r.register(ct.Control(id="FEA-DE-001", version="1.0.0", dimension="data_engineering", title="Full refresh",
                          severity="medium", requirement="kratosforge_recommendation", scope="item",
                          item_types=("Notebook",), evidence_kinds=("notebook.code",), evaluate=_code,
                          remediation=TEMPLATE, references=REF))
    return r


def rec(kind: str, subj: dict, payload: dict) -> dict:
    return ev.record(kind, subj, source="test", confidence="verified_configuration",
                     collected_at="2026-11-01T00:00:00+00:00", payload=payload)


def snap(at: str, admins: int | None = 5, notebooks: dict | None = None, *, reg: ct.Registry | None = None,
         profile: pf.Profile | None = None, scope: dict | None = None, fixture: bool = False) -> dict:
    notebooks = {"n1": "overwrite", "n2": "merge"} if notebooks is None else notebooks
    records = [rec("workspace.inventory", WS, {"items": [{"id": i, "type": "Notebook", "name": i} for i in notebooks]})]
    if admins is not None:
        records.append(rec("workspace.roles", WS, {"admins": admins}))
    for i, text in notebooks.items():
        if text is not None:
            records.append(rec("notebook.code", ev.subject("item", i, workspace_id="ws-1", item_type="Notebook"),
                               {"text": text}))
    return assess.assess(records, reg or registry(), scope=scope or {"workspaces": [{"id": "ws-1", "name": "Sales"}]},
                         assessed_at=f"2026-11-{at}T09:00:00+00:00", profile=profile, fixture=fixture)


def kinds(step: dict) -> dict:
    return {(t["control_id"], t["subject"]["id"]): t["kind"] for t in step["transitions"]}


# ---- transitions

def test_transitions_new_resolved_regressed_recurring():
    s1 = snap("01", 5, {"n1": "overwrite", "n2": "merge"})
    s2 = snap("02", 2, {"n1": "merge", "n2": "overwrite"})
    s3 = snap("03", 5, {"n1": "merge", "n2": "overwrite", "n3": "overwrite"})
    t = trends.trend([s1, s2, s3])
    assert t["count"] == 3 and t["comparable"] and len(t["steps"]) == 2
    assert kinds(t["steps"][0]) == {("FEA-SEC-001", "ws-1"): "resolved", ("FEA-DE-001", "n1"): "resolved",
                                    ("FEA-DE-001", "n2"): "regressed"}
    assert kinds(t["steps"][1]) == {("FEA-SEC-001", "ws-1"): "regressed", ("FEA-DE-001", "n2"): "recurring",
                                    ("FEA-DE-001", "n3"): "new_failure"}
    assert t["totals"]["regressed"] == 2 and t["totals"]["resolved"] == 2 and t["totals"]["new_failure"] == 1
    assert [(r["control_id"], r["at"]) for r in t["regressions"]] == [
        ("FEA-DE-001", s2["assessed_at"]), ("FEA-SEC-001", s3["assessed_at"])]
    step = t["steps"][0]
    assert step["overall"]["score"]["from"] == s1["scores"]["overall"]["score"]
    assert step["overall"]["score"]["delta"] == round(s2["scores"]["overall"]["score"] - s1["scores"]["overall"]["score"], 1)
    assert step["dimensions"]["security"]["assured"]["delta"] == 100.0
    assert "devops" not in step["dimensions"] or step["dimensions"]["devops"]["score"]["delta"] is None
    assert t["span"]["from"] == s1["assessed_at"] and t["span"]["to"] == s3["assessed_at"]
    json.dumps(t)


def test_fail_then_not_evaluated_is_never_resolved():
    s1 = snap("01", 5)
    s2 = snap("02", None)  # the role list couldn't be read
    t = trends.trend([s1, s2])
    gap = next(x for x in t["steps"][0]["transitions"] if x["control_id"] == "FEA-SEC-001")
    assert gap["kind"] == "newly_not_evaluated" and gap["still_failing_unverified"] and gap["last_decided"] == "fail"
    assert t["totals"]["resolved"] == 0
    assert [u["control_id"] for u in t["unverified_failures"]] == ["FEA-SEC-001"]
    assert "unverified_failures" in [n["id"] for n in t["steps"][0]["notes"]]
    # seen again failing: recurring, not new; seen passing: resolved, and no longer unverified
    again = trends.trend([s1, s2, snap("03", 6)])
    assert kinds(again["steps"][1])[("FEA-SEC-001", "ws-1")] == "recurring"
    fixed = trends.trend([s1, s2, snap("03", 2)])
    assert kinds(fixed["steps"][1])[("FEA-SEC-001", "ws-1")] == "resolved" and fixed["unverified_failures"] == []


def test_failing_item_that_leaves_the_snapshot_is_not_resolved():
    t = trends.trend([snap("01", 2, {"n1": "overwrite"}), snap("02", 2, {"n2": "merge"})])
    k = kinds(t["steps"][0])
    assert k[("FEA-DE-001", "n1")] == "no_longer_assessed" and k[("FEA-DE-001", "n2")] == "newly_passing"
    assert t["totals"]["resolved"] == 0
    assert [(u["control_id"], u["subject"]["id"], u["now"]) for u in t["unverified_failures"]] == [
        ("FEA-DE-001", "n1", None)]


def test_failing_control_turned_off_is_not_resolved():
    off = pf.Profile(**{**pf.get().__dict__, "disabled": {"FEA-SEC-001": "Not our policy."}})
    t = trends.trend([snap("01", 5), snap("02", 5, profile=off)])
    assert kinds(t["steps"][0])[("FEA-SEC-001", "ws-1")] == "no_longer_applicable"
    assert t["totals"]["resolved"] == 0 and not t["comparable"]
    assert t["steps"][0]["reasons"] == ["profile"]


def test_score_change_from_coverage_only_is_called_out():
    s1 = snap("01", 5, {"n1": "merge"})
    s2 = snap("02", None, {"n1": "merge"})  # the failing control just couldn't be checked
    step = trends.trend([s1, s2])["steps"][0]
    assert step["overall"]["score"]["delta"] > 0 and step["overall"]["coverage"]["delta"] < 0
    assert step["overall"]["assured"]["delta"] <= 0
    assert step["score_change_from_coverage_only"] and step["outcome_changes"] == 0 and step["coverage_changes"] == 1
    assert {n["id"] for n in step["notes"]} >= {"coverage_only", "score_up_coverage_down"}
    # a real change is not called coverage-only
    real = trends.trend([snap("01", 5, {"n1": "merge"}), snap("02", 2, {"n1": "merge"})])["steps"][0]
    assert not real["score_change_from_coverage_only"] and real["outcome_changes"] == 1


# ---- comparability

def test_not_comparable_profile_rules_scope_and_fixture():
    base = snap("01")
    cases = {
        "profile": snap("02", profile=pf.get("capacity")),
        "rules": snap("02", reg=registry("1.1.0")),
        "scope": snap("02", scope={"workspaces": ["ws-1"], "capacities": ["cap-9"]}),
        "fixture": snap("02", fixture=True),
    }
    for reason, other in cases.items():
        step = trends.trend([base, other])["steps"][0]
        assert step["comparable"] is False and reason in step["reasons"], reason
        assert "not_comparable" in [n["id"] for n in step["notes"]]
    rules = trends.trend([base, cases["rules"]])["steps"][0]
    assert rules["rules_changed"] == ["FEA-SEC-001"]
    assert next(x for x in rules["transitions"] if x["control_id"] == "FEA-SEC-001")["rule_changed"] is True
    assert trends.trend([base, snap("02")])["steps"][0]["comparable"] is True


# ---- determinism and inputs

def test_deterministic_whatever_the_input_order():
    snaps = [snap(f"{d:02d}", a, {"n1": t}) for d, a, t in ((1, 5, "overwrite"), (2, None, "merge"), (3, 2, None),
                                                            (4, 7, "overwrite"))]
    before = copy.deepcopy(snaps)
    want = json.dumps(trends.trend(snaps), sort_keys=True)
    for seed in range(5):
        shuffled = snaps[:]
        random.Random(seed).shuffle(shuffled)
        assert json.dumps(trends.trend(shuffled), sort_keys=True) == want
    assert snaps == before  # inputs aren't changed
    assert [s["assessed_at"][:10] for s in trends.trend(snaps[::-1])["snapshots"]] == [
        "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]


def test_timestamps_in_other_time_zones_order_by_instant():
    a, b = snap("01", 5), snap("01", 2)
    b["assessed_at"] = "2026-11-01T10:30:00+02:00"  # 08:30 UTC, before a's 09:00 UTC
    t = trends.trend([a, b])
    assert t["snapshots"][0]["assessed_at"] == b["assessed_at"]
    assert kinds(t["steps"][0])[("FEA-SEC-001", "ws-1")] == "regressed"


def test_empty_and_single():
    empty = trends.trend([])
    assert empty["count"] == 0 and empty["steps"] == [] and empty["span"] is None and empty["unverified_failures"] == []
    one = trends.trend([snap("01")])
    assert one["count"] == 1 and one["steps"] == [] and one["span"] is None and one["comparable"] is True
    assert sum(one["totals"].values()) == 0


def _broken(change) -> dict:
    s = snap("02")
    change(s)
    return s


@pytest.mark.parametrize("bad, message", [
    ("not a list", "list"),
    ([42], "isn't an object"),
    ([{}], "schema"),
    ([_broken(lambda s: s.update(schema="other@1"))], "schema"),
    ([_broken(lambda s: s.update(assessed_at="2026-11-02T09:00:00"))], "time zone"),
    ([_broken(lambda s: s.update(assessed_at="yesterday"))], "time zone"),
    ([_broken(lambda s: s.pop("policy_version"))], "policy_version"),
    ([_broken(lambda s: s.update(rule_versions=["FEA-SEC-001"]))], "rule_versions"),
    ([_broken(lambda s: s.update(profile={"id": "baseline"}))], "profile"),
    ([_broken(lambda s: s.update(scope={"workspaces": "ws-1"}))], "scope"),
    ([_broken(lambda s: s.update(scope={"workspaces": [{"name": "x"}]}))], "without an id"),
    ([_broken(lambda s: s.pop("scores"))], "scores"),
    ([_broken(lambda s: s["scores"].update(dimensions=[]))], "dimensions"),
    ([_broken(lambda s: s.update(outcomes={}))], "outcomes"),
    ([_broken(lambda s: s["outcomes"][0].update(outcome="passed"))], "malformed"),
    ([_broken(lambda s: s["outcomes"][0].pop("subject"))], "malformed"),
    ([_broken(lambda s: s["outcomes"].append(dict(s["outcomes"][0])))], "repeats"),
])
def test_malformed_input_is_refused(bad, message):
    with pytest.raises(ValueError, match=message):
        trends.trend(bad)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "80"])
@pytest.mark.parametrize("where", ["overall", "dimension"])
def test_non_finite_or_non_numeric_scores_are_refused(value, where):
    s = snap("02")
    block = s["scores"]["overall"] if where == "overall" else s["scores"]["dimensions"]["security"]
    block["coverage"] = value
    with pytest.raises(ValueError, match="finite number"):
        trends.trend([snap("01"), s])


def test_same_timestamp_twice_and_too_many_are_refused():
    with pytest.raises(ValueError, match="can't tell which came first"):
        trends.trend([snap("01", 5), snap("01", 2)])
    s = snap("01")
    with pytest.raises(ValueError, match=f"at most {trends.MAX_SNAPSHOTS}"):
        trends.trend([s] * (trends.MAX_SNAPSHOTS + 1))
