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
from .fabric_api import FabricError


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
                "provider": conn["provider"], "owner": conn["owner"], "project": conn["project"],
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
