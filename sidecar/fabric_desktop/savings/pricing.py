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

``from_settings`` turns today's single price (``costs.DEFAULTS``, Optimize > Cost and savings > Prices) into the
first scenario, labelled an assumption.

A version may also hold a rate per Databricks DBU for each SKU (``rate_per_dbu``) and a rate per
Snowflake credit (``rate_per_credit``), so the platform comparison (savings/compare.py) prices CU,
DBUs and credits under one version. Both are optional and additive: a version without them has the
same row and the same ``pricing_version`` as before. ``rate_for`` gives one unit's rate and
``price_native`` prices an amount in a platform's own unit, always as a Scenario figure.

Pure: no I/O.
"""

from __future__ import annotations

import datetime as dt
import math
import re
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
# A Databricks SKU as system.billing.usage names it (sku_name), such as PREMIUM_JOBS_COMPUTE.
_DBU_SKU = re.compile(r"^[A-Z0-9_]{1,100}$")
# The unit each rate prices, as engines/usage.py names it, and how a price line names one of it.
NATIVE_UNITS = {"fabric_cu_seconds": ("rate_per_cu_hour", "CU hour"), "databricks_dbu": ("rate_per_dbu", "DBU"),
                "snowflake_credits": ("rate_per_credit", "credit")}


def _rate(value: Any, what: str) -> float:
    bad = ValueError(f"The {what} must be a finite figure above 0, not {value!r}.")
    if isinstance(value, bool):
        raise bad
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise bad from None
    if not math.isfinite(rate) or rate <= 0:
        raise bad
    return round(rate, 6)


def _dbu_rates(rates: Any) -> dict[str, float]:
    if not isinstance(rates, dict):
        raise ValueError("The rates per DBU are given by SKU, each with its rate.")
    out = {}
    for sku, rate in rates.items():
        name = str(sku or "").strip().upper()
        if not _DBU_SKU.fullmatch(name):
            raise ValueError(f"{sku!r} isn't a Databricks SKU name such as PREMIUM_JOBS_COMPUTE.")
        out[name] = _rate(rate, f"rate per DBU for {name}")
    return dict(sorted(out.items()))


def _time(value: Any, what: str) -> dt.datetime:
    t = ms.parse_time(value)
    if t is None:
        raise ValueError(f"{what} isn't a time: {value!r}")
    return t


def scenario(*, scenario: str, rate_per_cu_hour: float, currency: str, effective_from: Any,
             effective_to: Any = None, sku: str = "", region: str = "", pricing_model: str = ASSUMPTION,
             source: str = "", confidence: str = "assumption", rate_per_dbu: dict[str, Any] | None = None,
             rate_per_credit: Any = None) -> dict[str, Any]:
    """One pricing version, checked, with its stable ``pricing_version``. ``rate_per_dbu`` (by SKU)
    and ``rate_per_credit`` are optional; when given, they are part of the version."""
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
    extra: list[Any] = []
    if rate_per_dbu:
        dbu = _dbu_rates(rate_per_dbu)
        row["rate_per_dbu"] = dbu
        extra += ["dbu", *(f"{k}={v}" for k, v in dbu.items())]
    if rate_per_credit not in (None, ""):
        row["rate_per_credit"] = _rate(rate_per_credit, "rate per Snowflake credit")
        extra += ["credit", row["rate_per_credit"]]
    # Without the new rates the parts, and so the id, are what they always were.
    row["pricing_version"] = "pv-" + record_id("pricing", *(row[k] for k in (
        "scenario", "sku", "region", "currency", "rate_per_cu_hour", "pricing_model", "effective_from",
        "effective_to")), *extra)[:16]
    return row


def rate_for(version: dict[str, Any], unit: str, sku: str = "") -> float | None:
    """The version's rate for one of ``unit`` (a usage.py unit in NATIVE_UNITS), or None when it has
    none. A DBU is priced only at its own SKU's rate, never another SKU's."""
    if unit not in NATIVE_UNITS:
        raise ValueError(f"{unit!r} isn't priced by a scenario. Units: {', '.join(NATIVE_UNITS)}.")
    if unit == "databricks_dbu":
        rate = (version.get("rate_per_dbu") or {}).get(str(sku or "").upper())
    else:
        rate = version.get(NATIVE_UNITS[unit][0])
    return float(rate) if rate is not None else None


def price_native(amount: float | None, unit: str, version: dict[str, Any], *, sku: str = "",
                 metric: str = "platform_price") -> dict[str, Any]:
    """``amount`` of ``unit`` priced at ``version``: a Scenario money figure with its
    ``pricing_version``, the ``rate`` and the ``quantity`` priced, or Unknown with the reason. Fabric
    CU seconds are priced per CU hour, DBUs at their SKU's rate, credits per credit. Units are never
    converted into each other."""
    word = NATIVE_UNITS[unit][1] if unit in NATIVE_UNITS else unit
    rate = rate_for(version, unit, sku)
    none = {"pricing_version": None, "rate": None, "quantity": None}
    if amount is None:
        return ms.unknown(metric, ms.CURRENCY, "There's no figure to price.") | none
    if isinstance(amount, bool) or not math.isfinite(amount) or amount < 0:
        raise ValueError(f"The amount to price must be a finite figure of zero or more, not {amount}.")
    if rate is None:
        what = f"rate per DBU for {sku or 'this SKU'}" if unit == "databricks_dbu" else f"rate per {word}"
        return ms.unknown(metric, ms.CURRENCY, f"The scenario {version['scenario']} has no {what}.") | none
    quantity = amount / 3600 if unit == "fabric_cu_seconds" else float(amount)
    m = ms.make(ms.SCENARIO, metric, ms.CURRENCY, quantity * rate, currency=version["currency"],
                method=f"{word}s x rate per {word}",
                assumptions=[(f"{version['scenario']}: {rate:g} {version['currency']} per {word}"
                              f"{f' ({sku})' if sku else ''} ({version['pricing_model'].replace('_', ' ')}, "
                              f"{version['confidence']})"), "A price applied to what was used, not a charge"],
                source=version["source"])
    return m | {"pricing_version": version["pricing_version"], "rate": rate, "quantity": round(quantity, 6)}


def from_settings(saved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Today's single price as the first scenario: an assumption, in effect from the beginning."""
    cfg = costs.settings(saved)
    return scenario(scenario=DEFAULT_SCENARIO, rate_per_cu_hour=cfg["price_per_cu_hour"], currency=cfg["currency"],
                    effective_from=_BEGINNING, pricing_model=ASSUMPTION, confidence="assumption",
                    source="Optimize > Cost and savings > Prices")


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
