"""Security roles withheld from what the AI tools (integrations) get back from Export and Import preview.

A role's filter expressions and members say who may see which rows. The app's own Semantic model tab shows them
in full; a tool outside KratosForge (``fabric_desktop_export_semantic_model_ossie``,
``fabric_desktop_preview_ossie_import``) gets each filter and each member's name and id as a length and
keyed fingerprint instead (``keyed_fingerprint``), so it can still see that a role restricts a table, and that two
filters differ, without reading how or who. Role names, model permissions and which tables have a filter stay.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from . import model_ossie as mo
from .semantic_parity import _fingerprint, _role_changes_redacted

NOTE = ("Role filters and members were withheld for this caller: each row-level security filter expression and "
        "each member's name and id is shown as its length and a short hash, here and in the YAML. Role names, "
        "model permissions and which tables have a filter are kept. KratosForge's Semantic model tab shows them "
        "in full.")
_CHANGED = "This security role came through changed: "
MIN_SCRUBBED = 6  # a shorter value is too common to replace everywhere it appears; the role rows never quote it


def _stash_roles(doc: Any) -> list[dict[str, Any]]:
    return mo._named_items(mo._stash_data(doc).get("roles")) if isinstance(doc, dict) else []


def _model_roles(bim: Any) -> list[dict[str, Any]]:
    model = bim.get("model") if isinstance(bim, dict) else None
    return mo._named_items(model.get("roles")) if isinstance(model, dict) else []


def _pairs(source: Any, destination: Any) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Each role object's id in the fidelity report, with the two roles ``model_ossie._role_changes`` compared."""
    out: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    if isinstance(source, dict) and isinstance(source.get("model"), dict):  # Export: the model to its file
        after = {r["name"]: r for r in _stash_roles(destination)}
        befores = _model_roles(source)
    else:  # Import: the file to the model
        after = {}
        for r in _model_roles(destination):
            after.setdefault(r["name"], r)
        befores = _stash_roles(source)
    for r in befores:
        if r["name"] in after:
            out.setdefault(f"role {mo._q(r['name'])}", (r, after[r["name"]]))
    return out


def _sensitive(roles: list[dict[str, Any]]) -> set[str]:
    """The filter texts and member names and ids of ``roles``."""
    out: set[str] = set()
    for role in roles:
        shape = mo._role_shape(role)
        out |= {text for _, text in shape["filters"]}
        out |= {part for name, member_id, _ in shape["members"] for part in (name, member_id)}
    return {s for s in out if len(s.strip()) >= MIN_SCRUBBED}


def _needles(values: set[str]) -> list[tuple[str, str]]:
    """Each value with the forms a message may quote it in (as written, as repr and JSON write it, cut at 80
    characters as ``model_ossie._shown`` cuts it), longest first, each with its replacement."""
    out: dict[str, str] = {}
    for v in values:
        mark = f"<withheld: {_fingerprint(v)}>"
        for form in (v, v[:80], repr(v)[1:-1], repr(v[:80])[1:-1], json.dumps(v)[1:-1]):
            if len(form.strip()) >= MIN_SCRUBBED:
                out.setdefault(form, mark)
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def _scrub(value: Any, needles: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for form, mark in needles:
            if form in value:
                value = value.replace(form, mark)
        return value
    if isinstance(value, dict):
        return {_scrub(k, needles): _scrub(v, needles) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, needles) for v in value]
    return value


def _withheld(row: dict[str, Any]) -> dict[str, Any]:
    kept = {k: row[k] for k in ("object", "kind", "check", "severity", "prominent") if k in row}
    return kept | {"withheld": _fingerprint(mo._canon(row))}


def _collection_row(row: Any) -> bool:
    """The converter's row about the whole roles collection (it names the collection, never a role's content)."""
    return isinstance(row, dict) and row.get("object") == "model" and "('roles')" in str(row.get("message", ""))


def _about_role(row: Any) -> bool:
    return isinstance(row, dict) and str(row.get("object", "")).startswith("role")


