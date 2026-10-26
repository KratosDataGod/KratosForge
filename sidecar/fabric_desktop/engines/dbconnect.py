"""Databricks Connect: a notebook's PySpark on a Databricks cluster (Preview; tested against fakes).

databricks-connect brings its own pyspark, which would fight the engine's local Spark stand-in, so
it never loads in the engine (ADR-004). Instead:

- ``Runtimes`` installs databricks-connect for one Databricks Runtime line into its own folder,
  ``<data>/runtimes/dbconnect-<x.y>/site-packages``, with ``pip install --target``. Installing is an
  explicit step the person starts; opening a session never installs anything.
- ``check`` compares the cluster's runtime, the client in that folder and this PC's Python before
  anything starts: databricks-connect's major.minor must equal the runtime's, and the runtime's
  Python must be the engine's (3.12), which Databricks requires for UDFs and pandas UDFs. So
  Databricks Runtime 16 and later are supported here; 13.3 to 15.4 need another Python.
- Each session is a process of its own (dbconnect_worker.py) in that folder. The cell's Python runs
  on this PC; ``spark`` work runs on the cluster. Closing the session ends the process; the cluster
  keeps running until its own auto-termination.

Nothing opens without a ``RemoteAuthorization`` for this engine and cluster: connecting to a
terminated cluster starts it, and a running cluster bills Databricks units (DBU). Serverless compute
isn't offered yet.

Sign-in goes to the process as DATABRICKS_* variables for the Databricks SDK inside
databricks-connect: the CLI profile's name, or the host with the CLI's own sign-in, or a service
principal's ID and the secret read from its reference. No token is passed or written by KratosForge,
and any DATABRICKS_* variable already set on this PC is left out so it can't pick another workspace.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import pyarrow as pa

from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported
from .databricks_auth import DatabricksAuthError, check_host, find_cli, resolve_secret

_T = "unit_tests"
INSTALL_SECONDS = 20 * 60
START_SECONDS = 15 * 60          # connecting can wait for a terminated cluster to start
WORKER = Path(__file__).with_name("dbconnect_worker.py")
# The Python each Databricks Runtime line runs (Databricks' release notes); a client must use the same.
RUNTIME_PYTHON = {13: "3.10", 14: "3.10", 15: "3.11", 16: "3.12", 17: "3.12", 18: "3.12", 19: "3.12"}
_RUNTIME = re.compile(r"^(\d+)\.(\d+)\.")
_CLUSTER = re.compile(r"^[A-Za-z0-9-]{1,64}$")

DATABRICKS_CONNECT = EngineDescriptor(
    id="databricks-connect", label="Databricks Connect (remote)", location="remote", platform="databricks",
    billing="databricks_dbu", adapter=True, family="databricks-connect", preview=True,
    languages={
        "pyspark": Support("native", "spark is a Databricks session: DataFrame and spark.sql work runs on the "
                           "cluster's Spark.", _T),
        "python": Support("partial", "Plain Python runs on this PC, in Databricks Connect's own environment; only "
                          "spark work goes to the cluster.", _T),
    },
    capabilities={
        "session": Support("native", "A process per session on this PC, connected to one cluster; closing it ends "
                           "the process. The cluster keeps running until its auto-termination.", _T),
        "execute": Support("native", evidence=_T),
        "cancel": Support("partial", "Stops the session's running Spark jobs on the cluster (interruptAll); "
                          "plain Python on this PC runs to the end.", _T),
        "stream": Support("unsupported", "Output arrives when the cell finishes."),
        "variables": Support("unsupported", "Not listed yet for a Databricks Connect session."),
        "debug": Support("unsupported", "No step debugger for a remote session."),
        "plan": Support("unsupported", "Call df.explain() in a cell; it prints the cluster's plan."),
        "metrics": Support("partial", "Wall time per cell. The Spark UI has the jobs' details.", _T),
        "catalog": Support("unsupported", "Use spark.catalog in a cell, or the Databricks SQL warehouse engine."),
        "cost": Support("unsupported", "Billed DBUs are in Databricks' system billing tables, per cluster and hour. "
                        "A running cluster bills DBUs until it auto-terminates."),
        "compat": Support("native", "The cluster's runtime, the installed client and this PC's Python are checked "
                          "before anything starts.", _T),
    },
    requirements=("An Azure Databricks workspace with Unity Catalog",
                  "A cluster on Databricks Runtime 16 or later, and CAN ATTACH TO on it",
                  "databricks-connect for that runtime, installed by KratosForge into its own folder",
                  "A sign-in: the Databricks CLI (databricks auth login) or a service principal's secret reference"),
    limitations=("Preview: tested against fakes, not yet against a live workspace.",
                 "Connecting to a terminated cluster starts it; a running cluster bills DBUs.",
                 "Spark Connect: no RDDs, SparkContext or sc; DataFrame and SQL APIs work.",
                 "Results show the first rows only; counting them all would run another Spark job.",
                 "Classic clusters only; serverless compute is planned.",
                 "Runtimes 13.3 to 15.4 need Python 3.10 or 3.11, which this app doesn't ship."),
)


def runtime_line(spark_version: str) -> tuple[int, int]:
    """(16, 4) from a cluster's spark_version, such as 16.4.x-scala2.12 or 16.4.x-cpu-ml-scala2.12."""
    m = _RUNTIME.match(spark_version or "")
    if not m:
        raise Unsupported(f"{spark_version!r} isn't a Databricks Runtime version KratosForge can read.")
    return int(m.group(1)), int(m.group(2))


