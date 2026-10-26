"""The notebook, SQL and semantic model best-practice checkers, registered with governance_checks.

A governance policy names rule ids (NB003, SQ005, ...) in its Checked by field; Check now hands each
checker the items of its kind with settings for only those rules. These adapters run the checks in
notebook_practices, sqlcompat/practices and model_practices with every other rule off, so a policy reports only the
rules it names.

Items:
    notebook  {kind: "notebook", id: <notebook name>, sources: [code cell sources], scheduled: bool}
    sql       {kind: "sql", id: <script name>, query: <T-SQL>, target: "warehouse"|"endpoint"|"sqldb"}
    model     model_practices.model_item: the layer's model as Update model writes it
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import governance_checks as gc
from . import model_practices, notebook_practices
from .sqlcompat import practices as sql_practices


def _only(rule_ids: list[str], settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The checker's settings: the rules a policy turned on as it says, every other rule off."""
    out: dict[str, dict[str, Any]] = {rid: {"enabled": False} for rid in rule_ids}
    for rid, s in settings.items():
        if rid in out and isinstance(s, dict):
            out[rid] = {"enabled": bool(s.get("enabled", True)), "severity": s.get("severity")}
    return out


def _adapter(kind: str, rule_ids: Callable[[], list[str]],
             run: Callable[[dict[str, Any], dict[str, dict[str, Any]]], list[dict[str, Any]]]):
    def check(item: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]] | None:
        ids = rule_ids()
        if item.get("kind") != kind or not any(rid in settings for rid in ids):
            return None
        own = _only(ids, settings)
        if not any(s["enabled"] for s in own.values()):
            return None
        found = run(item, own)
        for f in found:
            f["object"] = f"{item.get('id', '')}: {f['object']}"
            f["url"] = f["url"] or gc.DOCS
        return found
    return check


def _notebook(item: dict[str, Any], settings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sources = [s for s in item.get("sources") or [] if isinstance(s, str)]
    return notebook_practices.check(sources, bool(item.get("scheduled")), settings)["findings"]


def _sql(item: dict[str, Any], settings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    target = item.get("target") if item.get("target") in sql_practices.TARGETS else "warehouse"
    return sql_practices.check(str(item.get("query") or ""), target, settings)["findings"]


def _model(item: dict[str, Any], settings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return model_practices.check(item, settings)["findings"]


def _ids(rules: Callable[[], list[dict[str, Any]]]) -> Callable[[], list[str]]:
    return lambda: [r["id"] for r in rules()]


gc.register("notebook", notebook_practices.rules, _adapter("notebook", _ids(notebook_practices.rules), _notebook))
gc.register("sql", sql_practices.rules, _adapter("sql", _ids(sql_practices.rules), _sql))
gc.register("model", model_practices.rules, _adapter("model", _ids(model_practices.rules), _model))
