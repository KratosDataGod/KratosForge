"""Snowflake export profile (OSSIE-010a, engine only): an Ossie 0.2.0.dev0 document as Snowflake's Ossie 0.1.1.

``to_snowflake(doc_or_text)`` returns the YAML of one 0.1.1 file, ``version: 0.1.1`` with the model as the one item
of a ``semantic_model`` list (the shape ``SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW`` writes, as the
``snowflake-ossie`` profile reads it), and a report. Each report row has a fidelity class (``model_ossie.CLASSES``),
its label, the path in the output, a message and a remediation:

- the whole file is Unverified: no 0.1.1 schema is pinned and Snowflake hasn't read it. The report's
  ``validation`` is the output read back through ``ossie_validate.validate``;
- ``custom_extensions`` data that isn't text is written as JSON text (Preserved through extensions); an entry with
  no ``vendor_name`` or no ``data``, or a ``custom_extensions`` that isn't a list, is Unsupported and left out;
- a field or metric with no ``SNOWFLAKE`` or ``ANSI_SQL`` expression is Unsupported and listed in
  ``skipped_by_snowflake``, because Snowflake skips it silently. It stays in the file, so other tools still read it;
- an ``OSSIE_SQL_2026`` expression is translated to Snowflake SQL by sqlglot on this PC (``translate``) when it is
  one plain expression sqlglot fully knows, and added as a ``SNOWFLAKE`` entry beside it (Unverified).

Everything else is left as it is: ``datatype``, ``ai_context``, other dialect entries (DAX among them), metrics
inside datasets and vendor names. Whether 0.1.1 accepts each of these is for the live check (OSSIE-010b). Nothing
is fetched or executed here, and Snowflake is never called.
"""

from __future__ import annotations

import copy
import functools
import json
from typing import Any

from . import model_ossie as mo, ossie_profiles as op, ossie_validate as ov, sql_dialects

V011 = op.V011
SNOWFLAKE_DIALECTS = ("SNOWFLAKE", "ANSI_SQL")  # what Snowflake reads; anything else it skips
OSSIE_SQL = "OSSIE_SQL_2026"
MAX_REASON = 80
_FN_NAME = 24  # a function's name in a reason, cut short so the reason stays under MAX_REASON
_VERIFY = ("Check it with SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML and verify_only (OSSIE-010b) before "
           "relying on it.")


# ---- OSSIE_SQL_2026 to Snowflake SQL

def _refused(node: Any) -> bool:
    """A statement, a query or a list of statements (sqlglot reads ``a; b`` as a Block), which no field or metric
    expression is."""
    from sqlglot import exp
    return isinstance(node, (exp.Query, exp.Block, exp.DDL, exp.DML, exp.Command))


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def translate(sql: Any) -> tuple[str | None, str]:
    """``sql`` (an OSSIE_SQL_2026 expression) as Snowflake SQL with an empty reason, or None and a short reason (at most
    MAX_REASON characters, never quoting the expression). sqlglot reads it as generic SQL and writes Snowflake SQL,
    and both steps must succeed in full: one expression (no statement, query or ``;``), no function sqlglot doesn't
    know (an Anonymous call would be copied as written, unchecked) and nothing Snowflake SQL can't express. Comments
    are dropped, so no comment text reaches the semantic view whatever sqlglot's comment escaping does."""
    from sqlglot import exp
    from sqlglot.errors import ErrorLevel, SqlglotError, UnsupportedError
    if not isinstance(sql, str) or not sql.strip():
        return None, "is empty"
    try:
        tree = sql_dialects.parse_one(sql, error_level=ErrorLevel.RAISE)
    except SqlglotError:  # narrow: ParseError and TokenError; their text quotes the expression, so it isn't shown
        return None, "couldn't be read as SQL: check its brackets, quotes and operators"
    if tree is None:
        return None, "is empty"
    if not isinstance(tree, exp.Condition) or any(_refused(n) for n in tree.walk()):
        return None, "isn't one expression: it holds a statement, a query or a ;"
    unknown = list(dict.fromkeys(_cut(str(n.name), _FN_NAME) for n in tree.find_all(exp.Anonymous)))
    if unknown:
        names = " and ".join(unknown[:2]) + (f" and {len(unknown) - 2} more" if len(unknown) > 2 else "")
        return None, _cut(f"calls {names}, which sqlglot doesn't know", MAX_REASON)
    try:
        out = tree.sql(dialect="snowflake", comments=False, unsupported_level=ErrorLevel.RAISE)
    except UnsupportedError:  # narrow: sqlglot can't write part of it as Snowflake SQL
        return None, "uses something sqlglot can't write as Snowflake SQL"
    except RecursionError:  # narrow: nested deeper than sqlglot can write
        return None, "is nested too deeply to translate on this PC"
    if not out.strip():
        return None, "is empty"
    return out, ""


# ---- the conversion

def _read(doc_or_text: Any) -> dict[str, Any]:
    """The input as a copy of one Ossie 0.2.0.dev0 document; a plain ValueError for anything else."""
    try:
        return _read_doc(doc_or_text)
    except op.UnknownProfile as exc:  # narrow: the named version refusal, raised as a plain ValueError
        raise ValueError(str(exc)) from None