def check(spark_version: str, client: str | None, python: str = "%d.%d" % sys.version_info[:2]) -> list[str]:
    """Why a session can't start, as things a person can act on; empty when it can."""
    major, minor = runtime_line(spark_version)
    line = f"{major}.{minor}"
    problems = []
    needs = RUNTIME_PYTHON.get(major)
    if needs is None and major > max(RUNTIME_PYTHON):
        problems.append(f"Databricks Runtime {line} is newer than this version of KratosForge knows. Update "
                        "KratosForge, or use a cluster on an earlier runtime.")
    elif needs is None:
        problems.append(f"Databricks Runtime {line} isn't supported by Databricks Connect here (13.3 and later).")
    elif needs != python:
        problems.append(f"Databricks Runtime {line} runs Python {needs}; KratosForge's Python is {python}. Use a "
                        "cluster on Databricks Runtime 16 or later.")
    if not client:
        problems.append(f"databricks-connect {line} isn't installed yet. Install it for this runtime first.")
    elif ".".join(client.split(".")[:2]) != line:
        problems.append(f"The installed databricks-connect is {client}; this cluster needs {line}.x.")
    return problems


class Runtimes:
    """databricks-connect, one folder per runtime line, under ``<data>/runtimes``."""

    def __init__(self, root: Path, python: str | None = None, run: Callable[..., Any] = subprocess.run):
        self.root = Path(root) / "runtimes"
        self.python = python or sys.executable
        self._run = run

    def folder(self, line: str) -> Path:
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}", line or ""):
            raise ValueError(f"{line!r} isn't a runtime line like 16.4.")
        return self.root / f"dbconnect-{line}" / "site-packages"

    def installed(self, line: str) -> str | None:
        folder = self.folder(line)
        if not folder.is_dir():
            return None
        for dist in importlib.metadata.distributions(path=[str(folder)]):
            if (dist.metadata.get("Name") or "").lower() == "databricks-connect":
                return dist.version
        return None

    def install_command(self, line: str) -> list[str]:
        return [self.python, "-m", "pip", "install", "--target", str(self.folder(line)), "--upgrade", "--no-input",
                "--disable-pip-version-check", f"databricks-connect=={line}.*"]

    def install(self, line: str) -> dict[str, Any]:
        """Download databricks-connect for ``line`` from PyPI (or the PC's pip index). Takes minutes."""
        self.folder(line).mkdir(parents=True, exist_ok=True)
        try:
            done = self._run(self.install_command(line), capture_output=True, text=True, timeout=INSTALL_SECONDS,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "log": f"pip didn't finish within {INSTALL_SECONDS // 60} minutes, so it was stopped."}
        return {"ok": done.returncode == 0, "version": self.installed(line),
                "log": ((done.stdout or "") + (done.stderr or ""))[-20000:]}


