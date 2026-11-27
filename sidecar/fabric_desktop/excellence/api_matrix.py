"""The Fabric API capability matrix (FEA-106): every read the excellence assessment makes, what it
needs, and which controls depend on it.

Each endpoint records the delegated scopes and the workspace role or item permission Microsoft's REST
reference lists for it, its paging, whether it runs as a long-running operation, the throttling bucket
it draws from, the reference page and the date that page was checked. :data:`SOURCES` says which
endpoints (or local sources) each evidence kind is built from, so every control's needs follow from
its ``evidence_kinds`` and ``optional_kinds``.

:func:`gaps` lists the controls of a registry that rely on an evidence kind with no source here, an
endpoint not checked against the reference, or an admin-only endpoint. The tests require it to be
empty for the built-in catalog, so a new control can't ship until its reads are in the matrix. No
endpoint the assessment reads is admin-only: every one works with the signed-in person's own
workspace role.

docs/optimize/excellence-api-matrix.md holds :func:`markdown` verbatim (a test checks it), so the doc
and this module can't drift apart. Nothing here reads Fabric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .controls import Registry

CHECKED = "2026-11-26"  # the day each reference page below was read
_REST = "https://learn.microsoft.com/rest/api/fabric"
THROTTLING_DOC = f"{_REST}/articles/throttling"


@dataclass(frozen=True)
class Endpoint:
    id: str
    call: str  # method and path, as the reference names it
    scopes: tuple[str, ...]  # any one of them is enough, as the reference lists them
    role: str  # the workspace role or item permission the reference asks for, in its words
    paging: str
    long_running: bool
    throttling: str
    reference: str
    checked: str | None = CHECKED  # None: not yet checked against the reference
    admin_only: bool = False
    notes: str = ""


_PLATFORM = "Platform APIs, 500 calls a minute per identity; 429 with Retry-After"
_JOBS = "Job Scheduler APIs, 200 calls a minute per identity; 429 with Retry-After"
_GIT = "Platform APIs; this API is limited to 50 calls a minute; 429 with Retry-After"
_PAGED = "continuationToken and continuationUri"

ENDPOINTS: dict[str, Endpoint] = {e.id: e for e in (
    Endpoint("get-workspace", "GET /v1/workspaces/{workspaceId}",
             ("Workspace.Read.All", "Workspace.ReadWrite.All"), "Viewer or higher workspace role",
             "none (one object)", False, _PLATFORM, f"{_REST}/core/workspaces/get-workspace"),
    Endpoint("list-items", "GET /v1/workspaces/{workspaceId}/items",
             ("Workspace.Read.All", "Workspace.ReadWrite.All"), "Viewer or higher workspace role",
             _PAGED, False, _PLATFORM, f"{_REST}/core/items/list-items"),
    Endpoint("get-item-definition", "POST /v1/workspaces/{workspaceId}/items/{itemId}/getDefinition",
             ("Item.ReadWrite.All", "<ItemType>.ReadWrite.All"), "Read and write permission on the item",
             "none", True, _PLATFORM, f"{_REST}/core/items/get-item-definition",
             notes="Viewers can't read a definition, so the notebook, pipeline and semantic model controls "
                   "are not evaluated for them. Notebooks are read as ipynb, semantic models as TMSL. An item "
                   "with an encrypted sensitivity label needs the right to remove the label."),
    Endpoint("list-job-instances", "GET /v1/workspaces/{workspaceId}/items/{itemId}/jobs/instances",
             ("Item.Read.All", "Item.ReadWrite.All", "<ItemType>.Read.All", "<ItemType>.ReadWrite.All"),
             "Not stated on the reference page (it lists InsufficientPrivileges when the caller can't "
             "access the item)", _PAGED, False, _JOBS, f"{_REST}/core/job-scheduler/list-item-job-instances",
             notes="Most items keep at most 100 recently completed runs."),
    Endpoint("list-role-assignments", "GET /v1/workspaces/{workspaceId}/roleAssignments",
             ("Workspace.Read.All", "Workspace.ReadWrite.All"), "Member or higher workspace role",
             _PAGED, False, _PLATFORM, f"{_REST}/core/workspaces/list-workspace-role-assignments",
             notes="A Contributor or Viewer is refused, so the security controls are not evaluated for them."),
    Endpoint("list-capacities", "GET /v1/capacities",
             ("Capacity.Read.All", "Capacity.ReadWrite.All"),
             "Capacities the caller is an administrator or contributor of (a capacity role, not tenant admin)",
             _PAGED, False, _PLATFORM, f"{_REST}/core/capacities/list-capacities",
             notes="A capacity the caller can't see leaves the capacity controls not evaluated."),
    Endpoint("get-lakehouse", "GET /v1/workspaces/{workspaceId}/lakehouses/{lakehouseId}",
             ("Lakehouse.Read.All", "Lakehouse.ReadWrite.All", "Item.Read.All", "Item.ReadWrite.All"),
             "Read permission on the lakehouse", "none (one object)", False, _PLATFORM,
             f"{_REST}/lakehouse/items/get-lakehouse",
             notes="Gives defaultSchema (schema-enabled lakehouses only) and the SQL analytics endpoint's "
                   "provisioningStatus (InProgress, Success or Failed)."),
    Endpoint("get-warehouse", "GET /v1/workspaces/{workspaceId}/warehouses/{warehouseId}",
             ("Warehouse.Read.All", "Warehouse.ReadWrite.All", "Item.Read.All", "Item.ReadWrite.All"),
             "Read permission on the warehouse", "none (one object)", False, _PLATFORM,
             f"{_REST}/warehouse/items/get-warehouse",
             notes="Gives properties.collationType. The connection string it also returns is never kept."),
    Endpoint("get-git-connection", "GET /v1/workspaces/{workspaceId}/git/connection",
             ("Workspace.Read.All", "Workspace.ReadWrite.All"), "Contributor or higher workspace role",
             "none (one object)", False, _GIT, f"{_REST}/core/git/get-connection"),
    Endpoint("get-git-status", "GET /v1/workspaces/{workspaceId}/git/status",
             ("Workspace.GitUpdate.All", "Workspace.GitCommit.All"), "Contributor or higher workspace role",
             "none (one object)", True, _GIT, f"{_REST}/core/git/get-status",
             notes="Needs the caller's Git credentials configured in Fabric, and shouldn't run during an "
                   "update from Git. The assessment asks for its scope silently and never prompts."),
    Endpoint("onelake-list", "GET https://onelake.dfs.fabric.microsoft.com/{workspaceId}?resource=filesystem",
             ("https://storage.azure.com/user_impersonation",),
             "Admin, Member or Contributor workspace role (a Viewer only through OneLake security roles)",
             "x-ms-continuation header", False,
             "ADLS Gen2 limits: 503 when over the account's limits; no 429 documented",
             "https://learn.microsoft.com/fabric/onelake/onelake-api-parity",
             notes="Lists the folders under a lakehouse's Tables. Storage tokens only; asked for silently."),
    Endpoint("onelake-read", "GET https://onelake.dfs.fabric.microsoft.com/{workspaceId}/{itemId}/Tables/.../_delta_log",
             ("https://storage.azure.com/user_impersonation",),
             "Admin, Member or Contributor workspace role (a Viewer only through OneLake security roles)",
             "none", False, "ADLS Gen2 limits: 503 when over the account's limits; no 429 documented",
             "https://learn.microsoft.com/fabric/onelake/security/get-started-security",
             notes="Reads only each Delta table's log and checkpoint, never a data file."),
)}

LOCAL_USAGE = "local:capacity-metrics-imports"  # Capacity Metrics exports imported on this PC: no Fabric call

# evidence kind -> the endpoints (or local sources) its records are built from
SOURCES: dict[str, tuple[str, ...]] = {
    "workspace.info": ("get-workspace",),
    "workspace.inventory": ("list-items",),
    "workspace.roles": ("list-role-assignments",),
    "workspace.naming": ("get-workspace", "list-items", "get-lakehouse"),
    "workspace.git": ("get-git-connection", "get-git-status"),
    "workspace.warehouses": ("list-items", "get-warehouse"),
    "capacity.state": ("list-capacities",),
    "capacity.usage": ("list-capacities", LOCAL_USAGE),
    "item.optimization": ("list-items", "get-item-definition", "list-job-instances"),
    "item.inspection": ("get-item-definition",),
    "item.model": ("get-item-definition",),
    "item.binding": ("get-item-definition",),
    "lakehouse.properties": ("get-lakehouse",),
    "lakehouse.tables": ("list-items", "onelake-list", "onelake-read"),
    "sqlendpoint.properties": ("list-items", "get-lakehouse"),
    "warehouse.properties": ("get-warehouse",),
}
LOCAL = {LOCAL_USAGE: "Capacity Metrics exports imported on this PC (Optimize > Cost and savings): a local "
                      "read, no Fabric call, no permission"}


def endpoints_for(kinds: tuple[str, ...] | list[str]) -> list[str]:
    """The endpoint and local source ids the evidence ``kinds`` are built from, in matrix order."""
    want = {s for k in kinds for s in SOURCES.get(k, ())}
    return [i for i in (*ENDPOINTS, *LOCAL) if i in want]


def _kinds(c: Any) -> tuple[str, ...]:
    return (*c.evidence_kinds, *c.optional_kinds)


def gaps(registry: Registry) -> list[dict[str, Any]]:
    """Controls that can't be relied on yet: an evidence kind with no source, or a source that is
    unknown, unchecked against Microsoft's reference, or admin-only."""
    out = []
    for c in registry.controls():
        problems = [f"evidence {k} has no source in the matrix" for k in _kinds(c) if k not in SOURCES]
        for s in endpoints_for(_kinds(c)):
            e = ENDPOINTS.get(s)
            if e is None:
                continue  # a local source
            if not e.checked:
                problems.append(f"{s} isn't checked against the reference")
            if e.admin_only:
                problems.append(f"{s} needs a Fabric administrator")
        problems += [f"{s} isn't a known endpoint" for k in _kinds(c) for s in SOURCES.get(k, ())
                     if s not in ENDPOINTS and s not in LOCAL]
        if problems:
            out.append({"control_id": c.id, "problems": problems})
    return out


