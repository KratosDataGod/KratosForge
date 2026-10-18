"""The incident desk: numbers, priorities, SLAs and incidents people report.

Over the same incident log as incident_service.py (one document per environment, changed only
with conditional writes), so the agent, the app and every PC see the same numbers and priorities.
SLA targets come from the organization's policy (``incident_sla``) and are applied on read.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import app_settings
from .workloads import sla
from .workloads.incidents import ACTIVE, PRIORITIES, PRIORITY_LABELS, default_priority


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
        now = dt.datetime.now(dt.timezone.utc)
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
            counts["breached"] += inc["sla"]["state"] == "breached"
        out.update(counts=counts, sla={"targets": table, "source": source, "problems": problems})
        return out

    def incident_report(self, env: str, title: str, text: str = "", priority: str = "P3",
                        workload: str = "", name: str = "") -> dict[str, Any]:
        """Open an incident for a problem a person noticed. With a workload, its team, technical
        owner and criticality come from the workload's assignment."""
        assigned = self.workload_store.assignments([workload]).get(workload, {}) if workload else {}  # type: ignore[attr-defined]
        pick = lambda field: str((assigned.get(field) or {}).get("value") or "")  # noqa: E731
        return self._incident_log(env).report(  # type: ignore[attr-defined]
            title, self._incident_user(), text=text, priority=priority, workload=workload or None,  # type: ignore[attr-defined]
            name=name, owner=pick("technical_owner"), team=pick("team"), criticality=pick("criticality"))

    def incident_set_priority(self, env: str, id: str, priority: str, note: str) -> dict[str, Any]:
        return self._incident_log(env).set_priority(id, priority, self._incident_user(), note)  # type: ignore[attr-defined]
