"""Entra ID groups per domain (Preview): the Access groups dialog on Data mesh > Domains.

Reads the platform's domains, its group naming and the groups people linked to roles, and writes scripts
(central/entra.py) an Entra admin runs or follows by hand. Linking and naming are saved in the platform's
catalog. The only directory call is a read: finding a group by name, or checking which suggested groups
already exist, with Group.Read.All as the signed-in person (asked the first time). Nothing here creates a
group or grants a role.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any

from .. import http_session, sql_exec
from ..auth import GRAPH_GROUP_SCOPES
from ..redact import describe, one_line
from ..retrying import send_with_retry
from . import core, entra
from .requests_service import call_or_repair

_log = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"
_GUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
NO_GRAPH = ("KratosForge can't read groups in your directory: Group.Read.All needs an Entra admin's consent "
            "for the Fabric CLI app in your organization. Paste the group's object ID instead (Microsoft Entra "
            "admin center > Groups > the group > Object ID).")


class EntraGroupsMixin:

    def _entra_naming(self) -> dict[str, Any]:
        try:
            # Checked here too: whatever the platform answers, only letters, digits, -, _ and . reach a script.
            return core.checked_entra_naming(self._catalog().call("entra_group_naming"))  # type: ignore[attr-defined]
        except (LookupError, core.PlatformError, TypeError, AttributeError) as exc:  # an older UDF_Platform, or a bad value
            _log.info("entra_group_naming: %s", describe(exc))
            return core.checked_entra_naming({})

    def _entra_links(self, domain: str) -> list[dict[str, Any]]:
        try:
            return self._catalog().call("list_entra_groups", domain=domain)  # type: ignore[attr-defined]
        except (LookupError, core.PlatformError) as exc:
            _log.info("list_entra_groups: %s", describe(exc))
            return []

    def _entra_plan(self, domain_id: str, envs: list[str] | None, subdomains: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        domains = self.platform_domains()  # type: ignore[attr-defined]
        domain = next((d for d in domains if d["id"] == domain_id), None)
        if domain is None:
            raise ValueError(f"{domain_id} isn't a domain")
        links = self._entra_links(domain_id)
        if subdomains:
            for d in domains:
                if d.get("parent_id") == domain_id:
                    links += self._entra_links(d["id"])
        return domain, entra.plan(domains, domain_id, links, self._entra_naming(), envs, subdomains)

    def _entra_workspaces(self, domain_ids: set[str]) -> dict[str, list[dict[str, str]]]:
        """Each environment's workspaces that hold the domain's data products, from the catalog's ports."""
        out: dict[str, dict[str, str]] = {}
        try:
            products = self._catalog().call("list_products")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the script then says to grant workspace roles by hand
            _log.info("list_products: %s", describe(exc))
            products = []
        for p in products:
            if p.get("domain_id") not in domain_ids:
                continue
            for port in p.get("ports") or []:
                if port.get("workspace_id") and port.get("env") in core.ENTRA_ENVS:
                    out.setdefault(port["env"], {})[port["workspace_id"]] = port.get("workspace_name") or port["workspace_id"]
        return {env: [{"id": i, "name": n} for i, n in sorted(ws.items(), key=lambda x: x[1])] for env, ws in out.items()}

    def platform_entra_groups(self, domain_id: str, envs: list[str] | None = None,
                              subdomains: bool = False) -> dict[str, Any]:
        """The groups a domain's roles need: suggested names, linked groups, who goes in and what each gets."""
        domain, rows = self._entra_plan(domain_id, envs, subdomains)
        me = self.platform_whoami()  # type: ignore[attr-defined]
        owners = core._names(domain.get("owners"))
        parent = next((d for d in self.platform_domains() if d["id"] == domain.get("parent_id")), None)  # type: ignore[attr-defined]
        if parent:
            owners |= core._names(parent.get("owners"))
        return {"domain": domain, "rows": rows, "naming": self._entra_naming(), "owners": entra.owners_of(domain),
                "can_link": bool(me.get("admin")) or (me.get("user") or "") in owners, "can_name": bool(me.get("admin"))}

    def platform_entra_script(self, domain_id: str, kind: str, envs: list[str] | None = None,
                              subdomains: bool = False) -> dict[str, Any]:
        """One script (entra.KINDS) as text. Reads only."""
        return self._entra_script(domain_id, kind, envs, subdomains, save=False)

    def platform_entra_save(self, domain_id: str, kind: str, envs: list[str] | None = None,
                            subdomains: bool = False) -> dict[str, Any]:
        """The script as a new file in Downloads (never replaces one); returns its path."""
        return self._entra_script(domain_id, kind, envs, subdomains, save=True)

    def _entra_script(self, domain_id: str, kind: str, envs: list[str] | None, subdomains: bool,
                      save: bool) -> dict[str, Any]:
        if kind not in entra.KINDS:
            raise ValueError(f"kind is one of {', '.join(entra.KINDS)}")
        domain, rows = self._entra_plan(domain_id, envs, subdomains)
        ext, make = entra.KINDS[kind]
        at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if kind == "csv":
            text = make(rows, entra.owners_of(domain))
        elif kind == "roles":
            text = make(rows, self._entra_workspaces({r["domain_id"] for r in rows}), self._user(), at)  # type: ignore[attr-defined]
        else:
            text = make(rows, entra.owners_of(domain), self._user(), at)  # type: ignore[attr-defined]
        out: dict[str, Any] = {"kind": kind, "text": text, "new": sum(1 for r in rows if not r["linked"]),
                               "steps": list(entra.MANUAL_STEPS) if kind == "csv" else []}
        if save:
            folder = Path.home() / "Downloads"
            folder.mkdir(parents=True, exist_ok=True)
            label = {"powershell": "groups", "az": "groups", "csv": "groups", "roles": "Fabric roles"}[kind]
            path = sql_exec._reserve(folder, f"{entra._part(domain.get('name') or domain_id)} Entra {label}", f".{ext}")
            # The byte order mark tells Excel a CSV is UTF-8, and Windows PowerShell 5.1 a script is.
            path.write_text(text, encoding="utf-8-sig" if ext in ("csv", "ps1") else "utf-8", newline="\n" if ext == "sh" else None)
            out["path"] = str(path)
        return out

    def platform_entra_link(self, domain_id: str, role: str, env: str, group_id: str, group_name: str = "",
                            envs: list[str] | None = None) -> dict[str, Any]:
        """Use an existing group for a role (in ``env``, or each of ``envs``). Saved in the platform's catalog."""
        gid = str(group_id or "").strip()
        if not _GUID.fullmatch(gid):
            raise ValueError("Paste the group's object ID, like 00000000-0000-0000-0000-000000000000")
        if not group_name:
            group_name = self._entra_group_name(gid)
        for e in envs or [env]:
            call_or_repair(self._catalog(), "link_entra_group", "link Entra ID groups",  # type: ignore[attr-defined]
                           domain=domain_id, role=role, env=e, group_id=gid, group_name=group_name)
        return {"linked": self._entra_links(domain_id)}

    def platform_entra_unlink(self, domain_id: str, role: str, env: str = "") -> dict[str, Any]:
        call_or_repair(self._catalog(), "unlink_entra_group", "link Entra ID groups",  # type: ignore[attr-defined]
                       domain=domain_id, role=role, env=env)
        return {"linked": self._entra_links(domain_id)}

    def platform_entra_naming_save(self, naming: dict[str, Any]) -> dict[str, Any]:
        return call_or_repair(self._catalog(), "save_entra_group_naming", "name Entra ID groups",  # type: ignore[attr-defined]
                              naming=naming)

    # ---- reading the directory (Group.Read.All, as the signed-in person)

    def _graph(self, path: str, params: dict[str, str] | None = None, eventual: bool = False) -> Any:
        try:
            token = self.auth._token(GRAPH_GROUP_SCOPES)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - no consent in this tenant
            raise PermissionError(NO_GRAPH) from exc
        headers = {"Authorization": f"Bearer {token}"} | ({"ConsistencyLevel": "eventual"} if eventual else {})
        http = http_session.session()
        r = send_with_retry(lambda: http.get(f"{GRAPH}{path}", params=params, headers=headers, timeout=30), "GET")
        if r.status_code in (401, 403):
            raise PermissionError(NO_GRAPH)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Microsoft Graph answered {r.status_code}: {one_line(r.text, 200)}")
        return r.json()

    def _entra_group_name(self, group_id: str) -> str:
        """A linked group's name from the directory, or "" when it can't be read (the ID is kept either way)."""
        try:
            g = self._graph(f"/groups/{group_id}", {"$select": "id,displayName"})
        except Exception as exc:  # noqa: BLE001 - linking by ID works without directory rights
            _log.info("Group %s not read: %s", group_id, describe(exc))
            return ""
        if g is None:
            raise LookupError(f"No group with the object ID {group_id} in your organization")
        return str(g.get("displayName") or "")

    @staticmethod
    def _entra_group(g: dict[str, Any]) -> dict[str, Any]:
        unified = "Unified" in (g.get("groupTypes") or [])
        kind = "Microsoft 365 group" if unified else "Security group" if g.get("securityEnabled") else "Distribution list"
        if g.get("securityEnabled") and g.get("mailEnabled") and not unified:
            kind = "Mail-enabled security group"
        return {"id": g.get("id"), "name": g.get("displayName") or "", "description": g.get("description") or "",
                "kind": kind, "usable": bool(g.get("securityEnabled") or unified)}

    def platform_entra_find(self, query: str) -> list[dict[str, Any]]:
        """Groups whose name has these words, for Link existing group. A distribution list is shown but can't
        hold a Fabric role. A pasted object ID finds that group."""
        text = str(query or "").strip()
        select = "id,displayName,description,securityEnabled,mailEnabled,groupTypes"
        if _GUID.fullmatch(text):
            g = self._graph(f"/groups/{text}", {"$select": select})
            return [self._entra_group(g)] if g else []
        if len(text) < 2:
            return []
        words = re.sub(r'["\\]', " ", text)
        found = self._graph("/groups", {"$search": f'"displayName:{words}"', "$select": select, "$top": "25",
                                        "$orderby": "displayName"}, eventual=True) or {}
        return [self._entra_group(g) for g in found.get("value") or []]

    def platform_entra_check(self, domain_id: str, envs: list[str] | None = None,
                             subdomains: bool = False) -> dict[str, Any]:
        """Which suggested groups already exist in the directory, and whether linked groups are still there."""
        _, rows = self._entra_plan(domain_id, envs, subdomains)
        names = sorted({r["suggested"] for r in rows if not r["linked"]})
        found: dict[str, str] = {}
        for i in range(0, len(names), 15):  # Graph takes up to 15 values in one "in" filter
            chunk = names[i:i + 15]
            quoted = ", ".join("'" + n.replace("'", "''") + "'" for n in chunk)
            out = self._graph("/groups", {"$filter": f"displayName in ({quoted})", "$select": "id,displayName"}) or {}
            for g in out.get("value") or []:
                found[str(g.get("displayName") or "").lower()] = g.get("id")
        status = {}
        for r in rows:
            key = f"{r['domain_id']}|{r['role']}|{r['env']}"
            if r["linked"]:
                there = self._graph(f"/groups/{r['linked']['group_id']}", {"$select": "id"}) is not None
                status[key] = {"exists": there, "id": r["linked"]["group_id"]}
            else:
                gid = found.get(r["suggested"].lower())
                status[key] = {"exists": bool(gid), "id": gid}
        return {"status": status}
