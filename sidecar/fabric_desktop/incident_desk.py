"""The incident desk: numbers, priorities, SLAs and incidents people report.

Over the same incident log as incident_service.py (one document per environment, changed only
with conditional writes), so the agent, the app and every PC see the same numbers and priorities.
SLA targets come from the organization's policy (``incident_sla``) and are applied on read.
Every change is also copied to the central Fabric Desktop lakehouse (workloads/incident_history.py),
where the history is kept for good.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from . import app_settings
from .meta.project import ENVIRONMENTS
from .optimize import setup
from . import governance_notices as notices
from .central import core
from .workloads import impact, model, sla
from .workloads.incident_history import FAMILY, Sent, event_rows, snapshot_rows
from .workloads.incidents import ACTIVE, PRIORITIES, PRIORITY_LABELS, default_priority


_TOLD = "Told downstream in Teams:"


class IncidentDeskMixin:
    def _sla_targets(self) -> tuple[dict[str, dict[str, int | None]], list[str], str]:
        settings = app_settings.load()["incident_sla"]
        table, problems = sla.targets(settings["value"])
        return table, problems, settings["source"] if settings["value"] else "default"

    def incident_sla_policy(self) -> dict[str, Any]:
        """The SLA targets in effect, where they come from, and what in the policy was ignored."""
        table, problems, source = self._sla_targets()
        return {"targets": table, "source": source, "problems": problems,
                "priorities": [{"priority": p, "label": PRIORITY_LABELS[p]} for p in PRIORITIES]}

    def incidents_board(self, env: str, include_resolved: bool = True) -> dict[str, Any]:
        """The environment's incidents with priority and SLA state, and the counts the list shows."""
        out = self.incidents_list(env, include_resolved)  # type: ignore[attr-defined]
        table, problems, source = self._sla_targets()
        now = dt.datetime.now(dt.UTC)
        me = (out.get("user") or "").casefold()
        counts = {"open": 0, "mine": 0, "unassigned": 0, "at_risk": 0, "breached": 0}
        for inc in out["incidents"]:
            inc["priority"] = inc.get("priority") or default_priority(inc)
            inc["source"] = inc.get("source") or "detected"
            inc["sla"] = sla.status(inc, table, now)
            if inc["state"] not in ACTIVE:
                continue
            counts["open"] += 1
            owner = (inc.get("owner") or "").casefold()
            counts["mine"] += bool(me and owner == me)
            counts["unassigned"] += not owner
            counts["at_risk"] += inc["sla"]["state"] == "at_risk"
            counts["breached"] += inc["sla"]["state"] in ("breached", "missed")  # missed: acknowledged late
        out.update(counts=counts, sla={"targets": table, "source": source, "problems": problems})
        return out

    def incident_report(self, env: str, title: str, text: str = "", priority: str = "P3",
                        workload: str = "", name: str = "") -> dict[str, Any]:
        """Open an incident for a problem a person noticed. With a workload, its team, technical
        owner and criticality come from the workload's assignment."""
        assigned = self.workload_store.assignments([workload]).get(workload, {}) if workload else {}  # type: ignore[attr-defined]
        pick = lambda field: str((assigned.get(field) or {}).get("value") or "")
        return self._incident_log(env).report(  # type: ignore[attr-defined]
            title, self._incident_user(), text=text, priority=priority, workload=workload or None,  # type: ignore[attr-defined]
            name=name, owner=pick("technical_owner"), team=pick("team"), criticality=pick("criticality"))

    def incident_set_priority(self, env: str, id: str, priority: str, note: str) -> dict[str, Any]:
        return self._incident_log(env).set_priority(id, priority, self._incident_user(), note)  # type: ignore[attr-defined]

    # ---- who an incident reaches (workloads/impact.py)

    def _incident(self, env: str, id: str) -> dict[str, Any]:
        found = next((i for i in self._incident_log(env).list() if i["id"] == id), None)  # type: ignore[attr-defined]
        if found is None:
            raise LookupError("That incident isn't in the list any more.")
        return found

    def _followers(self, product_ids: list[str]) -> dict[str, list[str]]:
        """Each product's followers, on a platform that keeps them; none on one that doesn't."""
        if "product_followers" not in core.OPERATIONS:
            return {}
        out: dict[str, list[str]] = {}
        for pid in product_ids:
            try:
                out[pid] = self._catalog().call("product_followers", product_id=pid)  # type: ignore[attr-defined]
            except (core.PlatformError, LookupError):  # an older UDF_Platform: owners and links still count
                return {}
        return out

    def incident_impact(self, env: str, id: str) -> dict[str, Any]:
        """The data products built on this incident's workload in its environment, the projects
        that read them and the people to tell. Read only. ``available`` is false for an incident
        that isn't about a metadata table, with the reason."""
        inc = self._incident(env, id)
        where = impact.workload_entity(inc.get("workload"))
        if where is None:
            return {"available": False, "reason": "This incident isn't about a table, so no data product is built on it."}
        products = self._catalog().call("list_products")  # type: ignore[attr-defined]
        first = impact.blast_radius(products, *where, env)
        out = impact.blast_radius(products, *where, env, self._followers([p["id"] for p in first["products"]]),
                                  me=self._wl_user() or "")  # type: ignore[attr-defined]
        told = [n for n in inc.get("notes") or [] if str(n.get("text", "")).startswith(_TOLD)]
        return out | {"available": True, "told": told[-1] if told else None}

    def incident_tell_impacted(self, env: str, id: str, people: list[str]) -> dict[str, Any]:
        """Post one Teams notice naming the people this incident reaches, through the platform's
        Teams routes, and note on the incident who was told. ``people`` are the UPNs the person
        confirmed: if the mesh changed since, nothing is posted. Nothing else changes."""
        inc = self._incident(env, id)
        found = self.incident_impact(env, id)
        if not found["available"] or not found["people"]:
            raise ValueError("Nobody downstream to tell: no published data product is built on this table here.")
        if sorted(str(p).lower() for p in people or []) != sorted(p["upn"] for p in found["people"]):
            raise ValueError("Who's affected changed since you looked. Nothing was posted; check the list and tell them again.")
        out = self.platform_notify([notices.incident_impact(inc, found)])  # type: ignore[attr-defined]
        result = (out.get("events") or [{}])[0]
        if result.get("result") == "skipped":
            return {"told": False, "reason": result.get("reason") or out.get("skipped") or "", "people": found["people"]}
        upns = [p["upn"] for p in found["people"]]
        self._incident_log(env).note(id, f"{_TOLD} {', '.join(upns)}", self._incident_user())  # type: ignore[attr-defined]
        return {"told": True, "result": result.get("result"), "routes": result.get("routes") or [], "people": found["people"]}

    # ---- history in the central lakehouse

    def _history_unavailable(self) -> dict[str, Any] | None:
        if not app_settings.value("allow_optimize_lakehouse"):
            return {"state": "off", "message": "Your organization has turned off the central lakehouse "
                                               "(allow_optimize_lakehouse in its policy file)."}
        if not self.central_lakehouse():  # type: ignore[attr-defined]
            return {"state": "not_connected", "message": "Connect this PC to the central KratosForge lakehouse "
                                                         "(Settings > Optimize lakehouses) to keep incident history."}
        return None

    def incidents_history_sync(self, approve: bool = False) -> dict[str, Any]:
        """Copy every environment's incidents and timelines to the central lakehouse, only what this
        PC hasn't copied before. An environment that can't be read is reported and skipped. When the
        incident tables need setting up, nothing is copied until the person confirms (``approve``)."""
        if (off := self._history_unavailable()) is not None:
            return off
        try:
            lake, me = self.central_family(FAMILY, approve=approve)  # type: ignore[attr-defined]
        except PermissionError as exc:
            message = str(exc)
            return {"state": "needs_confirm" if message.endswith("Confirm to continue.") else "refused",
                    "message": message}
        conn = self.central_lakehouse()  # type: ignore[attr-defined]
        sent = Sent(self.root / "incident_history_sent.json")  # type: ignore[attr-defined]
        envs = []
        for env in ENVIRONMENTS:
            try:
                log = self._incident_log(env)  # type: ignore[attr-defined]
                items = log.list()
            except Exception as exc:  # noqa: BLE001 - not set up, or can't be read: nothing to copy
                state, message = model.classify_error(exc)
                envs.append({"env": env, "state": state, "detail": message})
                continue
            ws = str(getattr(log.backend, "workspace_id", "") or "")
            events = sent.new(conn["lakehouse_id"], event_rows(items, env, ws))
            snaps = sent.new(conn["lakehouse_id"], snapshot_rows(items, env, ws))
            if events:
                setup.write(lake, me, "incident_event", events, family=FAMILY)
            if snaps:
                setup.write(lake, me, "incident_snapshot", snaps, family=FAMILY)
            sent.add(conn["lakehouse_id"], [r["record_id"] for r in events + snaps])
            envs.append({"env": env, "state": "copied", "events": len(events), "snapshots": len(snaps)})
        return {"state": "copied", "lakehouse": conn["lakehouse_name"], "envs": envs}

    def incidents_history(self, env: str = "", limit: int = 500) -> dict[str, Any]:
        """Incidents as the central lakehouse last saw them, newest first: each incident's latest
        state, including the ones the live list no longer keeps."""
        if (off := self._history_unavailable()) is not None:
            return off | {"incidents": []}
        conn = self.central_lakehouse()  # type: ignore[attr-defined]
        lake = self._optimize_lake(conn["workspace_id"], conn["lakehouse_id"], conn.get("schemas", True))  # type: ignore[attr-defined]
        try:
            df = lake.rows(FAMILY.table("incident_snapshot"))
        except Exception as exc:
            if "not found" not in str(exc).lower() and "TableNotFound" not in type(exc).__name__:
                raise
            df = None
        if df is None or df.is_empty():
            return {"state": "empty", "lakehouse": conn["lakehouse_name"], "incidents": []}
        if env:
            df = df.filter(df["env"] == env)
        # The newest state per incident; within the same second, a later stage of handling wins.
        rank = pl.col("state").replace_strict({"open": 0, "acknowledged": 1, "resolved": 2}, default=0)
        latest = (df.with_columns(_rank=rank).sort(["as_of", "_rank", "created_at"], nulls_last=False)
                  .unique(subset=["incident_id"], keep="last", maintain_order=True).drop("_rank"))
        latest = latest.sort("opened_at", descending=True, nulls_last=True).head(max(1, min(int(limit), 5000)))
        cols = [c for c in latest.columns if c not in ("record_id", "installation_id", "app_version", "schema_version")]
        return {"state": "ready", "lakehouse": conn["lakehouse_name"], "incidents": latest.select(cols).to_dicts()}
