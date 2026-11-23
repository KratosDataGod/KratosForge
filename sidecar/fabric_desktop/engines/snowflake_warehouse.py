"""SQL cells on a Snowflake warehouse, read-only (SF-003, Preview; tested against fakes, not a live account).

The session sends each statement through Snowflake's SQL API with engines/snowflake_remote.py's
client and the sign-in from engines/snowflake_auth.py:

    POST /api/v2/statements?async=true          submit, with the connection's role, database, schema, warehouse
    GET  /api/v2/statements/{handle}            follow it to its first result partition
    GET  /api/v2/statements/{handle}?partition=N  later partitions, only as many as the preview or result needs
    POST /api/v2/statements/{handle}/cancel     Cancel

Nothing opens without a ``RemoteAuthorization`` for this engine and ``account/warehouse`` (ADR-003): a
statement on a suspended warehouse resumes it, and a running warehouse uses credits. KratosForge never
sends a cell here on its own and never falls back to another engine, nor to the local translator
(fabric_desktop/snowflake_sql.py), which this module doesn't use.

Read-only first: every statement of a cell is read with sqlglot (Snowflake dialect) before anything is
sent. Queries (SELECT, WITH, set operations, VALUES), SHOW, DESCRIBE and EXPLAIN run. Anything else
is refused by name, SYSTEM$ functions too (some change state), and SQL sqlglot can't read is refused
rather than guessed at. The check reads the SQL only. What a role may do on Snowflake is still up to
Snowflake.

A cell's statements run one after another and the last one's result is shown: its first
``preview_rows`` rows, read from as few partitions as that takes, with Snowflake's row count. Values
are typed from the result's rowType. A whole result (the ``result`` capability, to compare engines)
reads every partition, and more rows than asked for, or more than RESULT_BYTES, is an error, never a
cut table. Its rows come onto this PC, so a caller asks for them only after its own confirm.
"""

from __future__ import annotations

import json
import re
import threading
import time
from decimal import Decimal
from typing import Any, Callable

import pyarrow as pa

from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import RESULT_BYTES, CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported
from .local_sql import ResultTooLarge
from .snowflake_auth import SnowflakeAuthError, provider_for
from .snowflake_remote import Client, SnowflakeError, rows as answer_rows

_T = "unit_tests"
MAX_SECONDS = 2 * 60 * 60    # Snowflake stops a statement after this; this PC stops following a minute later
MAX_PARTITIONS = 10_000
CATALOG_LIMIT = 1000
READ_ONLY = "SELECT, WITH, SHOW, DESCRIBE and EXPLAIN"
_PLAIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

SNOWFLAKE_SQL = EngineDescriptor(
    id="snowflake-sql", label="Snowflake SQL warehouse (remote)", location="remote", platform="snowflake",
    billing="snowflake_credits", adapter=True, family="snowflake", preview=True,
    languages={"sql": Support("native", "Snowflake SQL on the warehouse, read-only statements for now.", _T)},
    dialects={"snowflake": Support("native", f"Read-only statements only ({READ_ONLY}).", _T)},
    capabilities={
        "session": Support("native", "Statements on one warehouse with the connection's role, database and schema. "
                           "The warehouse keeps running until its auto-suspend.", _T),
        "execute": Support("partial", f"SQL API, read-only statements ({READ_ONLY}). Anything else is refused "
                           "before it is sent. Several statements in a cell run in order.", _T),
        "cancel": Support("native", "Cancels the statement on Snowflake.", _T),
        "stream": Support("unsupported", "The result arrives when the statement finishes."),
        "variables": Support("unsupported", "A Snowflake warehouse holds tables, not variables. Catalog lists them."),
        "debug": Support("unsupported", "SQL has no step debugger. Use Plan."),
        "plan": Support("partial", "EXPLAIN USING TEXT, sent without the warehouse. The query profile is in "
                        "Snowsight's query history.", _T),
        "metrics": Support("partial", "Wall time and Snowflake's row count. Bytes scanned and the profile are in "
                           "Snowsight's query history.", _T),
        "catalog": Support("partial", "SHOW DATABASES, SCHEMAS or OBJECTS as the connection's role, without the "
                           "warehouse, up to 1,000 entries.", _T),
        "cost": Support("unsupported", "Credits are metered per warehouse, not per cell. A running warehouse uses "
                        "credits until it suspends."),
        "compat": Support("partial", "The language, the dialect and read-only statements are checked before "
                          "anything is sent. Snowflake checks the SQL itself.", _T),
        "result": Support("partial", "The last statement's whole result, every partition, typed from its rowType. "
                          "VARIANT, OBJECT, ARRAY and BINARY come back as text. More rows than asked for is an "
                          "error. The rows are copied onto this PC.", _T),
    },
    requirements=("A Snowflake account and a warehouse, with USAGE on it for the connection's role",
                  "Key-pair sign-in or a programmatic access token in a connection profile"),
    limitations=("Preview: tested against fakes of the documented API, not yet against a live account.",
                 "A statement on a suspended warehouse resumes it, and a running warehouse uses credits until it suspends.",
                 f"Read-only for now. {READ_ONLY} run, and anything else is refused before it is sent.",
                 "The read-only check reads the SQL. A user-defined function a query calls runs whatever it holds.",
                 "Only the last statement's result is shown.",
                 "TIMESTAMP_TZ values are shown in UTC, without their offset.",
                 "A statement still running after two hours is stopped."),
)


