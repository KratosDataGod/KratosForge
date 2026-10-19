"""Platform definitions as files: a platform repository's export and diff (repositories by scope, stage 3).

Git is the source of truth for platform definitions; each environment's platform holds what is
deployed. Export writes what is deployed as one TOML file per object, as changes to review: nothing
is committed and nothing in Fabric changes. Diff compares the files with one environment's platform
and writes nothing. The file shapes are in docs/source-control.md (Platform repository).

Only content goes in a file: no times, no who-changed-it, no key values, requests, grants, audit,
readers, shortcut health, connections or anything secret.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Callable

from ..central.core import PlatformError
from ..central.policy import clean_definition
from .cli import GitError
from .scopes import _replace_in

# Each kind of object: its folder (or single file), in the order a deploy would apply them.
FOLDERS = {"domains": "domains", "sources": "sources", "key_tables": "key_tables", "policies": "policies"}
FILES = {"admins": "admins.toml", "glossary": "glossary.toml", "shortcut_rules": "shortcut_rules.toml"}
ORDER = ("admins", "domains", "sources", "key_tables", "glossary", "shortcut_rules", "policies")
LABELS = {"admins": "Admins", "domains": "Domain", "sources": "Source system", "key_tables": "Key table",
          "glossary": "Glossary term", "shortcut_rules": "Shortcut rules", "policies": "Policy"}
# A file name made from an object's identity: lowercase letters, digits, - _ and ., starting with a
# letter or digit. Anything else is reported, never written.
_FILE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


# --------------------------------------------------------------------------- records
# One shape per kind, made the same way from the platform's rows and from a file, so comparing
# two records is plain equality.


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _people(v: Any) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,;]", _s(v))
    return sorted({_s(x) for x in items if _s(x)}, key=str.lower)


def _sorted(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _sorted(v[k]) for k in sorted(v)}
    if isinstance(v, list):
        return [_sorted(x) for x in v]
    return v


def domain_record(d: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {"id": _s(d.get("id"))}
    linked = _s(d.get("fabric_domain_id", d.get("fabric_domain")))
    if linked:  # Fabric owns its name and parent; we keep the link and our own fields
        rec["fabric_domain_id"] = linked
    else:
        rec["name"] = _s(d.get("name"))
        rec["parent"] = _s(d.get("parent", d.get("parent_id")))
    rec["description"] = _s(d.get("description"))
    rec["owners"] = _people(d.get("owners"))
    return rec


def source_record(s: dict[str, Any]) -> dict[str, Any]:
    sid = s.get("source_id")
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        sid = _s(sid)  # kept as written, so the diff shows it and a deploy refuses it
    active = s.get("active", True)
    return {"source_id": sid, "code": _s(s.get("code")) or _s(s.get("name")).upper(), "title": _s(s.get("title")),
            "description": _s(s.get("description")), "owner": _s(s.get("owner")),
            "system_type": _s(s.get("system_type")), "active": active is not False and active != "0",
            "expected_hours": _s(s.get("expected_hours"))}


def key_table_record(t: dict[str, Any]) -> dict[str, Any]:
    return {"name": _s(t.get("name")), "business_key": _s(t.get("business_key")),
            "description": _s(t.get("description")), "domain": _s(t.get("domain", t.get("domain_id")))}


def term_record(g: dict[str, Any]) -> dict[str, Any]:
    return {"term": _s(g.get("term")), "definition": _s(g.get("definition")),
            "domain": _s(g.get("domain", g.get("domain_id"))), "owner": _s(g.get("owner"))}


def _assign_record(a: dict[str, Any]) -> dict[str, Any]:
    return {"scope": _s(a.get("scope")), "target": _s(a.get("target")) or ("*" if a.get("scope") == "enterprise" else ""),
            "note": _s(a.get("note")), "settings": _sorted(dict(a.get("settings") or {}))}


def policy_record(p: dict[str, Any]) -> dict[str, Any]:
    """From get_policy's result (the current published version) or from a file. Raises
    PlatformError when the definition isn't valid."""
    definition = p.get("definition")
    if definition is None:
        definition = (p.get("current") or {}).get("definition") or {}
    assigns = [_assign_record(a) for a in (p.get("assign", p.get("assignments")) or [])]
    return {"id": _s(p.get("id")), "status": _s(p.get("status")) or "published",
            "definition": _sorted(clean_definition(definition)),
            "assign": sorted(assigns, key=lambda a: (a["scope"], a["target"]))}


