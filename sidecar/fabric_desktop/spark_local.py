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
import tempfile
import threading
import weakref
from typing import Any

import duckdb
import pyarrow as pa
from duckdb.experimental.spark.context import SparkContext
from duckdb.experimental.spark.sql import dataframe as _dataframe
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.session import SparkSession

from . import lakehouse, spark_api, spark_compat, sql as _sql
from .delta_dml import _LEADING_COMMENTS as delta_dml_comments

_RESTORE = re.compile(r"^\s*RESTORE\s+(TABLE\s+)?", re.I)


def _arrow(df: DataFrame) -> pa.Table:
    rel = df.relation
    return (getattr(rel, "to_arrow_table", None) or rel.arrow)()


# Writer options this PC honors, by lower-case name. Anything else is refused by name, so an
# option that changes what gets written (replaceWhere, partitionOverwriteMode, path) can never be
# dropped and turn into a full overwrite.
_SCHEMA_OPTIONS = ("overwriteschema", "mergeschema")
# Options that only change how files are laid out, not which rows end up in the table.
_LAYOUT_OPTIONS = ("optimizewrite", "maxrecordsperfile", "compression")
_REFUSED = {
    "replacewhere": "replaceWhere (replace only matching rows) isn't supported on this PC, and ignoring it would "
                    "replace the whole table. Run the cell in Fabric, or filter, union and overwrite the table yourself.",
    "partitionoverwritemode": "partitionOverwriteMode isn't supported on this PC, and ignoring it would replace the "
                              "whole table. Run the cell in Fabric.",
    "path": "Writing a table to its own path (an external table) runs in Fabric. Leave out the path option.",
}


_OVERWRITE_MODE = "spark.sql.sources.partitionOverwriteMode"


def _refuse_dynamic_overwrite(session: Any) -> None:
    """A dynamic partitionOverwriteMode set on the session (spark.conf, the builder) replaces only the
    written partitions in Fabric. Tables here have no partitions, so an overwrite would replace the
    whole table: refuse it, as the writer option is refused."""
    try:
        mode = session.conf.get(_OVERWRITE_MODE, "static")
    except (KeyError, NotImplementedError):  # a conf that can't answer has nothing set
        return
    if str(mode).strip().lower() == "dynamic":
        raise NotImplementedError(_REFUSED["partitionoverwritemode"] + " Unset it first: "
                                  f"spark.conf.unset({_OVERWRITE_MODE!r}).")


