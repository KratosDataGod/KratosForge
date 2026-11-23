"""Best practices for a notebook's code, checked on this PC without running it.

Each cell is read as text and, for Python cells, with Python's own parser; nothing runs and nothing
is read from Fabric. Findings have the shape model_bpa.findings gives a semantic model (severity,
rule, category, object, description, url), plus the rule's id and the cell and line it is about.

``settings`` maps a rule id to ``{"enabled": bool, "severity": "Error"|"Warning"|"Info"}``; that is
how a governance template turns a rule off or changes how much it matters.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .git.secrets import scan_text

SEVERITIES = ("Error", "Warning", "Info")
DOCS = "notebook-best-practices.md"   # each rule has a section there, anchored by its id: #nb001


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    category: str
    name: str
    description: str
    scheduled_only: bool = False


RULES: tuple[Rule, ...] = (
    Rule("NB001", "Error", "Security", "Secret written in a cell",
         "Keys, passwords and connection strings in code end up in Git, versions and outputs. "
         "Read them from Key Vault with notebookutils.credentials.getSecret."),
    Rule("NB002", "Warning", "Maintenance", "Workspace or lakehouse id written in a path",
         "A OneLake path with a workspace or item id breaks when the notebook is promoted to another "
         "workspace. Use the default lakehouse's relative path (Tables/..., Files/...) or a parameter."),
    Rule("NB003", "Warning", "Performance", "collect() or toPandas() without a limit",
         "Brings every row to one machine. Add .limit(n), or aggregate first."),
    Rule("NB004", "Info", "Performance", "display() of a whole table in a scheduled notebook",
         "Nobody reads a scheduled run's output, and a whole table makes it big. Display a sample or a count.",
         scheduled_only=True),
    Rule("NB005", "Warning", "Performance", "CSV or JSON read without a schema",
         "Inferring the schema reads the data an extra time and changes when the files do. Pass .schema(...)."),
    Rule("NB006", "Info", "Maintenance", "SELECT * in SQL",
         "Reads every column and breaks when columns are added or renamed. Name the columns."),
    Rule("NB007", "Info", "Performance", "Whole table overwritten",
         "An overwrite with no replaceWhere rewrites every file. Overwrite only what changed, or MERGE."),
    Rule("NB008", "Warning", "Reliability", "%pip install in a scheduled notebook",
         "Installs on every run, which is slow and can fail. Put the package in an environment.",
         scheduled_only=True),
    Rule("NB009", "Warning", "Reliability", "Package installed without a version",
         "A new release can change results without warning. Pin it: package==1.2.3."),
    Rule("NB010", "Warning", "Performance", "Table written inside a loop",
         "Each pass writes small files and a new table version. Collect the rows and write once."),
    Rule("NB011", "Info", "Performance", "count() called again and again on one DataFrame",
         "Each count() runs the whole query again. Keep the count in a variable, or cache the DataFrame."),
    Rule("NB012", "Warning", "Reliability", "Error caught and ignored",
         "except ...: pass hides failures, so a run looks fine when it isn't. Log the error, or let it fail."),
    Rule("NB014", "Warning", "Performance", "Row-by-row pandas loop",
         "iterrows(), itertuples() and apply(..., axis=1) run Python once per row, which is slow on large tables. "
         "Write the change as a column expression, or use Polars or DuckDB."),
    Rule("NB015", "Info", "Performance", "Polars reads the whole file, then filters",
         "pl.read_csv or pl.read_parquet loads every row before .filter() drops most of them. "
         "pl.scan_csv or pl.scan_parquet with .filter() and .collect() may read only what the filter keeps."),
    Rule("NB017", "Info", "Maintenance", "print() used for logging in a scheduled notebook",
         "Nobody reads a scheduled run's printed output, and it has no time or level. Use the logging module, "
         "or write the run's facts to a table.", scheduled_only=True),
    # NB018 to NB020 are based on guidance in microsoft/skills-for-fabric (MIT), written in our own words.
    Rule("NB018", "Info", "Performance", "Python UDF in Spark code",
         "A Python UDF moves every row out of Spark and back. Use a built-in function from "
         "pyspark.sql.functions, or a pandas_udf when no built-in fits."),
    Rule("NB019", "Warning", "Reliability", "Spark reads straight from a web address",
         "Spark can't read http(s) addresses reliably, and the file can change or vanish between runs. "
         "Copy it into the lakehouse's Files once, then read it from there."),
    Rule("NB020", "Warning", "Reliability", "VACUUM keeps less than 7 days of history",
         "Files a running reader or writer still needs can be deleted, and time travel to those versions "
         "stops working. Keep the default 168 hours unless nothing else uses the table."),
)
_BY_ID = {r.id: r for r in RULES}

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ONELAKE_ID_PATH = re.compile(rf"(?i)(abfss://{_GUID}@|onelake\.(?:dfs|blob)\.fabric\.microsoft\.com/{_GUID})")
_SELECT_STAR = re.compile(r"(?is)\bselect\s+(?:distinct\s+|top\s+\d+\s+)?\*")
_PIP = re.compile(r"^\s*[%!]\s*pip\s+install\b(.*)$", re.MULTILINE)
# pip options that take a value, so the value isn't read as a package.
_PIP_VALUE_OPTS = {"-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url",
                   "-f", "--find-links", "-t", "--target", "--platform", "--python-version", "-e", "--editable"}
_WRITES = {"saveAsTable", "insertInto", "save", "write_deltalake"}
_VACUUM = re.compile(r"(?is)\bvacuum\b[^;]*?\bretain\s+(\d+(?:\.\d+)?)\s+hours?\b")
_SAFE_HOURS = 168


def rules() -> list[dict[str, Any]]:
    """Every notebook rule, as a governance template lists them."""
    return [{"id": r.id, "kind": "notebook", "severity": r.severity, "category": r.category, "name": r.name,
             "description": r.description, "url": url(r.id), "scheduled_only": r.scheduled_only} for r in RULES]


def url(rule_id: str) -> str:
    """The docs section that explains a rule (open_doc opens it)."""
    return f"{DOCS}#{rule_id.lower()}"


def check(sources: list[str], scheduled: bool = False,
          settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Findings for a notebook's cells (their sources, in order), worst first."""
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise ValueError("sources must be a list of cell sources")
    settings = settings or {}
    found: list[dict[str, Any] | None] = []  # _finding gives None for a rule that's turned off
    counts: dict[str, list[tuple[int, int]]] = {}
    trees: list[tuple[int, ast.AST]] = []  # each Python cell's tree, for rules that read across cells
    for n, source in enumerate(sources, 1):
        for rule_id, line in _cell(source, scheduled, counts, n, trees):
            found.append(_finding(rule_id, n, line, settings))
    parsed = {n for n, _ in trees}
    other_text = [source if n not in parsed else  # a %%sql cell, or a cell's magic and shell lines, may name a frame
                  "\n".join(line for line in source.split("\n") if line.lstrip().startswith(("%", "!")))
                  for n, source in enumerate(sources, 1)]
    for cell, line in _eager_read_kept_then_filtered(trees, other_text):
        found.append(_finding("NB015", cell, line, settings))
    for name, where in counts.items():
        if len(where) >= 3:
            found.append(_finding("NB011", where[2][0], where[2][1], settings, f"{name}.count()"))
    rows = [f for f in found if f is not None]
    rows.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["cell"], f["line"], f["rule_id"]))
    return {"counts": {s: sum(1 for f in rows if f["severity"] == s) for s in SEVERITIES}, "findings": rows}


