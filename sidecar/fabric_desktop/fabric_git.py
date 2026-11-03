"""A Fabric workspace's own Git connection, next to a repository on this PC (Git-native Phase 2).

Fabric's Git integration connects a workspace to one branch and folder of an Azure DevOps or
GitHub repository. This module reads that connection (Get Connection, which needs only the
Workspace.ReadWrite.All scope sign-in already asks for), matches it to a repository's remotes,
keeps the link in a committed ``fabric-desktop.toml`` (workspace ids and names, never a secret or the tenant id, since the repository may be public), and lines
up three commits without asking Fabric for anything more:

* this PC: the repository's ``HEAD`` and branch;
* the Git remote: the remote-tracking commit of the branch Fabric follows (as of the last fetch);
* the workspace: the commit Fabric last synced (``gitSyncDetails.head``).

Which items changed in the workspace comes from Fabric's Get Status (``status``), which needs the
Workspace.GitUpdate.All scope (asked for on first use). Nothing here writes to Fabric or to Git.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import artifacts
from .git.cli import GitError
from .git.remote import PROVIDER_LABELS, parse
from .git.repo import Repository
from .git.scopes import _replace_in, _toml_str

LINK_FILE = "fabric-desktop.toml"
ENVS = ("dev", "test", "prod")
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SHA = re.compile(r"[0-9a-f]{40}")
# What a link keeps: ids KratosForge uses, names for people, and what Fabric said the workspace follows.
_LINK_FIELDS = ("workspace_id", "workspace_name", "provider", "host", "owner", "project", "repository",
                "branch", "folder")
_STATES = {"NotConnected", "Connected", "ConnectedAndInitialized"}


def _text(value: Any, limit: int = 256) -> str:
    """A field Fabric or a file gave, as one short line without control characters."""
    s = str(value or "").strip()
    return "" if any(ord(c) < 32 or ord(c) == 127 for c in s) else s[:limit]


def env_name(env: str) -> str:
    e = str(env or "").strip().lower()
    if e not in ENVS:
        raise GitError("Choose Dev, Test or Prod.", kind="invalid")
    return e


def guid(value: str, what: str = "workspace") -> str:
    v = str(value or "").strip()
    if not _GUID.fullmatch(v):
        raise GitError(f"That isn't a {what} id.", kind="invalid")
    return v.lower()


# ---- what Fabric says the workspace follows


def connection(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Fabric's Get Connection answer in the app's words. Unknown or missing fields come back
    empty, never guessed; a state Fabric doesn't document reads as not connected."""
    if not isinstance(raw, dict):
        raw = {}
    state = raw.get("gitConnectionState")
    state = state if state in _STATES else "NotConnected"
    got_details, got_sync = raw.get("gitProviderDetails"), raw.get("gitSyncDetails")
    details: dict[str, Any] = got_details if isinstance(got_details, dict) else {}
    sync: dict[str, Any] = got_sync if isinstance(got_sync, dict) else {}
    kind = details.get("gitProviderType")
    provider = {"GitHub": "github", "AzureDevOps": "azure_devops"}.get(kind or "", "")
    head = _text(sync.get("head")).lower()
    out = {
        "state": state,
        "connected": state != "NotConnected" and bool(provider),
        "initialized": state == "ConnectedAndInitialized",
        "selective": raw.get("gitConnectionType") == "Selective",
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, ""),
        "owner": _text(details.get("ownerName") if provider == "github" else details.get("organizationName"), 100),
        "project": _text(details.get("projectName"), 100) if provider == "azure_devops" else "",
        "repository": _text(details.get("repositoryName"), 128),
        "branch": _text(details.get("branchName"), 250),
        "folder": _text(details.get("directoryName"), 256),
        "host": _text(details.get("customDomainName"), 100) if provider == "github" else "",
        "synced_head": head if _SHA.fullmatch(head) else "",
        "last_sync": _text(sync.get("lastSyncTime"), 40),
    }
    if not out["connected"]:
        out.update(provider="", provider_label="", owner="", project="", repository="", branch="", folder="", host="",
                   synced_head="", last_sync="")
    return out


def _same(a: str, b: str) -> bool:
    return bool(a) and a.casefold() == b.casefold()


