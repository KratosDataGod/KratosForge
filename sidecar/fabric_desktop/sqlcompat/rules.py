"""The compatibility rules: each one names the targets it applies to, its source and its confidence.

A check reads one statement's tokens (and its sqlglot tree when it parsed) and yields ``Hit``s; the
analyzer turns them into diagnostics. Checks work on tokens first so they still run, with exact
positions, when a statement doesn't parse. Rules never rewrite SQL; ``action`` tells the person what
to change.

Severity:
- error: Microsoft documents the construct as unsupported on the target, or it isn't that engine's SQL;
- warning: it runs but may behave differently than expected, changes or deletes data, or is in preview;
- information: a documented difference worth knowing, not a problem by itself;
- unverified: the rules can't decide (the statement didn't parse, or the docs don't say).
Messages never quote string literals or parser output, so a password in the SQL can't end up in a
diagnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from sqlglot import exp
from sqlglot.tokens import TokenType

from .analyzer import Hit, Stmt, Tok
from .profiles import PROFILES

SP, WH, EP, DB, DK = "spark", "warehouse", "endpoint", "sqldb", "duckdb"
TSQL = (WH, EP, DB)
ALL = (SP, WH, EP, DB, DK)


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str | dict[str, str]  # one severity, or one per target
    category: str
    title: str
    reason: str
    action: str
    doc: str
    targets: tuple[str, ...]
    confidence: str = "high"

    def level(self, target: str) -> str:
        return self.severity if isinstance(self.severity, str) else self.severity[target]


def _r(id: str, severity, category: str, targets, title: str, reason: str, action: str, doc: str,
       confidence: str = "high") -> Rule:
    return Rule(id, severity, category, title, reason, action, doc, tuple(targets), confidence)


_RULES = [
    # ---- syntax that belongs to another engine
    _r("SYN-001", "error", "syntax", TSQL, "LIMIT isn't T-SQL",
       "{engine} doesn't support LIMIT.", "Use SELECT TOP (n) ... ORDER BY, or OFFSET ... FETCH NEXT n ROWS ONLY.", "top"),
    _r("SYN-002", "error", "syntax", (SP, DK), "TOP is T-SQL, not {target}",
       "{engine} doesn't support SELECT TOP.", "Put LIMIT n at the end of the query.", "spark"),
    _r("SYN-003", "error", "syntax", (SP, DK), "[Square brackets] are T-SQL names",
       "{engine} doesn't read [name] as a column or table name.",
       "Quote names with backticks in Spark SQL (`Order Id`) or double quotes in DuckDB (\"Order Id\").", "spark"),
    _r("SYN-004", "error", "syntax", (SP,), "@variables are T-SQL",
       "Spark SQL has no @variables.", "Write the value into the query, or use a notebook to pass parameters.", "spark"),
    _r("SYN-005", "error", "syntax", (SP, DK), "GO isn't SQL",
       "GO is a batch separator for SQL Server tools. {engine} reads it as part of the query.",
       "End statements with a semicolon instead.", "spark"),
    _r("SYN-006", "error", "syntax", TSQL, "`Backticks` aren't T-SQL",
       "{engine} doesn't read `name` as a name.", "Quote names with [square brackets] or \"double quotes\".", "surface"),
    _r("SYN-007", "unverified", "syntax", ALL, "Not checked: couldn't read this statement",
       "This statement couldn't be read as {target} SQL, so the rules couldn't check it. It may be a typo, or SQL "
       "the reader doesn't know.", "Check the statement near this point, or test it in {engine}.", "local", "medium"),
    _r("SYN-008", "warning", "syntax", TSQL + (DK,), "\"Double quotes\" make a name, not text",
       "In {engine}, \"...\" is a column or table name. Comparing with it compares with a column, or fails if there "
       "is none.", "Use 'single quotes' for text.", "surface", "medium"),
    _r("SYN-009", "error", "syntax", TSQL, "{construct} is Spark SQL, not T-SQL",
       "{engine} doesn't support {construct}.", "{fix}", "surface"),
    _r("SYN-010", "error", "syntax", (DB,), "{construct} isn't in SQL database T-SQL",
       "{construct} is Fabric Warehouse T-SQL; the Azure SQL Database engine behind a Fabric SQL database doesn't "
       "have it.", "{fix}", "sqldb", "medium"),

    # ---- functions
    _r("FN-001", "unverified", "function", ALL, "Not checked: function {name}",
       "{name} isn't a function the rules know for {target}. It may be a user-defined function, or one {engine} "
       "doesn't have.", "Check that {name} exists in {engine}.", "local", "low"),
    _r("FN-002", "error", "function", (SP,), "ISNULL takes one argument in Spark SQL",
       "In Spark SQL, isnull(x) returns true or false; it doesn't replace NULLs like T-SQL's ISNULL(a, b).",
       "Use COALESCE(a, b).", "spark_functions"),
    _r("FN-003", "error", "function", (SP,), "{name} is a T-SQL function",
       "Spark SQL doesn't have {name}.", "Use {fix}.", "spark_functions"),
    _r("FN-004", "error", "function", TSQL, "{name} isn't a T-SQL function",
       "{engine} doesn't have {name}.", "Use {fix}.", "surface"),
    _r("FN-005", {WH: "warning", EP: "warning"}, "function", (WH, EP), "{name} is in preview",
       "Regular expression functions are in preview in {engine}.", "Test the query in {engine} before relying on it.",
       "surface"),
    _r("FN-006", "error", "function", (WH, EP), "{name} isn't supported",
       "{engine} doesn't support {name}.", "{fix}", "surface"),

    # ---- data types (Warehouse tables)
    _r("TYP-001", "error", "type", (WH,), "{name} can't be used in a Warehouse table",
       "{engine} tables don't support {name}.", "Use {fix}.", "types"),
    _r("TYP-002", "warning", "type", (WH,), "The new table would get a {name} column",
       "This statement creates a table from a query, and {name} isn't supported in {engine} tables.",
       "Cast to {fix} instead.", "types", "medium"),
    _r("TYP-003", "error", "type", (WH,), "{name}({digits}) keeps more than 6 digits of seconds",
       "{engine} stores at most 6 digits of fractional seconds.", "Use {name}(6).", "types"),
    _r("TYP-004", "information", "type", (WH,), "uniqueidentifier is stored as binary",
       "{engine} stores uniqueidentifier as binary, and the SQL analytics endpoint can't read it.",
       "Use char(36) if the column must be read through the SQL analytics endpoint or Direct Lake.", "types"),
    _r("TYP-005", "information", "type", (WH,), "{name}(max) holds at most 16 MB",
       "In {engine}, {name}(max) stores up to 16 MB per value.", "Keep values under 16 MB.", "types"),

    # ---- DDL
    _r("DDL-001", "error", "ddl", (WH, EP), "{name} aren't supported",
       "{engine} doesn't support {name}.", "{fix}", "tables"),
    _r("DDL-002", "error", "ddl", (WH,), "Computed columns aren't supported",
       "{engine} tables can't have computed columns.", "Compute the value in a view, or when loading the table.",
       "tables"),
    _r("DDL-003", "error", "ddl", (WH, EP), "Unique indexes aren't supported",
       "{engine} doesn't support unique indexes.", "Use a UNIQUE NONCLUSTERED ... NOT ENFORCED constraint, and "
       "check uniqueness when loading.", "tables"),
    _r("DDL-004", "error", "ddl", (WH,), "{name} must be {need}",
       "{engine} only allows {need} {name} constraints.", "Write {need} {name}.", "tables"),
    _r("DDL-005", "error", "ddl", (WH, EP), "## global temp tables aren't supported",
       "{engine} supports session #temp tables only.", "Use a #temp table.", "temp"),
    _r("DDL-006", "information", "ddl", (WH,), "#temp table without distribution",
       "In {engine}, a #temp table is non-distributed by default.",
       "Add WITH (DISTRIBUTION = ROUND_ROBIN) for a distributed temp table, which Microsoft recommends.", "temp"),
    _r("DDL-007", {WH: "error"}, "ddl", (WH,), "{name}",
       "{reason}", "{fix}", "surface"),
    _r("DDL-008", "error", "ddl", (WH,), "Name '{name}' isn't allowed",
       "{engine} names can't contain / or \\ or end with a period.", "Rename it.", "tables"),
    _r("DDL-009", "error", "ddl", (WH,), "More than 1,024 columns",
       "{engine} tables have at most 1,024 columns; this one has {count}.", "Split the table.", "tables"),
    _r("DDL-010", "error", "ddl", (WH, EP), "Multi-column statistics aren't supported",
       "{engine} supports statistics on one column only.", "Create one statistics object per column.", "surface"),
    _r("DDL-011", "error", "ddl", (WH, EP), "CREATE USER isn't supported",
       "{engine} doesn't support CREATE USER.", "Share the item in the Fabric workspace, or grant access with GRANT.",
       "surface"),
    _r("DDL-012", "unverified", "ddl", (WH,), "Not checked: {name}",
       "The {engine} documentation doesn't say whether {name} is supported.", "Test it in {engine}.", "tables", "low"),
    _r("DDL-013", "error", "ddl", (WH, EP), "External tables aren't supported",
       "{engine} doesn't support CREATE EXTERNAL TABLE.", "Read files with OPENROWSET, or load them with COPY INTO.",
       "surface"),
    _r("DDL-014", "error", "ddl", (DB,), "Column name '{name}' isn't allowed",
       "Fabric SQL database column names can't contain spaces or , ; {{ }} ( ) = tabs or newlines.",
       "Rename the column.", "sqldb"),
    _r("DDL-015", "error", "ddl", TSQL, "Logins aren't supported",
       "{engine} doesn't support logins.", "Use Microsoft Entra users and Fabric workspace roles.", "sqldb"),

    # ---- SQL analytics endpoint
    _r("EP-001", "error", "endpoint", (EP,), "The SQL analytics endpoint can't change tables",
       "Lakehouse tables are read-only through the SQL analytics endpoint: no CREATE, ALTER or DROP TABLE, and no "
       "INSERT, UPDATE, DELETE, MERGE or TRUNCATE.",
       "Change the table in the lakehouse (a notebook, Save as table, or a pipeline), or use a Fabric Warehouse.",
       "surface"),

    # ---- SQL database
    _r("DB-001", "error", "database", (DB,), "{name} isn't supported",
       "Fabric SQL database doesn't support {name}.", "{fix}", "sqldb"),

    # ---- queries and DML
    _r("DML-001", "error", "dml", (WH, EP), "{name} isn't supported",
       "{engine} doesn't support {name}.", "{fix}", "surface"),
    _r("DML-002", {WH: "error", EP: "error", SP: "unverified"}, "dml", (WH, EP, SP), "Recursive CTE",
       "{why}", "{fix}", "surface"),
    _r("DML-003", "warning", "dml", (WH, EP), "Nested CTEs are in preview",
       "{engine} supports a WITH inside a CTE as a preview feature.", "Test it in {engine}, or move the inner CTE "
       "to the outer WITH.", "surface"),
    _r("DML-004", "error", "dml", (WH, EP), "System and user tables in one query",
       "{engine} doesn't run a query that reads both system views (sys., INFORMATION_SCHEMA) and user tables.",
       "Query them separately, for example into a #temp table first.", "surface"),
    _r("DML-005", "unverified", "dml", (WH, EP), "Not checked: {name}",
       "{engine} supports only some query and table hints.", "Check the hint against the {engine} documentation, or "
       "remove it.", "surface", "low"),
    _r("DML-006", "information", "dml", (WH,), "MERGE and write conflicts",
       "In {engine}, two transactions that change the same table at the same time can conflict, and one fails.",
       "Retry the statement if it fails with a write-write conflict (error 24556 or 24706).", "transactions"),
    _r("DML-007", {WH: "information", EP: "information", DB: "warning"}, "dml", TSQL, "Three-part name",
       "{why}", "{fix}", "crossdb"),
    _r("DML-008", {WH: "unverified", EP: "unverified", DB: "error"}, "dml", TSQL, "Four-part name",
       "{why}", "{fix}", "crossdb", "medium"),

    # ---- transactions
    _r("TRX-001", "warning", "transaction", (WH, EP), "Isolation level can't be changed",
       "{engine} always uses snapshot isolation. SET TRANSACTION ISOLATION LEVEL is listed as unsupported, and "
       "inside a transaction the level is ignored.", "Remove the statement.", "transactions"),
    _r("TRX-002", "error", "transaction", (WH, EP), "{name} aren't supported",
       "{engine} doesn't support {name}.", "{fix}", "transactions"),
    _r("TRX-003", "error", "transaction", TSQL, "Distributed transactions aren't supported",
       "{engine} doesn't support distributed transactions.", "Use a normal BEGIN TRANSACTION within one item.",
       "transactions"),
    _r("TRX-004", "information", "transaction", (WH,), "Snapshot isolation and conflicts",
       "{engine} runs transactions with snapshot isolation; two transactions changing the same table can conflict.",
       "Keep transactions short, and retry one that fails with a write-write conflict.", "transactions"),

    # ---- meaning
    _r("SEM-001", "warning", "semantics", ALL, "= NULL is never true",
       "Comparing with NULL using = or <> gives unknown, so no rows match.", "Use IS NULL or IS NOT NULL.", "null"),
    _r("SEM-002", "information", "semantics", (WH, EP), "Text compares case-sensitively",
       "{engine} uses the Latin1_General_100_BIN2_UTF8 collation by default, so 'abc' and 'ABC' are different.",
       "Use UPPER() or LOWER() on both sides, or a case-insensitive warehouse, if case shouldn't matter.", "collation"),
    _r("SEM-003", "warning", "semantics", TSQL, "Whole-number division drops the fraction",
       "In {engine}, dividing two whole numbers gives a whole number ({example}).",
       "Write one number as a decimal (7.0 / 2) or CAST it.", "division"),
    _r("SEM-004", "information", "semantics", ALL, "NOT IN with a subquery",
       "If the subquery returns a NULL, NOT IN matches no rows.", "Use NOT EXISTS, or filter NULLs in the subquery.",
       "notin"),
    _r("SEM-005", "information", "semantics", TSQL, "The clock is UTC",
       "{engine} gives the current time in UTC, and the time zone can't be changed.",
       "Convert with AT TIME ZONE when you need local time.", "sqldb", "medium"),
    _r("SEM-006", "warning", "semantics", (WH,), "Collation {name}",
       "{engine} supports the collations Latin1_General_100_BIN2_UTF8 and Latin1_General_100_CI_AS_KS_WS_SC_UTF8.",
       "Use one of those.", "collation", "medium"),

    # ---- statements that change or delete data
    _r("DES-001", "warning", "destructive", ALL, "DROP {name} deletes it",
       "This statement deletes the {name} and, for a table, its data.", "Make sure this is the right object. "
       "KratosForge never runs it without asking.", "local"),
    _r("DES-002", "warning", "destructive", ALL, "TRUNCATE deletes every row",
       "This statement deletes all rows of the table.", "Make sure this is the right table.", "local"),
    _r("DES-003", "warning", "destructive", ALL, "{name} without WHERE changes every row",
       "There is no WHERE clause, so {name} applies to every row of the table.",
       "Add a WHERE clause, or confirm that every row should change.", "local"),
    _r("DES-004", "warning", "destructive", (SP, DK), "{name} replaces the table",
       "This statement replaces all the rows of the table.", "Make sure that's intended.", "local"),
    _r("DES-005", "warning", "destructive", ALL, "DROP COLUMN deletes the column's data",
       "This statement removes the column and its values.", "Make sure this is the right column.", "local"),

    # ---- running on this PC instead of Fabric
    _r("LOC-001", "warning", "local", (SP,), "Changes data in Fabric, not here",
       "On this PC, a statement that changes tables runs against a throwaway copy and never reaches the lakehouse. "
       "In Fabric Spark it changes the table.", "Run it in Fabric (a notebook or Run in Fabric), or use Save as table "
       "to write a result.", "local"),
    _r("LOC-002", "warning", "local", (SP,), "Regular expressions differ on this PC",
       "Fabric Spark uses Java regular expressions; this PC runs them with DuckDB's RE2. Lookarounds and "
       "backreferences don't work here, and some patterns match differently.", "Compare the results in Fabric, or "
       "keep to simple patterns.", "duckdb_regex", "medium"),
    _r("LOC-003", "warning", "local", (SP,), "Date pattern is translated on this PC",
       "{name} uses a Spark date pattern. On this PC the pattern is translated for DuckDB, and unusual pattern "
       "letters can give different text.", "Compare the results in Fabric.", "spark_datetime", "medium"),
    _r("LOC-004", "information", "local", (SP, DK), "{name} gives a different value each run",
       "{name} doesn't return the same value on every run, so results here and in Fabric won't match.",
       "Don't compare these values between runs.", "local"),
    _r("LOC-005", "information", "local", (SP,), "Decimal results can differ",
       "Spark and DuckDB use different precision and rounding rules for decimal arithmetic.",
       "Compare the results in Fabric if exact decimals matter.", "local", "medium"),
    _r("LOC-006", "warning", "local", (SP,), "{name} has no fixed order",
       "{name} returns values in no fixed order, so the result here and in Fabric can differ.",
       "Sort the values (array_sort) or use an ordered window if order matters.", "spark_functions", "medium"),
    _r("LOC-007", "information", "local", (SP,), "Each run starts fresh",
       "On this PC every run starts a new session, so a temp view, cached table or setting doesn't carry over to the "
       "next run.", "Keep the whole script in one run.", "local"),
    _r("LOC-008", "information", "local", (DK,), "DuckDB SQL runs only on this PC",
       "No Fabric engine runs DuckDB SQL as written. Local results don't prove the query works in Fabric.",
       "Rewrite it as Spark SQL or T-SQL before using it in Fabric.", "local"),
    _r("LOC-009", "information", "local", (SP, DK), "{name} reads files on this PC",
       "{name} reads files from this PC, which Fabric doesn't have.", "Read lakehouse tables instead.", "local"),
]

RULES: dict[str, Rule] = {r.id: r for r in _RULES}
assert all(set(r.targets) <= set(PROFILES) for r in _RULES)
assert all(isinstance(r.severity, str) or set(r.severity) == set(r.targets) for r in _RULES)


# ---------- token helpers

_NAME = re.compile(r"[A-Za-z_][\w$]*")
_NAME_TYPES = {TokenType.VAR, TokenType.IDENTIFIER}
# words that end a name when reading table names
_CLAUSE = {"SELECT", "FROM", "WHERE", "JOIN", "ON", "GROUP", "ORDER", "HAVING", "UNION", "AS", "SET", "VALUES",
           "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER", "WITH", "USING", "WHEN", "THEN", "AND", "OR", "LIMIT",
           "INTO", "OPTION", "FOR", "PIVOT", "UNPIVOT", "LATERAL", "WINDOW", "QUALIFY", "EXCEPT", "INTERSECT"}


def _is_name(t: Tok) -> bool:
    if t.type in _NAME_TYPES:
        return True
    return bool(_NAME.fullmatch(t.raw)) and t.kw not in _CLAUSE


def _name_at(s: Stmt, i: int) -> tuple[list[Tok], int]:
    """The dotted name starting at token ``i`` (T-SQL #temp included) and the index after it."""
    toks, parts = s.toks, []
    if i < len(toks) and toks[i].type == TokenType.HASH:
        j = i
        while j < len(toks) and toks[j].type == TokenType.HASH:
            j += 1
        if j < len(toks) and _is_name(toks[j]) and toks[j].start == toks[j - 1].end:
            parts.append(toks[j])
            return parts, j + 1
        return [], i
    while i < len(toks) and _is_name(toks[i]):
        parts.append(toks[i])
        if i + 2 < len(toks) and toks[i + 1].type == TokenType.DOT:
            i += 2
        elif i + 2 < len(toks) and toks[i + 1].type == TokenType.DOT and toks[i + 2].type == TokenType.DOT:
            i += 1  # db..table
        else:
            return parts, i + 1
    return parts, i


def _hit(rule: str, a: Tok, b: Tok | None = None, **detail) -> Hit:
    return Hit(rule, a.start, (b or a).end, detail)


def _calls(s: Stmt) -> Iterator[tuple[int, Tok]]:
    """Function calls: a word followed by ``(``, not a table, a definition or a qualified name."""
    toks = s.toks
    for i, t in enumerate(toks[:-1]):
        if toks[i + 1].type != TokenType.L_PAREN or not t.kw or not _NAME.fullmatch(t.raw):
            continue
        prev = toks[i - 1] if i else None
        if prev is not None and (prev.type == TokenType.DOT or prev.kw in {"INTO", "TABLE", "UPDATE",
                                                                          "FUNCTION", "PROCEDURE", "PROC", "VIEW",
                                                                          "EXISTS", "REFERENCES", "INDEX"}):
            continue
        yield i, t


def _close(s: Stmt, i: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``i`` (the last token if unbalanced)."""
    d = s.toks[i].depth
    for j in range(i + 1, len(s.toks)):
        if s.toks[j].type == TokenType.R_PAREN and s.toks[j].depth == d:
            return j
    return len(s.toks) - 1


def _args(s: Stmt, i: int) -> int:
    """Number of arguments of the call whose ``(`` is at ``i``."""
    j = _close(s, i)
    if j == i + 1:
        return 0
    d = s.toks[i].depth + 1
    return 1 + sum(1 for t in s.toks[i + 1:j] if t.type == TokenType.COMMA and t.depth == d)


def _starts(s: Stmt) -> list[int]:
    """Indexes where a statement begins inside ``s``: its first token, and statements inside BEGIN ... END."""
    out = [0]
    for i, t in enumerate(s.toks[1:], 1):
        prev = s.toks[i - 1]
        if prev.kw in {"BEGIN", "ELSE", "END"} or (prev.kw == "AS" and s.first()[0] in {"CREATE", "ALTER"}
                                                     and s.has(("PROCEDURE", "PROC", "FUNCTION", "TRIGGER"))):
            out.append(i)
        elif prev.type == TokenType.R_PAREN and prev.depth == 0 and s.first()[0] == "WITH":
            out.append(i)
    return out


def _verbs(s: Stmt) -> list[tuple[int, str]]:
    """(index, verb) for each statement start, with the verb upper-cased (SELECT, INSERT, CREATE, ...)."""
    return [(i, s.toks[i].kw) for i in _starts(s) if s.toks[i].kw]


def _where_follows(s: Stmt, i: int) -> bool:
    stop = {"INSERT", "UPDATE", "DELETE", "MERGE", "END", "BEGIN", "IF", "ELSE", "DECLARE", "RETURN", "COMMIT",
            "SELECT", "EXEC", "EXECUTE", "PRINT"}
    for t in s.toks[i + 1:]:
        if t.depth == 0 and t.kw == "WHERE":
            return True
        if t.depth == 0 and t.kw in stop:
            return False
    return False


def _context(s: Stmt) -> list[str]:
    """For each token, the clause it sits in for comparisons: 'test' (WHERE, ON, WHEN, ...) or 'other'."""
    tests = {"WHERE", "ON", "HAVING", "WHEN", "AND", "OR", "NOT", "IF", "WHILE", "QUALIFY"}
    others = {"SET", "DECLARE", "SELECT", "VALUES", "UPDATE", "PROCEDURE", "PROC", "FUNCTION", "THEN", "ELSE"}
    ctx, out = "other", []
    for t in s.toks:
        if t.kw in tests:
            ctx = "test"
        elif t.kw in others:
            ctx = "other"
        out.append(ctx)
    return out


_CHECKS: list[Callable[[Stmt], Iterable[Hit]]] = []


def check(fn):
    _CHECKS.append(fn)
    return fn


def run_checks(stmt: Stmt, target: str) -> list[Hit]:
    """Every rule's findings for ``stmt`` on ``target``."""
    hits = []
    for fn in _CHECKS:
        for h in fn(stmt):
            rule = RULES[h.rule]
            if target in rule.targets and h.detail.get("only") in (None, target):
                hits.append(h)
    if stmt.error and not any(h.detail.get("severity", RULES[h.rule].level(target)) == "error" for h in hits):
        at = stmt.error_at if stmt.error_at is not None else stmt.start
        at = min(max(at, stmt.start), max(stmt.start, stmt.end - 1))
        tok = next((t for t in stmt.toks if t.end > at), stmt.toks[-1])
        hits.append(Hit("SYN-007", tok.start, tok.end, {}))
    # a call another rule already explains (TOP (5) in Spark SQL) isn't also "not checked"
    explained = {(h.start, h.end) for h in hits if h.rule != "FN-001"}
    hits = [h for h in hits if h.rule != "FN-001" or not any(a <= h.start < b for a, b in explained)]
    for h in hits:
        h.detail.pop("only", None)
    return hits


# ---------- syntax

@check
def _limit(s: Stmt):
    for i, t in enumerate(s.toks):
        if t.kw == "LIMIT" and (not i or s.toks[i - 1].type != TokenType.DOT) and i + 1 < len(s.toks) \
                and s.toks[i + 1].type in (TokenType.NUMBER, TokenType.PARAMETER, TokenType.VAR):
            yield _hit("SYN-001", t, s.toks[i + 1])


@check
def _top(s: Stmt):
    for i, t in enumerate(s.toks[1:], 1):
        if t.kw == "TOP" and s.toks[i - 1].kw in {"SELECT", "DISTINCT", "ALL"}:
            yield _hit("SYN-002", t)


@check
def _brackets(s: Stmt):
    toks = s.toks
    after_name = {"FROM", "JOIN", "INTO", "UPDATE", "TABLE"}
    for i, t in enumerate(toks):
        if t.type != TokenType.L_BRACKET:
            continue
        prev = toks[i - 1] if i else None
        # arr[0] and map['k'] index a value; [ after a name, ) or ] is that, not a name (except after TOP (n))
        if prev is not None and (prev.type == TokenType.R_BRACKET or
                                 (prev.type == TokenType.R_PAREN and not _after_top(s, i - 1)) or
                                 (_is_name(prev) and prev.kw not in _CLAUSE | {"BY", "DISTINCT"})):
            continue
        j = i + 1
        while j < len(toks) and toks[j].type != TokenType.R_BRACKET:
            j += 1
        inner = toks[i + 1:j]
        if j >= len(toks) or not inner or not all(_is_name(x) or x.kw.isalnum() for x in inner):
            continue
        name_place = prev is not None and (prev.type == TokenType.DOT or prev.kw in after_name)
        # DuckDB reads SELECT [a] as a list; only a [name] where a table name goes is surely T-SQL there
        yield _hit("SYN-003", t, toks[j], only=None if name_place else SP)


def _after_top(s: Stmt, close: int) -> bool:
    """Whether the ``)`` at ``close`` ends TOP (n)."""
    d = s.toks[close].depth
    for k in range(close - 1, -1, -1):
        if s.toks[k].type == TokenType.L_PAREN and s.toks[k].depth == d:
            return k > 0 and s.toks[k - 1].kw == "TOP"
    return False


@check
def _variables(s: Stmt):
    for i, t in enumerate(s.toks[:-1]):
        if t.type == TokenType.PARAMETER and t.raw == "@" and s.toks[i + 1].start == t.end and _is_name(s.toks[i + 1]):
            yield _hit("SYN-004", t, s.toks[i + 1])


@check
def _go(s: Stmt):
    for t in s.toks:
        if t.kw == "GO" and _alone(s, t):
            yield _hit("SYN-005", t)


def _alone(s: Stmt, t: Tok) -> bool:
    # the statement's text is enough: a GO alone on its line has a newline (or the statement edge) on both sides
    rel_start, rel_end = t.start - s.start, t.end - s.start
    before = s.text[:rel_start].rsplit("\n", 1)[-1]
    after = s.text[rel_end:].split("\n", 1)[0]
    return not before.strip() and not after.strip()


@check
def _backticks(s: Stmt):
    for t in s.toks:
        if t.raw.startswith("`"):
            yield _hit("SYN-006", t)
            return


@check
def _double_quoted_values(s: Stmt):
    for i, t in enumerate(s.toks[1:], 1):
        if t.type == TokenType.IDENTIFIER and t.raw.startswith('"') and \
                s.toks[i - 1].type in (TokenType.EQ, TokenType.NEQ, TokenType.LIKE, TokenType.LT, TokenType.GT):
            yield _hit("SYN-008", t)


_SPARK_SYNTAX = [
    (("INSERT", "OVERWRITE"), "INSERT OVERWRITE", "TRUNCATE TABLE and then INSERT, or DELETE and INSERT in one transaction."),
    (("CREATE", "OR", "REPLACE", "TABLE"), "CREATE OR REPLACE TABLE",
     "DROP TABLE IF EXISTS and then CREATE TABLE (or CREATE TABLE AS SELECT)."),
    (("USING", ("DELTA", "PARQUET", "CSV", "JSON")), "USING <format>", "Remove it: T-SQL tables have no format clause."),
    (("PARTITIONED", "BY"), "PARTITIONED BY", "Remove it: Warehouse tables aren't partitioned by you."),
    (("LATERAL", "VIEW"), "LATERAL VIEW", "Use CROSS APPLY with OPENJSON or STRING_SPLIT."),
    (("RLIKE",), "RLIKE", "Use REGEXP_LIKE (preview in Fabric Warehouse) or LIKE."),
]


_SECURABLE_CLASSES = {"SCHEMA", "OBJECT", "DATABASE", "ROLE", "USER", "TYPE", "LOGIN", "CERTIFICATE"}


@check
def _spark_syntax(s: Stmt):
    for words, construct, fix in _SPARK_SYNTAX:
        for i in s.find(*words):
            yield _hit("SYN-009", s.toks[i], s.toks[i + len(words) - 1], construct=construct, fix=fix)
    for i, t in enumerate(s.toks):
        # GRANT, DENY or REVOKE ... ON SCHEMA::sales is T-SQL's securable class, not a cast.
        if t.type == TokenType.DCOLON and not (i >= 2 and s.toks[i - 1].kw in _SECURABLE_CLASSES and s.toks[i - 2].kw == "ON"):
            yield _hit("SYN-009", t, construct="The :: cast", fix="Use CAST(value AS type).")
    for i in _spark_types(s):
        t = s.toks[i]
        yield _hit("SYN-009", t, construct=f"The {t.raw.upper()} type", fix=f"Use {_SPARK_TYPES[t.kw]}.")


_SPARK_TYPES = {"STRING": "varchar(n)", "BOOLEAN": "bit"}  # Spark SQL types T-SQL doesn't have: T-SQL alternative


def _spark_types(s: Stmt) -> Iterator[int]:
    """Where a Spark SQL type is used: CAST(x AS string), CONVERT(string, x), or a column definition."""
    toks = s.toks
    for i, t in enumerate(toks):
        if t.kw not in _SPARK_TYPES or not i:
            continue
        prev, after = toks[i - 1], toks[i + 1] if i + 1 < len(toks) else None
        cast = prev.kw == "AS" and after is not None and after.type == TokenType.R_PAREN
        convert = prev.type == TokenType.L_PAREN and i >= 2 and toks[i - 2].kw in ("CONVERT", "TRY_CONVERT")
        if cast or convert:
            yield i
    yield from (j for j, _ in _column_defs(s) if toks[j].kw in _SPARK_TYPES)


@check
def _warehouse_only(s: Stmt):
    for i in s.find("QUALIFY"):
        yield _hit("SYN-010", s.toks[i], construct="QUALIFY", fix="Filter the window function in an outer query.")
    for i in s.find(("GROUP", "ORDER"), "BY", "ALL"):
        yield _hit("SYN-010", s.toks[i], s.toks[i + 2], construct=f"{s.toks[i].kw} BY ALL",
                   fix="List the columns.")


# ---------- functions

_TSQL_ONLY = {  # name: Spark SQL alternative
    "GETDATE": "current_timestamp()", "SYSDATETIME": "current_timestamp()", "GETUTCDATE": "current_timestamp()",
    "SYSUTCDATETIME": "current_timestamp()", "CONVERT": "CAST(value AS type)", "IIF": "if(condition, a, b) or CASE",
    "CHARINDEX": "instr() or locate()", "PATINDEX": "regexp_instr() or locate()", "NEWID": "uuid()",
    "SCOPE_IDENTITY": "a key column you generate", "OBJECT_ID": "SHOW TABLES or the catalog",
    "DATALENGTH": "octet_length()", "STUFF": "overlay()", "EOMONTH": "last_day()",
}
_SPARK_ONLY = {  # name: T-SQL alternative
    "NVL": "ISNULL(a, b) or COALESCE(a, b)", "NVL2": "IIF(a IS NULL, c, b)", "IFNULL": "ISNULL(a, b) or COALESCE(a, b)",
    "DATE_FORMAT": "FORMAT(value, pattern) or CONVERT with a style", "COLLECT_LIST": "STRING_AGG",
    "COLLECT_SET": "STRING_AGG over SELECT DISTINCT", "EXPLODE": "CROSS APPLY OPENJSON or STRING_SPLIT",
    "SIZE": "a count over OPENJSON", "ARRAY_CONTAINS": "EXISTS over OPENJSON",
    "NOW": "GETDATE() or SYSDATETIME()", "TO_DATE": "CAST(value AS date) or CONVERT(date, value, style)",
    "TO_TIMESTAMP": "CAST(value AS datetime2(6))", "DATE_TRUNC": "DATETRUNC(part, value)",
    "SUBSTR": "SUBSTRING", "LCASE": "LOWER", "UCASE": "UPPER", "INSTR": "CHARINDEX",
    "STRFTIME": "FORMAT(value, pattern)", "REGEXP_MATCHES": "REGEXP_LIKE (preview)",
    "REGEXP_EXTRACT": "REGEXP_SUBSTR (preview)", "SPLIT": "STRING_SPLIT",
}
_REGEX_PREVIEW = {"REGEXP_LIKE", "REGEXP_REPLACE", "REGEXP_SUBSTR", "REGEXP_INSTR", "REGEXP_COUNT"}
_WAREHOUSE_UNSUPPORTED_FN = {
    "PREDICT": "Score the model in a notebook (Fabric's PREDICT for Spark) and write the results to a table.",
    "REGEXP_MATCHES": "Use REGEXP_LIKE (preview) in a WHERE clause.",
    "REGEXP_SPLIT_TO_TABLE": "Use STRING_SPLIT for a fixed separator.",
    "VECTOR_DISTANCE": "Compute distances in a notebook; vector functions aren't supported.",
    "VECTOR_NORM": "Compute in a notebook; vector functions aren't supported.",
    "VECTOR_NORMALIZE": "Compute in a notebook; vector functions aren't supported.",
    "VECTOR_SEARCH": "Search vectors in a notebook; vector functions aren't supported.",
}
_LOCAL_REGEX = {"RLIKE", "REGEXP", "REGEXP_EXTRACT", "REGEXP_EXTRACT_ALL", "REGEXP_REPLACE", "REGEXP_LIKE",
                "REGEXP_INSTR", "REGEXP_SUBSTR", "REGEXP_COUNT", "SPLIT"}
_DATE_PATTERN = {"DATE_FORMAT": 2, "TO_DATE": 2, "TO_TIMESTAMP": 2, "FROM_UNIXTIME": 2, "UNIX_TIMESTAMP": 2,
                 "TO_UNIX_TIMESTAMP": 2, "TRY_TO_TIMESTAMP": 2}
_NONDETERMINISTIC = {"RAND", "RANDOM", "RANDN", "UUID", "NOW", "CURRENT_TIMESTAMP", "CURRENT_DATE",
                     "MONOTONICALLY_INCREASING_ID", "SHUFFLE", "GEN_RANDOM_UUID"}
_NOW_WORDS = {"CURRENT_TIMESTAMP", "CURRENT_DATE", "NOW"}  # also valid without parentheses
_UNORDERED = {"COLLECT_LIST", "COLLECT_SET", "FIRST", "LAST", "ARRAY_AGG", "ANY_VALUE", "FIRST_VALUE",
              "LAST_VALUE"}
_FILE_READERS = {"READ_PARQUET", "READ_CSV", "READ_CSV_AUTO", "READ_JSON", "READ_JSON_AUTO", "READ_TEXT", "GLOB",
                 "PARQUET_SCAN", "READ_BLOB", "READ_NDJSON", "PARQUET_METADATA"}
_TSQL_NOW = {"GETDATE", "SYSDATETIME", "GETUTCDATE", "SYSUTCDATETIME", "SYSDATETIMEOFFSET", "CURRENT_TIMESTAMP"}
_LISTED = set(_TSQL_ONLY) | set(_SPARK_ONLY) | _REGEX_PREVIEW | set(_WAREHOUSE_UNSUPPORTED_FN) | _LOCAL_REGEX | \
    set(_DATE_PATTERN) | _NONDETERMINISTIC | _UNORDERED | _FILE_READERS | _TSQL_NOW | {"ISNULL"}


@check
def _functions(s: Stmt):
    for i, t in _calls(s):
        name = t.kw
        if name == "ISNULL" and _args(s, i + 1) == 2:
            yield _hit("FN-002", t)
        if name in _TSQL_ONLY:
            # IIF: Spark has if(); whether a runtime also accepts iif isn't documented, so it's a warning
            yield _hit("FN-003", t, name=name, fix=_TSQL_ONLY[name], **({"severity": "warning"} if name == "IIF" else {}))
        if name in _SPARK_ONLY and name not in _WAREHOUSE_UNSUPPORTED_FN:
            yield _hit("FN-004", t, name=name, fix=_SPARK_ONLY[name])
        if name in _REGEX_PREVIEW:
            yield _hit("FN-005", t, name=name)
        if name in _WAREHOUSE_UNSUPPORTED_FN:
            yield _hit("FN-006", t, name=name, fix=_WAREHOUSE_UNSUPPORTED_FN[name])
        if name in _LOCAL_REGEX and (name != "SPLIT" or _regex_pattern(s, i + 1)):
            yield _hit("LOC-002", t)
        if name in _DATE_PATTERN and _args(s, i + 1) >= _DATE_PATTERN[name]:
            yield _hit("LOC-003", t, name=name)
        if name in _NONDETERMINISTIC:
            yield _hit("LOC-004", t, name=name)
        if name in _UNORDERED:
            yield _hit("LOC-006", t, name=name)
        if name in _FILE_READERS:
            yield _hit("LOC-009", t, name=name)
        if name in _TSQL_NOW:
            yield _hit("SEM-005", t)
    for i, t in enumerate(s.toks):
        nxt = s.toks[i + 1] if i + 1 < len(s.toks) else None
        if t.kw in _NOW_WORDS - {"NOW"} and (nxt is None or nxt.type != TokenType.L_PAREN):
            yield _hit("LOC-004", t, name=t.kw)
            if t.kw == "CURRENT_TIMESTAMP":
                yield _hit("SEM-005", t)
        if t.kw in ("RLIKE", "REGEXP") and i and s.toks[i - 1].type != TokenType.DOT and \
                (nxt is None or nxt.type != TokenType.L_PAREN):
            yield _hit("LOC-002", t)
        if t.type == TokenType.STRING and i and s.toks[i - 1].kw in {"FROM", "JOIN"} and \
                re.search(r"\.(parquet|csv|json|ndjson|tsv)(\b|$)", t.text, re.I):
            yield _hit("LOC-009", t, name="A file path")


def _regex_pattern(s: Stmt, paren: int) -> bool:
    """Whether split()'s pattern (its second argument) uses regular expression characters."""
    d = s.toks[paren].depth + 1
    end = _close(s, paren)
    commas = [k for k in range(paren + 1, end) if s.toks[k].type == TokenType.COMMA and s.toks[k].depth == d]
    if not commas or commas[0] + 1 >= end:
        return False
    pat = s.toks[commas[0] + 1]
    return pat.type != TokenType.STRING or bool(re.search(r"[\\.\[\]()*+?^$|{}]", pat.text))


# Built-in functions sqlglot reads as unknown ("anonymous") calls. A name here isn't reported as unchecked.
_KNOWN_SPARK = set("""
ACOSH ASINH ATANH BASE64 BIN BIT_COUNT BIT_GET BITMAP_COUNT BROUND BTRIM CARDINALITY CBRT CHAR_LENGTH CHR CONV
CRC32 CSC CSV_TO_JSON CURRENT_CATALOG CURRENT_DATABASE CURRENT_SCHEMA CURRENT_TIMEZONE CURRENT_USER DATE_FROM_UNIX_DATE
DECODE ELT ENCODE EVERY FACTORIAL FIND_IN_SET FORMAT_NUMBER FORMAT_STRING FROM_CSV FROM_UTC_TIMESTAMP GETBIT HASH HEX
HYPOT INPUT_FILE_NAME ISNAN JAVA_METHOD JSON_ARRAY_LENGTH JSON_OBJECT_KEYS JSON_TUPLE KURTOSIS LEVENSHTEIN LOG1P
MAKE_DT_INTERVAL MAKE_INTERVAL MAKE_TIMESTAMP MAKE_YM_INTERVAL MAP_CONCAT MAP_CONTAINS_KEY MAP_ENTRIES MAP_FILTER
MAP_FROM_ENTRIES MAP_VALUES MAP_ZIP_WITH MASK MONOTONICALLY_INCREASING_ID NANVL NEGATIVE NTH_VALUE OCTET_LENGTH OVERLAY
PARSE_URL PERCENT_RANK PMOD POSITIVE PRINTF RAISE_ERROR REFLECT REGR_AVGX REGR_COUNT REGR_SLOPE REPEAT RINT SCHEMA_OF_CSV
SCHEMA_OF_JSON SEC SENTENCES SHA SHA1 SHIFTLEFT SHIFTRIGHT SHIFTRIGHTUNSIGNED SIGNUM SKEWNESS SOUNDEX SPACE SPARK_PARTITION_ID
SPLIT_PART STR_TO_MAP SUBSTRING_INDEX TIMESTAMP_MICROS TIMESTAMP_MILLIS TIMESTAMP_SECONDS TO_BINARY TO_CHAR TO_CSV
TO_NUMBER TO_UTC_TIMESTAMP TRY_ADD TRY_AVG TRY_DIVIDE TRY_ELEMENT_AT TRY_MULTIPLY TRY_SUBTRACT TRY_SUM TRY_TO_NUMBER
TYPEOF UNBASE64 UNHEX UNIX_DATE UNIX_MICROS UNIX_MILLIS UNIX_SECONDS URL_DECODE URL_ENCODE USER VERSION WIDTH_BUCKET
WINDOW XPATH XPATH_STRING XXHASH64 ZIP_WITH INLINE INLINE_OUTER POSEXPLODE POSEXPLODE_OUTER EXPLODE_OUTER STACK
""".split())
_KNOWN_TSQL = set("""
APP_NAME APPROX_PERCENTILE_CONT APPROX_PERCENTILE_DISC ASCII CHECKSUM CHECKSUM_AGG CHOOSE COL_LENGTH COL_NAME
COMPRESS CONCAT_WS CONNECTIONPROPERTY CURRENT_REQUEST_ID CURSOR_STATUS DATABASEPROPERTYEX DATALENGTH DATE_BUCKET
DATEDIFF_BIG DATEFROMPARTS DATENAME DATETIME2FROMPARTS DATETIMEFROMPARTS DATETIMEOFFSETFROMPARTS DB_ID DB_NAME DECOMPRESS
DENSE_RANK DIFFERENCE ERROR_LINE ERROR_MESSAGE ERROR_NUMBER ERROR_PROCEDURE ERROR_SEVERITY ERROR_STATE FORMATMESSAGE
GENERATE_SERIES GET_FILESTREAM_TRANSACTION_CONTEXT GETANSINULL HOST_ID HOST_NAME IDENT_CURRENT IDENT_INCR IDENT_SEED
IDENTITY ISDATE ISJSON ISNUMERIC JSON_ARRAY JSON_MODIFY JSON_OBJECT JSON_PATH_EXISTS JSON_QUERY NCHAR NEWSEQUENTIALID
NTILE OBJECT_DEFINITION OBJECT_NAME OBJECT_SCHEMA_NAME OPENJSON OPENROWSET ORIGINAL_LOGIN PARSE PARSENAME PERMISSIONS
QUOTENAME REPLICATE REVERSE ROWCOUNT_BIG SCHEMA_ID SCHEMA_NAME SESSION_CONTEXT SESSION_USER SIGN SMALLDATETIMEFROMPARTS
SOUNDEX SPACE SQL_VARIANT_PROPERTY SQUARE STATS_DATE STDEVP STRING_ESCAPE STRING_SPLIT STR SUSER_ID SUSER_NAME SUSER_SID
SUSER_SNAME SWITCHOFFSET SYSTEM_USER TIMEFROMPARTS TODATETIMEOFFSET TRANSLATE TRIGGER_NESTLEVEL TRY_PARSE TYPE_ID
TYPE_NAME UNICODE USER_ID USER_NAME VARP XACT_STATE CURRENT_TIMEZONE CURRENT_TIMEZONE_ID GREATEST LEAST EDIT_DISTANCE
JSON_ARRAYAGG JSON_OBJECTAGG JSON_CONTAINS DATETRUNC
""".split())
_KNOWN_DUCKDB = set("""
LIST_VALUE LIST_TRANSFORM LIST_FILTER LIST_REDUCE LIST_SORT LIST_DISTINCT LIST_CONTAINS LIST_EXTRACT STRING_SPLIT
STR_SPLIT REGEXP_FULL_MATCH STRPTIME EPOCH EPOCH_MS MAKE_DATE MAKE_TIMESTAMP DATE_PART DATE_DIFF DATE_SUB TIME_BUCKET
ARRAY_LENGTH LEN STRUCT_PACK MAP_FROM_ENTRIES JSON_EXTRACT JSON_EXTRACT_STRING HASH MD5 SHA256 RANGE GENERATE_SERIES
UNNEST ARG_MAX ARG_MIN BOOL_AND BOOL_OR FSUM HISTOGRAM MODE PRODUCT QUANTILE_CONT QUANTILE_DISC REPEAT REVERSE
""".split())
_KNOWN = {"spark": _KNOWN_SPARK, "warehouse": _KNOWN_TSQL, "endpoint": _KNOWN_TSQL, "sqldb": _KNOWN_TSQL,
          "duckdb": _KNOWN_DUCKDB}


@check
def _unknown_functions(s: Stmt):
    if s.ast is None:
        return
    used: set[int] = set()
    known = _KNOWN[s.target]
    for node in s.ast.find_all(exp.Anonymous):
        name = str(node.name or "").upper()
        if not name or name in _LISTED or name in known or isinstance(node.parent, exp.Dot):
            continue
        for i, t in _calls(s):
            if t.kw == name and i not in used:
                used.add(i)
                yield _hit("FN-001", t, name=name)
                break


# ---------- data types and DDL

_TYPE_ALTERNATIVES = {
    "MONEY": "decimal(19, 4)", "SMALLMONEY": "decimal(10, 4)", "DATETIME": "datetime2(6)",
    "SMALLDATETIME": "datetime2(0)", "DATETIMEOFFSET": "datetime2(6), converted with AT TIME ZONE",
    "NCHAR": "char (the UTF-8 collation stores any character)", "NVARCHAR": "varchar (the UTF-8 collation stores any character)",
    "TEXT": "varchar(max)", "NTEXT": "varchar(max)", "IMAGE": "varbinary(max)", "TINYINT": "smallint",
    "GEOGRAPHY": "varbinary (well-known binary) or varchar (well-known text)",
    "GEOMETRY": "varbinary (well-known binary) or varchar (well-known text)", "JSON": "varchar(max)",
    "XML": "varchar(max), if the XML can be kept as text", "SQL_VARIANT": "a column of one type",
    "HIERARCHYID": "varchar holding the path", "VECTOR": "varbinary or varchar; vectors aren't supported",
}
_CREATE_TABLE = ("CREATE", "TABLE")


def _column_defs(s: Stmt) -> Iterator[tuple[int, Tok]]:
    """(index of the type token, column name token) for each column defined in CREATE TABLE or ALTER TABLE ADD."""
    toks = s.toks
    kws = s.kws
    if kws[:2] == ["CREATE", "TABLE"]:
        _, k = _name_at(s, 2)
        if k < len(toks) and toks[k].type == TokenType.L_PAREN and toks[k].depth == 0:
            end = _close(s, k)
            j = k + 1
            while j < end:
                if _is_name(toks[j]) and toks[j].kw not in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK",
                                                             "INDEX", "PERIOD"} and j + 1 < end:
                    yield j + 1, toks[j]
                # skip to the next comma at this level
                while j < end and not (toks[j].type == TokenType.COMMA and toks[j].depth == 1):
                    j += 1
                j += 1
    elif kws[:2] == ["ALTER", "TABLE"]:
        for a in s.find("ADD"):
            j = a + 1
            while j < len(toks):
                if toks[j].kw in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "COLUMN"}:
                    if toks[j].kw != "COLUMN":
                        break
                    j += 1
                if j + 1 < len(toks) and _is_name(toks[j]):
                    yield j + 1, toks[j]
                while j < len(toks) and not (toks[j].type == TokenType.COMMA and toks[j].depth == 0):
                    j += 1
                j += 1


def _is_ctas(s: Stmt) -> bool:
    toks = s.toks
    if s.kws[:2] == ["CREATE", "TABLE"]:
        _, k = _name_at(s, 2)
        while k < len(toks):  # skip a column list and WITH (options)
            if toks[k].type == TokenType.L_PAREN:
                k = _close(s, k) + 1
            elif toks[k].kw == "WITH" and k + 1 < len(toks) and toks[k + 1].type == TokenType.L_PAREN:
                k = _close(s, k + 1) + 1
            else:
                break
        return k < len(toks) and toks[k].kw == "AS"
    return bool(s.find("INTO")) and s.first()[0] in ("SELECT", "WITH") and \
        any(s.toks[i - 1].depth == 0 and s.toks[i].depth == 0 for i in s.find("INTO") if i)


@check
def _types(s: Stmt):
    for j, col in _column_defs(s):
        t = s.toks[j]
        if t.kw in _TYPE_ALTERNATIVES:
            yield _hit("TYP-001", t, name=t.raw.lower(), fix=_TYPE_ALTERNATIVES[t.kw])
        elif t.kw in ("DATETIME2", "TIME") and j + 3 < len(s.toks) and s.toks[j + 1].type == TokenType.L_PAREN \
                and s.toks[j + 2].type == TokenType.NUMBER and s.toks[j + 2].text.isdigit() and int(s.toks[j + 2].text) > 6:
            yield _hit("TYP-003", t, s.toks[j + 3], name=t.raw.lower(), digits=s.toks[j + 2].text)
        elif t.kw == "UNIQUEIDENTIFIER":
            yield _hit("TYP-004", t)
        elif t.kw in ("VARCHAR", "VARBINARY") and j + 3 < len(s.toks) and s.toks[j + 2].kw == "MAX":
            yield _hit("TYP-005", t, s.toks[j + 3], name=t.raw.lower())
    if _is_ctas(s):
        for i, t in enumerate(s.toks[1:], 1):
            prev = s.toks[i - 1]
            in_convert = prev.type == TokenType.L_PAREN and i >= 2 and s.toks[i - 2].kw == "CONVERT"
            if t.kw in _TYPE_ALTERNATIVES and (prev.kw == "AS" or in_convert):
                yield _hit("TYP-002", t, name=t.raw.lower(), fix=_TYPE_ALTERNATIVES[t.kw])


_NOT_SUPPORTED_OBJECTS = [
    (("CREATE", "TRIGGER"), "Triggers", "Run the logic in the statement or pipeline that changes the table."),
    (("CREATE", "OR", "ALTER", "TRIGGER"), "Triggers", "Run the logic in the statement or pipeline that changes the table."),
    (("CREATE", "SYNONYM"), "Synonyms", "Use a view, or the full name."),
    (("CREATE", "MATERIALIZED", "VIEW"), "Materialized views", "Use a view, or a table you refresh."),
    (("CREATE", "SEQUENCE"), "Sequences", "Use an IDENTITY column, or ROW_NUMBER() over the new rows."),
    (("NEXT", "VALUE", "FOR"), "Sequences", "Use an IDENTITY column, or ROW_NUMBER() over the new rows."),
    (("CREATE", "TYPE"), "User-defined types", "Use a built-in type."),
]


@check
def _ddl(s: Stmt):
    toks, kws = s.toks, s.kws
    for words, name, fix in _NOT_SUPPORTED_OBJECTS:
        for i in s.find(*words):
            yield _hit("DDL-001", toks[i], toks[i + len(words) - 1], name=name, fix=fix)
    # computed columns: name AS (expression) in a column list
    if kws[:2] == ["CREATE", "TABLE"]:
        for j, col in _column_defs(s):
            if toks[j].kw == "AS":
                yield _hit("DDL-002", col, toks[j])
    for i in s.find("CREATE", "UNIQUE"):
        if s.has("INDEX"):
            yield _hit("DDL-003", toks[i], toks[i + 1])
    for i in s.find("CREATE", ("CLUSTERED", "NONCLUSTERED", "COLUMNSTORE", "INDEX")):
        if s.has("INDEX") and "UNIQUE" not in kws:
            yield _hit("DDL-012", toks[i], toks[i + 1], name="CREATE INDEX")
    if kws[:2] in (["CREATE", "TABLE"], ["ALTER", "TABLE"]):
        yield from _constraints(s)
        for i in s.find("DEFAULT"):
            yield _hit("DDL-012", toks[i], name="DEFAULT constraints")
        names = [p for j, p in _column_defs(s)]
        if len(names) > 1024:
            yield _hit("DDL-009", toks[0], toks[1], count=f"{len(names):,}")
        for p in names:
            if p.type == TokenType.IDENTIFIER and re.search(r"[\s,;{}()=]", p.text):
                yield _hit("DDL-014", p, name=p.text[:64])
    if kws[:2] == ["CREATE", "TABLE"] or kws[:2] == ["CREATE", "VIEW"] or kws[:2] == ["CREATE", "SCHEMA"]:
        parts, _ = _name_at(s, 2)
        for p in parts:
            if "/" in p.text or "\\" in p.text or p.text.endswith("."):
                yield _hit("DDL-008", p, name=p.text[:64])
    for i, t in enumerate(toks[:-2]):
        if t.type == TokenType.HASH and toks[i + 1].type == TokenType.HASH and toks[i + 1].start == t.end:
            yield _hit("DDL-005", t, toks[i + 2])
    if kws[:2] == ["CREATE", "TABLE"] and len(toks) > 2 and toks[2].type == TokenType.HASH and \
            not (len(toks) > 3 and toks[3].type == TokenType.HASH) and not s.has("DISTRIBUTION"):
        yield _hit("DDL-006", toks[0], toks[3] if len(toks) > 3 else toks[2])
    if kws[:2] == ["ALTER", "TABLE"]:
        yield from _alter_table(s)
    for i in s.find("CREATE", "STATISTICS"):
        on = s.find("ON", start=i)
        if on:
            k = on[0] + 1
            _, k = _name_at(s, k)
            if k < len(toks) and toks[k].type == TokenType.L_PAREN and _args(s, k) > 1:
                yield _hit("DDL-010", toks[i], toks[i + 1])
    for i in s.find("CREATE", "USER"):
        yield _hit("DDL-011", toks[i], toks[i + 1])
    for i in s.find("CREATE", "EXTERNAL", "TABLE"):
        yield _hit("DDL-013", toks[i], toks[i + 2])
    for i in s.find(("CREATE", "ALTER", "DROP"), "LOGIN"):
        yield _hit("DDL-015", toks[i], toks[i + 1])


def _constraints(s: Stmt):
    toks = s.toks
    for i, t in enumerate(toks):
        if t.kw == "PRIMARY" and i + 1 < len(toks) and toks[i + 1].kw == "KEY":
            kind, need, first, last = "PRIMARY KEY", "NONCLUSTERED ... NOT ENFORCED", t, toks[i + 1]
        elif t.kw == "UNIQUE" and not (i and toks[i - 1].kw == "CREATE"):
            kind, need, first, last = "UNIQUE", "NONCLUSTERED ... NOT ENFORCED", t, t
        elif t.kw == "REFERENCES":
            kind, need, first, last = "FOREIGN KEY", "NOT ENFORCED", t, t
            for k in range(i - 1, -1, -1):  # report from FOREIGN KEY when it's written out
                if toks[k].kw == "FOREIGN":
                    first = toks[k]
                    break
                if toks[k].type == TokenType.COMMA and toks[k].depth <= t.depth:
                    break
        else:
            continue
        d = t.depth
        span = []
        for k in range(i, len(toks)):
            if k > i and (toks[k].depth < d or (toks[k].type == TokenType.COMMA and toks[k].depth == d)):
                break
            span.append(toks[k].kw)
        enforced = any(span[k] == "NOT" and span[k + 1] == "ENFORCED" for k in range(len(span) - 1))
        ok = enforced and ("NONCLUSTERED" in span or kind == "FOREIGN KEY")
        if not ok:
            yield _hit("DDL-004", first, last, name=kind, need=need)


def _alter_table(s: Stmt):
    toks = s.toks
    _, k = _name_at(s, 2)
    if k >= len(toks):
        return
    op = toks[k]
    nxt = toks[k + 1].kw if k + 1 < len(toks) else ""
    if op.kw == "ADD":
        if nxt in {"PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT"}:
            # a named constraint: CHECK and DEFAULT aren't among the allowed ones
            if nxt == "CONSTRAINT" and k + 3 < len(toks) and toks[k + 3].kw in {"CHECK", "DEFAULT"}:
                yield _hit("DDL-007", op, toks[k + 3], name=f"ADD {toks[k + 3].kw} isn't supported",
                           reason="Fabric Data Warehouse's ALTER TABLE can add nullable columns and NOT ENFORCED keys, "
                                  "nothing else.", fix="Check the rule when loading the table instead.")
            return
        if nxt == "CHECK":
            yield _hit("DDL-007", op, toks[k + 1], name="ADD CHECK isn't supported",
                       reason="Fabric Data Warehouse's ALTER TABLE can add nullable columns and NOT ENFORCED keys, "
                              "nothing else.", fix="Check the rule when loading the table instead.")
            return
        for i in range(k + 1, len(toks) - 1):
            if toks[i].kw == "NOT" and toks[i + 1].kw == "NULL" and toks[i].depth == 0:
                yield _hit("DDL-007", toks[i], toks[i + 1], name="New columns must allow NULL",
                           reason="Fabric Data Warehouse can only add nullable columns to an existing table.",
                           fix="Add the column as NULL, fill it, and check it when loading.")
    elif op.kw == "DROP":
        if nxt == "COLUMN":
            yield _hit("DES-005", op, toks[k + 1])
    elif op.kw == "ALTER" and nxt == "COLUMN":
        yield Hit("DDL-007", op.start, toks[k + 1].end,
                  {"name": "ALTER COLUMN is in preview", "reason": "Fabric Data Warehouse supports ALTER COLUMN as a "
                   "preview feature.", "fix": "Test it in a non-production warehouse first.", "severity": "warning"})
    elif op.kw:
        yield _hit("DDL-007", op, name=f"ALTER TABLE ... {op.kw} isn't supported",
                   reason="Fabric Data Warehouse's ALTER TABLE can add nullable columns, drop columns, and add or drop "
                          "NOT ENFORCED keys. ALTER COLUMN is in preview.",
                   fix="Create a new table with CREATE TABLE AS SELECT and swap it in.")


_DB_UNSUPPORTED = [
    (("EXECUTE", "AS"), "EXECUTE AS", "Run the code as the signed-in user."),
    (("EXEC", "AS"), "EXECUTE AS", "Run the code as the signed-in user."),
    (("CREATE", "APPLICATION", "ROLE"), "Application roles", "Use database roles and Microsoft Entra groups."),
    (("BACKUP", ("DATABASE", "LOG")), "BACKUP", "Fabric backs up SQL databases automatically; restore to a point in time from the Fabric portal."),
    (("CREATE", "SERVER", "ROLE"), "Server roles", "Use database roles."),
    (("CREATE", ("QUEUE", "CONTRACT")), "Service Broker", "Use a pipeline or an eventstream."),
    (("SEND", "ON", "CONVERSATION"), "Service Broker", "Use a pipeline or an eventstream."),
    (("BEGIN", "DIALOG"), "Service Broker", "Use a pipeline or an eventstream."),
    (("LEDGER", "="), "Ledger tables", "Use a normal table."),
    (("MEMORY_OPTIMIZED", "="), "In-memory tables", "Use a normal table."),
    (("ENCRYPTED", "WITH"), "Always Encrypted", "Protect the column another way, for example with dynamic data masking."),
    (("DBCC", "TRACEON"), "Trace flags", "Remove it."),
    (("CREATE", "DATABASE", "AUDIT", "SPECIFICATION"), "Auditing", "Use Fabric's own audit logs."),
]
_DB_UNSUPPORTED_PROCS = {
    "SP_CDC_ENABLE_DB": ("Change data capture", "Use mirroring, which Fabric SQL databases do automatically."),
    "SP_CDC_ENABLE_TABLE": ("Change data capture", "Use mirroring, which Fabric SQL databases do automatically."),
    "SP_ADD_JOB": ("SQL Agent", "Schedule the work with a Fabric pipeline."),
    "SP_ADD_JOBSTEP": ("SQL Agent", "Schedule the work with a Fabric pipeline."),
    "SP_ADD_JOBSCHEDULE": ("SQL Agent", "Schedule the work with a Fabric pipeline."),
}


@check
def _sqldb(s: Stmt):
    toks = s.toks
    for words, name, fix in _DB_UNSUPPORTED:
        for i in s.find(*words):
            yield _hit("DB-001", toks[i], toks[i + len(words) - 1], name=name, fix=fix)
    for i, t in enumerate(toks):
        if t.kw in _DB_UNSUPPORTED_PROCS:
            name, fix = _DB_UNSUPPORTED_PROCS[t.kw]
            yield _hit("DB-001", t, name=name, fix=fix)


# ---------- the SQL analytics endpoint and writes in general

_WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}


