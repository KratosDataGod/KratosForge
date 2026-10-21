"""Running notebooks on Fabric capacity: the published notebook, Fabric's job scheduler, schedules.

A notebook written here imports ``fabric_desktop.lakehouse``, which only exists on this PC.
When such a notebook is published, a first cell is added that defines the same module with
the libraries a Fabric Python notebook already has (Polars, deltalake, DuckDB), so the
notebook runs unchanged in Fabric. ``%%sql`` cells become ``lakehouse.sql`` calls there.
Notebooks that don't use the module (such as the surrogate key templates, which MERGE with
Spark SQL) are published as they are, for a Spark notebook.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from . import magics
from .notebook import configure_report, to_ipynb

SHIM_MARKER = "# Fabric Desktop setup"
TERMINAL = {"Completed", "Failed", "Cancelled", "Deduped"}

# A Python (not Spark) notebook in Fabric.
PYTHON_NOTEBOOK_METADATA = {
    "kernel_info": {"name": "jupyter", "jupyter_kernel_name": "python3.11"},
    "kernelspec": {"name": "jupyter", "display_name": "Jupyter"},
    "language_info": {"name": "python"},
    "microsoft": {"language": "python", "language_group": "jupyter_python"},
}

_SHIM = '''# Fabric Desktop setup: lets `from fabric_desktop import lakehouse` run in Fabric.
# Added when the notebook is published from Fabric Desktop; it isn't in the local copy.
import os as _os, sys as _sys, types as _types
import polars as _pl
from deltalake import DeltaTable as _DeltaTable, write_deltalake as _write_deltalake
from deltalake.exceptions import TableNotFoundError as _TableNotFoundError
import notebookutils as _nbu

def _fabric_desktop_module(workspace_id, lakehouse_id, name, schemas, root=None, mount="/lakehouse/default"):
    root = root or f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}"

    def opts():
        return {"bearer_token": _nbu.credentials.getToken("storage"), "use_fabric_endpoint": "true"}

    def path(p):
        p = str(p).replace("\\\\", "/")
        if p == "Files" or p.startswith("Files/"):
            local = f"{mount}/{p}"
            if lh._proof is not None:
                lh._proof.file_read(p, local)
            return local
        return p

    def parts(table):
        # The same names as on the PC (lakehouse.table_ref there).
        parts = table.split(".")
        if parts[0].lower() == name.lower() and (len(parts) == 3 or len(parts) == 2 and not schemas):
            parts = parts[1:]
        if len(parts) > 2:
            raise ValueError(f"{table}: name tables as table or schema.table in the attached lakehouse ({name}).")
        schema, tbl = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
        if not schemas and schema.lower() not in ("", "dbo"):
            raise ValueError(f"{name} was created without schemas, so a table can't go in {schema!r}. "
                             f"Use {tbl!r}, or put the schema in the name: '{schema}_{tbl}'. "
                             f"lakehouse.has_schemas() tells a notebook which kind it has.")
        return schema or "dbo", tbl

    def uri(table):
        schema, tbl = parts(table)
        return f"{root}/Tables/{schema}/{tbl}" if schemas else f"{root}/Tables/{tbl}"

    lh = _types.ModuleType("fabric_desktop.lakehouse")
    lh._proof = None

    def bind_proof(proof):
        # A proof run (Fabric Desktop's "Prove"): pinned reads, writes to the proof's own folder.
        lh._proof = proof.bind(root, opts, mount)
    lh._bind_proof = bind_proof
    lh.name = lambda: name
    lh.has_schemas = lambda: schemas
    lh.read_csv = lambda p, **kw: _pl.read_csv(path(p), **kw)
    lh.read_parquet = lambda p, **kw: _pl.read_parquet(path(p), **kw)
    lh.read_json = lambda p, **kw: _pl.read_json(path(p), **kw)

    def open_table(table):
        if lh._proof is not None:
            return lh._proof.open(*parts(table), lambda location, version: _DeltaTable(location, version=version,
                                                                                         storage_options=opts()))
        return _DeltaTable(uri(table), storage_options=opts())
    lh.open_table = open_table

    def table_exists(table):
        # Only "not found" means no (a proof's ProofTableMissing is a LookupError): any other
        # failure is raised, so it can't turn into an overwrite. As on the PC.
        try:
            lh.open_table(table)
        except (_TableNotFoundError, LookupError):
            return False
        return True
    lh.table_exists = table_exists
    lh.read_table = lambda table: _pl.from_arrow(lh.open_table(table).to_pyarrow_table())
    lh.files = lambda directory="Files": sorted(f"{directory.rstrip('/')}/{n}" for n in _os.listdir(f"{mount}/{directory}"))

    def write_table(data, table, mode="overwrite"):
        if isinstance(data, _pl.LazyFrame):
            data = data.collect()
        arrow = data.to_arrow() if isinstance(data, _pl.DataFrame) else data

        def write(target, rows, how="overwrite"):
            _write_deltalake(target, rows, mode=how, storage_options=opts(),
                             schema_mode="overwrite" if how == "overwrite" else None)
        target = lh._proof.write_location(*parts(table), mode, write) if lh._proof is not None else uri(table)
        write(target, arrow, mode)
        version = _DeltaTable(target, storage_options=opts()).version()
        print(f"Wrote {arrow.num_rows} rows to {table} (Delta version {version})")
        return version
    lh.write_table = write_table

    def sql(query):
        import duckdb
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            import subprocess
            subprocess.check_call([_sys.executable, "-m", "pip", "install", "-q", "sqlglot"])
            import sqlglot
            from sqlglot import exp
        con, views, result = duckdb.connect(), {}, None
        con.execute("SET TimeZone = 'UTC'")  # SET, not connect's config, which tries to download ICU
        for st in [s for s in sqlglot.parse(query, read="spark") if s is not None]:
            ctes = {c.alias_or_name.lower() for c in st.find_all(exp.CTE)}
            for t in list(st.find_all(exp.Table)):
                if not isinstance(t.this, exp.Identifier) or (not t.db and t.name.lower() in ctes):
                    continue
                key = ".".join(p for p in (t.catalog, t.db, t.name) if p).lower()
                if key not in views:
                    views[key] = f"__t{len(views)}"
                    con.register(views[key], lh.open_table(key).to_pyarrow_dataset())
                alias = t.args.get("alias") or exp.TableAlias(this=exp.to_identifier(t.name))
                t.replace(exp.Table(this=exp.to_identifier(views[key]), alias=alias))
            result = con.execute(st.sql("duckdb"))
        if result is None or not result.description:
            return _pl.DataFrame()
        return _pl.from_arrow((getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)())
    lh.sql = sql

    pkg = _types.ModuleType("fabric_desktop")
    pkg.lakehouse = lh
    _sys.modules["fabric_desktop"], _sys.modules["fabric_desktop.lakehouse"] = pkg, lh

_fabric_desktop_module(%(workspace_id)r, %(lakehouse_id)r, %(name)r, %(schemas)r)
'''

_USES_MODULE = re.compile(r"\b(from\s+fabric_desktop\b|import\s+fabric_desktop\b)")


def uses_local_module(cells: list[dict[str, Any]]) -> bool:
    return any(c.get("type") != "markdown" and _USES_MODULE.search(c.get("source", "")) for c in cells)


def shim_cell(lakehouse: dict[str, Any], schemas_enabled: bool = True) -> dict[str, Any]:
    return {"type": "code", "source": SHIM_MARKER + "\n" + _SHIM % {
        "workspace_id": lakehouse["workspace_id"], "lakehouse_id": lakehouse["lakehouse_id"],
        "name": lakehouse.get("name", ""), "schemas": bool(schemas_enabled)}}


def _sql_cell(source: str, source_cell_id: str | None = None) -> dict[str, Any]:
    query = magics.cell(source).body.strip()
    return {"type": "code", "source": f"lakehouse.sql({json.dumps(query)})", "cell_id": source_cell_id}


_USES_SPARK = re.compile(r"(\bspark\s*\.|\b(from|import)\s+pyspark\b|^\s*%%pyspark\b|\bSparkSession\b)", re.M)


def uses_spark(cells: list[dict[str, Any]]) -> bool:
    return any(c.get("type") != "markdown" and _USES_SPARK.search(c.get("source", "")) for c in cells)


def fabric_ipynb(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None,
                 schemas_enabled: bool = True) -> dict[str, Any]:
    """The notebook as it is published to Fabric.

    A notebook that uses Spark is published as a Spark notebook, where ``spark`` and ``%%sql``
    are Fabric's own; one that uses only the lakehouse module becomes a Python notebook."""
    cells = fabric_sql_magics([magics.fabric_cell(c) for c in cells],
                              spark=not lakehouse or not uses_local_module(cells) or uses_spark(cells))
    if not lakehouse or not uses_local_module(cells):
        return to_ipynb(cells, lakehouse)
    if uses_spark(cells):
        return to_ipynb([shim_cell(lakehouse, schemas_enabled)] + cells, lakehouse)
    body = [_sql_cell(c["source"], c.get("cell_id")) if _is_sql(c) else c for c in cells]
    nb = to_ipynb([shim_cell(lakehouse, schemas_enabled)] + body, lakehouse)
    nb["metadata"] = PYTHON_NOTEBOOK_METADATA | {k: v for k, v in nb["metadata"].items()
                                                 if k not in ("kernelspec", "language_info")}
    return nb


