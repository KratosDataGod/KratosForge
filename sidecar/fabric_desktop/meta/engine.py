"""Runs entities: the generic steps every table goes through, with custom code at fixed points.

    read -> contract -> surrogate keys -> clean -> derive -> hook after_clean -> custom step -> lookups
         -> hook before_write -> output contract -> checks (gate 2) -> write -> log

The engine is the only code that writes a managed table. Custom code receives rows and
returns rows; it can read only the tables it declared and cannot write anything.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import uuid
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable

import polars as pl

from . import keys as keys_mod
from .project import SYSTEM_COLUMNS, Entity, GateError, Project, impact, load_python, run_order, validate
from . import batches as batches_mod
from . import preflight
from .store import LANDING_LAKEHOUSE, Store, lazy, open_table, read_table_uri

# A write that loses a Delta commit race re-reads the table and merges again, this many times.
WRITE_ATTEMPTS = 3

# Columns the quarantine adds to each held row: why, which run, when, and the row's own id.
QUARANTINE_COLUMNS = ["_check", "_run_id", "_loaded_at", "_qid"]
FAR_PAST = dt.date(1900, 1, 1)
FAR_FUTURE = dt.date(9999, 12, 31)
_SIMPLE_TYPES: dict[str, pl.DataType] = {
    "string": pl.String, "str": pl.String, "int64": pl.Int64, "bigint": pl.Int64, "int32": pl.Int32,
    "int": pl.Int32, "float64": pl.Float64, "double": pl.Float64, "float32": pl.Float32, "bool": pl.Boolean,
    "boolean": pl.Boolean, "date": pl.Date,
    # Fabric reads Delta timestamps as UTC; naive timestamps would need a newer Delta writer version.
    "datetime": pl.Datetime("us", "UTC"), "timestamp": pl.Datetime("us", "UTC"),
}


def _scd2_effective(versions: pl.LazyFrame, incoming: pl.DataFrame, e: Entity) -> pl.DataFrame:
    """The incoming rows a Type 2 merge would act on: new keys, keys whose tracked columns changed,
    and keys with a non-null Type 1 value that differs from any of their versions (scd2_merge copies
    it onto every version). Reads only the key, tracked and Type 1 columns."""
    scd = e.spec["scd"]
    bk, tracked = e.keys, list(scd["tracked"])
    type1 = [c for c in scd.get("type1", []) if c in incoming.columns]
    keys = incoming.select(bk).unique().drop_nulls()
    have = versions.select(bk + ["is_current"] + tracked + type1).join(keys.lazy(), on=bk, how="semi").collect()
    current = have.filter(pl.col("is_current")).select(bk + tracked).with_columns(pl.lit(True).alias("__known"))
    j = incoming.with_row_index("__r").join(current, on=bk, how="left", suffix="__c")
    same = pl.all_horizontal([pl.col(c).eq_missing(pl.col(f"{c}__c")) for c in tracked])
    keep = set(j.filter(pl.col("__known").is_null() | ~same)["__r"].to_list())
    if type1:
        t1 = incoming.with_row_index("__r").select(["__r"] + bk + type1).join(
            have.select(bk + type1), on=bk, how="inner", suffix="__v")
        differs = pl.any_horizontal([pl.col(c).is_not_null() & ~pl.col(c).eq_missing(pl.col(f"{c}__v")) for c in type1])
        keep |= set(t1.filter(differs)["__r"].to_list())
    return incoming.with_row_index("__r").filter(pl.col("__r").is_in(list(keep))).drop("__r")


def _type_changes(df: pl.DataFrame, have: pl.Schema) -> str:
    """Why a MERGE can't be used: a column whose type differs from the table's ('' if none)."""
    changed = [f"{c} {have[c]} to {t}" for c, t in df.schema.items() if c in have and have[c] != t]
    return f"rewrote the table because column types changed ({', '.join(changed)})" if changed else ""


def _fill(value: bool, n: int) -> pl.Series:
    return pl.Series([value] * n, dtype=pl.Boolean)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class CheckStop(Exception):
    pass


class NothingNew(Exception):
    """The source has nothing this run hasn't loaded already (every file is loaded)."""


class DecisionNeeded(Exception):
    def __init__(self, message: str, options: list[str]):
        super().__init__(message)
        self.options = options


def polars_type(name: str) -> pl.DataType:
    name = name.strip().lower()
    m = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", name)
    if m:
        return pl.Decimal(int(m.group(1)), int(m.group(2)))
    if name not in _SIMPLE_TYPES:
        raise ValueError(f"unknown column type {name!r}")
    return _SIMPLE_TYPES[name]


def _cast(df: pl.DataFrame, col: str, dtype: pl.DataType) -> pl.Expr:
    src = df.schema[col]
    if src == dtype:
        return pl.col(col)
    if src == pl.String and dtype == pl.Date:
        return pl.col(col).str.strip_chars().str.to_date(strict=False)
    if src == pl.String and isinstance(dtype, pl.Datetime):
        return pl.col(col).str.strip_chars().str.to_datetime(strict=False, time_zone="UTC").cast(dtype)
    if isinstance(src, pl.Datetime) and isinstance(dtype, pl.Datetime) and src.time_zone is None:
        return pl.col(col).dt.replace_time_zone("UTC").cast(dtype)
    if src == pl.String and dtype == pl.Boolean:
        lower = pl.col(col).str.strip_chars().str.to_lowercase()
        return (pl.when(lower.is_in(["true", "1", "yes", "y"])).then(True)
                .when(lower.is_in(["false", "0", "no", "n"])).then(False).otherwise(None))
    if src == pl.String and dtype.is_numeric():
        return pl.col(col).str.strip_chars().cast(dtype, strict=False)
    return pl.col(col).cast(dtype, strict=False)


def _wm_literal(value: str, dtype: pl.DataType) -> Any:
    s = pl.Series([value])
    if dtype == pl.Date:
        return s.str.to_date().item()
    if isinstance(dtype, pl.Datetime):
        return s.str.to_datetime(time_zone="UTC").cast(dtype).item()
    return s.cast(dtype).item()


def _wm_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@dataclass
class Step:
    name: str
    rows: int
    seconds: float
    detail: str = ""


@dataclass
class RunResult:
    run_id: str
    entity: str
    status: str  # ok | failed | needs_decision | skipped
    steps: list[Step] = field(default_factory=list)
    rows_read: int = 0
    rows_written: int = 0
    quarantined: int = 0
    delta_version: int | None = None
    message: str = ""
    options: list[str] = field(default_factory=list)
    # A failure another try may fix (network, throttling, a table busy with another writer).
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__ | {"steps": [s.__dict__ for s in self.steps]}
        return d


class HookContext:
    """What custom code receives as ``ctx``."""

    def __init__(self, engine: "Engine", entity: Entity):
        self._engine, self._entity = engine, entity
        self.run_id = engine.run_id
        self.load_date = engine.load_date
        self.env = engine.store.env
        self.params = dict(entity.spec.get("params", {}))
        self.notes: list[str] = []

    def read_table(self, name: str) -> pl.DataFrame:
        allowed = self._entity.spec.get("hooks", {}).get("reads", []) + self._entity.spec.get("step", {}).get("reads", [])
        if name not in allowed:
            raise PermissionError(f"{self._entity.name} custom code read {name} without declaring it in reads")
        df = self._engine.read_entity(name)
        if df is None:
            raise LookupError(f"{name} has not been loaded in {self.env} yet")
        return df.drop([c for c in SYSTEM_COLUMNS if c in df.columns])

    def log(self, message: str) -> None:
        self.notes.append(str(message))


