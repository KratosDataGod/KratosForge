"""Where each platform-neutral capability runs, on every engine (Chris's skins brief, 2026-11-03; docs/capabilities.md).

A capability is something a developer does that doesn't belong to one platform: run Spark SQL, run a
PySpark cell, check Snowflake SQL. For each engine in engines/registry.py this module says how the
capability runs there, as one of five classifications:

    native_local      runs on this PC the way the platform runs it
    compatible_local  runs on this PC with documented differences (rewritten for another engine, or
                      partly supported); the note says which
    simulated         a stand-in runs on this PC (DuckDB's PySpark API for Spark): good for development
                      and validation, not a runtime of the platform
    remote            runs in the platform's cloud and is billed there
    unsupported       not available on that engine

It adds no claims of its own. Every classification comes from the engine's own descriptor
(engines/capabilities.py), which already refuses a claim without evidence, so this registry can't say
more than the engines do. The skin a developer picks never changes any of it: a skin arranges pages,
and where code runs is the engine's choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engines import registry
from .engines.capabilities import PLATFORMS, EngineDescriptor, Support

CLASSIFICATIONS = {
    "native_local": "Native local execution",
    "compatible_local": "Compatible local execution, with documented differences",
    "simulated": "Local simulation or validation",
    "remote": "Remote cloud execution",
    "unsupported": "Unsupported",
}
# How a run is paid for, in the words the app shows. Never converted into each other (ADR-006).
BILLING_TEXT = {
    "none": "no cloud cost",
    "fabric_capacity": "Fabric capacity",
    "databricks_dbu": "Databricks DBUs",
    "snowflake_credits": "Snowflake credits",
}
PLATFORM_TEXT = {"local": "This PC", "fabric": "Fabric", "databricks": "Databricks", "snowflake": "Snowflake"}


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    platforms: tuple[str, ...]  # the platforms whose developers do this
    kind: str                   # "language" or "dialect": which part of a descriptor answers it
    key: str                    # the language or SQL dialect id

    def __post_init__(self) -> None:
        if self.kind not in ("language", "dialect"):
            raise ValueError(f"{self.id}: kind must be language or dialect")
        unknown = set(self.platforms) - set(PLATFORMS)
        if unknown or not self.platforms:
            raise ValueError(f"{self.id}: platforms must be some of {', '.join(PLATFORMS)}")

    def support(self, engine: EngineDescriptor) -> Support:
        return engine.language(self.key) if self.kind == "language" else engine.dialect(self.key)


CAPABILITIES: tuple[Capability, ...] = (
    Capability("sql.duckdb", "Run SQL on this PC", ("local",), "dialect", "duckdb"),
    Capability("sql.spark", "Run Spark SQL", ("fabric", "databricks"), "dialect", "spark"),
    Capability("sql.databricks", "Run Databricks SQL", ("databricks",), "dialect", "databricks"),
    Capability("sql.snowflake", "Run Snowflake SQL", ("snowflake",), "dialect", "snowflake"),
    Capability("python", "Run a Python cell", ("local", "fabric", "databricks", "snowflake"), "language", "python"),
    Capability("pyspark", "Run a PySpark cell", ("fabric", "databricks"), "language", "pyspark"),
    Capability("scala", "Run a Scala cell", ("fabric", "databricks"), "language", "scala"),
)


def classify(engine: EngineDescriptor, support: Support) -> str:
    """One of CLASSIFICATIONS for ``support`` on ``engine``. Remote wins: a cloud run is never local."""
    if not support.available:
        return "unsupported"
    if engine.remote:
        return "remote"
    return {"native": "native_local", "simulated": "simulated"}.get(support.level, "compatible_local")


def where_it_runs(engine: EngineDescriptor) -> str:
    """The words every skin shows on a run control, such as "This PC · no cloud cost"."""
    return f"{PLATFORM_TEXT[engine.platform] if engine.remote else PLATFORM_TEXT['local']} · {BILLING_TEXT[engine.billing]}"


def entry(cap: Capability, engine: EngineDescriptor) -> dict[str, Any]:
    support = cap.support(engine)
    kind = classify(engine, support)
    return {
        "engine": engine.id,
        "classification": kind,
        "level": support.level,
        "evidence": support.evidence,
        "note": support.note,
        "where": where_it_runs(engine),
        "billing": engine.billing,
        # A remote run spends the platform's money, so it is always asked for, never started silently.
        "asks_before_running": kind == "remote",
        "preview": engine.preview,
        "requirements": list(engine.requirements) if kind != "unsupported" else [],
    }


def capability(cap_id: str) -> dict[str, Any]:
    cap = next((c for c in CAPABILITIES if c.id == cap_id), None)
    if cap is None:
        raise ValueError(f"Unknown capability {cap_id!r}")
    engines = [entry(cap, e) for e in registry.ENGINES.values()]
    return {"id": cap.id, "label": cap.label, "platforms": list(cap.platforms),
            "engines": [e for e in engines if e["classification"] != "unsupported"]}


def listing() -> dict[str, Any]:
    """Every capability with the engines that can run it. Static: nothing starts or signs in."""
    return {"classifications": CLASSIFICATIONS, "capabilities": [capability(c.id) for c in CAPABILITIES]}


def docs_table() -> str:
    """The table in docs/capabilities.md (a test keeps the page equal to this)."""
    lines = ["| Capability | Engine | How it runs | Where |", "| --- | --- | --- | --- |"]
    for cap in CAPABILITIES:
        for e in capability(cap.id)["engines"]:
            label = registry.ENGINES[e["engine"]].label
            how = CLASSIFICATIONS[e["classification"]] + (" (Preview)" if e["preview"] else "")
            lines.append(f"| {cap.label} | {label} | {how} | {e['where']} |")
    # Known differences, from the engines' own notes: everything that isn't the platform's own runtime.
    notes = []
    for cap in CAPABILITIES:
        for e in capability(cap.id)["engines"]:
            if e["note"] and (e["classification"] in ("compatible_local", "simulated") or e["level"] != "native"):
                notes.append(f"- **{cap.label}, {registry.ENGINES[e['engine']].label}:** {e['note']}")
    if notes:
        lines += ["", "Known differences:", "", *notes]
    return "\n".join(lines) + "\n"
