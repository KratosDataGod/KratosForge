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
- Each session is a process of its own (runtime_worker.py) in that folder. The cell's Python runs
  on this PC; ``spark`` work runs on the cluster. Closing the session ends the process; the cluster
  keeps running until its own auto-termination.

Nothing opens without a ``RemoteAuthorization`` for this engine and cluster: connecting to a
terminated cluster starts it, and a running cluster bills Databricks units (DBU). Serverless compute
isn't offered yet.

Sign-in goes to the process as DATABRICKS_* variables for the Databricks SDK inside
databricks-connect: the CLI profile's name, or the host with the CLI's own sign-in, or a service
principal's ID and the secret read from its reference. No token is passed or written by KratosForge,
and any DATABRICKS_* variable already set on this PC is left out so it can't pick another workspace.

The install, the worker process and its environment rules are shared with any engine that needs a
runtime of its own (runtimes.py); this module says what is Databricks' about them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import pyarrow as pa

from . import runtimes
from .authorization import require
from .capabilities import EngineDescriptor, Support
from .contract import CellRequest, SessionConfig, Unsupported
from .databricks_auth import DatabricksAuthError, check_host, find_cli, resolve_secret

_T = "unit_tests"
KIND = "databricks-connect"      # the worker's start for this engine (runtime_worker.STARTS)
INSTALL_SECONDS = runtimes.INSTALL_SECONDS
START_SECONDS = 15 * 60          # connecting can wait for a terminated cluster to start
CELL_SECONDS = runtimes.CELL_SECONDS
STOP_SECONDS = runtimes.STOP_SECONDS
WORKER = runtimes.WORKER
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
    elif line not in supported_lines():
        # No pinned lock file, so nothing to install: saying "install it" would be a dead end.
        return [f"Databricks Connect for Databricks Runtime {line} isn't supported in this version of "
                f"KratosForge. Supported: {', '.join(supported_lines())}."]
    if not client:
        problems.append(f"databricks-connect {line} isn't installed yet. Install it for this runtime first.")
    elif ".".join(client.split(".")[:2]) != line:
        problems.append(f"The installed databricks-connect is {client}; this cluster needs {line}.x.")
    return problems


LOCKS = Path(__file__).with_name("dbconnect_locks")
_PIP_ENV = runtimes.PIP_ENV


def supported_lines() -> list[str]:
    """Runtime lines this version can install: each has a hash-pinned requirements file."""
    return runtimes.lock_lines(LOCKS)


class Runtimes(runtimes.LockedRuntimes):
    """databricks-connect, one folder per runtime line, under ``<data>/runtimes``: installed only
    from ``dbconnect_locks/<line>.txt`` (runtimes.LockedRuntimes). IT can point installs at an
    internal mirror (``dbconnect_index_url``; the hashes still apply) or turn them off
    (``allow_dbconnect_install``)."""

    def __init__(self, root: Path, python: str | None = None, run: Callable[..., Any] = subprocess.run):
        super().__init__(root, name="dbconnect", locks=LOCKS, distribution="databricks-connect",
                         label="Databricks Connect", line_label="Databricks Runtime", allow_key="allow_dbconnect_install",
                         index_key="dbconnect_index_url", python=python, run=run)

    @property
    def locks(self) -> Path:
        return LOCKS


def _uses_cli(cred: dict[str, Any], cfg_path: Path | None = None) -> bool:
    """Whether this sign-in gets its token from the Databricks CLI (so the CLI must be found first)."""
    method = cred.get("method") or "oauth-u2m"
    if method == "oauth-u2m":
        return True
    if method != "cli-profile":
        return False
    from .databricks_auth import _cfg_section
    section = _cfg_section(str(cred.get("profile") or ""), cfg_path)
    return not (section.get("token") or (section.get("client_id") and section.get("client_secret")))


def _check_profile_host(profile: str, host: str, cfg_path: Path | None) -> None:
    """The SDK takes DATABRICKS_HOST over the profile's own host, so a profile for another workspace
    would send that workspace's sign-in here: refused, as provider_for refuses it."""
    from .databricks_auth import _cfg_section
    own = _cfg_section(profile, cfg_path).get("host")
    if own and check_host(own) != host:
        raise DatabricksAuthError(f"The CLI profile {profile!r} is for another workspace than this connection.")


