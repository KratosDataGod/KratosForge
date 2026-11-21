"""Queries and changes on a Fabric Data warehouse or SQL analytics endpoint (SQL-004, WH-10, Preview).

The SQL page's run has ``target="warehouse"``: the query runs in Fabric, in the warehouse, as the
person who signed in, so it uses capacity. A run reads unless it was started to change the
warehouse (below):

- **One query that reads.** The T-SQL is read with sqlglot before anything connects. It must be a
  single statement that only reads (sql_policy.classify): no SELECT ... INTO, no EXEC, no
  variables, no second statement, and no OPENROWSET, OPENQUERY or OPENDATASOURCE, which read
  outside the warehouse. Anything else is refused and nothing is sent. The worker checks again
  before it connects.
- **Rolled back.** The connection opens a transaction and rolls it back at the end, so a statement
  this check misread still changes nothing.
- **Only Fabric's own server.** The server is read from Fabric for the item the person picked
  (``resolve``), never taken from the caller, and must be a ``*.datawarehouse.fabric.microsoft.com``
  host, so the Azure SQL token is never sent anywhere else.

A **change** (WH-10) is a run started with ``writes``: the person saw what it changes and
confirmed it, naming the warehouse (sql_exec.py checks the name against Fabric's). Its script may be
several statements, in batches split at ``GO`` lines like SSMS: DDL, DML, EXEC, variables. Still
refused, before anything is sent: BEGIN TRAN, COMMIT and ROLLBACK (the run is the transaction; a BEGIN TRAN first
line and COMMIT last line that wrap the whole script are taken off, see without_outer_transaction),
USE (another database), GRANT, REVOKE and DENY (permissions belong in Fabric), COPY INTO and BULK
INSERT (they load files and aren't run from the SQL page), CREATE, ALTER or DROP DATABASE, and OPENROWSET, OPENQUERY and
OPENDATASOURCE. Also refused: dynamic SQL (EXEC of a string or a variable, sp_executesql), since
what it runs can't be checked first (EXEC of a module named by a variable too); EXECUTE AS and
REVERT; and roles, users and AUTHORIZATION. Every batch is checked by its text for these words,
anywhere outside comments, strings and quoted names, procedure and function bodies included, since
a procedure the change creates could be called by the same script; procedure names are checked
with brackets and quotes unwrapped. A batch sqlglot can't read is refused, unless it defines a
procedure, function, view or trigger. A procedure already in the warehouse runs as it was
written; the page says so. Every batch runs on one connection in one transaction: committed once
all of them succeeded, rolled back when one fails or the run is stopped, so a failed change keeps
nothing.

The run is a process of its own (sql_worker.py, like a lakehouse run), so Stop and the time limit
end that process, which closes the connection; Fabric stops the query when its connection closes.
The worker asks the engine for the token (``call`` ``warehouse_token``) and keeps it in memory only.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import logging
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError

from . import sql_dialects, sql_params, sql_policy, tsql_clone

KINDS = ("warehouse", "endpoint")  # a Data warehouse, or a lakehouse's SQL analytics endpoint
HOST_SUFFIX = ".datawarehouse.fabric.microsoft.com"
BATCH_ROWS = 65_536
PROGRESS_SECONDS = 0.25
LOGIN_TIMEOUT_S = 60
# Azure SQL's transient login errors (a warehouse resuming, a busy service, not enough resources):
# only the connect is tried again, before anything is sent, so a change is never sent twice.
TRANSIENT_LOGIN_ERRORS = (40197, 40501, 40613, 49918, 49919, 49920, 10928, 10929, 4221)
# The same errors by their text: mssql-python's message ("Driver Error: ...; DDBC Error:
# [Microsoft][SQL Server]...") has no error number, which only pyodbc appends.
_TRANSIENT_TEXTS = (
    (40613, re.compile(r"is not currently available", re.IGNORECASE)),
    (40501, re.compile(r"service is currently busy", re.IGNORECASE)),
    (49918, re.compile(r"not enough resources to process request", re.IGNORECASE)),
    (49919, re.compile(r"too many create or update operations in progress", re.IGNORECASE)),
    (49920, re.compile(r"too many operations in progress", re.IGNORECASE)),
    (40197, re.compile(r"encountered an error processing your request", re.IGNORECASE)),
    (10928, re.compile(r"resource id\s*:\s*\d+\.\s*the \w+ limit for the database is \d+ and has been reached", re.IGNORECASE)),
    (10929, re.compile(r"resource id\s*:\s*\d+\.\s*the \w+ minimum guarantee is", re.IGNORECASE)),
    (4221, re.compile(r"HADR_DATABASE_WAIT_FOR_TRANSITION_TO_VERSIONING", re.IGNORECASE)),
)
CONNECT_TRIES = 4
CONNECT_WAITS = (5, 10, 20)  # seconds before tries 2, 3 and 4
CONNECT_DEADLINE_S = 120  # all tries and waits together; each try's login timeout is cut to fit
_ERROR_NUMBER = re.compile(r"\((\d{4,5})\)")  # pyodbc's "... (40613)"
_log = logging.getLogger(__name__)
_OUTSIDE = {"openrowset", "openquery", "opendatasource"}
# Possessive (*+): each run of blanks is read once, not split every way between the parts around it.
_GO = re.compile(r"^[ \t]*+GO[ \t]*+;?[ \t]*+$", re.IGNORECASE | re.MULTILINE)
# Comments, strings and quoted names in one left-to-right pass, so whichever starts first wins: a
# quote inside [it's] or -- inside [a--b] is part of the name, and a [ inside a string is text.
_TOKENS = re.compile(r"--[^\n]*|/\*.*?\*/|N?'(?:[^']|'')*'|\[((?:[^\]]|\]\])*)\]|\"((?:[^\"]|\"\")*)\"", re.DOTALL)
_DEFINES = re.compile(r"^\s*(?:CREATE|ALTER|CREATE\s+OR\s+ALTER)\s+(PROC|PROCEDURE|FUNCTION|TRIGGER|VIEW)\b\s*([^\s(]*)",
                      re.IGNORECASE)
# What a change may never do, looked for in each batch's text with comments and strings taken out,
# procedure and function bodies included (they run the same way when called). The words are looked
# for with quoted names taken out too, so a column called [User] is fine; the procedure names with
# quoted names unwrapped, so [sys].[sp_executesql] is still sp_executesql.
_TRANSACTION = ("the run is one transaction the app commits: BEGIN TRAN may be the script's first line and COMMIT its "
                "last, but not ROLLBACK, SAVE TRAN, a named transaction, or a COMMIT in the middle")
_PERMISSIONS = "permissions, roles and users are managed in Fabric"
_AS_NAME = "; a column or table with that name is written in brackets, such as [{}]"  # {}: the word found
_DYNAMIC = "it runs SQL made from text, which can't be checked before it runs"
_LOADS = ("{} loads files into the warehouse, which isn't run from the SQL page. Use the Load files with COPY INTO "
          "template and run it in Fabric's query editor.")
_NEVER_WORDS = [(re.compile(p, re.IGNORECASE), why) for p, why in (
    (r"\b(?:BEGIN\s+TRAN(?:SACTION)?|BEGIN\s+DISTRIBUTED|COMMIT|ROLLBACK|SAVE\s+TRAN(?:SACTION)?)\b", _TRANSACTION),
    (r"\bEXEC(?:UTE)?\s*\(|\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?@\w+\b(?!\s*=)", _DYNAMIC),
    (r"\bEXEC(?:UTE)?\s+AS\b|\bREVERT\b|\bSETUSER\b", "it runs as someone else; it runs as you"),
    (r"\b(?:GRANT|REVOKE|DENY|AUTHORIZATION)\b", _PERMISSIONS + _AS_NAME),
    (r"\b(?:CREATE|ALTER|DROP)\s+(?:ROLE|USER|LOGIN|APPLICATION\s+ROLE)\b", _PERMISSIONS),
    (r"\bUSE\b", "USE switches to another database: open that warehouse instead" + _AS_NAME),
    (r"\bCOPY\s+INTO\b", _LOADS.format("COPY INTO")),
    (r"\bBULK\s+INSERT\b", _LOADS.format("BULK INSERT")),
    (r"\b(?:CREATE|ALTER|DROP)\s+DATABASE\b", "a database is created, changed or removed in Fabric"),
)]
_NEVER_NAMES = [(re.compile(p, re.IGNORECASE), why) for p, why in (
    (r"\bsp_executesql\b", _DYNAMIC),
    (r"\bsp_(?:add|drop)(?:srv)?rolemember\b|\bsp_(?:add|drop)user\b|\bsp_(?:add|drop)role\b|\bsp_changedbowner\b",
     _PERMISSIONS),
)]
SQL_COPT_SS_ACCESS_TOKEN = 1256


@dataclass(frozen=True)
class Endpoint:
    server: str
    database: str


def resolve(fabric: Any, ref: dict[str, Any]) -> Endpoint:
    """The server and database of the item ``ref`` names, read from Fabric.

    ``ref``: ``workspace_id``, ``item_id`` and ``kind`` ("warehouse", or "endpoint" with the
    lakehouse's id as ``item_id``)."""
    kind = ref.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    if not all(isinstance(ref.get(k), str) and ref[k] for k in ("workspace_id", "item_id")):
        raise ValueError("Pick a warehouse to run the query on.")
    if kind == "warehouse":
        item = fabric.get_warehouse(ref["workspace_id"], ref["item_id"])
        server = (item.get("properties") or {}).get("connectionString") or ""
    else:
        item = fabric.get_lakehouse(ref["workspace_id"], ref["item_id"])
        server = ((item.get("properties") or {}).get("sqlEndpointProperties") or {}).get("connectionString") or ""
    database = str(item.get("displayName") or "")
    server = str(server).strip()
    if not server or not database:
        what = "warehouse" if kind == "warehouse" else "lakehouse's SQL analytics endpoint"
        raise ValueError(f"Fabric didn't give this {what}'s SQL connection. It may still be provisioning.")
    if not server.lower().endswith(HOST_SUFFIX) or any(c in server for c in ";,={}/ "):
        raise ValueError(f"{server!r} isn't a Fabric SQL endpoint, so the query wasn't sent.")
    return Endpoint(server, database)


def batches(query: str) -> list[str]:
    """The script's batches, split at ``GO`` lines as SSMS does; empty ones are left out."""
    return [b.strip("\n") for b in _GO.split(query.replace("\r\n", "\n")) if b.strip()]


# A change script written as one transaction (WR-204): BEGIN TRAN on its own first line and COMMIT on
# its own last line (GO lines may follow either). The run is already one transaction the app commits
# once every batch succeeded and rolls back otherwise, so those two lines are what it does anyway:
# they are taken off and the rest runs as before. A named transaction, ROLLBACK, SAVE TRAN, or BEGIN
# TRAN or COMMIT anywhere else is still refused (_NEVER_WORDS), since the app can't keep its promise
# that a failed change keeps nothing with the script deciding when to commit.
# Comments are blanked to spaces before these are read, so a long one is a long run of blanks: each
# run is read once (*+), not split every way between the parts around it, which took seconds on a
# few thousand. A GO line's blanks before its line break belong to it, as the line break's did.
_BEGIN_LINE = re.compile(r"\A\s*+BEGIN[ \t]++TRAN(?:SACTION)?[ \t]*+;?[ \t]*+(?:\n|\Z)", re.IGNORECASE)
_COMMIT_LINE = re.compile(r"(?:\A|\n)[ \t]*+COMMIT(?:[ \t]++(?:TRAN(?:SACTION)?|WORK))?[ \t]*+;?"
                          r"(?:[ \t]*+\n[ \t]*+GO[ \t]*+;?)*+\s*\Z", re.IGNORECASE)


def without_outer_transaction(query: str) -> tuple[str, bool]:
    """``query`` without a BEGIN TRAN first line and COMMIT last line that wrap all of it, and whether
    they were there. Comments and strings are blanked first, so a COMMIT in one doesn't count."""
    text = query.replace("\r\n", "\n")
    bare = _TOKENS.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)  # same length, lines kept
    begin, commit = _BEGIN_LINE.match(bare), _COMMIT_LINE.search(bare)
    if not begin or not commit or commit.start() < begin.end():
        return query, False
    return text[begin.end():commit.start()], True


