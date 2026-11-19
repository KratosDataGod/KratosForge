"""Provider contracts for the KratosForge Environment, and what each provider can do (CP-003).

Five small interfaces, one per job (plan section 7). Each maps onto code that already exists, so a
provider is a thin adapter, not a new storage system:

    EnvironmentDiscovery         list namespaces, find stores holding a manifest, read it (read-only)
    PlatformProvisioning         plan what would be created (no writes), apply a confirmed plan, write the manifest
    AnalyticalStorageProvider    optimize/setup.inspect and initialize, rows through optimize/shared and the outbox
    OperationalMetadataProvider  central/client's ``call(operation, **kwargs)``; the server checks the caller
    HealthCheck                  reachability, permissions and versions of an environment

What each provider can do is declared in a ``ProviderDescriptor``, with the same rules as the engine
registry (engines/capabilities.py): anything above *unsupported* names its evidence; a provider with no
adapter claims nothing; a provider whose evidence isn't ``live_verified`` is Preview; and a provider is
offered in setup only when every required capability is at least built and tested (CP-ADR-007).
Databricks and Snowflake are declared here claiming nothing, so the app can say they are planned
without suggesting they work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..engines.capabilities import EVIDENCE

CAPABILITIES = {
    "discovery": "Find environments this person can open, without changing anything",
    "provisioning": "Create an environment's resources after a confirmed plan",
    "manifest": "Keep the environment's manifest in the environment, with conditional writes",
    "analytical_store": "Insert-only history tables with idempotent writes",
    "operational_store": "Transactional metadata with authorization checked by the service",
    "migrations": "Additive, versioned schema changes that are safe to run again",
    "health": "Check reachability, permissions and versions",
}
# A provider is offered in Create and Connect only with these at least built and tested.
REQUIRED = ("discovery", "provisioning", "manifest", "analytical_store", "migrations", "health")
LEVELS = ("supported", "partial", "unsupported")


@dataclass(frozen=True)
class Capability:
    level: str = "unsupported"
    evidence: str = "none"
    note: str = ""

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        if self.evidence not in EVIDENCE:
            raise ValueError(f"evidence must be one of {', '.join(EVIDENCE)}")
        if self.level != "unsupported" and self.evidence == "none":
            raise ValueError("A capability above unsupported needs evidence (unit_tests or live_verified)")
        if self.level == "partial" and not self.note:
            raise ValueError("A partial capability says what's missing in its note")


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    label: str
    adapter: bool
    capabilities: dict[str, Capability] = field(default_factory=dict)
    planned: str = ""

    def __post_init__(self) -> None:
        unknown = set(self.capabilities) - set(CAPABILITIES)
        if unknown:
            raise ValueError(f"{self.id}: unknown capabilities {sorted(unknown)}")
        claimed = [k for k, c in self.capabilities.items() if c.level != "unsupported"]
        if not self.adapter and claimed:
            raise ValueError(f"{self.id} has no adapter, so it can't claim {', '.join(sorted(claimed))}")
        if not self.adapter and not self.planned:
            raise ValueError(f"{self.id}: a provider without an adapter says what is planned")

    def capability(self, name: str) -> Capability:
        return self.capabilities.get(name, Capability())

    @property
    def preview(self) -> bool:
        """Preview unless every claimed capability was verified on the real platform."""
        claimed = [c for c in self.capabilities.values() if c.level != "unsupported"]
        return not claimed or any(c.evidence != "live_verified" for c in claimed)

    @property
    def available(self) -> bool:
        """Whether setup offers this provider: every required capability built and tested."""
        return self.adapter and all(self.capability(k).level != "unsupported" for k in REQUIRED)

    def describe(self) -> dict[str, Any]:
        status = ("Not available yet" if not self.available else "Preview" if self.preview else "Available")
        return {"id": self.id, "label": self.label, "status": status, "available": self.available,
                "preview": self.preview, "planned": self.planned,
                "capabilities": {k: {"level": self.capability(k).level, "evidence": self.capability(k).evidence,
                                     "note": self.capability(k).note, "what": CAPABILITIES[k]}
                                 for k in CAPABILITIES}}


_TESTED = Capability("supported", "unit_tests")

# Fabric: discovery (controlplane/fabric.py) and the stores (optimize/, central/) are built and tested
# against fakes, not yet run on a live tenant (docs/platform.md "What isn't proven yet"). Provisioning
# and health arrive with CP-005 and CP-007 and are claimed here only when their tests land.
FABRIC = ProviderDescriptor(
    id="fabric", label="Microsoft Fabric", adapter=True,
    capabilities={
        "discovery": _TESTED,
        "manifest": _TESTED,
        "analytical_store": _TESTED,
        "operational_store": Capability("partial", "unit_tests",
                                        "Needs a Fabric SQL database and user data functions (F2 or larger, or a trial)"),
        "migrations": _TESTED,
    },
    planned="Provisioning (CP-005) and health checks (CP-007); then a live run in a test workspace (CP-012).",
)

LOCAL = ProviderDescriptor(
    id="local", label="This PC (one person, for trying it out)", adapter=True,
    # The stores on this PC are built and tested (optimize/lakehouse.py's folder, central/client.py's
    # SQLite). Discovery, provisioning and health for it arrive with CP-004 to CP-007.
    capabilities={k: _TESTED for k in ("manifest", "analytical_store", "operational_store", "migrations")},
    planned="Discovery, provisioning and health checks for an environment kept on this PC (CP-004 to CP-007).",
)

DATABRICKS = ProviderDescriptor(
    id="databricks", label="Azure Databricks", adapter=False,
    planned="A Unity Catalog catalog and schema as the namespace, Delta tables as the analytical store; the "
            "operational store is decided after checking what the workspace offers (CP-013).",
)

SNOWFLAKE = ProviderDescriptor(
    id="snowflake", label="Snowflake", adapter=False,
    planned="Sign-in first, then a database and schemas as the namespace and tables as the analytical store (CP-014).",
)

REGISTRY: dict[str, ProviderDescriptor] = {p.id: p for p in (FABRIC, DATABRICKS, SNOWFLAKE, LOCAL)}


def describe_all() -> list[dict[str, Any]]:
    """Every provider as setup shows it, the ones that can't be used yet included."""
    return [p.describe() for p in REGISTRY.values()]


