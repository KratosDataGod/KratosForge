"""What changed between two reads, and which controls to assess again (FEA-506).

:func:`changes` compares a stored assessment with a later one (or with the evidence records of a later
read) and lists what differs, per resource, with the controls whose declared evidence the change
touches. This is **polling**: two reads compared after the fact. It is not event-driven monitoring;
a change made and undone between the two reads is never seen, and nothing here listens to Fabric.

Change kinds:

- ``item_added`` / ``item_removed``: an item id is in one workspace inventory and not the other.
- ``definition_changed``: evidence about an item whose source is its definition (getDefinition) differs.
  No definition hash is kept, and no definition text ever enters evidence, so the comparison uses the
  evidence payload as a proxy: rule ids, names and counts derived from the definition. An edit that
  changes none of those (a comment, a cell that matches no rule) is not detected.
- ``role_assignment_changed``: the set of (principal, type, role, guest) in ``workspace.roles`` differs.
  A principal's display name alone changing is ``evidence_changed``.
- ``capacity_changed``: the workspace's capacity id in ``workspace.info`` differs (reassignment), or a
  capacity's ``capacity.state`` (state, SKU, region) differs.
- ``git_state_changed``: evidence of a kind that starts with ``git.`` or ``workspace.git`` differs. No
  evidence records Git state yet, so this is listed under ``not_tracked`` until one does.
- ``evidence_changed``: anything else a control reads that differs (a description, the naming heuristic,
  lakehouse properties, imported capacity usage), so no change a control depends on is dropped.

A change is ``changed`` or ``unknown``. When the evidence was collected on one side and is missing or
only ``insufficient`` on the other (the read failed, or wasn't allowed), the result is ``unknown``,
never "unchanged". Evidence of a kind neither side collected is not compared, but a workspace or
capacity in both scopes with no evidence at all in either read is one ``unknown`` entry. Records that
fail evidence.problems, or whose subject, source or payload shape can't be read, are treated as missing.
Raw records for ``current`` use the previous scope plus the resources the new read holds.

Order never counts as a change: lists inside payloads are compared as sorted. The result is the same
for the same inputs. ``controls_to_reassess`` comes from each control's declared ``evidence_kinds`` and
``optional_kinds``, not a hand list: a control is listed when it declares a kind the change touches and
(for an item control) covers the item's type. Pure: no Fabric call, no file, no clock.
"""

from __future__ import annotations

import re
from typing import Any

from . import controls as ct
from .evidence import Bundle, canonical, content_hash, problems

SCHEMA = "kratosforge/excellence-changes@1"
METHOD = "polling"
NOTE = ("Found by comparing two reads (polling), not by event-driven monitoring: a change made and undone "
        "between the two reads isn't seen. Unknown means one of the reads didn't have the evidence.")
KINDS = ("item_added", "item_removed", "definition_changed", "role_assignment_changed", "capacity_changed",
         "git_state_changed", "evidence_changed")
MAX_CHANGES = 50
MAX_NAME = 256
INVENTORY = "workspace.inventory"
ROLES = "workspace.roles"
INFO = "workspace.info"
CAPACITY_STATE = "capacity.state"
DEFINITION_SOURCE = "getdefinition"  # in an evidence source: the record was derived from the item's definition
_GIT = ("git.", "workspace.git")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_STATUS = ("changed", "unknown")


def _cut(text: Any) -> str:
    clean = _CONTROL.sub(" ", str(text if text is not None else ""))
    return clean if len(clean) <= MAX_NAME else clean[:MAX_NAME - 1] + "…"


def _subject(s: dict[str, Any]) -> dict[str, Any]:
    out = {"type": _cut(s.get("type")), "id": _cut(s.get("id")), "name": _cut(s.get("name") or s.get("id"))}
    for k in ("workspace_id", "item_type"):
        if s.get(k):
            out[k] = _cut(s[k])
    return out


def _norm(value: Any) -> Any:
    """``value`` with every list sorted, so reordering isn't a difference."""
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return sorted((_norm(v) for v in value), key=canonical)
    return value


def _digest(payload: Any) -> str:
    return content_hash(_norm(payload))


def _sound(r: Any) -> bool:
    """A record this module can read at all: a dict with a string kind and source, a subject with a
    string type and id, and a payload evidence.problems can check without raising (a lone surrogate
    can't be hashed). Anything else counts as missing."""
    if not (isinstance(r, dict) and isinstance(r.get("kind"), str) and isinstance(r.get("source"), str)
            and isinstance(r.get("subject"), dict) and isinstance(r["subject"].get("type"), str)
            and isinstance(r["subject"].get("id"), str) and r["subject"]["id"]):
        return False
    try:
        problems(r)
    except (TypeError, ValueError):  # UnicodeEncodeError is a ValueError
        return False
    return True


