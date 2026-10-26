"""Every engine KratosForge knows, with or without an adapter in this version.

Engines that can't run cells yet are listed too, claiming nothing, so the app and the docs can say
what is planned without suggesting it works. ``REGISTRY_VERSION`` changes with every change to a
descriptor; docs/engines.md lists the same table.
"""

from __future__ import annotations

import importlib.metadata as md
import shutil
from typing import Any

from .capabilities import EngineDescriptor
from .contract import EngineAdapter
from .local_python import PYTHON, NotebookSessionsAdapter, PythonAdapter
from .local_sql import DUCKDB, POLARS, SQLITE, DuckDBAdapter, PolarsAdapter, SQLiteAdapter

REGISTRY_VERSION = "2"

LOCAL_SPARK = EngineDescriptor(
    id="local-spark", label="Apache Spark (this PC)", location="local", platform="local", billing="none",
    adapter=False, family="spark",
    planned="Phase 2: run cells on Apache Spark installed on this PC (PySpark and Java). KratosForge doesn't ship "
            "Spark; whether it should is an open decision. Until then PySpark is simulated by the Python engine.",
    requirements=("PySpark (the Apache package, not DuckDB's)", "Java 17 or later"),
)
FABRIC_SPARK = EngineDescriptor(
    id="fabric-spark", label="Fabric Spark (remote)", location="remote", platform="fabric",
    billing="fabric_capacity", adapter=False, family="spark", preview=True,
    planned="Phase 3+: an interactive Spark session in a Fabric workspace (Livy API), started only after a "
            "confirm or the profile's policy. Today, Run in Fabric runs the whole published notebook as a job.",
    requirements=("A Fabric workspace on a capacity", "Contributor role on the workspace"),
)
DATABRICKS_CONNECT = EngineDescriptor(
    id="databricks-connect", label="Databricks Connect (remote)", location="remote", platform="databricks",
    billing="databricks_dbu", adapter=False, family="spark", preview=True,
    planned="Phase 3: PySpark DataFrame code sent to a Databricks cluster or serverless compute. It runs in its "
            "own Python environment (databricks-connect ships its own pyspark), with the version checked before "
            "anything starts: client major.minor at or below the cluster's runtime, and Python 3.12 for 16.4 "
            "and later. All execution is remote; nothing runs Spark on this PC.",
    requirements=("Unity Catalog-enabled workspace", "Cluster on Databricks Runtime 16.4+ or serverless v3+ "
                  "(the engine's Python is 3.12)", "databricks-connect matching the runtime, in its own environment"),
)
DATABRICKS_SQL = EngineDescriptor(
    id="databricks-sql", label="Databricks SQL warehouse (remote)", location="remote", platform="databricks",
    billing="databricks_dbu", adapter=False, family="databricks-sql", preview=True,
    planned="Phase 3: SQL cells on a Databricks SQL warehouse through the Statement Execution API, with cancel "
            "and the warehouse's query profile. A stopped warehouse is started only after a confirm or policy.",
    requirements=("A SQL warehouse", "CAN USE on the warehouse", "Unity Catalog privileges on the data"),
)

_ADAPTERS: dict[str, type] = {
    PYTHON.id: PythonAdapter, DUCKDB.id: DuckDBAdapter, SQLITE.id: SQLiteAdapter, POLARS.id: PolarsAdapter,
}
ENGINES: dict[str, EngineDescriptor] = {d.id: d for d in (
    PYTHON, DUCKDB, SQLITE, POLARS, LOCAL_SPARK, FABRIC_SPARK, DATABRICKS_CONNECT, DATABRICKS_SQL)}


def get(engine_id: str) -> EngineDescriptor:
    if engine_id not in ENGINES:
        raise ValueError(f"Unknown engine {engine_id!r}. Engines: {', '.join(ENGINES)}.")
    return ENGINES[engine_id]


def adapter(engine_id: str, notebook_sessions: Any = None) -> EngineAdapter:
    """A new adapter for ``engine_id``; an error naming what's planned when it has none yet.

    With ``notebook_sessions`` (the app's KernelSessions), the Python engine uses the notebook's own
    kernel instead of starting processes of its own."""
    desc = get(engine_id)
    if engine_id not in _ADAPTERS:
        raise ValueError(f"{desc.label} can't run cells in this version. {desc.planned}")
    if engine_id == PYTHON.id and notebook_sessions is not None:
        return NotebookSessionsAdapter(notebook_sessions)
    return _ADAPTERS[engine_id]()


def catalog() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "engines": [d.as_dict() for d in ENGINES.values()]}


def local_spark_status() -> dict[str, Any]:
    """Whether Apache Spark could run here: real PySpark installed and Java on the PATH. Reports only."""
    try:
        version = md.version("pyspark")
    except md.PackageNotFoundError:
        version = None
    java = shutil.which("java")
    missing = [n for n, ok in (("PySpark", version), ("Java", java)) if not ok]
    return {"pyspark": version, "java": bool(java), "ready": not missing,
            "detail": "Apache Spark could run on this PC." if not missing else f"Not installed: {', '.join(missing)}."}
