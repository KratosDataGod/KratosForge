"""Fabric workspace Git awareness (Git-native Phase 2, docs/git-integration-plan.md).

Fabric's own Git integration ties a workspace to a branch and folder; this is where KratosForge
shows that relationship. ``fabric_item_support`` is the artifact capability registry
(``artifacts.py``) and reads nothing from Fabric. The ``fabric_git_*`` calls read the workspace's
Git connection (Get Connection, with the scope sign-in already asks for), keep the link in the
repository's ``fabric-desktop.toml``, and line up this PC, the Git remote and the workspace.
None of them writes to Fabric or to Git; linking writes one file in the repository, to commit.
"""

from __future__ import annotations

from typing import Any

from . import artifacts, fabric_git
from .auth import GIT_STATUS_SCOPES
from .fabric_api import FabricError
from .git.cli import GitError


class FabricGitMixin:
    def fabric_item_support(self, item_type: str | None = None) -> dict[str, Any]:
        """The whole registry, or one type's entry (an unknown type comes back as not supported)."""
        if item_type is None:
            return artifacts.registry()
        return {"checked": artifacts.SOURCES_CHECKED, "item": artifacts.capability(str(item_type)).as_dict()}

    def _fabric_connection(self, workspace_id: str) -> dict[str, Any]:
        return fabric_git.connection(self.fabric.git_connection(workspace_id))  # type: ignore[attr-defined]

    def fabric_git_links(self, repo: str) -> dict[str, Any]:
        """The workspaces this repository is linked to, per environment. Reads the repository only."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        return fabric_git.read_links(r.root)

    def fabric_git_check(self, repo: str, workspace_id: str) -> dict[str, Any]:
        """What Fabric says the workspace follows, and which of this repository's remotes (if any)
        points there. A read of Fabric (Get Connection); nothing is saved."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        conn = self._fabric_connection(fabric_git.guid(workspace_id))
        return {"connection": conn, "remote": fabric_git.matching_remote(conn, r.remotes())}

    def fabric_git_link(self, repo: str, env: str, workspace_id: str, workspace_name: str = "",
                        confirm: bool = False) -> dict[str, Any]:
        """Link the repository to a workspace for one environment, in ``fabric-desktop.toml``.
        Reads the workspace's Git connection first. A workspace that isn't connected to Git, or
        follows a repository none of the remotes points to, is linked only with ``confirm``."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        env = fabric_git.env_name(env)
        ws = fabric_git.guid(workspace_id)
        conn = self._fabric_connection(ws)
        remote = fabric_git.matching_remote(conn, r.remotes())
        problem = ("Fabric says this workspace isn't connected to Git." if not conn["connected"] else
                   None if remote else
                   f"The workspace follows {conn['owner']}/{conn['repository']}, which none of this repository's "
                   "remotes points to.")
        if problem and not confirm:
            return {"needs_confirm": True, "message": problem + " Link it anyway?", "connection": conn}
        link = {"workspace_id": ws, "workspace_name": workspace_name,
                "provider": conn["provider"], "host": conn["host"], "owner": conn["owner"], "project": conn["project"],
                "repository": conn["repository"], "branch": conn["branch"], "folder": conn["folder"]}
        out = fabric_git.write_link(r.root, env, link)
        return {**out, "connection": conn, "remote": remote, "status": r.status()}

    def fabric_git_unlink(self, repo: str, env: str) -> dict[str, Any]:
        """Remove one environment's link from ``fabric-desktop.toml``. Nothing in Fabric changes."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        out = fabric_git.write_link(r.root, env, None)
        return {**out, "status": r.status()}

    def fabric_git_overview(self, repo: str, env: str = "dev") -> dict[str, Any]:
        """This PC, the Git remote and the linked workspace, side by side. Reads Fabric's Git
        connection and the repository; doesn't fetch, so the remote is as of the last fetch."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        env = fabric_git.env_name(env)
        links = fabric_git.read_links(r.root)
        link = links["envs"].get(env)
        if links["problem"] or not link:
            return {"env": env, "linked": False, "problem": links["problem"], "link": None}
        try:
            conn = self._fabric_connection(link["workspace_id"])
        except FabricError as exc:
            return {"env": env, "linked": True, "link": link, "problem": str(exc), "connection": None}
        remote = fabric_git.matching_remote(conn, r.remotes())
        moved = [k for k in ("repository", "branch", "folder") if link.get(k, "") != conn.get(k, "")] \
            if conn["connected"] else []
        return {"env": env, "linked": True, "link": link, "problem": None, "connection": conn,
                "changed_in_fabric": moved, "alignment": fabric_git.alignment(r, conn, remote)}

    def fabric_git_status(self, repo: str, env: str = "dev") -> dict[str, Any]:
        """Which items differ between the linked workspace and its Git branch, and which conflict
        (Fabric's Get Status, a long-running read). Asks for Workspace.GitUpdate.All the first time.
        Changes nothing in Fabric or in Git. The answer's ``fingerprint`` is what Commit to Git and
        Update from Git will check again before they act."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        env = fabric_git.env_name(env)
        links = fabric_git.read_links(r.root)
        link = links["envs"].get(env)
        if links["problem"]:
            raise GitError(links["problem"], kind="invalid")
        if not link:
            raise GitError(f"Link this repository to a {env.title()} workspace first.", kind="invalid")
        client = self._scoped(GIT_STATUS_SCOPES)  # type: ignore[attr-defined]
        try:
            raw = client.git_status(link["workspace_id"])
        except FabricError as exc:
            if exc.code == "WorkspaceNotConnectedToGit":
                raise FabricError("Fabric says this workspace isn't connected to Git any more. A workspace admin "
                                  "connects it in Fabric (Workspace settings > Git integration).", exc.code,
                                  exc.status, exc.request_id) from None
            raise
        out = fabric_git.status(raw)
        tracking = None
        remote = fabric_git.remote_for_link(link, r.remotes())
        branch = link.get("branch", "")
        if remote and branch and not branch.startswith("-") and ".." not in branch:
            tracking = (r._try("rev-parse", "--verify", "-q", "--end-of-options",
                               f"refs/remotes/{remote}/{branch}^{{commit}}") or "").strip() or None
        out["remote_tracking"] = tracking
        out["remote_fetched_matches"] = bool(tracking) and tracking == out["remote_commit"]
        return {"env": env, "link": link, **out}

    def fabric_git_update(self, repo: str, env: str, fingerprint: str, policy: str = "",
                          confirm_name: str = "") -> dict[str, Any]:
        """Update the linked workspace from Git, the only Fabric write on this tab. Every call reads
        Fabric's status again first and acts only when nothing changed since the preview the person
        saw (``fingerprint``), every conflict has a choice (``policy``) and ``confirm_name`` is the
        workspace's name exactly as linked. Otherwise it returns what's missing and writes nothing:
        ``stale`` (with the new status), ``needs_choice``, ``needs_confirm`` (with the plan) or
        ``nothing``. Fabric itself also refuses if the workspace moved meanwhile (WorkspaceHeadMismatch)."""
        r = self._repo(repo)  # type: ignore[attr-defined]
        env = fabric_git.env_name(env)
        links = fabric_git.read_links(r.root)
        link = links["envs"].get(env)
        if links["problem"] or not link:
            raise GitError(links["problem"] or f"Link this repository to a {env.title()} workspace first.", kind="invalid")
        client = self._scoped(GIT_STATUS_SCOPES)  # type: ignore[attr-defined]
        st = fabric_git.status(client.git_status(link["workspace_id"]))
        plan = fabric_git.update_plan(st, policy)
        name = link.get("workspace_name") or link["workspace_id"]
        base = {"env": env, "plan": plan, "confirm_text": name, "fingerprint": st["fingerprint"]}
        if not fingerprint or fingerprint != st["fingerprint"]:
            return {**base, "stale": True, "status": st, "done": False}
        if plan["nothing"] or not st["remote_commit"]:
            return {**base, "nothing": True, "done": False}
        if plan["needs_policy"]:
            return {**base, "needs_choice": True, "done": False}
        if confirm_name != name:
            return {**base, "needs_confirm": True, "done": False}
        client.git_update_from_git(link["workspace_id"], st["remote_commit"], st["workspace_head"],
                                   fabric_git.POLICIES[policy], allow_override=True)
        return {**base, "done": True}
