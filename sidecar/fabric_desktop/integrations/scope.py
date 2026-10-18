"""Workspace limits: which workspaces a capability reaches, and the one check that keeps a limited
tool inside its own (HD-014, finding EN-3).

The person can limit a connected tool to some workspaces (Settings > Integrations). Every
capability says how it is confined, in ``Capability.workspaces``:

- ``NONE``: it reaches no workspace or lakehouse (the engine's status, a pipeline definition
  passed in the call, a local pipeline run).
- ``ARGUMENT``: its ``workspace_id`` argument names the one workspace it reaches; empty means none.
- ``FILTERED``: a list. The handler leaves out every row outside the tool's workspaces with
  ``keep``, before paging, so totals count only what the tool may see.
- ``UNKNOWN``: it can't be confined to a workspace, so a limited tool can't use it.
- a resolver ``(service, arguments, ctx) -> set of workspace ids, or None``: reads what the call
  will touch (a notebook's lakehouse and publish workspace, a proof's, a receipt's, an
  execution's). ``None`` means it can't tell, and is refused.

A capability that declares nothing (an extension written before this) is treated as ``UNKNOWN``:
it still works for a tool with no limit and is refused for a limited one. So a capability can't
reach another workspace by forgetting to say which it reaches.

``enforce`` is called by ``PermissionAuthorizer.check`` for every call, after the permission
category and before any approval, so the person is never asked to approve a call that would leave
the tool's workspaces.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .core import CallContext, IntegrationError, error_from

NONE = "none"
ARGUMENT = "argument"
FILTERED = "filtered"
UNKNOWN = "unknown"
KINDS = (NONE, ARGUMENT, FILTERED, UNKNOWN)
WHERE = "Fabric Desktop by KratosBI > Settings > Integrations"
_LOCATED = ("workspace_name", "lakehouse_id", "lakehouse_name")  # a dict naming a place in Fabric
_MAX_DEPTH = 12
_MAX_PARENTS = 3


def check_declaration(cap: Any) -> None:
    """Raise ValueError if ``cap.workspaces`` isn't a valid declaration (at registration)."""
    value = cap.workspaces
    if value is None or callable(value):
        return
    if value not in KINDS:
        raise ValueError(f"{cap.id}: workspaces must be one of {', '.join(KINDS)}, or a function")
    if value == ARGUMENT and "workspace_id" not in cap.input_schema.get("properties", {}):
        raise ValueError(f"{cap.id}: workspaces={ARGUMENT!r} needs a workspace_id input")


def confinable(cap: Any) -> bool:
    """Whether a tool limited to some workspaces could ever use ``cap``."""
    return cap.workspaces is not None and cap.workspaces != UNKNOWN