def _writes(s: Stmt) -> Iterator[tuple[Tok, Tok, bool]]:
    """(first token, last token, is_temp) of each statement that changes a table."""
    toks = s.toks
    for i, verb in _verbs(s):
        if verb in _WRITE_VERBS:
            k = i + 1
            while k < len(toks) and toks[k].kw in {"INTO", "FROM", "TABLE", "OVERWRITE", "TOP"}:
                k += 1
            yield toks[i], toks[min(k, len(toks) - 1)], k < len(toks) and toks[k].type == TokenType.HASH
        elif verb in ("CREATE", "ALTER", "DROP") and i + 1 < len(toks):
            rest = [t.kw for t in toks[i + 1:i + 5]]
            if "TABLE" in rest[:3] and "EXTERNAL" not in rest[:2]:
                at = i + 1 + rest.index("TABLE")
                temp = at + 1 < len(toks) and toks[at + 1].type == TokenType.HASH
                yield toks[i], toks[at], temp
        elif verb in ("SELECT", "WITH"):
            for k in s.find("INTO", start=i):
                if toks[k].depth == 0 and k + 1 < len(toks) and not (k and toks[k - 1].kw == "INSERT"):
                    yield toks[k], toks[k], toks[k + 1].type == TokenType.HASH
                    break


