"""Evidence from what the app already reads (FEA-105): no new Fabric calls, only new shapes.

Each adapter takes the result of an existing reader and returns evidence records. A read that
failed gives no record of that kind (or an ``insufficient`` one when something was read but not
enough), so the controls that need it are *not evaluated*, never passed.

- :func:`workspace`       ``GET /workspaces/{id}`` and its items -> ``workspace.info``, ``workspace.inventory``
- :func:`roles`           ``GET /workspaces/{id}/roleAssignments`` -> ``workspace.roles``
- :func:`capacity`        ``GET /capacities`` -> ``capacity.state`` (only when the capacity is visible)
- :func:`optimization`    an ``optimization_assess`` result -> ``item.optimization`` per analyzed item
- :func:`inspection`      the same result, assessed with inspect_items.inspect -> ``item.inspection``
- :func:`models`          semantic model getDefinition (TMSL), via inspect_models.inspect -> ``item.model``

What is kept is what the controls need: roles keep each principal's type, role and whether it is a
guest, plus a display name so a finding can say who; e-mail addresses are dropped. Notebook code
and pipeline JSON never enter evidence: an item's evidence lists the optimization rules that
matched it, with their confidence, not the code; ``item.inspection`` keeps notebook rule ids with
cell and line, and pipeline activity names and counts (excellence.inspect_items), not the code;
``item.model`` keeps rule ids, table, column, relationship and role names and counts
(excellence.inspect_models), never DAX, M or the definition text.
"""

from __future__ import annotations

from typing import Any

from . import evidence as ev

GET_WORKSPACE = "GET /v1/workspaces/{id}"
LIST_ITEMS = "GET /v1/workspaces/{id}/items"
LIST_ROLES = "GET /v1/workspaces/{id}/roleAssignments"
LIST_CAPACITIES = "GET /v1/capacities"
OPTIMIZATION = "KratosForge optimization engine (getDefinition and job history, docs/optimize/engine.md)"
INSPECTION = "KratosForge definition inspection (the optimization engine's getDefinition read, excellence.inspect_items)"
INSPECTED = ("Notebook", "DataPipeline")
MODEL_DEFINITION = ("POST /v1/workspaces/{id}/items/{itemId}/getDefinition?format=TMSL "
                    "(read only, excellence.inspect_models)")


def _ws_subject(ws: dict[str, Any]) -> dict[str, Any]:
    return ev.subject("workspace", str(ws.get("id") or ""), ws.get("displayName") or ws.get("name"))


def workspace(ws: dict[str, Any], items: list[dict[str, Any]] | None, *, collected_at: str,
              info_read: bool = True) -> list[dict[str, Any]]:
    """The workspace's own settings (unless ``info_read`` is false: they couldn't be read), and its
    items when they could be listed (``None``: they couldn't)."""
    subj = _ws_subject(ws)
    out = []
    if info_read:
        out.append(ev.record("workspace.info", subj, source=GET_WORKSPACE, confidence="verified_configuration",
                             collected_at=collected_at,
                             payload={"description": str(ws.get("description") or ""),
                                      "capacity_id": ws.get("capacityId")}))
    if items is not None:
        listed = [{"id": str(i.get("id")), "type": str(i.get("type")), "name": str(i.get("displayName") or "")}
                  for i in items if i.get("id") and i.get("type")]
        out.append(ev.record("workspace.inventory", subj, source=LIST_ITEMS, confidence="verified_configuration",
                             collected_at=collected_at, payload={"items": sorted(listed, key=lambda i: i["id"])}))
    return out


def principal(a: dict[str, Any]) -> dict[str, Any]:
    """``guest`` is True or False for a user whose sign-in name Fabric sent, and None (not known) for a
    user without one; it is False for groups and apps."""
    p = a.get("principal") or {}
    kind = str(p.get("type") or "")
    upn = str((p.get("userDetails") or {}).get("userPrincipalName") or "")
    guest: bool | None = ("#EXT#" in upn.upper()) if upn else (None if kind == "User" else False)
    return {"id": str(p.get("id") or ""), "type": kind, "role": str(a.get("role") or ""),
            "name": str(p.get("displayName") or p.get("id") or ""), "guest": guest}


def roles(ws: dict[str, Any], assignments: list[dict[str, Any]] | None, *, collected_at: str) -> list[dict[str, Any]]:
    """Role assignments as Fabric lists them; ``None`` when they couldn't be read (a Viewer can't)."""
    if assignments is None:
        return []
    people = sorted((principal(a) for a in assignments), key=lambda p: (p["role"], p["type"], p["id"]))
    return [ev.record("workspace.roles", _ws_subject(ws), source=LIST_ROLES, confidence="verified_configuration",
                      collected_at=collected_at, payload={"assignments": people})]


