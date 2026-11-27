"""The datasets and flows a Lakeflow pipeline's source files define, and the datasets they read, found on
this PC (Preview). Python files are parsed with ``ast`` and SQL files are scanned as text: nothing is run,
imported or sent anywhere. databricks_bundle_check calls this for each pipeline in a bundle.

Sources (docs.databricks.com/aws/en/ldp/developer/..., read 2026-11-26):
- ldp-sql-ref-create-streaming-table: ``CREATE [OR REFRESH] [PRIVATE] STREAMING TABLE table_name ...
  [ {flow_clause | AS query} ]``; "Private streaming tables were previously created with the TEMPORARY
  parameter"; "The fully qualified table name must be unique."
- ldp-sql-ref-create-materialized-view: ``CREATE [OR REFRESH] [PRIVATE] MATERIALIZED VIEW view_name ... AS
  query``; "The fully qualified view name must be unique."
- ldp-sql-ref-create-temporary-view and ldp-sql-ref-create-view: ``CREATE TEMPORARY VIEW view_name`` and
  ``CREATE VIEW view_name``; "Views cannot have streaming queries, or be used as a streaming source."
- ldp-sql-ref-create-flow: ``CREATE FLOW flow_name [COMMENT comment] AS`` then ``AUTO CDC [ONCE] INTO
  target_table`` or ``INSERT [ONCE] INTO target_table BY NAME``; of target_table: "This must be a
  Streaming table."
- ldp-sql-ref-create-table-flow (Beta): ``CREATE TABLE table_name ... FLOW INSERT [ONCE] BY NAME query``.
- flow-examples: "You cannot reuse a flow name in a pipeline"; a streaming table's default flow "has the
  same name as the target"; several flows may append to one streaming table.
- python-ref: ``from pyspark import pipelines as dp``; "The pipelines module was previously called dlt";
  ``@dp.table``, ``@dp.materialized_view``, ``@dp.temporary_view``, ``@dp.append_flow(target=...)``,
  ``dp.create_streaming_table(...)``, ``dp.create_auto_cdc_flow`` (``dlt.apply_changes``); reads with
  ``spark.read.table`` and ``spark.readStream.table`` (and the older ``dlt.read`` and ``dlt.read_stream``).
  Its examples name a dataset with ``name=`` or, without it, by the function's name.

What the pages don't say isn't checked: how an unqualified name that no file here defines resolves (it
may be a table in the catalog), whether datasets may read each other in a cycle, and whether a
dataset name may differ only in case. Names are compared as written, after removing backticks, and
without regard to case, as Unity Catalog names are."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

STREAMING_TABLE = "streaming table"
MATERIALIZED_VIEW = "materialized view"
VIEW = "view"
TEMPORARY_VIEW = "temporary view"
TABLE = "table"
SINK = "sink"

ERROR, WARNING = "error", "warning"


@dataclass(frozen=True)
class Defined:
    name: str      # as written, without backticks
    kind: str
    file: str
    line: int


@dataclass(frozen=True)
class Flow:
    name: str
    target: str
    file: str
    line: int
    default: bool = False  # a streaming table's own flow (AS query or FLOW clause), named as the table
    named: bool = True     # False: no name= was given, and the pages don't say what the flow is called


@dataclass(frozen=True)
class Read:
    name: str
    stream: bool
    file: str
    line: int


@dataclass(frozen=True)
class Problem:
    severity: str
    file: str
    line: int
    message: str


@dataclass
class Sources:
    datasets: list[Defined] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)
    reads: list[Read] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        known = {_key(d.name) for d in self.datasets}
        return {
            "datasets": [vars(d) for d in self.datasets],
            "flows": [vars(f) for f in self.flows],
            "reads": [{**vars(r), "defined_here": _key(r.name) in known} for r in self.reads],
        }


def _key(name: str) -> str:
    return name.replace("`", "").lower()


# ---------------------------------------------------------------- SQL

_NAME = r"(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)){0,2}"
_QUALIFIED = re.compile(_NAME)
_CREATE = re.compile(
    r"\bCREATE\s+(?:OR\s+REFRESH\s+)?(?P<scope>PRIVATE\s+|TEMPORARY\s+)?"
    r"(?P<kind>STREAMING\s+TABLE|MATERIALIZED\s+VIEW|VIEW|TABLE)\s+(?P<name>" + _NAME + r")", re.I)
_FLOW = re.compile(r"\bCREATE\s+FLOW\s+(?P<name>" + _NAME + r")\b.*?\bAS\s+(?:AUTO\s+CDC|INSERT)\s+(?:ONCE\s+)?"
                   r"INTO\s+(?P<target>" + _NAME + r")", re.I | re.S)
_OWN_FLOW = re.compile(r"\bFLOW\s+(?:INSERT|AUTO\s+CDC|REPLACE)\b|\bAS\s*(?:\(\s*)?(?:SELECT|WITH|FROM)\b", re.I)
_STREAM_READ = re.compile(r"\bSTREAM\s*\(\s*(?P<name>" + _NAME + r")\s*\)", re.I)
_READ = re.compile(r"\b(?:FROM|JOIN)\s+(?P<name>" + _NAME + r")(?P<call>\s*\()?", re.I)
_NOT_A_TABLE = frozenset({"stream", "select", "values", "lateral", "unnest"})


def _blank_comments_and_strings(text: str) -> str:
    """The text with comments and the insides of quoted strings replaced by spaces, keeping every newline
    so line numbers still match."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(c + re.sub(r"[^\n]", " ", text[i + 1:j - 1]) + (c if j - 1 > i else ""))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _sql(text: str, file: str, found: Sources) -> None:
    clean = _blank_comments_and_strings(text)
    start = 0
    for statement in re.split(r"(;)", clean) + [""]:
        if statement == ";":
            start += 1
            continue
        line0 = clean.count("\n", 0, start) + 1

        def line(offset: int, _s: str = statement, _l: int = line0) -> int:
            return _l + _s.count("\n", 0, offset)

        flow = _FLOW.search(statement)
        if flow:
            found.flows.append(Flow(_unquote(flow["name"]), _unquote(flow["target"]), file, line(flow.start())))
        else:
            made = _CREATE.search(statement)
            if made:
                scope = (made["scope"] or "").strip().upper()
                kind = re.sub(r"\s+", " ", made["kind"]).lower()
                kind = TEMPORARY_VIEW if kind == VIEW and scope == "TEMPORARY" else kind
                name = _unquote(made["name"])
                found.datasets.append(Defined(name, kind, file, line(made.start())))
                if kind in (STREAMING_TABLE, TABLE) and _OWN_FLOW.search(statement, made.end()):
                    found.flows.append(Flow(name, name, file, line(made.start()), default=True))
        streamed = set()
        for m in _STREAM_READ.finditer(statement):
            streamed.add(m.start("name"))
            found.reads.append(Read(_unquote(m["name"]), True, file, line(m.start())))
        for m in _READ.finditer(statement):
            if m["call"] or m.start("name") in streamed or m["name"].lower() in _NOT_A_TABLE:
                continue  # a function such as read_files(...), or STREAM(...) already counted
            found.reads.append(Read(_unquote(m["name"]), False, file, line(m.start())))
        start += len(statement)


