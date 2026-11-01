"""Delta tables a local project writes, and reading a Delta table as it was (FD-LDE-005).

A project's own Delta tables live in its folder the way a schema-enabled lakehouse lays them out,
``<project>/Tables/<schema>/<table>``, so a later publish maps one to one onto OneLake. They are
written with delta-rs from a query's Arrow stream (no row limit, nothing held in memory), in one
commit each, and kept inside the conservative profile Fabric reads reliably (delta_profile.py):
reader version 1, writer version 2, no table features. So column types are made Fabric-safe
first: a DuckDB TIMESTAMP (no time zone) is written as a UTC timestamp, since a Delta
``timestamp_ntz`` column would need reader version 3; unsigned integers widen to the next signed
type. Types Delta has no column for (TIME, INTERVAL, all-NULL columns) are refused with what to
cast them to.

SQL that writes them, in either dialect:

    CREATE [OR REPLACE] TABLE [IF NOT EXISTS] sales USING DELTA [PARTITIONED BY (year)] AS SELECT ...
    INSERT INTO sales SELECT ...           (sales is one of the project's Delta tables: append)
    INSERT OVERWRITE [TABLE] sales SELECT ...

and SQL that reads one as it was:

    Spark SQL:   SELECT * FROM sales VERSION AS OF 3      ... TIMESTAMP AS OF '2026-10-01'
    DuckDB SQL:  SELECT * FROM sales AT (VERSION => 3)    ... AT (TIMESTAMP => '2026-10-01')
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import sqlglot
from sqlglot import exp

from .. import sql_dialects


def _duckdb(node: exp.Expr) -> str:
    """``node`` as DuckDB SQL, refused when sqlglot would leave part of it out (#1253)."""
    from .. import sql
    return sql.duckdb_sql(node)


NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,255}$")
# Characters Fabric (Spark, the SQL endpoint) can't take in a column name without column mapping,
# which is outside the Fabric-safe profile.
_BAD_COLUMN = re.compile(r"[ ,;{}()\n\t=]")
_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*)'
_QNAME = rf"(?P<name>{_IDENT}(?:\s*\.\s*{_IDENT})?)"
_CREATE = re.compile(
    rf"^\s*CREATE\s+(?P<replace>OR\s+REPLACE\s+)?TABLE\s+(?P<ine>IF\s+NOT\s+EXISTS\s+)?{_QNAME}\s+USING\s+DELTA\b\s*"
    r"(?:PARTITIONED\s+BY\s*\((?P<parts>[^)]*)\)\s*)?AS\b(?P<query>.*)$", re.IGNORECASE | re.DOTALL)
_INSERT = re.compile(rf"^\s*INSERT\s+(?P<how>INTO|OVERWRITE)\s+(?:TABLE\s+)?{_QNAME}\s*(?P<byname>BY\s+NAME\b)?"
                     r"(?P<query>.*)$", re.IGNORECASE | re.DOTALL)
_COLUMN_LIST = re.compile(rf"^\s*\(\s*{_IDENT}\s*(,\s*{_IDENT}\s*)*\)", re.IGNORECASE)
_ROWS = re.compile(r"^\s*(\(|SELECT\b|WITH\b|FROM\b|VALUES\b|TABLE\b)", re.IGNORECASE)
_AS_OF_HINT = re.compile(r"\b(VERSION|TIMESTAMP)\s+AS\s+OF\b|\bAT\s*\(\s*(VERSION|TIMESTAMP)\s*=>", re.IGNORECASE)
_LEADING_COMMENTS = re.compile(r"^(\s*(--[^\n]*(\n|$)|/\*.*?\*/))+", re.DOTALL)
_USING_DELTA = re.compile(r"\bUSING\s+DELTA\b", re.IGNORECASE)


@dataclass
class Write:
    """A statement that writes one of the project's Delta tables."""
    schema: str | None
    table: str
    mode: str                    # create | append | overwrite
    query: str                   # the rows, as DuckDB SQL
    if_not_exists: bool = False  # CREATE TABLE IF NOT EXISTS: nothing to do when it exists
    replace: bool = False        # CREATE OR REPLACE: a new schema and partitioning are fine
    partition_by: list[str] = field(default_factory=list)
    by_position: bool = False    # INSERT matches columns by position, as SQL does; the rest by name
    schema_mode: str | None = None  # merge: add new columns; overwrite (with overwrite): a new schema
    as_of: list["AsOf"] = field(default_factory=list)


