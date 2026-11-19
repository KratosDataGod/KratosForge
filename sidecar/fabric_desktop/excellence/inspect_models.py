"""What a semantic model's definition shows, without keeping the definition (FEA-304).

:func:`inspect` takes a parsed ``model.bim`` (TMSL, as Fabric's getDefinition returns it with
``format=TMSL``; model_ossie_service and model_directlake_service read it the same way) and returns
only names and counts:

- relationships that filter in both directions;
- the security roles that filter rows, and the tables they filter. Never the filter expression, and
  never a role's members;
- how many tables are Direct Lake, the model's Direct Lake behavior, and whether those tables read
  through the SQL endpoint or OneLake (model_directlake). Never the source expression;
- the definition-only semantic model rules SM001, SM002 and SM004 (model_practices, the same rules
  the Semantic model tab and governance checks use), as rule id, count and the relationship or
  column names they matched.

DAX, M and the definition text never leave this function. No Fabric call is made here.
"""

from __future__ import annotations

from typing import Any

from .. import model_directlake, model_practices

RULES = ("SM001", "SM002", "SM004")
MAX_NAMES = 50
MAX_NAME = 256  # characters kept of any one name, so a hostile model can't bloat the evidence


def _cut(name: Any) -> str:
    text = str(name or "")
    return text if len(text) <= MAX_NAME else text[:MAX_NAME - 1] + "…"


def _rel_name(r: dict[str, Any]) -> str:
    return _cut(f"{r.get('fromTable', '')}.{r.get('fromColumn', '')} to {r.get('toTable', '')}.{r.get('toColumn', '')}")


def _item(model: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """The model as model_practices.check reads one, with the relationships whose column types, or
    whose many-side column, the definition doesn't show."""
    tables = []
    cols: dict[tuple[str, str], dict[str, Any]] = {}
    for t in model.get("tables") or []:
        name = str(t.get("name") or "")
        # dimension and description are set so SM005 and SM006, which aren't asked for, find nothing
        columns: list[dict[str, Any]] = []
        for c in t.get("columns") or []:
            if c.get("type") == "rowNumber":
                continue
            cname = str(c.get("name") or "")
            col = {"name": cname, "type": c.get("dataType") or None, "hidden": bool(c.get("isHidden")),
                   "description": "x"}
            columns.append(col)
            cols[(name, cname)] = col
        tables.append({"name": name, "entity": None, "dimension": True, "description": "x", "columns": columns})
    rels, untyped, unseen = [], [], []
    for r in model.get("relationships") or []:
        if not r.get("fromTable") or not r.get("toTable"):
            continue
        a, b = cols.get((r["fromTable"], r.get("fromColumn", ""))), cols.get((r["toTable"], r.get("toColumn", "")))
        if a is None:
            unseen.append(_rel_name(r))
        if not (a or {}).get("type") or not (b or {}).get("type"):
            untyped.append(_rel_name(r))
        rels.append({"from_table": r["fromTable"], "from_column": r.get("fromColumn", ""), "to_table": r["toTable"],
                     "to_column": r.get("toColumn", ""), "from_type": (a or {}).get("type"),
                     "to_type": (b or {}).get("type"), "active": r.get("isActive", True) is not False})
    return {"kind": "model", "id": "", "tables": tables, "relationships": rels}, untyped, unseen


def inspect(bim: dict[str, Any]) -> dict[str, Any]:
    """Names and counts from a parsed model.bim (see the module docstring). A definition that isn't
    shaped like one gives an ``error``, so its controls are not evaluated."""
    model = bim.get("model") if isinstance(bim, dict) else None
    if not isinstance(model, dict):
        return {"read": True, "error": "The definition has no model."}
    try:
        return _inspect(bim, model)
    except (AttributeError, TypeError, KeyError, ValueError):
        return {"read": True, "error": "The definition isn't shaped like a model.bim, so it couldn't be inspected."}


def _inspect(bim: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    rels = [r for r in model.get("relationships") or [] if isinstance(r, dict)]
    # An inactive relationship filters nothing until a measure turns it on with USERELATIONSHIP.
    both = sorted(_rel_name(r) for r in rels if r.get("isActive", True) is not False
                  and str(r.get("crossFilteringBehavior") or "").lower() == "bothdirections")
    roles, filtered = [], set()
    for role in model.get("roles") or []:
        hits = [_cut(p.get("name")) for p in role.get("tablePermissions") or []
                if str(p.get("filterExpression") or "").strip()]
        if hits:
            roles.append(_cut(role.get("name")))
            filtered.update(hits)
    dl = model_directlake.direct_lake_tables(bim)
    kinds = sorted({(model_directlake.source(bim, t.expression) or {}).get("kind") or "unknown" for t in dl})
    item, untyped, unseen = _item(model)
    settings = {r.id: {"enabled": r.id in RULES} for r in model_practices.RULES}
    rules: dict[str, dict[str, Any]] = {}
    for f in model_practices.check(item, settings)["findings"]:
        got = rules.setdefault(f["rule_id"], {"count": 0, "objects": []})
        got["count"] += 1
        if len(got["objects"]) < MAX_NAMES:
            got["objects"].append(_cut(f["object"]))
    for got in rules.values():
        got["objects"].sort()
    return {"read": True, "kind": "model", "tables": len(item["tables"]), "relationships": len(item["relationships"]),
            "bidirectional": both[:MAX_NAMES], "bidirectional_count": len(both),
            "roles": len(model.get("roles") or []), "row_filter_roles": sorted(roles)[:MAX_NAMES],
            "row_filter_tables": sorted(filtered)[:MAX_NAMES],
            "direct_lake_tables": len(dl), "direct_lake_behavior": model_directlake.fallback_behavior(bim),
            "direct_lake_sources": kinds, "rules": dict(sorted(rules.items())),
            "types_unknown": sorted(untyped)[:MAX_NAMES], "columns_unknown": sorted(unseen)[:MAX_NAMES]}
