"""A ``spark`` session for notebooks on this PC, so PySpark code written for Fabric runs here too.

It is DuckDB's PySpark-compatible API (``duckdb.experimental.spark``) wired to the attached
lakehouse: ``spark.read.table("dbo.sales")`` and ``spark.sql("SELECT * FROM sales")`` read the
lakehouse's Delta tables from OneLake, ``Files/`` paths are downloaded first, and
``df.write.saveAsTable("dbo.out")`` writes a Delta table back with ``lakehouse.write_table``.
Everything runs on this PC; there is no Spark cluster. When the notebook runs in Fabric, the
same code runs on Fabric's real Spark.

``install()`` also makes ``import pyspark`` and ``from pyspark.sql import functions as F``
resolve to the DuckDB versions, unless real PySpark is installed.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import re
import sys
import datetime
import tempfile
import threading
import weakref
from typing import Any, NamedTuple, NoReturn, cast

import duckdb
import pyarrow as pa
from duckdb.experimental.spark.context import SparkContext
from duckdb.experimental.spark.sql import dataframe as _dataframe
from duckdb.experimental.spark.sql import session as _session_module
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.column import Column as _Column
from duckdb.experimental.spark.sql.session import SparkSession

from . import lakehouse, spark_api, spark_compat, spark_infer, sql as _sql, spark_replace_where, sql_dialects
from .delta_dml import _LEADING_COMMENTS as delta_dml_comments
from .duckdb_rows import keep_try_cast
from . import spark_hash, spark_time_zone
from .spark_complex import spark_complex_types
from .spark_decimals import spark_decimal_types
from .spark_branch_types import spark_branch_types
from .spark_text_compare import spark_text_comparisons
from . import trees


def _arrow(df: DataFrame) -> pa.Table:
    rel = _spark_types(df.relation)
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


_WIDE_INTS = ("HUGEINT", "UHUGEINT")
_NESTED = ("struct", "list", "array", "map", "union")


def _long_type(kind: Any) -> str | None:
    """DuckDB SQL for nested type ``kind`` with each 128-bit integer in it as BIGINT, or None when it
    holds none. Built from the type's parts, since its text doesn't quote field names."""
    def write(t: Any) -> tuple[str, bool]:
        if t.id in ("hugeint", "uhugeint"):
            return "BIGINT", True
        if t.id not in _NESTED:
            return str(t), False
        # An array lists its size as a child, and a union its tag (with an empty name): neither is a member.
        children = [(name, child) for name, child in t.children
                    if not (t.id == "array" and name == "size") and not (t.id == "union" and name == "")]
        parts = [(name, *write(child)) for name, child in children]
        wide = any(p[2] for p in parts)
        if t.id == "list":
            return f"{parts[0][1]}[]", wide
        if t.id == "array":
            return f"{parts[0][1]}[{dict(t.children)['size']}]", wide
        if t.id == "map":
            return f"MAP({parts[0][1]}, {parts[1][1]})", wide
        fields = ", ".join('"' + str(name).replace('"', '""') + f'" {sql}' for name, sql, _ in parts)
        return f"{t.id.upper()}({fields})", wide
    text, wide = write(kind)
    return text if wide else None


def _spark_types(rel: Any) -> Any:
    """``rel`` with DuckDB's 128-bit integers as BIGINT, Spark's long (EA-004). DuckDB's sum of INT or
    BIGINT is a HUGEINT, which Arrow and Delta would take as decimal(38,0) where Spark writes bigint; Spark
    has no 128-bit integer, so any HUGEINT column is one of these. A value too big for BIGINT stops the
    write with a message instead of being cut."""
    types = [str(t) for t in rel.types]
    nested = [_long_type(t) if t.id in _NESTED else None for t in rel.types]
    if not any(t in _WIDE_INTS for t in types) and not any(nested):
        return rel
    columns = []
    for name, kind, target in zip(rel.columns, types, nested, strict=True):
        quoted = '"' + name.replace('"', '""') + '"'
        if target:
            # A sum inside a struct, list or map (struct(sum(x)), collect_list over sums): the same
            # type with each 128-bit integer as BIGINT. A value that doesn't come through unchanged
            # stops the write, as at the top level.
            literal = "'" + name.replace("'", "''") + "'"
            columns.append(f"CASE WHEN {quoted} IS NULL OR TRY_CAST({quoted} AS {target}) IS NOT DISTINCT FROM {quoted} "
                           f"THEN CAST({quoted} AS {target}) ELSE ERROR('The column ' || {literal} || ' holds a value "
                           "too big for a long (BIGINT), the type Spark gives it, so nothing was written.') END AS "
                           f"{quoted}")
        elif kind in _WIDE_INTS:
            literal = "'" + name.replace("'", "''") + "'"
            columns.append(f"CASE WHEN {quoted} BETWEEN -9223372036854775808 AND 9223372036854775807 OR {quoted} IS NULL "
                           f"THEN CAST({quoted} AS BIGINT) ELSE ERROR('The column ' || {literal} || ' has a value too "
                           "big for a long (BIGINT), the type Spark gives it, so nothing was written.') END AS "
                           f"{quoted}")
        else:
            columns.append(quoted)
    return rel.project(", ".join(columns))


# Writer options this PC honors, by lower-case name. Anything else is refused by name, so an
# option that changes what gets written can never be dropped and turn into a full overwrite.
# A path option is save()'s path, as in Spark; a table write
# refuses it (_TABLE_PATH).
_SCHEMA_OPTIONS = ("overwriteschema", "mergeschema")
# Options that only change how files are laid out, not which rows end up in the table.
_LAYOUT_OPTIONS = ("optimizewrite", "maxrecordsperfile", "compression")
# partitionOverwriteMode: static or dynamic, as the session setting (_overwrite_mode).
_MODE_OPTIONS = ("partitionoverwritemode",)
# replaceWhere: an overwrite replaces only the rows that match it, and the data must match it (_replace_where).
_REPLACE_OPTIONS = ("replacewhere",)
_TABLE_PATH = "Writing a table to its own path (an external table) runs in Fabric. Leave out the path option."


_OVERWRITE_MODE = "spark.sql.sources.partitionOverwriteMode"


def _overwrite_mode(session: Any, options: dict[str, Any]) -> str:
    """static or dynamic: the writer's partitionOverwriteMode option, else the session's setting."""
    mode = options.get("partitionoverwritemode")
    if mode is None:
        try:
            mode = session.conf.get(_OVERWRITE_MODE, "static")
        except (KeyError, NotImplementedError):  # a conf that can't answer has nothing set
            mode = "static"
    mode = str(mode).strip().lower()
    if mode not in ("static", "dynamic"):
        raise ValueError(f"partitionOverwriteMode is static or dynamic, not {mode!r}.")
    return mode


def _sql_literal(value: Any) -> str:
    """A partition value as Delta SQL, for an overwrite's predicate."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _partitions_predicate(data: pa.Table, columns: list[str]) -> str | None:
    """The partitions ``data`` writes to, as Delta SQL: each (column = value AND ...) it has, ORed.
    None when it has no rows (a dynamic overwrite with no rows replaces nothing, as in Spark)."""
    names = {n.lower(): n for n in data.column_names}
    have = [names[c.lower()] for c in columns]
    seen = data.select(have).group_by(have).aggregate([]).to_pylist()
    if not seen:
        return None
    quoted = {c: '"' + c.replace('"', '""') + '"' for c in columns}

    def term(column: str, value: Any) -> str:
        return f"{quoted[column]} IS NULL" if value is None else f"{quoted[column]} = {_sql_literal(value)}"
    if len(columns) == 1:  # one IN list, which delta-rs checks far faster than many ORs
        col, values = quoted[columns[0]], [row[have[0]] for row in seen]
        terms = [f"{col} IN ({', '.join(_sql_literal(v) for v in values if v is not None)})"] \
            if any(v is not None for v in values) else []
        terms += [f"{col} IS NULL"] if any(v is None for v in values) else []
        return " OR ".join(terms)
    return " OR ".join("(" + " AND ".join(term(c, row[h]) for c, h in zip(columns, have, strict=True)) + ")"
                       for row in seen)