def controls_by_endpoint(registry: Registry) -> dict[str, list[str]]:
    """Endpoint or local source id -> the ids of the controls that depend on it, sorted."""
    out: dict[str, list[str]] = {i: [] for i in (*ENDPOINTS, *LOCAL)}
    for c in registry.controls():
        for s in endpoints_for(_kinds(c)):
            out[s].append(c.id)
    return {k: sorted(v) for k, v in out.items()}


def as_dict(registry: Registry) -> dict[str, Any]:
    """The matrix as data (the machine-readable copy)."""
    used = controls_by_endpoint(registry)
    return {"checked": CHECKED, "throttling": THROTTLING_DOC,
            "endpoints": [{**e.__dict__, "scopes": list(e.scopes), "controls": used[e.id]} for e in ENDPOINTS.values()],
            "local": [{"id": k, "what": v, "controls": used[k]} for k, v in LOCAL.items()],
            "sources": {k: list(v) for k, v in SOURCES.items()}}


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def markdown(registry: Registry) -> str:
    """The endpoint table and the evidence table, as docs/optimize/excellence-api-matrix.md shows them."""
    used = controls_by_endpoint(registry)
    lines = ["| Read | Scopes (any one) | Role or permission | Paging | Long-running | Throttling | Controls | Checked |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for e in ENDPOINTS.values():
        lines.append("| " + " | ".join(_cell(x) for x in (
            f"[`{e.call}`]({e.reference})" + (f" {e.notes}" if e.notes else ""),
            ", ".join(f"`{s}`" for s in e.scopes), e.role + (" (Fabric administrator)" if e.admin_only else ""),
            e.paging, "Yes" if e.long_running else "No", e.throttling,
            ", ".join(used[e.id]) or "none", e.checked or "not checked")) + " |")
    for k, v in LOCAL.items():
        lines.append(f"| {v} | none | none | none | No | none | {', '.join(used[k]) or 'none'} | n/a |")
    lines += ["", "| Evidence | Built from |", "| --- | --- |"]
    for k, srcs in SOURCES.items():
        lines.append(f"| `{k}` | " + ", ".join(f"`{s}`" for s in srcs) + " |")
    return "\n".join(lines) + "\n"