def matching_remote(conn: dict[str, Any], remotes: list[dict[str, Any]]) -> str | None:
    """The name of the repository's remote that points where the workspace's Git connection points,
    or None. GitHub matches owner and repository (on github.com, or the ghe.com domain Fabric
    names); Azure DevOps matches organization, project and repository. Case doesn't matter."""
    if not conn.get("connected"):
        return None
    for r in remotes:
        info = parse(r.get("url") or "")
        if info.get("provider") != conn["provider"]:
            continue
        if not (_same(info.get("owner") or "", conn["owner"]) and _same(info.get("repo") or "", conn["repository"])):
            continue
        if conn["provider"] == "azure_devops" and not _same(info.get("project") or "", conn["project"]):
            continue
        if conn["provider"] == "github":
            host = (info.get("host") or "").lower()
            want = conn["host"].lower()
            if want and host != want and not host.endswith(f".{want}"):
                continue
            if not want and host not in ("github.com", "www.github.com"):
                continue
        return str(r.get("name") or "")
    return None


def remote_for_link(link: dict[str, Any], remotes: list[dict[str, Any]]) -> str | None:
    """The remote that points where a saved link says the workspace follows (no Fabric call)."""
    if link.get("provider") not in ("github", "azure_devops"):
        return None
    conn = {"connected": True, "provider": link["provider"], "owner": link.get("owner", ""),
            "project": link.get("project", ""), "repository": link.get("repository", ""), "host": link.get("host", "")}
    return matching_remote(conn, remotes)


# ---- the link, kept in the repository


def read_links(root: str | Path) -> dict[str, Any]:
    """The workspaces this repository is linked to, per environment, from ``fabric-desktop.toml``."""
    envs: dict[str, Any] = {e: None for e in ENVS}
    path = Path(root) / LINK_FILE
    if path.is_symlink():  # writes replace the link itself, so reads never follow one either
        return {"envs": envs, "problem": f"{LINK_FILE} is a link to another file; replace it with a plain file.",
                "exists": True}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"envs": envs, "problem": None, "exists": False}
    except (OSError, ValueError) as e:
        return {"envs": envs, "problem": f"{LINK_FILE} can't be read: {e}", "exists": True}
    got = data.get("workspaces")
    workspaces: dict[str, Any] = got if isinstance(got, dict) else {}
    for env in ENVS:
        raw = workspaces.get(env)
        if not isinstance(raw, dict) or not _GUID.fullmatch(str(raw.get("workspace_id") or "")):
            continue
        envs[env] = {k: _text(raw[k]) for k in _LINK_FIELDS if isinstance(raw.get(k), str) and _text(raw[k])}
    return {"envs": envs, "problem": None, "exists": True}


def write_link(root: str | Path, env: str, link: dict[str, Any] | None) -> dict[str, Any]:
    """Sets (or with None, removes) one environment's link and rewrites ``fabric-desktop.toml``.
    Only the fields above are kept. The file shows as a change to commit."""
    env = env_name(env)
    current = read_links(root)
    if current["problem"]:
        raise GitError(current["problem"] + " Fix or delete the file first.", kind="invalid")
    envs = dict(current["envs"])
    envs[env] = None if link is None else {k: _text(link.get(k)) for k in _LINK_FIELDS if _text(link.get(k))}
    lines = ["# The Fabric workspaces this repository is linked to, per environment (KratosForge).",
             "# Ids and names only: never put passwords, keys or connection strings here.",
             "# Linking reads Fabric; it doesn't connect a workspace to Git or change one.", ""]
    for e in ENVS:
        fields = envs.get(e)
        if fields and fields.get("workspace_id"):
            lines += [f"[workspaces.{e}]", *(f"{k} = {_toml_str(v)}" for k, v in fields.items()), ""]
    _replace_in(Path(root), LINK_FILE, "\n".join(lines))
    return read_links(root)


# ---- three commits, side by side


def _count(repo: Repository, rng: str) -> int | None:
    out = repo._try("rev-list", "--count", "--end-of-options", rng)
    return int(out.strip()) if out and out.strip().isdigit() else None


