"""Try the fix beside a SQL best-practice finding (GD-02): a rewrite offered only where it returns
exactly what the SQL returned before.

``propose`` reads the SQL and the finding; nothing runs. A rule has a fix here only when it can be
shown to keep the result, from the SQL and the catalog the page already has (its tables and their
column types). When that can't be shown, there is no fix and the page shows no Try the fix:

    SQ002  ``YEAR(col) = 2026`` -> ``(col >= '2026-01-01' AND col < '2027-01-01')`` when ``col`` is a
           date or date-and-time column (not datetimeoffset) of the one table that has it. Both sides are
           NULL for a NULL date, and the literals are 1 January, which every date format reads the same.
    SQ005  ``sales`` -> ``dbo.sales`` when ``dbo`` is the only schema with that name. An unqualified name
           resolves to the default schema, which is dbo in a Fabric warehouse and SQL analytics endpoint.

A fix is an edit the person applies (and can undo) in the editor; this module changes nothing. For a
statement that only reads (SELECT or WITH, or the SELECT inside CREATE or ALTER VIEW) it also gives the
two SELECTs, as written and as fixed, so Compare can run both, read-only, and compare the rows.

``catalog`` is what the page knows: ``{"tables": [{"schema", "name"}], "columns": {"schema.name":
[{"name", "type"}]}}``. When the columns of a table the statement reads aren't in it, the answer lists
that table under ``needs`` and the page asks again once it has them.
"""

from __future__ import annotations

import re
from typing import Any

from sqlglot.tokens import TokenType

from . import guides
from ..sqlcompat.analyzer import Stmt, _line_starts, split
from ..sqlcompat.profiles import profile
from ..sqlcompat.rules import _close, _is_name, _name_at

RULES = ("SQ002", "SQ005")
_DATES = {"date", "datetime", "datetime2", "smalldatetime", "timestamp", "timestamp_ntz"}
_TABLE_AFTER = {"FROM", "JOIN"}
_CLAUSES = {"WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER", "ON", "GROUP", "ORDER", "HAVING",
            "UNION", "EXCEPT", "INTERSECT", "OPTION", "WITH"}
_STARTS = {"WHERE", "AND", "OR", "ON", "HAVING", "NOT", "WHEN"}
_WRITES = {"INTO", "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE", "EXEC", "EXECUTE"}
_ENDS = {"AND", "OR", "GROUP", "ORDER", "HAVING", "UNION", "EXCEPT", "INTERSECT", "OPTION", "WHERE", "ON"}
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PRINCIPLES = {rule_id: guides.load()[rule_id]["principle"] for rule_id in RULES}   # the docs' Principle: lines


def propose(query: str, target: str, findings: list[dict[str, Any]], catalog: dict[str, Any] | None) -> dict[str, Any]:
    """{fixes: [{index, rule_id, start, end, text, before, after, why_safe, principle, compare?}], needs: [...]}.

    ``findings`` are the page's findings ({rule_id, line, col}); ``index`` says which one a fix is for.
    ``start`` and ``end`` are character offsets in ``query``; ``text`` replaces that range."""
    if not isinstance(query, str):
        raise TypeError("query must be SQL text")
    tables, columns = _catalog(catalog)
    stmts, _problem = split(query, profile(target)["id"])
    starts = _line_starts(query)
    fixes: list[dict[str, Any]] = []
    needs: dict[str, dict[str, str]] = {}
    for index, f in enumerate(findings if isinstance(findings, list) else []):
        if not isinstance(f, dict) or f.get("rule_id") not in RULES:
            continue
        at = _offset(starts, f.get("line"), f.get("col"))
        found = _at(stmts, at)
        if not found:
            continue
        s, i = found
        fix = (_sq002 if f["rule_id"] == "SQ002" else _sq005)(s, i, tables, columns, needs)
        if fix:
            fix |= {"index": index, "rule_id": f["rule_id"], "before": query[fix["start"]:fix["end"]],
                    "principle": PRINCIPLES[f["rule_id"]]}
            pair = _reads(query, s, fix)
            if pair:
                fix["compare"] = pair
            fixes.append(fix)
    return {"fixes": fixes, "needs": sorted(needs.values(), key=lambda t: (t["schema"], t["name"]))}


def _catalog(catalog: Any) -> tuple[list[tuple[str, str]], dict[str, list[dict[str, Any]]]]:
    catalog = catalog if isinstance(catalog, dict) else {}
    tables = [(str(t.get("schema") or ""), str(t.get("name") or "")) for t in catalog.get("tables") or []
              if isinstance(t, dict)]
    cols = catalog.get("columns") if isinstance(catalog.get("columns"), dict) else {}
    return tables, {str(k).lower(): v for k, v in cols.items() if isinstance(v, list)}


def _offset(starts: list[int], line: Any, col: Any) -> int:
    if not isinstance(line, int) or not isinstance(col, int) or not 1 <= line <= len(starts) or col < 1:
        return -1
    return starts[line - 1] + col - 1


def _at(stmts: list[Stmt], at: int) -> tuple[Stmt, int] | None:
    for s in stmts:
        for i, t in enumerate(s.toks):
            if t.start == at:
                return s, i
    return None


