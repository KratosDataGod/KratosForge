"""Compile an App Studio specification into a Power BI report definition in PBIR format (AS-12).

The result is the ``parts`` of a Fabric item definition (path -> text) for Create Report, bound to an existing
semantic model by id (``byConnection``, which a REST deployment must use). Nothing here calls Fabric.

Verified against Microsoft Learn (report definition; Power BI Desktop projects, PBIR), 2026-11-03:
``definition.pbir`` version 4.0 with ``byConnection`` ``semanticmodelid=<id>``; PBIR ``definition/`` folder,
not ``report.json`` (mutually exclusive); ``page.json`` and ``visual.json`` shapes and their schema versions;
names of word characters or hyphens; 1,000 pages and 1,000 visuals per page at most.

UNVERIFIED until the first live publish (AS-13): ``version.json`` and ``pages.json`` contents, visual type
names other than ``barChart``, and the query role names other than ``Category`` and ``Y``. These are kept in
VISUALS and SCHEMAS so a correction is one line. What the specification asks for that isn't compiled yet
(filters, drillthrough, theme colours) is listed in ``warnings``, never dropped silently.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import spec as specs
from .spec import SpecError

SCHEMA_ROOT = "https://developer.microsoft.com/json-schemas/fabric/item/report"
SCHEMAS = {
    "pbir": f"{SCHEMA_ROOT}/definitionProperties/2.0.0/schema.json",
    "version": f"{SCHEMA_ROOT}/definition/versionMetadata/1.0.0/schema.json",       # UNVERIFIED version
    "report": f"{SCHEMA_ROOT}/definition/report/3.1.0/schema.json",
    "pages": f"{SCHEMA_ROOT}/definition/pagesMetadata/1.0.0/schema.json",            # UNVERIFIED version
    "page": f"{SCHEMA_ROOT}/definition/page/2.0.0/schema.json",
    "visual": f"{SCHEMA_ROOT}/definition/visualContainer/2.0.0/schema.json",
}
PBIR_VERSION = "4.0"
REPORT_VERSION = "2.0.0"  # definition/version.json; UNVERIFIED
BASE_THEME = {"name": "CY25SU12", "reportVersionAtImport": {"visual": "2.5.0", "report": "3.1.0", "page": "2.3.0"},
              "type": "SharedResources"}

# Spec component type -> (Power BI visualType, role for fields, role for measures). Only barChart with
# Category/Y is shown on Microsoft's page; the rest follow the same pattern and are UNVERIFIED.
VISUALS: dict[str, tuple[str, str, str]] = {
    "bar": ("barChart", "Category", "Y"),
    "column": ("columnChart", "Category", "Y"),
    "line": ("lineChart", "Category", "Y"),
    "area": ("areaChart", "Category", "Y"),
    "pie": ("pieChart", "Category", "Y"),
    "donut": ("donutChart", "Category", "Y"),
    "waterfall": ("waterfallChart", "Category", "Y"),
    "scatter": ("scatterChart", "Category", "Y"),
    "map": ("map", "Category", "Size"),
    "card": ("card", "Values", "Values"),
    "table": ("tableEx", "Values", "Values"),
    "matrix": ("pivotTable", "Rows", "Values"),
    "slicer": ("slicer", "Values", "Values"),
    "text": ("textbox", "", ""),
}
PAGE_WIDTH = 1280
PAGE_HEIGHT = 720
ROW_HEIGHT = 60  # px per grid row; the grid has spec.GRID_COLUMNS columns across the page
MAX_PAGES = 1000
MAX_VISUALS = 1000
_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
_FIELD = re.compile(r"^(?:'(?P<q>[^']+)'|(?P<p>[^\[\]]+))\[(?P<col>[^\[\]]+)\]$")


def _field(ref: str) -> tuple[str, str]:
    m = _FIELD.fullmatch(ref)
    if not m:
        raise SpecError([f"{ref!r} isn't a Table[Column] reference."])
    return (m.group("q") or m.group("p") or "").strip(), m.group("col")


def _projection(kind: str, entity: str, prop: str) -> dict[str, Any]:
    return {"field": {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "queryRef": f"{entity}.{prop}", "active": True}


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def compile_report(spec: dict[str, Any], semantic_model_id: str, measure_tables: dict[str, str]) -> dict[str, Any]:
    """``{"parts": {path: text}, "warnings": [...]}`` for ``spec``. ``measure_tables`` maps each measure's name to
    its home table in the model (from discovery): a measure the model doesn't have is refused here, before
    anything is published. The same spec always gives byte-equal parts."""
    specs.check(spec)
    if spec["target"] != "powerbi-report":
        raise SpecError(["Only a Power BI report specification compiles to PBIR."])
    if not re.fullmatch(r"[0-9a-fA-F\-]{36}", semantic_model_id or ""):
        raise SpecError(["Choose the semantic model the report uses (its id)."])
    measures = {m["id"]: m for m in spec["measures"]}
    missing = sorted({m["name"] for m in spec["measures"] if not measure_tables.get(m["name"])})
    if missing:
        raise SpecError([f"The semantic model has no measure named {n!r}; add it to the model first." for n in missing])
    if len(spec["pages"]) > MAX_PAGES:
        raise SpecError([f"A Power BI report has at most {MAX_PAGES} pages."])

    warnings: list[str] = []
    parts: dict[str, str] = {
        "definition.pbir": _dump({"$schema": SCHEMAS["pbir"], "version": PBIR_VERSION,
                                  "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={semantic_model_id}"}}}),
        "definition/version.json": _dump({"$schema": SCHEMAS["version"], "version": REPORT_VERSION}),
        "definition/report.json": _dump({"$schema": SCHEMAS["report"], "themeCollection": {"baseTheme": BASE_THEME}}),
        "definition/pages/pages.json": _dump({"$schema": SCHEMAS["pages"], "pageOrder": [p["id"] for p in spec["pages"]],
                                              "activePageName": spec["pages"][0]["id"]}),
    }
    col_width = PAGE_WIDTH / specs.GRID_COLUMNS
    for page in spec["pages"]:
        comps = page["components"]
        if len(comps) > MAX_VISUALS:
            raise SpecError([f"Page {page['title']!r} has more than {MAX_VISUALS} visuals."])
        bottom = max((c["layout"]["y"] + c["layout"]["h"] for c in comps), default=0)
        height = max(PAGE_HEIGHT, bottom * ROW_HEIGHT)
        base = f"definition/pages/{page['id']}"
        parts[f"{base}/page.json"] = _dump({"$schema": SCHEMAS["page"], "name": page["id"], "displayName": page["title"],
                                            "displayOption": "FitToPage", "height": height, "width": PAGE_WIDTH})
        for z, c in enumerate(comps):
            if not _NAME.fullmatch(c["id"]):
                raise SpecError([f"{c['id']!r} can't be a Power BI visual name."])
            visual_type, field_role, measure_role = VISUALS[c["type"]]
            lay = c["layout"]
            visual: dict[str, Any] = {"visualType": visual_type}
            roles: dict[str, list[dict[str, Any]]] = {}
            for ref in c.get("fields") or []:
                entity, prop = _field(ref)
                roles.setdefault(field_role, []).append(_projection("Column", entity, prop))
            for mid in c.get("measures") or []:
                name = measures[mid]["name"]
                roles.setdefault(measure_role, []).append(_projection("Measure", measure_tables[name], name))
            if roles:
                visual["query"] = {"queryState": {role: {"projections": projs} for role, projs in roles.items()}}
                visual["drillFilterOtherVisuals"] = True
            if c["type"] == "text":
                warnings.append(f"{c['id']}: text boxes aren't compiled yet; it is an empty text box.")
            parts[f"{base}/visuals/{c['id']}/visual.json"] = _dump({
                "$schema": SCHEMAS["visual"], "name": c["id"],
                "position": {"x": round(lay["x"] * col_width, 2), "y": lay["y"] * ROW_HEIGHT, "z": (z + 1) * 1000,
                             "height": lay["h"] * ROW_HEIGHT, "width": round(lay["w"] * col_width, 2), "tabOrder": (z + 1) * 1000},
                "visual": visual})
    if any(c.get("title") for p in spec["pages"] for c in p["components"]):
        warnings.append("Visual titles aren't compiled yet; Power BI shows each visual's default title.")
    for f in spec["filters"]:
        warnings.append(f"Filter {f['id']} ({f['field']}) isn't compiled yet; use a slicer, or add it in Power BI.")
    for it in spec["interactions"]:
        if it["kind"] != "cross-filter":
            warnings.append(f"{it['kind'].capitalize()} {it['id']} isn't compiled yet; add it in Power BI after publishing.")
    if (spec.get("theme") or {}).get("colors"):
        warnings.append("Theme colours aren't compiled yet; the report uses Power BI's default theme.")
    return {"parts": parts, "warnings": warnings}
