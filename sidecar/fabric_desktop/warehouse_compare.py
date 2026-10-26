"""Compare a warehouse project's files with the warehouse (WH-12).

Both sides are T-SQL: the project's files, and the scripts the page makes from the warehouse's
catalog (the same scripts Import writes). Two scripts are the same when they read the same as
T-SQL: comments, GO lines, semicolons, spacing, the letter case of keywords, and brackets or
quotes around plain names don't count. A Fabric warehouse's collation (Latin1_General_100_BIN2_UTF8
by default) makes names case-sensitive, so the case of a name, quoted or not, does count. For a table, the
columns that differ are named, since that is what a deploy (WH-13) can change with ALTER TABLE.
Nothing here reads files, runs SQL or calls Fabric.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

MAX_ROWS = 3000   # side-by-side rows returned for one object

# A GO line is a token here, so a "GO" inside a string or a comment stays part of it.
_TOKEN = re.compile(r"(?P<go>(?:^|\n)[ \t]*GO[ \t]*;?[ \t]*(?=\n|$))|--[^\n]*|/\*.*?\*/|N?'(?:[^']|'')*'|\[(?:[^\]]|\]\])*\]"
                    r"|\"(?:[^\"]|\"\")*\"|[\w@#$]+|[^\s\w]", re.DOTALL | re.IGNORECASE)
_PLAIN = re.compile(r"^[A-Za-z_][\w@#$]*$")
_NOT_COLUMN = {"constraint", "primary", "unique", "foreign", "check", "index"}
# Words T-SQL reads the same in any case. RESERVED can't be a name unless it's quoted, so it always
# folds. SOFT (built-in types and functions) can also be a column's name, such as Date or Count, so
# it folds only where it's a type or a call (soft_place); anywhere else it is a name and keeps its case.
RESERVED = frozenset("""
add all alter and any as asc authorization begin between by cascade case check close clustered
coalesce collate column commit constraint contains convert create cross current current_date
current_timestamp cursor declare default delete desc distinct distribution double drop else end
enforced escape except exec execute exists fetch for foreign from full function grant group hash
having identity if in index inner insert intersect into is join key left like merge nonclustered not
null nullif of off on open option or order outer over percent pivot primary print proc procedure
references return returns revoke right round_robin rollback schema select set some table then top
tran transaction truncate union unique unpivot update use values view when where while with within
""".split())
SOFT = frozenset("""
bigint binary bit cast char count date datetime datetime2 datetimeoffset dateadd datediff datepart
decimal float iif int isnull max min money nchar numeric nvarchar real replicate row_number rows
smallint sum sysdatetime time tinyint try_cast try_convert uniqueidentifier varbinary varchar
""".split())


def soft_place(prev2: str, prev: str, nxt: str) -> bool:
    """Whether a SOFT word, between these tokens, is a type or a call: max(, CAST(x AS int),
    CONVERT(date, ...), RETURNS int, DECLARE @x int, or a column's type in CREATE TABLE (a, b int)."""
    return (nxt == "(" or (prev == "as" and nxt == ")") or (prev == "(" and nxt == ",") or prev == "returns"
            or prev.startswith("@") or prev2 in ("(", ","))


def tokens(text: str, fold: bool = True) -> list[str]:
    """The script as T-SQL reads it: comments, GO lines and semicolons left out, keywords in lower
    case (unless ``fold`` is False; see RESERVED and SOFT), [Name] and "Name" as Name when plain
    (letters, digits, _), names and strings exactly as written."""
    raw: list[tuple[str, bool]] = []  # (token, a bare word that may be a keyword)
    for m in _TOKEN.finditer(text):
        t = m.group()
        if m.group("go") or t.startswith("--") or t.startswith("/*") or t == ";":
            continue
        if t[0] in "[\"":
            inner = t[1:-1].replace("]]", "]") if t[0] == "[" else t[1:-1].replace('""', '"')
            raw.append((inner if _PLAIN.match(inner) else f"[{inner}]", False))
        elif t[0] == "'" or t[:2].upper() == "N'":
            raw.append((t, False))
        else:
            raw.append((t, True))
    out: list[str] = []
    for i, (t, word) in enumerate(raw):
        low = t.lower()
        if fold and word and (low in RESERVED or (low in SOFT and soft_place(
                out[i - 2] if i > 1 else "", out[i - 1] if i else "", raw[i + 1][0].lower() if i + 1 < len(raw) else ""))):
            t = low
        out.append(t)
    return out


def same(a: str, b: str) -> bool:
    return tokens(a) == tokens(b)


def columns(text: str) -> dict[str, tuple[str, str]] | None:
    """A CREATE TABLE's columns, {name: (name, "type and options")}, or None when it isn't one.
    Names are case-sensitive, as the warehouse reads them; compare the options in lower case."""
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
            out[p[0]] = (p[0], " ".join(p[1:]).replace(" ( ", "(").replace(" , ", ",").replace(" )", ")"))
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
ONE_DEFINITION = ("the file holds more than the object's own CREATE (another batch after GO), so a deploy doesn't "
                  "offer it: keep one object per file")
ONE_TABLE = ("the file holds more than CREATE TABLE with its columns (another statement, or AS SELECT), so a deploy "
             "doesn't offer it: keep one table per file")
REBUILD = ("Changing a column's type or nullability, removing a column or adding one that isn't NULL or isn't last "
           "would rebuild the table, so a deploy doesn't offer it. Write that change as a script and run it yourself.")


def _quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _full(o: dict[str, Any]) -> str:
    return _quote(o["name"]) if o["kind"] == "schema" else f"{_quote(o['schema'])}.{_quote(o['name'])}"


def batches(text: str) -> list[str]:
    """The script's batches, split on GO lines outside strings and comments, leaving out batches
    with nothing but comments and spacing."""
    out, start = [], 0
    for m in _TOKEN.finditer(text):
        if m.group("go"):
            out.append(text[start:m.start()])
            start = m.end()
    out.append(text[start:])
    return [b.strip("\n") for b in out if tokens(b)]


def _one_definition(kind: str, text: str) -> str | None:
    """The script when it is one batch that only defines one object of ``kind``, else None. T-SQL
    makes CREATE VIEW, PROCEDURE and FUNCTION the only statement of its batch, so one batch is enough
    for those. A table is only CREATE TABLE name (columns); a schema only CREATE SCHEMA name, with an
    optional AUTHORIZATION: nothing that copies, removes or changes data rides along."""
    parts = batches(text)
    if len(parts) != 1:
        return None
    t = tokens(parts[0])
    if kind in ("view", "procedure", "function"):
        if not _LEADING_CREATE.match(parts[0]):
            return None
        # A view, or a function with no BEGIN ... END body, ends at its first ";": nothing may follow.
        if kind == "view" or "begin" not in t:
            words = [m.group() for m in _TOKEN.finditer(parts[0]) if not m.group().startswith(("--", "/*"))]
            if ";" in words and any(w != ";" for w in words[words.index(";"):]):
                return None
        return parts[0]
    if t[:2] != ["create", kind] or kind not in ("table", "schema"):
        return None
    i = 2
    while i + 1 < len(t) and t[i + 1] == ".":  # schema.name
        i += 2
    if kind == "schema":
        rest = t[i + 1:]
        return parts[0] if not rest or (len(rest) == 2 and rest[0] == "authorization") else None
    if i + 1 >= len(t) or t[i + 1] != "(":
        return None
    depth = 0
    for j in range(i + 1, len(t)):
        depth += {"(": 1, ")": -1}.get(t[j], 0)
        if depth == 0:
            return parts[0] if j == len(t) - 1 else None
    return None


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
    never ticked; a change that would rebuild a table isn't offered. A file's script is offered only
    when it is one batch that only defines its object (_one_definition), so nothing else in the file
    runs with it."""
    out: list[dict[str, Any]] = []
    for r in rows:
        base = {"kind": r["kind"], "schema": r["schema"], "name": r["name"], "path": r.get("path", ""),
                "detail": r.get("detail") or []}
        own = _one_definition(r["kind"], r["project_text"]) if r["state"] in ("only_project", "changed") else None
        if r["state"] in ("only_project", "changed") and r["kind"] != "table" and own is None:
            out.append({**base, "action": "create" if r["state"] == "only_project" else "alter", "sql": "",
                        "ticked": False, "offered": False, "why": ONE_DEFINITION})
        elif r["state"] == "only_project" and own is None:
            out.append({**base, "action": "create", "sql": "", "ticked": False, "offered": False, "why": ONE_TABLE})
        elif r["state"] == "only_project":
            out.append({**base, "action": "create", "sql": own, "ticked": True, "offered": True,
                        "why": "new" if r["kind"] != "table" else "new table, empty"})
        elif r["state"] == "only_warehouse":
            out.append({**base, "action": "drop", "sql": f"DROP {_DROP_WORD[r['kind']]} {_full(r)};", "ticked": False,
                        "offered": True, "why": "only in the warehouse. Drops are never ticked for you."})
        elif r["state"] == "changed" and r["kind"] in ("view", "procedure", "function"):
            sql = _as_create_or_alter(own)
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