def _shaped(kind: str, payload: Any) -> bool:
    """A payload this module can compare for ``kind``; a malformed one is missing, so it is unknown and
    never reads as an empty inventory or role list."""
    if kind == INVENTORY:
        return isinstance(payload, dict) and isinstance(payload.get("items"), list)
    if kind == ROLES:
        return isinstance(payload, dict) and isinstance(payload.get("assignments"), list)
    if kind == INFO:
        return isinstance(payload, dict)
    return True


class _Side:
    """One read: usable records by (subject id, kind), and which kinds were collected at all."""

    def __init__(self, records: list[dict[str, Any]]):
        self.bundle = Bundle([r for r in records if _sound(r)])
        self.subjects: dict[str, dict[str, Any]] = {}
        self.kinds: dict[str, set[str]] = {}
        for r in self.bundle.records:
            sid = r["subject"]["id"]
            self.subjects.setdefault(sid, r["subject"])
            self.kinds.setdefault(sid, set()).add(r["kind"])

    def usable(self, sid: str, kind: str) -> list[dict[str, Any]] | None:
        """The records, or None when there are none that can decide (missing or only insufficient)."""
        got = [r for r in self.bundle.get(sid, kind) if r["confidence"] != "insufficient" and _shaped(kind, r["payload"])]
        return got or None


def _records(x: Any) -> list[dict[str, Any]]:
    got = x.get("evidence") if isinstance(x, dict) else x
    return list(got) if isinstance(got, (list, tuple)) else []


def _scope(x: Any, side: _Side) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if isinstance(x, dict) and isinstance(x.get("scope"), dict):
        sc = x["scope"]
        return ({str(s["id"]): s for s in sc.get("workspaces") or [] if isinstance(s, dict) and s.get("id")},
                {str(s["id"]): s for s in sc.get("capacities") or [] if isinstance(s, dict) and s.get("id")})
    ws, caps = {}, {}
    for sid, s in side.subjects.items():
        if s["type"] == "workspace":
            ws[sid] = s
        elif s["type"] == "capacity":
            caps[sid] = s
        elif s["type"] == "item" and s.get("workspace_id"):
            ws.setdefault(str(s["workspace_id"]), {"type": "workspace", "id": str(s["workspace_id"])})
    return ws, caps


class _Mapper:
    """Controls to reassess, from what each control declares."""

    def __init__(self, registry: ct.Registry):
        self.controls = [(c, set(c.evidence_kinds) | set(c.optional_kinds)) for c in registry.controls()]

    def __call__(self, subj: dict[str, Any], kinds: set[str], *, item_type: str | None = None,
                 all_items: bool = False, capacity_scope: bool = False) -> list[str]:
        out = set()
        for c, declared in self.controls:
            if subj["type"] == "item":
                if c.scope == "item" and subj.get("item_type") in c.item_types and declared & kinds:
                    out.add(c.id)
                continue
            if declared & kinds:
                out.add(c.id)
            if c.scope == "item" and (all_items or (item_type and item_type in c.item_types)):
                out.add(c.id)
            if capacity_scope and c.scope == "capacity":
                out.add(c.id)
        return sorted(out)


