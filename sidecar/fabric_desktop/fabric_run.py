"""Running notebooks on Fabric capacity: the published notebook, Fabric's job scheduler, schedules.

A notebook written here imports ``fabric_desktop.lakehouse``, which only exists on this PC.
When such a notebook is published, a first cell is added that defines the same module with
the libraries a Fabric Python notebook already has (Polars, deltalake, DuckDB), so the
notebook runs unchanged in Fabric. ``%%sql`` cells become ``lakehouse.sql`` calls there.
Notebooks that don't use the module (such as the surrogate key templates, which MERGE with
Spark SQL) are published as they are, for a Spark notebook.
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
from collections.abc import Callable, Collection
from typing import Any

from . import magics
from .notebook import _RUN_SCRIPT, MAX_RUN_DEPTH, _run_target, configure_report, to_ipynb

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

    def write_table(data, table, mode="overwrite", schema_mode=None, upload=None, skip_unchanged=None):
        if isinstance(data, _pl.LazyFrame):
            data = data.collect()
        rows = [0]
        if hasattr(data, "read_all"):  # a stream of batches, as the PC takes
            if lh._proof is not None:
                data = data.read_all()  # a proof may write it more than once
            else:
                import pyarrow as _pa
                stream = data

                def counted():
                    for batch in stream:
                        rows[0] += batch.num_rows
                        yield batch
                data = _pa.RecordBatchReader.from_batches(stream.schema, counted())  # never all in memory
        arrow = data.to_arrow() if isinstance(data, _pl.DataFrame) else data

        def write(target, rows, how="overwrite"):
            _write_deltalake(target, rows, mode=how, storage_options=opts(),
                             schema_mode="overwrite" if how == "overwrite" else None)
        target = lh._proof.write_location(*parts(table), mode, write) if lh._proof is not None else uri(table)
        write(target, arrow, mode)
        version = _DeltaTable(target, storage_options=opts()).version()
        print(f"Wrote {getattr(arrow, 'num_rows', rows[0])} rows to {table} (Delta version {version})")
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
                 schemas_enabled: bool = True, sql_calls: bool = False) -> dict[str, Any]:
    """The notebook as it is published to Fabric.

    A notebook that uses Spark is published as a Spark notebook, where ``spark`` and ``%%sql``
    are Fabric's own; one that uses only the lakehouse module becomes a Python notebook.
    ``sql_calls`` makes every %%sql cell a ``spark.sql`` call, so a proof's Spark side sees it."""
    cells = fabric_sql_magics([magics.fabric_cell(c) for c in cells],
                              spark=not lakehouse or not uses_local_module(cells) or uses_spark(cells),
                              every=sql_calls)
    if not lakehouse or not uses_local_module(cells):
        return to_ipynb(cells, lakehouse)
    if uses_spark(cells):
        return to_ipynb([shim_cell(lakehouse, schemas_enabled)] + cells, lakehouse)
    body = [_sql_cell(c["source"], c.get("cell_id")) if _is_sql(c) else c for c in cells]
    nb = to_ipynb([shim_cell(lakehouse, schemas_enabled)] + body, lakehouse)
    nb["metadata"] = PYTHON_NOTEBOOK_METADATA | {k: v for k, v in nb["metadata"].items()
                                                 if k not in ("kernelspec", "language_info")}
    return nb


def fabric_sql_magics(cells: list[dict[str, Any]], spark: bool, every: bool = False) -> list[dict[str, Any]]:
    """Fabric has %%sql cells but neither ``df = %sql ...`` nor ``_sqldf`` (magics.py, the kernel's
    _run_sql): those become ``spark.sql`` calls (``lakehouse.sql`` in a Python notebook), and a
    %%sql query whose ``_sqldf`` a later cell uses becomes ``_sqldf = spark.sql(...)``. With
    ``every``, each %%sql cell becomes a call."""
    call = "spark.sql" if spark else "lakehouse.sql"
    later = [bool(_USES_SQLDF.search(c.get("source", ""))) and c.get("type") != "markdown" for c in cells]
    out = []
    for i, c in enumerate(cells):
        source = c.get("source", "")
        if c.get("type") == "markdown":
            out.append(c)
        elif _is_sql(c):
            query = magics.cell(source).body.strip()
            if (every or any(later[i + 1:])) and magics.returns_rows(query):
                c = {**c, "source": f"_sqldf = {call}({json.dumps(query)})\ndisplay(_sqldf)"}
            elif every:
                c = {**c, "source": f"{call}({json.dumps(query)})"}
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

