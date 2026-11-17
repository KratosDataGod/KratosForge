"""What a change to one column would break, worked out from the project's files, never from running anything.

Pick a column of an entity and a change (drop it, rename it, or change its type). The answer lists every
consumer in the project, each with a severity and the reasons:

* ``breaks``: it reads the column by name in a way the change takes away, so building it fails, or the
  save gate refuses it, or a relationship in the semantic model loses a side.
* ``check``: it may still work, but someone should look: a column built further on, code that reads the
  table and can't be read for columns, a visible model column reports may use, or a type that carries on.

Consumers are the project's entities (tables), its Python (``transforms/`` hooks, custom steps and custom
checks) and the Direct Lake semantic model KratosForge builds over the last layer. Entities that read the
table but not this column are counted apart (``table_only``). Notebooks outside the project aren't traced.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .. import semantic
from ..redact import describe
from . import lineage
from .project import Project, impact as entity_impact

_log = logging.getLogger(__name__)

CHANGES = ("drop", "rename", "retype")
_RANK = {"breaks": 2, "check": 1}

# How a downstream column reads the changed one, and what that means for each change.
_DIRECT = {
    "source": ("reads {col} by name from {src}", "its contract column {own} takes {src}.{col}'s type as it comes"),
    "derived": ("its expression for {own} uses {col}", "its expression for {own} uses {col}; check it works with the new type"),
    "lookup": ("matches {own} on {col}", "matches {own} on {col}; the two sides of the match must keep the same type"),
    "key": ("uses {col} as the business key for {own}", "uses {col} as the business key for {own}; key tables keep the old type"),
}


def _module_file(root: Path, ref: str) -> Path | None:
    """``transforms.pkg.mod:fn`` -> <project>/transforms/pkg/mod.py, if it's there."""
    module = ref.split(":", 1)[0]
    parts = module.split(".")
    if parts[0] != "transforms" or not all(p.isidentifier() for p in parts):
        return None
    base = (root / "transforms").resolve()
    if base != root.resolve() / "transforms":  # transforms/ itself is a link to somewhere else
        return None
    p = root.joinpath(*parts).with_suffix(".py").resolve()
    if not p.is_relative_to(base):  # a symlink can still point out; never read outside transforms/
        return None
    return p if p.is_file() else None


def _mentions(path: Path, column: str) -> bool | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.info("Couldn't read %s to look for a column: %s", path.name, describe(exc))
        return None
    return re.search(rf"(?<![\w]){re.escape(column)}(?![\w])", text) is not None


def _own_config_refs(e: Any, column: str) -> list[str]:
    """Places in the entity's own file that name ``column``, so dropping or renaming it fails the save gate."""
    s = e.spec
    out = []
    if column in e.keys:
        out.append(f"{column} is the table's key")
    if (s.get("load") or {}).get("watermark") == column:
        out.append(f"{column} is the incremental watermark")
    scd = s.get("scd") or {}
    for part in ("tracked", "type1"):
        if column in (scd.get(part) or []):
            out.append(f"[scd] {part} lists {column}")
    if any(isinstance(c, dict) and c.get("column") == column for c in s.get("clean", [])):
        out.append(f"a [[clean]] step names {column}")
    if any(isinstance(c, dict) and c.get("column") == column for c in s.get("check", [])):
        out.append(f"a [[check]] names {column}")
    return out


class _Items:
    """Consumers by (kind, name), each keeping its worst severity and every reason."""

    def __init__(self) -> None:
        self.by: dict[tuple[str, str], dict[str, Any]] = {}

    def add(self, kind: str, name: str, severity: str, why: str, *, entity: str | None = None,
            detail: str = "", chain: list[str] | None = None) -> None:
        it = self.by.setdefault((kind, name), {"kind": kind, "name": name, "severity": severity, "why": [],
                                               "detail": detail, "entity": entity, "chain": chain or []})
        if _RANK[severity] > _RANK[it["severity"]]:
            it["severity"] = severity
            if chain:
                it["chain"] = chain
        if why not in it["why"]:
            it["why"].append(why)

    def names(self, kind: str) -> set[str]:
        return {n for k, n in self.by if k == kind}

    def list(self) -> list[dict[str, Any]]:
        order = {"table": 0, "python": 1, "model": 2}
        return sorted(self.by.values(), key=lambda i: (-_RANK[i["severity"]], order[i["kind"]], i["name"]))


