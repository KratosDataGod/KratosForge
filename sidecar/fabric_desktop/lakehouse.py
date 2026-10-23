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

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable
from deltalake.exceptions import TableNotFoundError

from .onelake import FileStore, TableRef, storage_options
from .publish import publish, resolve_schema_mode, stage_locally
from .redact import describe

_log = logging.getLogger(__name__)

MODES = ("overwrite", "append")


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

    def __repr__(self) -> str:
        text = f"Wrote {self.rows} rows to {self.table} (Delta version {self.delta_version})"
        if self.delta_version == 0:
            # Fabric lists a table written from outside under Unidentified until the lakehouse is refreshed.
            text += ". New table: refresh the lakehouse in Fabric if it shows under Unidentified"
        return text


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


def _record_table(ref: TableRef, version: int | None, first: str) -> None:
    lh = _current()
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
    return pl.from_arrow(engine.run(query))


def files(directory: str = "Files") -> list[str]:
    lh = _current()
    return [e["path"] for e in FileStore(lh.storage_token).list(lh.workspace_id, lh.lakehouse_id, directory)]


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


def write_table(data: Any, table: str, mode: str = "overwrite", schema_mode: str | None = "auto") -> WriteResult:
    """Write ``data`` to the attached lakehouse as the Delta table ``schema.table``.

    ``schema_mode``: "auto" (the default) replaces the schema on overwrite; None refuses a
    different schema, as Spark does; "merge" adds new columns; "overwrite" replaces it."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    resolve_schema_mode(mode, schema_mode)
    started = time.monotonic()
    lh = _current()
    ref = table_ref(table)
    reason = write_guard(lh.name, ref.schema, ref.table) if write_guard else None
    if reason:
        raise PermissionError(reason)
    arrow = _to_arrow(data)
    stage_locally(arrow, lh.work_dir / "staging" / lh.lakehouse_id / ref.schema / ref.table)
    if (p := _proof()) is not None:
        return _write_proof(p, arrow, ref, mode, schema_mode)
    token = lh.storage_token()
    delta = publish(arrow, ref, mode, token) if schema_mode == "auto" else publish(arrow, ref, mode, token, schema_mode=schema_mode)
    result = WriteResult(f"{ref.schema}.{ref.table}", arrow.num_rows, delta.version(), ref.uri)
    # A table the notebook writes before reading starts, for a replay, at the version before that write.
    _record_table(ref, result.delta_version - 1 if result.delta_version > 0 else None, "write")
    if lh.history_file:
        record = {"job": f"Notebook write to {lh.name}", "table_uri": ref.uri, "mode": mode,
                  "rows": result.rows, "delta_version": result.delta_version,
                  "seconds": round(time.monotonic() - started, 2),
                  "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        lh.history_file.parent.mkdir(parents=True, exist_ok=True)
        with lh.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return result


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