def _finding(rule_id: str, cell: int, line: int, settings: dict[str, dict[str, Any]],
             detail: str = "") -> dict[str, Any] | None:
    rule = _BY_ID[rule_id]
    own = settings.get(rule_id)
    own = own if isinstance(own, dict) else {}
    if own.get("enabled") is False:
        return None
    severity = own.get("severity") if own.get("severity") in SEVERITIES else rule.severity
    where = f"cell {cell}, line {line}"
    return {"rule_id": rule_id, "severity": severity, "rule": rule.name, "category": rule.category,
            "object_type": "Cell", "object": f"{where}: {detail}" if detail else where, "cell": cell, "line": line,
            "description": rule.description, "url": url(rule_id)}


def _cell(source: str, scheduled: bool, counts: dict[str, list[tuple[int, int]]], n: int,
          trees: list[tuple[int, ast.AST]] | None = None) -> Iterator[tuple[str, int]]:
    for f in scan_text(source):
        yield "NB001", f["line"]
    for m in _ONELAKE_ID_PATH.finditer(source):
        yield "NB002", _line_of(source, m.start())
    for m in _PIP.finditer(source):
        line = _line_of(source, m.start())
        if scheduled:
            yield "NB008", line
        if _unpinned(m.group(1)):
            yield "NB009", line
    first = source.lstrip().split("\n", 1)[0].strip().lower()
    if first.startswith("%%") and first.split()[0] not in {"%%pyspark", "%%python"}:  # those cells are Python
        if first.startswith("%%sql") and _SELECT_STAR.search(source):
            yield "NB006", _line_of(source, _SELECT_STAR.search(source).start())  # type: ignore[union-attr]
        if first.startswith("%%sql"):
            yield from (("NB020", _line_of(source, at)) for at in _short_vacuums(source))
        return
    try:
        tree = ast.parse(_without_magics(source))
    except SyntaxError:
        return  # the editor already underlines it; nothing here can be read reliably
    if trees is not None:
        trees.append((n, tree))
    yield from _python(tree, source, scheduled, counts, n)


