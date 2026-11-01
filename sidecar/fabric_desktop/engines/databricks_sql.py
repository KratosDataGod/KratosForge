"""SQL cells on an Azure Databricks SQL warehouse (Preview; tested against fakes, not a live workspace).

The session talks to the documented REST APIs only, with a token from databricks_auth.py:

    Statement Execution API   POST /api/2.0/sql/statements, GET .../{id}, POST .../{id}/cancel
    SQL Warehouses API        GET /api/2.0/sql/warehouses/{id} (name and state; never starts it)
    Unity Catalog API         GET /api/2.1/unity-catalog/catalogs, schemas, tables (Catalog)

Nothing opens without a ``RemoteAuthorization`` for this engine and warehouse (ADR-003): a statement
sent to a stopped warehouse starts it, and a running warehouse bills Databricks units (DBU) for as
long as it runs. KratosForge never sends a cell here on its own, and never falls back to it.

A cell may hold several statements; they run one after another, as Databricks notebooks run a SQL
cell, and the last one's result is shown: the first ``preview_rows`` rows of its first result chunk,
with the warehouse's exact row count. Rows come back inline as JSON (the API's INLINE disposition),
so a result over 25 MB is refused by the warehouse; add a LIMIT. Cancel stops the running statement
on the warehouse; closing the session cancels it too. The warehouse itself is never stopped by this
session (it stops on its own after its auto-stop minutes).

A cell can also hand back its whole result (the ``result`` capability, to compare engines): the
last statement runs with a row limit one above the rows asked for, every result chunk is read in
order (``GET .../{id}/result/chunks/{n}``), and the values are typed from the manifest. More rows
than asked for, or more than ``RESULT_BYTES``, is an error, never a cut table. The rows come onto
this PC, so a caller asks for them only after its own confirm (engines/compare.py).

Usage: this PC measures wall seconds per cell. Billed DBUs are only in Databricks' system billing
tables, per warehouse and hour, never per cell, so this engine reports none (ADR-006).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

import pyarrow as pa

from .. import redact
from ..paging import Pages, PagingError
from ..retrying import send_remote
from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import RESULT_BYTES, CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported
from .databricks_auth import DatabricksAuthError, TokenProvider, provider_for
from .local_sql import ResultTooLarge

_T = "unit_tests"
WAIT = "10s"                 # how long the first request waits for a quick statement to finish
POLL_SECONDS = 1.0
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED")
CATALOG_LIMIT = 1000         # items listed per Catalog call, across pages
MAX_CHUNKS = 10_000          # result chunks read for a whole result; more stops with an error
MAX_SECONDS = 2 * 60 * 60    # a statement still running after this is cancelled and reported
# Warehouse and statement IDs as Databricks issues them; checked before either goes into a URL path.
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

DATABRICKS_SQL = EngineDescriptor(
    id="databricks-sql", label="Databricks SQL warehouse (remote)", location="remote", platform="databricks",
    billing="databricks_dbu", adapter=True, family="databricks-sql", preview=True,
    languages={"sql": Support("native", "Databricks SQL on the warehouse.", _T)},
    dialects={"databricks": Support("native", evidence=_T),
              "spark": Support("native", "Databricks SQL is Spark SQL with Databricks' additions.", _T)},
    capabilities={
        "session": Support("native", "Statements on one warehouse; closing the session cancels a running one. "
                           "The warehouse keeps running until its auto-stop.", _T),
        "execute": Support("native", "Statement Execution API; several statements in a cell run in order.", _T),
        "cancel": Support("native", "Cancels the statement on the warehouse.", _T),
        "stream": Support("unsupported", "The result arrives when the statement finishes."),
        "variables": Support("unsupported", "A SQL warehouse holds tables, not variables; Catalog lists them."),
        "debug": Support("unsupported", "SQL has no step debugger; use Plan."),
        "plan": Support("partial", "EXPLAIN runs on the warehouse, so it starts a stopped warehouse too. The "
                        "query profile is in Databricks' query history.", _T),
        "metrics": Support("partial", "Wall time and the warehouse's exact row count. Bytes scanned and the "
                           "profile are in Databricks' query history.", _T),
        "catalog": Support("native", "Unity Catalog catalogs, schemas and tables, without starting the warehouse.",
                           _T),
        "cost": Support("unsupported", "Billed DBUs are in Databricks' system billing tables, per warehouse and "
                        "hour, not per cell. A running warehouse bills DBUs until it auto-stops."),
        "compat": Support("partial", "The language and dialect are checked before running; the warehouse "
                          "checks the SQL itself.", _T),
        "result": Support("partial", "The last statement's whole result, every chunk, typed from the warehouse's "
                          "schema; arrays, maps, structs, binary and intervals come back as text. Inline results "
                          "are capped at 25 MB by the warehouse. More rows than asked for is an error. The rows "
                          "are copied onto this PC.", _T),
    },
    requirements=("An Azure Databricks workspace with Unity Catalog", "A SQL warehouse, and CAN USE on it",
                  "Unity Catalog privileges on the data",
                  "A sign-in: the Databricks CLI (databricks auth login) or a service principal's secret reference"),
    limitations=("Preview: tested against fakes of the documented APIs, not yet against a live workspace.",
                 "A statement sent to a stopped warehouse starts it; the warehouse bills DBUs while it runs.",
                 "Results come back inline: over 25 MB the warehouse refuses them, so add a LIMIT.",
                 "Only the last statement's result is shown, and only from its first result chunk (a whole "
                 "result for comparing engines reads every chunk).",
                 "Cells can change data on the warehouse (CREATE, INSERT, MERGE, DROP) with no further confirm, as "
                 "in a Databricks notebook; the confirm is for starting the session.",
                 "A statement still running after two hours is cancelled."),
)


class DatabricksError(RuntimeError):
    """The workspace refused or failed a request; the message says what to check."""


def split_statements(code: str) -> list[str]:
    """``code``'s statements as written, split on top-level semicolons (sqlglot's tokenizer, so a
    semicolon in a string or comment doesn't split). Unreadable SQL is sent whole: the warehouse decides.
    A part that is only comments is left out: sending it would wake a stopped warehouse for nothing."""
    import sqlglot
    from sqlglot.tokens import TokenType
    try:
        tokens = sqlglot.tokenize(code, read="databricks")
    except Exception:  # noqa: BLE001 - the warehouse reports what's wrong with SQL sqlglot can't read
        return [code.strip()] if code.strip() else []
    parts, start, has_sql = [], 0, False
    for tok in tokens:
        if tok.token_type == TokenType.SEMICOLON:
            if has_sql:
                parts.append(code[start:tok.start])
            start, has_sql = tok.end + 1, False
        else:
            has_sql = True
    if has_sql:
        parts.append(code[start:])
    return [p.strip() for p in parts if p.strip()]


_TYPES = {"BYTE": pa.int64(), "SHORT": pa.int64(), "INT": pa.int64(), "LONG": pa.int64(),
          "FLOAT": pa.float64(), "DOUBLE": pa.float64(), "BOOLEAN": pa.bool_(), "DATE": pa.date32()}


def _one_value_per_column(rows: list[list[Any]], width: int) -> None:
    if any(not isinstance(r, list) or len(r) != width for r in rows):
        raise DatabricksError("The warehouse sent a result that doesn't have one value per column.")


def to_table(manifest: dict[str, Any], rows: list[list[Any]]) -> pa.Table:
    """Rows from JSON_ARRAY (every value a string or null) typed by the manifest where that's safe;
    a column that won't convert stays text, so nothing is lost. JSON_ARRAY sends every column of every
    row, nulls included, so a row of another length is a broken reply: DatabricksError, never NULLs the
    warehouse didn't send."""
    columns = (manifest.get("schema") or {}).get("columns") or []
    _one_value_per_column(rows, len(columns))
    arrays, names = [], []
    for i, col in enumerate(columns):
        values = [r[i] for r in rows]
        arr = pa.array(values, type=pa.string())
        target = _TYPES.get(str(col.get("type_name") or "").upper())
        if target is not None:
            try:
                arr = arr.cast(target)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                pass  # keep the text: a value Arrow reads differently from the warehouse
        arrays.append(arr)
        names.append(str(col.get("name") or f"col{i}"))
    return pa.Table.from_arrays(arrays, names=names) if names else pa.table({})


