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

Usage: this PC measures wall seconds per cell. Billed DBUs are only in Databricks' system billing
tables, per warehouse and hour, never per cell, so this engine reports none (ADR-006).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import pyarrow as pa

from .. import redact
from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported
from .databricks_auth import DatabricksAuthError, TokenProvider, provider_for

_T = "unit_tests"
WAIT = "10s"                 # how long the first request waits for a quick statement to finish
POLL_SECONDS = 1.0
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED")
CATALOG_LIMIT = 1000         # items listed per Catalog call, across pages

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
    },
    requirements=("An Azure Databricks workspace with Unity Catalog", "A SQL warehouse, and CAN USE on it",
                  "Unity Catalog privileges on the data",
                  "A sign-in: the Databricks CLI (databricks auth login) or a service principal's secret reference"),
    limitations=("Preview: tested against fakes of the documented APIs, not yet against a live workspace.",
                 "A statement sent to a stopped warehouse starts it; the warehouse bills DBUs while it runs.",
                 "Results come back inline: over 25 MB the warehouse refuses them, so add a LIMIT.",
                 "Only the last statement's result is shown, and only from its first result chunk."),
)


class DatabricksError(RuntimeError):
    """The workspace refused or failed a request; the message says what to check."""


def split_statements(code: str) -> list[str]:
    """``code``'s statements as written, split on top-level semicolons (sqlglot's tokenizer, so a
    semicolon in a string or comment doesn't split). Unreadable SQL is sent whole: the warehouse decides."""
    import sqlglot
    from sqlglot.tokens import TokenType
    try:
        tokens = sqlglot.tokenize(code, read="databricks")
    except Exception:  # noqa: BLE001 - the warehouse reports what's wrong with SQL sqlglot can't read
        return [code.strip()] if code.strip() else []
    parts, start = [], 0
    for tok in tokens:
        if tok.token_type == TokenType.SEMICOLON:
            parts.append(code[start:tok.start])
            start = tok.end + 1
    parts.append(code[start:])
    return [p.strip() for p in parts if p.strip()]


_TYPES = {"BYTE": pa.int64(), "SHORT": pa.int64(), "INT": pa.int64(), "LONG": pa.int64(),
          "FLOAT": pa.float64(), "DOUBLE": pa.float64(), "BOOLEAN": pa.bool_(), "DATE": pa.date32()}


def to_table(manifest: dict[str, Any], rows: list[list[Any]]) -> pa.Table:
    """Rows from JSON_ARRAY (every value a string or null) typed by the manifest where that's safe;
    a column that won't convert stays text, so nothing is lost."""
    columns = (manifest.get("schema") or {}).get("columns") or []
    arrays, names = [], []
    for i, col in enumerate(columns):
        values = [r[i] if i < len(r) else None for r in rows]
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


class Client:
    """The few REST calls the session makes, with the token on each; errors never quote a token."""

    def __init__(self, tokens: TokenProvider, http: Any = None):
        if http is None:
            from ..http_session import session
            http = session()
        self.host = tokens.host
        self._tokens = tokens
        self._http = http

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = self._tokens.headers()
        try:
            resp = self._http.request(method, self.host + path, headers=headers, timeout=60, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a network failure becomes the person's message
            raise DatabricksError(f"Couldn't reach {self.host}: {type(exc).__name__}.") from None
        if resp.status_code >= 400:
            raise DatabricksError(self._refusal(resp))
        try:
            return resp.json() if resp.content else {}
        except ValueError:
            raise DatabricksError(f"{self.host} answered with something other than JSON (HTTP {resp.status_code}).") \
                from None

    @staticmethod
    def _refusal(resp: Any) -> str:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        message = redact.secrets(str(body.get("message") or "")) if isinstance(body, dict) else ""
        hint = {401: "Sign in again.", 403: "Ask for CAN USE on the warehouse or the Unity Catalog privilege.",
                404: "Check the warehouse ID and the workspace.", 429: "The workspace is busy; try again shortly."}
        return f"Databricks refused the request (HTTP {resp.status_code}). " + \
            (f"{message} " if message else "") + hint.get(resp.status_code, "")

    def warehouse(self, warehouse_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/2.0/sql/warehouses/{warehouse_id}")

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/api/2.0/sql/statements", json=body)

    def status(self, statement_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/2.0/sql/statements/{statement_id}")

    def cancel(self, statement_id: str) -> None:
        self._call("POST", f"/api/2.0/sql/statements/{statement_id}/cancel")

    def list(self, path: str, key: str, params: dict[str, str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while len(items) < CATALOG_LIMIT:
            page = self._call("GET", path, params=params | ({"page_token": token} if token else {}))
            items += page.get(key) or []
            token = page.get("next_page_token") or ""
            if not token:
                break
        return items[:CATALOG_LIMIT]


class DatabricksSqlSession:
    engine = DATABRICKS_SQL.id

    def __init__(self, config: SessionConfig, client: Client, warehouse_id: str,
                 sleep: Callable[[float], None] = time.sleep):
        self.notebook = config.notebook
        self.warehouse_id = warehouse_id
        self.catalog_name = str(config.options.get("catalog") or "")
        self.schema_name = str(config.options.get("schema") or "")
        self._client = client
        self._sleep = sleep
        self._lock = threading.Lock()
        self._statement = ""
        self._cancelled = False
        self._closed = False
        info = client.warehouse(warehouse_id)
        self.warehouse_name = str(info.get("name") or warehouse_id)
        self.warehouse_state = str(info.get("state") or "")

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosed("This Databricks SQL session was closed. Start the engine again to run cells.")

    def _run_one(self, statement: str) -> dict[str, Any]:
        body = {"warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": WAIT,
                "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY"}
        if self.catalog_name:
            body["catalog"] = self.catalog_name
        if self.schema_name:
            body["schema"] = self.schema_name
        reply = self._client.submit(body)
        self._statement = str(reply.get("statement_id") or "")
        if self._cancelled and self._statement:
            self._client.cancel(self._statement)
        while (reply.get("status") or {}).get("state") not in TERMINAL:
            self._sleep(POLL_SECONDS)
            reply = self._client.status(self._statement)
        state = reply["status"]["state"]
        if state == "FAILED":
            err = reply["status"].get("error") or {}
            raise DatabricksError(redact.secrets(str(err.get("message") or "The statement failed on the warehouse.")))
        if state != "SUCCEEDED":
            raise DatabricksError("Cancelled.")
        return reply

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
            for statement in statements:
                if self._cancelled:
                    raise DatabricksError("Cancelled.")
                reply = self._run_one(statement)
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
        if columns:
            from .local_sql import _table_output
            rows = ((reply.get("result") or {}).get("data_array") or [])[:request.preview_rows]
            total = int(manifest.get("total_row_count") or len(rows))
            out = _table_output(to_table(manifest, rows), total)
            if manifest.get("truncated"):
                out["truncated_by_warehouse"] = True
            result = CellResult("ok", [out], seconds, total, self.engine)
        else:
            out = {"type": "text", "text": f"Done in {seconds:.2f} s on {self.warehouse_name}."}
            result = CellResult("ok", [out], seconds, None, self.engine)
        if on_output:
            on_output(out)
        return result

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
