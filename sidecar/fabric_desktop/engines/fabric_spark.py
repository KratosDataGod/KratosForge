"""Fabric Spark: a notebook's cells in an interactive Spark session in a Fabric workspace (Preview;
tested against fakes of the documented Livy API, not a live tenant).

The session is a Livy session on a lakehouse, through Fabric's Livy API for Data Engineering:

    POST   .../lakehouses/{lakehouse}/livyapi/versions/2023-12-01/sessions
    GET    .../sessions/{id}                      (waits until it is idle)
    POST   .../sessions/{id}/statements           (one per cell: pyspark, sql or spark)
    GET    .../sessions/{id}/statements/{n}       (until the statement is available)
    POST   .../sessions/{id}/statements/{n}/cancel
    DELETE .../sessions/{id}                      (closing the session)

with the app's own Fabric sign-in. Statements are sent once, never retried, so a cell can't run
twice. Nothing opens without a ``RemoteAuthorization`` for this engine and lakehouse: a session runs
on the workspace's capacity and uses Fabric capacity units for as long as it is open, and the
organization's ``allow_fabric_runs`` policy can forbid it outright. Closing the session ends it in
Fabric; Fabric also ends an idle one on its own.

A SQL cell can also hand back its whole result (the ``result`` capability, to compare engines). It is
sent as a PySpark statement that runs the one query, collects at most the rows asked for plus one,
and prints them as JSON with Spark's schema; this PC types the values back from that schema. The
query text goes in as a Python string literal, so it runs as Spark SQL and nothing else. Over the
rows asked for, or over ``RESULT_BYTES``, is an error, never a cut table. The rows come onto this PC,
so a caller asks for them only after its own confirm (engines/compare.py).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .. import redact
from ..retrying import send_remote
from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import RESULT_BYTES, CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported

if TYPE_CHECKING:
    import pyarrow as pa

_T = "unit_tests"
API = "https://api.fabric.microsoft.com/v1"
LIVY = "livyapi/versions/2023-12-01"
POLL_SECONDS = 1.0
START_SECONDS = 15 * 60          # a custom pool can take minutes to start
MAX_SECONDS = 2 * 60 * 60        # a statement still running after this is cancelled
KINDS = {"pyspark": "pyspark", "python": "pyspark", "sql": "sql", "scala": "spark"}
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
_SESSION_DONE = ("dead", "error", "killed", "shutting_down", "success")
RESULT_MARK = "kratosforge-result@1"

FABRIC_SPARK = EngineDescriptor(
    id="fabric-spark", label="Fabric Spark (remote)", location="remote", platform="fabric",
    # Its own family, not LOCAL_SPARK's "spark": a session in Fabric can't carry its variables over to
    # Spark on this PC (or back) when the notebook switches engines.
    billing="fabric_capacity", adapter=True, family="fabric-spark", preview=True,
    languages={
        "pyspark": Support("native", "Fabric's own Spark, in a session on the workspace's capacity.", _T),
        "python": Support("native", "Python cells run in the Spark session's driver, as in a Fabric notebook.", _T),
        "sql": Support("native", "Spark SQL on the session, with the lakehouse as its default.", _T),
        "scala": Support("native", "Scala cells run in the same session.", _T),
    },
    dialects={"spark": Support("native", evidence=_T)},
    capabilities={
        "session": Support("native", "A Livy session on one lakehouse; closing it ends the session in Fabric. "
                           "Fabric also ends an idle one.", _T),
        "execute": Support("native", "One statement per cell, sent once and never retried.", _T),
        "cancel": Support("native", "Cancels the running statement in Fabric.", _T),
        "stream": Support("unsupported", "Output arrives when the cell finishes."),
        "variables": Support("unsupported", "Not listed for a remote session yet."),
        "debug": Support("unsupported", "No step debugger for a remote session."),
        "plan": Support("unsupported", "Call df.explain() or EXPLAIN in a cell; the Spark UI has the details."),
        "metrics": Support("partial", "Wall time per cell. The Spark UI and the Monitoring hub have the rest.", _T),
        "catalog": Support("unsupported", "Use the lakehouse explorer, or spark.catalog in a cell."),
        "cost": Support("unsupported", "Capacity use is in the Fabric Capacity Metrics app, per item and time, "
                        "not per cell. An open session uses capacity units until it ends."),
        "compat": Support("partial", "The cell's language is checked before sending; Spark checks the code.", _T),
        "result": Support("partial", "SQL cells only, one query. Spark sends the rows as JSON with its schema and "
                          "this PC types them back; timestamps as the session's time zone shows them; nested types "
                          "(arrays, maps, structs) and binary come back as text, and the cell says which. "
                          f"More rows than asked for, or over {RESULT_BYTES // 2**20} MB, is an error. "
                          "The rows are copied onto this PC.", _T),
    },
    requirements=("A Fabric workspace on a capacity, and Contributor on it", "A lakehouse for the session",
                  "Signed in to Fabric in KratosForge"),
    limitations=("Preview: tested against fakes of Fabric's Livy API, not yet against a live tenant.",
                 "A session uses Fabric capacity while it is open, including while idle, until it is closed or "
                 "Fabric ends it.",
                 "notebookutils and %magics are Fabric notebook features; a Livy session doesn't have all of them.",
                 "Your organization can turn off Fabric runs from KratosForge (allow_fabric_runs)."),
)


class FabricSparkError(RuntimeError):
    """Fabric refused or failed a request; the message says what to check."""


_UNTRACKED = ("The cell may still run in Fabric, and it can't be followed or cancelled from here: stop the "
              "engine to end the session.")


class NotLivyReply(FabricSparkError):
    """A 2xx reply that isn't the JSON object Livy documents."""


