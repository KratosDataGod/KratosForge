"""Scores, coverage and ratings (FEA-103), traceable to the control outcomes behind them.

Each control carries one weight, from its severity (profiles.SEVERITY_WEIGHTS). An item control that
ran on many items splits its weight across them, so a workspace with 50 notebooks doesn't drown the
other controls. For each dimension, over the controls that apply:

- **score**    = 100 x passed weight / evaluated weight: how good what was checked is.
- **coverage** = evaluated weight / applicable weight: how much of it was checked.
- **assured**  = 100 x passed weight / applicable weight: not-evaluated counts as not passed, so
  missing evidence can never raise it. Rankings and comparisons use this one.

Not-applicable controls are left out. A dimension with no applicable weight is left out of the
overall, and the other dimensions' weights are scaled back up to 100.

A rating comes from the score only at the profile's rated coverage (default 80%). Between the
provisional and rated coverage it is ``provisional``; below, ``insufficient_evidence`` with no rating.
A score of 100 needs coverage of 100%; otherwise it's shown as 99. ``critical_risk`` is set by any
failed critical control, whatever the score.

Ratings are KratosForge product ratings, not Microsoft certifications.
"""

from __future__ import annotations

from typing import Any

from .controls import DIMENSIONS
from .evidence import SCORED
from .profiles import Profile

RATINGS = ((90, "exemplary", "Exemplary"), (80, "well_architected", "Well architected"),
           (70, "needs_attention", "Needs attention"), (50, "at_risk", "At risk"),
           (0, "critical_improvement", "Critical improvement needed"))
APPLICABLE = ("pass", "fail", "not_evaluated", "error")


def rating(score: float | None) -> dict[str, str] | None:
    if score is None:
        return None
    for floor, rid, label in RATINGS:
        if score >= floor:
            return {"id": rid, "label": label}
    return None  # pragma: no cover - the last floor is 0


def _round(x: float) -> float:
    return round(x + 0.0, 1)


