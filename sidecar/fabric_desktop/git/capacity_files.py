"""Capacity definitions as a file: a capacity repository's drift report (repositories by scope, stage 4).

``capacity.toml`` says which capacities there should be, the settings each should have and which
workspaces should sit on each. The drift report compares it with what Fabric shows the signed-in
person (``GET /v1/capacities`` and ``GET /v1/workspaces``) and writes nothing. Writing Fabric's
state into the file is the only write, and it goes to the repository as a change to review.
Nothing here changes a capacity or moves a workspace.

People see different capacities and workspaces, so one Fabric doesn't show them is reported as
"not visible", never as drift: missing from a read says nothing about whether it changed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .cli import GitError
from ..redact import one_line
from .platform_files import dumps
from .scopes import _replace_in

FILE = "capacity.toml"
FIELDS = ("sku", "region", "state")  # compared when the file sets them; left out means "don't care"
HEADER = ("KratosForge capacity definitions: the capacities, their settings and the workspaces each\n"
          "should hold. The drift report compares this with Fabric; it never changes a capacity.\n"
          "Ids and names only. A setting left out isn't compared.")
_MAX = 256


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _ok_text(v: str) -> bool:
    return len(v) <= _MAX and not any(ord(c) < 32 or ord(c) == 127 for c in v)


def read(root: str | Path) -> dict[str, Any]:
    """The file's capacities: {"capacities": [{name, capacity_id, sku?, region?, state?,
    workspaces: [{id, name}]}], "problems": [...]}. A section the app can't use is reported, not used."""
    problems: list[str] = []
    path = Path(root) / FILE
    if path.is_symlink():
        return {"capacities": [], "problems": [f"{FILE} is a link, so it wasn't read."]}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"capacities": [], "problems": []}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return {"capacities": [], "problems": [f"{FILE} can't be read: {one_line(exc)}"]}
    sections = data.get("capacities") or {}
    if not isinstance(sections, dict):
        return {"capacities": [], "problems": [f"{FILE}: capacities must be a table of [capacities.<name>] sections."]}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, sec in sections.items():
        if not isinstance(sec, dict):
            problems.append(f"{FILE}: capacities.{one_line(name, 80)} isn't a section.")
            continue
        cid = _text(sec.get("capacity_id"))
        if not cid or not _ok_text(cid) or not _ok_text(name):
            problems.append(f"{FILE}: capacities.{one_line(name, 80)} needs a capacity_id.")
            continue
        if cid.lower() in seen:
            problems.append(f"{FILE}: capacity {cid} is listed twice; only the first is used.")
            continue
        seen.add(cid.lower())
        cap: dict[str, Any] = {"name": name, "capacity_id": cid, "workspaces": []}
        for f in FIELDS:
            if f in sec:
                v = _text(sec[f])
                if v and _ok_text(v):
                    cap[f] = v
                else:
                    problems.append(f"{FILE}: capacities.{name}.{f} must be text.")
        for w in sec.get("workspaces") or []:
            if isinstance(w, str):  # a bare id is fine too
                wid, wname = w.strip(), ""
            elif isinstance(w, dict):
                wid, wname = _text(w.get("id")), _text(w.get("name"))
            else:
                wid, wname = "", ""
            if wid and _ok_text(wid) and _ok_text(wname):
                cap["workspaces"].append({"id": wid, "name": wname})
            else:
                problems.append(f"{FILE}: a workspace under capacities.{name} needs an id.")
        out.append(cap)
    return {"capacities": out, "problems": problems}


