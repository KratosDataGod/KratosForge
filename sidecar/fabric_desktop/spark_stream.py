"""Structured streaming on this PC: ``spark.readStream`` and ``df.writeStream`` (Preview).

There is no Spark cluster here to keep a stream running, so a stream on this PC is the
``trigger(availableNow=True)`` kind Fabric and Databricks notebooks use for incremental loads:
each run picks up what arrived since the last run, writes it, and stops. The checkpoint
remembers what was processed, so running the cell again only takes new data:

- a Delta table source (``readStream.table("dbo.bronze")``, or ``format("delta").load(...)``)
  takes the data files added since the version it last read; a commit that removed or rewrote
  files stops the stream unless ``skipChangeCommits`` (or ``ignoreChanges``) is set, as in Spark;
- a folder of files (``format("csv"|"json"|"parquet").load("Files/landing/")``) takes the files
  it hasn't seen.

Sinks: a lakehouse table (``toTable``, or ``format("delta").start("Tables/...")``, appended, or
replaced for ``outputMode("complete")``), ``foreachBatch(fn)``, ``format("console")`` and
``format("memory")`` (a temp view named by ``queryName``). The checkpoint moves on only after the
sink succeeds, so a failed write is retried by the next run.

Checkpoints for lakehouse paths (``Files/checkpoints/...``) are kept on this PC under the app's
work folder, apart from the Fabric ones at the same path: a stream run here and in Fabric each
keep their own place. Continuous triggers (``trigger(continuous=...)``) run in Fabric; any other
trigger runs as availableNow here and says so.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.dataset as pads
from duckdb.experimental.spark.sql.dataframe import DataFrame

from . import jsonfile, lakehouse

_FILE_FORMATS = ("csv", "json", "parquet", "text")
_SUFFIXES = {"csv": (".csv",), "json": (".json", ".jsonl", ".ndjson"), "parquet": (".parquet",), "text": (".txt",)}


def _view_name() -> str:
    return "__stream_" + uuid.uuid4().hex[:12]


class _Source:
    """Where a stream reads from, and what reading the next batch means."""

    def __init__(self, session: Any, kind: str, fmt: str, target: str, options: dict[str, Any], reader: Any):
        self.session, self.kind, self.fmt, self.target, self.options = session, kind, fmt, target, options
        self.reader = reader  # a LakehouseReader with the same format, options and schema, for files
        self.view = _view_name()
        self.arrow_schema: pa.Schema | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.fmt}:{self.target}"

    # ---- Delta tables

    def _delta(self) -> Any:
        if self.kind == "table":
            return lakehouse.open_table(self.target)
        from deltalake import DeltaTable
        return DeltaTable(self.target)

    def _delta_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        dt = self._delta()
        now = dt.version()
        last = state.get("version")
        if last is not None and last >= now:
            return None, state, f"no new commits after version {last}"
        if last is None:
            new = _files(dt)
        else:
            before = _files(self._at(last))
            current = _files(dt)
            removed = before - current
            if removed and not (_flag(self.options.get("skipchangecommits")) or _flag(self.options.get("ignorechanges"))):
                raise ValueError(
                    f"{self.target} had rows updated or deleted after version {last}, and a stream only reads "
                    "appended rows. Add .option(\"skipChangeCommits\", \"true\") to skip those commits, as in "
                    "Fabric, or start over with a new checkpointLocation.")
            new = current - before
        dataset = dt.to_pyarrow_dataset()
        fragments = [f for f in dataset.get_fragments() if any(f.path.endswith(n) for n in new)]
        data = pads.FileSystemDataset(fragments, dataset.schema, dataset.format, dataset.filesystem).to_table() \
            if fragments else dataset.schema.empty_table()
        what = f"versions {('0' if last is None else last + 1)}..{now} ({len(fragments)} files)"
        return data, {**state, "version": now}, what

    def _at(self, version: int) -> Any:
        dt = self._delta()
        dt.load_as_version(version)
        return dt

    # ---- folders of files

    def _folder(self) -> Path:
        return lakehouse.local_path(self.target)

    def _file_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        folder = self._folder()
        seen = set(state.get("files", []))
        if folder.is_file():
            found = [folder]
        else:
            suffixes = _SUFFIXES[self.fmt]
            found = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
        new = [p for p in found if _file_key(p, folder) not in seen]
        limit = _int(self.options.get("maxfilespertrigger"))
        if not new:
            return None, state, "no new files"
        tables = [_arrow(getattr(self.reader, self.fmt)(str(p))) for p in new]
        data = pa.concat_tables(tables, promote_options="default") if len(tables) > 1 else tables[0]
        files = sorted(seen | {_file_key(p, folder) for p in new})
        note = f"{len(new)} new file{'' if len(new) == 1 else 's'}" + (" (maxFilesPerTrigger is ignored: availableNow takes them all)" if limit else "")
        return data, {**state, "files": files}, note

    def next_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        return self._delta_batch(state) if self.fmt == "delta" else self._file_batch(state)

    def schema(self) -> pa.Schema:
        if self.fmt == "delta":
            return self._delta().to_pyarrow_dataset().schema
        folder = self._folder()
        files = [folder] if folder.is_file() else sorted(p for p in folder.rglob("*") if p.is_file()
                                                          and p.suffix.lower() in _SUFFIXES[self.fmt])
        if not files:
            raise ValueError(f"{self.target} has no {self.fmt} files yet, so the stream's columns aren't known. "
                             "Give readStream a schema, or add a file first.")
        return _arrow(getattr(self.reader, self.fmt)(str(files[0]))).schema


def _files(dt: Any) -> set[str]:
    """The data files in a Delta table's current version, as paths relative to the table."""
    return set(pa.table(dt.get_add_actions(flatten=True)).column("path").to_pylist())