def alignment(repo: Repository, conn: dict[str, Any], remote: str | None) -> dict[str, Any]:
    """This PC, the Git remote (as of the last fetch) and the workspace's synced commit, with how
    far apart they are where Git on this PC can tell. Reads the repository only."""
    status = repo.status()
    local = {"branch": status.get("branch"), "commit": repo.head(),
             "uncommitted": len(status.get("staged", [])) + len(status.get("unstaged", []))
             + len(status.get("untracked", [])) + len(status.get("conflicts", []))}
    out: dict[str, Any] = {"local": local, "remote": None, "workspace": None, "notes": []}
    if not conn.get("connected"):
        out["notes"].append("The workspace isn't connected to Git in Fabric. A workspace admin connects it once in "
                            "Fabric (Workspace settings > Git integration).")
        return out
    branch = conn["branch"]
    tracking = None
    if remote and branch and not branch.startswith("-") and ".." not in branch:
        tracking = (repo._try("rev-parse", "--verify", "-q", "--end-of-options",
                              f"refs/remotes/{remote}/{branch}^{{commit}}") or "").strip() or None
    out["remote"] = {"name": remote, "branch": branch, "commit": tracking}
    synced = conn["synced_head"]
    known = bool(synced) and repo._try("cat-file", "-e", "--end-of-options", f"{synced}^{{commit}}") is not None
    out["workspace"] = {"commit": synced or None, "last_sync": conn["last_sync"] or None, "known_here": known,
                        "folder": conn["folder"]}
    if remote is None:
        out["notes"].append("None of this repository's remotes points to the repository the workspace follows.")
    elif tracking is None:
        out["notes"].append(f"This PC hasn't fetched {remote}/{branch} yet. Fetch to compare it with the workspace.")
    if tracking and synced and known:
        out["remote"]["ahead_of_workspace"] = _count(repo, f"{synced}..{tracking}")
        out["remote"]["behind_workspace"] = _count(repo, f"{tracking}..{synced}")
    elif synced and not known:
        out["notes"].append("The workspace's synced commit isn't on this PC. Fetch, then check again.")
    if local["commit"] and tracking:
        out["local"]["ahead_of_remote"] = _count(repo, f"{tracking}..{local['commit']}")
        out["local"]["behind_remote"] = _count(repo, f"{local['commit']}..{tracking}")
    if local["branch"] and branch and local["branch"] != branch:
        out["notes"].append(f"You're on {local['branch']}; the workspace follows {branch}. Your commits reach the "
                            f"workspace only after they're on {branch} and someone updates it from Git.")
    out["notes"].append("Pushing to Git doesn't update the workspace: it changes only when someone chooses Update "
                        "from Git.")
    return out


# ---- which items differ, and their conflicts (Get Status)

_CHANGES = {"Added": "added", "Deleted": "deleted", "Modified": "modified"}
_CONFLICTS = {"Conflict": "conflict", "SameChanges": "same"}


def _state(ws: str, remote: str, conflict: str) -> str:
    """One item's state, from both sides, in the order a person has to act on them."""
    if conflict == "conflict":
        return "conflict"
    if conflict == "same":
        return "same_change"
    if ws and remote:
        return "both_changed"  # Fabric didn't call it a conflict; shown next to conflicts anyway
    if remote:
        return "incoming"  # Git is newer: Update from Git would bring it in
    if ws:
        return "outgoing"  # the workspace is newer: Commit to Git would take it out
    return "unchanged"


