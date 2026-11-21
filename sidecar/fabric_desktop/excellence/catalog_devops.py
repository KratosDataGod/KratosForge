"""DevOps controls (FEA-305): the workspace's Git connection and whether it is in step with its branch.

Every control here decides from reads the app already makes for the Git tab, with the signed-in
person's own access, and from nothing else:

- ``GET /workspaces/{id}/git/connection`` (Get Connection): the provider, the branch and the
  connection state. A read, covered by the sign-in's Workspace.ReadWrite.All.
- ``GET /workspaces/{id}/git/status`` (Get Status, a long-running read): which items differ between
  the workspace and its branch. It needs Workspace.GitUpdate.All, which the app asks for the first
  time the Git item status opens. The assessment never asks for it: it uses that permission only
  when it was granted already, and otherwise lists the read as not made.

The evidence (``workspace.git``, verified configuration) keeps the provider type, the branch name,
the connection state and counts of changed items. Never the repository, its organization or owner,
a URL, a commit hash, an item name or anything inside an item. Text is cut to 256 characters.

A connection that couldn't be read (an error, or a role that can't see it) gives no record, so these
controls are not evaluated, never passed. ``NotConnected`` is what Fabric answered, so it is a fact:
FEA-DEVOPS-003 fails on it, and the two status controls don't apply (there is no branch to be out of
step with). A status that wasn't read leaves the status controls not evaluated.

Deployment pipeline stages and drift aren't checked: the app makes no deployment pipeline read yet,
and this story adds no Fabric endpoint. Update from Git and Commit to Git are never called here.

References are to be re-checked against Microsoft Learn under FEA-106 before these controls leave
Preview.
"""

from __future__ import annotations

from typing import Any

from . import controls as ct
from . import evidence as ev

KIND = "workspace.git"
GET_CONNECTION = "GET /v1/workspaces/{id}/git/connection"
GET_STATUS = "GET /v1/workspaces/{id}/git/status"
MAX_TEXT = 256
STATES = ("NotConnected", "Connected", "ConnectedAndInitialized")

_GIT_DOC = {"title": "What is Microsoft Fabric Git integration?",
            "url": "https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration"}
_GET_STARTED_DOC = {"title": "Get started with Git integration",
                    "url": "https://learn.microsoft.com/fabric/cicd/git-integration/git-get-started"}
_PROCESS_DOC = {"title": "Basic concepts in Git integration",
                "url": "https://learn.microsoft.com/fabric/cicd/git-integration/git-integration-process"}
_LIFECYCLE_DOC = {"title": "What is lifecycle management in Microsoft Fabric?",
                  "url": "https://learn.microsoft.com/fabric/cicd/cicd-overview"}
_BEST_PRACTICES_DOC = {"title": "Lifecycle management best practices",
                       "url": "https://learn.microsoft.com/fabric/cicd/best-practices-cicd"}
_STATUS_API_DOC = {"title": "Git - Get Status (Fabric REST API)",
                   "url": "https://learn.microsoft.com/rest/api/fabric/core/git/get-status"}


def _short(value: Any) -> str:
    """One line of at most MAX_TEXT characters, or empty when it carries control characters."""
    s = str(value or "").strip()
    return "" if any(ord(c) < 32 or ord(c) == 127 for c in s) else s[:MAX_TEXT]


def status_readable(raw: Any) -> bool:
    """True when ``raw`` is shaped like Get Status's answer: a dict whose ``changes`` is a list of
    dicts. Anything else can't be counted, so it must never read as "nothing changed"."""
    changes = raw.get("changes") if isinstance(raw, dict) else None
    return isinstance(changes, list) and all(isinstance(c, dict) for c in changes)


def _counts(raw: dict[str, Any]) -> dict[str, int]:
    """Counts of the items Get Status lists: changed in the workspace and not in Git (uncommitted),
    changed in Git and not in the workspace (incoming), and both (conflicts, also in the other two)."""
    uncommitted = incoming = conflicts = 0
    changes = raw.get("changes")
    for change in changes if isinstance(changes, list) else []:
        if not isinstance(change, dict):
            continue
        conflict = change.get("conflictType")
        if conflict == "SameChanges":
            continue  # the same change on both sides: nothing to commit or bring in
        here, there = bool(change.get("workspaceChange")), bool(change.get("remoteChange"))
        if conflict == "Conflict":
            here = there = True
            conflicts += 1
        uncommitted += here
        incoming += there
    return {"uncommitted": uncommitted, "incoming": incoming, "conflicts": conflicts}