def _arrow(df: DataFrame) -> pa.Table:
    rel = df.relation
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _file_key(path: Path, folder: Path) -> str:
    st = path.stat()
    rel = path.name if folder.is_file() else path.relative_to(folder).as_posix()
    return f"{rel}|{st.st_size}|{st.st_mtime_ns}"


class StreamReader:
    """``spark.readStream``."""

    def __init__(self, session: Any):
        from .spark_local import LakehouseReader
        self._session = session
        self._reader = LakehouseReader(session)
        self._format: str | None = None
        self._options: dict[str, Any] = {}

    def format(self, fmt: str) -> StreamReader:
        self._format = fmt.lower()
        self._reader.format(fmt)
        return self

    def option(self, key: str, value: Any) -> StreamReader:
        self._options[str(key).lower()] = value
        if str(key).lower() not in ("maxfilespertrigger", "skipchangecommits", "ignorechanges", "ignoredeletes",
                                    "maxbytespertrigger", "startingversion", "startingtimestamp"):
            self._reader.option(key, value)
        return self

    def options(self, **opts: Any) -> StreamReader:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def schema(self, schema: Any) -> StreamReader:
        self._reader.schema(schema)
        return self

    def _start(self, kind: str, fmt: str, target: str) -> DataFrame:
        for key in ("startingversion", "startingtimestamp"):
            if key in self._options:
                raise NotImplementedError(f"{key} runs in Fabric. On this PC a stream starts from the table's "
                                          "first version, or where its checkpoint left off.")
        src = _Source(self._session, kind, fmt, target, dict(self._options), self._reader)
        conn = self._session.conn
        src.arrow_schema = src.schema()
        conn.register(src.view, src.arrow_schema.empty_table())
        df = DataFrame(conn.table(src.view), self._session)
        _SOURCES[src.view] = src
        return df

    def table(self, name: str) -> DataFrame:
        return self._start("table", "delta", name)

    def load(self, path: str | None = None, format: str | None = None, **opts: Any) -> DataFrame:  # noqa: A002
        self.options(**opts)
        fmt = (format or self._format or "parquet").lower()
        if not path:
            raise ValueError("readStream.load needs a path.")
        rel = lakehouse.relative_path(path)
        if fmt == "delta":
            if rel and rel.startswith("Tables/"):
                return self.table(".".join(p for p in rel.split("/")[1:] if p))
            return self._start("path", "delta", path)
        if fmt not in _FILE_FORMATS:
            raise ValueError(f"Streaming from {fmt!r} runs in Fabric. On this PC, streams read Delta tables and "
                             "csv, json, parquet or text files.")
        return self._start("files", fmt, path)

    def csv(self, path: str, **opts: Any) -> DataFrame:
        return self.load(path, format="csv", **opts)

    def json(self, path: str, **opts: Any) -> DataFrame:
        return self.load(path, format="json", **opts)

    def parquet(self, path: str, **opts: Any) -> DataFrame:
        return self.load(path, format="parquet", **opts)

    def text(self, path: str, **opts: Any) -> DataFrame:
        return self.load(path, format="text", **opts)