def _true(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


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
        if name in _REFUSED:
            raise NotImplementedError(_REFUSED[name])
        self._options[name] = value
        return self

    def options(self, **opts: Any) -> LakehouseWriter:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def partitionBy(self, *_cols: Any) -> LakehouseWriter:
        print("Note: partitionBy is ignored on this PC; the table is written without partitions.")
        return self

    def _check_options(self, allowed: tuple[str, ...], what: str) -> None:
        for name in self._options:
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

    def _schema_mode(self, how: str) -> str | None:
        """Delta's schema rules, as Spark applies them: a different schema is an error unless
        overwriteSchema (overwrite only) or mergeSchema allows it."""
        if _true(self._options.get("overwriteschema")):
            if how == "overwrite":
                return "overwrite"
            print("Note: overwriteSchema only applies to an overwrite; this append keeps the table's schema.")
        return "merge" if _true(self._options.get("mergeschema")) else None

    def _write(self, data: pa.Table, name: str, how: str, schema_mode: str | None) -> None:
        if how == "overwrite":
            _refuse_dynamic_overwrite(self._df.session)
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
        print(_write_table(data, name, how, schema_mode))

    def saveAsTable(self, name: str, format: str | None = None, mode: str | None = None,  # noqa: A002
                    partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        if mode:
            self.mode(mode)
        if partitionBy:
            self.partitionBy(partitionBy)
        self.options(**options)
        if (format or self._format) not in ("delta", None):
            raise ValueError("Lakehouse tables are Delta tables. Leave out format, or use format(\"delta\").")
        self._check_options(_SCHEMA_OPTIONS, "a Delta table")
        how = self._write_mode(name)
        if how != "skip":
            self._write(_arrow(self._df), name, how, self._schema_mode(how))

    def insertInto(self, tableName: str, overwrite: bool | None = None) -> None:  # noqa: N802, N803
        """Spark's insertInto: the table must exist, columns match by position, and it appends
        unless ``overwrite=True`` or ``.mode("overwrite")``. The table's schema never changes."""
        self._check_options((), "with insertInto")
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
        self._write(data.rename_columns(current), tableName, "overwrite" if overwrite else "append", None)

    def save(self, path: str | None = None, format: str | None = None, mode: str | None = None,  # noqa: A002
             partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        if mode:
            self.mode(mode)
        if partitionBy:
            self.partitionBy(partitionBy)
        self.options(**options)
        fmt = format or self._format
        rel = lakehouse.relative_path(path) if path else None
        if fmt == "delta" and rel and rel.startswith("Tables/"):
            parts = rel.split("/")[1:]
            return self.saveAsTable(".".join(p for p in parts if p))
        if rel is not None:
            raise ValueError(f"On this PC, write lakehouse tables with saveAsTable or a Tables/ path. "
                             f"Writing files to {rel} runs in Fabric.")
        if fmt == "parquet":
            self._check_options((), "a Parquet file")
            return self._df.relation.write_parquet(path)
        if fmt == "csv":
            self._check_options(("header",), "a CSV file")
            return self._df.relation.write_csv(path, header=_true(self._options.get("header", True)))
        raise ValueError(f"Can't write format {fmt!r} to {path} on this PC.")

    def parquet(self, path: str, mode: str | None = None, partitionBy: Any = None, **options: Any) -> None:  # noqa: N803
        self.save(path, format="parquet", mode=mode, partitionBy=partitionBy, **options)

    def csv(self, path: str, mode: str | None = None, header: Any = None, **options: Any) -> None:
        if header is not None:
            self.option("header", header)
        self.save(path, format="csv", mode=mode, **options)


def _write_table(data: pa.Table, name: str, how: str, schema_mode: str | None) -> Any:
    from deltalake.exceptions import SchemaMismatchError
    try:
        return lakehouse.write_table(data, name, mode=how, schema_mode=schema_mode)
    except SchemaMismatchError as e:
        raise ValueError(f"The data doesn't match table {name}'s schema ({e}). Nothing was written. To replace "
                         "the schema, add .option(\"overwriteSchema\", \"true\") to an overwrite; to add columns, "
                         "add .option(\"mergeSchema\", \"true\").") from None


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


_MERGE_SOURCES = itertools.count(1)
_AUTO_MERGE = "spark.databricks.delta.schema.autoMerge.enabled"
_SCHEMA_EVOLUTION = re.compile(r"\s*MERGE\s+WITH\s+SCHEMA\s+EVOLUTION\b", re.I)


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
        trees = [t for t in sqlglot.parse(query, read="spark") if t is not None]
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
    "option": "a refused writer option, by its literal name (writer_option_refusal)",
    "_check_options": "an option saveAsTable or insertInto doesn't take, by its literal name (writer_option_refusal)",
    "_refuse_dynamic_overwrite": "spark.conf.set of a dynamic partitionOverwriteMode (REFUSED_CODE)",
    "_refuse_auto_merge": "spark.conf.set of autoMerge in a notebook that runs a MERGE (AUTO_MERGE_CODE)",
    "_preview_on": "change_runs_here, with Preview features off",
    "schema": "read.schema() given a string (READ_SCHEMA_TEXT)",
    "_no_schema": "read.schema() on a read of a table or text files (READ_SCHEMA_ON)",
    "sql": "sql_refusal: MERGE WITH SCHEMA EVOLUTION",
    "_statement": "sql_refusal: INSERT with PARTITION or REPLACE WHERE",
    "forPath": "DeltaTable.forPath of a literal path outside Tables/ (REFUSED_CODE)",
    "_in_fabric": "restoreToVersion, restoreToTimestamp, vacuum or optimize on a DeltaTable (DELTA_CHANGES)",
    "withSchemaEvolution": "withSchemaEvolution() on a MERGE (REFUSED_CODE)",
}

_IN_FABRIC_TEXT = "{}, which runs in Fabric (this PC's Spark refuses it)"
# Code this PC's Spark refuses, as (pattern on a Python cell's text, reason).
REFUSED_CODE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"""\.conf\.set\(\s*["']spark\.sql\.sources\.partitionOverwriteMode["']\s*,\s*["']dynamic["']""", re.I),
     "sets a dynamic partitionOverwriteMode, which this PC's Spark refuses: ignoring it would replace the whole table"),
    (re.compile(r"""\bDeltaTable\.forPath\(\s*[^,()]+,\s*[rRuU]?["'](?![^"']*Tables/)"""),
     "opens DeltaTable.forPath outside the lakehouse's Tables/, which this PC's Spark refuses (use DeltaTable.forName)"),
    (re.compile(r"\.withSchemaEvolution\("), _IN_FABRIC_TEXT.format("merges with schema evolution (withSchemaEvolution)")),
]
# DeltaTable methods that change the table, which this PC refuses (LocalDeltaTable._in_fabric).
DELTA_CHANGES = ("restoreToVersion", "restoreToTimestamp", "vacuum", "optimize")
DELTA_CHANGE_TEXT = _IN_FABRIC_TEXT.format("changes a Delta table with DeltaTable.{}")
# Turning on autoMerge only matters to a MERGE, which this PC then refuses (_refuse_auto_merge).
AUTO_MERGE_CODE = re.compile(r"""["']spark\.databricks\.delta\.schema\.autoMerge\.enabled["']\s*,\s*["']?true""", re.I)
AUTO_MERGE_TEXT = ("turns on Delta schema autoMerge, so its MERGE would add the source's new columns: schema evolution "
                   "runs in Fabric (this PC's Spark refuses it)")