def from_platform(raw: dict[str, Any]) -> dict[str, Any]:
    """What a platform has deployed, as records. ``raw`` holds the platform's own reads:
    domains, sources, key_tables ({env: list}), glossary, admins, shortcut_rules and policies
    (get_policy results; None when the platform has no policy tables)."""
    out: dict[str, Any] = {"domains": {}, "sources": {}, "key_tables": {}, "glossary": {}, "policies": {},
                           "admins": sorted({_s(a.get("user_name")) for a in raw.get("admins") or []} - {""}, key=str.lower),
                           "shortcut_rules": _sorted(dict(raw.get("shortcut_rules") or {})), "skipped": [],
                           # the platform's own rows, which a deploy needs beside the records
                           "raw": {"domains": {_s(d.get("id")): d for d in raw.get("domains") or []},
                                   "sources": {_s(s.get("name")): s for s in raw.get("sources") or []},
                                   "policies": {_s(p.get("id")): p for p in raw.get("policies") or []}}}
    for d in raw.get("domains") or []:
        out["domains"][_s(d.get("id"))] = domain_record(d)
    for s in raw.get("sources") or []:
        out["sources"][_s(s.get("name")) or _s(s.get("code")).lower()] = source_record(s)
    # Key tables live in each environment: {env: list}, the catalog's environment first. Its
    # fields win, and any environment that disagrees is listed rather than silently overruled.
    out["disagree"] = []
    first_env: dict[str, str] = {}
    for env, tables in (raw.get("key_tables") or {}).items():
        for t in tables or []:
            rec = key_table_record(t)
            name = rec["name"]
            if name not in out["key_tables"]:
                out["key_tables"][name], first_env[name] = rec, env
            elif rec != out["key_tables"][name]:
                out["disagree"].append({"kind": "key_tables", "id": name, "env": env, "kept_from": first_env[name],
                                        "changes": _changes(rec, out["key_tables"][name])})
    for g in raw.get("glossary") or []:
        out["glossary"][_s(g.get("term"))] = term_record(g)
    if raw.get("policies") is None:
        out["policies"] = None  # not set up on this platform: nothing to export or compare
    for p in raw.get("policies") or []:
        if not p.get("current"):
            out["skipped"].append({"kind": "policies", "id": p.get("id"), "why": "never published"})
            continue
        try:
            out["policies"][_s(p.get("id"))] = policy_record(p)
        except PlatformError as e:  # a version this app can't read: shown, never compared or exported
            out["skipped"].append({"kind": "policies", "id": p.get("id"), "why": f"can't be read: {e}"})
    return out


# --------------------------------------------------------------------------- writing TOML


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _str(k)


def _str(v: str) -> str:
    # JSON's escapes are all valid TOML basic-string escapes; DEL is the one it leaves raw.
    return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v if x is not None) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items() if x is not None) + " }"
    raise ValueError(f"can't write {type(v).__name__} to a file")


def _table_list(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _table(lines: list[str], path: list[str], d: dict[str, Any], array: bool = False) -> None:
    if path:
        name = ".".join(_key(p) for p in path)
        lines += ["", f"[[{name}]]" if array else f"[{name}]"]
    for k, v in d.items():
        if v is not None and not isinstance(v, dict) and not _table_list(v):
            lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            _table(lines, path + [k], v)
    for k, v in d.items():
        if _table_list(v):
            for item in v:
                _table(lines, path + [k], item, array=True)


def dumps(data: dict[str, Any], header: str = "") -> str:
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]
    _table(lines, [], data)
    return "\n".join(lines).lstrip("\n") + "\n"


_HEADER = "KratosForge platform definition. Git is the source of truth; deploy applies it.\nNever put passwords, keys, webhook URLs or connection strings here."


