"""Snowpark (this PC, simulated): Snowpark Python cells in Snowflake's local testing framework (Preview;
SF-007 option A). Tested against a fake of Snowpark in CI, never compared with Snowflake.

Snowpark brings snowflake-connector-python and a large tree of its own (cryptography, boto3, pandas...),
so it never loads in the engine (ADR-004). As for Databricks Connect (runtimes.py):

- ``Runtimes`` installs ``snowflake-snowpark-python[localtest]`` 1.58 into its own folder,
  ``<data>/runtimes/snowpark-1.58/site-packages``, only from the hash-pinned ``snowpark_locks/1.58.txt``.
  Installing is a step the person starts; opening a session never installs anything. IT can point
  installs at its own mirror (``snowpark_index_url``; the hashes still apply) or turn them off
  (``allow_snowpark_install``).
- Each session is a process of its own (runtime_worker.py, kind ``snowpark-local``). Before Snowpark is
  imported the worker refuses every network connection and name lookup from that process, so no call
  can reach Snowflake. It then opens ``Session.builder.configs({"local_testing": True,
  "disable_local_testing_telemetry": True}).create()``, always with telemetry off, and doesn't start if
  Snowpark's telemetry service is still on. Snowflake's own variables (SNOWFLAKE_*, SNOWSQL_*,
  SNOWPARK_*) and proxy settings never reach it.
- ``session.sql`` is refused: the local testing framework can't run SQL, and whether KratosForge
  translates it is an open decision. A call the framework doesn't implement (``NotImplementedError``)
  comes back as a refusal that says so, never as a failure of the cell's own code.

Everything a cell does is simulated by Snowflake's local testing framework: tables live in the session's
memory, nothing is billed, and no result has been compared with Snowflake.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import runtimes
from .capabilities import EngineDescriptor, Support
from .contract import CellRequest, SessionConfig, Unsupported

_T = "unit_tests"
KIND = "snowpark-local"          # the worker's start for this engine (runtime_worker.STARTS)
LINE = "1.58"                    # the Snowpark Python line this version pins (snowpark_locks/1.58.txt)
START_SECONDS = 3 * 60           # importing Snowpark and pandas on a cold PC
CELL_SECONDS = runtimes.CELL_SECONDS
LOCKS = Path(__file__).with_name("snowpark_locks")
SIMULATED = "simulated by Snowflake's local testing framework"
LABEL = "Snowpark (this PC, simulated)"
# Variables the worker never gets: an account's settings or secrets for the connector, Snowpark's own
# switches (SNOWPARK_LOCAL_TESTING_INTERNAL_TELEMETRY), and proxies that would give it a route out.
SCRUBBED = ("SNOWFLAKE_", "SNOWSQL_", "SNOWPARK_", "SF_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
SQL_REFUSED = ("session.sql is refused here: Snowflake's local testing framework can't run SQL, and whether "
               "KratosForge translates it for this engine is still open. Use DataFrame calls, or a Snowflake SQL "
               "cell on DuckDB.")

SNOWPARK_LOCAL = EngineDescriptor(
    id="snowpark-local", label=LABEL, location="local", platform="snowflake", billing="none", adapter=True,
    family="snowpark", preview=True,
    languages={
        "python": Support("simulated", f"Snowpark Python, {SIMULATED}: session is a local testing session with "
                          "no Snowflake account. Only some functions are implemented, and session.sql is refused.", _T),
    },
    capabilities={
        "session": Support("native", "A process per session on this PC, in Snowpark's own runtime folder, with "
                           "no network; closing it ends the process and the session's in-memory tables.", _T),
        "execute": Support("simulated", f"Each cell is {SIMULATED}. Not Snowflake.", _T),
        "cancel": Support("partial", "Cancel ends the session's process, because the local testing framework "
                          "can't stop a call; the session's tables and variables are lost.", _T),
        "stream": Support("unsupported", "Output arrives when the cell finishes."),
        "variables": Support("unsupported", "Not listed yet for a Snowpark session."),
        "debug": Support("unsupported", "No step debugger for a Snowpark session."),
        "plan": Support("unsupported", "No query plan: nothing goes to Snowflake."),
        "metrics": Support("partial", "Wall time per cell.", _T),
        "catalog": Support("unsupported", "Tables live in the session's memory; use session.table in a cell."),
        "cost": Support("unsupported", "Nothing is billed: no Snowflake account is used."),
        "compat": Support("unsupported", "Use the Snowpark API compatibility check (snowpark_api_check) first."),
    },
    requirements=(f"Snowpark Python {LINE} with local testing, installed by KratosForge into its own folder",),
    limitations=(f"Preview: {SIMULATED}, never compared with Snowflake.",
                 "session.sql is refused; so is any call the local testing framework doesn't implement.",
                 "Tables last as long as the session; nothing is read from or written to a Snowflake account.",
                 "The session's process can't open network connections, to Snowflake or anywhere else.",
                 "to_pandas needs pyarrow, which the pinned runtime doesn't include; results show as tables."),
)


def supported_lines() -> list[str]:
    return runtimes.lock_lines(LOCKS)


class Runtimes(runtimes.LockedRuntimes):
    """snowflake-snowpark-python[localtest], one folder per pinned line, under ``<data>/runtimes``: installed
    only from ``snowpark_locks/<line>.txt`` (runtimes.LockedRuntimes)."""

    def __init__(self, root: Path, python: str | None = None, run: Callable[..., Any] = subprocess.run):
        super().__init__(root, name="snowpark", locks=LOCKS, distribution="snowflake-snowpark-python",
                         label="Snowpark Python with local testing", line_label="Snowpark Python",
                         allow_key="allow_snowpark_install", index_key="snowpark_index_url", python=python, run=run)

    @property
    def locks(self) -> Path:
        return LOCKS


def worker_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """This PC's environment without Snowflake's, Snowpark's, proxy or PYTHON* variables."""
    return runtimes.scrub_env(base, SCRUBBED)


