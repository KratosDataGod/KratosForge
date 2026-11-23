"""The first controls (FEA-105), built on evidence the app already reads (excellence.adapters).

Each control says what it checks, why, how to fix it, how to check the fix and how to undo it, and
cites the Microsoft page it builds on. Where Microsoft gives a principle (least privilege) but no
number, the threshold is KratosForge's and the control says so (requirement
``kratosforge_recommendation``); a profile can turn it off or change its severity.

References are to be re-checked against Microsoft Learn under FEA-106 before these controls leave
Preview.
"""

from __future__ import annotations

from typing import Any

from . import catalog_architecture, catalog_artifacts, catalog_devops, catalog_models, catalog_warehouse
from . import controls as ct

WRITE_ROLES = ("Admin", "Member", "Contributor")
MAX_ADMINS = 4
MAX_DIRECT_WRITERS = 5

_ROLES_DOC = {"title": "Roles in workspaces in Microsoft Fabric",
              "url": "https://learn.microsoft.com/fabric/fundamentals/roles-workspaces"}
_ACCESS_DOC = {"title": "Give users access to workspaces",
               "url": "https://learn.microsoft.com/fabric/fundamentals/give-access-workspaces"}
_SECURITY_DOC = {"title": "Security in Microsoft Fabric", "url": "https://learn.microsoft.com/fabric/security/security-overview"}
_B2B_DOC = {"title": "B2B collaboration overview", "url": "https://learn.microsoft.com/entra/external-id/what-is-b2b"}
_WORKSPACE_DOC = {"title": "Create a workspace", "url": "https://learn.microsoft.com/fabric/fundamentals/create-workspaces"}
_PAUSE_DOC = {"title": "Pause and resume your capacity", "url": "https://learn.microsoft.com/fabric/enterprise/pause-resume"}
_DELTA_DOC = {"title": "Lakehouse and Delta Lake tables",
              "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-and-delta-tables"}
_COPY_DOC = {"title": "Copy activity in Data Factory for Microsoft Fabric",
             "url": "https://learn.microsoft.com/fabric/data-factory/copy-data-activity"}
_SHORTCUT_DOC = {"title": "OneLake shortcuts", "url": "https://learn.microsoft.com/fabric/onelake/onelake-shortcuts"}
_ENGINE_DOC = {"title": "KratosForge optimization engine",
               "url": "https://kratosdatagod.github.io/kratosforge/optimize/engine/"}


def _assignments(ctx: ct.Context) -> tuple[str, list[dict[str, Any]]]:
    r = ctx.one("workspace.roles")
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], list(r["payload"].get("assignments") or [])


def _names(people: list[dict[str, Any]], limit: int = 5) -> str:
    names = [p["name"] for p in people][:limit]
    more = len(people) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


# ---- security


def _admins(ctx: ct.Context) -> dict[str, Any]:
    rid, people = _assignments(ctx)
    admins = [p for p in people if p["role"] == "Admin"]
    if len(admins) > MAX_ADMINS:
        return ct.failed([rid], f"{len(admins)} principals are Admin of this workspace: {_names(admins)}.",
                         detail={"admins": len(admins), "threshold": MAX_ADMINS})
    return ct.passed([rid], f"{len(admins)} Admin assignment{'s' if len(admins) != 1 else ''} (at most {MAX_ADMINS}).")


def _guests(ctx: ct.Context) -> dict[str, Any]:
    rid, people = _assignments(ctx)
    writers = [p for p in people if p["role"] in WRITE_ROLES]
    guests = [p for p in writers if p["guest"] is True]
    if guests:
        return ct.failed([rid], f"{len(guests)} guest account{'s' if len(guests) != 1 else ''} from another organization "
                                f"can change this workspace: {_names(guests)}.", detail={"guests": len(guests)})
    unknown = [p for p in writers if p["guest"] is None]
    if unknown:
        return ct.not_evaluated(f"Fabric didn't send the sign-in name of {_names(unknown)}, so whether "
                                f"{'they are guests' if len(unknown) != 1 else 'this is a guest'} isn't known.",
                                needs="the users' sign-in names")
    return ct.passed([rid], "No directly assigned guest account has a role that can change this workspace "
                            "(members of groups aren't expanded).")


def _app_admins(ctx: ct.Context) -> dict[str, Any]:
    rid, people = _assignments(ctx)
    apps = [p for p in people if p["type"] == "ServicePrincipal" and p["role"] == "Admin"]
    if apps:
        return ct.failed([rid], f"{len(apps)} app{'s are' if len(apps) != 1 else ' is'} Admin: {_names(apps)}.",
                         detail={"apps": len(apps)})
    return ct.passed([rid], "No app (service principal) is Admin.")