@dataclass
class AsOf:
    """A table reference read at a version or a time, replaced by a temporary view."""
    placeholder: str
    schema: str | None
    table: str
    version: int | None = None
    timestamp: str | None = None


def unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in '"`':
        return name[1:-1].replace(name[0] * 2, name[0])
    return name


_PART = rf'{_IDENT}|`(?:[^`]|``)+`'
_FULL_NAME = re.compile(rf"^\s*(?:(?P<schema>{_PART})\s*\.\s*)?(?P<table>{_PART})\s*$")


def split_name(name: str) -> tuple[str | None, str]:
    """``schema.table`` or ``table`` (quoted or not) as (schema or None, table). Anything else comes
    back whole as the table, for check_name to refuse."""
    m = _FULL_NAME.match(name or "")
    if not m:
        return None, name
    return (unquote(m["schema"]) if m["schema"] else None), unquote(m["table"])


def check_name(schema: str, table: str) -> None:
    for kind, value in (("schema", schema), ("table", table)):
        if not NAME.match(value or "") or value.lower().startswith("__fd_"):
            raise ValueError(f"{value!r} can't be a Delta {kind} name: use letters, digits and underscores, "
                             "starting with a letter (as Fabric lakehouse tables do).")


# Statements

def parse(statement: str, is_delta: Any) -> Write | None:
    """The Delta write a DuckDB SQL ``statement`` makes, or None when it's ordinary SQL.

    ``is_delta(schema, table)`` says whether a name is one of the project's own Delta tables: an
    INSERT into anything else is left to DuckDB. The rows' query is kept as it was written."""
    statement = _LEADING_COMMENTS.sub("", statement)
    m = _CREATE.match(statement)
    if m:
        schema, table = split_name(m["name"])
        parts = [unquote(p) for p in (m["parts"] or "").split(",") if p.strip()]
        return Write(schema, table, "overwrite" if m["replace"] else "create", _rows(m["query"]),
                     if_not_exists=bool(m["ine"]), replace=bool(m["replace"]), partition_by=parts)
    if _USING_DELTA.search(statement) and re.match(r"^\s*CREATE\b", statement, re.IGNORECASE):
        raise ValueError("Write a local Delta table as CREATE TABLE name USING DELTA [PARTITIONED BY (col)] "
                         "AS SELECT ...")
    m = _INSERT.match(statement)
    if m:
        schema, table = split_name(m["name"])
        if not is_delta(schema, table):
            return None
        if _COLUMN_LIST.match(m["query"]) or not _ROWS.match(m["query"]):
            raise ValueError(f"Add rows to the Delta table {table} with INSERT INTO {table} SELECT ...: "
                             "a column list isn't supported for Delta tables yet.")
        return Write(schema, table, "overwrite" if m["how"].upper() == "OVERWRITE" else "append", _rows(m["query"]),
                     by_position=not m["byname"])
    return None


def _rows(query: str) -> str:
    query = query.strip().rstrip(";").strip()
    if not _ROWS.match(query):
        raise ValueError("A Delta write needs a query for its rows (AS SELECT ...).")
    return query


def parse_tree(tree: exp.Expression, is_delta: Any) -> Write | None:
    """The Delta write a parsed Spark SQL statement makes (its rows' query as DuckDB SQL), or None."""
    if isinstance(tree, exp.Create) and tree.args.get("kind") == "TABLE":
        props = tree.args.get("properties")
        listed = props.expressions if props else []
        if not any(isinstance(p, exp.FileFormatProperty) and p.name.upper() == "DELTA" for p in listed):
            return None
        query = tree.expression
        if query is None:
            raise ValueError("Write a local Delta table as CREATE TABLE name USING DELTA AS SELECT ...")
        target = tree.this if isinstance(tree.this, exp.Table) else tree.this.this
        parts: list[str] = []
        for p in listed:
            if isinstance(p, exp.PartitionedByProperty):
                cols = p.this.expressions if isinstance(p.this, (exp.Schema, exp.Tuple)) else [p.this]
                parts += [c.name for c in cols]
        replace = bool(tree.args.get("replace"))
        return Write(target.db or None, target.name, "overwrite" if replace else "create",
                     _duckdb(query), if_not_exists=bool(tree.args.get("exists")), replace=replace,
                     partition_by=parts)
    if isinstance(tree, exp.Insert):
        target = tree.this
        if isinstance(target, exp.Schema) and isinstance(target.this, exp.Table):
            if is_delta(target.this.db or None, target.this.name):
                raise ValueError(f"Add rows to the Delta table {target.this.name} with INSERT INTO "
                                 f"{target.this.name} SELECT ...: a column list isn't supported for Delta tables yet.")
            return None
        if not isinstance(target, exp.Table) or not is_delta(target.db or None, target.name) or tree.expression is None:
            return None
        return Write(target.db or None, target.name, "overwrite" if tree.args.get("overwrite") else "append",
                     _duckdb(tree.expression), by_position=not tree.args.get("by_name"))
    return None