def worker_env(connection: dict[str, Any], cluster_id: str, base: Mapping[str, str] | None = None,
               environ: Mapping[str, str] | None = None, cfg_path: Path | None = None) -> dict[str, str]:
    """The process's environment: this PC's, minus any DATABRICKS_* or PYTHON* variable, plus the
    connection's sign-in and the cluster. A service principal's secret is read from its reference here."""
    # SPARK_REMOTE and SPARK_CONNECT_* too: databricks-connect reads them before the SDK's settings.
    env = runtimes.scrub_env(base, ("DATABRICKS_", "SPARK_REMOTE", "SPARK_CONNECT_"))
    host = check_host(connection.get("host") or "")
    cred = connection.get("credential") or {}
    method = cred.get("method") or "oauth-u2m"
    env |= {"DATABRICKS_HOST": host, "DATABRICKS_CLUSTER_ID": cluster_id}
    if method == "cli-profile":
        if not cred.get("profile"):
            raise DatabricksAuthError("Name the Databricks CLI profile to use.")
        _check_profile_host(str(cred["profile"]), host, cfg_path)
    # The SDK finds the CLI itself, current folder included; give it the one find_cli trusts.
    cli = find_cli(base)
    if cli:
        env["DATABRICKS_CLI_PATH"] = cli
    elif method in ("oauth-u2m", "cli-profile") and _uses_cli(cred, cfg_path):
        # Without it the SDK would look for the CLI itself, current folder included.
        raise DatabricksAuthError("This sign-in uses the Databricks CLI, which isn't installed or isn't on PATH. "
                                  "Install it from https://docs.databricks.com/dev-tools/cli/install.html.")
    if method == "cli-profile":
        env["DATABRICKS_CONFIG_PROFILE"] = str(cred["profile"])
    elif method == "oauth-u2m":
        env["DATABRICKS_AUTH_TYPE"] = "databricks-cli"
    elif method == "oauth-m2m":
        if not cred.get("client_id") or not cred.get("secret"):
            raise DatabricksAuthError("OAuth machine-to-machine needs client_id and a secret reference.")
        env |= {"DATABRICKS_AUTH_TYPE": "oauth-m2m", "DATABRICKS_CLIENT_ID": str(cred["client_id"]),
                "DATABRICKS_CLIENT_SECRET": resolve_secret(str(cred["secret"]), environ)}
        env = runtimes.drop_ref_variable(env, str(cred["secret"]))
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


class DatabricksConnectSession(runtimes.WorkerSession):
    engine = DATABRICKS_CONNECT.id
    label = "Databricks Connect"

    def __init__(self, config: SessionConfig, command: list[str], env: dict[str, str],
                 start_seconds: float = START_SECONDS, cwd: str | None = None, cell_seconds: float = CELL_SECONDS):
        super().__init__(config, command, env, start_seconds, cwd, cell_seconds)

    def stop_seconds(self) -> float:
        return STOP_SECONDS

    def check_language(self, request: CellRequest) -> None:
        if request.language not in ("python", "pyspark"):
            raise Unsupported("Databricks Connect runs Python and PySpark cells. Use spark.sql() for SQL here, "
                              "or the Databricks SQL warehouse engine.")

    def output(self, out: dict[str, Any]) -> dict[str, Any]:
        return _frame_output(out) if out.get("type") == "frame" else out

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(DATABRICKS_CONNECT.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        raise Unsupported(DATABRICKS_CONNECT.capability("plan").note)

    def catalog(self) -> list[dict[str, Any]]:
        raise Unsupported(DATABRICKS_CONNECT.capability("catalog").note)


class DatabricksConnectAdapter:
    """``SessionConfig.options``: ``connection`` (a Databricks connection from a profile, references
    only) and ``cluster_id`` (or the connection's compute.cluster_id). ``cluster_of`` reads the cluster
    (spark_version, state) and ``runtimes`` finds the installed client; tests replace both."""

    def __init__(self, runtimes: Runtimes | None = None,
                 cluster_of: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                 python: str | None = None, cell_seconds: float = CELL_SECONDS):
        if runtimes is None:
            from ..service import data_dir
            runtimes = Runtimes(data_dir())
        self.runtimes = runtimes
        self._cluster_of = cluster_of or _read_cluster
        self.cell_seconds = cell_seconds
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
        command = runtimes.worker_command(self._python, folder, KIND)
        # Run in the runtime's folder, not wherever the app was started from.
        return DatabricksConnectSession(config, command, worker_env(conn, cluster), cwd=str(folder),
                                        cell_seconds=self.cell_seconds)


def _read_cluster(connection: dict[str, Any], cluster_id: str) -> dict[str, Any]:
    """The cluster's spark_version and state (Clusters API; reading never starts it)."""
    from .databricks_auth import provider_for
    from .databricks_sql import Client
    return Client(provider_for(connection)).cluster(cluster_id)
