"""Structured Streaming in an Academy PySpark exercise: ``spark.readStream`` and ``df.writeStream`` over
the exercise's Delta tables and its ``Files/`` folder.

It runs in the learner's process as part of the generated ``kf_learner`` package (learner_pkg.py). As
on this PC's notebooks (spark_stream.py), there is no cluster to keep a stream running, so every stream
is the ``trigger(availableNow=True)`` kind: a run takes what arrived since the last run, writes it and
stops, and its checkpoint remembers where it got to, so running the answer again takes only new data.

Sources:

- a Delta table, ``readStream.table("bronze.orders")``: the first run takes the table as it is, later
  runs the rows each new commit appended. A commit that updated or deleted rows stops the stream
  unless ``skipChangeCommits`` is set; OPTIMIZE-style commits are passed over, as in Spark. A change
  to the table's columns stops it too: start over with a new checkpointLocation.
- a folder of files, ``readStream.format("csv"|"json"|"parquet").load("Files/landing")``: the files
  it hasn't taken yet. Give ``.schema(...)`` (a StructType) to fix the columns, as Spark expects.

Sinks: ``toTable("bronze.orders")`` (appended), ``foreachBatch(fn)`` (``fn(batch_df, batch_id)``, for a
MERGE with delta.tables.DeltaTable) and ``format("console")``. ``checkpointLocation`` is a folder under
``Files/`` (``Files/checkpoints/orders``); the checkpoint moves on only after the sink succeeded.
Only append output mode runs here, and continuous triggers don't: both say so.

Delta rows are read through delta-rs's own reader (pyspark_shim.read_delta), and a commit's new rows
are the rows of that version less those of the one before, so they are read version by version.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import deltalake
import pyarrow as pa
from duckdb.experimental.spark.sql.dataframe import DataFrame

OFFSETS = "kratosforge-offsets.json"
# Commits that only rewrite files with the same rows (dataChange=false), which a stream passes over.
_REARRANGE = {"OPTIMIZE", "ZORDER", "Z-ORDER", "REORG", "VACUUM START", "VACUUM END"}
_SUFFIXES = {"csv": (".csv",), "json": (".json", ".jsonl", ".ndjson"), "parquet": (".parquet",)}
_PREFIXES = ("file:/lakehouse/default/", "/lakehouse/default/", "lakehouse/default/")
_CSV_OPTIONS = {"header": "header", "sep": "sep", "delimiter": "sep", "quote": "quotechar", "escape": "escapechar",
                "nullvalue": "na_values", "dateformat": "date_format", "timestampformat": "timestamp_format"}
_READ_OPTIONS = ("maxfilespertrigger", "maxbytespertrigger", "skipchangecommits", "ignorechanges", "ignoredeletes")


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _arrow(df: DataFrame) -> pa.Table:
    rel = df.relation
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


def lakehouse_parts(path: str, top: str) -> list[str] | None:
    """``path``'s parts under ``top`` (Files or Tables), or None if it isn't such a path."""
    text = str(path).replace("\\", "/")
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or not parts or parts[0] != top or ".." in parts:
        return None
    return parts


def _inside(work: Path, parts: list[str]) -> Path:
    """``work/parts``, refused if it or a folder on the way is a link."""
    local = work.joinpath(*parts)
    for p in [local, *local.parents]:
        if p == work:
            return local
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise PermissionError(f"{p.name} is a link; an exercise's files are plain files and folders.")
    raise PermissionError("That path isn't in this exercise.")


def _files_path(work: Path, path: str, what: str) -> Path:
    parts = lakehouse_parts(path, "Files")
    if parts is None:
        raise ValueError(f"{path!r}: in an exercise, {what} is a folder under Files/, like Files/checkpoints/orders.")
    return _inside(work, parts)


class _Source:
    def __init__(self, session: Any, kind: str, target: str, fmt: str, options: dict[str, Any], schema: Any):
        self.session, self.kind, self.target, self.fmt = session, kind, target, fmt
        self.options, self.user_schema = options, schema
        self.view = "__stream_" + uuid.uuid4().hex[:12]
        self.arrow_schema: pa.Schema | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.fmt}:{self.target}"

    # ---- Delta tables

    def _path(self) -> Path:
        from .pyspark_shim import table_path
        path = table_path(self.session.work, self.target)
        if not (path / "_delta_log").is_dir():
            raise ValueError(f"There's no table {self.target}")
        return path

    def _rows(self, version: int) -> pa.Table:
        from .pyspark_shim import read_delta
        return read_delta(self._path(), version)

    def _new_rows(self, version: int) -> pa.Table:
        """The rows commit ``version`` added: that version's rows less the version before's."""
        now, before = self._rows(version), self._rows(version - 1)
        if now.column_names != before.column_names:
            raise ValueError(f"{self.target}'s columns changed in version {version}, and a stream stops when its "
                             "source's columns change, as in Spark. Start over with a new checkpointLocation.")
        conn = self.session.conn
        a, b = f"{self.view}_now", f"{self.view}_before"
        conn.register(a, now)
        conn.register(b, before)
        try:
            return _arrow_of(conn.sql(f'SELECT * FROM "{a}" EXCEPT ALL SELECT * FROM "{b}"'))
        finally:
            conn.unregister(a)
            conn.unregister(b)

    def _delta_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        path = self._path()
        dt = deltalake.DeltaTable(str(path))
        now = dt.version()
        last = state.get("version")
        if last is not None and last >= now:
            return None, state, f"no new commits after version {last}"
        if last is None:
            return self._rows(now), {"version": now}, f"version {now} (the table as it is)"
        skip = _flag(self.options.get("skipchangecommits")) or _flag(self.options.get("ignorechanges"))
        operations = {h.get("version"): str(h.get("operation") or "") for h in dt.history(now - last)}
        before = set(deltalake.DeltaTable(str(path), version=last).file_uris())
        pieces: list[pa.Table] = []
        for version in range(last + 1, now + 1):
            current = set(deltalake.DeltaTable(str(path), version=version).file_uris())
            if operations.get(version, "").upper() in _REARRANGE:
                pass   # the same rows in other files (dataChange=false)
            elif before - current:
                if not skip:
                    raise ValueError(f"{self.target} had rows updated or deleted in version {version}, and a stream "
                                     "only reads appended rows. Add .option(\"skipChangeCommits\", \"true\") to skip "
                                     "those commits, as in Fabric, or start over with a new checkpointLocation.")
            elif current - before:
                pieces.append(self._new_rows(version))
            before = current
        data = pa.concat_tables(pieces) if pieces else None
        return data, {"version": now}, f"versions {last + 1}..{now}"

    # ---- folders of files

    def _folder(self) -> Path:
        return _files_path(self.session.work, self.target, "a stream's source")

    def _found(self) -> list[Path]:
        folder = self._folder()
        if folder.is_file():
            return [folder]
        if not folder.is_dir():
            return []
        out = []
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix.lower() in _SUFFIXES[self.fmt]:
                _inside(self.session.work, list(p.relative_to(self.session.work).parts))
                out.append(p)
        return out

    def _key(self, p: Path) -> str:
        folder = self._folder()
        rel = p.name if folder.is_file() else p.relative_to(folder).as_posix()
        st = p.stat()
        return f"{rel}|{st.st_size}|{st.st_mtime_ns}"

    def _read_file(self, p: Path) -> pa.Table:
        conn = self.session.conn
        if self.fmt == "csv":
            kw: dict[str, Any] = {}
            for key, value in self.options.items():
                if key in _CSV_OPTIONS:
                    kw[_CSV_OPTIONS[key]] = _flag(value) if key == "header" else value
            kw.setdefault("header", False)
            if self.user_schema is not None:
                dtype, names = self.user_schema.extract_types_and_names()
                rel = conn.read_csv(str(p), dtype=dtype, names=names, **kw)
            else:
                rel = conn.read_csv(str(p), **kw)
        elif self.fmt == "json":
            rel = conn.read_json(str(p))
        else:
            rel = conn.read_parquet(str(p))
        data = _arrow_of(rel)
        if self.user_schema is not None and self.fmt != "csv":
            dtype, names = self.user_schema.extract_types_and_names()
            missing = [n for n in names if n not in data.column_names]
            conn.register("__stream_file", data)
            try:
                cols = ", ".join(f'CAST({"NULL" if n in missing else _quote(n)} AS {t}) AS {_quote(n)}'
                                 for n, t in zip(names, dtype, strict=True))
                data = _arrow_of(conn.sql(f"SELECT {cols} FROM __stream_file"))
            finally:
                conn.unregister("__stream_file")
        return data

    def _file_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        seen = set(state.get("files", []))
        new = [p for p in self._found() if self._key(p) not in seen]
        if not new:
            return None, state, "no new files"
        tables = [self._read_file(p) for p in new]
        data = pa.concat_tables(tables, promote_options="default") if len(tables) > 1 else tables[0]
        files = sorted(seen | {self._key(p) for p in new})
        return data, {"files": files}, f"{len(new)} new file{'' if len(new) == 1 else 's'}"

    def next_batch(self, state: dict[str, Any]) -> tuple[pa.Table | None, dict[str, Any], str]:
        return self._delta_batch(state) if self.kind == "table" else self._file_batch(state)

    def schema(self) -> pa.Schema:
        if self.kind == "table":
            return self._rows(deltalake.DeltaTable(str(self._path())).version()).schema
        found = self._found()
        if found:
            return self._read_file(found[0]).schema
        if self.user_schema is None:
            raise ValueError(f"{self.target} has no {self.fmt} files yet, so the stream's columns aren't known. "
                             "Give readStream a schema.")
        dtype, names = self.user_schema.extract_types_and_names()
        cols = ", ".join(f"CAST(NULL AS {t}) AS {_quote(n)}" for n, t in zip(names, dtype, strict=True))
        return _arrow_of(self.session.conn.sql(f"SELECT {cols} LIMIT 0")).schema


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _arrow_of(rel: Any) -> pa.Table:
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


# The streams this process made, by the temp view their DataFrames read.
_SOURCES: dict[str, _Source] = {}


def _sources_of(df: DataFrame) -> list[_Source]:
    try:
        plan = df.relation.explain()
    except Exception:  # noqa: BLE001 - fall back to the SQL text
        plan = ""
    text = plan + " " + str(getattr(df.relation, "sql_query", lambda: "")())
    return [s for view, s in _SOURCES.items() if view in text]


def is_streaming(df: DataFrame) -> bool:
    return bool(_sources_of(df))


class StreamReader:
    """``spark.readStream``."""

    def __init__(self, session: Any):
        self._session = session
        self._format: str | None = None
        self._options: dict[str, Any] = {}
        self._schema: Any = None

    def format(self, fmt: str) -> StreamReader:  # noqa: A003
        self._format = str(fmt).lower()
        return self

    def option(self, key: str, value: Any) -> StreamReader:
        self._options[str(key).lower()] = value
        return self

    def options(self, **opts: Any) -> StreamReader:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def schema(self, schema: Any) -> StreamReader:
        if not hasattr(schema, "extract_types_and_names"):
            raise TypeError("In an exercise, give readStream.schema a StructType (from pyspark.sql.types).")
        self._schema = schema
        return self

    def _start(self, kind: str, target: str, fmt: str) -> DataFrame:
        for key in ("startingversion", "startingtimestamp"):
            if key in self._options:
                raise NotImplementedError(f"{key} isn't in an exercise: a stream starts from the table as it is, "
                                          "or where its checkpoint left off.")
        if self._options.get("maxfilespertrigger") or self._options.get("maxbytespertrigger"):
            print("Note: availableNow takes everything that has arrived, so maxFilesPerTrigger doesn't apply here.")
        src = _Source(self._session, kind, target, fmt, dict(self._options), self._schema)
        src.arrow_schema = src.schema()
        conn = self._session.conn
        conn.register(src.view, src.arrow_schema.empty_table())
        _SOURCES[src.view] = src
        return DataFrame(conn.table(src.view), self._session)

    def table(self, name: str) -> DataFrame:
        return self._start("table", name, "delta")

    def load(self, path: str | None = None, format: str | None = None, **opts: Any) -> DataFrame:  # noqa: A002
        self.options(**opts)
        fmt = (format or self._format or "delta").lower()
        if not path:
            raise ValueError("readStream.load needs a path.")
        if fmt == "delta":
            parts = lakehouse_parts(path, "Tables")
            if parts is None or len(parts) not in (2, 3):
                raise ValueError("In an exercise, a Delta stream reads a table: readStream.table(\"bronze.orders\") "
                                 "or load(\"Tables/bronze/orders\").")
            return self.table(".".join(parts[1:]))
        if fmt not in _SUFFIXES:
            raise ValueError(f"In an exercise, a stream reads Delta tables or csv, json or parquet files, not {fmt!r}.")
        _files_path(self._session.work, path, "a stream's source")
        return self._start("files", path, fmt)

    def csv(self, path: str, schema: Any = None, **opts: Any) -> DataFrame:
        if schema is not None:
            self.schema(schema)
        return self.load(path, format="csv", **opts)

    def json(self, path: str, schema: Any = None, **opts: Any) -> DataFrame:
        if schema is not None:
            self.schema(schema)
        return self.load(path, format="json", **opts)

    def parquet(self, path: str, **opts: Any) -> DataFrame:
        return self.load(path, format="parquet", **opts)


class StreamingQuery:
    """What ``start()`` and ``toTable()`` return: the run has already finished."""

    def __init__(self, name: str | None, progress: list[dict[str, Any]]):
        self.id = str(uuid.uuid4())
        self.runId = str(uuid.uuid4())  # noqa: N815
        self.name = name
        self.recentProgress = progress  # noqa: N815

    @property
    def isActive(self) -> bool:  # noqa: N802
        return False

    @property
    def lastProgress(self) -> dict[str, Any] | None:  # noqa: N802
        return self.recentProgress[-1] if self.recentProgress else None

    @property
    def status(self) -> dict[str, Any]:
        return {"message": "Stopped", "isDataAvailable": False, "isTriggerActive": False}

    def awaitTermination(self, timeout: float | None = None) -> bool:  # noqa: N802, ARG002
        return True

    def processAllAvailable(self) -> None:  # noqa: N802
        return None

    def stop(self) -> None:
        return None

    def exception(self) -> None:
        return None


class StreamWriter:
    """``df.writeStream``."""

    def __init__(self, df: DataFrame, session: Any):
        self._df, self._session = df, session
        self._format = "delta"
        self._options: dict[str, Any] = {}
        self._name: str | None = None
        self._batch_fn: Callable[[DataFrame, int], Any] | None = None
        self._note: str | None = None

    def format(self, fmt: str) -> StreamWriter:  # noqa: A003
        self._format = str(fmt).lower()
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
        if mode != "append":
            raise NotImplementedError(f"outputMode(\"{mode}\") isn't in an exercise: it keeps a running result "
                                      "across batches. Use append, or foreachBatch with a MERGE.")
        return self

    def queryName(self, name: str) -> StreamWriter:  # noqa: N802
        self._name = name
        return self

    def partitionBy(self, *_cols: Any) -> StreamWriter:  # noqa: N802
        return self

    def trigger(self, *, availableNow: bool | None = None, once: bool | None = None,  # noqa: N803, ARG002
                processingTime: str | None = None, continuous: str | None = None) -> StreamWriter:  # noqa: N803
        if continuous:
            raise NotImplementedError("Continuous streams aren't in an exercise: use trigger(availableNow=True).")
        if processingTime:
            self._note = (f"trigger(processingTime={processingTime!r}) runs as availableNow here: it processed what "
                          "had arrived and stopped. In Fabric it keeps running.")
        return self

    def foreachBatch(self, fn: Callable[[DataFrame, int], Any]) -> StreamWriter:  # noqa: N802
        self._batch_fn = fn
        return self

    def foreach(self, *_a: Any) -> StreamWriter:
        raise NotImplementedError("writeStream.foreach (a row at a time) isn't in an exercise: use foreachBatch.")

    def toTable(self, tableName: str, format: str | None = None, outputMode: str | None = None,  # noqa: N802, N803, A002
                partitionBy: Any = None, queryName: str | None = None, **options: Any) -> StreamingQuery:  # noqa: N803, ARG002
        if outputMode:
            self.outputMode(outputMode)
        if queryName:
            self.queryName(queryName)
        self.options(**options)
        if (format or self._format) != "delta":
            raise ValueError("Exercise tables are Delta tables: leave out format, or use format(\"delta\").")
        return self._run(lambda data, _batch: self._to_table(data, tableName))

    def start(self, path: str | None = None, format: str | None = None, outputMode: str | None = None,  # noqa: A002, N803
              partitionBy: Any = None, queryName: str | None = None, **options: Any) -> StreamingQuery:  # noqa: N803, ARG002
        if format:
            self.format(format)
        if outputMode:
            self.outputMode(outputMode)
        if queryName:
            self.queryName(queryName)
        self.options(**options)
        if self._batch_fn is not None:
            return self._run(self._foreach)
        if self._format == "console":
            return self._run(self._console)
        path = path or self._options.get("path")
        parts = lakehouse_parts(path, "Tables") if path else None
        if self._format == "delta" and parts and len(parts) in (2, 3):
            name = ".".join(parts[1:])
            return self._run(lambda data, _batch: self._to_table(data, name))
        raise ValueError("In an exercise, a stream writes to a table (toTable(\"bronze.orders\") or a Tables/ path), "
                         "to foreachBatch or to the console.")

    def _checkpoint(self) -> Path | None:
        location = self._options.get("checkpointlocation")
        if not location:
            if self._format == "console" and self._batch_fn is None:
                return None
            raise ValueError("Set option(\"checkpointLocation\", \"Files/checkpoints/...\"): it's where the stream "
                             "remembers what it has processed, so the next run only takes new data.")
        return _files_path(self._session.work, location, "checkpointLocation")

    def _run(self, sink: Callable[[pa.Table, int], Any]) -> StreamingQuery:
        sources = _sources_of(self._df)
        if not sources:
            raise ValueError("writeStream is for DataFrames read with spark.readStream; use df.write for this one.")
        if len(sources) > 1:
            raise NotImplementedError("Joining two streams isn't in an exercise: a stream reads one source (it can "
                                      "join tables read with spark.read).")
        src = sources[0]
        folder = self._checkpoint()
        state_file = folder / OFFSETS if folder is not None else None
        state: dict[str, Any] = {}
        if state_file is not None and state_file.is_file():
            state = json.loads(state_file.read_text(encoding="utf-8"))
        if state and state.get("source") not in (None, src.id):
            raise ValueError(f"The checkpoint at {self._options.get('checkpointlocation')} belongs to another source "
                             f"({state['source']}). Use a new checkpointLocation for this stream.")
        batch_id = int(state.get("batch", -1)) + 1
        started = time.monotonic()
        data, offsets, what = src.next_batch(state.get("offsets", {}))
        label = f"Stream {self._name}" if self._name else "Stream"
        progress: list[dict[str, Any]] = []
        if data is not None and src.arrow_schema is not None:
            data = _conform(data, src.arrow_schema, src.target)
            conn = self._session.conn
            conn.unregister(src.view)
            conn.register(src.view, data)
            try:
                rows = sink(_arrow(self._df), batch_id)   # the DataFrame as transformed, over this batch
            finally:
                conn.unregister(src.view)
                conn.register(src.view, src.arrow_schema.empty_table())
            if state_file is not None:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = state_file.with_name(OFFSETS + ".tmp")
                tmp.write_text(json.dumps({"source": src.id, "batch": batch_id, "offsets": offsets,
                                           "updated": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
                os.replace(tmp, state_file)
            progress.append({"id": batch_id, "batchId": batch_id, "name": self._name, "numInputRows": data.num_rows,
                             "durationMs": {"triggerExecution": int((time.monotonic() - started) * 1000)},
                             "sources": [{"description": src.id, "endOffset": offsets}],
                             "sink": {"numOutputRows": rows if isinstance(rows, int) else None}})
            print(f"{label} batch {batch_id}: {data.num_rows} row{'' if data.num_rows == 1 else 's'} from {what}.")
        else:
            print(f"{label}: nothing new ({what}).")
        if self._note:
            print(self._note)
        return StreamingQuery(self._name, progress)

    def _frame(self, data: pa.Table) -> DataFrame:
        return DataFrame(self._session.conn.from_arrow(data), self._session)

    def _foreach(self, data: pa.Table, batch: int) -> None:
        self._batch_fn(self._frame(data), batch)  # type: ignore[misc]

    def _console(self, data: pa.Table, batch: int) -> int:
        print("-------------------------------------------")
        print(f"Batch: {batch}")
        print("-------------------------------------------")
        self._frame(data).show()
        return data.num_rows

    def _to_table(self, data: pa.Table, name: str) -> int:
        from .pyspark_shim import Writer
        writer = Writer(self._frame(data), self._session.work).mode("append")
        if "mergeschema" in self._options:
            writer.option("mergeSchema", self._options["mergeschema"])
        writer.saveAsTable(name)
        return data.num_rows


def _conform(data: pa.Table, want: pa.Schema | None, source: str) -> pa.Table:
    """A batch in the stream's columns and types, fixed when the stream was read (missing columns null)."""
    if want is None or data.schema == want:
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