def _without_magics(source: str) -> str:
    """Line magics and shell lines blanked, so the cell parses and line numbers stay."""
    return "\n".join("" if line.lstrip().startswith(("%", "!")) else line for line in source.split("\n"))


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _unpinned(args: str) -> bool:
    words = args.split("#", 1)[0].split()
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        if w in _PIP_VALUE_OPTS:
            skip = True
            continue
        if w.startswith("-"):
            continue
        if "==" in w or "/" in w or "\\" in w or w.endswith((".whl", ".tar.gz", ".zip")) or w.startswith(("$", "{")):
            continue
        return True
    return False


def _python(tree: ast.AST, source: str, scheduled: bool, counts: dict[str, list[tuple[int, int]]],
            n: int) -> Iterator[tuple[str, int]]:
    in_loop: set[int] = set()
    decorators: set[int] = set()  # @udf(...) is found on its function, not again as a call
    # lf = pl.scan_parquet(...).filter(...), then lf.collect() later in the cell: lf is a Polars lazy frame
    lazy = {tgt.id for node in ast.walk(tree) if isinstance(node, ast.Assign) and _chain(node.value) & _POLARS_LAZY
            for tgt in node.targets if isinstance(tgt, ast.Name)}
    # totals = df.groupBy("r").agg(...), then totals.collect(): aggregated first, as NB003's advice says
    # Only a name every assignment of which is aggregated: one reassigned to a whole frame is collected as one.
    assigned: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            for tgt in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for ref in ast.walk(tgt):
                    if isinstance(ref, ast.Name):
                        assigned.setdefault(ref.id, []).append(node.value if isinstance(node, ast.Assign) and
                                                                isinstance(tgt, ast.Name) else ast.Constant(None))
    summed = frozenset(n for n, values in assigned.items() if all(_aggregated(v) for v in values))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_is_udf(d) for d in node.decorator_list):
            decorators.update(id(d) for d in node.decorator_list)
            yield "NB018", node.lineno
        if isinstance(node, ast.ExceptHandler) and all(isinstance(s, ast.Pass) for s in node.body):
            yield "NB012", node.lineno
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and _called(inner) in _WRITES and id(inner) not in in_loop:
                    in_loop.add(id(inner))  # a write in nested loops is one finding
                    yield "NB010", inner.lineno
        if not isinstance(node, ast.Call):
            continue
        name = _called(node)
        chain = _chain(node)
        if _row_by_row(node, name):
            yield "NB014", node.lineno
        if name == "filter" and _eager_polars_read(node):
            yield "NB015", node.lineno
        if scheduled and isinstance(node.func, ast.Name) and node.func.id == "print":
            yield "NB017", node.lineno
        if chain & {"read", "readStream"} and any(a.lower().startswith(("http://", "https://")) for a in _str_args(node)):
            yield "NB019", node.lineno
        if name in {"collect", "toPandas"} and not chain & {"limit", "head", "take", "sample", "tail"} \
                and not (name == "collect" and (chain & _POLARS_LAZY or chain & lazy)) \
                and not _aggregated(node, summed):  # a Polars lazy query's collect() runs it; few rows once aggregated
            yield "NB003", node.lineno
        elif name == "display" and scheduled and node.args and not (_chain(node.args[0]) & {"limit", "head", "sample"}):
            yield "NB004", node.lineno
        elif name in {"csv", "json", "load"} and chain & {"read", "readStream"}:
            fmt = name if name != "load" else _format(node)
            if fmt in {"csv", "json"} and "schema" not in chain:
                yield "NB005", node.lineno
        elif name == "sql" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            if _SELECT_STAR.search(node.args[0].value):
                yield "NB006", node.lineno
            if any(True for _ in _short_vacuums(node.args[0].value)):
                yield "NB020", node.lineno
        elif (_is_udf(node) and id(node) not in decorators) or (name == "register" and "udf" in _chain(node) and len(node.args) >= 2):
            yield "NB018", node.lineno
        elif name == "vacuum" and _short_hours(node):
            yield "NB020", node.lineno
        elif name in {"saveAsTable", "save"} and _overwrites(node) and not _filtered(node):
            yield "NB007", node.lineno
        elif name == "count" and not node.args and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            counts.setdefault(node.func.value.id, []).append((n, node.lineno))


