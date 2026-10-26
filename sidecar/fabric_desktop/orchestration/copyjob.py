"""Read a Copy job definition (Data Factory DF-6): where it copies from and to, and each table.

A Copy job getDefinition has ``copyjob-content.json``: ``properties`` (``jobMode`` Batch or CDC, the
job's ``source`` and ``destination`` connection settings, ``policy``) and ``activities``, one per
table, each with its own source and destination dataset settings, write behavior, incremental
(``changeDataSettings``) and upsert keys. Field names follow Microsoft's Copy job definition article
(the examples, where its tables spell a name differently); no real export has been checked yet (plan
decision 4), so anything unexpected is reported under ``problems`` rather than raised.

``local_check`` says whether DF-6's run on this PC could take the job: only when both ends are a
lakehouse (OneLake) and the job is Batch, since an external source needs Fabric's connections and
gateways (plan section 6). This only reads the text; nothing runs.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

CONTENT = "copyjob-content.json"
ONELAKE = {"Lakehouse"}  # connection types a run on this PC can reach


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def endpoint(value: Any) -> dict[str, Any]:
    """A job-level source or destination: its type, connection, and the OneLake item when there is one."""
    v = _dict(value)
    conn = _dict(v.get("connectionSettings"))
    props = _dict(conn.get("typeProperties"))
    return {"type": _text(v.get("type")), "connection_type": _text(conn.get("type")),
            "connection_id": _text(_dict(conn.get("externalReferences")).get("connection")).strip().lower(),
            "workspace_id": _text(props.get("workspaceId")), "item_id": _text(props.get("artifactId")),
            "root_folder": _text(props.get("rootFolder")), "database": _text(props.get("database"))}


def _dataset(side: dict[str, Any]) -> dict[str, str]:
    ds = _dict(side.get("datasetSettings"))
    location = _dict(ds.get("location"))
    return {"schema": _text(ds.get("schema")), "table": _text(ds.get("table")),
            "folder": "/".join(p for p in (_text(location.get("container")), _text(location.get("folderPath"))) if p)}


def table(activity: Any) -> dict[str, Any]:
    """One activity (a table the job copies) as a flat row."""
    a = _dict(activity)
    props = _dict(a.get("properties"))
    src, dst = _dict(props.get("source")), _dict(props.get("destination"))
    change = _dict(src.get("changeDataSettings"))
    upsert = _dict(dst.get("upsertSettings"))
    mappings = _dict(props.get("translator")).get("mappings")
    return {"id": _text(a.get("id")), "source": _dataset(src), "destination": _dataset(dst),
            "write_behavior": _text(dst.get("writeBehavior") or dst.get("writeBehaviour")),
            "keys": [k for k in upsert.get("keys") or [] if isinstance(k, str)],
            "table_option": _text(dst.get("tableOption")),
            "read_method": _text(change.get("readMethod")),
            "incremental_columns": [_text(c.get("name")) for c in change.get("columns") or [] if isinstance(c, dict)],
            "mapped_columns": len(mappings) if isinstance(mappings, list) else 0,
            "staging": bool(props.get("enableStaging"))}


def local_check(mode: str, source: dict[str, Any], destination: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    for label, end in (("source", source), ("destination", destination)):
        if end["connection_type"] not in ONELAKE:
            reasons.append(f"The {label} is {end['connection_type'] or end['type'] or 'not set'}, not a lakehouse: "
                           "it needs Fabric's connection, so it runs in Fabric only.")
    if mode == "CDC":
        reasons.append("Change data capture (CDC) jobs run in Fabric only.")
    return {"possible": not reasons, "reasons": reasons}


def describe(result: dict[str, Any]) -> dict[str, Any]:
    """A getDefinition result as {ok, job_mode, source, destination, timeout, retry, tables, local_check, problems}."""
    text = ""
    for part in _dict(_dict(result).get("definition")).get("parts") or []:
        if isinstance(part, dict) and part.get("path") == CONTENT:
            try:
                text = base64.b64decode(part.get("payload") or "").decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                text = ""
    problems = []
    try:
        content = json.loads(text) if text else None
    except ValueError:
        content = None
    if not isinstance(content, dict):
        problems.append(f"The definition has no readable {CONTENT}, so the copy job can't be shown.")
        content = {}
    props = _dict(content.get("properties"))
    mode = _text(props.get("jobMode"))
    if content and mode not in ("Batch", "CDC"):
        problems.append(f"Unknown job mode {mode or '(none)'}: this version knows Batch and CDC.")
    policy = _dict(props.get("policy"))
    source, destination = endpoint(props.get("source")), endpoint(props.get("destination"))
    activities = content.get("activities")
    if content and not isinstance(activities, list):
        problems.append("activities isn't a list, so the tables can't be shown.")
    tables = [table(a) for a in activities or [] if isinstance(a, dict)] if isinstance(activities, list) else []
    return {"ok": bool(content), "job_mode": mode, "source": source, "destination": destination,
            "timeout": _text(policy.get("timeout")), "retry": policy.get("retry") if isinstance(policy.get("retry"), int) else None,
            "tables": tables, "local_check": local_check(mode, source, destination), "problems": problems}
