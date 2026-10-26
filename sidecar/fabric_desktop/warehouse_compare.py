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


# ---- deploy (WH-13): the changes that make the warehouse match the project

_DROP_WORD = {"table": "TABLE", "view": "VIEW", "procedure": "PROCEDURE", "function": "FUNCTION", "schema": "SCHEMA"}
_ORDER = {("create", "schema"): 0, ("create", "table"): 1, ("add_columns", "table"): 1, ("create", "function"): 2,
          ("alter", "function"): 2, ("create", "view"): 3, ("alter", "view"): 3, ("create", "procedure"): 4,
          ("alter", "procedure"): 4, ("drop", "procedure"): 5, ("drop", "view"): 6, ("drop", "function"): 7,
          ("drop", "table"): 8, ("drop", "schema"): 9}
_LEADING_CREATE = re.compile(r"^((?:\s|--[^\n]*\n|/\*.*?\*/)*)CREATE\s+(?:OR\s+ALTER\s+)?(VIEW|PROC|PROCEDURE|FUNCTION)\b",
                             re.IGNORECASE | re.DOTALL)
REBUILD = ("Changing a column's type or nullability, removing a column or adding one that isn't NULL or isn't last "
           "would rebuild the table, so a deploy doesn't offer it. Write that change as a script and run it yourself.")


def _quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _full(o: dict[str, Any]) -> str:
    return _quote(o["name"]) if o["kind"] == "schema" else f"{_quote(o['schema'])}.{_quote(o['name'])}"


def _as_create_or_alter(text: str) -> str | None:
    m = _LEADING_CREATE.match(text)
    return None if not m else f"{m.group(1)}CREATE OR ALTER {m.group(2).upper()}" + text[m.end():]


def _added_columns(warehouse: str, project: str) -> list[tuple[str, str]] | None:
    """The columns a table only gains, at its end and NULL, as [(name, spec)], or None when the change
    is anything else (that would rebuild the table)."""
    w, p = columns(warehouse), columns(project)
    if w is None or p is None or not set(w) <= set(p):
        return None
    if any(w[c][1].lower() != p[c][1].lower() for c in w) or list(p)[: len(w)] != list(w):
        return None
    new = [p[c] for c in list(p)[len(w):]]
    for _, spec in new:
        words = spec.lower().split()
        if "null" not in words or ("not" in words and words[words.index("null") - 1] == "not") or "identity" in spec.lower():
            return None
    return new or None


def deploy_plan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The changes, in the order a deploy runs them, from compare() rows. Each is {kind, schema, name,
    path, action (create, alter, add_columns, drop), sql, ticked, offered, why}. A drop is offered but
    never ticked; a change that would rebuild a table isn't offered."""
    out: list[dict[str, Any]] = []
    for r in rows:
        base = {"kind": r["kind"], "schema": r["schema"], "name": r["name"], "path": r.get("path", ""),
                "detail": r.get("detail") or []}
        if r["state"] == "only_project":
            out.append({**base, "action": "create", "sql": r["project_text"], "ticked": True, "offered": True,
                        "why": "new" if r["kind"] != "table" else "new table, empty"})
        elif r["state"] == "only_warehouse":
            out.append({**base, "action": "drop", "sql": f"DROP {_DROP_WORD[r['kind']]} {_full(r)};", "ticked": False,
                        "offered": True, "why": "only in the warehouse. Drops are never ticked for you."})
        elif r["state"] == "changed" and r["kind"] in ("view", "procedure", "function"):
            sql = _as_create_or_alter(r["project_text"])
            out.append({**base, "action": "alter", "sql": sql or "", "ticked": bool(sql), "offered": bool(sql),
                        "why": "definition changes" if sql else "the file doesn't start with CREATE, so it can't be applied"})
        elif r["state"] == "changed" and r["kind"] == "table":
            new = _added_columns(r["warehouse_text"], r["project_text"])
            sql = "\n".join(f"ALTER TABLE {_full(r)} ADD {_quote(n)} {spec};" for n, spec in new) if new else ""
            out.append({**base, "action": "add_columns", "sql": sql, "ticked": bool(new), "offered": bool(new),
                        "why": (f"new nullable column{'s' if len(new) > 1 else ''} {', '.join(n for n, _ in new)}; existing rows "
                                "keep their data") if new else REBUILD})
        elif r["state"] == "changed":
            out.append({**base, "action": "alter", "sql": "", "ticked": False, "offered": False,
                        "why": "a schema can't be changed, only created or dropped"})
    out.sort(key=lambda c: (_ORDER.get((c["action"], c["kind"]), 10), c["schema"].lower(), c["name"].lower()))
    return out