# For a cell Python can't read: the refused writer options that only a writer takes (a reader's
# "path" option is fine, so it isn't here). A cell Python reads goes through writer_option_refusal.
REFUSED_OPTION_TEXT = re.compile(r"""\.option\(\s*["'](replaceWhere|partitionOverwriteMode)["']""", re.I)
READ_SCHEMA_TEXT = "gives read.schema() a DDL string, which this PC's Spark refuses (give it a StructType)"
READ_SCHEMA_ON = "applies read.schema() to a read of {}, which this PC's Spark refuses (leave it out and cast the columns after)"
READ_FORMATS = ("delta", "csv", "json", "parquet", "text")
READ_FORMAT_TEXT = "reads format {!r}, which this PC's Spark doesn't have (it reads csv, json, parquet, text and delta)"
TABLE_FORMAT_TEXT = "saves a lakehouse table as {!r}, which this PC's Spark refuses: lakehouse tables are Delta tables"
# The options each table write takes on this PC (_check_options), by lower-case name.
WRITE_TAKES = {"saveAsTable": _SCHEMA_OPTIONS + _LAYOUT_OPTIONS, "insertInto": _LAYOUT_OPTIONS}


def writer_option_refusal(name: str, terminal: str | None) -> str | None:
    """Why this PC's writer refuses option ``name`` on a write that ends in ``terminal`` (saveAsTable,
    insertInto, or None when the check can't tell), or None when it takes it."""
    key = name.lower()
    if key in _REFUSED:
        first = _REFUSED[key].split(". ")[0]
        return f"uses the {name} write option: {first[0].lower()}{first[1:].rstrip('.')}"
    takes = WRITE_TAKES.get(terminal or "")
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
    if _RESTORE.match(head):
        return "blocked", _IN_FABRIC_TEXT.format("restores a table to an older version (RESTORE)")
    if _SCHEMA_EVOLUTION.match(head):
        return "blocked", _IN_FABRIC_TEXT.format("merges WITH SCHEMA EVOLUTION")
    try:
        trees = [t for t in sqlglot.parse(query, read="spark") if t is not None]
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


def _refuse_auto_merge(session: Any) -> None:
    """With autoMerge on, Fabric's MERGE adds a source's new columns to the table. This PC's MERGE
    keeps the table's schema, so it would drop them without saying so: refuse it, as
    withSchemaEvolution is refused."""
    try:
        value = session.conf.get(_AUTO_MERGE, "false")
    except (KeyError, NotImplementedError):  # a conf that can't answer has nothing set
        return
    if str(value).strip().lower() == "true":
        raise NotImplementedError(f"{_AUTO_MERGE} is on, so in Fabric this MERGE would add the source's new columns "
                                  "to the table. Schema evolution in MERGE runs in Fabric. Run the cell in Fabric, or "
                                  f"turn it off first: spark.conf.unset({_AUTO_MERGE!r}).")


