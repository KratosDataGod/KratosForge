"""SQL over the attached lakehouse's tables, run on this PC with DuckDB.

Queries are written the way Fabric's SQL query tab takes them, in Spark SQL.
sqlglot translates each statement to DuckDB SQL, and every table it names is
read from OneLake as a Delta dataset, so DuckDB fetches only the columns and
files the query needs. Queries never change the lakehouse; writing a result
is ``lakehouse.write_table``.
"""

from __future__ import annotations

import itertools
import re
from typing import Any, Callable, Iterator

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

from . import lakehouse, sql_dialects

# View names stay unique on a connection even after superseded ones are dropped (superseded).
_VIEW_NAMES = itertools.count()


def parse(query: str, dialect: str = "spark") -> list[exp.Expression]:
    """Spark SQL first; DuckDB SQL is accepted too when it isn't valid Spark.

    ``dialect="duckdb"`` reads DuckDB SQL only. T-SQL dialects don't run here (sql_dialects.py)."""
    chosen = sql_dialects.get(dialect)
    if not chosen["runs_here"]:
        raise ValueError(f"{chosen['label']} can't run on this PC. KratosForge checks and formats "
                         "it here; run it in Fabric, or pick Spark SQL or DuckDB to run on this PC.")
    try:
        statements = sqlglot.parse(query, read=chosen["sqlglot"])
        if chosen["id"] == "spark":
            statements = [spark_fixes(s) for s in statements if s is not None]
    except ParseError as first_error:
        if chosen["id"] == "spark" and _only_empty_structs_fail(query):
            raise ValueError(_EMPTY_STRUCT) from None
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


_EMPTY_STRUCT = "An empty struct<> isn't supported on this PC. Give the struct at least one field, like struct<id: int>."


def _only_empty_structs_fail(query: str) -> bool:
    """Whether the query reads once each empty struct<> is given a field: only then is struct<> the
    error to name (not a 'struct<>' inside a string, nor a query with another mistake)."""
    try:
        tokens = sqlglot.tokenize(query, read="spark")
    except TokenError:  # a query the tokenizer refuses has another error to report
        return False
    spans = []
    for i, token in enumerate(tokens[:-1]):
        after = tokens[i + 1]
        if token.token_type == TokenType.STRUCT:
            if after.token_type == TokenType.NEQ:
                spans.append((after.start, after.end))
            elif after.token_type == TokenType.LT and i + 2 < len(tokens) and tokens[i + 2].token_type == TokenType.GT:
                spans.append((after.start, tokens[i + 2].end))
    if not spans:
        return False
    filled = query
    for start, end in reversed(spans):
        filled = filled[:start] + "<x: int>" + filled[end + 1:]
    try:
        sqlglot.parse(filled, read="spark")
    except ParseError:
        return False
    return True


# Spark's date and time unit names that DuckDB doesn't know ('MM' is minutes to DuckDB if anything).
_SPARK_UNITS = {"YYYY": "YEAR", "YY": "YEAR", "MON": "MONTH", "MM": "MONTH", "DD": "DAY"}


def spark_fixes(statement: exp.Expression) -> exp.Expression:
    """What a Spark SQL statement read by sqlglot needs to answer on DuckDB as it does on Spark:
    Spark's column names, its date functions and substring from 0. The Spark SQL path here and
    the DuckDB engine's Databricks SQL (engines/local_sql.py) both use it, so they can't drift."""
    return spark_substring(_spark_dates(_spark_names(statement)))


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


_TIME_TYPES = "'DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS'"


