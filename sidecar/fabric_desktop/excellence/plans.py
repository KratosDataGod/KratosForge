"""Remediation plans for findings (FEA-401). Engine only: a plan is a proposal, never an action.

``plan_for(finding, result, graph=...)`` turns one scored finding of an assessment result into a plan:

- the control and its exact rule version;
- the affected resources, from the finding and the evidence it cites (and the objects the finding names);
- the blast radius: what depends on the resource, from a dependency graph the caller hands in. The
  assessment reads no lineage (its item read asks Fabric for none), and the app's own graphs are of
  cells in one notebook (``depgraph``) and columns in a metadata project (``meta.lineage``), not of
  items in a workspace. So without a graph, or when the resource isn't in it, the blast radius is
  *not known*, with the reason. It is never guessed;
- what the resource depends on, from the same graph and from what the finding names;
- the permissions the change needs: the workspace (or Azure) role from a static table per control,
  with the Microsoft Learn page it comes from. Whether you hold it is never checked or claimed;
- the proposed change, from the control's remediation template, with room for FEA-402's change
  templates (``templates`` on the finding, carried as given);
- how to undo it, how to validate it (always: assess the control again, and it must *pass*: not
  evaluated, not applicable or an error doesn't confirm a fix), and risk notes.

A plan never says a change is safe and never invents a saving: the expected benefit is the
finding's own qualified text. Advisory findings, and findings whose outcome in ``result`` isn't a
fail (passed, not evaluated, not applicable, error), get no plan: ``None``.

Nothing here reads Fabric, files or the network, and nothing runs a change. Plans are deterministic:
the same finding, result and graph always give the same plan, byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from typing import Any

from ..redact import one_line, secrets
from . import controls as ct
from .evidence import canonical

SCHEMA = "kratosforge/excellence-plan@1"
MAX_TEXT = 256  # characters of any name or text taken from a finding, the evidence or a graph
MAX_LISTED = 50  # downstream or upstream resources listed; the counts are always whole
MAX_NAMED = 20  # objects listed from a finding's detail
MAX_SENTENCE = 1000  # what was discovered and the expected benefit: whole sentences, so a qualifier isn't cut
NOT_SPECIFIC = ("No specific steps yet: KratosForge has no remediation plan for this control. Use the control's "
                "guidance and references, and plan the change with the people who own the resource.")
DISCLAIMER = ("A proposal only. Nothing here has been run or changed, and it doesn't say the change is safe: "
              "check it with the resource's owner before you start.")
_HELD = "Not checked. This plan doesn't look at what access you have; Fabric refuses the change without it."

# ---- Microsoft Learn pages for the roles (to be re-checked under FEA-106 before plans leave Preview)

_ROLES = {"title": "Roles in workspaces in Microsoft Fabric",
          "url": "https://learn.microsoft.com/fabric/fundamentals/roles-workspaces"}
_ACCESS = {"title": "Give users access to workspaces",
           "url": "https://learn.microsoft.com/fabric/fundamentals/give-access-workspaces"}
_PAUSE = {"title": "Pause and resume your capacity", "url": "https://learn.microsoft.com/fabric/enterprise/pause-resume"}
_SCALE = {"title": "Scale your capacity", "url": "https://learn.microsoft.com/fabric/enterprise/scale-capacity"}
_KEYVAULT = {"title": "Azure Key Vault best practices",
             "url": "https://learn.microsoft.com/azure/key-vault/general/best-practices"}
_RLS = {"title": "Row-level security with Power BI",
        "url": "https://learn.microsoft.com/power-bi/enterprise/service-admin-rls"}
_GIT = {"title": "Notebook source control and deployment",
        "url": "https://learn.microsoft.com/fabric/data-engineering/notebook-source-control-deployment"}
_GIT_START = {"title": "Get started with Git integration",
              "url": "https://learn.microsoft.com/fabric/cicd/git-integration/git-get-started"}
_GIT_PROCESS = {"title": "Basic concepts in Git integration",
                "url": "https://learn.microsoft.com/fabric/cicd/git-integration/git-integration-process"}
_MAINT = {"title": "Use table maintenance feature to manage delta tables in Fabric",
          "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-table-maintenance"}
_INTEROP = {"title": "Delta Lake table format interoperability",
            "url": "https://learn.microsoft.com/fabric/fundamentals/delta-lake-interoperability"}
_ENDPOINT = {"title": "What is the SQL analytics endpoint for a lakehouse?",
             "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-sql-analytics-endpoint"}
_COLLATION = {"title": "Data Warehouse collation", "url": "https://learn.microsoft.com/fabric/data-warehouse/collation"}


def _p(role: str, where: str, *, also: tuple[str, ...] = (), refs: tuple[dict[str, str], ...] = (_ROLES,),
       notes: tuple[str, ...] = (), rollback: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"role": role, "where": where, "also": also, "refs": refs, "notes": notes, "rollback": rollback}


_CONTENT = "Contributor"  # create, edit and delete items in a workspace: the lowest role that can
_ACCESS_NOTE = ("Removing or lowering a role can stop someone's work at once, including scheduled work that runs "
                "as them.")
_SCHEDULE_NOTE = "Items that run on a schedule as the item's owner keep that owner after the change."
_RUN_AGAIN = "The change takes effect on the pipeline's next run, scheduled or triggered."
_REPUBLISH = "Readers of the model's reports see the change after the model is saved and refreshed."

# What each control's change needs, beyond its template. A control missing here gets a generic plan
# that says it has no specific steps yet.
SPECIFIC: dict[str, dict[str, Any]] = {
    "FEA-SEC-001": _p("Admin", "workspace", refs=(_ROLES, _ACCESS),
                      notes=(_ACCESS_NOTE, ("Keep at least two Admins, so the workspace isn't left with nobody "
                                           "who can manage it.")),
                      rollback=("Write down each Admin assignment (principal and role) before you change it.",)),
    "FEA-SEC-002": _p("Admin", "workspace", refs=(_ROLES, _ACCESS),
                      notes=(_ACCESS_NOTE, ("A Member can change Contributor and Member assignments; changing an "
                                           "Admin's role needs Admin, so Admin is listed.")),
                      rollback=("Write down each guest's role before you change it.",)),
    "FEA-SEC-003": _p("Admin", "workspace", refs=(_ROLES, _ACCESS),
                      notes=(_ACCESS_NOTE, ("An app's scheduled or triggered jobs can fail if the new role is too "
                                           "low for them.")),
                      rollback=("Write down the app's role before you change it.",)),
    "FEA-SEC-004": _p("Admin", "workspace", refs=(_ROLES, _ACCESS),
                      also=("A group you can manage in Microsoft Entra ID (or its owner's help).",),
                      notes=(_ACCESS_NOTE, ("Add the group before removing the individual assignments, so nobody "
                                           "is without access in between.")),
                      rollback=("Write down each individual assignment before you remove it.",)),
    "FEA-GOV-001": _p("Admin", "workspace", notes=("Changing the description changes no data and no access.",)),
    "FEA-CAP-001": _p("Azure Contributor (or Owner) on the capacity resource", "azure", refs=(_PAUSE,),
                      also=(("To move the workspace instead: workspace Admin, and permission to assign workspaces "
                            "to the other capacity."),),
                      notes=("A running capacity is billed while it runs.", ("Resuming affects every workspace on "
                             "the capacity, not just this one."))),
    "FEA-CAP-002": _p(_CONTENT, "workspace", notes=("Pausing a schedule stops its output from updating.",
                                                    _SCHEDULE_NOTE)),
    "FEA-CAP-003": _p(_CONTENT, "workspace", notes=(("A less frequent refresh makes its data older between "
                                                    "refreshes; check that with the people who read it."),)),
    "FEA-CAP-004": _p("Azure Contributor (or Owner) on the capacity resource", "azure", refs=(_SCALE, _PAUSE),
                      notes=("Scaling down or pausing affects every workspace on the capacity, not just this one.",
                             ("A smaller capacity can throttle work that fits today; capacity freed isn't money "
                             "saved until the capacity is resized, paused or retired."))),
    "FEA-PERF-001": _p(_CONTENT, "workspace", notes=(("Readers of the copy's tables read the kept notebook's tables "
                                                     "after the change; their names or schemas may differ."),
                                                     _SCHEDULE_NOTE)),
    "FEA-PERF-002": _p(_CONTENT, "workspace", notes=(("Consumers moved to the kept landing table see its refresh "
                                                     "timing, which may differ."),)),
    "FEA-DE-001": _p(_CONTENT, "workspace", refs=(_ROLES, _GIT),
                     notes=("An incremental load can miss deletes and late rows the full load caught; test them.",)),
    "FEA-DE-002": _p(_CONTENT, "workspace", refs=(_ROLES, _GIT),
                     notes=(("Each copy's schedule and pipeline activity must call the kept notebook with its own "
                            "parameter value."),)),
    "FEA-DE-003": _p(_CONTENT, "workspace", notes=(("Keeping 7 days of history keeps more files, so storage grows "
                                                   "until the next VACUUM after the change."),)),
    "FEA-DEVOPS-001": _p(_CONTENT, "workspace", refs=(_ROLES, _GIT),
                         notes=(("A relative path reads the default lakehouse, so check which lakehouse that is in "
                                "every stage."),)),
    "FEA-DEVOPS-002": _p(_CONTENT, "workspace", notes=(("Each stage needs its parameter or variable value set, or the "
                                                       "activity fails."),)),
    "FEA-DEVOPS-003": _p("Admin", "workspace", refs=(_ROLES, _GIT_START, _GIT_PROCESS),
                         also=("Permission to read and write the branch in the Azure DevOps or GitHub repository.",),
                         notes=(("The first sync copies one way: committing writes the workspace's items to the "
                                "branch, and updating from the branch overwrites the workspace's items. Choose the "
                                "direction on purpose."),)),
    "FEA-DEVOPS-004": _p(_CONTENT, "workspace", refs=(_ROLES, _GIT_PROCESS),
                         also=("Permission to write to the connected branch in the Git repository.",),
                         notes=(("A commit puts the change on the branch, so whatever deploys or syncs from that "
                                "branch picks it up."), "Undoing a change in the workspace discards it.")),
    "FEA-DEVOPS-005": _p(_CONTENT, "workspace", refs=(_ROLES, _GIT_PROCESS),
                         also=("Permission to read the connected branch in the Git repository.",),
                         notes=(("Updating overwrites the workspace's version of each changed item, and removes "
                                "items the branch no longer has."),
                                ("An item changed in the workspace too is a conflict: resolve it before updating, "
                                 "or the workspace's change can be lost."))),
    "FEA-REL-004": _p(_CONTENT, "workspace", notes=(_RUN_AGAIN, ("A retried step runs its work again, including "
                                                   "any writes it made before it failed."))),
    "FEA-REL-005": _p(_CONTENT, "workspace", notes=(_RUN_AGAIN, ("A timeout shorter than a normal run fails runs "
                                                   "that would have finished."))),
    "FEA-REL-006": _p(_CONTENT, "workspace", notes=(_RUN_AGAIN, ("The failure path runs on every failure, so its "
                                                   "alerts reach the people it names each time."))),
    "FEA-REL-001": _p(_CONTENT, "workspace", notes=(("Runs that succeed today while ignoring an error will fail after "
                                                    "the change, and alerts on them start."),)),
    "FEA-REL-002": _p(_CONTENT, "workspace", notes=(("Pinning stops picking up fixes in newer releases until you "
                                                    "change the pin."),)),
    "FEA-REL-003": _p(_CONTENT, "workspace", notes=(("An activated activity runs its work again on the next run, "
                                                    "including any writes it makes."),)),
    "FEA-SEC-005": _p(_CONTENT, "workspace", refs=(_ROLES, _KEYVAULT),
                      also=("Permission to set secrets in the Azure Key Vault.",
                            "Whoever can rotate the secret at its source system."),
                      notes=("Rotating the secret breaks every other place that still uses the old value.",),
                      rollback=("Rotation can't be undone: rolling back puts the code back, reading the new value.",)),
    "FEA-ARCH-002": _p(_CONTENT, "workspace", notes=(("A new schema-enabled lakehouse is a move: every reader of the "
                                                     "old lakehouse must be pointed at the new one."),)),
    "FEA-ARCH-003": _p(_CONTENT, "workspace", notes=("Deleting an item deletes its data and breaks whatever reads it.",),
                       rollback=("Make sure the item is in Git or another workspace before you delete it.",)),
    "FEA-ARCH-004": _p(_CONTENT, "workspace", also=(("At least Viewer on the lakehouse it should read, if that is in "
                                                    "another workspace."),),
                       notes=(("Tables the notebook reads by name come from the new default lakehouse after the "
                              "change."),)),
    "FEA-PERF-003": _p(_CONTENT, "workspace", refs=(_ROLES, _MAINT),
                       notes=(("Table maintenance uses capacity while it runs, so run it outside busy hours; it "
                              "rewrites files and changes no rows."),
                              "A job that keeps writing many small batches makes small files again.")),
    "FEA-PERF-004": _p(_CONTENT, "workspace", refs=(_ROLES, _MAINT),
                       notes=(("VACUUM can't be undone: files older than the retention are removed, and the table "
                              "can't be restored to a version that needed them."),),
                       rollback=("Agree the days of history each table must keep before you vacuum it.",)),
    "FEA-PERF-005": _p(_CONTENT, "workspace", refs=(_ROLES, _INTEROP),
                       notes=(("Readers move to the rewritten table: check its rows and columns match before "
                              "they do."),
                              "A job that turns the feature on again undoes the change on its next run.")),
    "FEA-WH-001": _p(_CONTENT, "workspace", refs=(_ROLES, _ENDPOINT),
                     notes=(("T-SQL readers of the lakehouse stay unavailable until provisioning finishes; its Delta "
                            "tables and Spark are unaffected."),)),
    "FEA-WH-002": _p(_CONTENT, "workspace", refs=(_ROLES, _COLLATION),
                     notes=(("Moving to another collation changes how text compares: a filter or join that relied on "
                            "case can return different rows."),
                            "Every reader of the old warehouse must be pointed at the new one.")),
    "FEA-SM-001": _p(_CONTENT, "workspace", refs=(_ROLES, _RLS),
                     notes=(_REPUBLISH, "Visuals that relied on filtering both ways can show different numbers.")),
    "FEA-SM-003": _p(_CONTENT, "workspace", notes=(_REPUBLISH, ("With Direct Lake only, a query that would have "
                                                   "fallen back fails instead of running slowly."))),
    "FEA-SM-004": _p(_CONTENT, "workspace", notes=(_REPUBLISH, ("Changing a column's type can change its values "
                                                   "(dates, decimals); check the measures that use it."))),
    "FEA-SM-005": _p(_CONTENT, "workspace", notes=(_REPUBLISH, ("A new key column has to be added to both tables, "
                                                   "usually upstream in the lakehouse."))),
    "FEA-SM-006": _p(_CONTENT, "workspace", notes=(_REPUBLISH, ("Reports that show the hidden column directly lose "
                                                   "that field."))),
}
# Without a specific plan: FEA-ARCH-001 (restructuring layers across lakehouses is a project, not a
# change) and FEA-SM-002 (whether a model needs row-level security is a decision about its readers).


# ---- cleaning text that came from Fabric, a file or a graph


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    """One line, no control characters, nothing credential-shaped, at most ``limit`` characters."""
    return one_line("" if value is None else value, limit)


def _named(detail: Any) -> list[str]:
    """The objects a finding's detail names (activities, relationships, places), as short text."""
    out: list[str] = []

    def add(v: Any) -> None:
        if len(out) >= MAX_NAMED:
            return
        if isinstance(v, dict):
            parts = [f"{_text(k)} {_text(x)}" for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))
                     if isinstance(x, (str, int, float)) and not isinstance(x, bool)]
            if parts:
                out.append(_text(", ".join(parts)))
        elif isinstance(v, str) and v.strip():
            out.append(_text(v))

    if isinstance(detail, dict):
        for _, v in sorted(detail.items(), key=lambda kv: str(kv[0])):
            for x in (v if isinstance(v, list) else [v] if isinstance(v, (str, dict)) else []):
                add(x)
    return out


