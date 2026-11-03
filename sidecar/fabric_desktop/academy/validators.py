"""Academy validators (ACD-004): deterministic, explained checks of what a learner made.

Each validator returns ``{kind, passed, detail}`` like the Learn checks (learn/checks.py), and
``detail`` names the problem in words, never just a red cross. They run in the engine process and
read the learner's outputs as data through ``workspace`` (paths contained, links refused, sizes
capped). Nothing here runs learner code. Same answer, same verdict: no clocks, no randomness, and
numbers compare with a tolerance only when the exercise declares one.

    schema_matches    a table's columns, types, nullability and key
    data_quality      rules over a table: unique, not_null, range, accepted, references, row_count
    config_matches    a settings document (JSON) meets rules on its fields
    artifact_present  required files and folders exist, with sizes and JSON keys
    delta_table       a Delta table's log is safe, then its versions, operations, partitions, files, columns
    pipeline_outcome  a local pipeline run record (orchestration/runner.py): status, activities, outputs
    unchanged         tables the answer must not touch hold the same rows as before it ran (``snapshot`` first)

``result_matches``, ``faster_than`` and the others stay in learn/checks.py and are reused as they are.
"""

from __future__ import annotations

import json
import math
import posixpath
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import duckdb

from . import workspace as ws
from ..duckdb_rows import one_row

MAX_LOG_ENTRIES = 2_000   # Delta commits a validator reads; an exercise table has a handful


def _ok(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": True, "detail": detail}


def _no(kind: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "passed": False, "detail": detail}


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, tuple[str, str, bool]]:
    """lower name -> (name, TYPE, nullable), in table order."""
    rows = con.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE lower(table_name) = lower(?) ORDER BY ordinal_position", [table]).fetchall()
    return {r[0].lower(): (r[0], str(r[1]).upper(), str(r[2]).upper() == "YES") for r in rows}


# ---- schema_matches

def schema_matches(con: duckdb.DuckDBPyConnection, table: str, columns: list[dict[str, Any]],
                   key: list[str] | None = None, exact: bool = False, ordered: bool = False) -> dict[str, Any]:
    """``columns``: ``[{name, type?, nullable?}]``. ``type`` matches the start of DuckDB's type name
    (``DECIMAL`` matches ``DECIMAL(18,2)``). ``nullable: false`` needs a NOT NULL column or, for a
    view or file, no NULL values. ``exact``: no other columns; ``ordered``: in this order. ``key``:
    those columns are unique together."""
    kind = "schema_matches"
    have = _columns(con, table)
    if not have:
        return _no(kind, f"There's no table {table} yet.")
    for col in columns:
        name = str(col["name"])
        got = have.get(name.lower())
        if got is None:
            return _no(kind, f"{table} has no column {name}.")
        typ = col.get("type")
        if typ and not got[1].startswith(str(typ).upper()):
            return _no(kind, f"{table}.{got[0]} is {got[1]}; it should be {str(typ).upper()}.")
        if col.get("nullable") is False and got[2]:
            nulls = one_row(con.execute(f"SELECT count(*) FROM {_ident(table)} WHERE {_ident(got[0])} IS NULL"))[0]  # type: ignore[index]
            if nulls:
                return _no(kind, f"{table}.{got[0]} must never be empty; {nulls:,} rows are NULL.")
    wanted = [str(c["name"]).lower() for c in columns]
    if exact:
        extra = [have[n][0] for n in have if n not in wanted]
        if extra:
            return _no(kind, f"{table} has columns the exercise doesn't ask for: {', '.join(extra)}.")
    if ordered:
        order = [n for n in have if n in wanted]
        if order != wanted:
            return _no(kind, f"{table}'s columns are in the order {', '.join(have[n][0] for n in order)}; "
                             f"the exercise asks for {', '.join(str(c['name']) for c in columns)}.")
    if key:
        missing = [k for k in key if k.lower() not in have]
        if missing:
            return _no(kind, f"{table} has no column {missing[0]} for its key.")
        cols = ", ".join(_ident(have[k.lower()][0]) for k in key)
        dupes = one_row(con.execute(
            f"SELECT count(*) FROM (SELECT {cols} FROM {_ident(table)} GROUP BY ALL HAVING count(*) > 1)"))[0]  # type: ignore[index]
        if dupes:
            return _no(kind, f"{dupes:,} values of the key {', '.join(key)} appear more than once in {table}.")
    return _ok(kind, f"{table} has the columns{', types' if any(c.get('type') for c in columns) else ''}"
                     f"{' and key' if key else ''} the exercise asks for.")


