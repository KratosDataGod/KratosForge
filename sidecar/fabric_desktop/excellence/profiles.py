"""Assessment profiles (FEA-103): how much each dimension counts, and the thresholds behind a rating.

A profile is plain, versioned data so it can be stored with a snapshot and reused across workspaces.
It can change dimension weights (they add up to 100), the weight of each severity, the coverage a
rating needs, a control's severity, and turn a control off (it is then *not applicable* with the
profile's reason, never passed).

The built-in profiles are KratosForge defaults. The Microsoft-aligned baseline uses the weights from
the product brief; the others shift weight toward what they emphasize. None of them is a Microsoft
standard or certification.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from .controls import DIMENSIONS, SEVERITIES
from .evidence import canonical, text

SEVERITY_WEIGHTS = {"critical": 10, "high": 5, "medium": 3, "low": 1, "informational": 0}
RATED_COVERAGE = 0.8       # a rating is shown as authoritative at this coverage or more
PROVISIONAL_COVERAGE = 0.5  # below this no rating is shown at all
HIGH_CONFIDENCE = 0.8       # share of evaluated weight on measured or configuration evidence
MEDIUM_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Profile:
    id: str
    version: str
    title: str
    weights: dict[str, float]
    severity_weights: dict[str, float] = field(default_factory=lambda: dict(SEVERITY_WEIGHTS))
    rated_coverage: float = RATED_COVERAGE
    provisional_coverage: float = PROVISIONAL_COVERAGE
    severity_overrides: dict[str, str] = field(default_factory=dict)
    disabled: dict[str, str] = field(default_factory=dict)  # control id -> reason
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "title": self.title, "weights": dict(self.weights),
                "severity_weights": dict(self.severity_weights), "rated_coverage": self.rated_coverage,
                "provisional_coverage": self.provisional_coverage,
                "severity_overrides": dict(sorted(self.severity_overrides.items())),
                "disabled": dict(sorted(self.disabled.items())), "description": self.description}

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical(self.as_dict()).encode()).hexdigest()[:16]


def _number(x: Any) -> bool:
    """A finite number (not a flag): NaN or infinity would make every score NaN."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def problems(p: Profile) -> list[str]:
    out: list[str] = []
    if set(p.weights) != set(DIMENSIONS):
        out.append("weights name every dimension, and only those")
    if any(not _number(w) or w < 0 for w in p.weights.values()):
        out.append("weights are numbers of 0 or more")
    elif abs(sum(p.weights.values()) - 100) > 1e-9:
        out.append(f"weights add up to {sum(p.weights.values()):g}, not 100")
    if set(p.severity_weights) != set(SEVERITIES) or any(not _number(w) or w < 0 for w in p.severity_weights.values()):
        out.append("severity weights name every severity, each 0 or more")
    if not _number(p.provisional_coverage) or not _number(p.rated_coverage) \
            or not 0 < p.provisional_coverage <= p.rated_coverage <= 1:
        out.append("coverage thresholds are 0 < provisional <= rated <= 1")
    if any(s not in SEVERITIES for s in p.severity_overrides.values()):
        out.append("a severity override names an unknown severity")
    if any(not str(r).strip() for r in p.disabled.values()):
        out.append("a disabled control needs a reason")
    return out


def checked(p: Profile) -> Profile:
    bad = problems(p)
    if bad:
        raise ValueError(f"profile {p.id}: " + "; ".join(bad))
    return p


def from_dict(d: dict[str, Any]) -> Profile:
    """A stored profile (as_dict) back, checked."""
    return checked(Profile(id=d["id"], version=d["version"], title=d["title"], weights=dict(d["weights"]),
                           severity_weights=dict(d["severity_weights"]), rated_coverage=d["rated_coverage"],
                           provisional_coverage=d["provisional_coverage"],
                           severity_overrides=dict(d.get("severity_overrides") or {}),
                           disabled=dict(d.get("disabled") or {}), description=d.get("description", "")))


def _w(architecture: float, performance: float, security: float, governance: float, data_engineering: float,
       semantic_models: float, capacity_finops: float, reliability: float, devops: float,
       local_development: float) -> dict[str, float]:
    return {"architecture": architecture, "performance": performance, "security": security,
            "governance": governance, "data_engineering": data_engineering, "semantic_models": semantic_models,
            "capacity_finops": capacity_finops, "reliability": reliability, "devops": devops,
            "local_development": local_development}


BUILT_IN = {p.id: checked(p) for p in (
    Profile("baseline", "1.1.0", "Baseline", _w(12, 15, 18, 10, 10, 10, 10, 7, 5, 3),
            description="Applicable Microsoft Fabric and Power BI guidance, weighted as the product brief sets out. "
                        "A KratosForge profile, not a Microsoft standard."),
    Profile("enterprise_security", "1.0.0", "Enterprise security", _w(8, 5, 35, 20, 5, 5, 5, 7, 8, 2),
            description="Least privilege, sharing controls, governance and auditability."),
    Profile("data_engineering", "1.0.0", "Data engineering excellence", _w(10, 18, 10, 8, 24, 8, 5, 10, 5, 2),
            description="Reliability, performance, maintainability, testing and reuse."),
    Profile("capacity", "1.0.0", "Capacity optimization", _w(8, 25, 10, 5, 10, 10, 22, 5, 2, 3),
            description="CU consumption, scheduling, redundancy, utilization and cost efficiency."),
    Profile("local_first", "1.0.0", "KratosForge local-first", _w(10, 15, 15, 8, 12, 8, 10, 5, 5, 12),
            description="Safe local development, testing and execution, alongside the baseline."),
)}
DEFAULT = "baseline"


def get(profile_id: str = DEFAULT) -> Profile:
    if not isinstance(profile_id, str) or profile_id not in BUILT_IN:
        raise ValueError(f"unknown profile {text(profile_id, 64)!r}")
    return BUILT_IN[profile_id]
