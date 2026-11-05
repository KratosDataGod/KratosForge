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


class OddReply(ValueError):
    """A reply that isn't the shape the documented API gives: that part says so, the rest still come back."""


_TEXT_MAX = 200   # characters of one name or state from the workspace
_ERROR_MAX = 600  # characters of one part's error in the summary (sign-in errors carry a hint)


def _text(value: Any, limit: int = _TEXT_MAX) -> str:
    """A name, id or state from the workspace as one line of plain text: control characters (a
    newline, a terminal escape) folded to spaces, so a name can't add lines to the summary or recolor
    a terminal, and cut to ``limit`` characters (_TEXT_MAX)."""
    line = " ".join("".join(c if c.isprintable() else " " for c in str(value or "")).split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def _minutes(value: Any) -> int:
    """An auto-stop in whole minutes; 0 (none) when missing. Anything but a whole number is an odd reply."""
    if value is None or value == "":
        return 0
    try:
        minutes = int(value)
    except (TypeError, ValueError, OverflowError):   # OverflowError: JSON's Infinity
        raise OddReply(f"an auto-stop of {_text(value)!r} isn't a number of minutes") from None
    return max(minutes, 0)


def _object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OddReply(f"{what} isn't an object")
    return value


def warehouse_row(w: dict[str, Any]) -> dict[str, Any]:
    w = _object(w, "a warehouse")
    auto_stop = _minutes(w.get("auto_stop_mins"))
    notes = [] if auto_stop else ["No auto-stop: once started, it bills DBUs until someone stops it."]
    return {"id": _text(w.get("id")), "name": _text(w.get("name")), "state": _text(w.get("state")),
            "size": _text(w.get("cluster_size")), "type": _text(w.get("warehouse_type")),
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
    c = _object(c, "a cluster")
    version = _text(c.get("spark_version"))
    mode = _text(c.get("data_security_mode"))
    auto_stop = _minutes(c.get("autotermination_minutes"))
    problems = connect_problems(version, mode)
    return {"id": _text(c.get("cluster_id")), "name": _text(c.get("cluster_name")),
            "state": _text(c.get("state")), "spark_version": version,
            "access_mode": CONNECT_MODES.get(mode, mode), "auto_stop_mins": auto_stop,
            "connect": {"usable": not problems, "problems": problems},
            "notes": [] if auto_stop else ["No auto-termination: once started, it bills DBUs until someone stops it."]}


class Discovery:
    """``client`` is databricks_sql.Client (tests pass one over a fake HTTP session)."""

    def __init__(self, client: Client, catalogs: bool = True):
        self.client = client
        self.catalogs = catalogs

    def _identity(self) -> dict[str, Any]:
        me = _object(self.client.get("/api/2.0/preview/scim/v2/Me"), "the signed-in user")
        return {"user": _text(me.get("userName")), "name": _text(me.get("displayName"))}

    def _warehouses(self) -> list[dict[str, Any]]:
        return [warehouse_row(w) for w in self.client.list("/api/2.0/sql/warehouses", "warehouses", {})]

    def _clusters(self) -> list[dict[str, Any]]:
        found = self.client.list("/api/2.1/clusters/list", "clusters", {})
        return [cluster_row(c) for c in found if str(_object(c, "a cluster").get("cluster_source") or "") != "JOB"]

    def _metastore(self) -> dict[str, Any]:
        m = _object(self.client.get(f"{UC}/metastore_summary"), "the metastore")
        return {"id": _text(m.get("metastore_id")), "name": _text(m.get("name")),
                "region": _text(m.get("region")), "default_catalog": _text(m.get("default_catalog_name"))}

    def _catalogs(self) -> list[dict[str, Any]]:
        return [{"name": _text(c.get("name")), "type": _text(c.get("catalog_type")), "comment": _text(c.get("comment"))}
                for c in (_object(x, "a catalog") for x in self.client.list(f"{UC}/catalogs", "catalogs", {}))]

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
                except (OddReply, TypeError, AttributeError) as exc:   # a list where an object belongs, say
                    why = str(exc) if isinstance(exc, OddReply) else "a value isn't the type the API documents"
                    parts[name] = {"ok": False, "error": f"Databricks answered in a shape discovery doesn't know ({why})."}
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
            # An error can quote the workspace's own message: one bounded line, like a name.
            lines.append(f"{labels[name]}: {how}: {_text(part['error'], _ERROR_MAX)}")
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
