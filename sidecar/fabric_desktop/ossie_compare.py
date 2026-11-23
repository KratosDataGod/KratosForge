"""Ossie version compare (OSSIE-002b): what changed between two versions of one semantic model.

``compare_versions(old, new)`` reads each version as ``semantic_parity`` reads a side (an Ossie 0.2.0.dev0 document
through the vendored converter, a ``model.bim`` (TMSL) or a TMDL folder's files) and lists, per object (tables,
columns, measures and metrics, relationships, roles, calculation groups, hierarchies and perspectives), what was
added, removed or changed, and how many objects are unchanged. A change says what changed: the data type, the
expression (compared through L2's canonical forms where both sides are inside its subset, so a rewrite with the same
form is told apart from one that computes something else), the format string, the description, a role's
permissions or a calculation item.

Ossie 0.3.0.dev0 and Snowflake's 0.1.1 wrapper are refused with the reason, as Import refuses them: the converter
reads 0.2.0.dev0 only. Bad input never raises: the report's ``error`` says why and nothing is listed.

Security: roles and calculation groups that changed are listed in ``look_at`` too. The report itself can quote a
role's filter (cut to 80 characters) and name its members, as the L1 parity report does; anything that leaves the
local pane must use ``redacted(report)``, which keeps counts, lengths and hashes instead.

Pure: nothing is fetched, written or executed.
"""

from __future__ import annotations

import collections
import copy
from typing import Any

from . import model_ossie as mo, semantic_parity as sp

ADDED, REMOVED, CHANGED = "added", "removed", "changed"
KINDS = ("table", "calculation_group", "column", "measure", "relationship", "role", "hierarchy", "perspective")
SECURITY = ("role", "calculation_group")
NOT_CHECKED = (
    {"what": "model rename", "reason": "the model's own name is only noted, never counted as a change: TMDL doesn't "
                                       "carry it and an export names the document after the item"},
    {"what": "column order", "reason": "columns are matched by name, so moving a column isn't reported"},
    {"what": "behavior", "reason": "no query runs; a changed expression form says nothing about the numbers "
                                   "(semantic_parity_l3 runs the SQL side)"},
    {"what": "other properties", "reason": "only type, key, expression, format string, description, home table, "
                                           "relationship ends and flags, role permissions and calculation items are "
                                           "compared; lineage tags, visibility, sort-by and annotations aren't"},
)
_LOOK_AT_WHY = {"role": "a security role: check who can see which rows and columns",
                "calculation_group": "a calculation group: it changes what every measure it applies to returns"}
_SIDE_WORDS = (("on the left side", "in the old version"), ("on the right side", "in the new version"),
               ("on the right", "in the new version"), ("the left side", "the old version"),
               ("the right side", "the new version"))


def _old_new(text: str) -> str:
    """semantic_parity's left/right wording as old/new."""
    for a, b in _SIDE_WORDS:
        text = text.replace(a, b)
    return text


def _change(what: str, detail: str, redacted: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"what": what, "detail": _old_new(detail), "detail_redacted": _old_new(detail if redacted is None else redacted),
            **extra}


# ---- reading one version

def _parsed(value: Any) -> Any:
    """Text as JSON (a model.bim or JSON Ossie) or YAML, with semantic_parity's and Ossie's caps."""
    if not isinstance(value, str):
        return value
    if value.lstrip("﻿ \t\r\n").startswith("{"):
        parsed = sp._json_text(value)
        if isinstance(parsed, dict) and sp._detect(parsed) == "ossie" and len(value) > mo.MAX_CHARS:
            raise ValueError(f"This file is {len(value) // 1_000_000} MB, but an Ossie model is at most "
                             f"{mo.MAX_CHARS // 1_000_000} MB")
        return parsed
    return mo.load_text(value)


def _refusal(value: Any) -> str:
    """Why version compare won't read ``value`` (an Ossie version the converter doesn't read), or ""."""
    if not isinstance(value, dict) or sp._detect(value) != "ossie":
        return ""
    from . import ossie_profiles

    profile = ossie_profiles.detect_profile(value)
    meta = ossie_profiles.PROFILES[profile]
    if meta["convertible"]:
        return ""
    return (f"This file is {meta['label']}. Version compare reads Ossie {mo.OSSIE_VERSION}, TMSL and TMDL, because the "
            "converter reads Ossie 0.2.0.dev0 only; converting between Ossie versions comes later (OSSIE-012c).")