def _read_doc(doc_or_text: Any) -> dict[str, Any]:
    mo.require()
    if isinstance(doc_or_text, bytes):
        try:
            doc_or_text = doc_or_text.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("This file isn't UTF-8 text") from None
    if isinstance(doc_or_text, str):
        try:
            doc = mo.parse(doc_or_text)
        except TypeError as exc:  # narrow: parse's refusal of a file that isn't one object, as a ValueError
            raise ValueError(str(exc)) from None
    elif isinstance(doc_or_text, dict):
        doc = doc_or_text
    else:
        raise ValueError("The Snowflake export takes an Ossie document: a dict, or its YAML or JSON text")
    shape = ov._shape(doc)
    if shape:
        raise ValueError(f"This document can't be exported: {shape[0]['path']} {shape[0]['message']}")
    profile = op.detect_profile(doc)  # UnknownProfile, a ValueError naming the version
    if profile != "ossie-0.2":
        raise ValueError(f"The Snowflake export takes Ossie {op.V02}; this file is {op.PROFILES[profile]['label']} "
                         f"(profile {profile})")
    if not doc.get("name") or not isinstance(doc.get("datasets"), list) or not doc["datasets"]:
        raise ValueError("An Ossie model needs a name and at least one dataset")
    return copy.deepcopy(doc)


def _row(cls: str, kind: str, obj: str, parts: tuple[Any, ...], message: str, remediation: str,
         rule: str = "") -> dict[str, Any]:
    return {"object": obj, "kind": kind, "class": cls, "label": mo.CLASSES[cls], "path": ov.json_path(parts),
            "_parts": parts, "message": message, "remediation": remediation, "rule": rule}


def _dicts(value: Any) -> list[tuple[int, dict[str, Any]]]:
    return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)] if isinstance(value, list) else []


def _owner(parts: tuple[Any, ...], model: dict[str, Any]) -> str:
    """The name of the object at ``parts`` (inside the model), for a row: dataset, dataset.field, or a metric's."""
    names = []
    node: Any = model
    for p in parts:
        node = node[p] if isinstance(node, (dict, list)) else None
        if isinstance(p, int) and isinstance(node, dict):
            names.append(mo._short(node.get("name", f"#{p}")))
    return ".".join(names) or mo._short(model.get("name"))


def _extensions(model: dict[str, Any], base: tuple[Any, ...], rows: list[dict[str, Any]]) -> None:
    """Every ``custom_extensions`` in the model made what 0.1.1 takes: data as text, entries with a vendor and data."""
    stack: list[tuple[tuple[Any, ...], Any]] = [((), model)]
    while stack:  # no recursion: _shape already bounded the depth
        parts, value = stack.pop()
        if isinstance(value, list):
            stack.extend(((*parts, i), v) for i, v in enumerate(value) if isinstance(v, (dict, list)))
            continue
        if not isinstance(value, dict):
            continue
        for key, v in list(value.items()):
            if key != "custom_extensions":
                if isinstance(v, (dict, list)):
                    stack.append(((*parts, key), v))
                continue
            owner = _owner(parts, model)
            at = (*base, *parts, key)
            if not isinstance(v, list):
                del value[key]
                rows.append(_row("unsupported", "extension", owner, at,
                                 "custom_extensions isn't a list, so it was left out",
                                 "Write custom_extensions as a list of vendor_name and data entries.",
                                 "snowflake/extensions"))
                continue
            kept: list[Any] = []
            for i, entry in enumerate(v):
                vendor = entry.get("vendor_name") if isinstance(entry, dict) else None
                data = entry.get("data") if isinstance(entry, dict) else None
                if not isinstance(vendor, str) or not vendor or data is None:
                    missing = "isn't an object" if not isinstance(entry, dict) else \
                        "has no vendor_name" if not isinstance(vendor, str) or not vendor else "has no data"
                    rows.append(_row("unsupported", "extension", owner, (*at, i),
                                     f"this extension {missing}, so it was left out",
                                     "Give each extension a vendor_name and its data.", "snowflake/extensions"))
                    continue
                if not isinstance(data, str):
                    entry["data"] = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
                    rows.append(_row("preserved_through_extensions", "extension", owner, (*at, len(kept), "data"),
                                     f"{mo._q(vendor)} data written as JSON text: 0.1.1 extension data is a string",
                                     "Nothing to change; a tool reading this vendor's data parses the JSON text.",
                                     "snowflake/extensions"))
                kept.append(entry)
            if kept:
                value[key] = kept
            else:
                del value[key]


def _things(model: dict[str, Any]) -> list[tuple[str, str, tuple[Any, ...], dict[str, Any]]]:
    """The model's fields and metrics (model-level and inside datasets) with their kind, name and path."""
    found: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = [
        ("metric", mo._short(m.get("name")), ("metrics", i), m) for i, m in _dicts(model.get("metrics"))]
    for di, d in _dicts(model.get("datasets")):
        ds = mo._short(d.get("name"))
        found += [("field", f"{ds}.{mo._short(f.get('name'))}", ("datasets", di, "fields", i), f)
                  for i, f in _dicts(d.get("fields"))]
        found += [("metric", f"{ds}.{mo._short(m.get('name'))}", ("datasets", di, "metrics", i), m)
                  for i, m in _dicts(d.get("metrics"))]
    return found


