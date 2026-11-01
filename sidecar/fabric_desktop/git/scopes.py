"""Kinds of repository: notebooks, data mesh projects, platform and capacity definitions.

Each kind of definition lives in its own repository, so a notebook change never rides along with
a platform change. A repository says its kind in ``fabric-desktop.repo.toml`` at its root. New
repositories start from a fixed layout; an existing folder gets only the marker, and only when
the person picks a kind. Nothing is committed for them: the new files show as changes to review.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from .cli import GitError
from .repo import Repository, _folder, init
from .. import jsonfile

MARKER = "fabric-desktop.repo.toml"

# kind -> label, who it is for, what it holds
KINDS: dict[str, dict[str, str]] = {
    "notebooks": {"label": "Notebooks", "for": "One per team or project.",
                  "holds": "Notebooks, and where they find their data in each environment."},
    "project": {"label": "Data mesh project", "for": "One per domain or project.",
                "holds": "A metadata project: entities, keys, transforms, tests and its environments."},
    "platform": {"label": "Platform", "for": "One per tenant, for platform admins.",
                 "holds": "Domains, source systems, key tables, landing zones, request rules, policies "
                          "and where the platform's items live."},
    "capacity": {"label": "Capacity", "for": "One or more, for capacity admins.",
                 "holds": "Which workspaces sit on which capacity, and the settings each capacity should have."},
}

_GITIGNORE = """# KratosForge: never commit local data, caches or sign-in files
.local/
__pycache__/
.ipynb_checkpoints/
*.tmp
.env
.env.*
!.env.example
"""

_LOCATIONS = """# Where these {what} find their data, per environment.
# Workspace and item ids are what KratosForge uses; the names are for people.
# Never put passwords, keys or connection strings here.

[dev]
runs_on = "this_pc"   # nothing attached: the notebooks work on this PC only

# [test]
# workspace_id = ""
# workspace_name = ""
# lakehouse_id = ""
# lakehouse_name = ""
# sql_id = ""          # a Fabric SQL database, when the notebooks use one
# sql_name = ""

# [prod]
# workspace_id = ""
# workspace_name = ""
# lakehouse_id = ""
# lakehouse_name = ""
"""

_PLATFORM = """# Where the platform's items live, per environment. Anything not listed is in that
# environment's Platform workspace. Ids are what the app uses; names are for people.

# [prod.sql_platform]
# workspace_id = ""
# item_id = ""
# name = "SQL_Platform"

# [prod.lh_meta]
# workspace_id = ""
# item_id = ""
# name = "LH_Meta"
"""

_CAPACITY = """# The capacities and the workspaces each should hold. KratosForge compares
# this with Fabric and reports differences; it doesn't change capacities.
# A setting left out isn't compared. Ids and names only.