def _read(which: str, value: Any) -> tuple[sp._Side, str]:
    """One version as semantic_parity's structural view, or the reason it can't be read or is refused."""
    side = sp._Side(which)
    try:
        parsed = _parsed(value)
        refused = _refusal(parsed)
        if refused:
            return side, refused
        sp._read(side, parsed, None)
    except sp._READ_ERRORS as exc:  # narrow: what a hostile or wrong input raises; the reason is the report
        side.error = f"{type(exc).__name__}: {mo._short(exc)}"
    return side, ""


class _Raw:
    """The TMSL objects behind a side's columns and measures, found through its tables' raw definitions."""

    def __init__(self, side: sp._Side) -> None:
        self.columns: dict[tuple[str, str], dict[str, Any]] = {}
        self.measures: dict[tuple[str, str], dict[str, Any]] = {}
        self.measures_by_name: dict[str, dict[str, Any]] = {}
        for e in side.entries:
            if e["kind"] not in ("table", "calculation_group") or not e["raw"]:
                continue
            table = e["key"][1]
            for c in mo._named_items(e["raw"].get("columns")):
                self.columns.setdefault((table, c["name"]), c)
            for m in mo._named_items(e["raw"].get("measures")):
                self.measures.setdefault((table, m["name"]), m)
                self.measures_by_name.setdefault(m["name"], m)

    def of(self, entry: dict[str, Any]) -> dict[str, Any]:
        if entry["kind"] == "column":
            return self.columns.get((entry["key"][1], entry["key"][2]), {})
        if entry["kind"] == "measure":
            home = entry["attrs"].get("home")
            hit = self.measures.get((home, entry["key"][1])) if home is not None else None
            return hit or self.measures_by_name.get(entry["key"][1], {})
        return entry["raw"]


# ---- what changed in one object

def _norm(text: str) -> str:
    """Expression text with line ends, trailing spaces and surrounding blank lines left out."""
    return "\n".join(line.rstrip() for line in mo._text(text).strip().split("\n"))


def _format(raw: dict[str, Any]) -> str:
    fmt = raw.get("formatString")
    dynamic = raw.get("formatStringDefinition")
    shown = "" if fmt is None else str(fmt)
    if dynamic is not None:
        shown += " | dynamic " + mo._canon(dynamic)
    return shown