def column_impact(project: Project, name: str, column: str, change: str, new_name: str | None = None,
                  new_type: str | None = None) -> dict[str, Any]:
    """Every consumer in the project that ``change`` to ``name.column`` breaks, or that should be checked."""
    if name not in project.entities:
        raise LookupError(f"No entity {name}")
    if change not in CHANGES:
        raise ValueError(f"change must be one of {', '.join(CHANGES)}")
    cols = lineage.entity_columns(project, name)
    if column not in cols:
        raise LookupError(f"{name} doesn't write {column}")
    gone = change in ("drop", "rename")
    verb = {"drop": "dropped", "rename": f"renamed to {new_name}" if new_name else "renamed",
            "retype": f"changed to {new_type}" if new_type else "given a new type"}[change]
    items = _Items()
    e = project.entities[name]

    # Every column of every entity, by the columns it reads.
    reads: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for other in project.entities:
        for col, rec in lineage.entity_columns(project, other).items():
            for i in rec["inputs"]:
                if "entity" in i:
                    reads.setdefault((i["entity"], i["column"]), []).append((other, col, rec["how"]))

    def label(ent: str) -> str:
        return "this table" if ent == name else "table"

    # 1. What reads the column directly. ``carried`` are the columns the change reaches as it is: a drop
    # takes plain copies with it, and a new type carries on through them.
    direct: list[tuple[str, str]] = []
    carried: set[tuple[str, str]] = set()
    for ent, col, how in reads.get((name, column), []):
        if (ent, col) == (name, column):
            continue
        gone_why, type_why = _DIRECT.get(how, ("reads {col}", "reads {col}"))
        args = {"col": column, "own": col, "src": name}
        if gone:
            items.add("table", ent, "breaks", gone_why.format(**args) + f"; once it's {verb}, building {ent} fails",
                      entity=ent, detail=label(ent), chain=[f"{name}.{column}", f"{ent}.{col}"])
        else:
            items.add("table", ent, "check", type_why.format(**args), entity=ent, detail=label(ent),
                      chain=[f"{name}.{column}", f"{ent}.{col}"])
        direct.append((ent, col))
        if how == "source" and change != "rename":
            carried.add((ent, col))

    # 2. Checks in other entities that look the column up (exists_in).
    for other, oe in project.entities.items():
        for c in oe.spec.get("check", []):
            if isinstance(c, dict) and c.get("exists_in") == f"{name}.{column}":
                items.add("table", other, "breaks" if gone else "check",
                          f"its [[check]] on {c.get('column', '?')} looks values up in {name}.{column}"
                          + ("; the save gate refuses it" if gone else "; both sides must keep the same type"),
                          entity=other, detail=label(other))

    # 3. The entity's own file, when the column goes.
    if gone:
        for why in _own_config_refs(e, column):
            items.add("table", name, "breaks", why + "; the save gate refuses the change until you update it",
                      entity=name, detail="this table")

    # 4. Further on: columns built from the ones above.
    seen = {(name, column), *direct}
    todo = [(ent, col, [f"{name}.{column}", f"{ent}.{col}"]) for ent, col in direct]
    while todo:
        src_ent, src_col, path = todo.pop(0)
        for ent, col, how in reads.get((src_ent, src_col), []):
            if (ent, col) in seen:
                continue
            seen.add((ent, col))
            chain = path + [f"{ent}.{col}"]
            if gone:
                items.add("table", ent, "check", f"{col} is built from {src_ent}.{src_col}, which breaks first",
                          entity=ent, detail=label(ent), chain=chain)
            elif how == "source":
                items.add("table", ent, "check", f"{col} carries the new type on from {src_ent}.{src_col}",
                          entity=ent, detail=label(ent), chain=chain)
            else:
                continue
            if change == "retype" or (change == "drop" and how == "source" and (src_ent, src_col) in carried):
                carried.add((ent, col))
            todo.append((ent, col, chain))

    # 5. Python: code that reads the table (or the table's own hooks) can't be read for columns, so it's
    # flagged when it names the column, and otherwise when it reads the table at all.
    for other, oe in project.entities.items():
        own = other == name
        reads_table = own or name in oe.reads() or any(n == other for n, _ in direct)
        if not reads_table:
            continue
        for kind, ref, _ in oe.python_refs():
            if not isinstance(ref, str):
                continue
            file = _module_file(project.root, ref)
            hit = _mentions(file, column) if file else None
            where = f"{kind} of {other}"
            detail = f"your Python, {where}"
            if hit:
                items.add("python", ref, "breaks" if gone else "check",
                          f"names {column} ({where}); it can't find it once it's {verb}" if gone
                          else f"names {column} ({where}); check it works with the new type",
                          entity=other, detail=detail)
            elif hit is None:
                items.add("python", ref, "check", f"{where}: the code couldn't be read from transforms/, "
                          "so whether it uses the column isn't known", entity=other, detail=detail)
            elif not own:
                items.add("python", ref, "check",
                          f"reads {name} without naming {column}; it may still use every column",
                          entity=other, detail=detail)

    # 6. The semantic model KratosForge builds over the last layer: its relationships and visible columns.
    layer = project.layer_names()[-1] if project.layers else None
    hit_cols = {(name, column), *direct, *carried}
    if layer and any(project.entities[ent].layer == layer for ent, _ in hit_cols):
        tables, rels = semantic.plan(project, layer)
        by_entity = {t.entity: t for t in tables}
        model = f"SM_{layer.title()}"
        for ent, col in sorted(hit_cols):
            t = by_entity.get(ent)
            if t is None:
                continue
            # A column that goes, or one whose build breaks, takes its relationship with it.
            breaks = gone and ((ent, col) == (name, column) or (ent, col) in direct or change == "drop")
            for r in rels:
                if (t.name, col) in ((r.from_table, r.from_column), (r.to_table, r.to_column)):
                    rel = f"{r.from_table}[{r.from_column}] → {r.to_table}[{r.to_column}]"
                    if breaks:
                        items.add("model", model, "breaks", f"its relationship {rel} loses a side", detail="semantic model")
                    elif not gone:
                        items.add("model", model, "check", f"its relationship {rel} needs both sides to keep one type",
                                  detail="semantic model")
            visible = not (col.startswith("_") or col.endswith("_sk") or col == t.surrogate_key)
            if visible and ((ent, col) == (name, column) or (ent, col) in carried):
                items.add("model", model, "check",
                          {"drop": f"reports that use {t.name}[{col}] lose it",
                           "rename": f"reports that use {t.name}[{col}] need the new name",
                           "retype": f"visuals and totals on {t.name}[{col}] may change"}[change],
                          detail="semantic model")

    listed = items.names("table")
    table_only = [n for n in entity_impact(project, name) if n not in listed and name in project.entities[n].reads()]
    out = items.list()
    return {
        "entity": name, "column": column, "change": change, "new_name": new_name, "new_type": new_type,
        "type": e.columns.get(column), "items": out,
        "counts": {"breaks": sum(i["severity"] == "breaks" for i in out),
                   "check": sum(i["severity"] == "check" for i in out), "table_only": len(table_only)},
        "table_only": table_only, "model_layer": layer,
    }