# ---- data_quality

def _rule_count(con: duckdb.DuckDBPyConnection, table: str, rule: dict[str, Any],
                have: dict[str, tuple[str, str, bool]]) -> tuple[int, str]:
    """(rows breaking the rule, how to say it)."""
    t = _ident(table)
    name = str(rule.get("rule"))
    cols = [str(c) for c in (rule.get("columns") or ([rule["column"]] if "column" in rule else []))]
    for c in cols:
        if c.lower() not in have:
            raise ValueError(f"{table} has no column {c}.")
    q = [_ident(have[c.lower()][0]) for c in cols]
    if name == "unique":
        n = one_row(con.execute(f"SELECT count(*) FROM (SELECT {', '.join(q)} FROM {t} GROUP BY ALL HAVING count(*) > 1)"))[0]  # type: ignore[index]
        return int(n), f"{n:,} values of {', '.join(cols)} appear more than once"
    if name == "not_null":
        cond = " OR ".join(f"{c} IS NULL" for c in q)
        n = one_row(con.execute(f"SELECT count(*) FROM {t} WHERE {cond}"))[0]  # type: ignore[index]
        return int(n), f"{n:,} rows have an empty (NULL) {' or '.join(cols)}"
    if name == "range":
        lo, hi = rule.get("min"), rule.get("max")
        parts, args = [], []
        if lo is not None:
            parts.append(f"{q[0]} < ?")
            args.append(lo)
        if hi is not None:
            parts.append(f"{q[0]} > ?")
            args.append(hi)
        if not parts:
            raise ValueError("A range rule needs min or max.")
        n = one_row(con.execute(f"SELECT count(*) FROM {t} WHERE {' OR '.join(parts)}", args))[0]  # type: ignore[index]
        bounds = " and ".join(x for x in (f"at least {lo}" if lo is not None else "", f"at most {hi}" if hi is not None else "") if x)
        return int(n), f"{n:,} rows have a {cols[0]} outside the range ({bounds})"
    if name == "accepted":
        values = list(rule.get("values") or [])
        if not values:
            raise ValueError("An accepted rule needs values.")
        marks = ", ".join("?" for _ in values)
        n = one_row(con.execute(f"SELECT count(*) FROM {t} WHERE {q[0]} IS NOT NULL AND {q[0]} NOT IN ({marks})", values))[0]  # type: ignore[index]
        return int(n), f"{n:,} rows have a {cols[0]} that isn't one of {', '.join(map(str, values))}"
    if name == "references":
        parent = str(rule["parent"])
        pc = str(rule.get("parent_column") or cols[0])
        n = one_row(con.execute(
            f"SELECT count(*) FROM {t} c WHERE c.{q[0]} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {_ident(parent)} p WHERE p.{_ident(pc)} = c.{q[0]})"))[0]  # type: ignore[index]
        return int(n), f"{n:,} rows have a {cols[0]} that {parent} doesn't have"
    if name == "row_count":
        n = int(one_row(con.execute(f"SELECT count(*) FROM {t}"))[0])  # type: ignore[index]
        lo, hi = rule.get("min"), rule.get("max")
        if rule.get("equals") is not None and n != int(rule["equals"]):
            return 1, f"it has {n:,} rows; the answer has {int(rule['equals']):,}"
        if (lo is not None and n < int(lo)) or (hi is not None and n > int(hi)):
            return 1, f"it has {n:,} rows; it should have between {lo if lo is not None else 0:,} and {hi if hi is not None else '∞'}"
        return 0, ""
    raise ValueError(f"Unknown data quality rule {name}.")