@check
def _endpoint(s: Stmt):
    for a, b, temp in _writes(s):
        if not temp:
            yield _hit("EP-001", a, b)
            return


# ---------- queries and DML

@check
def _queries(s: Stmt):
    toks = s.toks
    for i in s.find("BULK", "INSERT"):
        yield _hit("DML-001", toks[i], toks[i + 1], name="BULK INSERT", fix="Load files with COPY INTO or OPENROWSET.")
    for i in s.find("SET", "ROWCOUNT"):
        yield _hit("DML-001", toks[i], toks[i + 1], name="SET ROWCOUNT", fix="Use TOP (n).")
    for i in s.find("FOR", "XML"):
        yield _hit("DML-001", toks[i], toks[i + 1], name="FOR XML", fix="Return rows, or build text with STRING_AGG.")
    for i in s.find("FOR", "JSON"):
        if toks[i].depth > 0:
            yield _hit("DML-001", toks[i], toks[i + 1], name="FOR JSON inside a subquery",
                       fix="Use FOR JSON only on the outermost SELECT.")
    if s.first()[0] == "MERGE" or any(v == "MERGE" for _, v in _verbs(s)):
        yield _hit("DML-006", toks[0])
    yield from _ctes(s)
    yield from _hints(s)
    yield from _names(s)


