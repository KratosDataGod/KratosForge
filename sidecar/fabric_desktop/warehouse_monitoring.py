"""Workspace monitoring views for a warehouse (WH-19, Preview).

When a workspace admin turns on workspace monitoring, Fabric records each completed, user-initiated
query on the workspace's warehouses, SQL analytics endpoints and warehouse snapshots in the
``WarehouseExecutions`` table of a read-only KQL database (Fabric's monitoring Eventhouse). See
https://learn.microsoft.com/fabric/data-warehouse/warehouse-workspace-monitoring.

This module holds the views over that table, one warehouse at a time, and the two ways they are used:

- **In the app.** ``query`` runs one view in the monitoring database, as the person who signed in,
  and returns its rows. The KQL is only ever one of ``VIEWS`` (or ``SUMMARY``), filled in with a
  warehouse id that must be a GUID and a window from ``WINDOWS``; nothing the caller writes reaches
  it, and the request asks Kusto for a read-only run.
- **In Fabric.** ``queryset_definition`` is a KQL queryset with one tab per view, filtered to the
  warehouse, for anyone in the workspace to open, pin to a dashboard or alert on. The monitoring
  database can't hold functions of its own (it is read-only), so the views live next to it.
  ``merge_tabs`` keeps the tabs a person added to that queryset: only the app's own tabs (fixed ids
  per warehouse and view) are replaced.

The token goes only to a Fabric Eventhouse (``*.kusto.fabric.microsoft.com``), read from Fabric for
the KQL database picked, never taken from the caller.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from typing import Any, Callable

import requests

from .retrying import TRANSIENT_STATUS, send_with_retry

TABLE = "WarehouseExecutions"
HOST_SUFFIX = ".kusto.fabric.microsoft.com"
MAX_ROWS = 500
QUERY_ATTEMPTS = 3  # each try can take up to its 90 s timeout, so three keep a view under five minutes
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NS = uuid.UUID("6f1d2a8e-4b8f-4c55-9d43-2b0c5e7a9f10")  # tab and data source ids: the same ones on every build
WINDOWS = {"1h": "1h", "24h": "24h", "7d": "7d", "30d": "30d"}

# Who ran a query: Identity holds the caller's claims; which claim carries the name isn't documented.
_WHO = "tostring(coalesce(Identity.claims.upn, Identity.claims.unique_name, Identity.upn, Identity.name, Identity.claims.oid))"

# key: (title, what it shows, KQL). {item} is the warehouse id, {window} the time window.
VIEWS: dict[str, tuple[str, str, str]] = {
    "activity": ("Activity", "Queries, average time and CPU, per hour", """WarehouseExecutions
| where Timestamp > ago({window}) and ItemId == "{item}"
| summarize Queries = count(), AverageDurationMs = round(avg(DurationMs), 2), CpuTimeMs = sum(CpuTimeMs)
    by Hour = bin(Timestamp, 1h)
| order by Hour asc"""),
    "durations": ("Duration percentiles", "P50 and P95 time per day", """WarehouseExecutions
| where Timestamp > ago({window}) and ItemId == "{item}"
| summarize Queries = count(), P50DurationMs = percentile(DurationMs, 50), P95DurationMs = percentile(DurationMs, 95)
    by Day = bin(Timestamp, 1d)
| order by Day asc"""),
    "failures": ("Failures", "Failed queries by result code, with the failure rate", """let Runs = WarehouseExecutions
    | where Timestamp > ago({window}) and ItemId == "{item}";
let Total = toscalar(Runs | count);
Runs
| where Status == "Failed"
| summarize FailedQueries = count(), LastFailed = max(OperationEndTime) by ResultCode
| extend FailureRatePercent = round(100.0 * FailedQueries / Total, 2)
| order by FailedQueries desc"""),
    "slowest": ("Slowest queries", "The 50 longest, with their operation id", f"""WarehouseExecutions
| where Timestamp > ago({{window}}) and ItemId == "{{item}}"
| top 50 by DurationMs desc
| project OperationEndTime, OperationName, Status, ResultCode, DurationMs, CpuTimeMs, User = {_WHO}, OperationId"""),
    "people": ("Who runs what", "Queries and CPU by person", f"""WarehouseExecutions
| where Timestamp > ago({{window}}) and ItemId == "{{item}}"
| summarize Queries = count(), Failed = countif(Status == "Failed"), CpuTimeMs = sum(CpuTimeMs),
    P95DurationMs = percentile(DurationMs, 95) by User = {_WHO}
| order by CpuTimeMs desc"""),
    "alert": ("Failure alert check", "Last 15 minutes: at least 10 queries and 20% failing", """WarehouseExecutions
