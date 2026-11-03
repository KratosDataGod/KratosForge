"""What App Studio may tell a model about a semantic model (AS-09): names, types and descriptions, never rows.

``from_bim`` turns a model's TMSL definition (the ``model.bim`` Fabric returns for getDefinition as TMSL, the
same read the Direct Lake check and Ossie export already use) into sanitized metadata: tables, columns,
measures and relationships. Left out on purpose: partitions and expressions (their M or SQL can name
servers, paths and credentials), data sources, roles and their members, annotations, perspectives and
anything else not listed here. Every text passes redaction and is cut to a bounded length, and the totals
are capped so one huge model can't flood a request.

Measure expressions (DAX) are included only when asked, so "explain how revenue is calculated" can work;
they are redacted like everything else. This is the payload ``providers.build_messages`` sends as
``{"kind": "metadata"}`` after the person's consent.
"""

from __future__ import annotations

from typing import Any

from .. import redact

MAX_TABLES = 300
MAX_COLUMNS = 4000
MAX_MEASURES = 2000
MAX_RELATIONSHIPS = 1000
NAME_LIMIT = 128
DESCRIPTION_LIMIT = 300
EXPRESSION_LIMIT = 2000


def _text(value: Any, limit: int) -> str:
    if isinstance(value, list):  # TMSL keeps multi-line text as a list of lines
        value = "\n".join(str(v) for v in value)
    if not isinstance(value, str):
        return ""
    clean = redact.secrets(value).strip()
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"


def _name(value: Any) -> str:
    return redact.one_line(value, NAME_LIMIT) if isinstance(value, str) else ""


def from_bim(bim: dict[str, Any], include_expressions: bool = False) -> dict[str, Any]:
    """Sanitized metadata for a parsed model.bim. ``truncated`` says when a cap was reached."""
    model = bim.get("model") if isinstance(bim, dict) else None
    if not isinstance(model, dict):
        raise ValueError("That isn't a semantic model definition (no model in model.bim).")
    tables: list[dict[str, Any]] = []
    counts = {"columns": 0, "measures": 0}
    truncated = False
    for t in model.get("tables") or []:
        if not isinstance(t, dict) or not _name(t.get("name")):
            continue
        if len(tables) >= MAX_TABLES:
            truncated = True
            break
        columns = []
        for c in t.get("columns") or []:
            if not isinstance(c, dict) or c.get("type") == "rowNumber" or not _name(c.get("name")):
                continue
            if counts["columns"] >= MAX_COLUMNS:
                truncated = True
                break
            counts["columns"] += 1
            col = {"name": _name(c["name"]), "type": _name(c.get("dataType") or "string")}
            if c.get("isHidden"):
                col["hidden"] = True
            if c.get("description"):
                col["description"] = _text(c["description"], DESCRIPTION_LIMIT)
            columns.append(col)
        measures = []
        for m in t.get("measures") or []:
            if not isinstance(m, dict) or not _name(m.get("name")):
                continue
            if counts["measures"] >= MAX_MEASURES:
                truncated = True
                break
            counts["measures"] += 1
            meas: dict[str, Any] = {"name": _name(m["name"])}
            if m.get("isHidden"):
                meas["hidden"] = True
            if m.get("description"):
                meas["description"] = _text(m["description"], DESCRIPTION_LIMIT)
            if m.get("formatString"):
                meas["format"] = _name(m["formatString"])
            if include_expressions and m.get("expression"):
                meas["expression"] = _text(m["expression"], EXPRESSION_LIMIT)
            measures.append(meas)
        table: dict[str, Any] = {"name": _name(t["name"]), "columns": columns, "measures": measures}
        if t.get("isHidden"):
            table["hidden"] = True
        if t.get("description"):
            table["description"] = _text(t["description"], DESCRIPTION_LIMIT)
        tables.append(table)
    relationships = []
    for r in model.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        if len(relationships) >= MAX_RELATIONSHIPS:
            truncated = True
            break
        ends = [_name(r.get(k)) for k in ("fromTable", "fromColumn", "toTable", "toColumn")]
        if all(ends):
            rel = {"from": f"{ends[0]}[{ends[1]}]", "to": f"{ends[2]}[{ends[3]}]"}
            if r.get("isActive") is False:
                rel["active"] = False
            relationships.append(rel)
    return {"tables": tables, "relationships": relationships, "truncated": truncated}


def measure_tables(metadata: dict[str, Any]) -> dict[str, str]:
    """Measure name -> its home table, what the report compiler needs to reference a measure."""
    return {m["name"]: t["name"] for t in metadata.get("tables") or [] for m in t.get("measures") or []}


def columns(metadata: dict[str, Any]) -> set[tuple[str, str]]:
    """Every (table, column) the model has, so a generated specification can be checked against it."""
    return {(t["name"], c["name"]) for t in metadata.get("tables") or [] for c in t.get("columns") or []}