def to_files(defs: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """The files for a set of records: {path in the repository: text}, and objects that can't be
    written (an identity that isn't a safe file name)."""
    files: dict[str, str] = {}
    refused = []
    for kind, folder in FOLDERS.items():
        for ident, rec in sorted((defs[kind] or {}).items()):
            if not _FILE_NAME.match(ident) or ".." in ident:
                refused.append({"kind": kind, "id": ident, "why": "its name can't be a file name"})
                continue
            files[f"{folder}/{ident}.toml"] = dumps(rec, _HEADER)
    files[FILES["admins"]] = dumps({"admins": defs["admins"]}, _HEADER)
    files[FILES["glossary"]] = dumps({"term": [defs["glossary"][t] for t in sorted(defs["glossary"], key=str.lower)]}, _HEADER)
    files[FILES["shortcut_rules"]] = dumps(defs["shortcut_rules"], _HEADER)
    return files, refused


# --------------------------------------------------------------------------- reading the repository


def _load(path: Path, problems: list[str], rel: str) -> dict[str, Any] | None:
    if path.is_symlink():
        problems.append(f"{rel} is a link, so it isn't read.")
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        problems.append(f"{rel} can't be read: {e}")
        return None


def read_repo(root: str | Path) -> dict[str, Any]:
    """The records a platform repository's files hold, with a problem for each file that can't be
    read or doesn't match its name. A missing single file means that kind isn't in Git yet (None)."""
    root = Path(root)
    problems: list[str] = []
    out: dict[str, Any] = {"domains": {}, "sources": {}, "key_tables": {}, "glossary": None, "policies": {},
                           "admins": None, "shortcut_rules": None}
    makers: dict[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], str]] = {
        "domains": (domain_record, "id"), "sources": (source_record, "code"),
        "key_tables": (key_table_record, "name"), "policies": (policy_record, "id")}
    for kind, folder in FOLDERS.items():
        where = root / folder
        if where.is_symlink() or not where.is_dir():
            continue
        make, field = makers[kind]
        for f in sorted(where.glob("*.toml")):
            rel = f"{folder}/{f.name}"
            data = _load(f, problems, rel)
            if data is None:
                continue
            ident = f.stem
            if _s(data.get(field)).lower() != ident:
                problems.append(f"{rel}: its {field} should be {ident!r}, the file's name.")
                continue
            try:
                out[kind][ident] = make(data)
            except PlatformError as e:
                problems.append(f"{rel}: {e}")
    for kind, name in FILES.items():
        f = root / name
        if not f.exists() and not f.is_symlink():
            continue
        data = _load(f, problems, name)
        if data is None:
            continue
        if kind == "admins":
            out[kind] = _people(data.get("admins") or [])
        elif kind == "glossary":
            out[kind] = {r["term"]: r for r in (term_record(t) for t in data.get("term") or [] if isinstance(t, dict))}
        else:
            out[kind] = _sorted(data)
    out["problems"] = problems
    return out


# --------------------------------------------------------------------------- comparing


def _changes(git: Any, deployed: Any) -> list[dict[str, Any]]:
    if isinstance(git, dict) and isinstance(deployed, dict):
        return [{"field": k, "git": git.get(k), "deployed": deployed.get(k)}
                for k in sorted(set(git) | set(deployed)) if git.get(k) != deployed.get(k)]
    return [] if git == deployed else [{"field": "", "git": git, "deployed": deployed}]


def compare(git: dict[str, Any], deployed: dict[str, Any]) -> list[dict[str, Any]]:
    """Each object in Git or deployed: same, changed (field by field), only_git (not deployed yet)
    or only_deployed (removed in Git). Kinds Git doesn't hold yet are left out."""
    items = []
    for kind in ORDER:
        g, d = git.get(kind), deployed.get(kind)
        if g is None:
            continue
        if d is None:  # e.g. no policy tables on this platform yet: everything in Git is new there
            d = [] if kind == "admins" else {}
        if kind in ("admins", "shortcut_rules"):
            if kind == "admins":
                added, gone = sorted(set(g) - set(d)), sorted(set(d) - set(g))
                changes = ([{"field": "admins", "git": added, "deployed": gone}] if added or gone else [])
            else:
                changes = _changes(g, d)
            items.append({"kind": kind, "id": FILES[kind], "state": "changed" if changes else "same", "changes": changes})
            continue
        for ident in sorted(set(g) | set(d)):
            if ident not in d:
                items.append({"kind": kind, "id": ident, "state": "only_git", "changes": []})
            elif ident not in g:
                items.append({"kind": kind, "id": ident, "state": "only_deployed", "changes": []})
            else:
                changes = _changes(g[ident], d[ident])
                items.append({"kind": kind, "id": ident, "state": "changed" if changes else "same", "changes": changes})
    return items


def summary(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"same": 0, "changed": 0, "only_git": 0, "only_deployed": 0}
    for i in items:
        out[i["state"]] += 1
    return out


# --------------------------------------------------------------------------- export


def _safe_folder(root: Path, folder: str) -> Path:
    where = root / folder
    if where.is_symlink() or (where.exists() and not where.is_dir()):
        raise GitError(f"{folder} in this repository isn't a plain folder, so nothing was exported.", kind="invalid")
    where.mkdir(exist_ok=True)
    return where


