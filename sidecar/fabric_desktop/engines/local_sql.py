"""SQL engines on this PC: DuckDB, SQLite and Polars.

Each session is its own in-memory database, so nothing one notebook creates is visible to another
and closing the session frees everything. A session can also open an existing DuckDB or SQLite
file as its database, always read-only.

A session never reaches past its own database (the same refusals the SQL page makes, sql_policy):

- DuckDB refuses COPY, EXPORT, ATTACH, INSTALL, LOAD and the other statements sql_policy classes as
  changing something outside the run, before anything runs. Its connection also has external access
  turned off, so no statement can read or write a file on this PC, load an extension or reach the
  network, whatever the classifier makes of it.
- SQLite refuses ATTACH and DETACH (an authorizer on the connection), so a cell can't create or open
  another database file; Python's sqlite3 already refuses loading extensions.
- Polars has no statement that writes outside the session. Its read_parquet and read_csv read files
  the person names; they never change one.

The result of the last statement comes back as a table (the first ``preview_rows`` rows, with the
exact total counted by reading the rest in batches), the same shape the notebook already shows.
Results arrive when the statement finishes; none of these engines stream rows to the cell. DuckDB and
SQLite also hand back the whole result as a table when the request asks (``result_rows``), so
engines/compare.py can compare two engines' answers; more rows than asked for, or more than
``RESULT_BYTES``, is an error, never a cut table.

What runs where, honestly (see each descriptor):

- DuckDB runs DuckDB SQL natively, and Spark SQL and Databricks SQL translated by sqlglot (the same translation the SQL
  page and ``%%sql`` cells use, sql.parse). It is not Fabric's or Databricks' Spark. Snowflake SQL is translated too
  (snowflake_sql.py), statement by statement with a label, and the label comes back as the cell's first output.
- SQLite runs SQLite's SQL. Python's own sqlite3; nothing to install.
- Polars runs the SQL subset Polars implements over its own tables; its plan is Polars' optimized plan.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa

from .capabilities import EngineDescriptor, Support
from .contract import RESULT_BYTES, CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported
from ..duckdb_rows import duckdb_major, first_row, keep_try_cast

_T = "unit_tests"
_LOCAL_COST = Support("unsupported", "Runs on this PC, so the engine bills nothing; Metrics has the measured "
                      "time. Reading or writing OneLake from this PC still uses Fabric capacity.")
_NO_VARIABLES = Support("unsupported", "A SQL session holds tables, not variables; Catalog lists them.")
_NO_STREAM = Support("unsupported", "The result arrives when the statement finishes.")
_RESULT = Support("native", f"The whole result as a table, up to the rows asked for and {RESULT_BYTES // 2**20} MB; "
                  "a larger result is an error, never cut.", _T)
_NO_RESULT = Support("unsupported", "Not in this version; DuckDB and SQLite return whole results.")


class ResultTooLarge(RuntimeError):
    """The whole result was asked for, and it has more rows or bytes than allowed."""


def _too_many(rows: int) -> ResultTooLarge:
    return ResultTooLarge(f"The result has more than the {rows:,} rows asked for, so none of it was kept. "
                          "Narrow the query, or ask for more rows.")


def _too_big() -> ResultTooLarge:
    return ResultTooLarge(f"The result is larger than {RESULT_BYTES // 2**20} MB, so none of it was kept. "
                          "Narrow the query (fewer rows or columns).")


def _table_output(table: pa.Table, total: int) -> dict[str, Any]:
    from ..notebook import display
    out = display(table)
    out["total_rows"] = total
    return out


def _done(seconds: float) -> dict[str, Any]:
    return {"type": "text", "text": f"Done in {seconds:.2f} s."}


class _SqlSession:
    """What the three SQL sessions share: one statement at a time, cancel, close, timing."""

    engine = ""

    def __init__(self, config: SessionConfig) -> None:
        self.notebook = config.notebook
        self._lock = threading.Lock()
        self._running = False
        self._cancelled = False
        self._closed = False
        self._preamble: list[dict[str, Any]] = []   # outputs _run adds before the result (a translation report)
        self._result: pa.Table | None = None         # the whole result, when the request set result_rows

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosed(f"This {self.engine} session was closed. Start the engine again to run cells.")

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        self._check_open()
        if request.language != "sql":
            raise Unsupported(f"The {self.engine} engine runs SQL cells only.")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        self._running, self._cancelled = True, False
        self._preamble = []
        self._result = None
        started = time.perf_counter()
        try:
            try:
                table, total = self._run(request)
            except Exception as exc:  # noqa: BLE001 - reported to the person as the cell's error
                seconds = time.perf_counter() - started
                status = "cancelled" if self._cancelled else "error"
                out = {"type": "error", "ename": type(exc).__name__,
                       "evalue": "Cancelled." if self._cancelled else str(exc)}
                outputs = [*self._preamble, out]
                if on_output:
                    for o in outputs:
                        on_output(o)
                return CellResult(status, outputs, seconds, None, self.engine)
            seconds = time.perf_counter() - started
            outputs = [*self._preamble, _table_output(table, total) if table is not None else _done(seconds)]
            if on_output:
                for o in outputs:
                    on_output(o)
            return CellResult("ok", outputs, seconds, total if table is not None else None, self.engine,
                              table=self._result)
        finally:
            self._running = False
            self._lock.release()

    def _run(self, request: CellRequest) -> tuple[pa.Table | None, int]:
        raise NotImplementedError

    def _keep(self, table: pa.Table, total: int, request: CellRequest) -> tuple[pa.Table, int]:
        """Keep the whole result when it was asked for; the cell shows the first ``preview_rows``."""
        if request.result_rows <= 0:
            return table, total
        if total > request.result_rows:
            raise _too_many(request.result_rows)
        if table.nbytes > RESULT_BYTES:
            raise _too_big()
        self._result = table
        return table.slice(0, request.preview_rows), total

    def cancel(self) -> bool:
        if not self._running:
            return False
        self._cancelled = True
        self._interrupt()
        return True

    def _interrupt(self) -> None:
        raise Unsupported(f"The {self.engine} engine can't stop a running statement.")

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(_NO_VARIABLES.note)

    def close(self) -> None:
        if self._closed:
            return
        if self._running:
            with contextlib.suppress(Unsupported):   # Polars can't stop; the statement finishes on its own
                self.cancel()
        self._closed = True
        self._release()

    def _release(self) -> None:
        raise NotImplementedError


def _drain(reader: Any, limit: int, result_rows: int = 0) -> tuple[pa.Table, int]:
    """The first ``limit`` rows of an Arrow batch reader, and how many rows it had in all.

    With ``result_rows``, every row, stopping with an error as soon as there are more rows than that
    or more than RESULT_BYTES, so a big result is never held in memory first."""
    keep = max(limit, result_rows if result_rows > 0 else 0)
    kept: list[pa.RecordBatch] = []
    have = total = size = 0
    for batch in reader:
        total += batch.num_rows
        if result_rows > 0 and total > result_rows:
            raise _too_many(result_rows)
        if have < keep:
            part = batch.slice(0, keep - have)
            kept.append(part)
            have += part.num_rows
            size += part.nbytes
            if result_rows > 0 and size > RESULT_BYTES:
                raise _too_big()
    return pa.Table.from_batches(kept, schema=reader.schema), total


# ---------------------------------------------------------------- DuckDB

DUCKDB = EngineDescriptor(
    id="local-duckdb", label="DuckDB (this PC)", location="local", platform="local", billing="none", adapter=True,
    family="duckdb",
    languages={"sql": Support("native", evidence=_T)},
    dialects={"duckdb": Support("native", evidence=_T),
              "spark": Support("translated", "Spark SQL is rewritten for DuckDB by sqlglot. Most queries match; "
                               "Spark-only functions and Delta commands (OPTIMIZE, VACUUM) don't.", _T),
              "databricks": Support("translated", "Databricks SQL is rewritten for DuckDB by sqlglot, so warehouse "
                                    "queries can be written and tried on this PC without starting a warehouse. "
                                    "Most queries match; Unity Catalog three-part names need the catalog and schema "
                                    "in the session, and Delta-only commands don't run.", _T),
              "snowflake": Support("translated", "Snowflake SQL is rewritten for DuckDB by sqlglot with KratosForge's "
                                   "rules; each statement is labelled translated, runs with a known difference, or "
                                   "runs only in Snowflake, and a cell runs only when every statement can. Results "
                                   "are not checked against a Snowflake account.", _T)},
    capabilities={
        "session": Support("native", "An in-memory database per session; an existing file opens read-only.", _T),
        "execute": Support("native", evidence=_T),
        "cancel": Support("native", "Stops the running statement (DuckDB interrupt).", _T),
        "stream": _NO_STREAM, "variables": _NO_VARIABLES,
        "debug": Support("unsupported", "SQL has no step debugger; use Plan."),
        "plan": Support("native", "DuckDB's EXPLAIN.", _T),
        "metrics": Support("native", "Wall time and exact row count.", _T),
        "catalog": Support("native", "Schemas, tables and views in the session (information_schema).", _T),
        "cost": _LOCAL_COST,
        "compat": Support("native", "The language and dialect are checked before running.", _T),
        "result": _RESULT,
    },
    requirements=("duckdb (ships with KratosForge)",),
    limitations=("Not Fabric's or Databricks' Spark: results can differ where engines differ (use Prove to compare).",
                 "A database file the session opens is read-only.",
                 "No files on this PC, extensions or network: COPY, ATTACH, INSTALL and LOAD are refused."),
)


# A DuckDB session reads and writes only its own database: no files, extensions or network.
_DUCKDB_CLOSED = {"enable_external_access": False, "autoinstall_known_extensions": False,
                  "autoload_known_extensions": False}


class OutsideSession(PermissionError):
    """A statement would change something outside the session's own database."""