# The notebookutils parts that need Fabric, after "notebookutils." (or "mssparkutils.").
_UTILS_FABRIC = (r"(fs\.(mount|unmount)|lakehouse\.(create|update|delete|loadTable)|notebook\.(create|update|updateDefinition|delete)\b|"
                 r"warehouse|udf|connections)\b")
_UTILS_TEXT = "uses Fabric notebook utilities that need Fabric"

# Code that needs Fabric's Spark runtime or its notebook services. On this PC, ``spark`` is
# DuckDB's PySpark-compatible API and ``notebookutils`` covers fs, notebook, credentials,
# runtime and variableLibrary, so most PySpark notebooks run here; these parts don't.
_FABRIC_ONLY = [
    (re.compile(r"\b(sparkContext|\.rdd\b|pandas_udf|spark\.udf|\.mapInPandas|\.applyInPandas)"),
     "uses a Spark feature this PC's Spark doesn't have"),
    # Streams run here as trigger(availableNow=True) (spark_stream.py); these kinds don't.
    (re.compile(r"\.trigger\(\s*continuous\s*="), "runs a continuous stream"),
    (re.compile(r"""\.format\(\s*["'](kafka|eventhubs|socket|rate)["']"""), "streams from Kafka, Event Hubs or a socket"),
    (re.compile(r"\.option\(\s*[\"'](replaceWhere|partitionOverwriteMode)[\"']", re.I),
     "uses a write option this PC doesn't have (replaceWhere or partitionOverwriteMode); ignoring it would replace the whole table"),
    (re.compile(r"\b(notebookutils|mssparkutils)\." + _UTILS_FABRIC), _UTILS_TEXT),
]
# Databricks' utilities: this PC has them, Fabric has notebookutils instead.
_DBUTILS = re.compile(r"\bdbutils\.\w+|\bfrom\s+databricks\.sdk\.runtime\s+import\b")
# MERGE INTO, UPDATE and DELETE run on this PC (spark_local.py, PB-015), into the attached lakehouse.
_SQL_CHANGE = re.compile(r"\b(MERGE\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM)\b", re.I)
# A spark.sql call on a plain string literal (an f-string's text isn't known until it runs).
_SPARK_SQL_LITERAL = re.compile(r"""\bspark\.sql\(\s*(?P<lit>[rRuU]?(?P<q>\"\"\"|'''|"|')(?:\\.|(?!(?P=q)).)*(?P=q))""", re.S)


def _literal_sql(m: re.Match[str]) -> str:
    """The SQL text of a spark.sql string literal, as Python reads it (escapes and all)."""
    try:
        return str(ast.literal_eval(m["lit"]))
    except (ValueError, SyntaxError):
        return ""


def _in_comment(source: str, offset: int) -> bool:
    """Whether ``offset`` in Python ``source`` falls in a # comment. Source Python can't read
    (magics, a half-typed cell) has no comments found, so nothing in it is skipped."""
    import io
    import tokenize
    line = source.count("\n", 0, offset) + 1
    col = offset - (source.rfind("\n", 0, offset) + 1)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT and tok.start[0] == line and tok.start[1] <= col:
                return True
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return False
    return False


def _sql_in_place(sql: str, lakehouse_name: str | None = None) -> bool:
    """SQL that changes rows in place in a way this PC doesn't run, and so runs in Fabric only: a
    MERGE, UPDATE or DELETE of another lakehouse, or with Preview features off (spark_local.change_runs_here)."""
    if not _SQL_CHANGE.search(sql):
        return False
    from . import spark_local
    return not spark_local.change_runs_here(sql, lakehouse_name)
# A path on this PC's disk, like C:\data or D:/exports. Fabric can't reach it.
_LOCAL_PATH = re.compile(r"""["'][A-Za-z]:[\\/]""")


