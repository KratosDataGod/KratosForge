"""Best practices for T-SQL in a Fabric Warehouse, SQL analytics endpoint or SQL database.

Separate from the compatibility rules (rules.py): those say what an engine supports, these say what
is likely slow, fragile or hard to maintain. They read the same statements and tokens and never run
the SQL. DELETE or UPDATE without WHERE isn't repeated here; compatibility rule DES-003 reports it.

Findings have the shape the other best-practice checks give (notebook_practices, model_bpa):
severity, rule, category, object, description and url, plus the rule's id, line and column.
``settings`` maps a rule id to ``{"enabled": bool, "severity": "Error"|"Warning"|"Info"}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlglot.tokens import TokenType

from .analyzer import Stmt, _line_starts, _pos, split
from .profiles import profile
from .rules import _calls, _close, _is_name, _name_at, _verbs

SEVERITIES = ("Error", "Warning", "Info")
TARGETS = ("warehouse", "endpoint", "sqldb")
DOCS = "sql-best-practices.md"   # each rule has a section there, anchored by its id: #sq001


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    category: str
    name: str
    description: str


RULES: tuple[Rule, ...] = (
    Rule("SQ001", "Warning", "Maintenance", "SELECT * in a view, procedure or function",
         "The columns change when the table does, which breaks reports and callers. Name the columns."),
    Rule("SQ002", "Warning", "Performance", "Function on a column in WHERE",
         "Wrapping a column in a function (YEAR(order_date) = 2026) stops the engine skipping data by that "
         "column. Compare the column to a range instead (order_date >= '2026-01-01' AND order_date < '2027-01-01')."),
    Rule("SQ003", "Warning", "Performance", "varchar(max) or varbinary(max) column",
         "MAX columns are stored and read less efficiently. Give the column the length its values need."),
    Rule("SQ004", "Info", "Maintenance", "SELECT ... INTO makes a table with guessed column types",
         "The new table's types come from the query. CREATE TABLE with explicit types, then INSERT, keeps them stable."),
    Rule("SQ005", "Info", "Maintenance", "Table named without its schema",
         "Without a schema the name depends on the caller's default schema. Write dbo.sales, not sales."),
    Rule("SQ006", "Warning", "Performance", "CROSS JOIN, or a JOIN with no ON",
         "Every row is matched with every other row. Join on the key columns, or confirm the product is intended."),
    Rule("SQ007", "Warning", "Performance", "Cursor or WHILE loop",
         "Row-by-row loops are slow on a distributed engine. Write one set-based statement."),
    Rule("SQ008", "Info", "Reliability", "ORDER BY in a view, or TOP without ORDER BY",
         "A view's ORDER BY doesn't order its callers' results, and TOP without ORDER BY returns any rows. "
         "Order in the final query."),
    Rule("SQ009", "Info", "Maintenance", "DISTINCT with GROUP BY",
         "GROUP BY already returns one row per group, so DISTINCT only adds work."),
    Rule("SQ010", "Warning", "Reliability", "INSERT ... SELECT *",
         "Columns are matched by position, so a column added to either table puts values in the wrong place. "
         "List the columns on both sides."),
)
_BY_ID = {r.id: r for r in RULES}

# Functions that, wrapped round a column in WHERE, hide the column from data skipping.
_WRAPS = {"YEAR", "MONTH", "DAY", "DATEPART", "DATENAME", "DATEADD", "DATEDIFF", "EOMONTH", "CAST", "CONVERT",
          "TRY_CAST", "TRY_CONVERT", "UPPER", "LOWER", "LTRIM", "RTRIM", "TRIM", "SUBSTRING", "LEFT", "RIGHT",
          "LEN", "ISNULL", "COALESCE", "FORMAT", "REPLACE", "ABS", "ROUND", "FLOOR", "CEILING"}
_COMPARES = {TokenType.EQ, TokenType.NEQ, TokenType.GT, TokenType.GTE, TokenType.LT, TokenType.LTE}
_COMPARE_WORDS = {"LIKE", "IN", "BETWEEN", "IS"}
_CLAUSE_END = {"GROUP", "ORDER", "HAVING", "UNION", "EXCEPT", "INTERSECT", "OPTION", "INSERT", "UPDATE", "DELETE",
               "MERGE", "SELECT", "END", "BEGIN", "IF", "ELSE", "WHILE", "RETURN", "DECLARE", "SET", "EXEC", "EXECUTE"}
_NAME_AFTER = {"FROM", "JOIN", "INTO", "UPDATE", "TABLE", "VIEW"}
_NOT_TABLES = {"SELECT", "VALUES", "STATISTICS", "OPENROWSET", "OPENJSON", "STRING_SPLIT", "SYS", "INFORMATION_SCHEMA"}


def rules() -> list[dict[str, Any]]:
    """Every SQL best-practice rule, as a governance template lists them."""
    return [{"id": r.id, "kind": "sql", "severity": r.severity, "category": r.category, "name": r.name,
             "description": r.description, "url": url(r.id)} for r in RULES]


def url(rule_id: str) -> str:
    """The docs section that explains a rule (open_doc opens it)."""
    return f"{DOCS}#{rule_id.lower()}"


def check(query: str, target: str = "warehouse", settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Findings for ``query`` on a T-SQL ``target``, worst first. Reads the SQL; never runs it."""
    if not isinstance(query, str):
        raise TypeError("query must be SQL text")
    if target not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)}")
    settings = settings if isinstance(settings, dict) else {}
    stmts, _problem = split(query, profile(target)["id"])
    lines = _line_starts(query)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for s in stmts:
        for rule_id, at in _statement(s):
            if (rule_id, at) in seen:
                continue
            seen.add((rule_id, at))
            f = _finding(rule_id, at, s.index + 1, lines, settings)
            if f:
                rows.append(f)
    rows.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["line"], f["col"], f["rule_id"]))
    return {"target": target, "statements": len(stmts),
            "counts": {s: sum(1 for f in rows if f["severity"] == s) for s in SEVERITIES}, "findings": rows}