def _strptime_format(node: exp.Expression) -> tuple[str, bool]:
    """The DuckDB strptime format, as SQL, for a Spark datetime pattern sqlglot has read into ``node``,
    and whether the pattern has a year. S, SS and SSS (fractions of a second) become %g, and quoted
    text ('T', 'Z') stays literal; anything else sqlglot leaves untranslated is refused, since
    strptime would quietly match nothing."""
    from sqlglot.dialects.duckdb import DuckDB
    from sqlglot.dialects.spark import Spark
    # sqlglot has already turned pattern letters into its own %-codes, inside quotes too: turn
    # those back into the letters they came from (the shortest spelling of each).
    letters: dict[str, str] = {}
    for spark, code in sorted(Spark.TIME_MAPPING.items(), key=lambda kv: -len(kv[0])):
        letters[code] = spark
    codes = re.compile("|".join(re.escape(c) for c in sorted(letters, key=len, reverse=True)))
    generator = DuckDB().generator()
    out = []
    for i, piece in enumerate(re.split(r"('(?:[^']|'')*')", node.args["format"].name)):
        if i % 2:  # 'quoted text', as Spark writes a literal ('' is a quote)
            text = "'" if piece == "''" else codes.sub(lambda m: letters[m.group()], piece[1:-1]).replace("''", "'")
            out.append(text.replace("%", "%%"))
            continue
        piece = generator.format_time(exp.StrToUnix(format=exp.Literal.string(piece))) or "''"
        piece = re.sub(r"(?<![%S])S{1,3}(?!S)", "%g", sqlglot.parse_one(piece, read="duckdb").name)
        if re.search(r"[A-Za-z']", re.sub(r"%-?[A-Za-z%]", "", piece)):
            raise ValueError(f"unix_timestamp can't read part of its format ({piece}) on this PC. Use pattern "
                             "letters such as yyyy, MM, dd, HH, mm, ss and SSS.")
        out.append(piece)
    fmt = "".join(out)
    return exp.Literal.string(fmt).sql("duckdb"), bool(re.search(r"%-?[Yy]", fmt.replace("%%", "")))


def _as_timestamp(value: exp.Expression) -> str:
    """A date, timestamp or text as a timestamp, as Spark reads one (text that isn't one is NULL)."""
    return f"TRY_CAST({value.sql('duckdb')} AS TIMESTAMP)"


_SLICE_INDEX_0 = ("The index 0 is invalid. An index shall be either < 0 or > 0 "
                  "(the first element has index 1).")
_SLICE_LENGTH = "Unexpected value for length in function slice: length must be greater than or equal to 0."


# Functions this module rewrites into longer DuckDB SQL; a select of one keeps the name Spark gives it.
_REWRITTEN = (exp.IgnoreNulls, exp.AddMonths, exp.StrToUnix, exp.Substring, exp.ArraySlice, exp.MonthsBetween,
              exp.TsOrDsAdd, exp.DateTrunc, exp.TimestampTrunc)


def _spark_names(statement: exp.Expression) -> exp.Expression:
    """Name a selected expression this module rewrites as Spark names it ("add_months(d, 1)"), not after
    the DuckDB SQL it becomes."""
    for select in statement.find_all(exp.Select):
        for i, column in enumerate(select.expressions):
            if isinstance(column, (exp.Alias, exp.Column, exp.Star)) or not column.find(*_REWRITTEN):
                continue
            name = column.sql("spark", normalize_functions="lower")
            select.expressions[i] = exp.alias_(column, name, quoted=True)
            select.expressions[i].parent = select
    return statement