def rewrite_as_of(sql: str, start: int = 0) -> tuple[str, list[AsOf]]:
    """DuckDB SQL ``sql`` with each table read AT a version or time replaced by a placeholder view.

    Only SQL that mentions a version or a time is parsed here; anything else comes back as it was."""
    if not _AS_OF_HINT.search(sql):
        return sql, []
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise ValueError(f"Could not read the query's AT (VERSION/TIMESTAMP => ...) clause: {sql_dialects.parse_problem(exc)}") from None
    found = rewrite_tree(tree, start)
    return (_duckdb(tree), found) if found else (sql, [])


def rewrite_tree(tree: exp.Expression, start: int = 0) -> list[AsOf]:
    """Replace, in ``tree``, each table read at a version or time with a placeholder view's name."""
    found: list[AsOf] = []
    for table in list(tree.find_all(exp.Table)):
        version = table.args.get("version")   # Spark: VERSION AS OF n, TIMESTAMP AS OF '...'
        when = table.args.get("when")         # DuckDB: AT (VERSION => n)
        clause = version if isinstance(version, exp.Version) else when if isinstance(when, exp.HistoricalData) else None
        if clause is None:
            continue
        kind = (clause.name if isinstance(clause, exp.Version) else str(clause.args.get("kind", ""))).upper()
        value = clause.expression
        item = AsOf(f"__fd_as_of_{start + len(found)}", table.db or None, table.name)
        if kind == "VERSION":
            if not (isinstance(value, exp.Literal) and not value.is_string and str(value.this).isdigit()):
                raise ValueError(f"VERSION AS OF for {table.name} takes a version number.")
            item.version = int(value.this)
        elif kind == "TIMESTAMP":
            if not isinstance(value, exp.Literal) or not value.is_string:
                raise ValueError(f"TIMESTAMP AS OF for {table.name} takes a date or time in quotes.")
            item.timestamp = value.this
        else:
            raise ValueError(f"Read {table.name} as of a VERSION or a TIMESTAMP.")
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(item.placeholder), alias=alias))
        found.append(item)
    return found


def as_of_time(text: str) -> dt.datetime:
    """A TIMESTAMP AS OF value: a date or date and time, in UTC unless it says otherwise."""
    try:
        value = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{text!r} isn't a date or time (use 2026-10-01 or 2026-10-01 14:30:00).") from None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


# Fabric-safe column types

_UNSIGNED = {8: pa.int16(), 16: pa.int32(), 32: pa.int64(), 64: pa.decimal128(20, 0)}


def fabric_schema(schema: pa.Schema, names: bool = True) -> pa.Schema:
    """``schema`` with every column in a type Fabric reads from a plain (reader 1) Delta table.

    ``names``: check the column names too (not when they'll be the table's, by position)."""
    if not len(schema):
        raise ValueError("The query returns no columns, so there is nothing to write.")
    seen: dict[str, str] = {}
    fields = []
    for f in schema:
        if not names:
            fields.append(pa.field(f.name, _fabric_type(f.type, f.name), nullable=True))
            continue
        if _BAD_COLUMN.search(f.name) or not f.name:
            raise ValueError(f"Column {f.name!r} has a space or one of , ; {{ }} ( ) =, which Fabric only reads "
                             "with column mapping. Rename it in the query (AS a_name).")
        if f.name.lower() in seen:
            raise ValueError(f"Columns {seen[f.name.lower()]!r} and {f.name!r} differ only in case; Delta "
                             "column names ignore case. Rename one.")
        seen[f.name.lower()] = f.name
        fields.append(pa.field(f.name, _fabric_type(f.type, f.name), nullable=True))
    return pa.schema(fields)