def _preview_on(what: str) -> None:
    """Changing a table in place on this PC is in Preview (PB-015): with Preview features off
    (Settings, or the organization's policy) it runs in Fabric only, as before."""
    if not lakehouse._setting("show_preview", True):
        raise NotImplementedError(f"{what} on this PC is a Preview feature, and Preview features are off in Settings "
                                  "or by your organization's policy. Run the cell in Fabric.")


def _columns(table: str) -> list[str] | None:
    """The table's column names, or None when it doesn't exist."""
    from deltalake.exceptions import TableNotFoundError
    try:
        return [f.name for f in lakehouse.open_table(table).schema().fields]
    except TableNotFoundError:
        return None


class LakehouseReader:
    """``spark.read``: tables and files of the attached lakehouse, or files on this PC."""

    def __init__(self, session: LocalSparkSession):
        self._session = session
        self._format: str | None = None
        self._options: dict[str, Any] = {}

    def format(self, fmt: str) -> LakehouseReader:
        self._format = fmt.lower()
        return self

    def option(self, key: str, value: Any) -> LakehouseReader:
        self._options[str(key).lower()] = value
        return self

    def options(self, **opts: Any) -> LakehouseReader:
        for k, v in opts.items():
            self.option(k, v)
        return self

    def schema(self, schema: Any) -> LakehouseReader:
        """Applied after reading: CSV columns take the schema's names and types by position, other
        formats by name (a missing column is null). A value that doesn't fit is null, as in Spark's
        default PERMISSIVE mode."""
        if isinstance(schema, str) or not hasattr(schema, "fields"):
            raise NotImplementedError("On this PC, give read.schema() a StructType. A DDL string isn't supported.")
        self._schema = schema
        return self

    def _apply(self, df: DataFrame, by_position: bool) -> DataFrame:
        schema = getattr(self, "_schema", None)
        if schema is None:
            return df
        rel = df.relation
        have = rel.columns
        if by_position and len(have) != len(schema.fields):
            raise ValueError(f"The schema has {len(schema.fields)} columns and the file has {len(have)}.")
        lower = {c.lower(): c for c in have}
        cols = []
        for i, f in enumerate(schema.fields):
            src = have[i] if by_position else lower.get(f.name.lower())
            value = "NULL" if src is None else '"' + src.replace('"', '""') + '"'
            cols.append(f'TRY_CAST({value} AS {f.dataType.duckdb_type}) AS "' + f.name.replace('"', '""') + '"')
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
        if self._as_of():
            data = lakehouse.open_table(name, **self._as_of()).to_pyarrow_dataset()
            return self._session._from_arrow_dataset(data)
        return self._session.table(name)

    def _local(self, path: str) -> str:
        return str(lakehouse.local_path(path))

    def load(self, path: str | None = None, format: str | None = None, **opts: Any) -> DataFrame:  # noqa: A002
        self.options(**opts)
        fmt = (format or self._format or "parquet").lower()
        rel = lakehouse.relative_path(path) if path else None
        if fmt == "delta":
            self._no_schema("a Delta table")
            if rel and rel.startswith("Tables/"):
                return self.table(".".join(p for p in rel.split("/")[1:] if p))
            from deltalake import DeltaTable
            opened = DeltaTable(path)
            point = lakehouse.as_of(**self._as_of())
            if point is not None:
                opened.load_as_version(point)
            return self._session._from_arrow_dataset(opened.to_pyarrow_dataset())
        if fmt == "csv":
            return self.csv(path)
        if fmt == "json":
            return self.json(path)
        if fmt == "parquet":
            return self.parquet(path)
        if fmt == "text":
            return self.text(path)
        raise ValueError(f"Format {fmt!r} isn't supported on this PC. Use csv, json, parquet, text or delta.")

    def csv(self, path: str, header: Any = None, sep: str | None = None, inferSchema: Any = None, **opts: Any) -> DataFrame:
        self.options(**opts)
        header = self._options.get("header", header)
        sep = self._options.get("sep", self._options.get("delimiter", sep))
        conn = self._session.conn
        rel = conn.read_csv(self._local(path), header=header in (True, "true", "True"),
                            **({"sep": sep} if sep else {}), **({"all_varchar": True} if getattr(self, "_schema", None) else {}))
        return self._apply(DataFrame(rel, self._session), by_position=True)

    def parquet(self, *paths: str, **_opts: Any) -> DataFrame:
        files = [self._local(p) for p in paths]
        return self._apply(DataFrame(self._session.conn.read_parquet(files if len(files) > 1 else files[0]), self._session), False)

    def json(self, path: str, **_opts: Any) -> DataFrame:
        return self._apply(DataFrame(self._session.conn.read_json(self._local(path)), self._session), False)

    def text(self, path: str, **_opts: Any) -> DataFrame:
        self._no_schema("text files")
        conn = self._session.conn
        rel = conn.sql("SELECT line AS value FROM read_csv(?, columns={'line': 'VARCHAR'}, header=false, "
                       "delim=chr(1), quote='', escape='')", params=[self._local(path)])
        return DataFrame(rel, self._session)


