"""The Fabric provider: creating a KratosForge Environment (CP-005).

Two steps, so nothing is created before the person has seen it:

``plan(request)`` reads only. It says what would happen: which workspace and lakehouse are used or
created, which table families are created or upgraded, and the manifest written last, each step with
the permission it needs and what it may cost. A step that can't go ahead is *blocked* with the reason:
a name already taken, a lakehouse that already holds an environment, or a family whose tables are
newer than this app or aren't KratosForge's (``newer``, ``conflict``).

``apply(plan, confirmed)`` does the plan. Without ``confirmed`` it refuses before calling Fabric. It
creates by asking Fabric (``create_workspace``, ``create_lakehouse``), initializes each family through
``optimize/setup.initialize`` with this PC's Installation (Optimize's own family first, as today), and
writes the manifest last with a create-only write, so an environment exists only once everything
under it does. A step that fails stops the rest and says so; nothing is retried here, and running a new
plan with the ids already made finishes the job without making anything twice.

An environment chosen in a request is always by id. A new workspace or lakehouse is by name, and the
name must be free: an existing workspace or lakehouse is used only when the person picks it.

No operational store is created here (CP-ADR-006): an environment starts lakehouse-only, and the
Platform's database is linked or added later. Nothing here runs against a real tenant in tests.
"""

from __future__ import annotations

from typing import Any, Callable

from ..optimize import schema, setup
from ..optimize.installation import Installation
from ..redact import describe
from . import manifest as mf
from .providers import NotConfirmed, PlanStep, ProvisionPlan
from .store import ManifestStore

DEFAULT_WORKSPACE = "KratosForge"
DEFAULT_LAKEHOUSE = "KratosForgeLakehouse"
DEFAULT_OPERATIONAL = "KratosForgeMetadata"  # named here for the later step that adds it (CP-ADR-005)

WORKSPACE_PERMISSION = ("Your organization lets you create workspaces (Fabric tenant setting "
                        "\"Users can create workspaces\"), and you can assign the capacity you pick.")
WORKSPACE_COST = ("No new purchase. The workspace runs on the capacity you pick, and what runs in it uses "
                  "that capacity's units.")
LAKEHOUSE_PERMISSION = "Contributor, Member or Admin role on the workspace."
LAKEHOUSE_COST = "OneLake storage for what it holds. It's small, because it keeps history rows, not copies of data."
TABLES_PERMISSION = "Write access to the lakehouse (Contributor or higher on the workspace)."
READY_NOTE = "No table changes. This PC is added to its installations list (one row)."
UPGRADE_NOTE = ("Upgrades the shared tables. KratosForge on other PCs can only read them until it's updated "
                "to this version.")
MANIFEST_NOTE = "Ids and versions only, never a secret."
UPGRADE_CHANGED = ("Something changed since this plan was shown (another PC upgraded the tables or changed the "
                   "environment). Nothing was upgraded. Review the new plan and confirm again.")
UPGRADE_DONE = "Nothing to upgrade, because every shared table is already at this version."
CHANGED = ("Something changed since this plan was shown (a name was taken, or a lakehouse or its tables "
           "changed). Nothing was created. Review the new plan and confirm again.")


def _manifest_refuses(req: dict[str, Any]) -> str:
    """Why the manifest written last would refuse this request's name, labels or owner, or "". Checked
    in the plan, so a workspace and lakehouse are never created for a manifest that can't be written."""
    try:
        name = (req.get("name") or "").strip()
        for key, value in (("name", name), ("workspace_name", req.get("workspace_name")),
                           ("lakehouse_name", req.get("lakehouse_name"))):
            mf._text(value, key, required=False, limit=mf.MAX_NAME)
            mf._check_no_secrets(value, key)
        mf._guid(req.get("owner_object_id"), "owner_object_id", required=False)
    except mf.ManifestError as exc:
        return str(exc)
    return ""


def _same_name(a: Any, b: str) -> bool:
    """Fabric names are compared without case, so "kratosforge" is taken when "KratosForge" exists."""
    return isinstance(a, str) and a.strip().casefold() == b.strip().casefold()