| where Timestamp > ago(15m) and ItemId == "{item}"
| summarize QueryCount = count(), FailedQueries = countif(Status == "Failed")
| extend FailureRatePercent = round(100.0 * FailedQueries / QueryCount, 2)
| where QueryCount >= 10 and FailureRatePercent >= 20"""),
}

SUMMARY = """WarehouseExecutions
| where Timestamp > ago({window}) and ItemId == "{item}"
| summarize Queries = count(), Failed = countif(Status == "Failed"), P50DurationMs = percentile(DurationMs, 50),
    P95DurationMs = percentile(DurationMs, 95), CpuTimeMs = sum(CpuTimeMs), LastSeen = max(Timestamp)"""

# Whether a KQL database holds warehouse monitoring for a workspace, and when it last saw a query.
PROBE = """WarehouseExecutions
| where WorkspaceId == "{workspace}"
| summarize LastSeen = max(Timestamp), Items = dcount(ItemId)"""


def guid(value: Any, what: str) -> str:
    text = str(value or "")
    if not _GUID.fullmatch(text):
        raise ValueError(f"Pick a {what}: its Fabric id is needed.")
    return text.lower()


def view_kql(view: str, item_id: str, window: str = "24h") -> str:
    """The KQL of one view (or ``summary``) for one warehouse."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {', '.join(WINDOWS)}")
    if view == "summary":
        text = SUMMARY
    elif view in VIEWS:
        text = VIEWS[view][2]
    else:
        raise ValueError(f"There's no monitoring view called {view!r}.")
    return text.replace("{window}", WINDOWS[window]).replace("{item}", guid(item_id, "warehouse"))


def probe_kql(workspace_id: str) -> str:
    return PROBE.replace("{workspace}", guid(workspace_id, "workspace"))


_CLUSTER = re.compile(r"https://((?:[a-z0-9-]+\.)+kusto\.fabric\.microsoft\.com)/?", re.IGNORECASE)


def cluster(uri: Any) -> str:
    """A KQL database's query URI, when it is a Fabric Eventhouse's; else ValueError, and no token is
    sent. The whole text must be https://<labels>.kusto.fabric.microsoft.com: no other character
    (\\, %, @, :, a path or a trailing dot), so no client can read another host out of it."""
    text = str(uri or "")
    m = _CLUSTER.fullmatch(text)
    if not m:
        raise ValueError(f"{text!r} isn't a Fabric Eventhouse, so nothing was sent to it.")
    return f"https://{m.group(1).lower()}"


def queryset_name(warehouse_name: str) -> str:
    """The queryset's name: the warehouse's, with what Fabric doesn't take in a queryset name made _."""
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", str(warehouse_name or "").strip()) or "warehouse"
    return f"{base[:200]}_monitoring"


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


def own_tabs(item_id: str, source_id: str, window: str = "7d") -> list[dict[str, str]]:
    """The app's tabs for one warehouse: one per view, with ids that are the same on every build."""
    item = guid(item_id, "warehouse")
    return [{"id": _id(item, key), "title": title, "content": view_kql(key, item, window), "dataSourceId": source_id}
            for key, (title, _what, _kql) in VIEWS.items()]


def queryset_definition(item_id: str, cluster_uri: str, database: str, existing: dict[str, Any] | None = None,
                        window: str = "7d") -> dict[str, Any]:
    """A KQL queryset definition (RealTimeQueryset.json, base64) with the views of one warehouse.
    ``existing`` is the queryset's current definition (getDefinition), whose other tabs and data
    sources are kept."""
    uri = cluster(cluster_uri)
    if not str(database or "").strip():
        raise ValueError("Pick the monitoring KQL database.")
    item = guid(item_id, "warehouse")
    source = {"id": _id(item, "source"), "clusterUri": uri, "type": "AzureDataExplorer", "databaseName": str(database)}
    current = read_queryset(existing) if existing else {"version": "1.0.0", "dataSources": [], "tabs": []}
    body = {"queryset": merge(current, source, own_tabs(item, source["id"], window))}
    payload = base64.b64encode(json.dumps(body, indent=2).encode()).decode()
    return {"parts": [{"path": "RealTimeQueryset.json", "payload": payload, "payloadType": "InlineBase64"}]}


def read_queryset(definition: dict[str, Any]) -> dict[str, Any]:
    """The ``queryset`` object of a getDefinition result; an empty one when it holds none."""
    for part in (definition.get("definition") or definition).get("parts") or []:
        if part.get("path") == "RealTimeQueryset.json":
            try:
                body = json.loads(base64.b64decode(part.get("payload") or ""))
            except (ValueError, RecursionError) as exc:
                raise ValueError(f"The queryset's definition couldn't be read, so it was left as it is: {exc}") from None
            q = body.get("queryset") if isinstance(body, dict) else None
            if isinstance(q, dict):
                return {"version": q.get("version") or "1.0.0", "dataSources": list(q.get("dataSources") or []),
                        "tabs": list(q.get("tabs") or []), **{k: v for k, v in q.items()
                                                                if k not in ("version", "dataSources", "tabs")}}
    return {"version": "1.0.0", "dataSources": [], "tabs": []}


