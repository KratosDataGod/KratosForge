"""SQL transformations in a local project (FD-LDE-006): SQL files run in dependency order, each
step's output kept as a table to look at.

A project's steps are the files in ``<project>/transforms/<schema>/<name>.sql``. Each holds one
query (``SELECT ...`` or ``WITH ... SELECT``), and running it makes the table ``<schema>.<name>``
from its rows, replacing the one the last run made. So after a run every step's output is in the
project, to query, compare or write on. A step that reads another step's table runs after it: the
order comes from the tables each query reads (a cycle is refused), plus any ``-- depends_on:`` line.

Settings are comment lines at the top of a file:

    -- dialect: spark                  DuckDB SQL unless this says spark
    -- materialize: delta              a Delta table under Tables/ (Fabric-safe), instead of a table
    -- depends_on: dbo.calendar        runs after these steps, even if the query doesn't name them
    -- default: region = "EU"          a parameter's value when the run doesn't give one (JSON)

Parameters are ``$name`` in DuckDB SQL and ``:name`` or ``${name}`` in Spark SQL. They are bound as
values, never pasted into the SQL, so a value can't change what a query does.

A step only ever replaces a table a transformation made; an existing table or view with its name
that a person made, a registered folder's table or a Delta table written another way is left
alone and the step fails saying so. Deleting a step's file leaves its table as it is.

Each run is kept in ``transforms/.runs/`` (the last 50): every step with its status, rows, time and
error. A run stops at the first failing step; the steps after it are reported as skipped, and the
tables from the steps before it are kept.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import sqlglot
from sqlglot import exp

from .. import jsonfile, sql_dialects
from . import delta

__all__ = ["Step", "Transforms"]

_SETTING = re.compile(r"^\s*--\s*(dialect|materialize|depends_on|default)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_DEFAULT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")
_KEEP_RUNS = 50


@dataclass
class Step:
    name: str                       # schema.table, as the file's place says
    schema: str
    table: str
    path: str
    sql: str
    dialect: str = "duckdb"
    materialize: str = "table"      # table | delta
    depends_on: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    error: str = ""

    def about(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "dialect": self.dialect, "materialize": self.materialize,
                "depends_on": self.depends_on, "reads": self.reads, "defaults": self.defaults, "error": self.error}


class Transforms:
    """The steps of one project, and running them on its engine."""

    def __init__(self, engine: Any, check: Callable[[str, str], None] | None = None):
        self.engine = engine
        self.check = check  # raises for a query a managed local run would refuse (localdata_service)
        self.root: Path = engine.project.root / "transforms"

    # Reading the steps

    def steps(self) -> list[Step]:
        found: list[Step] = []
        if not self.root.is_dir():
            return found
        for path in sorted(self.root.glob("*/*.sql")):
            schema, table = path.parent.name, path.stem
            step = Step(f"{schema}.{table}".lower(), schema, table, str(path), "")
            try:
                delta.check_name(schema, table)
                self._read(step, path.read_text(encoding="utf-8"))
            except (ValueError, OSError, UnicodeDecodeError) as exc:
                step.error = str(exc)
            found.append(step)
        return found

    def _read(self, step: Step, text: str) -> None:
        for line in text.splitlines():
            if not line.strip():
                continue
            m = _SETTING.match(line)
            if not m:
                if line.lstrip().startswith("--"):
                    continue
                break
            key, value = m[1].lower(), m[2]
            if key == "dialect":
                if value.lower() not in ("duckdb", "spark"):
                    raise ValueError(f"dialect must be duckdb or spark, not {value!r}.")
                step.dialect = value.lower()
            elif key == "materialize":
                if value.lower() not in ("table", "delta"):
                    raise ValueError(f"materialize must be table or delta, not {value!r}.")
                step.materialize = value.lower()
            elif key == "depends_on":
                step.depends_on += [_key(v) for v in value.split(",") if v.strip()]
            else:
                d = _DEFAULT.match(value)
                if not d:
                    raise ValueError(f"A default is written name = value, not {value!r}.")
                try:
                    step.defaults[d[1]] = json.loads(d[2])
                except json.JSONDecodeError:
                    raise ValueError(f"The default for {d[1]} isn't a JSON value (strings in double quotes): "
                                     f"{d[2]}") from None
        step.sql = text.strip().rstrip(";").strip()
        if not step.sql:
            raise ValueError("The file has no query.")
        try:
            trees = [t for t in sqlglot.parse(step.sql, read=step.dialect) if t is not None]
        except sqlglot.errors.ParseError as exc:
            raise ValueError(f"Could not read the query: {sql_dialects.parse_problem(exc)}") from None
        if len(trees) != 1 or not isinstance(trees[0], exp.Query):
            raise ValueError("A step is one query (SELECT ..., WITH ... SELECT ...), with no other statements; "
                             "its rows become the step's table.")
        tree = trees[0]
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        reads = []
        for t in tree.find_all(exp.Table):
            if not t.name or (not t.db and t.name.lower() in ctes):
                continue
            reads.append(_key(f"{t.db}.{t.name}" if t.db else t.name))
        step.reads = sorted(set(reads))

    def plan(self) -> dict[str, Any]:
        """The steps in the order a run takes them, with what each reads and any problems."""
        steps = self.steps()
        order, problem = _order(steps)
        by_name = {s.name: s for s in steps}
        return {"folder": str(self.root), "order": order, "problem": problem,
                "steps": [by_name[n].about() | {"after": _after(by_name[n], by_name)} for n in order]
                + [s.about() for s in steps if s.name not in order]}

    # Running them

    def run(self, params: dict[str, Any] | None = None, only: list[str] | None = None,
            dry_run: bool = False) -> dict[str, Any]:
        """Run the steps in order. ``only``: these steps and every step after them that reads their
        tables (the steps before are taken as built). ``dry_run`` checks every query against the
        project as it is and makes nothing."""
        self.engine.open()
        steps = self.steps()
        by_name = {s.name: s for s in steps}
        broken = [s for s in steps if s.error]
        if broken:
            raise ValueError("Fix these steps first: " + "; ".join(f"{s.name}: {s.error}" for s in broken))
        order, problem = _order(steps)
        if problem:
            raise ValueError(problem)
        if only:
            chosen = {_key(n) for n in only}
            unknown = sorted(chosen - set(by_name))
            if unknown:
                raise ValueError(f"No step named {', '.join(unknown)}.")
            for name in order:  # in order, so downstream steps follow their inputs
                if set(_after(by_name[name], by_name)) & chosen:
                    chosen.add(name)
            order = [n for n in order if n in chosen]
        state = self._state()
        run = {"run_id": uuid.uuid4().hex, "started_at": _now(), "dry_run": dry_run, "params": sorted(params or {}),
               "status": "ok", "steps": []}
        failed = False
        for name in order:
            step = by_name[name]
            item = {"name": name, "materialize": step.materialize, "status": "skipped", "rows": None,
                    "seconds": None, "error": ""}
            run["steps"].append(item)
            if failed:
                continue
            values = step.defaults | (params or {})
            started = time.monotonic()
            try:
                self._may_replace(step, state)
                if self.check is not None:
                    self.check(step.sql, step.dialect)
                if dry_run:
                    waits = self._check(step, values, by_name)
                    item["status"] = "checked" if not waits else "not checked"
                    if waits:
                        item["error"] = f"Reads {', '.join(waits)}, which this run makes first; checked when it runs."
                elif step.materialize == "delta":
                    r = self.engine.write_delta(step.name, step.sql, mode="overwrite", dialect=step.dialect,
                                                schema_mode="overwrite", params=values)
                    item.update(status="ok", rows=r["rows"], version=r["version"])
                else:
                    r = self.engine.make_table(step.name, step.sql, dialect=step.dialect, params=values)
                    item.update(status="ok", rows=r["rows"])
                if not dry_run:
                    state["tables"][step.name] = {"materialize": step.materialize, "mark": self._mark(step)}
                    self._save_state(state)
            except Exception as exc:  # noqa: BLE001 - a failing step is reported, and the run stops
                item.update(status="failed", error=str(exc))
                run["status"] = "failed"
                failed = True
            item["seconds"] = round(time.monotonic() - started, 3)
        run["finished_at"] = _now()
        if not dry_run:
            self._keep(run)
        return run

    def _may_replace(self, step: Step, state: dict[str, Any]) -> None:
        """A step replaces only the table a transformation made, or makes a new one. What a run made is
        checked against the table itself, not only .state.json: a table someone made since with the
        same name, or a Delta table written since, is left alone."""
        made = state["tables"].get(step.name)
        if not isinstance(made, dict):
            made = None  # an entry this build can't check is treated as unknown
        if made is not None and made.get("materialize") != step.materialize:
            kind = "Delta table" if made.get("materialize") == "delta" else "table"
            raise ValueError(f"{step.name} was made as a {kind} by an earlier run. Drop it first to change how "
                             "it's kept.")
        if made is not None and (not self._built(step) or self._current_mark(step) == made.get("mark")):
            return
        if made is not None:
            raise ValueError(f"{step.name} has changed since a transformation made it (it was replaced or written "
                             "to another way), so it isn't replaced. Drop it, or rename the step.")
        engine = self.engine
        if step.materialize == "delta" and engine._is_own(step.schema, step.table):
            raise ValueError(f"{step.name} is a Delta table that wasn't made by a transformation, so it isn't "
                             "replaced. Rename the step or the table.")
        if step.materialize == "table" and engine._exists(step.schema, step.table):
            raise ValueError(f"{step.name} already exists and wasn't made by a transformation, so it isn't "
                             "replaced. Rename the step or the table.")

    def _check(self, step: Step, values: dict[str, Any], by_name: dict[str, Step]) -> list[str]:
        """Plan the query against the project as it is, reading no rows. Returns the earlier steps it
        reads that aren't built yet (then it can't be checked)."""
        unbuilt = [r for r in step.reads if r in by_name and not self._built(by_name[r])]
        if unbuilt:
            return unbuilt
        self.engine.execute(f"SELECT * FROM ({step.sql}) LIMIT 0" if step.dialect == "duckdb" else
                            f"SELECT * FROM ({step.sql}) AS q LIMIT 0", dialect=step.dialect, limit=0,
                            count=False, params=values)
        return []

    def _mark(self, step: Step) -> str:
        """Mark the table a step just made, and return the mark. A database table gets a comment (which
        CREATE OR REPLACE by anyone else clears); a Delta table is known by its version."""
        if step.materialize == "delta":
            return f"version:{self._current_version(step)}"
        mark = "fd-transform:" + uuid.uuid4().hex
        self.engine.execute(f"COMMENT ON TABLE {_qname(step.schema, step.table)} IS '{mark}'", count=False)
        return mark

    def _current_mark(self, step: Step) -> str | None:
        if step.materialize == "delta":
            return f"version:{self._current_version(step)}"
        rows = self.engine.execute(
            "SELECT comment FROM duckdb_tables() WHERE lower(schema_name) = lower('%s') AND lower(table_name) = "
            "lower('%s') AND database_name = current_database()" % (_lit(step.schema), _lit(step.table)),
            count=False)["rows"]
        return rows[0][0] if rows else None

    def _current_version(self, step: Step) -> int | None:
        history = self.engine.delta_history(step.name, 1).get("versions") or []
        return history[0]["version"] if history else None

    def _built(self, step: Step) -> bool:
        if step.materialize == "delta":
            return self.engine._is_own(step.schema, step.table)
        return self.engine._exists(step.schema, step.table)

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        folder = self.root / ".runs"
        if not folder.is_dir():
            return []
        files = sorted(folder.glob("*.json"), reverse=True)[:max(1, min(int(limit), _KEEP_RUNS))]
        out = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    # Files

    def _state(self) -> dict[str, Any]:
        try:
            data = json.loads((self.root / ".state.json").read_text(encoding="utf-8"))
            if isinstance(data.get("tables"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"tables": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json(self.root / ".state.json", state)

    def _keep(self, run: dict[str, Any]) -> None:
        folder = self.root / ".runs"
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{run['run_id'][:8]}.json", run)
        for old in sorted(folder.glob("*.json"))[:-_KEEP_RUNS]:
            old.unlink(missing_ok=True)


def _key(name: str) -> str:
    schema, table = delta.split_name(name.strip())
    return f"{schema or 'dbo'}.{table}".lower()


def _after(step: Step, by_name: dict[str, Step]) -> list[str]:
    """The steps ``step`` runs after."""
    return sorted({r for r in step.reads + step.depends_on if r in by_name and r != step.name})


def _order(steps: list[Step]) -> tuple[list[str], str]:
    """The steps in dependency order (by name where free to choose), and a problem if there is a cycle
    or a depends_on names no step."""
    by_name = {s.name: s for s in steps if not s.error}
    for s in by_name.values():
        missing = [d for d in s.depends_on if d not in by_name]
        if missing:
            return [], f"{s.name} depends on {', '.join(missing)}, which isn't a step."
    waiting = {n: set(_after(s, by_name)) for n, s in by_name.items()}
    order: list[str] = []
    while waiting:
        ready = sorted(n for n, deps in waiting.items() if not deps)
        if not ready:
            return order, ("These steps read each other in a cycle, so none can run first: "
                           + ", ".join(sorted(waiting)) + ".")
        for n in ready:
            order.append(n)
            del waiting[n]
        for deps in waiting.values():
            deps.difference_update(ready)
    return order, ""


def _qname(schema: str, table: str) -> str:
    return ".".join('"' + part.replace('"', '""') + '"' for part in (schema, table))


def _lit(text: str) -> str:
    return text.replace("'", "''")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    jsonfile.write_text(path, json.dumps(data, indent=2))