class FabricProvisioning:
    """``fabric``: the app's FabricClient. ``open_lake(workspace_id, lakehouse_id, schemas)``: the central
    lakehouse's tables (optimize/lakehouse.OneLakeLakehouse in the app; ``schemas`` None means look).
    ``manifest_store(workspace_id, lakehouse_id)``: the manifest file. ``families``: the table families
    a new environment gets (default: every registered one)."""

    def __init__(self, fabric: Any, open_lake: Callable[[str, str, bool | None], Any],
                 manifest_store: Callable[[str, str], ManifestStore], installation: Installation,
                 families: list[schema.Family] | None = None):
        self.fabric = fabric
        self._open_lake = open_lake
        self._manifest_store = manifest_store
        self.installation = installation
        self._families = families

    def families(self) -> list[schema.Family]:
        fams = list(self._families) if self._families is not None else list(schema.FAMILIES.values())
        # Optimize's family holds the installations, audit and config every family shares: first.
        return sorted(fams, key=lambda f: (f is not setup.CORE, f.prefix))

    # ---- plan: reads only

    def plan(self, request: dict[str, Any]) -> ProvisionPlan:
        """``request``: ``name`` (the environment's), and either ``workspace_id`` (an existing workspace)
        or ``workspace_name`` and ``capacity_id`` (a new one), and either ``lakehouse_id`` or
        ``lakehouse_name``. Optional ``owner_object_id``."""
        req = dict(request)
        steps: list[PlanStep] = []
        name = (req.get("name") or "").strip()
        if not name:
            steps.append(PlanStep("refuse", "environment", False, blocked="Give the environment a name."))

        ws_id = req.get("workspace_id") or ""
        if ws_id:
            ws = next((w for w in self.fabric.list_workspaces() if w.get("id") == ws_id), None)
            if ws is None:
                steps.append(PlanStep("refuse", ws_id, False, blocked="That workspace isn't one you can open."))
            else:
                req["workspace_name"] = ws.get("displayName", "")  # the manifest names it, for people
                steps.append(PlanStep("find", ws.get("displayName", ws_id), False))
        else:
            ws_name = (req.get("workspace_name") or DEFAULT_WORKSPACE).strip()
            req["workspace_name"] = ws_name
            taken = any(_same_name(w.get("displayName"), ws_name) for w in self.fabric.list_workspaces())
            steps.append(PlanStep("create", f"workspace {ws_name}", True, WORKSPACE_PERMISSION, WORKSPACE_COST,
                                  blocked=(f"A workspace called {ws_name} already exists. Choose it as an existing "
                                           "workspace, or pick another name.") if taken else
                                  ("" if req.get("capacity_id") else "Pick a capacity for the new workspace.")))

        lh_id = req.get("lakehouse_id") or ""
        if lh_id:
            if not ws_id:
                steps.append(PlanStep("refuse", lh_id, False, blocked="An existing lakehouse is chosen in an existing workspace."))
            else:
                steps.extend(self._existing_lakehouse(ws_id, lh_id))
        else:
            lh_name = (req.get("lakehouse_name") or DEFAULT_LAKEHOUSE).strip()
            req["lakehouse_name"] = lh_name
            taken = bool(ws_id) and any(_same_name(lh.get("displayName"), lh_name)
                                         for lh in self.fabric.list_lakehouses(ws_id))
            steps.append(PlanStep("create", f"lakehouse {lh_name}", True, LAKEHOUSE_PERMISSION, LAKEHOUSE_COST,
                                  blocked=(f"This workspace already has a lakehouse called {lh_name}. Choose it as an "
                                           "existing lakehouse, or pick another name.") if taken else ""))
            for fam in self.families():
                steps.append(PlanStep("initialize", f"{fam.title} tables ({fam.prefix})", True, TABLES_PERMISSION))
        steps.append(PlanStep("write_manifest", "Files/_kratosforge/environment.json", True, TABLES_PERMISSION,
                              note=MANIFEST_NOTE, blocked=_manifest_refuses(req)))
        return ProvisionPlan("fabric", tuple(steps), req)

    def _existing_lakehouse(self, ws_id: str, lh_id: str) -> list[PlanStep]:
        try:
            if self._manifest_store(ws_id, lh_id).read() is not None:
                return [PlanStep("refuse", lh_id, False, blocked="This lakehouse already holds a KratosForge Environment. "
                                 "Connect to it instead.")]
            lake = self._open_lake(ws_id, lh_id, None)
            states = [(fam, setup.inspect(lake, fam)) for fam in self.families()]
        except mf.ManifestError as exc:
            return [PlanStep("refuse", lh_id, False, blocked=f"Its KratosForge manifest is damaged: {exc}")]
        except Exception as exc:  # noqa: BLE001 - shown as a blocked step, with the reason
            return [PlanStep("refuse", lh_id, False, blocked="You can't read this lakehouse." if setup.denied(exc)
                             else f"This lakehouse couldn't be read ({describe(exc)}).")]
        steps = [PlanStep("find", lh_id, False)]
        for fam, st in states:
            label = f"{fam.title} tables ({fam.prefix})"
            if st["state"] == setup.READY:
                steps.append(PlanStep("find", label, False, TABLES_PERMISSION, note=READY_NOTE))
            elif st["state"] in (setup.NEWER, setup.CONFLICT):
                steps.append(PlanStep("refuse", label, False, blocked=st["message"]))
            else:
                action = "upgrade" if st["state"] == setup.UPGRADE else "initialize"
                steps.append(PlanStep(action, label, bool(st["adds"]), TABLES_PERMISSION,
                                      note=UPGRADE_NOTE if action == "upgrade" else ""))
        return steps

    # ---- apply

    def apply(self, plan: ProvisionPlan, confirmed: bool) -> dict[str, Any]:
        """Run a plan the person confirmed. Returns ``ok``, the ids made or used, each step's outcome,
        and the manifest when it was written."""
        if not confirmed:
            raise NotConfirmed("Nothing was created, because creating an environment needs your confirmation.")
        if plan.blocked:
            raise PermissionError(" ".join(plan.blocked))
        # The plan the person saw is checked again now, so a stale or edited plan never runs.
        fresh = self.plan(plan.request)
        if fresh.blocked:
            raise PermissionError(" ".join(fresh.blocked))
        if fresh.steps != plan.steps or fresh.provider != plan.provider:
            raise PermissionError(CHANGED)
        req = fresh.request
        done: list[dict[str, str]] = []
        out: dict[str, Any] = {"ok": False, "steps": done, "workspace_id": req.get("workspace_id", ""),
                               "lakehouse_id": req.get("lakehouse_id", ""), "manifest": None}

        def step(label: str, fn: Callable[[], str]) -> bool:
            try:
                done.append({"step": label, "status": "done", "detail": fn()})
                return True
            except Exception as exc:  # noqa: BLE001 - reported, and the steps after it don't run
                done.append({"step": label, "status": "failed", "detail": describe(exc)})
                return False

        def workspace() -> str:
            ws = self.fabric.create_workspace(req["workspace_name"], req["capacity_id"],
                                              "KratosForge Environment: shared history and settings for KratosForge")
            out["workspace_id"] = ws["id"]
            return f"Created {req['workspace_name']}"

        def lakehouse() -> str:
            lh = self.fabric.create_lakehouse(out["workspace_id"], req["lakehouse_name"],
                                              "KratosForge Environment: history tables and the environment's manifest")
            out["lakehouse_id"] = lh["id"]
            return f"Created {req['lakehouse_name']}"

        if not out["workspace_id"] and not step("Workspace", workspace):
            return out
        new_lakehouse = not out["lakehouse_id"]
        if new_lakehouse and not step("Lakehouse", lakehouse):
            return out
        lake = self._open_lake(out["workspace_id"], out["lakehouse_id"], True if new_lakehouse else None)
        versions: dict[str, int] = {}
        for fam in self.families():
            def init(fam: schema.Family = fam) -> str:
                # approve=True: the person confirmed this plan. initialize still refuses newer or conflicting tables.
                got = setup.initialize(lake, self.installation, approve=True, family=fam)
                versions[fam.prefix] = int(got["schema_version"] or fam.latest())
                return got["message"]
            if not step(f"{fam.title} tables", init):
                return out

        def manifest() -> str:
            made = self._manifest_store(out["workspace_id"], out["lakehouse_id"]).create(mf.new_manifest(
                name=req["name"].strip(), provider="fabric",
                namespace={"workspace_id": out["workspace_id"], "display_name": req.get("workspace_name", "")},
                analytical_store={"workspace_id": out["workspace_id"], "lakehouse_id": out["lakehouse_id"],
                                  "display_name": req.get("lakehouse_name", ""), "schemas": bool(getattr(lake, "schemas", True))},
                families=versions, owner_object_id=req.get("owner_object_id", "")))
            out["manifest"] = made.manifest
            return "Wrote the manifest"

        out["ok"] = step("Manifest", manifest)  # ManifestExists is reported here: connect instead
        return out

    # ---- upgrade (CP-010): the same plan-and-confirm, for an environment whose tables are older

    def plan_upgrade(self, manifest: dict[str, Any]) -> ProvisionPlan:
        """What upgrading the environment's shared tables would do. Reads only. Only families that need
        it get a step; a family that is newer or not KratosForge's blocks the plan."""
        store = manifest.get("analytical_store") or {}
        ws_id, lh_id = store.get("workspace_id", ""), store.get("lakehouse_id", "")
        req = {"environment_id": manifest.get("environment_id", ""), "workspace_id": ws_id, "lakehouse_id": lh_id}
        if not mf.writable(manifest):
            return ProvisionPlan("fabric", (PlanStep("refuse", "environment", False, blocked=(
                "A newer KratosForge wrote this environment. Update KratosForge to upgrade it.")),), req)
        try:
            lake = self._open_lake(ws_id, lh_id, bool(store.get("schemas", True)))
            states = [(fam, setup.inspect(lake, fam)) for fam in self.families()]
        except Exception as exc:  # noqa: BLE001 - shown as a blocked step, with the reason
            return ProvisionPlan("fabric", (PlanStep("refuse", lh_id, False, blocked="You can't read this lakehouse."
                                                     if setup.denied(exc) else
                                                     f"This lakehouse couldn't be read ({describe(exc)})."),), req)
        steps: list[PlanStep] = []
        for fam, st in states:
            label = f"{fam.title} tables ({fam.prefix})"
            if st["state"] in (setup.NEWER, setup.CONFLICT):
                steps.append(PlanStep("refuse", label, False, blocked=st["message"]))
            elif st["state"] != setup.READY:
                action = "upgrade" if st["state"] in (setup.UPGRADE, setup.INCOMPLETE) else "initialize"
                steps.append(PlanStep(action, label, bool(st["adds"]), TABLES_PERMISSION, note=UPGRADE_NOTE))
        recorded = (manifest.get("schema") or {}).get("families") or {}
        lagging = any(st["schema_version"] and recorded.get(fam.prefix) != st["schema_version"] for fam, st in states)
        if not steps and not lagging:  # a manifest behind its tables (a write that failed) is still caught up
            steps.append(PlanStep("refuse", "environment", False, blocked=UPGRADE_DONE))
        steps.append(PlanStep("write_manifest", "Files/_kratosforge/environment.json", False, TABLES_PERMISSION,
                              note=MANIFEST_NOTE))
        return ProvisionPlan("fabric", tuple(steps), req)

    def apply_upgrade(self, plan: ProvisionPlan, confirmed: bool) -> dict[str, Any]:
        """Run an upgrade plan the person confirmed: the plan is made again from the manifest as it is
        now and must match; each family is upgraded, then the manifest records the new versions with
        an etag-conditional write, so a change by another PC meanwhile is refused, not overwritten."""
        if not confirmed:
            raise NotConfirmed("Nothing was upgraded, because upgrading needs your confirmation.")
        req = plan.request
        current = self._manifest_store(req.get("workspace_id", ""), req.get("lakehouse_id", "")).read()
        if current is None or current.manifest.get("environment_id") != req.get("environment_id"):
            raise PermissionError(UPGRADE_CHANGED)
        fresh = self.plan_upgrade(current.manifest)
        if fresh.blocked:
            raise PermissionError(" ".join(fresh.blocked))
        if fresh.steps != plan.steps or fresh.request != req:
            raise PermissionError(UPGRADE_CHANGED)
        done: list[dict[str, str]] = []
        out: dict[str, Any] = {"ok": False, "steps": done, "manifest": None}
        store = current.manifest["analytical_store"]
        lake = self._open_lake(store["workspace_id"], store["lakehouse_id"], bool(store.get("schemas", True)))
        versions = dict((current.manifest.get("schema") or {}).get("families") or {})
        needs = {s.target for s in fresh.steps if s.action in ("upgrade", "initialize")}
        for fam in self.families():
            if f"{fam.title} tables ({fam.prefix})" not in needs:
                continue  # already at this version: left exactly as it is
            try:
                got = setup.initialize(lake, self.installation, approve=True, family=fam)
                versions[fam.prefix] = int(got["schema_version"] or fam.latest())
                done.append({"step": f"{fam.title} tables", "status": "done", "detail": got["message"]})
            except Exception as exc:  # noqa: BLE001 - reported, and the steps after it don't run
                done.append({"step": f"{fam.title} tables", "status": "failed", "detail": describe(exc)})
                return out
        for fam in self.families():  # what each family is at now, the ones left alone included
            at = setup.inspect(lake, fam).get("schema_version")
            if at:
                versions[fam.prefix] = int(at)
        try:
            schema_part = dict(current.manifest.get("schema") or {}) | {"families": versions}
            made = self._manifest_store(store["workspace_id"], store["lakehouse_id"]).replace(
                current, mf.updated(current.manifest, {"schema": schema_part}))
        except Exception as exc:  # noqa: BLE001 - ManifestChanged among them: reported, never overwritten
            done.append({"step": "Manifest", "status": "failed", "detail": describe(exc)})
            return out
        done.append({"step": "Manifest", "status": "done", "detail": "Recorded the new table versions"})
        out.update(ok=True, manifest=made.manifest)
        return out