def change_batches(query: str) -> list[str]:
    """A change's batches, as they are sent: without the BEGIN TRAN and COMMIT lines that wrap it."""
    return batches(without_outer_transaction(query)[0])


# S14: a script the page split into statements, each with a result of its own. The statements are sent one
# after the other on one connection, as batches are. A change's statements are its batches.
_BEGIN_UNIT = re.compile(r"\A\s*+BEGIN\s++TRAN(?:SACTION)?\s*+;?\s*\Z", re.IGNORECASE)
_COMMIT_UNIT = re.compile(r"\A\s*+COMMIT(?:\s++(?:TRAN(?:SACTION)?|WORK))?\s*+;?\s*\Z", re.IGNORECASE)


def change_units(units: list[str]) -> list[str]:
    """A change's statements as they are sent: without a BEGIN TRAN first and COMMIT last that wrap the
    rest, as change_batches takes those lines off a script (the run is already the transaction)."""
    if len(units) > 2 and _BEGIN_UNIT.match(units[0]) and _COMMIT_UNIT.match(units[-1]):
        return units[1:-1]
    return list(units)


def join_units(units: list[str]) -> str:
    """The statements as one script of batches, one per statement, for the checks and the audit."""
    return "\nGO\n".join(u.strip("\n") for u in units)