def _entry(kind: str, status: str, subj: dict[str, Any], kinds: set[str], controls: list[str],
           detail: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    out = {"kind": kind, "status": status, "subject": _subject(subj), "evidence_kinds": sorted(kinds),
           "controls_to_reassess": controls}
    if detail:
        out["detail"] = detail
    if reason:
        out["reason"] = reason
    return out


def _unknown_reason(a: list | None, b: list | None) -> str:
    if a is None and b is None:
        return "Neither read had evidence that could decide it."
    return ("The earlier read didn't have this evidence." if a is None
            else "The later read didn't have this evidence (the read failed or wasn't allowed).")


def _items(recs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for r in recs:
        for it in (r["payload"] or {}).get("items") or []:
            if isinstance(it, dict) and it.get("id") and it.get("type"):
                out[str(it["id"])] = {"type": str(it["type"]), "name": str(it.get("name") or "")}
    return out


def _roles(recs: list[dict[str, Any]]) -> set[tuple[str, str, str, Any]]:
    return {(str(p.get("id")), str(p.get("type")), str(p.get("role")), canonical(p.get("guest")))
            for r in recs for p in (r["payload"] or {}).get("assignments") or [] if isinstance(p, dict)}


def _digests(recs: list[dict[str, Any]], drop: tuple[str, ...] = ()) -> list[str]:
    def strip(p: Any) -> Any:
        return {k: v for k, v in p.items() if k not in drop} if isinstance(p, dict) else p
    return sorted({_digest(strip(r["payload"])) for r in recs})


def _classify(kind: str, subj: dict[str, Any], recs: list[dict[str, Any]]) -> str:
    if kind.startswith(_GIT):
        return "git_state_changed"
    if kind == CAPACITY_STATE:
        return "capacity_changed"
    if subj["type"] == "item" and any(DEFINITION_SOURCE in r["source"].replace(" ", "").lower() for r in recs):
        return "definition_changed"
    return "evidence_changed"


def changes(previous: dict[str, Any] | list[dict[str, Any]], current: dict[str, Any] | list[dict[str, Any]],
            registry: ct.Registry | None = None) -> dict[str, Any]:
    """The changes from ``previous`` (an assessment result, or evidence records) to ``current`` (an
    assessment result, or the evidence records of a new read of the same scope). See the module docstring."""
    if registry is None:
        from . import catalog
        registry = catalog.registry()
    mapper = _Mapper(registry)
    a, b = _Side(_records(previous)), _Side(_records(current))
    ws_a, cap_a = _scope(previous, a)
    if isinstance(current, dict) and isinstance(current.get("scope"), dict):
        ws_b, cap_b = _scope(current, b)
    else:  # raw records: the previous scope, plus what the new read holds, so nothing new is silently left out
        got_ws, got_caps = _scope(None, b)
        ws_b, cap_b = {**got_ws, **ws_a}, {**got_caps, **cap_a}
    found: list[dict[str, Any]] = []

    def subj_of(sid: str, fallback: dict[str, Any]) -> dict[str, Any]:
        return b.subjects.get(sid) or a.subjects.get(sid) or fallback

    def compare_kind(subj: dict[str, Any], kind: str) -> None:
        sid = subj["id"]
        ra, rb = a.usable(sid, kind), b.usable(sid, kind)
        if ra is None or rb is None:
            reason = _unknown_reason(ra, rb)
            if kind == INVENTORY:
                for k in ("item_added", "item_removed"):
                    found.append(_entry(k, "unknown", subj, {kind}, mapper(subj, {kind}, all_items=True), reason=reason))
            elif kind == ROLES:
                found.append(_entry("role_assignment_changed", "unknown", subj, {kind}, mapper(subj, {kind}), reason=reason))
            elif kind == INFO:
                found.append(_entry("capacity_changed", "unknown", subj, {kind}, mapper(subj, {kind}, capacity_scope=True),
                                    reason=reason))
                found.append(_entry("evidence_changed", "unknown", subj, {kind}, mapper(subj, {kind}), reason=reason))
            else:
                recs = (ra or []) + (rb or []) + a.bundle.get(sid, kind) + b.bundle.get(sid, kind)
                found.append(_entry(_classify(kind, subj, recs), "unknown", subj, {kind}, mapper(subj, {kind}),
                                    reason=reason))
            return
        if kind == INVENTORY:
            ia, ib = _items(ra), _items(rb)
            for iid in sorted(set(ib) - set(ia)):
                it = {"type": "item", "id": iid, "name": ib[iid]["name"] or iid, "workspace_id": sid,
                      "item_type": ib[iid]["type"]}
                found.append(_entry("item_added", "changed", it, {kind}, mapper(subj, {kind}, item_type=ib[iid]["type"])))
            for iid in sorted(set(ia) - set(ib)):
                it = {"type": "item", "id": iid, "name": ia[iid]["name"] or iid, "workspace_id": sid,
                      "item_type": ia[iid]["type"]}
                found.append(_entry("item_removed", "changed", it, {kind}, mapper(subj, {kind}, item_type=ia[iid]["type"])))
            if any(ia[i] != ib[i] for i in set(ia) & set(ib)) or (set(ia) == set(ib) and _digests(ra) != _digests(rb)):
                found.append(_entry("evidence_changed", "changed", subj, {kind}, mapper(subj, {kind}),
                                    detail={"renamed_or_retyped": sum(1 for i in set(ia) & set(ib) if ia[i] != ib[i])}))
            return
        if kind == ROLES:
            pa, pb = _roles(ra), _roles(rb)
            if pa != pb:
                found.append(_entry("role_assignment_changed", "changed", subj, {kind}, mapper(subj, {kind}),
                                    detail={"added": len(pb - pa), "removed": len(pa - pb)}))
            elif _digests(ra) != _digests(rb):
                found.append(_entry("evidence_changed", "changed", subj, {kind}, mapper(subj, {kind})))
            return
        if kind == INFO:
            ca = sorted({str((r["payload"] or {}).get("capacity_id") or "") for r in ra})
            cb = sorted({str((r["payload"] or {}).get("capacity_id") or "") for r in rb})
            if ca != cb:
                found.append(_entry("capacity_changed", "changed", subj, {kind},
                                    mapper(subj, {kind}, capacity_scope=True),
                                    detail={"from": [_cut(c) or None for c in ca], "to": [_cut(c) or None for c in cb]}))
            if _digests(ra, ("capacity_id",)) != _digests(rb, ("capacity_id",)):
                found.append(_entry("evidence_changed", "changed", subj, {kind}, mapper(subj, {kind})))
            return
        if _digests(ra) != _digests(rb):
            found.append(_entry(_classify(kind, subj, ra + rb), "changed", subj, {kind}, mapper(subj, {kind})))

    def kinds_of(sid: str) -> list[str]:
        return sorted(a.kinds.get(sid, set()) | b.kinds.get(sid, set()))

    def nothing(subj: dict[str, Any], scopes: tuple[str, ...]) -> None:
        """A resource in both scopes with no evidence in either read: unknown, never unchanged."""
        found.append(_entry("evidence_changed", "unknown", subj, set(),
                            sorted(c.id for c, _ in mapper.controls if c.scope in scopes),
                            reason=_unknown_reason(None, None)))

    workspaces = sorted(set(ws_a) & set(ws_b))
    capacities = sorted(set(cap_a) & set(cap_b))
    for wid in workspaces:
        subj = subj_of(wid, {"type": "workspace", "id": wid, "name": ws_b[wid].get("name") or ws_a[wid].get("name")})
        if not kinds_of(wid) and not any(str(s.get("workspace_id")) == wid for side in (a, b)
                                         for s in side.subjects.values() if s["type"] == "item"):
            nothing(subj, ("workspace", "item"))
            continue
        for kind in kinds_of(wid):
            compare_kind(subj, kind)
        inv_a, inv_b = a.usable(wid, INVENTORY), b.usable(wid, INVENTORY)
        gone = set(_items(inv_a)) ^ set(_items(inv_b)) if inv_a and inv_b else set()
        items = sorted({sid for side in (a, b) for sid, s in side.subjects.items()
                        if s["type"] == "item" and str(s.get("workspace_id")) == wid} - gone)
        for iid in items:
            for kind in kinds_of(iid):
                compare_kind(subj_of(iid, {}), kind)
    for cid in capacities:
        subj = subj_of(cid, {"type": "capacity", "id": cid, "name": cap_b[cid].get("name") or cap_a[cid].get("name")})
        if not kinds_of(cid):
            nothing(subj, ("capacity",))
            continue
        for kind in kinds_of(cid):
            compare_kind(subj, kind)

    order = {k: i for i, k in enumerate(KINDS)}
    found.sort(key=lambda e: (_STATUS.index(e["status"]), order[e["kind"]], e["subject"]["type"],
                              e["subject"].get("workspace_id", ""), e["subject"]["id"], e["evidence_kinds"]))
    every = {r["kind"] for side in (a, b) for r in side.bundle.records}
    not_compared = sorted([{"type": "workspace", "id": i} for i in set(ws_a) ^ set(ws_b)]
                          + [{"type": "capacity", "id": i} for i in set(cap_a) ^ set(cap_b)],
                          key=lambda s: (s["type"], s["id"]))
    not_tracked = [] if any(k.startswith(_GIT) for k in every) else [
        {"kind": "git_state_changed", "reason": "No evidence records Git state yet, so a Git change can't be seen."}]
    return {"schema": SCHEMA, "method": METHOD, "note": NOTE,
            "from": previous.get("assessed_at") if isinstance(previous, dict) else None,
            "to": current.get("assessed_at") if isinstance(current, dict) else None,
            "changes": found[:MAX_CHANGES], "count": len(found), "truncated": len(found) > MAX_CHANGES,
            "changed": sum(1 for e in found if e["status"] == "changed"),
            "unknown": sum(1 for e in found if e["status"] == "unknown"),
            "controls_to_reassess": sorted({c for e in found for c in e["controls_to_reassess"]}),
            "not_compared": [{"type": s["type"], "id": _cut(s["id"])} for s in not_compared[:MAX_CHANGES]],
            "not_tracked": not_tracked}