class Engine:
    # Batch ids start with this; the Fabric runner uses "fabric-" so the app can tell where a run ran.
    batch_prefix = "b-"

    def __init__(self, project: Project, store: Store, load_date: dt.date | None = None,
                 keys_store: Store | None = None, source_options: Callable[[], dict[str, str] | None] | None = None,
                 table_lock: Callable[[Entity], AbstractContextManager[Any]] | None = None,
                 run_context: dict[str, Any] | None = None, link_table: Callable[[str], str] | None = None,
                 merge_writes: bool = True):
        self.project, self.store = project, store
        # Incremental and replayed rows go into the table with a Delta MERGE (or an append), which
        # rewrites only the files they touch. False always reads the whole table and overwrites it,
        # as before 0.20; kept for comparison (examples/benchmark/large_tables.py).
        self.merge_writes = merge_writes
        # Before reading a Delta source, estimate the read from its log and stop a read that can't
        # fit in this machine's free memory (preflight.py). None for the free memory skips the check.
        self.available_memory: Callable[[], int | None] = preflight.available_memory
        # Where a link ([source] link = "alias") points in this environment: another team's table.
        self.link_table = link_table or self._no_links
        # Held for the whole of an entity's run, so no other writer (another agent, the app) can
        # read and overwrite the same table in between. The agent passes one; the app runs without.
        self.table_lock = table_lock or (lambda entity: nullcontext())
        # Extra run_log fields: which worker, host and attempt ran it (the agent sets these).
        self.run_context = dict(run_context or {})
        # Storage options for a source table elsewhere in OneLake ([source] table = "abfss://...").
        self.source_options = source_options or store.options
        self.keys_store = keys_store or store  # where LH_Keys lives: the environment's Keys workspace
        self.load_date = load_date or dt.date.today()
        self.run_id = ""
        self._evolved: list[str] = []
        self._replay_rows: pl.DataFrame | None = None
        self._held: list[pl.DataFrame] | None = None  # during a replay, rows that fail again wait here
        # Rows per batch for [load] batch_by; None works it out from free memory (batches.py).
        self.batch_rows: int | None = None
        # During a batched load, the values unique checks have seen in earlier batches.
        self._seen: dict[str, pl.Series] | None = None

    def _no_links(self, alias: str) -> str:
        raise CheckStop(f"link {alias} can't be resolved here: connect a platform, or pin its location for "
                        f"{self.store.env} in metadata/links/{alias}.toml")

    # ---- reading managed tables

    def lakehouse(self, entity: Entity) -> str:
        return self.project.layer(entity.layer).lakehouse

    def read_entity(self, name: str) -> pl.DataFrame | None:
        e = self.project.entities[name]
        return self.store.read(self.lakehouse(e), e.schema, e.table)

    def read_source_table(self, uri: str) -> pl.DataFrame | None:
        """An existing Delta table the entity loads from: an adopted lakehouse table, read as it is."""
        return read_table_uri(uri, self._uri_options(uri))

    def _uri_options(self, uri: str) -> dict[str, str] | None:
        return self.source_options() if uri.startswith(("abfss://", "https://")) else None

    # ---- batches

    def run_many(self, names: list[str] | None = None, backfill: str | None = None) -> list[RunResult]:
        order = run_order(self.project)
        targets = order if names is None else [n for n in order if n in set(names)]
        batch = self.batch_prefix + uuid.uuid4().hex[:8]
        blocked: set[str] = set()
        results = []
        for n in targets:
            upstream = [r for r in self.project.entities[n].reads() if r in blocked]
            if upstream:
                results.append(RunResult("", n, "skipped", message=f"skipped because {', '.join(upstream)} did not finish"))
                blocked.add(n)
                continue
            res = self.run(n, backfill=backfill, batch_id=batch)
            if res.status != "ok":
                blocked.add(n)
            results.append(res)
        return results

    def replay(self, name: str, rows: pl.DataFrame) -> tuple[RunResult, pl.DataFrame | None]:
        """Run quarantined rows (as text, perhaps fixed by hand) through the output contract, derive,
        the checks and the write. Returns the run and the rows that failed again, to keep in quarantine;
        nothing is held back from a run that didn't write."""
        self._replay_rows, self._held = rows, []
        try:
            res = self.run(name, backfill="replay")
            held = pl.concat(self._held, how="diagonal_relaxed") if self._held else None
        finally:
            self._replay_rows, self._held = None, None
        return res, held

    def backfill(self, name: str) -> list[RunResult]:
        """Rebuild an entity and everything downstream of it, in dependency order."""
        return self.run_many([name, *impact(self.project, name)], backfill="full")

    # ---- one entity

    def check_deployed(self) -> None:
        if self.store.env == "dev":
            return
        deps = self.store.meta("deployments").filter(pl.col("env") == self.store.env)
        deployed = deps["version"][-1] if deps.height else None
        if deployed != self.project.version:
            raise GateError("promote", [f"{self.store.env} is on metadata {deployed or 'nothing yet'}, and this "
                                        f"project is {self.project.version}. Promote it first."])

    def run(self, name: str, backfill: str | None = None, batch_id: str = "") -> RunResult:
        errors = validate(self.project, self.load_date)
        if errors:
            raise GateError("save", errors)
        self.check_deployed()
        if name not in self.project.entities:
            raise KeyError(f"no entity named {name}")
        self.project.purge_code()
        self.run_id = f"r-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        e = self.project.entities[name]
        res = RunResult(self.run_id, name, "ok")
        started = time.perf_counter()
        started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        self._dq: list[dict[str, Any]] = []
        self._schema_changes: list[dict[str, Any]] = []
        self._files_read: tuple[str, list[dict[str, Any]]] | None = None
        try:
            with self.table_lock(e):
                if backfill == "migrate":
                    self._migrate(e, res)
                if not (self._batched_applies(e, backfill) and self._run_batched(e, res, backfill)):
                    df = self._replayed(e, res) if backfill == "replay" else self._transform(e, res, backfill)
                    df, bad = self._checks(e, df, res)
                    self._write(e, df, res, backfill)
                self._record_files(e, res)
        except NothingNew as ex:  # nothing to do is a successful run
            res.steps.append(Step("read", 0, 0.0, str(ex)))
        except DecisionNeeded as ex:
            res.status, res.message, res.options = "needs_decision", str(ex), ex.options
            return res
        except CheckStop as ex:
            res.status, res.message = "failed", str(ex)
        except Exception as ex:  # custom code and source errors are reported, not raised
            from ..retrying import is_retryable
            res.status, res.message, res.retryable = "failed", f"{type(ex).__name__}: {ex}", is_retryable(ex)
        seconds = round(time.perf_counter() - started, 3)
        self.store.meta_append("run_log", [{
            "run_id": self.run_id, "batch_id": batch_id, "entity": name, "env": self.store.env,
            "status": res.status, "metadata": self.project.version, "logic": self.project.fingerprint(e),
            "load_date": self.load_date.isoformat(), "started_at": started_at, "seconds": seconds,
            "rows_read": res.rows_read, "rows_written": res.rows_written, "quarantined": res.quarantined,
            "delta_version": res.delta_version, "backfill": backfill or "", "message": res.message,
            "steps": json.dumps([s.__dict__ for s in res.steps])} | self.run_context])
        self.store.meta_append("dq_results", self._dq)
        self.store.meta_append("schema_history", self._schema_changes)
        return res

    def _step(self, res: RunResult, name: str, t0: float, rows: int, detail: str = "") -> None:
        res.steps.append(Step(name, rows, round(time.perf_counter() - t0, 3), detail))

    def _last_ok(self, e: Entity) -> dict[str, Any] | None:
        log = self.store.meta("run_log").filter((pl.col("entity") == e.name) & (pl.col("status") == "ok"))
        return log.row(-1, named=True) if log.height else None

    def _watermark(self, e: Entity) -> str | None:
        w = self.store.meta("watermarks").filter(pl.col("entity") == e.name)
        return w["value"][-1] if w.height else None

    # ---- steps before the checks

    def _read(self, e: Entity, res: RunResult, full: bool) -> pl.DataFrame:
        t0 = time.perf_counter()
        src = e.spec.get("source", {})
        if not src:
            df, detail = pl.DataFrame(), "no source: the custom step provides the rows"
        elif "files" in src or "landing" in src:
            where = LANDING_LAKEHOUSE if "landing" in src else self.lakehouse(e)
            pattern = src.get("landing") or src["files"]
            try:
                listed = self.store.list_files(where, pattern)
            except LookupError as exc:
                if where != LANDING_LAKEHOUSE:
                    raise
                raise CheckStop(f"there's no landing zone for {self.store.env} yet: create or join the platform in "
                                f"Settings > Platform ({exc})") from exc
            if not listed:
                raise CheckStop(f"no files match {pattern} in {where}/Files")
            todo, detail = listed, f"{plural(len(listed), 'file')}"
            if self._tracks_files(e):
                if not full:
                    done = self._loaded_files(e, where)
                    todo = [f for f in listed if (f["path"], f["size"], f["modified"]) not in done]
                    detail = f"{plural(len(todo), 'new file')} of {len(listed)}"
                    if not todo:
                        raise NothingNew(f"no new files: all {plural(len(listed), 'file')} matching {pattern} are loaded")
                self._files_read = (where, todo)  # a full rebuild has loaded every file too
            df, names = self.store.read_files(where, pattern, todo)
            if df is None:
                raise CheckStop(f"no files match {pattern} in {where}/Files")
            if src.get("add_source_columns", True):
                df = df.with_columns(pl.lit(self.load_date).alias("_load_date")) if "_load_date" in e.columns else df
        else:
            lf, keep, est, detail = self._plan_read(e, full)
            if e.spec.get("load", {}).get("memory_guard", True) is not False:
                why = preflight.too_big(est, self.available_memory())
                if why:
                    raise CheckStop(why)
            df = lf.select(keep).collect()
        df, detail = self._renamed(src, df, detail)
        res.rows_read = df.height
        self._step(res, "read", t0, df.height, detail)
        return df

    @staticmethod
    def _renamed(src: dict[str, Any], df: pl.DataFrame, detail: str) -> tuple[pl.DataFrame, str]:
        renames = {k: v for k, v in src.get("rename", {}).items() if k in df.columns}
        if renames:  # columns the source renamed, mapped back to the names the contract uses
            df = df.rename(renames)
            detail += "; renamed " + ", ".join(f"{k} to {v}" for k, v in renames.items())
        return df, detail

    def _plan_read(self, e: Entity, full: bool) -> tuple[pl.LazyFrame, list[str], preflight.Estimate, str]:
        """A Delta source (table, link or entity) ready to read: the scan with its filter, the columns
        to read, the estimate from its log, and the read step's text so far."""
        src = e.spec["source"]
        if "table" in src or "link" in src:
            uri = src["table"] if "table" in src else self.link_table(src["link"])
            options = self._uri_options(uri)
            table = open_table(uri, options)
            if table is None:
                what = f"link {src['link']}" if "link" in src else (src.get("from") or src["table"])
                raise CheckStop(f"the source table {what} doesn't exist")
        else:
            up = self.project.entities[src["entity"]]
            table, uri, options = self.store.table(self.lakehouse(up), up.schema, up.table)
            if table is None:
                raise CheckStop(f"{src['entity']} has not been loaded in {self.store.env} yet")
        lf = lazy(table, uri, options)
        # The filter and the column list go into the scan, so only the files and columns this run
        # needs are read: an incremental run of a large table reads its new rows, not all of it.
        schema = dict(lf.collect_schema())
        names = list(schema)
        detail = "full"
        after = None
        wm_col = e.spec.get("load", {}).get("watermark")
        if e.mode == "incremental" and wm_col and not full:
            wm = self._watermark(e)
            if wm is not None and wm_col in names:
                after = (wm_col, _wm_literal(wm, schema[wm_col]))
                lf = lf.filter(pl.col(wm_col) > after[1])
                detail = f"incremental, {wm_col} after {wm}"
        keep = self._source_columns(e, names)
        total = len([c for c in names if c not in SYSTEM_COLUMNS])
        if len(keep) < total:
            detail += f"; read {plural(len(keep), 'column')} of {total}"
        est = preflight.estimate(table, keep, schema, after)
        detail += f"; {est.text()}"
        return lf, keep, est, detail

    def preflight(self, name: str, backfill: str | None = None) -> dict[str, Any]:
        """What running ``name`` would read, without reading it: for the run dialog."""
        e = self.project.entities[name]
        src = e.spec.get("source", {})
        if not any(k in src for k in ("table", "link", "entity")):
            return {"entity": name, "estimate": None, "note": "only Delta sources (tables, links, entities) are estimated"}
        _, _, est, detail = self._plan_read(e, backfill == "full")
        free = self.available_memory()
        guard = e.spec.get("load", {}).get("memory_guard", True) is not False
        if self._batched_applies(e, backfill):
            col = e.spec["load"]["batch_by"]
            return {"entity": name, "estimate": est.to_dict(), "detail": detail, "free_memory_bytes": free,
                    "stop": "", "guard": guard, "batch_by": col,
                    "note": f"read in batches by {col}: memory is needed per batch, and the largest batch is "
                            "checked against free memory when the run starts"}
        return {"entity": name, "estimate": est.to_dict(), "detail": detail, "free_memory_bytes": free,
                "stop": preflight.too_big(est, free) if guard else "", "guard": guard}

    # ---- full loads in batches ([load] batch_by, batches.py)

    def _batched_applies(self, e: Entity, backfill: str | None) -> bool:
        """Whether this run replaces the table from a Delta source in batches: a full load, or an
        incremental entity's first load or full rebuild, with [load] batch_by set."""
        if not e.spec.get("load", {}).get("batch_by") or backfill in ("replay", "migrate"):
            return False
        src = e.spec.get("source", {})
        if not any(k in src for k in ("table", "link", "entity")) or "custom" in e.spec.get("step", {}):
            return False
        if e.mode == "full" or (e.mode == "incremental" and backfill == "full"):
            return True
        return e.mode == "incremental" and not self.store.exists(self.lakehouse(e), e.schema, e.table)

    def _run_batched(self, e: Entity, res: RunResult, backfill: str | None) -> bool:
        """Read, transform, check and write the source one batch at a time, replacing the table in
        one Delta commit. False when the source is empty: the ordinary path handles that."""
        import queue
        import threading

        import pyarrow as pa

        self._before_read(e, backfill)
        t0 = time.perf_counter()
        src, load = e.spec["source"], e.spec["load"]
        col = load["batch_by"]
        src_col = {v: k for k, v in (src.get("rename") or {}).items()}.get(col, col)
        lf, keep, est, detail = self._plan_read(e, True)
        if src_col not in keep:
            raise CheckStop(f"batch_by {col}: the source has no column {src_col}")
        counts = lf.group_by(src_col).len().collect(engine="streaming")
        if not int(counts["len"].sum()):
            return False
        free = self.available_memory()
        plan = batches_mod.plan(counts, src_col, self.batch_rows or batches_mod.batch_rows(est, free))
        biggest = max(plan, key=lambda b: b.rows)
        if load.get("memory_guard", True) is not False:
            part = est.part(biggest.rows)
            if preflight.too_big(part, free):
                raise CheckStop(f"batch_by {col}: the batch {biggest.text()} alone holds {biggest.rows:,} rows, about "
                                f"{preflight.mb(int(part.memory_bytes * preflight.HEADROOM))} with the steps after "
                                f"the read, but this machine has {preflight.mb(free or 0)} free. Batch by a column "
                                "with more values, run it in Fabric, or set memory_guard = false under [load] to try anyway")
        self._step(res, "plan batches", t0, int(counts["len"].sum()),
                   f"{detail}; {batches_mod.count(len(plan))} by {col}, the largest {biggest.rows:,} rows")
        lh = self.lakehouse(e)
        read_version = self.store.version(lh, e.schema, e.table)
        rebuild = e.mode == "incremental" and read_version is not None
        wm_col = load.get("watermark")
        metadata = {"fabric_desktop_run_id": self.run_id, "fabric_desktop_metadata": self.project.version,
                    "fabric_desktop_logic": self.project.fingerprint(e)}
        state: dict[str, Any] = {"written": 0, "kept": 0, "wm": None, "keys": []}
        mark = len(res.steps)
        stamp = [pl.lit(self.run_id).alias("_run_id"), pl.lit(self.load_date).alias("_loaded_at")]

        def tables():
            for b in plan:
                tb = time.perf_counter()
                df = lf.filter(b.expr(src_col)).select(keep).collect(engine="streaming")
                df, _ = self._renamed(src, df, "")
                res.rows_read += df.height
                self._step(res, "read", tb, df.height)
                df, _ = self._checks(e, self._steps(e, df, res), res)
                state["written"] += df.height
                if wm_col and wm_col in df.columns and df.height:
                    top = df[wm_col].max()
                    state["wm"] = top if state["wm"] is None or (top is not None and top > state["wm"]) else state["wm"]
                if rebuild and e.keys:
                    state["keys"].append(df.select(e.keys))
                yield df.with_columns(stamp).to_arrow()
            if rebuild and e.keys:  # rows the table has whose keys the source no longer has stay, as before
                old = self.store.scan(lh, e.schema, e.table, read_version)
                seen = pl.concat(state["keys"]).unique()
                old_counts = old.group_by(col).len().collect(engine="streaming")
                for b in batches_mod.plan(old_counts, col, self.batch_rows or batches_mod.batch_rows(est, free)):
                    kept = old.filter(b.expr(col)).join(seen.lazy(), on=e.keys, how="anti").collect(engine="streaming")
                    state["kept"] += kept.height
                    yield kept.to_arrow()

        # The batches are worked out on a thread of their own and handed to the Delta writer through
        # a queue of one: the writer pulls rows from inside delta-rs, where the steps (which read and
        # write Delta tables themselves) can't run, and the queue keeps one batch ahead at most.
        handoff: queue.Queue[Any] = queue.Queue(maxsize=1)
        stop, done = threading.Event(), object()

        def put(item: Any) -> bool:
            while not stop.is_set():
                try:
                    handoff.put(item, timeout=0.2)
                    return True
                except queue.Full:
                    continue
            return False

        def produce() -> None:
            try:
                for t in tables():
                    if not put(t):
                        return
                put(done)
            except BaseException as ex:  # a check that stops, an error in a hook: the commit never happens
                put(ex)

        def take() -> Any:
            item = handoff.get()
            if isinstance(item, BaseException):
                failure.append(item)
                raise item
            return item

        failure: list[BaseException] = []
        self._seen = {}
        worker = threading.Thread(target=produce, name=f"batches {e.name}", daemon=True)
        worker.start()
        try:
            first = take()
            schema = first.schema

            def fit(t: pa.Table) -> pa.Table:  # every batch in the first one's columns and types
                cols = [t.column(f.name).cast(f.type) if f.name in t.column_names else pa.nulls(t.num_rows, f.type)
                        for f in schema]
                return pa.Table.from_arrays(cols, schema=schema)

            def record_batches():
                item = first
                while item is not done:
                    yield from fit(item).to_batches()
                    item = take()

            tw = time.perf_counter()
            try:
                version = self.store.write_stream(lh, e.schema, e.table, pa.RecordBatchReader.from_batches(
                    schema, record_batches()), metadata=metadata, read_version=read_version)
            except Exception:
                if failure:
                    raise failure[0] from None
                raise
        finally:
            stop.set()
            worker.join()
            self._seen = None
            self._fold(res, mark)
        if state["wm"] is not None:
            self._advance_watermark(e, pl.DataFrame({wm_col: [state["wm"]]}), backfill)
        res.rows_written, res.delta_version = state["written"], version
        total = state["written"] + state["kept"]
        info = (f"rebuilt: {state['written']} rows from the full source" if rebuild
                else f"replaced with {plural(state['written'], 'row')}")
        if state["kept"]:
            info += f", kept {plural(state['kept'], 'row')} whose keys the source doesn't have"
        self._step(res, "write", tw, total, f"{info} from {batches_mod.count(len(plan))}, in one Delta commit; "
                                            f"Delta version {version}")
        return True

    def _fold(self, res: RunResult, mark: int) -> None:
        """A batched load's per-batch steps, quality results and schema changes, as one each."""
        res.steps[mark:] = [Step(*s) for s in batches_mod.fold_steps(res.steps[mark:])]
        failed: dict[tuple[str, str], int] = {}
        for r in self._dq:
            failed[(r["check"], r["action"])] = failed.get((r["check"], r["action"]), 0) + r["failed"]
        self._dq = [{"run_id": self.run_id, "entity": res.entity, "check": c, "failed": n, "action": a}
                    for (c, a), n in failed.items()]
        self._schema_changes = list({json.dumps(c, sort_keys=True): c for c in self._schema_changes}.values())

    def _tracks_files(self, e: Entity) -> bool:
        """Incremental and dimension loads from files read only files they haven't loaded (a full
        backfill reads them all). Full loads, and ``[source] new_files_only = false``, read them all."""
        return e.mode in ("incremental", "scd1", "scd2") and e.spec.get("source", {}).get("new_files_only", True) is not False

    def _loaded_files(self, e: Entity, lakehouse: str) -> set[tuple[str, int, str]]:
        m = self.store.meta("loaded_files").filter((pl.col("entity") == e.name) & (pl.col("lakehouse") == lakehouse))
        return {(r["path"], r["size"], r["modified"]) for r in m.iter_rows(named=True)}

    def _record_files(self, e: Entity, res: RunResult) -> None:
        """After the table commit: the files this run read are loaded. If this fails, the next run
        reads them again, which a keyed load absorbs."""
        if not self._files_read:
            return
        where, files = self._files_read
        try:
            self.store.meta_append("loaded_files", [{"entity": e.name, "lakehouse": where, "run_id": self.run_id,
                                                     "path": f["path"], "size": f["size"], "modified": f["modified"]}
                                                    for f in files])
        except Exception as exc:  # the table is written; only the next run's file list is affected
            res.steps.append(Step("files", len(files), 0.0, f"couldn't record the files read ({type(exc).__name__}: "
                                                             f"{exc}); the next run reads them again"))

    def _expected_needed(self, e: Entity, columns: list[str]) -> tuple[set[str], set[str]]:
        """The source columns the contract expects, and those this entity uses."""
        own = set(e.columns) | set(e.derived) | set(e.hook_adds)
        src = e.spec.get("source", {})
        if "entity" in src:
            up = self.project.entities[src["entity"]]
            expected = set(up.columns) | set(up.derived) | set(up.hook_adds) | up.mode_columns()
        else:
            expected = own
        lookups = {c for lk in e.spec.get("lookup", []) for c in (lk.get("on"), lk.get("at")) if c}
        exprs = set()
        for d in e.spec.get("derive", []):
            exprs |= set(pl.sql_expr(d["expr"]).meta.root_names())
        bks = {c for k in keys_mod.entity_keys(e) for c in k["business_key"]}
        needed = own | lookups | exprs | bks | (set(columns) if e.spec.get("step") else set())
        return expected, needed

    def _source_columns(self, e: Entity, columns: list[str]) -> list[str]:
        """The columns of a source table worth reading: never system columns, and from another entity
        only what this one uses, plus any column its contract doesn't know (drift is still seen)."""
        columns = [c for c in columns if c not in SYSTEM_COLUMNS]
        src = e.spec.get("source", {})
        if "entity" not in src or src.get("rename"):
            return columns
        expected, needed = self._expected_needed(e, columns)
        return [c for c in columns if c in needed or c not in expected]

    def _contract_in(self, e: Entity, df: pl.DataFrame, res: RunResult) -> pl.DataFrame:
        t0 = time.perf_counter()
        policy = e.spec.get("contract", {}).get("drift") or self.project.layer(e.layer).drift
        self._evolved = []
        src = e.spec.get("source", {})
        expected, needed = self._expected_needed(e, df.columns)
        new = [c for c in df.columns if c not in expected]
        if "entity" in src:
            df = df.select([c for c in df.columns if c in needed or c in new])
        missing = [c for c in e.columns if c not in df.columns and c not in e.derived and c not in e.hook_adds
                   and c not in e.mode_columns()] if not e.spec.get("step") and src else []
        if missing:
            raise CheckStop(f"schema drift: the source no longer has {', '.join(missing)}")
        detail = "no drift"
        if new and policy == "strict":
            raise CheckStop(f"schema drift: new columns {', '.join(new)} (layer {e.layer} does not allow new columns)")
        if new and policy == "evolve":
            self._evolved = new
            detail = f"kept new columns {', '.join(new)}; add them to the contract to keep them for good"
            self._schema_changes.append({"entity": e.name, "run_id": self.run_id, "change": "added",
                                         "columns": ", ".join(new)})
        elif new:
            df = df.drop(new)
            detail = f"ignored new columns {', '.join(new)}"
        self._step(res, "contract", t0, df.height, detail)
        return df

    def _keys(self, e: Entity, df: pl.DataFrame, res: RunResult) -> pl.DataFrame:
        """Cell 1 of the template: MERGE new business keys into LH_Keys, then key every row."""
        if not keys_mod.entity_keys(e):
            return df
        t0 = time.perf_counter()
        df, details = keys_mod.assign(self.keys_store, self.project, e, df)
        self._step(res, "surrogate keys", t0, df.height, "; ".join(details))
        return df

    def _clean(self, e: Entity, df: pl.DataFrame, res: RunResult) -> pl.DataFrame:
        rules = e.spec.get("clean", [])
        if not rules:
            return df
        t0 = time.perf_counter()
        df, changed = apply_clean(df, rules, e.keys)
        self._step(res, "clean", t0, df.height, f"{plural(len(rules), 'rule')}, {plural(changed, 'value')} changed")
        return df

    def _call(self, e: Entity, ref: str, df: pl.DataFrame, ctx: HookContext, res: RunResult, label: str) -> pl.DataFrame:
        t0 = time.perf_counter()
        before = df.height
        out = load_python(ref)(df, ctx)
        if isinstance(out, pl.LazyFrame):
            out = out.collect()
        if not isinstance(out, pl.DataFrame):
            raise CheckStop(f"{label} {ref} must return a Polars DataFrame, not {type(out).__name__}")
        lost = [k for k in e.keys if k in df.columns and k not in out.columns]
        if lost:
            raise CheckStop(f"{label} {ref} dropped key columns {', '.join(lost)}")
        declared = set(df.columns) | set(e.columns) | set(e.hook_adds) | e.mode_columns() | set(self._evolved)
        if label == "custom step":
            declared |= {c for lk in e.spec.get("lookup", []) for c in (lk.get("on"), lk.get("at")) if c}
        extra = [c for c in out.columns if c not in declared]
        if extra:
            raise CheckStop(f"{label} {ref} added columns {', '.join(extra)} that are not in the contract")
        notes = f"; {'; '.join(ctx.notes)}" if ctx.notes else ""
        ctx.notes.clear()
        self._step(res, label, t0, out.height, f"{ref} ({out.height - before:+d} rows){notes}")
        return out

    def _lookup(self, df: pl.DataFrame, lk: dict[str, Any], res: RunResult) -> pl.DataFrame:
        t0 = time.perf_counter()
        dim_e = self.project.entities[lk["dim"]]
        scan = self.store.scan(self.lakehouse(dim_e), dim_e.schema, dim_e.table)
        if scan is None:
            raise CheckStop(f"{dim_e.name} has not been loaded in {self.store.env} yet")
        sk, on, at = lk["as"], lk["on"], lk.get("at")
        dim_sk = dim_e.surrogate_key
        history = "valid_from" in scan.collect_schema().names()
        if not history:
            at = None  # a Type 1 dimension has one row per key, so the fact date doesn't matter
        # Only the key columns of the dimension are read, however wide it is.
        dim = scan.select([on, dim_sk] + (["valid_from", "valid_to"] if history else [])).collect()
        d = dim.filter(pl.col(dim_sk) != -1).select([on, pl.col(dim_sk).alias("__sk")]
                                                   + (["valid_from", "valid_to"] if at else []))
        df = df.with_row_index("__r")
        j = df.select(["__r", on] + ([at] if at else [])).join(d, on=on, how="inner")
        if at:
            day = pl.col(at).cast(pl.Date) if df.schema[at] != pl.Date else pl.col(at)
            j = j.filter((day >= pl.col("valid_from")) & (day < pl.col("valid_to")))
        j = j.select("__r", "__sk").unique("__r", keep="first")
        out = df.join(j, on="__r", how="left").with_columns(pl.col("__sk").fill_null(-1).alias(sk))
        out = out.drop("__r", "__sk")
        unknown = int((out[sk] == -1).sum())
        self._step(res, f"lookup {dim_e.name}", t0, out.height, f"{plural(unknown, 'row')} to the Unknown member (-1)")
        return out

    def _transform(self, e: Entity, res: RunResult, backfill: str | None) -> pl.DataFrame:
        self._before_read(e, backfill)
        df = self._read(e, res, backfill == "full")
        return self._steps(e, df, res)

    def _before_read(self, e: Entity, backfill: str | None) -> None:
        """Decisions a run needs before it reads anything."""
        if e.mode == "scd2" and backfill != "migrate":
            existing = self.store.scan(self.lakehouse(e), e.schema, e.table)
            if existing is not None and "valid_from" not in existing.collect_schema().names():
                raise DecisionNeeded(f"{e.name} is a Type 1 table today. Migrating it to Type 2 keeps every "
                                     "surrogate key and starts each row's history at 1900-01-01.", ["migrate"])
        last = self._last_ok(e)
        changed = last is not None and last["logic"] != self.project.fingerprint(e) and backfill != "migrate"
        if changed and e.mode in ("incremental", "scd1", "scd2") and backfill is None:
            raise DecisionNeeded(
                f"{e.name}'s logic changed since its last run. Rows already loaded were built with the old logic.",
                ["none", "full"])

    def _steps(self, e: Entity, df: pl.DataFrame, res: RunResult) -> pl.DataFrame:
        """Every step from the input contract to the output contract, on rows already read."""
        df = self._contract_in(e, df, res)
        df = self._keys(e, df, res)
        df = self._clean(e, df, res)
        s = e.spec
        if s.get("derive"):
            t0 = time.perf_counter()
            for d in s["derive"]:
                df = df.with_columns(pl.sql_expr(d["expr"]).alias(d["column"]))
            self._step(res, "derive", t0, df.height, ", ".join(e.derived))
        ctx = HookContext(self, e)
        if "after_clean" in s.get("hooks", {}):
            df = self._call(e, s["hooks"]["after_clean"], df, ctx, res, "hook after_clean")
        if "custom" in s.get("step", {}):
            df = self._call(e, s["step"]["custom"], df, ctx, res, "custom step")
        for lk in s.get("lookup", []):
            df = self._lookup(df, lk, res)
        if "before_write" in s.get("hooks", {}):
            df = self._call(e, s["hooks"]["before_write"], df, ctx, res, "hook before_write")
        return self._contract_out(e, df, res)

    def _replayed(self, e: Entity, res: RunResult) -> pl.DataFrame:
        """Quarantined rows back to contract types, with derived columns worked out again from any fixes."""
        t0 = time.perf_counter()
        df = self._replay_rows.drop([c for c in QUARANTINE_COLUMNS if c in self._replay_rows.columns])
        res.rows_read = df.height
        self._step(res, "read", t0, df.height, f"{plural(df.height, 'quarantined row')} to replay")
        self._evolved = []
        df = self._contract_out(e, df, res)
        redone = []
        t0 = time.perf_counter()
        for d in e.spec.get("derive", []):
            expr = pl.sql_expr(d["expr"])
            if all(c in df.columns for c in expr.meta.root_names()):
                df = df.with_columns(expr.alias(d["column"]))
                redone.append(d["column"])
        if redone:
            self._step(res, "derive", t0, df.height, ", ".join(redone))
            df = self._contract_out(e, df, res)
        return df

    def _contract_out(self, e: Entity, df: pl.DataFrame, res: RunResult) -> pl.DataFrame:
        """Shape and types exactly as the contract says; rows that can't be cast go to quarantine."""
        want = [c for c in e.columns if c not in e.mode_columns() or c in df.columns]
        missing = [c for c in want if c not in df.columns]
        if missing:
            raise CheckStop(f"output is missing contract columns {', '.join(missing)}")
        df = df.select(want + self._evolved)
        casts, failed = [], pl.lit(False)
        for c in want:
            dtype = polars_type(e.columns[c])
            expr = _cast(df, c, dtype)
            casts.append(expr.alias(c))
            failed = failed | (pl.col(c).is_not_null() & expr.is_null())
        flagged = df.with_columns(failed.alias("__bad"))
        n_bad = int(flagged["__bad"].sum())
        df = flagged.with_columns(casts)
        if n_bad:
            self._dq.append({"run_id": self.run_id, "entity": e.name, "check": "contract types",
                             "failed": n_bad, "action": "quarantine"})
            self._quarantine(e, flagged.filter(pl.col("__bad")).drop("__bad"), "contract types")
            res.quarantined += n_bad
        return df.filter(~pl.col("__bad")).drop("__bad")

    # ---- gate 2: on write

    def _checks(self, e: Entity, df: pl.DataFrame, res: RunResult) -> tuple[pl.DataFrame, int]:
        t0 = time.perf_counter()
        bad_total = _fill(False, df.height)
        reasons = pl.Series([""] * df.height, dtype=pl.String)
        ran = 0
        for c in e.spec.get("check", []):
            col = c.get("column")
            name = c.get("name") or col or "check"
            if c.get("not_null") or c.get("unique"):
                bad = _fill(False, df.height)
                if c.get("not_null"):
                    bad = bad | df[col].is_null()
                if c.get("unique"):
                    bad = bad | (df[col].is_duplicated() & df[col].is_not_null())
                    if self._seen is not None:  # a batched load: values earlier batches wrote count too
                        seen = self._seen.get(col)
                        if seen is not None:
                            bad = bad | (df[col].is_in(seen.implode()) & df[col].is_not_null())
                        self._seen[col] = pl.concat([seen, df[col].drop_nulls()]) if seen is not None \
                            else df[col].drop_nulls()
            elif "exists_in" in c:
                tgt, ref_col = c["exists_in"].rsplit(".", 1)
                ref_e = self.project.entities[tgt]
                ref = self.store.scan(self.lakehouse(ref_e), ref_e.schema, ref_e.table)
                ref = ref.select(ref_col).collect() if ref is not None else None
                bad = (~df[col].is_in(ref[ref_col].implode()) & df[col].is_not_null()) if ref is not None \
                    else _fill(True, df.height)
            elif "accepted_values" in c:
                bad = ~df[col].is_in(pl.Series(c["accepted_values"]).implode()) & df[col].is_not_null()
            elif "range" in c:
                lo, hi = c["range"].get("min"), c["range"].get("max")
                bad = _fill(False, df.height)
                if lo is not None:
                    bad = bad | (df[col] < lo).fill_null(False)
                if hi is not None:
                    bad = bad | (df[col] > hi).fill_null(False)
            elif "expr" in c:
                bad = ~df.select(pl.sql_expr(c["expr"]).fill_null(False)).to_series()
            elif "python" in c:
                out = load_python(c["python"])(df, HookContext(self, e))
                bad = ~(out if isinstance(out, pl.Series) else df.select(out).to_series()).fill_null(False)
            else:
                continue
            ran += 1
            n = int(bad.sum())
            on_fail = c.get("on_fail", "stop")
            self._dq.append({"run_id": self.run_id, "entity": e.name, "check": name, "failed": n, "action": on_fail})
            if n and on_fail == "stop":
                raise CheckStop(f"check {name} failed on {n} row{'s' if n != 1 else ''}, so nothing was written")
            if on_fail == "quarantine":
                reasons = pl.select(pl.when(bad & (reasons == "")).then(pl.lit(name)).otherwise(reasons)).to_series()
                bad_total = bad_total | bad
        n_bad = int(bad_total.sum())
        if n_bad:
            self._quarantine(e, df.filter(bad_total).with_columns(reasons.filter(bad_total).alias("_check")), None)
        res.quarantined += n_bad
        self._step(res, "checks", t0, df.height - n_bad, f"{plural(ran, 'check')}, {plural(n_bad, 'row')} quarantined")
        return df.filter(~bad_total), n_bad

    def _quarantine(self, e: Entity, rows: pl.DataFrame, check: str | None) -> None:
        if check is not None:
            rows = rows.with_columns(pl.lit(check).alias("_check"))
        rows = rows.select([pl.col(c).cast(pl.String) for c in rows.columns]).with_columns(
            pl.lit(self.run_id).alias("_run_id"), pl.lit(self.load_date).alias("_loaded_at"),
            pl.Series("_qid", [uuid.uuid4().hex[:12] for _ in range(rows.height)], dtype=pl.String))
        if self._held is not None:
            self._held.append(rows)
            return
        self.store.write(self.lakehouse(e), "quarantine", f"{e.schema}_{e.table}", rows, mode="append")

    # ---- write

    def _write(self, e: Entity, df: pl.DataFrame, res: RunResult, backfill: str | None) -> None:
        from deltalake.exceptions import CommitFailedError

        for attempt in range(1, WRITE_ATTEMPTS + 1):
            try:
                return self._write_once(e, df, res, backfill)
            except CommitFailedError:
                # Another writer committed first: merge again against the table as it is now.
                if attempt == WRITE_ATTEMPTS:
                    raise
                time.sleep(0.5 * attempt)

    def _merge_plan(self, e: Entity, df: pl.DataFrame, backfill: str | None) -> tuple[str, str]:
        """How the rows go in without rewriting the table: "upsert" (MERGE on the keys), "append", or
        "" with the reason the table has to be read and rewritten instead."""
        if not self.merge_writes:
            return "", ""
        if e.mode in ("scd1", "scd2"):
            return "", ""  # dimensions: see _write_dimension
        if not (backfill == "replay" or (e.mode == "incremental" and backfill != "full")):
            return "", ""  # a full load or a rebuild replaces the table anyway
        target = self.store.scan(self.lakehouse(e), e.schema, e.table)
        if target is None:
            return "", ""
        changed = _type_changes(df, target.collect_schema())
        if changed:
            return "", changed
        if not e.keys:
            return "append", ""
        if df.select(e.keys).is_duplicated().any():
            return "", f"rewrote the table because new rows repeat a key ({', '.join(e.keys)})"
        return "upsert", ""

    def _write_merged(self, e: Entity, df: pl.DataFrame, how: str, backfill: str | None,
                      metadata: dict[str, str]) -> tuple[int, str]:
        lh = self.lakehouse(e)
        have = self.store.scan(lh, e.schema, e.table).collect_schema()
        # A column the table has and the rows don't is null in them, as when the table was rewritten.
        df = df.with_columns([pl.lit(None, dtype=t).alias(c) for c, t in have.items() if c not in df.columns])
        what = "replayed" if backfill == "replay" else ("merged" if how == "upsert" else "appended")
        if how == "append":
            version = self.store.write(lh, e.schema, e.table, df, mode="append", metadata=metadata)
            return version, f"{what} {plural(df.height, 'row')} (Delta append, no files rewritten)"
        version, m = self.store.upsert(lh, e.schema, e.table, df, e.keys, metadata)
        removed, added = m.get("num_target_files_removed", 0), m.get("num_target_files_added", 0)
        return version, (f"{what} {plural(df.height, 'row')} on {', '.join(e.keys)}: "
                         f"{m.get('num_target_rows_updated', 0)} updated, {m.get('num_target_rows_inserted', 0)} new "
                         f"(Delta MERGE rewrote {plural(removed, 'file')}, added {added})")

    def _write_dimension(self, e: Entity, df: pl.DataFrame,
                         metadata: dict[str, str]) -> tuple[int, str, int] | str:
        """A Type 1 or Type 2 dimension without reading all of it.

        Only the versions of the business keys that arrived (and the Unknown member) are read,
        merged by the same scd1_merge/scd2_merge a full rewrite uses, checked, and written back with
        one MERGE on the surrogate key. A Type 2 key whose row would change nothing (same tracked
        values, no new Type 1 value) is left out first, so a full snapshot of a large dimension
        touches only what changed. Returns (version, detail, rows in the table), or the reason the
        whole table is read and rewritten instead ("" for no reason worth showing)."""
        lh, sk, bk = self.lakehouse(e), e.surrogate_key, e.keys
        # Everything below is worked out from one version, and the MERGE commits against it: a
        # writer that adds keys in between makes the commit fail, and _write runs it again.
        read_version = self.store.version(lh, e.schema, e.table)
        lf = self.store.scan(lh, e.schema, e.table, read_version)
        if lf is None or not sk or not bk:
            return ""
        have = lf.collect_schema()
        if sk not in have or any(c not in have for c in bk) or (e.mode == "scd2" and "is_current" not in have):
            return ""
        changed = _type_changes(df, have)
        if changed:
            return changed
        if df.select(bk).is_duplicated().any():
            return f"rewrote the table because new rows repeat a key ({', '.join(bk)})"
        arrived = df.height
        near = lf  # range filters skip files by their statistics; semi joins keep exactly the keys asked for
        keys = df.select(bk).unique().drop_nulls()
        for c in bk:
            if keys.height and (keys[c].dtype.is_integer() or keys[c].dtype == pl.String):
                near = near.filter(pl.col(c).is_between(keys[c].min(), keys[c].max()))
        unchanged = 0
        total = self.store.count(lh, e.schema, e.table)
        if e.mode == "scd2":
            df = _scd2_effective(near, df, e)
            unchanged = arrived - df.height
            if not df.height:  # nothing would change: no commit at all
                return (self.store.version(lh, e.schema, e.table),
                        f"{plural(unchanged, 'row')} unchanged; nothing to write", total)
            keys = df.select(bk).unique().drop_nulls()
        subset = pl.concat([near.join(keys.lazy(), on=bk, how="semi").collect(),
                            lf.filter(pl.col(sk) == -1).collect()], how="diagonal_relaxed")
        if total and subset.height * 2 > total:
            return (f"rewrote the table because most of it changed ({plural(subset.height, 'row')} of {total} "
                    "belong to the keys that arrived)")
        top = lf.select(pl.col(sk).max()).collect().item()
        next_key = max(int(top or 0), 0) + 1
        if e.mode == "scd1":
            out, info = scd1_merge(subset, df, e, next_key)
        else:
            out, info = scd2_merge(subset, df, e, self.load_date, next_key)
            problems = scd2_invariants(out, sk, bk)
            if problems:
                raise CheckStop("Type 2 checks failed: " + "; ".join(problems))
            if unchanged:
                info += f", {plural(unchanged, 'row')} unchanged"
        # The Unknown member never changes here, so it stays out of the MERGE.
        out = out.filter(pl.col(sk) != -1)
        version, m = self.store.upsert(lh, e.schema, e.table, out, [sk], metadata, prune=bk,
                                       read_version=read_version, new_from=(sk, next_key))
        removed, added = m.get("num_target_files_removed", 0), m.get("num_target_files_added", 0)
        detail = (f"{info}; read {plural(subset.height, 'row')} of the table for "
                  f"{plural(keys.height, 'business key')} (Delta MERGE rewrote {plural(removed, 'file')}, added {added})")
        return version, detail, self.store.count(lh, e.schema, e.table)

    def _write_once(self, e: Entity, df: pl.DataFrame, res: RunResult, backfill: str | None) -> None:
        t0 = time.perf_counter()
        lh = self.lakehouse(e)
        if df.height == 0 and (e.mode in ("incremental", "scd1", "scd2") or backfill == "replay") \
                and self.store.exists(lh, e.schema, e.table):
            self._step(res, "write", t0, self.store.count(lh, e.schema, e.table), "nothing new to write")
            return
        df = df.with_columns(pl.lit(self.run_id).alias("_run_id"), pl.lit(self.load_date).alias("_loaded_at"))
        metadata = {"fabric_desktop_run_id": self.run_id, "fabric_desktop_metadata": self.project.version,
                    "fabric_desktop_logic": self.project.fingerprint(e)}
        how, why = self._merge_plan(e, df, backfill)
        if how:
            version, info = self._write_merged(e, df, how, backfill, metadata)
            self._advance_watermark(e, df, backfill)
            res.rows_written, res.delta_version = df.height, version
            self._step(res, "write", t0, self.store.count(lh, e.schema, e.table), f"{info}; Delta version {version}")
            return
        if e.mode in ("scd1", "scd2") and self.merge_writes:
            done = self._write_dimension(e, df, metadata)
            if isinstance(done, tuple):
                version, info, rows = done
                self._advance_watermark(e, df, backfill)
                res.rows_written, res.delta_version = df.height, version
                self._step(res, "write", t0, rows, f"{info}; Delta version {version}")
                return
            why = done
        # The version read is the one the commit is made against: if another writer (the app, an
        # agent, a run in Fabric) commits in between, the commit fails and _write merges again.
        existing, read_version = self.store.read_at(lh, e.schema, e.table)
        if e.mode == "scd1":
            out, info = scd1_merge(existing, df, e)
        elif e.mode == "scd2":
            out, info = scd2_merge(existing, df, e, self.load_date)
            problems = scd2_invariants(out, e.surrogate_key, e.keys)
            if problems:
                raise CheckStop("Type 2 checks failed: " + "; ".join(problems))
        elif backfill == "replay" and existing is not None and e.mode not in ("scd1", "scd2"):
            # Replayed rows join the table: they replace rows with the same key, or are added.
            keep = existing.join(df.select(e.keys), on=e.keys, how="anti") if e.keys else existing
            out = pl.concat([keep, df], how="diagonal_relaxed")
            info = f"replayed {plural(df.height, 'row')}"
        elif e.mode == "incremental" and existing is not None and backfill != "full":
            keep = existing.join(df.select(e.keys), on=e.keys, how="anti") if e.keys else existing
            out = pl.concat([keep, df], how="diagonal_relaxed")
            info = f"merged {df.height} rows on {', '.join(e.keys)}" if e.keys else f"appended {df.height} rows"
        elif e.mode == "incremental" and existing is not None:
            keep = existing.join(df.select(e.keys), on=e.keys, how="anti") if e.keys else existing.clear()
            out = pl.concat([keep, df], how="diagonal_relaxed")
            info = f"rebuilt: {df.height} rows from the full source"
        else:
            out, info = df, f"replaced with {plural(df.height, 'row')}"
        if why:
            info = f"{info}; {why}"
        version = self.store.write(lh, e.schema, e.table, out, mode="overwrite", metadata=metadata,
                                   read_version=read_version)
        self._advance_watermark(e, df, backfill)
        res.rows_written, res.delta_version = df.height, version
        self._step(res, "write", t0, out.height, f"{info}; Delta version {version}")

    def _advance_watermark(self, e: Entity, df: pl.DataFrame, backfill: str | None) -> None:
        wm_col = e.spec.get("load", {}).get("watermark")
        # Replayed rows are old rows: they never move the watermark.
        if wm_col and backfill != "replay" and wm_col in df.columns and df.height and df[wm_col].max() is not None:
            self.store.meta_append("watermarks", [{"entity": e.name, "value": _wm_text(df[wm_col].max()),
                                                   "run_id": self.run_id}])

    def _migrate(self, e: Entity, res: RunResult) -> None:
        t0 = time.perf_counter()
        df = self.read_entity(e.name)
        if df is None or "valid_from" in df.columns:
            self._step(res, "migrate", t0, 0, "nothing to migrate")
            return
        df = df.with_columns(pl.lit(FAR_PAST).alias("valid_from"), pl.lit(FAR_FUTURE).alias("valid_to"),
                             pl.lit(True).alias("is_current"))
        version = self.store.write(self.lakehouse(e), e.schema, e.table, df, metadata={
            "fabric_desktop_run_id": self.run_id, "fabric_desktop_migration": "type1_to_type2"})
        self._step(res, "migrate", t0, df.height, f"Type 1 to Type 2, every key kept; Delta version {version}")


