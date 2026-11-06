"""Database-project exercises: the ``sqlproj_lab`` adapter.

A warehouse in Git is a SQL database project: one ``.sql`` file per object, laid out by schema and
object type the way Fabric's Git integration writes it (warehouse_project_service.py). The exercise's
``project`` maps those paths to files in its folder: the project as it is on ``main`` and as it is
deployed in the warehouse. The learner's answer (the exercise's ``starter``, such as ``changes.sql``)
is a change set over that project, one section per file::

    -- Comments before the first section are fine.
    -- file: dbo/Tables/FactOrders.sql
    CREATE TABLE dbo.FactOrders (OrderKey INT NOT NULL, Amount DECIMAL(18, 2));
    -- file: dbo/Views/vw_Orders.sql
    CREATE VIEW dbo.vw_Orders AS SELECT OrderKey, Amount FROM dbo.FactOrders;
    -- delete: dbo/Views/vw_Old.sql

A ``file`` section gives the file its whole new text; ``delete`` removes it. Nothing is written to
disk: the project is read as text in the engine.

Build (LOCAL VERIFIED) reads every file the way Deploy does: each must define exactly one object
(``warehouse_compare._one_definition``: nothing else may ride along) and sit where Fabric's layout puts
it; tables and views are translated from T-SQL (localdata/tsql.py) and created in an in-memory DuckDB
with no file or network access, a memory limit, locked settings and a time limit, schemas first, then
tables, then views in the order they depend on each other; every view is then read once, so a view
that names a column a table no longer has fails the build. Procedures and functions are read but not
run on this PC (the T-SQL engine doesn't run them), and the result says so. The exercise's ``seed``
(T-SQL INSERTs) loads rows after the build.

Checks (``validation``):

    project_builds    every file defines one object in its place, and every table and view builds
    objects_present   ``objects``: ``{kind, schema, name, columns?}`` must be in the project (names are
                      case-sensitive, as in a Fabric warehouse); ``columns`` must be among its columns
    deploy_safe       the deploy from the warehouse (the exercise's project) to the learner's project
                      drops nothing (unless named in ``allow_drop`` as schema.name) and changes no
                      table in a way that would rebuild it (a column removed, renamed or retyped)
    result_matches    ``query`` (T-SQL) returns the same rows on the learner's project as on the
                      reference's, both built and seeded (``ordered`` when order matters)

A pass proves the project's files are consistent and build on DuckDB on this PC. It doesn't prove a
Fabric warehouse builds or deploys them (the warehouse's own T-SQL surface and dacpac build do), and
DuckDB reads names without regard to case.

Grading material: ``grading/<id>/solution.sql`` (a change set) must pass and at least two
``wrong-*.sql`` must fail.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb

from .. import warehouse_compare as wc
from ..learn import checks
from ..learn import labs as learn_labs
from ..localdata import tsql
from ..warehouse_project_service import MAX_DEPTH, _segment_ok, default_path, defines

ADAPTER = "sqlproj_lab"
CHECKS = ("project_builds", "objects_present", "deploy_safe", "result_matches")
MAX_BYTES = 1024 * 1024        # a change set is a few KB
MAX_FILES = 500                # files in one exercise project
MAX_ROWS = 10_000              # rows result_matches compares
_MARKER = re.compile(r"^--[ \t]*(file|delete)[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
_KINDS = ("schema", "table", "view", "procedure", "function")


class Refused(ValueError):
    """A change set this lab won't read; the message says why, in words for the learner."""


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def _ok(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": True, "detail": detail}


def project_path(rel: str) -> str:
    """``rel`` as a ``.sql`` path in the project, or Refused."""
    text = str(rel).strip().replace("\\", "/")
    parts = PurePosixPath(text).parts
    if (not parts or text.startswith("/") or len(parts) > MAX_DEPTH + 1 or not all(_segment_ok(p) for p in parts)
            or not parts[-1].lower().endswith(".sql")):
        raise Refused(f"{rel!r} isn't a .sql file path in the project, such as dbo/Tables/DimCustomer.sql.")
    return "/".join(parts)


def parse(text: str) -> dict[str, str | None]:
    """The change set as ``{path: new text, or None to delete}``, in order. Raises Refused, naming the
    line, for anything it can't read."""
    raw = str(text)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise Refused(f"The change set is over {MAX_BYTES // (1024 * 1024)} MB; a change set is much smaller.")
    out: dict[str, str | None] = {}
    current: str | None = None
    body: list[str] = []

    def close() -> None:
        if current is not None:
            out[current] = "\n".join(body).strip("\n") + "\n"

    for n, line in enumerate(raw.lstrip("﻿").replace("\r\n", "\n").split("\n"), 1):
        m = _MARKER.match(line)
        if not m:
            if current is None and line.strip() and not line.strip().startswith("--"):
                raise Refused(f"Line {n}: start each file with a line like -- file: dbo/Tables/DimCustomer.sql.")
            body.append(line)
            continue
        close()
        try:
            path = project_path(m.group(2))
        except Refused as exc:
            raise Refused(f"Line {n}: {exc}") from None
        if path in out or path == current:
            raise Refused(f"Line {n}: {path} is in the change set twice.")
        if m.group(1).lower() == "delete":
            out[path] = None
            current, body = None, []
        else:
            current, body = path, []
    close()
    return out


def apply(project: dict[str, str], changes: dict[str, str | None]) -> dict[str, str]:
    """The project's files after the change set."""
    files = dict(project)
    for path, text in changes.items():
        if text is None:
            files.pop(path, None)
        else:
            files[path] = text
    if len(files) > MAX_FILES:
        raise Refused(f"The project has more than {MAX_FILES} files.")
    return files


# ---- reading and building the project

def objects(files: dict[str, str]) -> tuple[dict[tuple[str, str, str], dict[str, str]], list[str]]:
    """Every file's object keyed (kind, schema, name) as warehouse_compare keys them, and what's wrong:
    a file that defines nothing or more than its object, sits outside Fabric's layout, or repeats an
    object another file defines."""
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    problems: list[str] = []
    for path in sorted(files):
        text = files[path]
        obj = defines(text)
        if not obj:
            problems.append(f"{path} doesn't create a table, view, procedure, function or schema.")
            continue
        label = f"{obj['kind']} {obj['schema']}.{obj['name']}" if obj["kind"] != "schema" else f"schema {obj['name']}"
        if wc._one_definition(obj["kind"], text) is None:
            problems.append(f"{path} does more than define {label}: a project file holds one object and nothing else.")
        try:
            want = default_path(obj["kind"], obj["schema"], obj["name"])
        except ValueError as exc:
            problems.append(str(exc))
            want = path
        if want.lower() != path.lower():
            problems.append(f"{path} defines {label}, which Fabric's Git layout keeps at {want}.")
        key = (obj["kind"], obj["schema"].lower(), obj["name"].lower())
        if key in found:
            problems.append(f"{label} is defined twice: {found[key]['path']} and {path}.")
            continue
        found[key] = {**obj, "path": path, "text": text}
    return found, problems


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("SET enable_external_access = false")
    con.execute(f"SET memory_limit = '{learn_labs.MEMORY_LIMIT}'")
    con.execute("SET lock_configuration = true")
    return con


def _execute(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """``sql`` (T-SQL), translated and run with the lab's time limit; ValueError says why it failed."""
    try:
        statements, _ = tsql.translate(sql)
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    for s in statements:
        checks.guard(con, s)
        with learn_labs._time_limit(con) as stop:
            try:
                con.execute(s)
            except duckdb.Error as exc:
                raise ValueError(learn_labs._stopped() if stop.fired else str(exc).split("\n")[0]) from None


def build(files: dict[str, str], seed: str | None = None) -> tuple[duckdb.DuckDBPyConnection, list[dict[str, Any]], list[str]]:
    """The project built in a fresh in-memory DuckDB: the connection, one report row per file
    (``path, object, build``), and every problem in words. The caller closes the connection."""
    found, problems = objects(files)
    con = _connect()
    report = {o["path"]: {"path": o["path"], "object": f"{o['kind']} {o['schema']}.{o['name']}", "build": ""}
              for o in found.values()}
    for path in files:
        report.setdefault(path, {"path": path, "object": "", "build": "not read"})
    schemas = {"dbo"} | {o["name"] for o in found.values() if o["kind"] == "schema"} | {o["schema"] for o in found.values()}
    for s in sorted(schemas):
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{s.replace(chr(34), chr(34) * 2)}"')
    for o in found.values():
        if o["kind"] == "schema":
            report[o["path"]]["build"] = "built"
        elif o["kind"] in ("procedure", "function"):
            report[o["path"]]["build"] = "read, not run on this PC"
    tables = [o for o in found.values() if o["kind"] == "table"]
    views = [o for o in found.values() if o["kind"] == "view"]
    for o in tables:
        try:
            _execute(con, o["text"])
            report[o["path"]]["build"] = "built"
        except ValueError as exc:
            report[o["path"]]["build"] = f"failed: {exc}"
            problems.append(f"{o['path']} doesn't build: {exc}")
    waiting, why = list(views), {}
    while waiting:   # views that read other views build once those are there
        progress = False
        for o in list(waiting):
            try:
                _execute(con, o["text"])
                with learn_labs._time_limit(con):
                    con.execute(f'SELECT * FROM "{o["schema"]}"."{o["name"]}" LIMIT 0')
            except (ValueError, duckdb.Error) as exc:
                why[o["path"]] = str(exc).split("\n")[0]
                continue
            report[o["path"]]["build"] = "built"
            waiting.remove(o)
            progress = True
        if not progress:
            break
    for o in waiting:
        report[o["path"]]["build"] = f"failed: {why[o['path']]}"
        problems.append(f"{o['path']} doesn't build: {why[o['path']]}")
    if seed and not problems:
        try:
            _execute(con, seed)
        except ValueError as exc:
            problems.append(f"The exercise's rows couldn't be loaded into this project: {exc}")
    return con, sorted(report.values(), key=lambda r: r["path"]), problems


def plan(project: dict[str, str], files: dict[str, str]) -> list[dict[str, Any]]:
    """The deploy from the warehouse (the exercise's project) to the learner's files, as Deploy plans it."""
    mine, _ = objects(files)
    theirs, _ = objects(project)
    return wc.deploy_plan(wc.compare(mine, theirs))


# ---- checks

def check_problems(c: dict[str, Any]) -> list[str]:
    kind = c.get("kind")
    out = []
    if kind == "objects_present":
        objs = c.get("objects")
        if not isinstance(objs, list) or not objs:
            return ["check objects_present needs objects"]
        for n, o in enumerate(objs, 1):
            if not isinstance(o, dict) or o.get("kind") not in _KINDS or not o.get("schema") or not o.get("name"):
                out.append(f"check objects_present object {n} needs kind ({', '.join(_KINDS)}), schema and name")
            elif "columns" in o and not (isinstance(o["columns"], list) and all(isinstance(x, str) for x in o["columns"])):
                out.append(f"check objects_present object {n}: columns is a list of names")
    elif kind == "result_matches":
        if not isinstance(c.get("query"), str) or not c["query"].strip():
            out.append("check result_matches needs a query (T-SQL)")
    elif kind == "deploy_safe":
        if "allow_drop" in c and not (isinstance(c["allow_drop"], list) and all(isinstance(x, str) for x in c["allow_drop"])):
            out.append("check deploy_safe: allow_drop is a list of schema.name")
    return out


def spec_problems(eid: str, ex: dict[str, Any]) -> list[str]:
    project = ex.get("project")
    if not isinstance(project, dict) or not project:
        return [f"{eid}: a sqlproj_lab exercise needs project, its files by path in the project"]
    names = set(ex.get("files") or [])
    out = []
    for rel, source in project.items():
        try:
            project_path(rel)
        except Refused as exc:
            out.append(f"{eid}: project {exc}")
        if source not in names:
            out.append(f"{eid}: project takes {source!r}, which isn't listed in files")
    if ex.get("seed") and ex["seed"] not in names:
        out.append(f"{eid}: seed {ex['seed']!r} isn't listed in files")
    return out


def load_project(exercise_folder: Path, ex: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    project = {project_path(rel): (exercise_folder / str(src)).read_text(encoding="utf-8")
               for rel, src in (ex.get("project") or {}).items()}
    seed = (exercise_folder / str(ex["seed"])).read_text(encoding="utf-8") if ex.get("seed") else None
    return project, seed


def _rows(con: duckdb.DuckDBPyConnection, query: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    statements, _ = tsql.translate(query)
    if len(statements) != 1:
        raise ValueError("The check's query is one SELECT.")
    checks.guard(con, statements[0])
    with learn_labs._time_limit(con) as stop:
        try:
            cur = con.execute(statements[0])
        except duckdb.Error as exc:
            raise ValueError(learn_labs._stopped() if stop.fired else str(exc).split("\n")[0]) from None
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    return cols, [tuple(learn_labs._cell(v) for v in r) for r in rows[:MAX_ROWS]]


def _check(c: dict[str, Any], con: duckdb.DuckDBPyConnection, found: dict[tuple[str, str, str], dict[str, str]],
           problems: list[str], project: dict[str, str], files: dict[str, str],
           reference: tuple[list[str], list[tuple[Any, ...]]] | None) -> dict[str, Any]:
    kind = str(c["kind"])
    if kind == "project_builds":
        if problems:
            more = f" ({len(problems) - 1} more)" if len(problems) > 1 else ""
            return _no(kind, problems[0] + more)
        return _ok(kind, f"All {len(files)} files define one object each, in place, and build.")
    if kind == "objects_present":
        for o in c["objects"]:
            label = f"{o['kind']} {o['schema']}.{o['name']}"
            got = next((v for k, v in found.items() if k[0] == o["kind"] and v["schema"] == o["schema"]
                        and v["name"] == o["name"]), None)
            if not got:
                return _no(kind, f"The project has no {label}.")
            if o.get("columns"):
                try:
                    cur = con.execute(f'SELECT * FROM "{o["schema"]}"."{o["name"]}" LIMIT 0')
                    have = [d[0] for d in cur.description or []]
                except duckdb.Error:
                    return _no(kind, f"{label} doesn't build, so its columns can't be read.")
                missing = [x for x in o["columns"] if x not in have]
                if missing:
                    return _no(kind, f"{label} has no column{'s' if len(missing) > 1 else ''} {', '.join(missing)}.")
        return _ok(kind, f"The project has all {len(c['objects'])} objects.")
    if kind == "deploy_safe":
        allowed = set(c.get("allow_drop") or [])
        for step in plan(project, files):
            name = f"{step['schema']}.{step['name']}"
            if step["action"] == "drop" and name not in allowed:
                return _no(kind, f"Deploying would drop {step['kind']} {name}, which the warehouse has. Keep its file, "
                                 "or remove it on purpose in a separate, reviewed change.")
            if step["kind"] == "table" and step["action"] == "add_columns" and not step["offered"]:
                return _no(kind, f"Deploying would rebuild table {name}: {', '.join(step['detail']) or step['why']}. "
                                 "A column that is removed, renamed or retyped breaks what reads it.")
        return _ok(kind, "The deploy drops nothing and rebuilds no table.")
    if kind == "result_matches":
        if problems:
            return _no(kind, "The project doesn't build, so the query can't run on it.")
        if reference is None:
            return _no(kind, "The reference project doesn't build; this exercise needs fixing.")
        try:
            cols, rows = _rows(con, c["query"])
        except ValueError as exc:
            return _no(kind, f"The check's query failed on your project: {exc}")
        want_cols, want = reference
        if [x.lower() for x in cols] != [x.lower() for x in want_cols]:
            return _no(kind, f"The query returns columns {', '.join(cols)}; expected {', '.join(want_cols)}.")
        same = rows == want if c.get("ordered") else sorted(map(repr, rows)) == sorted(map(repr, want))
        if not same:
            return _no(kind, f"The query returns {len(rows)} rows that differ from the expected {len(want)}.")
        return _ok(kind, f"The query returns the expected {len(want)} rows.")
    return _no(kind, f"A database project exercise can't check {kind}.")


def _reference(exercise_folder: Path, grading: Path, ex: dict[str, Any]) -> dict[str, tuple[list[str], list[tuple[Any, ...]]] | None]:
    """Each result_matches query's rows on the reference project, built and seeded."""
    queries = [c["query"] for c in ex["validation"] if c.get("kind") == "result_matches"]
    if not queries:
        return {}
    project, seed = load_project(exercise_folder, ex)
    files = apply(project, parse((grading / "solution.sql").read_text(encoding="utf-8")))
    con, _, problems = build(files, seed)
    try:
        return {q: None if problems else _rows(con, q) for q in queries}
    finally:
        con.close()


def grade(text: str, exercise_folder: Path, grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """Every check of ``ex`` on the change set ``text``. A change set this lab can't read fails every
    check with the same plain reason."""
    project, seed = load_project(exercise_folder, ex)
    try:
        files = apply(project, parse(text))
    except Refused as exc:
        results = [_no(str(c["kind"]), str(exc)) for c in ex["validation"]]
        return {"passed": False, "results": results}
    reference = _reference(exercise_folder, grading, ex)
    found, _ = objects(files)
    con, _, problems = build(files, seed)
    try:
        results = [_check(c, con, found, problems, project, files, reference.get(c.get("query", ""))) for c in ex["validation"]]
    finally:
        con.close()
    return {"passed": bool(results) and all(r["passed"] for r in results), "results": results}


def run(exercise_folder: Path, ex: dict[str, Any], text: str, limit: int = 200) -> dict[str, Any]:
    """What Run shows: every file of the project after the change set, the object it defines, whether it
    built, and what a deploy from the warehouse would do with it. Nothing is checked here."""
    project, seed = load_project(exercise_folder, ex)
    files = apply(project, parse(text))
    con, report, problems = build(files, seed)
    con.close()
    steps = {s["path"]: s for s in plan(project, files) if s.get("path")}
    drops = [s for s in plan(project, files) if s["action"] == "drop"]
    rows = []
    for r in report:
        s = steps.get(r["path"])
        deploy = "" if not s else (f"{s['action']}: {s['why']}" if s["offered"] else f"not offered: {s['why']}")
        rows.append([r["path"], r["object"], r["build"], deploy])
    rows += [["(deleted)", f"{s['kind']} {s['schema']}.{s['name']}", "", f"drop: {s['why']}"] for s in drops]
    rows += [["(problem)", "", p, ""] for p in problems if not p.split(" ", 1)[0] in files]
    return {"columns": ["file", "object", "build", "deploy"], "rows": rows[:limit], "more": len(rows) > limit}


def self_test(exercise_folder: Path, grading: Path, ex: dict[str, Any]) -> dict[str, Any]:
    """The reference (``grading/<id>/solution.sql``) must pass and every ``wrong-*.sql`` fail."""
    return {"solution": grade((grading / "solution.sql").read_text(encoding="utf-8"), exercise_folder, grading, ex),
            "wrong": {p.name: grade(p.read_text(encoding="utf-8"), exercise_folder, grading, ex)
                      for p in sorted(grading.glob("wrong-*.sql"))}}

