"""Direct Lake check on the Semantic model tab (model_directlake.py does the checking).

Reads the model's definition from the environment's workspace (getDefinition as TMSL, read only), then each
table's Delta log and Parquet footers from OneLake, and checks on this PC. With ``keys`` it also reads the key
column on the one side of each relationship, to count values that appear more than once. Starts no Fabric job
and writes nothing; reading from OneLake uses capacity.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from . import model_directlake
from .model_directlake import TableStats
from .onelake import TableRef
from .redact import describe

METHODS = {"meta_model_directlake_setup", "meta_model_directlake_check"}
FOOTERS_READ = 200  # row groups come from this many files' footers at most; above it they're estimated

_log = logging.getLogger(__name__)


def table_stats(uri: str, options: dict[str, str] | None, footers: int = FOOTERS_READ) -> TableStats | None:
    """A Delta table's columns, rows, files and size from its log, and row groups from its Parquet footers.
    None when there's no Delta table there."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from deltalake import DeltaTable
    from deltalake.exceptions import TableNotFoundError
    from deltalake.fs import DeltaStorageHandler

    try:
        table = DeltaTable(uri, storage_options=options)
    except TableNotFoundError:
        return None
    fields = table.schema().fields
    columns = [(f.name, f.type.type if isinstance(f.type.type, str) else str(f.type)) for f in fields]
    actions = pa.table(table.get_add_actions(flatten=True))
    paths = actions.column("path").to_pylist() if actions.num_rows else []
    sizes = actions.column("size_bytes").to_pylist() if actions.num_rows else []
    counts = actions.column("num_records").to_pylist() if "num_records" in actions.column_names else [None] * len(paths)
    rows = None if any(c is None for c in counts) else int(sum(counts))
    groups = 0
    if paths:
        fs = DeltaStorageHandler(table.table_uri, options)
        step = max(1, len(paths) // footers)  # spread the sample over the table, not just its first files
        sample = paths[::step][:footers]
        for path in sample:
            with fs.open_input_file(path) as f:
                groups += pq.read_metadata(f).num_row_groups
        if len(sample) < len(paths):
            groups = round(groups / len(sample) * len(paths))
    return TableStats(columns=columns, rows=rows, files=len(paths), bytes=int(sum(sizes)),
                      row_groups=groups, row_groups_estimated=len(paths) > footers)


def duplicate_values(uri: str, options: dict[str, str] | None, column: str) -> int:
    """How many of a column's non-empty values appear more than once. Reads only that column."""
    import polars as pl

    from .meta.store import lazy, open_table

    table = open_table(uri, options)
    if table is None:
        return 0
    return int(lazy(table, uri, options).select(column).drop_nulls().group_by(column).len()
               .filter(pl.col("len") > 1).select(pl.len()).collect().item())


class ModelDirectLakeMixin:
    def _directlake_uri(self, workspace_id: str, lakehouse_id: str, schema: str, table: str) -> tuple[str, dict[str, str]]:
        """Where a Direct Lake table's Delta table is, with the options to read it."""
        ref = TableRef(workspace_id, lakehouse_id, schema or "dbo", table, flat=not schema)
        return ref.uri, self._onelake_options()  # type: ignore[attr-defined]

    def _onelake_options(self) -> dict[str, str]:
        from .onelake import storage_options
        return storage_options(self.auth.storage_token())  # type: ignore[attr-defined]

    def _directlake_lakehouse(self, project: Any, env: str, cfg: dict[str, Any], found: dict[str, Any],
                              cache: dict[str, tuple[str, str] | None]) -> tuple[str, str] | None:
        """(workspace id, lakehouse id) a Direct Lake expression reads, from the environment's workspaces."""
        if found["kind"] == "onelake":
            return found["workspace"], found["item"]
        endpoint = found["endpoint"]
        if endpoint in cache:
            return cache[endpoint]
        workspaces = [cfg["workspace_id"]]
        try:
            store = self._store(project, env)  # type: ignore[attr-defined]
            workspaces += [store.workspace_of(lh) for lh in getattr(store, "lakehouse_ids", {})]
        except Exception as exc:  # noqa: BLE001 - the environment's own workspace is still searched
            _log.info("No lakehouse workspaces for the Direct Lake check: %s", describe(exc))
        for ws in dict.fromkeys(w for w in workspaces if w):
            for lh in self.fabric.list_lakehouses(ws):  # type: ignore[attr-defined]
                props = lh.get("properties") or {}
                if "sqlEndpointProperties" not in props:
                    props = (self.fabric.get_lakehouse(ws, lh["id"]).get("properties") or {})  # type: ignore[attr-defined]
                if str((props.get("sqlEndpointProperties") or {}).get("id", "")).lower() == endpoint:
                    cache[endpoint] = (ws, lh["id"])
                    return cache[endpoint]
        cache[endpoint] = None
        return None

    def _env_sku(self, env: str) -> str:
        try:
            doc = self.capacity_health()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the person picks the capacity instead
            _log.info("Capacity not known for the Direct Lake check: %s", describe(exc))
            return ""
        e = next((x for x in doc.get("environments", []) if x.get("env") == env), {})
        return str((e.get("capacity") or {}).get("sku") or "")

    def meta_model_directlake_setup(self, env: str = "test") -> dict[str, Any]:
        """The capacity SKUs the check knows, and the environment's own when it's known."""
        sku = self._env_sku(env)
        return {"env": env, "sku": sku if model_directlake.limits(sku) else "", "capacity_sku": sku,
                "skus": model_directlake.SKUS}

    def meta_model_directlake_check(self, env: str = "test", name: str = "", sku: str = "",
                                    keys: bool = False) -> dict[str, Any]:
        """What would make the model fall back to DirectQuery or fail. Reads only; changes nothing."""
        project, cfg = self._ossie_workspace(env)  # type: ignore[attr-defined]
        model = self._ossie_model(cfg["workspace_id"], name)  # type: ignore[attr-defined]
        if model is None:
            raise LookupError(f"There's no semantic model named {name} in {cfg.get('workspace_name') or 'the workspace'}")
        bim = self._model_bim(cfg["workspace_id"], model)  # type: ignore[attr-defined]
        tables = model_directlake.direct_lake_tables(bim)
        lim = model_directlake.limits(sku or self._env_sku(env))
        base = {"env": env, "name": name, "workspace_name": cfg.get("workspace_name", ""),
                "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "sku": lim["sku"] if lim else ""}
        if not tables:
            return base | {"direct_lake": False, "findings": [], "tables": [], "counts": {"fail": 0, "warn": 0, "info": 0},
                           "unread": [], "keys_checked": False, "limits": lim}
        cache: dict[str, tuple[str, str] | None] = {}
        stats: dict[str, TableStats | None] = {}
        where: dict[str, tuple[str, dict[str, str]]] = {}
        unread = []
        for t in tables:
            found = model_directlake.source(bim, t.expression)
            lakehouse = self._directlake_lakehouse(project, env, cfg, found, cache) if found else None
            if lakehouse is None:
                unread.append({"table": t.name, "reason": "It reads a lakehouse outside this environment's workspaces."})
                continue
            try:
                uri, options = self._directlake_uri(*lakehouse, t.schema, t.entity)
                stats[t.name] = table_stats(uri, options)
                where[t.name] = (uri, options)
            except Exception as exc:  # noqa: BLE001 - one table that can't be read doesn't stop the others
                unread.append({"table": t.name, "reason": describe(exc)})
        duplicates = None
        if keys:
            duplicates = {}
            for table, column in model_directlake.unique_keys(bim):
                t = next((x for x in tables if x.name == table), None)
                col = next((c for c in (t.columns if t else []) if c["name"] == column), None)
                if t is None or col is None or not stats.get(table) or table not in where:
                    continue
                src = col["source"] or column
                if src not in dict(stats[table].columns):  # type: ignore[union-attr]
                    continue  # reported as a missing column
                try:
                    duplicates[f"{table}.{column}"] = duplicate_values(*where[table], src)
                except Exception as exc:  # noqa: BLE001 - the rest of the check still stands
                    unread.append({"table": table, "reason": f"Key {column} not read ({describe(exc)})"})
        ours = name in {f"SM_{layer.title()}" for layer in project.layer_names()}
        out = model_directlake.check(bim, stats, lim, duplicates, ours=ours)
        return base | out | {"direct_lake": True, "unread": unread, "keys_checked": keys}