def _ctes(s: Stmt):
    """Recursive CTEs (WITH RECURSIVE, or a CTE that names itself) and WITH nested inside a CTE."""
    toks = s.toks
    fix = "Unroll the levels you need with joins, or compute the hierarchy in a notebook."
    for w, t in enumerate(toks):
        if t.kw != "WITH":
            continue
        j = w + 1
        recursive = j < len(toks) and toks[j].kw == "RECURSIVE"
        j += recursive
        found = 0
        while j < len(toks) and _is_name(toks[j]):
            name = toks[j]
            k = j + 1
            if k < len(toks) and toks[k].type == TokenType.L_PAREN:
                k = _close(s, k) + 1
            if k >= len(toks) or toks[k].kw != "AS":
                break
            k += 1
            if k < len(toks) and toks[k].kw == "MATERIALIZED":
                k += 1
            elif k + 1 < len(toks) and toks[k].kw == "NOT" and toks[k + 1].kw == "MATERIALIZED":
                k += 2
            if k >= len(toks) or toks[k].type != TokenType.L_PAREN:
                break
            found += 1
            end = _close(s, k)
            key = (name.text or name.raw).lower()
            if not recursive and any(_is_name(b) and (b.text or b.raw).lower() == key for b in toks[k + 1:end]):
                yield _hit("DML-002", name, why="This CTE refers to itself, which makes it recursive. Fabric Data "
                           "Warehouse and the SQL analytics endpoint don't support recursive queries; Spark SQL "
                           "support depends on your Fabric runtime version.", fix=fix)
            j = end + 1
            if j < len(toks) and toks[j].type == TokenType.COMMA:
                j += 1
            else:
                break
        if not found:
            continue  # WITH (hint), WITH MARK, WITH ROLLUP, ...
        if recursive:
            yield _hit("DML-002", t, toks[w + 1], why="Fabric Data Warehouse and the SQL analytics endpoint don't "
                       "support recursive queries. Spark SQL support depends on your Fabric runtime version.", fix=fix)
        if t.depth > 0:
            yield _hit("DML-003", t)