# Manifest type names and the Arrow types a whole result uses for them; TIMESTAMP is read as UTC and
# kept without a zone, as the Fabric Spark engine keeps it, so the two compare.
_WHOLE_TYPES = {"BYTE": pa.int8(), "SHORT": pa.int16(), "INT": pa.int32(), "LONG": pa.int64(),
                "FLOAT": pa.float32(), "DOUBLE": pa.float64(), "BOOLEAN": pa.bool_(), "STRING": pa.string(),
                "DATE": pa.date32(), "TIMESTAMP_NTZ": pa.timestamp("us")}


def whole_table(manifest: dict[str, Any], rows: list[list[Any]]) -> tuple[pa.Table, list[str]]:
    """A whole result typed from the manifest, and the columns kept as text (with their type). Raises
    DatabricksError for a reply that isn't one value per column, or two columns of one name."""
    columns = (manifest.get("schema") or {}).get("columns") or []
    names = [str(c.get("name") or f"col{i}") for i, c in enumerate(columns)]
    _one_value_per_column(rows, len(names))
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:   # verify matches columns by name, so two of one name can't be compared honestly
        raise DatabricksError(f"The result has more than one column named {', '.join(twice)}. "
                              "Give each column its own name (AS ...) to compare it.")
    arrays, as_text = [], []
    for i, col in enumerate(columns):
        values = pa.array([r[i] for r in rows], pa.string())
        kind = str(col.get("type_name") or "").upper()
        try:
            if kind == "TIMESTAMP":
                arr = values.cast(pa.timestamp("us", tz="UTC")).cast(pa.timestamp("us"))
            elif kind == "DECIMAL":
                arr = values.cast(pa.decimal128(int(col["type_precision"]), int(col["type_scale"])))
            else:
                arr = values.cast(_WHOLE_TYPES.get(kind, pa.string()))
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, KeyError, TypeError, ValueError):
            arr = values   # a value that doesn't read as its type stays text
        if arr.type == pa.string() and kind != "STRING":
            as_text.append(f"{names[i]} ({str(col.get('type_text') or kind).lower()})")
        arrays.append(arr)
    return (pa.Table.from_arrays(arrays, names=names) if names else pa.table({})), as_text


