"""A critical risk caps the rating below Exemplary (Chris, 2026-11-24): the overall rating and the rating
of each dimension a failed critical control is in. The score itself never changes."""

from __future__ import annotations

from fabric_desktop.excellence import profiles as pf, scoring
from fabric_desktop.excellence.controls import DIMENSIONS


def _profile(**weights: float) -> pf.Profile:
    w = {d: 0.0 for d in DIMENSIONS}
    w.update(weights)
    return pf.Profile(id="t", version="1", title="Test", weights=w)


def _o(cid: str, dimension: str, outcome: str = "pass", severity: str = "high") -> dict:
    return {"control_id": cid, "rule": cid, "dimension": dimension, "severity": severity, "outcome": outcome,
            "basis": "verified_configuration"}


PASSES = [_o(f"S{i}", "security") for i in range(40)] + [_o(f"G{i}", "governance") for i in range(40)]
PROFILE = _profile(security=1, governance=1)


def test_rating_caps_only_above_well_architected():
    assert scoring.rating(95, critical=True) == {"id": "well_architected", "label": "Well architected",
                                                 "capped": "critical_risk"}
    assert scoring.rating(100, critical=True)["capped"] == "critical_risk"
    for s in (89.9, 80, 70, 50, 0):
        assert scoring.rating(s, critical=True) == scoring.rating(s)  # below Exemplary nothing changes
        assert "capped" not in scoring.rating(s, critical=True)
    assert scoring.rating(None, critical=True) is None
    assert scoring.rating(95) == {"id": "exemplary", "label": "Exemplary"}


def test_a_critical_failure_caps_the_overall_and_its_own_dimension_but_not_the_score():
    crit = _o("S-CRIT", "security", "fail", "critical")
    before = scoring.score(PASSES, PROFILE)
    after = scoring.score(PASSES + [crit], PROFILE)
    assert before["overall"]["rating"]["id"] == "exemplary" and not before["critical_risk"]
    assert after["critical_risk"] and after["critical_controls"] == ["S-CRIT"]
    assert after["overall"]["score"] >= 90  # still an Exemplary score...
    assert after["overall"]["rating"] == {"id": "well_architected", "label": "Well architected",
                                          "capped": "critical_risk"}  # ...but never an Exemplary rating
    assert after["dimensions"]["security"]["rating"]["capped"] == "critical_risk"
    assert after["dimensions"]["governance"]["rating"] == {"id": "exemplary", "label": "Exemplary"}


def test_a_critical_control_that_passes_or_isnt_evaluated_caps_nothing():
    for outcome in ("pass", "not_evaluated", "not_applicable"):
        got = scoring.score(PASSES + [_o("S-CRIT", "security", outcome, "critical")], PROFILE)
        assert not got["critical_risk"]
        assert got["overall"]["rating"]["id"] == "exemplary" and "capped" not in got["overall"]["rating"]


def test_a_provisional_or_unrated_score_stays_as_it_was():
    crit = _o("S-CRIT", "security", "fail", "critical")
    gaps = [_o(f"N{i}", "governance", "not_evaluated") for i in range(60)]
    got = scoring.score(PASSES + gaps + [crit], PROFILE)
    assert got["overall"]["status"] in ("provisional", "insufficient_evidence")
    if got["overall"]["status"] == "insufficient_evidence":
        assert got["overall"]["rating"] is None
