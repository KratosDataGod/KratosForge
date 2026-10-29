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
from .databricks_sql import DATABRICKS_SQL, DatabricksSqlAdapter
from .dbconnect import DATABRICKS_CONNECT, DatabricksConnectAdapter
from .fabric_spark import FABRIC_SPARK, FabricSparkAdapter
from .local_python import PYTHON, NotebookSessionsAdapter, PythonAdapter
from .local_sql import DUCKDB, POLARS, SQLITE, DuckDBAdapter, PolarsAdapter, SQLiteAdapter

REGISTRY_VERSION = "7"

LOCAL_SPARK = EngineDescriptor(
    id="local-spark", label="Apache Spark (this PC)", location="local", platform="local", billing="none",
    adapter=False, family="spark",
    planned="Phase 2: run cells on Apache Spark installed on this PC (PySpark and Java). KratosForge doesn't ship "
            "Spark; whether it should is an open decision. Until then PySpark is simulated by the Python engine.",
    requirements=("PySpark (the Apache package, not DuckDB's)", "Java 17 or later"),
)
# Snowflake (SF-00): planned, claiming nothing. None shares a family with a local engine: a Snowflake
# session can't carry variables to or from this PC's engines, so switching always restarts.
SNOWFLAKE_SQL = EngineDescriptor(
    id="snowflake-sql", label="Snowflake SQL (remote)", location="remote", platform="snowflake",
    billing="snowflake_credits", adapter=False, family="snowflake", preview=True,
    planned="SF-07: SQL cells on a Snowflake warehouse through Snowflake's SQL API, only after a confirm or the "
            "profile's policy. A statement can resume a suspended warehouse, which uses Snowflake credits.",
    requirements=("A Snowflake account and a warehouse", "Key pair, a programmatic access token or OAuth sign-in"),
)
SNOWPARK_LOCAL = EngineDescriptor(
    id="snowpark-local", label="Snowpark (this PC, simulated)", location="local", platform="snowflake",
    billing="none", adapter=False, family="snowpark",
    planned="SF-08: Snowpark Python on Snowflake's local testing framework, in its own runtime folder, with no "
            "Snowflake account. Simulated: session.sql doesn't run and only some functions are implemented. "
            "Preview when it's built.",
    requirements=("Snowpark Python with local testing, installed by KratosForge into its own folder",),
)
SNOWPARK_CONNECT = EngineDescriptor(
    id="snowpark-connect", label="Snowpark Connect (remote)", location="remote", platform="snowflake",
    billing="snowflake_credits", adapter=False, family="snowpark-connect", preview=True,
    planned="SF-10: PySpark cells run on a Snowflake warehouse through Snowpark Connect, in its own runtime folder, "
            "only after a confirm or the profile's policy. Uses Snowflake credits.",
    requirements=("A Snowflake account and a warehouse", "Snowpark Connect, installed by KratosForge into its own folder"),
)
_ADAPTERS: dict[str, type] = {
    PYTHON.id: PythonAdapter, DUCKDB.id: DuckDBAdapter, SQLITE.id: SQLiteAdapter, POLARS.id: PolarsAdapter,
    DATABRICKS_SQL.id: DatabricksSqlAdapter, DATABRICKS_CONNECT.id: DatabricksConnectAdapter,
    FABRIC_SPARK.id: FabricSparkAdapter,
}
ENGINES: dict[str, EngineDescriptor] = {d.id: d for d in (
    PYTHON, DUCKDB, SQLITE, POLARS, LOCAL_SPARK, FABRIC_SPARK, DATABRICKS_CONNECT, DATABRICKS_SQL,
    SNOWFLAKE_SQL, SNOWPARK_LOCAL, SNOWPARK_CONNECT)}


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
