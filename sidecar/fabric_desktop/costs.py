"""What runs cost in Fabric capacity, and what running on this PC saved.

Fabric bills compute in capacity units (CU). Spark and Python notebooks use 1 CU for every 2
vCores while they run, so a job's CU-seconds are its run time times the CUs of what it ran
on. Fabric Desktop knows each run's time; the CUs per kind of run and the price of a CU hour
are settings (defaults: a 2-vCore Python notebook is 1 CU, a Spark job on a starter pool's two
medium nodes is 8 CU, and pay-as-you-go is about $0.18 per CU hour). These are estimates; the
Capacity Metrics app in Fabric has the billed numbers.

Runs on this PC start no Fabric job, so their Fabric job CU is 0. That isn't zero capacity: their
OneLake reads and writes still use the capacity of the workspace they read and write, and
aren't counted here. What the job would have used in Fabric (``avoided_cu``) comes from Fabric
runs of the same workload (``calibration.py``, FINOPS-005): an **estimate** when there are such
runs, otherwise **unknown**. Time on this PC is never turned into CU for it. The older rough
figure, the same time on a Python notebook, stays as ``scenario_cu`` and is never added to it.

Every figure is also a classified measurement (``measurement.py``): Fabric use worked out from
run time is **estimated**, the rough figure for a run on this PC is a **scenario**, and a run
whose time isn't known is **unknown** rather than 0. The plain numbers (``cu_hours``, ``cost``,
``saved``...) stay for older callers as they were: ``saved`` is still the rough scenario.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import calibration
from . import measurement as ms

DEFAULTS: dict[str, Any] = {"price_per_cu_hour": 0.18, "currency": "USD", "python_cu": 1.0, "spark_cu": 8.0}
# F SKUs: F2 has 2 CUs, F64 has 64.
_SKU_CU = {f"F{n}": n for n in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048)}
_SKU_CU |= {"P1": 64, "P2": 128, "P3": 256, "P4": 512, "P5": 1024, "FT1": 64, "FTL64": 64}


def settings(saved: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for k, v in (saved or {}).items():
        if k in DEFAULTS and v not in (None, ""):
            out[k] = v if k == "currency" else float(v)
    return out


def sku_cu(sku: str | None) -> int | None:
    return _SKU_CU.get(str(sku or "").upper())


def _parse(at: Any) -> dt.datetime | None:
    if not at:
        return None
    try:
        t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _seconds(started: Any, finished: Any) -> float | None:
    a, b = _parse(started), _parse(finished)
    return max((b - a).total_seconds(), 0.0) if a and b else None


_PRICE_NOTE = ("Priced at {price} {cur} per CU hour (Monitor > Cost > Prices). This is a price applied to CU use, not "
               "a charge: on a reserved or already-running capacity the invoice doesn't change with each run.")


def _cu_assumptions(engine: str, cu: float, timing: str) -> list[str]:
    what = {"python": "a Python notebook", "spark": "a Spark job"}.get(engine, "an item whose engine wasn't recorded, counted as Spark")
    return [timing, f"{cu:g} CU while it ran, the configured CUs of {what} (Monitor > Cost > Prices).",
            "Fabric's billed CU for the job isn't read; the Capacity Metrics app has it."]


def _estimated_cu(seconds: float, cu: float, engine: str, timing: str, ids: dict[str, Any], at: Any,
                  source: str) -> dict[str, Any]:
    return ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, seconds * cu, source=source,
                   method="run time x configured CUs", period_start=at, assumptions=_cu_assumptions(engine, cu, timing), **ids)


def _scenario_cu(seconds: float, cu: float, ids: dict[str, Any], at: Any) -> dict[str, Any]:
    return ms.make(ms.SCENARIO, "fabric_job_cu_avoided", ms.CU_SECONDS, seconds * cu,
                   method="time on this PC x configured CUs of a Python notebook", period_start=at,
                   assumptions=["The run would take as long in Fabric as it took on this PC.",
                                (f"It would run as a Python notebook at {cu:g} CU, which is what KratosForge's "
                                 "runner uses in Fabric."),
                                "OneLake reads and writes made from this PC still use capacity and aren't counted."], **ids)


def activities(meta_runs: list[dict[str, Any]], fabric_runs: list[dict[str, Any]],
               notebook_writes: list[dict[str, Any]], cfg: dict[str, Any], *,
               now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Every run as one row: what it was, where it ran, how long, and its CU-seconds.

    Each row carries ``fabric_job_cu`` (a classified measurement of the CU its Fabric job used; OneLake
    storage transactions are not part of it) and, for runs on this PC, ``avoided_cu`` (what it
    would have used in Fabric, from Fabric runs of the same workload; see ``calibrate``) and
    ``scenario_cu`` (the rough figure: its time here on a Python notebook). A run whose time isn't
    known has ``seconds`` None and unknown measurements, never 0.

    Metadata runs in Fabric are counted per table from the run log; the runner job that holds
    them is left out so its time isn't counted twice."""
    py, spark = float(cfg["python_cu"]), float(cfg["spark_cu"])
    rows: list[dict[str, Any]] = []

    def row(kind: str, name: str, env: str, where: str, at: Any, secs: float | None,
            fabric_cu: dict[str, Any], scenario: dict[str, Any] | None, **workload: Any) -> None:
        rows.append({"kind": kind, "name": name, "env": env, "where": where, "at": at, "seconds": secs,
                     "cu_seconds": fabric_cu["value"] or 0.0, "would_cu_seconds": (scenario or {}).get("value") or 0.0,
                     "fabric_job_cu": fabric_cu, "avoided_cu": None, "scenario_cu": scenario} | workload)

    def local_cu(ids: dict[str, Any], at: Any) -> dict[str, Any]:
        return ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 0.0, source="Fabric Desktop run log",
                       method="ran on this PC; no Fabric job was started (its OneLake reads and writes still use "
                              "capacity and aren't counted)", collected_at=at, period_start=at, **ids)

    for r in meta_runs:
        fabric = r.get("compute") == "fabric"
        ids = {"run_id": r.get("run_id") or None}
        secs = r.get("seconds")
        secs = float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) and secs >= 0 else None
        at = r.get("started_at")
        # One metadata table is one workload wherever it runs; "logic" is its definition's fingerprint.
        workload = {"workload_id": f"meta:{r['entity']}" if r.get("entity") else None,
                    "version": r.get("logic") or None, "rows_read": r.get("rows_read")}
        if secs is None:
            why = "The run log has no run time for this table."
            row("Metadata run", r.get("entity") or "", r.get("env") or "", "fabric" if fabric else "local", at, None,
                ms.unknown("fabric_job_cu", ms.CU_SECONDS, why, **ids) if fabric else local_cu(ids, at),
                None if fabric else ms.unknown("fabric_job_cu_avoided", ms.CU_SECONDS, why, **ids), **workload)
        elif fabric:
            row("Metadata run", r.get("entity") or "", r.get("env") or "", "fabric", at, secs,
                _estimated_cu(secs, py, "python", "Run time per table from the metadata run log; the runner "
                              "notebook's session start isn't counted.", ids, at, "Metadata run log"), None, **workload)
        else:
            row("Metadata run", r.get("entity") or "", r.get("env") or "", "local", at, secs, local_cu(ids, at),
                _scenario_cu(secs, py, ids, at), **workload)
    for r in fabric_runs:
        if r.get("kind") == "Metadata run":
            continue
        ids = {"run_id": r.get("job_id") or None, "workspace_id": r.get("workspace_id") or None,
               "workload_id": f"fabric:{r['workspace_id']}:{r['item_id']}" if r.get("workspace_id") and r.get("item_id") else None}
        secs = _seconds(r.get("started_at"), r.get("finished_at"))
        kind, name = r.get("kind") or "Fabric job", r.get("table") or r.get("name") or ""
        if secs is None:
            why = ("Still running, or Fabric hasn't reported its end yet." if not r.get("finished_at")
                   else "Its start or end time can't be read.")
            row(kind, name, r.get("env") or "", "fabric", r.get("started_at"), None,
                ms.unknown("fabric_job_cu", ms.CU_SECONDS, why, **ids), None)
            continue
        engine = r.get("engine") or ("spark" if kind == "Table maintenance" else "")
        cu = py if engine == "python" else spark
        row(kind, name, r.get("env") or "", "fabric", r.get("started_at"), secs,
            _estimated_cu(secs, cu, engine, "Run time from when this app started the job to when Fabric reported "
                          "its end, so it includes queueing and session start.", ids, r.get("started_at"),
                          "Fabric job API"), None)
    for r in notebook_writes:
        ids: dict[str, Any] = {}
        secs = r.get("seconds")
        secs = float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) and secs >= 0 else None
        at = r.get("finished_at")
        avoided = (_scenario_cu(secs, py, ids, at) if secs is not None
                   else ms.unknown("fabric_job_cu_avoided", ms.CU_SECONDS, "The write's time wasn't recorded."))
        row("Notebook write", r.get("job") or "", "", "local", at, secs, local_cu(ids, at), avoided)
    return calibrate(rows, now=now)