def _checked_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DatabricksError(f"That isn't a Databricks {what} ID.")
    return value


# What to check when an API refuses, by the API that refused: (path prefix, {status: hint}). The
# first prefix that matches wins; anything else gets _ANY_HINTS.
_HINTS: tuple[tuple[str, dict[int, str]], ...] = (
    ("/api/2.0/sql/statements", {403: "Ask for CAN USE on the SQL warehouse and the Unity Catalog privileges on the data.",
                                 404: "The statement is no longer on the warehouse; run the cell again."}),
    ("/api/2.0/sql/warehouses", {403: "Ask for CAN USE on the SQL warehouse.",
                                 404: "Check the SQL warehouse ID and the workspace."}),
    ("/api/2.1/clusters", {403: "Ask for CAN ATTACH TO on the cluster, or for access to clusters in this workspace.",
                           404: "Check the cluster ID and the workspace."}),
    ("/api/2.1/unity-catalog/metastore_summary", {
        403: "Ask for access to the workspace's Unity Catalog metastore.",
        404: "This workspace has no Unity Catalog metastore assigned; an account admin assigns one."}),
    ("/api/2.1/unity-catalog", {403: "Ask for USE CATALOG and USE SCHEMA in Unity Catalog, and SELECT on the tables.",
                                404: "Check the catalog and schema names."}),
    ("/api/2.0/preview/scim", {403: "Your sign-in can't read its own user details in this workspace; ask a workspace admin.",
                               404: "Check the workspace URL."}),
)
_ANY_HINTS = {401: "Sign in again.", 403: "Ask a workspace admin for access.", 404: "Check the workspace URL.",
              429: "The workspace is busy; try again shortly."}


