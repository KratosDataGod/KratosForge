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
refused, before anything is sent: BEGIN TRAN, COMMIT and ROLLBACK (the run is the transaction),
USE (another database), GRANT, REVOKE and DENY (permissions belong in Fabric), COPY INTO (loading
files comes with data loading), CREATE, ALTER or DROP DATABASE, and OPENROWSET, OPENQUERY and
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
from typing import Any, Callable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError

from . import sql_dialects, sql_policy

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
_GO = re.compile(r"^[ \t]*GO[ \t]*;?[ \t]*$", re.IGNORECASE | re.MULTILINE)
# Comments, strings and quoted names in one left-to-right pass, so whichever starts first wins: a
# quote inside [it's] or -- inside [a--b] is part of the name, and a [ inside a string is text.
_TOKENS = re.compile(r"--[^\n]*|/\*.*?\*/|N?'(?:[^']|'')*'|\[((?:[^\]]|\]\])*)\]|\"((?:[^\"]|\"\")*)\"", re.DOTALL)
_DEFINES = re.compile(r"^\s*(?:CREATE|ALTER|CREATE\s+OR\s+ALTER)\s+(PROC|PROCEDURE|FUNCTION|TRIGGER|VIEW)\b\s*([^\s(]*)",
                      re.IGNORECASE)
# What a change may never do, looked for in each batch's text with comments and strings taken out,
# procedure and function bodies included (they run the same way when called). The words are looked
# for with quoted names taken out too, so a column called [User] is fine; the procedure names with
# quoted names unwrapped, so [sys].[sp_executesql] is still sp_executesql.
_TRANSACTION = "the run is one transaction the app commits: leave out BEGIN TRAN, COMMIT and ROLLBACK"
_PERMISSIONS = "permissions, roles and users are managed in Fabric"
_AS_NAME = "; a column or table with that name is written in brackets, such as [{}]"  # {}: the word found
_DYNAMIC = "it runs SQL made from text, which can't be checked before it runs"
_NEVER_WORDS = [(re.compile(p, re.IGNORECASE), why) for p, why in (
    (r"\b(?:BEGIN\s+TRAN(?:SACTION)?|BEGIN\s+DISTRIBUTED|COMMIT|ROLLBACK|SAVE\s+TRAN(?:SACTION)?)\b", _TRANSACTION),
    (r"\bEXEC(?:UTE)?\s*\(|\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?@\w+\b(?!\s*=)", _DYNAMIC),
    (r"\bEXEC(?:UTE)?\s+AS\b|\bREVERT\b|\bSETUSER\b", "it runs as someone else; it runs as you"),
    (r"\b(?:GRANT|REVOKE|DENY|AUTHORIZATION)\b", _PERMISSIONS + _AS_NAME),
    (r"\b(?:CREATE|ALTER|DROP)\s+(?:ROLE|USER|LOGIN|APPLICATION\s+ROLE)\b", _PERMISSIONS),
    (r"\bUSE\b", "USE switches to another database: open that warehouse instead" + _AS_NAME),
    (r"\bCOPY\s+INTO\b", "COPY INTO loads files, which comes with loading data"),
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


def verdicts(query: str, writes: bool = False) -> list[sql_policy.Verdict]:
    """What each statement of ``query`` is. Without ``writes`` only a single statement that reads
    is read_only. With ``writes`` each statement of each batch is read_only, warehouse_write, or
    refused (fabric_mutation) for the reasons in this module's docstring."""
    if writes:
        return _change_verdicts(query)
    try:
        statements = [s for s in sqlglot.parse(query, read="tsql", error_level=ErrorLevel.RAISE) if s is not None]
    except ParseError as exc:
        raise ValueError("The query couldn't be read as T-SQL, so it can't be shown to only read: "
                         + sql_dialects.parse_problem(exc)) from None
    if not statements:
        raise ValueError("The query is empty.")
    out = []
    for v, st in zip(sql_policy.classify(statements), statements):
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
    parts = batches(query)
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
        never = next((why.format(m.group().capitalize()) for rule, why in _NEVER_WORDS if (m := rule.search(bare))), "") or next(
            (why for rule, why in _NEVER_NAMES if rule.search(names)), "")
        if never:
            out.append(sql_policy.Verdict(n, "fabric_mutation", never))
            continue
        defines = _DEFINES.match(names)
        if defines:  # its body was checked above; sqlglot can't read most bodies, so it isn't parsed
            out.append(sql_policy.Verdict(n, "warehouse_write", f"defines {defines.group(1).upper()} {defines.group(2)}"
                                          .rstrip()))
            continue
        try:
            statements = [st for st in sqlglot.parse(batch, read="tsql", error_level=ErrorLevel.RAISE) if st is not None]
        except ParseError:
            out.append(sql_policy.Verdict(n, "fabric_mutation", "it couldn't be read as T-SQL, so it can't be checked; "
                                                                 "a procedure or function definition can be"))
            continue
        for st in statements:
            out.append(_change_verdict(len(out) + 1, st))
    return out


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
_TRAILING_GO = re.compile(r"\n[ \t]*GO[ \t]*;?[ \t]*\s*$", re.IGNORECASE)


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


def run(job: dict[str, Any], token: Callable[[], str], say: Callable[..., None]) -> None:
    """Run the job's query and stream the last result's rows into the job's Arrow IPC file. A job
    with ``writes`` runs its batches in one transaction and commits it once they all succeeded. A job
    with ``plan`` (WH-09) doesn't run its query: with SHOWPLAN_XML on, Fabric returns the estimated
    plan as one row of XML instead, and the session is set back before the connection closes."""
    import pyarrow as pa

    writes = bool(job.get("writes"))
    checked = verdicts(job["query"], writes=writes)
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
        for batch in (batches(job["query"]) if writes else [job["query"]]):
            cursor.execute(batch)
            while True:
                if cursor.description:
                    result = _write_result(cursor, out, say)  # each result replaces the one before: the last is shown
                elif writes and isinstance(getattr(cursor, "rowcount", -1), int) and cursor.rowcount > 0:
                    affected += cursor.rowcount
                if not writes or not cursor.nextset():
                    break
        if plan:
            cursor.execute("SET SHOWPLAN_XML OFF")
        if result is None:
            with pa.ipc.new_file(str(out), pa.schema([])):
                pass
            result = (0, [], pa.schema([]))
        if writes:
            con.commit()
            committed = True
        rows, batch_rows, schema = result
        say("done", rows=rows, batches=batch_rows, columns=[{"name": f.name, "type": str(f.type)} for f in schema],
            **({"affected": affected, "committed": True} if writes else {}))
    finally:
        try:
            if not committed:
                con.rollback()
        finally:
            con.close()


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

    fields = []
    for i, d in enumerate(description):
        kind, precision, scale = d[1], d[4], d[5]
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
        elif kind is dt.date:
            t = pa.date32()
        elif kind is dt.time:
            t = pa.time64("us")
        elif kind is bytes:
            t = pa.binary()
        else:
            t = pa.string()  # text, uniqueidentifier, xml and anything new
        fields.append(pa.field(str(d[0] or f"column{i + 1}"), t))
    return pa.schema(fields)


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