# Streams created in this kernel, by the temp view their DataFrames read.
_SOURCES: dict[str, _Source] = {}


def _sources_of(df: DataFrame) -> list[_Source]:
    """The stream sources a DataFrame reads: their views appear in its plan."""
    try:
        plan = df.relation.explain()
    except Exception:  # noqa: BLE001 - fall back to the SQL text
        plan = ""
    text = plan + " " + str(getattr(df.relation, "sql_query", lambda: "")())
    return [s for view, s in _SOURCES.items() if view in text]


def is_streaming(df: DataFrame) -> bool:
    return bool(_sources_of(df))


class StreamingQuery:
    """What ``start()``/``toTable()`` return. The run has already finished on this PC."""

    def __init__(self, name: str | None, progress: list[dict[str, Any]], error: BaseException | None = None):
        self.id = str(uuid.uuid4())
        self.runId = str(uuid.uuid4())  # noqa: N815
        self.name = name
        self.recentProgress = progress  # noqa: N815
        self._error = error

    @property
    def isActive(self) -> bool:  # noqa: N802
        return False

    @property
    def lastProgress(self) -> dict[str, Any] | None:  # noqa: N802
        return self.recentProgress[-1] if self.recentProgress else None

    @property
    def status(self) -> dict[str, Any]:
        return {"message": "Stopped", "isDataAvailable": False, "isTriggerActive": False}

    def awaitTermination(self, timeout: float | None = None) -> bool:  # noqa: N802
        return True

    def processAllAvailable(self) -> None:  # noqa: N802
        return None

    def stop(self) -> None:
        return None

    def exception(self) -> BaseException | None:
        return self._error

    def explain(self, extended: bool = False) -> None:
        print("A stream on this PC runs as trigger(availableNow=True): it has already processed what was available.")