def _finding(rule_id: str, at: int, statement: int, lines: list[int], settings: dict[str, Any]) -> dict[str, Any] | None:
    rule = _BY_ID[rule_id]
    own = settings.get(rule_id)
    own = own if isinstance(own, dict) else {}
    if own.get("enabled") is False:
        return None
    severity = own.get("severity") if own.get("severity") in SEVERITIES else rule.severity
    line, col = _pos(lines, at)
    return {"rule_id": rule_id, "severity": severity, "rule": rule.name, "category": rule.category,
            "object_type": "Statement", "object": f"statement {statement}, line {line}", "statement": statement,
            "line": line, "col": col, "description": rule.description, "url": url(rule_id)}


def _statement(s: Stmt) -> Iterator[tuple[str, int]]:
    toks = s.toks
    kws = s.kws
    defines = s.first()[0] in {"CREATE", "ALTER"} and s.has(("VIEW", "PROCEDURE", "PROC", "FUNCTION"))
    is_view = s.first()[0] in {"CREATE", "ALTER"} and s.has("VIEW")
    creates_table = s.has("CREATE", "TABLE")
    ctes = {toks[i - 1].kw for i in range(1, len(toks) - 1) if toks[i].kw == "AS" and toks[i + 1].type == TokenType.L_PAREN
            and _is_name(toks[i - 1])}
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.kw == "SELECT" and _star_follows(s, i):
            star = _star_at(s, i)
            if defines:
                yield "SQ001", toks[star].start
            if _insert_before(s, i):
                yield "SQ010", toks[star].start
        if t.kw in {"VARCHAR", "NVARCHAR", "VARBINARY"} and creates_table and i + 3 < len(toks) \
                and toks[i + 1].type == TokenType.L_PAREN and toks[i + 2].kw == "MAX":
            yield "SQ003", t.start
        cross = t.kw == "CROSS" and nxt is not None and nxt.kw == "JOIN"
        if cross or (t.kw == "JOIN" and (i == 0 or toks[i - 1].kw != "CROSS") and not _on_follows(s, i)):
            yield "SQ006", t.start
        if t.kw == "WHILE" or (t.kw == "CURSOR" and "DECLARE" in kws[:i]):
            yield "SQ007", t.start
        if t.kw == "WHERE":
            yield from (("SQ002", at) for at in _wrapped_columns(s, i))
        variable = i > 0 and toks[i - 1].raw.startswith("@") and toks[i - 1].end == t.start  # @from is a parameter
        if t.kw in _NAME_AFTER and nxt is not None and not variable and not (t.kw == "UPDATE" and _from_follows(s, i)):
            parts, after = _name_at(s, i + 1)
            if len(parts) == 1 and parts[0].kw not in _NOT_TABLES and parts[0].kw not in ctes \
                    and not (after < len(toks) and toks[after].type == TokenType.L_PAREN and t.kw in {"FROM", "JOIN"}) \
                    and toks[i + 1].type != TokenType.HASH and not parts[0].raw.startswith("@"):
                yield "SQ005", parts[0].start
    for i, verb in _verbs(s):
        if verb == "SELECT" and _select_into(s, i):
            yield "SQ004", toks[i].start
    depth0 = [i for i, t in enumerate(toks) if t.depth == toks[0].depth]
    order_by = [i for i in depth0 if toks[i].kw == "ORDER" and i + 1 < len(toks) and toks[i + 1].kw == "BY"]
    if is_view and order_by:
        yield "SQ008", toks[order_by[0]].start
    for i in depth0:
        if toks[i].kw == "TOP" and not order_by:
            yield "SQ008", toks[i].start
        if toks[i].kw == "DISTINCT" and any(toks[j].kw == "GROUP" for j in depth0 if j > i):
            yield "SQ009", toks[i].start