def _refuse_outside(code: str, dialect: str | None) -> None:
    """Refuse COPY, ATTACH, INSTALL and the rest before anything runs (sql_policy's fabric_mutation).

    A cell sqlglot can't read is left to DuckDB, whose closed connection still can't reach outside."""
    from .. import snowflake_sql, sql, sql_policy
    try:
        if dialect == "snowflake":
            statements = snowflake_sql.parse(code)
        else:
            statements = _databricks(code) if dialect == "databricks" else sql.parse(code, dialect or "duckdb")
    except ValueError:
        return
    for verdict in sql_policy.classify(statements, "lakehouse"):
        if verdict.kind == "fabric_mutation":
            word = (statements[verdict.statement - 1].sql(dialect="duckdb").split(None, 1) or ["This"])[0].upper()
            raise OutsideSession(f"{word} isn't allowed in a DuckDB engine session (statement {verdict.statement}). "
                                 "A session reads and writes only its own tables, never files, extensions or other "
                                 "databases.")


def _databricks(code: str) -> list[Any]:
    """Databricks SQL statements read by sqlglot's Databricks dialect, with the same Spark fixes
    sql.parse makes for Spark SQL (substring from 0, date_add returning a date, trunc, unix_timestamp,
    Spark's column names), so a query answers here as it does on the warehouse. ValueError when
    it can't be read."""
    import sqlglot
    from sqlglot.errors import ParseError

    from .. import sql, sql_dialects
    try:
        statements = [s for s in sqlglot.parse(code, read="databricks") if s is not None]
    except ParseError as exc:
        raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(exc)}") from None
    if not statements:
        raise ValueError("The query is empty.")
    return [sql.spark_fixes(s) for s in statements]