def _unquote(name: str) -> str:
    return re.sub(r"\s*\.\s*", ".", name).replace("`", "")


# ---------------------------------------------------------------- Python

_MODULES = ("dp", "dlt", "pipelines")
_DEFINE = {"table": STREAMING_TABLE, "materialized_view": MATERIALIZED_VIEW, "temporary_view": TEMPORARY_VIEW,
           "view": VIEW}
# dlt.table made a streaming table or a materialized view, as its query reads a stream or not.
_DLT_DEFINE = {**_DEFINE, "table": TABLE}
_FLOWS = ("append_flow",)
_STREAMING_FUNCTIONS = ("create_streaming_table",)
_CDC = ("create_auto_cdc_flow", "create_auto_cdc_from_snapshot_flow", "apply_changes",
        "apply_changes_from_snapshot")


def _attr(node: ast.AST) -> str | None:
    """``dp.table`` (or ``dlt.table``) as "table"; None for anything else."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in _MODULES:
        return node.attr
    return None


def _text_arg(call: ast.Call, keyword: str, position: int | None = 0) -> str | None:
    for k in call.keywords:
        if k.arg == keyword:
            return k.value.value if isinstance(k.value, ast.Constant) and isinstance(k.value.value, str) else None
    if position is not None and len(call.args) > position:
        arg = call.args[position]
        return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
    return None


def _has_keyword(call: ast.Call, keyword: str) -> bool:
    return any(k.arg == keyword for k in call.keywords)


def _python(text: str, file: str, found: Sources) -> None:
    # A Databricks source notebook's own lines (# Databricks notebook source, # COMMAND ---, # MAGIC) are
    # comments, so it parses as Python.
    try:
        tree = ast.parse(text, filename=file)
    except (SyntaxError, ValueError) as exc:
        found.problems.append(Problem(WARNING, file, getattr(exc, "lineno", None) or 1,
                                      f"{file} couldn't be read as Python, so its datasets aren't checked "
                                      f"({exc.__class__.__name__})."))
        return
    marks = (len(found.datasets), len(found.flows), len(found.reads))
    _python_nodes(tree, file, found)
    # ast.walk goes breadth first; each list is put back in line order
    found.datasets[marks[0]:] = sorted(found.datasets[marks[0]:], key=lambda d: d.line)
    found.flows[marks[1]:] = sorted(found.flows[marks[1]:], key=lambda f: f.line)
    found.reads[marks[2]:] = sorted(found.reads[marks[2]:], key=lambda r: r.line)


def _python_nodes(tree: ast.AST, file: str, found: Sources) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                what = _attr(call.func if call else dec)
                if what in _DEFINE:
                    name = _text_arg(call, "name", None) if call else None
                    if call and name is None and _has_keyword(call, "name"):
                        continue  # a name worked out at run time isn't known here
                    module = (call.func if call else dec).value.id  # type: ignore[attr-defined]
                    kind = (_DLT_DEFINE if module == "dlt" else _DEFINE)[what]
                    found.datasets.append(Defined(name or node.name, kind, file, dec.lineno))
                    if kind in (STREAMING_TABLE, TABLE):
                        found.flows.append(Flow(name or node.name, name or node.name, file, dec.lineno, True))
                elif what in _FLOWS and call:
                    target = _text_arg(call, "target")
                    name = _text_arg(call, "name", None)
                    if target is not None:
                        found.flows.append(Flow(name or node.name, target, file, dec.lineno, named=name is not None))
        elif isinstance(node, ast.Call):
            what = _attr(node.func)
            if what in _STREAMING_FUNCTIONS:
                name = _text_arg(node, "name")
                if name is not None:
                    found.datasets.append(Defined(name, STREAMING_TABLE, file, node.lineno))
            elif what in _CDC:
                target = _text_arg(node, "target")
                name = _text_arg(node, "name", None)
                if target is not None:
                    found.flows.append(Flow(name or target, target, file, node.lineno, named=name is not None))
            elif what in ("read", "read_stream"):
                name = _text_arg(node, "name")
                if name is not None:
                    found.reads.append(Read(name, what == "read_stream", file, node.lineno))
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "table":
                reader = node.func.value  # spark.read.table, spark.readStream.table, spark.table
                stream = isinstance(reader, ast.Attribute) and reader.attr == "readStream"
                plain = (isinstance(reader, ast.Attribute) and reader.attr == "read") or (
                    isinstance(reader, ast.Name) and reader.id == "spark")
                name = _text_arg(node, "tableName")
                if (stream or plain) and name is not None:
                    found.reads.append(Read(name, stream, file, node.lineno))


# ---------------------------------------------------------------- reading and checking


def read(files: list[tuple[Path, str]], max_bytes: int) -> Sources:
    """``files`` are (path on this PC, name to show). .sql files are scanned as SQL and .py files parsed
    as Python; anything else is skipped. A file over ``max_bytes`` isn't read."""
    found = Sources()
    for path, shown in files:
        suffix = path.suffix.lower()
        if suffix not in (".sql", ".py"):
            continue
        try:
            if path.stat().st_size > max_bytes:
                found.problems.append(Problem(WARNING, shown, 1, f"{shown} is over {max_bytes:,} bytes, so its "
                                                                 "datasets aren't checked."))
                continue
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            found.problems.append(Problem(WARNING, shown, 1, f"{shown} couldn't be read ({exc.strerror or exc})."))
            continue
        (_sql if suffix == ".sql" else _python)(text, shown, found)
    return found


