"""Connections for the Data Factory page (DF-5, engine half): read-only.

``fabric_connections`` lists the cloud and gateway connections the signed-in person can use and works
out what uses each one from the pipeline, copy job and dataflow definitions in each environment's
workspace (``externalReferences.connection`` anywhere in a pipeline or copy job, ``DatasourceId`` in a
dataflow's queryMetadata.json). A reference to a connection the person
can't use (deleted, or not shared with them) is listed under ``missing``: that's what breaks a run or
a deploy. The scan runs in the background, like Fabric schedules: ``{"pending": True}`` until it's
done. KratosForge never stores, creates or changes a connection or its credentials; nothing from a
credential is read (Fabric's list has none), and the connection's path passes through redact.secrets.
See docs/orchestration/data-factory.md.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .auth import CONNECTION_SCOPES, GATEWAY_SCOPES, SignInError
from .fabric_api import FabricClient
from .orchestration import mashup
from .redact import describe, one_line, secrets

_log = logging.getLogger(__name__)

ENV_ORDER = ("dev", "test", "prod")
# Items whose definitions name connections. A dataflow names its own in queryMetadata.json.
ITEM_TYPES = {"DataPipeline": "pipeline", "CopyJob": "copy job", "Dataflow": "dataflow"}
MAX_ITEMS = 500  # definitions read in one scan
# Sign-in answers that say "try again later", not "no": they aren't kept as a decline.
TRANSIENT_SIGN_IN = {"temporarily_unavailable", "server_error", "unknown_error"}


def declined(exc: Exception) -> bool:
    """Microsoft sign-in said no (declined, cancelled, or not allowed), rather than failing for now."""
    return isinstance(exc, SignInError) and not {exc.code, exc.error} & TRANSIENT_SIGN_IN


def connection_ids(value: Any) -> set[str]:
    """Every ``externalReferences.connection`` in a definition, nested activities included."""
    found: set[str] = set()
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            ref = v.get("externalReferences")
            if isinstance(ref, dict) and isinstance(ref.get("connection"), str) and ref["connection"].strip():
                found.add(ref["connection"].strip().lower())
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return found


def definition_parts(result: dict[str, Any]) -> list[Any]:
    """The JSON parts of a getDefinition result; parts that aren't JSON are left out."""
    out = []
    for part in ((result or {}).get("definition") or {}).get("parts") or []:
        try:
            out.append(json.loads(base64.b64decode(part.get("payload") or "").decode("utf-8")))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
    return out


def dataflow_connection_ids(result: dict[str, Any]) -> set[str]:
    """The connection ids a Dataflow Gen2 definition names (queryMetadata.json's ``DatasourceId``).
    A definition the reader can't fully read raises, so the scan counts it as missed, not as using none."""
    out = mashup.describe(result)
    if out["problems"]:
        raise ValueError(out["problems"][0])
    return {c["connection_id"].strip().lower() for c in out["connections"] if c["connection_id"].strip()}


def connection_row(c: dict[str, Any]) -> dict[str, Any]:
    details = c.get("connectionDetails") or {}
    cred = c.get("credentialDetails") or {}
    return {"id": str(c.get("id") or "").lower(), "name": c.get("displayName") or c.get("id") or "",
            "type": details.get("type") or "", "path": secrets(str(details.get("path") or "")),
            "connectivity": c.get("connectivityType") or "", "gateway_id": str(c.get("gatewayId") or "").lower(),
            "gateway_name": "", "gateway_members": None,
            "privacy": c.get("privacyLevel") or "", "credential_type": cred.get("credentialType") or "",
            "used_by": []}