def _first_word(text: str) -> str:
    word = re.match(r"\s*([A-Za-z_]+)", text)
    return word.group(1).upper() if word else "This statement"


def _flow_chain(text: str) -> bool:
    """Whether ``text`` holds Snowflake's flow operator ``->>``, which chains statements, outside strings
    and comments. A command's tail (EXPLAIN ...) comes back from the tokenizer as one string, so it is
    read again. SQL the tokenizer can't read counts as a chain, so it is refused."""
    from sqlglot.dialects.snowflake import Snowflake
    from sqlglot.errors import TokenError
    from sqlglot.tokens import TokenType

    try:
        tokens = Snowflake().tokenize(text)
    except TokenError:  # unreadable SQL is refused, never guessed read-only
        return True
    for i, token in enumerate(tokens):
        if token.token_type == TokenType.DARROW:
            return True
        if (token.token_type == TokenType.COMMAND and i + 1 < len(tokens)
                and tokens[i + 1].token_type == TokenType.STRING and _flow_chain(tokens[i + 1].text)):
            return True
    return False


def read_only_statements(code: str) -> list[str]:
    """``code``'s statements, each checked read-only. Raises Unsupported, naming the statement, for
    anything else, and for SQL sqlglot can't read, before anything is sent."""
    import sqlglot
    from sqlglot import exp

    from .databricks_sql import split_statements
    out = []
    for text in split_statements(code, "snowflake"):
        if _flow_chain(text):
            raise Unsupported("->> chains statements on Snowflake, and this engine runs one read-only statement at "
                              "a time for now. Run each statement on its own, or run the chain in Snowsight.")
        try:
            tree = sqlglot.parse_one(text, read="snowflake")
        except Exception:  # noqa: BLE001 - unreadable SQL is refused, never guessed read-only
            raise Unsupported("This engine runs only statements it can tell are read-only, and it couldn't read "
                              f"this one ({text[:60]}). Check its syntax.") from None
        query = isinstance(tree, (exp.Query, exp.Values, exp.Show, exp.Describe)) or (
            isinstance(tree, exp.Command) and str(tree.this).upper() in ("SHOW", "DESCRIBE", "DESC", "EXPLAIN"))
        if isinstance(tree, exp.Select) and tree.args.get("into"):
            name = "SELECT ... INTO"
        elif isinstance(tree, exp.Query) and tree.args.get("locks"):
            name = "SELECT ... FOR UPDATE"
        elif not query:
            name = str(tree.this).upper() if isinstance(tree, exp.Command) else _first_word(text)
            if name in ("WITH", "This statement"):
                name = type(tree).__name__.upper()
        else:
            system = next((f.name for f in tree.find_all(exp.Anonymous) if f.name.upper().startswith("SYSTEM$")), "")
            if not system:
                out.append(text)
                continue
            raise Unsupported(f"{system.upper()} is a system function, and some of those change state on Snowflake, "
                              "so this engine doesn't send them for now. Run it in Snowsight.")
        raise Unsupported(f"{name} changes data on Snowflake, and this engine runs read-only statements for now "
                          f"({READ_ONLY}). Run it in Snowsight.")
    return out