def _star_at(s: Stmt, i: int) -> int:
    """Index of the * a SELECT at ``i`` lists, past DISTINCT and TOP (n)."""
    j = i + 1
    toks = s.toks
    if j < len(toks) and toks[j].kw in {"DISTINCT", "ALL"}:
        j += 1
    if j < len(toks) and toks[j].kw == "TOP":
        j += 1
        if j < len(toks) and toks[j].type == TokenType.L_PAREN:
            j = _close(s, j) + 1
        else:
            j += 1
        if j < len(toks) and toks[j].kw == "PERCENT":
            j += 1
    return j


def _star_follows(s: Stmt, i: int) -> bool:
    j = _star_at(s, i)
    return j < len(s.toks) and s.toks[j].type == TokenType.STAR


def _insert_before(s: Stmt, i: int) -> bool:
    """Whether the SELECT at ``i`` feeds an INSERT (INSERT INTO t [(cols)] SELECT ...)."""
    for j in range(i - 1, -1, -1):
        t = s.toks[j]
        if t.depth < s.toks[i].depth:
            return False
        if t.depth == s.toks[i].depth and t.kw in {"INSERT"}:
            return True
        if t.depth == s.toks[i].depth and t.kw in {"SELECT", "FROM", "WHERE", "AS", "BEGIN", "VALUES"} | _CLAUSE_END:
            return False
    return False


def _on_follows(s: Stmt, i: int) -> bool:
    d = s.toks[i].depth
    for t in s.toks[i + 1:]:
        if t.depth < d:
            return False
        if t.depth == d and t.kw in {"ON", "USING"}:
            return True
        if t.depth == d and (t.kw in {"JOIN", "WHERE", "APPLY"} | _CLAUSE_END):
            return False
    return False


def _wrapped_columns(s: Stmt, where: int) -> Iterator[int]:
    toks = s.toks
    d = toks[where].depth
    end = next((j for j in range(where + 1, len(toks)) if toks[j].depth <= d and toks[j].kw in _CLAUSE_END
                or toks[j].depth < d), len(toks))
    for i, t in _calls(s):
        if not (where < i < end) or t.kw not in _WRAPS or t.depth != d:
            continue
        close = _close(s, i + 1)
        after = toks[close + 1] if close + 1 < len(toks) else None
        if after is None or not (after.type in _COMPARES or after.kw in _COMPARE_WORDS or after.kw == "NOT"):
            continue
        for k in range(i + 2, close):
            a, before = toks[k], toks[k - 1]
            variable = a.raw.startswith("@") or (before.raw.startswith("@") and before.end == a.start)
            if _is_name(a) and not variable and a.type != TokenType.STRING and a.kw not in _TYPE_WORDS:
                yield t.start
                break


# type names and date parts inside CAST(x AS int) / DATEPART(year, x) aren't columns
_TYPE_WORDS = {"INT", "BIGINT", "SMALLINT", "TINYINT", "BIT", "DECIMAL", "NUMERIC", "FLOAT", "REAL", "DATE", "TIME",
               "DATETIME2", "DATETIME", "VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "VARBINARY", "AS", "MAX", "YEAR",
               "QUARTER", "MONTH", "DAY", "WEEK", "HOUR", "MINUTE", "SECOND", "DAYOFYEAR", "WEEKDAY", "YY", "YYYY",
               "MM", "DD", "QQ", "WK", "HH", "MI", "SS", "DW", "DY", "NULL"}


def _from_follows(s: Stmt, i: int) -> bool:
    """UPDATE alias SET ... FROM table AS alias: the name after UPDATE is an alias, not a table."""
    d = s.toks[i].depth
    for t in s.toks[i + 1:]:
        if t.depth == d and t.kw == "FROM":
            return True
        if t.depth == d and t.kw in {"WHERE"} | (_CLAUSE_END - {"SET"}):
            return False
    return False


def _select_into(s: Stmt, i: int) -> bool:
    """SELECT ... INTO name ... at the SELECT's own depth (not INSERT INTO)."""
    d = s.toks[i].depth
    for j in range(i + 1, len(s.toks)):
        t = s.toks[j]
        if t.depth < d:
            return False
        if t.depth == d and t.kw == "INTO":
            return j + 1 < len(s.toks) and s.toks[j + 1].type != TokenType.HASH  # a #temp table is fine
        if t.depth == d and t.kw in {"FROM", "WHERE"} | _CLAUSE_END:
            return False
    return False