def _sq002(s: Stmt, i: int, tables: list[tuple[str, str]], columns: dict[str, list[dict[str, Any]]],
           needs: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    toks = s.toks
    if toks[i].kw != "YEAR" or i + 1 >= len(toks) or toks[i + 1].type != TokenType.L_PAREN:
        return None
    if i == 0 or not (toks[i - 1].kw in _STARTS or toks[i - 1].type == TokenType.L_PAREN):
        return None   # 1 + YEAR(d) = 2027 and the like: the comparison isn't YEAR(d) = 2027 alone
    close = _close(s, i + 1)
    parts, after = _name_at(s, i + 2)
    if not parts or after != close or close + 2 >= len(toks):
        return None
    eq, num = toks[close + 1], toks[close + 2]
    if eq.type != TokenType.EQ or num.type != TokenType.NUMBER or not re.fullmatch(r"[1-9]\d{3}", num.raw) \
            or num.raw == "9999":
        return None
    nxt = toks[close + 3] if close + 3 < len(toks) else None
    if nxt is not None and not (nxt.kw in _ENDS or nxt.type in {TokenType.R_PAREN, TokenType.SEMICOLON}):
        return None   # YEAR(d) = 2026 + 1 and the like: leave it
    kind = _column_type(s, parts, tables, columns, needs)
    if kind is None or kind.split("(")[0].strip().lower() not in _DATES:
        return None
    col = "".join(t.raw if k == 0 else f".{t.raw}" for k, t in enumerate(parts))
    year = int(num.raw)
    text = f"({col} >= '{year}-01-01' AND {col} < '{year + 1}-01-01')"
    return {"start": toks[i].start, "end": num.end, "text": text,
            "why_safe": f"{parts[-1].text} is a {kind} column, so the range returns exactly the same rows."}


def _column_type(s: Stmt, parts: list[Any], tables: list[tuple[str, str]], columns: dict[str, list[dict[str, Any]]],
                 needs: dict[str, dict[str, str]]) -> str | None:
    """The type of the column ``parts`` names (``col`` or ``alias.col``) when exactly one table the
    statement reads has it, among those its qualifier names; None otherwise."""
    read = _tables_read(s, tables)
    if read is None or len(parts) > 2:
        return None
    missing = [t for t in read if f"{t[0]}.{t[1]}".lower() not in columns]
    for schema, name, _alias in missing:
        needs[f"{schema}.{name}".lower()] = {"schema": schema, "name": name}
    if missing:
        return None
    if len(parts) == 2:
        q = parts[0].text.lower()
        read = [t for t in read if q in {t[1].lower(), t[2].lower()}]
    column = parts[-1].text.lower()
    kinds = [str(c.get("type") or "") for t in read for c in columns[f"{t[0]}.{t[1]}".lower()]
             if isinstance(c, dict) and str(c.get("name") or "").lower() == column]
    return kinds[0] if len(kinds) == 1 else None


def _tables_read(s: Stmt, tables: list[tuple[str, str]]) -> list[tuple[str, str, str]] | None:
    """Each table after FROM or JOIN as (schema, name, alias), resolved in the catalog; None when any
    can't be (a derived table, a CTE, a function, a name the catalog doesn't have, FROM a, b)."""
    toks = s.toks
    out: list[tuple[str, str, str]] = []
    for i, t in enumerate(toks):
        if t.kw not in _TABLE_AFTER or i + 1 >= len(toks):
            continue
        parts, after = _name_at(s, i + 1)
        if not parts:
            return None
        if after < len(toks) and toks[after].kw == "AS":
            after += 1
        alias = toks[after].text if after < len(toks) and _is_name(toks[after]) and toks[after].kw not in _CLAUSES else ""
        nxt = after + 1 if alias else after
        if nxt < len(toks) and toks[nxt].type in {TokenType.COMMA, TokenType.L_PAREN}:
            return None
        names = [p.text for p in parts]
        if len(names) == 1:
            hits = [x for x in tables if x[1].lower() == names[0].lower()]
            hits = [x for x in hits if x[0].lower() == "dbo"] or hits
        elif len(names) == 2:
            hits = [x for x in tables if x[0].lower() == names[0].lower() and x[1].lower() == names[1].lower()]
        else:
            return None
        if len(hits) != 1:
            return None
        out.append((*hits[0], alias))
    return out or None


def _sq005(s: Stmt, i: int, tables: list[tuple[str, str]], columns: dict[str, list[dict[str, Any]]],
           needs: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    t = s.toks[i]
    if not _is_name(t):
        return None
    hits = [x for x in tables if x[1].lower() == t.text.lower()]
    if len(hits) != 1 or hits[0][0].lower() != "dbo":
        return None
    schema = hits[0][0] if _PLAIN.match(hits[0][0]) else f"[{hits[0][0]}]"
    return {"start": t.start, "end": t.end, "text": f"{schema}.{t.raw}",
            "why_safe": f"{t.text} is only in the {hits[0][0]} schema, the default one, so it names the same table."}


def _reads(query: str, s: Stmt, fix: dict[str, Any]) -> dict[str, str] | None:
    """The SELECT to run before and after the fix, when the statement only reads: the statement itself
    (SELECT or WITH), or the query of CREATE or ALTER VIEW. None for anything else."""
    toks = s.toks
    if not toks:
        return None
    first = toks[0].kw
    if first in {"SELECT", "WITH"}:
        start = toks[0].start
    elif first in {"CREATE", "ALTER"} and len(toks) > 2 and toks[1].kw == "VIEW":
        body = next((j for j in range(2, len(toks)) if toks[j].kw == "AS" and toks[j].depth == toks[0].depth), None)
        if body is None or body + 1 >= len(toks) or toks[body + 1].kw not in {"SELECT", "WITH"}:
            return None
        start = toks[body + 1].start
    else:
        return None
    if any(t.kw in _WRITES for t in toks if t.start >= start):
        return None   # WITH ... DELETE, SELECT ... INTO: not a query that only reads
    end = toks[-1].end
    if toks[-1].type == TokenType.SEMICOLON:
        end = toks[-2].end if len(toks) > 1 else end
    if not start <= fix["start"] < fix["end"] <= end:
        return None
    before = query[start:end]
    after = query[start:fix["start"]] + fix["text"] + query[fix["end"]:end]
    return {"before": before, "after": after}