def refusal_hint(status: int, path: str, error_code: str = "") -> str:
    """What to check after ``status`` from the API at ``path``. Sign-in and throttling read the same
    everywhere; a missing metastore is named whichever Unity Catalog call found it."""
    if error_code == "METASTORE_DOES_NOT_EXIST":
        return dict(_HINTS)["/api/2.1/unity-catalog/metastore_summary"][404]
    if status in (401, 429):
        return _ANY_HINTS[status]
    for prefix, hints in _HINTS:
        if path.startswith(prefix):
            return hints.get(status, "")
    return _ANY_HINTS.get(status, "")


class Client:
    """The few REST calls the session makes, with the token on each; errors never quote a token."""

    def __init__(self, tokens: TokenProvider, http: Any = None, sleep: Callable[[float], None] = time.sleep):
        if http is None:
            from ..http_session import session
            http = session()
        self.host = tokens.host
        self._tokens = tokens
        self._http = http
        self._sleep = sleep
        self.stopped: Callable[[], bool] = lambda: False  # the session points this at its Cancel

    def _call(self, method: str, path: str, repeatable: bool | None = None, **kwargs: Any) -> dict[str, Any]:
        """One REST call. Throttling (429) is tried again; 502/503/504 too when the call is
        ``repeatable`` (left out: GETs are). Cancels are never cut short by Cancel itself."""
        if repeatable is None:
            repeatable = method == "GET"
        headers = self._tokens.headers()  # a sign-in problem is its own error, not "couldn't reach"

        def send() -> Any:
            return self._http.request(method, self.host + path, headers=headers, timeout=60, **kwargs)
        try:
            resp, tries = send_remote(send, repeatable, self._sleep,
                                      (lambda: False) if path.endswith("/cancel") else self.stopped)
        except Exception as exc:  # noqa: BLE001 - a network failure becomes the person's message
            raise DatabricksError(f"Couldn't reach {self.host}: {type(exc).__name__}.") from None
        if resp.status_code >= 400:
            raise DatabricksError(self._refusal(resp, path).rstrip() + (f" Tried {tries} times." if tries > 1 else ""))
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            raise DatabricksError(f"{self.host} answered with something other than JSON (HTTP {resp.status_code}).") \
                from None

    @staticmethod
    def _refusal(resp: Any, path: str = "") -> str:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        # The workspace's own message, as one bounded line: it reaches the CLI, the UI and the log.
        message = redact.one_line(body.get("message") or "", 600) if isinstance(body, dict) else ""
        code = str(body.get("error_code") or "") if isinstance(body, dict) else ""
        return f"Databricks refused the request (HTTP {resp.status_code}). " + \
            (f"{message} " if message else "") + refusal_hint(resp.status_code, path, code)

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """A read (GET) of a documented API path; reading never starts compute."""
        return self._call("GET", path, params=params or {})

    def warehouse(self, warehouse_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/2.0/sql/warehouses/{_checked_id(warehouse_id, 'SQL warehouse')}")

    def cluster(self, cluster_id: str) -> dict[str, Any]:
        """A cluster's spark_version and state (Clusters API). Reading never starts it."""
        return self._call("GET", "/api/2.1/clusters/get", params={"cluster_id": _checked_id(cluster_id, "cluster")})

    def stop_warehouse(self, warehouse_id: str) -> None:
        """Ask the warehouse to stop (SQL Warehouses API). Only the live test calls it."""
        self._call("POST", f"/api/2.0/sql/warehouses/{_checked_id(warehouse_id, 'SQL warehouse')}/stop")

    def terminate_cluster(self, cluster_id: str) -> None:
        """Terminate the cluster, keeping its definition (Clusters API ``delete``). Only the live test calls it."""
        self._call("POST", "/api/2.1/clusters/delete", json={"cluster_id": _checked_id(cluster_id, "cluster")})

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/api/2.0/sql/statements", json=body)

    def status(self, statement_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/2.0/sql/statements/{_checked_id(statement_id, 'statement')}")

    def chunk(self, statement_id: str, index: int) -> dict[str, Any]:
        """One result chunk of a finished statement (INLINE: rows in ``data_array``)."""
        return self._call("GET", f"/api/2.0/sql/statements/{_checked_id(statement_id, 'statement')}"
                                 f"/result/chunks/{int(index)}")

    def cancel(self, statement_id: str) -> None:
        self._call("POST", f"/api/2.0/sql/statements/{_checked_id(statement_id, 'statement')}/cancel",
                   repeatable=True)

    def list(self, path: str, key: str, params: dict[str, str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        pages = Pages(f"{self.host}{path}")  # empty pages with a next token would otherwise never reach the limit
        while len(items) < CATALOG_LIMIT:
            page = self._call("GET", path, params=params | ({"page_token": token} if token else {}))
            items += page.get(key) or []
            token = page.get("next_page_token") or ""
            if not token:
                break
            try:
                pages.follow(token)
            except PagingError as exc:
                raise DatabricksError(str(exc)) from None
        return items[:CATALOG_LIMIT]


class DatabricksSqlSession:
    engine = DATABRICKS_SQL.id

    def __init__(self, config: SessionConfig, client: Client, warehouse_id: str,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 max_seconds: float = MAX_SECONDS):
        self.notebook = config.notebook
        self.warehouse_id = warehouse_id
        self.catalog_name = str(config.options.get("catalog") or "")
        self.schema_name = str(config.options.get("schema") or "")
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._max_seconds = max_seconds
        self._lock = threading.Lock()
        self._statement = ""
        self._cancelled = False
        self._closed = False
        client.stopped = lambda: self._cancelled
        info = client.warehouse(warehouse_id)
        self.warehouse_name = str(info.get("name") or warehouse_id)
        self.warehouse_state = str(info.get("state") or "")

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosed("This Databricks SQL session was closed. Start the engine again to run cells.")

    def _run_one(self, statement: str, row_limit: int = 0) -> dict[str, Any]:
        body: dict[str, Any] = {"warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": WAIT,
                                "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY"}
        if row_limit:
            body["row_limit"] = row_limit
        if self.catalog_name:
            body["catalog"] = self.catalog_name
        if self.schema_name:
            body["schema"] = self.schema_name
        started = self._clock()
        reply = self._client.submit(body)
        self._statement = str(reply.get("statement_id") or "")
        if self._cancelled and self._statement:  # Cancel was pressed while the statement was being sent
            try:
                self._client.cancel(self._statement)
            except (DatabricksError, DatabricksAuthError):
                raise DatabricksError(self._abandon("Cancelled.")) from None
        while (reply.get("status") or {}).get("state") not in TERMINAL:
            if self._clock() - started > self._max_seconds:
                raise DatabricksError(self._abandon(
                    f"The statement was still running after {self._max_seconds / 60:.0f} minutes."))
            self._sleep(POLL_SECONDS)
            try:
                reply = self._client.status(self._statement)
            except (DatabricksError, DatabricksAuthError) as exc:
                raise DatabricksError(self._abandon(str(exc))) from None
        state = reply["status"]["state"]
        if state == "FAILED":
            err = reply["status"].get("error") or {}
            raise DatabricksError(redact.secrets(str(err.get("message") or "The statement failed on the warehouse.")))
        if state != "SUCCEEDED":
            raise DatabricksError("Cancelled.")
        return reply

    def _abandon(self, problem: str) -> str:
        """``problem`` (a failed poll, the time limit, a Cancel whose first try failed), once the statement
        has been asked to stop: a statement this
        PC lost track of must not keep the warehouse busy (and billing) with no one watching it."""
        try:
            self._client.cancel(self._statement)
        except (DatabricksError, DatabricksAuthError):
            return (f"{problem} The statement may still be running on {self.warehouse_name}: stop it in "
                    "Databricks' query history.")
        return f"{problem} The statement was stopped on {self.warehouse_name}."

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        self._check_open()
        if request.language != "sql":
            raise Unsupported("The Databricks SQL engine runs SQL cells only.")
        if request.dialect not in (None, "databricks", "spark"):
            raise Unsupported(f"A Databricks SQL warehouse runs Databricks SQL, not {request.dialect}.")
        statements = split_statements(request.code)
        if not statements:
            raise Unsupported("The cell has no SQL to run.")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        self._cancelled = False
        started = time.perf_counter()
        try:
            reply: dict[str, Any] = {}
            whole = request.result_rows > 0
            for n, statement in enumerate(statements, 1):
                if self._cancelled:
                    raise DatabricksError("Cancelled.")
                last = n == len(statements)
                reply = self._run_one(statement, request.result_rows + 1 if whole and last else 0)
            if whole:
                rows = self._all_rows(reply, request.result_rows)
        except ResultTooLarge as exc:
            out = {"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}
            if on_output:
                on_output(out)
            return CellResult("error", [out], time.perf_counter() - started, None, self.engine)
        except (DatabricksError, DatabricksAuthError) as exc:
            out = {"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}
            if on_output:
                on_output(out)
            return CellResult("cancelled" if self._cancelled else "error", [out], time.perf_counter() - started,
                              None, self.engine)
        finally:
            self._statement = ""
            self._lock.release()
        seconds = time.perf_counter() - started
        manifest = reply.get("manifest") or {}
        columns = (manifest.get("schema") or {}).get("columns") or []
        if whole:
            return self._whole_result(manifest, rows, request, seconds, on_output)
        if columns:
            from .local_sql import _table_output
            rows = ((reply.get("result") or {}).get("data_array") or [])[:request.preview_rows]
            total = int(manifest.get("total_row_count") or len(rows))
            try:
                out = _table_output(to_table(manifest, rows), total)
            except DatabricksError as exc:
                out = {"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}
                if on_output:
                    on_output(out)
                return CellResult("error", [out], seconds, None, self.engine)
            if manifest.get("truncated"):
                out["truncated_by_warehouse"] = True
            result = CellResult("ok", [out], seconds, total, self.engine)
        else:
            out = {"type": "text", "text": f"Done in {seconds:.2f} s on {self.warehouse_name}."}
            result = CellResult("ok", [out], seconds, None, self.engine)
        if on_output:
            on_output(out)
        return result

    def _all_rows(self, reply: dict[str, Any], limit: int) -> list[list[Any]]:
        """Every row of a finished statement, chunk by chunk, within ``limit`` rows and RESULT_BYTES."""
        from .local_sql import _too_big, _too_many
        statement = str(reply.get("statement_id") or "")
        data = reply.get("result") or {}
        rows: list[list[Any]] = []
        size, index, read = 0, 0, 1   # the statement's own reply carries chunk 0
        while True:
            batch = data.get("data_array") or []
            size += len(json.dumps(batch))
            rows += batch
            if len(rows) > limit:
                raise _too_many(limit)
            if size > RESULT_BYTES:
                raise _too_big()
            nxt = data.get("next_chunk_index")
            if nxt is None:
                return rows
            if self._cancelled:
                raise DatabricksError("Cancelled.")
            # Chunks come in order, one after another, and a result has at most MAX_CHUNKS: a reply that
            # skips, repeats or never ends (empty chunks included) stops here.
            if isinstance(nxt, bool) or not isinstance(nxt, int) or nxt != index + 1:
                raise DatabricksError("The warehouse sent result chunks out of order.")
            if read >= MAX_CHUNKS:
                raise DatabricksError(f"The result has more than {MAX_CHUNKS:,} chunks, so none of it was kept.")
            index, read = nxt, read + 1
            data = self._client.chunk(statement, nxt)

    def _whole_result(self, manifest: dict[str, Any], rows: list[list[Any]], request: CellRequest,
                      seconds: float, on_output: OutputSink | None) -> CellResult:
        from .local_sql import _table_output
        if not (manifest.get("schema") or {}).get("columns"):
            out = {"type": "error", "ename": "DatabricksError",
                   "evalue": "The last statement returned no result to compare. End the cell with a query."}
            if on_output:
                on_output(out)
            return CellResult("error", [out], seconds, None, self.engine)
        try:
            table, as_text = whole_table(manifest, rows)
        except DatabricksError as exc:
            out = {"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}
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
        if not self._lock.locked():
            return False
        self._cancelled = True
        if self._statement:
            try:
                self._client.cancel(self._statement)
            except DatabricksError:
                return False  # the statement may have just finished; the cell reports what happened
        return True

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(DATABRICKS_SQL.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        self._check_open()
        statements = split_statements(code)
        if len(statements) != 1:
            raise Unsupported("Plan explains one statement at a time.")
        reply = self._run_one("EXPLAIN " + statements[0])
        rows = (reply.get("result") or {}).get("data_array") or []
        return "\n".join(str(r[0]) for r in rows if r)

    def catalog(self) -> list[dict[str, Any]]:
        """Tables of the session's catalog.schema; schemas when only a catalog is set; else catalogs."""
        self._check_open()
        uc = "/api/2.1/unity-catalog"
        if self.catalog_name and self.schema_name:
            tables = self._client.list(f"{uc}/tables", "tables",
                                       {"catalog_name": self.catalog_name, "schema_name": self.schema_name})
            return [{"catalog": t.get("catalog_name", self.catalog_name), "schema": t.get("schema_name", self.schema_name),
                     "name": t.get("name", ""), "type": "view" if "VIEW" in str(t.get("table_type", "")) else "table"}
                    for t in tables]
        if self.catalog_name:
            return [{"catalog": self.catalog_name, "schema": s.get("name", ""), "name": "", "type": "schema"}
                    for s in self._client.list(f"{uc}/schemas", "schemas", {"catalog_name": self.catalog_name})]
        return [{"catalog": c.get("name", ""), "schema": "", "name": "", "type": "catalog"}
                for c in self._client.list(f"{uc}/catalogs", "catalogs", {})]

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True


class DatabricksSqlAdapter:
    """``SessionConfig.options``: ``connection`` (a Databricks connection from a connection profile,
    references only), ``warehouse_id`` (or the connection's compute.warehouse_id), and optional
    ``catalog`` and ``schema``. ``client_for`` replaces the HTTP client in tests."""

    def __init__(self, client_for: Callable[[dict[str, Any]], Client] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._client_for = client_for or (lambda conn: Client(provider_for(conn)))
        self._sleep = sleep

    def describe(self) -> EngineDescriptor:
        return DATABRICKS_SQL

    def open_session(self, config: SessionConfig, authorization: Any = None) -> DatabricksSqlSession:
        conn = dict(config.options.get("connection") or {})
        warehouse = str(config.options.get("warehouse_id") or (conn.get("compute") or {}).get("warehouse_id") or "")
        if not warehouse:
            raise Unsupported("Pick a SQL warehouse for this connection first.")
        require(DATABRICKS_SQL, warehouse, authorization)
        options = {"catalog": conn.get("catalog", ""), "schema": conn.get("schema", "")} | \
            {k: v for k, v in config.options.items() if k in ("catalog", "schema") and v}
        return DatabricksSqlSession(SessionConfig(config.notebook, "", options), self._client_for(conn), warehouse,
                                    self._sleep)