def _direct_users(ctx: ct.Context) -> dict[str, Any]:
    rid, people = _assignments(ctx)
    direct = [p for p in people if p["type"] == "User" and p["role"] in WRITE_ROLES]
    if len(direct) > MAX_DIRECT_WRITERS:
        return ct.failed([rid], f"{len(direct)} people can change this workspace through their own account rather "
                                f"than a group: {_names(direct)}.", detail={"direct": len(direct)})
    return ct.passed([rid], f"{len(direct)} directly assigned user{'s' if len(direct) != 1 else ''} with a write role "
                            f"(at most {MAX_DIRECT_WRITERS}); group members aren't expanded.")


# ---- governance and capacity


def _description(ctx: ct.Context) -> dict[str, Any]:
    r = ctx.one("workspace.info")
    assert r is not None
    if str(r["payload"].get("description") or "").strip():
        return ct.passed([r["id"]], "The workspace has a description.")
    return ct.failed([r["id"]], "The workspace has no description, so nobody can tell what it holds or who owns it "
                                "without asking.")


def _capacity_active(ctx: ct.Context) -> dict[str, Any]:
    r = ctx.one("capacity.state")
    assert r is not None
    state = r["payload"].get("state") or ""
    if state.lower() == "active":
        return ct.passed([r["id"]], f"The capacity ({r['payload'].get('sku') or 'size not given'}) is Active.")
    return ct.failed([r["id"]], f"The capacity is {state or 'in an unknown state'}: scheduled work on it doesn't run.",
                     detail={"state": state})


# ---- optimization-engine evidence, per notebook or pipeline