class DuckDBSession(_SqlSession):
    engine = DUCKDB.id

    def __init__(self, config: SessionConfig) -> None:
        super().__init__(config)
        import duckdb
        target = str(Path(config.database)) if config.database else ":memory:"
        self._con = duckdb.connect(target, read_only=bool(config.database), config=dict(_DUCKDB_CLOSED))
        keep_try_cast(self._con)

    def _sql(self, code: str, dialect: str | None) -> str:
        if (dialect or "duckdb") == "duckdb":
            return code
        if dialect == "snowflake":
            from .. import snowflake_sql
            translation = snowflake_sql.translate(code)
            self._preamble = [translation.as_output()]
            if not translation.runnable:
                raise Unsupported(translation.first_refusal() + " Nothing in the cell ran.")
            return translation.duckdb_sql
        if dialect not in ("spark", "databricks"):
            raise Unsupported(f"DuckDB doesn't run the {dialect} dialect. Use Spark SQL, Databricks SQL, Snowflake SQL "
                              "or DuckDB SQL.")
        from .. import sql
        statements = _databricks(code) if dialect == "databricks" else sql.parse(code, "spark")
        try:
            return ";\n".join(sql.duckdb_sql(s) for s in statements)
        except ValueError as exc:
            raise Unsupported(str(exc)) from None

    def _run(self, request: CellRequest) -> tuple[pa.Table | None, int]:
        if request.dialect == "snowflake":   # translated first, so even a refused cell shows its label
            code = self._sql(request.code, request.dialect)
            _refuse_outside(request.code, request.dialect)
        else:
            _refuse_outside(request.code, request.dialect)
            code = self._sql(request.code, request.dialect)
        if request.dialect not in ("spark", "databricks"):
            return self._execute(code, request)
        # Spark's TIMESTAMP is read in the session's time zone, which is UTC in Fabric and Databricks
        # unless a notebook changes it; sqlglot makes it DuckDB's TIMESTAMPTZ, which DuckDB reads in
        # this PC's zone. So translated cells run in UTC, and the cell after gets the zone back.
        zone = first_row(self._con.execute("SELECT current_setting('TimeZone')"))
        self._con.execute("SET TimeZone = 'UTC'")
        try:
            return self._execute(code, request)
        finally:
            if zone:
                self._con.execute("SET TimeZone = ?", [str(zone[0])])

    def _execute(self, code: str, request: CellRequest) -> tuple[pa.Table | None, int]:
        if duckdb_major() >= 2:
            # DuckDB 2.0 warns that Arrow from a DB-API result (execute) copies every row once more,
            # and points to a relation's reader. con.sql runs each statement and gives the last one's
            # result, the same result execute gave there (a Count or Success row for a change).
            rel = self._con.sql(code)
            if rel is None:
                return None, 0
            reader = rel.to_arrow_reader()
        else:
            # On 1.x con.sql gives no result for a change, where execute gives its Count, so keep execute.
            cur = self._con.execute(code)
            if cur.description is None:
                return None, 0
            reader = (getattr(cur, "to_arrow_reader", None) or cur.fetch_record_batch)()
        table, total = _drain(reader, request.preview_rows, request.result_rows)
        return self._keep(table, total, request)

    def _interrupt(self) -> None:
        self._con.interrupt()

    def plan(self, code: str, dialect: str | None = None) -> str:
        self._check_open()
        _refuse_outside(code, dialect)
        rows = self._con.execute("EXPLAIN " + self._sql(code, dialect)).fetchall()
        text = "\n".join(str(r[-1]) for r in rows)
        if dialect == "snowflake":
            return "Plan of the DuckDB translation on this PC, not of Snowflake's plan.\n" + text
        return text

    def catalog(self) -> list[dict[str, Any]]:
        self._check_open()
        rows = self._con.execute(
            "SELECT table_catalog, table_schema, table_name, table_type FROM information_schema.tables "
            "ORDER BY 1, 2, 3").fetchall()
        return [{"catalog": c, "schema": s, "name": n, "type": "view" if "VIEW" in t else "table"} for c, s, n, t in rows]

    def _release(self) -> None:
        self._con.close()


