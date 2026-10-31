"""One SQL query page run, in a process of its own (SQL-003; started by sql_exec.py).

The engine writes one JSON line to stdin: the query, its dialect, the lakehouse, where to write
the result, the limits and the classes of statement the run may
use (sql_policy.py). This process then:

1. reads the SQL and refuses it, before anything runs, if a statement is beyond the allowed classes;
2. opens DuckDB with the memory limit and temp folder, then turns external access off and locks
   the configuration, so no statement can read or write files, load extensions or reach the
   network: only the lakehouse tables the engine bound are readable;
3. runs the statements and streams the last one's result, batch by batch, into an Arrow IPC file
   (the snapshot the page pages through and exports from). The result is never held in memory
   whole and never counted with COUNT(*): the row count is what was written.

It reports on stdout, one JSON line each: ``started``, ``progress`` (rows so far), then ``done``
(columns, rows, the rows in each batch) or ``error``. When it first reads a lakehouse table it asks
the engine for a storage token (``call``), answered on stdin; the token stays in memory and is
never printed or logged. Stopping a run ends this process; nothing else (no notebook kernel, no
other run) is touched.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

BATCH_ROWS = 65_536
PROGRESS_SECONDS = 0.25


def _say(event: str, **fields: Any) -> None:
    sys.stdout.write(json.dumps({"event": event, **fields}, default=str) + "\n")
    sys.stdout.flush()


def run(job: dict[str, Any]) -> None:
    if job.get("target") == "warehouse":  # SQL-004: one read query in Fabric (warehouse.py)
        from . import warehouse
        warehouse.run(job, lambda: str(_ask("warehouse_token") or ""), _say)
        return
    import duckdb
    import pyarrow as pa
    import pyarrow.ipc as ipc

    from . import lakehouse, sql, sql_policy
    from .duckdb_rows import keep_try_cast

    lh = job["lakehouse"]
    lakehouse.attach(lakehouse.Attached(
        workspace_id=lh["workspace_id"], lakehouse_id=lh["lakehouse_id"], name=lh["name"],
        workspace_name=lh.get("workspace_name") or "", storage_token=_storage_token, work_dir=Path(job["work_dir"]),
        schemas_enabled=lambda: bool(lh["schemas_enabled"] if "schemas_enabled" in lh else _ask("schemas_enabled"))))
    if job.get("local_root"):  # tests: the lakehouse's tables are local Delta folders
        from deltalake import DeltaTable
        root = Path(job["local_root"])
        lakehouse.open_table = lambda name: DeltaTable(str(root / lakehouse.table_ref(name).table))

    statements = sql.parse(job["query"], job["dialect"])
    verdicts = sql_policy.classify(statements)
    stop = sql_policy.refused(verdicts, tuple(job["allow"]))
    if stop is not None:
        _say("error", kind="permission", permission=stop.kind, statement=stop.statement,
             message=f"Statement {stop.statement} {sql_policy.LABELS[stop.kind]} ({stop.reason}), so nothing ran. "
                     + _advice(stop.kind))
        return

    con = keep_try_cast(duckdb.connect())
    con.execute("SET TimeZone = 'UTC'")  # as in sql.run: Fabric's Spark sessions are UTC
    if job.get("memory_mb"):
        con.execute(f"SET memory_limit = '{int(job['memory_mb'])}MB'")
    if job.get("threads"):
        con.execute(f"SET threads = {int(job['threads'])}")
    temp = Path(job["temp_dir"])
    temp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{_quote(str(temp))}'")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    _say("started", permission=sql_policy.strongest(verdicts))

    views: dict[str, str] = {}
    result = None
    for statement in statements:
        sql.bind_tables(statement, con, views, lambda name: sql.is_local(con, name))
        result = con.execute(sql.duckdb_sql(statement))

    out = Path(job["out"])
    rows, batches, last = 0, [], time.monotonic()
    reader = None
    if result is not None and result.description:
        reader = (getattr(result, "to_arrow_reader", None) or result.fetch_record_batch)(BATCH_ROWS)
        schema = reader.schema
    else:
        schema = pa.schema([])
    with ipc.new_file(str(out), schema) as writer:
        for batch in reader or []:
            if not batch.num_rows:
                continue
            writer.write_batch(batch)
            rows += batch.num_rows
            batches.append(batch.num_rows)
            if time.monotonic() - last >= PROGRESS_SECONDS:
                _say("progress", rows=rows)
                last = time.monotonic()
    _say("done", rows=rows, batches=batches,
         columns=[{"name": f.name, "type": str(f.type)} for f in schema])


def _quote(text: str) -> str:
    return text.replace("'", "''")


def _advice(kind: str) -> str:
    if kind == "local_mutation":
        return "Run it again and confirm to let it change this run's own session."
    if kind == "onelake_write":
        return "The SQL page only reads lakehouse tables: write a result with Save as table, or use a notebook."
    return "The SQL page runs queries on this PC only and never changes Fabric, files or extensions."


_replies: queue.Queue[dict[str, Any]] = queue.Queue()
_asked: dict[str, Any] = {}


def _ask(name: str) -> Any:
    """Ask the engine, once, for something only it has: ``token`` (the storage token),
    ``warehouse_token`` (a warehouse run's Azure SQL token) or ``schemas_enabled`` (the lakehouse's
    layout, from Fabric). Asked the first time a lakehouse
    table is read, so a query that reads no table never needs a sign-in. Kept in memory only."""
    if name not in _asked:
        _say("call", name=name)
        try:
            reply = _replies.get(timeout=600)  # a sign-in window may be open
        except queue.Empty:
            raise RuntimeError("Signing in took too long, so the query stopped.") from None
        if reply.get("error"):
            raise RuntimeError(str(reply["error"]))
        _asked[name] = reply.get("value")
    return _asked[name]


def _storage_token() -> str:
    return str(_ask("token") or "")


def _listen() -> None:
    """Answers from the engine come on stdin. The engine keeps it open while it runs; when it
    closes (the app quit or the engine was ended), this run ends too rather than going on unwatched."""
    for line in _engine:
        try:
            _replies.put(json.loads(line))
        except ValueError:
            continue
    os._exit(3)


def _take_stdin() -> Any:
    """Move the engine's pipe off stdin and give stdin the null device.

    _listen keeps a read pending on the pipe while the query runs. On Windows, while a read is
    pending on a synchronous pipe, anything else that asks about that handle (a library checking
    whether stdin is a terminal, as imports do) waits for the read, which only ends when the engine
    writes. The worker then froze before saying anything. Nothing else ever sees the pipe now."""
    engine = os.fdopen(os.dup(0), "r", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        ctypes.windll.kernel32.SetStdHandle(-10, msvcrt.get_osfhandle(0))  # STD_INPUT_HANDLE
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - stdin for the process's life
    return engine


_engine: Any = None


def main() -> None:
    global _engine
    _engine = _take_stdin()
    line = _engine.readline()
    try:
        job = json.loads(line)
    except ValueError:
        _say("error", kind="engine", message="The run got no instructions.")
        return
    threading.Thread(target=_listen, daemon=True, name="engine-listen").start()
    try:
        run(job)
    except BaseException as exc:  # noqa: BLE001 - every failure goes back to the engine as a message
        message = str(exc).strip() or type(exc).__name__
        if "configuration has been locked" in message:
            message = ("A query can't change the run's settings (memory, threads, files). They come from "
                       "Settings › SQL.")
        _say("error", kind="sql", message=message[:4000])
    # Leave without the interpreter's shutdown. _listen is still blocked reading stdin, which the
    # engine keeps open until this process ends; on Windows closing stdin then waits for that read
    # to finish, so a normal exit would hang every finished run.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