def control_rows(outcomes: list[dict[str, Any]], profile: Profile) -> list[dict[str, Any]]:
    """One row per control: its weight and how much of it passed, was evaluated and applied.

    An outcome's severity is the one the engine gave it (profile override already applied)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for o in outcomes:
        by.setdefault(o["control_id"], []).append(o)
    rows = []
    for cid, outs in sorted(by.items()):
        sev = outs[0]["severity"]
        w = float(profile.severity_weights[sev])
        counts = {k: sum(1 for o in outs if o["outcome"] == k) for k in ("pass", "fail", "not_applicable",
                                                                          "not_evaluated", "error")}
        applicable = sum(counts[k] for k in APPLICABLE)
        evaluated = counts["pass"] + counts["fail"]
        strong = sum(1 for o in outs if o["outcome"] in ("pass", "fail") and o["basis"] in SCORED)
        if applicable == 0:
            status = "not_applicable"
        elif counts["fail"]:
            status = "fail"
        elif evaluated == 0:
            status = "not_evaluated"
        elif evaluated < applicable:
            status = "partial"
        else:
            status = "pass"
        share = (w / applicable) if applicable else 0.0
        rows.append({"control_id": cid, "rule": outs[0]["rule"], "dimension": outs[0]["dimension"], "severity": sev,
                     "weight": w, "status": status, "counts": counts,
                     "applicable_w": w if applicable else 0.0, "evaluated_w": share * evaluated,
                     "passed_w": share * counts["pass"], "strong_w": share * strong})
    return rows


def _status(score: float | None, coverage: float, p: Profile) -> str:
    if score is None or coverage < p.provisional_coverage:
        return "insufficient_evidence"
    return "rated" if coverage >= p.rated_coverage else "provisional"


def _block(applicable: float, evaluated: float, passed: float, p: Profile) -> dict[str, Any]:
    coverage = evaluated / applicable if applicable else 0.0
    score = 100 * passed / evaluated if evaluated else None
    assured = 100 * passed / applicable if applicable else None
    if score is not None and score >= 100 and coverage < 1:
        score = 99.0
    st = _status(score, coverage, p)
    return {"score": None if score is None else _round(score), "assured": None if assured is None else _round(assured),
            "coverage": _round(100 * coverage), "status": st,
            "rating": rating(score) if st != "insufficient_evidence" else None}


def score(outcomes: list[dict[str, Any]], profile: Profile) -> dict[str, Any]:
    """Dimension and overall scores for ``outcomes`` (controls.evaluate results)."""
    rows = control_rows(outcomes, profile)
    dims: dict[str, dict[str, Any]] = {}
    for d, label in DIMENSIONS.items():
        mine = [r for r in rows if r["dimension"] == d]
        app = sum(r["applicable_w"] for r in mine)
        ev_ = sum(r["evaluated_w"] for r in mine)
        ps = sum(r["passed_w"] for r in mine)
        block = _block(app, ev_, ps, profile) if app else {"score": None, "assured": None, "coverage": None,
                                                           "status": "not_applicable", "rating": None}
        dims[d] = {"dimension": d, "label": label, "weight": profile.weights[d], **block,
                   "controls": _counts(mine), "_app": app, "_ev": ev_, "_ps": ps}
    live = {d: x for d, x in dims.items() if x["_app"] and profile.weights[d] > 0}
    total_w = sum(profile.weights[d] for d in live)
    if not total_w:
        overall: dict[str, Any] = {"score": None, "assured": None, "coverage": None,
                                   "status": "insufficient_evidence", "rating": None}
    else:
        cov = sum(profile.weights[d] * x["_ev"] / x["_app"] for d, x in live.items()) / total_w
        assured = sum(profile.weights[d] * 100 * x["_ps"] / x["_app"] for d, x in live.items()) / total_w
        scored = {d: x for d, x in live.items() if x["_ev"]}
        sw = sum(profile.weights[d] for d in scored)
        sc = sum(profile.weights[d] * 100 * x["_ps"] / x["_ev"] for d, x in scored.items()) / sw if sw else None
        if sc is not None and sc >= 100 and cov < 1:
            sc = 99.0
        st = _status(sc, cov, profile)
        overall = {"score": None if sc is None else _round(sc), "assured": _round(assured), "coverage": _round(100 * cov),
                   "status": st, "rating": rating(sc) if st != "insufficient_evidence" else None}
    for x in dims.values():
        x["normalized_weight"] = _round(100 * profile.weights[x["dimension"]] / total_w) if x["dimension"] in live else 0.0
        for k in ("_app", "_ev", "_ps"):
            x.pop(k)
    evaluated_w = sum(r["evaluated_w"] for r in rows)
    strong_w = sum(r["strong_w"] for r in rows)
    critical = sorted({o["control_id"] for o in outcomes if o["outcome"] == "fail" and o["severity"] == "critical"})
    return {"overall": overall, "dimensions": dims, "controls": rows, "counts": _counts(rows),
            "critical_risk": bool(critical), "critical_controls": critical,
            "confidence": _confidence(strong_w, evaluated_w, overall["coverage"], profile),
            "methodology": "docs/optimize/excellence-assessment.md#how-scores-are-worked-out"}


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {k: 0 for k in ("pass", "fail", "partial", "not_applicable", "not_evaluated")}
    for r in rows:
        out[r["status"]] += 1
    out["errors"] = sum(r["counts"]["error"] for r in rows)
    return out


def _confidence(strong: float, evaluated: float, coverage_pct: float | None, p: Profile) -> str:
    if not evaluated or coverage_pct is None:
        return "low"
    share, cov = strong / evaluated, coverage_pct / 100
    if share >= 0.8 and cov >= p.rated_coverage:
        return "high"
    if share >= 0.5 and cov >= p.provisional_coverage:
        return "medium"
    return "low"
