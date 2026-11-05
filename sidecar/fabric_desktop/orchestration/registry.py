"""What KratosForge can do with each Fabric pipeline activity type (ORCH-002).

One ``ActivitySupport`` entry per activity type says, honestly:

    classification   what works today: local, simulated, static, cloud or unsupported (CLASSIFICATIONS)
    planned          what a later stage aims for, and which stage (never counted as support)
    evidence         what backs the classification: unit_tests (automated tests in this repository,
                     no Fabric), fabric_tested (compared against a run in Fabric), or none
    properties       per typeProperties key: checked here, carried unchanged, or not supported by
                     the planned local adapter, each with why
    auth, side_effects, external_dependencies, fabric_capacity
                     what running it needs and changes, and whether it needs paid Fabric capacity

Rules the registry enforces, so a wrong claim can't be registered:

- ``local`` or ``simulated`` needs an adapter that can execute and evidence of tests (ORCH-003's
  control-flow adapters are the first).
- ``cloud`` activities always need Fabric capacity to run.
- Every type the registry doesn't know is ``unsupported`` and kept in the report, never dropped.

``compatibility(imported)`` reports on a whole pipeline from its ORCH-001 import, without calling
Fabric or running anything. The registry is versioned: change ``REGISTRY_VERSION`` with every
change to an entry and note it in docs/orchestration/activity-registry.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .definition import Activity, ImportedPipeline
from .validate import Diagnostic, summary, validate

REGISTRY_VERSION = "9"

CLASSIFICATIONS = {
    "local": "Runs on this PC with a tested adapter.",
    "simulated": "Runs on this PC against fixtures or a stand-in, with no real side effects.",
    "static": "Checked on this PC (structure, references, required properties) but not run here.",
    "cloud": "Only runs in Fabric (or the Azure service it calls); not run on this PC.",
    "unsupported": "Not known to this version. Kept as it is, but neither checked nor run.",
}
EVIDENCE = ("none", "unit_tests", "fabric_tested")
# Fabric meters every activity run, control flow included. "fabric_only": can only run in Fabric (or the
# service it calls). "metered_in_fabric": metered when it runs in Fabric; this PC may run it instead once an
# adapter can execute it.
CAPACITY = ("fabric_only", "metered_in_fabric")
PROPERTY_STATUS = ("checked", "carried", "unsupported_locally")
SIDE_EFFECTS = ("none", "pipeline_state", "reads_onelake", "writes_onelake", "reads_external", "writes_external",
                "calls_http", "runs_code", "sends_message", "starts_fabric_job", "deletes_data")
# The order a pipeline's overall classification takes: the least capable activity decides.
_RANK = {"local": 0, "simulated": 1, "static": 2, "cloud": 3, "unsupported": 4}


@dataclass(frozen=True)
class PropertySupport:
    status: str   # PROPERTY_STATUS
    note: str = ""


@dataclass(frozen=True)
class Planned:
    classification: str
    stage: str      # e.g. ORCH-003
    note: str = ""


class ActivityAdapter(Protocol):
    """What an adapter offers. ``validate`` checks one activity statically; ``execute`` runs it
    (ORCH-003). Adapters never see the scheduler: they get the activity and a context and return."""

    can_execute: bool

    def validate(self, activity: Activity, ctx: "CheckContext") -> list[Diagnostic]: ...

    def execute(self, activity: Activity, run: Any) -> Any: ...


@dataclass
class CheckContext:
    """What a static check may look at: the pipeline's declarations. No network, no Fabric."""
    parameters: dict[str, Any]
    variables: dict[str, Any]


class StaticAdapter:
    """Checks required typeProperties. Can't run anything."""

    can_execute = False

    def __init__(self, required: tuple[str, ...] = (), check: Callable[[Activity, CheckContext], list[Diagnostic]] | None = None):
        self.required, self.check = required, check

    def validate(self, activity: Activity, ctx: CheckContext) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        tp = activity.type_properties
        for key in self.required:
            if key not in tp or tp[key] in (None, ""):
                out.append(Diagnostic("missing_property", "error",
                                      f"{activity.name} ({activity.type}) needs typeProperties.{key}.",
                                      f"{activity.path}.typeProperties.{key}", activity.name,
                                      hint=f"Add {key}; Fabric needs it for this activity type."))
        if self.check is not None:
            out += self.check(activity, ctx)
        return out

    def execute(self, activity: Activity, run: Any) -> Any:
        raise NotImplementedError(f"{activity.type} can't run on this PC yet")