class StreamWriter:
    """``df.writeStream``."""

    def __init__(self, df: DataFrame):
        self._df = df
        self._format = "delta"
        self._options: dict[str, Any] = {}
        self._mode = "append"
        self._name: str | None = None
        self._batch_fn: Callable[[DataFrame, int], Any] | None = None
        self._trigger_note: str | None = None

    def format(self, fmt: str) -> StreamWriter:
        self._format = fmt.lower()
        return self

    def option(self, key: str, value: Any) -> StreamWriter:
        self._options[str(key).lower()] = value
        return self

    def options(self, **opts: Any) -> StreamWriter:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def outputMode(self, mode: str) -> StreamWriter:  # noqa: N802
        mode = str(mode).lower()
        if mode not in ("append", "complete", "update"):
            raise ValueError(f"Unknown output mode {mode!r}: use append, complete or update.")
        if mode == "update":
            raise NotImplementedError("outputMode(\"update\") runs in Fabric. On this PC use append, complete, "
                                      "or foreachBatch with a MERGE.")
        self._mode = mode
        return self

    def queryName(self, name: str) -> StreamWriter:  # noqa: N802
        self._name = name
        return self

    def partitionBy(self, *_cols: Any) -> StreamWriter:  # noqa: N802
        return self

    def trigger(self, *, availableNow: bool | None = None, once: bool | None = None,  # noqa: N803
                processingTime: str | None = None, continuous: str | None = None) -> StreamWriter:  # noqa: N803
        if continuous:
            raise NotImplementedError("Continuous streams run in Fabric. On this PC use trigger(availableNow=True): "
                                      "each run processes what has arrived since the last one.")
        if processingTime:
            self._trigger_note = (f"trigger(processingTime={processingTime!r}) runs as availableNow on this PC: it "
                                  "processed what was available and stopped. Run the cell again for new data; in "
                                  "Fabric it keeps running.")
        return self

    def foreachBatch(self, fn: Callable[[DataFrame, int], Any]) -> StreamWriter:  # noqa: N802
        self._batch_fn = fn
        return self

    def foreach(self, *_a: Any) -> StreamWriter:
        raise NotImplementedError("writeStream.foreach (a row at a time) runs in Fabric. On this PC use foreachBatch.")

    # ---- running

    def toTable(self, tableName: str, format: str | None = None, outputMode: str | None = None,  # noqa: A002, N802, N803
                partitionBy: Any = None, queryName: str | None = None, **options: Any) -> StreamingQuery:  # noqa: N803
        if outputMode:
            self.outputMode(outputMode)
        if queryName:
            self.queryName(queryName)
        self.options(**options)
        if (format or self._format) not in ("delta", None):
            raise ValueError("Lakehouse tables are Delta tables. Leave out format, or use format(\"delta\").")
        return self._run(lambda data, _batch: self._to_table(data, tableName))

    def start(self, path: str | None = None, format: str | None = None, outputMode: str | None = None,  # noqa: A002, N803
              partitionBy: Any = None, queryName: str | None = None, **options: Any) -> StreamingQuery:  # noqa: N803
        if format:
            self.format(format)
        if outputMode:
            self.outputMode(outputMode)
        if queryName:
            self.queryName(queryName)
        self.options(**options)
        path = path or self._options.get("path")
        if self._batch_fn is not None:
            return self._run(self._foreach)
        if self._format == "console":
            return self._run(lambda data, batch: _console(self._df, data, batch))
        if self._format == "memory":
            if not self._name:
                raise ValueError("A memory sink needs queryName(...): it becomes the temp view's name.")
            return self._run(lambda data, _batch: self._to_memory(data))
        if self._format == "delta":
            rel = lakehouse.relative_path(path) if path else None
            if rel and rel.startswith("Tables/"):
                return self._run(lambda data, _batch: self._to_table(data, ".".join(p for p in rel.split("/")[1:] if p)))
            raise ValueError("On this PC a stream writes to a lakehouse table: use toTable(\"name\") or a Tables/ path.")
        if self._format in ("parquet", "csv", "json") and path and lakehouse.relative_path(path) is None:
            return self._run(lambda data, batch: self._to_files(data, Path(path), batch))
        raise ValueError(f"Streaming into {self._format!r} at {path} runs in Fabric. On this PC, write to a "
                         "lakehouse table, foreachBatch, console, memory, or a folder on this PC.")

    def _checkpoint(self) -> Path:
        location = self._options.get("checkpointlocation")
        if not location:
            if self._format in ("console", "memory"):
                return Path()  # nothing to remember, as in Spark
            raise ValueError("Set option(\"checkpointLocation\", ...): it's where the stream remembers what it "
                             "has processed, so the next run only takes new data.")
        rel = lakehouse.relative_path(location)
        if rel is None:
            return Path(location)
        lh = lakehouse._current()
        return lh.work_dir / "stream-checkpoints" / lh.lakehouse_id / rel

    def _run(self, sink: Callable[[pa.Table, int], Any]) -> StreamingQuery:
        sources = _sources_of(self._df)
        if not sources:
            raise ValueError("writeStream is for DataFrames read with spark.readStream. Use df.write for this one.")
        if len(sources) > 1:
            raise NotImplementedError("Joining two streams runs in Fabric. On this PC a stream reads one source "
                                      "(it can join tables read with spark.read).")
        src = sources[0]
        folder = self._checkpoint()
        state_file = folder / "kratosforge-offsets.json" if folder != Path() else None
        state = jsonfile.read_json(state_file, {}) if state_file else {}
        if state and state.get("source") not in (None, src.id):
            raise ValueError(f"The checkpoint at {self._options.get('checkpointlocation')} belongs to another "
                             f"source ({state['source']}). Use a new checkpointLocation for this stream.")
        batch_id = int(state.get("batch", -1)) + 1
        started = time.monotonic()
        data, new_state, what = src.next_batch(state.get("offsets", {}))
        progress: list[dict[str, Any]] = []
        label = f"Stream {self._name}" if self._name else "Stream"
        conn = src.session.conn
        if data is not None:
            data = _conform(data, src.arrow_schema, src.target)
            conn.unregister(src.view)
            conn.register(src.view, data)
            try:
                rows = sink(_arrow(self._df), batch_id)  # the DataFrame as transformed, over this batch
            finally:
                conn.unregister(src.view)
                conn.register(src.view, src.arrow_schema.empty_table())
            if state_file:
                jsonfile.write_json(state_file, {"source": src.id, "batch": batch_id, "offsets": new_state,
                                                 "updated": datetime.now(timezone.utc).isoformat()})
            progress.append({"id": batch_id, "batchId": batch_id, "name": self._name, "numInputRows": data.num_rows,
                             "timestamp": datetime.now(timezone.utc).isoformat(),
                             "durationMs": {"triggerExecution": int((time.monotonic() - started) * 1000)},
                             "sources": [{"description": src.id, "endOffset": new_state}],
                             "sink": {"description": self._format, "numOutputRows": rows if isinstance(rows, int) else None}})
            n = data.num_rows
            print(f"{label} batch {batch_id}: {n} row{'' if n == 1 else 's'} from {what}.")
        else:
            print(f"{label}: nothing new ({what}).")
        if self._trigger_note:
            print(self._trigger_note)
        return StreamingQuery(self._name, progress)

    # ---- sinks

    def _foreach(self, data: pa.Table, batch: int) -> None:
        from .spark_local import session
        frame = DataFrame(session().conn.from_arrow(data), session())
        self._batch_fn(frame, batch)  # type: ignore[misc]

    def _to_table(self, data: pa.Table, name: str) -> int:
        from .spark_local import LakehouseWriter, session
        writer = LakehouseWriter(DataFrame(session().conn.from_arrow(data), session()))
        for k in ("mergeschema", "overwriteschema"):
            if k in self._options:
                writer.option(k, self._options[k])
        writer.mode("overwrite" if self._mode == "complete" else "append").saveAsTable(name)
        return data.num_rows

    def _to_memory(self, data: pa.Table) -> int:
        from .spark_local import session
        conn = session().conn
        name = self._name or ""
        conn.register("__stream_batch", data)
        try:
            if self._mode == "complete" or not _exists(conn, name):
                conn.execute(f'CREATE OR REPLACE TABLE "{_ident(name)}" AS SELECT * FROM __stream_batch')
            else:
                conn.execute(f'INSERT INTO "{_ident(name)}" SELECT * FROM __stream_batch')
        finally:
            conn.unregister("__stream_batch")
        return data.num_rows

    def _to_files(self, data: pa.Table, folder: Path, batch: int) -> int:
        import pyarrow.csv as pacsv
        import pyarrow.json  # noqa: F401
        import pyarrow.parquet as pq
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"part-{batch:05d}-{uuid.uuid4().hex[:8]}"
        if self._format == "parquet":
            pq.write_table(data, folder / f"{stem}.parquet")
        elif self._format == "csv":
            pacsv.write_csv(data, folder / f"{stem}.csv")
        else:
            import json
            with open(folder / f"{stem}.json", "w", encoding="utf-8") as f:
                for row in data.to_pylist():
                    f.write(json.dumps(row, default=str) + "\n")
        return data.num_rows


