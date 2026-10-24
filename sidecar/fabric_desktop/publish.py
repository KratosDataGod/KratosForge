"""Run a job locally and publish its result to OneLake as a Delta table.

delta-rs writes the Parquet data files first and makes them visible with a
single atomic commit to ``_delta_log``, so Fabric never sees a half-written
version. The table delta-rs would create is staged locally first (schema
only), so the Delta profile is checked before anything leaves the machine.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
from deltalake import CommitProperties, DeltaTable, WriterProperties, write_deltalake

from . import delta_profile
from .fabric_api import FabricClient
from .jobs import Job, run_local
from .leases import LocalBackend, check_held, onelake_table_lease, table_lease
from .onelake import TableRef, storage_options


@dataclass
class RunRecord:
    job: str
    table_uri: str
    mode: str
    rows: int
    delta_version: int
    seconds: float
    finished_at: str


def stage_locally(data: pa.Table, root: Path, *parts: str) -> DeltaTable:
    """The table delta-rs would create for ``data``, on disk, so its protocol is checked against the profile
    before anything leaves the PC. Only the schema decides that, so no rows are written: copying every
    row here cost about a second per 70 MB on each write and kept every old copy.

    It is made in a folder of its own under ``root`` (the staging folder) and deleted once checked, so
    nothing stays on disk (two writes to one table at once never share it). ``root/<parts>``, where
    builds before #609 kept full copies of every write, is deleted too. ``parts`` (a job's name, or
    lakehouse, schema and table) must each be one plain folder name: a name such as ".." or one
    with a separator could otherwise point the delete outside the staging folder. The returned
    table is loaded, so its protocol and schema can still be read."""
    for part in parts:
        if not part or part in (".", "..", "_writes") or any(c in part for c in "/\\:") or Path(part).is_absolute():
            raise ValueError(f"{part!r} can't be a staging folder name: use a plain name, without .. or separators, and not _writes.")
    root = root.resolve()
    staging_dir = root.joinpath(*parts).resolve()
    if root not in staging_dir.parents:
        raise ValueError(f"{staging_dir} isn't inside the staging folder {root}.")
    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staging_dir, ignore_errors=True)  # an old build's full copies
    here = Path(tempfile.mkdtemp(prefix=f"{staging_dir.name}-", dir=staging_dir.parent))
    try:
        write_deltalake(str(here), data.schema.empty_table(), mode="overwrite", schema_mode="overwrite")
        table = DeltaTable(str(here))
        delta_profile.check_table(table)
        return table
    finally:
        shutil.rmtree(here, ignore_errors=True)


def resolve_target(job: Job, fabric: FabricClient) -> TableRef:
    t = job.target
    lakehouse = fabric.find_lakehouse(t.workspace_id, t.lakehouse)
    if lakehouse is None:
        lakehouse = fabric.create_lakehouse(t.workspace_id, t.lakehouse)
    return TableRef(t.workspace_id, lakehouse["id"], t.schema, t.table)


SCHEMA_MODES = ("auto", "overwrite", "merge", None)


def resolve_schema_mode(mode: str, schema_mode: str | None) -> str | None:
    if schema_mode not in SCHEMA_MODES:
        raise ValueError(f"schema_mode must be one of {SCHEMA_MODES}, got {schema_mode!r}")
    if schema_mode == "auto":
        return "overwrite" if mode == "overwrite" else None
    if schema_mode == "overwrite" and mode != "overwrite":
        raise ValueError("A table's schema can only be replaced by an overwrite.")
    return schema_mode


# The commit metadata key that keeps what an overwrite wrote (write_queue.fingerprint).
ROWS_KEY = "kratosforge.rows"


def writer_properties() -> WriterProperties:
    """zstd unless the onelake_compression setting says snappy: about a quarter fewer bytes to upload
    for the same encoding time. Fabric's Spark, SQL analytics endpoint and delta-rs all read it."""
    from . import app_settings
    try:
        codec = str(app_settings.value("onelake_compression")).lower()
    except Exception:  # noqa: BLE001 - a broken settings file keeps the default
        codec = "zstd"
    if codec == "snappy":
        return WriterProperties(compression="SNAPPY")
    return WriterProperties(compression="ZSTD", compression_level=3)


