"""Enterprise governance, as the desktop UI calls it: Platform > Governance.

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

Resolving runs on this PC from what the platform returns: no Fabric capacity is used.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ..meta import mesh
from .policy import SETTINGS

GOVERNANCE_FILE = "governance.toml"


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

    def governance_overview(self, query: str = "", category: str = "", status: str = "") -> dict[str, Any]:
        """Policies (searchable), the fixed lists a policy form offers, and who the caller is."""
        p = self._policies()
        everything = p.call("list_policies")
        return {"me": self.platform_whoami(),  # type: ignore[attr-defined]
                "options": p.call("policy_options"),
                "policies": p.call("list_policies", query=query, category=category, status=status),
                "counts": {s: len([x for x in everything if x["status"] == s]) for s in ("draft", "published", "deprecated", "retired")},
                "domains": p.call("list_domains"), "projects": [x["name"] for x in p.call("list_projects")]}

    def governance_policy(self, id: str) -> dict[str, Any]:
        return self._policies().call("get_policy", id=id)

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