def refusal(out: dict[str, Any]) -> dict[str, Any]:
    """A NotImplementedError from the local testing framework as a refusal saying so."""
    if out.get("type") != "error" or out.get("ename") != "NotImplementedError":
        return out
    said = str(out.get("evalue") or "").strip()
    if said.startswith("[KratosForge]"):
        text = said.removeprefix("[KratosForge]").strip()
    else:
        text = ("Refused: Snowflake's local testing framework doesn't implement this call, so it can't be "
                f"simulated on this PC. It may still work on Snowflake; nothing was sent there. ({said or 'no detail'})")
    return {**out, "ename": "Refused", "evalue": text, "refused": True, "traceback": [text]}


def _frame_output(out: dict[str, Any]) -> dict[str, Any]:
    from .dbconnect import _frame_output as shown
    table = shown(out)
    table["note"] = f"Preview rows, {SIMULATED}."
    return table


class SnowparkLocalSession(runtimes.WorkerSession):
    engine = SNOWPARK_LOCAL.id
    label = LABEL

    def __init__(self, config: SessionConfig, command: list[str], env: dict[str, str],
                 start_seconds: float = START_SECONDS, cwd: str | None = None, cell_seconds: float = CELL_SECONDS):
        super().__init__(config, command, env, start_seconds, cwd, cell_seconds)

    def check_language(self, request: CellRequest) -> None:
        if request.language != "python":
            raise Unsupported(f"{LABEL} takes Python cells that use Snowpark. Snowflake SQL runs translated on the "
                              "DuckDB engine instead.")

    def output(self, out: dict[str, Any]) -> dict[str, Any]:
        return _frame_output(out) if out.get("type") == "frame" else refusal(out)

    def cancel(self) -> bool:
        """Ends the process: the local testing framework has nothing that stops a running call."""
        if self._closed or not self._running:
            return False
        self.close()
        return True

    def variables(self) -> list[dict[str, Any]]:
        raise Unsupported(SNOWPARK_LOCAL.capability("variables").note)

    def plan(self, code: str, dialect: str | None = None) -> str:
        raise Unsupported(SNOWPARK_LOCAL.capability("plan").note)

    def catalog(self) -> list[dict[str, Any]]:
        raise Unsupported(SNOWPARK_LOCAL.capability("catalog").note)


class SnowparkLocalAdapter:
    """No options and no authorization: nothing leaves this PC and nothing is billed. ``runtimes`` finds
    the installed Snowpark; tests give a folder holding a fake of it."""

    def __init__(self, runtimes: Runtimes | None = None, python: str | None = None,
                 cell_seconds: float = CELL_SECONDS, line: str = LINE):
        if runtimes is None:
            from ..service import data_dir
            runtimes = Runtimes(data_dir())
        self.runtimes = runtimes
        self.cell_seconds = cell_seconds
        self.line = line
        self._python = python or sys.executable

    def describe(self) -> EngineDescriptor:
        return SNOWPARK_LOCAL

    def open_session(self, config: SessionConfig, authorization: Any = None) -> SnowparkLocalSession:
        installed = self.runtimes.installed(self.line)
        if not installed:
            raise Unsupported(f"Snowpark Python {self.line} with local testing isn't installed yet. Install it for "
                              "this engine first; KratosForge puts it in a folder of its own.")
        if ".".join(installed.split(".")[:2]) != self.line:
            raise Unsupported(f"The installed Snowpark Python is {installed}; this version of KratosForge pins "
                              f"{self.line}.x. Install it again.")
        folder = self.runtimes.folder(self.line)
        command = runtimes.worker_command(self._python, folder, KIND)
        return SnowparkLocalSession(config, command, worker_env(), cwd=str(folder), cell_seconds=self.cell_seconds)