class DuckDBAdapter:
    def describe(self) -> EngineDescriptor:
        return DUCKDB

    def open_session(self, config: SessionConfig, authorization: Any = None) -> DuckDBSession:
        return DuckDBSession(config)


# ---------------------------------------------------------------- SQLite

SQLITE = EngineDescriptor(
    id="local-sqlite", label="SQLite (this PC)", location="local", platform="local", billing="none", adapter=True,
    family="sqlite",
    languages={"sql": Support("native", evidence=_T)},
    dialects={"sqlite": Support("native", evidence=_T)},
    capabilities={
        "session": Support("native", "An in-memory database per session; an existing file opens read-only.", _T),
        "execute": Support("native", "Several statements run in order; the last one's rows are shown.", _T),
        "cancel": Support("native", "Stops the running statement (sqlite3 interrupt).", _T),
        "stream": _NO_STREAM, "variables": _NO_VARIABLES,
        "debug": Support("unsupported", "SQL has no step debugger; use Plan."),
        "plan": Support("native", "SQLite's EXPLAIN QUERY PLAN.", _T),
        "metrics": Support("native", "Wall time and exact row count.", _T),
        "catalog": Support("native", "Tables and views in the session (sqlite_master).", _T),
        "cost": _LOCAL_COST,
        "compat": Support("native", "The language and dialect are checked before running.", _T),
        "result": _RESULT,
    },
    requirements=("Python's sqlite3 (built in)",),
    limitations=("SQLite's types and functions are not Spark's or T-SQL's.", "A database file the session opens is read-only.",
                 "ATTACH and DETACH are refused."),
)


