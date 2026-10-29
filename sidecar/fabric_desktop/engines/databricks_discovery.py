"""What an Azure Databricks workspace offers you (Preview; tested against fakes, not a live workspace).

Read-only: every request is a GET on a documented REST API, so discovery never starts a warehouse or
a cluster and bills no Databricks units (DBU). It needs no ``RemoteAuthorization`` for that reason.

    SCIM API              GET /api/2.0/preview/scim/v2/Me              who you're signed in as
    SQL Warehouses API    GET /api/2.0/sql/warehouses                  warehouses, state, auto-stop
    Clusters API          GET /api/2.1/clusters/list                   all-purpose clusters (job clusters left out)
    Unity Catalog API     GET /api/2.1/unity-catalog/metastore_summary, catalogs

Each part is read on its own: a part you can't read (a 403, say) says why, and the rest still come
back. A failed sign-in stops at the first part, so the CLI isn't asked for a token five times.

For each cluster it says whether Databricks Connect can use it (the runtime line this version
supports, and a Unity Catalog access mode), and for each warehouse whether it has an auto-stop.
"""

from __future__ import annotations

from typing import Any

from .contract import Unsupported
from .databricks_auth import DatabricksAuthError
from .databricks_sql import Client, DatabricksError

SCHEMA = "databricks-discovery@1"
UC = "/api/2.1/unity-catalog"
# Unity Catalog access modes Databricks Connect works with: Dedicated (single user) and Standard
# (shared), under their older and newer API names; AUTO lets Databricks pick one of them.
CONNECT_MODES = {"SINGLE_USER": "Dedicated", "USER_ISOLATION": "Standard",
                 "DATA_SECURITY_MODE_DEDICATED": "Dedicated", "DATA_SECURITY_MODE_STANDARD": "Standard",
                 "DATA_SECURITY_MODE_AUTO": "Automatic"}
PARTS = ("identity", "warehouses", "clusters", "metastore", "catalogs")


def warehouse_row(w: dict[str, Any]) -> dict[str, Any]:
    auto_stop = int(w.get("auto_stop_mins") or 0)
    notes = [] if auto_stop else ["No auto-stop: once started, it bills DBUs until someone stops it."]
    return {"id": str(w.get("id") or ""), "name": str(w.get("name") or ""), "state": str(w.get("state") or ""),
            "size": str(w.get("cluster_size") or ""), "type": str(w.get("warehouse_type") or ""),
            "serverless": bool(w.get("enable_serverless_compute")), "auto_stop_mins": auto_stop, "notes": notes}


def connect_problems(spark_version: str, access_mode: str) -> list[str]:
    """Why Databricks Connect can't use a cluster, as things a person can act on; empty when it can.
    The runtime check is dbconnect.check's, as if the matching client were installed: installing it is
    a later step, not something about the cluster."""
    from . import dbconnect

    problems = []
    try:
        line = "%d.%d" % dbconnect.runtime_line(spark_version)
        problems += dbconnect.check(spark_version, f"{line}.0")
    except Unsupported as exc:
        problems.append(str(exc))
    if access_mode not in CONNECT_MODES:
        problems.append(f"Its access mode ({access_mode or 'not set'}) has no Unity Catalog: Databricks Connect "
                        "needs Dedicated or Standard access mode.")
    return problems


def cluster_row(c: dict[str, Any]) -> dict[str, Any]:
    version = str(c.get("spark_version") or "")
    mode = str(c.get("data_security_mode") or "")
    auto_stop = int(c.get("autotermination_minutes") or 0)
    problems = connect_problems(version, mode)
    return {"id": str(c.get("cluster_id") or ""), "name": str(c.get("cluster_name") or ""),
            "state": str(c.get("state") or ""), "spark_version": version,
            "access_mode": CONNECT_MODES.get(mode, mode), "auto_stop_mins": auto_stop,
            "connect": {"usable": not problems, "problems": problems},
            "notes": [] if auto_stop else ["No auto-termination: once started, it bills DBUs until someone stops it."]}