def unit_verdicts(units: list[str], writes: bool = False) -> list[sql_policy.Verdict]:
    """What each of a split script's statements is, numbered by statement (S14). Without ``writes`` each
    must be one query that reads, as a run of one is; with it, each is checked as a change's batch."""
    out: list[sql_policy.Verdict] = []
    for n, unit in enumerate(units, 1):
        found = _change_verdicts(unit) if writes else verdicts(unit)
        out.extend(sql_policy.Verdict(n, v.kind, v.reason, v.confirm) for v in found)
    return out


# A read at a past time (time travel): Fabric's query hint OPTION (FOR TIMESTAMP AS OF '<UTC time>') at
# the end of a SELECT. sqlglot can't read it, so the check reads the query without it; the hint only
# picks which version of the tables is read, and the query is sent with it as written. A change's
# batches are read the same way, so a past-time read still runs with Allow changes ticked. It starts
# where blanks start, not again inside them, so a long run of blanks is read once.
_AS_OF = re.compile(r"(?<!\s)\s*\bOPTION\s*\(\s*FOR\s+TIMESTAMP\s+AS\s+OF\s+'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?'\s*\)"
                    r"\s*+;?\s*$", re.IGNORECASE)


def verdicts(query: str, writes: bool = False) -> list[sql_policy.Verdict]:
    """What each statement of ``query`` is. Without ``writes`` only a single statement that reads
    is read_only. With ``writes`` each statement of each batch is read_only, warehouse_write, or
    refused (fabric_mutation) for the reasons in this module's docstring."""
    if writes:
        return _change_verdicts(query)
    bare, _names = _bare_and_names(query)
    # WR-226: sqlglot reads neither as a statement, so they are named here
    cloned = tsql_clone.clones(query)
    if cloned:
        return [sql_policy.Verdict(1, "fabric_mutation", f"{cloned[0].statement() if cloned[0].clone else 'CREATE TABLE ... AS CLONE OF'}"
                                                         " makes a table in the warehouse, which is a change")]
    if tsql_clone.snapshot_timestamp(query):
        return [sql_policy.Verdict(1, "fabric_mutation", tsql_clone.SNAPSHOT_TIMESTAMP)]
    # named before parsing: sqlglot can't read OPENROWSET(BULK ...) or BULK INSERT (WR-220)
    # a call, with quoted names taken out, so a column called [openrowset] still reads
    outside = next((f for f in sorted(_OUTSIDE) if re.search(rf"\b{f}\s*\(", bare, re.IGNORECASE)), "")
    if outside:
        return [sql_policy.Verdict(1, "fabric_mutation", f"{outside.upper()} reads outside the warehouse")]
    loads = re.search(r"\b(?:COPY\s+INTO|BULK\s+INSERT)\b", bare, re.IGNORECASE)
    if loads:
        word = " ".join(loads.group().upper().split())
        return [sql_policy.Verdict(1, "fabric_mutation", _LOADS.format(word))]
    try:
        statements = [cast(exp.Expression, s)  # sqlglot types parse as its Expr base
                      for s in sql_dialects.parse(_AS_OF.sub("", query), read="tsql", error_level=ErrorLevel.RAISE)
                      if s is not None]
    except ParseError as exc:
        raise ValueError("The query couldn't be read as T-SQL, so it can't be shown to only read: "
                         + sql_dialects.parse_problem(exc)) from None
    if not statements:
        raise ValueError("The query is empty.")
    out = []
    for v, st in zip(sql_policy.classify(statements), statements, strict=True):
        if v.statement > 1:
            v = sql_policy.Verdict(v.statement, "fabric_mutation", "a warehouse run is one query at a time")
        elif v.kind == "read_only":
            refused = _reads_outside(st)
            v = (sql_policy.Verdict(1, "fabric_mutation", refused) if refused
                 else sql_policy.Verdict(1, "read_only", "reads the warehouse's tables"))
        else:
            v = sql_policy.Verdict(v.statement, "fabric_mutation", f"{st.key.upper()} isn't a query that only reads")
        out.append(v)
    return out


