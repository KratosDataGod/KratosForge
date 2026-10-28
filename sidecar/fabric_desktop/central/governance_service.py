"""Enterprise governance, as the desktop UI calls it: Platform > Govern > Governance.

Policies live in the platform's catalog (Prod's platform, like domains and the glossary), so
every project in the organization reads the same ones. Who may change them is decided by the
platform itself (central/policy.py), never by this file or the UI.

A metadata project can add policies, or tighten the ones it inherits, in its own
``governance.toml``, reviewed through Git like the rest of the project:

    [[policy]]
    id = "owner-required"          # a published policy in the registry
    enforcement = "block"          # optional: advisory, warn, block, or off (only if not mandatory)
    severity = "high"              # optional
    version = 2                    # optional: a published version to use
    entity = "silver.customer"     # optional: only this entity; else the whole project
    [policy.params]
    max_length = 40

Resolving runs on this PC from what the platform returns: no Fabric job runs and no OneLake data is read.
Reading the policies from a platform in Fabric is one UDF_Platform call, which uses a little capacity.

Exceptions: an exception that ends within EXCEPTION_WARN_DAYS (14, as for a project's exceptions.toml)
is marked ``ending``; governance_exceptions_ending sends each one Teams notice (to whoever asked and the
project or domain it covers) and says which concern the person or the open project, for the bell and the
Data mesh banner. The Teams notice is claimed in the platform first (claim_exception_warning), so one PC
in the organization posts it; with an older UDF_Platform that can't claim, none is posted. A platform admin can end an exception early (governance_end_exception), with a reason;
the platform checks that, and keeps the row.
"""

from __future__ import annotations

import datetime as dt
import logging
import tomllib
from pathlib import Path
from typing import Any

from .. import governance_notices as notices
from ..meta import mesh
from ..meta.project import EXCEPTION_WARN_DAYS
from .core import PlatformError
from .policy import SETTINGS
from .requests_service import call_or_repair

GOVERNANCE_FILE = "governance.toml"
_log = logging.getLogger(__name__)


def local_assignments(root: Path, project: str) -> tuple[list[dict[str, Any]], list[str]]:
    """A project's governance.toml as assignments at project or entity scope, and its problems.
    A project without the file has none; a broken file is reported, never fatal."""
    path = Path(root) / GOVERNANCE_FILE
    if not path.exists():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return [], [f"{GOVERNANCE_FILE}: {exc}"]
    out, problems = [], []
    for i, entry in enumerate(data.get("policy", []) or [], start=1):
        if not isinstance(entry, dict) or not entry.get("id"):
            problems.append(f"{GOVERNANCE_FILE}: policy {i} needs an id")
            continue
        extra = sorted(set(entry) - {"id", "entity", "note", *SETTINGS})
        if extra:
            problems.append(f"{GOVERNANCE_FILE}: {entry['id']}: {', '.join(extra)} isn't a policy setting")
        entity = str(entry.get("entity") or "").strip()
        out.append({"policy": str(entry["id"]).strip().lower(), "scope": "entity" if entity else "project",
                    "target": f"{project}:{entity}" if entity else project, "origin": GOVERNANCE_FILE,
                    "settings": {k: entry[k] for k in SETTINGS if k in entry}})
    return out, problems