# ---- reading a cell's Python (PB-014)
#
# The patterns above read a cell's text. Python cells are also parsed, so notebookutils is found by
# any name an import or assignment gives it (import notebookutils as nu, from notebookutils import
# fs, fs = nu.fs), and spark.sql's SQL is read where it's kept in a variable or an f-string. Code the
# check can't follow is Unknown: never taken as fine, never taken as needing Fabric.

_UTILS = ("notebookutils", "mssparkutils")
_UTILS_USE = re.compile(r"(notebookutils|mssparkutils)\." + _UTILS_FABRIC)  # matched at the start of a full name
_UTILS_STAR = ("fs", "lakehouse", "notebook", "warehouse", "udf", "connections", "credentials", "runtime", "mssparkutils")
_UTILS_RUN = re.compile(r"(notebookutils|mssparkutils)\.notebook\.(run|runMultiple)\Z")
_RUN_TEXT = "runs another notebook ({how}), whose cells this check doesn't read"
_VALUE = "fd_value_"  # stands in SQL text for a part known only at run time
_SQL_DYNAMIC_TABLE = ("changes table rows with SQL whose table is known only at run time, so the check can't tell "
                      "whether it's this lakehouse's (runs here) or another's (Fabric only)")
_SQL_DYNAMIC = ("runs SQL built at run time (spark.sql on text the check can't read), so it can't tell whether "
                "it changes another lakehouse's table, which runs in Fabric only")


def _python(source: str) -> str | None:
    """A Python cell's code as Python reads it (magics blanked, line numbers kept), or None for another language."""
    c = magics.cell(source)
    if c.language != "python":
        return None
    return magics.blank("\n".join("" if ln.lstrip().startswith("%%") else ln for ln in c.body.split("\n")))


def _call_name(node: ast.Call) -> str:
    """``importlib.import_module`` for ``importlib.import_module(...)``; "" when it isn't a plain dotted name."""
    f, parts = node.func, []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    return ".".join([f.id, *reversed(parts)]) if isinstance(f, ast.Name) else ""


def _text(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _imported(node: ast.AST) -> str | None:
    """The module ``importlib.import_module("m")`` or ``__import__("m")`` returns, when named in a literal."""
    if not isinstance(node, ast.Call) or not node.args or (name := _text(node.args[0])) is None:
        return None
    call = _call_name(node)
    if call == "__import__":
        return name.split(".")[0]
    return name if call in ("importlib.import_module", "import_module") else None


def _full(node: ast.AST, utils: dict[str, str]) -> str | None:
    """The full name (notebookutils.fs.mount) of the notebookutils part ``node`` refers to, by whatever
    name the notebook gave it, or None when it isn't notebookutils."""
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call) and _call_name(node) == "getattr" and len(node.args) >= 2 \
                and (attr := _text(node.args[1])) is not None:
            parts.append(attr)
            node = node.args[0]
        else:
            break
    if isinstance(node, ast.Name):
        root = utils.get(node.id) or (node.id if node.id in _UTILS else None)
    else:
        root = _imported(node)
    if root is None or root.split(".")[0] not in _UTILS:
        return None
    return ".".join([root, *reversed(parts)]).replace("notebookutils.mssparkutils", "mssparkutils", 1)


def _names(trees: list[ast.AST]) -> tuple[dict[str, str], dict[str, list[ast.expr | None]]]:
    """What the notebook's code makes of its names: which stand for notebookutils or a part of it
    (by import or assignment), and every value each name is given (None for one the check can't
    see, such as a loop variable or a function's argument), for SQL text kept in a variable."""
    utils: dict[str, str] = {}
    values: dict[str, list[ast.expr | None]] = {}
    plain: set[int] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                utils |= {a.asname: a.name for a in node.names if a.asname and a.name.split(".")[0] in _UTILS}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module \
                    and node.module.split(".")[0] in _UTILS:
                for a in node.names:
                    utils |= ({n: f"{node.module}.{n}" for n in _UTILS_STAR} if a.name == "*"
                              else {a.asname or a.name: f"{node.module}.{a.name}"})
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(t, ast.Name):
                        plain.add(id(t))
                        values.setdefault(t.id, []).append(node.value)
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and id(node) not in plain:
                values.setdefault(node.id, []).append(None)
            elif isinstance(node, ast.arg):
                values.setdefault(node.arg, []).append(None)
    for _ in range(5):  # fs = nu.fs, then mount = fs.mount
        found = {}
        for name, vals in values.items():
            fulls = {_full(v, utils) if v is not None else None for v in vals}
            if name not in utils and len(fulls) == 1 and (full := fulls.pop()):
                found[name] = full
        if not found:
            break
        utils |= found
    return utils, values