def _called(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def _chain(node: ast.AST) -> set[str]:
    """The attribute names along a method chain: spark.read.schema(s).csv(p) -> {read, schema, csv, spark}."""
    names: set[str] = set()
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            names.add(node.id)
            return names
        else:
            return names


# Aggregating first: a grouped or summarised frame, or a select of aggregate functions, has few rows to bring back.
_AGGREGATING = {"agg", "groupBy", "groupby", "group_by", "rollup", "cube", "describe", "summary"}
_AGGREGATES = {"sum", "count", "countDistinct", "count_distinct", "approx_count_distinct", "avg", "mean", "min",
               "max", "stddev", "variance", "percentile_approx", "median", "first", "last"}
_EXPR_AGGREGATE = re.compile(r"(?i)\s*(?:sum|count|avg|mean|min|max|stddev|variance|percentile_approx|"
                             r"approx_count_distinct)\s*\(")
# Checked on the query's top level only (subqueries and function arguments emptied), so a GROUP BY or an aggregate
# in a subquery doesn't count, and an aggregate with OVER (...) is a window, which keeps every row.
_SQL_GROUP_BY = re.compile(r"(?is)\bgroup\s+by\b")
_SQL_SELECT_AGGREGATE = re.compile(r"(?is)^\s*select\b(?:(?!\bfrom\b).)*\b(?:sum|count|avg|min|max)\s*\x01(?!\s*over\b)")
_SQL_WINDOW = re.compile(r"(?is)\bover\s*\x01")
_EXPR_WINDOW = re.compile(r"(?is)\bover\s*\(")
# Calls after an aggregate that can bring the row count back up.
_GROWING = {"join", "crossJoin", "union", "unionAll", "unionByName", "explode", "explode_outer", "posexplode"}


def _top_level(sql: str) -> str:
    """``sql`` with each parenthesised part, innermost first, replaced by \\x01:
    SELECT a, f(b) FROM (SELECT ...) t -> SELECT a, f\\x01 FROM \\x01 t."""
    while True:
        flat = re.sub(r"\([^()]*\)", "\x01", sql)
        if flat == sql:
            return flat
        sql = flat


def _sql_aggregated(sql: str) -> bool:
    top = _top_level(sql)
    if _SQL_WINDOW.search(top):
        return False
    return bool(_SQL_GROUP_BY.search(top) or _SQL_SELECT_AGGREGATE.search(top))


def _aggregated(node: ast.AST, summed: frozenset[str] = frozenset()) -> bool:
    """Whether the frame a chain ends in is aggregated, with nothing after that can bring rows back (a join, a union,
    an explode): groupBy/agg/describe along it, a select whose every column is an aggregate function, spark.sql of a
    query with GROUP BY or aggregates in its top-level SELECT list, or a variable in ``summed``."""
    for call in _chain_calls(node):
        method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
        if method in _GROWING or any(isinstance(a, ast.Call) and _called(a) in _GROWING
                                     for arg in [*call.args, *(k.value for k in call.keywords)] for a in ast.walk(arg)):
            return False
        if method in _AGGREGATING:
            return True
        if method == "select" and call.args and all(
                isinstance(a, ast.Call) and _called(a) in _AGGREGATES for a in call.args):
            return True
        if method in {"selectExpr", "sql"} and call.args and all(
                isinstance(a, ast.Constant) and isinstance(a.value, str) and
                (_sql_aggregated(a.value) if method == "sql" else
                 _EXPR_AGGREGATE.match(a.value) and not _EXPR_WINDOW.search(a.value))
                for a in call.args):
            return True
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in summed:
            return True
    return False


def _chain_calls(node: ast.AST) -> Iterator[ast.Call]:
    while True:
        if isinstance(node, ast.Call):
            yield node
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return


def _str_args(call: ast.Call) -> list[str]:
    return [a.value for a in [*call.args, *(k.value for k in call.keywords)] if isinstance(a, ast.Constant) and isinstance(a.value, str)]


def _format(call: ast.Call) -> str:
    for c in _chain_calls(call):
        if _called(c) == "format":
            return next((s.lower() for s in _str_args(c)), "")
        if _called(c) == "load":
            fmt = next((k.value for k in c.keywords if k.arg == "format"), None)
            if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
                return fmt.value.lower()
    return ""


def _overwrites(call: ast.Call) -> bool:
    for c in _chain_calls(call):
        if _called(c) == "mode" and any(s.lower() == "overwrite" for s in _str_args(c)):
            return True
        if _called(c) in {"saveAsTable", "save"} and any(k.arg == "mode" and isinstance(k.value, ast.Constant)
                                                          and str(k.value.value).lower() == "overwrite" for k in c.keywords):
            return True
    return False


def _filtered(call: ast.Call) -> bool:
    for c in _chain_calls(call):
        if _called(c) == "option" and any(s in {"replaceWhere", "partitionOverwriteMode"} for s in _str_args(c)):
            return True
        if _called(c) == "options" and any(k.arg in {"replaceWhere", "partitionOverwriteMode"} for k in c.keywords):
            return True
        if _called(c) == "partitionBy":
            return True
    return False


def _is_udf(node: ast.AST) -> bool:
    """udf(...), F.udf(...), or a bare @udf: a scalar Python UDF. pandas_udf is vectorised, and Fabric's own
    User Data Functions (@udf.function()) aren't Spark UDFs."""
    if isinstance(node, ast.Name):
        return node.id == "udf"
    if isinstance(node, ast.Attribute):
        return node.attr == "udf" and _chain(node) & {"F", "functions", "sql", "pyspark"} != set()
    if isinstance(node, ast.Call):
        return _is_udf(node.func)
    return False


def _short_vacuums(sql: str) -> Iterator[int]:
    """Where a VACUUM keeps less than 7 days of history, as offsets into the SQL."""
    for m in _VACUUM.finditer(sql):
        if float(m.group(1)) < _SAFE_HOURS:
            yield m.start()


def _short_hours(call: ast.Call) -> bool:
    """DeltaTable.vacuum(0) or delta-rs vacuum(retention_hours=0): fewer hours than the 7-day default."""
    given = [*call.args[:1], *(k.value for k in call.keywords if k.arg in {"retentionHours", "retention_hours"})]
    return any(isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) and not isinstance(v.value, bool)
               and v.value < _SAFE_HOURS for v in given)


