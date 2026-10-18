"""Where the optimization tables live: one lakehouse in OneLake, or a folder on this PC.

Both reuse the metadata framework's stores (meta/store.py) for the Delta work: create-if-absent
(two installations creating the same table at once leave one table), insert-only MERGE, and
reads. The folder on this PC is laid out like a lakehouse and is what the tests use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import polars as pl

from ..meta.store import LocalStore, OneLakeStore
from ..onelake import FileStore, TableRef
from ..retrying import local_retry
from . import schema

NAME = "optimization"  # the one lakehouse these stores hold, as meta/store.py's methods name it

class _Tables:
    """The calls Optimize makes, by table name (``fdopt_...``)."""

    schemas: bool

    def table_names(self) -> list[str]:
        raise NotImplementedError

    def has(self, name: str) -> bool:
        return self.exists(NAME, schema.SCHEMA, name)  # type: ignore[attr-defined]

    def rows(self, name: str) -> pl.DataFrame | None:
        return self.read(NAME, schema.SCHEMA, name)  # type: ignore[attr-defined]

    def columns_of(self, name: str) -> dict[str, pl.DataType]:
        lf = self.scan(NAME, schema.SCHEMA, name)  # type: ignore[attr-defined]
        return dict(lf.collect_schema()) if lf is not None else {}

    def create(self, name: str, columns: dict[str, pl.DataType]) -> bool:
        return self.create_if_absent(NAME, schema.SCHEMA, name, columns)  # type: ignore[attr-defined]

    def add_columns(self, name: str, columns: dict[str, pl.DataType]) -> list[str]:
        """Add the ``columns`` the table doesn't have yet, as nullable columns: one schema-merge
        commit of no rows. Existing rows read the new columns as null."""
        have = self.columns_of(name)
        new = {c: t for c, t in columns.items() if c not in have}
        if new:
            self.write(NAME, schema.SCHEMA, name, pl.DataFrame(schema=have | new), mode="append")  # type: ignore[attr-defined]
        return list(new)

    def insert_new(self, name: str, df: pl.DataFrame) -> int:
        """Insert the rows whose record_id isn't in the table yet: one commit."""
        return self.merge_insert(NAME, schema.SCHEMA, name, df, on=[schema.KEY])  # type: ignore[attr-defined]

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError


class LocalLakehouse(_Tables, LocalStore):
    """A folder on this PC laid out like a lakehouse: <root>/Tables/dbo/<table> (or Tables/<table>)."""

    def __init__(self, root: Path, schemas: bool = True):
        super().__init__(Path(root), env="optimize")
        self.schemas = schemas

    def uri(self, lakehouse: str, schema_: str, table: str) -> str:
        return str(self.root / "Tables" / (f"{schema_}/{table}" if self.schemas else table))

    # Two installations sharing a folder on Windows can hit the Delta log race (retrying.windows_log_race).
    # LocalStore covers its own repeat-safe calls; these also cover exists, scan and the add-columns append.
    def has(self, name: str) -> bool:
        return local_retry(lambda: super(LocalLakehouse, self).has(name))

    def rows(self, name: str) -> pl.DataFrame | None:
        return local_retry(lambda: super(LocalLakehouse, self).rows(name))

    def columns_of(self, name: str) -> dict[str, pl.DataType]:
        return local_retry(lambda: super(LocalLakehouse, self).columns_of(name))

    def create(self, name: str, columns: dict[str, pl.DataType]) -> bool:
        return local_retry(lambda: super(LocalLakehouse, self).create(name, columns))

    def add_columns(self, name: str, columns: dict[str, pl.DataType]) -> list[str]:
        return local_retry(lambda: super(LocalLakehouse, self).add_columns(name, columns))

    def insert_new(self, name: str, df: pl.DataFrame) -> int:
        return local_retry(lambda: super(LocalLakehouse, self).insert_new(name, df))

    def table_names(self) -> list[str]:
        folder = self.root / "Tables" / (schema.SCHEMA if self.schemas else "")
        return sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []

    def describe(self) -> dict[str, Any]:
        return {"kind": "folder", "path": str(self.root)}


class OneLakeLakehouse(_Tables, OneLakeStore):
    """A lakehouse in OneLake, addressed by workspace and lakehouse ids, as the signed-in person."""

    def __init__(self, workspace_id: str, lakehouse_id: str, schemas: bool, storage_token: Callable[[], str],
                 cache_dir: Path, files: FileStore | None = None):
        super().__init__("optimize", workspace_id, {NAME: lakehouse_id}, storage_token, cache_dir, files)
        self.lakehouse_id = lakehouse_id
        self.schemas = schemas

    def uri(self, lakehouse: str, schema_: str, table: str) -> str:
        return TableRef(self.workspace_id, self.lakehouse_id, schema_, table, flat=not self.schemas).uri

    def table_names(self) -> list[str]:
        folder = f"Tables/{schema.SCHEMA}" if self.schemas else "Tables"
        try:
            entries = self._files.list(self.workspace_id, self.lakehouse_id, folder)
        except Exception as exc:
            # A new lakehouse has no Tables/dbo folder until its first table.
            if getattr(getattr(exc, "response", None), "status_code", None) == 404:
                return []
            raise
        return sorted(e["name"] for e in entries if e["is_dir"])

    def describe(self) -> dict[str, Any]:
        return {"kind": "onelake", "workspace_id": self.workspace_id, "lakehouse_id": self.lakehouse_id}