@dataclass(frozen=True)
class ActivitySupport:
    type: str
    title: str
    classification: str
    evidence: str
    fabric_capacity: str
    planned: Planned | None = None
    aliases: tuple[str, ...] = ()
    type_name_verified: bool = True   # the type string is in Microsoft's documentation or a known export
    auth: tuple[str, ...] = ()        # what running it needs: fabric, connection, workspace_role ...
    side_effects: tuple[str, ...] = ("none",)
    external_dependencies: tuple[str, ...] = ()
    properties: dict[str, PropertySupport] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    container: bool = False
    adapter: Any = None

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "title": self.title, "aliases": list(self.aliases),
                "type_name_verified": self.type_name_verified,
                "classification": self.classification, "classification_text": CLASSIFICATIONS[self.classification],
                "implemented": True, "evidence": self.evidence,
                "planned": None if self.planned is None else
                {"classification": self.planned.classification, "stage": self.planned.stage,
                 "note": self.planned.note, "implemented": False},
                "fabric_capacity": self.fabric_capacity, "auth": list(self.auth),
                "side_effects": list(self.side_effects), "external_dependencies": list(self.external_dependencies),
                "properties": {k: {"status": v.status, "note": v.note} for k, v in self.properties.items()},
                "limitations": list(self.limitations), "container": self.container,
                "can_execute": bool(getattr(self.adapter, "can_execute", False))}


UNKNOWN = ActivitySupport(
    "(unknown)", "Activity type this version doesn't know", "unsupported", "none", "metered_in_fabric",
    limitations=("Kept exactly as it is in the definition, but not checked or run. A newer KratosForge "
                 "may know it; otherwise it has to run in Fabric.",))


class ActivityRegistry:
    def __init__(self, version: str = REGISTRY_VERSION):
        self.version = version
        self._by_type: dict[str, ActivitySupport] = {}
        self._entries: list[ActivitySupport] = []

    def register(self, entry: ActivitySupport) -> ActivitySupport:
        """Add an entry after checking it can't overclaim. Raises ValueError otherwise."""
        problems = check_entry(entry)
        if problems:
            raise ValueError(f"{entry.type}: " + "; ".join(problems))
        for name in (entry.type, *entry.aliases):
            if name in self._by_type:
                raise ValueError(f"{name} is already registered")
        for name in (entry.type, *entry.aliases):
            self._by_type[name] = entry
        self._entries.append(entry)
        return entry

    def get(self, activity_type: str) -> ActivitySupport:
        return self._by_type.get(activity_type, UNKNOWN)

    def known(self, activity_type: str) -> bool:
        return activity_type in self._by_type

    def entries(self) -> list[ActivitySupport]:
        return list(self._entries)

    def describe(self) -> dict[str, Any]:
        from .gotchas import GOTCHAS   # gotchas uses adapters, which use this module
        return {"registry_version": self.version, "classifications": CLASSIFICATIONS,
                "evidence": {"none": "Nothing backs it yet.",
                             "unit_tests": "Automated tests in this repository, with no Fabric.",
                             "fabric_tested": "Compared against the same activity run in Fabric."},
                "activities": [e.to_json() for e in self._entries], "unknown": UNKNOWN.to_json(),
                "fabric_differences": [g.to_json() for g in GOTCHAS]}

    # ---- pipeline reports

    def compatibility(self, imp: ImportedPipeline) -> dict[str, Any]:
        """Every activity in the pipeline (nested ones included) with its classification, the
        properties this version can't use, what it needs and changes, and a pipeline summary.
        Reads the definition only: no Fabric call, nothing runs. Known differences from Fabric
        (gotchas.py) are warnings: they never stop a local run."""
        from . import gotchas
        p = imp.pipeline
        ctx = CheckContext(p.parameters, p.variables)
        rows: list[dict[str, Any]] = []
        diags: list[Diagnostic] = list(validate(imp)) + gotchas.for_item(imp)
        for a in p.all_activities():
            entry = self.get(a.type)
            found = entry.adapter.validate(a, ctx) if entry.adapter is not None else []
            found += gotchas.for_activity(a, imp)
            if entry is UNKNOWN:
                found.append(Diagnostic("unknown_activity_type", "warning",
                                        f"{a.name}: this version doesn't know the activity type {a.type!r}, so it is "
                                        "kept as it is but not checked or run.", f"{a.path}.type", a.name,
                                        hint="It still runs in Fabric if Fabric knows the type."))
            diags += found
            rows.append(_row(a, entry, found))
        counts = {c: 0 for c in CLASSIFICATIONS}
        for r in rows:
            counts[r["classification"]] += 1
        worst = max((r["classification"] for r in rows), key=_RANK.__getitem__, default="static")
        here = ("local", "simulated")
        capacity = [r["name"] for r in rows if r["classification"] not in here]
        fabric_only = [r["name"] for r in rows if r["fabric_capacity"] == "fabric_only"]
        planned_local = [{"name": r["name"], "classification": r["planned"]["classification"],
                          "stage": r["planned"]["stage"]} for r in rows
                         if r["planned"] and r["planned"]["classification"] in ("local", "simulated")]
        return {
            "registry_version": self.version, "pipeline": p.name, "schema": imp.schema(),
            "summary": {
                "activities": len(rows), "by_classification": counts,
                "overall": worst,
                "runs_on_this_pc": bool(rows) and all(r["classification"] in ("local", "simulated") for r in rows),
                "needs_fabric_capacity": capacity,
                "fabric_only": fabric_only,
                "unknown_types": sorted({r["type"] for r in rows if not r["known"]}),
                "planned_to_run_on_this_pc": planned_local,
                "verified_against_fabric": [r["name"] for r in rows if r["evidence"] == "fabric_tested"],
            },
            "activities": rows,
            "diagnostics": [d.to_json() for d in diags], "counts": summary(diags),
            "notes": ["Classifications describe what this version does today; 'planned' is a proposal, not support.",
                      "Nothing here was run, and no Fabric call was made to produce this report.",
                      "Fabric meters every activity run, control flow included. 'needs_fabric_capacity' lists the "
                      "activities this version can't run on this PC, so running the pipeline today uses capacity "
                      "for each of them; 'fabric_only' lists those that can only ever run in Fabric.",
                      "Some warnings are known places where a pipeline that runs here fails in Fabric "
                      "(fabric_differences in the registry). They never stop a local run."],
        }


