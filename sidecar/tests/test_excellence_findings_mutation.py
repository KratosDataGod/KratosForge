"""Kill tests for excellence/findings.py (docs/quality/mutation-testing.md, batch 110).

The finding id's length, what an advisory finding carries, the detail passed through, the ranking by
evidence, effort and risk when severity ties, and the five next actions by default.
"""

from __future__ import annotations

from fabric_desktop.excellence import controls as ct
from fabric_desktop.excellence import findings as fx

TEMPLATE = {"why": "w", "solution": "s", "steps": ["one"], "benefit": "may help", "effort": "low",
            "risk": "low", "validation": "v", "rollback": "r"}


def _control() -> ct.Control:
    return ct.Control(id="FEA-SEC-001", version="1.0.0", dimension="security", title="Too many admins",
                      severity="high", requirement="microsoft_recommendation", scope="workspace",
                      evidence_kinds=("workspace.roles",), evaluate=lambda ctx: {}, remediation=TEMPLATE)


def _outcome(**over):
    base = {"outcome": "fail", "subject": {"id": "ws-1"}, "text": "5 admins", "evidence_ids": ["e1"],
            "basis": "measured"}
    return {**base, **over}


def test_a_finding_id_is_fx_and_sixteen_hex_digits():
    fid = fx.finding_id("FEA-SEC-001", "ws-1")
    assert fid.startswith("fx-") and len(fid) == 19


def test_a_failed_finding_takes_its_text_and_evidence_from_the_outcome():
    f = fx.make(_control(), _outcome(advisory={"text": "advice", "evidence_ids": ["a1"]}), "high")
    assert not f["advisory"]
    assert f["discovered"] == "5 admins" and f["evidence_ids"] == ["e1"]


def test_an_advisory_finding_takes_its_text_and_evidence_from_the_advice():
    out = _outcome(outcome="not_evaluated", text="only inferred", evidence_ids=[], basis="inferred",
                   advisory={"text": "5 admins seen", "evidence_ids": ["a1", "a2"]})
    f = fx.make(_control(), out, "high")
    assert f["advisory"]
    assert f["discovered"] == "5 admins seen" and f["evidence_ids"] == ["a1", "a2"]


def test_an_advisory_finding_without_advice_is_empty():
    out = _outcome(outcome="not_evaluated", text="x", evidence_ids=["e9"], basis="inferred", advisory=None)
    f = fx.make(_control(), out, "high")
    assert f["discovered"] == "" and f["evidence_ids"] == []


def test_the_outcome_detail_is_kept_and_a_missing_one_is_empty():
    assert fx.make(_control(), _outcome(detail={"admins": 5}), "high")["detail"] == {"admins": 5}
    assert fx.make(_control(), _outcome(), "high")["detail"] == {}


def _f(fid, rule, **over):
    base = {"id": fid, "rule": rule, "advisory": False, "severity": "high", "confidence": "measured",
            "effort": "low", "risk": "low", "title": fid, "resource": {}, "solution": ""}
    return {**base, **over}


def _ids(fs):
    return [f["id"] for f in fx.rank(fs)]


def test_with_severity_tied_stronger_evidence_comes_first():
    # The rule and id order the other way, so only the evidence order can put them like this.
    assert _ids([_f("b", "R2", confidence="measured"), _f("a", "R1", confidence="verified_configuration")]) == ["b", "a"]
    assert _ids([_f("b", "R2", confidence="verified_configuration"), _f("a", "R1", confidence="inferred")]) == ["b", "a"]
    assert _ids([_f("b", "R2", confidence="inferred"), _f("a", "R1", confidence="insufficient")]) == ["b", "a"]


def test_with_evidence_tied_easier_then_safer_comes_first():
    assert _ids([_f("b", "R2", effort="low"), _f("a", "R1", effort="medium")]) == ["b", "a"]
    assert _ids([_f("b", "R2", effort="medium"), _f("a", "R1", effort="high")]) == ["b", "a"]
    assert _ids([_f("b", "R2", risk="low"), _f("a", "R1", risk="medium")]) == ["b", "a"]
    assert _ids([_f("b", "R2", risk="medium"), _f("a", "R1", risk="high")]) == ["b", "a"]


def test_next_actions_are_five_by_default():
    fs = [_f(f"f{i}", f"R{i}") for i in range(7)]
    assert [a["finding_id"] for a in fx.next_actions(fs)] == ["f0", "f1", "f2", "f3", "f4"]