def _property_changes(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Format string and description, for any kind that has them."""
    out = []
    fa, fb = _format(a), _format(b)
    if fa != fb:
        out.append(_change("format", f"format string changed from {mo._shown(fa)} to {mo._shown(fb)}",
                           f"format string changed ({len(fa)} to {len(fb)} characters)"))
    da, db = mo._text(a.get("description")).strip(), mo._text(b.get("description")).strip()
    if da != db:
        out.append(_change("description", "description " + ("added" if not da else "removed" if not db else "changed"),
                           None))
    return out


def _expression_changes(a: dict[str, Any], b: dict[str, Any], left: sp._Side, right: sp._Side,
                        names: tuple[frozenset[str], frozenset[str]]) -> list[dict[str, Any]]:
    """A measure's expression: which dialects' text changed, and whether L2's canonical form changed with it. A
    dialect written on one side only (an export adds SQL beside the DAX) is a change only when the forms differ."""
    da = {k: _norm(v) for k, v in ((a.get("expr") or {}).get("dialects") or {}).items()}
    db = {k: _norm(v) for k, v in ((b.get("expr") or {}).get("dialects") or {}).items()}
    changed = sorted(d for d in da.keys() & db.keys() if da[d] != db[d])
    one_side = sorted(da.keys() ^ db.keys())
    if not changed and not one_side:
        return []
    pa, pb = sp._side_form(left, a, names[0]), sp._side_form(right, b, names[1])
    forms = {"old": None if pa["form"] is None else sp._form_text(pa["form"]),
             "new": None if pb["form"] is None else sp._form_text(pb["form"])}
    what = ", ".join(mo._short(d) for d in changed[:5]) + " text changed" if changed else \
        "dialects changed: " + ", ".join(f"{mo._short(d)} {'removed' if d in da else 'added'}" for d in one_side[:5])
    if pa["form"] is not None and pb["form"] is not None:
        lines = sp._form_changes(pa["form"], pb["form"])
        if lines:
            return [_change("expression", f"{what}: " + "; ".join(lines), forms=forms)]
        if not changed:
            return []
        return [_change("expression text", f"{what}; the canonical form (L2) is the same", forms=forms)]
    why = "; ".join(f"{s} version: {p['reason']}" for s, p in (("old", pa), ("new", pb)) if p["form"] is None)
    return [_change("expression", f"{what}; not compared as a form ({why})", forms=forms)]


def _calc_changes_redacted(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """``model_ossie._calc_changes`` with no expression text: what changed, by item name."""
    a, b = mo._calc_items(before), mo._calc_items(after)
    out = [f"calculation item {mo._q(n)} is gone" for n in a if n not in b]
    out += [f"calculation item {mo._q(n)} was added" for n in b if n not in a]
    for n in (n for n in a if n in b):
        for i, what in enumerate(("expression", "ordinal", "format string expression")):
            if a[n][i] != b[n][i]:
                out.append(f"calculation item {mo._q(n)} {what} changed")
    return out


def _object_changes(a: dict[str, Any], b: dict[str, Any], left: sp._Side, right: sp._Side, raws: tuple[_Raw, _Raw],
                    names: tuple[frozenset[str], frozenset[str]]) -> list[dict[str, Any]]:
    kind = a["kind"]
    ra, rb = raws[0].of(a), raws[1].of(b)
    out: list[dict[str, Any]] = []
    if a["cls"] == "unsupported" or b["cls"] == "unsupported":
        return out  # the converter couldn't carry it: only its presence is known
    if a["kind"] != b["kind"]:
        out.append(_change("kind", f"a {a['kind'].replace('_', ' ')} in the old version, a "
                                   f"{b['kind'].replace('_', ' ')} in the new version"))
        return out
    notes: list[str] = []
    if kind == "role":
        exact, safe = mo._role_changes(ra, rb), sp._role_changes_redacted(ra, rb)
        out += [_change("permissions", line, safe[i] if i < len(safe) else "a permission changed")
                for i, line in enumerate(exact)]
    elif kind == "calculation_group":
        exact, safe = mo._calc_changes(ra, rb), _calc_changes_redacted(ra, rb)
        out += [_change("calculation item", line, safe[i]) for i, line in enumerate(exact)]
    elif kind == "column":
        for line in sp._changes(kind, a, b, left, right, notes):
            out.append(_change("type" if line.startswith("data type") else "key", line))
        ea, eb = _norm(ra.get("expression", "")), _norm(rb.get("expression", ""))
        if ea != eb:
            out.append(_change("expression", f"calculated column expression changed ({len(ea)} to {len(eb)} characters)"))
    elif kind == "measure":
        out += [_change("home table", line) for line in sp._changes(kind, a, b, left, right, notes)]
        out += _expression_changes(a, b, left, right, names)
    else:
        what = {"relationship": "relationship", "hierarchy": "levels", "perspective": "members"}.get(kind, "structure")
        out += [_change(what, line) for line in sp._changes(kind, a, b, left, right, notes)]
    return out + _property_changes(ra, rb)


# ---- the report

def _pairs(left: sp._Side, right: sp._Side) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    """Every compared object of both versions once, matched as L1 matches: relationships by name, then by key."""
    keep = [e for e in left.entries if e["kind"] in KINDS], [e for e in right.entries if e["kind"] in KINDS]
    by_key: dict[str, collections.deque[dict[str, Any]]] = {}
    by_name: dict[str, collections.deque[dict[str, Any]]] = {}
    for e in keep[1]:
        by_key.setdefault(mo._canon(e["key"]), collections.deque()).append(e)
        name = e["attrs"].get("name") if e["kind"] == "relationship" else None
        if isinstance(name, str) and name:
            by_name.setdefault(name, collections.deque()).append(e)
    used: set[int] = set()
    match: dict[int, dict[str, Any] | None] = {}
    for e in keep[0]:
        name = e["attrs"].get("name") if e["kind"] == "relationship" else None
        if isinstance(name, str) and name:
            match[id(e)] = sp._claim(by_name.get(name), used)
    for e in keep[0]:
        if match.get(id(e)) is None:
            match[id(e)] = sp._claim(by_key.get(mo._canon(e["key"])), used)
    return [(e, match[id(e)]) for e in keep[0]] + [(None, e) for e in keep[1] if id(e) not in used]


def _info(side: sp._Side, refused: str) -> dict[str, Any]:
    return {"format": side.format, "version": side.version, "profile": side.profile,
            "label": side.label if side.kind else "", "read": not side.error and not refused,
            "error": side.error, "refused": refused}


def _empty_counts() -> dict[str, dict[str, int]]:
    return {k: dict.fromkeys((ADDED, REMOVED, CHANGED, "unchanged"), 0) for k in KINDS}


def _summary(by_kind: dict[str, dict[str, int]]) -> dict[str, Any]:
    totals = {s: sum(c[s] for c in by_kind.values()) for s in (ADDED, REMOVED, CHANGED, "unchanged")}
    parts = [f"{totals[s]} {s}" for s in (ADDED, REMOVED, CHANGED)]
    text = "No changes" if not any(totals[s] for s in (ADDED, REMOVED, CHANGED)) else ", ".join(parts)
    return {**totals, "by_kind": by_kind, "text": f"{text}; {totals['unchanged']} unchanged"}


def _report(sides: tuple[sp._Side, sp._Side], refused: tuple[str, str], objects: list[dict[str, Any]],
            by_kind: dict[str, dict[str, int]], error: str = "", notes: list[str] | None = None) -> dict[str, Any]:
    look_at = [{"id": o["id"], "kind": o["kind"], "status": o["status"], "why": _LOOK_AT_WHY[o["kind"]]}
               for o in objects if o["security"]]
    status = "refused" if any(refused) else "failed" if error else "compared"
    return {"status": status, "error": error, "summary": _summary(by_kind),
            "sides": {"old": _info(sides[0], refused[0]), "new": _info(sides[1], refused[1])},
            "objects": objects, "unchanged": {k: c["unchanged"] for k, c in by_kind.items()},
            "look_at": look_at, "notes": notes or [], "not_checked": [dict(n) for n in NOT_CHECKED]}


def compare_versions(old: Any, new: Any) -> dict[str, Any]:
    """What changed from ``old`` to ``new`` (module docstring). Never raises for bad input: ``status`` is
    "refused" or "failed" with the reason in ``error``, and nothing is listed."""
    (left, ref_l), (right, ref_r) = _read("left", old), _read("right", new)
    sides, refused = (left, right), (ref_l, ref_r)
    problems = [f"the {w} version {'is refused' if r else 'could not be read'}: {r or s.error}"
                for w, s, r in (("old", left, ref_l), ("new", right, ref_r)) if r or s.error]
    if not problems:
        problems += [f"the {w} version: {s.unsupported}" for w, s in (("old", left), ("new", right)) if s.unsupported]
    if problems:
        return _report(sides, refused, [], _empty_counts(), "; ".join(problems))
    try:
        return _compare(left, right)
    except sp._SHAPE_ERRORS as exc:  # narrow: a shape the comparison trips on is a failed report, never a crash
        reason = f"the comparison couldn't be completed: {type(exc).__name__}: {mo._short(exc)}"
        return _report(sides, refused, [], _empty_counts(), reason)


def _compare(left: sp._Side, right: sp._Side) -> dict[str, Any]:
    raws = (_Raw(left), _Raw(right))
    names = (sp._measure_names(left), sp._measure_names(right))
    by_kind = _empty_counts()
    ids = mo._Objects()
    objects: list[dict[str, Any]] = []
    carried_by_name = 0
    for a, b in _pairs(left, right):
        e = a or b
        assert e is not None
        kind = e["kind"] if a is None or b is None or a["kind"] == b["kind"] else b["kind"]
        if a is None or b is None:
            status, changes = (ADDED if a is None else REMOVED), []
        else:
            changes = _object_changes(a, b, left, right, raws, names)
            status = CHANGED if changes else ""
            carried_by_name += a["cls"] == "unsupported" or b["cls"] == "unsupported"
        if not status:
            by_kind[kind]["unchanged"] += 1
            continue
        by_kind[kind][status] += 1
        objects.append({"id": ids.add(kind, e["id"], "fully_preserved")["object"], "kind": kind, "status": status,
                        "changes": changes, "security": kind in SECURITY})
    notes = [f"{carried_by_name} object(s) the converter couldn't carry were compared by name only"] \
        if carried_by_name else []
    return _report((left, right), ("", ""), objects, by_kind, notes=notes)


def redacted(report: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``report`` safe outside the local pane: every change line is its redacted form (a role's filters
    as a length and hash, its members and permissions as counts, a calculation item or format string without its
    text), and expression forms are a length and hash. Names stay, cut short as every report cuts them. The
    report itself is unchanged."""
    out = copy.deepcopy(report)
    for o in out.get("objects") or []:
        for c in o.get("changes") or []:
            c["detail"] = c.pop("detail_redacted", None) or f"{c.get('what', 'a property')} changed"
            if isinstance(c.get("forms"), dict):
                c["forms"] = {k: v if v is None else sp._fingerprint(str(v)) for k, v in c["forms"].items()}
    errors = []
    for which, side in (out.get("sides") or {}).items():
        if side.get("error"):  # a read error can quote the input: outside the pane only its type stays
            side["error"] = str(side["error"]).split(":", 1)[0]
            errors.append(f"the {which} version could not be read: {side['error']}")
        elif side.get("refused"):
            errors.append(f"the {which} version is refused: {side['refused']}")
    if out.get("error"):
        out["error"] = "; ".join(errors) or "the comparison couldn't be completed"
    out["redacted"] = True
    return out


__all__ = ["ADDED", "CHANGED", "KINDS", "NOT_CHECKED", "REMOVED", "compare_versions", "redacted"]