def from_fabric(capacities: list[dict[str, Any]], workspaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What Fabric shows, in the file's shape (every field set), sorted by name."""
    on: dict[str, list[dict[str, str]]] = {}
    for w in workspaces:
        cid = _text(w.get("capacityId")).lower()
        if cid:
            on.setdefault(cid, []).append({"id": _text(w.get("id")), "name": _text(w.get("displayName"))})
    out = []
    for c in capacities:
        cid = _text(c.get("id"))
        if not cid:
            continue
        out.append({"name": _text(c.get("displayName")) or cid, "capacity_id": cid,
                    "sku": _text(c.get("sku")), "region": _text(c.get("region")), "state": _text(c.get("state")),
                    "workspaces": sorted(on.get(cid.lower(), []), key=lambda w: (w["name"].lower(), w["id"]))})
    return sorted(out, key=lambda c: (c["name"].lower(), c["capacity_id"]))


def compare(git: list[dict[str, Any]], fabric: list[dict[str, Any]], workspaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item per capacity, in the file's order then Fabric's: state same, differs, not_visible
    (in the file, not shown to this person) or not_in_git, with the settings that differ and where
    the file's workspaces really are."""
    fab = {c["capacity_id"].lower(): c for c in fabric}
    where = {_text(w.get("id")).lower(): _text(w.get("capacityId")).lower() for w in workspaces if _text(w.get("id"))}
    in_git = {w["id"].lower() for c in git for w in c["workspaces"]}
    items = []
    for c in git:
        cid = c["capacity_id"].lower()
        f = fab.get(cid)
        if f is None:
            items.append({"capacity_id": c["capacity_id"], "name": c["name"], "state": "not_visible",
                          "fields": [], "workspaces": {"same": 0, "elsewhere": [], "not_visible": [], "not_in_git": []}})
            continue
        fields = [{"field": k, "git": c[k], "fabric": f[k]} for k in FIELDS if k in c and c[k].lower() != f[k].lower()]
        ws: dict[str, Any] = {"same": 0, "elsewhere": [], "not_visible": [], "not_in_git": []}
        for w in c["workspaces"]:
            now = where.get(w["id"].lower())
            if now is None:
                ws["not_visible"].append(w)
            elif now == cid:
                ws["same"] += 1
            else:
                other = fab.get(now)
                ws["elsewhere"].append(w | {"on": other["name"] if other else None})
        ws["not_in_git"] = [w for w in f["workspaces"] if w["id"].lower() not in in_git]
        drift = fields or ws["elsewhere"] or ws["not_in_git"]
        items.append({"capacity_id": c["capacity_id"], "name": c["name"], "fabric_name": f["name"],
                      "state": "differs" if drift else "same", "fields": fields, "workspaces": ws})
    listed = {c["capacity_id"].lower() for c in git}
    for f in fabric:
        if f["capacity_id"].lower() not in listed:
            items.append({"capacity_id": f["capacity_id"], "name": f["name"], "state": "not_in_git", "fields": [],
                          "workspaces": {"same": 0, "elsewhere": [], "not_visible": [], "not_in_git": f["workspaces"]}})
    return items


def summary(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"same": 0, "differs": 0, "not_visible": 0, "not_in_git": 0}
    for i in items:
        out[i["state"]] += 1
    return out


def _section(c: dict[str, Any]) -> dict[str, Any]:
    return {"capacity_id": c["capacity_id"], **{f: c[f] for f in FIELDS if c.get(f)},
            "workspaces": [{"id": w["id"], "name": w["name"]} for w in c["workspaces"]]}


def write(root: str | Path, git: list[dict[str, Any]], fabric: list[dict[str, Any]], uncommitted: bool,
          confirm: bool = False, workspaces: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Replaces capacity.toml with what Fabric shows. What this person can't see stays as it is:
    sections for capacities they can't see, and the file's workspaces they can't see (``workspaces``
    is everything Fabric shows them). State is written only where the file already sets it, since a
    pause or resume would otherwise read as drift. With uncommitted changes, only with ``confirm``."""
    root = Path(root)
    if (root / FILE).is_symlink():
        raise GitError(f"{FILE} is a link, so it wasn't written.", kind="invalid")
    seen = {_text(w.get("id")).lower() for w in workspaces or []}
    by_id = {c["capacity_id"].lower(): c for c in git}
    shown = {c["capacity_id"].lower() for c in fabric}
    kept = [c for c in git if c["capacity_id"].lower() not in shown]
    hidden: list[dict[str, str]] = []
    merged = []
    for c in fabric:
        before = by_id.get(c["capacity_id"].lower()) or {}
        unseen = [w for w in before.get("workspaces", []) if w["id"].lower() not in seen]
        hidden += unseen
        merged.append(c | {"state": c["state"] if "state" in before else "", "workspaces": c["workspaces"] + unseen})
    sections: dict[str, Any] = {}
    for c in [*merged, *kept]:
        name = c["name"] if c["name"] not in sections else f"{c['name']} ({c['capacity_id']})"
        sections[name] = _section(c)
    text = dumps({"capacities": sections}, HEADER) if sections else "\n".join(f"# {x}" for x in HEADER.splitlines()) + "\n"
    try:
        same = (root / FILE).read_text(encoding="utf-8") == text
    except (OSError, UnicodeDecodeError):
        same = False
    if not same and uncommitted and not confirm:
        err = GitError(f"{FILE} has changes that aren't committed. Confirm to replace them with what Fabric "
                       "shows, or commit them first.", kind="confirm")
        err.changes = [FILE]  # type: ignore[attr-defined]
        raise err
    if not same:
        _replace_in(root, FILE, text)
    return {"written": [] if same else [FILE], "capacities": len(fabric), "kept": [c["name"] for c in kept],
            "kept_workspaces": [w["name"] or w["id"] for w in hidden],
            "workspaces": sum(len(c["workspaces"]) for c in fabric)}