def check(found: Sources, complete: bool = True) -> list[Problem]:
    """The problems the pages above name, in the pipeline these sources make up. ``complete`` is False when
    some of the pipeline's source isn't on this PC (a workspace path, a file that couldn't be read): then a
    flow into a table no file here defines isn't reported."""
    problems = list(found.problems)
    first: dict[str, Defined] = {}
    for d in found.datasets:
        seen = first.get(_key(d.name))
        if seen is None:
            first[_key(d.name)] = d
            continue
        problems.append(Problem(ERROR, d.file, d.line, f"{d.name} is defined again here ({d.kind}); it's already "
                                f"a {seen.kind} at {seen.file}:{seen.line}. A dataset's name must be unique."))
    flows: dict[str, Flow] = {}
    for f in found.flows:
        seen_flow = flows.get(_key(f.name)) if f.named else None
        if f.named and seen_flow is None:
            flows[_key(f.name)] = f
        elif seen_flow is not None and not (f.default and seen_flow.default):  # a table defined twice is already an error above
            problems.append(Problem(ERROR, f.file, f.line, f"The flow name {f.name} is used again here (first at "
                                    f"{seen_flow.file}:{seen_flow.line}). A flow name can't be reused in a "
                                    "pipeline; a streaming table's own flow has the table's name."))
        if f.default:
            continue
        target = first.get(_key(f.target))
        if target is not None and target.kind in (MATERIALIZED_VIEW, VIEW, TEMPORARY_VIEW):
            problems.append(Problem(ERROR, f.file, f.line, f"The flow {f.name} writes into {f.target}, a "
                                    f"{target.kind} ({target.file}:{target.line}). A flow's target must be a "
                                    "streaming table."))
        elif target is None and complete and not found.problems:
            problems.append(Problem(WARNING, f.file, f.line, f"The flow {f.name} writes into {f.target}, which no "
                                    "file of this pipeline defines. Define it as a streaming table first, for "
                                    f"example CREATE OR REFRESH STREAMING TABLE {f.target}."))
    for r in found.reads:
        source = first.get(_key(r.name))
        if r.stream and source is not None and source.kind in (VIEW, TEMPORARY_VIEW):
            problems.append(Problem(ERROR, r.file, r.line, f"{r.name} is read as a stream here, but it's a "
                                    f"{source.kind} ({source.file}:{source.line}). A view can't be used as a "
                                    "streaming source."))
    return problems