def _hints(s: Stmt):
    toks = s.toks
    for i in s.find("OPTION"):
        if i + 1 < len(toks) and toks[i + 1].type == TokenType.L_PAREN:
            yield _hit("DML-005", toks[i], toks[_close(s, i + 1)], name="query hint OPTION (...)")
    if s.first()[0] != "CREATE":
        for i in s.find("WITH"):
            if i and _is_name(toks[i - 1]) and i + 1 < len(toks) and toks[i + 1].type == TokenType.L_PAREN \
                    and toks[i - 1].kw not in _CLAUSE:
                yield _hit("DML-005", toks[i], toks[_close(s, i + 1)], name="table hint WITH (...)")
    for i in s.find(("HASH", "LOOP", "MERGE", "REMOTE"), "JOIN"):
        yield _hit("DML-005", toks[i], toks[i + 1], name=f"join hint {toks[i].kw} JOIN")


_SYSTEM = {"SYS", "INFORMATION_SCHEMA"}


def _names(s: Stmt):
    toks = s.toks
    system, user = None, None
    for i, t in enumerate(toks[:-1]):
        if t.kw not in {"FROM", "JOIN", "INTO", "UPDATE", "TABLE", "USING", "MERGE", "APPLY"} or toks[i + 1].type == TokenType.L_PAREN:
            continue
        k = i + 1
        if toks[k].kw == "INTO":
            k += 1
        parts, _ = _name_at(s, k)
        if not parts:
            continue
        if len(parts) == 3:
            yield _hit("DML-007", parts[0], parts[-1],
                       why="A three-part name reads another item: in a Fabric Warehouse or SQL analytics endpoint, "
                           "an item in the same workspace. In a Fabric SQL database, other items are reached through "
                           "their SQL analytics endpoint, read-only.",
                       fix="Make sure the other item is in the same workspace, and that only reading is needed.")
        elif len(parts) >= 4:
            yield _hit("DML-008", parts[0], parts[-1],
                       why="A four-part name goes through a linked server. Fabric documents three-part names only; a "
                           "Fabric SQL database supports linked servers only as a target.",
                       fix="Use a three-part name for an item in the same workspace, or copy the data in.")
        schema = parts[-2].kw if len(parts) >= 2 else ""
        if schema in _SYSTEM:
            system = system or parts[0]
        elif t.kw in ("FROM", "JOIN", "APPLY"):
            user = user or parts[0]
    if system is not None and user is not None:
        yield _hit("DML-004", system)


