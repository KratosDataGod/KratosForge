"""SQL over the attached lakehouse's tables, run on this PC with DuckDB.

Queries are written the way Fabric's SQL query tab takes them, in Spark SQL.
sqlglot translates each statement to DuckDB SQL, and every table it names is
read from OneLake as a Delta dataset, so DuckDB fetches only the columns and
files the query needs. Queries never change the lakehouse; writing a result
is ``lakehouse.write_table``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from . import lakehouse, sql_dialects


def parse(query: str, dialect: str = "spark") -> list[exp.Expression]:
    """Spark SQL first; DuckDB SQL is accepted too when it isn't valid Spark.

    ``dialect="duckdb"`` reads DuckDB SQL only. T-SQL dialects don't run here (sql_dialects.py)."""
    chosen = sql_dialects.get(dialect)
    if not chosen["runs_here"]:
        raise ValueError(f"{chosen['label']} can't run on this PC. Fabric Desktop by KratosBI checks and formats "
                         "it here; run it in Fabric, or pick Spark SQL or DuckDB to run on this PC.")
    try:
        statements = sqlglot.parse(query, read=chosen["sqlglot"])
        if chosen["id"] == "spark":
            statements = [_spark_dates(s) for s in statements if s is not None]
    except ParseError as first_error:
        if chosen["id"] != "spark":
            raise ValueError(f"Could not read the query: {first_error}") from None
        try:
            statements = sqlglot.parse(query, read="duckdb")
        except ParseError:
            raise ValueError(f"Could not read the query: {first_error}") from None
    statements = [s for s in statements if s is not None]
    if not statements:
        raise ValueError("The query is empty.")
    return statements


# Spark's date and time unit names that DuckDB doesn't know ('MM' is minutes to DuckDB if anything).
_SPARK_UNITS = {"YYYY": "YEAR", "YY": "YEAR", "MON": "MONTH", "MM": "MONTH", "DD": "DAY"}


def _spark_dates(statement: exp.Expression) -> exp.Expression:
    """Keep Spark's date results where sqlglot's DuckDB SQL differs: date_add and date_sub return
    a date (DuckDB's date + INTERVAL is a timestamp), trunc(date, 'MM') returns a date, and Spark's
    unit spellings ('MM', 'YYYY') become ones DuckDB reads."""
    for node in list(statement.find_all(exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc)):
        if isinstance(node, (exp.DateTrunc, exp.TimestampTrunc)):
            unit = node.args.get("unit")
            spark_trunc = isinstance(unit, exp.Literal)  # trunc(date, 'MM'); date_trunc('MM', ts) has a Var
            name = unit.name.upper() if unit is not None else ""
            if name in _SPARK_UNITS:
                node.set("unit", exp.Literal.string(_SPARK_UNITS[name]) if spark_trunc else exp.var(_SPARK_UNITS[name]))
            if not spark_trunc:
                continue
        node.replace(exp.cast(node.copy(), exp.DataType.Type.DATE))
    return statement


def bind_tables(statement: exp.Expression, con: duckdb.DuckDBPyConnection, views: dict[str, str],
                is_local: Callable[[str], bool] = lambda _name: False) -> None:
    """Point every lakehouse table ``statement`` names at a Delta dataset registered on ``con``.

    ``is_local(name)`` says a name is already a view or table on ``con`` (like a Spark temp
    view), so it is left alone."""
    ctes = {c.alias_or_name.lower() for c in statement.find_all(exp.CTE)}
    # CREATE ... v AS ...: v is what the statement makes, not a lakehouse table to read.
    made = statement.this if isinstance(statement, exp.Create) else None
    made = made.this if isinstance(made, exp.Schema) else made
    for table in list(statement.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            continue  # a table function such as range(10)
        if table is made:
            continue
        if not table.db and (table.name.lower() in ctes or is_local(table.name)):
            continue
        # lakehouse.table_ref checks the parts: a catalog is only allowed as the lakehouse's own name.
        name = ".".join(p for p in (table.catalog, table.db, table.name) if p)
        key = name.lower()
        if key not in views:
            views[key] = f"__lakehouse_table_{len(views)}"
            con.register(views[key], lakehouse.open_table(name).to_pyarrow_dataset())
        # Keep the name the query used as the alias, so sales.Country still resolves.
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(views[key]), alias=alias))


READ_ONLY = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def check_read_only(statements: list[exp.Expression]) -> exp.Expression:
    """The one statement of a sandboxed query, if it only reads (SELECT, WITH ... SELECT, UNION)."""
    if len(statements) != 1:
        raise ValueError("Send one statement at a time.")
    statement = statements[0]
    if not isinstance(statement, READ_ONLY) or any(
            isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
                              exp.Command, exp.Copy)) for node in statement.walk()):
        raise PermissionError("Only queries that read (SELECT) can run here.")
    return statement


def _execute(query: str, dialect: str, sandbox: bool) -> tuple[duckdb.DuckDBPyConnection, Any]:
    """Run every statement in ``query``; the connection and the last one's result (None for none).

    ``sandbox``: one read-only statement, and DuckDB's external access is turned off (and locked)
    once the lakehouse tables are registered, so the query can't read local files, load
    extensions or reach the network (external AI tools, integrations/executions.py)."""
    # UTC like Fabric's Spark sessions; otherwise year() and date_format() follow this PC's time zone.
    # SET, not connect's config: config makes DuckDB try to download ICU, which fails offline.
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    views: dict[str, str] = {}
    result = None
    statements = parse(query, dialect)
    if sandbox:
        statements = [check_read_only(statements)]
        bind_tables(statements[0], con, views)
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        result = con.execute(statements[0].sql("duckdb"))
        statements = []
    for statement in statements:
        bind_tables(statement, con, views)
        result = con.execute(statement.sql("duckdb"))
    return con, result


def run(query: str, dialect: str = "spark", sandbox: bool = False) -> pa.Table:
    """Run every statement in ``query`` and return the last one's whole result (see _execute)."""
    _con, result = _execute(query, dialect, sandbox)
    if result is None or not result.description:
        return pa.table({})
    return (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()


BATCH_ROWS = 50_000  # rows held in memory at a time when a result is streamed


class Stream:
    """The last statement's result, read in batches of at most ``batch_rows`` rows so a large
    result is never held whole on this side (see _execute). ``schema`` is None when the last
    statement returns no result set. Use it as a context manager so the connection is closed."""

    def __init__(self, query: str, dialect: str = "spark", sandbox: bool = False, batch_rows: int = BATCH_ROWS):
        self._con, result = _execute(query, dialect, sandbox)
        reader = None
        if result is not None and result.description:
            reader = (getattr(result, "to_arrow_reader", None) or result.fetch_record_batch)(batch_rows)
        self._reader = reader
        self.schema: pa.Schema | None = self._reader.schema if self._reader is not None else None

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self._reader) if self._reader is not None else iter(())

    def __enter__(self) -> "Stream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._con.close()


def head(query: str, dialect: str = "spark", sandbox: bool = False, limit: int = 1000) -> tuple[pa.Table, int]:
    """The first ``limit`` rows and the result's full row count. Later rows are counted as they
    stream past and dropped, so memory holds the first rows and one batch."""
    with Stream(query, dialect, sandbox) as result:
        if result.schema is None:
            return pa.table({}), 0
        kept: list[pa.RecordBatch] = []
        have = total = 0
        for batch in result:
            total += batch.num_rows
            if have < limit:
                kept.append(batch.slice(0, limit - have))
                have += kept[-1].num_rows
        return pa.Table.from_batches(kept, schema=result.schema), total
