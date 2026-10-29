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
    found: list[dict[str, Any]] = []
    counts: dict[str, list[tuple[int, int]]] = {}
    for n, source in enumerate(sources, 1):
        for rule_id, line in _cell(source, scheduled, counts, n):
            found.append(_finding(rule_id, n, line, settings))
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


def _cell(source: str, scheduled: bool, counts: dict[str, list[tuple[int, int]]], n: int) -> Iterator[tuple[str, int]]:
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
        return
    try:
        tree = ast.parse(_without_magics(source))
    except SyntaxError:
        return  # the editor already underlines it; nothing here can be read reliably
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
    for node in ast.walk(tree):
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
        if name in {"collect", "toPandas"} and not chain & {"limit", "head", "take", "sample", "tail"}:
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