def _statements(code: str) -> list[str]:
    """SQLite runs one statement per call; split where sqlite3 says a statement is complete."""
    out: list[str] = []
    buf = ""
    parts = code.split(";")
    for i, part in enumerate(parts):
        buf += part
        if i < len(parts) - 1:
            buf += ";"
            if sqlite3.complete_statement(buf):
                out.append(buf.strip())
                buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s.strip(" ;\n\t")]


def _sqlite_table(names: list[str], rows: list[Any]) -> pa.Table:
    columns = list(zip(*rows)) if rows else [[] for _ in names]
    return pa.table({n: pa.array([None if v is None else (v if isinstance(v, (int, float, str)) else str(v))
                                  for v in col]) for n, col in zip(names, columns)}) if names else pa.table({})


def _sqlite_authorizer(action: int, *_args: Any) -> int:
    """ATTACH and DETACH would open or create another database file; everything else is the session's own."""
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


class SQLiteSession(_SqlSession):
    engine = SQLITE.id

    def __init__(self, config: SessionConfig) -> None:
        super().__init__(config)
        if config.database:
            uri = Path(config.database).resolve().as_uri() + "?mode=ro"
            self._con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.set_authorizer(_sqlite_authorizer)

    def _run(self, request: CellRequest) -> tuple[pa.Table | None, int]:
        if (request.dialect or "sqlite") != "sqlite":
            raise Unsupported(f"SQLite doesn't run the {request.dialect} dialect.")
        statements = _statements(request.code)
        if not statements:
            raise ValueError("The cell is empty.")
        cur = None
        for statement in statements:
            cur = self._con.execute(statement)
        self._con.commit()
        assert cur is not None
        if cur.description is None:
            return None, 0
        names = [d[0] for d in cur.description]
        keep = max(request.preview_rows, request.result_rows)
        parts: list[pa.Table] = []   # Arrow per batch, as DuckDB's _drain keeps, never a list of Python rows
        have = total = size = 0
        while True:   # in batches, so a whole result is checked against RESULT_BYTES as it is read
            batch = cur.fetchmany(10_000)
            if not batch:
                break
            total += len(batch)
            if request.result_rows > 0 and total > request.result_rows:
                raise _too_many(request.result_rows)
            if have < keep:
                part = _sqlite_table(names, batch[:keep - have])
                parts.append(part)
                have += part.num_rows
                size += part.nbytes
                if request.result_rows > 0 and size > RESULT_BYTES:
                    raise _too_big()
        # A column that is all NULL in one batch takes its type from the others.
        table = pa.concat_tables(parts, promote_options="permissive") if parts else _sqlite_table(names, [])
        return self._keep(table, total, request)

    def _interrupt(self) -> None:
        self._con.interrupt()

    def plan(self, code: str, dialect: str | None = None) -> str:
        self._check_open()
        statements = _statements(code)
        if len(statements) != 1:
            raise ValueError("Plan shows one statement at a time.")
        rows = self._con.execute("EXPLAIN QUERY PLAN " + statements[0]).fetchall()
        return "\n".join(str(r[-1]) for r in rows)

    def catalog(self) -> list[dict[str, Any]]:
        self._check_open()
        rows = self._con.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view') "
                                 "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [{"catalog": "main", "schema": "main", "name": n, "type": t} for t, n in rows]

    def _release(self) -> None:
        self._con.close()