def _sql_texts(node: ast.AST, values: dict[str, list[ast.expr | None]], depth: int = 0) -> list[str] | None:
    """Every SQL text a spark.sql argument can be, with fd_value_N for each part known only at run
    time, or None when the text itself can't be read."""
    if depth > 5:
        return None
    if (text := _text(node)) is not None:
        return [text]
    if isinstance(node, ast.JoinedStr):
        joined = [""]
        for i, part in enumerate(node.values):
            # f"{t}" with t a name given only strings is read as those strings; anything else stays a run-time part.
            inner = part.value if isinstance(part, ast.FormattedValue) and part.conversion == -1 \
                and part.format_spec is None else None
            read = _sql_texts(inner, values, depth + 1) if isinstance(inner, ast.Name) else None
            options = [str(part.value)] if isinstance(part, ast.Constant) else read or [f"{_VALUE}{i}"]
            joined = [t + o for t in joined for o in options][:16]
        return joined
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_texts(node.left, values, depth + 1) or [f"{_VALUE}0"]
        right = _sql_texts(node.right, values, depth + 1) or [f"{_VALUE}1"]
        return [a + b for a in left for b in right][:16]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format" \
            and (template := _text(node.func.value)) is not None:
        return [re.sub(r"\{[^{}]*\}", f"{_VALUE}0", template)]
    if isinstance(node, ast.Name):
        vals = values.get(node.id) or [None]
        out: list[str] = []
        for v in vals:
            texts = _sql_texts(v, values, depth + 1) if v is not None else None
            if texts is None:
                return None
            out += texts
        return out[:16]
    return None


def _sessions(values: dict[str, list[ast.expr | None]]) -> set[str]:
    """The names that stand for the Spark session: ``spark``, and any name every value of which is
    one of them or a ``SparkSession.builder...getOrCreate()`` call."""
    names = {"spark"}
    for _ in range(5):
        found = {n for n, vals in values.items() if n not in names and vals and all(
            isinstance(v, ast.Name) and v.id in names or isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
            and v.func.attr == "getOrCreate" for v in vals)}
        if not found:
            break
        names |= found
    return names


def _session_sql(node: ast.Call, sessions: Collection[str]) -> bool:
    """Whether ``node`` is ``.sql(...)`` on the Spark session, by any of its names."""
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr != "sql":
        return False
    return isinstance(f.value, ast.Name) and f.value.id in sessions or \
        isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Attribute) and f.value.func.attr == "getOrCreate"


def _change_target(sql: str) -> str | None:
    """The table a single MERGE, UPDATE or DELETE changes, as SQL, or None when it doesn't read as one."""
    import sqlglot
    from sqlglot import exp
    try:
        trees = [t for t in sqlglot.parse(sql, read="spark") if t is not None]
    except sqlglot.errors.SqlglotError:
        return None
    if len(trees) != 1 or not isinstance(trees[0], (exp.Merge, exp.Update, exp.Delete)) \
            or not isinstance(trees[0].this, exp.Table):
        return None
    return str(trees[0].this.sql())


def _sql_where(sql: str, own: str | None) -> str | None:
    """"fabric" for SQL that runs in Fabric only, "table" when that depends on a table named only at run time,
    "statement" when the statement itself is chosen then, else None."""
    if _VALUE not in sql:
        return "fabric" if _sql_in_place(sql, own) else None
    if sql.lstrip().startswith(_VALUE):
        return "statement"
    if not _SQL_CHANGE.search(sql):
        return None
    target = _change_target(sql)
    in_place = _sql_in_place(sql, own)
    if target is None or (_VALUE in target and not in_place):
        return "table"
    return "fabric" if in_place else None