def evidence(ws: dict[str, Any], connection: dict[str, Any] | None, status: dict[str, Any] | None, *,
             collected_at: str) -> list[dict[str, Any]]:
    """One ``workspace.git`` record when the connection was read (``None``: it wasn't, so no record).
    ``status`` is Get Status's answer, or None when it wasn't read (or there is no branch to read)."""
    if not isinstance(connection, dict):
        return []
    state = connection.get("gitConnectionState")
    state = state if state in STATES else None
    details = connection.get("gitProviderDetails")
    details = details if isinstance(details, dict) else {}
    connected = state in ("Connected", "ConnectedAndInitialized")
    payload = {"state": state,
               "provider": _short(details.get("gitProviderType")) if connected else "",
               "branch": _short(details.get("branchName")) if connected else "",
               "status": _counts(status) if status_readable(status) and state == "ConnectedAndInitialized" else None}
    subj = ev.subject("workspace", str(ws.get("id") or ""), ws.get("displayName") or ws.get("name"))
    source = GET_CONNECTION + (" and " + GET_STATUS if payload["status"] is not None else "")
    return [ev.record(KIND, subj, source=source, confidence="verified_configuration", collected_at=collected_at,
                      payload=payload)]


# ---- controls


def _git(ctx: ct.Context) -> tuple[str, dict[str, Any]]:
    r = ctx.one(KIND)
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], r["payload"] or {}


def _where(p: dict[str, Any]) -> str:
    provider = {"AzureDevOps": "Azure DevOps", "GitHub": "GitHub"}.get(p.get("provider") or "", p.get("provider") or "")
    branch = p.get("branch") or ""
    return " ".join(x for x in (provider, f"branch {branch}" if branch else "") if x) or "a Git branch"


def _connected(ctx: ct.Context) -> dict[str, Any]:
    """FEA-DEVOPS-003, severity medium: a workspace that isn't in Git has no history of its items, no
    review before a change reaches it and no earlier version to go back to, so a broken or deleted
    notebook, pipeline or model can't be restored from source. That is a real lifecycle weakness, but
    it neither exposes data nor stops anything running, so it isn't high. "Not connected" is what
    Fabric answered (verified configuration), not a read that failed, so it fails rather than being
    not evaluated; a sandbox someone keeps out of Git on purpose can turn the control off in a
    profile. Connected but never synced also fails: nothing is in Git yet."""
    rid, p = _git(ctx)
    state = p.get("state")
    if state is None:
        return ct.not_evaluated("Fabric answered with a Git connection state that isn't documented.",
                                needs="a documented connection state")
    if state == "NotConnected":
        return ct.failed([rid], "Fabric says this workspace isn't connected to Git.")
    if state == "Connected":
        return ct.failed([rid], f"The workspace is connected to {_where(p)}, but its first sync was never done, so "
                                "none of its items is in Git yet.", detail={"state": state})
    return ct.passed([rid], f"Connected to {_where(p)} and synced.")


def _status(ctx: ct.Context, what: str) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    rid, p = _git(ctx)
    state = p.get("state")
    if state == "NotConnected":
        return rid, p, ct.not_applicable("The workspace isn't connected to Git, so there is no branch for it to be "
                                         f"{what} (FEA-DEVOPS-003 covers the connection).")
    if state != "ConnectedAndInitialized":
        return rid, p, ct.not_evaluated("The workspace's first sync with Git was never done, so which items differ "
                                        "can't be read." if state == "Connected" else
                                        "Fabric answered with a Git connection state that isn't documented.",
                                        needs="a synced Git connection")
    if not isinstance(p.get("status"), dict):
        return rid, p, ct.not_evaluated("Which items differ from Git wasn't read.",
                                        needs="the workspace's Git status (Workspace.GitUpdate.All)")
    return rid, p, None


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _uncommitted(ctx: ct.Context) -> dict[str, Any]:
    """FEA-DEVOPS-004, severity low: a change made in the workspace and not committed has no history
    and no review, and Update from Git can overwrite it. Uncommitted work is normal while someone is
    building, and nothing is lost until a later update, so it is low; a long-lived uncommitted change
    in a production workspace is the case this control is for. Verified from Fabric's Get Status."""
    rid, p, skip = _status(ctx, "out of step with")
    if skip:
        return skip
    st = p["status"]
    n, conflicts = int(st.get("uncommitted") or 0), int(st.get("conflicts") or 0)
    if n:
        extra = f", {conflicts} of them in conflict" if conflicts else ""
        return ct.failed([rid], f"{_plural(n, 'item has', 'items have')} changes in the workspace that aren't "
                                f"committed to {_where(p)}{extra}.",
                         detail={"uncommitted": n, "conflicts": conflicts})
    return ct.passed([rid], f"Every change in the workspace is committed to {_where(p)}.")


