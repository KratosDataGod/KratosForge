"""Semantic models to and from Apache Ossie, the open semantic model format (formerly Open Semantic Interchange).

A model travels as TMSL (``model.bim``), which Fabric's getDefinition and updateDefinition read and write, and
the vendored Apache Ossie Microsoft converter (``_vendor/ossie_microsoft``) turns that into an Ossie YAML document
and back, on this PC. Power BI details Ossie has no place for (security roles, hierarchies, format strings,
lineage tags) travel in the document's ``POWER_BI`` extension, so a model sent to Ossie and back is the same model;
other tools don't see them. Whatever can't be carried is reported by name, never dropped silently.

Importing into an existing model is an update by default: the file's tables, columns, measures and relationships
are laid over the model, and what the file doesn't mention stays (its roles, hierarchies, measures written in
Fabric) unless the caller names it in ``remove``. Lineage tags are kept by name, so reports keep binding.
"""

from __future__ import annotations

import copy
import json
import threading
import warnings
from typing import Any

import sqlglot
from sqlglot import exp

from . import sql_dialects

# PyYAML is needed only here and in the vendored converter. Both load on first use, not at engine start (they add
# about 25 ms to every start, and service.py imports this module on every start). Without PyYAML (an engine installed
# before the converter needed it, or a package removed by hand) Export to Ossie and Import from Ossie say what's
# missing, and the rest of the engine still works. These names are set by _load(); reading model_ossie.MISSING or
# model_ossie.OSSIE_VERSION from outside loads them first (see __getattr__ below).
yaml: Any
OSSIE_VERSION: str
ConversionError: type[Exception]
convert_ossie_to_semantic_model: Any
convert_semantic_model_to_ossie: Any
_NoAliasLoader: Any
MISSING: str
_LOAD_LOCK = threading.Lock()


def _load() -> None:
    """Import PyYAML and the converter once, setting MISSING to "" or to the package that is missing."""
    global yaml, OSSIE_VERSION, ConversionError, convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
    global _NoAliasLoader, MISSING
    with _LOAD_LOCK:
        if "MISSING" in globals():
            return
        try:
            import yaml as _yaml

            from ._vendor.ossie_microsoft import (
                OSSIE_VERSION as _version,
                ConversionError as _error,
                convert_ossie_to_semantic_model as _to_model,
                convert_semantic_model_to_ossie as _to_ossie,
            )
        except ImportError as exc:  # narrow: a missing or broken package; the reason is kept in MISSING and shown
            MISSING = {"yaml": "PyYAML"}.get((exc.name or "").partition(".")[0], exc.name or "") or str(exc)
            return

        class _NoAlias(_yaml.SafeLoader):
            """YAML without anchors and aliases (``*name``). Ossie files don't need them, and nested aliases let a
            few hundred bytes expand into a model of gigabytes once it is written out as JSON."""

            def compose_node(self, parent: Any, index: Any) -> Any:
                if self.check_event(_yaml.AliasEvent):
                    event = self.peek_event()
                    raise ValueError(f"This file repeats parts of itself with YAML aliases (line {event.start_mark.line + 1}); "
                                     "write them out in full")
                return super().compose_node(parent, index)

        yaml, OSSIE_VERSION, ConversionError = _yaml, _version, _error
        convert_ossie_to_semantic_model, convert_semantic_model_to_ossie = _to_model, _to_ossie
        _NoAliasLoader = _NoAlias
        MISSING = ""  # last: its presence means the names above are set


def __getattr__(name: str) -> Any:
    """``model_ossie.MISSING``, ``model_ossie.OSSIE_VERSION`` and the other loaded names, loading them first."""
    if name in __annotations__ and name not in globals():
        _load()
        if name in globals():
            return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