class GovernanceMixin:
    root: Path

    def _policies(self) -> Any:
        return self._catalog()  # type: ignore[attr-defined]

    def _exceptions(self, policy: str = "", include_ended: bool = False) -> tuple[list[dict[str, Any]], str]:
        """Exceptions that haven't ended (with ``include_ended``, those that have too), each with its days
        left and whether it's ``ending`` (within EXCEPTION_WARN_DAYS), and a problem line when the platform
        can't say (an older UDF_Platform has no list_policy_exceptions): the policies still show."""
        try:
            rows = call_or_repair(self._policies(), "list_policy_exceptions", "list policy exceptions",
                                  policy=policy, active=not include_ended)
        except LookupError as exc:
            return [], str(exc)
        today = dt.datetime.now(dt.timezone.utc).date()  # the date UDF_Platform runs on
        for row in rows:
            try:
                left = (dt.date.fromisoformat(str(row.get("until") or "")[:10]) - today).days
            except ValueError:
                left = None
            active = bool(row.get("active")) and not row.get("ended_at")
            row["active"] = active
            row["days_left"] = left if active else None
            row["ending"] = active and left is not None and left <= EXCEPTION_WARN_DAYS
        return rows, ""

    def governance_overview(self, query: str = "", category: str = "", status: str = "") -> dict[str, Any]:
        """Policies (searchable), the fixed lists a policy form offers, who the caller is, and the
        exceptions in force (granted on Data mesh > Requests)."""
        p = self._policies()
        everything = p.call("list_policies")
        exceptions, problem = self._exceptions()
        return {"me": self.platform_whoami(),  # type: ignore[attr-defined]
                "options": p.call("policy_options"),
                "policies": p.call("list_policies", query=query, category=category, status=status),
                "counts": {s: len([x for x in everything if x["status"] == s]) for s in ("draft", "published", "deprecated", "retired")},
                "domains": p.call("list_domains"), "projects": [x["name"] for x in p.call("list_projects")],
                "exceptions": exceptions, "problems": [problem] if problem else []}

    def governance_policy(self, id: str, include_ended: bool = False) -> dict[str, Any]:
        out = self._policies().call("get_policy", id=id)
        exceptions, problem = self._exceptions(out["id"], include_ended=bool(include_ended))
        return out | {"exceptions": exceptions} | ({"problems": [problem]} if problem else {})

    def governance_version(self, id: str, version: int) -> dict[str, Any]:
        return self._policies().call("policy_version", id=id, version=version)

    def governance_create(self, policy: dict[str, Any]) -> dict[str, Any]:
        return self._policies().call("create_policy", policy=policy)

    def governance_save_draft(self, id: str, policy: dict[str, Any], note: str = "") -> dict[str, Any]:
        return self._policies().call("save_policy_draft", id=id, policy=policy, note=note)

    def governance_discard_draft(self, id: str) -> dict[str, Any]:
        return self._policies().call("discard_policy_draft", id=id)

    def governance_publish(self, id: str, note: str = "") -> dict[str, Any]:
        return self._policies().call("publish_policy", id=id, note=note)

    def governance_set_status(self, id: str, status: str, note: str = "") -> dict[str, Any]:
        return self._policies().call("set_policy_status", id=id, status=status, note=note)

    def governance_assign(self, policy: str, scope: str, target: str = "", settings: dict[str, Any] | None = None,
                          note: str = "") -> dict[str, Any]:
        self._policies().call("save_assignment", policy=policy, scope=scope, target=target, settings=settings or {},
                              note=note)
        return self.governance_policy(policy)

    def governance_unassign(self, policy: str, scope: str, target: str = "") -> dict[str, Any]:
        self._policies().call("remove_assignment", policy=policy, scope=scope, target=target)
        return self.governance_policy(policy)

    def governance_applicability(self, id: str) -> dict[str, Any]:
        return self._policies().call("policy_applicability", id=id)

    def governance_resolve(self, project: str = "", entity: str = "", domain: str = "", subdomain: str = "",
                           environment: str = "") -> dict[str, Any]:
        """The policies in force, and why. With no project named, or the open project named, that
        includes the open project's governance.toml, its domain from project.toml, and the entity's layer."""
        local: list[dict[str, Any]] = []
        problems: list[str] = []
        context: dict[str, Any] = {"environment": environment} if environment else {}
        try:
            open_project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            open_project = None
        if open_project is not None and project in ("", mesh.project_name(open_project)):
            me = mesh.identity(open_project)
            project = me["name"]
            domain, subdomain = domain or me["domain"], subdomain or me["subdomain"]
            local, problems = local_assignments(open_project.root, project)
            if entity and entity in open_project.entities:
                context["layer"] = open_project.entities[entity].layer
        out = self._policies().call("resolve_policies", project=project, entity=entity, domain=domain,
                                    subdomain=subdomain, local=local, context=context)
        return out | {"problems": problems}

    def governance_end_exception(self, id: str, reason: str) -> dict[str, Any]:
        """End a granted exception now, before its date. The platform checks that the caller is a
        platform admin and the reason, keeps the row and records it in the audit and the policy's
        history (policy.end_policy_exception); this only passes the call on."""
        return call_or_repair(self._policies(), "end_policy_exception", "end policy exceptions early",
                              id=str(id or ""), reason=str(reason or ""))

    def governance_exceptions_ending(self) -> dict[str, Any]:
        """Exceptions that end within EXCEPTION_WARN_DAYS. Each is sent once as a Teams notice (per
        exception and end date) to whoever asked and the project or domain it covers. ``for_you`` marks
        the ones the signed-in person asked for or that cover the open project or its domain: the bell
        and the Data mesh banner show those."""
        rows, problem = self._exceptions()
        ending = [r for r in rows if r.get("ending")]
        me = (self._user() or "").strip().lower()  # type: ignore[attr-defined]
        try:
            here = mesh.identity(self._project())  # type: ignore[attr-defined]
        except LookupError:
            here = {"name": "", "domain": "", "subdomain": ""}
        domains = {d for d in (here.get("domain"), here.get("subdomain")) if d}
        for r in ending:
            r["for_you"] = bool((me and str(r.get("requested_by") or "").strip().lower() == me)
                                or (r.get("scope") == "project" and here.get("name") and r.get("target") == here["name"])
                                or (r.get("scope") == "domain" and r.get("target") in domains))
        problems = [problem] if problem else []
        mine = self._claim_warnings(ending, problems)
        if mine:
            sent = self._governance_notify(  # type: ignore[attr-defined]
                lambda: [e for e in (notices.exception_ending(r, r["days_left"]) for r in mine) if e])
            if sent:
                problems.append(sent)
        return {"ending": ending, "warn_days": EXCEPTION_WARN_DAYS, "problems": problems}

    def _claim_warnings(self, ending: list[dict[str, Any]], problems: list[str]) -> list[dict[str, Any]]:
        """The ending exceptions whose Teams notice this PC claimed in the platform, so each is posted by
        one PC in the organization, not one per PC. A platform that can't claim (an older UDF_Platform)
        gets no Teams notice at all rather than one from every PC; the bell and banner still show."""
        mine = []
        for r in ending:
            try:
                out = call_or_repair(self._policies(), "claim_exception_warning", "send policy exception warnings once",
                                     id=str(r.get("id") or ""), until=str(r.get("until") or ""))
            except LookupError as exc:
                problems.append(f"No Teams notice for exceptions about to end: {exc}")
                return []
            except PlatformError as exc:
                _log.warning("Couldn't claim the warning for exception %s: %s", r.get("id"), exc)
                continue
            if isinstance(out, dict) and out.get("claimed"):
                mine.append(r)
        return mine
