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
import re
import sys
import weakref
from typing import Any

import duckdb
import pyarrow as pa
from duckdb.experimental.spark.context import SparkContext
from duckdb.experimental.spark.sql import dataframe as _dataframe
from duckdb.experimental.spark.sql.dataframe import DataFrame
from duckdb.experimental.spark.sql.session import SparkSession

from . import lakehouse, spark_api, spark_compat, sql as _sql

_UNSUPPORTED_SQL = re.compile(r"^\s*(MERGE\s+INTO|UPDATE|DELETE\s+FROM)\b", re.I)
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
        rows = self.conn.execute("SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?) "
                                 "UNION ALL SELECT 1 FROM duckdb_tables() WHERE lower(table_name) = lower(?)",
                                 [name, name]).fetchall()
        return bool(rows)

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
        if _RESTORE.match(sqlQuery):
            raise ValueError("RESTORE rewrites a table to an older version, which runs in Fabric. On this PC, read the "
                             "older version (SELECT * FROM t VERSION AS OF n) and overwrite the table with it if you mean to.")
        if _UNSUPPORTED_SQL.match(sqlQuery):
            raise ValueError("MERGE, UPDATE and DELETE change tables in place, which runs in Fabric. "
                             "On this PC, read with spark.sql and write with df.write.saveAsTable.")
        statements = _sql.parse(sqlQuery)
        result: DataFrame | None = None
        for statement in statements:
            result = self._statement(statement)
        return result if result is not None else DataFrame(self.conn.sql("SELECT 1 WHERE false"), self)

    def _statement(self, statement: Any) -> DataFrame | None:
        from sqlglot import exp
        statement = _sql.history_query(statement, self.conn, self._bound())  # DESCRIBE HISTORY t
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
                data = self.conn.sql(query.sql("duckdb"))
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
        text = statement.sql("duckdb")
        if isinstance(statement, (exp.Query, exp.Select, exp.Describe, exp.Show)) or text.upper().startswith(("SELECT", "WITH", "FROM", "VALUES", "DESCRIBE", "SHOW")):
            return DataFrame(self.conn.sql(text), self)
        self.conn.execute(text)
        return None

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


def _tracked_init(self: DataFrame, relation: Any, session: Any) -> None:
    _frame_init(self, relation, session)
    if isinstance(session, LocalSparkSession):
        session._frames.add(self)


_dataframe.DataFrame.__init__ = _tracked_init  # type: ignore[method-assign]
_dataframe.DataFrame.write = property(lambda self: LakehouseWriter(self))  # type: ignore[assignment]


class LocalDeltaTable:
    """``from delta.tables import DeltaTable`` on this PC: reading a table's history and details.
    Changes made through it (merge, update, delete, restore, vacuum, optimize) run in Fabric."""

    def __init__(self, name: str):
        self._name = name

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

    def merge(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("merge")

    def update(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("update")

    def delete(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("delete")

    def vacuum(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("vacuum")

    def optimize(self, *_a: Any, **_kw: Any) -> Any:
        return self._in_fabric("optimize")


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


def interrupt() -> None:
    """Stop a running DuckDB query, for Cancel."""
    for conn in ([_session.conn] if _session else []) + [duckdb.default_connection()]:
        try:
            conn.interrupt()
        except Exception:  # noqa: BLE001  # silent-ok: best-effort Cancel; a connection with nothing running may refuse
            pass
