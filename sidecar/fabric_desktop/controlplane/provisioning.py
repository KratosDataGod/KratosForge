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
LAKEHOUSE_COST = "OneLake storage for what it holds (small: history rows, not data copies)."
TABLES_PERMISSION = "Write access to the lakehouse (Contributor or higher on the workspace)."
READY_NOTE = "No table changes. This PC is added to its installations list (one row)."
UPGRADE_NOTE = ("Upgrades the shared tables. KratosForge on other PCs can only read them until it's updated "
                "to this version.")
CHANGED = ("Something changed since this plan was shown (a name was taken, or a lakehouse or its tables "
           "changed). Nothing was created. Review the new plan and confirm again.")


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
        steps.append(PlanStep("write_manifest", "Files/_kratosforge/environment.json", True, TABLES_PERMISSION))
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
            raise NotConfirmed("Nothing was created: creating an environment needs your confirmation.")
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