def unchanged(table: DeltaTable | None, rows: str | None) -> bool:
    """Whether ``table`` holds exactly the rows ``rows`` names: its newest commit is an overwrite this
    app made with the same fingerprint, so nothing has changed the table since."""
    if table is None or not rows:
        return False
    last = (table.history(1) or [{}])[0]
    return last.get(ROWS_KEY) == rows and (last.get("operationParameters") or {}).get("mode") == "Overwrite"


def publish(data: Any, ref: TableRef, mode: str, storage_token: str, schema_mode: str | None = "auto",
            rows: str | None = None, skip_unchanged: bool = False) -> DeltaTable:
    """Write ``data`` (an Arrow table or a stream of batches) to ``ref``. ``schema_mode`` "auto" replaces
    the schema on overwrite (as jobs always have); None refuses a different schema, as Spark does without
    overwriteSchema; "merge" adds new columns. ``rows`` is the data's fingerprint, kept in an
    overwrite's commit; with ``skip_unchanged`` an overwrite of the same rows writes nothing, and the
    returned table has ``skipped`` set."""
    opts = storage_options(storage_token)
    how = resolve_schema_mode(mode, schema_mode)
    with _lease(ref, storage_token):
        # One load of the log for the profile check, the write and the new version: delta-rs moves a
        # table it was handed on to the version it committed. Loaded under the lease, so no other
        # KratosForge writer can commit in between.
        table = delta_profile.open_target(ref.uri, opts)
        if mode == "overwrite" and skip_unchanged and unchanged(table, rows):
            table.skipped = True  # type: ignore[union-attr]
            return table  # type: ignore[return-value]
        meta = CommitProperties(custom_metadata={ROWS_KEY: rows}) if rows and mode == "overwrite" else None
        check_held()  # the lease is still ours, with time to commit (HD-016)
        write_deltalake(table if table is not None else ref.uri, data, mode=mode, storage_options=opts, schema_mode=how,
                        writer_properties=writer_properties(), commit_properties=meta)
        if table is None:
            table = DeltaTable(ref.uri, storage_options=opts)
    delta_profile.note_after_write(table)
    table.skipped = False  # type: ignore[attr-defined]
    return table


WRITER = "KratosForge"


def _lease(ref: TableRef, storage_token: str) -> AbstractContextManager[Any]:
    """The table's write lease (leases.table_lease): waits for another writer, then raises LeaseBusy."""
    if ref.uri.startswith("abfss://"):
        return onelake_table_lease(ref.workspace_id, ref.lakehouse_id, ref.schema, ref.table,
                                   lambda: storage_token, owner=WRITER)
    return table_lease(LocalBackend(_local_leases(ref)), ref.schema, ref.table, owner=WRITER)


def _local_leases(ref: TableRef) -> Path:
    """A table on disk (tests point TableRef there) keeps its lease files beside it."""
    return Path(ref.uri).parent / "_leases"


def run_job(job: Job, fabric: FabricClient, storage_token: Callable[[], str],
            work_dir: Path, history_file: Path | None = None,
            guard: Callable[[str, str, str, str], str | None] | None = None) -> RunRecord:
    """Run the job here and write its table. ``guard`` (workspace id, lakehouse, schema, table)
    says why the target may not be written; then nothing runs or is written."""
    t = job.target
    if guard and (reason := guard(t.workspace_id, t.lakehouse, t.schema, t.table)):
        raise PermissionError(reason)
    started = time.monotonic()
    data = run_local(job)
    stage_locally(data, work_dir / "staging", job.name)
    ref = resolve_target(job, fabric)
    table = publish(data, ref, job.target.mode, storage_token())
    record = RunRecord(
        job=job.name,
        table_uri=ref.uri,
        mode=job.target.mode,
        rows=data.num_rows,
        delta_version=table.version(),
        seconds=round(time.monotonic() - started, 2),
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    if history_file:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")
    return record