def _read_python(tree: ast.AST, utils: dict[str, str], values: dict[str, list[ast.expr | None]],
                 own: str | None, sessions: Collection[str] = ("spark",)) -> dict[str, list[Any]]:
    """What a Python cell's code says about where it runs, each as (line, text): ``fabric`` (runs in
    Fabric only), ``unknown_local`` (the check can't tell for this PC) and ``unknown`` (it can't tell
    for either place). ``runs`` are the notebooks it runs by a literal name, as (line, name, how)."""
    out: dict[str, list[Any]] = {"fabric": [], "unknown_local": [], "unknown": [], "runs": []}
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 1)
        if isinstance(node, ast.ImportFrom) and not node.level and node.module and node.module.split(".")[0] in _UTILS:
            if any(_UTILS_USE.match(f"{node.module}.{a.name}".replace("notebookutils.mssparkutils", "mssparkutils", 1))
                   for a in node.names):
                out["fabric"].append((line, _UTILS_TEXT))
        elif isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load):
            if (full := _full(node, utils)) and _UTILS_USE.match(full):
                out["fabric"].append((line, _UTILS_TEXT))
        if not isinstance(node, ast.Call):
            continue
        call, first = _call_name(node), node.args[0] if node.args else None
        if call == "getattr" and len(node.args) >= 2 and _text(node.args[1]) is None and _full(node.args[0], utils):
            out["unknown_local"].append((line, "looks up a notebookutils function by a name known only at run time, "
                                               "so the check can't tell whether it needs Fabric"))
        elif call == "getattr" and (full := _full(node, utils)) and _UTILS_USE.match(full):
            out["fabric"].append((line, _UTILS_TEXT))
        elif call in ("exec", "eval", "compile") and first is not None and _text(first) is None:
            out["unknown"].append((line, f"runs code built at run time ({call}), which the check can't read"))
        elif call in ("__import__", "importlib.import_module", "import_module") and first is not None \
                and _text(first) is None:
            out["unknown"].append((line, "imports a module named only at run time, which the check can't read"))
        elif (full := _full(node.func, utils)) and _UTILS_RUN.match(full):
            how = full.split(".", 1)[1]
            name = _text(first) if first is not None and how == "notebook.run" else None
            if name is not None:
                out["runs"].append((line, name.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".ipynb"), how))
            else:
                out["unknown"].append((line, _RUN_TEXT.format(how=how)))
        elif _session_sql(node, sessions):
            arg = first if first is not None else next((k.value for k in node.keywords if k.arg == "sqlQuery"), None)
            texts = _sql_texts(arg, values) if arg is not None else None
            if texts is None:
                out["unknown_local"].append((line, _SQL_DYNAMIC))
                continue
            verdicts = {_sql_where(t, own) for t in texts}
            if "fabric" in verdicts:
                out["fabric"].append((line, "changes table rows in place with spark.sql (MERGE, UPDATE or DELETE of another "
                                            "lakehouse, or with Preview features off), which runs in Fabric only"))
            elif verdicts & {"statement", "table"}:
                out["unknown_local"].append((line, _SQL_DYNAMIC if "statement" in verdicts else _SQL_DYNAMIC_TABLE))
    for kind in out:
        out[kind].sort()
    return out


def _line(source: str, line: int) -> str:
    """Line ``line`` (from 1) of ``source``, short enough to quote."""
    lines = source.split("\n")
    return _snippet(lines[line - 1] if 0 < line <= len(lines) else source)


def _snippet(source: str, match: re.Match[str] | None = None) -> str:
    """The line that matched (or the first line), short enough to quote."""
    start = source.rfind("\n", 0, match.start()) + 1 if match else 0
    line = next((ln.strip() for ln in source[start:].splitlines() if ln.strip()), "")
    return line if len(line) <= 60 else line[:57] + "..."