# [capacities."F64 Prod"]
# capacity_id = ""
# sku = "F64"
# region = "West Europe"
# state = "Active"
# workspaces = [{ id = "", name = "" }]
"""

# Files and folders each new repository starts with (folders get a .gitkeep so Git keeps them).
_LAYOUT: dict[str, dict[str, str]] = {
    "notebooks": {"locations.toml": _LOCATIONS.format(what="notebooks"), "notebooks/.gitkeep": ""},
    "project": {},
    "platform": {"platform.toml": _PLATFORM, **{f"{d}/.gitkeep": "" for d in
                 ("domains", "sources", "key_tables", "landing_zones", "policies")}},
    "capacity": {"capacity.toml": _CAPACITY},
}


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise GitError(f"A repository holds {', '.join(k['label'].lower() for k in KINDS.values())}.",
                       kind="invalid")
    return kind


def read_kind(root: str | Path) -> str | None:
    """The kind the repository at ``root`` says it is, or None when it has no (readable) marker."""
    try:
        data = tomllib.loads((Path(root) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    kind = data.get("kind")
    return kind if kind in KINDS else None


def guess_kind(root: str | Path) -> str | None:
    """A likely kind for a folder with no marker, from what is in it; None when nothing says."""
    p = Path(root)
    if (p / "environments.toml").is_file() or (p / "layers.toml").is_file():
        return "project"
    if (p / "platform.toml").is_file():
        return "platform"
    if (p / "capacity.toml").is_file():
        return "capacity"
    try:
        if next(p.glob("*.ipynb"), None) or next(p.glob("notebooks/*.ipynb"), None):
            return "notebooks"
    except OSError:
        pass
    return None


def describe(root: str | Path) -> dict[str, Any]:
    kind = read_kind(root)
    return {"kind": kind, "suggested": None if kind else guess_kind(root)}


def _marker(kind: str) -> str:
    return (f'# KratosForge: what this repository holds.\nkind = "{kind}"\nversion = 1\n')


def create(path: str | Path, kind: str, branch: str = "main") -> Repository:
    """A new repository of ``kind`` at ``path``, with that kind's starting files. Refuses a folder
    inside another repository (``init`` does) and a folder that already has files in it."""
    check_kind(kind)
    p = _folder(path, must_exist=False)  # the same folder init() will use (~, %VARS%, spaces)
    if p.is_dir() and any(p.iterdir()):
        raise GitError(f"{p} already has files in it. Open it and choose what it holds instead.", kind="exists")
    repo = init(p, branch)
    files = {MARKER: _marker(kind), ".gitignore": _GITIGNORE, **_LAYOUT[kind]}
    for rel, text in files.items():
        f = repo.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "x", encoding="utf-8") as out:  # never replaces a file that appeared meanwhile
            out.write(text)
    return repo


def set_kind(repo: Repository, kind: str, confirm: bool = False) -> dict[str, Any]:
    """Writes the marker for an existing repository. Only the marker file changes; it shows as a
    change to commit. Changing a kind the repository already states needs ``confirm``."""
    check_kind(kind)
    current = read_kind(repo.root)
    if current == kind:
        return {"kind": kind, "changed": False}
    if not confirm:
        if current:
            raise GitError(f"This repository says it holds {KINDS[current]['label'].lower()}. "
                           f"Confirm to change it to {KINDS[kind]['label'].lower()}.", kind="confirm")
        if (repo.root / MARKER).exists():
            raise GitError(f"This repository's {MARKER} isn't one this app can read. "
                           f"Confirm to replace it with {KINDS[kind]['label'].lower()}.", kind="confirm")
    _replace_in(repo.root, MARKER, _marker(kind))
    return {"kind": kind, "changed": True}


LOCATIONS = "locations.toml"
ENVS = ("dev", "test", "prod")
# The fields a location may hold: ids the app uses and names for people. Nothing else is kept.
_LOCATION_FIELDS = ("workspace_id", "workspace_name", "lakehouse_id", "lakehouse_name", "sql_id", "sql_name")


def read_locations(root: str | Path) -> dict[str, Any]:
    """Where a notebooks repository's notebooks find their data, per environment, from its
    ``locations.toml``: {"envs": {env: {...} | None}, "problem": str | None}. An environment with
    no lakehouse (or ``runs_on = "this_pc"``) attaches nothing."""
    envs: dict[str, Any] = {e: None for e in ENVS}
    try:
        data = tomllib.loads((Path(root) / LOCATIONS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"envs": envs, "problem": None, "exists": False}
    except (OSError, ValueError) as e:
        return {"envs": envs, "problem": f"{LOCATIONS} can't be read: {e}", "exists": True}
    for env in ENVS:
        raw = data.get(env)
        if not isinstance(raw, dict):
            continue
        loc = {k: str(raw[k]) for k in _LOCATION_FIELDS if isinstance(raw.get(k), str) and raw[k]}
        this_pc = raw.get("runs_on") == "this_pc" or not loc.get("workspace_id")
        envs[env] = {**loc, "this_pc": this_pc,
                     "lakehouse": None if this_pc or not loc.get("lakehouse_id") else {
                         "workspace_id": loc["workspace_id"], "lakehouse_id": loc["lakehouse_id"],
                         "name": loc.get("lakehouse_name") or "Lakehouse",
                         "workspace_name": loc.get("workspace_name", "")}}
    return {"envs": envs, "problem": None, "exists": True}


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_locations(root: str | Path, envs: dict[str, Any]) -> dict[str, Any]:
    """Writes ``locations.toml`` from {env: {field: value} | None}. Only ids and names are kept.
    ``{"this_pc": true}`` means nothing is attached in that environment; an environment left out
    has no location yet. The file shows as a change to commit."""
    lines = ["# Where these notebooks find their data, per environment.",
             "# Workspace and item ids are what KratosForge uses; the names are for people.",
             "# Never put passwords, keys or connection strings here.", ""]
    for env in ENVS:
        loc = envs.get(env) or {}
        if not isinstance(loc, dict):
            raise GitError(f"The {env} location isn't valid.", kind="invalid")
        fields = {k: loc[k].strip() for k in _LOCATION_FIELDS if isinstance(loc.get(k), str) and loc[k].strip()}
        for k, v in fields.items():
            if len(v) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in v):
                raise GitError(f"The {env} {k.replace('_', ' ')} isn't valid.", kind="invalid")
        if fields.get("workspace_id"):
            lines += [f"[{env}]", *(f"{k} = {_toml_str(v)}" for k, v in fields.items()), ""]
        elif loc.get("this_pc"):
            lines += [f"[{env}]", 'runs_on = "this_pc"', ""]
        # anything else: no location for this environment yet
    _replace_in(Path(root), LOCATIONS, "\n".join(lines))
    return read_locations(root)


def _replace_in(root: Path, name: str, text: str) -> None:
    """Replaces the file ``name`` at the repository root with ``text``. The new file is written
    beside it and moved into place, so a link there is replaced itself and its target (which can
    be anywhere, in a cloned repository) is never written."""
    root = Path(root)
    fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
            out.write(text)
        jsonfile.replace(tmp, root / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def wrong_place(kind: str | None, item: str) -> str | None:
    """Why saving an ``item`` ("notebooks" or "project") into a repository of ``kind`` mixes scopes,
    or None when it fits (or the repository doesn't say what it holds)."""
    if kind is None or kind == item:
        return None
    return (f"This repository holds {KINDS[kind]['label'].lower()} definitions. "
            f"Keep {KINDS[item]['label'].lower()} in a {KINDS[item]['label'].lower()} repository.")