def spark_substring(node: exp.Expression) -> exp.Expression:
    """Spark's substring counts position 0 as 1 (substring('hello', 0, 2) is 'he'); DuckDB starts
    before the first character and returns 'h'.

    unix_timestamp also takes a date or timestamp (DuckDB's strptime takes only text), reads its
    value in the session time zone, is NULL for text that doesn't match the format, and is a whole
    number of seconds, as in Spark.

    add_months is a date, as in Spark, also for text or a timestamp (text that isn't a date is NULL).

    slice refuses index 0 and a negative length, and element_at an array's index 0, as in Spark.

    months_between counts the time of day and rounds to 8 digits, as in Spark.

    first(x, true) and last(x, true) skip NULLs as aggregates too (DuckDB takes IGNORE NULLS only
    in a window)."""
    for ignore in list(node.find_all(exp.IgnoreNulls)):
        agg = ignore.this
        if not isinstance(agg, (exp.First, exp.Last, exp.FirstValue, exp.LastValue)):
            continue
        name = "first" if isinstance(agg, (exp.First, exp.FirstValue)) else "last"
        value = agg.this.sql("duckdb")
        if isinstance(ignore.parent, exp.Window):  # sqlglot drops the IGNORE NULLS DuckDB's windows take
            window = ignore.parent.copy()
            window.set("this", exp.column("__fd_ignoring__"))
            text = window.sql("duckdb").replace("__fd_ignoring__", f"{name}_value({value} IGNORE NULLS)", 1)
            target = ignore.parent
        else:
            text, target = f"{name}({value}) FILTER (WHERE {value} IS NOT NULL)", ignore
        swapped = target.replace(sqlglot.parse_one(text, read="duckdb"))
        node = swapped if target is node else node
    for months in list(node.find_all(exp.AddMonths)):  # Spark's add_months is a date, whatever it's given
        months.set("this", exp.TryCast(this=exp.TryCast(this=months.this, to=exp.DataType.build("TIMESTAMP")),
                                       to=exp.DataType.build("DATE")))
        swapped = months.replace(exp.cast(months.copy(), exp.DataType.Type.DATE))
        node = swapped if months is node else node
    for kind in node.find_all(exp.DataType):  # struct< > reads as a struct with no fields
        if kind.this == exp.DataType.Type.STRUCT and not kind.expressions:
            raise ValueError(_EMPTY_STRUCT)
    for constraint in list(node.find_all(exp.NotNullColumnConstraint)):  # DuckDB has no NOT NULL in a struct
        column = constraint.parent.parent if constraint.parent else None
        if isinstance(column, exp.ColumnDef) and isinstance(column.parent, exp.DataType):
            constraint.parent.pop()
    for part in list(node.find_all(exp.ArraySlice)):  # Spark refuses index 0 and a negative length
        if not part.args.get("is_length"):
            continue
        for key, refused, message in (("start", lambda n: n == 0, _SLICE_INDEX_0),
                                      ("end", lambda n: n < 0, _SLICE_LENGTH)):
            value = part.args.get(key)
            if value is None:
                continue
            v = value.sql("duckdb")
            try:
                if not refused(int(v)):
                    continue  # a whole number Spark takes
            except ValueError:
                pass
            test = "= 0" if key == "start" else "< 0"
            part.set(key, sqlglot.parse_one(
                f"CASE WHEN {v} {test} THEN error({exp.Literal.string(message).sql('duckdb')}) ELSE {v} END",
                read="duckdb"))
    for item in list(node.find_all(exp.Bracket)):  # element_at: an array's index 0 is refused
        if item.args.get("offset") != 1 or item.args.get("safe") or len(item.expressions) != 1:
            continue
        v = item.expressions[0].sql("duckdb")
        try:
            if int(v) != 0:
                continue
        except ValueError:
            pass
        item.set("expressions", [sqlglot.parse_one(
            f"CASE WHEN {v} = 0 AND typeof({item.this.sql('duckdb')}) LIKE '%[]' "
            f"THEN error({exp.Literal.string(_SLICE_INDEX_0).sql('duckdb')}) ELSE {v} END", read="duckdb")])
    for between in list(node.find_all(exp.MonthsBetween)):
        later, earlier = (_as_timestamp(between.this), _as_timestamp(between.expression))
        months = (f"((year({later}) * 12 + month({later})) - (year({earlier}) * 12 + month({earlier})))")
        same = (f"day({later}) = day({earlier}) OR (day({later}) = day(last_day({later})) "
                f"AND day({earlier}) = day(last_day({earlier})))")
        seconds = f"(epoch({later}) - epoch(date_trunc('day', {later})) - epoch({earlier}) + epoch(date_trunc('day', {earlier})))"
        value = (f"CASE WHEN {same} THEN CAST({months} AS DOUBLE) ELSE {months} + "
                 f"((day({later}) - day({earlier})) * 86400 + {seconds}) / 2678400.0 END")
        roundoff = between.args.get("roundoff")
        if not (isinstance(roundoff, exp.Boolean) and roundoff.this is False):
            value = f"round({value}, 8)"
        swapped = between.replace(sqlglot.parse_one(value, read="duckdb"))
        node = swapped if between is node else node
    for unix in list(node.find_all(exp.StrToUnix)):
        value = unix.this.sql("duckdb")
        fmt, has_year = _strptime_format(unix) if unix.args.get("format") else ("'%Y-%m-%d %H:%M:%S'", True)
        # Without a year Spark reads the date as 1970-01-01; strptime starts from 1900-01-01.
        parsed = f"try_strptime(CAST({value} AS VARCHAR), {fmt})" + ("" if has_year else " + INTERVAL 70 YEAR")
        swapped = unix.replace(sqlglot.parse_one(
            f"CAST(floor(epoch(CASE WHEN typeof({value}) IN ({_TIME_TYPES}) THEN TRY_CAST({value} AS TIMESTAMPTZ) "
            f"ELSE CAST({parsed} AS TIMESTAMPTZ) END)) AS BIGINT)", read="duckdb"))
        node = swapped if unix is node else node
    for sub in list(node.find_all(exp.Substring)):
        start = sub.args.get("start")
        if start is None:
            continue
        if isinstance(start, exp.Literal) and not start.is_string:
            if start.this == "0":
                sub.set("start", exp.Literal.number(1))
        elif not isinstance(start, (exp.Literal, exp.Neg)):  # -3 counts from the end in both
            sub.set("start", exp.Case(ifs=[exp.If(this=exp.EQ(this=start.copy(), expression=exp.Literal.number(0)),
                                                  true=exp.Literal.number(1))], default=start.copy()))
    return node


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
        if not table.db and (table.name.lower() in ctes or is_local(table.name) or table.name in views.values()):
            continue
        # lakehouse.table_ref checks the parts: a catalog is only allowed as the lakehouse's own name.
        name = ".".join(p for p in (table.catalog, table.db, table.name) if p)
        point = _time_travel(table)  # VERSION AS OF n, TIMESTAMP AS OF '...', or Databricks' sales@v3
        opened = None
        if point is None:
            # The latest version is looked up each time, so a table written since an earlier statement
            # on a long-lived connection (spark on this PC) is read as it is now, as Spark does. Its
            # location is part of the key: in a proof, a table it wrote is read from the proof's copy.
            opened = lakehouse.open_table(name)
            key = f"{name.lower()}@{opened.table_uri}#{opened.version()}"
        else:
            key = f"{name.lower()}@{point}"
        if key not in views:
            views[key] = f"__lakehouse_table_{next(_VIEW_NAMES)}"
            opened = opened or lakehouse.open_table(name, **point_args(point))
            con.register(views[key], opened.to_pyarrow_dataset())
        # Keep the name the query used as the alias, so sales.Country still resolves.
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        table.replace(exp.Table(this=exp.to_identifier(views[key]), alias=alias))


