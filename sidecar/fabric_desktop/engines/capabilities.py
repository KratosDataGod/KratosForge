"""What each execution engine can do, said honestly (ADR-001).

Every engine registers one ``EngineDescriptor``. For each language, SQL dialect and capability it
names a level:

    native       the engine does it itself, as the platform does
    partial      the engine does it, with documented gaps (``note`` says which)
    simulated    a stand-in runs on this PC (DuckDB's PySpark API standing in for Spark)
    translated   the code is rewritten for another engine first (Spark SQL to DuckDB by sqlglot)
    unsupported  not available; anything not named is unsupported

and the evidence behind it: ``unit_tests`` (tests in this repository, no cloud), ``live_verified``
(run against the real platform) or ``none``.

Rules ``EngineDescriptor`` enforces, so a wrong claim can't be registered (the same idea as the
pipeline activity registry, orchestration/registry.py):

- Anything above ``unsupported`` needs evidence; ``none`` is only allowed for ``unsupported``.
- An engine without an adapter (``adapter=False``) claims nothing but ``unsupported``; what it will
  do later goes in ``planned``, which never counts as support.
- A remote engine whose evidence is not ``live_verified`` is Preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LEVELS = ("native", "partial", "simulated", "translated", "unsupported")
EVIDENCE = ("none", "unit_tests", "live_verified")
LOCATIONS = ("local", "remote")
PLATFORMS = ("local", "fabric", "databricks", "snowflake")
# How running on the engine is paid for. Never converted into each other (ADR-006).
BILLING = ("none", "fabric_capacity", "databricks_dbu", "snowflake_credits")
CAPABILITIES = {
    "session": "Start and dispose a session",
    "execute": "Run a cell",
    "cancel": "Stop a running cell",
    "stream": "Show output while a cell runs",
    "variables": "List the session's variables",
    "debug": "Step through code",
    "plan": "Show the query plan",
    "metrics": "Measure a run (time, rows, memory)",
    "catalog": "List the catalogs, schemas and tables the session can read",
    "cost": "Report usage the platform bills",
    "compat": "Check a cell before running it",
}
LANGUAGES = ("python", "pyspark", "sql", "scala", "r", "markdown")


@dataclass(frozen=True)
class Support:
    level: str = "unsupported"
    note: str = ""
    evidence: str = "none"

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"Unknown support level {self.level!r}. Levels: {', '.join(LEVELS)}.")
        if self.evidence not in EVIDENCE:
            raise ValueError(f"Unknown evidence {self.evidence!r}. Evidence: {', '.join(EVIDENCE)}.")
        if self.level != "unsupported" and self.evidence == "none":
            raise ValueError(f"A {self.level} claim needs evidence (unit_tests or live_verified).")

    @property
    def available(self) -> bool:
        return self.level != "unsupported"

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "note": self.note, "evidence": self.evidence}


UNSUPPORTED = Support()


@dataclass(frozen=True)
class EngineDescriptor:
    id: str
    label: str
    location: str                   # LOCATIONS
    platform: str                   # PLATFORMS
    billing: str                    # BILLING
    adapter: bool                   # an adapter in this version can open sessions
    languages: dict[str, Support] = field(default_factory=dict)
    dialects: dict[str, Support] = field(default_factory=dict)   # sql_dialects ids, plus "sqlite", "polars"
    capabilities: dict[str, Support] = field(default_factory=dict)
    requirements: tuple[str, ...] = ()          # what must be installed or reachable
    limitations: tuple[str, ...] = ()
    planned: str = ""                           # what a later phase adds; never support
    family: str = ""                            # engines of one family can keep a session across a switch
    preview: bool = False

    def __post_init__(self) -> None:
        if self.location not in LOCATIONS or self.platform not in PLATFORMS or self.billing not in BILLING:
            raise ValueError(f"{self.id}: location, platform or billing is not one of the known values.")
        unknown = set(self.capabilities) - set(CAPABILITIES)
        if unknown:
            raise ValueError(f"{self.id}: unknown capabilities {sorted(unknown)}.")
        unknown = set(self.languages) - set(LANGUAGES)
        if unknown:
            raise ValueError(f"{self.id}: unknown languages {sorted(unknown)}.")
        claims = [*self.languages.values(), *self.dialects.values(), *self.capabilities.values()]
        if not self.adapter and any(s.available for s in claims):
            raise ValueError(f"{self.id}: has no adapter in this version, so it can't claim support. "
                             "Put what it will do in 'planned'.")
        if self.adapter and not self.capability("execute").available:
            raise ValueError(f"{self.id}: an adapter must at least execute.")
        if (self.location == "remote" and not self.preview
                and any(s.available and s.evidence != "live_verified" for s in claims)):
            raise ValueError(f"{self.id}: a remote engine not verified against the live platform must be Preview.")
        if self.location == "remote" and self.billing == "none":
            raise ValueError(f"{self.id}: remote engines say how they are billed.")

    def capability(self, name: str) -> Support:
        if name not in CAPABILITIES:
            raise ValueError(f"Unknown capability {name!r}.")
        return self.capabilities.get(name, UNSUPPORTED)

    def language(self, name: str) -> Support:
        return self.languages.get(name, UNSUPPORTED)

    def dialect(self, name: str) -> Support:
        return self.dialects.get(name, UNSUPPORTED)

    @property
    def remote(self) -> bool:
        return self.location == "remote"

    def as_dict(self) -> dict[str, Any]:
        """Every capability listed, unsupported ones included, so nothing is left to guess."""
        return {
            "id": self.id, "label": self.label, "location": self.location, "platform": self.platform,
            "billing": self.billing, "adapter": self.adapter, "preview": self.preview, "family": self.family,
            "languages": {k: v.as_dict() for k, v in self.languages.items()},
            "dialects": {k: v.as_dict() for k, v in self.dialects.items()},
            "capabilities": {k: self.capability(k).as_dict() for k in CAPABILITIES},
            "requirements": list(self.requirements), "limitations": list(self.limitations),
            "planned": self.planned,
        }


def check_cell(engine: EngineDescriptor, language: str, dialect: str | None = None) -> dict[str, Any]:
    """Can ``engine`` run a cell in ``language`` (and SQL ``dialect``)? Static: nothing starts.

    The answer is the weaker of the language's and the dialect's support, with a reason a person can
    act on. ``ok`` is false for unsupported; simulated and translated are ok but say so."""
    lang = engine.language(language)
    parts = [("language", language, lang)]
    if language == "sql" and dialect:
        parts.append(("dialect", dialect, engine.dialect(dialect)))
    worst = max(parts, key=lambda p: LEVELS.index(p[2].level))
    kind, name, support = worst
    if not engine.adapter:
        reason = f"{engine.label} can't run cells in this version." + (f" Planned: {engine.planned}" if engine.planned else "")
        return {"ok": False, "level": "unsupported", "reason": reason}
    if not support.available:
        reason = f"{engine.label} doesn't run {name} ({kind}). Pick another engine for this cell."
    elif support.level in ("simulated", "translated"):
        reason = f"{engine.label} runs {name} {support.level}: {support.note}" if support.note else \
            f"{engine.label} runs {name} {support.level}, not on the platform's own engine."
    else:
        reason = support.note
    return {"ok": support.available, "level": support.level, "reason": reason}
