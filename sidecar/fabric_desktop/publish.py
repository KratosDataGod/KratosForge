"""Run a job locally and publish its result to OneLake as a Delta table.

delta-rs writes the Parquet data files first and makes them visible with a
single atomic commit to ``_delta_log``, so Fabric never sees a half-written
version. The table delta-rs would create is staged locally first (schema
only), so the Delta profile is checked before anything leaves the machine.
"""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from . import delta_profile
from .fabric_api import FabricClient
from .jobs import Job, run_local
from .leases import LocalBackend, onelake_table_lease, table_lease
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


def stage_locally(data: pa.Table, staging_dir: Path) -> DeltaTable:
    """The table delta-rs would create for ``data``, on disk, so its protocol is checked against the profile
    before anything leaves the PC. Only the schema decides that, so no rows are written: copying every
    row here cost about a second per 70 MB on each write and kept every old copy."""
    write_deltalake(str(staging_dir), data.schema.empty_table(), mode="overwrite", schema_mode="overwrite")
    table = DeltaTable(str(staging_dir))
    delta_profile.check_table(table)
    return table


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


def publish(data: pa.Table, ref: TableRef, mode: str, storage_token: str, schema_mode: str | None = "auto") -> DeltaTable:
    """Write ``data`` to ``ref``. ``schema_mode`` "auto" replaces the schema on overwrite (as jobs
    always have); None refuses a different schema, as Spark does without overwriteSchema; "merge"
    adds new columns."""
    opts = storage_options(storage_token)
    how = resolve_schema_mode(mode, schema_mode)
    with _lease(ref, storage_token):
        # One load of the log for the profile check, the write and the new version: delta-rs moves a
        # table it was handed on to the version it committed. Loaded under the lease, so no other
        # KratosForge writer can commit in between.
        table = delta_profile.open_target(ref.uri, opts)
        write_deltalake(table if table is not None else ref.uri, data, mode=mode, storage_options=opts, schema_mode=how)
        if table is None:
            table = DeltaTable(ref.uri, storage_options=opts)
    delta_profile.note_after_write(table)
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
    stage_locally(data, work_dir / "staging" / job.name)
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