def _reads_outside(st: exp.Expression) -> str:
    """Why a statement that reads isn't only reading the warehouse, or ""."""
    if st.find(exp.Into) is not None:
        return "SELECT ... INTO creates a table in the warehouse"
    outside = next((n.name.lower() for n in st.find_all(exp.Anonymous) if n.name.lower() in _OUTSIDE), "")
    return f"{outside.upper()} reads outside the warehouse" if outside else ""


def _bare_and_names(batch: str) -> tuple[str, str]:
    """The batch with comments and strings blanked, twice: ``bare`` with each quoted name as a
    placeholder (for words, so a column called [User] is fine), ``names`` with each unwrapped (for
    procedure names, so [sys].[sp_executesql] is still sp_executesql)."""
    def quoted(m: re.Match[str]) -> bool:
        return m.group(0)[:1] in "[\""

    def unwrap(m: re.Match[str]) -> str:
        if not quoted(m):
            return " "
        return m.group(1).replace("]]", "]") if m.group(0)[0] == "[" else m.group(2).replace('""', '"')

    return (_TOKENS.sub(lambda m: " x " if quoted(m) else " ", batch), _TOKENS.sub(unwrap, batch))


def _change_verdicts(query: str) -> list[sql_policy.Verdict]:
    parts = change_batches(query)
    if not parts:
        raise ValueError("The query is empty.")
    out: list[sql_policy.Verdict] = []
    for batch in parts:
        n = len(out) + 1
        bare, names = _bare_and_names(batch)
        outside = next((f for f in _OUTSIDE if re.search(rf"\b{f}\b", names, re.IGNORECASE)), "")
        if outside:
            out.append(sql_policy.Verdict(n, "fabric_mutation", f"{outside.upper()} reads outside the warehouse"))
            continue
        never = (tsql_clone.SNAPSHOT_TIMESTAMP if tsql_clone.snapshot_timestamp(batch) else "") or next(
            (why.format(m.group().capitalize()) for rule, why in _NEVER_WORDS if (m := rule.search(bare))), "") or next(
            (why for rule, why in _NEVER_NAMES if rule.search(names)), "")
        if never:
            out.append(sql_policy.Verdict(n, "fabric_mutation", never))
            continue
        defines = _DEFINES.match(names)
        if defines:  # its body was checked above; sqlglot can't read most bodies, so it isn't parsed
            out.append(sql_policy.Verdict(n, "warehouse_write", f"defines {defines.group(1).upper()} {defines.group(2)}"
                                          .rstrip()))
            continue
        # WR-226: a clone is read from its tokens, in its place among the batch's other statements
        cloned = tsql_clone.clones(batch)
        at = 0
        for piece in [*cloned, None]:
            text = batch[at:piece.start if piece is not None else len(batch)]
            if text.strip() and not _parsed_verdicts(text, out):
                break
            if piece is not None:
                out.append(_clone_verdict(len(out) + 1, piece))
                at = piece.end
    return out


def _parsed_verdicts(text: str, out: list[sql_policy.Verdict]) -> bool:
    """Add the verdicts of a change batch's text (or a part of it) to ``out``; False when it can't be read."""
    try:
        statements = [cast(exp.Expression, st)  # sqlglot types parse as its Expr base
                      for st in sql_dialects.parse(_AS_OF.sub("", text), read="tsql", error_level=ErrorLevel.RAISE)
                      if st is not None]
    except ParseError:
        out.append(sql_policy.Verdict(len(out) + 1, "fabric_mutation", "it couldn't be read as T-SQL, so it can't be "
                                                                       "checked; a procedure or function definition can be"))
        return False
    for st in statements:
        out.append(_change_verdict(len(out) + 1, st))
    return True


def _clone_verdict(n: int, c: tsql_clone.Clone) -> sql_policy.Verdict:
    """CREATE TABLE ... AS CLONE OF (WR-226): a zero-copy clone in this warehouse, or refused by name before
    anything is sent."""
    if not c.clone:
        return sql_policy.Verdict(n, "fabric_mutation", "CREATE TABLE ... AS CLONE OF couldn't be read. Write it as "
                                  "CREATE TABLE schema.clone AS CLONE OF schema.source [AT 'YYYY-MM-DDThh:mm:ss'].")
    other = next((name for name in (c.clone, c.source) if len(name) > 2), None)
    if other is not None:
        return sql_policy.Verdict(n, "fabric_mutation", f"{'.'.join(other)} names a database: a Fabric Warehouse "
                                  "clones a table only within the same warehouse, so name it as schema.table")
    if c.at is not None:
        problem = tsql_clone.at_problem(c.at)
        if problem:
            return sql_policy.Verdict(n, "fabric_mutation", problem)
    when = f" as it was at {c.at.replace('T', ' ')} UTC" if c.at is not None else " as it is now"
    return sql_policy.Verdict(n, "warehouse_write", f"CREATE TABLE {c.clone_name} AS CLONE OF {c.source_name} makes "
                              f"a zero-copy clone of {c.source_name}{when}")


