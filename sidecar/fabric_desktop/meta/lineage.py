"""Column lineage worked out from what the project's files state, never from running anything.

Each column an entity writes gets one record saying where it comes from:

* ``source``: read as-is from the entity's source (by the same name, or through ``[source] rename``).
* ``derived``: a ``[[derive]]`` expression, with the columns it reads.
* ``lookup``: a dimension's surrogate key, matched on a column (and a date for Type 2).
* ``key``: a surrogate key from a key table, for a business key.
* ``framework``: added by the framework (a dimension's surrogate key, Type 2 history, run stamps).
* ``custom``: produced by Python (``[hooks] adds`` or a ``[step] custom``): opaque, never guessed.

and a label: ``declared`` when a file names the link (a rename, an expression, a lookup, a key),
``inferred`` when it's assumed from a matching name, ``opaque`` when custom code decides, ``unknown``
when an expression can't be read for its columns (they're never guessed). Columns a hook may touch
after they're produced say so (``custom_may_change``), because a hook can change any column.
"""

from __future__ import annotations

import logging
from typing import Any

from ..redact import describe
from . import keys as keys_mod
from .project import SCD2_COLUMNS, Entity, Project, sql_expr

_log = logging.getLogger(__name__)

RUN_COLUMNS = ("_run_id", "_loaded_at")


def _expr_columns(expr: Any) -> list[str] | None:
    """The columns an expression reads, or None when that isn't known: it doesn't parse, or it picks
    columns by pattern (COLUMNS('^a.*$')), which only the data decides."""
    try:
        parsed = sql_expr(expr)
        if parsed.meta.has_multiple_outputs():
            return None
        return list(dict.fromkeys(parsed.meta.root_names()))
    except Exception as exc:  # noqa: BLE001 - an expression the save gate would refuse anyway
        _log.info("Couldn't parse an expression's columns: %s", describe(exc))
        return None


def _origin(e: Entity) -> dict[str, Any]:
    src = e.spec.get("source", {})
    for kind in ("files", "landing", "table", "link"):
        if kind in src:
            return {"kind": kind, "value": src[kind]}
    return {"kind": "entity", "value": e.sources()}


def entity_columns(project: Project, name: str) -> dict[str, dict[str, Any]]:
    """{column: lineage record} for every column the entity writes, in contract order."""
    e = project.entities[name]
    s = e.spec
    origin = _origin(e)
    renames = {v: k for k, v in (s.get("source", {}).get("rename") or {}).items()}  # contract name -> source name
    derive_list = [d for d in s.get("derive", []) if isinstance(d, dict) and "column" in d]
    derives = {d["column"]: d for d in derive_list}
    # Derives run in order, so one reads a column another makes only when that one comes first; before
    # then it reads the column as it came in (x = y + 1 then y = x * 2 is no loop).
    derive_at = {d["column"]: n for n, d in enumerate(derive_list)}
    first_derived: dict[str, int] = {}
    for n, d in enumerate(derive_list):
        first_derived.setdefault(d["column"], n)
    lookups = {lk["as"]: lk for lk in s.get("lookup", []) if isinstance(lk, dict) and "as" in lk}
    keys = {k["column"]: k for k in keys_mod.entity_keys(e)}
    hooks = s.get("hooks", {})
    adds = set(hooks.get("adds", []))
    custom_step = "custom" in s.get("step", {})
    hook_points = [p for p in ("after_clean", "before_write") if p in hooks]
    cleaned: dict[str, list[str]] = {}
    for c in s.get("clean", []):
        if isinstance(c, dict) and c.get("column"):
            cleaned.setdefault(c["column"], []).extend(k for k in c if k != "column")

    def src_inputs(c: str) -> list[dict[str, Any]]:
        """Where a column the entity reads (before anything it adds) comes from."""
        sc = renames.get(c, c)
        if origin["kind"] != "entity":
            return [{"origin": origin["kind"], "value": origin["value"], "column": sc}]
        return [{"entity": up, "column": sc,
                 **({} if up in project.entities and sc in entity_columns_names(project, up) else {"missing": True})}
                for up in origin["value"]]

    def ref(c: str, this: str) -> list[dict[str, Any]]:
        """A column used in building ``this``: one the entity makes itself, or one it reads."""
        if c != this and c in written:  # as the entity has it: renamed, cleaned or made here
            return [{"entity": name, "column": c}]
        return src_inputs(c)

    columns = list(e.columns) + [c for c in sorted(e.mode_columns()) if c not in e.columns]
    written = set(columns)
    if e.mode == "scd2":
        columns += [c for c in SCD2_COLUMNS if c not in columns]
    columns += [c for c in RUN_COLUMNS if c not in columns]
    out: dict[str, dict[str, Any]] = {}
    for col in columns:
        rec: dict[str, Any]
        if col in RUN_COLUMNS:
            rec = {"how": "framework", "label": "declared", "inputs": [], "note": "run stamp"}
        elif col in adds:
            rec = {"how": "custom", "label": "opaque", "inputs": [], "note": "added by your Python (hooks adds)"}
        elif col in derives:
            expr = derives[col].get("expr")
            used = _expr_columns(expr)
            rec = {"how": "derived", "label": "declared", "expr": expr,
                   "inputs": [i for c in used or []
                              for i in (src_inputs(c) if first_derived.get(c, -1) >= derive_at[col] else ref(c, col))]}
            if used is None:  # never guessed: the columns it reads aren't known
                rec["label"] = "unknown"
                rec["note"] = "the expression couldn't be read"
        elif col in lookups:
            lk = lookups[col]
            dim = project.entities.get(lk.get("dim") or "")
            rec = {"how": "lookup", "label": "declared",
                   # a "dim" that isn't a dimension doesn't make the key: the link is there, the column isn't
                   "inputs": ([{"entity": dim.name, "column": dim.surrogate_key,
                                **({} if dim.surrogate_key in entity_columns_names(project, dim.name) else {"missing": True})}]
                              if dim and dim.surrogate_key else [])
                   + [i for c in (lk.get("on"), lk.get("at")) if c for i in ref(c, col)],
                   "note": f"{lk.get('dim')} key matched on {lk.get('on')}" + (f" as of {lk['at']}" if lk.get("at") else "")}
        elif col in keys:
            k = keys[col]
            rec = {"how": "key", "label": "declared", "key_table": k["table"],
                   "inputs": [i for c in k["business_key"] for i in ref(c, col)],
                   "note": f"surrogate key from key table {k['table']}"}
        elif col == e.surrogate_key or (e.mode == "scd2" and col in SCD2_COLUMNS):
            rec = {"how": "framework", "label": "declared", "inputs": [],
                   "note": "dimension surrogate key" if col == e.surrogate_key else "Type 2 history"}
        elif custom_step:
            rec = {"how": "custom", "label": "opaque", "inputs": [], "note": "your Python step builds the table"}
        else:
            rec = {"how": "source", "label": "declared" if col in renames else "inferred",
                   "source_column": renames.get(col, col), "inputs": src_inputs(col)}
        if col in cleaned:
            rec["cleaned"] = cleaned[col]
        if hook_points and rec["how"] not in ("framework", "custom"):
            rec["custom_may_change"] = hook_points
        out[col] = rec
    return out


