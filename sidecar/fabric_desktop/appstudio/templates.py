"""App Studio templates (AS-15): starting specifications for a new project.

A template is a function of the new app's name and target that returns a valid specification. Templates
read from a ``sample`` source, so a new project previews on this PC at once; connecting a semantic model
replaces the source and keeps the pages. Nothing here calls a model or Fabric.
"""

from __future__ import annotations

from typing import Any, Callable

from . import spec as specs


def _comp(id_: str, type_: str, title: str, measures: list[str], fields: list[str], x: int, y: int, w: int, h: int,
          props: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": id_, "type": type_, "title": title, "measures": measures, "fields": fields,
            "layout": {"x": x, "y": y, "w": w, "h": h}, "props": props or {}}


def _blank(name: str, target: str) -> dict[str, Any]:
    return specs.new(name, target)


def _sales(name: str, target: str) -> dict[str, Any]:
    doc = specs.new(name, target, "Revenue, profit and regions for sales leads")
    doc["audience"] = ["Sales leads"]
    doc["sources"] = [{"id": "src_sales", "kind": "sample", "name": "Sample sales"}]
    doc["measures"] = [
        {"id": "m_rev", "name": "Revenue", "source": "src_sales", "expression": "SUM(Sales[Amount])"},
        {"id": "m_profit", "name": "Profit", "source": "src_sales", "expression": "SUM(Sales[Profit])"},
        {"id": "m_orders", "name": "Orders", "source": "src_sales", "expression": "COUNTROWS(Sales)"}]
    doc["pages"] = [
        {"id": "pg_overview", "title": "Overview", "components": [
            _comp("c_rev", "card", "Revenue", ["m_rev"], [], 0, 0, 3, 2),
            _comp("c_profit", "card", "Profit", ["m_profit"], [], 3, 0, 3, 2),
            _comp("c_orders", "card", "Orders", ["m_orders"], [], 6, 0, 3, 2),
            _comp("c_slicer", "slicer", "Region", [], ["Region[Name]"], 9, 0, 3, 2),
            _comp("c_region", "bar", "Revenue by region", ["m_rev"], ["Region[Name]"], 0, 2, 6, 4, {"sort": "desc"}),
            _comp("c_month", "line", "Revenue by month", ["m_rev"], ["Date[Month]"], 6, 2, 6, 4)]},
        {"id": "pg_products", "title": "Products", "components": [
            _comp("c_cat", "column", "Profit by category", ["m_profit"], ["Product[Category]"], 0, 0, 6, 4),
            _comp("c_table", "table", "Categories", ["m_rev", "m_profit"], ["Product[Category]"], 6, 0, 6, 4)]}]
    doc["filters"] = [{"id": "f_region", "field": "Region[Name]", "pages": "all"}]
    doc["interactions"] = [{"id": "i_region", "kind": "cross-filter", "from": "c_slicer", "to": "c_region"}]
    doc["theme"] = {"colors": {"primary": "#2A78D6"}, "font": "Segoe UI"}
    return doc


def _operations(name: str, target: str) -> dict[str, Any]:
    doc = specs.new(name, target, "Order volume, late deliveries and backlog by site")
    doc["audience"] = ["Operations managers"]
    doc["sources"] = [{"id": "src_ops", "kind": "sample", "name": "Sample operations"}]
    doc["measures"] = [
        {"id": "m_shipped", "name": "Shipped", "source": "src_ops", "expression": "SUM(Orders[Shipped])"},
        {"id": "m_late", "name": "Late", "source": "src_ops", "expression": "SUM(Orders[Late])"},
        {"id": "m_backlog", "name": "Backlog", "source": "src_ops", "expression": "SUM(Orders[Open])"}]
    doc["pages"] = [
        {"id": "pg_today", "title": "Today", "components": [
            _comp("c_shipped", "card", "Shipped", ["m_shipped"], [], 0, 0, 4, 2),
            _comp("c_late", "card", "Late", ["m_late"], [], 4, 0, 4, 2),
            _comp("c_backlog", "card", "Backlog", ["m_backlog"], [], 8, 0, 4, 2),
            _comp("c_site", "column", "Late by site", ["m_late"], ["Site[Name]"], 0, 2, 6, 4),
            _comp("c_trend", "area", "Shipped by month", ["m_shipped"], ["Date[Month]"], 6, 2, 6, 4)]}]
    doc["theme"] = {"colors": {"primary": "#1BAF7A"}, "font": "Segoe UI"}
    return doc


TEMPLATES: dict[str, tuple[str, str, Callable[[str, str], dict[str, Any]]]] = {
    "blank": ("Blank", "One empty page. Describe the app, or add components in Design.", _blank),
    "sales": ("Sales overview", "Revenue, profit and orders with a region slicer, by month and by product.", _sales),
    "operations": ("Operations status", "Shipped, late and backlog cards with late orders by site.", _operations),
}


def catalog() -> list[dict[str, str]]:
    return [{"id": k, "title": title, "text": text} for k, (title, text, _build) in TEMPLATES.items()]


def build(template: str, name: str, target: str = "powerbi-report") -> dict[str, Any]:
    """A new specification from ``template``. Raises SpecError when the target can't express it."""
    if template not in TEMPLATES:
        raise ValueError(f"Choose a template: {', '.join(TEMPLATES)}.")
    if target not in specs.TARGETS:
        raise ValueError(f"target must be one of: {', '.join(sorted(specs.TARGETS))}.")
    doc = TEMPLATES[template][2](name, target)
    if target != "powerbi-report":  # interactions a KratosForge app can't express are left out, not simulated
        doc["interactions"] = [i for i in doc["interactions"] if i["kind"] in specs.TARGETS[target]["interactions"]]
    specs.check(doc)
    return doc