def data_quality(con: duckdb.DuckDBPyConnection, table: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Every rule must hold; the detail names the first one that doesn't, with how many rows break it."""
    kind = "data_quality"
    have = _columns(con, table)
    if not have:
        return _no(kind, f"There's no table {table} yet.")
    for rule in rules:
        try:
            broken, said = _rule_count(con, table, rule, have)
        except (ValueError, KeyError, duckdb.Error) as exc:
            return _no(kind, f"The {rule.get('rule')} check couldn't run: {exc}")
        if broken:
            return _no(kind, f"In {table}, {said}.")
    return _ok(kind, f"{table} passes all {len(rules)} data quality rules.")


# ---- config_matches

_MISSING = object()


def _lookup(doc: Any, path: str) -> Any:
    """``a.b.0.c``: object keys and list positions."""
    here = doc
    for part in str(path).split("."):
        if isinstance(here, dict) and part in here:
            here = here[part]
        elif isinstance(here, list) and part.isdigit() and int(part) < len(here):
            here = here[int(part)]
        else:
            return _MISSING
    return here


_SECRETISH = re.compile(r"(?i)(secret|password|token|key|sig=|bearer|eyJ)")


def _say(value: Any) -> str:
    """A value the learner wrote, safe to show: numbers, true/false and short plain words as they
    are; anything long or that looks like a secret as "a different value". A check's message never
    carries file contents beyond that (Security read, ACD-004)."""
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value)
    if isinstance(value, str) and len(value) <= 40 and not _SECRETISH.search(value) and value.isprintable():
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return f"a list of {len(value)}"
    if isinstance(value, dict):
        return "an object"
    return "a different value"


