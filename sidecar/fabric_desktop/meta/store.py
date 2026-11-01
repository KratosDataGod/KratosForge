"""Where an environment's tables live: Delta tables on this PC, or in OneLake.

Each layer is a lakehouse named LH_<Layer>. Run metadata (run log, quality results,
watermarks, schema history, deployments, restores) goes to the ``meta`` schema of LH_Meta in the
same environment, so the same rows exist locally in Dev and in OneLake for Test and Prod.
Quarantined rows go to the ``quarantine`` schema of the entity's own lakehouse.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import logging
from pathlib import Path
from typing import Any, Callable

import polars as pl
import pyarrow as pa
from deltalake import CommitProperties, DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from .. import delta_profile
from ..errors import NotSetUp
from ..leases import LeaseLost, check_held
from ..retrying import local_retry
from ..onelake import FileStore, TableRef, storage_options
from ..redact import describe

_log = logging.getLogger(__name__)
_LEASE_LOST = {"removed": 0, "note": "the run no longer holds the table's lease, so the files this write left are "
                                     "left for VACUUM"}

META_LAKEHOUSE = "LH_Meta"
# The organization's one landing zone: raw files from every source, in the platform's workspace.
# Entities read it with [source] landing = "<folder>/<pattern>"; each team builds its own Bronze from it.
# With one central landing workspace (Preview), each source is in a zone, and each zone is its own
# lakehouse there (LH_Landing for Shared, LH_Landing_<Name> for the rest): see Store.landing_zone.
LANDING_LAKEHOUSE = "LH_Landing"
SHARED_ZONE = "shared"
# A column type as META_SCHEMAS spells it: the class (pl.String) or an instance (pl.Datetime("us")).
# polars takes either in a schema.
Dtype = pl.DataType | type[pl.DataType]

META_SCHEMAS: dict[str, dict[str, Dtype]] = {
    "run_log": {"run_id": pl.String, "batch_id": pl.String, "entity": pl.String, "env": pl.String,
                "status": pl.String, "metadata": pl.String, "logic": pl.String, "load_date": pl.String,
                "started_at": pl.String, "seconds": pl.Float64, "rows_read": pl.Int64, "rows_written": pl.Int64,
                "quarantined": pl.Int64, "delta_version": pl.Int64, "backfill": pl.String, "message": pl.String,
                "steps": pl.String,
                # Set by the headless agent: which worker and host ran it, which try, which queued job.
                "worker": pl.String, "host": pl.String, "attempt": pl.Int64, "job_id": pl.String},
    "dq_results": {"run_id": pl.String, "entity": pl.String, "check": pl.String, "failed": pl.Int64,
                   "action": pl.String},
    "watermarks": {"entity": pl.String, "value": pl.String, "run_id": pl.String, "at": pl.String},
    # One row per kind of change a commit made to an entity's table (meta/drift.py schema_changes):
    # the Delta version that made it and the entity's schema version. Older rows don't have the last four.
    "schema_history": {"entity": pl.String, "run_id": pl.String, "change": pl.String, "columns": pl.String,
                       "at": pl.String, "detail": pl.String, "breaking": pl.Boolean, "delta_version": pl.Int64,
                       "schema_version": pl.Int64},
    # A promote's release record (meta/release.py): the commit, whether the files matched it, the
    # engine, and the full receipt. Older rows don't have these columns and read as empty.
    "deployments": {"env": pl.String, "version": pl.String, "source_env": pl.String, "at": pl.String,
                    "commit": pl.String, "branch": pl.String, "dirty": pl.Boolean, "pushed": pl.Boolean,
                    "engine_version": pl.String, "lock_hash": pl.String, "by": pl.String,
                    "receipt_id": pl.String, "receipt": pl.String},
    "entities": {"version": pl.String, "name": pl.String, "config": pl.String, "at": pl.String},
    # Every file of a release promoted here, base64, so the environment runs and rolls back to
    # exactly what was released (meta/snapshot.py).
    "release_files": {"version": pl.String, "path": pl.String, "sha256": pl.String, "content": pl.String,
                      "at": pl.String},
    "restores": {"entity": pl.String, "env": pl.String, "from_version": pl.Int64, "to_version": pl.Int64,
                 "new_version": pl.Int64, "by": pl.String, "reason": pl.String, "at": pl.String},
    # Files an entity has loaded (path under the lakehouse's Files, size, modified time), so the next
    # run reads only new or changed ones. Written after the table commit.
    "loaded_files": {"entity": pl.String, "lakehouse": pl.String, "path": pl.String, "size": pl.Int64,
                     "modified": pl.String, "run_id": pl.String, "at": pl.String},
}
# Delta returns rows in no particular order, so every meta table is sorted by when it was written.
_ORDER = {"run_log": "started_at", "dq_results": None, "watermarks": "at", "schema_history": "at",
          "deployments": "at", "entities": "at", "restores": "at", "loaded_files": "at",
          "release_files": "at"}


def ran_on(run: dict[str, Any]) -> str:
    """Where a run_log row ran, in words: Fabric, an agent (by name and host), or this PC."""
    if str(run.get("batch_id") or "").startswith("fabric-"):
        return "Fabric"
    if run.get("worker"):
        return f"Agent {run['worker']}" + (f" on {run['host']}" if run.get("host") else "")
    return "This PC"


def _read(uri: str, options: dict[str, str] | None, version: int | None = None) -> pl.DataFrame | None:
    try:
        table = DeltaTable(uri, storage_options=options, version=version)
    except TableNotFoundError:
        return None
    return pl.from_arrow(table.to_pyarrow_table())


read_table_uri = _read  # any Delta table by URI: a local path, or OneLake with storage options


def open_table(uri: str, options: dict[str, str] | None, version: int | None = None) -> DeltaTable | None:
    """The Delta table (its log, not its data), or None when there is none."""
    try:
        return DeltaTable(uri, storage_options=options, version=version)
    except TableNotFoundError:
        return None


def lazy(table: DeltaTable, uri: str, options: dict[str, str] | None) -> pl.LazyFrame:
    """``table`` as a LazyFrame, so a filter or column list is applied while reading: files whose
    statistics rule them out are skipped and unused columns are never loaded."""
    if options is None and "://" not in uri:
        # On this PC Polars reads the files itself and skips them by their Delta statistics.
        return pl.scan_delta(table)
    # In OneLake, read through delta-rs's own file system (the one every other read uses), which
    # still pushes the filter and the column list down to the Parquet files.
    return pl.scan_pyarrow_dataset(table.to_pyarrow_dataset())


def _scan(uri: str, options: dict[str, str] | None, version: int | None = None) -> pl.LazyFrame | None:
    """The table (at ``version``, default the latest) as a LazyFrame (see lazy); None when there's none."""
    table = open_table(uri, options, version)
    return None if table is None else lazy(table, uri, options)


