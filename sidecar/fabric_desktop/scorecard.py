"""Run scorecards: correctness, time, resources and cost of a run, each labeled for what it is.

A scorecard is a plain dict built from what a run measured, so any view can show it (the proof
panel, Monitor, a workload page). Every number says whether it is **measured** (observed on this
PC or reported by Fabric's job API), **estimated** (worked out from settings, with the
assumptions listed) or **not available**. Nothing estimated is shown as measured, and capacity
use is never shown as money saved.

Shape::

    {"correctness": {"verdict", "text", "tables", "matched", "levels": {"complete": n, "fingerprint": n}},
     "time": {"pc": {"seconds", "kind": "measured"},
              "fabric": {"total_seconds", "job_seconds", "run_seconds", "startup_seconds", "kind": "measured"},
              "faster": text},
     "resources": {"pc": {"cpu_seconds", "peak_memory_mb", "cores", "memory_gb", "kind": "measured"},
                   "fabric": {"text", "kind": "not available"}},
     "cost": {"pc": {"text", "kind": "not available", "measurement"},
              "fabric": {"cu_seconds", "amount", "currency", "kind": "estimated", "assumptions": [...],
                         "classification", "measurement"},
              "text"},
     "notes": [...]}

Each cost side also carries ``measurement``, a FinOps measurement of the Fabric *job* CU it used
(measurement.make), with its own ``classification`` (measured, estimated, scenario or unknown, as
in Monitor > Cost). Fabric's side repeats it as ``classification``; this PC's side doesn't, so it
can't be read as its total. A side whose job CU isn't known is ``unknown`` with value None,
never 0. Neither side counts OneLake reads and writes, which use capacity wherever the notebook
runs, so this PC's total capacity use is ``not available``: only its job CU (0, no job started)
is known.
"""

from __future__ import annotations

from typing import Any

from . import measurement as ms

PC_CAPACITY = ("A run on this PC starts no Fabric job, so it uses no Fabric compute. Its OneLake reads and writes "
               "still use the workspace's capacity; they aren't measured. The PC's own power and hardware aren't counted.")


def _faster(pc: float | None, fabric: float | None, what: str) -> str:
    if not pc or not fabric:
        return ""
    if pc <= fabric:
        return f"This PC was {fabric / pc:.1f}x faster {what}"
    return f"Fabric was {pc / fabric:.1f}x faster {what}"


def fabric_cost(job_seconds: float | None, engine: str, cfg: dict[str, Any], *, at: Any = None,
                ids: dict[str, Any] | None = None) -> dict[str, Any]:
    """The estimated capacity use and pay-as-you-go price of a Fabric job, with its assumptions.

    ``at`` is when the job started and ``ids`` the run's ``run_id``/``workspace_id``/``workload_id``
    for the measurement."""
    cu = float(cfg["python_cu"] if engine == "python" else cfg["spark_cu"])
    ids = {k: v for k, v in (ids or {}).items() if v}
    if not job_seconds:
        why = "Fabric didn't report the job's start and end, so capacity use isn't known."
        return {"kind": "not available", "text": why, "classification": ms.UNKNOWN,
                "measurement": ms.unknown("fabric_job_cu", ms.CU_SECONDS, why, period_start=at, **ids)}
    cu_seconds = round(job_seconds * cu, 1)
    amount = round(cu_seconds / 3600 * float(cfg["price_per_cu_hour"]), 4)
    what = "Python" if engine == "python" else "Spark"
    timing = f"Job time {job_seconds:,.1f} s (from Fabric's job API) x {cu:g} CU for a {what} notebook (Monitor > Cost settings)."
    billed = "Billed numbers are in Fabric's Capacity Metrics app."
    return {"cu_seconds": cu_seconds, "amount": amount, "currency": cfg["currency"], "kind": "estimated",
            "classification": ms.ESTIMATED,
            "assumptions": [
                timing,
                f"Priced at {cfg['price_per_cu_hour']} {cfg['currency']} per CU hour (pay-as-you-go). On a reserved "
                "or already-running capacity this is a share of capacity you pay for anyway, not an added charge.",
                billed],
            "measurement": ms.make(ms.ESTIMATED, "fabric_job_cu", ms.CU_SECONDS, job_seconds * cu,
                                   source="Fabric job API", method="run time x configured CUs", period_start=at,
                                   assumptions=[timing, billed], **ids)}