def _console(df: DataFrame, data: pa.Table, batch: int) -> int:
    print("-------------------------------------------")
    print(f"Batch: {batch}")
    print("-------------------------------------------")
    from .spark_local import session
    DataFrame(session().conn.from_arrow(data), session()).show()
    return data.num_rows


def _conform(data: pa.Table, want: pa.Schema, source: str) -> pa.Table:
    """A batch in the stream's columns and types (fixed when it was read: a later CSV whose column
    reads as text can't change it), missing columns as null, as Spark does with a schema."""
    if data.schema == want:
        return data
    cols = []
    for f in want:
        if f.name in data.column_names:
            try:
                cols.append(data[f.name].cast(f.type))
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
                raise ValueError(f"New data in {source} has column {f.name} as {data[f.name].type}, but the stream "
                                 f"reads it as {f.type}. Nothing was written. ({e})") from None
        else:
            cols.append(pa.nulls(data.num_rows, f.type))
    return pa.table(cols, schema=want)


def _exists(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM duckdb_tables() WHERE table_name = ?", [name]).fetchall())


def _ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"{name!r} can't be a view name: use letters, numbers and underscores.")
    return name


def install() -> None:
    """``spark.readStream``, ``df.writeStream`` and ``df.isStreaming``."""
    from .spark_local import LocalSparkSession
    LocalSparkSession.readStream = property(lambda self: StreamReader(self))  # type: ignore[attr-defined]
    DataFrame.writeStream = property(lambda self: StreamWriter(self))  # type: ignore[attr-defined]
    DataFrame.isStreaming = property(is_streaming)  # type: ignore[attr-defined]