def fabric_sql_magics(cells: list[dict[str, Any]], spark: bool) -> list[dict[str, Any]]:
    """Fabric has %%sql cells but neither ``df = %sql ...`` nor ``_sqldf`` (magics.py, the kernel's
    _run_sql): those become ``spark.sql`` calls (``lakehouse.sql`` in a Python notebook), and a
    %%sql query whose ``_sqldf`` a later cell uses becomes ``_sqldf = spark.sql(...)``."""
    call = "spark.sql" if spark else "lakehouse.sql"
    later = [bool(_USES_SQLDF.search(c.get("source", ""))) and c.get("type") != "markdown" for c in cells]
    out = []
    for i, c in enumerate(cells):
        source = c.get("source", "")
        if c.get("type") == "markdown":
            out.append(c)
        elif _is_sql(c):
            query = magics.cell(source).body.strip()
            if any(later[i + 1:]) and magics.returns_rows(query):
                c = {**c, "source": f"_sqldf = {call}({json.dumps(query)})\ndisplay(_sqldf)"}
            out.append(c)
        elif found := [m for m in magics.find(source) if m.name == "sql"]:
            lines = source.split("\n")
            last = max((n for n, ln in enumerate(lines, 1) if ln.strip()), default=0)
            for m in found:
                indent = re.match(r"\s*", lines[m.line - 1]).group(0)
                query = f"{call}({json.dumps(m.rest.strip())})"
                # A bare %sql on the cell's last line shows its result, as it does on this PC.
                lines[m.line - 1] = indent + (f"{m.target} = {query}" if m.target
                                              else f"display({query})" if m.line == last else query)
            out.append({**c, "source": "\n".join(lines)})
        else:
            out.append(c)
    return out