# --------------------------------------------------------------------------- dimensions


def apply_clean(df: pl.DataFrame, rules: list[dict[str, Any]], keys: list[str]) -> tuple[pl.DataFrame, int]:
    """Apply ``[[clean]]`` rules in order; returns the cleaned rows and how many values changed."""
    changed = 0
    for r in rules:
        col = r.get("column")
        if "dedupe_keep" in r:
            which, _, order = r["dedupe_keep"].partition(":")
            before = df.height
            df = df.sort(order, nulls_last=True).unique(subset=keys, keep="last" if which == "newest" else "first",
                                                        maintain_order=True)
            changed += before - df.height
            continue
        if col not in df.columns:
            continue
        expr = pl.col(col)
        if df.schema[col] == pl.String:
            if r.get("trim"):
                expr = expr.str.strip_chars()
            case = r.get("case")
            if case == "lower":
                expr = expr.str.to_lowercase()
            elif case == "upper":
                expr = expr.str.to_uppercase()
            elif case == "title":
                expr = expr.str.to_titlecase()
            for old, new_ in r.get("replace", {}).items():
                expr = pl.when(expr == old).then(pl.lit(new_)).otherwise(expr)
            if r.get("null_if"):
                expr = pl.when(expr.is_in(r["null_if"])).then(None).otherwise(expr)
            if r.get("parse_date"):
                fmts = [f.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d") for f in r["parse_date"]]
                expr = pl.coalesce([expr.str.to_date(f, strict=False) for f in fmts])
        if "fill_null" in r:
            expr = expr.fill_null(r["fill_null"])
        if "round" in r:
            expr = expr.round(int(r["round"]))
        after = df.with_columns(expr.alias("__new"))
        changed += int((~after[col].cast(pl.String).eq_missing(after["__new"].cast(pl.String))).sum())
        df = after.with_columns(pl.col("__new").alias(col)).drop("__new")
    return df, changed


def _next_key(existing: pl.DataFrame | None, sk: str) -> int:
    if existing is None or existing.height == 0:
        return 1
    return max(int(existing[sk].max()), 0) + 1


def unknown_member(schema: pl.Schema, e: Entity) -> pl.DataFrame:
    row: dict[str, Any] = {}
    for c, t in schema.items():
        row[c] = "Unknown" if t == pl.String and c not in e.keys and not c.startswith("_") else None
    row[e.surrogate_key] = -1
    if "valid_from" in schema:
        row |= {"valid_from": FAR_PAST, "valid_to": FAR_FUTURE, "is_current": True}
    return pl.DataFrame([row], schema=schema)


def _ordered(df: pl.DataFrame, e: Entity) -> pl.DataFrame:
    sk = e.surrogate_key
    return df.select([sk] + [c for c in df.columns if c != sk])


def scd1_merge(existing: pl.DataFrame | None, incoming: pl.DataFrame, e: Entity,
               next_key: int | None = None) -> tuple[pl.DataFrame, str]:
    """``existing`` may be only the rows of the incoming business keys (and the Unknown member), with
    ``next_key`` the table's highest surrogate key plus one: the result is then those rows merged."""
    sk, bk = e.surrogate_key, e.keys
    if existing is not None and "valid_from" in existing.columns:
        raise CheckStop(f"{e.name} is a Type 2 table; turning it back into Type 1 would lose history")
    if sk in incoming.columns:
        # The rows already carry their key from LH_Keys, so the dimension uses it instead of issuing one.
        incoming = _ordered(incoming.with_columns(pl.col(sk).cast(pl.Int64)), e)
        if existing is None:
            return pl.concat([unknown_member(incoming.schema, e), incoming.filter(pl.col(sk) != -1)]).sort(sk), \
                f"{incoming.height} new, keys from LH_Keys"
        matched = incoming.join(existing.select(bk), on=bk, how="semi").height
        untouched = existing.join(incoming.select(bk), on=bk, how="anti")
        out = pl.concat([untouched, incoming.filter(pl.col(sk) != -1)], how="diagonal_relaxed")
        return out.sort(sk), f"{incoming.height - matched} new, {matched} updated, keys from LH_Keys"
    if existing is None:
        rows = _ordered(incoming.with_columns(pl.int_range(1, incoming.height + 1, dtype=pl.Int64).alias(sk)), e)
        return pl.concat([unknown_member(rows.schema, e), rows]), f"{incoming.height} new"
    keys = existing.select(bk + [sk])
    matched = incoming.join(keys, on=bk, how="inner")
    new = incoming.join(keys, on=bk, how="anti")
    start = next_key or _next_key(existing, sk)
    new = new.with_columns(pl.int_range(start, start + new.height, dtype=pl.Int64).alias(sk))
    untouched = existing.join(incoming.select(bk), on=bk, how="anti")
    out = pl.concat([untouched, _ordered(matched, e), _ordered(new, e)], how="diagonal_relaxed")
    return out.sort(sk), f"{new.height} new, {matched.height} updated"


def scd2_merge(existing: pl.DataFrame | None, incoming: pl.DataFrame, e: Entity,
               load_date: dt.date, next_key: int | None = None) -> tuple[pl.DataFrame, str]:
    """As scd1_merge: ``existing`` may be only the incoming keys' versions, with ``next_key``."""
    scd = e.spec["scd"]
    sk, bk, tracked = e.surrogate_key, e.keys, list(scd["tracked"])
    # Each version gets its own key, so a key that came with the rows (from LH_Keys) is replaced.
    incoming = incoming.drop(sk, strict=False)
    type1 = [c for c in scd.get("type1", []) if c in incoming.columns]
    first_from = load_date if scd.get("first_version_from") == "load_date" else FAR_PAST
    if existing is not None and "valid_from" not in existing.columns:
        raise DecisionNeeded(f"{e.name} exists as a Type 1 table. Migrating keeps every surrogate key and starts "
                             "each row's history at 1900-01-01.", ["migrate"])

    def versioned(df: pl.DataFrame, start: int, valid_from: Any) -> pl.DataFrame:
        df = df.with_columns(pl.int_range(start, start + df.height, dtype=pl.Int64).alias(sk),
                             (valid_from if isinstance(valid_from, pl.Expr) else pl.lit(valid_from)).alias("valid_from"),
                             pl.lit(FAR_FUTURE).alias("valid_to"), pl.lit(True).alias("is_current"))
        return _ordered(df, e)

    if existing is None:
        rows = versioned(incoming, 1, first_from)
        return pl.concat([unknown_member(rows.schema, e), rows]), f"{incoming.height} new"

    unknown = existing.filter(pl.col(sk) == -1)
    real = existing.filter(pl.col(sk) != -1)
    current = real.filter(pl.col("is_current")).select(bk + tracked + [sk])
    joined = incoming.join(current, on=bk, how="left", suffix="__cur")
    is_new = pl.col(sk).is_null()
    same = pl.all_horizontal([pl.col(c).eq_missing(pl.col(f"{c}__cur")) for c in tracked])
    drop = [sk] + [f"{c}__cur" for c in tracked]
    new = joined.filter(is_new).drop(drop)
    changed = joined.filter(~is_new & ~same).drop(drop)

    closing = changed.select(bk).with_columns(pl.lit(True).alias("__close"))
    real = real.join(closing, on=bk, how="left").with_columns(
        pl.when(pl.col("__close") & pl.col("is_current")).then(pl.lit(load_date)).otherwise(pl.col("valid_to")).alias("valid_to"),
        pl.when(pl.col("__close") & pl.col("is_current")).then(False).otherwise(pl.col("is_current")).alias("is_current"),
    ).drop("__close")
    t1 = 0
    if type1:
        before = real.select(bk + type1)
        real = real.update(incoming.select(bk + type1), on=bk, how="left")
        t1 = int((~pl.concat([before, real.select(type1).rename({c: f"{c}__n" for c in type1})], how="horizontal")
                  .select(pl.all_horizontal([pl.col(c).eq_missing(pl.col(f"{c}__n")) for c in type1])).to_series()).sum())
    start = next_key or _next_key(existing, sk)
    inserts = pl.concat([versioned(changed, start, load_date), versioned(new, start + changed.height, first_from)],
                        how="diagonal_relaxed")
    out = pl.concat([unknown, real, inserts], how="diagonal_relaxed")
    return out.sort(sk), f"{new.height} new, {changed.height} changed (new versions), {t1} Type 1 updates"


def scd2_invariants(df: pl.DataFrame, sk: str, bk: list[str]) -> list[str]:
    """The Validate section of the Type 2 template, run before every write."""
    errs = []
    real = df.filter(pl.col(sk) != -1)
    cur = real.filter(pl.col("is_current")).group_by(bk).len().filter(pl.col("len") != 1)
    if real.height and cur.height:
        errs.append(f"{cur.height} keys without exactly one current row")
    if df[sk].is_duplicated().any():
        errs.append("surrogate key is not unique")
    if df.filter(pl.col(sk) == -1).height != 1:
        errs.append("the Unknown member (-1) is missing")
    s = real.sort(bk + ["valid_from"])
    gaps = s.with_columns(pl.col("valid_from").shift(-1).over(bk).alias("__next")).filter(
        pl.col("__next").is_not_null() & (pl.col("__next") != pl.col("valid_to")))
    if gaps.height:
        errs.append(f"{gaps.height} gaps or overlaps in valid_from / valid_to")
    return errs