def _change_verdict(n: int, st: exp.Expression) -> sql_policy.Verdict:
    if isinstance(st, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.Values)):
        if st.find(exp.Into) is not None:
            return sql_policy.Verdict(n, "warehouse_write", "SELECT ... INTO creates a table")
        return sql_policy.Verdict(n, "read_only", "reads the warehouse's tables")
    if isinstance(st, (exp.Declare, exp.Set)):
        return sql_policy.Verdict(n, "read_only", "sets a variable for this run")
    target = st.find(exp.Table)
    name = ".".join(p for p in (target.catalog, target.db, target.name) if p) if target is not None else ""
    if target is not None and target.catalog:
        return sql_policy.Verdict(n, "fabric_mutation", f"{name} is in another database: open that warehouse to "
                                                         "change it")
    if isinstance(st, exp.Command):
        verb = str(st.this).upper()
    elif isinstance(st, (exp.Create, exp.Drop)):
        verb = f"{st.key.upper()} {str(st.args.get('kind') or '').upper()}".strip()
    else:
        verb = {"truncatetable": "TRUNCATE", "execute": "EXEC", "ifblock": "IF", "whileblock": "WHILE"}.get(
            st.key, st.key.upper())
    return sql_policy.Verdict(n, "warehouse_write", f"{verb} {name}".strip() + " changes the warehouse")


def connect(server: str, database: str, token: str, login_timeout: int = LOGIN_TIMEOUT_S) -> Any:
    """A connection to ``database`` on ``server`` as the token's user, in a transaction."""
    import mssql_python

    mssql_python.pooling(enabled=False)  # one connection per run, closed when it ends
    raw = token.encode("utf-16-le")
    return mssql_python.connect(
        f"Server=tcp:{server},1433;Database={{{database.replace('}', '}}')}}};Encrypt=yes;"
        "TrustServerCertificate=no",
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: struct.pack(f"<I{len(raw)}s", len(raw), raw)},
        autocommit=False, timeout=login_timeout)


def transient_login_error(exc: BaseException) -> int | None:
    """The error number when ``exc`` is a connect failure worth another try, else None. A login
    timeout counts (0); a refused login (18456, 40532 and the rest) never does. Read from the
    message's text, since mssql-python doesn't give the number."""
    text = str(exc)
    if "login timeout" in text.lower() or "HYT00" in text:
        return 0
    numbers = {int(n) for n in _ERROR_NUMBER.findall(text)} & set(TRANSIENT_LOGIN_ERRORS)
    if numbers:
        return min(numbers)
    return next((number for number, pattern in _TRANSIENT_TEXTS if pattern.search(text)), None)