scan_table_uri = _scan


def _key_bounds(df: pl.DataFrame, on: list[str]) -> list[str]:
    """``t.k >= lo AND t.k <= hi`` for each integer or text key in ``df`` (other types are left
    unbounded). Null keys never match in a MERGE, so leaving them out of the range is safe."""
    out = []
    for c in on:
        col = df[c].drop_nulls()
        if not len(col):
            continue
        if col.dtype.is_integer():
            lo, hi = str(col.min()), str(col.max())
        elif col.dtype == pl.String:
            lo, hi = ("'" + str(v).replace("'", "''") + "'" for v in (col.min(), col.max()))
        else:
            continue
        out += [f"t.`{c}` >= {lo}", f"t.`{c}` <= {hi}"]
    return out


class Store:
    env: str
    # Central landing (Preview): reads {"zones": {id: zone}, "sources": {source: zone id, "" for Shared}}
    # from the platform's catalog, called on the first landing read only. None: no central landing,
    # so [source] landing reads this environment's own LH_Landing, as before.
    landing_zones: Callable[[], dict[str, Any] | None] | None = None
    _landing: dict[str, Any] | None = None

    def landing_map(self) -> dict[str, Any] | None:
        if self.landing_zones is None:
            return None
        if self._landing is None:
            self._landing = self.landing_zones() or {}  # None: no map known, so no zones
        return self._landing

    def landing_zone(self, source: str) -> dict[str, Any] | None:
        """The zone a source's files land in, or None when there are no zones (its own LH_Landing)."""
        found = self.landing_map()
        if not found or not found.get("zones"):
            return None
        zones = found["zones"]
        zone = zones.get(found.get("sources", {}).get(source.lower()) or SHARED_ZONE) or zones.get(SHARED_ZONE)
        # Shared sources read this environment's own LH_Landing until reads are switched (core.switch_landing_reads).
        if zone and zone.get("id", SHARED_ZONE) == SHARED_ZONE and not zone.get("switched_at"):
            return None
        return zone

    def landing_lakehouse(self, zone: dict[str, Any]) -> str:
        """The lakehouse name a zone is read as, in this store."""
        return zone.get("lakehouse_name") or LANDING_LAKEHOUSE

    def uri(self, lakehouse: str, schema: str, table: str) -> str:
        raise NotImplementedError

    def options(self) -> dict[str, str] | None:
        return None

    def file_paths(self, lakehouse: str, pattern: str) -> list[Path]:
        return self.fetch_files(lakehouse, self.list_files(lakehouse, pattern))

    def list_files(self, lakehouse: str, pattern: str) -> list[dict[str, Any]]:
        """Files under the lakehouse's Files matching ``pattern``, without reading them:
        ``{"path"`` (under Files)``, "size", "modified"}``, sorted by path."""
        raise NotImplementedError

    def fetch_files(self, lakehouse: str, entries: list[dict[str, Any]]) -> list[Path]:
        """Local paths for listed files (OneLake downloads them)."""
        raise NotImplementedError

    # ---- tables

    def read(self, lakehouse: str, schema: str, table: str, version: int | None = None) -> pl.DataFrame | None:
        """The table now, or as it was at a Delta ``version`` (time travel)."""
        return _read(self.uri(lakehouse, schema, table), self.options(), version)

    def scan(self, lakehouse: str, schema: str, table: str, version: int | None = None) -> pl.LazyFrame | None:
        """The table to filter and select from before reading it (see _scan); None when there's none."""
        return _scan(self.uri(lakehouse, schema, table), self.options(), version)

    def table(self, lakehouse: str, schema: str, table: str) -> tuple[DeltaTable | None, str, dict[str, str] | None]:
        """The Delta table with its address and storage options, for :func:`lazy` and the preflight."""
        uri, options = self.uri(lakehouse, schema, table), self.options()
        return open_table(uri, options), uri, options

    def exists(self, lakehouse: str, schema: str, table: str) -> bool:
        return DeltaTable.is_deltatable(self.uri(lakehouse, schema, table), storage_options=self.options())

    def _committed(self, uri: str, table: DeltaTable) -> int:
        """After a write's commit: the table's new version. Its schema is kept too, read from the log
        this write already opened, so the run that wrote it needn't read the log again (written_schema)."""
        delta_profile.note_after_write(table)
        version = table.version()
        try:
            kept = (version, lazy(table, uri, self.options()).collect_schema())
            self._written = {**getattr(self, "_written", {}), uri: kept}
        except Exception as exc:  # noqa: BLE001  silent-ok: the commit stands; written_schema then reads the log
            _log.debug("Couldn't keep the schema of %s after writing it: %s", uri, describe(exc))
        return version

    def written_schema(self, lakehouse: str, schema: str, table: str, version: int) -> pl.Schema | None:
        """The table's schema at ``version`` when this store's last write to it made that version,
        else None (read it from the log instead)."""
        kept = getattr(self, "_written", {}).get(self.uri(lakehouse, schema, table))
        return kept[1] if kept and kept[0] == version else None

    def version(self, lakehouse: str, schema: str, table: str) -> int | None:
        try:
            return DeltaTable(self.uri(lakehouse, schema, table), storage_options=self.options()).version()
        except TableNotFoundError:
            return None

    def count(self, lakehouse: str, schema: str, table: str, version: int | None = None) -> int:
        """Rows in the table, from the Delta log's file statistics: no data is read. A file written
        without them (CONVERT TO DELTA, some other writers) would be left out of that sum, so then
        the rows are counted from the files themselves."""
        uri = self.uri(lakehouse, schema, table)
        try:
            dt_ = DeltaTable(uri, storage_options=self.options(), version=version)
        except TableNotFoundError:
            return 0
        actions = pa.table(dt_.get_add_actions(flatten=True))
        if "num_records" in actions.column_names and actions.column("num_records").null_count == 0:
            return dt_.count()
        return int(lazy(dt_, uri, self.options()).select(pl.len()).collect().item())

    def write(self, lakehouse: str, schema: str, table: str, df: pl.DataFrame, mode: str = "overwrite",
              metadata: dict[str, str] | None = None, read_version: int | None | bool = False) -> int:
        """Write the table. ``read_version`` is the Delta version the rows were worked out from
        (see read_at; None when there was no table): an overwrite then commits only if no other
        writer committed since, and raises CommitFailedError otherwise, so a read-merge-overwrite
        never drops another writer's rows. False (the default) writes whatever is there."""
        from deltalake.exceptions import CommitFailedError
        uri = self.uri(lakehouse, schema, table)
        data = df.to_arrow()
        safe = self._delta_safe(uri, schema, table, data.schema, mode == "append")
        if safe != data.schema:
            data = data.cast(safe)
        if isinstance(self, LocalStore):
            Path(uri).mkdir(parents=True, exist_ok=True)
        delta_profile.check_target(uri, self.options())
        target: Any = uri
        extra: dict[str, Any] = {}
        if mode == "overwrite" and read_version is not False:
            if read_version is None:
                extra["mode"] = "error"  # there was no table: someone else creating it first is a conflict
            else:
                # Replacing every row as a predicate against the version read makes Delta check for
                # commits since then (a plain overwrite doesn't).
                target = DeltaTable(uri, storage_options=self.options(), version=int(read_version))
                extra["predicate"] = "true"
        try:
            how = extra.get("mode", mode)
            check_held()  # the table's lease is still ours, with time to commit (HD-016)
            write_deltalake(target, data, **({"mode": mode} | extra), storage_options=self.options(),
                            schema_mode={"overwrite": "overwrite", "error": None}.get(how, "merge"),
                            commit_properties=CommitProperties(custom_metadata=metadata) if metadata else None)
        except Exception as exc:
            if extra.get("mode") == "error" and "already exists" in str(exc):
                raise CommitFailedError(f"{table} was created by another writer meanwhile") from exc
            raise
        return self._committed(uri, DeltaTable(uri, storage_options=self.options()))

    def write_stream(self, lakehouse: str, schema: str, table: str, rows: Any,
                     metadata: dict[str, str] | None = None, read_version: int | None = None) -> int:
        """Replace the table with ``rows`` (a pyarrow RecordBatchReader), written as they come, in
        one commit. As with write(), the commit is made against ``read_version`` (None: there was
        no table), so another writer committing meanwhile makes it fail instead of being lost."""
        from deltalake.exceptions import CommitFailedError
        uri = self.uri(lakehouse, schema, table)
        safe = self._delta_safe(uri, schema, table, rows.schema, False)
        if safe != rows.schema:
            rows = pa.RecordBatchReader.from_batches(safe, (b.cast(safe) for b in rows))
        if isinstance(self, LocalStore):
            Path(uri).mkdir(parents=True, exist_ok=True)
        delta_profile.check_target(uri, self.options())
        target: Any = uri
        extra: dict[str, Any] = {"mode": "error"}
        if read_version is not None:
            target = DeltaTable(uri, storage_options=self.options(), version=int(read_version))
            extra = {"mode": "overwrite", "predicate": "true"}
        self.last_cleanup = None
        before: set[str] | None = None
        if self.removes_unwritten:
            try:
                before = self._data_files(lakehouse, schema, table)
            except Exception as exc:  # noqa: BLE001 - the write goes ahead; only its cleanup is lost
                _log.warning("Couldn't list %s.%s before writing it: %s", schema, table, describe(exc))
        try:
            check_held()
            write_deltalake(target, rows, **extra, storage_options=self.options(),
                            schema_mode="overwrite" if read_version is not None else None,
                            commit_properties=CommitProperties(custom_metadata=metadata) if metadata else None)
        except Exception as exc:
            if not self.removes_unwritten:
                self.last_cleanup = {"removed": 0, "note": "the files this write left are left for VACUUM: in OneLake "
                                                           "another writer's files that aren't committed yet can't be "
                                                           "told apart from them"}
            elif isinstance(exc, LeaseLost):
                self.last_cleanup = dict(_LEASE_LOST)
            elif before is None:
                self.last_cleanup = {"removed": 0, "note": "couldn't list the table's files before the write, so any "
                                                           "it left are left for VACUUM"}
            else:
                self.last_cleanup = self._remove_unwritten(lakehouse, schema, table, before, read_version)
            if read_version is None and "already exists" in str(exc):
                raise CommitFailedError(f"{table} was created by another writer meanwhile") from exc
            raise
        return self._committed(uri, DeltaTable(uri, storage_options=self.options()))

    def _delta_safe(self, uri: str, schema: str, table: str, columns: pa.Schema, append: bool) -> pa.Schema:
        """``columns`` with each type delta-rs can't write made Fabric-safe, or a plain error naming the
        table and column (localdata.delta.delta_safe), before anything is written (#954)."""
        from ..localdata import delta as local_delta

        def existing() -> dict[str, pa.DataType]:
            current = open_table(uri, self.options())
            return {} if current is None else \
                {f.name.lower(): f.type for f in pa.schema(current.schema().to_arrow())}
        try:
            return local_delta.delta_safe(columns, existing if append else None)
        except ValueError as exc:
            raise ValueError(f"Table {schema}.{table}: {exc}") from None

    def _merge_source(self, uri: str, schema: str, table: str, df: pl.DataFrame) -> pa.Table:
        """``df`` as Arrow a MERGE can take: types made Fabric-safe as for an append, so a TIME column
        stops with a plain message and a UInt32 isn't narrowed to INT (#954)."""
        data = df.to_arrow()
        safe = self._delta_safe(uri, schema, table, data.schema, True)
        return data.cast(safe) if safe != data.schema else data

    # ---- files a write that didn't commit left behind (PB-067)

    # What the last write_stream that failed did about its files: {"removed": n, "note": text}, or None.
    last_cleanup: dict[str, Any] | None = None
    # Only on this PC: the run's lease covers every writer of a local table. In OneLake a Fabric job can
    # be writing the same table, and its uncommitted files look just like this write's, so they're
    # all left for VACUUM there.
    removes_unwritten = False

    def _data_files(self, lakehouse: str, schema: str, table: str) -> set[str]:
        """The table folder's files outside _delta_log, as paths relative to the folder."""
        raise NotImplementedError

    def _delete_data_files(self, lakehouse: str, schema: str, table: str, paths: list[str]) -> None:
        raise NotImplementedError

    def _remove_unwritten(self, lakehouse: str, schema: str, table: str, before: set[str],
                          read_version: int | None) -> dict[str, Any]:
        """After a stream that didn't commit: remove the data files it wrote. Those are the files that
        weren't in the folder when it started and that no version of the table's log names. Only
        while the log is still at ``read_version``: a commit by another writer meanwhile means
        another writer was at work, and its files can't be told from these, so they are all left
        for VACUUM. The run holds the table's lease (leases.py), so no other run of this app writes it;
        a run that no longer holds it (expired, taken over) removes nothing, since the new holder's
        files can't be told from these either."""
        try:
            check_held()
        except LeaseLost:
            return dict(_LEASE_LOST)
        try:
            current = open_table(self.uri(lakehouse, schema, table), self.options())
            if (current.version() if current is not None else None) != read_version:
                return {"removed": 0, "note": "another writer committed to the table meanwhile, so the files this "
                                              "write left are left for VACUUM"}
            # With no commit since the write started, no version of the log can name a file that wasn't
            # there then; the latest version's files are left out anyway, to be sure.
            named = set(pa.table(current.get_add_actions()).column("path").to_pylist()) if current is not None else set()
            new = sorted(p for p in self._data_files(lakehouse, schema, table) - before if p not in named)
            self._delete_data_files(lakehouse, schema, table, new)
            return {"removed": len(new), "note": f"removed {len(new)} data file{'' if len(new) == 1 else 's'} this write left"}
        except Exception as exc:  # noqa: BLE001 - the write's own error is what the run reports
            _log.warning("Couldn't remove the files a failed write left in %s.%s: %s", schema, table, describe(exc))
            return {"removed": 0, "note": f"couldn't remove the files this write left ({describe(exc)}); VACUUM "
                                          "removes them later"}

    def read_at(self, lakehouse: str, schema: str, table: str) -> tuple[pl.DataFrame | None, int | None]:
        """The table and the Delta version it was read at (None, None when there's no table), so a
        write computed from it can commit against that same version."""
        try:
            t = DeltaTable(self.uri(lakehouse, schema, table), storage_options=self.options())
        except TableNotFoundError:
            return None, None
        return pl.from_arrow(t.to_pyarrow_table()), t.version()

    def read_where(self, lakehouse: str, schema: str, table: str, where: pl.Expr,
                   columns: list[str] | None = None, version: int | None = None
                   ) -> tuple[pl.DataFrame | None, int | None]:
        """Only the rows matching ``where`` (and only ``columns``), with the Delta version they were
        read at (default the latest), as read_at gives the whole table. Files whose statistics rule
        ``where`` out are skipped, so a few rows of a large table cost a few files. (None, None)
        when there's no table."""
        uri, options = self.uri(lakehouse, schema, table), self.options()
        t = open_table(uri, options, version)
        if t is None:
            return None, None
        lf = lazy(t, uri, options).filter(where)
        return (lf.select(columns) if columns else lf).collect(), t.version()

    def column_max(self, lakehouse: str, schema: str, table: str, column: str,
                   version: int | None = None) -> Any:
        """The highest value of ``column`` (None when there are no rows or no table), from the Delta
        log's file statistics when every file has them, so no data is read. A file without them
        means the column itself is read (that column only)."""
        uri, options = self.uri(lakehouse, schema, table), self.options()
        t = open_table(uri, options, version)
        if t is None:
            return None
        actions = pa.table(t.get_add_actions(flatten=True))
        name = f"max.{column}"
        if name in actions.column_names and actions.num_rows and actions.column(name).null_count == 0:
            # A file's max can be a row since deleted (a deletion vector), so this can be higher
            # than the true max, never lower.
            return max(actions.column(name).to_pylist())
        return lazy(t, uri, options).select(pl.col(column).max()).collect().item()

    def create_if_absent(self, lakehouse: str, schema: str, table: str, columns: dict[str, Dtype]) -> bool:
        """Create an empty table, only if there is none: never overwrites one another writer made or
        filled. True if this call created it."""
        uri = self.uri(lakehouse, schema, table)
        try:
            DeltaTable(uri, storage_options=self.options())
            return False
        except TableNotFoundError:
            pass
        if isinstance(self, LocalStore):
            Path(uri).mkdir(parents=True, exist_ok=True)
        # mode="ignore" is one put-if-absent commit of version 0: when two writers race, one creates it
        # and the other leaves it as it is.
        DeltaTable.create(uri, schema=pl.DataFrame(schema=columns).to_arrow().schema, mode="ignore",
                          storage_options=self.options())
        return True

    def merge_insert(self, lakehouse: str, schema: str, table: str, df: pl.DataFrame, on: list[str],
                     version: int | None = None) -> int:
        """Insert the rows whose ``on`` columns aren't in the table yet, as one Delta MERGE commit.

        With ``version`` (the one the rows were computed from, see read_at), the commit fails with
        CommitFailedError if any other writer committed since, so nothing is decided on stale data.
        """
        from deltalake.exceptions import CommitFailedError
        uri = self.uri(lakehouse, schema, table)
        data = self._merge_source(uri, schema, table, df)
        try:
            target = DeltaTable(uri, storage_options=self.options(), version=version)
        except TableNotFoundError:
            if version is not None:
                raise
            # A new table: created empty only if still absent, then merged into like any other.
            self.create_if_absent(lakehouse, schema, table, dict(pl.from_arrow(data.schema.empty_table()).schema))
            target = DeltaTable(uri, storage_options=self.options(), version=0)
        current = DeltaTable(uri, storage_options=self.options())
        delta_profile.check_table(current)
        if version is not None and current.version() != version:
            raise CommitFailedError(f"{table} changed since version {version}")
        predicate = " AND ".join(f"t.{c} = s.{c}" for c in on)
        check_held()
        target.merge(data, predicate, source_alias="s", target_alias="t") \
            .when_not_matched_insert_all().execute()
        return self._committed(uri, DeltaTable(uri, storage_options=self.options()))

    def upsert(self, lakehouse: str, schema: str, table: str, df: pl.DataFrame, on: list[str],
               metadata: dict[str, str] | None = None, prune: list[str] | None = None,
               read_version: int | None = None, new_from: tuple[str, int] | None = None,
               delete_flag: str | None = None) -> tuple[int, dict[str, Any]]:
        """Replace the rows whose ``on`` columns match and add the rest, as one Delta MERGE commit.

        Only the files holding matched rows are rewritten; the rest of the table is never read into
        memory. New columns in ``df`` are added to the table. Returns the new version and delta-rs's
        merge metrics (rows updated and inserted, files added and removed). Raises CommitFailedError
        when another writer's commit conflicts, like any other write. ``prune`` names other columns
        whose range in ``df`` bounds every matching target row (default: the ``on`` columns).
        ``read_version`` is the version ``df`` was worked out from: the MERGE then commits against
        it, so a commit by another writer since then raises CommitFailedError instead of being
        merged over (new surrogate keys depend on the keys the table already had). ``new_from``
        (column, first new key) makes rows from that key up part of what the MERGE reads, so a
        concurrent insert there conflicts. ``delete_flag`` names a boolean column of ``df``: its true
        rows delete the matching table rows instead (the source deletes of [load] deletes = "hard"),
        in the same commit, and the column is never written.
        """
        uri = self.uri(lakehouse, schema, table)
        data = self._merge_source(uri, schema, table, df)
        delta_profile.check_target(uri, self.options())
        target = DeltaTable(uri, storage_options=self.options(), version=read_version)
        # The keys' range bounds the target rows that can match, so delta-rs skips files by their
        # statistics and scans less (a 1,000-row merge into 5 million rows peaks at half the memory).
        bounds = " AND ".join(_key_bounds(df, prune or on))
        if bounds and new_from:
            # Also read the rows from the first new key up, so a writer that inserted keys there
            # meanwhile is a conflict rather than a key handed out twice.
            col, first = new_from
            bounds = f"(({bounds}) OR t.`{col}` >= {int(first)})"
        predicate = " AND ".join(([bounds] if bounds else []) + [f"t.`{c}` = s.`{c}`" for c in on])
        check_held()
        merger = target.merge(data, predicate, source_alias="s", target_alias="t", merge_schema=True,
                              commit_properties=CommitProperties(custom_metadata=metadata) if metadata else None)
        if delete_flag:
            # Rows flagged in df remove their match from the table; the flag itself is never written.
            flag = f"coalesce(s.`{delete_flag}`, false)"  # a null flag is a row to keep, never one dropped
            merger = merger.when_matched_delete(flag) \
                .when_matched_update_all(except_cols=[delete_flag]) \
                .when_not_matched_insert_all(predicate=f"NOT {flag}", except_cols=[delete_flag])
        else:
            merger = merger.when_matched_update_all().when_not_matched_insert_all()
        metrics = merger.execute()
        return self._committed(uri, DeltaTable(uri, storage_options=self.options())), metrics

    def history(self, lakehouse: str, schema: str, table: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            return DeltaTable(self.uri(lakehouse, schema, table), storage_options=self.options()).history(limit)
        except TableNotFoundError:
            return []

    def has_commit(self, lakehouse: str, schema: str, table: str, key: str, value: str,
                   since: int | None = None) -> bool:
        """Whether a commit after version ``since`` (None: any commit) carries ``key`` = ``value`` in
        its commit metadata. Reads only the commits made since then, however many other writers made."""
        try:
            t = DeltaTable(self.uri(lakehouse, schema, table), storage_options=self.options())
        except TableNotFoundError:
            return False
        if since is not None and t.version() <= since:
            return False
        limit = None if since is None else t.version() - since
        return any(commit.get(key) == value for commit in t.history(limit))

    # ---- files

    def read_files(self, lakehouse: str, pattern: str,
                   entries: list[dict[str, Any]] | None = None) -> tuple[pl.DataFrame | None, list[str]]:
        """Every file matching ``pattern`` as one frame, or only ``entries`` (from list_files)."""
        paths = self.fetch_files(lakehouse, entries) if entries is not None else self.file_paths(lakehouse, pattern)
        frames = []
        for p in paths:
            suffix = p.suffix.lower()
            if suffix == ".csv":
                frames.append(pl.read_csv(p, try_parse_dates=True, infer_schema_length=10000))
            elif suffix == ".parquet":
                frames.append(pl.read_parquet(p))
            elif suffix in (".json", ".jsonl", ".ndjson"):
                frames.append(pl.read_ndjson(p) if suffix != ".json" else pl.read_json(p))
            else:
                raise ValueError(f"Can't read {p.name}: use CSV, Parquet or JSON")
        if not frames:
            return None, []
        return pl.concat(frames, how="diagonal_relaxed"), [p.name for p in paths]

    # ---- run metadata

    def meta_append(self, table: str, rows: list[dict[str, Any]], metadata: dict[str, str] | None = None) -> None:
        if not rows:
            return
        schema = META_SCHEMAS[table]
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        df = pl.DataFrame([{k: r.get(k) for k in schema} | ({"at": r.get("at") or now} if "at" in schema else {})
                           for r in rows], schema=schema)
        self.write(META_LAKEHOUSE, "meta", table, df, mode="append", metadata=metadata)

    def meta(self, table: str) -> pl.DataFrame:
        df = self.read(META_LAKEHOUSE, "meta", table)
        if df is None:
            return pl.DataFrame(schema=META_SCHEMAS[table])
        order = _ORDER.get(table)
        return df.sort(order, maintain_order=True) if order else df

    def meta_where(self, table: str, where: pl.Expr) -> pl.DataFrame:
        """The rows of a meta table that match ``where``, in meta()'s order: meta(table).filter(where)
        without reading the whole table (files whose statistics rule ``where`` out are skipped)."""
        df, _ = self.read_where(META_LAKEHOUSE, "meta", table, where)
        if df is None:
            return pl.DataFrame(schema=META_SCHEMAS[table])
        order = _ORDER.get(table)
        return df.sort(order, maintain_order=True) if order else df


class LocalStore(Store):
    """Delta tables on this PC, laid out like OneLake: <root>/<lakehouse>/Tables/<schema>/<table>."""

    def __init__(self, root: Path, env: str = "dev", landing: Path | None = None):
        self.root = Path(root)
        self.env = env
        # The PC's shared landing folder (every project on this PC reads the same one).
        self.landing = Path(landing) if landing else None

    def uri(self, lakehouse: str, schema: str, table: str) -> str:
        return str(self.root / lakehouse / "Tables" / schema / table)

    # Runs at once on this PC can hit the Windows Delta log race (retrying.windows_log_race). Each call here
    # is safe to repeat: a read, create-if-absent, or an insert-only MERGE (a repeat after a commit
    # that landed inserts nothing, and one pinned to a version fails with CommitFailedError instead).
    def read(self, lakehouse: str, schema: str, table: str, version: int | None = None) -> pl.DataFrame | None:
        return local_retry(lambda: super(LocalStore, self).read(lakehouse, schema, table, version))

    def scan(self, lakehouse: str, schema: str, table: str, version: int | None = None) -> pl.LazyFrame | None:
        return local_retry(lambda: super(LocalStore, self).scan(lakehouse, schema, table, version))

    def read_at(self, lakehouse: str, schema: str, table: str) -> tuple[pl.DataFrame | None, int | None]:
        return local_retry(lambda: super(LocalStore, self).read_at(lakehouse, schema, table))

    def read_where(self, lakehouse: str, schema: str, table: str, where: pl.Expr,
                   columns: list[str] | None = None, version: int | None = None
                   ) -> tuple[pl.DataFrame | None, int | None]:
        return local_retry(lambda: super(LocalStore, self).read_where(lakehouse, schema, table, where, columns,
                                                                       version))

    def column_max(self, lakehouse: str, schema: str, table: str, column: str,
                   version: int | None = None) -> Any:
        return local_retry(lambda: super(LocalStore, self).column_max(lakehouse, schema, table, column, version))

    def create_if_absent(self, lakehouse: str, schema: str, table: str, columns: dict[str, Dtype]) -> bool:
        return local_retry(lambda: super(LocalStore, self).create_if_absent(lakehouse, schema, table, columns))

    def merge_insert(self, lakehouse: str, schema: str, table: str, df: pl.DataFrame, on: list[str],
                     version: int | None = None) -> int:
        return local_retry(lambda: super(LocalStore, self).merge_insert(lakehouse, schema, table, df, on, version))

    def landing_lakehouse(self, zone: dict[str, Any]) -> str:
        return LANDING_LAKEHOUSE  # on this PC every zone is the one local landing folder

    removes_unwritten = True

    def _data_files(self, lakehouse: str, schema: str, table: str) -> set[str]:
        root = Path(self.uri(lakehouse, schema, table))
        if not root.is_dir():
            return set()
        return {p.relative_to(root).as_posix() for p in root.rglob("*")
                if p.is_file() and p.relative_to(root).parts[0] != "_delta_log"}

    def _delete_data_files(self, lakehouse: str, schema: str, table: str, paths: list[str]) -> None:
        root = Path(self.uri(lakehouse, schema, table))
        for rel in paths:
            (root / rel).unlink(missing_ok=True)

    def files_dir(self, lakehouse: str) -> Path:
        if lakehouse == LANDING_LAKEHOUSE and self.landing is not None:
            return self.landing / "Files"
        return self.root / lakehouse / "Files"

    def list_files(self, lakehouse: str, pattern: str) -> list[dict[str, Any]]:
        root = self.files_dir(lakehouse)
        out = []
        for p in sorted(p for p in root.glob(pattern) if p.is_file()):
            st = p.stat()
            out.append({"path": p.relative_to(root).as_posix(), "size": st.st_size,
                        "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat()})
        return out

    def fetch_files(self, lakehouse: str, entries: list[dict[str, Any]]) -> list[Path]:
        root = self.files_dir(lakehouse)
        return [root / e["path"] for e in entries]


class OneLakeStore(Store):
    """Delta tables in an environment's workspace: one lakehouse per layer, addressed by GUID."""

    def __init__(self, env: str, workspace_id: str, lakehouse_ids: dict[str, str],
                 storage_token: Callable[[], str], cache_dir: Path, files: FileStore | None = None,
                 lakehouse_workspaces: dict[str, str] | None = None):
        self.env = env
        self.workspace_id = workspace_id
        self.lakehouse_ids = lakehouse_ids
        # A layer can live in another workspace (its designation's): lakehouse name -> workspace id.
        self.lakehouse_workspaces = dict(lakehouse_workspaces or {})
        self._token = storage_token
        self.cache_dir = Path(cache_dir)
        self._files = files or FileStore(storage_token)

    def _lakehouse_id(self, lakehouse: str) -> str:
        if lakehouse not in self.lakehouse_ids:
            how = "run Dev to create it" if self.env == "dev" else "promote to create it"
            raise NotSetUp(f"{lakehouse} doesn't exist in the {self.env} workspace yet; {how}")
        return self.lakehouse_ids[lakehouse]

    def workspace_of(self, lakehouse: str) -> str:
        return self.lakehouse_workspaces.get(lakehouse) or self.workspace_id

    def landing_lakehouse(self, zone: dict[str, Any]) -> str:
        """A zone's lakehouse in the central landing workspace, the same one for every environment."""
        name = zone.get("lakehouse_name") or LANDING_LAKEHOUSE
        if not zone.get("lakehouse_id") or not zone.get("workspace_id"):
            raise LookupError(f"the landing zone {zone.get('name') or zone.get('id')} has no lakehouse yet: "
                              "a platform admin sets it up in Platform > Set up > Landing zone")
        self.lakehouse_ids[name] = zone["lakehouse_id"]
        self.lakehouse_workspaces[name] = zone["workspace_id"]
        return name

    def uri(self, lakehouse: str, schema: str, table: str) -> str:
        return TableRef(self.workspace_of(lakehouse), self._lakehouse_id(lakehouse), schema, table).uri

    def options(self) -> dict[str, str]:
        return storage_options(self._token())

    def list_files(self, lakehouse: str, pattern: str) -> list[dict[str, Any]]:
        lh = self._lakehouse_id(lakehouse)
        directory, _, name_pattern = f"Files/{pattern.strip('/')}".rpartition("/")
        out = [{"path": entry["path"].removeprefix("Files/"), "size": entry["size"], "modified": entry["modified"]}
               for entry in self._files.list(self.workspace_of(lakehouse), lh, directory)
               if not entry["is_dir"] and fnmatch.fnmatch(entry["name"], name_pattern)]
        return sorted(out, key=lambda e: e["path"])

    def fetch_files(self, lakehouse: str, entries: list[dict[str, Any]]) -> list[Path]:
        lh, ws = self._lakehouse_id(lakehouse), self.workspace_of(lakehouse)
        return [self._files.download(ws, lh, f"Files/{e['path']}", self.cache_dir / self.env / lakehouse / "Files" / e["path"])
                for e in entries]