PLACEHOLDER_ANNOTATION = "OssieExpression"  # the converter keeps the Ossie expression here, translated or not
_MODEL_KEPT = ("roles", "perspectives", "cultures", "expressions", "dataSources", "queryGroups", "annotations")
# What an Ossie file says about a table, column or measure whether or not it has a Power BI section: when the file
# doesn't set one of these, the object doesn't have it. lineageTag and partitions merge takes from the model, and
# annotations it merges by name. Every other property the model has and the file doesn't set is carried over.
_OSSIE_OWNS = {
    "table": frozenset({"name", "description", "columns", "measures", "partitions", "lineageTag", "annotations"}),
    "column": frozenset({"name", "description", "sourceColumn", "type", "expression", "dataType", "isKey", "isUnique",
                         "lineageTag", "annotations"}),
    "measure": frozenset({"name", "description", "expression", "lineageTag", "annotations"}),
}
# The annotations the converter writes from the Ossie file itself (AI context, an expression and its dialect).
_OSSIE_ANNOTATIONS = frozenset({"OssieAIContext", "OssieExpressionDialect", PLACEHOLDER_ANNOTATION})
_TABLE_ALWAYS_KEPT = ("hierarchies", "calculationGroup", "refreshPolicy")  # kept even over a Power BI section
# warnings.catch_warnings swaps process-wide state, so conversions run one at a time.
_LOCK = threading.Lock()
# A large real model is a few MB of YAML; this keeps a mistaken pick (a data file) from filling memory.
MAX_CHARS = 20_000_000


def unavailable() -> str:
    """Why Export to Ossie and Import from Ossie can't run on this engine, or "" when they can."""
    _load()
    if not MISSING:
        return ""
    return (f"Export to Ossie and Import from Ossie need the Python package {MISSING}, which this engine doesn't have. "
            "Everything else works. In a build from source, run python -m pip install -e . -c constraints.txt in the "
            "sidecar folder, then restart the app; otherwise reinstall KratosForge.")


def require() -> None:
    """Raise with the reason when the Ossie converter can't run on this engine."""
    reason = unavailable()
    if reason:
        raise RuntimeError(reason)


def _convert(fn: Any, *args: Any, **kwargs: Any) -> tuple[Any, list[dict[str, str]]]:
    """Run a converter call and collect what it reported: ``kept`` (in the Power BI section, for Fabric only)
    or ``lost`` (not carried across)."""
    with _LOCK, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fn(*args, **kwargs)
    report = []
    for w in caught:
        text = str(w.message)
        scope, _, message = text[1:].partition("] ") if text.startswith("[") else ("model", "", text)
        report.append({"object": scope, "message": message or text,
                       "kind": "kept" if "preserved" in text or "kept as-is" in text else "lost"})
    return result, report


def parse(text: str) -> dict[str, Any]:
    """An Ossie document (YAML or JSON) as a dict, refusing what isn't one, with a plain reason."""
    require()
    if len(text) > MAX_CHARS:
        raise ValueError(f"This file is {len(text) // 1_000_000} MB; an Ossie model is at most {MAX_CHARS // 1_000_000} MB")
    try:
        doc = yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader that also refuses aliases
    except yaml.YAMLError as exc:
        raise ValueError(f"This isn't valid YAML or JSON: {exc}") from exc
    except RecursionError:
        raise ValueError("This file is nested too deeply to be an Ossie model") from None
    if not isinstance(doc, dict):
        raise TypeError("This isn't an Ossie semantic model: it should be one object with version, name and datasets")
    if "semantic_model" in doc:
        raise ValueError("This file uses the older Ossie layout with a semantic_model list. "
                         f"Ossie {OSSIE_VERSION} puts one model's name and datasets at the top of the file.")
    version = str(doc.get("version") or "")
    if version != OSSIE_VERSION:
        raise ValueError(f"This file is Ossie version {version or '(none)'}; this app reads version {OSSIE_VERSION}.")
    if not doc.get("name") or not isinstance(doc.get("datasets"), list) or not doc["datasets"]:
        raise ValueError("An Ossie model needs a name and at least one dataset")
    return doc


def export(bim: dict[str, Any], translate_dax: bool = True) -> dict[str, Any]:
    """A model (parsed ``model.bim``) as Ossie YAML, with counts and the conversion report.

    ``translate_dax`` also writes single-column aggregate measures (SUM, AVERAGE, MIN, MAX, COUNTA,
    DISTINCTCOUNTNOBLANK) as portable SQL, keeping the DAX beside it."""
    require()
    text, report = _convert(convert_semantic_model_to_ossie, bim, translate_dax=translate_dax)
    doc = yaml.safe_load(text)
    datasets = doc.get("datasets") or []
    metrics = list(doc.get("metrics") or []) + [m for d in datasets for m in d.get("metrics") or []]
    portable = sum(1 for m in metrics if any(d.get("dialect") != "DAX"
                                             for d in (m.get("expression") or {}).get("dialects") or []))
    return {"yaml": text, "name": doc.get("name", ""), "version": doc.get("version", ""), "report": report,
            "counts": {"datasets": len(datasets), "fields": sum(len(d.get("fields") or []) for d in datasets),
                       "metrics": len(metrics), "portable_metrics": portable,
                       "relationships": len(doc.get("relationships") or [])}}