def worker_env(connection: dict[str, Any], cluster_id: str, base: Mapping[str, str] | None = None,
               environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The process's environment: this PC's, minus any DATABRICKS_* or PYTHON* variable, plus the
    connection's sign-in and the cluster. A service principal's secret is read from its reference here."""
    # SPARK_REMOTE too: databricks-connect connects to it before reading the SDK's settings.
    env = {k: v for k, v in (os.environ if base is None else base).items()
           if not k.upper().startswith(("DATABRICKS_", "PYTHON", "SPARK_REMOTE"))}
    host = check_host(connection.get("host") or "")
    cred = connection.get("credential") or {}
    method = cred.get("method") or "oauth-u2m"
    env |= {"DATABRICKS_HOST": host, "DATABRICKS_CLUSTER_ID": cluster_id}
    # The SDK finds the CLI itself, current folder included; give it the one find_cli trusts.
    cli = find_cli(base)
    if cli:
        env["DATABRICKS_CLI_PATH"] = cli
    if method == "cli-profile":
        if not cred.get("profile"):
            raise DatabricksAuthError("Name the Databricks CLI profile to use.")
        env["DATABRICKS_CONFIG_PROFILE"] = str(cred["profile"])
    elif method == "oauth-u2m":
        env["DATABRICKS_AUTH_TYPE"] = "databricks-cli"
    elif method == "oauth-m2m":
        if not cred.get("client_id") or not cred.get("secret"):
            raise DatabricksAuthError("OAuth machine-to-machine needs client_id and a secret reference.")
        env |= {"DATABRICKS_AUTH_TYPE": "oauth-m2m", "DATABRICKS_CLIENT_ID": str(cred["client_id"]),
                "DATABRICKS_CLIENT_SECRET": resolve_secret(str(cred["secret"]), environ)}
    else:
        raise DatabricksAuthError(f"Unknown Databricks sign-in method {method!r}.")
    return env


def _frame_output(out: dict[str, Any]) -> dict[str, Any]:
    from .local_sql import _table_output
    columns, rows = out["columns"], out["rows"]
    try:
        table = pa.Table.from_pylist([dict(zip(columns, r)) for r in rows]) if rows else \
            pa.table({c: pa.array([], pa.string()) for c in columns})
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        table = pa.table({c: pa.array([None if r[i] is None else str(r[i]) for r in rows], pa.string())
                          for i, c in enumerate(columns)})
    shown = _table_output(table, len(rows))
    shown["total_rows"] = None      # unknown: counting would run another job on the cluster
    shown["preview_only"] = True
    return shown


class DatabricksConnectSession:
    engine = DATABRICKS_CONNECT.id

    def __init__(self, config: SessionConfig, command: list[str], env: dict[str, str],
                 start_seconds: float = START_SECONDS, cwd: str | None = None):
        self.notebook = config.notebook
        self._closed = False
        self._lock = threading.Lock()
        self._running = False
        self._lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._n = 0
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", env=env, cwd=cwd,
                                     start_new_session=sys.platform != "win32",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, daemon=True, name=f"dbconnect-{config.notebook}").start()
        self._send({"op": "start"})
        reply = self._wait(lambda m: m.get("op") in ("started", "error"), start_seconds)
        if reply.get("op") != "started":
            self.close()
            raise RuntimeError(f"Databricks Connect couldn't start: {reply.get('evalue') or 'the process ended'}")
        self.client = reply.get("client", "")

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with contextlib.suppress(ValueError):  # silent-ok: a stray line from a library isn't a message
                self._lines.put(json.loads(line))
        self._lines.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise SessionClosed("The Databricks Connect process has ended. Start the engine again.") from None

    def _wait(self, wanted: Callable[[dict[str, Any]], bool], seconds: float | None) -> dict[str, Any]:
        deadline = None if seconds is None else time.monotonic() + seconds
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if left == 0.0:
                return {"op": "error", "evalue": f"no answer within {seconds / 60:.0f} minutes"}
            try:
                msg = self._lines.get(timeout=left)
            except queue.Empty:
                continue
            if msg is None:
                return {"op": "error", "evalue": "the process ended"}
            if wanted(msg):
                return msg

    @property
    def closed(self) -> bool:
        return self._closed

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        if self._closed:
            raise SessionClosed("This Databricks Connect session was closed. Start the engine again to run cells.")
        if request.language not in ("python", "pyspark"):
            raise Unsupported("Databricks Connect runs Python and PySpark cells. Use spark.sql() for SQL here, "
                              "or the Databricks SQL warehouse engine.")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A cell is already running in this session. Wait for it, or Cancel it.")
        started = time.perf_counter()
        try:
            self._running = True
            self._n += 1
            cid = self._n
            self._send({"op": "exec", "id": cid, "code": request.code, "preview_rows": request.preview_rows})
            reply = self._wait(lambda m: m.get("op") in ("result", "error") and m.get("id", cid) == cid, None)
        finally:
            self._running = False
            self._lock.release()
        if reply.get("op") != "result":
            self._closed = True
            raise SessionClosed("The Databricks Connect process ended while the cell ran. Start the engine again.")
        raw = reply.get("outputs") or []
        outputs = [_frame_output(o) if o.get("type") == "frame" else o for o in raw]
        if on_output:
            for o in outputs:
                on_output(o)
        rows = next((len(o["rows"]) for o in reversed(raw) if o.get("type") == "frame"), None)
        return CellResult(reply.get("status", "error"), outputs, time.perf_counter() - started, rows, self.engine)

    def cancel(self) -> bool:
        if self._closed or not self._running:
            return False
        self._send({"op": "interrupt"})
        return True

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(DATABRICKS_CONNECT.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        raise Unsupported(DATABRICKS_CONNECT.capability("plan").note)

    def catalog(self) -> list[dict[str, Any]]:
        raise Unsupported(DATABRICKS_CONNECT.capability("catalog").note)

    def close(self) -> None:
        if self._closed and self.proc.poll() is not None:
            return
        self._closed = True
        with contextlib.suppress(SessionClosed):  # silent-ok: the process has already gone
            self._send({"op": "quit"})
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            from ..shell import end
            end(self.proc)


class DatabricksConnectAdapter:
    """``SessionConfig.options``: ``connection`` (a Databricks connection from a profile, references
    only) and ``cluster_id`` (or the connection's compute.cluster_id). ``cluster_of`` reads the cluster
    (spark_version, state) and ``runtimes`` finds the installed client; tests replace both."""

    def __init__(self, runtimes: Runtimes | None = None,
                 cluster_of: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                 python: str | None = None):
        if runtimes is None:
            from ..service import data_dir
            runtimes = Runtimes(data_dir())
        self.runtimes = runtimes
        self._cluster_of = cluster_of or _read_cluster
        self._python = python or sys.executable

    def describe(self) -> EngineDescriptor:
        return DATABRICKS_CONNECT

    def open_session(self, config: SessionConfig, authorization: Any = None) -> DatabricksConnectSession:
        conn = dict(config.options.get("connection") or {})
        if conn.get("compute", {}).get("serverless") or config.options.get("serverless"):
            raise Unsupported("Serverless compute isn't offered for Databricks Connect yet. Pick a cluster.")
        cluster = str(config.options.get("cluster_id") or (conn.get("compute") or {}).get("cluster_id") or "")
        if not _CLUSTER.fullmatch(cluster):
            raise Unsupported("Pick a cluster for this connection first.")
        require(DATABRICKS_CONNECT, cluster, authorization)
        info = self._cluster_of(conn, cluster)
        line = "%d.%d" % runtime_line(str(info.get("spark_version") or ""))
        problems = check(str(info.get("spark_version") or ""), self.runtimes.installed(line))
        if problems:
            raise Unsupported(" ".join(problems))
        folder = self.runtimes.folder(line)
        command = [self._python, "-I", str(WORKER), str(folder)]
        # Run in the runtime's folder, not wherever the app was started from.
        return DatabricksConnectSession(config, command, worker_env(conn, cluster), cwd=str(folder))


def _read_cluster(connection: dict[str, Any], cluster_id: str) -> dict[str, Any]:
    """The cluster's spark_version and state (Clusters API; reading never starts it)."""
    from .databricks_auth import provider_for
    from .databricks_sql import Client
    return Client(provider_for(connection)).cluster(cluster_id)