def _fabric_type(t: pa.DataType, column: str) -> pa.DataType:
    if pa.types.is_timestamp(t):
        return pa.timestamp("us", tz="UTC")
    if pa.types.is_unsigned_integer(t):
        return _UNSIGNED[t.bit_width]
    if pa.types.is_float16(t):
        return pa.float32()
    if pa.types.is_large_string(t) or getattr(pa.types, "is_string_view", lambda _: False)(t):
        return pa.string()
    if pa.types.is_large_binary(t) or getattr(pa.types, "is_binary_view", lambda _: False)(t) or \
            pa.types.is_fixed_size_binary(t):
        return pa.binary()
    if pa.types.is_dictionary(t):
        return _fabric_type(t.value_type, column)
    if pa.types.is_decimal(t):
        if t.precision > 38:
            raise ValueError(f"Column {column} is a decimal with {t.precision} digits; Delta holds up to 38. "
                             "Cast it to DECIMAL(38, s) or DOUBLE.")
        return pa.decimal128(t.precision, t.scale)
    if pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t) or \
            getattr(pa.types, "is_list_view", lambda _: False)(t) or \
            getattr(pa.types, "is_large_list_view", lambda _: False)(t):
        return pa.list_(_fabric_type(t.value_type, column))
    if pa.types.is_map(t):
        return pa.map_(_fabric_type(t.key_type, column), _fabric_type(t.item_type, column))
    if pa.types.is_struct(t):
        return pa.struct([pa.field(c.name, _fabric_type(c.type, f"{column}.{c.name}"), nullable=True) for c in t])
    if pa.types.is_null(t):
        raise ValueError(f"Column {column} is NULL in every row, so it has no type. Cast it, for example "
                         f"CAST(NULL AS VARCHAR) AS {column}.")
    if pa.types.is_time(t):
        raise ValueError(f"Column {column} is a TIME, and Delta has no time-of-day type. Cast it to VARCHAR, "
                         "or combine it with a date into a TIMESTAMP.")
    if pa.types.is_duration(t) or pa.types.is_interval(t):
        raise ValueError(f"Column {column} is an INTERVAL, which Delta can't hold. Cast it to a number "
                         "(for example epoch(x) seconds) or to VARCHAR.")
    if isinstance(t, pa.ExtensionType):
        return _fabric_type(t.storage_type, column)
    return t


def delta_writable(t: pa.DataType) -> bool:
    """Whether delta-rs writes a column of type ``t`` as it is. Unsigned integers, float16, decimals
    over 38 digits, TIME and INTERVAL it refuses with a raw error; NULL (no type) it writes as a void
    column Spark can't use, and an append of one hangs it (deltalake 1.6.6)."""
    if pa.types.is_unsigned_integer(t) or pa.types.is_float16(t) or pa.types.is_null(t) or pa.types.is_time(t) \
            or pa.types.is_duration(t) or pa.types.is_interval(t) or (pa.types.is_decimal(t) and t.precision > 38):
        return False
    if pa.types.is_dictionary(t):
        return delta_writable(t.value_type)
    if pa.types.is_map(t):
        return delta_writable(t.key_type) and delta_writable(t.item_type)
    if pa.types.is_struct(t):
        return all(delta_writable(f.type) for f in t)
    value = getattr(t, "value_type", None)  # lists of every kind
    return value is None or delta_writable(value)


def naive_timestamps(schema: pa.Schema) -> list[str]:
    """The columns (a.b for a struct field, a[] for list items) that hold a timestamp without a time
    zone, which delta-rs writes as timestampNtz: a reader 3 table Fabric can't read."""
    def walk(t: pa.DataType, name: str) -> list[str]:
        if pa.types.is_timestamp(t):
            return [] if t.tz else [name]
        if pa.types.is_dictionary(t):
            return walk(t.value_type, name)
        if pa.types.is_struct(t):
            return [n for f in t for n in walk(f.type, f"{name}.{f.name}")]
        if pa.types.is_map(t):
            return walk(t.key_type, f"{name}{{key}}") + walk(t.item_type, f"{name}{{value}}")
        value = getattr(t, "value_type", None)
        return walk(value, f"{name}[]") if value is not None else []
    return [n for f in schema for n in walk(f.type, f.name)]