_SQL_DIALECTS = {"ANSI_SQL": None, "OSSIE_SQL_2026": None, "SNOWFLAKE": "snowflake", "DATABRICKS": "databricks",
                 "BIGQUERY": "bigquery"}


def _qualified(expression: str, dataset: str, dialect: str | None) -> str:
    """A dataset-scoped SQL metric's unqualified field references written as ``dataset.field``."""
    tree = sql_dialects.parse_one(expression, read=dialect)
    for col in tree.find_all(exp.Column):
        if not col.table:
            col.set("table", exp.to_identifier(dataset))
    return tree.sql(dialect=dialect)


def _lift_dataset_metrics(doc: dict[str, Any], report: list[dict[str, str]]) -> dict[str, Any]:
    """Dataset-scoped metrics (``datasets[].metrics``) moved to the model's metrics with their dataset as the
    measure's home table: the converter reads only model-level metrics and would leave them out."""
    doc = copy.deepcopy(doc)
    root = doc.setdefault("metrics", [])
    taken = {m.get("name") for m in root if isinstance(m, dict)}
    for d in doc.get("datasets") or []:
        for m in d.pop("metrics", None) or []:
            if not isinstance(m, dict) or not m.get("name"):
                continue
            scope = f"dataset '{d.get('name')}' metric '{m['name']}'"
            if m["name"] in taken:
                report.append({"object": scope, "kind": "lost",
                               "message": "a model-level metric has the same name, so this one was left out"})
                continue
            for entry in (m.get("expression") or {}).get("dialects") or []:
                if entry.get("dialect") in _SQL_DIALECTS:
                    try:
                        entry["expression"] = _qualified(str(entry.get("expression") or ""), d["name"],
                                                         _SQL_DIALECTS[entry["dialect"]])
                    except sqlglot.errors.ParseError:
                        pass  # left as written: the converter reports SQL it can't translate
            stash = next((e for e in m.get("custom_extensions") or [] if e.get("vendor_name") == "POWER_BI"), None)
            data = json.loads(stash["data"]) if stash else {"_v": 2}
            data.setdefault("table", d["name"])
            m["custom_extensions"] = [e for e in m.get("custom_extensions") or [] if e is not stash] + \
                [{"vendor_name": "POWER_BI", "data": json.dumps(data, sort_keys=True)}]
            root.append(m)
            taken.add(m["name"])
    if not root:
        del doc["metrics"]
    return doc