def _matched(ctx: ct.Context, *rules: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    r = ctx.one("item.optimization")
    assert r is not None
    hits = [m for m in r["payload"].get("matched") or [] if m.get("rule") in rules]
    return r["id"], r["payload"], hits


def _unseen(payload: dict[str, Any], what: str) -> dict[str, Any] | None:
    """Not evaluated when a peer this item is compared with wasn't read: its duplicate could be there."""
    peers = list(payload.get("peers_not_read") or [])
    if not peers:
        return None
    shown = ", ".join(peers[:5]) + (f" and {len(peers) - 5} more" if len(peers) > 5 else "")
    return ct.not_evaluated(f"Nothing matched, but {len(peers)} other {what} couldn't be read ({shown}), so a "
                            "match among them can't be ruled out.", needs="the definitions of " + shown)


def _duplicates(ctx: ct.Context) -> dict[str, Any]:
    rid, payload, hits = _matched(ctx, "OE-DUP-NB", "OE-DUP-NB-SUSPECT")
    proven = [h for h in hits if h["rule"] == "OE-DUP-NB"]
    if proven:
        return ct.failed([rid], "This notebook runs the same code on the same data as another notebook "
                                f"({proven[0]['title']}).", detail={"opportunity_id": proven[0]["opportunity_id"]})
    if hits:
        return ct.not_evaluated("This notebook has the same code as another one, but which data each reads isn't "
                                "known (no default lakehouse in the definitions).", needs="the notebooks' data sources")
    return _unseen(payload, "notebooks") or ct.passed([rid], "No other notebook in the workspace runs the same code on the same data.")


def _repeated_copies(ctx: ct.Context) -> dict[str, Any]:
    rid, payload, hits = _matched(ctx, "OE-DUP-COPY", "OE-REPEAT-EXTRACT")
    if hits:
        return ct.failed([rid], f"A Copy activity here reads a source another pipeline also copies ({hits[0]['title']}).",
                         detail={"opportunity_id": hits[0]["opportunity_id"]})
    return _unseen(payload, "pipelines") or ct.passed(
        [rid], "No Copy activity here reads a source another pipeline in the workspace also copies.")


def _scheduled(ctx: ct.Context) -> bool | None:
    p = (ctx.one("item.optimization") or {}).get("payload") or {}
    if not p.get("history_read"):
        return None
    return bool(p.get("scheduled_runs"))


def _full_refresh(ctx: ct.Context) -> dict[str, Any]:
    rid, _, hits = _matched(ctx, "OE-FULL-REFRESH")
    if hits:
        return ct.failed([rid], "It runs on a schedule and rewrites whole tables each time.",
                         detail={"opportunity_id": hits[0]["opportunity_id"]})
    return ct.passed([rid], "It runs on a schedule and doesn't rewrite whole tables.")


def _parameterize(ctx: ct.Context) -> dict[str, Any]:
    rid, payload, hits = _matched(ctx, "OE-DUP-NB-PARAM")
    if hits:
        return ct.failed([rid], "The same code is kept in more than one notebook, one per lakehouse.",
                         detail={"opportunity_id": hits[0]["opportunity_id"]})
    return _unseen(payload, "notebooks") or ct.passed([rid], "Its code isn't copied into another notebook for a different lakehouse.")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


CONTROLS = (
    ct.Control(
        id="FEA-SEC-001", version="1.0.0", dimension="security", title="Too many workspace admins", severity="medium",
        requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.roles",),
        evaluate=_admins, references=(_ROLES_DOC, _SECURITY_DOC), academy="security-least-privilege",
        description=f"More than {MAX_ADMINS} Admin assignments. Admins can delete the workspace and change who has access.",
        remediation=_t("An Admin can delete the workspace, change its settings and give anyone access. Every extra "
                       "Admin widens who can do that by mistake or on purpose.",
                       "Keep two or three Admins, ideally through a group, and give everyone else the role their work needs.",
                       ["List the Admins in Manage access.", "Agree who must stay Admin (two or three).",
                        "Change the others to Member or Contributor.", "Assess again."],
                       "Fewer people can delete the workspace or change its access.", "low", "medium",
                       "Assess again: the control passes and the people moved still have the access they need.",
                       "Give the role back in Manage access.")),
    ct.Control(
        id="FEA-SEC-002", version="1.0.0", dimension="security", title="Guest accounts can change the workspace",
        severity="high", requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.roles",),
        evaluate=_guests, references=(_ROLES_DOC, _B2B_DOC), academy="security-least-privilege",
        description="A guest from another organization is Admin, Member or Contributor.",
        remediation=_t("Guest accounts are managed by another organization. With a write role they can change or "
                       "remove items and read every table in the workspace.",
                       "Give guests Viewer, or share only the items they need; review guest access regularly.",
                       ["Confirm each guest still needs access.", "Change their role to Viewer, or remove it.",
                        "Share single items where that's enough.", "Assess again."],
                       "Data and items can't be changed by accounts your organization doesn't manage.", "low", "medium",
                       "Assess again, and ask the guest to confirm they can still do their part.",
                       "Give the role back in Manage access.")),
    ct.Control(
        id="FEA-SEC-003", version="1.0.0", dimension="security", title="An app is workspace Admin", severity="medium",
        requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.roles",),
        evaluate=_app_admins, references=(_ROLES_DOC, _SECURITY_DOC), academy="security-least-privilege",
        description="A service principal holds the Admin role.",
        remediation=_t("An app's credentials can be used without a person signing in. As Admin it can delete the "
                       "workspace or grant access, and a leaked secret gives all of that away.",
                       "Give the app the lowest role its job needs, usually Contributor.",
                       ["Find what the app does in this workspace.", "Change its role to Contributor (or lower).",
                        "Run its job once to confirm it still works.", "Assess again."],
                       "A leaked app credential can't delete the workspace or change access.", "low", "medium",
                       "The app's next run succeeds and the control passes.", "Give the Admin role back.")),
    ct.Control(
        id="FEA-SEC-004", version="1.0.0", dimension="security", title="Write access given person by person",
        severity="low", requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.roles",),
        evaluate=_direct_users, references=(_ACCESS_DOC,), academy="security-least-privilege",
        description=f"More than {MAX_DIRECT_WRITERS} people have a write role through their own account.",
        remediation=_t("Access given person by person is hard to review and is often left behind when people change "
                       "jobs.", "Grant write roles to a few groups and manage membership in the groups.",
                       ["Create or pick a group per role.", "Add the people to it.", "Give the group the role.",
                        "Remove the individual assignments.", "Assess again."],
                       "Access is reviewed in a few groups instead of many assignments.", "medium", "low",
                       "Each person can still work; the control passes.", "Re-add the individual assignments.")),
    ct.Control(
        id="FEA-GOV-001", version="1.0.0", dimension="governance", title="Workspace has no description", severity="low",
        requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.info",),
        evaluate=_description, references=(_WORKSPACE_DOC,),
        remediation=_t("A description tells people what the workspace is for and who to ask before they change it.",
                       "Add a one-line description with its purpose and owner.",
                       ["Open Workspace settings.", "Write the purpose and the owning team.", "Save, then assess again."],
                       "People can tell what the workspace holds without asking.", "low", "low",
                       "Assess again.", "Clear the description.")),
    ct.Control(
        id="FEA-CAP-001", version="1.0.0", dimension="capacity_finops", title="Capacity isn't running", severity="medium",
        requirement="kratosforge_recommendation", scope="capacity", evidence_kinds=("capacity.state",),
        evaluate=_capacity_active, references=(_PAUSE_DOC,),
        description="The workspace's capacity is paused or suspended. Only capacities you can see are checked.",
        remediation=_t("Nothing scheduled on a paused capacity runs, and items on it can't be opened.",
                       "Resume the capacity if work should run, or move the workspace to one that runs.",
                       ["Check with the capacity admin whether the pause is planned.",
                        "Resume it in the Azure portal, or reassign the workspace.", "Assess again."],
                       "Scheduled work runs again. A running capacity is billed while it runs.", "low", "medium",
                       "The capacity shows Active and the next scheduled run starts.", "Pause the capacity again.")),
    ct.Control(
        id="FEA-PERF-001", version="1.0.0", dimension="performance", title="Notebook duplicates another one",
        severity="medium", requirement="kratosforge_recommendation", scope="item", item_types=("Notebook",),
        evidence_kinds=("item.optimization",), evaluate=_duplicates, references=(_ENGINE_DOC,),
        description="Optimization engine rule OE-DUP-NB: the same code on the same data.",
        remediation=_t("Two notebooks that do the same work on the same data use capacity twice and can drift apart.",
                       "Keep one, and point whatever reads the copy's output at the one you keep.",
                       ["Open the evidence: both notebooks and their runs.", "Pick the one to keep.",
                        "Move readers of the copy's tables to the kept one.", "Stop the copy's schedule.",
                        "Compare outputs for a few runs.", "Assess again."],
                       "One run's capacity less per schedule. How much is measured from the copy's job history, "
                       "or Unknown without it.", "medium", "medium",
                       "The kept notebook's output matches, and the copy no longer runs.", "Turn the copy's schedule back on.")),
    ct.Control(
        id="FEA-PERF-002", version="1.0.0", dimension="performance", title="Same source copied by more than one pipeline",
        severity="medium", requirement="kratosforge_recommendation", scope="item", item_types=("DataPipeline",),
        evidence_kinds=("item.optimization",), evaluate=_repeated_copies, references=(_COPY_DOC, _SHORTCUT_DOC),
        description="Optimization engine rules OE-DUP-COPY and OE-REPEAT-EXTRACT.",
        remediation=_t("Copying the same source twice moves the same data twice and loads the source twice.",
                       "Land the data once, and let the other consumers read it there (a shortcut if it's another "
                       "workspace).", ["Open the evidence: both Copy activities.", "Pick the landing table to keep.",
                                       "Point the other consumers at it.", "Disable the second copy.", "Assess again."],
                       "One copy's run time and source load less per schedule (measured from job history, or Unknown).",
                       "medium", "medium", "Both consumers read the same, current data.", "Re-enable the second copy.")),
    ct.Control(
        id="FEA-DE-001", version="1.0.0", dimension="data_engineering", title="Scheduled full refresh", severity="medium",
        requirement="kratosforge_recommendation", scope="item", item_types=("Notebook", "DataPipeline"),
        evidence_kinds=("item.optimization",), evaluate=_full_refresh, applies=_scheduled,
        references=(_DELTA_DOC,), academy="incremental-processing",
        description="Optimization engine rule OE-FULL-REFRESH. Doesn't apply to items that don't run on a schedule.",
        remediation=_t("Rewriting whole tables on every run processes unchanged data again and gets slower as the "
                       "table grows. Some tables are small or need a full rewrite; that's a reason to turn this "
                       "control off for them in your profile.",
                       "Load only what changed, with a watermark or change tracking and a Delta MERGE.",
                       ["Find how the source marks changed rows.", "Write the incremental load with MERGE.",
                        "Test inserts, updates, deletes and late rows.", "Compare with the full load's output.",
                        "Measure run time before and after.", "Release it the usual way."],
                       "Shorter runs once the table is large. How much is Unknown until measured before and after.",
                       "high", "medium", "Outputs match the full load and the run is measured faster.",
                       "Switch back to the full load.")),
    ct.Control(
        id="FEA-DE-002", version="1.0.0", dimension="data_engineering", title="Notebook copied per lakehouse",
        severity="low", requirement="kratosforge_recommendation", scope="item", item_types=("Notebook",),
        evidence_kinds=("item.optimization",), evaluate=_parameterize, references=(_ENGINE_DOC,),
        description="Optimization engine rule OE-DUP-NB-PARAM. Not duplicate processing, duplicate maintenance.",
        remediation=_t("The same code kept in several notebooks has to be fixed in several places, and the copies "
                       "drift apart.", "Keep one notebook with a parameter for the lakehouse.",
                       ["Add a parameter cell.", "Replace the copies with runs of the one notebook.", "Assess again."],
                       "One place to fix and review.", "medium", "low", "Each run writes what its copy wrote.",
                       "Bring the copies back.")),
)


def registry() -> ct.Registry:
    """A registry with every control in this catalog."""
    r = ct.Registry()
    from .catalog_capacity import CONTROLS as CAPACITY  # it imports this module
    for c in (*CONTROLS, *catalog_artifacts.CONTROLS, *catalog_models.CONTROLS, *CAPACITY):
        r.register(c)
    for c in (*catalog_architecture.CONTROLS, *catalog_devops.CONTROLS, *catalog_warehouse.CONTROLS):
        r.register(c)
    return r