def open_connection(server: str, database: str, token: Callable[[], str],
                    sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Any:
    """``connect``, tried again (with a fresh token) while the warehouse turns logins away for a
    passing reason, for up to CONNECT_TRIES tries within CONNECT_DEADLINE_S. Stop ends this process,
    so it also ends a wait between tries."""
    deadline = clock() + CONNECT_DEADLINE_S
    attempt = 0
    while True:
        attempt += 1
        login_timeout = max(1, min(LOGIN_TIMEOUT_S, int(deadline - clock())))
        try:
            return connect(server, database, token(), login_timeout=login_timeout)
        except Exception as exc:
            number = transient_login_error(exc)
            if number is None:
                raise
            wait = CONNECT_WAITS[min(attempt - 1, len(CONNECT_WAITS) - 1)]
            if attempt >= CONNECT_TRIES or clock() + wait >= deadline:
                raise ConnectionError(
                    f"The warehouse didn't accept a connection after {attempt} tries (it may be resuming, or busy). "
                    "Try again in a minute. " + (f"Last error {number}." if number else "The login timed out.")) from exc
            _log.info("The warehouse turned the login away (%s); trying again in %ss (%s of %s)",
                      number or "login timeout", wait, attempt + 1, CONNECT_TRIES)
            sleep(wait)


# ---- deploy preconditions (#947): a change runs only against the object its preview saw

PRECONDITION_KINDS = ("schema", "table", "view", "procedure", "function")
PRECONDITION_KEYS = ("kind", "schema", "name", "exists", "definition", "columns")
_MODULE_TYPES = {"view": ("V",), "procedure": ("P",), "function": ("FN", "IF", "TF")}
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_TRAILING_GO = re.compile(r"\n[ \t]*+GO[ \t]*+;?\s*$", re.IGNORECASE)


def definition_fingerprint(text: str) -> str:
    """A view's, procedure's or function's definition as one hash: the text the catalog gives
    (sys.sql_modules), without a trailing GO and the blank space around it."""
    body = _TRAILING_GO.sub("", "\n" + str(text).replace("\r\n", "\n")).strip()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def precondition_problem(p: Any) -> str | None:
    """Why ``p`` isn't a precondition a deploy change can carry, or None:
    {"kind", "schema", "name", "exists"} plus, when it exists, "definition" (a view, procedure or
    function's fingerprint) or "columns" (a table's column names, in order)."""
    if not isinstance(p, dict) or p.get("kind") not in PRECONDITION_KINDS or not isinstance(p.get("exists"), bool):
        return "a precondition names the object's kind and whether it exists"
    if not all(isinstance(p.get(k), str) and 0 < len(p[k]) <= 128 for k in ("schema", "name")):
        return "a precondition names the object's schema and name"
    if p["exists"] and p["kind"] in _MODULE_TYPES and not (isinstance(p.get("definition"), str)
                                                          and _FINGERPRINT.fullmatch(p["definition"])):
        return "a precondition on an existing view, procedure or function gives its definition's fingerprint"
    if p["exists"] and p["kind"] == "table" and not (isinstance(p.get("columns"), list) and 0 < len(p["columns"]) <= 1024
                                                      and all(isinstance(c, str) and 0 < len(c) <= 128 for c in p["columns"])):
        return "a precondition on an existing table gives its column names"
    return None


def _bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _now(cursor: Any, sql: str, value: str) -> list[Any]:
    cursor.execute(sql, value)
    return [row[0] for row in cursor.fetchall()]


def object_changed(cursor: Any, p: dict[str, Any]) -> bool:
    """Whether the object ``p`` names is no longer as the preview saw it. Reads the catalog in the
    change's own transaction, so what it reads is what the change runs against."""
    full = f"{_bracket(p['schema'])}.{_bracket(p['name'])}"
    if p["kind"] == "schema":
        found = _now(cursor, "SELECT s.name FROM sys.schemas AS s WHERE s.name = ?", p["name"])
        return bool(found) != p["exists"]
    types = _now(cursor, "SELECT RTRIM(o.type) FROM sys.objects AS o WHERE o.object_id = OBJECT_ID(?)", full)
    wanted = ("U",) if p["kind"] == "table" else _MODULE_TYPES[p["kind"]]
    if not p["exists"]:
        return bool(types)  # someone made it since the preview
    if not types or str(types[0]) not in wanted:
        return True
    if p["kind"] == "table":
        names = _now(cursor, "SELECT c.name FROM sys.columns AS c WHERE c.object_id = OBJECT_ID(?) ORDER BY c.column_id", full)
        return [str(n) for n in names] != list(p["columns"])
    definition = _now(cursor, "SELECT m.definition FROM sys.sql_modules AS m WHERE m.object_id = OBJECT_ID(?)", full)
    return not definition or definition_fingerprint(str(definition[0] or "")) != p["definition"]


# ---- a run the warehouse or the network ended part way (WR-207)

class Interrupted(RuntimeError):
    """A run that ended part way, by name: ``kind`` is connection, signin, throttled, timeout, cancelled,
    commit_unknown or sql (a change's batch failed on its own SQL); ``batch`` the batch that was running.
    The message says what was kept and what to do next."""

    def __init__(self, kind: str, message: str, batch: int | None = None):
        super().__init__(message)
        self.kind, self.batch = kind, batch


_TIMED_OUT = re.compile(r"HYT00|query timeout expired", re.IGNORECASE)
_CANCELLED = re.compile(r"HY008|operation (was )?cancel", re.IGNORECASE)
_SIGNIN_EXPIRED = re.compile(r"token (is|has) expired|expired token|AADSTS70043|AADSTS700082|\(18456\)", re.IGNORECASE)
_DROPPED = re.compile(r"communication link failure|08S01|08S02|08003|transport-level error|TCP Provider|"
                      r"connection (was|has been) (forcibly )?closed|connection is (closed|broken)|forcibly closed|"
                      r"physical connection is not usable|\(10054\)|\(10053\)", re.IGNORECASE)
_THROTTLED = re.compile(r"throttl|too many requests|currently busy|capacity .*(limit|exceeded)|not currently available",
                        re.IGNORECASE)


def interruption(exc: BaseException) -> tuple[str, int | None] | None:
    """Why the warehouse or the network ended a run part way, from the driver's error text, and the error
    number when there is one; None for the SQL's own error. After the connection opened, so a 18456 means
    the sign-in expired, not a refused login."""
    text = str(exc)
    numbers = sorted({int(n) for n in _ERROR_NUMBER.findall(text)} & set(TRANSIENT_LOGIN_ERRORS))
    if _TIMED_OUT.search(text):
        return "timeout", None
    if _CANCELLED.search(text):
        return "cancelled", None
    if _SIGNIN_EXPIRED.search(text):
        return "signin", None
    if _DROPPED.search(text):
        return "connection", None
    if numbers or _THROTTLED.search(text):
        return "throttled", numbers[0] if numbers else None
    return None


def _first_line(batch: str) -> str:
    first = next((line.strip() for line in batch.splitlines() if line.strip() and not line.strip().startswith("--")), "")
    return f"{first[:80]}{'…' if len(first) > 80 else ''}"


def _interrupted(exc: BaseException, at: int, sent: list[str], writes: bool, unit: str = "batch") -> BaseException:
    """The error a run ends with when batch ``at`` of ``sent`` failed: named, with what was kept and what to
    do next. A read's own SQL error stays the driver's, as before."""
    why = interruption(exc)
    if why is None and isinstance(exc, (OSError, MemoryError)):
        return exc  # this PC's own trouble writing the result (a full disk), not the batch's: its own message says so
    if why is None:
        return Interrupted("sql", _failed_batch(at, sent, exc, unit), at) if writes else exc
    kind, number = why
    if writes:
        where = f"during {unit} {at} of {len(sent)} ({_first_line(sent[at - 1])})" if len(sent) > 1 else \
            f"during the change ({_first_line(sent[at - 1])})"
        kept = "so the whole change was rolled back and nothing was kept"
    else:
        where, kept = "while the query ran", "so no result was kept"
    message = {
        "connection": f"The connection to the warehouse dropped {where}, {kept}. Check this PC's network "
                      "(and VPN, if you use one), then run it again.",
        "signin": f"Your sign-in to Fabric expired {where}, {kept}. Sign in again, then run it again.",
        "throttled": f"The warehouse stopped the run {where} because its capacity is busy"
                     f"{f' (error {number})' if number else ''}, {kept}. Wait a few minutes and run it again; if "
                     "it keeps happening, ask your Fabric capacity admin about the capacity's load.",
        "timeout": f"The run reached its time limit {where}, {kept}. Narrow the query, or raise the time limit in "
                   "Settings › SQL, then run it again.",
        "cancelled": f"The warehouse cancelled the run {where}, {kept}. Run it again; if it's cancelled again, an "
                     "admin may be stopping it: check the warehouse's activity in Fabric.",
    }[kind]
    return Interrupted(kind, message, at)


def _commit(con: Any) -> None:
    """Commit a change. If the connection drops while the commit is on its way, the warehouse may or may not
    have kept the change, and the error says so; any other refusal means nothing was kept."""
    try:
        con.commit()
    except Exception as exc:
        why = interruption(exc)
        if why is not None and why[0] in ("connection", "timeout", "cancelled"):
            raise Interrupted("commit_unknown", "The connection to the warehouse was lost while the change was being "
                              "committed, so it may or may not have been kept. Check the tables it changes before "
                              "running it again.") from exc
        raise Interrupted("sql", "The warehouse didn't commit the change, so nothing was kept. Fix what it "
                          f"reports, then run it again: {str(exc).strip() or type(exc).__name__}") from exc


def _end(con: Any, committed: bool) -> None:
    """Roll back what wasn't committed and close. When the connection is already gone these fail too; the
    warehouse rolls an open transaction back on its own then, and the run's own error is the one to keep."""
    try:
        if not committed:
            con.rollback()
    except Exception as exc:  # noqa: BLE001 - the connection may be gone, and the run's own error is the one to keep
        _log.warning("Rolling back after the run failed: %s", type(exc).__name__)
    try:
        con.close()
    except Exception as exc:  # noqa: BLE001 - closing a connection that is already gone mustn't hide the run's result
        _log.warning("Closing the warehouse connection: %s", type(exc).__name__)


def run(job: dict[str, Any], token: Callable[[], str], say: Callable[..., None]) -> None:
    """Run the job's query and stream the last result's rows into the job's Arrow IPC file. A job
    with ``writes`` runs its batches in one transaction and commits it once they all succeeded. A job
    with ``plan`` (WH-09) doesn't run its query: with SHOWPLAN_XML on, Fabric returns the estimated
    plan as one row of XML instead, and the session is set back before the connection closes."""
    import pyarrow as pa

    writes = bool(job.get("writes"))
    units: list[str] = list(job.get("statements") or [])  # S14: each statement's result is a part of its own
    checked = unit_verdicts(units, writes=writes) if units else verdicts(job["query"], writes=writes)
    stop = sql_policy.refused(checked, ("read_only", "warehouse_write") if writes else ("read_only",))
    if stop is not None:
        say("error", kind="permission", permission=stop.kind, statement=stop.statement,
            message=f"Statement {stop.statement} was refused ({stop.reason}), so nothing was sent. "
                    + ("A warehouse change can't do this." if writes else "A warehouse run only reads."))
        return
    wh = job["warehouse"]
    con = open_connection(wh["server"], wh["database"], token)
    committed = False
    try:
        if job.get("time_limit_s"):
            con.timeout = int(job["time_limit_s"])  # the driver cancels the query too, as well as Stop ending the process
        say("started", permission=sql_policy.strongest(checked))
        cursor = con.cursor()
        out = Path(job["out"])
        result: tuple[int, list[int], Any] | None = None
        affected = 0
        plan = bool(job.get("plan")) and not writes
        precondition = job.get("precondition") if writes else None
        if precondition and object_changed(cursor, precondition):
            say("error", kind="changed", message=f"{job.get('warehouse', {}).get('database') or 'The warehouse'}'s "
                f"{precondition['schema']}.{precondition['name']} changed since the preview, so nothing was sent. "
                "Compare again." if precondition["kind"] != "schema" else
                f"Schema {precondition['name']} changed since the preview, so nothing was sent. Compare again.")
            return
        if plan:
            cursor.execute("SET SHOWPLAN_XML ON")  # its own batch, as T-SQL requires
        values = sql_params.typed(job.get("params"))  # S17: each sent to the driver as a ? parameter
        sent = units or (change_batches(job["query"]) if writes else [job["query"]])
        for at, batch in enumerate(sent, 1):
            began, mine, changed = time.monotonic(), None, None
            if units:
                say("statement", statement=at)
            try:
                text, ordered = sql_params.as_positional(batch, values) if values or sql_params.find(batch, True) else (batch, [])
                cursor.execute(text, *ordered) if ordered else cursor.execute(text)
                while True:
                    if cursor.description:
                        # each result replaces the one before: the last is shown (and, S14, is the statement's part)
                        result = mine = _write_result(cursor, Path(job["parts_dir"]) / f"result-{at}.arrow" if units else out, say)
                    elif (writes or units) and isinstance(getattr(cursor, "rowcount", -1), int) and cursor.rowcount >= 0:
                        if writes and cursor.rowcount > 0:
                            affected += cursor.rowcount
                        changed = (changed or 0) + cursor.rowcount
                    if not (writes or units) or not cursor.nextset():
                        break
            except Exception as exc:
                failure = _interrupted(exc, at, sent, writes, "statement" if units else "batch")
                if failure is exc:
                    raise
                raise failure from exc
            if units:
                if mine is None:  # a statement with no rows: an empty part, with what it changed
                    import pyarrow.ipc as ipc
                    with ipc.new_file(str(Path(job["parts_dir"]) / f"result-{at}.arrow"), pa.schema([])):
                        pass
                    mine = (0, [], pa.schema([]))
                rows_n, sizes, schema = mine
                say("part", statement=at, rows=rows_n, batches=sizes, seconds=round(time.monotonic() - began, 2),
                    columns=[{"name": f.name, "type": str(f.type)} for f in schema],
                    **({"affected": changed} if changed is not None and not schema.names else {}))
        if plan:
            cursor.execute("SET SHOWPLAN_XML OFF")
        if result is None:
            with pa.ipc.new_file(str(out), pa.schema([])):
                pass
            result = (0, [], pa.schema([]))
        if writes:
            say("committing")  # a Stop from here on may come after the warehouse kept the change
            _commit(con)
            committed = True
        rows, batch_rows, schema = result
        say("done", rows=rows, batches=batch_rows, columns=[{"name": f.name, "type": str(f.type)} for f in schema],
            **({"affected": affected, "committed": True} if writes else {}))
    finally:
        _end(con, committed)


def _failed_batch(at: int, sent: list[str], exc: BaseException, unit: str = "batch") -> str:
    """Which batch (or statement, S14) of a change failed, its first line, and that nothing was kept (the run
    rolls back)."""
    first = _first_line(sent[at - 1])
    where = f"{unit.capitalize()} {at} of {len(sent)} ({first})" if len(sent) > 1 else f"The change ({first})"
    fix = f"Fix that {unit} and run the change again." if len(sent) > 1 else "Fix it and run it again."
    return (f"{where} failed, so the whole change was rolled back and nothing was kept. {fix} "
            f"{str(exc).strip() or type(exc).__name__}")


def _write_result(cursor: Any, out: Path, say: Callable[..., None]) -> tuple[int, list[int], Any]:
    """The cursor's current result set into ``out``: its row count, batch sizes and schema."""
    import pyarrow.ipc as ipc

    rows, sizes, last = 0, [], time.monotonic()
    chunk = cursor.fetchmany(BATCH_ROWS)
    schema = _schema(cursor.description, chunk)
    with ipc.new_file(str(out), schema) as writer:
        while chunk:
            writer.write_batch(_batch(schema, chunk))
            rows += len(chunk)
            sizes.append(len(chunk))
            if time.monotonic() - last >= PROGRESS_SECONDS:
                say("progress", rows=rows)
                last = time.monotonic()
            chunk = cursor.fetchmany(BATCH_ROWS)
    return rows, sizes, schema


def _schema(description: Any, sample: list[Any]) -> Any:
    import pyarrow as pa

    from .frames import NOTE_KEY

    fields = []
    for i, d in enumerate(description):
        kind, precision, scale = d[1], d[4], d[5]
        note = ""
        if kind is bool:
            t = pa.bool_()
        elif kind is int:
            t = pa.int64()
        elif kind is float:
            t = pa.float64()
        elif kind is decimal.Decimal and precision and 0 < int(precision) <= 38:
            t = pa.decimal128(int(precision), int(scale or 0))
        elif kind is dt.datetime:
            aware = next((r[i].tzinfo is not None for r in sample if r[i] is not None), False)
            t = pa.timestamp("us", tz="UTC" if aware else None)  # datetimeoffset comes back in UTC
            note = _time_note(scale, aware)
        elif kind is dt.date:
            t = pa.date32()
        elif kind is dt.time:
            t = pa.time64("us")
            note = _time_note(scale, False)
        elif kind is bytes:
            t = pa.binary()
        else:
            t = pa.string()  # text, uniqueidentifier, xml and anything new
        fields.append(pa.field(str(d[0] or f"column{i + 1}"), t, metadata={NOTE_KEY: note.encode()} if note else None))
    return pa.schema(fields)


def _time_note(scale: Any, aware: bool) -> str:
    """What a result keeps of a warehouse time (WR-221), said with its export. Times are kept to the microsecond,
    so the seventh digit of datetime2(7), time(7) or datetimeoffset(7) isn't (the driver's scale says how many it
    has); a datetimeoffset is kept in UTC, without the offset it was written with."""
    seventh = isinstance(scale, int) and scale > 6
    if seventh and aware:
        return ("times are kept to the microsecond and given in UTC, so the seventh digit of their fraction and the "
                "offset each was written with aren't kept. To keep them, convert the column to text in the query, "
                "such as CAST(col AS VARCHAR(34))")
    if seventh:
        return ("times are kept to the microsecond, so the seventh digit of their fraction isn't kept. To keep it, "
                "convert the column to text in the query, such as CAST(col AS VARCHAR(34))")
    if aware:
        return ("datetimeoffset values are given in UTC, without the offset each was written with. To keep the "
                "offset, convert the column to text in the query, such as CAST(col AS VARCHAR(34))")
    return ""


def _batch(schema: Any, rows: list[Any]) -> Any:
    import pyarrow as pa

    columns = []
    for i, f in enumerate(schema):
        values = [r[i] for r in rows]
        if pa.types.is_string(f.type):
            values = [v if v is None or isinstance(v, str) else str(v) for v in values]  # uuid, xml, wide decimals
        elif pa.types.is_timestamp(f.type) and f.type.tz:
            values = [None if v is None else v.astimezone(dt.timezone.utc) for v in values]
        columns.append(pa.array(values, type=f.type))
    return pa.RecordBatch.from_arrays(columns, schema=schema)
