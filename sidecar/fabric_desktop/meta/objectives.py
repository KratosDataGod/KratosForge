"""Reliability objectives: how often a table's runs must succeed, declared in its own file.

    [objectives]
    criticality = "high"      # critical, high, standard or low; sets the defaults below
    success_rate = 95         # percent of the last `runs` runs that must succeed
    runs = 20                 # how many recent runs count
    envs = ["prod"]           # where it applies; Test and Prod when left out

Defaults by criticality: critical 99 %, high 95 %, standard 90 %, low none. An entity with no
``[objectives]`` and no criticality has no success objective; its freshness promise (if any) is
its only objective. The run log is the evidence, as for freshness.
"""

from __future__ import annotations

from typing import Any

CRITICALITY = ("critical", "high", "standard", "low")
DEFAULT_RATE = {"critical": 99.0, "high": 95.0, "standard": 90.0, "low": None}
DEFAULT_RUNS = 20
DEFAULT_ENVS = ["test", "prod"]
MIN_RUNS = 5  # fewer runs than this say nothing about a rate
_KEYS = {"criticality", "success_rate", "runs", "envs"}


def objective(spec: dict[str, Any], criticality: str | None = None) -> dict[str, Any] | None:
    """The success objective an entity declares, or one from ``criticality`` (assigned elsewhere)."""
    o = spec.get("objectives") if isinstance(spec.get("objectives"), dict) else {}
    crit = o.get("criticality") or criticality
    rate = o.get("success_rate", DEFAULT_RATE.get(crit) if crit else None)
    if rate is None:
        return None
    return {"criticality": crit, "success_rate": float(rate), "runs": int(o.get("runs") or DEFAULT_RUNS),
            "envs": list(o.get("envs") or DEFAULT_ENVS),
            "source": "entity file" if "success_rate" in o or "criticality" in o else "criticality"}


def problems(name: str, spec: dict[str, Any], environments: tuple[str, ...]) -> list[str]:
    o = spec.get("objectives")
    if o is None:
        return []
    if not isinstance(o, dict):
        return [f'{name}: [objectives] must be a table, like criticality = "high"']
    errors = []
    unknown = set(o) - _KEYS
    if unknown:
        errors.append(f"{name}: [objectives] doesn't know {', '.join(sorted(unknown))} "
                      f"(use {', '.join(sorted(_KEYS))})")
    if "criticality" in o and o["criticality"] not in CRITICALITY:
        errors.append(f"{name}: objectives criticality must be one of {', '.join(CRITICALITY)}")
    rate = o.get("success_rate")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 100):
        errors.append(f"{name}: objectives success_rate is a percent above 0, up to 100")
    runs = o.get("runs")
    if runs is not None and (isinstance(runs, bool) or not isinstance(runs, int) or runs < MIN_RUNS):
        errors.append(f"{name}: objectives runs must be a whole number of at least {MIN_RUNS}")
    envs = o.get("envs")
    if envs is not None and (not isinstance(envs, list) or not envs or any(x not in environments for x in envs)):
        errors.append(f"{name}: objectives envs must list some of {', '.join(environments)}")
    return errors


def success(obj: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """How one table stands against its success objective. ``runs`` are newest first; skipped runs
    and runs still going don't count."""
    counted = [r for r in runs if r.get("status") in ("ok", "failed", "needs_decision")][:obj["runs"]]
    ok = sum(1 for r in counted if r.get("status") == "ok")
    rate = round(100.0 * ok / len(counted), 1) if counted else None
    if len(counted) < min(MIN_RUNS, obj["runs"]):
        status = "not_enough"
    else:
        status = "met" if rate is not None and rate >= obj["success_rate"] else "breached"
    return {"status": status, "rate": rate, "counted": len(counted), "ok": ok, "target": obj["success_rate"],
            "window": obj["runs"], "expected": f"{obj['success_rate']:g} % of the last {obj['runs']} runs succeed"}