def _resource(r: Any) -> dict[str, Any]:
    r = r if isinstance(r, dict) else {}
    out = {"type": _text(r.get("type")), "id": _text(r.get("id")), "name": _text(r.get("name") or r.get("id"))}
    for k in ("workspace_id", "item_type"):
        if r.get(k):
            out[k] = _text(r[k])
    return out


# ---- the graph


def _graph(graph: Any) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], dict[str, set[str]], int]:
    """Nodes by id, the edges both ways, and how many malformed parts were skipped. ``edges`` are
    {"from", "to"}: ``to`` depends on ``from`` (data or a reference flows from ``from`` to ``to``). An
    edge's ends that aren't listed as nodes are nodes with only an id. Anything malformed is skipped,
    never guessed, and counted, so a graph with skipped parts is never reported complete."""
    nodes: dict[str, dict[str, Any]] = {}
    down: dict[str, set[str]] = {}
    up: dict[str, set[str]] = {}
    skipped = 0
    raw_nodes, raw_edges = graph.get("nodes"), graph.get("edges")
    if raw_nodes is not None and not isinstance(raw_nodes, list):
        raw_nodes, skipped = [], skipped + 1
    if raw_edges is not None and not isinstance(raw_edges, list):
        raw_edges, skipped = [], skipped + 1
    for n in raw_nodes or []:
        if isinstance(n, dict) and isinstance(n.get("id"), str) and n["id"]:
            nodes.setdefault(n["id"], n)
        else:
            skipped += 1
    for e in raw_edges or []:
        if not isinstance(e, dict):
            skipped += 1
            continue
        a, b = e.get("from"), e.get("to")
        if not (isinstance(a, str) and isinstance(b, str) and a and b):
            skipped += 1
            continue
        for x in (a, b):
            nodes.setdefault(x, {"id": x})
        down.setdefault(a, set()).add(b)
        up.setdefault(b, set()).add(a)
    return nodes, down, up, skipped