def compute_check(cells: list[dict[str, Any]], lakehouse: dict[str, Any] | None,
                  load: Callable[[str], list[dict[str, Any]]] | None = None, _seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """Whether a notebook can run on this PC and in Fabric, and if not, which cells stop it.

    Each side is {"ok": bool, "status": "ok" | "blocked" | "unknown", "reasons": [...], "unknown": [...]}.
    ``reasons`` are the cells that stop it there; ``unknown`` the cells the check can't read well
    enough to tell (code built at run time, SQL whose table is known only then, a cell Python can't
    read). Each is {"cell": n, "text": ..., "code": the line}, cells numbered from 1 among all cells,
    as the notebook shows them. ``ok`` is True only when nothing stops it and nothing is unknown:
    Unknown is never taken as runs there. ``status`` is "blocked" when anything stops it.

    ``load`` gives the cells of another notebook on this PC by name, as the kernel's ``%run`` finds
    it. With it, a notebook the code runs (``%run Name``, ``notebook.run("Name")``) is checked too,
    and what stops it or is unknown in it is this cell's; without it, that cell is Unknown."""
    local: list[dict[str, Any]] = []
    fabric: list[dict[str, Any]] = []
    local_unknown: list[dict[str, Any]] = []
    fabric_unknown: list[dict[str, Any]] = []
    own = (lakehouse or {}).get("name")  # the notebook's lakehouse, for a check made with none attached
    trees: dict[int, ast.AST | SyntaxError] = {}
    for n, c in enumerate(cells, 1):
        if c.get("type") != "markdown" and (code := _python(c.get("source", ""))) is not None and code.strip():
            try:
                trees[n] = ast.parse(code)
            except (SyntaxError, ValueError) as exc:
                trees[n] = exc if isinstance(exc, SyntaxError) else SyntaxError(str(exc))
    utils, values = _names([t for t in trees.values() if isinstance(t, ast.AST)])
    sessions = _sessions(values)
    for n, c in enumerate(cells, 1):
        source = c.get("source", "")
        if c.get("type") == "markdown" or not source.strip():
            continue
        blocked = len(local)
        tree = trees.get(n)
        read = _read_python(tree, utils, values, own, sessions) if isinstance(tree, ast.AST) else None
        unknown: list[tuple[int, str]] = []
        if isinstance(tree, SyntaxError):
            unknown = [(tree.lineno or 1, f"isn't Python the check can read (line {tree.lineno or 1}: {tree.msg}), "
                                          "so it can't tell where the cell runs")]
        language = magics.cell(source).language
        if language in ("scala", "r", "csharp"):
            local.append({"cell": n, "text": "uses a Scala, R or .NET cell", "code": _snippet(source)})
        elif language == "tsql":
            local.append({"cell": n, "text": "runs T-SQL in a Fabric Warehouse or SQL endpoint (%%tsql)", "code": _snippet(source)})
        elif language == "sql":
            if _sql_in_place(magics.cell(source).body, own):
                local.append({"cell": n, "text": "changes table rows in place (MERGE, UPDATE or DELETE of another lakehouse, or with Preview features off), which runs in Fabric only",
                              "code": _snippet(source)})
        else:
            hit = next(((m, text) for rx, text in _FABRIC_ONLY if (m := rx.search(source))), None)
            if read is None:  # not parsed: the literal spark.sql calls in its text
                hit = hit or next(((m, "changes table rows in place with spark.sql (MERGE, UPDATE or DELETE of another "
                                       "lakehouse, or with Preview features off), which runs in Fabric only")
                                   for m in _SPARK_SQL_LITERAL.finditer(source)
                                   if not _in_comment(source, m.start()) and _sql_in_place(_literal_sql(m), own)), None)
            if hit:
                local.append({"cell": n, "text": hit[1], "code": _snippet(source, hit[0])})
            elif read and read["fabric"]:
                line, text = read["fabric"][0]
                local.append({"cell": n, "text": text, "code": _line(source, line)})
            elif m := next((m for m in magics.find(source) if m.name == "sql" and _sql_in_place(m.rest, own)), None):
                local.append({"cell": n, "text": "changes table rows in place (MERGE, UPDATE or DELETE of another lakehouse, or with Preview features off), which runs in Fabric only",
                              "code": _snippet(source.split("\n", m.line - 1)[-1])})
        if read:
            unknown = list(read["unknown"])
        runs: list[tuple[int, str | None, str]] = list(read["runs"]) if read else []
        if language == "python":
            for magic in magics.find(source):
                if magic.name == "run":
                    try:  # %run -b/-c runs a resources file, which the check doesn't read
                        name = None if _RUN_SCRIPT.match(magic.rest) else _run_target(magic.rest)[0]
                    except ValueError:
                        name = None
                    runs.append((magic.line, name, "%run"))
        run_block: dict[str, tuple[int, str] | None] = {"local": None, "fabric": None}
        run_unknown: dict[str, list[tuple[int, str]]] = {"local": [], "fabric": []}
        for line, name, how in sorted(runs, key=lambda r: r[0]):
            for side, got in _followed(name, how, lakehouse, load, _seen).items():
                if got and got[0] == "blocked":
                    run_block[side] = run_block[side] or (line, got[1])
                elif got:
                    run_unknown[side].append((line, got[1]))
        if len(local) == blocked and (hit_run := run_block["local"]):
            local.append({"cell": n, "text": hit_run[1], "code": _line(source, hit_run[0])})
        if len(local) == blocked and (here := sorted(unknown + run_unknown["local"]
                                                    + (read["unknown_local"] if read else []))):
            local_unknown.append({"cell": n, "text": here[0][1], "code": _line(source, here[0][0])})
        fabric_before = len(fabric)
        if m := _DBUTILS.search(source):
            fabric.append({"cell": n, "text": "uses Databricks dbutils, which Fabric doesn't have; "
                                               "use notebookutils in Fabric", "code": _snippet(source, m)})
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
        if len(fabric) == fabric_before and (hit_run := run_block["fabric"]):
            fabric.append({"cell": n, "text": hit_run[1], "code": _line(source, hit_run[0])})
        elif len(fabric) == fabric_before and (unknown or run_unknown["fabric"]):
            first = min(unknown + run_unknown["fabric"])
            fabric_unknown.append({"cell": n, "text": first[1], "code": _line(source, first[0])})
    if not lakehouse:
        fabric.insert(0, {"cell": None, "text": "has no lakehouse attached. A Fabric run reads and writes "
                                                "through the attached lakehouse.", "code": ""})
    return {"local": _side(local, local_unknown), "fabric": _side(fabric, fabric_unknown)}


def _followed(name: str | None, how: str, lakehouse: dict[str, Any] | None,
              load: Callable[[str], list[dict[str, Any]]] | None, seen: tuple[str, ...]) -> dict[str, tuple[str, str] | None]:
    """What a cell that runs notebook ``name`` gets from it on each side: ("blocked" or "unknown", text), or None."""
    def both(text: str) -> dict[str, tuple[str, str] | None]:
        return {"local": ("unknown", text), "fabric": ("unknown", text)}
    if name is None or load is None:
        return both(_RUN_TEXT.format(how=how))
    if name.lower() in (s.lower() for s in seen) or len(seen) >= MAX_RUN_DEPTH:
        return both(f"runs {name} ({how}), which runs notebooks in a loop or more than {MAX_RUN_DEPTH} deep, "
                    "so the check stops there")
    try:
        cells = load(name)
    except (OSError, ValueError, KeyError, TypeError):
        return both(f"runs {name} ({how}), which isn't a notebook on this PC, so the check can't read it")
    child = compute_check(cells, lakehouse, load, seen + (name,))
    out: dict[str, tuple[str, str] | None] = {}
    for side in ("local", "fabric"):
        blockers = [r for r in child[side]["reasons"] if r.get("cell")]  # not "no lakehouse": that's this notebook's own
        r = blockers[0] if blockers else (child[side]["unknown"] or [None])[0]
        out[side] = (("blocked" if blockers else "unknown"), f"runs {name} ({how}), whose cell {r['cell']} {r['text']}") \
            if r else None
    return out


def _side(reasons: list[dict[str, Any]], unknown: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": not reasons and not unknown, "status": "blocked" if reasons else "unknown" if unknown else "ok",
            "reasons": reasons, "unknown": unknown}


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