def merge(current: dict[str, Any], source: dict[str, str], tabs: list[dict[str, str]]) -> dict[str, Any]:
    """``current`` with the app's data source and tabs put in place of its earlier ones; every other
    tab and data source stays, in its order, and the app's tabs come first."""
    ours = {t["id"] for t in tabs}
    sources = [s for s in current.get("dataSources") or [] if s.get("id") != source["id"]]
    kept = [t for t in current.get("tabs") or [] if t.get("id") not in ours]
    return {**current, "dataSources": [source, *sources], "tabs": [*tabs, *kept]}


def query(post: Callable[..., Any], cluster_uri: str, database: str, kql: str, token: str,
          sleep: Callable[[float], None] = time.sleep, refresh: Callable[[], str] | None = None) -> dict[str, Any]:
    """Run ``kql`` (one of this module's views) on a KQL database, read-only, and return its first
    table as {columns: [{name, type}], rows, truncated}. ``post`` is requests' post. The query only
    reads, so it is sent again after throttling (as Retry-After says), a 5xx, a timeout or a dropped
    connection, like every other Fabric read (retrying.send_with_retry). After a 401, ``refresh`` gives a
    new token once (as FabricClient does): Kusto refused the read unread, so it is sent once more."""
    uri = cluster(cluster_uri)
    props = {"Options": {"request_readonly": True, "servertimeout": "00:01:00", "truncationmaxrecords": MAX_ROWS + 1}}
    body = {"db": database, "csl": kql, "properties": json.dumps(props)}

    bearer = [token]

    def send() -> Any:  # a new request id on every try, as Kusto asks
        return post(f"{uri}/v1/rest/query", json=body,
                    headers={"Authorization": f"Bearer {bearer[0]}", "Accept": "application/json",
                             "x-ms-app": "KratosForge", "x-ms-client-request-id": f"KratosForge;{uuid.uuid4()}"},
                    timeout=90)

    try:
        resp = send_with_retry(send, "POST", sleep=sleep, attempts=QUERY_ATTEMPTS, safe=True)
        if resp.status_code == 401 and refresh is not None:
            fresh = _fresh(refresh)
            if fresh and fresh != token:  # once, and only with a token that is actually new
                bearer[0] = fresh
                resp = send_with_retry(send, "POST", sleep=sleep, attempts=QUERY_ATTEMPTS, safe=True)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise RuntimeError(f"The monitoring database couldn't be reached after {QUERY_ATTEMPTS} tries. "
                           f"Try again in a minute. ({type(exc).__name__})") from None
    if resp.status_code >= 400:
        raise RuntimeError(_kusto_error(resp))
    try:
        reply = resp.json()
    except ValueError:
        reply = None
    tables = (reply.get("Tables") or []) if isinstance(reply, dict) else None
    if not isinstance(tables, list) or not all(isinstance(t, dict) for t in tables):
        # A proxy's sign-in page or a gateway's page: its text isn't repeated (it may echo the request).
        raise RuntimeError(f"The monitoring database answered with something other than a KQL result "
                           f"(HTTP {resp.status_code}). Check the network or proxy between this PC and Fabric.")
    if not tables:
        return {"columns": [], "rows": [], "truncated": False}
    first = tables[0]
    rows = first.get("Rows") or []
    return {"columns": [{"name": c.get("ColumnName"), "type": c.get("DataType") or c.get("ColumnType")}
                        for c in first.get("Columns") or []],
            "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


_MESSAGE_MAX = 300  # characters of Kusto's own message in an error


def _fresh(refresh: Callable[[], str]) -> str:
    """A new token, or "" when none can be had without the person: the 401 is then reported as it came."""
    try:
        return refresh() or ""
    except Exception:  # noqa: BLE001  # silent-ok: the caller reports the original 401
        return ""


def _one_line(text: str) -> str:
    """Kusto's message as part of one line: whitespace and control characters folded, cut to _MESSAGE_MAX."""
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    return text if len(text) <= _MESSAGE_MAX else text[:_MESSAGE_MAX - 1].rstrip() + "…"


def _kusto_error(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    message = _one_line(str(err.get("@message") or err.get("message") or "")) if isinstance(err, dict) else ""
    if resp.status_code in (401, 403):
        return ("The monitoring database didn't let you read it. Reading it needs at least the Contributor role "
                "in the workspace, or the KQL database shared with you." + (f" ({message})" if message else ""))
    if resp.status_code in TRANSIENT_STATUS:
        return (f"The monitoring database is busy or out of reach (it answered {resp.status_code} after "
                f"{QUERY_ATTEMPTS} tries). Try again in a minute." + (f" ({message})" if message else ""))
    return f"The monitoring database answered {resp.status_code}" + (f": {message}" if message else ".")


def missing_table(message: str) -> bool:
    """Whether a query failed because the database has no WarehouseExecutions table (no warehouse
    query has been recorded in it, or it isn't a monitoring database)."""
    return bool(re.search(r"(?:SEM0100|Failed to resolve table|'WarehouseExecutions')", message, re.IGNORECASE))