class Discovery:
    """``client`` is databricks_sql.Client (tests pass one over a fake HTTP session)."""

    def __init__(self, client: Client, catalogs: bool = True):
        self.client = client
        self.catalogs = catalogs

    def _identity(self) -> dict[str, Any]:
        me = self.client.get("/api/2.0/preview/scim/v2/Me")
        return {"user": str(me.get("userName") or ""), "name": str(me.get("displayName") or "")}

    def _warehouses(self) -> list[dict[str, Any]]:
        return [warehouse_row(w) for w in self.client.list("/api/2.0/sql/warehouses", "warehouses", {})]

    def _clusters(self) -> list[dict[str, Any]]:
        found = self.client.list("/api/2.1/clusters/list", "clusters", {})
        return [cluster_row(c) for c in found if str(c.get("cluster_source") or "") != "JOB"]

    def _metastore(self) -> dict[str, Any]:
        m = self.client.get(f"{UC}/metastore_summary")
        return {"id": str(m.get("metastore_id") or ""), "name": str(m.get("name") or ""),
                "region": str(m.get("region") or ""), "default_catalog": str(m.get("default_catalog_name") or "")}

    def _catalogs(self) -> list[dict[str, Any]]:
        return [{"name": str(c.get("name") or ""), "type": str(c.get("catalog_type") or ""),
                 "comment": str(c.get("comment") or "")} for c in self.client.list(f"{UC}/catalogs", "catalogs", {})]

    def run(self) -> dict[str, Any]:
        readers = {"identity": self._identity, "warehouses": self._warehouses, "clusters": self._clusters,
                   "metastore": self._metastore, "catalogs": self._catalogs}
        parts: dict[str, Any] = {}
        signed_out = ""
        for name in PARTS:
            if name == "catalogs" and not self.catalogs:
                parts[name] = {"ok": False, "skipped": True, "error": "Not asked for."}
            elif signed_out:
                parts[name] = {"ok": False, "skipped": True, "error": signed_out}
            else:
                try:
                    parts[name] = {"ok": True, "value": readers[name]()}
                except DatabricksAuthError as exc:
                    signed_out = str(exc)
                    parts[name] = {"ok": False, "error": signed_out}
                except DatabricksError as exc:
                    parts[name] = {"ok": False, "error": str(exc)}
        return {"schema": SCHEMA, "host": self.client.host, "signed_in": not signed_out, "parts": parts,
                "suggested": suggested(parts)}


def suggested(parts: dict[str, Any]) -> dict[str, str]:
    """The warehouse and cluster to try first: running ones before stopped, and only ones that stop
    on their own; a cluster only if Databricks Connect can use it. Empty when nothing fits."""
    out = {"warehouse_id": "", "cluster_id": ""}
    rank = {"RUNNING": 0, "STARTING": 1}
    whs = [w for w in (parts.get("warehouses") or {}).get("value") or [] if w["auto_stop_mins"]]
    if whs:
        out["warehouse_id"] = min(whs, key=lambda w: rank.get(w["state"], 2))["id"]
    cls = [c for c in (parts.get("clusters") or {}).get("value") or [] if c["connect"]["usable"] and c["auto_stop_mins"]]
    if cls:
        out["cluster_id"] = min(cls, key=lambda c: rank.get(c["state"], 2))["id"]
    return out


def summary_lines(result: dict[str, Any]) -> list[str]:
    """The result as plain lines for the command line."""
    parts = result["parts"]
    lines = [f"Workspace: {result['host']}"]
    labels = {"identity": "Signed in", "warehouses": "SQL warehouses", "clusters": "Clusters",
              "metastore": "Unity Catalog metastore", "catalogs": "Catalogs"}
    for name in PARTS:
        part = parts[name]
        if not part["ok"]:
            how = "skipped" if part.get("skipped") else "couldn't read"
            lines.append(f"{labels[name]}: {how}: {part['error']}")
            continue
        value = part["value"]
        if name == "identity":
            lines.append(f"Signed in as {value['user'] or value['name'] or 'an unnamed principal'}")
        elif name == "metastore":
            lines.append(f"Unity Catalog metastore: {value['name']} ({value['region']})")
        elif name == "catalogs":
            lines.append(f"Catalogs ({len(value)}): " + ", ".join(c["name"] for c in value[:20]) +
                         (" …" if len(value) > 20 else ""))
        elif name == "warehouses":
            lines.append(f"SQL warehouses ({len(value)}):")
            for w in value:
                stop = f"auto-stop {w['auto_stop_mins']} min" if w["auto_stop_mins"] else "no auto-stop"
                lines.append(f"  {w['name']} [{w['id']}] {w['state']}, {w['size'] or w['type']}, {stop}")
        else:
            lines.append(f"Clusters ({len(value)}):")
            for c in value:
                why = "Databricks Connect can use it" if c["connect"]["usable"] else \
                    "Databricks Connect can't: " + " ".join(c["connect"]["problems"])
                lines.append(f"  {c['name']} [{c['id']}] {c['state']}, {c['spark_version']}, {why}")
    pick = result["suggested"]
    if pick["warehouse_id"] or pick["cluster_id"]:
        lines.append("Try first: " + ", ".join(f"{k.replace('_id', '')} {v}" for k, v in pick.items() if v))
    lines.append("Nothing was started: discovery only reads.")
    return lines
