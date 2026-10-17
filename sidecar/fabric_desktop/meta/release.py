"""Where a project's files stand in Git, and the release record a promote writes.

Git is read through the Git layer (``fabric_desktop.git``) with the person's own ``git``
command: no new sign-in, no token, no network. Ahead and behind are counted against the upstream
branch as of the last fetch, so a status never prompts for credentials or waits on a server. Only files that count toward the metadata version
(``Project.version``) make the project "uncommitted": local data, caches and
``environments.toml`` (deployment settings) don't.

A promote records a release: a run receipt (``receipts.SCHEMA``, kind ``release``) naming the
commit, whether the files matched it, the engine and the packages it runs on, and a hash of each
entity's file. The receipt goes into ``meta.deployments`` next to the columns a reader filters on,
so the Workload management views, and anyone with the row, can check it with ``receipts.receipt_id``.
No remote URL is recorded: it can carry credentials.
"""

from __future__ import annotations

import getpass
import hashlib
import importlib.metadata as md
import platform
from pathlib import Path
from typing import Any

from .. import __version__, receipts
from ..git import cli
from ..git.cli import GitError
from ..git.repo import Repository
from .project import _IGNORED, _UNVERSIONED, Project

# The packages the engine's results depend on; their versions make up the lock hash.
ENGINE_PACKAGES = ("deltalake", "duckdb", "polars", "pyarrow")
MAX_CHANGES = 200


def _counts(rel: str) -> bool:
    """Whether a project-relative path counts toward the metadata version."""
    parts = Path(rel).parts
    return not (set(parts) & _IGNORED) and rel not in _UNVERSIONED and not rel.endswith(".pyc")


def status(root: Path | str) -> dict[str, Any]:
    """Branch, commit, upstream, ahead/behind and uncommitted files for the project folder, read
    through the Git layer (``git.repo.Repository``)."""
    root = Path(root).resolve()
    if not cli.git_path():
        return {"available": False, "repo": False,
                "message": "Git isn't installed on this PC, so releases can't name a commit."}
    try:
        repo = Repository(root)
        git = repo.status()
    except GitError:
        return {"available": True, "repo": False,
                "message": "The project folder isn't in a Git repository, so releases can't name a commit."}
    prefix = root.relative_to(repo.root).as_posix() if root.is_relative_to(repo.root) else ""
    prefix = "" if prefix == "." else prefix
    changes: dict[str, str] = {}
    for kind in ("conflicts", "staged", "unstaged", "untracked"):
        for entry in git[kind]:
            path = entry["path"]
            if prefix and not path.startswith(prefix + "/"):
                continue  # outside the project folder
            rel = path[len(prefix) + 1:] if prefix else path
            if _counts(rel):
                changes.setdefault(rel, "conflict" if kind == "conflicts" else _STATES.get(entry["state"], "changed"))
    commit, branch, upstream = git["commit"], git["branch"], git["upstream"]
    ahead, behind = (git["ahead"], git["behind"]) if upstream else (None, None)
    rows = [{"path": p, "state": st} for p, st in changes.items()]
    return {"available": True, "repo": True, "branch": branch, "commit": commit, "upstream": upstream,
            "ahead": ahead, "behind": behind, "dirty": bool(rows), "changes": rows[:MAX_CHANGES],
            "change_count": len(rows), "pushed": bool(commit and upstream and ahead == 0),
            "message": _summary(branch, commit, upstream, ahead, behind, len(rows))}


# The Git layer's words for a file's state, in the words a release record has always used.
_STATES = {"untracked": "new", "modified": "changed", "type changed": "changed", "added": "added",
           "deleted": "deleted", "renamed": "renamed", "copied": "copied"}


def _summary(branch, commit, upstream, ahead, behind, changed) -> str:
    if not commit:
        return "Nothing is committed yet."
    where = f"{branch} at {commit[:7]}" if branch else f"{commit[:7]} (no branch)"
    bits = [where]
    if changed:
        bits.append(f"{changed} uncommitted file{'s' if changed != 1 else ''}")
    if not upstream:
        bits.append("not pushed (no upstream branch)")
    else:
        if ahead:
            bits.append(f"{ahead} commit{'s' if ahead != 1 else ''} not pushed")
        if behind:
            bits.append(f"{behind} behind {upstream}")
    return ", ".join(bits) + "."