def targets(cap: Any, service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    """The workspace ids this call reaches; an empty set for none, None when it can't tell.
    Raises the engine's own error (a missing notebook is not_found) when the resolver does."""
    kind = cap.workspaces
    if kind in (NONE, FILTERED):
        return set()
    if kind == ARGUMENT:
        ws = arguments.get("workspace_id") or ""
        return {ws} if ws else set()
    if callable(kind):
        if service is None:
            return None
        try:
            found = kind(service, dict(arguments), ctx)
        except IntegrationError:
            raise
        except Exception as exc:  # noqa: BLE001 - the engine's message, as for a handler
            raise error_from(exc) from exc
        return None if found is None else {str(w) for w in found}
    return None  # UNKNOWN, or not declared


def enforce(cap: Any, limit: frozenset[str] | None, resolve: Callable[[], set[str] | None]) -> None:
    """Refuse the call unless every workspace it reaches is in ``limit``. ``None`` is no limit."""
    if not limit:
        return
    if not confinable(cap):
        raise IntegrationError("permission_denied", unconfined_reason(cap), hint=f"The person can change its workspaces in {WHERE}.")
    reached = resolve()
    if reached is None:
        raise IntegrationError("permission_denied", unconfined_reason(cap), hint=f"The person can change its workspaces in {WHERE}.")
    if reached - limit:
        raise IntegrationError("permission_denied", "This tool is limited to other workspaces.",
                               hint=f"The person can change its workspaces in {WHERE}.")


def unconfined_reason(cap: Any) -> str:
    return (f"This tool is limited to some workspaces, and Fabric Desktop by KratosBI can't tell which workspace "
            f"{cap.title.lower()} would reach, so it isn't allowed.")


def keep(ctx: CallContext, rows: Iterable[Any], of: Callable[[Any], set[str] | None]) -> list[Any]:
    """The rows a tool may see: all of them with no limit; otherwise those whose workspaces (``of``)
    are known and all inside the limit. A row ``of`` can't place, or fails on, is left out."""
    rows = list(rows)
    limit = getattr(ctx, "workspaces", None)
    if not limit:
        return rows
    out = []
    for row in rows:
        try:
            found = of(row)
        except Exception:  # noqa: BLE001 - a row that can't be read can't be checked: leave it out
            continue
        if found is not None and found <= limit:
            out.append(row)
    return out


# ---- reading where things are

def located(*parts: Any) -> set[str] | None:
    """Every ``workspace_id`` in ``parts`` (dicts and lists, at any depth). None when a part names a
    place in Fabric (a workspace name, a lakehouse) without its workspace id: it can't be checked."""
    found: set[str] = set()

    def walk(value: Any, depth: int) -> bool:
        if depth > _MAX_DEPTH:
            return False
        if isinstance(value, dict):
            ws = value.get("workspace_id")
            if ws:
                found.add(str(ws))
            elif any(value.get(k) for k in _LOCATED):
                return False
            return all(walk(v, depth + 1) for k, v in value.items() if isinstance(v, (dict, list)))
        if isinstance(value, list):
            return all(walk(v, depth + 1) for v in value)
        return True

    return found if all(walk(p, 0) for p in parts) else None


def _somewhere(found: set[str] | None) -> set[str] | None:
    """For a record that must be somewhere (a proof, a receipt): no workspace at all can't be checked."""
    return found or None


def notebook_of(nb: dict[str, Any]) -> set[str] | None:
    """A saved notebook's attached lakehouse's workspace and the workspace it publishes to. A
    notebook with neither reaches none."""
    return located(nb.get("lakehouse") or {}, nb.get("workspace") or {})


def notebook(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    found = notebook_of(service.notebooks.load(arguments["name"]))
    if found is not None and arguments.get("workspace_id"):
        found.add(arguments["workspace_id"])
    return found


def record_of(record: dict[str, Any]) -> set[str] | None:
    """A proof or side-by-side run: its lakehouse's workspace, where it published, and its inputs."""
    if not isinstance(record, dict):
        return None
    lake = record.get("lakehouse") or {}
    if lake and not lake.get("workspace_id"):
        return None  # saved before HD-014, with only the workspace's name
    return _somewhere(located(record))


def proof(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    return record_of(service.prove_status(arguments["id"]))


def side_by_side(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    return record_of(service.side_by_side_status(arguments["id"]))


def receipt_of(service: Any, receipt_id: str) -> set[str] | None:
    """Where a receipt's run was. A verification receipt names no place itself; it is where the proof
    it checks was, read from that proof's receipt."""
    found: set[str] = set()
    rid: str | None = receipt_id
    for _ in range(_MAX_PARENTS):
        if not rid:
            break
        body = service.receipt_get(rid)["receipt"]
        here = located(body)
        if here is None:
            return None
        found |= here
        rid = (body.get("proof") or {}).get("receipt") if body.get("kind") == "verification" else None
    else:
        if rid:
            return None
    return _somewhere(found)


def receipt(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    return receipt_of(service, arguments["id"])


def execution_of(record: Any) -> set[str] | None:
    view = record.view() if hasattr(record, "view") else record
    return located((view or {}).get("target") or {})


def execution(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    from .services import _executions
    client = ctx.caller.client_id if ctx is not None else ""
    return execution_of(_executions(service).get(arguments["execution_id"], client))


def metadata_of(meta: dict[str, Any]) -> set[str] | None:
    """The open metadata project: the workspaces its environments and entities name."""
    if not meta.get("path"):
        return set()
    return located(meta.get("environments") or {}, meta.get("entities") or [])


def project(service: Any, arguments: dict[str, Any], ctx: CallContext | None) -> set[str] | None:
    """A project: the notebooks project is a list (filtered); the metadata project is where it points."""
    if arguments.get("id", "metadata") == "notebooks":
        return set()
    return metadata_of(service.meta_project())