def config_matches(doc: Any, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Rules on a parsed settings document. Each rule has ``path`` and one of ``equals``, ``one_of``,
    ``absent``, ``present``, ``min``/``max``, ``contains``, ``not_contains``; ``why`` is said when it fails
    (the reason the setting matters, such as least privilege)."""
    kind = "config_matches"
    for rule in rules:
        path = str(rule["path"])
        value = _lookup(doc, path)
        why = f" {rule['why']}" if rule.get("why") else ""
        if rule.get("absent"):
            if value is not _MISSING:
                return _no(kind, f"{path} is set to {_say(value)}; it shouldn't be there.{why}")
            continue
        if value is _MISSING:
            return _no(kind, f"{path} isn't set.{why}")
        if "equals" in rule and value != rule["equals"]:
            return _no(kind, f"{path} is {_say(value)}; it should be {_say(rule['equals'])}.{why}")
        if "one_of" in rule and value not in rule["one_of"]:
            return _no(kind, f"{path} is {_say(value)}; it should be one of {', '.join(map(_say, rule['one_of']))}.{why}")
        for bound, bad in (("min", lambda v, b: v < b), ("max", lambda v, b: v > b)):
            if bound in rule:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return _no(kind, f"{path} is {_say(value)}; it should be a number.{why}")
                if bad(value, rule[bound]):
                    return _no(kind, f"{path} is {_say(value)}; it should be at {'least' if bound == 'min' else 'most'} {rule[bound]}.{why}")
        if "contains" in rule:
            if not isinstance(value, (list, str)) or rule["contains"] not in value:
                return _no(kind, f"{path} doesn't include {_say(rule['contains'])}.{why}")
        if "not_contains" in rule and isinstance(value, (list, str)) and rule["not_contains"] in value:
            return _no(kind, f"{path} includes {_say(rule['not_contains'])}; it shouldn't.{why}")
    return _ok(kind, f"The settings meet all {len(rules)} rules.")


def config_file_matches(workspace: Path, rel: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        doc = ws.read_json(workspace, rel)
    except ws.Refused as exc:
        return _no("config_matches", str(exc))
    return config_matches(doc, rules)


# ---- artifact_present

def artifact_present(workspace: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    """``items``: ``[{path, kind: file|folder|json|delta, min_bytes?, max_bytes?, keys?}]``. ``keys``
    (for json) are dotted paths that must be there."""
    kind = "artifact_present"
    for item in items:
        rel, want = str(item["path"]), str(item.get("kind", "file"))
        try:
            path = ws.inside(workspace, rel)
        except ws.Refused as exc:
            return _no(kind, str(exc))
        if want == "folder" and not path.is_dir():
            return _no(kind, f"{rel} should be a folder.")
        if want == "delta":
            if not path.is_dir():
                return _no(kind, f"{rel} should be a Delta table folder.")
            try:
                log = ws.inside(workspace, f"{rel}/_delta_log")
            except ws.Refused:
                return _no(kind, f"{rel} has no _delta_log, so it isn't a Delta table.")
            if not log.is_dir() or not any(p.suffix == ".json" for p in log.iterdir()):
                return _no(kind, f"{rel} has no commits in _delta_log, so it isn't a Delta table yet.")
        if want in ("file", "json"):
            if not path.is_file():
                return _no(kind, f"{rel} should be a file.")
            size = path.stat().st_size
            if item.get("min_bytes") is not None and size < int(item["min_bytes"]):
                return _no(kind, f"{rel} is {size:,} bytes; it should be at least {int(item['min_bytes']):,}.")
            if item.get("max_bytes") is not None and size > int(item["max_bytes"]):
                return _no(kind, f"{rel} is {size:,} bytes; it should be at most {int(item['max_bytes']):,}.")
        if want == "json":
            try:
                doc = ws.read_json(workspace, rel)
            except ws.Refused as exc:
                return _no(kind, str(exc))
            for key in item.get("keys") or []:
                if _lookup(doc, key) is _MISSING:
                    return _no(kind, f"{rel} has no {key}.")
    return _ok(kind, f"All {len(items)} required items are there.")


# ---- delta_table

def _safe_data_path(raw: Any) -> bool:
    """A Delta log path the engine may follow: relative, inside the table, no scheme, no drive."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return False
    text = unquote(raw).replace("\\", "/")
    if text.startswith("/") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", text):
        return False   # absolute, a drive letter (C:) or a URI (file:, abfss:, s3:)
    norm = posixpath.normpath(text)
    return not (norm == ".." or norm.startswith("../") or norm.startswith("_delta_log"))


def _log_problem(action: dict[str, Any]) -> str | None:
    for name in ("add", "remove", "cdc"):
        body = action.get(name)
        if isinstance(body, dict):
            if not _safe_data_path(body.get("path")):
                return f"a {name} entry points outside the table folder"
            dv = body.get("deletionVector")
            if isinstance(dv, dict) and dv.get("storageType") == "p":
                return "a deletion vector points at an absolute path"
    return None


def check_log(workspace: Path, rel: str) -> list[dict[str, Any]]:
    """The table's commits in order, read as data, refusing any entry that points outside the
    table. Checkpoint files are refused too: an exercise table is small enough not to have one,
    and the engine reads only what it has checked."""
    names = ws.files_under(workspace, f"{rel}/_delta_log")
    commits = sorted(n for n in names if re.fullmatch(r"\d{20}\.json", n.rsplit("/", 1)[-1]))
    if not commits:
        raise ws.Refused(f"{rel} has no commits in _delta_log, so it isn't a Delta table yet.")
    if any("checkpoint" in n.rsplit("/", 1)[-1] for n in names):
        raise ws.Refused(f"{rel} has a checkpoint; exercise tables are checked from their commits only.")
    if len(commits) > MAX_LOG_ENTRIES:
        raise ws.Refused(f"{rel} has more than {MAX_LOG_ENTRIES:,} commits; an exercise table has fewer.")
    out: list[dict[str, Any]] = []
    for name in commits:
        version = int(name.rsplit("/", 1)[-1][:20])
        actions = []
        try:
            text = ws.read_bytes(workspace, name, ws.MAX_JSON_BYTES).decode("utf-8")
        except UnicodeDecodeError:
            raise ws.Refused(f"Commit {version} of {rel} isn't valid Delta JSON.") from None
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                action = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                raise ws.Refused(f"Commit {version} of {rel} isn't valid Delta JSON.") from None
            if not isinstance(action, dict):
                raise ws.Refused(f"Commit {version} of {rel} isn't valid Delta JSON.")
            problem = _log_problem(action)
            if problem:
                raise ws.Refused(f"Commit {version} of {rel}: {problem}. The check reads only files in the exercise folder.")
            actions.append(action)
        out.append({"version": version, "actions": actions})
    return out


def _name(value: Any) -> str:
    """An operation or column name from the log, short enough to show."""
    text = str(value)
    return text if len(text) <= 40 and text.isprintable() else text[:40].encode("ascii", "replace").decode() + "…"


def _table_facts(commits: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        return _facts(commits)
    except (AttributeError, TypeError, ValueError, KeyError):
        raise ws.Refused("The table's Delta log has an entry the check can't read.") from None


def _facts(commits: list[dict[str, Any]]) -> dict[str, Any]:
    """Versions, operations, partition columns, live files and schema from the checked log, without
    opening data files."""
    live: dict[str, int] = {}
    operations: list[str] = []
    partitions: list[str] = []
    schema: list[dict[str, Any]] = []
    for commit in commits:
        for a in commit["actions"]:
            if "commitInfo" in a:
                operations.append(_name(a["commitInfo"].get("operation", "")).upper())
            if "metaData" in a:
                partitions = [_name(p) for p in a["metaData"].get("partitionColumns") or []]
                try:
                    schema = json.loads(a["metaData"].get("schemaString") or "{}").get("fields") or []
                except (json.JSONDecodeError, AttributeError, RecursionError):
                    schema = []
            if "add" in a:
                live[unquote(a["add"]["path"])] = int(a["add"].get("size") or 0)
            if "remove" in a:
                live.pop(unquote(a["remove"]["path"]), None)
    return {"versions": [c["version"] for c in commits], "operations": operations, "partitions": partitions,
            "files": live, "schema": schema}


def _names(values: list[str]) -> str:
    shown = ", ".join(values[:8])
    return shown + (f" and {len(values) - 8} more" if len(values) > 8 else "")


def delta_table(workspace: Path, path: str, versions_min: int | None = None, versions_max: int | None = None,
                operations: list[str] | None = None, operations_in_order: bool = False,
                partition_by: list[str] | None = None, files_min: int | None = None, files_max: int | None = None,
                avg_file_bytes_min: int | None = None, columns: list[str] | None = None) -> dict[str, Any]:
    """A Delta table the learner wrote. ``operations``: these operations happened (``WRITE``, ``MERGE``,
    ``OPTIMIZE``...), and with ``operations_in_order`` in this order among the others. Everything is read
    from the checked log; no data file is opened."""
    kind = "delta_table"
    try:
        facts = _table_facts(check_log(workspace, path))
    except ws.Refused as exc:
        return _no(kind, str(exc))
    count = len(facts["versions"])
    if versions_min is not None and count < versions_min:
        return _no(kind, f"{path} has {count} versions; the exercise needs at least {versions_min}.")
    if versions_max is not None and count > versions_max:
        return _no(kind, f"{path} has {count} versions; the exercise allows at most {versions_max}.")
    done = facts["operations"]
    for op in operations or []:
        if op.upper() not in done:
            return _no(kind, f"{path}'s history has no {op.upper()}; it has {_names(done) or 'no operations'}.")
    if operations and operations_in_order:
        it = iter(done)
        if not all(op.upper() in it for op in operations):
            return _no(kind, f"{path}'s history is {_names(done)}; the exercise needs {', '.join(o.upper() for o in operations)} in that order.")
    if partition_by is not None and [p.lower() for p in facts["partitions"]] != [p.lower() for p in partition_by]:
        return _no(kind, f"{path} is partitioned by {_names(facts['partitions']) or 'nothing'}; "
                         f"the exercise asks for {', '.join(partition_by) or 'no partitions'}.")
    n_files = len(facts["files"])
    if files_min is not None and n_files < files_min:
        return _no(kind, f"{path} has {n_files} data files; the exercise expects at least {files_min}.")
    if files_max is not None and n_files > files_max:
        return _no(kind, f"{path} has {n_files} data files; the exercise expects at most {files_max}. Small files slow every read.")
    if avg_file_bytes_min is not None and n_files:
        avg = sum(facts["files"].values()) / n_files
        if avg < avg_file_bytes_min:
            return _no(kind, f"{path}'s data files average {math.floor(avg):,} bytes; the exercise expects at least {avg_file_bytes_min:,}.")
    names = [str(f.get("name", "")).lower() for f in facts["schema"]]
    for col in columns or []:
        if col.lower() not in names:
            return _no(kind, f"{path} has no column {col}.")
    return _ok(kind, f"{path} is a Delta table with {count} version{'s' if count != 1 else ''} and {n_files} "
                     f"data file{'s' if n_files != 1 else ''}, as the exercise asks.")


# ---- pipeline_outcome

def pipeline_outcome(run: dict[str, Any], status: str = "Succeeded", activities: dict[str, str] | None = None,
                     outputs: dict[str, Any] | None = None, return_value: dict[str, Any] | None = None) -> dict[str, Any]:
    """A run record from the local runner (``PipelineRun.to_json``). ``activities``: name -> Fabric
    status (``Succeeded``, ``Failed``, ``Skipped``...). ``outputs``: ``"<activity>.<dotted path>"`` ->
    the value its output must have. ``return_value``: what the pipeline must return."""
    kind = "pipeline_outcome"
    state = str(run.get("state", ""))
    if state.lower() != status.lower():
        why = f" ({run['stop_reason']})" if run.get("stop_reason") else ""
        return _no(kind, f"The run ended {state or 'without a state'}{why}; the exercise needs {status}.")
    by_name: dict[str, dict[str, Any]] = {}
    for act in run.get("activities") or []:
        by_name.setdefault(str(act.get("name")), act)   # the first record (inside a loop, the first pass)
    for name, want in (activities or {}).items():
        act = by_name.get(name)
        if act is None:
            return _no(kind, f"The activity {name} didn't run.")
        if str(act.get("status", "")).lower() != want.lower():
            return _no(kind, f"{name} ended {act.get('status')}; the exercise needs {want}.")
    for key, want in (outputs or {}).items():
        name, _, sub = key.partition(".")
        act = by_name.get(name)
        if act is None:
            return _no(kind, f"The activity {name} didn't run, so it has no output.")
        got = _lookup(act.get("output"), sub) if sub else act.get("output", _MISSING)
        if got is _MISSING:
            return _no(kind, f"{name}'s output has no {sub or 'value'}.")
        if got != want:
            return _no(kind, f"{name}'s output {sub} is {_say(got)}; the exercise needs {_say(want)}.")
    for key, want in (return_value or {}).items():
        got = (run.get("return_value") or {}).get(key, _MISSING)
        if got != want:
            return _no(kind, f"The pipeline returns {key} = {_say(None if got is _MISSING else got)}; the exercise needs {_say(want)}.")
    return _ok(kind, f"The run ended {state} with the activity results the exercise asks for.")


# ---- unchanged

def snapshot(con: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, tuple[int, int] | None]:
    """Each table's row count and an order-free fingerprint of its rows (a sum of row hashes, so
    duplicates count), computed by DuckDB without loading the rows. Compared only within one run of
    the engine; a missing table is None."""
    out: dict[str, tuple[int, int] | None] = {}
    for table in tables:
        if not _columns(con, table):
            out[table] = None
            continue
        n, fp = one_row(con.execute(f"SELECT count(*), coalesce(sum(hash(t)::HUGEINT), 0) FROM {_ident(table)} t"))  # type: ignore[misc]
        out[table] = (int(n), int(fp))
    return out


def unchanged(con: duckdb.DuckDBPyConnection, tables: list[str],
              before: dict[str, tuple[int, int] | None]) -> dict[str, Any]:
    """Run after the learner's answer, with ``before`` from ``snapshot`` taken just before it: passes
    when every listed table holds the same rows, which shows the fix changed nothing else."""
    kind = "unchanged"
    after = snapshot(con, tables)
    changed = [t for t in tables if before.get(t) != after[t]]
    if changed:
        said = []
        for t in changed[:8]:
            b, a = before.get(t), after[t]
            if a is None:
                said.append(f"{t} is gone")
            elif b is None:
                said.append(f"{t} is new")
            elif a[0] != b[0]:
                said.append(f"{t} went from {b[0]:,} to {a[0]:,} rows")
            else:
                said.append(f"{t} has {a[0]:,} rows as before, but some of them changed")
        more = f" and {len(changed) - 8} more" if len(changed) > 8 else ""
        return _no(kind, f"The answer changed tables it shouldn't touch: {'; '.join(said)}{more}.")
    return _ok(kind, f"Nothing else changed: {', '.join(tables[:8])}{' and others' if len(tables) > 8 else ''} hold the same rows.")