class SQLiteAdapter:
    def describe(self) -> EngineDescriptor:
        return SQLITE

    def open_session(self, config: SessionConfig, authorization: Any = None) -> SQLiteSession:
        return SQLiteSession(config)


# ---------------------------------------------------------------- Polars

POLARS = EngineDescriptor(
    id="local-polars", label="Polars SQL (this PC)", location="local", platform="local", billing="none", adapter=True,
    family="polars",
    languages={"sql": Support("native", evidence=_T)},
    dialects={"polars": Support("partial", "Polars implements a subset of SQL (SELECT, joins, CTEs, CREATE TABLE AS, "
                                "read_parquet/read_csv); no UPDATE or DELETE.", _T)},
    capabilities={
        "session": Support("native", "Tables live in the session's SQL context and go when it closes.", _T),
        "execute": Support("native", evidence=_T),
        "cancel": Support("unsupported", "Polars can't stop a query part way; it finishes, then the session can close."),
        "stream": _NO_STREAM, "variables": _NO_VARIABLES,
        "debug": Support("unsupported", "SQL has no step debugger; use Plan."),
        "plan": Support("native", "Polars' optimized query plan (LazyFrame.explain).", _T),
        "metrics": Support("native", "Wall time and exact row count.", _T),
        "catalog": Support("native", "Tables registered in the session.", _T),
        "cost": _LOCAL_COST,
        "compat": Support("native", "The language and dialect are checked before running.", _T),
        "result": _NO_RESULT,
    },
    requirements=("polars (ships with KratosForge)",),
    limitations=("Polars SQL is not Spark SQL; many Spark functions don't exist.",),
)


class PolarsSession(_SqlSession):
    engine = POLARS.id

    def __init__(self, config: SessionConfig) -> None:
        super().__init__(config)
        import polars as pl
        self._ctx: Any = pl.SQLContext()

    def _run(self, request: CellRequest) -> tuple[pa.Table | None, int]:
        if (request.dialect or "polars") != "polars":
            raise Unsupported(f"Polars doesn't run the {request.dialect} dialect.")
        result = self._ctx.execute(request.code, eager=True)
        if result.width == 0:
            return None, 0
        return result.head(request.preview_rows).to_arrow(), result.height

    def plan(self, code: str, dialect: str | None = None) -> str:
        self._check_open()
        return str(self._ctx.execute(code, eager=False).explain())

    def catalog(self) -> list[dict[str, Any]]:
        self._check_open()
        return [{"catalog": "session", "schema": "session", "name": n, "type": "table"} for n in sorted(self._ctx.tables())]

    def _release(self) -> None:
        for name in list(self._ctx.tables()):
            self._ctx.unregister(name)


class PolarsAdapter:
    def describe(self) -> EngineDescriptor:
        return POLARS

    def open_session(self, config: SessionConfig, authorization: Any = None) -> PolarsSession:
        return PolarsSession(config)
