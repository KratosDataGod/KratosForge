"""Read a Dataflow Gen2 definition (Data Factory DF-7): its Power Query (M) queries and where each loads.

A Dataflow Gen2 getDefinition has ``mashup.pq`` (an M section document: ``section Section1;`` then
``shared Name = expression;`` per query, a query's attributes in ``[...]`` before ``shared``) and
``queryMetadata.json`` (each query's load and hidden flags, and the connections). A query that loads
somewhere carries ``[DataDestinations = {[Definition = [Kind = "Reference", QueryName = "X_DataDestination"],
Settings = [...]]}]``, and the ``X_DataDestination`` query navigates to the target item and table.

This only reads the text: nothing is evaluated, and there is no local M engine (plan section 6).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

_NAME = re.compile(r'\s*shared\s+(#"(?:[^"]|"")*"|[A-Za-z_][\w.]*)\s*=\s*', re.S)
_QUOTED = r'"((?:[^"]|"")*)"'
_DEST_REF = re.compile(r'QueryName\s*=\s*' + _QUOTED)
_UPDATE = re.compile(r'UpdateMethod\s*=\s*\[\s*Kind\s*=\s*' + _QUOTED)
_FIELD = {key: re.compile(rf'\b{key}\s*=\s*' + _QUOTED) for key in
          ("workspaceId", "lakehouseId", "warehouseId", "Id", "Item", "Schema", "Name")}
# The function a destination query starts from, and the kind of target it means.
_TARGETS = (("Lakehouse.Contents", "Lakehouse"), ("Fabric.Warehouse", "Warehouse"), ("Sql.Database", "Azure SQL database"),
            ("AzureDataExplorer.Contents", "KQL database"), ("Kusto.Contents", "KQL database"))


def _unquote(name: str) -> str:
    return name[2:-1].replace('""', '"') if name.startswith('#"') else name


def statements(text: str) -> list[str]:
    """The section's statements, split on top-level semicolons; strings and comments are skipped over."""
    out, start, depth, i, n = [], 0, 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == ";" and depth == 0:
            out.append(text[start:i])
            start = i + 1
        i += 1
    if text[start:].strip():
        out.append(text[start:])
    return out


def _trivia(text: str) -> str:
    """``text`` without its leading spaces and comments."""
    s = text.lstrip()
    while s.startswith(("//", "/*")):
        end = s.find("\n") if s.startswith("//") else s.find("*/") + 1
        s = "" if end <= 0 else s[end + 1:].lstrip()
    return s


def _attributes(text: str) -> tuple[str, str]:
    """A statement's leading ``[...]`` attribute record and the rest."""
    s = _trivia(text)
    if not s.startswith("["):
        return "", s
    depth, i = 0, 0
    while i < len(s):
        if s[i] == '"':
            i = s.find('"', i + 1)
            while i >= 0 and i + 1 < len(s) and s[i + 1] == '"':
                i = s.find('"', i + 2)
            if i < 0:
                return "", s
        elif s[i] == "[":
            depth += 1
        elif s[i] == "]":
            depth -= 1
            if depth == 0:
                return s[:i + 1], _trivia(s[i + 1:])
        i += 1
    return "", s


def queries(mashup: str) -> list[dict[str, Any]]:
    """Each ``shared`` query: name, M expression and attributes, in document order."""
    out = []
    for stmt in statements(mashup):
        attrs, rest = _attributes(stmt)
        m = _NAME.match(rest)
        if not m:
            continue  # "section Section1" and anything this version doesn't know
        out.append({"name": _unquote(m.group(1)), "m": rest[m.end():].strip(), "attributes": attrs})
    return out


def _field(text: str, key: str) -> str:
    m = _FIELD[key].search(text)
    return m.group(1).replace('""', '"') if m else ""


def destination(attrs: str, by_name: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Where a query loads, from its DataDestinations attribute and the query that attribute names."""
    if "DataDestinations" not in attrs:
        return None
    ref = _DEST_REF.search(attrs)
    update = _UPDATE.search(attrs)
    target = by_name.get(ref.group(1).replace('""', '"')) if ref else None
    m = target["m"] if target else ""
    kind = next((label for fn, label in _TARGETS if fn in m), "Unknown" if m else "")
    return {"query": ref.group(1) if ref else "", "kind": kind, "workspace_id": _field(m, "workspaceId"),
            "item_id": _field(m, "lakehouseId") or _field(m, "warehouseId"),
            "schema": _field(m, "Schema"), "table": _field(m, "Id") or _field(m, "Item") or _field(m, "Name"),
            "update_method": update.group(1) if update else "", "new_table": "IsNewTarget = true" in attrs}


def _connection_id(raw: Any) -> str:
    """queryMetadata keeps a connection's id as JSON text with ``DatasourceId``, or as a plain id."""
    if not isinstance(raw, str):
        return ""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    return str(value.get("DatasourceId") or "") if isinstance(value, dict) else raw


def describe(result: dict[str, Any]) -> dict[str, Any]:
    """A getDefinition result as {ok, queries, connections, mashup, problems}. Hidden queries
    (destinations, helpers) are listed too, marked hidden, so the page can fold them away."""
    parts: dict[str, str] = {}
    for part in ((result or {}).get("definition") or {}).get("parts") or []:
        try:
            parts[str(part.get("path") or "")] = base64.b64decode(part.get("payload") or "").decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
    mashup = parts.get("mashup.pq", "")
    try:
        meta = json.loads(parts.get("queryMetadata.json") or "{}")
    except ValueError:
        meta = {}
    problems = []
    if not isinstance(meta, dict):
        problems.append("queryMetadata.json isn't an object, so load flags and connections can't be shown.")
        meta = {}
    if not mashup:
        problems.append("The definition has no mashup.pq, so its queries can't be shown.")
    found = queries(mashup)
    by_name = {q["name"]: q for q in found}
    flags = meta.get("queriesMetadata") if isinstance(meta.get("queriesMetadata"), dict) else {}
    rows = []
    for q in found:
        info = flags.get(q["name"]) or {}
        rows.append({"name": q["name"], "m": q["m"], "load_enabled": bool(info.get("loadEnabled", True)),
                     "hidden": bool(info.get("isHidden", False)), "destination": destination(q["attributes"], by_name)})
    connections = [{"kind": c.get("kind") or "", "path": c.get("path") or "", "connection_id": _connection_id(c.get("connectionId"))}
                   for c in meta.get("connections") or [] if isinstance(c, dict)]
    return {"ok": bool(mashup), "queries": rows, "connections": connections, "mashup": mashup, "problems": problems}