def _checked(value: str, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FabricSparkError(f"That isn't a Fabric {what} ID.")
    return value


class LivyClient:
    """The Livy calls for one lakehouse. A write is sent again only after a 429; errors never quote the token."""

    def __init__(self, token: Callable[[], str], workspace_id: str, lakehouse_id: str, http: Any = None,
                 sleep: Callable[[float], None] = time.sleep):
        if http is None:
            from ..http_session import session
            http = session()
        self._token = token
        self._http = http
        self._sleep = sleep
        self.stopped: Callable[[], bool] = lambda: False  # the session points this at its Cancel
        self.base = (f"{API}/workspaces/{_checked(workspace_id, _GUID, 'workspace')}/lakehouses/"
                     f"{_checked(lakehouse_id, _GUID, 'lakehouse')}/{LIVY}/sessions")

    def _call(self, method: str, path: str = "", repeatable: bool | None = None, **kwargs: Any) -> dict[str, Any]:
        """One Livy call. Throttling (429) is tried again; 502/503/504 too when the call is
        ``repeatable`` (left out: GETs are). Cancels and deletes are never cut short by Cancel itself."""
        if repeatable is None:
            repeatable = method == "GET"
        undo = method == "DELETE" or path.endswith("/cancel")
        headers = {"Authorization": f"Bearer {self._token()}"}

        def send() -> Any:
            return self._http.request(method, self.base + path, headers=headers, timeout=60, **kwargs)
        try:
            resp, tries = send_remote(send, repeatable, self._sleep, (lambda: False) if undo else self.stopped)
        except Exception as exc:  # noqa: BLE001 - a network failure becomes the person's message
            raise FabricSparkError(f"Couldn't reach Fabric: {type(exc).__name__}.") from None
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            message = redact.secrets(str(body.get("message") or "")) if isinstance(body, dict) else ""
            hint = {401: "Sign in again.", 403: "You need Contributor on the workspace.",
                    404: "Check the workspace and lakehouse.", 429: "The capacity is busy; try again shortly.",
                    430: "The capacity's Spark limit is reached; close another session or try later."}
            raise FabricSparkError((f"Fabric refused the request (HTTP {resp.status_code}). "
                                    + (f"{message} " if message else "") + hint.get(resp.status_code, "")).rstrip()
                                   + (f" Tried {tries} times." if tries > 1 else ""))
        if not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            # A proxy's sign-in page or a gateway's error page: never read as an empty answer, and its
            # text isn't repeated (it may echo the request).
            raise NotLivyReply(f"Fabric answered with something other than the JSON object Livy documents "
                               f"(HTTP {resp.status_code}). Check the network or proxy between this PC and Fabric.")
        return body

    def create(self, name: str, conf: dict[str, str]) -> dict[str, Any]:
        return self._call("POST", json={"name": name, "conf": conf})

    def session(self, sid: str) -> dict[str, Any]:
        return self._call("GET", f"/{_checked(sid, _ID, 'session')}")

    def submit(self, sid: str, code: str, kind: str) -> dict[str, Any]:
        return self._call("POST", f"/{_checked(sid, _ID, 'session')}/statements", json={"code": code, "kind": kind})

    def statement(self, sid: str, n: str) -> dict[str, Any]:
        return self._call("GET", f"/{_checked(sid, _ID, 'session')}/statements/{_checked(n, _ID, 'statement')}")

    def cancel(self, sid: str, n: str) -> None:
        self._call("POST", f"/{_checked(sid, _ID, 'session')}/statements/{_checked(n, _ID, 'statement')}/cancel",
                   repeatable=True)

    def delete(self, sid: str) -> None:
        self._call("DELETE", f"/{_checked(sid, _ID, 'session')}", repeatable=True)


def _outputs(output: dict[str, Any], preview_rows: int) -> tuple[list[dict[str, Any]], int | None]:
    """Livy's statement output as the notebook's outputs: text, a table for SQL, or the error."""
    if output.get("status") == "error":
        return [{"type": "error", "ename": str(output.get("ename") or "Error"),
                 "evalue": redact.secrets(str(output.get("evalue") or "")),
                 "traceback": [redact.secrets(str(t)) for t in output.get("traceback") or []]}], None
    data = output.get("data") or {}
    table = data.get("application/json")
    if isinstance(table, dict) and isinstance(table.get("data"), list):
        import pyarrow as pa

        from .local_sql import _table_output
        fields = [str(f.get("name")) for f in (table.get("schema") or {}).get("fields") or []]
        rows = table["data"]
        if any(not isinstance(r, list) or len(r) != len(fields) for r in rows):
            # Never NULLs Fabric didn't send, nor a value cut off (#1369).
            return [{"type": "error", "ename": "FabricSparkError",
                     "evalue": "Fabric sent a result that doesn't have one value per column."}], None
        shown = rows[:preview_rows]
        arrays = []
        for i in range(len(fields)):   # by position, so columns that share a name all stay (#1369)
            values = [r[i] for r in shown]
            try:
                arrays.append(pa.array(values) if values else pa.array([], pa.string()))
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                arrays.append(pa.array([None if v is None else str(v) for v in values], pa.string()))
        arrow = pa.Table.from_arrays(arrays, names=fields) if fields else pa.table({})
        return [_table_output(arrow, len(rows))], len(rows)
    text = data.get("text/plain")
    return ([{"type": "stream", "text": str(text)}] if text not in (None, "") else []), None


def _result_code(query: str, rows: int) -> str:
    """A PySpark statement that runs one Spark SQL query and prints its result as JSON: at most
    ``rows`` + 1 rows, so "more than asked for" is known without collecting everything.

    Timestamps are formatted by Spark in the session's time zone, so they read as the query shows
    them; collect() would turn them into the driver's local time. Columns are renamed c0, c1... first,
    so duplicate or odd names can't clash. The statement's own names are deleted at the end."""
    return ("import json as _kf_json\n"
            "from pyspark.sql import functions as _kf_F\n"
            f"_kf_df = spark.sql({query!r})\n"
            "_kf_fields = [[f.name, f.dataType.simpleString()] for f in _kf_df.schema.fields]\n"
            "_kf_df = _kf_df.toDF(*['c%d' % i for i in range(len(_kf_fields))]).select(["
            "_kf_F.date_format('c%d' % i, 'yyyy-MM-dd HH:mm:ss.SSSSSS').alias('c%d' % i) "
            "if t in ('timestamp', 'timestamp_ntz') else _kf_F.col('c%d' % i) for i, (n, t) in enumerate(_kf_fields)])\n"
            f"_kf_rows = _kf_df.limit({int(rows) + 1}).collect()\n"
            f"print(_kf_json.dumps({{'mark': {RESULT_MARK!r}, 'fields': _kf_fields, "
            "'rows': [[None if v is None else str(v) for v in r] for r in _kf_rows]}))\n"
            "del _kf_json, _kf_F, _kf_df, _kf_fields, _kf_rows\n")


def _arrow_type(spark_type: str) -> Any:
    """The Arrow type for a Spark type's simpleString; anything else (nested, binary...) is text."""
    import pyarrow as pa
    simple = {"tinyint": pa.int8(), "smallint": pa.int16(), "int": pa.int32(), "bigint": pa.int64(),
              "float": pa.float32(), "double": pa.float64(), "boolean": pa.bool_(), "string": pa.string(),
              "date": pa.date32(), "timestamp": pa.timestamp("us"), "timestamp_ntz": pa.timestamp("us")}
    if spark_type in simple:
        return simple[spark_type]
    m = re.fullmatch(r"decimal\((\d+),(\d+)\)", spark_type)
    if m:
        return pa.decimal128(int(m.group(1)), int(m.group(2)))
    return pa.string()


def _result_table(text: str, result_rows: int) -> tuple[pa.Table, list[str]]:
    """The table a result statement printed, and the columns kept as text (with their Spark type).
    Raises when it has more rows or bytes than allowed."""
    from .local_sql import _too_big, _too_many
    if len(text.encode("utf-8", "replace")) > RESULT_BYTES:
        raise _too_big()
    line = next((ln for ln in reversed(text.splitlines()) if RESULT_MARK in ln), "")
    try:
        printed = json.loads(line)
    except ValueError:
        raise FabricSparkError("Fabric Spark didn't send the result back in the expected form.") from None
    if not (isinstance(printed, dict) and printed.get("mark") == RESULT_MARK):
        raise FabricSparkError("Fabric Spark didn't send the result back in the expected form.")
    fields, rows = printed.get("fields") or [], printed.get("rows") or []
    if len(rows) > result_rows:
        raise _too_many(result_rows)
    try:
        return _typed(fields, rows)
    except (TypeError, ValueError, IndexError):   # a reply that carries the mark but not its shape
        raise FabricSparkError("Fabric Spark didn't send the result back in the expected form.") from None


def _typed(fields: list[Any], rows: list[Any]) -> tuple[pa.Table, list[str]]:
    import pyarrow as pa
    names = [str(name) for name, _ in fields]
    if any(len(r) != len(names) for r in rows):
        raise ValueError("a row doesn't have one value per column")
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:   # verify matches columns by name, so two of one name can't be compared honestly
        raise FabricSparkError(f"The result has more than one column named {', '.join(twice)}. "
                               "Give each column its own name (AS ...) to compare it.")
    columns, as_text = {}, []
    for i, (name, spark_type) in enumerate(fields):
        values = pa.array([r[i] for r in rows], pa.string())
        target = _arrow_type(str(spark_type))
        try:
            columns[str(name)] = values.cast(target)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            columns[str(name)], target = values, pa.string()   # a value that doesn't read as its type
        if target == pa.string() and spark_type != "string":
            as_text.append(f"{name} ({spark_type})")
    return (pa.table(columns) if columns else pa.table({})), as_text


class FabricSparkSession:
    engine = FABRIC_SPARK.id

    def __init__(self, config: SessionConfig, client: LivyClient, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, start_seconds: float = START_SECONDS,
                 max_seconds: float = MAX_SECONDS):
        self.notebook = config.notebook
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._max_seconds = max_seconds
        self._lock = threading.Lock()
        self._statement = ""
        self._cancelled = False
        self._closed = False
        client.stopped = lambda: self._cancelled
        conf = {k: str(v) for k, v in (config.options.get("conf") or {}).items()}
        try:
            created = client.create(f"KratosForge {config.notebook}"[:100], conf)
        except NotLivyReply:
            created = {}   # the request reached Fabric's side, so a session may exist: say so below
        self.id = str(created.get("id", ""))
        if not self.id:
            self._closed = True
            raise FabricSparkError("Fabric accepted the Spark session but didn't say which one it is, so it can't be "
                                   "used. Check the Monitoring hub, and stop it there if it is running.")
        try:
            started = clock()
            state = str(created.get("state") or "")
            while state != "idle":
                if state in _SESSION_DONE:
                    raise FabricSparkError(f"The Spark session couldn't start (it is {state}). The Monitoring hub "
                                           "has its log.")
                if clock() - started > start_seconds:
                    raise FabricSparkError(f"The Spark session didn't start within {start_seconds / 60:.0f} minutes, "
                                           "so it was ended.")
                sleep(POLL_SECONDS)
                state = str(client.session(self.id).get("state") or "")
        except BaseException:
            # Fabric created it, so it uses capacity until it is ended: whatever stopped the wait
            # (a refused poll, the time limit, Stop), end it now rather than at Fabric's idle timeout.
            self.close()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        if self._closed:
            raise SessionClosed("This Fabric Spark session was closed. Start the engine again to run cells.")
        kind = KINDS.get(request.language)
        if kind is None:
            raise Unsupported(f"Fabric Spark doesn't run {request.language} cells.")
        if request.language == "sql" and request.dialect not in (None, "spark"):
            raise Unsupported(f"Fabric Spark runs Spark SQL, not {request.dialect}.")
        whole = request.result_rows > 0
        if whole and request.language != "sql":
            raise Unsupported("Fabric Spark returns whole results for SQL cells only.")
        if not request.code.strip():
            raise Unsupported("There's no code in this cell, so nothing was sent to Fabric.")
        code = _result_code(request.code, request.result_rows) if whole else request.code
        kind = "pyspark" if whole else kind
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        self._cancelled = False
        started = self._clock()
        t0 = time.perf_counter()
        try:
            try:
                reply = self._client.submit(self.id, code, kind)
            except NotLivyReply as exc:
                raise FabricSparkError(f"{exc} {_UNTRACKED}") from None
            self._statement = str(reply.get("id", ""))
            if not _ID.fullmatch(self._statement):
                self._statement = ""
                raise FabricSparkError(f"Fabric took the cell but didn't say which statement it is. {_UNTRACKED}")
            if self._cancelled:  # Cancel was pressed while the cell was being sent
                try:
                    self._client.cancel(self.id, self._statement)
                except Exception:  # noqa: BLE001 - a refused cancel must not hide that the cell may still run
                    raise FabricSparkError(self._abandon(FabricSparkError("Cancelled."))) from None
            while reply.get("state") not in ("available", "error", "cancelled"):
                if self._clock() - started > self._max_seconds:
                    raise FabricSparkError(self._abandon(FabricSparkError(
                        f"The cell was still running after {self._max_seconds / 60:.0f} minutes.")))
                self._sleep(POLL_SECONDS)
                try:
                    reply = self._client.statement(self.id, self._statement)
                except Exception as exc:  # noqa: BLE001 - any failed poll (network, sign-in) stops the statement
                    raise FabricSparkError(self._abandon(exc)) from None
        except FabricSparkError as exc:
            out = {"type": "error", "ename": "FabricSparkError", "evalue": str(exc)}
            if on_output:
                on_output(out)
            return CellResult("cancelled" if self._cancelled else "error", [out], time.perf_counter() - t0, None,
                              self.engine)
        finally:
            self._statement = ""
            self._lock.release()
        # Only Fabric's own word counts: a statement that finished before the cancel keeps its output.
        table = None
        if reply.get("state") == "cancelled":
            outputs, rows, status = [{"type": "error", "ename": "Cancelled", "evalue": "Cancelled."}], None, "cancelled"
        else:
            outputs, rows = _outputs(reply.get("output") or {}, request.preview_rows)
            status = "error" if any(o.get("type") == "error" for o in outputs) else "ok"
            if whole and status == "ok":
                outputs, rows, table, status = self._whole(reply.get("output") or {}, request)
        if on_output:
            for o in outputs:
                on_output(o)
        return CellResult(status, outputs, time.perf_counter() - t0, rows, self.engine, table=table)

    def _whole(self, output: dict[str, Any], request: CellRequest) -> tuple[list[dict[str, Any]], int | None,
                                                                            pa.Table | None, str]:
        from .local_sql import ResultTooLarge, _table_output
        text = str((output.get("data") or {}).get("text/plain") or "")
        try:
            table, as_text = _result_table(text, request.result_rows)
        except (ResultTooLarge, FabricSparkError) as exc:
            return [{"type": "error", "ename": type(exc).__name__, "evalue": str(exc)}], None, None, "error"
        note = [{"type": "stream", "text": f"Kept as text: {', '.join(as_text)}.\n"}] if as_text else []
        return [*note, _table_output(table.slice(0, request.preview_rows), table.num_rows)], table.num_rows, table, "ok"

    def _abandon(self, exc: Exception) -> str:
        """The failed poll's (or time limit's, or failed Cancel's) message, once the statement has been asked
        to stop: a statement this PC
        lost track of must not keep the session busy (and using capacity) with no one watching it."""
        problem = str(exc) if isinstance(exc, FabricSparkError) else f"Couldn't check the cell: {type(exc).__name__}."
        try:
            self._client.cancel(self.id, self._statement)
        except Exception:  # noqa: BLE001 - the first failure is the one to report
            return (f"{problem} The cell may still be running in the Fabric Spark session: stop it from "
                    "the Monitor hub in Fabric, or close the session here.")
        return f"{problem} The cell was stopped in Fabric."

    def cancel(self) -> bool:
        if not self._lock.locked():
            return False
        if self._statement:
            try:
                self._client.cancel(self.id, self._statement)
            except FabricSparkError:
                return False
        # Before the statement exists, execute cancels it as soon as submit says which one it is.
        self._cancelled = True
        return True

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(FABRIC_SPARK.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        raise Unsupported(FABRIC_SPARK.capability("plan").note)

    def catalog(self) -> list[dict[str, Any]]:
        raise Unsupported(FABRIC_SPARK.capability("catalog").note)

    def close(self) -> None:
        if self._closed:
            return
        self.cancel()
        self._closed = True
        if self.id:
            try:
                self._client.delete(self.id)
            except FabricSparkError:
                pass  # silent-ok: Fabric ends an idle session on its own; this session is closed either way


class FabricSparkAdapter:
    """``SessionConfig.options``: ``workspace_id`` and ``lakehouse_id`` (GUIDs), optional ``conf``
    (Spark settings, as %%configure takes). ``token`` is the app's Fabric sign-in; ``client_for``
    replaces the Livy client in tests."""

    def __init__(self, token: Callable[[], str] | None = None,
                 client_for: Callable[[str, str], LivyClient] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token
        self._client_for = client_for
        self._sleep = sleep

    def describe(self) -> EngineDescriptor:
        return FABRIC_SPARK

    def open_session(self, config: SessionConfig, authorization: Any = None) -> FabricSparkSession:
        ws = str(config.options.get("workspace_id") or "")
        lh = str(config.options.get("lakehouse_id") or "")
        if not (_GUID.fullmatch(ws) and _GUID.fullmatch(lh)):
            raise Unsupported("Pick the workspace and lakehouse for the Spark session first.")
        require(FABRIC_SPARK, f"{ws}/{lh}", authorization)
        from ..fabric_api import _check_fabric_runs_allowed
        _check_fabric_runs_allowed("interactive Spark sessions")
        if self._client_for is not None:
            client = self._client_for(ws, lh)
        elif self._token is not None:
            client = LivyClient(self._token, ws, lh, sleep=self._sleep)
        else:
            raise Unsupported("Fabric Spark sessions use the app's Fabric sign-in. Sign in first.")
        return FabricSparkSession(config, client, self._sleep)
