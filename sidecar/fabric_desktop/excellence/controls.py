"""Versioned controls, their outcomes, and the registry (FEA-102).

A control checks one thing about one kind of resource and says one of:

- ``pass``: the evidence it needs is there and meets the rule.
- ``fail``: the evidence is there and doesn't. The engine builds a finding from it.
- ``not_applicable``: the rule doesn't apply here (no semantic models in the workspace). Not scored.
- ``not_evaluated``: the evidence is missing, unreadable or not strong enough, or no analyzer covers
  this kind of item yet. Always with a reason, and when known, what access or evidence would let it run.
- ``error``: the control raised, or broke a rule below. Scored as not evaluated, reported as a defect.

:func:`evaluate` enforces what a control's own code can't be trusted to: evidence it declares but
doesn't have makes it not evaluated before it runs; a pass or fail must cite evidence ids it was
given, or it becomes an error; a result resting only on inferred evidence is advisory (not evaluated,
with the would-be result attached) unless the control sets ``accepts_inferred``. There is no default
pass: a control can't pass what it never looked at.

``scope`` is ``workspace``, ``capacity`` or ``item``. Item controls name the Fabric item types they
cover; the engine runs them once per item of those types in the workspace inventory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import evidence as ev

DIMENSIONS = {
    "architecture": "Architecture and design",
    "performance": "Performance and workload optimization",
    "security": "Security and access control",
    "governance": "Governance and compliance",
    "data_engineering": "Data engineering excellence",
    "semantic_models": "Semantic models and Power BI",
    "capacity_finops": "Capacity and FinOps",
    "reliability": "Reliability and operations",
    "devops": "DevOps and lifecycle",
    "local_development": "Cost-efficient local development",
}
SEVERITIES = ("critical", "high", "medium", "low", "informational")
REQUIREMENTS = {
    "microsoft_requirement": "Microsoft requirement",
    "microsoft_recommendation": "Microsoft recommendation",
    "kratosforge_recommendation": "KratosForge recommendation",
    "customer_policy": "Your organization's policy",
}
OUTCOMES = ("pass", "fail", "not_applicable", "not_evaluated", "error")
SCOPES = ("workspace", "capacity", "item")
LEVELS = ("low", "medium", "high")
_ID = re.compile(r"^[A-Z]{2,5}-[A-Z0-9]{2,6}-\d{3}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
TEMPLATE_FIELDS = ("why", "solution", "steps", "benefit", "effort", "risk", "validation", "rollback")


@dataclass
class Context:
    """What a control sees: its subject and the evidence of the kinds it declared, for that subject
    and (for an item) its workspace."""

    subject: dict[str, Any]
    _evidence: dict[str, list[dict[str, Any]]]

    def get(self, kind: str) -> list[dict[str, Any]]:
        """Records of ``kind`` (one the control declared); others raise, so a control can't read
        evidence the engine didn't check for it."""
        if kind not in self._evidence:
            raise KeyError(f"{kind!r} isn't declared by this control")
        return list(self._evidence[kind])

    def one(self, kind: str) -> dict[str, Any] | None:
        got = self.get(kind)
        return got[0] if got else None


# ---- results a control returns

def passed(evidence_ids: list[str], text: str) -> dict[str, Any]:
    return {"outcome": "pass", "evidence_ids": list(evidence_ids), "text": text}