def _pc_cost(record: dict[str, Any]) -> dict[str, Any]:
    """This PC's side: no Fabric job was started, so its Fabric job CU is 0 (measured). Its OneLake
    reads and writes still use capacity and aren't measured, so its total is not available."""
    out = {"kind": "not available", "text": PC_CAPACITY}
    at = record.get("finished_at") or record.get("started_at")
    ids = {"run_id": record.get("id")} if record.get("id") else {}
    if not at:
        why = "The proof record has no run time."
        return out | {"measurement": ms.unknown("fabric_job_cu", ms.CU_SECONDS, why, **ids)}
    return out | {"measurement": ms.make(ms.MEASURED, "fabric_job_cu", ms.CU_SECONDS, 0.0, source="Fabric Desktop proof record",
                                         method="ran on this PC; no Fabric job was started (its OneLake reads and writes still use capacity)", collected_at=at,
                                         period_start=record.get("started_at"), **ids)}


def build(record: dict[str, Any], cost_cfg: dict[str, Any]) -> dict[str, Any]:
    """The scorecard of a finished proof record (prove_service) or any record with the same parts."""
    pc, fab = record.get("pc") or {}, record.get("fabric") or {}
    tables = record.get("tables") or []
    levels: dict[str, int] = {}
    for t in tables:
        if t.get("level"):
            levels[t["level"]] = levels.get(t["level"], 0) + 1
    verdict = record.get("verdict") or {}
    machine = record.get("machine") or {}
    fab_ids = {"run_id": fab.get("job_id"), "workspace_id": fab.get("workspace_id")}
    cost = (fabric_cost(fab.get("job_seconds"), fab.get("engine", "python"), cost_cfg, at=record.get("started_at"), ids=fab_ids)
            if fab else {"kind": "not available", "classification": ms.UNKNOWN,
                         "measurement": ms.unknown("fabric_job_cu", ms.CU_SECONDS, "The notebook didn't run in Fabric.")})
    notes = []
    if fab.get("total_seconds") and fab.get("job_seconds"):
        notes.append("Fabric's total includes publishing the notebook and queueing; the job time is what Fabric ran.")
    if pc.get("cpu_seconds") is not None:
        notes.append("This PC's CPU and memory are the engine process's while the notebook ran in it.")
    if fab:
        notes.append("Fabric doesn't report a notebook run's CPU or memory; a Python notebook starts with 2 vCores and "
                     "16 GB unless %%configure changes it.")
    return {
        "correctness": {"verdict": verdict.get("verdict"), "text": verdict.get("text"), "tables": len(tables),
                        "matched": sum(1 for t in tables if t.get("result") == "match"), "levels": levels},
        "time": {"pc": {"seconds": pc.get("seconds"), "kind": "measured"},
                 "fabric": {"total_seconds": fab.get("total_seconds"), "job_seconds": fab.get("job_seconds"),
                            "run_seconds": fab.get("run_seconds"), "startup_seconds": fab.get("start_seconds"),
                            "kind": "measured"},
                 "faster": _faster(pc.get("seconds"), fab.get("total_seconds"), "end to end"),
                 "faster_run": _faster(pc.get("seconds"), fab.get("run_seconds"), "on run time alone, leaving out Fabric's session start")},
        "resources": {"pc": {"cpu_seconds": pc.get("cpu_seconds"), "peak_memory_mb": pc.get("peak_memory_mb"),
                             "cores": machine.get("cores"), "memory_gb": machine.get("memory_gb"), "kind": "measured"},
                      "fabric": {"text": "Not reported by Fabric for a notebook run.", "kind": "not available"}},
        "cost": {"pc": _pc_cost(record),
                 "fabric": cost,
                 "text": (f"Fabric: about {cost['cu_seconds']:,.0f} CU-seconds, about {cost['amount']:.4f} {cost['currency']} "
                          "at pay-as-you-go prices (estimate)." if cost.get("kind") == "estimated" else
                          "Fabric capacity use isn't known for this run.")},
        "notes": notes,
    }