_NO_WORKLOAD = "A notebook write from this PC has no Fabric run of the same work to compare with."


def calibrate(rows: list[dict[str, Any]], *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Set ``avoided_cu`` on each run on this PC from the Fabric runs of its workload (calibration.py).

    The Fabric runs are a metadata table's own runs in Fabric, with their estimated job CU. Measured CU
    from an imported Capacity Metrics export isn't used yet: it covers the whole runner job, and one
    job runs many tables. Fabric runs of the same environment are preferred; others are used, with a
    note, when the same environment has none. Changes the rows in place and returns them."""
    baselines: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["where"] == "fabric" and r.get("workload_id"):
            baselines.setdefault(r["workload_id"], []).append(
                {"cu": r["fabric_job_cu"], "at": r["at"], "rows": r.get("rows_read"), "version": r.get("version"), "env": r["env"]})
    for r in rows:
        if r["where"] != "local":
            continue
        ids = {"run_id": r["fabric_job_cu"].get("run_id"), "workload_id": r.get("workload_id")}
        if not r.get("workload_id"):
            r["avoided_cu"] = ms.unknown(calibration.METRIC, ms.CU_SECONDS, _NO_WORKLOAD, **ids)
            continue
        local = {"workload_id": r["workload_id"], "run_id": ids["run_id"], "at": r["at"], "rows": r.get("rows_read"),
                 "version": r.get("version")}
        found = baselines.get(r["workload_id"], [])
        same = calibration.calibrate(local, [b for b in found if b["env"] == r["env"]], now=now)
        if same["classification"] != ms.UNKNOWN:
            r["avoided_cu"] = same
            continue
        others = [b for b in found if b["env"] != r["env"]]
        envs = ", ".join(sorted({b["env"] or "?" for b in others}))
        r["avoided_cu"] = (calibration.calibrate(local, others, now=now, notes=[
            f"Based on Fabric runs in another environment ({envs}); this run was in {r['env'] or 'no environment'}."])
            if others else same)
    return rows


def _money(cu: dict[str, Any], price: float, currency: str, metric: str) -> dict[str, Any]:
    """A CU total priced at the configured rate. Pricing adds an assumption, so it is never measured."""
    cls = cu["classification"]
    if cls == ms.UNKNOWN:
        return {"classification": ms.UNKNOWN, "label": ms.LABELS[ms.UNKNOWN], "metric": metric, "unit": ms.CURRENCY,
                "value": None, "currency": currency, "reason": cu.get("reason") or "CU use isn't known"}
    price_note = _PRICE_NOTE.format(price=price, cur=currency)
    if cls == ms.MEASURED:
        cls = ms.ESTIMATED if cu["value"] else ms.MEASURED  # 0 CU costs 0 at any price
    out = dict(cu, classification=cls, label=ms.LABELS[cls], metric=metric, unit=ms.CURRENCY, currency=currency,
               value=round(ms.convert(cu["value"], cu["unit"], ms.CU_HOURS) * price, 6),
               assumptions=sorted(set(cu.get("assumptions") or []) | ({price_note} if cu["value"] else set())),
               methods=sorted(set(cu.get("methods") or []) | ({"CU hours x price per CU hour"} if cu["value"] else set())))
    return out


_UNCALIBRATED = ("{n} of {total} run{s} on this PC {have} no comparable Fabric run of the same workload (same definition, "
                 "last {days:g} days), so what {they} would have used in Fabric isn't known. A Fabric run of the same "
                 "table lets later runs here be estimated from it.")


def _evidence(group: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """The classified totals of a group of rows: Fabric CU and its price; what the runs on this PC
    would have used in Fabric, from Fabric runs of the same workload; and, apart, the rough scenario."""
    price, cur = float(cfg["price_per_cu_hour"]), cfg["currency"]
    cu = ms.total([r["fabric_job_cu"] for r in group], "fabric_job_cu", ms.CU_HOURS)
    local = [r for r in group if r.get("avoided_cu")]
    avoided = ms.total([r["avoided_cu"] for r in local], "fabric_job_cu_avoided", ms.CU_HOURS)
    if avoided["unknown_parts"]:
        n = avoided["unknown_parts"]
        avoided["reason"] = _UNCALIBRATED.format(n=n, total=len(local), s="s" if len(local) != 1 else "",
                                                 have="has" if n == 1 else "have", they="it" if n == 1 else "they",
                                                 days=calibration.MAX_AGE_DAYS)
    scenario = ms.total([r["scenario_cu"] for r in group if r.get("scenario_cu")], "fabric_job_cu_avoided", ms.CU_HOURS)
    scenario = dict(scenario, classification=ms.SCENARIO, label=ms.LABELS[ms.SCENARIO]) \
        if scenario["classification"] != ms.UNKNOWN else scenario
    return {"fabric_job_cu": cu, "fabric_cost": _money(cu, price, cur, "fabric_cost"),
            "avoided_cu": avoided, "avoided_cost": _money(avoided, price, cur, "fabric_cost_avoided"),
            "scenario_cu": scenario, "scenario_cost": _money(scenario, price, cur, "fabric_cost_avoided")}


def report(rows: list[dict[str, Any]], cfg: dict[str, Any], days: int, now: dt.datetime | None = None,
           capacities: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Totals for the last ``days``: overall, per environment, per table or notebook, per day.

    ``capacities`` maps an environment to its capacity ({"name", "sku", "id"}), to show what share
    of the capacity its runs used over the period. Each total has an ``evidence`` dict of
    classified measurements; ``unknown_runs`` counts runs whose capacity use isn't known."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    price = float(cfg["price_per_cu_hour"])
    kept = [r for r in rows if (t := _parse(r["at"])) is not None and t >= since]

    def money(cu_seconds: float) -> float:
        return round(cu_seconds / 3600 * price, 2)

    def total(group: list[dict[str, Any]]) -> dict[str, Any]:
        cu = sum(r["cu_seconds"] for r in group)
        would = sum(r["would_cu_seconds"] for r in group)
        return {"runs": len(group), "seconds": round(sum(r["seconds"] or 0 for r in group), 1),
                "fabric_runs": sum(r["where"] == "fabric" for r in group),
                "local_runs": sum(r["where"] == "local" for r in group),
                "unknown_runs": sum(r["fabric_job_cu"]["classification"] == ms.UNKNOWN
                                    or (r.get("scenario_cu") or {}).get("classification") == ms.UNKNOWN for r in group),
                "uncalibrated_runs": sum((r.get("avoided_cu") or {}).get("classification") == ms.UNKNOWN for r in group),
                "cu_hours": round(cu / 3600, 3), "cost": money(cu),
                "saved_cu_hours": round(would / 3600, 3), "saved": money(would), "evidence": _evidence(group, cfg)}

    def grouped(key) -> list[dict[str, Any]]:
        groups: dict[Any, list[dict[str, Any]]] = {}
        for r in kept:
            groups.setdefault(key(r), []).append(r)
        return [{"key": k} | total(g) for k, g in groups.items()]

    items = sorted(grouped(lambda r: (r["kind"], r["name"], r["env"])),
                   key=lambda g: -(g["cost"] + g["saved"] + g["seconds"] / 1e6))
    by_item = [{"kind": g["key"][0], "name": g["key"][1], "env": g["key"][2]} | {k: v for k, v in g.items() if k != "key"}
               for g in items]
    by_env = []
    for g in sorted(grouped(lambda r: r["env"]), key=lambda g: str(g["key"])):
        env = g.pop("key")
        cap = (capacities or {}).get(env) or {}
        by_env.append({"env": env, "capacity": cap.get("name"), "sku": cap.get("sku")}
                      | capacity_share(g["evidence"]["fabric_job_cu"], cap, days, since, now) | g)
    by_day = sorted(({"day": g.pop("key")} | g for g in grouped(lambda r: _parse(r["at"]).date().isoformat())),
                    key=lambda g: g["day"])
    return {"days": days, "since": since.isoformat(timespec="seconds"), "settings": cfg, "total": total(kept),
            "by_env": by_env, "by_item": by_item, "by_day": by_day}


def capacity_share(cu: dict[str, Any], cap: dict[str, Any], days: int, since: dt.datetime,
                   now: dt.datetime) -> dict[str, Any]:
    """What percent of a capacity's CU over the period the runs used: ``capacity_share`` (the plain
    number, None when not known) and ``capacity_share_evidence`` (its measurement)."""
    cus = sku_cu(cap.get("sku"))
    ids = {"capacity_id": cap.get("id") or None}
    if not cus or cu["value"] is None:
        why = ("The capacity's size isn't known: sign in with capacity access, or the workspace isn't on an F or P SKU."
               if not cus else "The runs' CU use isn't known.")
        return {"capacity_share": None, "capacity_share_evidence": ms.unknown("capacity_share", ms.PERCENT, why, **ids)}
    value = min(cu["value"] * 3600 / (cus * days * 86400) * 100, 100.0)
    if cu["classification"] == ms.MEASURED:  # 0 CU of a known capacity: observed, not assumed
        m = ms.make(ms.MEASURED, "capacity_share", ms.PERCENT, value, source="Fabric Desktop run log",
                    collected_at=now, period_start=since, period_end=now, **ids)
    else:
        m = ms.make(ms.ESTIMATED, "capacity_share", ms.PERCENT, value,
                    method=f"estimated CU hours / ({cap.get('sku')} = {cus} CU x {days} days x 24 h)",
                    period_start=since, period_end=now,
                    assumptions=cu["assumptions"] + [f"{cap.get('sku')} has {cus} CUs, every hour of the period.",
                                                     ("Only runs started from KratosForge are counted, not "
                                                      "everything else on the capacity.")], **ids)
    return {"capacity_share": round(value, 3), "capacity_share_evidence": m}
