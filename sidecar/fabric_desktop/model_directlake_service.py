"""Direct Lake check on the Semantic model tab (model_directlake.py does the checking).

Reads the model's definition from the environment's workspace (getDefinition as TMSL, read only), then each
table's Delta log and Parquet footers from OneLake, and checks on this PC. With ``keys`` it also reads both
columns of each relationship: values that appear more than once on the one side, and rows on the many side whose
key has no match there (Power BI shows those under "(Blank)"). Starts no Fabric job and writes nothing; reading
from OneLake uses capacity.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from . import model_directlake
from .model_directlake import TableStats
from .onelake import TableRef
from .auth import SignInError, SignInRequired
from .redact import describe

METHODS = {"meta_model_directlake_setup", "meta_model_directlake_check"}
FOOTERS_READ = 200  # row groups come from this many files' footers at most; above it they're estimated
HISTORY_COMMITS = 200  # the forecast reads at most this many of a table's latest commits ...
HISTORY_BUDGET = 2_000  # ... and at most this many commit files for the whole model
MIN_HISTORY = 20  # every table gets at least this many, however many tables the model has

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


def _log_actions(fs: Any, version: int) -> list[dict[str, Any]]:
    """The actions of one commit's log file, or [] when it's gone (log retention removed it)."""
    import json

    try:
        with fs.open_input_file(f"_delta_log/{version:020d}.json") as f:
            text = f.read().decode("utf-8")
    except FileNotFoundError:  # narrow: older commits are cleaned up after the log retention period
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _records(add: dict[str, Any]) -> int | None:
    import json

    try:
        n = json.loads(add.get("stats") or "{}").get("numRecords")
    except ValueError:  # narrow: a writer that wrote no or broken stats leaves the row count unknown
        return None
    return int(n) if isinstance(n, int) else None


def history(uri: str, options: dict[str, str] | None, max_commits: int = HISTORY_COMMITS) -> list[model_directlake.Commit] | None:
    """The table after each of its latest commits (at most ``max_commits``), rebuilt backwards from today's files
    and the add and remove actions in each commit's log file, through the same reader as table_stats. Rows are
    known back to the first commit that removes a file whose row count wasn't seen. None when there's no Delta
    table there. Reads only the log; writes nothing."""
    from deltalake import DeltaTable
    from deltalake.exceptions import TableNotFoundError
    from deltalake.fs import DeltaStorageHandler

    try:
        table = DeltaTable(uri, storage_options=options)
    except TableNotFoundError:
        return None
    import pyarrow as pa

    fs = DeltaStorageHandler(table.table_uri, options)
    current = pa.table(table.get_add_actions(flatten=True)).to_pylist()
    records: dict[str, int | None] = {a["path"]: a.get("num_records") for a in current}
    logs = []  # newest first; a gap (log retention) ends the history
    for version in range(table.version(), max(-1, table.version() - max_commits), -1):
        actions = _log_actions(fs, version)
        if not actions:
            break
        logs.append((version, actions))
        for a in actions:
            if "add" in a:
                records.setdefault(a["add"]["path"], _records(a["add"]))
    files = len(current)
    counts = [a.get("num_records") for a in current]
    rows: int | None = None if any(n is None for n in counts) else sum(int(n) for n in counts)
    commits = []
    for version, actions in logs:
        info: dict[str, Any] = next((a["commitInfo"] for a in actions if "commitInfo" in a), {})
        adds = [a["add"] for a in actions if "add" in a]
        removes = [a["remove"] for a in actions if "remove" in a]
        stamp = info.get("timestamp") or max((a.get("modificationTime") or 0 for a in adds), default=0)
        if not stamp:
            break  # a commit with no time can't be placed on the forecast's line
        commits.append(model_directlake.Commit(version, int(stamp), files, rows))
        files += len(removes) - len(adds)
        if rows is not None:
            added = [records.get(a["path"]) for a in adds]
            gone = [records.get(r["path"], _records(r)) for r in removes]
            known = [n for n in added + gone if n is not None]
            rows = None if len(known) < len(added) + len(gone) else \
                rows - sum(n for n in added if n is not None) + sum(n for n in gone if n is not None)
    return commits[::-1]


def predict_every_sku(bim: dict[str, Any], stats: dict[str, TableStats | None],
                      commits: dict[str, list[model_directlake.Commit]], sku: str, **kw: Any) -> dict[str, Any]:
    """The fallback prediction at the picked SKU, and at every other one under ``by_sku``, so picking another
    capacity on the card judges the same numbers again without reading anything."""
    out = model_directlake.predict(bim, stats, commits, sku, **kw)
    keep = ("sku", "limits", "will_label", "model", "counts", "tables", "size")
    out["by_sku"] = {s: {k: v for k, v in model_directlake.predict(bim, stats, commits, s, **kw).items() if k in keep}
                     for s in model_directlake.SKUS}
    return out


def duplicate_values(uri: str, options: dict[str, str] | None, column: str) -> int:
    """How many of a column's non-empty values appear more than once. Reads only that column."""
    import polars as pl

    from .meta.store import lazy, open_table

    table = open_table(uri, options)
    if table is None:
        return 0
    return int(lazy(table, uri, options).select(column).drop_nulls().group_by(column).len()
               .filter(pl.col("len") > 1).select(pl.len()).collect().item())