def _row_by_row(call: ast.Call, name: str) -> bool:
    """df.iterrows() / df.itertuples() / df.apply(f, axis=1): pandas calling Python once per row."""
    if not isinstance(call.func, ast.Attribute):
        return False
    if name in {"iterrows", "itertuples"} and not call.args:
        return True
    return name == "apply" and any(k.arg == "axis" and isinstance(k.value, ast.Constant) and k.value.value in {1, "columns"}
                                   for k in call.keywords)


_POLARS_LAZY = {"scan_csv", "scan_parquet", "scan_ndjson", "scan_ipc", "scan_delta", "lazy"}
_POLARS_READS = {"read_csv", "read_parquet", "read_ndjson", "read_ipc"}


def _is_eager_polars_read(node: ast.AST) -> bool:
    """pl.read_csv(...) (and the other eager reads), maybe followed by with_columns, select or rename."""
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"with_columns", "select", "rename"}:
        node = node.func.value
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _POLARS_READS
            and isinstance(node.func.value, ast.Name) and node.func.value.id in {"pl", "polars"})


def _eager_read_kept_then_filtered(trees: list[tuple[int, ast.AST]], other_text: list[str] | None = None,
                                   ) -> Iterator[tuple[int, int]]:
    """NB015 across lines and cells: df = pl.read_csv(...), and every use of df anywhere in the notebook is
    df.filter(...). A name bound twice (assigned, a loop or with target, an argument, an import), used any
    other way in any cell (its columns, its length, passed on), or named in a string or in ``other_text``
    (a %%sql cell, magic lines: duckdb.sql("... FROM df") reads it whole), isn't reported, because then the
    whole frame may be needed. Yields (cell, line)."""
    bound: dict[str, list[ast.AST]] = {}
    strings: list[str] = list(other_text or [])
    for _, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.setdefault(node.id, []).append(node)
            elif isinstance(node, ast.arg):
                bound.setdefault(node.arg, []).append(node)
            elif isinstance(node, ast.alias):
                bound.setdefault((node.asname or node.name).split(".")[0], []).append(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.setdefault(node.name, []).append(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
    parents = {id(child): node for _, tree in trees for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    eager = set()
    for name, nodes in bound.items():
        assign = parents.get(id(nodes[0])) if len(nodes) == 1 else None
        if isinstance(assign, ast.Assign) and len(assign.targets) == 1 and assign.targets[0] is nodes[0] \
                and _is_eager_polars_read(assign.value) \
                and not any(re.search(rf"\b{re.escape(name)}\b", text) for text in strings):
            eager.add(name)
    if not eager:
        return
    filters: dict[str, list[tuple[int, int]]] = {}
    other: set[str] = set()
    for n, tree in trees:
        parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in eager):
                continue
            attr = parents.get(id(node))
            call = parents.get(id(attr)) if attr is not None else None
            if isinstance(attr, ast.Attribute) and attr.attr == "filter" and isinstance(call, ast.Call) and call.func is attr:
                filters.setdefault(node.id, []).append((n, call.lineno))
            else:
                other.add(node.id)
    for name, where in filters.items():
        if name not in other:
            yield min(where)


def _eager_polars_read(call: ast.Call) -> bool:
    """pl.read_csv(...).filter(...) in one chain: the read loads every row first."""
    if not isinstance(call.func, ast.Attribute):
        return False
    inner = call.func.value
    while isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr in {"with_columns", "select", "rename", "filter"}:
        inner = inner.func.value
    return (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr in _POLARS_READS
            and isinstance(inner.func.value, ast.Name) and inner.func.value.id in {"pl", "polars"})
