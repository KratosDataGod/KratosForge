"""The app's calls for local data projects (localdata/): SQL over files on this PC, no Fabric needed."""

from __future__ import annotations

from typing import Any

from .localdata import LocalData
from .localdata.transform import Transforms

# Every call here works offline and never reaches Fabric or OneLake.
METHODS = {"list_local_projects", "local_create_project", "local_forget_project", "local_close_project",
           "local_project_settings", "local_add_source", "local_remove_source", "local_refresh",
           "list_local_tables", "local_set_types", "local_query", "local_query_start", "local_query_status",
           "local_query_cancel", "local_explain", "local_write_delta", "local_change_delta", "local_delta_history",
           "local_drop_delta", "local_transform_plan", "local_transform_run", "local_transform_runs"}


# A managed run's state, as local_query_status named it before KF-DE-027.
_LEGACY_STATES = {"queued": "running", "running": "running", "cancelling": "running", "completed": "done",
                  "failed": "error", "timed_out": "error", "cancelled": "cancelled"}


class LocalDataMixin:
    root: Any
    sql_runs: Any  # Service.sql_runs: the managed SQL runs (sql_exec.py)

    @property
    def _local_query_limits(self) -> dict[str, int]:
        """Row limits asked for by local_query_start, by run id."""
        if getattr(self, "_local_limits", None) is None:
            self._local_limits: dict[str, int] = {}
        return self._local_limits

    @property
    def local_data(self) -> LocalData:
        if getattr(self, "_local_data", None) is None:
            self._local_data = LocalData(self.root / "local-data")
        return self._local_data

    def list_local_projects(self) -> list[dict[str, Any]]:
        return self.local_data.projects()

    def local_create_project(self, name: str, storage: str = "persistent", folder: str = "",
                             settings: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.local_data.create(name, storage, folder, settings)

    def local_forget_project(self, project: str) -> dict[str, Any]:
        """Take a project off the list. Its folder and database are left on disk."""
        folder = self.local_data.project(project).folder
        self.local_data.forget(project)
        return {"forgotten": project, "folder": folder}

    def local_close_project(self, project: str) -> dict[str, Any]:
        """Close the database, so another program (a notebook) can open it."""
        self.local_data.close(project)
        return {"closed": project}

    def local_project_settings(self, project: str, settings: dict[str, Any]) -> dict[str, Any]:
        return self.local_data.set_settings(project, settings)

    def local_add_source(self, project: str, path: str, schema: str | None = None, naming: str | None = None,
                         max_depth: int | None = None, exclude: list[str] | None = None,
                         names: dict[str, str] | None = None) -> dict[str, Any]:
        return self.local_data.add_source(project, path, schema=schema, naming=naming, max_depth=max_depth,
                                          exclude=exclude, names=names)

    def local_remove_source(self, project: str, path: str) -> dict[str, Any]:
        """Stop reading a folder: its views go. The files themselves are never touched."""
        self.local_data.engine(project).remove_source(path)
        return self.local_data.catalog(project)

    def local_refresh(self, project: str, path: str | None = None) -> dict[str, Any]:
        result = self.local_data.engine(project).refresh(path, force=True)
        return result | {"sources": self.local_data.catalog(project)["sources"]}

    def list_local_tables(self, project: str) -> dict[str, Any]:
        return self.local_data.catalog(project)

    def local_set_types(self, project: str, table: str, columns: dict[str, str]) -> dict[str, Any]:
        """Read a discovered file's columns as these types (empty: as inferred)."""
        return self.local_data.engine(project).set_override(table, columns)

    def local_query(self, project: str, query: str, dialect: str = "duckdb", limit: int = 1000,
                    timeout: float | None = None) -> dict[str, Any]:
        return self.local_data.engine(project).execute(query, dialect=dialect, limit=limit, timeout=timeout)

    def local_explain(self, project: str, query: str, dialect: str = "duckdb", analyze: bool = False,
                      timeout: float | None = None) -> dict[str, Any]:
        """How the local engine runs one query; ``analyze`` runs it and reports each operator's rows and time."""
        return self.local_data.engine(project).explain(query, dialect=dialect, analyze=analyze, timeout=timeout)

    # Deprecated (KF-DE-027): background queries run as managed SQL runs now, sql_start with
    # target="local" (sql_exec.py): one lifecycle, with paging and export. These three forward to it
    # and keep their old answers for existing callers.

    def local_query_start(self, project: str, query: str, dialect: str = "duckdb", limit: int = 1000,
                          timeout: float | None = None, confirm: bool = False) -> dict[str, Any]:
        """Deprecated: use ``sql_start(query, target="local", project=...)``. Starts that run;
        ``local_query_status`` returns its first ``limit`` rows. DROP, TRUNCATE and DELETE or UPDATE
        without WHERE run only with ``confirm`` (sql_policy.py)."""
        status = self.sql_runs.start(query, None, dialect, target="local", project=project, confirm=confirm,
                                     timeout=timeout)
        self._local_query_limits[status["id"]] = max(0, min(int(limit), 100_000))
        return {"query_id": status["id"], "state": "running"}

    def local_query_status(self, query_id: str) -> dict[str, Any]:
        """Deprecated: use ``sql_status`` and ``sql_page``. ``state`` is running, done, cancelled or
        error, as before; ``result`` holds the first rows once done."""
        status = self.sql_runs.status(query_id)
        out: dict[str, Any] = {"query_id": query_id, "project": status["project"], "elapsed": status["seconds"],
                               "state": _LEGACY_STATES[status["state"]]}
        if status["state"] == "completed":
            limit = self._local_query_limits.get(query_id, 1000)
            page = self.sql_runs.page(query_id, 0, limit) if limit else {"rows": []}
            out["result"] = {"columns": status["columns"], "rows": page["rows"][:limit], "row_count": status["rows"],
                             "truncated": status["rows"] > limit, "query_id": query_id, "engine": "local",
                             "dialect": status["dialect"], "project": status["project"], "seconds": status["seconds"]}
        elif status["state"] in ("failed", "timed_out", "cancelled"):
            out["error"] = status["error"]
            if status["refused"]:
                out["refused"] = status["refused"]
        return out

    def local_query_cancel(self, query_id: str) -> dict[str, Any]:
        """Deprecated: use ``sql_cancel``."""
        try:
            before = self.sql_runs.status(query_id)["state"]
        except KeyError:
            return {"cancelled": False}
        if before not in ("queued", "running"):
            return {"cancelled": False}
        self.sql_runs.cancel(query_id)
        return {"cancelled": True}

    # The project's own Delta tables (<project>/Tables/<schema>/<table>), also written by SQL:
    # CREATE TABLE t USING DELTA AS SELECT ..., INSERT INTO t SELECT ..., INSERT OVERWRITE t SELECT ...

    def local_write_delta(self, project: str, table: str, query: str, mode: str = "create",
                          dialect: str = "duckdb", partition_by: list[str] | None = None,
                          schema_mode: str | None = None, dry_run: bool = False,
                          timeout: float | None = None) -> dict[str, Any]:
        """Write a query's rows to a Delta table in the project, in one commit. ``dry_run`` runs the
        query and says what would be written, writing nothing."""
        return self.local_data.engine(project).write_delta(table, query, mode=mode, dialect=dialect,
                                                           partition_by=partition_by, schema_mode=schema_mode,
                                                           dry_run=dry_run, timeout=timeout)

    def local_change_delta(self, project: str, query: str, dialect: str = "duckdb", dry_run: bool = False,
                           confirm: bool = False, timeout: float | None = None) -> dict[str, Any]:
        """Run one MERGE INTO, UPDATE or DELETE on a Delta table in the project, in one commit.
        ``dry_run`` says how many rows would change, changing nothing; an UPDATE or DELETE without
        WHERE needs ``confirm``."""
        return self.local_data.engine(project).change_delta(query, dialect=dialect, dry_run=dry_run,
                                                            confirm=confirm, timeout=timeout)

    def local_transform_plan(self, project: str) -> dict[str, Any]:
        """The project's SQL steps (transforms/<schema>/<name>.sql) in the order a run takes them."""
        return Transforms(self.local_data.engine(project)).plan()

    def local_transform_run(self, project: str, params: dict[str, Any] | None = None,
                            steps: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
        """Run the project's SQL steps in order, each making its table. ``steps``: these and the steps
        that read their tables. ``dry_run`` checks each query and makes nothing."""
        return Transforms(self.local_data.engine(project)).run(params=params, only=steps, dry_run=dry_run)

    def local_transform_runs(self, project: str, limit: int = 20) -> dict[str, Any]:
        return {"runs": Transforms(self.local_data.engine(project)).runs(limit)}

    def local_delta_history(self, project: str, table: str, limit: int = 50) -> dict[str, Any]:
        return self.local_data.engine(project).delta_history(table, limit)

    def local_drop_delta(self, project: str, table: str, confirm: bool = False) -> dict[str, Any]:
        """Delete one of the project's Delta tables with every version. Without ``confirm``, only
        says what would be deleted."""
        return self.local_data.engine(project).drop_delta(table, confirm)