def entity_columns_names(project: Project, name: str) -> list[str]:
    """Every column the entity writes (the keys of ``entity_columns``), its run stamps too: a table that
    reads _run_id from it reads a column that is there."""
    e = project.entities[name]
    names = list(e.columns) + sorted(e.mode_columns() - set(e.columns))
    return names + [c for c in RUN_COLUMNS if c not in names]


def upstream(project: Project, name: str, column: str) -> list[dict[str, Any]]:
    """Every step back from ``name.column`` to where it starts, nearest first (depth first, each step once).
    Walked with a stack, not recursion, so a chain of any length (or a cycle in hand-written files) ends."""
    cols: dict[str, dict[str, dict[str, Any]]] = {}  # each entity's columns, worked out once per call
    seen: set[tuple[str, str]] = set()
    steps: list[dict[str, Any]] = []
    todo = [(name, column)]
    while todo:
        ent, col = todo.pop()
        if (ent, col) in seen or ent not in project.entities:
            continue
        seen.add((ent, col))
        if ent not in cols:
            cols[ent] = entity_columns(project, ent)
        rec = cols[ent].get(col)
        if rec is None:
            steps.append({"entity": ent, "column": col, "how": "unknown", "label": "inferred", "inputs": [],
                          "note": f"{ent} doesn't write {col}"})
            continue
        steps.append({"entity": ent, "column": col, **rec})
        todo += [(i["entity"], i["column"]) for i in reversed(rec["inputs"]) if not i.get("missing") and "entity" in i]
    return steps


def downstream(project: Project, name: str, column: str) -> list[dict[str, Any]]:
    """Every column, in any entity of the project, built from ``name.column`` directly or further on."""
    reads: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for other in project.entities:
        for col, rec in entity_columns(project, other).items():
            for i in rec["inputs"]:
                if "entity" not in i:
                    continue
                reads.setdefault((i["entity"], i["column"]), []).append((other, col, rec["how"]))
    out, todo, seen = [], [(name, column)], {(name, column)}
    while todo:
        for ent, col, how in reads.get(todo.pop(0), []):
            if (ent, col) not in seen:
                seen.add((ent, col))
                out.append({"entity": ent, "column": col, "how": how})
                todo.append((ent, col))
    return out