def _node(n: dict[str, Any], depth: int) -> dict[str, Any]:
    out = {"id": _text(n.get("id")), "name": _text(n.get("name") or n.get("id")), "depth": depth}
    for k in ("type", "workspace_id"):
        if n.get(k):
            out[k] = _text(n[k])
    return out


def _walk(start: str, nodes: dict[str, dict[str, Any]], adj: dict[str, set[str]]) -> tuple[list[dict[str, Any]], int, bool]:
    """Everything reachable from ``start`` by ``adj``, nearest first, then by name and id; each node
    once, so a cycle ends the walk instead of looping. The list is cut to MAX_LISTED; the count isn't.
    The flag says whether a path leads back to ``start`` (a cycle through the resource)."""
    seen = {start}
    order: list[tuple[int, str, str]] = []
    cycle = False
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        cur, depth = queue.popleft()
        nxt = sorted(adj.get(cur, ()), key=lambda i: (str(nodes[i].get("name") or i), i))
        for i in nxt:
            if i == start:
                cycle = True
            if i in seen:
                continue
            seen.add(i)
            order.append((depth + 1, str(nodes[i].get("name") or i), i))
            queue.append((i, depth + 1))
    order.sort()
    return [_node(nodes[i], d) for d, _, i in order[:MAX_LISTED]], len(order), cycle


