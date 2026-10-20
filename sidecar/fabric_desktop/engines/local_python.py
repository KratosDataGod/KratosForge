"""Local Python: the notebook's own kernel, in a process of its own, behind the engine contract.

This wraps ``ProcessKernel`` (kernel_proc.py) without changing it. Each session is its own Python
process, so two sessions never share variables, imported modules, ``spark`` or ``notebookutils``
state; the in-process ``Kernel`` can't promise that (it sets ``sys.modules`` for the whole engine),
which is why this adapter doesn't use it. Closing the session ends the process and everything its
cells started.

``spark`` in this engine is DuckDB's PySpark API (spark_local.py), so PySpark is *simulated*:
the code runs on this PC against the attached lakehouse, not on Spark.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .capabilities import EngineDescriptor, Support
from .contract import CellRequest, CellResult, OutputSink, SessionClosed, SessionConfig, Unsupported

_T = "unit_tests"

PYTHON = EngineDescriptor(
    id="local-python", label="Python (this PC)", location="local", platform="local", billing="none", adapter=True,
    family="python",
    languages={
        "python": Support("native", "The engine's Python, in its own process.", _T),
        "pyspark": Support("simulated", "spark is DuckDB's PySpark-compatible API on this PC, not Apache Spark. "
                           "Common DataFrame and spark.sql code runs; RDDs, UDF registration and Spark "
                           "configuration don't.", _T),
    },
    capabilities={
        "session": Support("native", "A Python process per session; closing it ends the process tree.", _T),
        "execute": Support("native", evidence=_T),
        "cancel": Support("native", "Interrupts the cell and keeps variables; Stop ends the process.", _T),
        "stream": Support("native", "Printed output shows while the cell runs.", _T),
        "variables": Support("native", "Names, types and short values of the session's variables.", _T),
        "debug": Support("unsupported", "No step debugger yet; watches and variable history are in the notebook."),
        "plan": Support("unsupported", "Python cells have no query plan; a DuckDB or Polars engine shows one for SQL."),
        "metrics": Support("partial", "Wall time per cell. CPU and memory are sampled for the process by the "
                           "kernel's limits, not reported per cell yet.", _T),
        "catalog": Support("unsupported", "Use the attached lakehouse's tables in the notebook's Data panel."),
        "cost": Support("unsupported", "Runs on this PC, so the engine bills nothing. Reading or writing OneLake "
                        "from this PC still uses Fabric capacity."),
        "compat": Support("partial", "Imports are checked against this PC's packages (envprofile) before running.", _T),
    },
    requirements=("The Python that ships with KratosForge (3.12) and its fixed packages; %pip is not supported.",),
    limitations=("PySpark is simulated with DuckDB; for Spark itself, run in Fabric or on Databricks.",
                 "Packages are the engine's own; there is no per-notebook environment yet."),
)


def _new_outputs(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What changed between two snapshots of a cell's outputs: new outputs, and new text on a stream."""
    news: list[dict[str, Any]] = []
    for i, out in enumerate(after):
        if i >= len(before):
            news.append(out)
        elif out != before[i]:
            old = before[i]
            if out.get("type") == old.get("type") == "stream" and out["text"].startswith(old["text"]):
                news.append({"type": "stream", "text": out["text"][len(old["text"]):]})
            else:
                news.append(out)
    return news


class PythonSession:
    engine = PYTHON.id

    def __init__(self, config: SessionConfig, kernel: Any) -> None:
        self.notebook = config.notebook
        self._kernel = kernel
        self._kernel.notebook_name = config.notebook
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pid(self) -> int | None:
        return self._kernel.pid

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosed("This Python session was closed. Start the engine again to run cells.")

    def execute(self, request: CellRequest, on_output: OutputSink | None = None) -> CellResult:
        self._check_open()
        if request.language not in ("python", "pyspark"):
            raise Unsupported(f"The Python engine doesn't run {request.language} cells.")
        started = time.perf_counter()
        snap = self._kernel.start(request.code)
        version, outputs = -1, []
        while True:
            if snap.get("outputs") is not None:
                if on_output:
                    for out in _new_outputs(outputs, snap["outputs"]):
                        on_output(out)
                outputs = snap["outputs"]
            version = snap.get("version", version)
            if snap["status"] != "running":
                break
            snap = self._kernel.poll(version, 0.5)
        return CellResult(snap["status"], outputs, time.perf_counter() - started, None, self.engine)

    def cancel(self) -> bool:
        if self._closed:
            return False
        return bool(self._kernel.interrupt().get("interrupted"))

    def variables(self) -> list[dict[str, Any]]:
        self._check_open()
        return self._kernel.variables()

    def plan(self, code: str, dialect: str | None = None) -> str:
        raise Unsupported(PYTHON.capability("plan").note)

    def catalog(self) -> list[dict[str, Any]]:
        raise Unsupported(PYTHON.capability("catalog").note)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._kernel.close()


class PythonAdapter:
    """``kernel_factory`` makes the session's kernel; the default is a new ProcessKernel with the tokens
    and notebook loader the engine passes in (the same ones Service gives its kernel)."""

    def __init__(self, kernel_factory: Callable[[], Any] | None = None) -> None:
        self._factory = kernel_factory

    def describe(self) -> EngineDescriptor:
        return PYTHON

    def open_session(self, config: SessionConfig, authorization: Any = None) -> PythonSession:
        if self._factory is None:
            from ..kernel_proc import ProcessKernel
            kernel = ProcessKernel()
        else:
            kernel = self._factory()
        return PythonSession(config, kernel)