def _expressions(model: dict[str, Any], base: tuple[Any, ...], translate_sql: bool, rows: list[dict[str, Any]]) -> None:
    """Each field and metric made readable by Snowflake where it can be, and named where it can't."""
    for kind, name, parts, obj in _things(model):
        at = (*base, *parts, "expression")
        expression = obj.get("expression")
        entries = expression.get("dialects") if isinstance(expression, dict) else None
        entries = entries if isinstance(entries, list) else []
        dialects = [e.get("dialect") for e in entries if isinstance(e, dict)]
        if any(d in SNOWFLAKE_DIALECTS for d in dialects):
            continue
        ossie = next((e for e in entries if isinstance(e, dict) and e.get("dialect") == OSSIE_SQL), None)
        why = ""
        if ossie is not None and not translate_sql:
            why = f"; its {OSSIE_SQL} expression wasn't translated (translation is off)"
        elif ossie is not None:
            sql, reason = translate(ossie.get("expression"))
            if sql is not None:
                entries.append({"dialect": "SNOWFLAKE", "expression": sql})
                rows.append(_row("unverified", kind, name, (*at, "dialects", len(entries) - 1),
                                 f"{OSSIE_SQL} translated to Snowflake SQL on this PC; Snowflake hasn't run it",
                                 _VERIFY, "snowflake/translated"))
                continue
            why = f"; its {OSSIE_SQL} expression {reason}"
        has = sorted({str(d) for d in dialects if isinstance(d, str)})
        shown = f" (it has {', '.join(mo._short(d) for d in has[:3])})" if has else ""
        rows.append(_row("unsupported", kind, name, at,
                         f"has no SNOWFLAKE or ANSI_SQL expression{shown}, so Snowflake silently skips this "
                         f"{kind}{why}. It is left in the file for other tools",
                         "Add a dialects entry with dialect: SNOWFLAKE or ANSI_SQL.", "snowflake/dialect"))


@functools.lru_cache(maxsize=1)
def _dumper() -> Any:
    """The vendored converter's pure-Python dumper (through semantic_view's line-break fix) that never writes an
    alias, so an object the document holds twice is written out twice."""
    from . import semantic_view

    class _NoAliasDumper(semantic_view._dumper()[0]):  # type: ignore[misc]
        def ignore_aliases(self, data: Any) -> bool:
            return True

    return _NoAliasDumper


def _dump(doc: dict[str, Any]) -> str:
    text: str = mo.yaml.dump(doc, Dumper=_dumper(), sort_keys=False, default_flow_style=False, allow_unicode=True,
                             width=float("inf"))
    return text


def to_snowflake(doc_or_text: Any, translate_ossie_sql: bool = True) -> dict[str, Any]:
    """An Ossie 0.2.0.dev0 document (a dict, or YAML or JSON text) as Snowflake's Ossie 0.1.1: ``yaml`` and
    ``report``. The input is never changed. ``translate_ossie_sql`` False leaves OSSIE_SQL_2026 untranslated.
    Anything that isn't a 0.2.0.dev0 model is a ValueError with a plain reason."""
    source = _read(doc_or_text)
    model = {k: v for k, v in source.items() if k != "version"}
    base: tuple[Any, ...] = ("semantic_model", 0)
    rows: list[dict[str, Any]] = []
    _extensions(model, base, rows)
    _expressions(model, base, translate_ossie_sql, rows)
    out = {"version": V011, "semantic_model": [model]}
    text = _dump(out)
    rows.sort(key=lambda r: ov._sort_key({"_parts": r["_parts"], "rule": r["rule"], "message": r["message"]}))
    name = mo._short(model.get("name"))
    rows.insert(0, _row("unverified", "model", name, (),
                        f"written as Snowflake's Ossie {V011}; no {V011} schema is pinned and Snowflake hasn't read "
                        "this file, so the whole file is Unverified", _VERIFY, "unverified"))
    for r in rows:
        r.pop("_parts")
    skipped = [{"object": r["object"], "kind": r["kind"], "path": r["path"]} for r in rows
               if r["rule"] == "snowflake/dialect"]
    counts = {c: sum(1 for r in rows if r["class"] == c) for c in mo.CLASSES}
    return {"yaml": text, "report": {
        "profile": "snowflake-ossie", "status": "unverified",
        "source": {"format": "Apache Ossie", "version": op.V02},
        "destination": {"format": "Snowflake Ossie", "version": V011, "profile": "snowflake-ossie"},
        "translate_ossie_sql": bool(translate_ossie_sql), "rows": rows, "counts": counts,
        "skipped_by_snowflake": skipped, "validation": ov.validate(text)}}


__all__ = ["MAX_REASON", "SNOWFLAKE_DIALECTS", "to_snowflake", "translate"]
