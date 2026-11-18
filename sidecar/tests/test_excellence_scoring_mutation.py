"""Mutation batch 92: excellence/scoring.py (docs/quality/mutation-testing.md). The rating floors, the
coverage thresholds at their exact values, a dimension with a small weight, and the confidence levels."""

from __future__ import annotations

import math

from fabric_desktop.excellence import profiles as pf, scoring
from fabric_desktop.excellence.controls import DIMENSIONS


def _profile(**weights: float) -> pf.Profile:
    w = {d: 0.0 for d in DIMENSIONS}
    w.update(weights)
    return pf.Profile(id="t", version="1", title="Test", weights=w)


def _outcome(cid: str, dimension: str, outcome: str = "pass", severity: str = "high",
             basis: str = "verified_configuration") -> dict:
    return {"control_id": cid, "rule": cid, "dimension": dimension, "severity": severity, "outcome": outcome,
            "basis": basis}


def test_each_rating_starts_at_its_floor():
    got = {s: scoring.rating(s)["id"] for s in (90, 89.9, 80, 79.9, 70, 69.9, 50, 49.9, 0)}
    assert got == {90: "exemplary", 89.9: "well_architected", 80: "well_architected", 79.9: "needs_attention",
                   70: "needs_attention", 69.9: "at_risk", 50: "at_risk", 49.9: "critical_improvement",
                   0: "critical_improvement"}
    assert scoring.rating(None) is None


def test_a_rounded_zero_is_never_negative():
    assert math.copysign(1, scoring._round(-0.0)) == 1
    assert scoring._round(12.345) == 12.3


def test_coverage_at_exactly_the_thresholds():
    p = _profile(security=1)
    assert scoring._status(50.0, p.provisional_coverage, p) == "provisional"
    assert scoring._status(50.0, p.provisional_coverage - 0.01, p) == "insufficient_evidence"
    assert scoring._status(50.0, p.rated_coverage, p) == "rated"
    assert scoring._status(50.0, p.rated_coverage - 0.01, p) == "provisional"
    assert scoring._status(None, 1.0, p) == "insufficient_evidence"


def test_a_dimension_with_a_weight_of_one_still_counts():
    p = _profile(security=1, governance=3)
    out = scoring.score([_outcome("A", "security"), _outcome("B", "governance", "fail")], p)
    assert out["dimensions"]["security"]["normalized_weight"] == 25.0
    assert out["dimensions"]["governance"]["normalized_weight"] == 75.0
    assert out["overall"]["score"] == 25.0


def test_a_dimension_with_no_weight_is_left_out():
    p = _profile(security=1)
    out = scoring.score([_outcome("A", "security"), _outcome("B", "governance", "fail")], p)
    assert out["dimensions"]["governance"]["normalized_weight"] == 0.0
    assert out["overall"]["score"] == 100.0


def test_confidence_at_exactly_its_thresholds():
    p = _profile(security=1)
    assert scoring._confidence(8, 10, 80.0, p) == "high"   # 80% strong, coverage 80%: both at the line
    assert scoring._confidence(8, 10, 79.9, p) == "medium"
    assert scoring._confidence(7.9, 10, 80.0, p) == "medium"
    assert scoring._confidence(5, 10, 50.0, p) == "medium"
    assert scoring._confidence(5, 10, 49.9, p) == "low"
    assert scoring._confidence(4.9, 10, 50.0, p) == "low"
    assert scoring._confidence(0, 0, 80.0, p) == "low"
    assert scoring._confidence(8, 10, None, p) == "low"


def test_outcomes_only_in_dimensions_with_no_weight_give_no_overall():
    p = _profile(security=1)
    out = scoring.score([_outcome("B", "governance")], p)
    assert out["overall"]["status"] == "insufficient_evidence" and out["overall"]["score"] is None
    assert out["dimensions"]["governance"]["normalized_weight"] == 0.0
    assert out["dimensions"]["governance"]["score"] == 100.0  # the dimension itself is still scored
