"""The local SQL engine for one project: DuckDB on this PC, no Fabric sign-in or capacity (FD-LDE-001).

One engine per open project, holding one DuckDB connection. Requests from the app share it one at a
time (DuckDB connections aren't for concurrent use), so temporary tables last for the session;
catalog reads use their own cursor and don't wait for a running query. A query can be cancelled or
given a time limit, and every query returns its columns, rows, row count and timings.

The database file is DuckDB's: another process (a second copy of the app, a notebook) can't open it
for writing while this engine has it, so two writers never corrupt it. That conflict is reported
in words; it isn't retried.

Discovered files become views (``CREATE VIEW dbo.orders AS SELECT * FROM read_parquet([...])``), so
they persist with the database and DuckDB reads only the columns and row groups a query needs. A
Delta table becomes a view over the Parquet files of its current version, read from the Delta log;
a table using a Delta feature that a plain Parquet read would get wrong (deletion vectors, column
mapping) is listed but not made into a view.
"""

from __future__ import annotations

import re
import sys
import threading
from contextlib import contextmanager
import time
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from .discovery import Found, Source, discover
from .projects import Project, ProjectStore

_REFRESH_EVERY = 2.0  # seconds between automatic looks for changed files
DIALECTS = ("duckdb", "spark")


class EngineBusy(RuntimeError):
    pass


def q(name: str) -> str:
    """A quoted DuckDB identifier."""
    return '"' + name.replace('"', '""') + '"'


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _paths(paths: list[str]) -> str:
    return "[" + ", ".join(lit(Path(p).as_posix()) for p in paths) + "]"


# Delta features a read of the data files alone would get wrong.
_UNREADABLE = {"deletionVectors": "deletion vectors", "columnMapping": "column mapping",
               "typeWidening": "type widening", "v2Checkpoint": None, "timestampNtz": None,
               "invariants": None, "appendOnly": None, "checkConstraints": None, "generatedColumns": None,
               "changeDataFeed": None, "identityColumns": None, "domainMetadata": None, "rowTracking": None,
               "vacuumProtocolCheck": None, "inCommitTimestamp": None, "icebergCompatV1": None,
               "icebergCompatV2": None, "variantType": "variant columns", "variantType-preview": "variant columns"}


def delta_select(folder: str) -> tuple[str, dict[str, Any]]:
    """A SELECT over the current version of the Delta table in ``folder``, and facts about it."""
    from deltalake import DeltaTable

    table = DeltaTable(folder)
    protocol = table.protocol()
    features = set(protocol.reader_features or []) | set(protocol.writer_features or [])
    config = table.metadata().configuration or {}
    problems = sorted({label for f in features if (label := _UNREADABLE.get(f, f"the {f} feature"))})
    if str(config.get("delta.columnMapping.mode", "none")).lower() not in ("", "none"):
        problems.append("column mapping")
    if protocol.min_reader_version > 3:
        problems.append(f"reader version {protocol.min_reader_version}")
    info = {"version": table.version(), "reader_version": protocol.min_reader_version,
            "writer_version": protocol.min_writer_version, "features": sorted(features),
            "partitions": table.metadata().partition_columns}
    if problems:
        raise ValueError("This Delta table uses " + ", ".join(sorted(set(problems))) +
                         ", which a local read can't apply yet, so it isn't queryable here.")
    schema = pa.schema(table.schema().to_arrow()) if hasattr(table.schema(), "to_arrow") else \
        table.schema().to_pyarrow()
    files = [f.removeprefix("file://") for f in table.file_uris()]
    if not files:
        return _empty_select(schema), info
    partitions = info["partitions"]
    for part in partitions:
        if not all(f"/{part}=" in f.replace("\\", "/") for f in files):
            raise ValueError(f"The table's files aren't in {part}=... folders, so its partition column "
                             f"{part} can't be read locally yet.")
    options = "union_by_name = true"
    if partitions:
        types = _duckdb_types(pa.schema([schema.field(p) for p in partitions]))
        options += ", hive_partitioning = true, hive_types = {" + \
            ", ".join(f"{lit(p)}: {lit(types[p])}" for p in partitions) + "}"
    columns = ", ".join(q(f.name) for f in schema)
    return f"SELECT {columns} FROM read_parquet({_paths(files)}, {options})", info


