"""The attached lakehouse, as notebooks see it.

    import polars as pl
    from fabric_desktop import lakehouse

    sales = lakehouse.read_csv("Files/raw/sales.csv")
    lakehouse.write_table(sales, "dbo.sales")

Paths that start with ``Files/`` are read from the attached lakehouse through
OneLake; any other path is a file on this machine. Tables are named
``schema.table`` (``dbo`` when the schema is left out). All compute runs
locally; only the input file download and the Delta write touch OneLake.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

from .onelake import FileStore, TableRef, storage_options
from .publish import publish, resolve_schema_mode, stage_locally
from .redact import describe
from .retrying import data_upload_dropped

_log = logging.getLogger(__name__)

MODES = ("overwrite", "append")
# The write history names a notebook's table write "Notebook write to <lakehouse>"; costs.py tells it from a job run by this.
WRITE_JOB_PREFIX = "Notebook write to "


@dataclass(frozen=True)
class Attached:
    workspace_id: str
    lakehouse_id: str
    name: str
    storage_token: Callable[[], str]
    work_dir: Path
    history_file: Path | None = None
    workspace_name: str = ""
    # False for a lakehouse created without schemas (tables live directly under Tables/).
    schemas_enabled: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class WriteResult:
    table: str
    rows: int
    delta_version: int
    uri: str
    skipped: bool = False  # the table already held exactly these rows, so nothing was uploaded

    def __repr__(self) -> str:
        if self.skipped:
            return (f"{self.table} unchanged: it already holds these {self.rows} rows at Delta version "
                    f"{self.delta_version}, so nothing was uploaded")
        text = f"Wrote {self.rows} rows to {self.table} (Delta version {self.delta_version})"
        if self.delta_version == 0:
            # Fabric lists a table written from outside under Unidentified until the lakehouse is refreshed.
            text += ". New table: refresh the lakehouse in Fabric if it shows under Unidentified"
        return text


class PendingWrite:
    """write_table's answer while the write uploads in the background: anything that needs the
    committed version (``delta_version``, ``skipped``, printing it) waits for the upload."""

    def __init__(self, table: str, rows: int, uri: str, future: Any):
        self.table, self.rows, self.uri = table, rows, uri
        self._future = future

    def result(self) -> WriteResult:
        return self._future.result()

    @property
    def delta_version(self) -> int:
        return self.result().delta_version

    @property
    def skipped(self) -> bool:
        return self.result().skipped

    def __repr__(self) -> str:
        if self._future.cancelled() or self._future.exception() is not None:
            return f"{self.table}: not written (the cell's error says why)"  # the kernel reports it once, at the cell's end
        return repr(self.result())


_attached: Attached | None = None

# Set while a proof runs on this PC (proof_runtime.ProofRuntime): tables are read at their pinned
# versions and written to the proof's sandbox instead of the lakehouse's shared tables.
proof: Any = None
# The thread that answers the app's other requests (the RPC loop) while a proof runs: the Explorer's
# previews and lookups read the real lakehouse, not the proof's view of it. Every other thread (the
# notebook's cells and any threads they start) goes through the proof.
proof_exempt_thread: threading.Thread | None = threading.main_thread()


# What the notebook's cells have read since the kernel started, for replay bundles (replay.py):
# each table's Delta version when the notebook first read it (or the version before its first
# write), and each Files/ file's local copy, hashed only when a bundle is saved. Reads from the
# exempt thread (the Explorer) and during a proof (which keeps its own log) aren't the cells' own.
reads: dict[str, dict[str, dict[str, Any]]] = {"tables": {}, "files": {}}


def reads_reset() -> None:
    reads["tables"].clear()
    reads["files"].clear()


def _record(kind: str, key: str, entry: Callable[[], dict[str, Any]]) -> None:
    if proof is not None or threading.current_thread() is proof_exempt_thread or key in reads[kind]:
        return
    try:
        reads[kind][key] = entry()
    except Exception as exc:  # noqa: BLE001 - a replay record never fails the cell's own read
        _log.info("Couldn't record a read for replay: %s", describe(exc))


def _record_table(ref: TableRef, version: int | None, first: str, lh: Attached | None = None) -> None:
    lh = lh or _current()  # a background write passes the lakehouse it was made in
    _record("tables", f"{ref.lakehouse_id}/{ref.schema}.{ref.table}".lower(), lambda: {
        "table": f"{ref.schema}.{ref.table}", "version": version, "first": first,
        "workspace_id": ref.workspace_id, "lakehouse_id": ref.lakehouse_id, "lakehouse": lh.name})


def _proof() -> Any:
    if proof is None or threading.current_thread() is proof_exempt_thread:
        return None
    return proof

# Set by the service when a metadata project is open: returns why a table may not be
# written from a notebook (it is managed by the framework), or None.
write_guard: Callable[[str, str, str], str | None] | None = None

# Checks for the writes of one cell, by the thread the engine's own kernel runs it on: a cell a
# paired VS Code window started asks in the app before each write (editor_service.py). A process
# kernel asks the engine through its write_guard call instead (kernel_proc.ProcessKernel.write_check).
cell_write_checks: "weakref.WeakKeyDictionary[threading.Thread, Callable[[str, str, str], str | None]]" = \
    weakref.WeakKeyDictionary()


def write_refusal(name: str, schema: str, table: str) -> str | None:
    """Why this write may not happen (the open metadata project, or the cell's own check), or None."""
    reason = write_guard(name, schema, table) if write_guard else None
    if reason:
        return reason
    check = cell_write_checks.get(threading.current_thread())
    return check(name, schema, table) if check else None


def attach(context: Attached | None) -> None:
    """Set the lakehouse that notebook code reads from and writes to."""
    global _attached
    _attached = context


def _current() -> Attached:
    if _attached is None:
        raise RuntimeError("No lakehouse is attached. Use Add data items to attach one.")
    return _attached


def name() -> str | None:
    return _attached.name if _attached else None


def has_schemas() -> bool:
    """False when the attached lakehouse was created without schemas (tables are just ``table``)."""
    return _current().schemas_enabled()


def table_ref(table: str) -> TableRef:
    """``table``, ``schema.table``, or either with the lakehouse name in front, as Spark in Fabric allows."""
    lh = _current()
    parts = table.split(".")
    # lakehouse.schema.table, or lakehouse.table when the lakehouse has no schemas.
    if parts[0].lower() == lh.name.lower() and (len(parts) == 3 or len(parts) == 2 and not lh.schemas_enabled()):
        parts = parts[1:]
    if len(parts) > 2:
        raise ValueError(f"{table}: name tables as table or schema.table in the attached lakehouse ({lh.name}).")
    schema, name_ = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
    if lh.schemas_enabled():
        return TableRef(lh.workspace_id, lh.lakehouse_id, schema or "dbo", name_)
    if schema.lower() not in ("", "dbo"):
        raise ValueError(f"{lh.name} was created without schemas, so a table can't go in {schema!r}. "
                         f"Use {name_!r}, or put the schema in the name: '{schema}_{name_}'. "
                         f"lakehouse.has_schemas() tells a notebook which kind it has.")
    return TableRef(lh.workspace_id, lh.lakehouse_id, "dbo", name_, flat=True)


MOUNT = "/lakehouse/default/"


def relative_path(path: str) -> str | None:
    """``Files/...`` or ``Tables/...`` when ``path`` names something in the attached lakehouse.

    Accepts the forms Fabric notebooks use: ``Files/x.csv``, ``/lakehouse/default/Files/x.csv``
    and ``abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>/Files/x.csv``."""
    p = str(path).replace("\\", "/")
    if p.startswith(MOUNT):
        p = p[len(MOUNT):]
    elif p.startswith("abfss://") and "onelake." in p:
        workspace, _, rest = p[len("abfss://"):].partition("@")
        _host, _, rest = rest.partition("/")
        item, _, p = rest.partition("/")
        lh = _current()
        if workspace not in (lh.workspace_id, lh.workspace_name) or \
                item.removesuffix(".Lakehouse") not in (lh.lakehouse_id, lh.name):
            raise ValueError(f"{path} is in another lakehouse. On this PC, notebooks read the attached "
                             f"lakehouse ({lh.name}); attach that one with Add data items.")
    p = p.lstrip("/")
    return p if p in ("Files", "Tables") or p.startswith(("Files/", "Tables/")) else None


def local_path(path: str) -> Path:
    """A local path for ``path``, downloading it first when it is in the lakehouse's ``Files/``."""
    rel = relative_path(path)
    if rel is None:
        return Path(path)
    if not rel.startswith("Files/"):
        raise ValueError(f"{path} is not a file under Files/. Read tables with read_table.")
    lh = _current()
    dest = lh.work_dir / "files" / lh.lakehouse_id / rel
    got = FileStore(lh.storage_token).download(lh.workspace_id, lh.lakehouse_id, rel, dest)
    if (p := _proof()) is not None:
        p.file_read(rel, got)

    def entry() -> dict[str, Any]:
        st = got.stat()
        return {"path": rel, "local": str(got), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                "workspace_id": lh.workspace_id, "lakehouse_id": lh.lakehouse_id, "lakehouse": lh.name}
    _record("files", f"{lh.lakehouse_id}/{rel}".lower(), entry)
    return got


def read_csv(path: str, **kwargs: Any) -> pl.DataFrame:
    return pl.read_csv(local_path(path), **kwargs)


def read_parquet(path: str, **kwargs: Any) -> pl.DataFrame:
    return pl.read_parquet(local_path(path), **kwargs)


def read_json(path: str, **kwargs: Any) -> pl.DataFrame:
    return pl.read_json(local_path(path), **kwargs)


def as_of(version: Any = None, timestamp: Any = None) -> int | datetime | None:
    """A Delta time travel point, as Spark's versionAsOf / timestampAsOf give it: a version
    number, or a time (an ISO date or date and time; without a zone it is UTC, like Fabric's
    Spark sessions). None for the latest version."""
    if version is not None and timestamp is not None:
        raise ValueError("Give a version or a timestamp to read, not both.")
    if version is not None:
        try:
            n = int(str(version).strip().lstrip("vV"))
        except ValueError:
            raise ValueError(f"versionAsOf must be a whole number, not {version!r}.") from None
        if n < 0:
            raise ValueError("versionAsOf can't be negative.")
        return n
    if timestamp is None:
        return None
    if isinstance(timestamp, datetime):
        when = timestamp
    else:
        try:
            when = datetime.fromisoformat(str(timestamp).strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"timestampAsOf must be a date or a date and time like 2026-10-20 13:00:00, "
                             f"not {timestamp!r}.") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def open_table(table: str, version: Any = None, timestamp: Any = None) -> DeltaTable:
    """The table's Delta log, at its latest version or, with ``version`` or ``timestamp``, as it
    was then (Delta time travel; only reads)."""
    point = as_of(version, timestamp)
    opened = _open_table(table, record=point is None)
    if point is not None:
        try:
            opened.load_as_version(point)
        except Exception as exc:
            what = f"version {point}" if isinstance(point, int) else f"{point.isoformat()}"
            raise ValueError(f"{table} has no {what} to read: {exc}. DESCRIBE HISTORY {table} lists its versions; "
                             "versions removed by VACUUM can't be read.") from None
        _record_table(table_ref(table), opened.version(), "read")  # the version it read, for a replay
    return opened


def table_history(table: str, limit: int | None = None) -> pl.DataFrame:
    """DESCRIBE HISTORY: the table's versions, newest first, from its Delta log (only reads)."""
    rows = _open_table(table).history(limit)
    cols = ("version", "timestamp", "operation", "operationParameters", "operationMetrics", "userName",
            "engineInfo", "isBlindAppend", "readVersion")
    out = {c: [r.get(c) for r in rows] for c in cols}
    # In UTC without a zone, as Fabric's Spark sessions show it (a zoned time needs pytz in DuckDB).
    out["timestamp"] = [datetime.fromtimestamp(t / 1000, timezone.utc).replace(tzinfo=None) if t else None
                        for t in out["timestamp"]]
    for c in ("operationParameters", "operationMetrics"):
        out[c] = [json.dumps(v, default=str) if v is not None else None for v in out[c]]
    return pl.DataFrame(out, schema={"version": pl.Int64, "timestamp": pl.Datetime("us"), "operation": pl.Utf8,
                                     "operationParameters": pl.Utf8, "operationMetrics": pl.Utf8, "userName": pl.Utf8,
                                     "engineInfo": pl.Utf8, "isBlindAppend": pl.Boolean, "readVersion": pl.Int64})


def _open_table(table: str, record: bool = True) -> DeltaTable:
    ref = table_ref(table)
    _see_own_writes()
    if (p := _proof()) is not None:
        from .proof_runtime import ProofTableMissing
        try:
            return p.open(ref.schema, ref.table,
                          lambda uri, version: DeltaTable(uri, version=version, storage_options=p.options()))
        except ProofTableMissing as e:
            raise TableNotFoundError(str(e)) from None
    opts = storage_options(_current().storage_token())
    try:
        opened = DeltaTable(ref.uri, storage_options=opts)
        if record:
            _record_table(ref, opened.version(), "read")
        return opened
    except TableNotFoundError as e:
        lh, folder = _current().name, ref.uri.split("/Tables/", 1)[1]
        shown = ref.table if ref.flat else f"{ref.schema}.{ref.table}"
        # Nothing there (delta-rs says "No files in log segment" for an empty or missing folder in
        # OneLake): say so plainly. Anything else keeps delta-rs's words.
        detail = "" if any(m in str(e) for m in _MISSING) else f" ({e})"
        raise TableNotFoundError(f"{shown} isn't in {lh}: there's no Delta table at Tables/{folder}{detail}. "
                                 f"Check the name in the Explorer, or run the notebook that creates it "
                                 f"with {lh} attached first.") from None


# delta-rs's words for a table folder with no Delta log in it.
_MISSING = ("No files in log segment", "does not exist")


def table_exists(table: str) -> bool:
    """Whether the attached lakehouse has this table. Only "not found" means no: any other
    failure (sign-in, throttling, network) is raised, so it can't turn into an overwrite."""
    try:
        open_table(table)
    except TableNotFoundError:
        return False
    return True


def read_table(table: str, version: int | None = None, timestamp: Any = None) -> pl.DataFrame:
    """The table as a data frame; ``version`` or ``timestamp`` read it as it was then."""
    return pl.from_arrow(open_table(table, version, timestamp).to_pyarrow_table())


def sql(query: str) -> pl.DataFrame:
    """Run Spark SQL over the attached lakehouse's tables on this PC, e.g. ``sql("SELECT * FROM sales")``."""
    from . import sql as engine
    _see_own_writes()
    return pl.from_arrow(engine.run(query))


def files(directory: str = "Files") -> list[str]:
    lh = _current()
    return [e["path"] for e in FileStore(lh.storage_token).list(lh.workspace_id, lh.lakehouse_id, directory)]


def _delta_safe(schema: pa.Schema, table: str, mode: str) -> pa.Schema:
    """``schema`` with each column delta-rs can't write in the type Fabric reads, or a plain error
    (localdata.delta.delta_safe). An all-NULL column appended to a table that has it takes the
    table's type."""
    from .localdata import delta as local_delta

    def existing() -> dict[str, pa.DataType]:
        try:
            return {f.name.lower(): f.type for f in pa.schema(open_table(table).schema().to_arrow())}
        except TableNotFoundError:
            return {}
    if naive := local_delta.naive_timestamps(schema):
        listed = ", ".join(naive)
        raise ValueError(f"Column{'s' if len(naive) > 1 else ''} {listed} hold{'' if len(naive) > 1 else 's'} timestamps "
                         "without a time zone, which Delta stores in a form Fabric can't read (timestampNtz). Nothing was "
                         "written. Give them a time zone first: in Polars .dt.replace_time_zone(\"UTC\"), in pandas "
                         ".dt.tz_localize(\"UTC\"), in Spark SQL CAST(x AS TIMESTAMP), or with "
                         "to_utc_timestamp(x, 'your zone').")
    return local_delta.delta_safe(schema, existing if mode == "append" else None)


def _to_arrow(data: Any) -> pa.Table:
    if isinstance(data, pa.Table):
        return data
    if isinstance(data, pl.LazyFrame):
        data = data.collect()
    if isinstance(data, pl.DataFrame):
        return data.to_arrow()
    if type(data).__module__.startswith("duckdb"):
        return (getattr(data, "to_arrow_table", None) or data.arrow)()
    if type(data).__module__.startswith("pandas"):
        return pa.Table.from_pandas(data, preserve_index=False)
    return pa.table(data)


UPLOADS = ("local_first", "direct")


def _queue() -> Any:
    """The write queue of the cell running on this thread (write_queue.cell_writes)."""
    from .write_queue import cell_writes
    return getattr(cell_writes, "queue", None)


def _see_own_writes() -> None:
    """A read waits for the cell's own writes still uploading, so it sees them."""
    if (q := _queue()) is not None:
        from . import write_queue
        stats = write_queue.stats()
        with stats.waiting() if stats is not None else contextlib.nullcontext():
            q.wait()


def _setting(key: str, default: Any) -> Any:
    from . import app_settings
    try:
        return app_settings.value(key)
    except Exception as exc:  # noqa: BLE001 - a broken settings file keeps the default
        _log.info("Couldn't read the %s setting, using %r: %s", key, default, describe(exc))
        return default


def _stream(data: Any) -> tuple[pa.Schema, Iterator[pa.Table]] | None:
    """A stream of batches (a RecordBatchReader, DuckDB's reader, or any iterable of tables, batches
    or data frames) as its schema and its parts; None for data that is one table."""
    if isinstance(data, (pa.Table, pl.DataFrame, pl.LazyFrame, pa.RecordBatch)) or \
            type(data).__module__.startswith(("duckdb", "pandas")) and not isinstance(data, pa.RecordBatchReader):
        if not (type(data).__module__.startswith("duckdb") and hasattr(data, "read_next_batch")):
            return None
    if isinstance(data, pa.RecordBatchReader) or hasattr(data, "read_next_batch"):
        reader = data if isinstance(data, pa.RecordBatchReader) else pa.RecordBatchReader.from_stream(data)
        return reader.schema, (pa.Table.from_batches([b]) for b in reader)
    if isinstance(data, (dict, list, tuple)) or not hasattr(data, "__iter__"):
        return None
    parts = (_to_arrow(p) if not isinstance(p, pa.RecordBatch) else pa.Table.from_batches([p]) for p in iter(data))
    first = next(parts, None)
    if first is None:
        raise ValueError("write_table was given an empty stream: there's no schema to write. Pass a table instead.")

    def chain() -> Iterator[pa.Table]:
        yield first
        yield from parts
    return first.schema, chain()


# A table write whose data file upload was dropped (retrying.data_upload_dropped) is written again,
# whole: nothing of it was committed. On top of object_store's own retries of each part.
UPLOAD_ATTEMPTS = 3
UPLOAD_RETRY_WAIT = 15.0  # seconds, then twice that


def write_table(data: Any, table: str, mode: str = "overwrite", schema_mode: str | None = "auto",
                upload: str | None = None, skip_unchanged: bool | None = None) -> WriteResult | PendingWrite:
    """Write ``data`` to the attached lakehouse as the Delta table ``schema.table``.

    ``data`` is a table (polars, pandas, Arrow, DuckDB) or a stream of batches (a RecordBatchReader,
    DuckDB's ``fetch_record_batch``, or any iterable of tables): a stream is written as one commit,
    spilled to this PC's disk on the way so it never has to fit in memory.

    ``schema_mode``: "auto" (the default) replaces the schema on overwrite; None refuses a
    different schema, as Spark does; "merge" adds new columns; "overwrite" replaces it.

    ``upload``: "direct" writes to OneLake before returning. Otherwise (None or "local_first"), in a
    notebook set to write local first (its OneLake writes choice, or the app's onelake_writes), the
    write is kept on this PC and uploads in the background while the cell goes on; the cell ends
    when its writes are in. Elsewhere (scripts, Fabric) every write is direct.

    ``skip_unchanged``: an overwrite of exactly the rows this app last wrote to the table uploads
    nothing; None follows the onelake_skip_unchanged setting."""
    from . import write_queue
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if upload not in (None, *UPLOADS):
        raise ValueError(f"upload must be one of {UPLOADS}, got {upload!r}")
    resolve_schema_mode(mode, schema_mode)
    started = time.monotonic()
    lh = _current()
    ref = table_ref(table)
    reason = write_refusal(lh.name, ref.schema, ref.table)
    if reason:
        raise PermissionError(reason)
    streamed = _stream(data)
    arrow, spill = None, None
    if streamed is None:
        arrow = _to_arrow(data)
        schema = _delta_safe(arrow.schema, table, mode)
        if schema != arrow.schema:
            arrow = arrow.cast(schema)
    else:
        schema = _delta_safe(streamed[0], table, mode)
        if schema != streamed[0]:
            streamed = (schema, (part.cast(schema) for part in streamed[1]))
    stage_locally(schema.empty_table(), lh.work_dir / "staging", lh.lakehouse_id, ref.schema, ref.table)
    if (p := _proof()) is not None:
        if arrow is None:
            arrow = pa.concat_tables(list(streamed[1]), promote_options="none") if streamed else schema.empty_table()
        return _write_proof(p, arrow, ref, mode, schema_mode)
    if skip_unchanged is None:
        skip_unchanged = bool(_setting("onelake_skip_unchanged", True))
    parts: list[tuple[int, int, int]] = []
    if arrow is not None:
        rows = arrow.num_rows
        if mode == "overwrite":
            parts.append(write_queue.fingerprint_part(arrow))
    else:
        spill = write_queue.Spill(lh.work_dir / "staging" / "_writes", schema)
        try:
            for part in streamed[1]:
                if part.schema != schema:
                    part = part.cast(schema)
                if mode == "overwrite":
                    parts.append(write_queue.fingerprint_part(part))
                for batch in part.to_batches():
                    spill.add(batch)
        except BaseException:
            spill.close()
            spill.remove()
            raise
        spill.close()
        rows = spill.rows
    fingerprint = write_queue.fingerprint(schema, write_queue.combine(parts)) if mode == "overwrite" else None
    name_ = f"{ref.schema}.{ref.table}"
    stats = write_queue.stats()  # the cell's, for its timing; a background write runs on another thread

    def commit() -> WriteResult:
        uploading = time.monotonic()
        try:
            for attempt in range(UPLOAD_ATTEMPTS):
                try:
                    delta = publish(arrow if arrow is not None else spill.reader(), ref, mode, lh.storage_token(),
                                    schema_mode=schema_mode, rows=fingerprint, skip_unchanged=skip_unchanged)
                    break
                except Exception as exc:  # noqa: BLE001 - only a dropped data file upload is tried again
                    if attempt == UPLOAD_ATTEMPTS - 1 or not data_upload_dropped(exc):
                        raise
                    wait = UPLOAD_RETRY_WAIT * (attempt + 1)
                    _log.warning("Writing %s again in %.0f s: a data file's upload was dropped (%s)", name_, wait, describe(exc))
                    time.sleep(wait)
        finally:
            if spill is not None:
                spill.remove()
        skipped = bool(getattr(delta, "skipped", False))
        result = WriteResult(name_, rows, delta.version(), ref.uri, skipped)
        if stats is not None:
            stats.wrote(uploading, 0 if skipped else rows, getattr(delta, "written_bytes", None), skipped)
        # A table the notebook writes before reading starts, for a replay, at the version before that write.
        _record_table(ref, result.delta_version if skipped else
                      result.delta_version - 1 if result.delta_version > 0 else None, "write", lh)
        if lh.history_file:
            record = {"job": f"{WRITE_JOB_PREFIX}{lh.name}", "table_uri": ref.uri, "mode": mode,
                      "rows": result.rows, "delta_version": result.delta_version,
                      "seconds": round(time.monotonic() - started, 2),
                      "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if skipped:
                record["skipped"] = "unchanged"
            lh.history_file.parent.mkdir(parents=True, exist_ok=True)
            with lh.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        return result

    queue = _queue()  # set for this cell when its notebook writes local first (notebook.py)
    with stats.waiting() if stats is not None else contextlib.nullcontext():
        if queue is None or upload == "direct":
            if queue is not None:
                queue.wait()  # in order with the cell's writes still uploading
            return commit()
        # Returns at once, unless the writes already waiting hold too much of this PC's memory.
        future = queue.submit(ref.uri, name_, arrow.nbytes if arrow is not None else 0, commit)
    if spill is not None:  # a write cancelled before it started never runs commit(), which removes it
        future.add_done_callback(lambda f: spill.remove() if f.cancelled() else None)
    return PendingWrite(name_, rows, ref.uri, future)


def _write_proof(proof: Any, arrow: pa.Table, ref: TableRef, mode: str, schema_mode: str | None = "auto") -> WriteResult:
    """A write during a proof: to this run's sandbox, never the shared table."""
    from deltalake import write_deltalake

    from . import delta_profile

    def write(uri: str, data: pa.Table, how: str = "overwrite", schema: str | None = "auto") -> DeltaTable:
        delta_profile.check_target(uri, proof.options())
        write_deltalake(uri, data, mode=how, storage_options=proof.options(),
                        schema_mode=resolve_schema_mode(how, schema))
        table = DeltaTable(uri, storage_options=proof.options())
        delta_profile.note_after_write(table)
        return table
    uri = proof.write_location(ref.schema, ref.table, mode, write)
    table = write(uri, arrow, mode, schema_mode)
    return WriteResult(f"{ref.schema}.{ref.table}", arrow.num_rows, table.version(), uri)