def is_local(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether ``name`` is a view or table already on ``con`` (a temp view an earlier statement made),
    for bind_tables. Case is ignored, as identifiers are in Spark SQL and DuckDB."""
    return bool(con.execute("SELECT 1 FROM duckdb_views() WHERE lower(view_name) = lower(?) "
                            "UNION ALL SELECT 1 FROM duckdb_tables() WHERE lower(table_name) = lower(?)",
                            [name, name]).fetchall())


def superseded(views: dict[str, str]) -> list[str]:
    """Keys in ``views`` (from bind_tables) for a table's latest version that a newer version of the
    same table, registered later, replaced. A time travel point is never superseded."""
    newest: dict[str, str] = {}
    old = []
    for key in views:  # in the order they were registered
        if "#" not in key:
            continue
        table = key.rpartition("#")[0]
        if table in newest:
            old.append(newest[table])
        newest[table] = key
    return old


def _time_travel(table: exp.Table) -> str | None:
    """A table's time travel point as "v<n>" or "t<time>", taken off the table so DuckDB never
    sees it; None to read the latest version."""
    version = table.args.get("version")
    if isinstance(version, exp.Version):
        if str(version.args.get("kind", "")).upper() != "AS OF":
            raise ValueError("Only VERSION AS OF and TIMESTAMP AS OF can be read on this PC.")
        value = version.expression.name if isinstance(version.expression, exp.Literal) else version.expression.sql("spark")
        table.set("version", None)
        return f"v{value}" if str(version.this).upper() == "VERSION" else f"t{value}"
    alias = table.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Parameter):  # sales@v3 (Databricks)
        value = alias.this.name
        table.set("alias", None)
        if re.fullmatch(r"[vV]\d+", value):
            return f"v{value[1:]}"
        if re.fullmatch(r"\d{14,17}", value):  # yyyyMMddHHmmssSSS
            return f"t{value[:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}:{value[12:14]}"
        raise ValueError(f"{table.name}@{value} isn't a version (@v3) or a time (@20261020130000000).")
    return None


def point_args(point: str) -> dict[str, str]:
    return {"version": point[1:]} if point.startswith("v") else {"timestamp": point[1:]}


def history_query(statement: exp.Expression, con: duckdb.DuckDBPyConnection,
                  views: dict[str, str]) -> exp.Expression:
    """DESCRIBE HISTORY t becomes a query over the table's Delta log; any other statement is
    returned as it is."""
    if not (isinstance(statement, exp.Describe) and str(statement.args.get("style") or "").upper() == "HISTORY"):
        return statement
    table = statement.this
    name = ".".join(p for p in (table.catalog, table.db, table.name) if p)
    view = f"__lakehouse_history_{len(views)}"
    views[f"history:{name.lower()}:{len(views)}"] = view
    con.register(view, lakehouse.table_history(name).to_arrow())
    return sqlglot.parse_one(f"SELECT * FROM {view} ORDER BY version DESC", read="duckdb")


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


def _connect() -> duckdb.DuckDBPyConnection:
    # UTC like Fabric's Spark sessions; otherwise year() and date_format() follow this PC's time zone.
    # SET, not connect's config: config makes DuckDB try to download ICU, which fails offline.
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


def _arrow(result: duckdb.DuckDBPyConnection | None) -> pa.Table:
    if result is None or not result.description:
        return pa.table({})
    return (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()


def prepare_sandboxed(query: str, dialect: str = "spark", memory_mb: int | None = None,
                      threads: int | None = None) -> tuple[duckdb.DuckDBPyConnection, str]:
    """A connection with ``query``'s lakehouse tables registered and its configuration locked, and
    the one read-only statement to run on it (``execute_sandboxed``).

    The tables need the attached lakehouse only while they are registered here (each dataset keeps
    its own storage token), so a caller can run the statement after letting the lakehouse go.
    ``memory_mb`` and ``threads`` are set before the configuration is locked."""
    con = _connect()
    try:
        views: dict[str, str] = {}
        statement = check_read_only([history_query(s, con, views) for s in parse(query, dialect)])
        bind_tables(statement, con, views)
        if memory_mb:
            con.execute(f"SET memory_limit = '{int(memory_mb)}MB'")
        if threads:
            con.execute(f"SET threads = {int(threads)}")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
    except BaseException:
        con.close()
        raise
    return con, statement.sql("duckdb")


def execute_sandboxed(con: duckdb.DuckDBPyConnection, statement: str) -> pa.Table:
    """Run a statement from ``prepare_sandboxed``. ``con.interrupt()`` from another thread stops it
    (duckdb.InterruptException); an interrupt sent before DuckDB starts the statement is lost."""
    return _arrow(con.execute(statement))


def _execute(query: str, dialect: str, sandbox: bool) -> tuple[duckdb.DuckDBPyConnection, Any]:
    """Run every statement in ``query``; the connection and the last one's result (None for none).

    ``sandbox``: one read-only statement, and DuckDB's external access is turned off (and locked)
    once the lakehouse tables are registered, so the query can't read local files, load
    extensions or reach the network (external AI tools, integrations/executions.py)."""
    if sandbox:
        con, statement = prepare_sandboxed(query, dialect)
        return con, con.execute(statement)
    con = _connect()
    views: dict[str, str] = {}
    result = None
    for statement in parse(query, dialect):
        statement = history_query(statement, con, views)
        bind_tables(statement, con, views, lambda name: is_local(con, name))
        result = con.execute(statement.sql("duckdb"))
    return con, result


def run(query: str, dialect: str = "spark", sandbox: bool = False) -> pa.Table:
    """Run every statement in ``query`` and return the last one's whole result (see _execute)."""
    _con, result = _execute(query, dialect, sandbox)
    return _arrow(result)


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