def _type(stats: dict[str, TableStats | None], table: str, where: tuple[str, Any, str]) -> str:
    return dict(stats[table].columns).get(where[2], "")  # type: ignore[union-attr]


def unmatched_rows(many: tuple[str, dict[str, str] | None, str], one: tuple[str, dict[str, str] | None, str]) -> dict[str, int]:
    """Rows of the many side whose key isn't in the one side's column: ``rows`` with a key that has no match (and
    how many distinct ``values``), and ``empty`` rows with no key at all. Each is a (uri, options, column); reads only
    those two columns."""
    import polars as pl

    from .meta.store import lazy, open_table

    (m_uri, m_opt, m_col), (o_uri, o_opt, o_col) = many, one
    m_table, o_table = open_table(m_uri, m_opt), open_table(o_uri, o_opt)
    if m_table is None or o_table is None:
        return {"rows": 0, "values": 0, "empty": 0}
    keys = lazy(m_table, m_uri, m_opt).select(pl.col(m_col).alias("k"))
    known = lazy(o_table, o_uri, o_opt).select(pl.col(o_col).alias("k")).drop_nulls().unique()
    missing = keys.drop_nulls().join(known, on="k", how="anti")
    rows, values = missing.select(pl.len().alias("rows"), pl.col("k").n_unique().alias("values")).collect().row(0)
    empty = keys.select(pl.col("k").null_count()).collect().item()
    return {"rows": int(rows), "values": int(values), "empty": int(empty)}


class ModelDirectLakeMixin:
    def _directlake_uri(self, workspace_id: str, lakehouse_id: str, schema: str, table: str) -> tuple[str, dict[str, str]]:
        """Where a Direct Lake table's Delta table is, with the options to read it."""
        ref = TableRef(workspace_id, lakehouse_id, schema or "dbo", table, flat=not schema)
        return ref.uri, self._onelake_options()

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
        e: dict[str, Any] = next((x for x in doc.get("environments", []) if x.get("env") == env), {})
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
        signed_out = False
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
            except (SignInRequired, SignInError):  # narrow: no OneLake sign-in; the card says how to sign in
                signed_out = True
                unread.append({"table": t.name, "reason": "There's no OneLake sign-in on this PC."})
            except Exception as exc:  # noqa: BLE001 - one table that can't be read doesn't stop the others
                unread.append({"table": t.name, "reason": describe(exc)})
        commits: dict[str, list[model_directlake.Commit]] = {}
        per_table = min(HISTORY_COMMITS, max(MIN_HISTORY, HISTORY_BUDGET // max(1, len(where))))
        for table, (uri, options) in where.items():
            if not stats.get(table):
                continue
            try:
                found_commits = history(uri, options, per_table)
            except Exception as exc:  # noqa: BLE001 - a table without history still gets today's verdict
                _log.info("Delta history not read for the Direct Lake forecast: %s", describe(exc))
                continue
            if found_commits is not None:
                commits[table] = found_commits
        duplicates = unmatched = None
        if keys:
            duplicates, unmatched = {}, {}

            def column(table: str, col_name: str) -> tuple[str, dict[str, str] | None, str] | None:
                """Where a model column's values are, or None when it can't be read (or is reported as missing)."""
                t = next((x for x in tables if x.name == table), None)
                col = next((c for c in (t.columns if t else []) if c["name"] == col_name), None)
                if col is None or not stats.get(table) or table not in where:
                    return None
                src = col["source"] or col_name
                return (*where[table], src) if src in dict(stats[table].columns) else None  # type: ignore[union-attr]

            for table, key in model_directlake.unique_keys(bim):
                one = column(table, key)
                if one is None:
                    continue
                try:
                    duplicates[f"{table}.{key}"] = duplicate_values(*one)
                except Exception as exc:  # noqa: BLE001 - the rest of the check still stands
                    unread.append({"table": table, "reason": f"Key {key} not read ({describe(exc)})"})
            for r in model_directlake.relationships(bim):
                many, one = column(r["from_table"], r["from_column"]), column(r["to_table"], r["to_column"])
                if many is None or one is None or _type(stats, r["from_table"], many) != _type(stats, r["to_table"], one):
                    continue  # types that differ are reported on their own
                try:
                    unmatched[f"{r['from_table']}.{r['from_column']}"] = unmatched_rows(many, one)
                except Exception as exc:  # noqa: BLE001 - the rest of the check still stands
                    unread.append({"table": r["from_table"], "reason": f"Key {r['from_column']} not matched ({describe(exc)})"})
        ours = name in {f"SM_{layer.title()}" for layer in project.layer_names()}
        out = model_directlake.check(bim, stats, lim, duplicates, ours=ours, unmatched=unmatched)
        prediction = predict_every_sku(bim, stats, commits, lim["sku"] if lim else "",
                                       layer=name[3:] if ours else "", read_problem="no_signin" if signed_out else "")
        return base | out | {"direct_lake": True, "unread": unread, "keys_checked": keys, "prediction": prediction}