_USES_SQLDF = re.compile(r"\b_sqldf\b")


def _is_sql(c: dict[str, Any]) -> bool:
    return c.get("type") != "markdown" and magics.cell(c.get("source", "")).language == "sql"


# ---- where a notebook can run

# Code that needs Fabric's Spark runtime or its notebook services. On this PC, ``spark`` is
# DuckDB's PySpark-compatible API and ``notebookutils`` covers fs, notebook, credentials,
# runtime and variableLibrary, so most PySpark notebooks run here; these parts don't.
_FABRIC_ONLY = [
    (re.compile(r"\b(sparkContext|\.rdd\b|pandas_udf|spark\.udf|\.mapInPandas|\.applyInPandas)"),
     "uses a Spark feature this PC's Spark doesn't have"),
    (re.compile(r"\b(spark\.readStream|\.writeStream)\b"), "uses Spark streaming"),
    (re.compile(r"\b(notebookutils|mssparkutils)\.(fs\.(mount|unmount)|session|lakehouse\.(create|update|delete|loadTable)|notebook\.(create|update|updateDefinition|delete)\b|warehouse|"
                r"udf|connections)\b"), "uses Fabric notebook utilities that need Fabric"),
]
_SQL_WRITE = re.compile(r"\b(MERGE\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.I)
# A path on this PC's disk, like C:\data or D:/exports. Fabric can't reach it.
_LOCAL_PATH = re.compile(r"""["'][A-Za-z]:[\\/]""")


def _snippet(source: str, match: re.Match[str] | None = None) -> str:
    """The line that matched (or the first line), short enough to quote."""
    start = source.rfind("\n", 0, match.start()) + 1 if match else 0
    line = next((ln.strip() for ln in source[start:].splitlines() if ln.strip()), "")
    return line if len(line) <= 60 else line[:57] + "..."


def compute_check(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None) -> dict[str, Any]:
    """Whether a notebook can run on this PC and in Fabric, and if not, which cells stop it.

    Each side is {"ok": bool, "reasons": [{"cell": n, "text": ..., "code": first line}]}, cells numbered from 1
    among all cells, as the notebook shows them."""
    local: list[dict[str, Any]] = []
    fabric: list[dict[str, Any]] = []
    for n, c in enumerate(cells, 1):
        source = c.get("source", "")
        if c.get("type") == "markdown" or not source.strip():
            continue
        language = magics.cell(source).language
        if language in ("scala", "r", "csharp"):
            local.append({"cell": n, "text": "uses a Scala, R or .NET cell", "code": _snippet(source)})
        elif language == "tsql":
            local.append({"cell": n, "text": "runs T-SQL in a Fabric Warehouse or SQL endpoint (%%tsql)", "code": _snippet(source)})
        elif language == "sql":
            if _SQL_WRITE.search(source):
                local.append({"cell": n, "text": "changes table rows in place (MERGE, UPDATE or DELETE), which runs in Fabric only",
                              "code": _snippet(source)})
        else:
            hit = next(((m, text) for rx, text in _FABRIC_ONLY if (m := rx.search(source))), None)
            if hit:
                local.append({"cell": n, "text": hit[1], "code": _snippet(source, hit[0])})
            elif m := next((m for m in magics.find(source) if m.name == "sql" and _SQL_WRITE.search(m.rest)), None):
                local.append({"cell": n, "text": "changes table rows in place (MERGE, UPDATE or DELETE), which runs in Fabric only",
                              "code": _snippet(source.split("\n", m.line - 1)[-1])})
        if magics.cell(source).language == "configure":
            try:
                configure_report(magics.cell(source).body)
            except ValueError as exc:
                fabric.append({"cell": n, "text": f"has a %%configure Fabric can't read: {exc}", "code": _snippet(source)})
        if magics.cell(source).header in ("%%cmd", "%%powershell"):
            fabric.append({"cell": n, "text": "runs a Windows shell; Fabric runs on Linux (use %%sh there)",
                           "code": _snippet(source)})
        if m := _LOCAL_PATH.search(source):
            fabric.append({"cell": n, "text": "reads or writes a path on this PC, which Fabric can't reach",
                           "code": _snippet(source, m)})
    if not lakehouse:
        fabric.insert(0, {"cell": None, "text": "has no lakehouse attached. A Fabric run reads and writes "
                                                "through the attached lakehouse.", "code": ""})
    return {"local": {"ok": not local, "reasons": local}, "fabric": {"ok": not fabric, "reasons": fabric}}


# ---- schedules

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def schedule_configuration(kind: str, times: list[str] | None = None, every_minutes: int | None = None,
                           weekdays: list[str] | None = None, time_zone: str = "UTC",
                           start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Fabric's schedule configuration for "every N minutes", "daily at" or "weekly on ... at"."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = start or now.strftime("%Y-%m-%dT%H:%M:%S")
    end = end or (now + dt.timedelta(days=3650)).strftime("%Y-%m-%dT%H:%M:%S")
    config: dict[str, Any] = {"startDateTime": start, "endDateTime": end, "localTimeZoneId": time_zone}
    times = [t.strip() for t in (times or []) if t.strip()]
    for t in times:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError(f"Times are HH:MM in 24-hour form, like 06:00 (got {t!r})")
    if kind == "cron":
        if not every_minutes or not 1 <= int(every_minutes) <= 5270400:
            raise ValueError("Pick how many minutes between runs")
        return config | {"type": "Cron", "interval": int(every_minutes)}
    if kind == "daily":
        if not times:
            raise ValueError("Pick at least one time of day")
        return config | {"type": "Daily", "times": times}
    if kind == "weekly":
        days = [d for d in (weekdays or []) if d in WEEKDAYS]
        if not days or not times:
            raise ValueError("Pick at least one day and one time")
        return config | {"type": "Weekly", "weekdays": days, "times": times}
    raise ValueError(f"unknown schedule kind {kind!r}")


def describe_schedule(s: dict[str, Any]) -> str:
    c = s.get("configuration", {})
    tz = c.get("localTimeZoneId", "")
    if c.get("type") == "Cron":
        return f"Every {c.get('interval')} minutes"
    if c.get("type") == "Daily":
        return f"Daily at {', '.join(c.get('times', []))} {tz}".strip()
    if c.get("type") == "Weekly":
        return f"{', '.join(d[:3] for d in c.get('weekdays', []))} at {', '.join(c.get('times', []))} {tz}".strip()
    return c.get("type", "Schedule")
