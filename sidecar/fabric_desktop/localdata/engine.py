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

The project's own Delta tables (delta.py) are folders under ``<project>/Tables/<schema>/<table>``,
each a view in the same way. SQL that creates or adds to one is run here, between the other
statements: DuckDB runs the query, and delta-rs writes its Arrow stream in one commit.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from contextlib import contextmanager
import time
import uuid
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from .. import delta_profile
from . import delta
from .discovery import Found, Source, discover
from .projects import Project, ProjectStore

_REFRESH_EVERY = 2.0  # seconds between automatic looks for changed files
DIALECTS = ("duckdb", "spark")
OWN = "delta table"            # catalog kind of the project's own Delta tables
OWN_KEY = "<project tables>"   # their entry in project.refreshed
_WRITE_BATCH = 100_000


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


def delta_select(folder: str, version: int | None = None, timestamp: str | None = None,
                 name: str = "") -> tuple[str, dict[str, Any]]:
    """A SELECT over the Delta table in ``folder`` (its current version, or as of ``version`` or
    ``timestamp``), and facts about it."""
    table = _load(folder, version, timestamp, name)
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
    schema = _arrow_schema(table)
    files = [_local_path(f) for f in table.file_uris()]
    info.update(files=len(files), bytes=sum(_size(f) for f in files))
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


def _load(folder: str, version: int | None, timestamp: str | None, name: str) -> Any:
    from deltalake import DeltaTable

    table = DeltaTable(folder)
    if version is None and timestamp is None:
        return table
    latest = table.version()
    if version is not None:
        if version > latest:
            raise ValueError(f"{name or folder} has no version {version}: its latest is {latest}.")
        try:
            table.load_as_version(int(version))
        except Exception as exc:  # noqa: BLE001 - delta-rs's own words
            raise ValueError(f"{name or folder} can't be read at version {version}: {_short(exc)}") from None
        return table
    when = delta.as_of_time(timestamp or "")
    first = min((h.get("timestamp") or 0 for h in table.history()), default=0)
    if first and when.timestamp() * 1000 < first:
        start = _utc(first)
        raise ValueError(f"{name or folder} has no version as of {timestamp} (UTC): its first is from {start}.")
    try:
        table.load_as_version(when)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name or folder} has no version as of {timestamp} (UTC): {_short(exc)}") from None
    return table


def _arrow_schema(table: Any) -> pa.Schema:
    raw = table.schema()
    return pa.schema(raw.to_arrow()) if hasattr(raw, "to_arrow") else raw.to_pyarrow()