# ---------- transactions

@check
def _transactions(s: Stmt):
    toks = s.toks
    tran = ("TRAN", "TRANSACTION")
    for i in s.find("SET", "TRANSACTION", "ISOLATION"):
        yield _hit("TRX-001", toks[i], toks[i + 2])
    for i in s.find(("SAVE",), tran):
        yield _hit("TRX-002", toks[i], toks[i + 1], name="Savepoints", fix="Use one transaction, or several in a row.")
    for i in s.find("BEGIN", "DISTRIBUTED", tran):
        yield _hit("TRX-003", toks[i], toks[i + 2])
    for i in s.find("BEGIN", tran):
        k = i + 2
        named = k < len(toks) and (toks[k].type == TokenType.PARAMETER or
                                   (_is_name(toks[k]) and toks[k].kw not in {"WITH", "INSERT", "UPDATE", "DELETE",
                                                                             "MERGE", "SELECT", "CREATE", "DROP",
                                                                             "ALTER", "TRUNCATE", "EXEC", "EXECUTE",
                                                                             "SET", "DECLARE", "IF", "COMMIT",
                                                                             "ROLLBACK", "BEGIN", "END"}))
        if named:
            yield _hit("TRX-002", toks[i], toks[k], name="Named transactions", fix="Remove the name.")
        yield _hit("TRX-004", toks[i], toks[i + 1])
    for i in s.find("WITH", "MARK"):
        yield _hit("TRX-002", toks[i], toks[i + 1], name="Marked transactions", fix="Remove WITH MARK.")