def _radius(subject: dict[str, Any], graph: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Blast radius (downstream) and dependencies (upstream) of ``subject`` in ``graph``."""
    def unknown(reason: str) -> dict[str, Any]:
        return {"known": False, "reason": reason, "listed": [], "count": None, "shown": 0, "truncated": False,
                "cycle": None, "complete": False, "source": None}

    if graph is None:
        why = ("Not known: no dependency graph was given. The assessment reads no lineage, so what depends on "
               "this resource wasn't looked at.")
        return unknown(why), unknown(why)
    if not isinstance(graph, dict):
        why = "Not known: the dependency graph given isn't one this plan can read."
        return unknown(why), unknown(why)
    nodes, down, up, skipped = _graph(graph)
    sid = subject.get("id")
    if not isinstance(sid, str) or sid not in nodes:
        why = "Not known: this resource isn't in the dependency graph given."
        return unknown(why), unknown(why)
    complete = graph.get("complete") is True and not skipped
    source = _text(graph.get("source")) or None
    reason = (None if complete else
              f"{skipped} malformed part{'s' if skipped != 1 else ''} of the graph given {'were' if skipped != 1 else 'was'} "
              "skipped, so there may be more than this." if skipped else
              "The graph given doesn't say it is complete, so there may be more than this.")

    def known(listed: list[dict[str, Any]], count: int, cycle: bool) -> dict[str, Any]:
        return {"known": True, "reason": reason,
                "listed": listed, "count": count, "shown": len(listed), "truncated": count > len(listed),
                "cycle": cycle, "complete": complete, "source": source}

    return known(*_walk(sid, nodes, down)), known(*_walk(sid, nodes, up))


# ---- the plan


def _outcome_for(finding: dict[str, Any], result: dict[str, Any]) -> str | None:
    """The outcome in ``result`` of the finding's rule on its resource, or None if there is none. More
    than one outcome for it is a fail only when every one is a fail (in any order), and an outcome of
    another rule version isn't this finding's."""
    rule = finding.get("rule")
    res = finding.get("resource")
    sid = res.get("id") if isinstance(res, dict) else None
    outcomes = result.get("outcomes")
    if not isinstance(rule, str) or not rule or not isinstance(sid, str) or not sid or not isinstance(outcomes, list):
        return None
    cid = rule.split("@")[0]
    seen = set()
    for o in outcomes:
        if not isinstance(o, dict) or o.get("control_id") != cid:
            continue
        subj = o.get("subject")
        if not isinstance(subj, dict) or subj.get("id") != sid:
            continue
        if o.get("rule") is not None and o.get("rule") != rule:
            continue
        seen.add(o.get("outcome") if isinstance(o.get("outcome"), str) else None)
    if not seen:
        return None
    return "fail" if seen == {"fail"} else "not a fail"


def _evidence(finding: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    raw, recs = finding.get("evidence_ids"), result.get("evidence")
    ids = {i for i in raw if isinstance(i, str)} if isinstance(raw, list) else set()
    out = []
    for r in recs if isinstance(recs, list) else []:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"] in ids:
            out.append({"id": _text(r["id"]), "kind": _text(r.get("kind")), "source": _text(r.get("source")),
                        "confidence": _text(r.get("confidence")), "resource": _resource(r.get("subject"))})
    return sorted(out, key=lambda x: x["id"])


def _clean(value: Any) -> Any:
    """A JSON copy of ``value`` with credential-shaped text removed, lone surrogates and control
    characters other than line breaks and tabs replaced, and NaN or infinity as None."""
    if isinstance(value, str):
        value = value.encode("utf-8", "replace").decode("utf-8")
        return secrets("".join(c if c.isprintable() or c in "\n\t" else " " for c in value))
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {_clean(str(k)): _clean(v) for k, v in value.items()}
    return value


def _templates(finding: dict[str, Any]) -> list[Any] | None:
    """FEA-402's change templates on the finding, carried as given (a JSON copy, with nothing
    credential-shaped and no control characters but line breaks and tabs), shown as proposals."""
    t = finding.get("templates")
    if not isinstance(t, list) or not t:
        return None
    return _clean(json.loads(canonical(t)))


def plan_for(finding: dict[str, Any], result: dict[str, Any], *, graph: dict[str, Any] | None = None,
             registry: ct.Registry | None = None) -> dict[str, Any] | None:
    """The remediation plan for ``finding`` of assessment ``result``, or None when it gets none
    (advisory, or not a fail in ``result``). ``graph`` is an optional dependency graph:
    ``{"nodes": [{"id", "name", "type", "workspace_id"}], "edges": [{"from", "to"}], "complete": bool,
    "source": str}``, an edge meaning ``to`` depends on ``from``."""
    if not isinstance(finding, dict) or finding.get("advisory") or not isinstance(result, dict):
        return None
    if _outcome_for(finding, result) != "fail":
        return None
    if registry is None:
        from . import catalog  # catalog imports nothing from here
        registry = catalog.registry()
    rule = _text(finding.get("rule"))
    cid, _, version = rule.partition("@")
    try:
        control: ct.Control | None = registry.get(cid)
    except KeyError:
        control = None
    spec = SPECIFIC.get(cid) if control is not None and control.version == version else None
    subject = _resource(finding.get("resource"))
    raw = finding.get("resource")
    raw_subject: dict[str, Any] = raw if isinstance(raw, dict) else {}
    downstream, upstream = _radius(raw_subject, graph)
    t = control.remediation if control is not None else {}
    title = control.title if control is not None else _text(finding.get("title"))
    risk_level = t.get("risk") if t.get("risk") in ct.LEVELS else None

    notes = [DISCLAIMER]
    if control is None:
        notes.append("This finding's rule isn't in the current catalog, so the plan has no specific steps.")
    elif control.version != version:
        notes.append(f"The rule has changed since this finding ({rule} then, {control.rule} now): assess again "
                     "before planning a change.")
    if spec:
        notes += list(spec["notes"])
    if not downstream["known"]:
        notes.append("What depends on this resource isn't known. Find out before you change it.")
    elif downstream["count"]:
        notes.append(f"{downstream['count']} resource{'s' if downstream['count'] != 1 else ''} depend on this one "
                     f"directly or indirectly{'' if downstream['complete'] else ' (at least)'}; each may be affected.")
    if downstream.get("cycle"):
        notes.append("The graph has a cycle through this resource: a change can come back to it.")

    if spec:
        permissions = {"known": True, "role": spec["role"], "where": spec["where"], "also": list(spec["also"]),
                       "references": [dict(r) for r in spec["refs"]], "held": _HELD}
        steps = list(t.get("steps") or [])
        change = {"specific": True, "summary": t.get("solution", ""), "steps": steps, "note": None}
        rollback_steps = list(spec["rollback"]) + [t["rollback"]]
    else:
        permissions = {"known": False, "role": None, "where": None, "also": [], "references": [dict(_ROLES)],
                       "held": _HELD, "reason": "Not known: no specific plan for this control yet."}
        change = {"specific": False, "summary": t.get("solution") or _text(finding.get("solution")), "steps": [],
                  "note": NOT_SPECIFIC}
        rollback_steps = [t["rollback"]] if t.get("rollback") else []
    rollback_steps.append("Before you start, keep what you change (the old setting, definition or assignment) so "
                          "it can be put back exactly.")
    templates = _templates(finding)
    if templates is not None:
        change["templates"] = templates

    target = control.rule if control is not None else rule
    validation = [s for s in [t.get("validation")] if s]
    if downstream["known"] and downstream["count"]:
        validation.append("Check that the resources that depend on this one still run and give the same results.")
    validation.append(f"Assess again and check that {target} passes on {subject['name']}. Only a pass confirms "
                      "the fix: not evaluated, not applicable or an error doesn't.")

    plan = {
        "schema": SCHEMA, "engine_only": True, "executes": False, "fixture": bool(result.get("fixture")),
        "finding_id": _text(finding.get("id")), "assessed_at": _text(result.get("assessed_at")),
        "control": {"id": _text(cid), "rule": rule, "current_rule": control.rule if control else None,
                    "title": _text(title), "dimension": control.dimension if control else None,
                    "severity": _text(finding.get("severity")), "requirement": control.requirement if control else None,
                    "references": [dict(r) for r in control.references] if control else []},
        "affected_resources": {"resource": subject, "evidence": _evidence(finding, result),
                               "named_in_finding": _named(finding.get("detail")),
                               "discovered": _text(finding.get("discovered"), MAX_SENTENCE)},
        "blast_radius": downstream,
        "dependencies": upstream,
        "permissions": permissions,
        "proposed_change": change,
        "expected_benefit": _text(finding.get("benefit"), MAX_SENTENCE) or None,
        "rollback": {"steps": rollback_steps},
        "validation": {"steps": validation, "control": target, "required_outcome": "pass"},
        "risk": {"level": risk_level, "notes": notes},
    }
    plan["plan_id"] = "fp-" + hashlib.sha256(canonical(plan).encode()).hexdigest()[:16]
    return plan