def status(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Fabric's Get Status answer in the app's words: the two commits it compared, one row per
    item that differs (with its support from the capability registry) and counts. Anything Fabric
    sends that isn't documented is left out rather than guessed. ``fingerprint`` changes whenever
    any of it changes, so a later write can refuse to act on an out-of-date preview."""
    if not isinstance(raw, dict):
        raw = {}
    heads = {k: _text(raw.get(k)).lower() for k in ("workspaceHead", "remoteCommitHash")}
    rows: list[dict[str, Any]] = []
    changes = raw.get("changes")
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        got_meta = change.get("itemMetadata")
        meta: dict[str, Any] = got_meta if isinstance(got_meta, dict) else {}
        got_ident = meta.get("itemIdentifier")
        ident: dict[str, Any] = got_ident if isinstance(got_ident, dict) else {}
        logical = _text(ident.get("logicalId")).lower()
        object_id = _text(ident.get("objectId")).lower()
        if not (_GUID.fullmatch(logical) or _GUID.fullmatch(object_id)):
            continue  # Fabric always names an item by at least one of these
        ws_change = _CHANGES.get(change.get("workspaceChange") or "", "")
        remote_change = _CHANGES.get(change.get("remoteChange") or "", "")
        conflict = _CONFLICTS.get(change.get("conflictType") or "", "")
        item_type = _text(meta.get("itemType"), 80)
        cap = artifacts.capability(item_type)
        rows.append({"item_type": item_type, "label": cap.label, "name": _text(meta.get("displayName")) or "(no name)",
                     "logical_id": logical if _GUID.fullmatch(logical) else "",
                     "object_id": object_id if _GUID.fullmatch(object_id) else "",
                     "workspace_change": ws_change, "remote_change": remote_change, "conflict": conflict,
                     "state": _state(ws_change, remote_change, conflict),
                     "git_support": cap.git, "round_trip": cap.round_trip})
    order = {"conflict": 0, "both_changed": 1, "incoming": 2, "outgoing": 3, "same_change": 4, "unchanged": 5}
    rows.sort(key=lambda r: (order[r["state"]], r["name"].lower()))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in order}
    fingerprint = hashlib.sha256(json.dumps(
        [heads, [[r["logical_id"], r["object_id"], r["workspace_change"], r["remote_change"], r["conflict"]]
                 for r in rows]], sort_keys=True).encode()).hexdigest()[:16]
    return {"workspace_head": heads["workspaceHead"] if _SHA.fullmatch(heads["workspaceHead"]) else "",
            "remote_commit": heads["remoteCommitHash"] if _SHA.fullmatch(heads["remoteCommitHash"]) else "",
            "items": rows, "counts": counts, "fingerprint": fingerprint,
            "aligned": not rows}


# ---- Update from Git: what it would do, before it does it

POLICIES = {"": None, "PreferWorkspace": "PreferWorkspace", "PreferRemote": "PreferRemote"}


def update_plan(st: dict[str, Any], policy: str = "") -> dict[str, Any]:
    """What Update from Git would do to each item of a ``status()`` answer, with ``policy`` for
    conflicts ("PreferWorkspace" keeps the workspace's version, "PreferRemote" takes Git's; Fabric
    applies one choice to every conflict). ``changes`` are items the workspace takes from Git,
    ``overwrites`` those where a change made in the workspace is lost or may be, ``kept`` the rest.

    Microsoft Learn says only that the update "only affects items in the workspace that were changed in
    those commits" (Core > Git > Update From Git, checked 2026-11-03); it doesn't say what happens to an
    uncommitted change in the workspace. So a change made only in the workspace counts as at risk, and
    the person is told to commit it in Fabric first to be sure of keeping it."""
    if policy not in POLICIES:
        raise GitError("Choose Keep the workspace version or Take the Git version.", kind="invalid")
    changes, overwrites, kept = [], [], []
    conflicts = [i for i in st.get("items", []) if i["state"] in ("conflict", "both_changed")]
    for i in st.get("items", []):
        if i.get("git_support") == "no":
            kept.append({**i, "why": "Fabric Git doesn't carry this item type"})
        elif i["state"] == "incoming":
            changes.append({**i, "why": {"added": "Added from Git", "deleted": "Deleted from the workspace, as in Git"}
                            .get(i["remote_change"], "Replaced with the version in Git")})
        elif i["state"] in ("conflict", "both_changed"):
            if policy == "PreferRemote":
                overwrites.append({**i, "why": "Takes the Git version; the workspace's change is lost"})
            elif policy == "PreferWorkspace":
                kept.append({**i, "why": "Keeps the workspace version; Git's change isn't applied"})
        elif i["state"] == "outgoing":
            overwrites.append({**i, "why": "Changed only in the workspace and not committed; Microsoft doesn't "
                               "say Update from Git keeps it, so commit it in Fabric first to be sure"})
        else:
            kept.append({**i, "why": "Same on both sides"})
    return {"from": st.get("workspace_head", ""), "to": st.get("remote_commit", ""), "policy": policy,
            "needs_policy": bool(conflicts) and not policy, "conflicts": len(conflicts),
            "changes": changes, "overwrites": overwrites, "kept": kept,
            "nothing": not changes and not conflicts}  # workspace-only changes give Fabric nothing to update