def _local_path(uri: str) -> str:
    path = uri.removeprefix("file://")
    if re.match(r"^/[A-Za-z]:", path):  # file:///C:/... on Windows
        path = path[1:]
    return path


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
            self._refresh_own()  # its Delta tables may have changed while it was closed
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
            if source_path is None and force:
                self._refresh_own()
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
                    out.append({**entry, "kind": entry.get("kind", "file"),
                                "columns": cols.get((schema, name), entry["columns"])})
                else:
                    out.append({"schema": schema, "table": name, "kind": "temp table" if temporary else kind,
                                "status": "ok", "columns": cols.get((schema, name), [])})
            out += [{**e, "kind": e.get("kind", "file")} for e in discovered.values()]  # unreadable: no view
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
    def _query(self, sql: str, dialect: str, timeout: float | None, query_id: str | None,
               write: "delta.Write | None" = None, dry_run: bool = False):
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
            as_of: list[str] = []
            run = {"query_id": query_id, "started": started, "statements": steps, "dialect": dialect}
            try:
                statements = self._statements(con, sql, dialect)
                if write is not None:
                    statements = [self._as_write(statements, write)]
                result = None
                for i, step in enumerate(statements):
                    if state["cancelled"]:  # between statements, or before DuckDB saw the interrupt
                        raise duckdb.InterruptException("cancelled")
                    step_started = time.monotonic()
                    as_of += self._make_as_of(con, step["as_of"])
                    if step["write"] is not None:
                        summary = self._write(con, step["write"], state, query_id, dry_run=dry_run)
                        run["write"] = summary
                        result = con.execute(
                            'SELECT ?::VARCHAR AS "table", ?::VARCHAR AS mode, ?::BIGINT AS version, '
                            '?::BIGINT AS rows_written',
                            [summary["table"], summary["mode"], summary["version"], summary["rows"]])
                    else:
                        result = con.execute(step["sql"])
                    steps.append({"statement": i + 1, "type": step["kind"],
                                  "seconds": round(time.monotonic() - step_started, 3)})
                yield result, run
            except duckdb.InterruptException:
                if state["timed_out"]:
                    raise TimeoutError(f"The query ran longer than {timeout:g} seconds and was stopped.") from None
                raise QueryCancelled("The query was cancelled.") from None
            finally:
                if timer:
                    timer.cancel()
                self._running.pop(query_id, None)
                for name in as_of:
                    try:
                        con.execute(f"DROP VIEW IF EXISTS temp.{q(name)}")
                    except duckdb.Error:
                        pass

    # Statements, and the project's own Delta tables

    def _statements(self, con: duckdb.DuckDBPyConnection, sql: str, dialect: str) -> list[dict[str, Any]]:
        """Each statement as DuckDB SQL, or as the Delta write it makes, with any tables it reads
        at a version or time."""
        steps: list[dict[str, Any]] = []
        n = 0
        if dialect == "spark":
            import sqlglot
            from sqlglot.errors import ParseError
            try:
                trees = [t for t in sqlglot.parse(sql, read="spark") if t is not None]
            except ParseError as exc:
                raise ValueError(f"Could not read the query as Spark SQL: {exc}") from None
            for tree in trees:
                found = delta.rewrite_tree(tree, n)
                n += len(found)
                write = delta.parse_tree(tree, self._is_own)
                text = "" if write else tree.sql(dialect="duckdb")
                if write is None and not text.strip():
                    continue
                if write is None:
                    self._refuse_drop(text)
                steps.append({"sql": text, "write": write, "as_of": found,
                              "kind": "write delta" if write else _kind(text)})
            return steps
        try:
            texts = [s.query for s in con.extract_statements(sql)]
        except duckdb.Error as exc:
            # DuckDB can't parse CREATE TABLE ... USING DELTA: split the script without it, and let it
            # report anything else that's wrong.
            texts = _split_script(sql)
            if not any(delta.parse(t, self._is_own) for t in texts):
                raise ValueError(_short(exc)) from None
        for text in texts:
            write = delta.parse(text, self._is_own)
            if write is None and len(texts) > 1:
                try:
                    con.extract_statements(text)
                except duckdb.Error as exc:
                    raise ValueError(_short(exc)) from None
            if write is not None:
                write.query, found = delta.rewrite_as_of(write.query, n)
            else:
                self._refuse_drop(text)
                text, found = delta.rewrite_as_of(text, n)
            n += len(found)
            steps.append({"sql": "" if write else text, "write": write, "as_of": found,
                          "kind": "write delta" if write else _kind(text)})
        return steps

    def _refuse_drop(self, statement: str) -> None:
        m = _DROP.match(statement)
        if m and self._is_own(*delta.split_name(m["name"])):
            raise ValueError(f"{m['name']} is one of the project's Delta tables. Dropping it deletes its folder "
                             "and every version, so it's done as its own step (local_drop_delta), not in SQL.")

    def _as_write(self, steps: list[dict[str, Any]], write: "delta.Write") -> dict[str, Any]:
        if len(steps) != 1 or steps[0]["write"] is not None:
            raise ValueError(_ONE_QUERY)
        write.query = steps[0]["sql"]
        return {"sql": "", "write": write, "as_of": steps[0]["as_of"], "kind": "write delta"}

    @property
    def tables_dir(self) -> Path:
        return self.project.root / "Tables"

    def _own_key(self, schema: str | None, table: str) -> str:
        return f"{schema or 'dbo'}.{table}".lower()

    def _is_own(self, schema: str | None, table: str) -> bool:
        entry = self.project.catalog.get(self._own_key(schema, table))
        return bool(entry and entry.get("kind") == OWN)

    def _delta_entry(self, schema: str | None, table: str) -> dict[str, Any]:
        entry = self.project.catalog.get(self._own_key(schema, table))
        name = f"{schema + '.' if schema else ''}{table}"
        if entry is None:
            raise ValueError(f"{name} isn't a Delta table in {self.project.name}, so it has no versions.")
        if entry.get("format") != "delta" or not entry.get("folder"):
            raise ValueError(f"{name} isn't a Delta table ({entry.get('format')}), so it has no versions.")
        return entry

    def _make_as_of(self, con: duckdb.DuckDBPyConnection, items: list["delta.AsOf"]) -> list[str]:
        made = []
        for item in items:
            entry = self._delta_entry(item.schema, item.table)
            select, _info = delta_select(entry["folder"], item.version, item.timestamp,
                                         name=f"{entry['schema']}.{entry['table']}")
            con.execute(f"CREATE OR REPLACE TEMP VIEW {q(item.placeholder)} AS {select}")
            made.append(item.placeholder)
        return made

    def _refresh_own(self) -> None:
        """Make the views for the Delta tables in the project's Tables folder, and drop the views of
        any that are gone."""
        con = self._con
        assert con is not None
        catalog = self.project.catalog
        issues: list[dict[str, str]] = []
        seen: set[str] = set()
        logs = sorted(self.tables_dir.glob("*/*/_delta_log")) if self.tables_dir.is_dir() else []
        for log in logs:
            folder = log.parent
            schema, table = folder.parent.name, folder.name
            key = self._own_key(schema, table)
            try:
                delta.check_name(schema, table)
            except ValueError as exc:
                issues.append({"path": str(folder), "problem": str(exc)})
                continue
            entry = catalog.get(key)
            if entry is not None and entry.get("kind") != OWN:
                issues.append({"path": str(folder), "problem": f"{schema}.{table} is also a file from "
                               f"{entry.get('source')}, which is read instead. Rename one of them."})
                continue
            if entry is None and self._exists(schema, table):
                issues.append({"path": str(folder), "problem": f"{schema}.{table} is already a table or view you "
                               "made in this project, so the Delta table wasn't put over it."})
                continue
            seen.add(key)
            self._register_own(con, schema, table, folder, save=False)
        for key in [k for k, v in catalog.items() if v.get("kind") == OWN and k not in seen]:
            old = catalog.pop(key)
            con.execute(f"DROP VIEW IF EXISTS {q(old['schema'])}.{q(old['table'])}")
        self.project.refreshed[OWN_KEY] = {"issues": issues,
                                           "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with self._meta_lock:
            self.store.save(self.project)

    def _register_own(self, con: duckdb.DuckDBPyConnection, schema: str, table: str, folder: Path,
                      save: bool = True) -> dict[str, Any]:
        key = self._own_key(schema, table)
        entry: dict[str, Any] = {"schema": schema, "table": table, "format": "delta", "kind": OWN, "source": "",
                                 "folder": str(folder), "paths": [], "files": 0, "bytes": 0, "status": "ok",
                                 "error": "", "columns": []}
        try:
            select, info = delta_select(str(folder), name=f"{schema}.{table}")
            entry["delta"] = info
            entry["files"], entry["bytes"] = info["files"], info["bytes"]
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {q(schema)}")
            con.execute(f"CREATE OR REPLACE VIEW {q(schema)}.{q(table)} AS {select}")
            entry["columns"] = [{"name": r[0], "type": r[1]} for r in
                                con.execute(f"DESCRIBE {q(schema)}.{q(table)}").fetchall()]
        except Exception as exc:  # noqa: BLE001 - listed with the reason
            entry["status"], entry["error"] = "error", _short(exc)
            con.execute(f"DROP VIEW IF EXISTS {q(schema)}.{q(table)}")
        self.project.catalog[key] = entry
        if save:
            with self._meta_lock:
                self.store.save(self.project)
        return entry

    def write_delta(self, table: str, query: str, mode: str = "create", dialect: str = "duckdb",
                    partition_by: list[str] | None = None, schema_mode: str | None = None,
                    dry_run: bool = False, timeout: float | None = None,
                    query_id: str | None = None) -> dict[str, Any]:
        """Write ``query``'s rows to the project's Delta table ``table`` ("schema.table" or "table").

        ``mode``: create (it mustn't exist), append or overwrite (either makes it when it doesn't).
        ``schema_mode``: None (the columns must match, widening allowed), merge (new columns are
        added) or overwrite (with mode overwrite: a new schema and partitioning). ``dry_run`` runs
        the query and reports what would be written, and writes nothing."""
        if mode not in ("create", "append", "overwrite"):
            raise ValueError(f"mode must be create, append or overwrite, got {mode!r}")
        if schema_mode not in (None, "merge", "overwrite"):
            raise ValueError(f"schema_mode must be merge or overwrite, got {schema_mode!r}")
        if schema_mode == "overwrite" and mode != "overwrite":
            raise ValueError("A new schema (schema_mode overwrite) goes with mode overwrite.")
        schema, name = delta.split_name(table or "")
        write = delta.Write(schema, name, mode, "", partition_by=list(partition_by or []),
                            replace=schema_mode == "overwrite", schema_mode=schema_mode)
        with self._query(query, dialect, timeout, query_id, write=write, dry_run=dry_run) as (_result, run):
            summary = run["write"]
        summary.update(seconds=round(time.monotonic() - run["started"], 3), **_about(run, self.project.id))
        return summary

    def _write(self, con: duckdb.DuckDBPyConnection, w: "delta.Write", state: dict[str, Any], query_id: str,
               dry_run: bool = False) -> dict[str, Any]:
        """Run ``w``'s query and write its rows in one Delta commit. Holds the connection."""
        from deltalake import CommitProperties, DeltaTable, write_deltalake

        schema = w.schema or "dbo"
        delta.check_name(schema, w.table)
        key = self._own_key(schema, w.table)
        name = f"{schema}.{w.table}"
        entry = self.project.catalog.get(key)
        if entry is not None and entry.get("kind") != OWN:
            if entry.get("format") == "delta":
                raise ValueError(f"{name} is a Delta table in a registered folder ({entry.get('source')}), and "
                                 "registered folders are only read. Write a copy into the project instead: "
                                 f"CREATE TABLE new_name USING DELTA AS SELECT * FROM {name}")
            raise ValueError(f"{name} is a file from {entry.get('source')}. Pick another name for the Delta table.")
        if entry is None and self._exists(schema, w.table):
            raise ValueError(f"{name} is already a table or view in this project. Pick another name, or drop it "
                             "first.")
        folder = Path(entry["folder"]) if entry else self.tables_dir / schema / w.table
        exists = entry is not None and (folder / "_delta_log").is_dir()
        out = {"table": name, "mode": w.mode, "folder": str(folder), "created": not exists, "rows": 0,
               "bytes": 0, "version": None, "dry_run": dry_run, "schema_change": []}
        if exists and w.mode == "create":
            if w.if_not_exists:
                out.update(mode="none", created=False, version=DeltaTable(str(folder)).version())
                return out
            raise ValueError(f"{name} already exists. Add rows with INSERT INTO {w.table} SELECT ..., replace them "
                             f"with INSERT OVERWRITE {w.table} SELECT ..., or use CREATE OR REPLACE TABLE.")
        if not exists and folder.exists() and any(folder.iterdir()):
            raise ValueError(f"{folder} already has files that aren't a Delta table, so nothing was written there.")
        current = DeltaTable(str(folder)) if exists else None
        if current is not None:
            try:
                delta_profile.check_table(current)
            except delta_profile.ProfileViolation as exc:
                raise ValueError(f"{name} isn't written here: {exc}. Writing to it could make it unreadable in "
                                 "Fabric.") from None
            out["version"] = current.version()

        _only_reads(con, w.query)  # before anything runs, dry run or not
        result = con.execute(w.query)
        if not result.description:
            raise ValueError("The query returns no rows to write.")
        reader_of = getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
        reader = reader_of(_WRITE_BATCH)
        positional = current is not None and not w.replace and w.by_position
        incoming = delta.fabric_schema(reader.schema, names=not positional)
        target, schema_mode, by_position = incoming, None, False
        partitions = list(w.partition_by)
        if current is not None and not w.replace:
            table_schema = _arrow_schema(current)
            if w.by_position:
                if len(incoming) != len(table_schema):
                    raise ValueError(f"The query returns {len(incoming)} columns and {name} has {len(table_schema)}"
                                     f" ({', '.join(table_schema.names)}). INSERT matches columns by position; "
                                     "INSERT ... BY NAME matches them by name.")
                incoming = pa.schema([pa.field(t.name, f.type) for f, t in zip(incoming, table_schema)])
                by_position = True
            problems = delta.fits(incoming, table_schema)
            if problems and w.schema_mode == "merge" and \
                    all(p.endswith((" is new", " is missing")) for p in problems):
                target = pa.schema([table_schema.field(f.name) if f.name in table_schema.names else f
                                    for f in incoming])
                schema_mode = "merge"
                out["schema_change"] = problems
            elif problems:
                raise ValueError(f"The query's columns don't match {name}: " + "; ".join(problems) + ". "
                                 + ("Use schema_mode merge to add new columns, or " if w.mode == "append" else "")
                                 + "CREATE OR REPLACE TABLE to give it a new schema.")
            else:
                target = pa.schema([table_schema.field(f.name) for f in incoming]) if by_position else table_schema
            existing_parts = list(current.metadata().partition_columns)
            if partitions and [p.lower() for p in partitions] != [p.lower() for p in existing_parts]:
                raise ValueError(f"{name} is partitioned by {existing_parts or 'nothing'}. Use CREATE OR REPLACE "
                                 "TABLE to change its partitioning.")
            partitions = existing_parts
        elif current is not None:  # a new schema and partitioning
            schema_mode = "overwrite"
            out["schema_change"] = delta.fits(incoming, _arrow_schema(current))
        missing = [p for p in partitions if p not in target.names]
        if missing:
            raise ValueError(f"Can't partition by {', '.join(missing)}: the query has no such column.")
        conform = delta.Conform(reader, target, by_position=by_position)
        counter = delta.Counter(conform.reader)
        try:
            if dry_run:
                for _ in counter.reader:
                    pass
            else:
                folder.mkdir(parents=True, exist_ok=True)
                mode = "error" if current is None else ("overwrite" if w.mode == "overwrite" else "append")
                write_deltalake(str(folder), counter.reader, mode=mode, partition_by=partitions or None,
                                schema_mode=schema_mode,
                                commit_properties=CommitProperties(custom_metadata={
                                    "fabric_desktop.project": self.project.id, "fabric_desktop.query": query_id}))
        except BaseException as exc:
            if not dry_run and current is None and not (folder / "_delta_log").is_dir():
                _remove_tree(folder, self.tables_dir)  # nothing committed: leave no half-written folder
            if state["cancelled"] or state["timed_out"]:
                raise duckdb.InterruptException("cancelled") from None
            if isinstance(exc, (ValueError, duckdb.Error, KeyboardInterrupt)):
                raise
            raise ValueError(f"Writing {name} failed, and nothing was committed: "
                             f"{conform.error or _short(exc).split(' Detail: Python exception')[0]}") from None
        out.update(rows=counter.rows, bytes=counter.bytes, columns=[{"name": f.name, "type": str(f.type)}
                                                                    for f in target])
        if dry_run:
            out["version"] = None if current is None else current.version() + 1
            out["would_create"] = current is None
            return out
        after = DeltaTable(str(folder))
        delta_profile.check_table(after)
        out["version"] = after.version()
        self._register_own(con, schema, w.table, folder)
        return out

    def delta_history(self, table: str, limit: int = 50) -> dict[str, Any]:
        """The versions of a Delta table in the project (its own or a registered folder's), newest first."""
        from deltalake import DeltaTable

        entry = self._delta_entry(*delta.split_name(table or ""))
        dt_ = DeltaTable(entry["folder"])
        versions = []
        for item in dt_.history(max(1, min(int(limit), 1000))):
            when = item.get("timestamp")
            metrics = item.get("operationMetrics") or {}
            versions.append({
                "version": item.get("version"),
                "timestamp": _utc(when) if when else None,
                "operation": item.get("operation"),
                "mode": (item.get("operationParameters") or {}).get("mode"),
                "rows_added": _int(metrics.get("num_added_rows")),
                "files_added": _int(metrics.get("num_added_files")),
                "files_removed": _int(metrics.get("num_removed_files")),
            })
        return {"table": f"{entry['schema']}.{entry['table']}", "folder": entry["folder"],
                "version": dt_.version(), "own": entry.get("kind") == OWN, "versions": versions}

    def drop_delta(self, table: str, confirm: bool = False) -> dict[str, Any]:
        """Delete one of the project's Delta tables: its view, and its folder with every version.

        Without ``confirm`` nothing is deleted; it says what would be."""
        schema, name = delta.split_name(table or "")
        if not self._is_own(schema, name):
            raise ValueError(f"{table} isn't one of {self.project.name}'s Delta tables. Tables in registered "
                             "folders are never deleted from here.")
        key = self._own_key(schema, name)
        entry = self.project.catalog[key]
        folder = Path(entry["folder"])
        out = {"table": f"{entry['schema']}.{entry['table']}", "folder": str(folder), "files": entry.get("files"),
               "bytes": entry.get("bytes"), "version": (entry.get("delta") or {}).get("version")}
        if not confirm:
            return out | {"dropped": False, "confirm_required": True}
        with self._lock:
            con = self._open_locked()
            con.execute(f"DROP VIEW IF EXISTS {q(entry['schema'])}.{q(entry['table'])}")
            _remove_tree(folder, self.tables_dir)
            self.project.catalog.pop(key, None)
            with self._meta_lock:
                self.store.save(self.project)
        return out | {"dropped": True}

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


_ONE_QUERY = ("The rows to write come from one query (SELECT, WITH ... SELECT, FROM or VALUES), with no "
              "other statements.")


def _only_reads(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Refuse rows to write that come from anything but one SELECT, as DuckDB itself reads it: a
    CTE can start a DELETE, INSERT or UPDATE (WITH c AS (...) DELETE FROM t)."""
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise ValueError(_short(exc)) from None
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError(_ONE_QUERY)


_DROP = re.compile(r"^\s*DROP\s+(TABLE|VIEW)\s+(IF\s+EXISTS\s+)?(?P<name>[^\s;]+)\s*;?\s*$", re.IGNORECASE)


def _split_script(sql: str) -> list[str]:
    """The statements of a script, split at semicolons outside strings, quotes and comments."""
    from sqlglot import Dialect
    from sqlglot.tokens import TokenType

    try:
        tokens = Dialect.get_or_raise("duckdb").tokenize(sql)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not split the script into statements: {exc}") from None
    out, begin = [], 0
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            out.append(sql[begin:token.start])
            begin = token.end + 1
    out.append(sql[begin:])
    return [t.strip() for t in out if t.strip()]


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _utc(ms: float) -> str:
    """A Delta commit time (milliseconds since 1970) as 2026-10-01T12:30:00.123Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000)) + f".{int(ms) % 1000:03d}Z"


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _remove_tree(folder: Path, tables_dir: Path) -> None:
    """Delete a Delta table's folder, which must be a table folder inside the project's Tables folder."""
    folder, root = folder.resolve(), tables_dir.resolve()
    if folder.parent.parent != root:
        raise ValueError(f"{folder} isn't a table folder of this project, so it wasn't deleted.")
    if folder.is_dir():
        shutil.rmtree(folder)


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