def export(root: str | Path, defs: dict[str, Any], uncommitted: set[str], confirm: bool = False) -> dict[str, Any]:
    """Writes the files for ``defs`` into the repository at ``root``. Only files whose text changes
    are written. A file with uncommitted changes is replaced only with ``confirm``; files for
    objects that aren't deployed are left as they are, never deleted."""
    root = Path(root)
    files, refused = to_files(defs)
    changed = {}
    for rel, text in files.items():
        f = root / rel
        try:
            same = not f.is_symlink() and f.read_text(encoding="utf-8") == text
        except FileNotFoundError:
            same = False
        except (OSError, UnicodeDecodeError):
            same = False
        if not same:
            changed[rel] = text
    held = sorted(rel for rel in changed if rel in uncommitted)
    if held and not confirm:
        err = GitError(f"{len(held)} of these files have changes that aren't committed: "
                       f"{', '.join(held[:5])}{' and more' if len(held) > 5 else ''}. "
                       "Confirm to replace them with what is deployed, or commit them first.", kind="confirm")
        err.changes = held  # type: ignore[attr-defined]
        raise err
    for rel, text in changed.items():
        folder, _, name = rel.rpartition("/")
        _replace_in(_safe_folder(root, folder) if folder else root, name, text)
    in_git = read_repo(root)
    left = [{"kind": k, "id": i} for k in FOLDERS for i in sorted(set(in_git[k]) - set(defs[k] or {}))]
    return {"written": sorted(changed), "unchanged": len(files) - len(changed), "left": left,
            "skipped": defs.get("skipped", []) + refused, "replaced_uncommitted": held}


# --------------------------------------------------------------------------- deploy
# A deploy applies the files to the platform as a list of steps, each one platform operation that
# checks who may do it and writes its own audit row as the deployer. The steps are worked out first
# and shown; nothing runs until the person confirms that exact plan.


def _step(kind: str, ident: str, action: str, op: str, args: dict[str, Any], env: str | None = None,
          what: str = "") -> dict[str, Any]:
    return {"kind": kind, "id": ident, "action": action, "op": op, "args": args, "env": env, "what": what}


def _domain_args(ident: str, rec: dict[str, Any], raw: dict[str, Any] | None) -> dict[str, Any]:
    if rec.get("fabric_domain_id"):  # Fabric's name and parent stay as the platform has them
        name, parent = (raw or {}).get("name"), (raw or {}).get("parent_id")
    else:
        name, parent = rec["name"], rec["parent"] or None
    return {"domain": {"id": ident, "name": name, "parent_id": parent, "description": rec["description"],
                       "owners": ", ".join(rec["owners"]), "fabric_domain": rec.get("fabric_domain_id", "")}}


def _policies_plan(git: dict[str, Any], cat: dict[str, Any], note: str, steps: list, blocked: list) -> None:
    raw_all, deployed = cat["raw"]["policies"], cat.get("policies") or {}
    for ident, rec in sorted((git.get("policies") or {}).items()):
        raw, dep = raw_all.get(ident), deployed.get(ident)
        if rec["status"] not in ("published", "deprecated", "retired"):
            blocked.append({"kind": "policies", "id": ident, "why": "status is published, deprecated or retired."})
            continue
        policy = rec["definition"] | {"id": ident, "note": note}
        if raw is None:
            steps.append(_step("policies", ident, "create", "create_policy", {"policy": policy}, what="create, as a draft"))
            steps.append(_step("policies", ident, "create", "publish_policy", {"id": ident, "note": note},
                               what="publish version 1"))
            dep = {"assign": [], "status": "published", "definition": rec["definition"]}
        elif dep is None:
            blocked.append({"kind": "policies", "id": ident, "why": "is only a draft in the platform. Publish or discard "
                            "that draft first (Platform > Governance)."})
            continue
        elif raw.get("status") == "retired":
            if rec != dep:
                blocked.append({"kind": "policies", "id": ident, "why": "is retired in the platform, so it can't change."})
            continue
        elif rec["definition"] != dep["definition"]:
            if raw.get("draft_version"):
                blocked.append({"kind": "policies", "id": ident, "why": "has a draft in progress in the platform. "
                                "Publish or discard it first, so a deploy never overwrites someone's draft."})
                continue
            steps.append(_step("policies", ident, "update", "save_policy_draft",
                               {"id": ident, "policy": rec["definition"], "note": note}, what="new draft from Git"))
            steps.append(_step("policies", ident, "update", "publish_policy", {"id": ident, "note": note},
                               what="publish it as a new version"))
        have = {(a["scope"], a["target"]): a for a in dep["assign"]}
        want = {(a["scope"], a["target"]): a for a in rec["assign"]}
        for key in sorted(set(have) - set(want)):
            steps.append(_step("policies", ident, "update", "remove_assignment",
                               {"policy": ident, "scope": key[0], "target": key[1]}, what=f"unassign {key[0]} {key[1]}"))
        for key in sorted(want):
            if have.get(key) != want[key]:
                a = want[key]
                steps.append(_step("policies", ident, "update", "save_assignment",
                                   {"policy": ident, "scope": a["scope"], "target": a["target"],
                                    "settings": a["settings"], "note": a["note"]}, what=f"assign {key[0]} {key[1]}"))
        if rec["status"] != dep["status"]:
            steps.append(_step("policies", ident, "update", "set_policy_status",
                               {"id": ident, "status": rec["status"], "note": note}, what=f"mark it {rec['status']}"))