def delta_safe(schema: pa.Schema, existing: Any = None) -> pa.Schema:
    """``schema`` with each column delta-rs can't write (delta_writable) in the type Fabric reads,
    as fabric_schema writes it, or fabric_schema's plain error saying how to cast it. Every other
    column is left exactly as it is, so tables written today keep their types. ``existing``, for an
    append: a function giving the table's column types by lower-case name, so an all-NULL column
    takes the table's type."""
    if all(delta_writable(f.type) for f in schema):
        return schema
    table: dict[str, pa.DataType] | None = None
    fields = []
    for f in schema:
        if delta_writable(f.type):
            fields.append(f)
            continue
        if pa.types.is_null(f.type) and existing is not None:
            table = existing() if table is None else table
            if f.name.lower() in table:
                fields.append(pa.field(f.name, table[f.name.lower()], nullable=True))
                continue
        fields.append(pa.field(f.name, _fabric_type(f.type, f.name), nullable=True, metadata=f.metadata))
    return pa.schema(fields, metadata=schema.metadata)


def fits(source: pa.Schema, target: pa.Schema) -> list[str]:
    """Why rows of ``source`` can't be added to a table of ``target`` as they are (empty: they can).

    Columns may come in another order, and a value may be stored in another type of the same kind
    (a number in any number column, a date in a timestamp column), as SQL's INSERT allows. Each
    value is converted as it is written, and one that doesn't fit (300 in a SMALLINT, 2.5 in an
    INT) stops the write before anything is committed. Any other difference is a schema change."""
    problems = []
    have = {f.name: f for f in source}
    for f in target:
        if f.name not in have:
            problems.append(f"{f.name} is missing")
    for f in source:
        if f.name not in target.names:
            problems.append(f"{f.name} is new")
        elif not _assignable(f.type, target.field(f.name).type):
            problems.append(f"{f.name} is {f.type}, the table has {target.field(f.name).type}")
    return problems


def _numeric(t: pa.DataType) -> bool:
    return pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t)


def _assignable(src: pa.DataType, dst: pa.DataType) -> bool:
    if src.equals(dst):
        return True
    if _numeric(src) and _numeric(dst):
        return True
    if pa.types.is_timestamp(dst) and (pa.types.is_timestamp(src) or pa.types.is_date(src)):
        return True
    if pa.types.is_string(src) and pa.types.is_string(dst) or pa.types.is_binary(src) and pa.types.is_binary(dst):
        return True
    if pa.types.is_list(src) and pa.types.is_list(dst):
        return _assignable(src.value_type, dst.value_type)
    if pa.types.is_map(src) and pa.types.is_map(dst):
        return _assignable(src.key_type, dst.key_type) and _assignable(src.item_type, dst.item_type)
    if pa.types.is_struct(src) and pa.types.is_struct(dst):
        return [c.name for c in src] == [c.name for c in dst] and \
            all(_assignable(a.type, b.type) for a, b in zip(src, dst))
    return False


class Conform:
    """``reader``'s batches as ``target``, made as they are read: columns matched by name (in
    ``target``'s order), or ``by_position``. A value that doesn't fit its column stops the stream,
    and ``error`` says which."""

    def __init__(self, reader: Any, target: pa.Schema, by_position: bool = False):
        self.target = target
        self.by_position = by_position
        self.error: str | None = None
        self.reader = pa.RecordBatchReader.from_batches(target, self._batches(reader))

    def _batches(self, reader: Any):
        target = self.target
        for batch in reader:
            if batch.schema.equals(target):
                yield batch
                continue
            if self.by_position:
                columns = list(batch.columns)
            else:
                columns = [batch.column(batch.schema.get_field_index(f.name)) for f in target]
            out = []
            for column, f in zip(columns, target):
                try:
                    out.append(column.cast(f.type))
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                    self.error = f"a value in column {f.name} doesn't fit its type {f.type}: {exc}"
                    raise ValueError(self.error) from None
            yield pa.RecordBatch.from_arrays(out, schema=target)


class Counter:
    """Passes batches through, counting the rows and bytes."""

    def __init__(self, reader: pa.RecordBatchReader):
        self.rows = 0
        self.bytes = 0
        self.reader = pa.RecordBatchReader.from_batches(reader.schema, self._count(reader))

    def _count(self, reader: Any):
        for batch in reader:
            self.rows += batch.num_rows
            self.bytes += batch.nbytes
            yield batch
