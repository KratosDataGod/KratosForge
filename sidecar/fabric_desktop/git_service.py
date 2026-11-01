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
from . import jsonfile, vscode_setup
from .git.cli import GitError
from .git import capacity_files, platform_files, scopes
from .git.ops import Operations
from .git.repo import Repository, _folder, init as git_init
from .integrations.config import write_cli_launcher

MAX_REPOS = 50
_OLDER_FUNCTIONS = ("The platform's functions are older than this app. Open Platform > Settings, choose "
                    "Finish or repair, then try again.")


def _newer_op(cat: Any, op: str, **kwargs: Any) -> Any:
    """A platform read that came with an update (landing zones, policy templates). A UDF_Platform made
    before it has no such function: Fabric answers 404 (LookupError), or the platform says it has no
    such operation. Either way, say what to do."""
    from .central.core import PlatformError

    try:
        return cat.call(op, **kwargs)
    except LookupError:
        raise GitError(_OLDER_FUNCTIONS, kind="invalid") from None
    except PlatformError as exc:
        text = str(exc).lower()
        if op in str(exc) and ("not found" in text or "no operation" in text):
            raise GitError(_OLDER_FUNCTIONS, kind="invalid") from None
        raise


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
            data = jsonfile.read_json(self.git_repos_file, None)
            if isinstance(data, dict) and isinstance(data.get("repos"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"repos": [], "current": None}

    def _git_save(self, data: dict[str, Any]) -> None:
        jsonfile.write_text(self.git_repos_file, json.dumps(data, indent=2))

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
        repos = []
        for r in data["repos"]:
            if not r.get("path"):
                continue
            exists = Path(r["path"]).is_dir()
            repos.append({**r, "exists": exists, **(scopes.describe(r["path"]) if exists else
                                                    {"kind": None, "suggested": None})})
        return {"repos": repos, "current": data.get("current"), "kinds": scopes.KINDS}

    def _notebook_places(self) -> list[tuple[str, str, Path]]:
        """The notebooks repositories this PC opened, as places notebooks can be saved."""
        out = []
        for r in self._git_saved()["repos"]:
            root = r.get("path")
            if root and Path(root).is_dir() and scopes.read_kind(root) == "notebooks":
                out.append((root, r.get("name") or Path(root).name, Path(root) / "notebooks"))
        return out

    def _git_locations_of(self, root: str) -> dict[str, Any]:
        return scopes.read_locations(root)

    def git_locations(self, repo: str) -> dict[str, Any]:
        """Where a notebooks repository's notebooks find their data, per environment."""
        r = self._notebooks_repo(repo)
        return scopes.read_locations(r.root)

    def git_set_locations(self, repo: str, envs: dict[str, Any]) -> dict[str, Any]:
        """Writes the repository's locations.toml (ids and names only). It shows as a change to
        commit; nothing in Fabric changes."""
        r = self._notebooks_repo(repo)
        if not isinstance(envs, dict):
            raise GitError("Locations are given per environment.", kind="invalid")
        return scopes.write_locations(r.root, envs) | {"status": r.status()}

    def _notebooks_repo(self, repo: str) -> Repository:
        r = self._repo(repo)
        if scopes.read_kind(r.root) != "notebooks":
            raise GitError("Locations belong to a notebooks repository.", kind="invalid")
        return r

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

    def git_init(self, path: str, branch: str = "main", kind: str | None = None) -> dict[str, Any]:
        """Makes an existing folder a repository. With ``kind``, also writes the marker saying what
        it holds (the folder's own files are left as they are)."""
        if kind:
            scopes.check_kind(kind)
            if (_folder(path, must_exist=False) / scopes.MARKER).exists():
                raise GitError(f"This folder already says what it holds in {scopes.MARKER}. "
                               "Make it a repository first, then choose what it holds.", kind="confirm")
        repo = git_init(path, branch)
        if kind:
            scopes.set_kind(repo, kind)
        self._git_remember(repo)
        return repo.status()

    def git_create(self, path: str, kind: str, branch: str = "main") -> dict[str, Any]:
        """A new, empty folder made a repository of ``kind`` with that kind's starting files."""
        repo = scopes.create(path, kind, branch)
        self._git_remember(repo)
        return repo.status()

    def git_set_kind(self, repo: str, kind: str, confirm: bool = False) -> dict[str, Any]:
        """Says what an opened repository holds, by writing its marker file (a change to commit)."""
        r = self._repo(repo)
        return {**scopes.set_kind(r, kind, confirm), "status": r.status()}

    # ---- a capacity repository: capacity.toml, and a drift report against Fabric (reads only)

    def _capacity_repo(self, repo: str) -> Repository:
        r = self._repo(repo)
        if scopes.read_kind(r.root) != "capacity":
            raise GitError("This repository doesn't hold capacity definitions. Create a capacity repository, "
                           "or choose what this one holds.", kind="invalid")
        return r

    def _fabric_capacities(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The capacities and workspaces Fabric shows the signed-in person. Only reads."""
        from .auth import CAPACITY_SCOPES
        from .fabric_api import FabricClient

        caps = FabricClient(lambda: self.auth._token(CAPACITY_SCOPES)).list_capacities()  # type: ignore[attr-defined]
        return caps, self.fabric.list_workspaces()  # type: ignore[attr-defined]

    def git_capacity_diff(self, repo: str) -> dict[str, Any]:
        """The drift report: the repository's capacity.toml compared with what Fabric shows.
        Reads only: nothing in the repository or in Fabric changes."""
        r = self._capacity_repo(repo)
        git = capacity_files.read(r.root)
        caps, workspaces = self._fabric_capacities()
        items = capacity_files.compare(git["capacities"], capacity_files.from_fabric(caps, workspaces), workspaces)
        return {"items": items, "counts": capacity_files.summary(items), "problems": git["problems"],
                "read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def git_capacity_write(self, repo: str, confirm: bool = False) -> dict[str, Any]:
        """Writes what Fabric shows into capacity.toml, as a change to review. Capacities this
        person can't see keep their sections. Nothing is committed and nothing in Fabric changes."""
        r = self._capacity_repo(repo)
        st = r.status()
        uncommitted = any(e["path"] == capacity_files.FILE
                          for k in ("staged", "unstaged", "untracked", "conflicts") for e in st.get(k) or [])
        git = capacity_files.read(r.root)
        if git["problems"]:
            raise GitError(f"{git['problems'][0]} Fix the file first, so nothing in it is lost.", kind="invalid")
        caps, workspaces = self._fabric_capacities()
        try:
            out = capacity_files.write(r.root, git["capacities"], capacity_files.from_fabric(caps, workspaces),
                                       uncommitted, confirm, workspaces)
        except GitError as exc:
            if exc.kind == "confirm":
                return {"needs_confirm": True, "message": str(exc)}
            raise
        return out | {"status": r.status()}

    # ---- a platform repository: definitions in Git, deployed in each environment's platform

    def _platform_repo(self, repo: str) -> Repository:
        r = self._repo(repo)
        if scopes.read_kind(r.root) != "platform":
            raise GitError("This repository doesn't hold platform definitions. Create a platform repository, "
                           "or choose what this one holds.", kind="invalid")
        return r

    def _deployed(self, env: str | None = None) -> dict[str, Any]:
        """What the platform has deployed, as platform-file records. Only reads, with the calls the
        Platform and Policies pages make. ``env`` None: what export writes (the catalog, and key
        tables from the shared keys database, or from every environment until keys have moved there);
        otherwise what that environment's platform and keys hold."""
        from .central.service import CATALOG_ENV
        from .meta.project import ENVIRONMENTS

        if env is not None and env not in ENVIRONMENTS:
            raise GitError(f"{env!r} isn't an environment.", kind="invalid")
        cat = self._catalog()  # type: ignore[attr-defined]
        if env is not None:
            tables = {env: self._keys_platform(env).call("list_key_tables")}  # type: ignore[attr-defined]
            admins = self._platform(env).call("list_admins")  # type: ignore[attr-defined]
        else:
            where = sorted(self._key_platforms(), key=lambda e: e[0] != CATALOG_ENV)  # type: ignore[attr-defined]
            tables = {e: client.call("list_key_tables") for e, client in where}
            admins = cat.call("list_admins")
        # policies_ready only looks; list_policies would create the policy tables on an older platform.
        try:
            ready = cat.call("policies_ready")
        except LookupError:  # a platform whose functions predate this check
            raise GitError(_OLDER_FUNCTIONS, kind="invalid") from None
        policies = templates = None
        if ready:  # templates sit with policies: reading them on a platform without policies would make their table
            policies = [cat.call("get_policy", id=p["id"]) for p in cat.call("list_policies")]
            templates = _newer_op(cat, "list_policy_templates")["templates"]
        return platform_files.from_platform({
            "domains": cat.call("list_domains"), "sources": cat.call("list_sources"), "key_tables": tables,
            "landing_zones": _newer_op(cat, "list_landing_zones"),
            "glossary": cat.call("list_glossary"), "admins": admins, "shortcut_rules": cat.call("shortcut_rules"),
            "policies": policies, "policy_templates": templates, "projects": cat.call("list_projects")})

    def git_platform_diff(self, repo: str, env: str = "prod") -> dict[str, Any]:
        """The platform repository's files compared with what ``env``'s platform has deployed.
        Reads only: nothing in the repository or in Fabric changes."""
        r = self._platform_repo(repo)
        git = platform_files.read_repo(r.root)
        deployed = self._deployed(env)
        items = platform_files.compare(git, deployed)
        return {"env": env, "items": items, "counts": platform_files.summary(items), "problems": git["problems"],
                "policies_set_up": deployed["policies"] is not None, "labels": platform_files.LABELS}

    def git_platform_export(self, repo: str, confirm: bool = False) -> dict[str, Any]:
        """Writes what is deployed into the platform repository, one file per object, as changes to
        review. Nothing is committed and nothing in Fabric changes. A file with uncommitted changes
        is replaced only with ``confirm``; files for objects that aren't deployed are left alone."""
        r = self._platform_repo(repo)
        st = r.status()
        uncommitted = {e["path"] for k in ("staged", "unstaged", "untracked", "conflicts") for e in st.get(k) or []}
        defs = self._deployed()
        try:
            out = platform_files.export(r.root, defs, uncommitted, confirm)
        except GitError as exc:
            if exc.kind == "confirm":
                return {"needs_confirm": True, "message": str(exc), "changes": getattr(exc, "changes", [])}
            raise
        return out | {"disagree": defs["disagree"], "policies_set_up": defs["policies"] is not None,
                      "status": r.status()}

    def _deployed_envs(self) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str]:
        """Each distinct platform's deployed records, its client, and which one holds the catalog;
        plus "keys", the shared keys database's admins and key tables, once keys have moved there."""
        from .central.service import CATALOG_ENV

        def ident(client: Any) -> str:
            return getattr(client, "functions_id", None) or str(getattr(client, "path", ""))

        catalog = ident(self._catalog())  # type: ignore[attr-defined]
        envs, clients, cat_env = {}, {}, CATALOG_ENV
        for env, client in self._distinct_platforms():  # type: ignore[attr-defined]
            envs[env], clients[env] = self._deployed(env), client
            if ident(client) == catalog:
                cat_env = env
        if self._keys_config().get("shared"):  # type: ignore[attr-defined]
            keys = clients["keys"] = self._shared_keys()  # type: ignore[attr-defined]
            d = platform_files.from_platform({"admins": keys.call("list_admins"),
                                              "key_tables": {"keys": keys.call("list_key_tables")}})
            envs["keys"] = {"admins": d["admins"], "key_tables": d["key_tables"]}
        return envs, clients, cat_env

    def git_platform_deploy(self, repo: str, remove: list[dict[str, Any]] | None = None,
                            confirm: str = "") -> dict[str, Any]:
        """Makes the platform match the platform repository's committed files: the catalog, admins
        in every environment, and key tables in the shared keys database (in every environment
        until keys have moved there). Without ``confirm`` it only returns the plan;
        with that plan's id it runs it, step by step, stopping at the first refusal. Objects only
        deployed are removed only when listed in ``remove``. Platform admins only; each step is
        audited by the platform as the person deploying."""
        r = self._platform_repo(repo)
        st = r.status()
        if any(st.get(k) for k in ("staged", "unstaged", "untracked", "conflicts")):
            raise GitError("Commit the changes in this repository first, so what is deployed is in Git's history.",
                           kind="dirty")
        if not st.get("commit"):
            raise GitError("Commit the platform files first.", kind="invalid")
        # Files Git ignores aren't in any commit, so a deploy that read them wouldn't be "from" one.
        paths = [*platform_files.FOLDERS.values(), *platform_files.FILES.values()]
        ignored = [x for x in r.git("ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", *paths,
                                    read_only=True).split("\0") if platform_files.is_platform_file(x)]
        if ignored:
            raise GitError(f"Git ignores {', '.join(sorted(ignored)[:5])}{' and more' if len(ignored) > 5 else ''}, so "
                           "it isn't in any commit. Move it out of the platform folders or stop ignoring it and "
                           "commit it, then deploy.", kind="dirty")
        if not self._catalog().call("whoami").get("admin"):  # type: ignore[attr-defined]
            raise GitError("Only platform admins deploy platform definitions.", kind="denied")
        envs, clients, cat_env = self._deployed_envs()
        ticked = {(str(x.get("kind")), str(x.get("id"))) for x in remove or []}
        note = f"Deployed from the platform repository at commit {st['commit'][:12]}"
        # What the commit holds, read from Git itself: exactly what the note says was deployed.
        git = platform_files.read_repo(r.root, platform_files.committed_files(r, st["commit"]))
        p = platform_files.plan(git, envs, cat_env, ticked, note)
        out = {**p, "commit": st["commit"], "catalog_env": cat_env, "labels": platform_files.LABELS}
        if p["stops"] or confirm != p["plan_id"]:
            return out | {"needs_confirm": not p["stops"], "done": []}
        done = []
        for step in p["steps"]:
            client = clients[step["env"] or cat_env]
            try:
                if step["op"] in platform_files.APP_OPS:  # the app's own create path: a zone's lakehouse first
                    res = getattr(self, step["op"])(**step["args"])
                else:
                    res = client.call(step["op"], **step["args"])
                if step["op"] == "adopt_sources" and res.get("conflicts"):  # reported, not raised: stop here
                    raise GitError("; ".join(res["conflicts"]), kind="invalid")
            except Exception as exc:  # noqa: BLE001 - stop at the first refusal and say where
                return out | {"done": done, "failed": step, "message": str(exc), "total": len(p["steps"])}
            done.append(step)
        return out | {"done": done, "deployed": True}

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

    def git_apply_hunk(self, repo: str, path: str, hunk: str, action: str, confirm: bool = False) -> dict[str, Any]:
        """Stage, unstage or discard one block of a file's changes (``hunk`` is its id from git_diff)."""
        return self._repo(repo).apply_hunk(path, hunk, action, confirm)

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

    def git_vscode_plan(self, repo: str) -> dict[str, Any]:
        """The files Set up for VS Code would add to ``repo`` (vscode_setup.py). Reads only."""
        r = self._repo(repo)
        return {"name": r.root.name} | vscode_setup.plan(r.root)

    def git_vscode_apply(self, repo: str, files: list[str]) -> dict[str, Any]:
        """Adds the chosen files that aren't there yet, and the ``kratosforge`` launcher their task
        runs. Nothing is committed: they show as changes in Source control."""
        r = self._repo(repo)
        try:
            out = vscode_setup.apply(r.root, files)
        except ValueError as exc:
            raise GitError(str(exc), kind="invalid") from exc
        write_cli_launcher(self.root)
        return out | {"status": r.status()}

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
                kind = scopes.read_kind(repo.root)
                return {"repo": str(repo.root), "name": repo.root.name, "branch": st["branch"], "path": rel,
                        "state": state, "changed": st["changed"], "kind": kind,
                        "wrong_place": scopes.wrong_place(kind, "notebooks") if rel.endswith(".ipynb") else None}
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