def _true(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


# Spark writes a folder of part files: these are its formats on this PC, with each file's extension.
_FILE_FORMATS = {"parquet": ".snappy.parquet", "csv": ".csv", "json": ".json", "text": ".txt"}
# What a Spark write leaves in its folder; overwrite clears a folder only when it holds nothing else.
_SPARK_OUTPUT = re.compile(r"part-\d{5}-.*|_SUCCESS|_committed_.*|_started_.*|\.(?:part-\d{5}-.*|_SUCCESS)\.crc")


def _is_link(path: str) -> bool:
    return os.path.islink(path) or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def _clear_output(path: str) -> None:
    """Remove a Spark write's folder (or a file) at ``path`` before an overwrite, as Spark does. A
    folder that holds anything a Spark write doesn't leave (part files, _SUCCESS, name=value folders),
    or a link, is refused instead, so an overwrite can't delete someone's own files."""
    refused = "so overwrite didn't delete anything. Write to a new path, or empty the folder first."
    if _is_link(path):
        raise ValueError(f"{path} is a link, {refused}")
    if not os.path.isdir(path):
        os.remove(path)
        return
    for root, dirs, files in os.walk(path):
        rel = os.path.relpath(root, path)
        if rel != "." and not all("=" in part for part in rel.split(os.sep)):
            raise ValueError(f"{path} holds a folder ({rel}) that a Spark write doesn't make, {refused}")
        linked = [n for n in dirs + files if _is_link(os.path.join(root, n))]
        if linked:
            raise ValueError(f"{path} holds a link ({linked[0]}), {refused}")
        foreign = [f for f in files if not _SPARK_OUTPUT.fullmatch(f)]
        if foreign:
            raise ValueError(f"{path} holds files a Spark write doesn't make ({foreign[0]}), {refused}")
    trees.remove(path)


def write_files(rel: Any, path: str, fmt: str, partition_by: list[str], header: bool = False,
                sep: str = ",") -> None:
    """``rel`` written into the folder ``path`` as Spark writes it: part-00000-<uuid>-c000 files (one
    folder per value, name=value/, for each partitionBy column) and an empty _SUCCESS file."""
    import uuid
    columns = list(rel.columns)
    missing = [c for c in partition_by if c not in columns]
    if missing:
        raise ValueError(f"partitionBy column {missing[0]} isn't in the data, which has {', '.join(columns)}.")
    if fmt == "text":
        data = [c for c in columns if c not in partition_by]
        types = dict(zip(columns, (str(t) for t in rel.types), strict=True))
        if len(data) != 1 or types[data[0]] != "VARCHAR":
            raise ValueError("[UNSUPPORTED_DATA_TYPE] The text data source writes one string column; this "
                             f"DataFrame has {', '.join(f'{c} {types[c]}' for c in data) or 'none'}. Select one "
                             "string column, or write csv or parquet.")
    os.makedirs(path, exist_ok=True)
    stem = f"part-00000-{uuid.uuid4()}-c000"
    options = {"parquet": "FORMAT parquet, COMPRESSION snappy", "json": "FORMAT json",
               "csv": f"FORMAT csv, HEADER {str(header).lower()}, DELIMITER {_quoted_text(sep)}",
               "text": "FORMAT csv, HEADER false, QUOTE '', ESCAPE '', DELIMITER chr(1)"}[fmt]
    view = f"_kf_write_{uuid.uuid4().hex}"
    conn = _session.conn if _session is not None else duckdb.default_connection()
    rel.create_view(view, replace=True)  # on the connection the DataFrame reads from: spark's
    try:
        if partition_by:
            keep = ", ".join(_sql_name(c) for c in columns if c not in partition_by) or "*"
            names = ", ".join(_sql_name(c) for c in partition_by)
            texts = ", ".join(f"CAST({_sql_name(c)} AS VARCHAR)" for c in partition_by)
            for values in conn.execute(f"SELECT DISTINCT {names}, {texts} FROM {view}").fetchall():
                raw, shown = values[:len(partition_by)], values[len(partition_by):]
                folder = os.path.join(path, *(f"{_partition_text(c)}={_partition_text(v)}"
                                              for c, v in zip(partition_by, shown, strict=True)))
                os.makedirs(folder, exist_ok=True)
                where = " AND ".join(f"{_sql_name(c)} IS NOT DISTINCT FROM ${i + 1}" for i, c in enumerate(partition_by))
                target = os.path.join(folder, stem + _FILE_FORMATS[fmt])
                conn.execute(f"COPY (SELECT {keep} FROM {view} WHERE {where}) TO {_quoted_text(target)} ({options})",
                             list(raw))
        else:
            target = os.path.join(path, stem + _FILE_FORMATS[fmt])
            conn.execute(f"COPY (SELECT * FROM {view}) TO {_quoted_text(target)} ({options})")
    finally:
        conn.execute(f"DROP VIEW IF EXISTS {view}")
    open(os.path.join(path, "_SUCCESS"), "wb").close()  # noqa: SIM115


# The characters Spark escapes (as %XX) in a partition folder's name (ExternalCatalogUtils), as on
# Fabric's Linux, plus < > |, which a Windows file name can't hold. A space stays a space, as in Fabric.
_PARTITION_ESCAPE = set('"#%\'*/:=?\\{[]^<>|') | {chr(c) for c in range(1, 32)} | {chr(127)}


def _partition_text(value: str | None) -> str:
    """A partition column's name or value as Spark writes it in a folder name; NULL is Spark's
    __HIVE_DEFAULT_PARTITION__."""
    if value is None:
        return "__HIVE_DEFAULT_PARTITION__"
    return "".join(f"%{ord(ch):02X}" if ch in _PARTITION_ESCAPE else ch for ch in value)


def _quoted_text(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _sql_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class LakehouseWriter:
    """``df.write``: Delta tables in the attached lakehouse, or files on this PC."""

    def __init__(self, df: DataFrame):
        self._df = df
        self._mode = "errorifexists"
        self._format = "delta"
        self._options: dict[str, Any] = {}

    def mode(self, mode: str) -> LakehouseWriter:
        self._mode = mode.lower()
        return self

    def format(self, fmt: str) -> LakehouseWriter:
        self._format = fmt.lower()
        return self

    def option(self, key: str, value: Any) -> LakehouseWriter:
        name = str(key).lower()
        if name in _MODE_OPTIONS:
            _overwrite_mode(None, {name: value})  # static or dynamic, else refused here
        self._options[name] = value
        return self

    def options(self, **opts: Any) -> LakehouseWriter:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def partitionBy(self, *cols: Any) -> LakehouseWriter:
        """Folders per value for files (name=value/), as Spark writes them. A lakehouse table is
        written without partitions on this PC, with a note."""
        flat = [c for col in cols for c in (col if isinstance(col, (list, tuple)) else [col])]
        self._partition_by = [str(c) for c in flat]
        return self

    def bucketBy(self, numBuckets: int, col: Any, *cols: Any) -> LakehouseWriter:  # noqa: N802, N803, ARG002
        """Refused, as in Fabric: a lakehouse table is a Delta table, and Delta tables have no buckets."""
        raise ValueError("Delta tables have no buckets, so this PC refuses bucketBy as Fabric does (\"Operation not "
                         "allowed: Bucketing is not supported for Delta tables\"). Leave bucketBy out, or use "
                         "partitionBy to split the table into folders.")

    def sortBy(self, col: Any, *cols: Any) -> LakehouseWriter:  # noqa: N802, ARG002
        """Refused: sortBy only goes with bucketBy, which Delta tables don't have."""
        raise ValueError("sortBy only goes with bucketBy, and Delta tables have no buckets, so this PC refuses it as "
                         "Fabric does. Leave it out, or sort the DataFrame with orderBy before you write it.")

    def _table_partitions_note(self) -> None:
        if getattr(self, "_partition_by", None):
            print("Note: partitionBy is ignored on this PC; the table is written without partitions.")

    def _check_options(self, allowed: tuple[str, ...], what: str) -> None:
        for name in self._options:
            if name == "path":  # save() takes its path first, so here it's a table write's
                raise NotImplementedError(_TABLE_PATH)
            if name in _LAYOUT_OPTIONS:
                print(f"Note: the {name} option only changes file layout and is ignored on this PC.")
            elif name not in allowed:
                raise NotImplementedError(f"The {name} option isn't supported when writing {what} on this PC. "
                                          "Leave it out, or run the cell in Fabric.")

    def _write_mode(self, table: str) -> str:
        if self._mode in ("overwrite", "append"):
            return self._mode
        if self._mode == "ignore" or self._mode in ("error", "errorifexists", "default"):
            exists = lakehouse.table_exists(table)
            if exists and self._mode == "ignore":
                return "skip"
            if exists:
                raise ValueError(f"Table {table} already exists. Use .mode(\"overwrite\") or .mode(\"append\").")
            return "overwrite"
        raise ValueError(f"Unknown save mode {self._mode!r}. Use overwrite, append, ignore or error.")

    def _folder_mode(self, path: str) -> bool:
        """Whether to write files into the folder ``path``, following the save mode as Spark does.
        Error (the default) refuses a path that is already there and ignore leaves it as it is;
        append adds files beside those there, and overwrite replaces what a Spark write left there."""
        exists = os.path.exists(path)
        if self._mode == "overwrite":
            if exists:
                _clear_output(path)
            return True
        if self._mode == "append":
            return True
        if self._mode == "ignore":
            return not exists  # silently, as in Spark
        if self._mode in ("error", "errorifexists", "default"):
            if exists:
                raise ValueError(f"[PATH_ALREADY_EXISTS] {path} already exists. Use .mode(\"overwrite\") to "
                                 "replace it, .mode(\"append\") to add to it, or write to a new path.")
            return True
        raise ValueError(f"Unknown save mode {self._mode!r}. Use overwrite, append, ignore or error.")

    def _schema_mode(self, how: str) -> str | None:
        """Delta's schema rules, as Spark applies them: a different schema is an error unless
        overwriteSchema (overwrite only) or mergeSchema allows it."""
        if _true(self._options.get("overwriteschema")):
            if how == "overwrite":
                return "overwrite"
            print("Note: overwriteSchema only applies to an overwrite; this append keeps the table's schema.")
        return "merge" if _true(self._options.get("mergeschema")) else None

    def _write(self, data: pa.Table, name: str, how: str, schema_mode: str | None) -> None:
        predicate = None
        replace = self._options.get("replacewhere")
        if replace is not None and how != "overwrite":
            print("Note: replaceWhere only applies to an overwrite; this append adds the rows, as Delta does.")
            replace = None
        if replace is not None and not lakehouse.table_exists(name):
            replace = None  # a new table gets every row, as in Delta (there is nothing to replace yet)
        if replace is not None:
            if str(self._options.get("partitionoverwritemode", "")).strip().lower() == "dynamic" \
                    and lakehouse.open_table(name).metadata().partition_columns:  # Delta ignores it unpartitioned
                raise ValueError("[DELTA_REPLACE_WHERE_WITH_DYNAMIC_PARTITION_OVERWRITE] A 'replaceWhere' expression "
                                 "and 'partitionOverwriteMode'='dynamic' cannot both be set in the DataFrameWriter "
                                 "options. Nothing was written.")
            if schema_mode == "overwrite":  # Delta's canOverwriteSchema is false with replaceWhere
                print("Note: overwriteSchema doesn't apply with replaceWhere; the table keeps its schema, as in Delta.")
                schema_mode = "merge" if _true(self._options.get("mergeschema")) else None
            predicate = _replace_where(str(replace), name)
            _rows_match(data, predicate, str(replace), name)
        elif how == "overwrite" and _overwrite_mode(self._df.session, self._options) == "dynamic" \
                and lakehouse.table_exists(name):
            parts = list(lakehouse.open_table(name).metadata().partition_columns)
            if parts and schema_mode == "overwrite":
                raise ValueError("overwriteSchema can't go with a dynamic partition overwrite, which replaces only "
                                 "some partitions. Leave one of them out.")
            if parts:
                missing = [c for c in parts if c.lower() not in {n.lower() for n in data.column_names}]
                if missing:
                    raise ValueError(f"Table {name} is partitioned by {', '.join(parts)}, and the data has no "
                                     f"{', '.join(missing)}, so a dynamic overwrite can't tell which partitions to "
                                     "replace. Nothing was written.")
                predicate = _partitions_predicate(data, parts)
                if predicate is None:
                    print(f"Note: no rows, so the dynamic overwrite replaced no partitions of {name}.")
                    return
        if schema_mode is None:
            current = _columns(name)
            extra = [c for c in data.column_names if c.lower() not in {x.lower() for x in current or []}]
            if current is not None and extra:
                raise ValueError(
                    f"Table {name} has no column {', '.join(extra)}. To replace the table's schema, add "
                    ".option(\"overwriteSchema\", \"true\") to an overwrite; to add the columns, add "
                    ".option(\"mergeSchema\", \"true\").")
        if schema_mode != "overwrite" and lakehouse.table_exists(name):
            _check_types(data, name, how)  # mergeSchema adds columns; it never changes a column's type
        print(_write_table(data, name, how, schema_mode, predicate, replace))

    def saveAsTable(self, name: str, format: str | None = None, mode: str | None = None,  # noqa: A002
                    partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        if mode:
            self.mode(mode)
        if partitionBy:
            self.partitionBy(partitionBy)
        self.options(**options)
        if (format or self._format) not in ("delta", None):
            raise ValueError("Lakehouse tables are Delta tables. Leave out format, or use format(\"delta\").")
        self._table_partitions_note()
        self._check_options(_SCHEMA_OPTIONS + _MODE_OPTIONS + _REPLACE_OPTIONS, "a Delta table")
        how = self._write_mode(name)
        if how != "skip":
            self._write(_arrow(self._df), name, how, self._schema_mode(how))

    def insertInto(self, tableName: str, overwrite: bool | None = None) -> None:  # noqa: N802, N803
        """Spark's insertInto: the table must exist, columns match by position, and it appends
        unless ``overwrite=True`` or ``.mode("overwrite")``. The table's schema never changes."""
        self._check_options(_MODE_OPTIONS, "with insertInto")
        if overwrite is None:
            overwrite = self._mode == "overwrite"
        current = _columns(tableName)
        if current is None:
            raise ValueError(f"Table {tableName} doesn't exist. insertInto writes to an existing table; "
                             "use saveAsTable to create one.")
        data = _arrow(self._df)
        if len(current) != data.num_columns:
            raise ValueError(f"insertInto matches columns by position: the data has {data.num_columns} columns "
                             f"and table {tableName} has {len(current)}.")
        self._write(_store_assign(data.rename_columns(current), tableName), tableName,
                    "overwrite" if overwrite else "append", None)

    def save(self, path: str | None = None, format: str | None = None, mode: str | None = None,  # noqa: A002
             partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        if mode:
            self.mode(mode)
        if partitionBy:
            self.partitionBy(partitionBy)
        self.options(**options)
        given = self._options.pop("path", None)  # .option("path", p).save(), as in Spark
        if given is not None and path is not None:  # Spark refuses both too, rather than pick one
            raise ValueError("There's a path option and a path given to save(). Give the path once.")
        path = given if path is None else path
        if path is not None and (volume := _volume_path(path, write=True)) is not None:
            path = str(volume)  # the volume's folder on this PC (databricks_volumes)
        fmt = format or self._format
        rel = lakehouse.relative_path(path) if path else None
        if fmt == "delta" and rel and rel.startswith("Tables/"):
            parts = rel.split("/")[1:]
            return self.saveAsTable(".".join(p for p in parts if p))
        if rel is not None:
            raise ValueError(f"On this PC, write lakehouse tables with saveAsTable or a Tables/ path. "
                             f"Writing files to {rel} runs in Fabric.")
        if path is None:
            raise ValueError("Give save a path, or write a lakehouse table with saveAsTable.")
        if fmt == "orc":
            raise NotImplementedError("ORC files are written in Fabric; DuckDB on this PC can't write ORC. Write "
                                      "Parquet here (df.write.parquet), or run the cell in Fabric.")
        if fmt not in _FILE_FORMATS:
            raise ValueError(f"Can't write format {fmt!r} to {path} on this PC. Use parquet, csv, json or text.")
        allowed = ("header", "sep", "delimiter") if fmt == "csv" else ()
        self._check_options(allowed, f"{fmt} files")
        if self._folder_mode(path):
            write_files(self._df.relation, path, fmt, getattr(self, "_partition_by", None) or [],
                        header=_true(self._options.get("header", False)),
                        sep=str(self._options.get("sep") or self._options.get("delimiter") or ","))
        return None

    def parquet(self, path: str, mode: str | None = None, partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        self.save(path, format="parquet", mode=mode, partitionBy=partitionBy, **options)

    def csv(self, path: str, mode: str | None = None, header: Any = None, sep: str | None = None,
            partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        if header is not None:
            self.option("header", header)
        if sep is not None:
            self.option("sep", sep)
        self.save(path, format="csv", mode=mode, partitionBy=partitionBy, **options)

    def json(self, path: str, mode: str | None = None, partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        self.save(path, format="json", mode=mode, partitionBy=partitionBy, **options)

    def text(self, path: str, partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        self.save(path, format="text", partitionBy=partitionBy, **options)

    def orc(self, path: str, mode: str | None = None, partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        self.save(path, format="orc", mode=mode, partitionBy=partitionBy, **options)


def _replace_where(condition: str, name: str) -> str:
    """replaceWhere's condition as the predicate delta-rs replaces by (spark_replace_where.predicate)."""
    return spark_replace_where.predicate(condition, name, _columns(name) or [])


_replace_mismatch = spark_replace_where.mismatch
_rows_match = spark_replace_where.check_rows


def _write_table(data: pa.Table, name: str, how: str, schema_mode: str | None, predicate: str | None = None,
                 replace: Any = None) -> Any:
    from deltalake.exceptions import DeltaError, SchemaMismatchError
    try:
        if predicate is not None:
            return lakehouse.write_table(data, name, mode=how, schema_mode=schema_mode, predicate=predicate)
        return lakehouse.write_table(data, name, mode=how, schema_mode=schema_mode)
    except SchemaMismatchError as e:
        raise ValueError(f"The data doesn't match table {name}'s schema ({e}). Nothing was written. To replace "
                         "the schema, add .option(\"overwriteSchema\", \"true\") to an overwrite; to add columns, "
                         "add .option(\"mergeSchema\", \"true\").") from None
    except DeltaError as e:
        if replace is None or "failed validation check" not in str(e):
            raise
        raise _replace_mismatch(str(replace), name) from None


_KINDS = (("a whole number", pa.types.is_integer), ("a decimal", pa.types.is_decimal),
          ("a floating-point number", pa.types.is_floating), ("text", lambda t: pa.types.is_string(t)
          or pa.types.is_large_string(t) or getattr(pa.types, "is_string_view", bool)(t)), ("binary", lambda t: pa.types.is_binary(t)
          or pa.types.is_large_binary(t)), ("true/false", pa.types.is_boolean), ("a date", pa.types.is_date),
          ("a timestamp", pa.types.is_timestamp))


def _kind(t: pa.DataType) -> str | None:
    return next((name for name, test in _KINDS if test(t)), None)


def _check_types(data: pa.Table, table: str, how: str) -> None:
    """Refuse a column whose type would change, as Delta does in Fabric: writing it would cast
    the values silently (1.7 into a whole-number column becomes 1)."""
    have = {f.name.lower(): f.type for f in pa.schema(lakehouse.open_table(table).schema().to_arrow())}
    for field in data.schema:
        old = have.get(field.name.lower())
        new_kind, old_kind = _kind(field.type), old is not None and _kind(old)
        if old is None or pa.types.is_null(field.type) or not new_kind or not old_kind or new_kind == old_kind:
            continue
        fix = ("add .option(\"overwriteSchema\", \"true\") to replace the table's schema, or cast the column"
               if how == "overwrite" else "cast the column to the table's type")
        raise ValueError(f"Column {field.name} is {new_kind} here but {old_kind} in table {table}. Nothing was "
                         f"written. To write it, {fix}.")


_AUTO_MERGE = "spark.databricks.delta.schema.autoMerge.enabled"
_SPARK_SET = re.compile(r"\s*SET\s+(?P<key>spark\.[\w.]+)\s*=\s*(?P<value>[^;]*?)\s*;?\s*", re.I)
_ONE_CHANGE = ("spark.sql runs MERGE INTO, UPDATE and DELETE only as the only statement in the call. "
               "Nothing ran. Call spark.sql once for each statement.")
_CHANGE = re.compile(r"\s*(?:MERGE|UPDATE|DELETE)\b", re.I)
_TIME_ZONE_KEY = "spark.sql.session.timeZone"
_SET_TIME_ZONE = re.compile(r"\s*SET\s+TIME\s+ZONE\s+(?P<zone>'[^']*'|\"[^\"]*\")\s*;?\s*", re.I)
_SPARK_RESET = re.compile(r"\s*RESET(?:\s+(?P<key>spark\.[\w.]+))?\s*;?\s*", re.I)
_SESSION_SETTING = re.compile(r"(?i)\bRESET\b|\bTIME\s+ZONE\b|\bspark\.sql\.session\.timeZone\b")


def _session_setting_pieces(query: str) -> list[str]:
    """``query``'s statements as text, when there are several and one sets (or resets) the session time zone
    (SET TIME ZONE, SET spark.sql.session.timeZone, RESET); else []."""
    if not _SESSION_SETTING.search(query):
        return []
    import sqlglot
    from sqlglot.tokens import TokenType
    try:
        tokens = sqlglot.Dialect.get_or_raise("spark").tokenize(query)
    except Exception:  # noqa: BLE001  # silent-ok: text the tokenizer can't read is parse()'s to report
        return []
    pieces, start = [], 0
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            pieces.append(query[start:token.start])
            start = token.end + 1
    pieces = [p for p in (*pieces, query[start:]) if p.strip()]
    def sets(piece: str) -> bool:
        head = delta_dml_comments.sub("", piece)
        setting = _SPARK_SET.fullmatch(head)
        return bool(_SET_TIME_ZONE.fullmatch(head) or _SPARK_RESET.fullmatch(head)
                    or (setting is not None and setting["key"] == _TIME_ZONE_KEY))
    return pieces if len(pieces) > 1 and any(sets(p) for p in pieces) else []


_SCHEMA_EVOLUTION = re.compile(r"\s*MERGE\s+WITH\s+SCHEMA\s+EVOLUTION\b", re.I)
_SCHEMA_EVOLUTION_WORDS = re.compile(r"\s+WITH\s+SCHEMA\s+EVOLUTION\b", re.I)


def change_runs_here(query: str, lakehouse_name: str | None = None) -> bool:
    """Whether ``query`` is a change this PC runs (PB-015): one MERGE INTO, UPDATE or DELETE of a
    table of the attached lakehouse, named table or schema.table (or with the attached lakehouse's
    name in front), with Preview features on. A change to another lakehouse (a Data mesh template's
    keys MERGE, LH_Keys.keys.customer) still runs in Fabric only. ``lakehouse_name``: the notebook's
    own lakehouse, for a check made with none attached (fabric_run.compute_check)."""
    import sqlglot
    from sqlglot import exp

    from . import delta_dml
    if not lakehouse._setting("show_preview", True) or not delta_dml.looks_like(query):
        return False
    try:
        trees = [t for t in sql_dialects.parse(query, read="spark") if t is not None]
        if len(trees) != 1 or not isinstance(trees[0], (exp.Merge, exp.Update, exp.Delete)):
            return False
        _in_attached(trees[0], lakehouse_name)
        change = delta_dml.parse_tree(trees[0])
    except (ValueError, sqlglot.errors.SqlglotError):
        return False
    return change is not None


# ---- what fabric_run.compute_check reads before a run (#1286)
#
# Every NotImplementedError this module raises, by the function that raises it, and how the check
# behind Runs on finds it in a notebook's code. tests/test_spark_refusals.py fails when a refusal is
# added here without an entry, so Runs on can't say "runs on this PC" for code this PC refuses.
CHECKED_REFUSALS = {
    "_check_options": "an option saveAsTable or insertInto doesn't take, by its literal name (writer_option_refusal)",
    "_preview_on": "change_runs_here, with Preview features off",
    "schema": "read.schema() given a string (READ_SCHEMA_TEXT)",
    "_no_schema": "read.schema() on a read of a table or text files (READ_SCHEMA_ON)",
    "_statement": "sql_refusal: INSERT with PARTITION or REPLACE WHERE",
    "forPath": "DeltaTable.forPath of a literal path outside Tables/ (REFUSED_CODE)",
    "_catalog_call_in_fabric": ("spark.catalog.currentDatabase, refused only with no lakehouse or one with schemas, "
                                "which the check doesn't know, so it isn't flagged"),
    "save": "an ORC file write, df.write.orc or format(\"orc\") (REFUSED_CODE)",
    "_refusal": ("a CSV, JSON or Parquet read option this PC doesn't take, a non-UTF-8 encoding, a CSV mode "
                 "without a schema, a JSON mode it has no match for, or a Parquet rebase mode other than CORRECTED "
                 "(read_option_refusal)"),
}

_IN_FABRIC_TEXT = "{}, which runs in Fabric (this PC's Spark refuses it)"
# Code this PC's Spark refuses, as (pattern on a Python cell's text, reason).
REFUSED_CODE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"""\bDeltaTable\.forPath\(\s*[^,()]+,\s*[rRuU]?["'](?![^"']*Tables/)"""),
     "opens DeltaTable.forPath outside the lakehouse's Tables/, which this PC's Spark refuses (use DeltaTable.forName)"),
    (re.compile(r"""\.write\b[^\n]*?\.(?:orc\(|format\(\s*["']orc["']\s*\))"""),
     "writes an ORC file, which this PC's Spark refuses (write Parquet, or run the cell in Fabric)"),
    (re.compile(r"\binput_file_name\s*\("),
     "calls input_file_name(), which this PC's Spark refuses: it doesn't track the file each row came from"),
    (re.compile(r"\.applyInPandasWithState\s*\("),
     "calls applyInPandasWithState, which this PC's Spark refuses: state across a stream's micro-batches runs in Fabric"),
]
# DeltaTable maintenance (spark_maint, Preview): runs on this PC only with Preview features on (_preview_on).
DELTA_MAINTENANCE = ("restoreToVersion", "restoreToTimestamp", "vacuum", "optimize")
MAINTENANCE_PREVIEW_TEXT = "runs {}, which this PC runs only with Preview features on"
READ_SCHEMA_TEXT = "gives read.schema() a DDL string, which this PC's Spark refuses (give it a StructType)"
READ_SCHEMA_ON = "applies read.schema() to a read of {}, which this PC's Spark refuses (leave it out and cast the columns after)"
READ_FORMATS = ("delta", "csv", "json", "parquet", "text")
READ_FORMAT_TEXT = "reads format {!r}, which this PC's Spark doesn't have (it reads csv, json, parquet, text and delta)"
TABLE_FORMAT_TEXT = "saves a lakehouse table as {!r}, which this PC's Spark refuses: lakehouse tables are Delta tables"
# The options each table write takes on this PC (_check_options), by lower-case name.
WRITE_TAKES = {"saveAsTable": _SCHEMA_OPTIONS + _LAYOUT_OPTIONS + _MODE_OPTIONS + _REPLACE_OPTIONS, "insertInto": _LAYOUT_OPTIONS + _MODE_OPTIONS}


def writer_option_refusal(name: str, terminal: str | None) -> str | None:
    """Why this PC's writer refuses option ``name`` on a write that ends in ``terminal`` (saveAsTable,
    insertInto, or None when the check can't tell), or None when it takes it."""
    key = name.lower()
    takes = WRITE_TAKES.get(terminal or "")
    if takes is not None and key == "path":
        return f"uses the {name} write option on {terminal}: writing a table to its own path (an external table) runs in Fabric"
    if takes is not None and key not in takes:
        return f"uses the {name} write option, which {terminal} doesn't take on this PC"
    return None


def sql_refusal(query: str) -> tuple[str, str] | None:
    """Why this PC's spark.sql refuses ``query``, as ("blocked" or "unknown", reason), or None when
    nothing in its text is refused. A DROP TABLE or TRUNCATE TABLE of a name with no schema is
    "unknown": it runs here for a temp view and in Fabric for a lakehouse table."""
    import sqlglot
    from sqlglot import exp

    head = delta_dml_comments.sub("", query)
    from . import spark_maint
    if (maint := spark_maint.STARTS.match(head)) is not None:
        if not lakehouse._setting("show_preview", True):
            return "blocked", MAINTENANCE_PREVIEW_TEXT.format(maint.group(1).upper())
        try:
            spark_maint.statement(head)
        except ValueError as exc:
            return "blocked", f"runs {str(exc).split('. ')[0]}, which this PC's Spark refuses otherwise"
        return None
    try:
        trees = [t for t in sql_dialects.parse(query, read="spark") if t is not None]
    except sqlglot.errors.SqlglotError:
        return None
    if len(trees) > 1 and any(isinstance(t, (exp.Merge, exp.Update, exp.Delete)) for t in trees):
        return "blocked", ("runs MERGE, UPDATE or DELETE with other statements in one spark.sql call, which this "
                           "PC's Spark refuses (give each statement its own call)")
    for tree in trees:
        if isinstance(tree, exp.Insert) and (tree.find(exp.Partition) or tree.args.get("where")):
            return "blocked", ("inserts with PARTITION or REPLACE WHERE, which this PC's Spark refuses: ignoring it "
                               "would replace the whole table")
        truncate = isinstance(tree, exp.TruncateTable)
        if truncate or (isinstance(tree, exp.Drop) and str(tree.args.get("kind", "")).upper() == "TABLE"):
            tables = tree.expressions if truncate else tree.args.get("tables") or []
            what = "empties" if truncate else "drops"
            word = "TRUNCATE TABLE" if truncate else "DROP TABLE"
            named = [".".join(p for p in (t.db, t.name) if p) for t in tables if isinstance(t, exp.Table)]
            if any(t.db for t in tables if isinstance(t, exp.Table)):
                return "blocked", _IN_FABRIC_TEXT.format(f"{what} the lakehouse table {', '.join(named)} ({word})")
            if named:
                return "unknown", (f"{what} {', '.join(named)} ({word}), which runs here for a temp view and in "
                                   "Fabric for a lakehouse table")
    return None


def _in_attached(tree: Any, name: str | None = None) -> None:
    """Drop the attached lakehouse's own name from a MERGE's, UPDATE's or DELETE's target (Sales.dbo.sales, or Sales.sales
    in a lakehouse without schemas), as table_ref does for reads and writes. ``name``, with nothing
    attached: the lakehouse to take as attached, whose schema setting isn't known, so only its
    three-part form (name.schema.table) is recognised."""
    from sqlglot import exp
    current, target = lakehouse._attached, tree.this
    if current is not None:
        name = current.name
    if not name or not isinstance(target, exp.Table):
        return
    if target.catalog and target.catalog.lower() == name.lower():
        target.set("catalog", None)
    elif current is not None and not target.catalog and target.db and target.db.lower() == name.lower() \
            and not current.schemas_enabled():
        target.set("db", None)


def _use(statement: Any) -> None:
    """USE CATALOG, USE SCHEMA, USE DATABASE and USE name. On this PC a table name resolves in the attached
    lakehouse (its dbo schema when it has schemas), and that can't be changed. So USE runs only when it
    names where names already resolve, and changes nothing; any other catalog or schema is refused by
    name, so an unqualified table is never read from or written to a place the cell didn't mean."""
    kind = str(statement.args.get("kind") or "").upper()
    target = statement.this
    parts = [p for p in (target.catalog, target.db, target.name) if p] if hasattr(target, "catalog") else [target.name]
    _use_parts(kind, parts)


def _use_parts(kind: str, parts: list[str], call: str = "") -> None:
    """_use for ``parts`` (catalog and schema names); ``call`` names the call in the message when it isn't
    USE itself (spark.catalog.setCurrentCatalog('main'))."""
    lh = lakehouse._attached
    home = lh.name.lower() if lh is not None else None
    catalogs = {"spark_catalog", *([home] if home else [])}
    schemas = {"dbo", *([home] if home else [])}  # USE Sales: Fabric's database is the lakehouse
    if kind == "CATALOG":
        ok = len(parts) == 1 and parts[0].lower() in catalogs
    else:
        *catalog, schema = parts
        ok = len(parts) <= 2 and schema.lower() in schemas and all(c.lower() in catalogs for c in catalog)
    if ok:
        return
    named = ".".join(parts)
    where = f"the attached lakehouse ({lh.name})" if lh is not None else "the attached lakehouse"
    said = call or f"USE {kind + ' ' if kind else ''}{named}"
    if kind == "CATALOG" or len(parts) == 2 and parts[0].lower() not in catalogs:
        raise ValueError(f"{said}: the catalog {parts[0]} (such as a Unity Catalog "
                         f"catalog) isn't on this PC, so nothing changed. Tables here are in {where}. "
                         "Run the cell where that catalog is, or name tables as table or schema.table.")
    raise ValueError(f"{said}: on this PC an unqualified table name always resolves "
                     f"in {where}, schema dbo, so USE can't point it at {parts[-1]} and nothing changed. "
                     f"Name the schema in each table instead ({parts[-1]}.table).")


class _CatalogMetadata(NamedTuple):
    name: str
    description: str | None


def _list_dirs(directory: str) -> list[dict[str, Any]]:
    """The folders in a lakehouse folder (tables under Tables/, or schemas), not _delta_log or other files."""
    lh = lakehouse._attached
    if lh is None:
        return []
    from .onelake import FileStore
    entries = FileStore(lh.storage_token).list(lh.workspace_id, lh.lakehouse_id, directory)
    return sorted((e for e in entries if e.get("is_dir") and not e["name"].startswith(("_", "."))), key=lambda e: e["name"].lower())


def _catalog_call_in_fabric(call: str, why: str) -> NoReturn:
    raise NotImplementedError(f"spark.catalog.{call} runs in Fabric. {why}")


def _catalog_calls(session: LocalSparkSession) -> dict[str, Any]:
    """spark.catalog on this PC. DuckDB's Catalog answers about its own connection (its databases memory,
    system and temp; its own tables), which isn't what a notebook asks, so each call answers about the
    attached lakehouse and this session's temp views (currentDatabase is refused when it can't say)."""
    from duckdb.experimental.spark.sql.catalog import Column

    def list_columns(tableName: str, dbName: str | None = None) -> list[Column]:  # noqa: N803
        name = f"{dbName}.{tableName}" if dbName else tableName
        frame = session.table(name)
        # A lakehouse table's partition columns are in its Delta log (a table Fabric wrote partitioned); a
        # temp view has none.
        local = "." not in name and session._is_local(name)
        parts = set() if local else {c.lower() for c in lakehouse.open_table(name).metadata().partition_columns}
        return [Column(name=f.name, description=None, dataType=f.dataType.simpleString(), nullable=f.nullable,
                       isPartition=f.name.lower() in parts, isBucket=False) for f in frame.schema.fields]

    def current_database() -> str:
        lh = lakehouse._attached
        if lh is None or lh.schemas_enabled():
            _catalog_call_in_fabric("currentDatabase", "On this PC unqualified table names resolve in the attached "
                                                       "lakehouse, schema dbo.")
        return lh.name  # Fabric's database is the default lakehouse

    def set_catalog(catalogName: str) -> None:  # noqa: N803
        _use_parts("CATALOG", [catalogName], f"spark.catalog.setCurrentCatalog({catalogName!r})")

    def set_database(dbName: str) -> None:  # noqa: N803
        _use_parts("SCHEMA", lakehouse.name_parts(dbName), f"spark.catalog.setCurrentDatabase({dbName!r})")

    from duckdb.experimental.spark.sql.catalog import Database, Function, Table

    def temp_views() -> list[str]:
        """This session's temp views, not the views the session makes for lakehouse tables or its own work."""
        own = {v.lower() for v in session._bound().values()}
        rows = session.conn.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal ORDER BY view_name").fetchall()
        return [name for (name,) in rows if name.lower() not in own and not name.startswith(("__kf_", "fd_", "_fd_"))]

    def lakehouse_tables(schema: str | None) -> list[tuple[str, str]]:
        """(schema or lakehouse name, table) for each Delta table in the attached lakehouse's Tables folder."""
        lh = lakehouse._attached
        if lh is None:
            return []
        out = []
        if lh.schemas_enabled():
            schemas = [schema] if schema else [e["name"] for e in _list_dirs("Tables")]
            for sch in schemas:
                out += [(sch, e["name"]) for e in _list_dirs(f"Tables/{sch}")]
        else:
            if schema and schema.lower() != lh.name.lower():
                return []
            out = [(lh.name, e["name"]) for e in _list_dirs("Tables")]
        return out

    def matches(name: str, pattern: str | None) -> bool:
        """Spark's catalog pattern: * is any text, | separates alternatives, case-insensitive."""
        if not pattern:
            return True
        # Only * is special; anything else (a "(" or "+") is literal text, so no pattern can fail to compile.
        return any(re.fullmatch(".*".join(re.escape(part) for part in p.strip().split("*")), name, re.I)
                   for p in str(pattern).split("|"))

    def list_tables(dbName: str | None = None, pattern: str | None = None) -> list[Table]:  # noqa: N803
        found = [Table(name=t, database=db, description=None, tableType="MANAGED", isTemporary=False)
                 for db, t in lakehouse_tables(dbName)]
        if dbName is None or dbName.lower() == spark_api.GLOBAL_TEMP:
            prefix = spark_api.GLOBAL_PREFIX
            rows = session.conn.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
            if dbName is not None:
                found = []
            found += [Table(name=n[len(prefix):], database=spark_api.GLOBAL_TEMP, description=None,
                            tableType="TEMPORARY", isTemporary=True) for (n,) in sorted(rows) if n.startswith(prefix)]
        if dbName is None:
            found += [Table(name=v, database=None, description=None, tableType="TEMPORARY", isTemporary=True)
                      for v in temp_views()]
        return [t for t in found if matches(t.name, pattern)]

    def list_databases(pattern: str | None = None) -> list[Database]:
        lh = lakehouse._attached
        if lh is None:
            return []
        names = [e["name"] for e in _list_dirs("Tables")] if lh.schemas_enabled() else [lh.name]
        return [Database(name=n, description=None, locationUri=f"Tables/{n}" if lh.schemas_enabled() else "Tables")
                for n in names if matches(n, pattern)]

    def list_functions(dbName: str | None = None, pattern: str | None = None) -> list[Function]:  # noqa: N803, ARG001
        from .spark_coverage import load
        names = {f["name"] for f in load()}
        out = [Function(name=n, description=None, className="", isTemporary=True) for n in sorted(names)]
        # This session's Python UDFs, once spark_udf is loaded (it registers them).
        udfs = getattr(sys.modules.get(f"{__package__}.spark_udf"), "_registered", {})
        out += [Function(name=n, description=None, className="python", isTemporary=True) for n in sorted(udfs)]
        return [f for f in out if matches(f.name, pattern)]

    return {
        "tableExists": lambda tableName, dbName=None: spark_api.table_exists(session, tableName, dbName),  # noqa: N803
        "dropTempView": lambda viewName: spark_api.drop_view(session, viewName),  # noqa: N803
        "dropGlobalTempView": lambda viewName: spark_api.drop_view(session, spark_api.GLOBAL_PREFIX + viewName),  # noqa: N803
        "listColumns": list_columns,
        "currentCatalog": lambda: "spark_catalog",
        "setCurrentCatalog": set_catalog,
        "setCurrentDatabase": set_database,
        "listTables": list_tables,
        "listDatabases": list_databases,
        "listCatalogs": lambda pattern=None: [c for c in [_CatalogMetadata("spark_catalog", None)] if matches(c.name, pattern)],
        "listFunctions": list_functions,
        "currentDatabase": current_database,
    }


def _auto_merge_on(session: Any) -> bool:
    """Whether spark.databricks.delta.schema.autoMerge.enabled is on: then a MERGE adds the source's new
    columns to the table, as WITH SCHEMA EVOLUTION does."""
    try:
        value = session.conf.get(_AUTO_MERGE, "false")
    except (KeyError, NotImplementedError):  # a conf that can't answer has nothing set
        return False
    return str(value).strip().lower() == "true"


def _preview_on(what: str) -> None:
    """Changing a table in place on this PC is in Preview (PB-015): with Preview features off
    (Settings, or the organization's policy) it runs in Fabric only, as before."""
    if not lakehouse._setting("show_preview", True):
        raise NotImplementedError(f"{what} on this PC is a Preview feature, and Preview features are off in Settings "
                                  "or by your organization's policy. Run the cell in Fabric.")


def _store_assign(data: pa.Table, table: str) -> pa.Table:
    """INSERT and insertInto cast their values to the table's column types where Spark's ANSI store
    assignment does (a number into another number, anything into text, a date into a timestamp: the
    literal 1.0 is a decimal, and goes into a double column), and only when every value comes through
    the cast as it was. Any other column is left as it is, for write_table to refuse (EA-001): '5'
    into a whole-number column, true into one, 2.75 into one, 1.1 into a float column."""
    target = {f.name.lower(): f for f in pa.schema(lakehouse.open_table(table).schema().to_arrow())}
    columns = []
    for name, column in zip(data.column_names, data.columns, strict=True):
        field = target.get(name.lower())
        if field is None or column.type.equals(field.type) or not _store_assigns(column.type, field.type):
            columns.append(column)
            continue
        try:
            cast = column.cast(field.type, safe=True)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise ValueError(f"Inserting into {table} would change values in {name} to fit its type "
                             f"({field.type}): {exc}. Nothing was written. Cast the column first.") from None
        if not _same_values(column, cast):
            raise ValueError(f"Inserting into {table} would change values in {name} to fit its type "
                             f"({field.type}). Nothing was written. Cast the column first.")
        columns.append(cast)
    return pa.table(columns, names=data.column_names)


def _numeric_type(t: pa.DataType) -> bool:
    return pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)


def _store_assigns(src: pa.DataType, dst: pa.DataType) -> bool:
    """The casts Spark's ANSI store assignment makes (Cast.canANSIStoreAssign), for top-level columns."""
    if pa.types.is_null(src):
        return False  # write_table gives an all-NULL column the table's type
    if _numeric_type(src) and _numeric_type(dst):
        return True
    if pa.types.is_string(dst) or pa.types.is_large_string(dst):
        return True
    return pa.types.is_date(src) and pa.types.is_timestamp(dst)


def _same_values(before: Any, after: Any) -> bool:
    """Whether a cast kept every value. A safe cast refuses an overflow and a truncated fraction,
    but still rounds a number into a float (1.1 into a float, 2**53 + 1 into a double) and a float
    into a decimal, so those go back to the source type and are compared."""
    if not (pa.types.is_floating(after.type) or (pa.types.is_floating(before.type) and pa.types.is_decimal(after.type))):
        return True
    import pyarrow.compute as pc
    try:
        back = after.cast(before.type, safe=False)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return False
    same = pc.equal(back, before)
    if pa.types.is_floating(before.type):  # NaN is NaN after the cast, though it equals nothing
        same = pc.or_(same, pc.and_(pc.is_nan(back), pc.is_nan(before)))
    return pc.all(pc.or_kleene(same, pc.is_null(before))).as_py() is not False


def _columns(table: str) -> list[str] | None:
    """The table's column names, or None when it doesn't exist."""
    from deltalake.exceptions import TableNotFoundError
    try:
        return [f.name for f in lakehouse.open_table(table).schema().fields]
    except TableNotFoundError:
        return None


# Spark's CSV read options this PC honours (LakehouseReader.csv), by lower-case name. multiLine: a quoted
# value may span lines, which DuckDB always reads. Options of other
# readers and of time travel (_READ_OPTIONS) are let through, since one reader may be reused.
_CSV_OPTION_NAMES = ("header", "sep", "delimiter", "inferSchema", "nullValue", "quote", "escape", "comment",
                     "encoding", "charset", "mode", "multiLine")
_CSV_OPTIONS = {n.lower() for n in _CSV_OPTION_NAMES}
_READ_OPTIONS = {"versionasof", "timestampasof"}
_WHOLE_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT")


# Spark's JSON and Parquet read options this PC honours (LakehouseReader.json and .parquet), by lower-case
# name (#1669). JSON: multiLine picks JSON Lines (false) or one document per file (true); mode PERMISSIVE
# reads a malformed line as a row of nulls and FAILFAST stops the read, as in Spark. Parquet: mergeSchema
# reads every file's columns by name; the rebase modes are taken only as CORRECTED, which is how this PC
# reads old dates.
_JSON_OPTION_NAMES = ("multiLine", "mode", "encoding", "charset")
_PARQUET_OPTION_NAMES = ("mergeSchema", "datetimeRebaseMode", "int96RebaseMode")
_TAKES = {"csv": _CSV_OPTION_NAMES, "json": _JSON_OPTION_NAMES, "parquet": _PARQUET_OPTION_NAMES}


def _utf8(value: Any) -> bool:
    return str(value).lower().replace("-", "").replace("_", "") == "utf8"


def read_option_refusal(fmt: str, options: list[tuple[str, Any]], has_schema: bool = False) -> str | None:
    """Why this PC's ``fmt`` reader (csv, json or parquet) refuses ``options`` (name, value; None when the
    value isn't known before the run), as a phrase after "This PC's Spark", or None when it takes them
    all. Each option is honoured as Spark does or refused here, never dropped (EA-013, #1669). The
    reader and the check behind Runs on (fabric_run) both use it."""
    takes = _TAKES[fmt]
    known = {n.lower() for n in takes}
    label = {"csv": "CSV", "json": "JSON", "parquet": "Parquet"}[fmt]
    for name, value in options:
        key = str(name).lower()
        if key not in known and key not in _READ_OPTIONS and key != "path":  # load() takes the path option
            return f"doesn't take the {label} option {name!r} (it takes {', '.join(takes)})"
        if key in ("encoding", "charset") and value is not None and not _utf8(value):
            return f"reads {label} files as UTF-8 only, not {value} (save the file as UTF-8 first)"
        if key == "mode" and value is not None:
            mode = str(value).upper()
            if mode not in ("PERMISSIVE", "FAILFAST", "DROPMALFORMED"):
                return f"has no {label} mode {value!r} (Spark's are PERMISSIVE, DROPMALFORMED and FAILFAST)"
            if fmt == "csv" and mode != "PERMISSIVE" and not has_schema:
                return f"needs read.schema() for CSV mode {mode}, to say what a malformed value is"
            if fmt == "json" and mode == "DROPMALFORMED":
                return ("can't tell a malformed JSON line from a line of nulls, so it doesn't take mode DROPMALFORMED "
                        "(use PERMISSIVE, where a malformed line is a row of nulls, or FAILFAST)")
        if key in ("datetimerebasemode", "int96rebasemode") and value is not None and str(value).upper() != "CORRECTED":
            return (f"reads Parquet dates and times as they are written (CORRECTED), so it doesn't take {name} "
                    f"{value!r}")
    if fmt == "json":
        given = {str(n).lower(): v for n, v in options}
        if str(given.get("mode") or "").upper() == "PERMISSIVE" and _true(given.get("multiline", False)):
            return ("reads a multiLine JSON file that is malformed by stopping, so it doesn't take mode PERMISSIVE "
                    "with multiLine (use FAILFAST, or leave mode out)")
    return None


def _volume_path(path: Any, write: bool = False) -> Any:
    """The file or folder on this PC that a /Volumes path names (a Unity Catalog volume here, databricks_volumes),
    or None for any other path. A read of a folder holding a link is refused (what it points at may be outside the
    volume), and so is a read of a path that isn't there, as Spark's PATH_NOT_FOUND."""
    from . import databricks_volumes
    if not databricks_volumes.is_volume_path(path):
        return None
    local = databricks_volumes.local_path(str(path))
    shown = databricks_volumes.parse(str(path)).uc_path()
    if not write and not local.exists():
        raise FileNotFoundError(f"[PATH_NOT_FOUND] Path does not exist: {shown}.")
    if local.is_dir():
        databricks_volumes.check_tree(local, shown)
    return local


class LakehouseReader:
    """``spark.read``: tables and files of the attached lakehouse, files on this PC, or a /Volumes path."""

    def __init__(self, session: LocalSparkSession):
        self._session = session
        self._format: str | None = None
        self._options: dict[str, Any] = {}
        self._names: dict[str, str] = {}  # each option's name as the notebook wrote it, for messages

    def format(self, fmt: str) -> LakehouseReader:
        self._format = fmt.lower()
        return self

    def option(self, key: str, value: Any) -> LakehouseReader:
        self._options[str(key).lower()] = value
        self._names[str(key).lower()] = str(key)
        return self

    def _refusal(self, fmt: str) -> None:
        """Raise when the ``fmt`` reader refuses one of the options given (read_option_refusal)."""
        named = [(self._names.get(k, k), v) for k, v in self._options.items()]
        refused = read_option_refusal(fmt, named, getattr(self, "_schema", None) is not None)
        if refused:
            raise NotImplementedError(f"This PC's Spark {refused}, so the file wasn't read.")

    def options(self, **opts: Any) -> LakehouseReader:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def schema(self, schema: Any) -> LakehouseReader:
        """Applied after reading: CSV columns take the schema's names and types by position, other
        formats by name (a missing column is null). A value that doesn't fit is null, as in Spark's
        default PERMISSIVE mode."""
        if isinstance(schema, str):
            schema = ddl_schema(schema)  # "id INT, name STRING", as Spark takes it
        if not hasattr(schema, "fields"):
            raise NotImplementedError("On this PC, give read.schema() a StructType or a DDL string such as "
                                      "'id INT, name STRING'.")
        self._schema = schema
        return self

    def _apply(self, df: DataFrame, by_position: bool, mode: str = "PERMISSIVE") -> DataFrame:
        schema = getattr(self, "_schema", None)
        if schema is None:
            return df
        # A limit no file reaches keeps DuckDB 2.0 from pushing the TRY_CASTs below into the file scan,
        # where a value that doesn't fit fails the read instead of being NULL (#1341).
        rel = df.relation.limit(2**62)
        have = rel.columns
        if by_position and len(have) != len(schema.fields):
            raise ValueError(f"The schema has {len(schema.fields)} columns and the file has {len(have)}.")
        lower = {c.lower(): c for c in have}
        text_cols = {c for c, t in zip(have, rel.types, strict=True) if str(t) == "VARCHAR"}
        cols, fits = [], []
        for i, f in enumerate(schema.fields):
            src = have[i] if by_position else lower.get(f.name.lower())
            value = "NULL" if src is None else '"' + src.replace('"', '""') + '"'
            target = str(f.dataType.duckdb_type)
            read = value
            if src in text_cols and target in _WHOLE_TYPES:  # text that isn't a whole number doesn't fit
                read = f"CASE WHEN regexp_matches({value}, '^[+-]?[0-9]+$') THEN {value} END"  # (DuckDB rounds 3.5)
            cast = f"TRY_CAST({read} AS {target})"
            fit = f"({value} IS NULL OR {cast} IS NOT NULL)"
            fits.append(fit)
            if mode == "FAILFAST":  # a value that doesn't fit stops the read, as Spark's FAILFAST does
                cast = (f"CASE WHEN {fit} THEN {cast} ELSE ERROR('Malformed CSV value ' || left(CAST({value} AS "
                        f"VARCHAR), 100) || ' for column {f.name.replace(chr(39), chr(39) * 2)} ({target}), and "
                        "mode is FAILFAST.') END")
            cols.append(f'{cast} AS "' + f.name.replace('"', '""') + '"')
        if mode == "DROPMALFORMED":  # rows with a value that doesn't fit are left out
            rel = rel.filter(" AND ".join(fits))
        return DataFrame(rel.project(", ".join(cols)), self._session)

    def _no_schema(self, what: str) -> None:
        if getattr(self, "_schema", None) is not None:
            raise NotImplementedError(f"read.schema() can't be applied to {what} on this PC. Leave it out and cast "
                                      "the columns after reading.")

    def _as_of(self) -> dict[str, Any]:
        """versionAsOf / timestampAsOf: Delta time travel, which only reads."""
        point = {"version": self._options.get("versionasof"), "timestamp": self._options.get("timestampasof")}
        return {k: v for k, v in point.items() if v is not None}

    def table(self, name: str) -> DataFrame:
        self._no_schema("a table")
        from . import delta_cdf
        if delta_cdf.asked(self._options):  # readChangeFeed: the table's changes, never the table itself
            return self._session._from_arrow_dataset(delta_cdf.read(lakehouse.open_table(name), name, self._options,
                                                                    self._session._instant))
        if self._as_of():
            data = lakehouse.table_dataset(lakehouse.open_table(name, **self._as_of()))
            return self._session._from_arrow_dataset(data)
        return self._session.table(name)

    def _local(self, path: str) -> str | list[str]:
        """A local path for ``path``; a folder is its data files, as Spark reads one: every file below
        it except those (and folders) whose names start with _ or . (_SUCCESS, .crc files)."""
        local = str(_volume_path(path) or lakehouse.local_path(path))
        if not os.path.isdir(local):
            return local
        self._root = local
        found = []
        for root, dirs, files in os.walk(local):
            dirs[:] = sorted(d for d in dirs if not d.startswith(("_", ".")))
            found += [os.path.join(root, f) for f in sorted(files) if not f.startswith(("_", "."))]
        if not found:
            raise _NoDataFiles(f"[UNABLE_TO_INFER_SCHEMA] {path} is a folder with no data files, so there's no "
                             "schema to read. Write data there first, or give a file.")
        return found

    def _empty(self, exc: "_NoDataFiles") -> DataFrame:
        """A folder with no data files read with a schema: no rows, with the schema's columns, as in Spark.
        Without a schema there is nothing to infer, so the error stands."""
        schema = getattr(self, "_schema", None)
        if schema is None:
            raise ValueError(str(exc)) from None  # the error, not this module's private type for it
        return self._session.createDataFrame([], schema)

    def _hive(self, files: str | list[str]) -> dict[str, Any]:
        """Partition columns from the name=value folders below the folder read, as Spark reads a
        folder written with partitionBy; reading one partition's folder (name=x) gives no such column."""
        root = getattr(self, "_root", None)
        if not isinstance(files, list) or root is None:
            return {"hive_partitioning": False}
        below = any("=" in part for f in files for part in os.path.relpath(os.path.dirname(f), root).split(os.sep))
        return {"hive_partitioning": below}

    def load(self, path: str | None = None, format: str | None = None, **opts: Any) -> DataFrame:  # noqa: A002
        if opts.get("schema") is not None:  # load(path, schema=s) is read.schema(s), as in Spark
            self.schema(opts["schema"])
        opts.pop("schema", None)
        self.options(**opts)
        fmt = (format or self._format or "parquet").lower()
        given = self._options.pop("path", None)  # .option("path", p).load(), as in Spark
        if given is not None and path is not None:  # Spark refuses both too, rather than pick one
            raise ValueError("There's a path option and a path given to load(). Give the path once.")
        path = given if path is None else path
        if path is None:
            raise ValueError("Give load a path: a Tables/ or Files/ path, or a file or folder on this PC.")
        rel = lakehouse.relative_path(path) if path else None
        if fmt == "delta":
            self._no_schema("a Delta table")
            if rel and rel.startswith("Tables/"):
                return self.table(".".join(p for p in rel.split("/")[1:] if p))
            from deltalake import DeltaTable
            opened = DeltaTable(str(_volume_path(path) or path))
            from . import delta_cdf
            if delta_cdf.asked(self._options):
                return self._session._from_arrow_dataset(delta_cdf.read(opened, path, self._options,
                                                                        self._session._instant))
            point = lakehouse.as_of(**self._as_of())
            if point is not None:
                opened.load_as_version(point)
            return self._session._from_arrow_dataset(lakehouse.table_dataset(opened))
        if fmt == "csv":
            return self.csv(path)
        if fmt == "json":
            return self.json(path)
        if fmt == "parquet":
            return self.parquet(path)
        if fmt == "text":
            return self.text(path)
        raise ValueError(f"Format {fmt!r} isn't supported on this PC. Use csv, json, parquet, text or delta.")

    def csv(self, path: str, header: Any = None, sep: str | None = None, inferSchema: Any = None,  # noqa: N803
            **opts: Any) -> DataFrame:
        """Spark's CSV options (EA-013): each is honoured, or refused with a message, never dropped.
        As in Spark, every column is a string unless inferSchema is true or a schema is given."""
        if header is not None:
            opts.setdefault("header", header)
        if sep is not None:
            opts.setdefault("sep", sep)
        if inferSchema is not None:
            opts.setdefault("inferSchema", inferSchema)
        if opts.get("schema") is not None:  # csv(path, schema=s) is read.schema(s), as in Spark
            self.schema(opts["schema"])
        opts.pop("schema", None)
        self.options(**{k: v for k, v in opts.items() if v is not None})
        given = self._options
        self._refusal("csv")
        # A comma unless sep or delimiter says otherwise, as in Spark: DuckDB would guess one from the file.
        # skiprows 0: Spark never leaves out a line, where DuckDB's sniffer drops the first lines of a file
        # when they don't look like the rest (a title, or a first row with fewer columns), with no error.
        extra: dict[str, Any] = {"sep": str(given.get("sep") or given.get("delimiter") or ","), "skiprows": 0}
        if not _true(given.get("inferschema", False)) or getattr(self, "_schema", None):
            extra["all_varchar"] = True
        if given.get("nullvalue") not in (None, ""):
            extra["na_values"] = [str(given["nullvalue"]), ""]
        for name, arg in (("quote", "quotechar"), ("escape", "escapechar"), ("comment", "comment")):
            if given.get(name) not in (None, ""):
                extra[arg] = str(given[name])
        mode = str(given.get("mode", "PERMISSIVE")).upper()
        header = _true(given.get("header", False))
        try:
            files = self._local(path)
        except _NoDataFiles as exc:
            return self._empty(exc)
        rel = self._session.conn.read_csv(files, header=header, **extra, **self._hive(files))  # type: ignore[arg-type]  # a list of files too
        if not header:  # Spark names a file's columns _c0, _c1... (DuckDB: column0, column1...)
            rel = rel.project(", ".join(f'"{c.replace(chr(34), chr(34) * 2)}" AS "_c{i}"' for i, c in enumerate(rel.columns)))
        return self._apply(DataFrame(rel, self._session), by_position=True, mode=mode)

    def _given(self, fmt: str, opts: dict[str, Any]) -> dict[str, Any]:
        """The reader's options with ``opts`` (keyword arguments to json or parquet) added, refused as
        read_option_refusal says. A ``schema`` keyword is read.schema(), as in Spark."""
        if opts.get("schema") is not None:
            self.schema(opts.pop("schema"))
        opts.pop("schema", None)
        self.options(**{k: v for k, v in opts.items() if v is not None})
        self._refusal(fmt)
        return self._options

    def parquet(self, *paths: str, **opts: Any) -> DataFrame:
        """Spark's Parquet options (#1669): each is honoured, or refused with a message, never dropped."""
        given = self._given("parquet", opts)
        if not paths:
            raise ValueError("Give parquet a path: a Tables/ or Files/ path, or a file or folder on this PC.")
        files: list[str] = []
        for p in paths:
            try:
                local = self._local(p)
            except _NoDataFiles as exc:
                if len(paths) == 1:
                    return self._empty(exc)
                raise
            files += local if isinstance(local, list) else [local]
        rel = self._session.conn.read_parquet(files if len(files) > 1 else files[0],
                                              union_by_name=_true(given.get("mergeschema", False)),
                                              **self._hive(files))
        return self._apply(DataFrame(rel, self._session), False)

    def json(self, path: str, **opts: Any) -> DataFrame:
        """Spark's JSON options (#1669): each is honoured, or refused with a message, never dropped.
        Without multiLine this PC reads JSON Lines and also a file that is one document, which Spark
        reads only with multiLine true."""
        given = self._given("json", opts)
        extra: dict[str, Any] = {}
        if "multiline" in given:
            extra["format"] = "auto" if _true(given["multiline"]) else "newline_delimited"
        if str(given.get("mode") or "").upper() == "PERMISSIVE":
            # A malformed line is a row of nulls, as in Spark (which also has no _corrupt_record column
            # unless the schema asks for one, and this PC refuses that option).
            extra.update(format="newline_delimited", ignore_errors=True)
        try:
            files = self._local(path)
        except _NoDataFiles as exc:
            return self._empty(exc)
        rel = self._session.conn.read_json(files, **extra, **self._hive(files))  # type: ignore[arg-type]  # a list of files too
        return self._apply(DataFrame(rel, self._session), False)

    def text(self, path: str, **_opts: Any) -> DataFrame:
        """Each line as a row of one string column, value (or the one string field read.schema() names,
        as in Spark)."""
        name = "value"
        schema = getattr(self, "_schema", None)
        if schema is not None:
            fields = list(getattr(schema, "fields", []))
            if len(fields) != 1 or type(fields[0].dataType).__name__ != "StringType":
                raise ValueError("A text file's schema is one string column, such as StructType([StructField("
                                 "\"value\", StringType())]).")
            name = fields[0].name
        conn = self._session.conn
        try:
            local = self._local(path)
        except _NoDataFiles as exc:
            return self._empty(exc)
        rel = conn.sql("SELECT line AS \"" + name.replace('"', '""') + "\" FROM read_csv(?, columns={'line': 'VARCHAR'}, "
                       "header=false, delim=chr(1), quote='', escape='')", params=[local])
        return DataFrame(rel, self._session)


class LocalSparkSession(SparkSession):
    """DuckDB's SparkSession, reading and writing the attached lakehouse."""

    def __init__(self) -> None:
        super().__init__(SparkContext("local"))
        self.conn.execute("SET TimeZone = 'UTC'")  # like Fabric's Spark sessions, not this PC's zone
        keep_try_cast(self.conn)
        spark_hash.register(self.conn)  # sha2 at 224, 384 and 512 bits
        # The running query's progress, for the line under a running cell (progress()); never printed.
        self.conn.execute("SET enable_progress_bar = true")
        self.conn.execute("SET enable_progress_bar_print = false")
        self._views: dict[str, str] = {}
        self._snapshot: lakehouse.Attached | None = None
        self._frames: weakref.WeakSet[DataFrame] = weakref.WeakSet()  # this session's DataFrames still in use
        self._conf = spark_api.LocalRuntimeConfig(self)  # type: ignore[assignment]  # spark.conf (spark_compat's RuntimeConfig patches don't apply)
        self._local_context = spark_api.LocalSparkContext(self)
        catalog = self.catalog
        vars(catalog).update(_catalog_calls(self))

    @property
    def sparkContext(self) -> spark_api.LocalSparkContext:  # type: ignore[override]  # noqa: N802
        return self._local_context

    @property
    def version(self) -> str:
        return spark_api.SPARK_VERSION

    @property
    def udf(self) -> Any:
        """``spark.udf.register(name, f, returnType)``: a Python UDF callable from SQL (spark_udf.py)."""
        from .spark_udf import UDFRegistration
        return UDFRegistration()

    def range(self, start: int, end: int | None = None, step: int = 1, numPartitions: int | None = None) -> DataFrame:  # noqa: A003, N803, ARG002
        """``spark.range``: one column ``id``, as in Spark."""
        if end is None:
            start, end = 0, start
        return DataFrame(self.conn.sql(f"SELECT range AS id FROM range({int(start)}, {int(end)}, {int(step)})"), self)

    # Lakehouse tables are registered once per attached lakehouse; a new attach starts over.
    def _bound(self) -> dict[str, str]:
        current = lakehouse._attached
        if current is not self._snapshot:
            for view in self._views.values():
                try:
                    self.conn.unregister(view)
                except Exception:  # noqa: BLE001  # silent-ok: best-effort cleanup of a view on a connection being reset
                    pass
            self._views = {}
            self._snapshot = current
        return self._views

    def _bind(self, statement: Any) -> None:
        """Point ``statement``'s lakehouse tables at registered views, then drop the views of older
        versions that no DataFrame or temp view still reads, so a notebook that writes and reads a
        table many times doesn't keep every version's view on the connection."""
        views = self._bound()
        self._global_temp(statement)
        _sql.bind_tables(statement, self.conn, views, self._is_local)
        old = _sql.superseded(views)
        if not old:
            return
        # A DataFrame is a query over the view's name, run when it's used: a view one still reads stays.
        used = [frame.relation.sql_query() for frame in list(self._frames) if frame.relation is not None]
        # DuckDB 2.0 lists the registered views too, each with its own name in its SQL: those don't count.
        ours = {v.lower() for v in views.values()}
        used += [sql for name, sql in self.conn.execute("SELECT view_name, sql FROM duckdb_views() WHERE NOT internal")
                 .fetchall() if sql and name.lower() not in ours]
        text = "\n".join(used)
        for key in old:
            view = views[key]
            if not re.search(rf"\b{view}\b", text):
                self.conn.unregister(view)
                del views[key]

    def _global_temp(self, statement: Any) -> None:
        """Point global_temp.name at the global temp view createGlobalTempView made, as Spark reads it;
        one that isn't there is Spark's TABLE_OR_VIEW_NOT_FOUND, not a lakehouse lookup."""
        from duckdb.experimental.spark.errors import AnalysisException
        from sqlglot import exp
        for table in list(statement.find_all(exp.Table)):
            if table.catalog or not table.db or table.db.lower() != spark_api.GLOBAL_TEMP:
                continue
            view = spark_api.GLOBAL_PREFIX + table.name
            if not self._is_view(view) and not isinstance(statement, exp.Drop):
                raise AnalysisException(message=f"[TABLE_OR_VIEW_NOT_FOUND] The table or view `{table.db}`.`{table.name}` "
                                                "cannot be found. Verify the spelling and correctness of the schema "
                                                "and catalog.")
            alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
            table.replace(exp.Table(this=exp.to_identifier(view), alias=None if isinstance(statement, exp.Drop) else alias))

    def _is_view(self, name: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?)", [name]).fetchall())

    def _is_local(self, name: str) -> bool:
        return _sql.is_local(self.conn, name)

    def _from_arrow_dataset(self, data: Any) -> DataFrame:
        return DataFrame(self.conn.from_arrow(data), self)

    @property
    def read(self) -> LakehouseReader:  # type: ignore[override]  # this PC's reader, not DuckDB's
        return LakehouseReader(self)

    def table(self, tableName: str) -> DataFrame:  # noqa: N803
        if "." not in tableName and self._is_local(tableName):
            return DataFrame(self.conn.table(tableName), self)
        return self.sql(f"SELECT * FROM {tableName}")

    def sql(self, sqlQuery: str, args: Any = None, **kwargs: Any) -> DataFrame:  # noqa: N803
        """As PySpark 3.5's: ``kwargs`` fill ``{name}`` fields (a DataFrame by a temporary view, text as a
        quoted literal) and ``args`` binds ``:name`` or ``?`` markers as typed literals (spark_sql_params)."""
        from . import spark_sql_params
        if kwargs:
            sqlQuery = spark_sql_params.format_query(
                sqlQuery, kwargs, lambda df, name: df.createOrReplaceTempView(name),
                lambda v: isinstance(v, DataFrame), lambda v: isinstance(v, _Column))
        if args is not None:
            sqlQuery = spark_sql_params.bind(sqlQuery, args)
        with spark_time_zone.for_session(self):  # its casts are written for this session's time zone
            return self._sql(sqlQuery)

    @staticmethod
    def _setting_pieces(query: str) -> list[str]:
        """``_session_setting_pieces(query)``, checked before any piece runs: a call holding OPTIMIZE, VACUUM,
        RESTORE or MERGE WITH SCHEMA EVOLUTION isn't split, so it stops as it would without the time zone
        statement (each must be the only statement in its call), and one holding a MERGE, UPDATE or DELETE
        is refused, as any call with one beside other statements is."""
        pieces = _session_setting_pieces(query)
        if not pieces:
            return []
        from sqlglot import exp

        from . import spark_maint
        heads = [delta_dml_comments.sub("", p) for p in pieces]
        if any(spark_maint.STARTS.match(h) or _SCHEMA_EVOLUTION.match(h) for h in heads):
            return []
        for head in heads:
            if _CHANGE.match(head):
                raise ValueError(_ONE_CHANGE)
            if _SET_TIME_ZONE.fullmatch(head) or _SPARK_SET.fullmatch(head) or _SPARK_RESET.fullmatch(head):
                continue
            try:
                trees = _sql.parse(head)
            except Exception:  # noqa: BLE001  # silent-ok: the piece's own run reports what it can't read
                continue
            if any(t is not None and t.find(exp.Merge, exp.Update, exp.Delete) for t in trees):
                raise ValueError(_ONE_CHANGE)  # WITH c AS (...) DELETE ... too
        return pieces

    def _sql(self, sqlQuery: str) -> DataFrame:  # noqa: N803
        head = delta_dml_comments.sub("", sqlQuery)  # a leading comment doesn't hide RESTORE or SCHEMA EVOLUTION
        if (zone := _SET_TIME_ZONE.fullmatch(head)) is not None:  # SET TIME ZONE 'z' is spark.conf's, as in Spark
            head = f"SET {_TIME_ZONE_KEY} = {zone['zone']}"
        if (setting := _SPARK_SET.fullmatch(head)) is not None:  # SET spark.x = y goes to spark.conf, as in Spark
            key, value = setting["key"], setting["value"].strip().strip("'\"")
            self.conf.set(key, value)
            return self.createDataFrame([(key, value)], ["key", "value"])
        if (reset := _SPARK_RESET.fullmatch(head)) is not None:  # RESET [spark.x]: back to the default, as in Spark
            # Spark's bare RESET puts every key back to its session default, builder settings included; those
            # aren't kept apart from later ones here, so a bare RESET resets the session time zone only
            self.conf.unset(reset["key"] or _TIME_ZONE_KEY)
            return DataFrame(self.conn.sql("SELECT 1 WHERE false"), self)
        if len(pieces := self._setting_pieces(sqlQuery)) > 1:
            # a later statement is written for the time zone an earlier one sets, so each runs on its own
            result: DataFrame | None = None
            for piece in pieces:
                result = self._sql(piece)
            return cast(DataFrame, result)
        from . import spark_maint
        spec = spark_maint.statement(head)  # OPTIMIZE, VACUUM, RESTORE (Preview), as the only statement
        if spec is not None:
            answer, note = spark_maint.run(spec)
            if note:
                print(f"Note: {note}")
            return self._from_arrow_dataset(answer)
        evolve = bool(_SCHEMA_EVOLUTION.match(head))
        if evolve:  # MERGE WITH SCHEMA EVOLUTION INTO: the MERGE adds the source's new columns (Preview)
            sqlQuery = _SCHEMA_EVOLUTION_WORDS.sub(" ", head, count=1)
        statements = _sql.parse(sqlQuery)
        from sqlglot import exp
        if len(statements) > 1 and any(isinstance(s, (exp.Merge, exp.Update, exp.Delete)) for s in statements):
            # The change would commit before a later statement could fail; Spark runs one statement per call.
            raise ValueError(_ONE_CHANGE)
        result = None
        for statement in statements:
            result = self._statement(statement, evolve=evolve)
        return result if result is not None else DataFrame(self.conn.sql("SELECT 1 WHERE false"), self)

    def _instant(self, text: str) -> datetime.datetime:
        """``text`` (a date, or a date and time with or without a zone) as an instant, a time with no zone read
        in the session time zone (spark.sql.session.timeZone), as Spark reads it."""
        from zoneinfo import ZoneInfo

        from . import spark_time_zone
        when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if when.tzinfo is None:  # no query on the connection here: a cell's own result may still be open on it
            zone = spark_time_zone.zone_id(self.conf.get("spark.sql.session.timeZone", "UTC"))
            when = when.replace(tzinfo=ZoneInfo(zone))
        return when

    def _table_changes(self, statement: Any) -> Any:
        """Each ``table_changes(table_str, start [, end])`` in ``statement`` read from the table's change data
        feed (delta_cdf) into a view, as Databricks and Fabric read it: a whole number is a version, text a
        timestamp. Only literal arguments, since the read happens before the query runs."""
        from sqlglot import exp

        from . import delta_cdf
        for node in list(statement.find_all(exp.Anonymous)):
            if node.name.lower() != "table_changes":
                continue
            values = []
            for arg in node.expressions:
                if isinstance(arg, exp.Neg) and isinstance(arg.this, exp.Literal) and not arg.this.is_string:
                    raise ValueError("table_changes takes versions from 0 up, so nothing ran. A version can't be "
                                     "negative.")
                if isinstance(arg, exp.Literal) and arg.is_string and arg.name.strip().isdigit() \
                        and arg is not node.expressions[0]:
                    raise ValueError(f"table_changes reads '{arg.name}' in quotes as a timestamp, and it isn't one, "
                                     f"so nothing ran. Write a version without quotes: table_changes(..., "
                                     f"{arg.name.strip()}).")
                if not isinstance(arg, exp.Literal):
                    raise ValueError("table_changes takes a table name and versions or timestamps written as "
                                     "literals on this PC, so nothing ran. Write each one as a number or a "
                                     "quoted time.")
                if not arg.is_string and arg.name.isdigit() and len(arg.name.lstrip("0")) > delta_cdf.VERSION_DIGITS:
                    raise ValueError(f"table_changes takes a version of at most {delta_cdf.VERSION_DIGITS} digits, "
                                     "so nothing ran.")
                values.append(int(arg.name) if not arg.is_string and arg.name.isdigit() else arg.name)
            if len(values) not in (2, 3) or not isinstance(values[0], str):
                raise ValueError("table_changes takes a table name, a start and an optional end: "
                                 "table_changes('orders', 1) or table_changes('orders', 1, 3). Nothing ran.")
            options: dict[str, Any] = {"readchangefeed": "true"}
            for side, value in zip(("starting", "ending"), values[1:], strict=False):
                options[f"{side}{'version' if isinstance(value, int) else 'timestamp'}"] = value
            name = values[0]
            views = self._bound()
            view = f"__lakehouse_changes_{len(views)}"
            views[f"changes:{name.lower()}:{len(views)}"] = view
            self.conn.register(view, delta_cdf.read(lakehouse.open_table(name), name, options, self._instant))
            holder = node.parent if isinstance(node.parent, exp.Table) else node
            replacement = exp.Table(this=exp.to_identifier(view))
            if isinstance(holder, exp.Table) and holder.args.get("alias") is not None:
                replacement.set("alias", holder.args["alias"])
            holder.replace(replacement)
        return statement

    def _statement(self, statement: Any, evolve: bool = False) -> DataFrame | None:
        from sqlglot import exp
        if isinstance(statement, exp.Use):
            _use(statement)
            return None
        statement = _sql.history_query(statement, self.conn, self._bound())  # DESCRIBE HISTORY t
        statement = self._table_changes(statement)  # table_changes('t', 1): the table's change data feed
        if isinstance(statement, exp.Merge):
            return self._merge(statement, evolve)
        if isinstance(statement, (exp.Update, exp.Delete)):
            return self._update_or_delete(statement)
        # DROP TABLE and TRUNCATE TABLE on a lakehouse table; a temp view is DuckDB's to drop.
        if isinstance(statement, exp.TruncateTable) or (
                isinstance(statement, exp.Drop) and str(statement.args.get("kind", "")).upper() == "TABLE"):
            tables = statement.expressions if isinstance(statement, exp.TruncateTable) else statement.args.get("tables") or []
            names = [_sql.table_name(t) for t in tables  # with its catalog: DROP TABLE IF EXISTS main.dbo.t is refused
                     if t.db or t.catalog or not self._is_local(t.name)]
            if names:
                if isinstance(statement, exp.Drop) and statement.args.get("exists") \
                        and not any(lakehouse.table_exists(n) for n in names):
                    return None  # DROP TABLE IF EXISTS of a table that isn't there changes nothing, as in Spark
                listed = ", ".join(names)
                if isinstance(statement, exp.TruncateTable):
                    raise ValueError(f"TRUNCATE TABLE empties {listed} in place, which runs in Fabric. On this PC, "
                                     "overwrite it with no rows: spark.table(name).limit(0).write.mode('overwrite').saveAsTable(name).")
                raise ValueError(f"DROP TABLE removes {listed} from the lakehouse, which runs in Fabric. "
                                 "Run the cell in Fabric.")
            if isinstance(statement, exp.Drop) and all(self._is_view(t.name) for t in tables):
                statement.set("kind", "VIEW")  # Spark's DROP TABLE drops a temp view of that name too
        # CREATE TABLE ... AS SELECT and INSERT INTO ... SELECT (or VALUES) write a lakehouse table.
        if isinstance(statement, (exp.Create, exp.Insert)) and isinstance(statement.expression, (exp.Query, exp.Select, exp.Values)):
            target = statement.this
            while not isinstance(target, exp.Table) and target is not None:
                target = target.this
            is_view = isinstance(statement, exp.Create) and str(statement.args.get("kind", "")).upper() == "VIEW"
            temporary = statement.args.get("properties") and "TEMPORARY" in statement.args["properties"].sql().upper()
            if isinstance(target, exp.Table) and not is_view and not temporary:
                name = _sql.table_name(target)  # with its catalog: main.dbo.t is refused, never written to dbo.t
                query = statement.expression.copy()
                self._bind(query)
                data = self.conn.sql(_sql.duckdb_sql(_using_star(spark_complex_types(spark_decimal_types(
                    spark_text_comparisons(spark_branch_types(query, self.conn), self.conn), self.conn), self.conn),
                    self.conn)))
                insert = isinstance(statement, exp.Insert)
                mode = "append" if insert and not statement.args.get("overwrite") else "overwrite"
                if insert and (statement.find(exp.Partition) or statement.args.get("where")):
                    # A static partition or REPLACE WHERE replaces part of the table; ignoring it would replace all of it.
                    raise NotImplementedError("INSERT with PARTITION or REPLACE WHERE isn't supported on this PC, and "
                                              "ignoring it would replace the whole table. Run the cell in Fabric.")
                # CREATE TABLE as Spark does it: an existing table is kept (IF NOT EXISTS) or is an
                # error, and only CREATE OR REPLACE overwrites it.
                if isinstance(statement, exp.Create) and not statement.args.get("replace") and lakehouse.table_exists(name):
                    if statement.args.get("exists"):
                        print(f"Table {name} already exists; left as it is.")
                        return None
                    raise ValueError(f"Table {name} already exists. Use CREATE OR REPLACE TABLE, "
                                     "INSERT OVERWRITE or INSERT INTO.")
                arrow = (getattr(data, "to_arrow_table", None) or data.arrow)()
                if insert:
                    # INSERT matches columns by position and never changes the table's schema, as in Spark.
                    current = _columns(name)
                    if current is None:
                        raise ValueError(f"Table {name} doesn't exist. Create it with CREATE TABLE ... AS SELECT.")
                    if isinstance(statement.this, exp.Schema):
                        current = [c.name for c in statement.this.expressions]
                    if len(current) != arrow.num_columns:
                        raise ValueError(f"INSERT matches columns by position: the query has {arrow.num_columns} "
                                         f"columns and table {name} has {len(current)}.")
                    print(_write_table(_store_assign(arrow.rename_columns(current), name), name, mode, None))
                else:
                    print(lakehouse.write_table(arrow, name, mode=mode))
                return None
        self._bind(statement)
        statement = _using_star(spark_complex_types(spark_decimal_types(spark_text_comparisons(
            spark_branch_types(statement, self.conn), self.conn), self.conn), self.conn), self.conn)
        text = _sql.duckdb_sql(statement)
        if isinstance(statement, (exp.Query, exp.Select, exp.Describe, exp.Show)) or text.upper().startswith(("SELECT", "WITH", "FROM", "VALUES", "DESCRIBE", "SHOW")):
            return DataFrame(self.conn.sql(text), self)
        self.conn.execute(text)
        return None

    def _merge(self, tree: Any, evolve: bool = False) -> DataFrame:
        """MERGE INTO a lakehouse table (PB-015, Preview): DuckDB reads the source, delta-rs makes the
        change in one commit (lakehouse.change_table). Returns Spark's counts as a one-row DataFrame."""
        from sqlglot import exp

        from . import delta_dml
        from .localdata import delta as local_delta
        _in_attached(tree)
        change = delta_dml.parse_tree(tree)
        if change is None or change.source is None:  # parse_tree refuses a MERGE without USING itself
            raise ValueError("MERGE INTO needs USING (the rows to merge).")
        _preview_on("MERGE INTO")
        if not change.schema and self._is_local(change.table):
            raise ValueError(f"MERGE INTO {change.table}: {change.table} is a temp view on this PC. MERGE changes a "
                             "Delta table of the attached lakehouse.")
        query = change.source.copy() if not isinstance(change.source, exp.Table) else \
            exp.select("*").from_(change.source.copy())
        self._bind(query)
        source = _arrow(DataFrame(self.conn.sql(_sql.duckdb_sql(query)), self))
        target = _columns(change.name)
        if target is None:
            raise ValueError(f"Table {change.name} doesn't exist, so MERGE has nothing to change. Create it first "
                             "(df.write.saveAsTable or CREATE TABLE ... AS SELECT).")
        # Fabric-safe types, and the table's spelling of each column name (Spark matches them without case).
        cased = {n.lower(): n for n in target}
        safe = local_delta.fabric_schema(source.schema, names=False)
        change.schema_evolution = evolve or _auto_merge_on(self)
        if change.schema_evolution:
            # The source's new columns become the table's: hold them to the same name rules as saveAsTable, before
            # anything is written, so evolution can't add a column Fabric reads only with column mapping.
            new = [f for f in safe if f.name.lower() not in cased]
            if new:
                local_delta.fabric_schema(pa.schema(new), names=True)
        names = [cased.get(f.name.lower(), f.name) for f in safe]
        source = source.rename_columns(names).cast(pa.schema([pa.field(n, f.type) for n, f in zip(names, safe, strict=True)]))
        result = lakehouse.change_table(change, source)
        affected = result.rows_inserted + result.rows_updated + result.rows_deleted
        return self._from_arrow_dataset(pa.table({
            "num_affected_rows": [affected], "num_updated_rows": [result.rows_updated],
            "num_deleted_rows": [result.rows_deleted], "num_inserted_rows": [result.rows_inserted]}))

    def _update_or_delete(self, tree: Any) -> DataFrame:
        """UPDATE or DELETE FROM a lakehouse table (PB-015, Preview): delta-rs makes the change in one
        commit (lakehouse.change_table). Returns Spark's count as a one-row DataFrame."""
        from . import delta_dml
        _in_attached(tree)
        change = delta_dml.parse_tree(tree)
        if change is None:  # an Update or Delete tree always parses to a change
            raise ValueError("Only UPDATE and DELETE are changed here.")
        what = change.kind.upper()
        _preview_on(what)
        if not change.schema and self._is_local(change.table):
            raise ValueError(f"{what} {change.table}: {change.table} is a temp view on this PC. {what} changes a "
                             "Delta table of the attached lakehouse.")
        if _columns(change.name) is None:
            raise ValueError(f"Table {change.name} doesn't exist, so {what} has nothing to change.")
        result = lakehouse.change_table(change)
        return self._from_arrow_dataset(pa.table({
            "num_affected_rows": [result.rows_updated if change.kind == "update" else result.rows_deleted]}))

    def createDataFrame(self, data: Any, schema: Any = None, samplingRatio: Any = None,  # noqa: N802, N803
                        verifySchema: bool = True) -> DataFrame:  # noqa: N803
        module = type(data).__module__
        if module.startswith(("polars", "pyarrow")):
            arrow = data.to_arrow() if module.startswith("polars") else data
            return DataFrame(self.conn.from_arrow(arrow), self)
        data, schema = _atomic_schema(data, schema)
        if isinstance(schema, str) and _DDL.search(schema):  # "id int, name string", as in PySpark
            schema = ddl_schema(schema)
        if schema is None and isinstance(data, (list, tuple)) and data:
            fields = getattr(data[0], "__fields__", None)  # Row(name=..., age=...): its names, as Spark infers them
            if isinstance(fields, (list, tuple)) and fields and all(isinstance(f, str) for f in fields):
                schema = list(fields)  # a tuple (a Row itself) when the Row came from Row("name", "age")
        if isinstance(data, (list, tuple)) and data and (
                schema is None or (isinstance(schema, (list, tuple)) and all(isinstance(n, str) for n in schema))):
            # PySpark's inference (a tuple in a row is a struct, a dict a map, columns _1, _2), not DuckDB's
            inferred_rows = spark_infer.infer(data, list(schema) if schema else None)
            if inferred_rows is not None:  # as one Arrow table, which DuckDB reads far faster than row by row
                return DataFrame(self.conn.from_arrow(spark_infer.arrow_table(*inferred_rows)), self)
        if verifySchema:  # verifySchema=False skips the check, as in Spark
            check_not_null(data, schema)
        # DuckDB 1.x's own createDataFrame refuses samplingRatio and verifySchema=False; both only change
        # how Spark infers and checks the schema, which is done above. DuckDB 2.0 checks the schema
        # itself unless verifySchema=False reaches it.
        try:
            df = super().createDataFrame(data, schema, verifySchema=verifySchema)
        except NotImplementedError:  # DuckDB 1.x: verifySchema=False
            if verifySchema:
                raise
            df = super().createDataFrame(data, schema)
        inferred = schema is None or (isinstance(schema, (list, tuple)) and all(isinstance(n, str) for n in schema))
        return self._ints_as_long(df) if inferred and isinstance(data, (list, tuple)) else df

    def _createDataFrameFromPandas(self, data: Any, types: Any, names: Any) -> DataFrame:  # noqa: N802
        """DuckDB 1.x casts a pandas frame's columns to the schema's types as ``name::TYPE as name``
        with the names unquoted, so a column called left, order or select is a ParserException there,
        where Spark takes any name. The frame's columns are given plain positional names for that cast;
        the schema's names come back after it, quoted."""
        if types and names and len(data.columns) == len(names):
            # Only a name the cast can't take as written is replaced, so an error (a value that isn't the
            # schema's type, read when the frame is) names every other column as the schema does.
            plain = re.compile(r"[a-z_][a-z0-9_]*")
            reserved = _duckdb_keywords(self.conn)
            data = data.set_axis([n if isinstance(n, str) and plain.fullmatch(n) and n not in reserved
                                  and not n.startswith("_fd_col") else f"_fd_col{i}"
                                  for i, n in enumerate(data.columns)], axis=1)
        return super()._createDataFrameFromPandas(data, types, names)

    def _ints_as_long(self, df: DataFrame) -> DataFrame:
        """Spark infers every Python int as bigint (LongType); DuckDB 1.x makes an int that fits INTEGER,
        so a column of them (or of lists of them) says int where Spark says bigint. DuckDB 2.0 says bigint."""
        rel = df.relation
        exprs, changed = [], False
        for name, kind in zip(rel.columns, rel.types, strict=True):
            text = str(kind)
            wanted = re.sub(r"\bINTEGER\b", "BIGINT", text)
            quoted = '"' + name.replace('"', '""') + '"'
            changed |= wanted != text
            exprs.append(f"CAST({quoted} AS {wanted}) AS {quoted}" if wanted != text else quoted)
        return DataFrame(rel.project(", ".join(exprs)), self) if changed else df


_JOIN_SOURCES = ("Table", "Subquery", "Values")


def _source_columns(source: Any, ctes: list[Any], conn: Any) -> list[tuple[str, Any]]:
    """Each column of a FROM or JOIN source, as its name and a reference qualified by the source's name."""
    from sqlglot import exp
    probe = exp.select("*").from_(source.copy()).limit(0)
    if ctes:
        probe.set("with_", exp.With(expressions=[c.copy() for c in ctes]))
    return [(n, exp.column(n, table=source.alias_or_name, quoted=True))
            for n in conn.sql(_sql.duckdb_sql(probe)).columns]


def _using_star(statement: Any, conn: Any) -> Any:
    """``SELECT *`` over a JOIN ... USING (k) or a NATURAL JOIN in Spark 3.5's order: the join keys first,
    then the rest of the left's columns, then the right's (the key the left's for an inner or left join, the
    right's for a right join, either side's for a full join); DuckDB keeps the left's order. Each such ``*``
    is written out as the columns it stands for, each side's read from DuckDB with no rows. A semi or anti
    join's is the left's keys, then the rest of the left's columns (as Spark 3.5's analyzer writes it, read
    from its source, not run against Spark). ``ta.*`` is left as it is; so is any select this can't read
    (an unnamed or lateral source), which DuckDB then answers in its own order."""
    from sqlglot import exp
    for select in reversed(list(statement.find_all(exp.Select))):  # the innermost first: an outer * reads it
        joins = select.args.get("joins") or []
        if not any(j.args.get("using") or j.method == "NATURAL" for j in joins):
            continue
        if not any(isinstance(e, exp.Star) and not any(e.args.values()) for e in select.expressions):
            continue
        sources = [select.args["from_"].this, *(j.this for j in joins)] if select.args.get("from_") else []
        if not sources or any(type(s).__name__ not in _JOIN_SOURCES or not s.alias_or_name or s.args.get("pivots")
                              for s in sources):
            continue
        ctes: list[Any] = []
        node = select
        while node is not None:  # the CTEs a source may name, the outermost first
            if (w := node.args.get("with_")) is not None:
                ctes[:0] = w.expressions
            node = node.parent
        try:
            out = _source_columns(sources[0], ctes, conn)
            for join in joins:
                right = _source_columns(join.this, ctes, conn)
                existence = str(join.kind).upper() in ("SEMI", "ANTI")  # the left's columns only
                if join.method == "NATURAL":
                    keys = [n for n, _ in out if n.lower() in {r.lower() for r, _ in right}]
                elif join.args.get("using"):
                    keys = [k.name for k in join.args["using"]]
                else:
                    out += [] if existence else right
                    continue
                side = "LEFT" if existence else str(join.side).upper()
                merged = []
                for key in keys:
                    lhs = [c for c in out if c[0].lower() == key.lower()]
                    rhs = [c for c in right if c[0].lower() == key.lower()]
                    if len(lhs) != 1 or len(rhs) != 1:
                        raise LookupError(key)  # DuckDB reports a missing or ambiguous key itself
                    if side == "FULL":
                        # an earlier full join's key is already COALESCE(...) AS k: coalesce what it holds
                        left_key, right_key = (c[0][1].this if isinstance(c[0][1], exp.Alias) else c[0][1]
                                               for c in (lhs, rhs))
                        merged.append((lhs[0][0], exp.alias_(exp.func("COALESCE", left_key, right_key), lhs[0][0],
                                                             quoted=True)))
                    else:
                        merged.append((rhs if side == "RIGHT" else lhs)[0])
                lowered = {k.lower() for k in keys}
                out = merged + [c for c in out if c[0].lower() not in lowered] + \
                    ([] if existence else [c for c in right if c[0].lower() not in lowered])
        except (duckdb.Error, LookupError):  # silent-ok: DuckDB answers (or reports) the query as written
            continue
        projections: list[Any] = []
        for e in select.expressions:
            projections += [c[1].copy() for c in out] if isinstance(e, exp.Star) and not any(e.args.values()) else [e]
        select.set("expressions", projections)
    return statement


def _atomic_schema(data: Any, schema: Any) -> tuple[Any, Any]:
    """createDataFrame(["a"], "string") or (data, IntegerType()): Spark makes one column, value, of
    that type, each item one row. A type string that's a whole struct is that struct."""
    from duckdb.experimental.spark.sql import types as T
    kind = None
    if isinstance(schema, str):
        try:
            kind = _ddl_type(schema)
        except ValueError:  # silent-ok: not one type ("id int, name string"), read below as before
            return data, schema
    elif isinstance(schema, T.DataType) and not isinstance(schema, T.StructType):
        kind = schema
    if kind is None:
        return data, schema
    if isinstance(kind, T.StructType):
        return data, kind
    rows = list(data) if isinstance(data, (list, tuple)) else data
    if isinstance(rows, list):
        for item in rows:
            if isinstance(item, (tuple, list, dict)) and not isinstance(kind, (T.ArrayType, T.MapType)):
                raise ValueError(f"createDataFrame with the type {kind.simpleString()} makes each item one value, "
                                 f"and {item!r} isn't one: Spark would store its Java object's name. Give plain "
                                 "values, or a schema such as \"value string\" for rows.")
        rows = [(item,) for item in rows]
    return rows, T.StructType([T.StructField("value", kind, True)])


def _nan(value: Any) -> bool:
    return isinstance(value, float) and value != value


def _keeping_nan(combine: Any) -> Any:
    """DuckDB 1.x's createDataFrame with a schema makes each value a duckdb.Value of the field's type,
    which turns a float NaN into NULL (DuckDB 2.0 keeps it). A NaN field is passed as the float itself
    instead, and the frame is cast to the schema's types afterwards, so Spark's NaN stays NaN. (A NaN
    inside an array or struct still becomes NULL on DuckDB 1.x: its parameters do the same.)"""
    def combined(data: Any, schema: Any) -> list[Any]:
        rows = list(data)
        values = combine(rows, schema)
        if not any(isinstance(row, (list, tuple)) and any(_nan(v) for v in row) for row in rows):
            return values
        return [[raw if _nan(raw) else made for raw, made in zip(row, made_row, strict=False)]
                if isinstance(row, (list, tuple)) else made_row for row, made_row in zip(rows, values, strict=True)]
    return combined


if hasattr(_session_module, "_combine_data_and_schema"):  # DuckDB 1.x
    _session_module._combine_data_and_schema = _keeping_nan(_session_module._combine_data_and_schema)


# A DDL schema string has a type after a name ("id int") or a comma; one bare word is a column name.
_DDL = re.compile(r"[,:]|\w\s+\w")
_ATOMIC = {"int": "IntegerType", "integer": "IntegerType", "bigint": "LongType", "long": "LongType",
           "smallint": "ShortType", "short": "ShortType", "tinyint": "ByteType", "byte": "ByteType",
           "string": "StringType", "varchar": "StringType", "char": "StringType", "double": "DoubleType",
           "float": "FloatType", "real": "FloatType", "boolean": "BooleanType", "bool": "BooleanType",
           "date": "DateType", "timestamp": "TimestampType", "timestamp_ntz": "TimestampNTZType",
           "binary": "BinaryType"}


def _split_top(text: str) -> list[str]:
    """``text`` split at commas outside <>, () and a COMMENT's quotes."""
    parts, depth, start, quote, escaped = [], 0, 0, "", False
    for i, c in enumerate(text):
        if quote:
            escaped, quote = (False, quote) if escaped else (c == "\\", "" if c == quote else quote)
            continue
        if c in "'\"":
            quote = c
            continue
        depth += c in "<(" and 1 or (-1 if c in ">)" else 0)
        if c == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _ddl_type(text: str) -> Any:
    from duckdb.experimental.spark.sql import types as T
    t = text.strip()
    low = t.lower()
    if low in _ATOMIC:
        return getattr(T, _ATOMIC[low])()
    if m := re.fullmatch(r"(?:decimal|numeric|dec)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?", low):
        return T.DecimalType(int(m[1] or 10), int(m[2] or 0))
    if m := re.fullmatch(r"array\s*<(.*)>", t, re.I | re.S):
        return T.ArrayType(_ddl_type(m[1]))
    if m := re.fullmatch(r"map\s*<(.*)>", t, re.I | re.S):
        key, value = (_split_top(m[1]) + ["", ""])[:2]
        return T.MapType(_ddl_type(key), _ddl_type(value))
    if m := re.fullmatch(r"struct\s*<(.*)>", t, re.I | re.S):
        if not m[1].strip():  # Spark allows struct<>; DuckDB can't store a struct with no fields
            raise ValueError("An empty struct<> isn't supported on this PC. Give the struct at least one field, "
                             "like struct<id: int>.")
        return ddl_schema(m[1])
    raise ValueError(f"The schema type {t!r} isn't supported on this PC. Use a StructType, or types such as "
                     "int, bigint, string, double, boolean, date, timestamp, decimal(p,s), array<...>, map<...>, "
                     "struct<...>.")


_NOT_NULL = re.compile(r"\s+NOT\s+NULL\s*$", re.I)
_COMMENT = re.compile(r"\s+COMMENT\s+(?:'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\s*$", re.I | re.S)


def _duckdb_keywords(conn: Any) -> frozenset[str]:
    """DuckDB's keywords, in lower case: a name among them must be quoted, which DuckDB 1.x's pandas cast
    doesn't do. Read once per process."""
    global _KEYWORDS
    if _KEYWORDS is None:
        _KEYWORDS = frozenset(r[0].lower() for r in conn.execute("SELECT keyword_name FROM duckdb_keywords()").fetchall())
    return _KEYWORDS


_KEYWORDS: frozenset[str] | None = None


class _NoDataFiles(ValueError):
    """A folder read has no data files: Spark gives no rows when a schema is given, and an error otherwise."""


def ddl_schema(text: str) -> Any:
    """A PySpark DDL schema string, ``"id int, name string"`` or ``"id: int, name: string"``, as a StructType."""
    from duckdb.experimental.spark.sql import types as T
    fields = []
    for part in _split_top(text):
        m = re.fullmatch(r"(?:`([^`]+)`|([^`:\s]+))\s*(?::\s*|\s+)(.+)", part, re.S)
        if not m:
            raise ValueError(f"Couldn't read {part!r} in the schema {text!r}. Write it as \"name type\", like \"id int\".")
        name, kind = m[1] or m[2], _COMMENT.sub("", m[3])  # a column comment is for the catalog, not the data
        nullable = not _NOT_NULL.search(kind)
        fields.append(T.StructField(name, _ddl_type(_NOT_NULL.sub("", kind)), nullable))
    return T.StructType(fields)


def _refuses_null(kind: Any) -> bool:
    """Whether a type has a NOT NULL anywhere in it: a struct field, an array element, a map value."""
    from duckdb.experimental.spark.sql import types as T
    if isinstance(kind, T.StructType):
        return any(not f.nullable or _refuses_null(f.dataType) for f in kind.fields)
    if isinstance(kind, T.ArrayType):
        return not kind.containsNull or _refuses_null(kind.elementType)
    if isinstance(kind, T.MapType):
        return not kind.valueContainsNull or _refuses_null(kind.valueType)
    return False


def _not_nullable(where: str) -> Exception:
    from duckdb.experimental.spark.errors import PySparkValueError
    return PySparkValueError(  # PySpark 3.4+'s error, a ValueError
        message=f"[FIELD_NOT_NULLABLE_WITH_NAME] field {where}: This field is not nullable, but got None.")


def check_not_null(data: Any, schema: Any) -> None:
    """Spark refuses a null where the schema says NOT NULL when it makes a DataFrame from Python
    rows; DuckDB would keep it, so the rows are checked here first: struct fields, array elements
    (containsNull=False) and map values (valueContainsNull=False), at any depth."""
    from duckdb.experimental.spark.sql import types as T
    if not isinstance(schema, T.StructType) or not _refuses_null(schema):
        return  # nothing to check, so large frames don't pay for a walk over every row

    def check(value: Any, struct: Any, path: str) -> None:
        if value is None:
            return
        items = value.asDict() if hasattr(value, "asDict") else value
        for i, field in enumerate(struct.fields):
            v = items.get(field.name) if isinstance(items, dict) else (items[i] if i < len(items) else None)
            where = f"{path}{field.name}"
            if v is None and not field.nullable:
                raise _not_nullable(where)
            inner(v, field.dataType, where)

    def inner(value: Any, kind: Any, where: str) -> None:
        """What's inside a field: a struct's fields, or each element of an array or value of a map."""
        if value is None or not _refuses_null(kind):
            return
        if isinstance(kind, T.StructType):
            check(value, kind, where + ".")
        elif isinstance(kind, T.ArrayType) and isinstance(value, (list, tuple)):
            for item in value:
                if item is None and not kind.containsNull:
                    raise _not_nullable(where + ".element")
                inner(item, kind.elementType, where + ".element")
        elif isinstance(kind, T.MapType) and isinstance(value, dict):
            for item in value.values():
                if item is None and not kind.valueContainsNull:
                    raise _not_nullable(where + ".value")
                inner(item, kind.valueType, where + ".value")

    if isinstance(data, (list, tuple)):
        for row in data:
            check(row, schema, "")


# What DuckDB's API leaves out (Window, agg, fillna, pivot, row_number...), on DuckDB SQL.
spark_compat.install()

# ``df.write`` goes to the lakehouse instead of a DuckDB table in memory.
_frame_init = _dataframe.DataFrame.__init__


def _tracked_init(self: DataFrame, relation: Any, session: Any, *args: Any, **kwargs: Any) -> None:
    _frame_init(self, relation, session, *args, **kwargs)  # duckdb 2.0 adds names and join_inputs (#1264)
    if isinstance(session, LocalSparkSession):
        session._frames.add(self)


_dataframe.DataFrame.__init__ = _tracked_init  # type: ignore[method-assign]
_dataframe.DataFrame.write = property(lambda self: LakehouseWriter(self))  # type: ignore[assignment]


class LocalDeltaTable:
    """``from delta.tables import DeltaTable`` on this PC: reading a table's history and details, and
    ``merge``, ``update`` and ``delete`` (Preview, PB-015), which run as MERGE INTO, UPDATE and
    DELETE through spark.sql. Other changes made through it (restore, vacuum, optimize) run in Fabric."""

    def __init__(self, name: str, alias: str = ""):
        self._name = name
        self._alias = alias

    def alias(self, aliasName: str) -> LocalDeltaTable:  # noqa: N803
        return LocalDeltaTable(self._name, aliasName)

    @classmethod
    def forName(cls, sparkSession: Any, tableOrViewName: str) -> LocalDeltaTable:  # noqa: N802, N803, ARG003
        lakehouse.open_table(tableOrViewName)  # a missing table fails here, as in Spark
        return cls(tableOrViewName)

    @classmethod
    def forPath(cls, sparkSession: Any, path: str) -> LocalDeltaTable:  # noqa: N802, N803, ARG003
        rel = lakehouse.relative_path(path) or ""
        if not rel.startswith("Tables/"):
            raise NotImplementedError("On this PC, DeltaTable.forPath reads tables of the attached lakehouse "
                                      "(Tables/...). Use DeltaTable.forName for other tables.")
        return cls.forName(sparkSession, ".".join(p for p in rel.split("/")[1:] if p))

    @staticmethod
    def isDeltaTable(sparkSession: Any, identifier: str) -> bool:  # noqa: N802, ARG004
        rel = lakehouse.relative_path(identifier) or identifier
        name = ".".join(p for p in rel.split("/")[1:] if p) if rel.startswith("Tables/") else rel
        return lakehouse.table_exists(name)

    def toDF(self) -> DataFrame:  # noqa: N802
        return session().table(self._name)

    def history(self, limit: int | None = None) -> DataFrame:
        return session()._from_arrow_dataset(lakehouse.table_history(self._name, limit).to_arrow())

    def detail(self) -> DataFrame:
        t = lakehouse.open_table(self._name)
        meta = t.metadata()
        row = {"format": ["delta"], "name": [self._name], "location": [t.table_uri], "version": [t.version()],
               "numFiles": [len(t.file_uris())], "partitionColumns": [list(meta.partition_columns)],
               "createdAt": [meta.created_time], "properties": [str(meta.configuration)]}
        return session()._from_arrow_dataset(pa.table(row))

    def _maintain(self, spec: dict[str, Any]) -> DataFrame:
        from . import spark_maint
        answer, note = spark_maint.run({"table": self._name} | spec)
        if note:
            print(f"Note: {note}")
        return session()._from_arrow_dataset(answer)

    def restoreToVersion(self, version: int) -> DataFrame:  # noqa: N802
        """RESTORE to ``version`` (Preview): one new commit that brings back that version's rows."""
        return self._maintain({"operation": "restore", "version": int(version)})

    def restoreToTimestamp(self, timestamp: Any) -> DataFrame:  # noqa: N802
        from . import spark_maint
        when = timestamp if isinstance(timestamp, datetime.datetime) else spark_maint.parse_timestamp(str(timestamp))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return self._maintain({"operation": "restore", "timestamp": when})

    def merge(self, source: Any, condition: Any) -> LocalMergeBuilder:
        _preview_on("DeltaTable.merge")
        return LocalMergeBuilder(self, source, condition)

    def update(self, condition: Any = None, set: dict[str, Any] | None = None) -> None:  # noqa: A002
        _preview_on("DeltaTable.update")
        if not set:
            raise ValueError("update needs the columns to set, as a dict of column name to SQL text or Column.")
        sets = ", ".join(f"{k} = {_sql_text(v)}" for k, v in set.items())
        where = f" WHERE {_sql_text(condition)}" if condition is not None else ""
        session().sql(f"UPDATE {self._name}{f' AS {self._alias}' if self._alias else ''} SET {sets}{where}")

    def updateExpr(self, condition: Any = None, set: dict[str, str] | None = None) -> None:  # noqa: N802, A002
        self.update(condition, set)

    def delete(self, condition: Any = None) -> None:
        _preview_on("DeltaTable.delete")
        where = f" WHERE {_sql_text(condition)}" if condition is not None else ""
        session().sql(f"DELETE FROM {self._name}{f' AS {self._alias}' if self._alias else ''}{where}")

    def vacuum(self, retentionHours: float | None = None) -> DataFrame:  # noqa: N803
        """VACUUM (Preview), keeping at least 7 days of history, or the table's own retention when longer."""
        return self._maintain({"operation": "vacuum", "retain_hours": retentionHours})

    def optimize(self) -> LocalOptimizeBuilder:
        """OPTIMIZE (Preview): ``.where("part = 'v'")`` then ``executeCompaction()`` or ``executeZOrderBy(cols)``."""
        return LocalOptimizeBuilder(self)


class LocalOptimizeBuilder:
    """delta-spark's DeltaOptimizeBuilder on this PC."""

    def __init__(self, table: LocalDeltaTable):
        self._table, self._where = table, ""

    def where(self, partitionFilter: str) -> LocalOptimizeBuilder:  # noqa: N803
        self._where = f"{self._where} AND ({partitionFilter})" if self._where else str(partitionFilter)
        return self

    def _run(self, zorder: list[str] | None) -> DataFrame:
        from . import spark_maint
        where = spark_maint._partition_filters(self._where.replace("(", "").replace(")", "")) if self._where else None
        return self._table._maintain({"operation": "optimize", "where": where, "zorder": zorder})

    def executeCompaction(self) -> DataFrame:  # noqa: N802
        return self._run(None)

    def executeZOrderBy(self, *cols: Any) -> DataFrame:  # noqa: N802
        names = [c for group in cols for c in (group if isinstance(group, (list, tuple)) else [group])]
        if not names:
            raise ValueError("executeZOrderBy needs at least one column.")
        return self._run([str(c) for c in names])


def _sql_text(value: Any) -> str:
    """A condition or value given to the merge builder: SQL text, or a Column (its expression)."""
    if isinstance(value, str):
        return value
    expr = getattr(value, "expr", None)
    if expr is None:
        raise TypeError(f"Expected SQL text or a Column, got {type(value).__name__}.")
    import sqlglot

    # A Column prints as DuckDB SQL, which from DuckDB 2.0 quotes names ("s"."amount"): a string in Spark SQL.
    return sqlglot.transpile(str(expr), read="duckdb", write="spark")[0]


class LocalMergeBuilder:
    """``DeltaTable.merge(source, condition).whenMatched...().execute()`` as delta-spark has it. The
    clauses are written out as one MERGE INTO and run by spark.sql, so they behave as SQL MERGE does
    on this PC: the same checks, the same single commit."""

    _MERGE_VIEWS = itertools.count(1)

    def __init__(self, table: LocalDeltaTable, source: Any, condition: Any):
        self._table, self._source, self._on = table, source, _sql_text(condition)
        self._clauses: list[str] = []
        self._evolve = False

    def _add(self, when: str, condition: Any, action: str) -> LocalMergeBuilder:
        cond = f" AND ({_sql_text(condition)})" if condition is not None else ""
        self._clauses.append(f"WHEN {when}{cond} THEN {action}")
        return self

    @staticmethod
    def _sets(values: dict[str, Any] | None, what: str) -> dict[str, str]:
        if not values:
            raise ValueError(f"{what} needs the columns to set, as a dict of column name to SQL text or Column.")
        return {str(k): _sql_text(v) for k, v in values.items()}

    def whenMatchedUpdate(self, condition: Any = None, set: dict[str, Any] | None = None) -> LocalMergeBuilder:  # noqa: N802, A002
        sets = self._sets(set, "whenMatchedUpdate")
        return self._add("MATCHED", condition, "UPDATE SET " + ", ".join(f"{k} = {v}" for k, v in sets.items()))

    def whenMatchedUpdateAll(self, condition: Any = None) -> LocalMergeBuilder:  # noqa: N802
        return self._add("MATCHED", condition, "UPDATE SET *")

    def whenMatchedDelete(self, condition: Any = None) -> LocalMergeBuilder:  # noqa: N802
        return self._add("MATCHED", condition, "DELETE")

    def whenNotMatchedInsert(self, condition: Any = None, values: dict[str, Any] | None = None) -> LocalMergeBuilder:  # noqa: N802
        sets = self._sets(values, "whenNotMatchedInsert")
        return self._add("NOT MATCHED", condition,
                         f"INSERT ({', '.join(sets)}) VALUES ({', '.join(sets.values())})")

    def whenNotMatchedInsertAll(self, condition: Any = None) -> LocalMergeBuilder:  # noqa: N802
        return self._add("NOT MATCHED", condition, "INSERT *")

    def whenNotMatchedBySourceUpdate(self, condition: Any = None, set: dict[str, Any] | None = None) -> LocalMergeBuilder:  # noqa: N802, A002
        sets = self._sets(set, "whenNotMatchedBySourceUpdate")
        return self._add("NOT MATCHED BY SOURCE", condition,
                         "UPDATE SET " + ", ".join(f"{k} = {v}" for k, v in sets.items()))

    def whenNotMatchedBySourceDelete(self, condition: Any = None) -> LocalMergeBuilder:  # noqa: N802
        return self._add("NOT MATCHED BY SOURCE", condition, "DELETE")

    def withSchemaEvolution(self) -> LocalMergeBuilder:  # noqa: N802
        """UPDATE SET * and INSERT * add the source's new columns to the table, in the MERGE's one commit."""
        self._evolve = True
        return self

    def execute(self) -> DataFrame:
        if not self._clauses:
            raise ValueError("The merge has no whenMatched or whenNotMatched clause, so there is nothing to do.")
        relation = getattr(self._source, "relation", None)
        if relation is None:
            raise TypeError("DeltaTable.merge needs a spark DataFrame as its source.")
        spark = session()
        view = f"__merge_builder_{next(self._MERGE_VIEWS)}"
        source_alias = getattr(relation, "alias", "") or ""
        if not source_alias or source_alias.startswith(("unnamed_relation", "arrow_object")):  # no df.alias(...)
            source_alias = "source"
        relation.create_view(view, replace=True)
        try:
            target = self._table._name + (f" AS `{self._table._alias}`" if self._table._alias else "")
            how = "MERGE WITH SCHEMA EVOLUTION INTO" if self._evolve else "MERGE INTO"
            return spark.sql(f"{how} {target} USING {view} AS `{source_alias}` ON {self._on} "
                             + " ".join(self._clauses))
        finally:
            spark.conn.execute(f"DROP VIEW IF EXISTS {view}")


def _alias_delta() -> None:
    """``from delta.tables import DeltaTable`` resolves to LocalDeltaTable when delta-spark isn't installed."""
    if "delta" in sys.modules or importlib.util.find_spec("delta") is not None:
        return
    import types
    delta, tables = types.ModuleType("delta"), types.ModuleType("delta.tables")
    tables.DeltaTable = LocalDeltaTable  # type: ignore[attr-defined]
    delta.tables = tables  # type: ignore[attr-defined]
    sys.modules.update({"delta": delta, "delta.tables": tables})


def _alias_pyspark() -> None:
    """``import pyspark...`` resolves to DuckDB's API when real PySpark isn't installed."""
    if "pyspark" in sys.modules or importlib.util.find_spec("pyspark") is not None:
        return
    import duckdb.experimental.spark as spark_pkg
    import duckdb.experimental.spark.sql as spark_sql
    import duckdb.experimental.spark.sql.functions as functions
    import duckdb.experimental.spark.sql.types as types
    sys.modules.update({"pyspark": spark_pkg, "pyspark.sql": spark_sql,
                        "pyspark.sql.functions": functions, "pyspark.sql.types": types,
                        "pyspark.sql.window": spark_compat.window_module})
    spark_compat.sql_names(spark_sql)  # Row, Column, GroupedData, Window
    # ``SparkSession.builder.getOrCreate()`` in a cell returns the notebook's session.
    spark_sql.SparkSession = LocalSparkSession  # type: ignore[misc,assignment]
    LocalSparkSession.builder = _Builder()  # type: ignore[assignment]


class _Builder:
    def appName(self, *_a: Any) -> _Builder:  # noqa: N802
        return self

    def config(self, key: Any = None, value: Any = None, conf: Any = None, map: Any = None) -> _Builder:  # noqa: A002
        """Settings given to the builder go to spark.conf, as in Spark."""
        pairs = dict(map or {})
        if conf is not None and hasattr(conf, "getAll"):
            pairs.update(dict(conf.getAll()))
        if key is not None:
            pairs[key] = value
        for k, v in pairs.items():
            session().conf.set(k, v)
        return self

    master = enableHiveSupport = appName

    def getOrCreate(self) -> LocalSparkSession:  # noqa: N802
        return session()


_session: LocalSparkSession | None = None


def session() -> LocalSparkSession:
    global _session
    if _session is None:
        _session = LocalSparkSession()
    return _session


def reset() -> None:
    """A fresh session, as after a kernel restart."""
    global _session
    if _session is not None:
        try:
            _session.conn.close()
        except Exception:  # noqa: BLE001  # silent-ok: best-effort close of a session being replaced
            pass
    _session = None
    from . import spark_udf
    spark_udf._registered.clear()  # a restart forgets registered UDFs, as a new Spark session does


def install(namespace: dict[str, Any]) -> None:
    spark_api.install()
    from . import spark_udf
    spark_udf.install()
    from . import spark_map
    spark_map.install()
    from . import spark_df_more
    spark_df_more.install()
    _alias_pyspark()
    _alias_delta()
    from . import spark_df_extras, spark_stream
    spark_stream.install()
    spark_df_extras.install()
    namespace["spark"] = session()


def progress() -> float | None:
    """How far ``spark``'s running DuckDB query is, 0-100, or None when none is running or DuckDB
    can't tell (a query over a table function, or one that hasn't got going)."""
    if _session is None:
        return None
    try:
        p = _session.conn.query_progress()
    except Exception:  # noqa: BLE001  # silent-ok: progress is a hint; a closed connection has none
        return None
    shown = round(p, 1) if p is not None else None
    return shown if shown is not None and 0 < shown <= 100 else None  # 0.04% shows as 0.0: not got going


PROFILE_NODES = 200  # operators a query profile lists; a bigger plan says how many it left out
_profile_lock = threading.Lock()


def query_profile(df: Any) -> dict[str, Any]:
    """Run a ``spark`` DataFrame's query again under DuckDB's profiler (EXPLAIN ANALYZE): each step
    of the plan with the rows it produced, its time and its share of the total, the closest local
    view to a Spark UI's query details. Reads only, like the query itself; it costs one more run."""
    rel = getattr(df, "relation", None)
    if _session is None or rel is None or not type(df).__module__.startswith("duckdb.experimental.spark"):
        raise ValueError("A query profile is for a result of spark or %%sql. Show one as the cell's result, "
                         "then ask for its profile.")
    conn = _session.conn
    fd, out = tempfile.mkstemp(prefix="kratosforge-profile-", suffix=".json")
    os.close(fd)
    with _profile_lock:  # the profiler is the connection's own setting; one profile at a time
        try:
            conn.execute("PRAGMA enable_profiling='json'")
            conn.execute("SET profiling_output='" + out.replace("'", "''") + "'")  # SET takes no parameters
            text = rel.explain(type="analyze")
        finally:
            for statement in ("PRAGMA disable_profiling", "RESET profiling_output"):
                try:
                    conn.execute(statement)
                except duckdb.Error:  # silent-ok: a closed connection keeps no setting to undo
                    pass
            try:
                os.unlink(out)
            except OSError:  # silent-ok: an empty temp file
                pass
    return _read_profile(json.loads(text))


# The profiler's own steps above the query's plan: 1.x wraps the plan in EXPLAIN_ANALYZE, 2.0 also
# in RESULT_COLLECTOR.
_PROFILER_STEPS = {"EXPLAIN_ANALYZE", "RESULT_COLLECTOR"}


def _read_profile(root: dict[str, Any]) -> dict[str, Any]:
    """DuckDB's JSON profile as query_profile returns it. duckdb 1.x gives one tree whose nodes have
    operator_name, operator_timing and operator_cardinality, with totals on the root. 2.0 gives an
    "operator" list of nodes with type, timing and intermediate_rows, totals under "query" and
    "system", and names a function scan TABLE_SCAN with the function in extra_info (#1264)."""
    query, system = root.get("query") or {}, root.get("system") or {}
    steps: list[dict[str, Any]] = []
    total = 0.0
    leaf_rows = 0

    def first(node: dict[str, Any], *keys: str) -> Any:
        return next((node[k] for k in keys if node.get(k) is not None), None)

    def walk(node: dict[str, Any], depth: int) -> None:
        nonlocal total, leaf_rows
        extra = node.get("extra_info") or {}
        name = first(node, "operator_name", "operator_type", "type") or ""
        if name == "TABLE_SCAN" and extra.get("Function"):
            name = extra["Function"]
        children = node.get("children") or []
        if name and name not in _PROFILER_STEPS:
            seconds = float(first(node, "operator_timing", "timing") or 0)
            rows = int(first(node, "operator_cardinality", "intermediate_rows") or 0)
            total += seconds
            if not children:
                leaf_rows += rows
            if len(steps) < PROFILE_NODES:
                details = {k: str(v) for k, v in extra.items() if v not in ("", None)}
                steps.append({"depth": depth, "name": name, "rows": rows,
                              "rows_scanned": int(first(node, "operator_rows_scanned", "rows_scanned") or 0),
                              "seconds": round(seconds, 6), "details": details})
            else:
                steps.append({})  # counted, not listed
            depth += 1
        for child in children:
            walk(child, depth)
    for top in root["operator"] if "operator" in root else [root]:
        walk(top, 0)
    listed = [st for st in steps if st]
    for st in listed:
        st["share"] = round(st["seconds"] / total, 4) if total else 0.0
    # Scans of a function or an Arrow table (every lakehouse table) report no rows scanned; the rows
    # their steps produced are the closest count then.
    scanned = int(first(root, "cumulative_rows_scanned") or first(query, "total_rows_scanned") or 0) or leaf_rows
    return {"seconds": round(float(first(root, "latency") or first(query, "total_time") or 0), 6),
            "rows": listed[0]["rows"] if listed else 0,
            "rows_scanned": scanned,
            "peak_memory": int(first(root, "system_peak_buffer_memory") or first(system, "peak_buffer_memory") or 0),
            "steps": listed, "more_steps": len(steps) - len(listed)}


# Connections a notebook's cells open with duckdb.connect, by the thread of the cell running now, so
# Cancel reaches them (EA-016). Only a cell's thread records: the app's own connections, when the
# kernel runs inside the engine, are never interrupted by a notebook's Cancel.
# Connections the app opens itself (a module of fabric_desktop calling duckdb.connect, even from a
# cell's thread) are never recorded. _connections_lock guards the recorded sets.
_cell_connections: dict[int, Any] = {}
_connections_lock = threading.Lock()
_duckdb_connect = duckdb.connect


def _connect(*args: Any, **kwargs: Any) -> Any:
    conn = _duckdb_connect(*args, **kwargs)
    watched = _cell_connections.get(threading.get_ident())
    caller = str(sys._getframe(1).f_globals.get("__name__", ""))
    if watched is not None and not caller.startswith("fabric_desktop"):
        with _connections_lock:
            watched.add(conn)
    return conn


def watch_connections(ident: int, connections: Any) -> None:
    """While the cell on thread ``ident`` runs, record the DuckDB connections it opens in ``connections``
    (a WeakSet), or stop recording with None."""
    if connections is None:
        _cell_connections.pop(ident, None)
        return
    if duckdb.connect is not _connect:
        duckdb.connect = _connect
    _cell_connections[ident] = connections


def release_cancelled() -> None:
    """Release a result that a Cancel left unread on ``spark``'s connection. A Cancel that lands after
    DuckDB has run a query but before Python has read its result leaves that result open: it holds
    its memory, and query_progress keeps reporting its last figure under the next cell. A no-op
    query releases it. A connection with nothing open is left alone.

    DuckDB's default connection is released every time: Cancel interrupts it even when the cell never
    used it, and a result an earlier cell read only part of keeps that interrupt. The next query that
    read a file then stopped with "Interrupted!" and every later one failed with "Current transaction
    is aborted". Reading that result raises the interrupt anyway, so releasing it loses nothing."""
    conns = [duckdb.default_connection()] + ([_session.conn] if progress() is not None else [])  # type: ignore[union-attr]
    for conn in conns:
        try:
            conn.execute("SELECT 1").fetchall()
        except Exception:  # noqa: BLE001  # silent-ok: best-effort release; a closed connection holds nothing
            pass


def interrupt(connections: Any = ()) -> None:
    """Stop a running DuckDB query, for Cancel: on ``spark``, DuckDB's default connection and
    ``connections`` (those the notebook opened)."""
    def stop(conns: list[Any]) -> None:
        for conn in conns:
            try:
                conn.interrupt()
            except Exception:  # noqa: BLE001  # silent-ok: best-effort Cancel; a connection with nothing running may refuse
                pass
    stop(([_session.conn] if _session else []) + [duckdb.default_connection()])
    with _connections_lock:  # a snapshot: a cell's thread may be adding to the set
        opened = list(connections)
    stop(opened)