def edit_changes(old: dict[str, str], new: dict[str, str]) -> list[dict[str, Any]]:
    """The column changes an edit of a contract makes: dropped, retyped, and renamed when exactly one
    column goes and exactly one of the same type arrives (otherwise a rename reads as a drop)."""
    gone = [c for c in old if c not in new]
    added = [c for c in new if c not in old]
    out: list[dict[str, Any]] = []
    if len(gone) == 1 and len(added) == 1 and old[gone[0]] == new[added[0]]:
        out.append({"column": gone[0], "change": "rename", "new_name": added[0]})
    else:
        out += [{"column": c, "change": "drop"} for c in gone]
    out += [{"column": c, "change": "retype", "new_type": new[c]} for c in old if c in new and str(new[c]) != str(old[c])]
    return out


def edit_impact(project: Project, name: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """What saving ``spec`` over the entity ``name`` would break: one answer for each contract column it
    drops, renames or retypes, leaving out changes that touch nothing."""
    if name not in project.entities:
        return []
    old = (project.entities[name].spec.get("contract") or {}).get("columns") or {}
    new = (spec.get("contract") or {}).get("columns") or {}
    if not isinstance(new, dict) or not isinstance(old, dict):
        return []
    known = lineage.entity_columns(project, name)
    out = []
    for ch in edit_changes({str(k): str(v) for k, v in old.items()}, {str(k): str(v) for k, v in new.items()}):
        if ch["column"] not in known:
            continue
        res = column_impact(project, name, ch["column"], ch["change"], ch.get("new_name"), ch.get("new_type"))
        if res["items"]:
            out.append(res)
    return out
