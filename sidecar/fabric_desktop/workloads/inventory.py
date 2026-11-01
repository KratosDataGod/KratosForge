"""Turn each native source into workload records.

Every function here takes plain data (what the services already return) and returns plain
dicts, so the rules can be tested without Fabric. Nothing is copied from a definition except
its name, a short description and where it lives.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable

from . import model
from .model import ROLES, observation, run_state

ENV_LABELS = {"dev": "Dev", "test": "Test", "prod": "Prod"}
# Fabric item type -> its job type (schedules use it too). Copy jobs and dataflows run through their
# own "execute" path (FabricClient.run_item_execute).
SUPPORTED_FABRIC_TYPES = {"Notebook": "RunNotebook", "DataPipeline": "Pipeline", "SparkJobDefinition": "sparkjob",
                          "CopyJob": "Execute", "Dataflow": "Execute"}
FABRIC_TYPE_LABELS = {"Notebook": "Fabric notebook", "DataPipeline": "Pipeline", "SparkJobDefinition": "Spark job definition",
                      "CopyJob": "Copy job", "Dataflow": "Dataflow Gen2"}
RUNNER_NOTEBOOK = "nb_fabric_desktop_runner"
FABRIC_APP = "https://app.fabric.microsoft.com"
# Item types tagged Preview in the app until a run against a real tenant has been checked.
PREVIEW_FABRIC_TYPES = {"CopyJob", "Dataflow"}
_ITEM_PATHS = {"Notebook": "synapsenotebooks", "DataPipeline": "pipelines", "SparkJobDefinition": "sparkjobdefinitions"}


def fabric_url(workspace_id: str, item_id: str, item_type: str) -> str:
    return f"{FABRIC_APP}/groups/{workspace_id}/{_ITEM_PATHS.get(item_type, 'items')}/{item_id}?experience=fabric-developer"


# --------------------------------------------------------------------------- runs


def meta_run(r: dict[str, Any], env: str, source_label: str) -> dict[str, Any]:
    """A metadata run_log row, normalized. Fabric runs carry batch ids "fabric-..."; the agent sets worker."""
    batch = str(r.get("batch_id") or "")
    if batch.startswith("fabric-") or r.get("compute") == "fabric":
        where, label = "fabric", "Fabric"
    elif r.get("worker"):
        where = "agent"
        label = f"Agent {r['worker']}" + (f" on {r['host']}" if r.get("host") else "")
    else:
        where, label = "this_pc", "This PC"
    started = model.parse_time(r.get("started_at"))
    seconds = r.get("seconds")
    ended = started + dt.timedelta(seconds=float(seconds)) if started and seconds not in (None, "") else None
    return {"id": r.get("run_id") or "", "state": run_state(r.get("status")), "native_status": r.get("status"),
            "started_at": model.iso(started), "ended_at": model.iso(ended), "seconds": seconds, "env": env,
            "where": where, "ran_on": r.get("ran_on") or label, "message": r.get("message") or "",
            "rows": r.get("rows_written"), "job_id": r.get("job_id") or "", "source": source_label,
            "open": {"kind": "meta_run", "run_id": r.get("run_id"), "env": env}}


def fabric_run(r: dict[str, Any]) -> dict[str, Any]:
    """A job this app started in Fabric (fabric_runs.jsonl)."""
    return {"id": r.get("job_id") or "", "state": run_state(r.get("status"), "fabric"), "native_status": r.get("status"),
            "started_at": r.get("started_at"), "ended_at": r.get("finished_at"), "seconds": None,
            "env": r.get("env") or "", "where": "fabric", "ran_on": f"Fabric · {r.get('workspace_name') or 'workspace'}",
            "message": r.get("message") or "", "rows": None, "job_id": r.get("job_id") or "",
            "source": "Fabric job started from this app",
            "open": {"kind": "url", "url": r.get("url")} if r.get("url") else None}


def fabric_job(j: dict[str, Any], workspace_name: str = "") -> dict[str, Any]:
    """A Fabric job instance (the job scheduler API)."""
    reason = j.get("failureReason") or {}
    return {"id": j.get("id") or "", "state": run_state(j.get("status"), "fabric"), "native_status": j.get("status"),
            "started_at": j.get("startTimeUtc") or j.get("started"), "ended_at": j.get("endTimeUtc") or j.get("ended"),
            "seconds": None, "env": "", "where": "fabric", "ran_on": f"Fabric · {workspace_name or 'workspace'}",
            "message": (reason.get("message", "") if isinstance(reason, dict) else str(reason)) or j.get("message", ""),
            "rows": None, "job_id": j.get("id") or "", "source": "Fabric job history",
            "invoke": j.get("invokeType") or "", "open": None}


def pc_schedule_run(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"{r.get('schedule_id')}:{r.get('started')}", "state": run_state(r.get("status")),
            "native_status": r.get("status"), "started_at": r.get("started"), "ended_at": r.get("ended"),
            "seconds": r.get("seconds"), "env": "", "where": "this_pc", "ran_on": "This PC (schedule)",
            "message": r.get("message") or "", "rows": r.get("rows"), "job_id": "",
            "source": "Schedules on this PC", "open": {"kind": "pc_schedule", "schedule_id": r.get("schedule_id")}}


def _newest(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=lambda r: str(r.get("started_at") or ""), reverse=True)


# --------------------------------------------------------------------------- ownership


def owners(declared: dict[str, tuple[str, str]], assigned: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each role's owner. ``declared`` is {role: (value, where)} from Git; it wins over an assignment."""
    out: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        value, where = declared.get(role, ("", ""))
        if str(value).strip():
            out[role] = {"value": str(value).strip(), "source": "declared", "where": where,
                         "placeholder": str(value).strip().lower() in model.PLACEHOLDER_OWNERS}
        elif role in assigned:
            a = assigned[role]
            out[role] = {"value": a["value"], "source": "assigned", "where": "on this PC",
                         "set_by": a.get("set_by"), "set_at": a.get("set_at"), "placeholder": False}
    return out