def fidelity(report: dict[str, Any], source: Any, destination: Any) -> dict[str, Any]:
    """A copy of a ``model_ossie.fidelity`` report of ``source`` converted to ``destination`` that quotes no role
    filter or member: each role's changes as ``semantic_parity`` writes them for a redacted parity report (a
    filter as a length and hash, members and permissions as counts), the security warning built from those, and
    any evidence or warning row about a role withheld (a length and hash of it), except the converter's row about
    the roles collection. Any other text that quotes a filter or member of either side is replaced too."""
    out = copy.deepcopy(report)
    pairs = _pairs(source, destination)
    changes: dict[str, list[str]] = {}
    for o in out.get("objects") or []:
        if o.get("kind") == "role":
            pair = pairs.get(o.get("object", ""))
            if o.get("changes"):
                o["changes"] = _role_changes_redacted(*pair) if pair else [f"{len(o['changes'])} change(s) withheld"]
            changes[o.get("object", "")] = list(o.get("changes") or [])
            o["evidence"] = [e if _collection_row(e) else _withheld(e) for e in o.get("evidence") or []]
        else:
            o["evidence"] = [_withheld(e) if _about_role(e) else e for e in o.get("evidence") or []]
    if "metadata_changes" in out:  # as fidelity builds it, from the objects' changes and notes
        objs = out.get("objects") or []
        out["metadata_changes"] = ([{"object": o["object"], "change": c} for o in objs for c in o.get("changes") or []]
                                   + [{"object": o["object"], "change": n, "informational": True}
                                      for o in objs for n in o.get("notes") or []])
    warnings = []
    for w in out.get("warnings") or []:
        if isinstance(w, dict) and w.get("object") in changes and str(w.get("message", "")).startswith(_CHANGED):
            warnings.append(w | {"message": _CHANGED + "; ".join(changes[w["object"]])})
        elif _about_role(w) and not str(w.get("message", "")).startswith("This security role "):
            warnings.append(_withheld(w))
        else:
            warnings.append(w)
    out["warnings"] = warnings
    roles = [r for pair in pairs.values() for r in pair] + _model_roles(source) + _model_roles(destination) \
        + _stash_roles(source) + _stash_roles(destination)
    return _scrub(out, _needles(_sensitive(roles)))


def _member(m: Any) -> Any:
    if not isinstance(m, dict):
        return m
    return {k: _fingerprint(str(v)) if k in ("memberName", "name", "memberId") else v for k, v in m.items()}


def _role(role: dict[str, Any]) -> dict[str, Any]:
    out = dict(role)
    if isinstance(role.get("tablePermissions"), list):
        out["tablePermissions"] = [
            p | {"filterExpression": _fingerprint(mo._text(p["filterExpression"]))}
            if isinstance(p, dict) and "filterExpression" in p else p for p in role["tablePermissions"]]
    if isinstance(role.get("members"), list):
        out["members"] = [_member(m) for m in role["members"]]
    return out


def document(doc: dict[str, Any]) -> dict[str, Any]:
    """A copy of an Ossie document whose roles (in its POWER_BI extension) keep their names, model permission and
    which tables have a filter, with each filter expression and each member's name and id a length and hash."""
    out = copy.deepcopy(doc)
    for e in out.get("custom_extensions") or []:
        if not (isinstance(e, dict) and e.get("vendor_name") == "POWER_BI"):
            continue
        data = mo._stash_data(out)
        if isinstance(data.get("roles"), list):
            data["roles"] = [_role(r) if isinstance(r, dict) else r for r in data["roles"]]
            e["data"] = json.dumps(data, ensure_ascii=False)
        break
    return out


def export_result(out: dict[str, Any], bim: dict[str, Any]) -> dict[str, Any]:
    """Export's answer for an AI tool: the YAML's roles as ``document`` leaves them, the fidelity report as
    ``fidelity`` leaves it, nothing else quoting a filter or member, and ``note`` saying so."""
    doc = mo.load_own(out["yaml"])
    result = dict(out)
    result["fidelity"] = fidelity(out["fidelity"], bim, doc)
    needles = _needles(_sensitive(_model_roles(bim) + _stash_roles(doc)))
    result = {k: v if k == "fidelity" else _scrub(v, needles) for k, v in result.items() if k != "yaml"}
    return {"yaml": mo.dump_yaml(document(doc)), **result, "note": NOTE}


def preview_result(out: dict[str, Any], doc: Any, incoming: Any) -> dict[str, Any]:
    """Import preview's answer for an AI tool: the fidelity report as ``fidelity`` leaves it, nothing else quoting
    a filter or member of the file's roles (or the converted model's), and ``note`` saying so."""
    result = dict(out)
    if isinstance(out.get("fidelity"), dict):
        result["fidelity"] = fidelity(out["fidelity"], doc, incoming)
    needles = _needles(_sensitive(_stash_roles(doc) + _model_roles(incoming)))
    return {**{k: v if k == "fidelity" else _scrub(v, needles) for k, v in result.items()}, "note": NOTE}