# ---------- meaning

@check
def _semantics(s: Stmt):
    toks = s.toks
    ctx = _context(s)
    told_case = False
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.type in (TokenType.EQ, TokenType.NEQ) and nxt is not None and nxt.kw == "NULL" and ctx[i] == "test":
            yield _hit("SEM-001", t, nxt)
        if not told_case and ctx[i] == "test" and nxt is not None and nxt.type == TokenType.STRING and \
                t.type in (TokenType.EQ, TokenType.NEQ, TokenType.LIKE) and re.search(r"[A-Za-z]", nxt.text):
            told_case = True
            yield _hit("SEM-002", t, nxt)
        if t.type == TokenType.SLASH and i and nxt is not None and toks[i - 1].type == TokenType.NUMBER and \
                nxt.type == TokenType.NUMBER and toks[i - 1].text.isdigit() and nxt.text.isdigit() and nxt.text != "0":
            a, b = int(toks[i - 1].text), int(nxt.text)
            if a % b:
                yield _hit("SEM-003", toks[i - 1], nxt, example=f"{a} / {b} = {a // b}")
        if t.kw == "NOT" and nxt is not None and nxt.kw == "IN" and i + 3 < len(toks) and \
                toks[i + 2].type == TokenType.L_PAREN and toks[i + 3].kw in ("SELECT", "WITH"):
            yield _hit("SEM-004", t, nxt)
        if t.kw == "COLLATE" and nxt is not None and nxt.kw not in {"LATIN1_GENERAL_100_BIN2_UTF8",
                                                                     "LATIN1_GENERAL_100_CI_AS_KS_WS_SC_UTF8",
                                                                     "DATABASE_DEFAULT"}:
            yield _hit("SEM-006", t, nxt, name=nxt.text[:64])