def _duckdb_types(schema: pa.Schema) -> dict[str, str]:
    con = duckdb.connect()
    con.register("__empty", schema.empty_table())
    return {row[0]: row[1] for row in con.execute("DESCRIBE SELECT * FROM __empty").fetchall()}


def _empty_select(schema: pa.Schema) -> str:
    types = _duckdb_types(schema)
    if not types:
        return "SELECT NULL WHERE false"
    return "SELECT " + ", ".join(f"CAST(NULL AS {t}) AS {q(n)}" for n, t in types.items()) + " WHERE false"


def file_select(found: Found) -> str:
    if found.format == "delta":
        return delta_select(found.folder)[0]
    if found.format == "excel":
        raise ValueError("Excel workbooks are found but can't be read yet: that needs a package the engine "
                         "doesn't ship. Save the sheet as CSV for now.")
    source = _paths(found.paths)
    hive = ", hive_partitioning = true" if found.hive else ""
    reader = {"parquet": "read_parquet", "csv": "read_csv", "json": "read_json"}[found.format]
    return f"SELECT * FROM {reader}({source}, union_by_name = true{hive})"


_TYPE_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]*(\(\s*\d+\s*(,\s*\d+\s*)?\))?(\[\])?$")


def with_overrides(select: str, overrides: dict[str, str]) -> str:
    if not overrides:
        return select
    for column, type_ in overrides.items():
        if not _TYPE_OK.match(type_ or ""):
            raise ValueError(f"{type_!r} is not a column type (for {column}).")
    casts = ", ".join(f"CAST({q(c)} AS {t}) AS {q(c)}" for c, t in overrides.items())
    return f"SELECT * REPLACE ({casts}) FROM ({select})"


def _friendly_open_error(exc: Exception, path: str) -> Exception:
    text = str(exc)
    if "lock" in text.lower():
        return EngineBusy(f"{path} is open in another program (another Fabric Desktop window or a notebook), "
                          f"so this one can't write to it. Close it there and try again. ({text})")
    return exc


