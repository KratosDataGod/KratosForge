"""The app's calls for local data projects (localdata/): SQL over files on this PC, no Fabric needed."""

from __future__ import annotations

from typing import Any

from .localdata import LocalData

# Every call here works offline and never reaches Fabric or OneLake.
METHODS = {"list_local_projects", "local_create_project", "local_forget_project", "local_close_project",
           "local_project_settings", "local_add_source", "local_remove_source", "local_refresh",
           "list_local_tables", "local_set_types", "local_query", "local_query_start", "local_query_status",
           "local_query_cancel"}


class LocalDataMixin:
    root: Any

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

    def local_query_start(self, project: str, query: str, dialect: str = "duckdb", limit: int = 1000,
                          timeout: float | None = None) -> dict[str, Any]:
        return self.local_data.start(project, query, dialect=dialect, limit=limit, timeout=timeout)

    def local_query_status(self, query_id: str) -> dict[str, Any]:
        return self.local_data.status(query_id)

    def local_query_cancel(self, query_id: str) -> dict[str, Any]:
        return self.local_data.cancel(query_id)
