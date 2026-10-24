"""Read-only queries on a Fabric Data warehouse or SQL analytics endpoint (SQL-004, Preview).

The SQL page's run has ``target="warehouse"``: the query runs in Fabric, in the warehouse, as the
person who signed in, so it uses capacity. This stage only reads:

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

The run is a process of its own (sql_worker.py, like a lakehouse run), so Stop and the time limit
end that process, which closes the connection; Fabric stops the query when its connection closes.
The worker asks the engine for the token (``call`` ``warehouse_token``) and keeps it in memory only.
"""

from __future__ import annotations

import datetime as dt
import decimal
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError

from . import sql_policy

KINDS = ("warehouse", "endpoint")  # a Data warehouse, or a lakehouse's SQL analytics endpoint
HOST_SUFFIX = ".datawarehouse.fabric.microsoft.com"
BATCH_ROWS = 65_536
PROGRESS_SECONDS = 0.25
LOGIN_TIMEOUT_S = 60
_OUTSIDE = {"openrowset", "openquery", "opendatasource"}
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


def verdicts(query: str) -> list[sql_policy.Verdict]:
    """What each statement of ``query`` is. Only a single statement that reads is read_only."""
    try:
        statements = [s for s in sqlglot.parse(query, read="tsql", error_level=ErrorLevel.RAISE) if s is not None]
    except ParseError as exc:
        raise ValueError(f"The query couldn't be read as T-SQL, so it can't be shown to only read: {exc}") from None
    if not statements:
        raise ValueError("The query is empty.")
    out = []
    for v, st in zip(sql_policy.classify(statements), statements):
        if v.statement > 1:
            v = sql_policy.Verdict(v.statement, "fabric_mutation", "a warehouse run is one query at a time")
        elif v.kind == "read_only":
            into = st.find(exp.Into)
            outside = next((n.name.lower() for n in st.find_all(exp.Anonymous) if n.name.lower() in _OUTSIDE), "")
            if into is not None:
                v = sql_policy.Verdict(1, "fabric_mutation", "SELECT ... INTO creates a table in the warehouse")
            elif outside:
                v = sql_policy.Verdict(1, "fabric_mutation", f"{outside.upper()} reads outside the warehouse")
            else:
                v = sql_policy.Verdict(1, "read_only", "reads the warehouse's tables")
        else:
            v = sql_policy.Verdict(v.statement, "fabric_mutation", f"{st.key.upper()} isn't a query that only reads")
        out.append(v)
    return out


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


def run(job: dict[str, Any], token: Callable[[], str], say: Callable[..., None]) -> None:
    """Run the job's one read query and stream its rows into the job's Arrow IPC file."""
    import pyarrow as pa
    import pyarrow.ipc as ipc

    stop = sql_policy.refused(verdicts(job["query"]), ("read_only",))
    if stop is not None:
        say("error", kind="permission", permission=stop.kind, statement=stop.statement,
            message=f"Statement {stop.statement} was refused ({stop.reason}), so nothing was sent. A warehouse run "
                    "only reads.")
        return
    wh = job["warehouse"]
    con = connect(wh["server"], wh["database"], token())
    try:
        if job.get("time_limit_s"):
            con.timeout = int(job["time_limit_s"])  # the driver cancels the query too, as well as Stop ending the process
        say("started", permission="read_only")
        cursor = con.cursor()
        cursor.execute(job["query"])
        out = Path(job["out"])
        rows, batches, last = 0, [], time.monotonic()
        chunk = cursor.fetchmany(BATCH_ROWS) if cursor.description else []
        schema = _schema(cursor.description, chunk) if cursor.description else pa.schema([])
        with ipc.new_file(str(out), schema) as writer:
            while chunk:
                writer.write_batch(_batch(schema, chunk))
                rows += len(chunk)
                batches.append(len(chunk))
                if time.monotonic() - last >= PROGRESS_SECONDS:
                    say("progress", rows=rows)
                    last = time.monotonic()
                chunk = cursor.fetchmany(BATCH_ROWS)
        say("done", rows=rows, batches=batches, columns=[{"name": f.name, "type": str(f.type)} for f in schema])
    finally:
        try:
            con.rollback()
        finally:
            con.close()


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
