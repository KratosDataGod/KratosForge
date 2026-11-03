"""Teams ask for a source with a Copy job, platform admins approve (scale plan stage 4.1, decision D2).

A ``source`` request may carry ``detail.copy`` (orchestration/copyjob_build.py): a Fabric connection,
the tables and how often. Sending it checks those fields here; the platform records it as any source
request. Approving it makes, as the person approving, before the platform marks it approved:

    1. the Copy job CJ_Landing_<source> in the landing workspace, writing each table to
       LH_Landing/Files/<source>/<table>/ (or the lakehouse of the source's zone, with one landing
       workspace); its description names the request
    2. its daily schedule, when the request asked for times

Then the platform registers the source, as for any source request. Each step is recorded on the
request (record_request_progress), so approving again after a failed step carries on: the Copy job
this request made is reused, and one of the same name it didn't make is never taken over. Creating
the job starts no run. Copy jobs made here are Preview until a tenant run has checked the definition.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..fabric_api import FabricError
from ..fabric_run import schedule_configuration
from ..redact import describe
from ..orchestration import copyjob_build
from . import core
from .requests_service import ITEM_DESCRIPTION, StepError, marked, request_marker

_log = logging.getLogger(__name__)

COPY_JOB_TYPE = "CopyJob"
SCHEDULE_JOB_TYPE = "Execute"  # the job type a Copy job runs as (workloads/inventory.py); unverified on a tenant


_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _gone(exc: FabricError) -> bool:
    """Fabric says the workspace isn't there. No access (403) isn't; the admin API confirms the rest."""
    return exc.status == 404 or exc.code in ("WorkspaceNotFound", "EntityNotFound")


class LandingCopyMixin:

    def platform_request(self, kind: str, target: str, reason: str = "", detail: dict[str, Any] | None = None,
                         project: str = "") -> dict[str, Any]:
        detail = dict(detail or {})
        if kind == "source" and "copy" in detail:
            try:
                detail["copy"] = copyjob_build.check(detail["copy"])
            except copyjob_build.CopyRequestError as exc:
                raise core.PlatformError(str(exc)) from exc
        return super().platform_request(kind, target, reason, detail, project)  # type: ignore[misc]

    def platform_decide(self, id: str, approve: bool, note: str = "", capacity_id: str = "",
                        env: str = "prod") -> dict[str, Any]:
        """Approving a source request that asks for a Copy job makes the job first. ``env`` names the
        environment whose LH_Landing it writes to when there's no one landing workspace."""
        req = None
        if approve:
            req = next((r for r in self._catalog().call("list_requests", status="waiting")  # type: ignore[attr-defined]
                        if r["id"] == id), None)
        if req is None or req["kind"] != "source" or not (req.get("detail") or {}).get("copy"):
            return super().platform_decide(id, approve, note, capacity_id)  # type: ignore[misc]
        if not req.get("can_decide"):
            raise PermissionError("Only a platform admin can approve this request")
        made = self._lc_make(req, env)
        out = super().platform_decide(id, approve, note, capacity_id)  # type: ignore[misc]
        out["made"] = made
        return out

    # ---- carrying it out

    def _lc_where(self, req: dict[str, Any], env: str) -> dict[str, str]:
        """The workspace and lakehouse the Copy job writes to."""
        central = self._central_landing()  # type: ignore[attr-defined]
        if central:
            zone_id = str(req["detail"].get("zone") or core.SHARED_ZONE)
            zone = next((z for z in self._landing_zones() if z["id"] == zone_id), None)  # type: ignore[attr-defined]
            names = {"zone": zone_id, "zone_name": (zone or {}).get("name") or zone_id.title(),
                     "secure": bool((zone or {}).get("secure")),
                     "workspace_name": (zone or {}).get("workspace_name") or central.get("workspace_name") or ""}
            if zone and zone.get("workspace_id") and zone.get("lakehouse_id"):
                return names | {"workspace_id": zone["workspace_id"], "lakehouse_id": zone["lakehouse_id"],
                                "lakehouse_name": zone.get("lakehouse_name") or ""}
            if zone_id == core.SHARED_ZONE and central.get("lakehouse_id"):
                return names | {"workspace_id": central["workspace_id"], "lakehouse_id": central["lakehouse_id"],
                                "lakehouse_name": "LH_Landing"}
            raise StepError(f"The landing zone {zone_id} has no lakehouse yet: set it up on Platform > Set up > "
                            "Landing zone, then approve again")
        cfg = self._platform_config().get(env, {})  # type: ignore[attr-defined]
        if not (cfg.get("workspace_id") and cfg.get("landing_id")):
            raise StepError(f"{env.title()} has no LH_Landing in Fabric yet: create the platform for {env.title()} on "
                            "Platform > Settings, then approve again")
        return {"workspace_id": cfg["workspace_id"], "lakehouse_id": cfg["landing_id"], "lakehouse_name": "LH_Landing",
                "workspace_name": cfg.get("workspace_name") or "", "zone": "", "zone_name": "", "secure": False}

    def platform_requests(self, status: str = "") -> list[dict[str, Any]]:
        """Each waiting source request that asks for a Copy job says where it would land with one landing
        workspace (``lands_in``: its zone and lakehouse), so the approver sees it before approving: a
        requester picks the zone. Without one landing workspace the approver picks the environment."""
        out = super().platform_requests(status)  # type: ignore[misc]
        asks = [r for r in out if r.get("kind") == "source" and r.get("status") == "waiting"
                and (r.get("detail") or {}).get("copy")]
        if asks and self._central_landing():  # type: ignore[attr-defined]
            for r in asks:
                try:
                    r["lands_in"] = self._lc_where(r, "prod")
                except StepError as exc:
                    r["lands_in"] = {"problem": str(exc)}
        return out

    def _lc_make(self, req: dict[str, Any], env: str) -> dict[str, Any]:
        d = req["detail"]
        try:
            spec = copyjob_build.check(d["copy"])
        except copyjob_build.CopyRequestError as exc:
            raise core.PlatformError(f"{exc}. Decline the request and ask for it again.") from exc
        made = dict(d.get("made") or {})
        where = self._lc_where(req, env)
        api = self._rq_fabric()  # type: ignore[attr-defined]
        source, name = req["target"], copyjob_build.job_name(req["target"])
        marker = request_marker(req)

        def find_or_create() -> tuple[dict[str, Any], bool]:
            same = [i for i in api.list_items(where["workspace_id"])
                    if i.get("type") == COPY_JOB_TYPE and str(i.get("displayName") or "").lower() == name.lower()]
            ours = next((i for i in same if i.get("id") == made.get("copy_job_id")
                         or marker in str(i.get("description") or "")), None)
            if ours:
                return ours, False
            if same:
                raise core.PlatformError(f"A Copy job named {name} is already in the landing workspace and this "
                                         "request didn't make it. Decline the request, or rename that Copy job.")
            job = copyjob_build.content(spec, source, where["workspace_id"], where["lakehouse_id"])
            why = str(req.get("reason") or "").strip()
            created = api.save_item(where["workspace_id"], COPY_JOB_TYPE, name, copyjob_build.parts(job),
                                    marked(f"Lands {source} in the landing zone. {why}", marker, ITEM_DESCRIPTION))
            return created, True

        item, created = self._rq_step(f"Create the Copy job {name}", find_or_create)  # type: ignore[attr-defined]
        if not item.get("id"):
            raise StepError(f"Create the Copy job {name} failed, so the request is still waiting: Fabric returned no id")
        if created or made.get("copy_job_id") != item["id"]:
            made |= {"copy_job_id": item["id"], "workspace_id": where["workspace_id"], "copy_job_name": name}
            self._rq_progress(req, {k: made[k] for k in ("copy_job_id", "workspace_id", "copy_job_name")})  # type: ignore[attr-defined]
        if spec["times"] and not made.get("schedule_id"):
            config = schedule_configuration("daily", spec["times"])

            def schedule() -> dict[str, Any]:
                jobs = self.fabric_jobs  # type: ignore[attr-defined]
                have = jobs.list_schedules(where["workspace_id"], item["id"], SCHEDULE_JOB_TYPE)
                return have[0] if have else jobs.create_schedule(where["workspace_id"], item["id"],
                                                                 SCHEDULE_JOB_TYPE, config)

            sched = self._rq_step(f"Schedule {name} daily at {', '.join(spec['times'])} UTC", schedule)  # type: ignore[attr-defined]
            made["schedule_id"] = str(sched.get("id") or "")
            self._rq_progress(req, {"schedule_id": made["schedule_id"]})  # type: ignore[attr-defined]
        return made

    # ---- a source isn't deleted while the Copy job that lands it is still in Fabric

    def platform_delete_source(self, name: str) -> list[dict[str, Any]]:
        """Refuses while a Copy job a request made for this source is still in the landing workspace:
        deleting the source would leave the job running on its schedule with nothing in the app that
        names it. Nothing in Fabric is changed. A job that's gone from Fabric no longer holds it."""
        source = core.key_name(name)
        if not self._catalog().call("whoami").get("admin"):  # type: ignore[attr-defined]
            # Anyone else is refused by the platform as before, before any request record is read.
            return super().platform_delete_source(name)  # type: ignore[misc]
        jobs = self._lc_jobs_for(source)
        if jobs:
            api = self._rq_fabric()  # type: ignore[attr-defined]
            for job in jobs:
                try:
                    there = any(i.get("id") == job["id"] for i in api.list_items(job["workspace_id"]))
                except FabricError as exc:
                    if not (_gone(exc) and self._lc_workspace_gone(job["workspace_id"])):
                        raise self._lc_unchecked(source, job, exc) from exc
                    there = False  # the landing workspace itself is gone, and its Copy job with it
                except Exception as exc:
                    raise self._lc_unchecked(source, job, exc) from exc
                if there:
                    raise core.PlatformError(
                        f"{source} has the Copy job {job['name']} in the landing workspace, on its schedule: "
                        "delete it in Fabric first, then delete the source")
        return super().platform_delete_source(name)  # type: ignore[misc]

    def _lc_workspace_gone(self, workspace_id: str) -> bool:
        """Some tenants answer "not found" to someone who isn't a member, so a 404 alone could leave a
        running Copy job with no source. Fabric's admin API sees every workspace: gone only when it
        says so too. Unreadable (not a Fabric admin) is not gone."""
        try:
            ws = self._fabric_admin().admin_get_workspace(workspace_id)  # type: ignore[attr-defined]
        except FabricError as exc:
            if exc.status == 404 or exc.code in ("WorkspaceNotFound", "EntityNotFound"):
                return True
            _log.info("Fabric's admin API couldn't confirm the landing workspace is gone: %s", describe(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - unconfirmed keeps the source
            _log.info("Fabric's admin API couldn't confirm the landing workspace is gone: %s", describe(exc))
            return False
        return str(ws.get("state") or "") in ("Deleted", "Removing")

    def _lc_unchecked(self, source: str, job: dict[str, str], exc: BaseException) -> core.PlatformError:
        _log.info("Couldn't read the landing workspace for %s's Copy job: %s", source, describe(exc))
        return core.PlatformError(
            f"Couldn't check whether {source}'s Copy job {job['name']} is still in the landing "
            f"workspace ({describe(exc)}), so the source is kept. Try again when Fabric can be reached")

    def _lc_jobs_for(self, source: str) -> list[dict[str, str]]:
        """The Copy jobs any request for this source recorded making, whatever became of the request:
        a declined or withdrawn one can have made its job before it stopped."""
        out, seen = [], set()
        for r in self._catalog().call("list_requests"):  # type: ignore[attr-defined]
            m = (r.get("detail") or {}).get("made") or {}
            jid, ws = str(m.get("copy_job_id") or ""), str(m.get("workspace_id") or "")
            if r.get("kind") != "source" or str(r.get("target") or "").strip().lower() != source or not jid or jid in seen:
                continue
            if not _GUID.fullmatch(jid):
                # A job id that isn't Fabric's can't name a Copy job, and never goes into a Fabric call.
                _log.warning("Request %s for %s recorded a Copy job id KratosForge can't read; skipped",
                             r.get("id"), source)
                continue
            if not _GUID.fullmatch(ws):
                # The job may be real, but where it is can't be read: keep the source.
                raise core.PlatformError(
                    f"{source}'s request recorded the Copy job {jid} in a landing workspace KratosForge can't read, "
                    "so the source is kept. Check that Copy job in Fabric, and delete it there if it isn't wanted")
            seen.add(jid)
            out.append({"id": jid, "workspace_id": ws,
                        "name": str(m.get("copy_job_name") or copyjob_build.job_name(source))})
        return out

    # ---- the landing zone shows which sources a Copy job lands

    def platform_landing(self, env: str = "dev") -> dict[str, Any]:
        out = super().platform_landing(env)  # type: ignore[misc]
        try:
            approved = self._catalog().call("list_requests", status="approved")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the sources still show, without their Copy jobs
            _log.info("Requests not read for the landing zone's Copy jobs: %s", describe(exc))
            return out
        jobs = {}
        for r in approved:
            d = r.get("detail") or {}
            m = d.get("made") or {}
            if r.get("kind") == "source" and m.get("copy_job_id"):
                jobs[r["target"]] = {"id": m["copy_job_id"], "workspace_id": m.get("workspace_id", ""),
                                     "name": m.get("copy_job_name") or copyjob_build.job_name(r["target"]),
                                     "tables": len((d.get("copy") or {}).get("tables") or []),
                                     "times": (d.get("copy") or {}).get("times") or [], "request_id": r["id"]}
        for s in out.get("sources", []):
            if s["name"] in jobs:
                s["copy_job"] = jobs[s["name"]]
        return out