class LocalEngine:
    def __init__(self, project: Project, store: ProjectStore):
        self.project = project
        self.store = store
        self._con: duckdb.DuckDBPyConnection | None = None
        # One request at a time on the connection. Not re-entrant: a streamed result holds it until
        # the reader is used up or closed, and may be closed on another thread.
        self._lock = threading.Lock()
        self._meta_lock = threading.Lock()    # project.json and the catalog
        self._running: dict[str, dict[str, Any]] = {}
        # Cancelled before they started (a background query whose thread hadn't reached DuckDB yet).
        self._cancel_first: set[str] = set()
        self._last_check = 0.0
        self.opened_at = 0.0

    # Lifecycle

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """The connection, opened if needed. Only for use while holding no lock."""
        self.open()
        return self._con  # type: ignore[return-value]

    def open(self) -> None:
        with self._lock:
            self._open_locked()

    def _open_locked(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            p = self.project
            p.root.mkdir(parents=True, exist_ok=True)
            p.temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                con = duckdb.connect(p.database)
            except duckdb.Error as exc:
                raise _friendly_open_error(exc, p.database) from None
            # UTC, like Fabric. SET, not connect's config: that tries to download ICU, which fails offline.
            con.execute("SET TimeZone = 'UTC'")
            con.execute(f"SET temp_directory = {lit(str(p.temp_dir))}")
            # dbo first, as in Fabric's SQL endpoints and SQL Server: CREATE TABLE t makes dbo.t, and
            # dbo.orders can be written orders.
            con.execute("CREATE SCHEMA IF NOT EXISTS dbo")
            con.execute("SET search_path = 'dbo,main'")
            self._con = con
            self.apply_settings()
            self.opened_at = time.time()
            if p.storage == "memory":  # nothing persisted: make the views again
                for source in p.sources:
                    self._refresh_source(source, force=True)
        return self._con

    def apply_settings(self) -> None:
        s = self.project.settings
        con = self._con
        if con is None:
            return
        con.execute(f"SET memory_limit = {lit(s.memory_limit)}" if s.memory_limit else "RESET memory_limit")
        con.execute(f"SET threads = {int(s.threads)}" if s.threads else "RESET threads")
        con.execute(f"SET max_temp_directory_size = {lit(s.max_temp_size)}" if s.max_temp_size
                    else "RESET max_temp_directory_size")

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                try:
                    if self.project.storage == "persistent":
                        self._con.execute("CHECKPOINT")  # fold the write-ahead log into the file
                finally:
                    self._con.close()
                    self._con = None

    @property
    def is_open(self) -> bool:
        return self._con is not None

    # Catalog

    def refresh(self, source_path: str | None = None, force: bool = True) -> dict[str, Any]:
        """Look at the registered folders again; make, change or drop views for what changed."""
        sources = [self.project.source(source_path)] if source_path else list(self.project.sources)
        if None in sources:
            raise KeyError(f"{source_path} is not a registered folder of {self.project.name}.")
        with self._lock:
            self._open_locked()
            changed = [s.path for s in sources if self._refresh_source(s, force=force)]
        self._last_check = time.monotonic()
        return {"changed": changed, "tables": self.tables()}

    def refresh_if_changed(self) -> None:
        if not self.project.settings.auto_refresh or time.monotonic() - self._last_check < _REFRESH_EVERY:
            return
        self.refresh(force=False)

    def _refresh_source(self, source: Source, force: bool) -> bool:
        found = discover(source)
        state = self.project.refreshed.get(source.path, {})
        if not force and state.get("fingerprint") == found.fingerprint:
            return False
        con = self._con
        assert con is not None
        catalog = self.project.catalog
        mine = {k for k, v in catalog.items() if v.get("source") == source.path}
        issues = [{"path": i.path, "problem": i.problem} for i in found.issues]
        others = {k for k, v in catalog.items() if v.get("source") != source.path}
        keep: set[str] = set()
        for table in found.tables:
            key = table.key
            entry = {"schema": table.schema, "table": table.table, "format": table.format, "source": source.path,
                     "paths": table.paths if table.files <= 50 else table.paths[:1], "folder": table.folder,
                     "files": table.files, "bytes": table.size, "status": "ok", "error": "", "columns": []}
            if key in others:
                issues.append({"path": table.folder or table.paths[0],
                               "problem": f"{table.schema}.{table.table} already comes from another folder "
                                          f"({catalog[key]['source']}). Set a different name for one of them."})
                continue
            if key not in mine and self._exists(table.schema, table.table):
                issues.append({"path": table.folder or table.paths[0],
                               "problem": f"{table.schema}.{table.table} is already a table or view you made in "
                                          "this project, so the file wasn't put over it. Set a different name."})
                continue
            keep.add(key)
            try:
                if table.format == "delta":
                    select, info = delta_select(table.folder)
                    entry["delta"] = info
                else:
                    select = file_select(table)
                select = with_overrides(select, self.project.overrides.get(key, {}))
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {q(table.schema)}")
                con.execute(f"CREATE OR REPLACE VIEW {q(table.schema)}.{q(table.table)} AS {select}")
                entry["columns"] = [{"name": r[0], "type": r[1]} for r in
                                    con.execute(f"DESCRIBE {q(table.schema)}.{q(table.table)}").fetchall()]
            except Exception as exc:  # one unreadable file never stops the others
                entry["status"], entry["error"] = "error", _short(exc)
                con.execute(f"DROP VIEW IF EXISTS {q(table.schema)}.{q(table.table)}")
            catalog[key] = entry
        for key in mine - keep:
            old = catalog.pop(key)
            con.execute(f"DROP VIEW IF EXISTS {q(old['schema'])}.{q(old['table'])}")
        self.project.refreshed[source.path] = {"fingerprint": found.fingerprint, "issues": issues,
                                               "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with self._meta_lock:
            self.store.save(self.project)
        return True

    def _exists(self, schema: str, table: str) -> bool:
        row = self._con.execute(  # type: ignore[union-attr]
            "SELECT count(*) FROM duckdb_tables() WHERE lower(schema_name) = lower(?) AND lower(table_name) = lower(?) "
            "AND database_name = current_database()", [schema, table]).fetchone()
        views = self._con.execute(  # type: ignore[union-attr]
            "SELECT count(*) FROM duckdb_views() WHERE lower(schema_name) = lower(?) AND lower(view_name) = lower(?) "
            "AND NOT internal AND database_name = current_database()", [schema, table]).fetchone()
        return bool(row[0] or views[0])

    def remove_source(self, path: str) -> None:
        source = self.project.source(path)
        if source is None:
            raise KeyError(f"{path} is not a registered folder of {self.project.name}.")
        with self._lock:
            con = self._open_locked()
            for key in [k for k, v in self.project.catalog.items() if v.get("source") == source.path]:
                old = self.project.catalog.pop(key)
                con.execute(f"DROP VIEW IF EXISTS {q(old['schema'])}.{q(old['table'])}")
            self.project.sources.remove(source)
            self.project.refreshed.pop(source.path, None)
            with self._meta_lock:
                self.store.save(self.project)

    def tables(self) -> list[dict[str, Any]]:
        """Every schema, table, view and column in the project: discovered files and what people made."""
        # The session's own connection sees its temporary tables; while a query holds it, a cursor
        # lists the rest without waiting.
        self.open()
        own = self._lock.acquire(blocking=False)
        con = self._con if own else self._con.cursor()  # type: ignore[union-attr]
        try:
            discovered = {k: v for k, v in self.project.catalog.items()}
            out: list[dict[str, Any]] = []
            cols: dict[tuple[str, str], list[dict[str, str]]] = {}
            for schema, table, column, type_ in con.execute(
                    "SELECT schema_name, table_name, column_name, data_type FROM duckdb_columns() "
                    "WHERE database_name = current_database() AND NOT internal ORDER BY column_index").fetchall():
                cols.setdefault((schema, table), []).append({"name": column, "type": type_})
            for schema, name, kind, temporary in con.execute(
                    "SELECT schema_name, table_name, 'table', temporary FROM duckdb_tables() "
                    "WHERE NOT internal AND (database_name = current_database() OR temporary) UNION ALL "
                    "SELECT schema_name, view_name, 'view', temporary FROM duckdb_views() "
                    "WHERE NOT internal AND (database_name = current_database() OR temporary)").fetchall():
                key = f"{schema}.{name}".lower()
                entry = discovered.pop(key, None)
                if entry is not None:
                    out.append({**entry, "kind": "file", "columns": cols.get((schema, name), entry["columns"])})
                else:
                    out.append({"schema": schema, "table": name, "kind": "temp table" if temporary else kind,
                                "status": "ok", "columns": cols.get((schema, name), [])})
            out += [{**e, "kind": "file"} for e in discovered.values()]  # unreadable: no view
            return sorted(out, key=lambda t: (t["schema"].lower(), t["table"].lower()))
        finally:
            if own:
                self._lock.release()
            else:
                con.close()

    def set_override(self, table: str, columns: dict[str, str]) -> dict[str, Any]:
        key = table.lower()
        entry = self.project.catalog.get(key)
        if entry is None:
            raise KeyError(f"{table} is not a discovered file in {self.project.name}.")
        for type_ in columns.values():
            if not _TYPE_OK.match(type_ or ""):
                raise ValueError(f"{type_!r} is not a column type.")
        if columns:
            self.project.overrides[key] = dict(columns)
        else:
            self.project.overrides.pop(key, None)
        source = self.project.source(entry["source"])
        with self._lock:
            self._open_locked()
            self._refresh_source(source, force=True)  # type: ignore[arg-type]
        return self.project.catalog[key]

    # Queries

    def execute(self, sql: str, dialect: str = "duckdb", limit: int = 1000, timeout: float | None = None,
                query_id: str | None = None, count: bool = True) -> dict[str, Any]:
        """Run every statement in ``sql``; return the last one's result with timings."""
        limit = max(0, min(int(limit), 100_000))
        with self._query(sql, dialect, timeout, query_id) as (result, run):
            payload = _collect(result, limit, count)
        payload.update(seconds=round(time.monotonic() - run["started"], 3), **_about(run, self.project.id))
        return payload

    def execute_reader(self, sql: str, dialect: str = "duckdb", timeout: float | None = None,
                       query_id: str | None = None, batch_size: int = 10_000) -> "ResultStream":
        """Run every statement in ``sql``; stream the last one's whole result, with no row limit.

        The project's connection stays busy until the stream is read to the end or closed, so do one
        or the other (``with engine.execute_reader(...) as stream:`` closes it). ``cancel(query_id)``
        and the time limit stop it mid-stream: reading then raises QueryCancelled or TimeoutError.
        ``stream.reader`` is a pyarrow RecordBatchReader for code that needs one."""
        query = self._query(sql, dialect, timeout, query_id)
        result, _run = query.__enter__()
        try:
            if result is None or not result.description:
                inner = pa.RecordBatchReader.from_batches(pa.schema([]), iter(()))
            else:
                reader_of = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
                inner = reader_of(max(1, int(batch_size)))
        except BaseException:
            if not query.__exit__(*sys.exc_info()):
                raise
            raise

        def batches():
            try:
                yield from inner
            except BaseException:
                if not query.__exit__(*sys.exc_info()):
                    raise
            else:
                query.__exit__(None, None, None)
        # Start it now, so closing or dropping the reader before the first batch still frees the connection.
        stream = batches()
        try:
            first = next(stream)
        except StopIteration:
            return ResultStream(inner.schema, iter(()), stream)
        return ResultStream(inner.schema, _chain(first, stream), stream)

    @contextmanager
    def _query(self, sql: str, dialect: str, timeout: float | None, query_id: str | None):
        if dialect not in DIALECTS:
            if dialect == "tsql":
                raise ValueError("T-SQL isn't translated for the local engine yet (FD-LDE-004). "
                                 "Use DuckDB or Spark SQL.")
            raise ValueError(f"dialect must be one of {DIALECTS}, got {dialect!r}")
        if not (sql or "").strip():
            raise ValueError("The query is empty.")
        query_id = query_id or uuid.uuid4().hex
        timeout = self.project.settings.timeout_seconds if timeout is None else timeout
        self.refresh_if_changed()
        with self._lock:
            con = self._open_locked()
            started = time.monotonic()
            state = {"cancelled": False, "timed_out": False, "started": started}
            self._running[query_id] = state
            if query_id in self._cancel_first:
                self._cancel_first.discard(query_id)
                self._running.pop(query_id, None)
                raise QueryCancelled("The query was cancelled.")
            timer = None
            if timeout and timeout > 0:
                def expire():
                    state["timed_out"] = True
                    con.interrupt()
                timer = threading.Timer(timeout, expire)
                timer.daemon = True
                timer.start()
            steps: list[dict[str, Any]] = []
            try:
                statements = _split(con, sql, dialect)
                result = None
                for i, statement in enumerate(statements):
                    if state["cancelled"]:  # between statements, or before DuckDB saw the interrupt
                        raise duckdb.InterruptException("cancelled")
                    step_started = time.monotonic()
                    result = con.execute(statement)
                    steps.append({"statement": i + 1, "type": _kind(statement),
                                  "seconds": round(time.monotonic() - step_started, 3)})
                yield result, {"query_id": query_id, "started": started, "statements": steps, "dialect": dialect}
            except duckdb.InterruptException:
                if state["timed_out"]:
                    raise TimeoutError(f"The query ran longer than {timeout:g} seconds and was stopped.") from None
                raise QueryCancelled("The query was cancelled.") from None
            finally:
                if timer:
                    timer.cancel()
                self._running.pop(query_id, None)

    def cancel(self, query_id: str | None = None, not_started: bool = False) -> bool:
        """Stop the running query (``query_id``'s, or any). True when one was running.

        ``not_started``: ``query_id`` is about to run (its thread hasn't reached the engine yet);
        it is stopped as soon as it starts."""
        if query_id and query_id not in self._running:
            if not_started:
                self._cancel_first.add(query_id)
                return True
            return False
        if not self._running:
            return False
        for qid, state in list(self._running.items()):
            if query_id in (None, qid):
                state["cancelled"] = True
        threading.Thread(target=self._interrupt_until_stopped, args=(query_id,), daemon=True,
                         name="local query cancel").start()
        return True

    def _interrupt_until_stopped(self, query_id: str | None, seconds: float = 10.0) -> None:
        """Interrupt until the query has stopped: an interrupt that lands just before DuckDB starts a
        statement is otherwise lost."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            running = [q for q, st in list(self._running.items()) if st["cancelled"] and query_id in (None, q)]
            if not running or self._con is None:
                return
            self._con.interrupt()
            time.sleep(0.05)

    @property
    def running(self) -> list[str]:
        return list(self._running)


class QueryCancelled(RuntimeError):
    pass


def _split(con: duckdb.DuckDBPyConnection, sql: str, dialect: str) -> list[str]:
    if dialect == "spark":
        import sqlglot
        from sqlglot.errors import ParseError
        try:
            return [s for s in sqlglot.transpile(sql, read="spark", write="duckdb") if s.strip()]
        except ParseError as exc:
            raise ValueError(f"Could not read the query as Spark SQL: {exc}") from None
    try:
        return [s.query for s in con.extract_statements(sql)]
    except duckdb.Error as exc:
        raise ValueError(_short(exc)) from None


def _kind(statement: str) -> str:
    word = statement.lstrip().split(None, 1)[0].lower() if statement.strip() else ""
    return {"with": "select", "from": "select", "values": "select", "table": "select"}.get(word, word)


class ResultStream:
    """A query's whole result, batch by batch. Closing it (or reading it to the end) frees the
    project's connection; a pyarrow reader's own close() wouldn't, so this wraps one."""

    def __init__(self, schema: pa.Schema, batches: Any, generator: Any):
        self.schema = schema
        self._generator = generator
        self.reader = pa.RecordBatchReader.from_batches(schema, batches)

    def __iter__(self):
        return iter(self.reader)

    def read_next_batch(self) -> pa.RecordBatch:
        return self.reader.read_next_batch()

    def read_all(self) -> pa.Table:
        try:
            return self.reader.read_all()
        finally:
            self.close()

    def __arrow_c_stream__(self, requested_schema: Any = None) -> Any:
        return self.reader.__arrow_c_stream__(requested_schema)

    def close(self) -> None:
        self._generator.close()  # runs the query's cleanup: frees the connection
        self.reader.close()

    def __enter__(self) -> "ResultStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _about(run: dict[str, Any], project: str) -> dict[str, Any]:
    return {"query_id": run["query_id"], "statements": run["statements"], "engine": "local",
            "dialect": run["dialect"], "project": project}


def _chain(first: pa.RecordBatch, rest: Any) -> Any:
    yield first
    yield from rest


def _collect(result: Any, limit: int, count: bool) -> dict[str, Any]:
    if result is None or not result.description:
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
    reader_of = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
    reader = reader_of(min(max(limit, 1), 10_000) if limit else 10_000)
    kept: list[pa.RecordBatch] = []
    have = total = 0
    truncated = False
    for batch in reader:
        total += batch.num_rows
        if have < limit:
            take = batch.slice(0, limit - have)
            kept.append(take)
            have += take.num_rows
        if total > limit:
            truncated = True
            if not count:
                break
    table = pa.Table.from_batches(kept, schema=reader.schema)
    columns = [c.to_pylist() for c in table.columns]
    return {"columns": [{"name": f.name, "type": str(f.type)} for f in reader.schema],
            "rows": [[_json(v) for v in row] for row in zip(*columns)],
            "row_count": total if (count or not truncated) else None, "truncated": truncated}


def _json(value: Any) -> Any:
    import math
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def _short(exc: Exception) -> str:
    text = str(exc).strip()
    return text.split("\n\nLINE")[0][:1000] or type(exc).__name__
