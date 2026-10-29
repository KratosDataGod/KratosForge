"""What KratosForge supports on each data platform, and how we know (KUDP-02).

Each platform's manifest is put together from what the code already registers, never claimed a
second time: its engines and their capabilities (engines/registry), its sign-in methods
(connprofiles), its pipeline activities (orchestration), its SQL targets (sqlcompat) and its notebook
formats. A short list of ``Feature`` entries adds what lives outside those registries (sign-in, the
lakehouse catalog, deployment, Optimize), each naming the module that does it and the tests behind it.

A platform's support **level** follows from the evidence for seven areas:

    connect   sign in                       catalog   list what the platform holds
    execute   run work                      test      compare or validate results
    deploy    publish or deploy             optimize  measure usage and cost
    enterprise  security, reliability and lifecycle checked for an enterprise rollout

    Level 0 Discovered   known, nothing integrated
    Level 1 Connected    connect and catalog
    Level 2 Development  + execute
    Level 3 Integrated   + test and deploy
    Level 4 Optimized    + optimize
    Level 5 Enterprise   + enterprise

Each level is worked out three times, from three strengths of evidence:

    built     the code does it, with tests in this repository (unit_tests or better)
    in_use    it has been used against the live platform in normal work (live_used or better)
    verified  a recorded test against the live platform passed (live_verified)

For the ``local`` platform the tests run the real engines, so ``unit_tests`` already counts as
in use and verified. A platform can't reach a level its evidence doesn't support, and planned work never counts.
docs/platforms.md shows ``docs_table()``; a test keeps the two equal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import connprofiles
from .engines import registry as engine_registry
from .engines.capabilities import EngineDescriptor
from .orchestration.activities import default_registry as activity_registry
from .sqlcompat import profiles as sql_profiles  # the package exports the profiles() function

MANIFEST_VERSION = "1"
AREAS = {
    "connect": "Sign in",
    "catalog": "List what the platform holds",
    "execute": "Run work",
    "test": "Compare or validate results",
    "deploy": "Publish or deploy",
    "optimize": "Measure usage and cost",
    "enterprise": "Checked for an enterprise rollout",
}
LEVELS = (
    (0, "Discovered", ()),
    (1, "Connected", ("connect", "catalog")),
    (2, "Development", ("connect", "catalog", "execute")),
    (3, "Integrated", ("connect", "catalog", "execute", "test", "deploy")),
    (4, "Optimized", ("connect", "catalog", "execute", "test", "deploy", "optimize")),
    (5, "Enterprise ready", tuple(AREAS)),
)
# Weakest to strongest. Engine evidence maps on: an engine's live_verified is a recorded live test.
EVIDENCE = ("none", "unit_tests", "live_used", "live_verified")
STRENGTHS = {"built": "unit_tests", "in_use": "live_used", "verified": "live_verified"}


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    # Areas this platform has nothing to do for (this PC needs no sign-in); they count as met.
    not_applicable: tuple[str, ...] = ()
    # The weakest evidence that counts as verified: a recorded live test, or for this PC the tests.
    verified_by: str = "live_verified"
    notebook_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feature:
    """Something a platform does that no registry already records."""

    platform: str
    area: str
    label: str
    evidence: str
    where: tuple[str, ...]          # modules under fabric_desktop/ that do it
    tests: tuple[str, ...] = ()     # test files under sidecar/tests/ that back it
    note: str = ""                  # how it was used live, or what it lacks

    def __post_init__(self) -> None:
        if self.area not in AREAS:
            raise ValueError(f"{self.label}: unknown area {self.area!r}. Areas: {', '.join(AREAS)}.")
        if self.evidence not in EVIDENCE or self.evidence == "none":
            raise ValueError(f"{self.label}: a feature needs evidence ({', '.join(EVIDENCE[1:])}); "
                             "put planned work in the platform's notes instead.")
        if not self.tests:
            raise ValueError(f"{self.label}: name the tests that back it.")
        if self.evidence in ("live_used", "live_verified") and not self.note:
            raise ValueError(f"{self.label}: say how it was used against the live platform.")


PLATFORMS: dict[str, Platform] = {p.id: p for p in (
    Platform("fabric", "Microsoft Fabric", notebook_formats=("fabric_py", "ipynb")),
    Platform("databricks", "Azure Databricks", notebook_formats=("databricks_py", "dbc")),
    Platform("snowflake", "Snowflake"),
    Platform("local", "This PC", not_applicable=("connect", "deploy"), verified_by="unit_tests",
             notebook_formats=("ipynb", "percent_py")),
)}

FEATURES: tuple[Feature, ...] = (
    Feature("fabric", "connect", "The app's own Microsoft sign-in", "live_used", ("auth.py",),
            ("test_offline_sign_in.py", "test_sign_in_errors.py"),
            "Every session signs in this way; no recorded live test yet (FD-101 hasn't run)."),
    Feature("fabric", "catalog", "Workspaces, lakehouses and tables", "live_used", ("fabric_api.py", "onelake.py"),
            ("test_fabric_api.py",), "Browsed in daily use; no recorded live test yet."),
    Feature("fabric", "execute", "Notebooks on this PC reading and writing OneLake", "live_used",
            ("lakehouse.py", "publish.py"), ("test_onelake_writes.py",),
            "TPC-H loads to OneLake from this PC (2026-10-26); no recorded live test yet."),
    Feature("fabric", "execute", "Run in Fabric as a job", "unit_tests", ("fabric_run.py",), ("test_cloud.py",)),
    Feature("fabric", "execute", "SQL page queries on this PC and on a warehouse", "unit_tests",
            ("sql_exec.py", "warehouse.py"), ("test_sql_exec.py", "test_warehouse_queries.py")),
    Feature("fabric", "test", "Side by side and table verification", "unit_tests", ("side_by_side.py", "verify.py"),
            ("test_side_by_side.py", "test_prove.py")),
    Feature("fabric", "deploy", "Publish notebooks, platform files from Git, warehouse deploy plans", "unit_tests",
            ("git/platform_files.py", "warehouse_compare.py"), ("test_platform_repo.py", "test_warehouse_project.py",
                                                                "test_notebook_publish.py")),
    Feature("fabric", "optimize", "Optimize: capacity use and local runs", "unit_tests",
            ("optimize/local_runs.py", "capacity.py"), ("test_optimize_local_runs.py", "test_capacity.py")),
    Feature("databricks", "connect", "Databricks CLI profiles, browser sign-in and service principals", "unit_tests",
            ("engines/databricks_auth.py", "connprofiles.py"), ("test_databricks_sql.py", "test_connprofiles.py"),
            "Tested against fakes of the Databricks CLI and APIs."),
    Feature("local", "test", "Table verification and the engine contract checks", "unit_tests", ("verify.py",),
            ("test_prove.py", "test_engine_conformance.py")),
    Feature("local", "optimize", "Measured time, CPU and memory of a run", "unit_tests", ("measure.py",),
            ("test_measurement.py",)),
)

# Engine capabilities that stand for an area.
_ENGINE_AREAS = {"execute": "execute", "catalog": "catalog", "cost": "optimize"}
_SQL_TARGETS = {"fabric": ("spark", "warehouse", "endpoint", "sqldb"), "local": ("duckdb",)}
_RANK = {e: i for i, e in enumerate(EVIDENCE)}


def _engine_evidence(desc: EngineDescriptor, capability: str) -> str:
    support = desc.capability(capability)
    if not support.available:
        return "none"
    return support.evidence


def _areas(pid: str) -> dict[str, dict[str, Any]]:
    """For each area, the strongest evidence and where it comes from."""
    plat = PLATFORMS[pid]
    found: dict[str, dict[str, Any]] = {a: {"evidence": "none", "sources": []} for a in AREAS}
    for area in plat.not_applicable:
        found[area] = {"evidence": "not_applicable", "sources": []}

    def add(area: str, evidence: str, source: str) -> None:
        if found[area]["evidence"] == "not_applicable" or evidence == "none":
            return
        found[area]["sources"].append({"source": source, "evidence": evidence})
        if _RANK[evidence] > _RANK[found[area]["evidence"]]:
            found[area]["evidence"] = evidence

    for desc in engine_registry.ENGINES.values():
        if desc.platform != pid or not desc.adapter:
            continue
        for cap, area in _ENGINE_AREAS.items():
            add(area, _engine_evidence(desc, cap), f"engine {desc.id}")
    for f in FEATURES:
        if f.platform == pid:
            add(f.area, f.evidence, f.label)
    return found


def _level(areas: dict[str, dict[str, Any]], weakest: str) -> dict[str, Any]:
    def met(area: str) -> bool:
        ev = areas[area]["evidence"]
        return ev == "not_applicable" or _RANK[ev] >= _RANK[weakest]

    reached = LEVELS[0]
    for level in LEVELS[1:]:
        if not all(met(a) for a in level[2]):
            break
        reached = level
    nxt = LEVELS[reached[0] + 1] if reached[0] + 1 < len(LEVELS) else None
    missing = [a for a in nxt[2] if not met(a)] if nxt else []
    return {"level": reached[0], "name": reached[1], "next_needs": missing}


def _weakest(plat: Platform, wanted: str) -> str:
    """The evidence a strength asks for on this platform: never more than its ``verified_by``."""
    return wanted if _RANK[wanted] <= _RANK[plat.verified_by] else plat.verified_by


def manifest(pid: str) -> dict[str, Any]:
    """One platform's manifest. Reads registries only: nothing signs in, runs or touches a file."""
    if pid not in PLATFORMS:
        raise ValueError(f"Unknown platform {pid!r}. Platforms: {', '.join(PLATFORMS)}.")
    plat = PLATFORMS[pid]
    areas = _areas(pid)
    engines = [d for d in engine_registry.ENGINES.values() if d.platform == pid]
    out: dict[str, Any] = {
        "id": pid, "label": plat.label,
        "sign_in": list(connprofiles.METHODS.get(pid, ())),
        "engines": [{"id": d.id, "label": d.label, "location": d.location, "billing": d.billing,
                     "adapter": d.adapter, "preview": d.preview, "planned": d.planned} for d in engines],
        "sql_targets": [t for t in _SQL_TARGETS.get(pid, ()) if t in {p["id"] for p in sql_profiles()}],
        "notebook_formats": list(plat.notebook_formats),
        "areas": areas,
        "levels": {name: _level(areas, _weakest(plat, weakest)) for name, weakest in STRENGTHS.items()},
    }
    if pid == "fabric":
        acts = activity_registry().entries()
        out["pipeline_activities"] = {c: sum(1 for a in acts if a.classification == c)
                                      for c in ("local", "simulated", "static", "cloud")}
    return out


def manifests() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "engine_registry": engine_registry.REGISTRY_VERSION,
            "platforms": [manifest(p) for p in PLATFORMS]}


def docs_table() -> str:
    """The support levels as the Markdown table in docs/platforms.md."""
    # No engine registry version here: a descriptor change that moves no level shouldn't change the docs.
    lines = [f"Manifest {MANIFEST_VERSION}.", "",
             "| Platform | Built | In use | Verified | To verify the next level |", "| --- | --- | --- | --- | --- |"]
    for pid in PLATFORMS:
        m = manifest(pid)
        lv = m["levels"]
        cells = [f"{lv[k]['level']} {lv[k]['name']}" for k in STRENGTHS]
        needs = ", ".join(AREAS[a].lower() for a in lv["verified"]["next_needs"]) or "nothing"
        lines.append(f"| {m['label']} | " + " | ".join(cells) + f" | {needs} |")
    return "\n".join(lines)
