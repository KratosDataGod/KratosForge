"""Compare a warehouse project's files with the warehouse (WH-12).

Both sides are T-SQL: the project's files, and the scripts the page makes from the warehouse's
catalog (the same scripts Import writes). Two scripts are the same when they read the same as
T-SQL in a case-insensitive warehouse: comments, GO lines, semicolons, spacing, letter case
outside strings, and brackets or quotes around plain names don't count. For a table, the
columns that differ are named, since that is what a deploy (WH-13) can change with ALTER TABLE.
Nothing here reads files, runs SQL or calls Fabric.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

MAX_ROWS = 3000   # side-by-side rows returned for one object

_GO = re.compile(r"^[ \t]*GO[ \t]*;?[ \t]*$", re.IGNORECASE | re.MULTILINE)
_TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|N?'(?:[^']|'')*'|\[(?:[^\]]|\]\])*\]|\"(?:[^\"]|\"\")*\"|[\w@#$]+|[^\s\w]",
                    re.DOTALL)
_PLAIN = re.compile(r"^[A-Za-z_][\w@#$]*$")
_NOT_COLUMN = {"constraint", "primary", "unique", "foreign", "check", "index"}


def tokens(text: str, fold: bool = True) -> list[str]:
    """The script as T-SQL reads it: comments, GO lines and semicolons left out, words in lower case
    (unless ``fold`` is False), [Name] and "Name" as name when plain (letters, digits, _), strings
    exactly as written."""
    low = str.lower if fold else str
    out: list[str] = []
    for t in _TOKEN.findall(_GO.sub(" ", text)):
        if t.startswith("--") or t.startswith("/*") or t == ";":
            continue
        if t[0] in "[\"":
            inner = t[1:-1].replace("]]", "]") if t[0] == "[" else t[1:-1].replace('""', '"')
            out.append(low(inner) if _PLAIN.match(inner) else f"[{low(inner)}]")
        elif t[0] == "'" or t[:2].upper() == "N'":
            out.append(t)
        else:
            out.append(low(t))
    return out


def same(a: str, b: str) -> bool:
    return tokens(a) == tokens(b)


def columns(text: str) -> dict[str, tuple[str, str]] | None:
    """A CREATE TABLE's columns, {name in lower case: (name, "type and options")}, or None when it
    isn't one. Names keep their case for messages; compare the options in lower case."""
    t = tokens(text, fold=False)
    low = [x.lower() for x in t]
    try:
        start = low.index("table")
        if start == 0 or low[start - 1] != "create":
            return None
        open_at = low.index("(", start)
    except ValueError:
        return None
    depth, part, parts = 0, [], []
    for tok in t[open_at + 1:]:
        if tok == "(":
            depth += 1
        elif tok == ")":
            if depth == 0:
                break
            depth -= 1
        if tok == "," and depth == 0:
            parts.append(part)
            part = []
        else:
            part.append(tok)
    parts.append(part)
    out: dict[str, tuple[str, str]] = {}
    for p in parts:
        if p and p[0].lower() not in _NOT_COLUMN:
            out[p[0].lower()] = (p[0], " ".join(p[1:]).replace(" ( ", "(").replace(" , ", ",").replace(" )", ")"))
    return out


def table_detail(warehouse: str, project: str) -> list[str]:
    """What differs between two CREATE TABLEs, column by column, as short sentences."""
    w, p = columns(warehouse), columns(project)
    if w is None or p is None:
        return []
    out = [f"column {p[c][0]} is only in the project" for c in p if c not in w]
    out += [f"column {w[c][0]} is only in the warehouse" for c in w if c not in p]
    out += [f"column {p[c][0]}: {w[c][1]} in the warehouse, {p[c][1]} in the project"
            for c in w if c in p and w[c][1].lower() != p[c][1].lower()]
    if not out and list(w) != list(p):
        out.append("the columns are in another order")
    return out or ["the table's options differ"]


def side_by_side(left: str, right: str) -> dict[str, Any]:
    """Rows for a side-by-side view, left the warehouse and right the project:
    [{"op": same|changed|removed|added, "left", "right"}] (None for a side with no line)."""
    a, b = left.replace("\r\n", "\n").rstrip("\n").split("\n"), right.replace("\r\n", "\n").rstrip("\n").split("\n")
    rows: list[dict[str, Any]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            rows += [{"op": "same", "left": a[i], "right": b[j]} for i, j in zip(range(i1, i2), range(j1, j2))]
        elif op == "delete":
            rows += [{"op": "removed", "left": a[i], "right": None} for i in range(i1, i2)]
        elif op == "insert":
            rows += [{"op": "added", "left": None, "right": b[j]} for j in range(j1, j2)]
        else:
            n = max(i2 - i1, j2 - j1)
            rows += [{"op": "changed", "left": a[i1 + k] if i1 + k < i2 else None,
                      "right": b[j1 + k] if j1 + k < j2 else None} for k in range(n)]
    return {"rows": rows[:MAX_ROWS], "cut": len(rows) > MAX_ROWS}


def compare(project: dict[tuple[str, str, str], dict[str, str]],
            warehouse: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    """One row per object on either side, keyed (kind, schema, name) in lower case. Each side's value
    is {"kind", "schema", "name", "text"} (the project's also "path"). The state is same, changed,
    only_project or only_warehouse; changed tables say which columns differ. Rows come in that
    order of state (same last), then by schema and name."""
    out: list[dict[str, Any]] = []
    for key in sorted(set(project) | set(warehouse)):
        p, w = project.get(key), warehouse.get(key)
        first = w or p
        row: dict[str, Any] = {"kind": first["kind"], "schema": first["schema"], "name": first["name"],
                               "path": (p or {}).get("path", "")}
        if p and w:
            if same(w["text"], p["text"]):
                row["state"] = "same"
            else:
                row.update(state="changed", detail=table_detail(w["text"], p["text"]) if key[0] == "table" else [],
                           warehouse_text=w["text"], project_text=p["text"])
        elif p:
            row.update(state="only_project", project_text=p["text"])
        else:
            row.update(state="only_warehouse", warehouse_text=w["text"])
        out.append(row)
    order = {"changed": 0, "only_project": 1, "only_warehouse": 2, "same": 3}
    return sorted(out, key=lambda r: (order[r["state"]], r["schema"].lower(), r["name"].lower()))