# ---------- statements that change or delete data

_DROPPABLE = {"TABLE", "VIEW", "SCHEMA", "DATABASE", "PROCEDURE", "PROC", "FUNCTION", "INDEX", "STATISTICS",
              "TRIGGER", "SYNONYM", "SEQUENCE", "USER", "ROLE", "TYPE"}


def _update_statistics(s: Stmt, i: int) -> bool:
    """UPDATE STATISTICS t, not an UPDATE of a table named statistics (which has SET). The T-SQL
    tokenizer folds the rest of UPDATE STATISTICS into one token, so SET is looked for in the text."""
    toks = s.toks
    if not (toks[i].kw == "UPDATE" and i + 1 < len(toks) and toks[i + 1].kw == "STATISTICS"):
        return False
    return not re.search(r"\bSET\b", s.text[toks[i + 1].end - s.start:], re.IGNORECASE)


@check
def _destructive(s: Stmt):
    toks = s.toks
    for i, verb in _verbs(s):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if verb == "DROP" and nxt is not None and nxt.kw in _DROPPABLE:
            yield _hit("DES-001", toks[i], nxt, name="procedure" if nxt.kw == "PROC" else nxt.kw.lower())
        elif verb == "TRUNCATE":
            yield _hit("DES-002", toks[i], nxt or toks[i])
        elif verb in ("DELETE", "UPDATE") and not _where_follows(s, i) and not _update_statistics(s, i):
            yield _hit("DES-003", toks[i], name=verb)
    for i in s.find("INSERT", "OVERWRITE"):
        yield _hit("DES-004", toks[i], toks[i + 1], name="INSERT OVERWRITE")
    for i in s.find("CREATE", "OR", "REPLACE", "TABLE"):
        yield _hit("DES-004", toks[i], toks[i + 3], name="CREATE OR REPLACE TABLE")


# ---------- running on this PC

_LOCAL_WRITES = _WRITE_VERBS | {"CREATE", "DROP", "ALTER", "OPTIMIZE", "VACUUM", "RESTORE"}


@check
def _local(s: Stmt):
    toks = s.toks
    for i, verb in _verbs(s):
        if verb in _LOCAL_WRITES:
            rest = [t.kw for t in toks[i + 1:i + 4]]
            if verb == "CREATE" and ("VIEW" in rest and ("TEMP" in rest or "TEMPORARY" in rest)):
                yield _hit("LOC-007", toks[i])
                continue
            yield _hit("LOC-001", toks[i])
        elif verb in ("CACHE", "SET", "USE"):
            yield _hit("LOC-007", toks[i])
    if any(t.kw in ("DECIMAL", "NUMERIC") for t in toks) and any(t.type in (TokenType.SLASH,) for t in toks):
        first = next(t for t in toks if t.kw in ("DECIMAL", "NUMERIC"))
        yield _hit("LOC-005", first)
    if s.index == 0:
        yield _hit("LOC-008", toks[0])