def _row(a: Activity, entry: ActivitySupport, found: list[Diagnostic]) -> dict[str, Any]:
    props, checked = [], []
    for key in a.type_properties:
        support = entry.properties.get(key)
        if support is None:
            props.append({"property": key, "status": "unknown",
                          "note": "Not in the registry for this type: carried unchanged, not checked."})
        elif support.status == "checked":
            checked.append(key)
        elif support.status != "carried":
            props.append({"property": key, "status": support.status, "note": support.note})
    return {"name": a.name, "type": a.type, "path": a.path, "container": a.container, "known": entry is not UNKNOWN,
            "title": entry.title, "classification": entry.classification, "evidence": entry.evidence,
            "verified": entry.evidence == "fabric_tested",
            "planned": None if entry.planned is None else
            {"classification": entry.planned.classification, "stage": entry.planned.stage, "note": entry.planned.note},
            "fabric_capacity": entry.fabric_capacity, "auth": list(entry.auth),
            "side_effects": list(entry.side_effects), "external_dependencies": list(entry.external_dependencies),
            "inactive": a.inactive, "properties": props, "checked_properties": checked, "limitations": list(entry.limitations),
            "problems": [d.code for d in found]}


def check_entry(e: ActivitySupport) -> list[str]:
    """Why an entry would overclaim or be malformed; empty if it is fine."""
    problems = []
    if e.classification not in CLASSIFICATIONS:
        problems.append(f"classification must be one of {', '.join(CLASSIFICATIONS)}")
    if e.evidence not in EVIDENCE:
        problems.append(f"evidence must be one of {', '.join(EVIDENCE)}")
    if e.fabric_capacity not in CAPACITY:
        problems.append(f"fabric_capacity must be one of {', '.join(CAPACITY)}")
    if e.classification in ("local", "simulated"):
        if not getattr(e.adapter, "can_execute", False):
            problems.append(f"{e.classification} needs an adapter that can execute")
        if e.evidence == "none":
            problems.append(f"{e.classification} needs tests as evidence")
    if e.classification == "static" and e.adapter is None:
        problems.append("static needs an adapter that checks it")
    if e.classification == "cloud" and e.fabric_capacity != "fabric_only":
        problems.append("a cloud activity needs Fabric capacity to run")
    if e.classification in ("local", "simulated") and e.fabric_capacity == "fabric_only":
        problems.append(f"a {e.classification} activity can't be fabric_only")
    if e.planned is not None:
        if e.planned.classification not in CLASSIFICATIONS:
            problems.append("planned classification is unknown")
        if _RANK[e.planned.classification] >= _RANK.get(e.classification, 9):
            problems.append("planned must be more capable than today's classification")
        if not e.planned.stage:
            problems.append("planned needs the stage that will build it")
    for effect in e.side_effects:
        if effect not in SIDE_EFFECTS:
            problems.append(f"unknown side effect {effect!r}")
    for key, prop in e.properties.items():
        if prop.status not in PROPERTY_STATUS:
            problems.append(f"property {key}: status must be one of {', '.join(PROPERTY_STATUS)}")
    return problems
