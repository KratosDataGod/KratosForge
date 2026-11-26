"""What to optimize next: open opportunities ranked by the benefit their evidence shows (ESI-603).

The opportunity engine already ranks one assessment's findings (``opportunities.assess.rank``):
counted before overlapping, then confidence, then known run time, effort and risk. This module
answers a different question across any set of opportunity records: of the ones still open, which
has the most evidence-backed benefit? It reads the same records (``opportunities.model``), their
``impact`` figures (``opportunities.impact``) and the same ``ACTION_ORDER``, and changes none of them.

**Open** means a status of ``proposed``, ``validating`` or ``approved`` in
``opportunities.model.STATUSES``: found, not yet done and not turned down. ``implemented`` and
``verified`` are done; ``rejected`` and ``superseded`` are closed. Those are listed under
``not_open`` and never ranked.

Figures of different kinds are never put in one sort key. Each opportunity falls in one tier, and
tiers come in this order:

1. ``measured``: CU avoided per month, measured.
2. ``estimated``: CU avoided per month, estimated.
3. ``run_time``: CU isn't known, but the run time avoided per month is (measured or estimated).
   Run time is not CU, so these come after every CU figure and are compared only with each other.
4. ``scenario``: the only CU figure is a scenario (what it would use if things ran differently).
   A scenario isn't evidence, so it comes after every figure that is.
5. ``unknown``: no figure. Its benefit is None, never 0 and never an average. It is still listed.

Within a tier: bigger benefit first, then ``ACTION_ORDER`` (eliminate before optimize), then id,
so the same input always gives the same order. Money (``cu_value_per_month``) is never used: it is
a value at a price, not a saving.

Pure: nothing is read or written. A bad figure (NaN, infinite or negative) is refused with an error
that names the opportunity, never skipped.
"""

from __future__ import annotations

import math
from typing import Any

from .. import measurement as ms
from ..opportunities.model import ACTION_ORDER, STATUSES

OPEN_STATUSES = ("proposed", "validating", "approved")
TIERS = ("measured", "estimated", "run_time", "scenario", "unknown")
METRIC = "fabric_cu_avoided"
_ACT = {a: i for i, a in enumerate(ACTION_ORDER)}
_TIER = {t: i for i, t in enumerate(TIERS)}
_CU_UNITS = (ms.CU_SECONDS, ms.CU_HOURS)


def _figure(o: dict[str, Any], key: str, units: tuple[str, ...]) -> dict[str, Any] | None:
    """The opportunity's impact figure ``key``, checked, or None when it has none."""
    m = (o.get("impact") or {}).get(key)
    if m is None:
        return None
    oid = o["id"]
    if not isinstance(m, dict):
        raise ValueError(f"Opportunity {oid} has an impact {key} that isn't a measurement.")
    c = m.get("classification")
    if c not in ms.CLASSIFICATIONS:
        raise ValueError(f"Opportunity {oid} has an impact {key} with an unknown classification {c!r}.")
    if m.get("unit") not in units:
        raise ValueError(f"Opportunity {oid} has an impact {key} in {m.get('unit')!r}; "
                         f"it must be in {' or '.join(units)}.")
    v = m.get("value")
    if c == ms.UNKNOWN:
        if v is not None:
            raise ValueError(f"Opportunity {oid} has an unknown impact {key} with a value; an unknown figure has none.")
        return m
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        raise ValueError(f"Opportunity {oid} has an impact {key} of {v!r}; it must be a finite number of zero or more.")
    return m


def _num(v: float) -> str:
    return f"{v:,.6g}"