def _extras(assigned: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    crit = assigned.get("criticality")
    tags = (assigned.get("tags") or {}).get("value") or []
    return ({"value": crit["value"], "source": "assigned", "where": "on this PC",
             "set_by": crit.get("set_by"), "set_at": crit.get("set_at")} if crit else None), list(tags)


# --------------------------------------------------------------------------- metadata entities


def meta_workloads(project: dict[str, Any], *, version: str, impacts: dict[str, list[str]],
                   env_data: dict[str, dict[str, Any]], freshness: list[dict[str, Any]],
                   pc_schedules: list[dict[str, Any]], fabric_schedules: dict[str, list[dict[str, Any]]],
                   assignments: dict[str, dict[str, dict[str, Any]]], user: str = "") -> list[dict[str, Any]]:
    """One workload per entity of the open metadata project.

    ``project`` is ``Project.summary()``. ``env_data`` is {env: {"configured", "observation",
    "runs", "deployments"}} for every environment; ``fabric_schedules`` is {env: [schedule]} for
    environments whose runner is scheduled in Fabric."""
    ident = project.get("project") or {}
    pname = ident.get("name") or "project"
    team = ident.get("team") or ""
    contacts = ident.get("contacts") or []
    envs_cfg = project.get("environments") or {}
    products = project.get("products") or []
    errors = project.get("errors") or []
    by_env_entity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for env, data in env_data.items():
        for r in data.get("runs") or []:
            by_env_entity.setdefault((env, r.get("entity", "")), []).append(r)
    fresh = {(f["env"], f["entity"]): f for f in freshness}
    out = []
    for e in project.get("entities") or []:
        name = e["name"]
        wid = model.meta_id(pname, name)
        assigned = assignments.get(wid, {})
        declared = {"team": (team, "project.toml"), "technical_owner": (e.get("owner", ""), "entity file"),
                    "business_contact": (contacts[0] if contacts else "", "project.toml")}
        crit, tags = _extras(assigned)
        deployments, runs = [], []
        for env in ENV_LABELS:
            data = env_data.get(env) or {}
            if not data.get("configured"):
                continue
            cfg = envs_cfg.get(env) or {}
            compute = cfg.get("compute", "local")
            local_tables = cfg.get("target", "onelake" if env != "dev" else "local") == "local"
            ws_name = cfg.get("workspace_name") or ""
            if compute == "fabric":
                location = {"kind": "fabric", "label": f"Fabric · {ws_name or 'workspace'}",
                            "workspace_id": cfg.get("workspace_id", ""), "workspace_name": ws_name}
                identity = {"kind": "you_or_schedule_owner",
                            "label": "You, for runs started here; the schedule's owner for Fabric schedules"}
            else:
                location = {"kind": "this_pc", "label": "This PC"}
                identity = {"kind": "you", "label": f"You{f' ({user})' if user else ''}, signed in on this PC"}
            env_runs = [meta_run(r, env, f"meta.run_log in {ENV_LABELS[env]}")
                        for r in by_env_entity.get((env, name), [])]
            runs += env_runs
            dep_rows = sorted(data.get("deployments") or [], key=lambda d: str(d.get("at") or ""), reverse=True)
            deployed = dep_rows[0] if dep_rows else None
            version_info = ({"deployed": version, "current": version, "behind": False, "at": None, "from": None}
                            if env == "dev" else
                            {"deployed": deployed.get("version") if deployed else None, "current": version,
                             "behind": bool(deployed and deployed.get("version") != version),
                             "at": deployed.get("at") if deployed else None,
                             "from": deployed.get("source_env") if deployed else None})
            schedules = [{"where": "This PC", "text": s.get("text", ""), "enabled": s.get("enabled", True),
                          "id": s.get("id"), "next_run": s.get("next_run")}
                         for s in pc_schedules if (s.get("target") or {}).get("kind") == "meta"
                         and (s.get("target") or {}).get("env") == env
                         and (not (s.get("target") or {}).get("entities") or name in s["target"]["entities"])]
            schedules += [{"where": "Fabric", "text": s.get("text", ""), "enabled": s.get("enabled", False),
                           "id": s.get("id")} for s in fabric_schedules.get(env, [])]
            deployments.append({
                "env": env, "env_label": ENV_LABELS[env], "location": location, "identity": identity,
                "tables_in": "This PC only" if local_tables else (ws_name or "Workspace"),
                "version": version_info, "schedules": schedules,
                "latest_run": env_runs[0] if env_runs else None,
                "freshness": fresh.get((env, name)),
                "observation": data.get("observation") or observation("meta.run_log", "unknown"),
                "actions": _meta_actions(data, compute),
            })
        downstream = impacts.get(name, [])
        feeds = [{"product": p["name"], "title": p.get("title") or p["name"], "port": port["name"],
                  "status": p.get("status"), "owner": p.get("owner", "")}
                 for p in products for port in p.get("ports") or [] if port.get("entity") == name]
        out.append({
            "id": wid, "kind": "meta_entity", "kind_label": model.KIND_LABELS["meta_entity"], "name": name,
            "description": e.get("description") or "",
            "group": {"type": "project", "id": f"project:{pname}", "label": pname},
            "definition": {"project": pname, "entity": name, "layer": e.get("layer"), "path": e.get("path"),
                           "table": f"{e.get('schema')}.{e.get('table')}", "mode": e.get("mode")},
            "capabilities": ["discoverable", "observable", "runnable", "schedulable", "deployable"],
            "owners": owners(declared, assigned), "criticality": crit, "tags": tags,
            "deployments": deployments, "runs": _newest(runs),
            "errors": [x.split(": ", 1)[1] if ": " in x else x for x in errors if x.startswith(f"{name}: ")],
            "dependencies": {"upstream": [{"name": r, "declared": True} for r in e.get("reads") or []],
                             "downstream": [{"name": d, "declared": True} for d in downstream],
                             "products": feeds},
            "search": " ".join([name, pname, team, e.get("owner", ""), e.get("layer") or "", e.get("description") or ""]),
        })
    return out


def _meta_actions(data: dict[str, Any], compute: str) -> list[str]:
    obs_state = (data.get("observation") or {}).get("state")
    if obs_state in ("not_set_up",):
        return []
    return ["run"]


# --------------------------------------------------------------------------- notebooks


def notebook_link(name: str, nb: dict[str, Any], links: dict[str, dict[str, Any]],
                  fabric_runs: list[dict[str, Any]], fabric_items: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Which Fabric item this notebook is: recorded or hand-made link first, then a run this app
    started (its item id), and only then the same name in its publish workspace (labelled).

    ``kept`` is true only for a link this PC keeps (the ``link`` table): the only kind Unlink forgets.
    The other two are found again on every read, so unlinking them changes nothing."""
    if name in links:
        link = links[name]
        return {"workspace_id": link["workspace_id"], "workspace_name": link.get("workspace_name") or "",
                "item_id": link["item_id"], "how": link.get("how") or "recorded", "at": link.get("at"), "kept": True}
    for r in reversed(fabric_runs):
        if r.get("kind") == "Notebook run" and r.get("name") == name and r.get("item_id"):
            return {"workspace_id": r["workspace_id"], "workspace_name": r.get("workspace_name") or "",
                    "item_id": r["item_id"], "how": "from a run", "at": r.get("started_at"), "kept": False}
    ws = (nb.get("workspace") or {}).get("workspace_id")
    if ws and fabric_items:
        same = [i for i in fabric_items if i.get("workspace_id") == ws and i.get("type") == "Notebook"
                and i.get("displayName") == name]
        if len(same) == 1:
            return {"workspace_id": ws, "workspace_name": same[0].get("workspace_name") or "",
                    "item_id": same[0]["id"], "how": "name match", "at": None, "kept": False}
    return None


def notebook_workloads(notebooks: list[dict[str, Any]], *, links: dict[str, dict[str, Any]],
                       fabric_runs: list[dict[str, Any]], fabric_items: list[dict[str, Any]] | None,
                       fabric_items_obs: dict[str, Any], fabric_schedules: list[dict[str, Any]],
                       pc_schedules: list[dict[str, Any]], pc_runs: list[dict[str, Any]],
                       assignments: dict[str, dict[str, dict[str, Any]]], user: str = "",
                       signed_in: bool = False) -> list[dict[str, Any]]:
    """One workload per notebook on this PC, with its Fabric item inside it when linked."""
    out = []
    local_obs = observation("Notebooks on this PC", "live")
    runs_by_item: dict[Any, list[dict[str, Any]]] = {}
    for r in fabric_runs:
        runs_by_item.setdefault(r.get("item_id"), []).append(r)
    pc_by_schedule: dict[Any, list[dict[str, Any]]] = {}
    for r in pc_runs:
        pc_by_schedule.setdefault(r.get("schedule_id"), []).append(r)
    for nb in notebooks:
        name = nb["name"]
        wid = model.notebook_id(name)
        assigned = assignments.get(wid, {})
        crit, tags = _extras(assigned)
        link = notebook_link(name, nb, links, fabric_runs, fabric_items)
        my_schedules = [s for s in pc_schedules if (s.get("target") or {}).get("kind") == "notebook"
                        and s["target"].get("name") == name]
        ids = {s.get("id") for s in my_schedules}
        runs = _newest(pc_schedule_run(r) for i in ids for r in pc_by_schedule.get(i, []))
        deployments = [{
            "env": None, "env_label": "", "location": {"kind": "this_pc", "label": "This PC"},
            "identity": {"kind": "you", "label": f"You{f' ({user})' if user else ''}, signed in on this PC"},
            "tables_in": (nb.get("lakehouse") or {}).get("name") or "No lakehouse attached",
            "version": None,
            "schedules": [{"where": "This PC", "text": s.get("text", ""), "enabled": s.get("enabled", True),
                           "id": s.get("id"), "next_run": s.get("next_run")} for s in my_schedules],
            "latest_run": runs[0] if runs else None, "freshness": None,
            "observation": observation("Schedules on this PC", "live",
                                       detail="Runs you start in the notebook itself aren't recorded as runs."),
            "actions": ["run_schedule"] if my_schedules else [],
        }]
        if link:
            f_runs = [fabric_run(r) for r in runs_by_item.get(link["item_id"], [])]
            sched = [s for s in fabric_schedules if s.get("item_id") == link["item_id"]]
            runs += f_runs
            last = next((s.get("last") for s in sched if s.get("last")), None)
            if last and not any(r["started_at"] == last.get("started") for r in f_runs):
                runs.append(fabric_job({"status": last.get("status"), "startTimeUtc": last.get("started"),
                                        "endTimeUtc": last.get("ended"), "message": last.get("message")},
                                       link.get("workspace_name", "")) | {"source": "Fabric schedule's last run"})
            deployments.append({
                "env": None, "env_label": "",
                "location": {"kind": "fabric", "label": f"Fabric · {link.get('workspace_name') or 'workspace'}",
                             "workspace_id": link["workspace_id"], "workspace_name": link.get("workspace_name") or "",
                             "item_id": link["item_id"], "url": fabric_url(link["workspace_id"], link["item_id"], "Notebook")},
                "identity": {"kind": "you_or_schedule_owner",
                             "label": "You, for runs started here; the schedule's owner for Fabric schedules"},
                "tables_in": (nb.get("lakehouse") or {}).get("workspace_name") or "",
                "version": None, "link": link,
                "schedules": [{"where": "Fabric", "text": s.get("text", ""), "enabled": s.get("enabled", False),
                               "id": s.get("id")} for s in sched],
                "latest_run": _newest(f_runs)[0] if f_runs else None, "freshness": None,
                "observation": fabric_items_obs if link["how"] == "name match" else observation(
                    "Fabric jobs started from this app", "live" if signed_in else "cached"),
                "actions": ["run_in_fabric"] + (["cancel"] if any(r["state"] == "running" for r in f_runs) else []),
            })
        out.append({
            "id": wid, "kind": "notebook", "kind_label": model.KIND_LABELS["notebook"], "name": name,
            "description": "", "group": {"type": "notebooks", "id": "notebooks", "label": "Notebooks on this PC"},
            "definition": {"name": name, "modified": nb.get("modified"), "compute": nb.get("compute"),
                           "lakehouse": nb.get("lakehouse"), "workspace": nb.get("workspace")},
            "capabilities": ["discoverable", "observable", "runnable", "schedulable", "deployable"]
                            + (["cancelable"] if link else []),
            "owners": owners({}, assigned), "criticality": crit, "tags": tags,
            "deployments": deployments, "runs": _newest(runs), "errors": [], "link": link,
            "dependencies": {"upstream": [], "downstream": [], "products": []},
            "observation": local_obs,
            "search": " ".join([name, (nb.get("lakehouse") or {}).get("name") or "",
                                (nb.get("workspace") or {}).get("workspace_name") or ""]),
        })
    return out


# --------------------------------------------------------------------------- pipelines on this PC (Preview)


def pipeline_run(r: dict[str, Any], message: str = "") -> dict[str, Any]:
    """A local pipeline run (pipeline_service.pipeline_runs). ``mocked`` counts the activities it
    mocked: a run with mocks never shows the whole pipeline works."""
    native = r.get("state")
    state = "cancelled" if native == "interrupted" else run_state(native)
    if native == "interrupted" and not message:
        message = "KratosForge stopped while this run was going. It wasn't resumed."
    return {"id": r.get("run_id") or "", "state": state, "native_status": native, "started_at": r.get("started"),
            "ended_at": r.get("ended"), "seconds": r.get("seconds"), "env": "", "where": "this_pc",
            "ran_on": "This PC", "message": message or r.get("stop_reason") or "", "rows": None, "job_id": "",
            "mocked": len((r.get("options") or {}).get("mocked") or []), "source": "Pipeline runs on this PC",
            "open": {"kind": "pipeline_run", "run_id": r.get("run_id")}}


def pipeline_workloads(pipelines: list[dict[str, Any]], *, runs: list[dict[str, Any]],
                       pc_schedules: list[dict[str, Any]], pc_runs: list[dict[str, Any]],
                       assignments: dict[str, dict[str, dict[str, Any]]], user: str = "") -> list[dict[str, Any]]:
    """One workload per pipeline in the repositories this PC opened, and per pipeline a schedule here
    runs. A run counts for a pipeline when its schedule started it, or when the pipeline's name is
    unique among them (runs are kept by name); a shared name never guesses which one ran."""
    by_path: dict[str, dict[str, Any]] = {}
    for p in pipelines:
        by_path.setdefault(str(p["path"]), p)
    for s in pc_schedules:
        t = s.get("target") or {}
        if t.get("kind") == "pipeline" and t.get("path") and str(t["path"]) not in by_path:
            by_path[str(t["path"])] = {"path": str(t["path"]), "name": t.get("name") or Path(str(t["path"])).name
                                       .removesuffix(".DataPipeline"), "repo": "", "relative": "", "scheduled_only": True}
    names: dict[str, int] = {}
    for p in by_path.values():
        names[p["name"]] = names.get(p["name"], 0) + 1
    runs_by_id = {r.get("run_id"): r for r in runs}
    pc_by_schedule: dict[Any, list[dict[str, Any]]] = {}
    for r in pc_runs:
        pc_by_schedule.setdefault(r.get("schedule_id"), []).append(r)
    out = []
    for path, p in by_path.items():
        wid = model.pipeline_id(path)
        assigned = assignments.get(wid, {})
        crit, tags = _extras(assigned)
        mine = [s for s in pc_schedules if (s.get("target") or {}).get("kind") == "pipeline"
                and str(s["target"].get("path")) == path]
        found: dict[str, dict[str, Any]] = {}
        unrecorded: list[dict[str, Any]] = []
        for s in mine:
            for row in pc_by_schedule.get(s.get("id"), []):
                rec = runs_by_id.get(row.get("run_id"))
                if rec:
                    found[rec["run_id"]] = pipeline_run(rec, row.get("message") or "")
                else:  # it couldn't start, or its record is gone: the schedule's own row says how it went
                    unrecorded.append(pc_schedule_run(row))
        if names[p["name"]] == 1:
            for rec in runs:
                if rec.get("pipeline") == p["name"] and rec.get("run_id") not in found:
                    found[rec["run_id"]] = pipeline_run(rec)
        history = _newest(list(found.values()) + unrecorded)
        where = " · ".join(x for x in (p.get("repo"), p.get("relative")) if x)
        out.append({
            "id": wid, "kind": "pipeline", "kind_label": model.KIND_LABELS["pipeline"], "name": p["name"],
            "description": where, "preview": True,
            "group": {"type": "pipelines", "id": "pipelines", "label": "Local pipelines"},
            "definition": {"path": path, "repo": p.get("repo") or "", "relative": p.get("relative") or "",
                           "activity_count": p.get("activity_count")},
            "capabilities": ["discoverable", "observable", "runnable", "schedulable"],
            "owners": owners({}, assigned), "criticality": crit, "tags": tags,
            "deployments": [{
                "env": None, "env_label": "", "location": {"kind": "this_pc", "label": "This PC"},
                "identity": {"kind": "you", "label": f"You{f' ({user})' if user else ''}, signed in on this PC"},
                "tables_in": "", "version": None,
                "schedules": [{"where": "This PC", "text": s.get("text", ""), "enabled": s.get("enabled", True),
                               "id": s.get("id"), "next_run": s.get("next_run")} for s in mine],
                "latest_run": history[0] if history else None, "freshness": None,
                "observation": observation("Pipelines on this PC", "live",
                                           detail="Runs started from the Pipelines page and from schedules here."),
                "actions": ["run_schedule"] if mine else [],
            }],
            "runs": history,
            "errors": [p["problem"]] if p.get("problem") else [],
            "dependencies": {"upstream": [], "downstream": [], "products": []},
            "observation": observation("Pipelines on this PC", "live"),
            "search": " ".join([p["name"], where, path]),
        })
    return out


# --------------------------------------------------------------------------- Fabric items


def fabric_workloads(items: list[dict[str, Any]], *, items_obs: dict[str, Any], linked: set[str],
                     fabric_schedules: list[dict[str, Any]], fabric_runs: list[dict[str, Any]],
                     assignments: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Supported Fabric items in the workspaces this app works in, except ones shown inside a
    notebook workload (``linked`` item ids) and the metadata runner, which belongs to its project."""
    out = []
    sched_by_item: dict[Any, list[dict[str, Any]]] = {}
    for s in fabric_schedules:
        sched_by_item.setdefault(s.get("item_id"), []).append(s)
    runs_by_item: dict[Any, list[dict[str, Any]]] = {}
    for r in fabric_runs:
        runs_by_item.setdefault(r.get("item_id"), []).append(r)
    for it in items:
        if it.get("type") not in SUPPORTED_FABRIC_TYPES or it.get("id") in linked:
            continue
        if it.get("type") == "Notebook" and it.get("displayName") == RUNNER_NOTEBOOK:
            continue
        ws, iid = it["workspace_id"], it["id"]
        wid = model.fabric_id(ws, iid)
        assigned = assignments.get(wid, {})
        crit, tags = _extras(assigned)
        sched = sched_by_item.get(iid, [])
        runs = [fabric_run(r) for r in runs_by_item.get(iid, [])]
        last = next((s.get("last") for s in sched if s.get("last")), None)
        if last and not any(r["started_at"] == last.get("started") for r in runs):
            runs.append(fabric_job({"status": last.get("status"), "startTimeUtc": last.get("started"),
                                    "endTimeUtc": last.get("ended"), "message": last.get("message")},
                                   it.get("workspace_name", "")) | {"source": "Fabric schedule's last run"})
        runs = _newest(runs)
        observed = bool(runs)
        out.append({
            "id": wid, "kind": "fabric_item", "kind_label": FABRIC_TYPE_LABELS[it["type"]],
            "preview": it["type"] in PREVIEW_FABRIC_TYPES,
            "name": it.get("displayName") or iid, "description": it.get("description") or "",
            "group": {"type": "workspace", "id": f"workspace:{ws}", "label": it.get("workspace_name") or ws},
            "definition": {"workspace_id": ws, "workspace_name": it.get("workspace_name") or "", "item_id": iid,
                           "item_type": it["type"], "job_type": SUPPORTED_FABRIC_TYPES[it["type"]],
                           "url": fabric_url(ws, iid, it["type"])},
            # Schedules for the Preview types wait for a live check of the job type Fabric's schedule API wants.
            "capabilities": ["discoverable", "observable", "runnable", "cancelable"]
            + ([] if it["type"] in PREVIEW_FABRIC_TYPES else ["schedulable"]),
            "owners": owners({}, assigned), "criticality": crit, "tags": tags,
            "deployments": [{
                "env": None, "env_label": "",
                "location": {"kind": "fabric", "label": f"Fabric · {it.get('workspace_name') or 'workspace'}",
                             "workspace_id": ws, "workspace_name": it.get("workspace_name") or "", "item_id": iid,
                             "url": fabric_url(ws, iid, it["type"])},
                "identity": {"kind": "you_or_schedule_owner",
                             "label": "You, for runs started here; the schedule's owner for Fabric schedules"},
                "tables_in": "", "version": None,
                "schedules": [{"where": "Fabric", "text": s.get("text", ""), "enabled": s.get("enabled", False),
                               "id": s.get("id")} for s in sched],
                "latest_run": runs[0] if runs else None, "freshness": None,
                "observation": items_obs if observed else observation(
                    "Fabric job history", "unknown", detail="Open the workload to read its run history."),
                "actions": ["run_item"] + (["cancel"] if any(r["state"] == "running" for r in runs) else []),
            }],
            "runs": runs, "errors": [],
            "dependencies": {"upstream": [], "downstream": [], "products": []},
            "observation": items_obs,
            "search": " ".join([it.get("displayName") or "", it.get("workspace_name") or "", it.get("type") or "",
                                it.get("description") or ""]),
        })
    return out