def to_tmsl(text: str, source: dict[str, str] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """An Ossie document as a parsed ``model.bim``, and the report. ``source`` is the lakehouse new Direct Lake
    tables read: ``{"workspaceId": ..., "itemId": ...}``."""
    lifted: list[dict[str, str]] = []
    doc = _lift_dataset_metrics(parse(text), lifted)
    try:
        bim, report = _convert(convert_ossie_to_semantic_model, yaml.safe_dump(doc, sort_keys=False), source=source)
    except ConversionError as exc:
        raise ValueError(f"The Ossie file couldn't be converted: {exc}") from exc
    # The caller always passes the lakehouse when it has one; without it the ids are placeholders it reports itself.
    report = lifted + [r for r in report if "placeholder ids" not in r["message"]]
    return bim, report


def _has_stash(obj: Any) -> bool:
    return isinstance(obj, dict) and any(isinstance(e, dict) and e.get("vendor_name") == "POWER_BI"
                                         for e in obj.get("custom_extensions") or [])


def _stash_data(obj: dict[str, Any]) -> dict[str, Any]:
    for e in obj.get("custom_extensions") or []:
        if isinstance(e, dict) and e.get("vendor_name") == "POWER_BI":
            try:
                data = json.loads(e.get("data") or "{}")
            except (TypeError, json.JSONDecodeError):
                return {}  # the converter refuses such a file with the reason before this matters
            return data if isinstance(data, dict) else {}
    return {}


def stashed(text: str) -> set[str]:
    """The tables (``"table"``), columns and measures (``"table[name]"``) the Ossie document gives a Power BI section
    (a POWER_BI custom extension, as Export to Ossie writes for each object with Fabric-only properties). For those
    the file speaks for every property, so ``merge`` takes the file's set; for the rest it carries the model's
    Fabric-only properties over."""
    doc = parse(text)
    datasets = [d for d in doc.get("datasets") or [] if isinstance(d, dict) and d.get("name")]
    found: set[str] = set()
    for d in datasets:
        if _has_stash(d):
            found.add(str(d["name"]))
        for f in d.get("fields") or []:
            if _has_stash(f) and f.get("name"):
                found.add(f"{d['name']}[{f['name']}]")
        for m in d.get("metrics") or []:  # lifted to the model with this dataset as their table (_lift_dataset_metrics)
            if _has_stash(m) and m.get("name"):
                data = _stash_data(m)
                found.add(f"{data.get('table') or d['name']}[{data.get('name') or m['name']}]")
    for m in doc.get("metrics") or []:
        if _has_stash(m) and m.get("name") and datasets:
            data = _stash_data(m)
            found.add(f"{data.get('table') or datasets[0]['name']}[{data.get('name') or m['name']}]")
    return found


def _annotation_names(obj: dict[str, Any]) -> set[Any]:
    return {a.get("name") for a in obj.get("annotations") or [] if isinstance(a, dict)}


def _carry(old: dict[str, Any], new: dict[str, Any], kind: str, ref: str, stashed: set[str],
           report: list[dict[str, str]], why_not: str = "") -> None:
    """The properties ``old`` (the model's) has that ``new`` (the file's) doesn't set and the file doesn't own,
    carried over into ``new``. Over an object with a Power BI section the file's set stands, and each property
    it leaves out is named in ``report``; with ``why_not`` nothing is carried and each is named with that reason."""
    owned = _OSSIE_OWNS[kind]
    keys = [k for k in old if k not in owned and k not in new and not (kind == "table" and k in _TABLE_ALWAYS_KEPT)]
    have = _annotation_names(new)
    notes = [a for a in old.get("annotations") or [] if isinstance(a, dict) and a.get("name") not in have
             and a.get("name") not in _OSSIE_ANNOTATIONS]
    if ref in stashed:
        message = "taken out, because the file's Power BI section for it doesn't have it"
    elif why_not:
        message = f"not kept, because {why_not}"
    else:
        for k in keys:
            new[k] = copy.deepcopy(old[k])
        if notes:
            new["annotations"] = list(new.get("annotations") or []) + copy.deepcopy(notes)
        return
    report += [{"object": f"{ref} {k}", "kind": "lost", "message": message} for k in keys]
    report += [{"object": f"{ref} annotation {a.get('name')}", "kind": "lost", "message": message} for a in notes]


def _column_kind(c: dict[str, Any]) -> str:
    return str(c.get("type") or "data")


def _by_name(items: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in items or [] if isinstance(i, dict) and i.get("name")}


def _rel_key(r: dict[str, Any]) -> str:
    return f"{r.get('fromTable')}.{r.get('fromColumn')} -> {r.get('toTable')}.{r.get('toColumn')}"


def _is_placeholder(obj: dict[str, Any]) -> bool:
    """A BLANK() the converter put in place of SQL it couldn't translate."""
    return str(obj.get("expression") or "").strip().upper() == "BLANK()" and any(a.get("name") == PLACEHOLDER_ANNOTATION for a in obj.get("annotations") or [] if isinstance(a, dict))


def _direct_lake_expression(model: dict[str, Any]) -> str | None:
    """The shared expression the model's Direct Lake partitions read through, if any."""
    for t in model.get("tables") or []:
        for p in t.get("partitions") or []:
            if p.get("mode") == "directLake" and (p.get("source") or {}).get("expressionSource"):
                return p["source"]["expressionSource"]
    return None


def merge(current: dict[str, Any], incoming: dict[str, Any], remove: list[str] | None = None,
          stashed: set[str] | None = None, report: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """``incoming`` laid over ``current`` (both parsed ``model.bim``): the update an import writes.

    Tables, columns and measures named in both take the incoming definition with the current lineage tags and
    partitions; what only ``current`` has stays unless named in ``remove`` (``"table"``, ``"table[column]"`` or
    ``"table[measure]"``). Roles, perspectives, translations and shared expressions the file doesn't carry stay,
    and a table that only the file has reads through the model's existing Direct Lake source.

    A property of a table, column or measure in both that the file doesn't set and doesn't own (``_OSSIE_OWNS``),
    such as isHidden, formatString or one KratosForge doesn't know, is carried over, and so are the model's
    annotations the file doesn't have, by name. Objects in ``stashed`` (``stashed(text)``: the file has a Power BI
    section for them) take the file's set instead. Over a column the file gives another type or data type,
    nothing is carried, nor a sortByColumn naming a column the table no longer has. Each property not carried
    is named in ``report``, so nothing is dropped silently."""
    remove_set = set(remove or [])
    stashed = stashed or set()
    report = report if report is not None else []
    out = copy.deepcopy(incoming)
    cur_model, model = current.get("model") or {}, out.setdefault("model", {})
    cur_tables = _by_name(cur_model.get("tables"))
    existing_source = _direct_lake_expression(cur_model)
    incoming_source = _direct_lake_expression(model)
    tables = model.setdefault("tables", [])
    for t in tables:
        old = cur_tables.get(t["name"])
        if old is None:
            if existing_source and incoming_source:
                for p in t.get("partitions") or []:
                    if (p.get("source") or {}).get("expressionSource") == incoming_source:
                        p["source"]["expressionSource"] = existing_source
            continue
        if old.get("lineageTag"):
            t["lineageTag"] = old["lineageTag"]
        if old.get("partitions"):
            t["partitions"] = copy.deepcopy(old["partitions"])
        _carry(old, t, "table", t["name"], stashed, report)
        incoming_items = {key: list(t.get(key) or []) for key in ("columns", "measures")}
        for key in ("columns", "measures"):
            olds, news = _by_name(old.get(key)), t.setdefault(key, [])
            for item in news:
                if olds.get(item["name"], {}).get("lineageTag"):
                    item["lineageTag"] = olds[item["name"]]["lineageTag"]
            names = {i["name"] for i in news}
            news += [copy.deepcopy(i) for n, i in olds.items()
                     if n not in names and f"{t['name']}[{n}]" not in remove_set]
            if not news:
                del t[key]
        columns = {c.get("name") for c in t.get("columns") or [] if isinstance(c, dict)}
        for key, kind in (("columns", "column"), ("measures", "measure")):
            olds = _by_name(old.get(key))
            for item in incoming_items[key]:
                was = olds.get(item.get("name"))
                if was is None:
                    continue
                ref, why_not = f"{t['name']}[{item['name']}]", ""
                if kind == "column" and _column_kind(was) != _column_kind(item):
                    why_not = f"the file makes it a {_column_kind(item)} column, not {_column_kind(was)}"
                elif kind == "column" and item.get("dataType") and was.get("dataType") not in (None, item["dataType"]):
                    why_not = f"the file makes its data type {item['dataType']}, not {was['dataType']}"
                sort_by = was.get("sortByColumn")
                if (not why_not and ref not in stashed and sort_by is not None and "sortByColumn" not in item
                        and sort_by not in columns):
                    report.append({"object": f"{ref} sortByColumn", "kind": "lost",
                                   "message": f"not kept, because the table no longer has the column {sort_by}"})
                    was = {k: v for k, v in was.items() if k != "sortByColumn"}
                _carry(was, item, kind, ref, stashed, report, why_not)
        for key in ("hierarchies", "calculationGroup", "refreshPolicy"):
            if key in old and key not in t:
                t[key] = copy.deepcopy(old[key])
    names = {t["name"] for t in tables}
    tables += [copy.deepcopy(t) for n, t in cur_tables.items() if n not in names and n not in remove_set]
    present = {t["name"] for t in tables}
    rels = model.setdefault("relationships", [])
    keys = {_rel_key(r) for r in rels}
    rels += [copy.deepcopy(r) for r in cur_model.get("relationships") or []
             if _rel_key(r) not in keys and _rel_key(r) not in remove_set
             and r.get("fromTable") in present and r.get("toTable") in present]
    if not rels:
        del model["relationships"]
    # One Direct Lake connection per model: the model's own, which may share the converter's default name.
    cur_expr = next((e for e in cur_model.get("expressions") or [] if e.get("name") == existing_source), None)
    if cur_expr is not None and incoming_source:
        model["expressions"] = [e for e in model.get("expressions") or []
                                if e.get("name") not in (incoming_source, existing_source)] + [copy.deepcopy(cur_expr)]
    for key in _MODEL_KEPT:
        old = cur_model.get(key)
        if not old:
            continue
        if key not in model:
            model[key] = copy.deepcopy(old)
        elif isinstance(old, list):
            have = {i.get("name") for i in model[key] if isinstance(i, dict)}
            model[key] += [copy.deepcopy(i) for i in old if isinstance(i, dict) and i.get("name") not in have]
    for key in ("compatibilityLevel", "name"):
        if current.get(key):
            out[key] = current[key]
    return out


def plan(current: dict[str, Any] | None, final: dict[str, Any]) -> list[dict[str, str]]:
    """What writing ``final`` changes in ``current`` (None: a new model), one row per object:
    ``change`` is add, change, remove or needs_dax (a BLANK() placeholder someone has to write in DAX)."""
    rows: list[dict[str, str]] = []
    cur = _by_name(((current or {}).get("model") or {}).get("tables"))
    new = _by_name((final.get("model") or {}).get("tables"))
    for name, t in new.items():
        old = cur.get(name)
        if old is None:
            rows.append({"change": "add", "kind": "table", "object": name,
                         "detail": f"{len(t.get('columns') or [])} columns, {len(t.get('measures') or [])} measures"})
        for key, kind in (("columns", "column"), ("measures", "measure")):
            olds, news = _by_name((old or {}).get(key)), _by_name(t.get(key))
            for n, item in news.items():
                ref = f"{name}[{n}]"
                if _is_placeholder(item):
                    rows.append({"change": "needs_dax", "kind": kind, "object": ref,
                                 "detail": "Not translated from the Ossie file's SQL; added as BLANK() until it's written in DAX"})
                elif old is not None and n not in olds:
                    rows.append({"change": "add", "kind": kind, "object": ref, "detail": str(item.get("expression") or "")})
                elif old is not None and (item.get("expression"), item.get("dataType"), item.get("description")) != \
                        (olds[n].get("expression"), olds[n].get("dataType"), olds[n].get("description")):
                    rows.append({"change": "change", "kind": kind, "object": ref, "detail": str(item.get("expression") or "")})
            rows += [{"change": "remove", "kind": kind, "object": f"{name}[{n}]", "detail": ""}
                     for n in olds if n not in news]
    rows += [{"change": "remove", "kind": "table", "object": n, "detail": ""} for n in cur if n not in new]
    old_rels = {_rel_key(r) for r in ((current or {}).get("model") or {}).get("relationships") or []}
    new_rels = {_rel_key(r) for r in (final.get("model") or {}).get("relationships") or []}
    rows += [{"change": "add", "kind": "relationship", "object": k, "detail": ""} for k in sorted(new_rels - old_rels)]
    rows += [{"change": "remove", "kind": "relationship", "object": k, "detail": ""} for k in sorted(old_rels - new_rels)]
    return rows


def removable(current: dict[str, Any] | None, incoming: dict[str, Any]) -> list[str]:
    """What an update keeps although the file doesn't have it: the names ``merge`` accepts in ``remove``."""
    if not current:
        return []
    return [r["object"] for r in plan(current, incoming) if r["change"] == "remove"]


def bim_parts(bim: dict[str, Any]) -> dict[str, str]:
    """The definition parts updateDefinition takes for a model written as TMSL."""
    return {"model.bim": json.dumps(bim, indent=2, ensure_ascii=False),
            "definition.pbism": json.dumps({"version": "4.0", "settings": {}}, indent=2)}


def round_trip(bim: dict[str, Any]) -> list[dict[str, str]]:
    """What sending ``bim`` (a parsed ``model.bim``) to Ossie and back would not bring back, one row per object
    by name: the round trip on this PC, run for real. Empty when every object comes back as it was."""
    from . import tmdl
    out = export(bim, translate_dax=False)
    back, _ = to_tmsl(out["yaml"])
    return [{"object": where, "kind": "lost", "message": "doesn't come back from Ossie as it was"}
            for where in tmdl.differences(bim, back)]


def tmdl_to_ossie(files: dict[str, str], name: str, translate_dax: bool = True) -> dict[str, Any]:
    """A model's TMDL files (path -> text) as Ossie YAML, like ``export``; the report also lists what the TMDL
    reader left out and anything that wouldn't come back from Ossie."""
    from . import tmdl
    bim, read_report = tmdl.to_bim(files, name)
    out = export(bim, translate_dax=translate_dax)
    out["report"] = read_report + out["report"] + round_trip(bim)
    return out


def ossie_to_tmdl(text: str, source: dict[str, str] | None = None) -> tuple[dict[str, str], list[dict[str, str]]]:
    """An Ossie document as TMDL files (path -> text, as in a ``.SemanticModel`` folder) and the report, on this
    PC with no Fabric call. The files are read back before they are returned; whatever wouldn't survive is
    reported by name, and a name or expression that would break TMDL's layout is refused (ValueError)."""
    from . import tmdl
    bim, report = to_tmsl(text, source)
    files, written = tmdl.from_bim(bim)
    return files, report + written