class LocalSparkSession(SparkSession):
    """DuckDB's SparkSession, reading and writing the attached lakehouse."""

    def __init__(self) -> None:
        super().__init__(SparkContext("local"))
        self.conn.execute("SET TimeZone = 'UTC'")  # like Fabric's Spark sessions, not this PC's zone
        # The running query's progress, for the line under a running cell (progress()); never printed.
        self.conn.execute("SET enable_progress_bar = true")
        self.conn.execute("SET enable_progress_bar_print = false")
        self._views: dict[str, str] = {}
        self._snapshot = None
        self._frames: weakref.WeakSet[DataFrame] = weakref.WeakSet()  # this session's DataFrames still in use
        self._conf = spark_api.LocalRuntimeConfig(self)  # spark.conf (spark_compat's RuntimeConfig patches don't apply)
        self._local_context = spark_api.LocalSparkContext(self)
        catalog = self.catalog
        catalog.tableExists = lambda tableName, dbName=None: spark_api.table_exists(self, tableName, dbName)  # noqa: N803

    @property
    def sparkContext(self) -> spark_api.LocalSparkContext:  # noqa: N802
        return self._local_context

    @property
    def version(self) -> str:
        return spark_api.SPARK_VERSION

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
        _sql.bind_tables(statement, self.conn, views, self._is_local)
        old = _sql.superseded(views)
        if not old:
            return
        # A DataFrame is a query over the view's name, run when it's used: a view one still reads stays.
        used = [frame.relation.sql_query() for frame in list(self._frames) if frame.relation is not None]
        used += [sql for (sql,) in self.conn.execute("SELECT sql FROM duckdb_views() WHERE NOT internal").fetchall() if sql]
        text = "\n".join(used)
        for key in old:
            view = views[key]
            if not re.search(rf"\b{view}\b", text):
                self.conn.unregister(view)
                del views[key]

    def _is_view(self, name: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?)", [name]).fetchall())

    def _is_local(self, name: str) -> bool:
        return _sql.is_local(self.conn, name)

    def _from_arrow_dataset(self, data: Any) -> DataFrame:
        return DataFrame(self.conn.from_arrow(data), self)

    @property
    def read(self) -> LakehouseReader:
        return LakehouseReader(self)

    def table(self, tableName: str) -> DataFrame:  # noqa: N803
        if "." not in tableName and self._is_local(tableName):
            return DataFrame(self.conn.table(tableName), self)
        return self.sql(f"SELECT * FROM {tableName}")

    def sql(self, sqlQuery: str, **kwargs: Any) -> DataFrame:  # noqa: N803
        if kwargs:
            sqlQuery = sqlQuery.format(**kwargs)
        head = delta_dml_comments.sub("", sqlQuery)  # a leading comment doesn't hide RESTORE or SCHEMA EVOLUTION
        if _RESTORE.match(head):
            raise ValueError("RESTORE rewrites a table to an older version, which runs in Fabric. On this PC, read the "
                             "older version (SELECT * FROM t VERSION AS OF n) and overwrite the table with it if you mean to.")
        if _SCHEMA_EVOLUTION.match(head):
            raise NotImplementedError("MERGE WITH SCHEMA EVOLUTION adds the source's new columns to the table, which "
                                      "runs in Fabric. Run the cell in Fabric, or leave out WITH SCHEMA EVOLUTION.")
        statements = _sql.parse(sqlQuery)
        from sqlglot import exp
        if len(statements) > 1 and any(isinstance(s, (exp.Merge, exp.Update, exp.Delete)) for s in statements):
            # The change would commit before a later statement could fail; Spark runs one statement per call.
            raise ValueError("spark.sql runs MERGE INTO, UPDATE and DELETE only as the only statement in the call. "
                             "Nothing ran. Call spark.sql once for each statement.")
        result: DataFrame | None = None
        for statement in statements:
            result = self._statement(statement)
        return result if result is not None else DataFrame(self.conn.sql("SELECT 1 WHERE false"), self)

    def _statement(self, statement: Any) -> DataFrame | None:
        from sqlglot import exp
        statement = _sql.history_query(statement, self.conn, self._bound())  # DESCRIBE HISTORY t
        if isinstance(statement, exp.Merge):
            return self._merge(statement)
        if isinstance(statement, (exp.Update, exp.Delete)):
            return self._update_or_delete(statement)
        # DROP TABLE and TRUNCATE TABLE on a lakehouse table; a temp view is DuckDB's to drop.
        if isinstance(statement, exp.TruncateTable) or (
                isinstance(statement, exp.Drop) and str(statement.args.get("kind", "")).upper() == "TABLE"):
            tables = statement.expressions if isinstance(statement, exp.TruncateTable) else statement.args.get("tables") or []
            names = [".".join(p for p in (t.db, t.name) if p) for t in tables
                     if t.db or not self._is_local(t.name)]
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
                name = f"{target.db}.{target.name}" if target.db else target.name
                query = statement.expression.copy()
                self._bind(query)
                data = self.conn.sql(_sql.duckdb_sql(query))
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
                    print(_write_table(arrow.rename_columns(current), name, mode, None))
                else:
                    print(lakehouse.write_table(arrow, name, mode=mode))
                return None
        self._bind(statement)
        text = _sql.duckdb_sql(statement)
        if isinstance(statement, (exp.Query, exp.Select, exp.Describe, exp.Show)) or text.upper().startswith(("SELECT", "WITH", "FROM", "VALUES", "DESCRIBE", "SHOW")):
            return DataFrame(self.conn.sql(text), self)
        self.conn.execute(text)
        return None

    def _merge(self, tree: Any) -> DataFrame:
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
        _refuse_auto_merge(self)
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
        names = [cased.get(f.name.lower(), f.name) for f in safe]
        source = source.rename_columns(names).cast(pa.schema([pa.field(n, f.type) for n, f in zip(names, safe)]))
        self._one_match_each(change, source)
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

    def _one_match_each(self, change: Any, source: pa.Table) -> None:
        """Spark refuses a MERGE in which one row of the table matches two source rows (which one
        would change it is undefined); delta-rs doesn't, so it is checked here for ON t.a = s.a [AND ...]."""
        import sqlglot

        from . import delta_dml
        keys = delta_dml.equi_keys(change)
        matched = [w for w in change.whens if w.match == "matched"]
        if not keys or not matched or not source.num_rows:
            return
        if len(matched) == 1 and matched[0].action == "delete" and matched[0].condition is None:
            return  # Spark allows it: every source row deletes the same table row, so the result is defined
        view = f"__merge_source_{next(_MERGE_SOURCES)}"
        self.conn.register(view, source)
        try:
            sa, ta = change.source_alias or "s", change.alias or change.table
            group = ", ".join(f"`{sa}`.`{s_col}`" for _t, s_col in keys)
            target = f"`{change.schema}`.`{change.table}`" if change.schema else f"`{change.table}`"
            check = sqlglot.parse_one(
                f"SELECT {group} FROM {view} AS `{sa}` WHERE EXISTS (SELECT 1 FROM {target} AS `{ta}` WHERE "
                f"{change.on.sql(dialect='spark')}) GROUP BY {group} HAVING count(*) > 1 LIMIT 1", read="spark")
            self._bind(check)
            duplicate = self.conn.execute(_sql.duckdb_sql(check)).fetchone()
        finally:
            self.conn.unregister(view)
        if duplicate is not None:
            raise ValueError(f"More than one source row matches the same row of {change.name} (source key "
                             f"{', '.join(str(v) for v in duplicate)}), so which one changes it is undefined, and "
                             "Spark refuses it too. Keep one source row per key, for example with QUALIFY "
                             "row_number() OVER (PARTITION BY key ORDER BY ...) = 1.")

    def createDataFrame(self, data: Any, schema: Any = None, samplingRatio: Any = None,  # noqa: N802, N803
                        verifySchema: bool = True) -> DataFrame:  # noqa: N803
        module = type(data).__module__
        if module.startswith(("polars", "pyarrow")):
            arrow = data.to_arrow() if module.startswith("polars") else data
            return DataFrame(self.conn.from_arrow(arrow), self)
        if isinstance(schema, str) and _DDL.search(schema):  # "id int, name string", as in PySpark
            schema = ddl_schema(schema)
        if verifySchema:  # verifySchema=False skips the check, as in Spark
            check_not_null(data, schema)
        # DuckDB's own createDataFrame refuses samplingRatio and verifySchema=False; both only change
        # how Spark infers and checks the schema, which is done above.
        return super().createDataFrame(data, schema)


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

    def _in_fabric(self, what: str) -> Any:
        raise NotImplementedError(f"DeltaTable.{what} changes the table, which runs in Fabric. On this PC, read "
                                  "with spark.read and write with df.write.saveAsTable.")

    def restoreToVersion(self, version: int) -> Any:  # noqa: N802, ARG002
        return self._in_fabric("restoreToVersion")

    def restoreToTimestamp(self, timestamp: str) -> Any:  # noqa: N802, ARG002
        return self._in_fabric("restoreToTimestamp")

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

    def vacuum(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("vacuum")

    def optimize(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("optimize")


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
        raise NotImplementedError("MERGE with schema evolution runs in Fabric. On this PC, add the new columns "
                                  "first (write with mergeSchema), then merge.")

    def execute(self) -> DataFrame:
        if not self._clauses:
            raise ValueError("The merge has no whenMatched or whenNotMatched clause, so there is nothing to do.")
        relation = getattr(self._source, "relation", None)
        if relation is None:
            raise TypeError("DeltaTable.merge needs a spark DataFrame as its source.")
        spark = session()
        view = f"__merge_builder_{next(self._MERGE_VIEWS)}"
        source_alias = getattr(relation, "alias", "") or ""
        if not source_alias or source_alias.startswith("unnamed_relation"):  # no df.alias(...)
            source_alias = "source"
        relation.create_view(view, replace=True)
        try:
            target = self._table._name + (f" AS `{self._table._alias}`" if self._table._alias else "")
            return spark.sql(f"MERGE INTO {target} USING {view} AS `{source_alias}` ON {self._on} "
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
    spark_sql.Window = spark_compat.Window  # type: ignore[attr-defined]
    # ``SparkSession.builder.getOrCreate()`` in a cell returns the notebook's session.
    spark_sql.SparkSession = LocalSparkSession  # type: ignore[attr-defined]
    LocalSparkSession.builder = _Builder()  # type: ignore[attr-defined]


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


def install(namespace: dict[str, Any]) -> None:
    spark_api.install()
    _alias_pyspark()
    _alias_delta()
    from . import spark_stream
    spark_stream.install()
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
    return round(p, 1) if p is not None and 0 < p <= 100 else None


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
                steps.append({"depth": depth, "name": name, "rows": rows, "seconds": round(seconds, 6),
                              "details": details})
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


def interrupt() -> None:
    """Stop a running DuckDB query, for Cancel."""
    for conn in ([_session.conn] if _session else []) + [duckdb.default_connection()]:
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001  # silent-ok: best-effort Cancel; a connection with nothing running may refuse
            pass