def problems(git: dict[str, Any]) -> list[str]:
    """Why these files can't be named by a pushed commit, in words; empty when they can."""
    if not git.get("repo"):
        return [git.get("message") or "The project isn't in Git."]
    out = []
    if not git.get("commit"):
        out.append("nothing is committed yet")
    if git.get("dirty"):
        n = git.get("change_count") or 0
        out.append(f"{n} file{'s' if n != 1 else ''} changed since the last commit; commit them")
    if git.get("commit") and not git.get("upstream"):
        out.append(f"branch {git.get('branch') or '(none)'} has no upstream; push it")
    elif git.get("ahead"):
        out.append(f"{git['ahead']} commit{'s' if git['ahead'] != 1 else ''} not pushed; push them")
    return out


def engine() -> dict[str, Any]:
    """The engine and the package versions its results depend on, with one hash over them."""
    packages = {}
    for name in ENGINE_PACKAGES:
        try:
            packages[name] = md.version(name)
        except md.PackageNotFoundError:
            packages[name] = None
    lock = "\n".join(f"{k}=={v}" for k, v in sorted(packages.items()))
    return {"engine_version": __version__, "python": platform.python_version(), "packages": packages,
            "lock_hash": hashlib.sha256(lock.encode()).hexdigest()}


def record(project: Project, source_env: str, target_env: str, git: dict[str, Any]) -> dict[str, Any]:
    """The release receipt for promoting ``project`` from ``source_env`` to ``target_env``."""
    entities = [{"name": e.name, "path": e.path.relative_to(project.root).as_posix(),
                 "sha256": hashlib.sha256(e.path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()}
                for e in project.entities.values()]
    code = {"repo": bool(git.get("repo")), "commit": git.get("commit"), "branch": git.get("branch"),
            "dirty": bool(git.get("dirty")) if git.get("repo") else None,
            "pushed": bool(git.get("pushed")) if git.get("repo") else None,
            "uncommitted": [c["path"] for c in git.get("changes") or []]}
    return receipts.new_receipt(
        "release",
        project={"name": project.root.name, "metadata_version": project.version,
                 "source_env": source_env, "target_env": target_env},
        code=code, entities=entities, environment=engine(),
        identity={"user": _user()})


def _user() -> str | None:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name (a service account without one)
        return None


def deployment_columns(body: dict[str, Any]) -> dict[str, Any]:
    """The ``meta.deployments`` columns a release fills: the receipt and what readers filter on."""
    code, env = body["code"], body["environment"]
    return {"commit": code["commit"], "branch": code["branch"], "dirty": code["dirty"], "pushed": code["pushed"],
            "engine_version": env["engine_version"], "lock_hash": env["lock_hash"],
            "by": (body.get("identity") or {}).get("user"),
            "receipt_id": receipts.receipt_id(body), "receipt": receipts.canonical(body).decode("utf-8")}


def committed(root: Path | str, folder: str, suffix: str = "") -> dict[str, str] | None:
    """The files under ``folder`` (project-relative) as the last commit has them, {path: text},
    or None when the project isn't in Git or nothing is committed."""
    root = Path(root).resolve()
    repo = _repo(root)
    if repo is None or repo.head() is None:
        return None
    try:
        listing = cli.text(root, ["ls-tree", "-r", "-z", "--name-only", "HEAD", "--", folder], read_only=True)
    except GitError:
        return None
    out = {}
    for rel in filter(None, listing.split("\0")):
        if suffix and not rel.endswith(suffix):
            continue
        text = _at_head(repo, root / rel)
        if text is not None:
            out[rel] = text.replace("\r\n", "\n")
    return out


def committed_file(path: Path | str) -> str | None:
    """One file as the last commit has it, or None (not in Git, never committed, no Git)."""
    path = Path(path).resolve()
    repo = _repo(path.parent)
    return _at_head(repo, path) if repo is not None else None


def _repo(folder: Path) -> Repository | None:
    if not cli.git_path() or not folder.is_dir():
        return None
    try:
        return Repository(folder)
    except GitError:
        return None


def _at_head(repo: Repository, path: Path) -> str | None:
    try:
        f = repo.file_at("HEAD", str(path))
    except GitError:
        return None
    return None if f["binary"] or f["truncated"] else f["text"]
