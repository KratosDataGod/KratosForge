"""The predeployment risk check, mixed into ``Service`` (FINOPS-004).

Gathers the facts for one action on one workload from services the app already has and hands
them to :func:`risk.analyze`. It only reads: the capacity reading (GET calls with an existing
sign-in, never a prompt), the Delta log for a metadata entity's input size, and files on this PC.
Nothing is started, paused, published or changed.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from . import advisor
from . import app_settings
from . import capacity as capacity_mod
from . import costs as costs_mod
from . import receipts as receipts_mod
from . import risk
from .auth import CAPACITY_SCOPES, FABRIC_SCOPES
from .fabric_run import compute_check
from .meta import mesh as mesh_mod
from .meta.project import run_order
from .workloads import model

# Actions that start work; cancel needs no assessment.
FABRIC_STEP = "run_fabric_step"  # a notebook's last cell, run on Fabric Spark (fabric_step.py)
ASSESSED = ("run", "run_in_fabric", "run_item", "run_schedule", FABRIC_STEP)
_ACKS = "risk_acknowledgements.jsonl"
log = logging.getLogger(__name__)


class RiskMixin:
    def _risk_settings(self) -> dict[str, Any]:
        return risk.settings(self._settings().get("risk"))  # type: ignore[attr-defined]

    def risk_settings(self) -> dict[str, Any]:
        return self._risk_settings()

    def set_risk_settings(self, warn_run_cu_hours: float | None = None, warn_failed_runs: int | None = None,
                          schedule_window_minutes: int | None = None,
                          require_acknowledgement: bool | None = None) -> dict[str, Any]:
        """Change when the risk check warns. A CU threshold of 0 or "" turns that warning off."""
        cfg = self._risk_settings()
        if warn_run_cu_hours is not None:
            if warn_run_cu_hours in ("", 0):
                cfg["warn_run_cu_hours"] = None
            elif float(warn_run_cu_hours) < 0:
                raise ValueError("The CU hours threshold can't be negative")
            else:
                cfg["warn_run_cu_hours"] = float(warn_run_cu_hours)
        for key, value, low in (("warn_failed_runs", warn_failed_runs, 0), ("schedule_window_minutes", schedule_window_minutes, 0)):
            if value is not None:
                if int(value) < low:
                    raise ValueError(f"{key.replace('_', ' ')} can't be negative")
                cfg[key] = int(value)
        if require_acknowledgement is not None:
            cfg["require_acknowledgement"] = bool(require_acknowledgement)
        self._save_settings(risk=cfg)  # type: ignore[attr-defined]
        return cfg

    # ---- facts

    def _risk_capacity(self, workspace_id: str, workspace_name: str, refresh: bool,
                       reads: list[str], memo: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The capacity reading for a workspace: an environment's reading when the workspace is one
        (from the last hour unless ``refresh``), otherwise a read of that workspace now. ``memo``
        keeps readings across the checks of one batch, so each workspace is read once."""
        if memo is not None and ("capacity", workspace_id) in memo:
            cap, cap_reads = memo[("capacity", workspace_id)]
            reads.extend(cap_reads)
            return cap
        mine: list[str] = []
        cap = self._risk_capacity_read(workspace_id, workspace_name, refresh, mine)
        reads.extend(mine)
        if memo is not None:
            memo[("capacity", workspace_id)] = (cap, mine)
        return cap

    def _risk_capacity_read(self, workspace_id: str, workspace_name: str, refresh: bool,
                            reads: list[str]) -> dict[str, Any] | None:
        now = dt.datetime.now(dt.timezone.utc)
        try:
            health = self.capacity_health(refresh=refresh)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the workspace is read directly below instead
            log.warning("Risk check: the environments' capacity reading failed: %s", exc)
            reads.append(f"The environments' capacity reading failed ({str(exc)[:200]}); the workspace was read instead")
            health = {"environments": []}
        for e in health.get("environments", []):
            if e.get("workspace_id") and e["workspace_id"] == workspace_id:
                if refresh:
                    reads.append("Capacity of the environments (GET /workspaces/{id}, GET /capacities)")
                return e | capacity_mod.freshness(e, now)
        fabric = self._silent_client(FABRIC_SCOPES)  # type: ignore[attr-defined]
        if fabric is None:
            return None
        cap_client = self._silent_client(CAPACITY_SCOPES)  # type: ignore[attr-defined]
        caps, cap_error = None, None
        if cap_client:
            try:
                caps = {str(c.get("id", "")).lower(): c for c in cap_client.list_capacities() if c.get("id")}
            except Exception as exc:  # noqa: BLE001
                cap_error = capacity_mod.classify_error(exc)
                if cap_error in ("workspace_no_access", "workspace_not_found"):
                    cap_error = "scope_not_granted"
        else:
            cap_error = "scope_not_granted"
        reads.append("Capacity of the workspace (GET /workspaces/{id}" + (", GET /capacities)" if cap_client else ")"))
        obs = capacity_mod.observe_env("", workspace_id, workspace_name, fabric.get_workspace, caps, cap_error, now)
        return obs | capacity_mod.freshness(obs, now)

    def _risk_cu(self, runs: list[dict[str, Any]],
                 memo: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """Measured (from usage imports matched to these runs) and estimated CU of the given Fabric
        runs, newest first, and why the imports couldn't be read ("" when they could). Nothing is
        read from Fabric. ``memo`` keeps the run history and imports across one batch."""
        job_ids = [r["job_id"] for r in runs if r.get("job_id")]
        if not job_ids:
            return [], [], ""
        memo = {} if memo is None else memo
        if "cu" not in memo:
            memo["cu"] = self._risk_cu_index()
        measured, est, problem = memo["cu"]
        return [measured[j] for j in job_ids if j in measured], [est[j] for j in job_ids if j in est], problem

    def _risk_cu_index(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        fabric = self.fabric_runs(limit=500, refresh=False)  # type: ignore[attr-defined]
        rows = costs_mod.activities([], fabric, [], self._cost_settings())  # type: ignore[attr-defined]
        est = {r["fabric_job_cu"]["run_id"]: r["fabric_job_cu"] for r in rows
               if r["where"] == "fabric" and r["fabric_job_cu"].get("run_id")}
        measured: dict[str, dict[str, Any]] = {}
        problem = ""
        try:
            rec = self._usage_reconcile(90, fabric, rows)  # type: ignore[attr-defined]
            measured = {b["job_id"]: b["measured"] for b in rec.get("by_run", []) if b.get("measured")}
        except Exception as exc:  # noqa: BLE001 - the estimate is still shown, with why nothing is measured
            log.warning("Risk check: imported usage couldn't be read: %s", exc)
            problem = f"Imported usage couldn't be read ({str(exc)[:200]}), so nothing is measured."
        return measured, est, problem

    def _risk_record(self, id: str, action: str) -> dict[str, Any]:  # noqa: A002
        """The workload the check reads. A Fabric step runs in the attached lakehouse's workspace,
        which the notebook needn't be deployed to, so its record comes from the notebook itself."""
        if action != FABRIC_STEP:
            return self._wl_record(id)  # type: ignore[attr-defined]
        parsed = model.parse_id(id)
        if parsed["kind"] != "notebook":
            raise ValueError("Only a notebook has a Fabric step")
        lh = self.notebooks.load(parsed["name"]).get("lakehouse") or {}  # type: ignore[attr-defined]
        if not lh.get("workspace_id"):
            raise ValueError("Attach a OneLake lakehouse first: the Fabric step runs in its workspace.")
        return {"id": id, "name": parsed["name"], "kind": "fabric_step", "errors": [], "runs": [],
                "deployments": [{"env": None, "actions": [FABRIC_STEP], "location": {
                    "kind": "fabric", "workspace_id": lh["workspace_id"], "workspace_name": lh.get("workspace_name") or ""}}]}

    def _risk_facts(self, w: dict[str, Any], action: str, env: str | None, refresh: bool,
                    memo: dict[str, Any] | None = None) -> dict[str, Any]:
        dep = next((d for d in w["deployments"] if (d.get("env") or None) == (env or None)
                    and action in (d.get("actions") or [])), None)
        if dep is None:
            raise ValueError(f"{w['name']} doesn't support that here.")
        loc = dep["location"]
        where = "fabric" if loc["kind"] == "fabric" and action != "run_schedule" else "this_pc"
        reads: list[str] = []
        facts: dict[str, Any] = {
            "workload_id": w["id"], "workload": w["name"], "action": action, "label": action.replace("_", " "),
            "env": env, "env_label": dep.get("env_label") or "", "where": where,
            "needs_sign_in": w["kind"] == "meta_entity" and dep.get("tables_in") not in ("This PC only",),
            "policy": {"allow_fabric_runs": bool(app_settings.value("allow_fabric_runs"))},
            "signed_in": bool(self._wl_token(FABRIC_SCOPES)),  # type: ignore[attr-defined]
            "definition_errors": w.get("errors") or [], "version": dep.get("version"),
            "schedules": dep.get("schedules") or [], "reads": reads,
        }
        if where == "fabric":
            facts["capacity"] = self._risk_capacity(loc.get("workspace_id") or "", loc.get("workspace_name") or "",
                                                    refresh, reads, memo)
        runs = [r for r in w.get("runs") or []
                if (r.get("where") == "fabric") == (where == "fabric") and (not env or not r.get("env") or r["env"] == env)]
        facts["runs"] = runs
        if where == "fabric":
            facts["measured"], facts["estimated"], facts["measured_problem"] = self._risk_cu(
                [r for r in runs if r.get("where") == "fabric"], memo)
        if w["kind"] == "notebook":
            try:
                nb = self.notebooks.load(w["name"])  # type: ignore[attr-defined]
                facts["compatibility"] = compute_check(nb.get("cells") or [], nb.get("lakehouse"),
                                                          **self._checks(w["name"]))  # type: ignore[attr-defined]
            except (OSError, ValueError):
                facts["compatibility"] = None
        elif w["kind"] == "meta_entity" and action == "run":
            try:
                facts["input"] = self.meta_preflight(w["name"], env or "dev")  # type: ignore[attr-defined]
                reads.append("Delta log of the source (input size)")
            except Exception as exc:  # noqa: BLE001 - the size is then unknown
                facts["input"] = {"error": str(exc)[:300]}
        return facts

    # ---- what the app calls

    def risk_check(self, id: str, action: str, env: str | None = None, refresh: bool = False) -> dict[str, Any]:  # noqa: A002
        """What could stop or slow this action, with evidence, before it starts. Read only."""
        if action not in ASSESSED:
            raise ValueError(f"{action} isn't assessed: only actions that start work are")
        w = self._risk_record(id, action)
        return risk.analyze(self._risk_facts(w, action, env, refresh), self._risk_settings())

    def _risk_for_preflight(self, w: dict[str, Any], action: str, env: str | None) -> dict[str, Any] | None:
        """The assessment shown in the run dialog; a failure in it never stops the dialog."""
        if action not in ASSESSED:
            return None
        return self._risk_or_unknown(lambda: w, action, env, None)

    def _risk_or_unknown(self, record: Any, action: str, env: str | None,
                         memo: dict[str, Any] | None) -> dict[str, Any]:
        """The assessment, or an unknown one saying why it couldn't run: the check is advice."""
        try:
            w = record()
            return risk.analyze(self._risk_facts(w, action, env, False, memo), self._risk_settings())
        except Exception as exc:  # noqa: BLE001 - the check is advice, the run or dialog still works
            log.warning("Risk check failed for %s %s: %s", action, env, exc)
            return {"result": risk.UNKNOWN, "label": risk.LABELS[risk.UNKNOWN], "findings": [], "can_proceed": True,
                    "needs_acknowledgement": False, "enforced": False, "summary": f"The risk check couldn't run: {exc}"[:300]}

    def meta_risk_gate(self, names: list[str] | None = None, env: str = "dev") -> dict[str, Any]:
        """The risk check for every Data mesh entity a run would start (all of them when ``names`` is
        None), in run order, read once per batch: each workspace's capacity and the run history are
        shared. ``run`` lists the entities that can start: not blocked, and not reading from one
        that is (``held``), so nothing runs on top of a table that wasn't refreshed. Read only."""
        project = self._project()  # type: ignore[attr-defined]
        pname = mesh_mod.project_name(project)
        order = run_order(project)
        targets = order if names is None else [n for n in order if n in set(names)]
        memo: dict[str, Any] = {}
        checks = [{"entity": n} | self._risk_or_unknown(
            lambda n=n: self._wl_record(model.meta_id(pname, n)), "run", env, memo)  # type: ignore[attr-defined]
            for n in targets]
        blocked = [c["entity"] for c in checks if c["result"] == risk.BLOCKED]
        stopped, held = set(blocked), {}
        for n in targets:
            upstream = [r for r in project.entities[n].reads() if r in stopped]
            if n not in stopped and upstream:
                held[n] = upstream
                stopped.add(n)
        return {"checks": checks, "blocked": blocked, "held": held, "run": [n for n in targets if n not in stopped],
                "enforced": False}

    def risk_acknowledge(self, id: str, action: str, risk_id: str, env: str | None = None,  # noqa: A002
                         note: str = "") -> dict[str, Any]:
        """Record that the person saw this assessment's warnings and chose to go ahead. Checked again
        first: a blocked result can't be acknowledged, and a changed one needs a fresh look."""
        current = self.risk_check(id, action, env)
        if current["result"] == risk.BLOCKED:
            raise PermissionError("A blocked run can't be acknowledged: " + current["summary"])
        if current["id"] != risk_id:
            raise ValueError("The findings changed since you looked. Check them again before you go ahead.")
        record = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "risk_id": risk_id,
                  "workload_id": id, "workload": current["workload"], "action": action, "env": env or "",
                  "result": current["result"], "user": self._wl_user(),  # type: ignore[attr-defined]
                  "warnings": [f["title"] for f in current["findings"] if f["outcome"] == risk.WARNING],
                  "note": str(note or "")[:500]}
        self._risk_ack_write(record)
        return record

    def _risk_ack_write(self, record: dict[str, Any]) -> None:
        path = self.root / _ACKS  # type: ignore[attr-defined]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def risk_acknowledgements(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = (self.root / _ACKS).read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
        except OSError:
            return []
        return [json.loads(x) for x in lines[-int(limit):] if x.strip()][::-1]

    def placement_advice(self, name: str) -> dict[str, Any]:
        """Where to run notebook ``name``, on this PC or in Fabric, with the evidence (FINOPS-005, advisor.py).

        Reads the notebook, its Prove receipts and side-by-side runs on this PC. Changes nothing."""
        nb = self.notebooks.load(name)  # type: ignore[attr-defined]
        cells = nb["cells"]
        proofs = []
        for row in self.receipts_list(name, 20):  # type: ignore[attr-defined]
            if row.get("valid") and row.get("kind") == "proof":
                proofs.append(self.receipt_get(row["id"]))  # type: ignore[attr-defined]
        done = [c for c in self.side_by_side_list(name, 10) if c.get("state") == "done"]  # type: ignore[attr-defined]
        compat = compute_check(cells, nb.get("lakehouse"), **self._checks(name))  # type: ignore[attr-defined]
        return advisor.advise(name, receipts_mod.code_revision(cells)["revision"], compat, proofs, done)

