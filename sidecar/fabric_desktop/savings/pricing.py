"""Versioned pricing scenarios, and the capacity value of avoided CU under one (ESI-401).

A pricing scenario is a named rate per CU hour with what it rests on: SKU, region, currency,
the pricing model (pay as you go, reserved, actual billing, or an assumption), when it applies
(``effective_from`` up to, not including, ``effective_to``), its source and how confident it is.
Each version has a stable ``pricing_version`` id, so the same scenario entered on two PCs is one
version, and the columns match ``fdopt_pricing`` (schema version 2).

``value`` prices CU hours at the version in effect when the run happened, so a price change applies
only from its effective date and a run keeps the version it was priced at. The result is always a
**Scenario** figure (measure C in the plan): a price applied to CU, never billed savings, which
come only from Cost Management exports or a SKU change (ESI-403). Scenario figures are never added
to measured or estimated ones (``measurement.total`` refuses it).

``from_settings`` turns today's single price (``costs.DEFAULTS``, Monitor > Cost > Prices) into the
first scenario, labelled an assumption.

Pure: no I/O.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .. import costs
from .. import measurement as ms
from ..optimize.shared import record_id

METRIC = "capacity_value"
PAY_AS_YOU_GO, RESERVED, ACTUAL_BILLING, ASSUMPTION = "pay_as_you_go", "reserved", "actual_billing", "assumption"
MODELS = (PAY_AS_YOU_GO, RESERVED, ACTUAL_BILLING, ASSUMPTION)
CONFIDENCE = ("confirmed", "estimated", "assumption")
DEFAULT_SCENARIO = "Default price"
_BEGINNING = "2000-01-01T00:00:00+00:00"


def _time(value: Any, what: str) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"{what} isn't a time: {value!r}")
    return t


def scenario(*, scenario: str, rate_per_cu_hour: float, currency: str, effective_from: Any,
             effective_to: Any = None, sku: str = "", region: str = "", pricing_model: str = ASSUMPTION,
             source: str = "", confidence: str = "assumption") -> dict[str, Any]:
    """One pricing version, checked, with its stable ``pricing_version``."""
    name = (scenario or "").strip()
    if not name:
        raise ValueError("A pricing scenario needs a name.")
    rate = float(rate_per_cu_hour)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"The rate per CU hour must be a finite figure above 0, not {rate_per_cu_hour}.")
    cur = (currency or "").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise ValueError(f"The currency is a three-letter code such as USD, not {currency!r}.")
    if pricing_model not in MODELS:
        raise ValueError(f"The pricing model is one of {', '.join(MODELS)}.")
    if confidence not in CONFIDENCE:
        raise ValueError(f"The confidence is one of {', '.join(CONFIDENCE)}.")
    if pricing_model == ASSUMPTION and confidence != "assumption":
        raise ValueError("An assumed price can't be more confident than an assumption.")
    start = _time(effective_from, "effective_from")
    end = _time(effective_to, "effective_to") if effective_to not in (None, "") else None
    if end is not None and end <= start:
        raise ValueError("The end date must be after the start date.")
    sku = (sku or "").strip().upper()
    if sku and costs.sku_cu(sku) is None:
        raise ValueError(f"Unknown capacity SKU {sku!r}.")
    row = {"scenario": name, "sku": sku, "region": (region or "").strip().lower(), "currency": cur,
           "rate_per_cu_hour": round(rate, 6), "pricing_model": pricing_model,
           "effective_from": start.astimezone(dt.timezone.utc).isoformat(),
           "effective_to": end.astimezone(dt.timezone.utc).isoformat() if end else None,
           "source": (source or "").strip(), "confidence": confidence}
    row["pricing_version"] = "pv-" + record_id("pricing", *(row[k] for k in (
        "scenario", "sku", "region", "currency", "rate_per_cu_hour", "pricing_model", "effective_from",
        "effective_to")))[:16]
    return row


def from_settings(saved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Today's single price as the first scenario: an assumption, in effect from the beginning."""
    cfg = costs.settings(saved)
    return scenario(scenario=DEFAULT_SCENARIO, rate_per_cu_hour=cfg["price_per_cu_hour"], currency=cfg["currency"],
                    effective_from=_BEGINNING, pricing_model=ASSUMPTION, confidence="assumption",
                    source="Monitor > Cost > Prices")