# ---- the contracts

@dataclass(frozen=True)
class StoreRef:
    """Where an analytical store is: ids only."""
    provider: str
    workspace_id: str
    lakehouse_id: str
    display_name: str = ""


@dataclass(frozen=True)
class Found:
    """A store discovery saw. ``state`` is ``environment`` (holds a manifest), ``adoptable`` (holds
    KratosForge tables but no manifest: CP-006), or ``unreadable`` with ``reason``."""
    ref: StoreRef
    state: str
    manifest: dict[str, Any] | None = None
    reason: str = ""


@dataclass(frozen=True)
class PlanStep:
    action: str           # find, create, initialize, upgrade, write_manifest, refuse
    target: str           # what it acts on, by label
    creates: bool         # whether it would create a resource
    permission: str = ""  # the role or permission the step needs
    cost: str = ""        # what it may cost, or "" for none
    blocked: str = ""     # why it can't go ahead, or ""


@dataclass(frozen=True)
class ProvisionPlan:
    provider: str
    steps: tuple[PlanStep, ...]

    @property
    def blocked(self) -> list[str]:
        return [s.blocked for s in self.steps if s.blocked]


@runtime_checkable
class EnvironmentDiscovery(Protocol):
    provider: str

    def list_namespaces(self) -> list[dict[str, str]]: ...

    def find_environments(self, namespace_id: str) -> list[Found]: ...


@runtime_checkable
class PlatformProvisioning(Protocol):
    def plan(self, request: dict[str, Any]) -> ProvisionPlan: ...

    def apply(self, plan: ProvisionPlan, confirmed: bool) -> dict[str, Any]: ...


@runtime_checkable
class AnalyticalStorageProvider(Protocol):
    def inspect(self, family: Any) -> dict[str, Any]: ...

    def initialize(self, family: Any, approve: bool) -> dict[str, Any]: ...


@runtime_checkable
class OperationalMetadataProvider(Protocol):
    def call(self, operation: str, **kwargs: Any) -> Any: ...


@runtime_checkable
class HealthCheck(Protocol):
    def check(self, manifest: dict[str, Any]) -> list[dict[str, Any]]: ...


class NotConfirmed(PermissionError):
    """A provisioning plan applied without the person's confirm. Nothing was created."""
