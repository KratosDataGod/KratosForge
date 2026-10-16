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
     "cost": {"pc": {"capacity_cu_seconds": 0, "text", "kind": "measured"},
              "fabric": {"cu_seconds", "amount", "currency", "kind": "estimated", "assumptions": [...]},
              "text"},
     "notes": [...]}
"""

from __future__ import annotations

from typing import Any


def _faster(pc: float | None, fabric: float | None, what: str) -> str:
    if not pc or not fabric:
        return ""
    if pc <= fabric:
        return f"This PC was {fabric / pc:.1f}x faster {what}"
    return f"Fabric was {pc / fabric:.1f}x faster {what}"


def fabric_cost(job_seconds: float | None, engine: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """The estimated capacity use and pay-as-you-go price of a Fabric job, with its assumptions."""
    cu = float(cfg["python_cu"] if engine == "python" else cfg["spark_cu"])
    if not job_seconds:
        return {"kind": "not available", "text": "Fabric didn't report the job's start and end, so capacity use isn't known."}
    cu_seconds = round(job_seconds * cu, 1)
    amount = round(cu_seconds / 3600 * float(cfg["price_per_cu_hour"]), 4)
    return {"cu_seconds": cu_seconds, "amount": amount, "currency": cfg["currency"], "kind": "estimated",
            "assumptions": [
                f"Job time {job_seconds:,.1f} s (from Fabric's job API) x {cu:g} CU for a "
                f"{'Python' if engine == 'python' else 'Spark'} notebook (Monitor > Cost settings).",
                f"Priced at {cfg['price_per_cu_hour']} {cfg['currency']} per CU hour (pay-as-you-go). On a reserved "
                "or already-running capacity this is a share of capacity you pay for anyway, not an added charge.",
                "Billed numbers are in Fabric's Capacity Metrics app."]}


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
    cost = fabric_cost(fab.get("job_seconds"), fab.get("engine", "python"), cost_cfg) if fab else {"kind": "not available"}
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
        "cost": {"pc": {"capacity_cu_seconds": 0, "kind": "measured",
                        "text": "No Fabric capacity used. The PC's own power and hardware aren't counted."},
                 "fabric": cost,
                 "text": (f"Fabric: about {cost['cu_seconds']:,.0f} CU-seconds, about {cost['amount']:.4f} {cost['currency']} "
                          "at pay-as-you-go prices (estimate)." if cost.get("kind") == "estimated" else
                          "Fabric capacity use isn't known for this run.")},
        "notes": notes,
    }