class FabricConnectionsMixin:
    _fabric_connections: dict[str, Any] | None = None
    _fabric_connections_lock = threading.Lock()
    _gateway_declined: str = ""  # why Gateway.Read.All was declined; not asked again until the app restarts

    def fabric_connections(self, refresh: bool = False) -> dict[str, Any]:
        """Connections and what uses them, from a background scan ({"pending": True} meanwhile). Reads only."""
        with self._fabric_connections_lock:
            cached = self._fabric_connections
            if cached is not None and not refresh and not cached.get("error"):
                return dict(cached)  # an error isn't kept: the next call tries again (after sign-in, say)
            if cached and cached.get("pending"):
                return dict(cached)  # a scan is already going
            try:
                # Both tokens here: MSAL isn't thread-safe. Connection.Read.All is asked for once.
                token = self.auth.fabric_token()  # type: ignore[attr-defined]
                conn_token = self.auth._token(CONNECTION_SCOPES)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._fabric_connections = {"pending": False, "connections": [], "missing": [], "error": describe(exc)}
            else:
                # Gateway names are extra: without Gateway.Read.All (declined, or not allowed) the tab
                # still lists the connections, and says why the names are missing. A decline is kept,
                # so Refresh doesn't bring the sign-in prompt back.
                gw_token, gw_error = "", self._gateway_declined
                if not gw_error:
                    try:
                        gw_token = self.auth._token(GATEWAY_SCOPES)  # type: ignore[attr-defined]
                    except Exception as exc:  # noqa: BLE001
                        gw_error = describe(exc)
                        if declined(exc):  # a network blip asks again on the next Refresh
                            self._gateway_declined = gw_error
                self._fabric_connections = {"pending": True, "connections": [], "missing": []}
                threading.Thread(target=self._scan_fabric_connections, args=(token, conn_token, gw_token, gw_error),
                                 daemon=True).start()
            return dict(self._fabric_connections)

    def _connection_workspaces(self, token: str) -> list[dict[str, str]]:
        """Each environment's workspace (dev, test, prod first), else the workspaces Schedules lists."""
        listed = {w["id"]: w["displayName"] for w in FabricClient(lambda: token).list_workspaces()}
        try:
            envs = dict(self._project().environments)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.info("No metadata project; connections are checked in the Schedules workspaces: %s", describe(exc))
            envs = {}
        order = sorted(envs, key=lambda e: (ENV_ORDER.index(e) if e in ENV_ORDER else len(ENV_ORDER), e))
        out = [{"id": envs[e]["workspace_id"], "name": listed[envs[e]["workspace_id"]], "env": e}
               for e in order if (envs[e] or {}).get("workspace_id") in listed]
        if out:
            return out
        return [{**w, "env": ""} for w in self._schedule_workspaces(False, token)]  # type: ignore[attr-defined]

    def _scan_fabric_connections(self, token: str, conn_token: str, gw_token: str = "", gw_error: str = "") -> None:
        client = FabricClient(lambda: token)
        try:
            rows = {r["id"]: r for r in map(connection_row, FabricClient(lambda: conn_token).list_connections())}
            workspaces = self._connection_workspaces(token)
        except Exception as exc:  # noqa: BLE001
            self._set_connections({"pending": False, "connections": [], "missing": [], "error": describe(exc)})
            return
        if gw_token:
            try:
                gateways = {str(g.get("id") or "").lower(): g for g in FabricClient(lambda: gw_token).list_gateways()}
            except Exception as exc:  # noqa: BLE001
                gateways, gw_error = {}, describe(exc)
            for r in rows.values():
                g = gateways.get(r["gateway_id"])
                if g:
                    r["gateway_name"] = g.get("displayName") or ""
                    r["gateway_members"] = g.get("numberOfMemberGateways")
        missed: list[str] = []

        def items(ws: dict[str, str]) -> list[dict[str, Any]]:
            try:
                return [{"workspace_id": ws["id"], "workspace_name": ws["name"], "env": ws["env"], "item_id": it.get("id") or "",
                         "item_name": it.get("displayName") or "", "item_type": ITEM_TYPES[it["type"]]}
                        for it in client.list_items(ws["id"]) if it.get("type") in ITEM_TYPES]
            except Exception as exc:  # noqa: BLE001
                missed.append(f"items in workspace {one_line(ws['id'], 100)}: {describe(exc)}")
                return []

        def uses(it: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
            try:
                result = client.get_item_definition(it["workspace_id"], it["item_id"])
                # Parsed here too: a definition in a shape this version doesn't expect is one item
                # that couldn't be read, never the end of the scan.
                if it["item_type"] == "dataflow":
                    return it, dataflow_connection_ids(result)
                return it, connection_ids(definition_parts(result))
            except Exception as exc:  # noqa: BLE001
                missed.append(f"definition of {one_line(it['item_id'], 100)}: {describe(exc)}")
                return it, set()

        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [it for chunk in pool.map(items, workspaces) for it in chunk]
            used = list(pool.map(uses, found[:MAX_ITEMS]))
        if missed:
            _log.warning("Fabric connections: %d reads failed; the first was %s", len(missed), missed[0])
        missing: dict[str, dict[str, Any]] = {}
        for it, ids in used:
            for cid in sorted(ids):
                if cid not in rows and it["item_type"] == "dataflow":
                    # Not checked against a real export yet that a dataflow's DatasourceId is always
                    # the connection's id, so an unmatched one isn't called missing.
                    continue
                target = rows.get(cid) or missing.setdefault(cid, {"id": cid, "used_by": []})
                target["used_by"].append(it)
        connections = sorted(rows.values(), key=lambda r: str(r["name"]).lower())
        self._set_connections({"pending": False, "connections": connections, "missing": list(missing.values()),
                               "workspaces": workspaces, "items": len(found), "skipped": max(0, len(found) - MAX_ITEMS),
                               "missed": len(missed), "gateways_error": gw_error,
                               "scanned": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})

    def _set_connections(self, value: dict[str, Any]) -> None:
        with self._fabric_connections_lock:
            self._fabric_connections = value