def _place(o: dict[str, Any]) -> dict[str, Any]:
    """The tier, sort value and reason of one open opportunity."""
    cu = _figure(o, "cu_per_month", _CU_UNITS)
    secs = _figure(o, "run_seconds_per_month", (ms.SECONDS,))
    if cu is not None and cu["classification"] in (ms.MEASURED, ms.ESTIMATED):
        v = ms.convert(cu["value"], cu["unit"], ms.CU_HOURS)
        if cu["classification"] == ms.MEASURED:
            reason = f"Its CU avoided is measured at {_num(v)} CU hours a month, and measured figures come first."
        else:
            reason = (f"Its CU avoided is estimated at {_num(v)} CU hours a month, "
                      "and estimates rank after measured figures.")
        return {"tier": cu["classification"], "value": v, "unit": ms.CU_HOURS, "benefit": cu, "reason": reason}
    if secs is not None and secs["classification"] in (ms.MEASURED, ms.ESTIMATED):
        v = float(secs["value"])
        return {"tier": "run_time", "value": v, "unit": ms.SECONDS, "benefit": secs,
                "reason": (f"Its CU isn't known, so it is ranked by the {_num(v)} seconds of run time a month it "
                           f"avoids ({secs['classification']}), after every CU figure.")}
    if cu is not None and cu["classification"] == ms.SCENARIO:
        v = ms.convert(cu["value"], cu["unit"], ms.CU_HOURS)
        return {"tier": "scenario", "value": v, "unit": ms.CU_HOURS, "benefit": cu,
                "reason": (f"Its only CU figure is a scenario of {_num(v)} CU hours a month, which isn't evidence, "
                           "so it comes after every figure that is.")}
    why = next((str(m["reason"]) for m in (cu, secs) if m is not None and m.get("reason")),
               "No impact figure was recorded for it.")
    benefit = cu if cu is not None else ms.unknown(METRIC, ms.CU_HOURS, why)
    return {"tier": "unknown", "value": None, "unit": ms.CU_HOURS, "benefit": benefit,
            "reason": f"Its benefit isn't known, so it is listed last: {why.strip().rstrip('.')}."}


def _check(o: Any) -> None:
    if not isinstance(o, dict):
        raise ValueError(f"Each opportunity must be a record (a dict), not {type(o).__name__}.")
    oid = o.get("id")
    if not isinstance(oid, str) or not oid:
        raise ValueError(f"An opportunity has no id (got {oid!r}); every opportunity needs one to be ranked.")
    if o.get("status") not in STATUSES:
        raise ValueError(f"Opportunity {oid} has an unknown status {o.get('status')!r}; "
                         f"it must be one of {', '.join(STATUSES)}.")
    if o.get("action") not in _ACT:
        raise ValueError(f"Opportunity {oid} has an unknown action {o.get('action')!r}; "
                         f"it must be one of {', '.join(ACTION_ORDER)}.")


def rank(opportunities: list[dict[str, Any]]) -> dict[str, Any]:
    """Open opportunities in the order to work on them, each with its benefit and why it is there.

    Returns ``items`` (ranked, 1 first), ``not_open`` (id and status of the rest), ``open_statuses``
    and ``counts`` per tier. Raises ValueError on a bad record or figure, or a repeated id."""
    seen: set[str] = set()
    placed, not_open = [], []
    for o in opportunities:
        _check(o)
        if o["id"] in seen:
            raise ValueError(f"Opportunity {o['id']} appears twice; each opportunity is ranked once.")
        seen.add(o["id"])
        if o["status"] not in OPEN_STATUSES:
            not_open.append({"opportunity_id": o["id"], "status": o["status"]})
            continue
        placed.append((o, _place(o)))
    placed.sort(key=lambda p: (_TIER[p[1]["tier"]], -(p[1]["value"] or 0.0), _ACT[p[0]["action"]], p[0]["id"]))
    items = [{"rank": i, "opportunity_id": o["id"], "title": o.get("title"), "action": o["action"],
              "status": o["status"], "tier": p["tier"], "classification": p["benefit"]["classification"],
              "benefit_value": None if p["value"] is None else round(p["value"], 6), "unit": p["unit"],
              "benefit": p["benefit"], "overlaps": list(o.get("overlaps") or []), "reason": p["reason"]}
             for i, (o, p) in enumerate(placed, start=1)]
    return {"items": items, "not_open": sorted(not_open, key=lambda n: n["opportunity_id"]),
            "open_statuses": list(OPEN_STATUSES),
            "counts": {t: sum(1 for i in items if i["tier"] == t) for t in TIERS}}
