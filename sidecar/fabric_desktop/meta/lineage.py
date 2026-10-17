"""Column lineage worked out from what the project's files state, never from running anything.

Each column an entity writes gets one record saying where it comes from:

* ``source``: read as-is from the entity's source (by the same name, or through ``[source] rename``).
* ``derived``: a ``[[derive]]`` expression, with the columns it reads.
* ``lookup``: a dimension's surrogate key, matched on a column (and a date for Type 2).
* ``key``: a surrogate key from a key table, for a business key.
* ``framework``: added by the framework (a dimension's surrogate key, Type 2 history, run stamps).
* ``custom``: produced by Python (``[hooks] adds`` or a ``[step] custom``): opaque, never guessed.

and a label: ``declared`` when a file names the link (a rename, an expression, a lookup, a key),
``inferred`` when it's assumed from a matching name, ``opaque`` when custom code decides. Columns a
hook may touch after they're produced say so (``custom_may_change``), because a hook can change any
column.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from . import keys as keys_mod
from .project import SCD2_COLUMNS, Entity, Project

RUN_COLUMNS = ("_run_id", "_loaded_at")


def _expr_columns(expr: str) -> list[str] | None:
    try:
        return list(dict.fromkeys(pl.sql_expr(expr).meta.root_names()))
    except Exception:  # noqa: BLE001 - an expression the save gate would refuse anyway
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
    derives = {d["column"]: d for d in s.get("derive", []) if isinstance(d, dict) and "column" in d}
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
            used = _expr_columns(derives[col]["expr"])
            rec = {"how": "derived", "label": "declared", "expr": derives[col]["expr"],
                   "inputs": [i for c in used or [] for i in ref(c, col)]}
            if used is None:
                rec["note"] = "the expression couldn't be read"
        elif col in lookups:
            lk = lookups[col]
            dim = project.entities.get(lk.get("dim"))
            rec = {"how": "lookup", "label": "declared",
                   "inputs": ([{"entity": dim.name, "column": dim.surrogate_key}] if dim and dim.surrogate_key else [])
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
    e = project.entities[name]
    return list(e.columns) + sorted(e.mode_columns() - set(e.columns))


def upstream(project: Project, name: str, column: str, _seen: set | None = None) -> list[dict[str, Any]]:
    """Every step back from ``name.column`` to where it starts, nearest first."""
    seen = _seen if _seen is not None else set()
    if (name, column) in seen or name not in project.entities:
        return []
    seen.add((name, column))
    rec = entity_columns(project, name).get(column)
    if rec is None:
        return [{"entity": name, "column": column, "how": "unknown", "label": "inferred", "inputs": [],
                 "note": f"{name} doesn't write {column}"}]
    steps = [{"entity": name, "column": column, **rec}]
    for i in rec["inputs"]:
        if i.get("missing") or "entity" not in i:
            continue
        steps += upstream(project, i["entity"], i["column"], seen)
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