def _behind(ctx: ct.Context) -> dict[str, Any]:
    """FEA-DEVOPS-005, severity low: changes merged into the branch and not brought into the workspace
    mean it runs something other than what was reviewed, so a fix in Git isn't live yet. Waiting
    for a planned update is legitimate and nothing is lost meanwhile, so it is low. Verified from
    Fabric's Get Status. The control never updates the workspace."""
    rid, p, skip = _status(ctx, "behind")
    if skip:
        return skip
    st = p["status"]
    n, conflicts = int(st.get("incoming") or 0), int(st.get("conflicts") or 0)
    if n:
        extra = f", {conflicts} of them in conflict" if conflicts else ""
        return ct.failed([rid], f"{_plural(n, 'item has', 'items have')} changes in {_where(p)} that aren't in the "
                                f"workspace yet{extra}.",
                         detail={"incoming": n, "conflicts": conflicts})
    return ct.passed([rid], f"The workspace has every change in {_where(p)}.")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


def _ws(**kw: Any) -> ct.Control:
    return ct.Control(version="1.0.0", dimension="devops", scope="workspace", evidence_kinds=(KIND,), **kw)


CONTROLS = (
    _ws(id="FEA-DEVOPS-003", title="Workspace isn't connected to Git", severity="medium",
        requirement="microsoft_recommendation", evaluate=_connected,
        references=(_GIT_DOC, _GET_STARTED_DOC, _LIFECYCLE_DOC),
        description="Fabric says the workspace isn't connected to a Git branch, or is connected and was never "
                    "synced.",
        remediation=_t("Without Git, the workspace's items have no history, no review before a change lands and no "
                       "earlier version to go back to.",
                       "Connect the workspace to a branch of an Azure DevOps or GitHub repository, and do the first "
                       "sync. If this workspace is a sandbox kept out of Git on purpose, turn this control off in "
                       "your profile.",
                       ["Agree the repository, branch and folder this workspace follows.",
                        "A workspace admin opens Workspace settings > Git integration and connects it.",
                        "Do the first sync (commit the workspace's items, or update from the branch).",
                        "Assess again."],
                       "Every change to the workspace's items is recorded, can be reviewed and can be undone.",
                       "low", "low", "Fabric reports the workspace connected and synced, and the control passes.",
                       "Disconnect the workspace from Git in Workspace settings. Its items stay as they are.")),
    _ws(id="FEA-DEVOPS-004", title="Workspace has changes not committed to Git", severity="low",
        requirement="kratosforge_recommendation", evaluate=_uncommitted,
        references=(_PROCESS_DOC, _STATUS_API_DOC, _BEST_PRACTICES_DOC),
        description="Fabric's Git status lists items changed in the workspace and not in its branch. Not applicable "
                    "when the workspace isn't connected to Git.",
        remediation=_t("A change made in the workspace and not committed has no history and no review, and an "
                       "update from Git can overwrite it.",
                       "Commit the changes to the branch, or undo the ones nobody needs.",
                       ["Open the workspace's Source control panel in Fabric.",
                        "Check each changed item with its owner.",
                        "Commit what should be kept, and undo the rest.",
                        "Resolve any conflict before committing.", "Assess again."],
                       "Every change is in Git, where it can be reviewed and undone.", "low", "low",
                       "The Source control panel shows nothing to commit, and the control passes.",
                       "Revert the commit in Git.")),
    _ws(id="FEA-DEVOPS-005", title="Workspace is behind its Git branch", severity="low",
        requirement="kratosforge_recommendation", evaluate=_behind,
        references=(_PROCESS_DOC, _STATUS_API_DOC, _BEST_PRACTICES_DOC),
        description="Fabric's Git status lists items changed in the branch and not yet in the workspace. Not "
                    "applicable when the workspace isn't connected to Git.",
        remediation=_t("Changes merged into the branch aren't live until someone updates the workspace from Git, so "
                       "it runs something other than what was reviewed.",
                       "Update the workspace from Git when the change is meant to go live, after resolving any "
                       "conflict.",
                       ["Open the workspace's Source control panel in Fabric, Updates tab.",
                        "Check which items the update changes, and whether any item was changed in the workspace too.",
                        "Resolve conflicts, keeping the workspace's or the branch's version on purpose.",
                        "Update the workspace.", "Assess again."],
                       "The workspace runs what the branch holds.", "low", "medium",
                       "The Source control panel shows no updates, and the control passes.",
                       "Revert the change in Git and update the workspace again.")),
)
