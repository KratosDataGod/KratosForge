"""Source control: what the Source control page calls.

Every method names its repository by folder. Only repositories opened, initialized or cloned
from the app are used (their folders are listed in ``git_repos.json``, nothing else), so a call
can't reach an arbitrary folder, and each repository's credentials stay Git's business: Git
picks them per remote address from its credential helper.

Answers the page has to act on come back as data rather than errors: a commit that holds
possible secrets (``blocked: "secrets"``), and a switch with uncommitted changes
(``needs_choice: true``). Steps that lose work still refuse without ``confirm``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any

from .git import cli
from .git.cli import GitError
from .git.ops import Operations
from .git.repo import Repository, _folder, init as git_init

MAX_REPOS = 50


class GitMixin:
    root: Path

    # ---- the repositories this PC opened

    @property
    def git_repos_file(self) -> Path:
        return self.root / "git_repos.json"

    @property
    def _git_ops(self) -> Operations:
        ops = getattr(self, "_git_ops_obj", None)
        if ops is None:
            ops = self._git_ops_obj = Operations()
        return ops

    def _git_lock(self) -> threading.Lock:
        lock = getattr(self, "_git_repos_lock", None)
        if lock is None:
            lock = self._git_repos_lock = threading.Lock()
        return lock

    def _git_saved(self) -> dict[str, Any]:
        try:
            data = json.loads(self.git_repos_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("repos"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"repos": [], "current": None}

    def _git_save(self, data: dict[str, Any]) -> None:
        self.git_repos_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.git_repos_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.git_repos_file)

    def _git_remember(self, repo: Repository) -> None:
        with self._git_lock():
            data = self._git_saved()
            root = str(repo.root)
            now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            data["repos"] = [r for r in data["repos"] if not _same(r.get("path"), root)]
            data["repos"].insert(0, {"path": root, "name": repo.root.name, "opened_at": now})
            data["repos"] = data["repos"][:MAX_REPOS]
            data["current"] = root
            self._git_save(data)

    def _repo(self, repo: str) -> Repository:
        """The repository at ``repo``, which must be one this PC opened in Source control."""
        p = _folder(repo)
        if not any(_same(r.get("path"), str(p)) for r in self._git_saved()["repos"]):
            raise GitError("Open this repository in Source control first.", kind="invalid")
        return Repository(p)

    def git_info(self) -> dict[str, Any]:
        """Whether Git is installed, its version, and the repositories this PC opened."""
        exe = cli.git_path()
        return {"installed": bool(exe), "version": cli.version() if exe else None, **self.git_repos(),
                "notebooks_folder": str(self.notebooks.folder),  # type: ignore[attr-defined]
                "clone_folder": str(Path.home() / "source" / "repos")}

    def git_repos(self) -> dict[str, Any]:
        data = self._git_saved()
        repos = [{**r, "exists": Path(r["path"]).is_dir()} for r in data["repos"] if r.get("path")]
        return {"repos": repos, "current": data.get("current")}

    def git_open(self, path: str) -> dict[str, Any]:
        """Opens the repository holding ``path`` (any folder inside it) and remembers it."""
        repo = Repository(path)
        self._git_remember(repo)
        return repo.status()

    def git_forget(self, repo: str) -> dict[str, Any]:
        """Takes a repository off this list. Its folder and files are left exactly as they are."""
        with self._git_lock():
            data = self._git_saved()
            data["repos"] = [r for r in data["repos"] if not _same(r.get("path"), repo)]
            if _same(data.get("current"), repo):
                data["current"] = data["repos"][0]["path"] if data["repos"] else None
            self._git_save(data)
        return self.git_repos()

    def git_init(self, path: str, branch: str = "main") -> dict[str, Any]:
        repo = git_init(path, branch)
        self._git_remember(repo)
        return repo.status()

    # ---- the work that talks to a remote (background)

    def git_clone_start(self, url: str, parent: str, name: str | None = None) -> dict[str, Any]:
        return self._git_ops.clone(url, parent, name)

    def git_fetch(self, repo: str, remote: str | None = None) -> dict[str, Any]:
        return self._git_ops.fetch(self._repo(repo), remote)

    def git_pull(self, repo: str, mode: str = "ff-only") -> dict[str, Any]:
        return self._git_ops.pull(self._repo(repo), mode)

    def git_push(self, repo: str, remote: str | None = None) -> dict[str, Any]:
        return self._git_ops.push(self._repo(repo), remote)

    def git_op_status(self, id: str) -> dict[str, Any]:
        op = self._git_ops.status(id)
        if op["kind"] == "clone" and op["status"] == "ok" and op.get("result", {}).get("root"):
            self.git_open(op["result"]["root"])  # so the new clone is on the list and current
        return op

    def git_op_cancel(self, id: str) -> dict[str, Any]:
        return self._git_ops.cancel(id)

    def git_ops(self, repo: str | None = None) -> list[dict[str, Any]]:
        return self._git_ops.list(str(_folder(repo)) if repo else None)

    # ---- local work

    def git_status(self, repo: str) -> dict[str, Any]:
        r = self._repo(repo)
        st = r.status()
        st["running"] = self._git_ops.running(str(r.root))
        return st

    def git_diff(self, repo: str, path: str, staged: bool = False, base: str | None = None,
                 target: str | None = None, notebook: bool = True) -> dict[str, Any]:
        return self._repo(repo).diff(path, staged=staged, base=base, target=target, notebook=notebook)

    def git_stage(self, repo: str, paths: list[str]) -> dict[str, Any]:
        return self._repo(repo).stage(paths)

    def git_stage_all(self, repo: str) -> dict[str, Any]:
        return self._repo(repo).stage_all()

    def git_unstage(self, repo: str, paths: list[str] | None = None) -> dict[str, Any]:
        return self._repo(repo).unstage(paths)

    def git_discard(self, repo: str, paths: list[str], confirm: bool = False) -> dict[str, Any]:
        return self._repo(repo).discard(paths, confirm=confirm)

    def git_scan(self, repo: str) -> dict[str, Any]:
        """Possible secrets in what is staged, before committing."""
        return {"findings": self._repo(repo).scan_staged()}

    def git_commit(self, repo: str, message: str, allow_secrets: bool = False) -> dict[str, Any]:
        try:
            return self._repo(repo).commit(message, allow_secrets=allow_secrets)
        except GitError as exc:
            if exc.kind == "secrets":
                return {"blocked": "secrets", "message": str(exc), "findings": getattr(exc, "findings", [])}
            raise

    def git_log(self, repo: str, ref: str | None = None, limit: int = 50, skip: int = 0,
                path: str | None = None) -> dict[str, Any]:
        return self._repo(repo).log(ref, limit, skip, path)

    def git_commit_detail(self, repo: str, commit: str) -> dict[str, Any]:
        return self._repo(repo).commit_detail(commit)

    def git_compare(self, repo: str, base: str, target: str) -> dict[str, Any]:
        return self._repo(repo).compare(base, target)

    def git_file_at(self, repo: str, commit: str, path: str) -> dict[str, Any]:
        return self._repo(repo).file_at(commit, path)

    def git_branches(self, repo: str) -> dict[str, Any]:
        r = self._repo(repo)
        return {**r.branches(), "stashes": r.stashes()}

    def git_create_branch(self, repo: str, name: str, start: str | None = None, switch: bool = True) -> dict[str, Any]:
        return self._repo(repo).create_branch(name, start, switch)

    def git_switch(self, repo: str, name: str, mode: str | None = None) -> dict[str, Any]:
        try:
            return self._repo(repo).switch(name, mode)
        except GitError as exc:
            if exc.kind == "dirty":
                return {"needs_choice": True, "message": str(exc), "changes": getattr(exc, "changes", [])}
            raise

    def git_delete_branch(self, repo: str, name: str, confirm: bool = False, force: bool = False) -> dict[str, Any]:
        return self._repo(repo).delete_branch(name, confirm=confirm, force=force)

    def git_restore_stash(self, repo: str, ref: str) -> dict[str, Any]:
        return self._repo(repo).restore_stash(ref)

    def git_abort_merge(self, repo: str, confirm: bool = False) -> dict[str, Any]:
        return self._repo(repo).abort_merge(confirm=confirm)

    def git_settings(self, repo: str) -> dict[str, Any]:
        return self._repo(repo).settings()

    def git_set_identity(self, repo: str, name: str, email: str) -> dict[str, Any]:
        return self._repo(repo).set_identity(name, email)

    def git_add_remote(self, repo: str, url: str, name: str = "origin") -> dict[str, Any]:
        return {"remotes": self._repo(repo).add_remote(name, url)}

    def git_path_info(self, path: str) -> dict[str, Any] | None:
        """Which opened repository holds ``path``, its branch and the file's state; None when none does.
        Used by the notebook editor to show the notebook's branch."""
        try:
            p = Path(path).resolve()
        except (OSError, ValueError):
            return None
        for r in self._git_saved()["repos"]:
            root = Path(r["path"])
            if root.is_dir() and p.is_relative_to(root):
                try:
                    repo = Repository(root)
                    st = repo.status()
                except GitError:
                    return None
                rel = p.relative_to(repo.root).as_posix()
                state = next((e["state"] for group in ("conflicts", "staged", "unstaged", "untracked")
                              for e in st[group] if e["path"] == rel), None)
                return {"repo": str(repo.root), "name": repo.root.name, "branch": st["branch"], "path": rel,
                        "state": state, "changed": st["changed"]}
        return None

    def git_notebook_info(self, name: str) -> dict[str, Any] | None:
        return self.git_path_info(str(self.notebooks.path(name)))  # type: ignore[attr-defined]


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, ValueError):
        return False