def plan(git: dict[str, Any], envs: dict[str, dict[str, Any]], catalog_env: str, remove: set[tuple[str, str]],
         note: str) -> dict[str, Any]:
    """The steps that make the platform match the files. ``envs`` is each environment's deployed
    records (the catalog's environment among them); ``remove`` the objects only deployed that an
    admin ticked to remove. Returns steps, blocked objects (with why), stops (which refuse the whole
    deploy before anything runs), removable objects and notes on what a deploy leaves as it is."""
    cat = envs[catalog_env]
    steps: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    stops: list[str] = [f"{p} Fix the file first." for p in git.get("problems") or []]
    notes: list[str] = []
    removable: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    ordered = [catalog_env] + [e for e in envs if e != catalog_env]

    # admins, in every environment's platform (the catalog's first)
    if git.get("admins") is not None:
        for env in ordered:
            for who in sorted(set(git["admins"]) - set(envs[env]["admins"])):
                steps.append(_step("admins", who, "create", "add_admin", {"user": who}, env, f"add as admin in {env}"))
            for who in sorted(set(envs[env]["admins"]) - set(git["admins"])):
                removable.append({"kind": "admins", "id": who, "env": env})
                if ("admins", who) in remove:
                    removals.append(_step("admins", who, "remove", "remove_admin", {"user": who}, env,
                                          f"remove as admin in {env}"))

    # domains, parents first
    gd, dd = git.get("domains") or {}, cat["domains"]
    for ident in sorted(gd, key=lambda i: (bool(gd[i].get("parent")), i)):
        rec, dep = gd[ident], dd.get(ident)
        if rec.get("fabric_domain_id") and dep is None:
            blocked.append({"kind": "domains", "id": ident, "why": "comes from Fabric. Import it from Fabric on Platform > Domains, then deploy again."})
        elif dep is not None and dep.get("fabric_domain_id", "") != rec.get("fabric_domain_id", ""):
            blocked.append({"kind": "domains", "id": ident, "why": "is linked to a different Fabric domain in the platform."})
        elif rec != dep:
            steps.append(_step("domains", ident, "update" if dep else "create", "save_domain",
                               _domain_args(ident, rec, cat["raw"]["domains"].get(ident))))
    parent_of = {i: _s(r.get("parent_id")) for i, r in cat["raw"]["domains"].items()}  # Fabric's too
    for ident in sorted(set(dd) - set(gd), key=lambda i: (not parent_of.get(i), i)):  # children first
        removable.append({"kind": "domains", "id": ident, "env": None})
        if ("domains", ident) in remove:
            removals.append(_step("domains", ident, "remove", "delete_domain", {"did": ident}))

    # sources: an id is never changed, and a new source keeps the id its file gives it
    gs, ds = git.get("sources") or {}, cat["sources"]
    ids = {s["source_id"]: n for n, s in ds.items()}
    for ident, rec in sorted(gs.items()):
        dep = ds.get(ident)
        if not isinstance(rec["source_id"], int) or rec["source_id"] <= 0:
            stops.append(f"sources/{ident}.toml: source_id is a whole number above 0.")
        elif dep is not None and dep["source_id"] != rec["source_id"]:
            stops.append(f"sources/{ident}.toml says source_id {rec['source_id']}, but {ident} is id {dep['source_id']} "
                         "in the platform. Ids are never renumbered: put the platform's id back in the file.")
        elif dep is None and rec["source_id"] in ids:
            stops.append(f"sources/{ident}.toml: id {rec['source_id']} is already {ids[rec['source_id']]} in the platform.")
        elif rec != dep:
            fields = {"name": ident, "code": rec["code"], "title": rec["title"], "description": rec["description"],
                      "owner": rec["owner"], "system_type": rec["system_type"], "active": rec["active"],
                      "expected_hours": rec["expected_hours"],
                      # connections stay per environment in the platform; a deploy keeps them as they are
                      "connections": (cat["raw"]["sources"].get(ident) or {}).get("connections") or {}}
            if dep is None:
                steps.append(_step("sources", ident, "create", "adopt_sources", {
                    "systems": [{"code": rec["code"], "id": rec["source_id"], "name": rec["title"],
                                 "description": rec["description"], "owner": rec["owner"],
                                 "system_type": rec["system_type"], "active": rec["active"]}],
                    "project": "the platform repository"}, what=f"add with id {rec['source_id']}"))
            steps.append(_step("sources", ident, "update" if dep else "create", "save_source", {"source": fields}))
    for ident in sorted(set(ds) - set(gs)):
        removable.append({"kind": "sources", "id": ident, "env": None})
        if ("sources", ident) in remove:
            removals.append(_step("sources", ident, "remove", "delete_source", {"name": ident}))

    # key tables: made in every environment; never renamed, changed or removed (they hold keys)
    gk = git.get("key_tables") or {}
    for env in ordered:
        dk = envs[env]["key_tables"]
        for ident, rec in sorted(gk.items()):
            if ident not in dk:
                steps.append(_step("key_tables", ident, "create", "ensure_key_table", {
                    "name": ident, "business_key": rec["business_key"], "description": rec["description"],
                    "domain_id": rec["domain"]}, env, f"create in {env}"))
            elif dk[ident] != rec:
                notes.append(f"Key table {ident} in {env}: its fields differ from Git. A deploy doesn't change an "
                             "existing key table.")
        for ident in sorted(set(dk) - set(gk)):
            notes.append(f"Key table {ident} is in {env} but not in Git. A deploy never removes a key table: its keys stay.")

    # glossary
    if git.get("glossary") is not None:
        dg = cat["glossary"]
        for term, rec in sorted(git["glossary"].items()):
            if dg.get(term) != rec:
                steps.append(_step("glossary", term, "update" if term in dg else "create", "save_term",
                                   {"term": {"term": term, "definition": rec["definition"], "domain_id": rec["domain"] or None,
                                             "owner": rec["owner"]}}))
        for term in sorted(set(dg) - set(git["glossary"])):
            notes.append(f"Glossary term {term!r} isn't in Git. The platform can't remove glossary terms yet, so it stays.")

    # shortcut rules
    if git.get("shortcut_rules") is not None and git["shortcut_rules"] != cat["shortcut_rules"]:
        steps.append(_step("shortcut_rules", FILES["shortcut_rules"], "update", "save_shortcut_rules",
                           {"rules": git["shortcut_rules"]}))

    _policies_plan(git, cat, note, steps, blocked)
    retire = []  # a removed policy is retired, never deleted: its versions stay readable
    for ident, rec in sorted((cat.get("policies") or {}).items()):
        if ident in (git.get("policies") or {}) or rec["status"] == "retired":
            continue
        removable.append({"kind": "policies", "id": ident, "env": None})
        if ("policies", ident) in remove:
            retire += [_step("policies", ident, "remove", "remove_assignment",
                             {"policy": ident, "scope": a["scope"], "target": a["target"]},
                             what=f"unassign {a['scope']} {a['target']}") for a in rec["assign"]]
            retire.append(_step("policies", ident, "remove", "set_policy_status",
                                {"id": ident, "status": "retired", "note": note}, what="retire it"))

    # Removals run after every change, in reverse: policies, sources, domains (children first), admins last.
    rank = {"sources": 0, "domains": 1, "admins": 2}
    steps += retire + sorted(removals, key=lambda r: rank[r["kind"]])  # sorted is stable: each kind keeps its order
    body = json.dumps({"steps": steps, "blocked": blocked, "stops": stops}, sort_keys=True, default=str)
    return {"steps": steps, "blocked": blocked, "stops": stops, "notes": notes, "removable": removable,
            "plan_id": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]}