def _ns(value: str) -> int:
    """Snowflake's ``seconds.nanoseconds`` (a TIMESTAMP_TZ's offset after a space is dropped) as nanoseconds."""
    return int(Decimal(value.split(" ", 1)[0]) * 1_000_000_000)


def _column(values: list[Any], col: dict[str, Any]) -> tuple[pa.Array, bool]:
    """One column typed from its rowType entry, and whether it was. A value that won't read as its
    type keeps the whole column as text, so nothing is lost."""
    kind = str(col.get("type") or "").lower()
    text = pa.array(values, pa.string())
    try:
        if kind == "fixed":
            scale, precision = int(col.get("scale") or 0), int(col.get("precision") or 38)
            if scale == 0:   # NUMBER(38,0), Snowflake's default integer: int64 while every value fits
                try:
                    return text.cast(pa.int64()), True
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    pass
            return pa.array([None if v is None else Decimal(v) for v in values],
                            pa.decimal128(min(max(precision, 1), 38), scale)), True
        if kind == "real":
            return text.cast(pa.float64()), True
        if kind == "boolean":
            return text.cast(pa.bool_()), True
        if kind == "date":
            return text.cast(pa.int32()).cast(pa.date32()), True
        if kind in ("time", "timestamp_ntz", "timestamp_ltz", "timestamp_tz"):
            ns = pa.array([None if v is None else _ns(v) for v in values], pa.int64())
            target = pa.time64("ns") if kind == "time" else \
                pa.timestamp("ns") if kind == "timestamp_ntz" else pa.timestamp("ns", tz="UTC")
            return ns.cast(target), True
    except (ArithmeticError, ValueError, TypeError, OverflowError, pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return text, False
    return text, kind in ("text", "")


def to_table(row_type: list[dict[str, Any]], data: list[list[Any]]) -> tuple[pa.Table, list[str]]:
    """Rows typed from the rowType, and the columns kept as text (with their Snowflake type)."""
    arrays, names, as_text = [], [], []
    for i, col in enumerate(row_type):
        arr, typed = _column([r[i] for r in data], col)
        names.append(str(col.get("name") or f"col{i}"))
        arrays.append(arr)
        if not typed:
            as_text.append(f"{names[-1]} ({str(col.get('type') or 'unknown').lower()})")
    return (pa.Table.from_arrays(arrays, names=names) if names else pa.table({})), as_text


def _data(page: dict[str, Any], width: int, info: Any) -> list[list[Any]]:
    data = page.get("data")
    data = [] if data is None else data
    if not isinstance(data, list) or any(not isinstance(r, list) or len(r) != width for r in data):
        raise SnowflakeError("Snowflake sent result rows that don't match its columns, so none of it was kept. "
                             "Run the cell again.")
    count = info.get("rowCount") if isinstance(info, dict) else None
    if isinstance(count, int) and not isinstance(count, bool) and count != len(data):
        raise SnowflakeError(f"Snowflake said a result partition holds {count:,} rows but sent {len(data):,}, so "
                             "none of it was kept. Run the cell again.")
    return data


def _name(name: str) -> str:
    """A profile's database or schema as Snowflake reads it: plain names as typed, others quoted."""
    return name if _PLAIN_NAME.fullmatch(name) else '"' + name.replace('"', '""') + '"'


def target_for(connection: dict[str, Any], warehouse: str) -> str:
    """What a RemoteAuthorization names: the account and the warehouse."""
    return f"{connection.get('account') or ''}/{warehouse}"


class SnowflakeSqlSession:
    engine = SNOWFLAKE_SQL.id

    def __init__(self, config: SessionConfig, client: Client, warehouse: str, max_seconds: float = MAX_SECONDS):
        self.notebook = config.notebook
        self.warehouse = warehouse
        self.role = str(config.options.get("role") or "")
        self.database = str(config.options.get("database") or "")
        self.schema = str(config.options.get("schema") or "")
        self._client = client
        self._max_seconds = max_seconds
        self._lock = threading.Lock()
        self._cancelled = False
        self._closed = False
        client.stopped = lambda: self._cancelled

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosed("This Snowflake SQL session was closed. Start the engine again to run cells.")

    def _run(self, sql: str, on_warehouse: bool = True) -> dict[str, Any]:
        return self._client.statement(sql, role=self.role, database=self.database, schema=self.schema,
                                      warehouse=self.warehouse if on_warehouse else "", timeout=int(self._max_seconds),
                                      max_wait=self._max_seconds + 60, asynchronous=True)

    def _rows(self, answer: dict[str, Any], want: int, whole: bool) -> list[list[Any]]:
        """The first ``want`` rows (a preview), or every row within ``want`` and RESULT_BYTES (``whole``),
        reading later partitions only as needed."""
        from .local_sql import _too_big, _too_many
        meta = answer.get("resultSetMetaData") or {}
        width = len(meta.get("rowType") or [])
        info = meta.get("partitionInfo")
        parts: list[Any] = info if isinstance(info, list) else []
        total = meta.get("numRows")
        total = total if isinstance(total, int) and not isinstance(total, bool) else None
        if len(parts) > MAX_PARTITIONS:
            raise SnowflakeError(f"The result has more than {MAX_PARTITIONS:,} partitions, so none of it was kept. "
                                 "Narrow the query.")
        if whole and total is not None and total > want:
            raise _too_many(want)
        if whole and sum(int(p.get("uncompressedSize") or 0) for p in parts if isinstance(p, dict)) > RESULT_BYTES:
            raise _too_big()
        handle = str(answer.get("statementHandle") or "")
        rows: list[list[Any]] = []
        size = 0
        for i in range(max(len(parts), 1)):
            if i and not whole and len(rows) >= want:
                break
            if i and self._cancelled:
                raise SnowflakeError("Cancelled.")
            batch = _data(answer if i == 0 else self._client.partition(handle, i), width,
                          parts[i] if i < len(parts) else None)
            rows += batch
            if whole:
                size += len(json.dumps(batch))
                if len(rows) > want:
                    raise _too_many(want)
                if size > RESULT_BYTES:
                    raise _too_big()
        if whole and total is not None and total != len(rows):
            raise SnowflakeError(f"Snowflake said the result has {total:,} rows but sent {len(rows):,}, so none of it "
                                 "was kept. Run the cell again.")
        return rows if whole else rows[:want]

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        self._check_open()
        if request.language != "sql":
            raise Unsupported("The Snowflake SQL engine runs SQL cells only.")
        if request.dialect not in (None, "snowflake"):
            raise Unsupported(f"A Snowflake warehouse runs Snowflake SQL, not {request.dialect}.")
        statements = read_only_statements(request.code)
        if not statements:
            raise Unsupported("The cell has no SQL to run.")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        self._cancelled = False
        started = time.perf_counter()
        whole = request.result_rows > 0
        try:
            answer: dict[str, Any] = {}
            for statement in statements:
                if self._cancelled:
                    raise SnowflakeError("Cancelled.")
                answer = self._run(statement)
            meta = answer.get("resultSetMetaData") or {}
            row_type = [c for c in meta.get("rowType") or [] if isinstance(c, dict)]
            rows = self._rows(answer, request.result_rows if whole else request.preview_rows, whole) \
                if row_type else []
            table, as_text = to_table(row_type, rows)
        except (ResultTooLarge, SnowflakeError, SnowflakeAuthError) as exc:
            out: dict[str, Any] = {"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}
            if on_output:
                on_output(out)
            status = "cancelled" if self._cancelled and not isinstance(exc, ResultTooLarge) else "error"
            return CellResult(status, [out], time.perf_counter() - started, None, self.engine)
        finally:
            self._lock.release()
        seconds = time.perf_counter() - started
        if not row_type:
            out = {"type": "text", "text": f"Done in {seconds:.2f} s on {self.warehouse}."}
            if on_output:
                on_output(out)
            return CellResult("ok", [out], seconds, None, self.engine)
        if whole:
            return self._whole_result(table, as_text, request, seconds, on_output)
        from .local_sql import _table_output
        total = meta.get("numRows")
        total = total if isinstance(total, int) and not isinstance(total, bool) else len(rows)
        out = _table_output(table, total)
        if on_output:
            on_output(out)
        return CellResult("ok", [out], seconds, total, self.engine)

    def _whole_result(self, table: pa.Table, as_text: list[str], request: CellRequest, seconds: float,
                      on_output: OutputSink | None) -> CellResult:
        from .local_sql import _table_output
        twice = sorted({n for n in table.column_names if table.column_names.count(n) > 1})
        if twice:   # verify matches columns by name, so two of one name can't be compared honestly
            out = {"type": "error", "ename": "SnowflakeError",
                   "evalue": f"The result has more than one column named {', '.join(twice)}. "
                             "Give each column its own name (AS ...) to compare it."}
            if on_output:
                on_output(out)
            return CellResult("error", [out], seconds, None, self.engine)
        outputs = [{"type": "stream", "text": f"Kept as text: {', '.join(as_text)}.\n"}] if as_text else []
        outputs.append(_table_output(table.slice(0, request.preview_rows), table.num_rows))
        if on_output:
            for o in outputs:
                on_output(o)
        return CellResult("ok", outputs, seconds, table.num_rows, self.engine, table=table)

    def cancel(self) -> bool:
        """Cancel the running cell: the client stops following its statement and cancels it on Snowflake."""
        if not self._lock.locked():
            return False
        self._cancelled = True
        return True

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(SNOWFLAKE_SQL.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        self._check_open()
        statements = read_only_statements(code)
        if len(statements) != 1:
            raise Unsupported("Plan explains one statement at a time.")
        answer = self._run("EXPLAIN USING TEXT " + statements[0], on_warehouse=False)
        return "\n".join(str(r[0]) for r in answer.get("data") or [] if isinstance(r, list) and r)

    def catalog(self) -> list[dict[str, Any]]:
        """Objects of the session's database.schema; schemas when only a database is set; else databases."""
        self._check_open()
        db, schema = self.database, self.schema
        if db and schema:
            found = answer_rows(self._run(f"SHOW OBJECTS IN SCHEMA {_name(db)}.{_name(schema)}", on_warehouse=False))
            return [{"catalog": r.get("database_name") or db, "schema": r.get("schema_name") or schema,
                     "name": r.get("name") or "", "type": "view" if "VIEW" in str(r.get("kind") or "").upper()
                     else "table"} for r in found[:CATALOG_LIMIT]]
        if db:
            found = answer_rows(self._run(f"SHOW SCHEMAS IN DATABASE {_name(db)}", on_warehouse=False))
            return [{"catalog": db, "schema": r.get("name") or "", "name": "", "type": "schema"}
                    for r in found[:CATALOG_LIMIT]]
        found = answer_rows(self._run("SHOW DATABASES", on_warehouse=False))
        return [{"catalog": r.get("name") or "", "schema": "", "name": "", "type": "catalog"}
                for r in found[:CATALOG_LIMIT]]

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True


class SnowflakeSqlAdapter:
    """``SessionConfig.options``: ``connection`` (a Snowflake connection from a connection profile,
    references only), ``warehouse`` (or the connection's), and optional ``role``, ``database`` and
    ``schema``. ``client_for`` replaces the HTTP client in tests."""

    def __init__(self, client_for: Callable[[dict[str, Any]], Client] | None = None):
        self._client_for = client_for or (lambda conn: Client(provider_for(conn)))

    def describe(self) -> EngineDescriptor:
        return SNOWFLAKE_SQL

    def open_session(self, config: SessionConfig, authorization: Any = None) -> SnowflakeSqlSession:
        conn = dict(config.options.get("connection") or {})
        warehouse = str(config.options.get("warehouse") or conn.get("warehouse") or "")
        if not warehouse:
            raise Unsupported("Pick a warehouse for this Snowflake connection first, in Settings > Connections.")
        require(SNOWFLAKE_SQL, target_for(conn, warehouse), authorization)
        options = {k: conn.get(k, "") for k in ("role", "database", "schema")} | \
            {k: v for k, v in config.options.items() if k in ("role", "database", "schema") and v}
        return SnowflakeSqlSession(SessionConfig(config.notebook, "", options), self._client_for(conn), warehouse)