def check(versions: list[dict[str, Any]]) -> None:
    """Refuse two versions of one scenario (same name, SKU and region) whose dates overlap, and a
    scenario in more than one currency: either would make a run's price depend on which row was
    read first, or add amounts in different currencies."""
    currencies: dict[str, set[str]] = {}
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for v in versions:
        # The id covers the price and its dates, not its source or confidence: two rows with one id
        # and different labels would let whichever was read first decide what the figure says.
        seen = by_id.setdefault(v["pricing_version"], v)
        if (seen["source"], seen["confidence"]) != (v["source"], v["confidence"]):
            raise ValueError(f"Pricing version {v['pricing_version']} of {v['scenario']!r} was entered twice with "
                             "different sources or confidence; keep one.")
        currencies.setdefault(v["scenario"], set()).add(v["currency"])
        by_key.setdefault((v["scenario"], v["sku"], v["region"]), []).append(v)
    for name, curs in currencies.items():
        if len(curs) > 1:
            raise ValueError(f"Scenario {name!r} mixes currencies; make one scenario per currency.")
    for key, vs in by_key.items():
        vs = sorted(vs, key=lambda v: _time(v["effective_from"], "effective_from"))
        for a, b in zip(vs, vs[1:], strict=False):
            if a["pricing_version"] == b["pricing_version"]:
                continue  # the same version from two PCs
            if a["effective_to"] is None or _time(a["effective_to"], "effective_to") > _time(
                    b["effective_from"], "effective_from"):
                raise ValueError(f"Two versions of scenario {key[0]!r} overlap from {b['effective_from']}; "
                                 "end the earlier one first.")


def in_effect(versions: list[dict[str, Any]], at: Any, scenario: str, *, sku: str = "",
              region: str = "") -> dict[str, Any] | None:
    """The version of ``scenario`` in effect at ``at`` (from inclusive, to exclusive), or None.
    A version for the exact SKU or region is preferred over one for any SKU or region."""
    check(versions)
    when = _time(at, "The run's time")
    sku, region = (sku or "").upper(), (region or "").lower()
    found = []
    for v in versions:
        if v["scenario"] != scenario or v["sku"] not in ("", sku) or v["region"] not in ("", region):
            continue
        if _time(v["effective_from"], "effective_from") <= when and (
                v["effective_to"] is None or when < _time(v["effective_to"], "effective_to")):
            found.append(v)
    if not found:
        return None
    return max(found, key=lambda v: (bool(v["sku"]), bool(v["region"])))


def value(cu_hours: float | None, at: Any, versions: list[dict[str, Any]], scenario: str, *, sku: str = "",
          region: str = "", run_id: str | None = None) -> dict[str, Any]:
    """CU hours priced at the version in effect at ``at``: a Scenario measurement carrying its
    ``pricing_version``, or Unknown with the reason."""
    version = in_effect(versions, at, scenario, sku=sku, region=region)
    if cu_hours is None:
        return ms.unknown(METRIC, ms.CURRENCY, "The CU avoided isn't known", run_id=run_id) | {"pricing_version": None}
    if not math.isfinite(cu_hours) or cu_hours < 0:
        raise ValueError(f"CU hours must be a finite figure of zero or more, not {cu_hours}.")
    if version is None:
        return ms.unknown(METRIC, ms.CURRENCY, f"No version of {scenario!r} was in effect then",
                          run_id=run_id) | {"pricing_version": None}
    m = ms.make(ms.SCENARIO, METRIC, ms.CURRENCY, cu_hours * version["rate_per_cu_hour"], currency=version["currency"],
                run_id=run_id, method="CU hours avoided x rate per CU hour",
                assumptions=[(f"{version['scenario']}: {version['rate_per_cu_hour']:g} {version['currency']} per CU "
                              f"hour ({version['pricing_model'].replace('_', ' ')}, {version['confidence']})"),
                             "A price applied to CU avoided, not a change in what was billed"],
                source=version["source"])
    return m | {"pricing_version": version["pricing_version"]}


def value_runs(results: list[dict[str, Any]], versions: list[dict[str, Any]], scenario: str, *, sku: str = "",
               region: str = "", net: bool = True) -> dict[str, Any]:
    """Each run's net (or gross) CU avoided priced at the version in effect when it ran, and the
    scenario total. A run counts once by execution id; runs with no figure or no price are counted
    beside the total, never as 0. The same result read twice counts once; two different results
    for one run are refused, so the total never depends on which was read first. With no runs the
    total is a Scenario 0, since a priced figure is always a scenario."""
    seen: dict[Any, dict[str, Any]] = {}
    for r in results:
        first = seen.setdefault(r["execution_id"], r)
        if first is not r and first != r:
            raise ValueError(f"Run {r['execution_id']} has two different attributions; "
                             "price one attribution per run.")
    rows = []
    for r in seen.values():
        cu = r["net_cu_hours" if net else "gross_cu_hours"]
        priced = value(cu, r["at"], versions, scenario, sku=sku, region=region, run_id=str(r["execution_id"]))
        rows.append({"execution_id": r["execution_id"], "at": r["at"], "cu_hours": cu, "value": priced})
    known = [x["value"] for x in rows if x["value"]["value"] is not None]
    currencies = {m["currency"] for m in known}
    if len(currencies) > 1:
        raise ValueError("These runs were priced in more than one currency; pick a scenario with one currency.")
    total = ms.total([x["value"] for x in rows], METRIC, ms.CURRENCY, currency=next(iter(currencies), None))
    if not rows:
        total |= {"classification": ms.SCENARIO, "label": ms.LABELS[ms.SCENARIO]}
    return {"scenario": scenario, "basis": "net" if net else "gross", "runs": rows, "total": total,
            "pricing_versions": sorted({m["pricing_version"] for m in known})}