def capacity(capacity_id: str, visible: list[dict[str, Any]] | None, *, collected_at: str) -> list[dict[str, Any]]:
    """The capacity's state and SKU, only when the signed-in person can see it. Not visible is not
    the same as not running, so it gives no evidence rather than a failing one."""
    if not capacity_id or visible is None:
        return []
    got = next((c for c in visible if str(c.get("id") or "").lower() == capacity_id.lower()), None)
    if got is None:
        return []
    subj = ev.subject("capacity", capacity_id.lower(), got.get("displayName"))
    return [ev.record("capacity.state", subj, source=LIST_CAPACITIES, confidence="verified_configuration",
                      collected_at=collected_at,
                      payload={"state": str(got.get("state") or ""), "sku": str(got.get("sku") or ""),
                               "region": str(got.get("region") or "")})]


def optimization(result: dict[str, Any], *, collected_at: str) -> list[dict[str, Any]]:
    """One ``item.optimization`` record per notebook or pipeline the optimization engine looked at.

    Its confidence is ``verified_configuration`` when the definition was read, ``insufficient`` when
    it wasn't (the controls on it are then not evaluated). Unsupported item types give nothing.
    ``peers_not_read`` names the other items of the same type whose definitions weren't read: a
    control that compares an item with its peers can't pass it while any of them is unseen."""
    matched: dict[str, list[dict[str, Any]]] = {}
    for o in result.get("opportunities") or []:
        for a in o.get("artifacts") or []:
            matched.setdefault(str(a.get("item_id")), []).append(
                {"rule": o.get("rule"), "opportunity_id": o.get("id"), "title": o.get("title"),
                 "confidence": o.get("confidence"), "role": a.get("role")})
    unread: dict[str, list[tuple[str, str]]] = {}
    for w in result.get("workloads") or []:
        if w.get("definition") == "not_read":
            unread.setdefault(str(w["type"]), []).append((str(w["item_id"]), str(w.get("name") or w["item_id"])))
    out = []
    for w in result.get("workloads") or []:
        if w.get("definition") not in ("read", "not_read"):
            continue
        peers = sorted(name for item, name in unread.get(str(w["type"]), []) if item != str(w["item_id"]))
        subj = ev.subject("item", str(w["item_id"]), w.get("name"), workspace_id=str(w.get("workspace_id")),
                          item_type=str(w["type"]))
        read = w["definition"] == "read"
        runs = w.get("runs") or {}
        out.append(ev.record("item.optimization", subj, source=OPTIMIZATION,
                             confidence="verified_configuration" if read else "insufficient", collected_at=collected_at,
                             payload={"definition": w["definition"], "problem": w.get("problem"),
                                      "scheduled_runs": runs.get("scheduled"), "history_read": bool(runs.get("read")),
                                      "peers_not_read": peers,
                                      "matched": sorted(matched.get(str(w["item_id"]), []),
                                                        key=lambda m: (str(m["rule"]), str(m["opportunity_id"])))}))
    return out


def inspection(result: dict[str, Any], *, collected_at: str) -> list[dict[str, Any]]:
    """One ``item.inspection`` record per notebook or pipeline whose definition the engine tried to read.

    ``verified_configuration`` when the definition was read and inspected, ``insufficient`` when it
    wasn't read or couldn't be inspected (its controls are then not evaluated). A workload assessed
    without the inspection hook, or of another type, gives nothing."""
    out = []
    for w in result.get("workloads") or []:
        got = w.get("inspected")
        if str(w.get("type")) not in INSPECTED or w.get("definition") not in ("read", "not_read") or not isinstance(got, dict):
            continue
        ok = w["definition"] == "read" and bool(got.get("read")) and not got.get("error")
        payload = dict(got) if ok else {"read": False, "problem": got.get("error") or w.get("problem")
                                        or "The definition wasn't read."}
        subj = ev.subject("item", str(w["item_id"]), w.get("name"), workspace_id=str(w.get("workspace_id")),
                          item_type=str(w["type"]))
        out.append(ev.record("item.inspection", subj, source=INSPECTION,
                             confidence="verified_configuration" if ok else "insufficient", collected_at=collected_at,
                             payload=payload))
    return out


def models(reads: list[dict[str, Any]], *, collected_at: str) -> list[dict[str, Any]]:
    """One ``item.model`` record per semantic model whose definition was read or tried.

    Each of ``reads`` is ``{"item_id", "name", "workspace_id", "inspected"}``, ``inspected`` being
    inspect_models.inspect's result, or ``{"read": False, "problem": text}``. ``verified_configuration``
    when it was read and inspected, ``insufficient`` otherwise (its controls are then not evaluated)."""
    out = []
    for m in reads:
        raw = m.get("inspected")
        got: dict[str, Any] = raw if isinstance(raw, dict) else {}
        ok = bool(got.get("read")) and not got.get("error")
        payload = dict(got) if ok else {"read": False, "problem": got.get("error") or got.get("problem")
                                        or "The definition wasn't read."}
        subj = ev.subject("item", str(m["item_id"]), m.get("name"), workspace_id=str(m.get("workspace_id")),
                          item_type="SemanticModel")
        out.append(ev.record("item.model", subj, source=MODEL_DEFINITION,
                             confidence="verified_configuration" if ok else "insufficient", collected_at=collected_at,
                             payload=payload))
    return out