def failed(evidence_ids: list[str], observation: str, *, benefit: str | None = None,
           detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """``observation``: what was discovered, specifically (names, counts). ``benefit`` overrides the
    template's when this case can say more; it is still qualified, never a promised saving."""
    return {"outcome": "fail", "evidence_ids": list(evidence_ids), "text": observation, "benefit": benefit,
            "detail": detail or {}}


def not_applicable(reason: str) -> dict[str, Any]:
    return {"outcome": "not_applicable", "evidence_ids": [], "text": reason}


def not_evaluated(reason: str, needs: str | None = None) -> dict[str, Any]:
    return {"outcome": "not_evaluated", "evidence_ids": [], "text": reason, "needs": needs}


@dataclass(frozen=True)
class Control:
    id: str
    version: str
    dimension: str
    title: str
    severity: str
    requirement: str
    scope: str
    evidence_kinds: tuple[str, ...]
    evaluate: Callable[[Context], dict[str, Any]]
    remediation: dict[str, Any]
    references: tuple[dict[str, str], ...] = ()
    item_types: tuple[str, ...] = ()
    applies: Callable[[Context], bool | None] | None = None
    accepts_inferred: bool = False
    inferred_reason: str = ""
    academy: str | None = None
    description: str = ""
    optional_kinds: tuple[str, ...] = field(default=())

    @property
    def rule(self) -> str:
        return f"{self.id}@{self.version}"


def problems(c: Control) -> list[str]:
    """Everything wrong with a control's definition; empty when it can be registered."""
    out: list[str] = []
    if not _ID.fullmatch(c.id):
        out.append(f"id {c.id!r} isn't like FEA-SEC-001")
    if not _VERSION.fullmatch(c.version):
        out.append(f"version {c.version!r} isn't like 1.0.0")
    if c.dimension not in DIMENSIONS:
        out.append(f"unknown dimension {c.dimension!r}")
    if c.severity not in SEVERITIES:
        out.append(f"unknown severity {c.severity!r}")
    if c.requirement not in REQUIREMENTS:
        out.append(f"unknown requirement {c.requirement!r}")
    if c.scope not in SCOPES:
        out.append(f"unknown scope {c.scope!r}")
    if c.scope == "item" and not c.item_types:
        out.append("an item control names the item types it covers")
    if c.scope != "item" and c.item_types:
        out.append("only item controls name item types")
    if not c.title.strip():
        out.append("no title")
    if not c.evidence_kinds:
        out.append("a control declares the evidence it needs")
    if set(c.optional_kinds) & set(c.evidence_kinds):
        out.append("a kind is either needed or optional, not both")
    if c.accepts_inferred and not c.inferred_reason.strip():
        out.append("accepts_inferred needs inferred_reason")
    missing = [k for k in TEMPLATE_FIELDS if not c.remediation.get(k)]
    if missing:
        out.append("remediation is missing " + ", ".join(missing))
    if not isinstance(c.remediation.get("steps"), (list, tuple)):
        out.append("remediation steps are a list")
    for k in ("effort", "risk"):
        if c.remediation.get(k) and c.remediation[k] not in LEVELS:
            out.append(f"remediation {k} is low, medium or high")
    if c.requirement != "customer_policy" and not c.references:
        out.append("a Microsoft or KratosForge control cites at least one reference")
    for r in c.references:
        if not str(r.get("url", "")).startswith("https://") or not r.get("title"):
            out.append("a reference has a title and an https url")
    return out


class Registry:
    """Controls by id. Registering the same id twice is refused: a rule changes by a new version,
    never by a second control with the same name."""

    def __init__(self) -> None:
        self._controls: dict[str, Control] = {}
        self.payloads: dict[str, Any] = {}  # evidence kind -> its payload's shape (evidence.fits)

    def register(self, c: Control) -> Control:
        bad = problems(c)
        if bad:
            raise ValueError(f"{c.id}: " + "; ".join(bad))
        if c.id in self._controls:
            raise ValueError(f"{c.id} is already registered")
        self._controls[c.id] = c
        return c

    def get(self, control_id: str) -> Control:
        return self._controls[control_id]

    def controls(self, dimension: str | None = None) -> list[Control]:
        return [c for _, c in sorted(self._controls.items()) if dimension is None or c.dimension == dimension]

    def versions(self) -> dict[str, str]:
        return {i: c.version for i, c in sorted(self._controls.items())}

    def item_types(self) -> set[str]:
        return {t for c in self._controls.values() for t in c.item_types}

    def __len__(self) -> int:
        return len(self._controls)


# ---- running one control on one subject

def _outcome(c: Control, subj: dict[str, Any], result: dict[str, Any], basis: str) -> dict[str, Any]:
    return {"control_id": c.id, "rule": c.rule, "dimension": c.dimension, "severity": c.severity,
            "subject": subj, "outcome": result["outcome"], "text": result.get("text", ""),
            "evidence_ids": sorted(result.get("evidence_ids") or []), "basis": basis,
            "needs": result.get("needs"), "benefit": result.get("benefit"), "detail": result.get("detail") or {},
            "advisory": result.get("advisory")}


def _error(c: Control, subj: dict[str, Any], text: str) -> dict[str, Any]:
    return _outcome(c, subj, {"outcome": "error", "text": text}, "insufficient")


def evaluate(c: Control, subj: dict[str, Any], gather: Callable[[str], list[dict[str, Any]]]) -> dict[str, Any]:
    """One outcome of ``c`` on ``subj``. ``gather(kind)`` returns the subject's evidence of a kind."""
    found = {k: gather(k) for k in (*c.evidence_kinds, *c.optional_kinds)}
    missing = [k for k in c.evidence_kinds if not found[k]]
    if missing:
        return _outcome(c, subj, not_evaluated("No evidence of " + ", ".join(missing) + " was collected.",
                                               needs=", ".join(missing)), "insufficient")
    usable = [r for k in c.evidence_kinds for r in found[k] if r["confidence"] != "insufficient"]
    if len({r["kind"] for r in usable}) < len(c.evidence_kinds):
        thin = [k for k in c.evidence_kinds if all(r["confidence"] == "insufficient" for r in found[k])]
        return _outcome(c, subj, not_evaluated("The evidence of " + ", ".join(thin) + " isn't enough to decide.",
                                               needs=", ".join(thin)), "insufficient")
    ctx = Context(subj, found)
    try:
        if c.applies is not None:
            applies = c.applies(ctx)
            if applies is None:
                return _outcome(c, subj, not_evaluated("Whether this control applies couldn't be told from the evidence."),
                                "insufficient")
            if not applies:
                return _outcome(c, subj, not_applicable("Doesn't apply to this resource."), "insufficient")
        result = c.evaluate(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken control is a defect to report, never a pass or a crash
        return _error(c, subj, f"The control raised {type(exc).__name__}.")
    if not isinstance(result, dict) or result.get("outcome") not in OUTCOMES or result["outcome"] == "error":
        return _error(c, subj, "The control returned something that isn't an outcome.")
    if result["outcome"] == "not_evaluated" and not str(result.get("text", "")).strip():
        return _error(c, subj, "Not evaluated without a reason.")
    if result["outcome"] not in ("pass", "fail"):
        return _outcome(c, subj, result, "insufficient")
    given = {r["id"]: r for recs in found.values() for r in recs}
    cited = list(result.get("evidence_ids") or [])
    if not cited:
        return _error(c, subj, f"A {result['outcome']} that cites no evidence.")
    if any(i not in given for i in cited):
        return _error(c, subj, f"A {result['outcome']} that cites evidence it wasn't given.")
    basis = ev.strongest([given[i] for i in cited])
    if basis == "insufficient":
        return _error(c, subj, f"A {result['outcome']} resting only on insufficient evidence.")
    if basis == "inferred" and not c.accepts_inferred:
        advisory = {"outcome": result["outcome"], "text": result.get("text", ""), "evidence_ids": sorted(cited)}
        return _outcome(c, subj, {**not_evaluated("Only inferred evidence was found; shown as advice, not scored.",
                                                  needs="configuration or measured evidence"),
                                  "advisory": advisory}, "inferred")
    return _outcome(c, subj, result, basis)
