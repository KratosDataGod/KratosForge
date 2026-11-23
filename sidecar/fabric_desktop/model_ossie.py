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

import collections
import copy
import json
import re
import threading
import warnings
from typing import Any, Literal

import sqlglot
from sqlglot import exp

from . import sql_dialects, tmdl

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

            def construct_mapping(self, node: Any, deep: bool = False) -> Any:
                """A key given twice in one object is refused: YAML's keys are unique, and the later one would
                silently replace the earlier (a second ``datasets:`` dropping the first)."""
                seen: set[Any] = set()
                for key_node, _ in node.value:
                    if not isinstance(key_node, _yaml.ScalarNode) or key_node.tag == "tag:yaml.org,2002:merge":
                        continue
                    key = self.construct_object(key_node)
                    if key in seen:
                        raise ValueError(f"This file gives {str(key)[:80]!r} twice in one object (line "
                                         f"{key_node.start_mark.line + 1}). Keep one.")
                    seen.add(key)
                return super().construct_mapping(node, deep)

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
# The file name every converter warning carries (ossie_microsoft._common.warn): only those are the report.
CONVERTER_WARNINGS = "ossie_microsoft"
# A large real model is a few MB of YAML; this keeps a mistaken pick (a data file) from filling memory.
MAX_CHARS = 20_000_000
# Levels of nesting a document may have, as the schema check (ossie_validate) allows: deeper is refused before the
# converter recurses into it.
MAX_DEPTH = tmdl.MAX_DEPTH
NOT_A_MODEL = "This isn't an Ossie semantic model: it should be one object with version, name and datasets"


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
        if w.filename != CONVERTER_WARNINGS:  # not the converter's: a warning Python raised meanwhile, such as
            # a ResourceWarning for another thread's socket the garbage collector closed. Passed on, not reported.
            warnings.showwarning(w.message, w.category, w.filename, w.lineno, w.file, w.line)
            continue
        text = str(w.message)
        scope, _, message = text[1:].partition("] ") if text.startswith("[") else ("model", "", text)
        report.append({"object": scope, "message": message or text,
                       "kind": "kept" if "preserved" in text or "kept as-is" in text else "lost"})
    return result, report


def load_text(text: str) -> Any:
    """Untrusted YAML or JSON text as plain values: at most MAX_CHARS, a SafeLoader that also refuses aliases,
    and no nesting deeper than Python's recursion limit. Refuses with a plain reason (ValueError)."""
    require()
    if not isinstance(text, str):
        raise ValueError("An Ossie file is text (YAML or JSON)")
    if len(text) > MAX_CHARS:
        raise ValueError(f"This file is {len(text) // 1_000_000} MB; an Ossie model is at most {MAX_CHARS // 1_000_000} MB")
    try:
        return yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader that also refuses aliases
    except yaml.YAMLError as exc:
        raise ValueError(f"This isn't valid YAML or JSON: {exc}") from exc
    except RecursionError:
        raise ValueError("This file is nested too deeply to be an Ossie model") from None


def parse(text: str) -> dict[str, Any]:
    """An Ossie document (YAML or JSON) as a dict, refusing what isn't one, with a plain reason.

    Any known profile is read (``ossie_profiles.detect_profile``: Ossie 0.2.0.dev0, 0.3.0.dev0, or Snowflake's
    0.1.1 wrapper), and any other version is refused by name. Reading isn't converting: only a document
    ``convertible`` names can go through the converter (``to_tmsl`` refuses the rest)."""
    from . import ossie_profiles

    doc = load_text(text)
    _refuse_root(doc)
    profile = ossie_profiles.detect_profile(doc)  # UnknownProfile, a ValueError naming the version
    if ossie_profiles.PROFILES[profile]["root"] == "flat" and (
            not doc.get("name") or not isinstance(doc.get("datasets"), list) or not doc["datasets"]):
        raise ValueError("An Ossie model needs a name and at least one dataset")
    return doc


def model_name(doc: dict[str, Any]) -> str:
    """The model's name: at the top of a flat file, or in Snowflake's 0.1.1 wrapper ``semantic_model[0].name``
    (a list) or ``semantic_model.name`` (one object). "" when there is none."""
    if not isinstance(doc, dict):
        return ""
    wrapper = doc.get("semantic_model")
    inner = wrapper[0] if isinstance(wrapper, list) and wrapper else wrapper
    name = doc.get("name") if "name" in doc else inner.get("name") if isinstance(inner, dict) else None
    return name.strip() if isinstance(name, str) else ""


def not_convertible(doc: dict[str, Any]) -> str:
    """Why the converter can't take ``doc`` (what ``parse`` read), or "" when it can. The vendored converter reads
    Ossie 0.2.0.dev0 only; a 0.3.0.dev0 file or Snowflake's 0.1.1 wrapper is read and checked, never handed to it
    as if it were 0.2. Converting between versions is OSSIE-012c."""
    from . import ossie_profiles

    profile = ossie_profiles.detect_profile(doc)
    if ossie_profiles.PROFILES[profile]["convertible"]:
        return ""
    return (f"This file is {ossie_profiles.PROFILES[profile]['label']} (profile {profile}). KratosForge reads it and "
            f"checks it, but can't convert it yet: the converter reads Ossie {OSSIE_VERSION} only, and converting "
            "between Ossie versions comes later (OSSIE-012c). Import's preview still shows the schema check.")


def export(bim: dict[str, Any], translate_dax: bool = True, read_report: list[dict[str, str]] | None = None,
           validate: bool = False) -> dict[str, Any]:
    """A model (parsed ``model.bim``) as Ossie YAML, with counts, the conversion report and the ``fidelity`` report.

    ``translate_dax`` also writes single-column aggregate measures (SUM, AVERAGE, MIN, MAX, COUNTA,
    DISTINCTCOUNTNOBLANK) as portable SQL, keeping the DAX beside it. ``read_report`` (what the TMDL reader left
    out) leads the report. ``validate`` also runs the round trip (``round_trip_check``): its rows are
    ``round_trip`` and its result is the fidelity report's validation. A part of the wrong type is refused by
    path before conversion (``_refuse_shape``)."""
    require()
    _refuse_shape(bim, _BIM_SHAPE, "export")
    if tmdl.too_deep(bim):
        raise ValueError("This model couldn't be converted to Ossie, because part of it is nested too deeply to read "
                         f"(more than {MAX_DEPTH} levels). Flatten that part (often an annotation value), then try again.")
    try:
        text, report = _convert(convert_semantic_model_to_ossie, bim, translate_dax=translate_dax)
    except (TypeError, KeyError, IndexError, AttributeError) as exc:  # a part of the wrong type (a number for tables)
        raise ValueError("This model couldn't be converted to Ossie, because part of it isn't the shape a semantic model's "
                         f"part has ({exc}). Fix that part, then try again.") from exc
    except RecursionError:  # the converter recurses (and json-encodes) each part; Windows' C stack ends it soonest
        raise ValueError("This model couldn't be converted to Ossie, because part of it is nested too deeply to read. "
                         "Flatten that part (often an annotation value), then try again.") from None
    report = list(read_report or []) + report
    doc = yaml.safe_load(text)
    datasets = doc.get("datasets") or []
    metrics = list(doc.get("metrics") or []) + [m for d in datasets for m in d.get("metrics") or []]
    portable = sum(1 for m in metrics if any(d.get("dialect") != "DAX"
                                             for d in (m.get("expression") or {}).get("dialects") or []))
    check = round_trip_check(bim) if validate else None
    out = {"yaml": text, "name": doc.get("name", ""), "version": doc.get("version", ""), "report": report,
           "counts": {"datasets": len(datasets), "fields": sum(len(d.get("fields") or []) for d in datasets),
                      "metrics": len(metrics), "portable_metrics": portable,
                      "relationships": len(doc.get("relationships") or [])},
           "fidelity": safe_fidelity(bim, doc, report, check)}
    if check is not None:  # a check that didn't run is never an empty (passing) list
        out["round_trip"] = check["rows"] if check["ran"] else [
            {"object": "model", "kind": "lost",
             "message": f"the round-trip check didn't run: {check['reason']}" + (f" ({check['error']})" if check["error"] else "")}]
    return out


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
                    except sqlglot.errors.SqlglotError:  # ParseError or TokenError (e.g. an unclosed quote)
                        pass  # left as written: the converter reports SQL it can't translate
            stash = next((e for e in m.get("custom_extensions") or [] if e.get("vendor_name") == "POWER_BI"), None)
            raw = stash.get("data") if stash else None
            try:  # refused as the converter refuses a model-level metric's data
                data = json.loads(raw) if raw is not None else {"_v": 2}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"The Ossie file couldn't be converted, because the POWER_BI custom_extensions data "
                                 f"in {scope} isn't valid JSON ({exc}). Fix it, then try again.") from None
            if not isinstance(data, dict):
                raise ValueError(f"The Ossie file couldn't be converted, because the POWER_BI custom_extensions data "
                                 f"in {scope} isn't a JSON object. Fix it, then try again.")
            data.setdefault("table", d["name"])
            m["custom_extensions"] = [e for e in m.get("custom_extensions") or [] if e is not stash] + \
                [{"vendor_name": "POWER_BI", "data": json.dumps(data, sort_keys=True)}]
            root.append(m)
            taken.add(m["name"])
    if not root:
        del doc["metrics"]
    return doc


def _refuse_root(doc: Any) -> None:
    """Refuse (ValueError) a document that isn't one object: a list, text, a number, or nothing (an empty file)."""
    if not isinstance(doc, dict):
        raise ValueError(NOT_A_MODEL)


def refuse_depth(doc: Any) -> None:
    """Refuse (ValueError) an Ossie document nested more than ``MAX_DEPTH`` levels deep, as the schema check does,
    before a converter recurses into it."""
    if tmdl.too_deep(doc):
        raise ValueError(f"The Ossie file couldn't be converted, because it is nested too deeply (more than {MAX_DEPTH} "
                         "levels). Take out the extra nesting, then try again.")


def _parsed(text: str | dict[str, Any]) -> dict[str, Any]:
    """``text`` parsed, or as it is when the caller already has it from ``parse`` (so a file is read once); a
    document that isn't one object is refused (ValueError) either way."""
    if isinstance(text, str):
        return parse(text)
    _refuse_root(text)
    return text


def to_tmsl(text: str | dict[str, Any], source: dict[str, str] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """An Ossie document (its text, or what ``parse`` made of it) as a parsed ``model.bim``, and the report.
    ``source`` is the lakehouse new Direct Lake tables read: ``{"workspaceId": ..., "itemId": ...}``. A part of
    the wrong type is refused by path before conversion (``_refuse_shape``), and so is a document that isn't one
    object or nests more than ``MAX_DEPTH`` levels deep."""
    lifted: list[dict[str, str]] = []
    parsed = _parsed(text)
    refusal = not_convertible(parsed)
    if refusal:  # never fed to the 0.2 converter as if it were 0.2
        raise ValueError(refusal)
    _refuse_shape(parsed, _OSSIE_SHAPE, "ossie")
    refuse_depth(parsed)  # after the shape check, which names a wrong type by path however deep it nests
    try:
        doc = _lift_dataset_metrics(parsed, lifted)  # works on a copy: the caller's document is unchanged
        bim, report = _convert(convert_ossie_to_semantic_model, yaml.safe_dump(doc, sort_keys=False), source=source)
    except ConversionError as exc:
        raise ValueError(f"The Ossie file couldn't be converted: {exc}") from exc
    except (TypeError, KeyError, IndexError, AttributeError) as exc:  # a part of the wrong type (a list for a name)
        raise ValueError(_wrong_shape(parsed)) from exc
    # The caller always passes the lakehouse when it has one; without it the ids are placeholders it reports itself.
    report = lifted + [r for r in report if "placeholder ids" not in r["message"]]
    return bim, report


def _wrong_shape(doc: dict[str, Any]) -> str:
    """Why a document the converter tripped on can't be converted: the schema check's first finding, by path."""
    from . import ossie_validate

    found = ossie_validate.validate(doc)["diagnostics"]
    where = f": {found[0]['path']} {found[0]['message']}" if found else ""
    return (f"The Ossie file couldn't be converted, because part of it isn't the shape Ossie {OSSIE_VERSION} gives it{where}. "
            "Fix that part, then try again.")


# ---- shape checks: the types of the keys the conversion reads, checked once before it runs
# A shape maps a key to what its value must be: a type (or a tuple of types), a dict (an object of that shape) or a
# one-item list (a list whose items are each that). A key that is absent, null or empty ("", [] or {}) isn't checked:
# the converter reads it as missing. Keys not named here aren't read, so they aren't checked.
Shape = dict[str, Any]
_TEXT = (str, list)  # TMSL keeps multi-line text as a string or as a list of lines
_EXTENSIONS: list[Shape] = [{"vendor_name": str}]  # data: read and refused by name where it is used
_EXPRESSION: Shape = {"dialects": [{"dialect": str, "expression": str}]}
_METRIC: Shape = {"name": str, "description": str, "datatype": str, "expression": _EXPRESSION,
                  "custom_extensions": _EXTENSIONS}
_OSSIE_SHAPE: Shape = {
    "name": str, "description": str, "custom_extensions": _EXTENSIONS, "metrics": [_METRIC],
    "datasets": [{"name": str, "description": str, "source": str, "primary_key": [str], "unique_keys": [[str]],
                  "custom_extensions": _EXTENSIONS, "metrics": [_METRIC],
                  "fields": [{"name": str, "description": str, "datatype": str, "expression": _EXPRESSION,
                              "dimension": dict, "custom_extensions": _EXTENSIONS}]}],
    "relationships": [{"name": str, "from": str, "to": str, "from_columns": [str], "to_columns": [str],
                       "custom_extensions": _EXTENSIONS}],
}
_ANNOTATIONS: list[Shape] = [{"name": str, "value": _TEXT}]
_NAMED: list[Shape] = [{"name": str}]
_BIM_SHAPE: Shape = {
    "name": str, "description": _TEXT, "compatibilityLevel": int,
    "model": {
        "description": _TEXT, "annotations": _ANNOTATIONS, "roles": _NAMED, "perspectives": _NAMED,
        "cultures": _NAMED, "expressions": [{"name": str, "kind": str, "expression": _TEXT}],
        "relationships": [{"name": str, "fromTable": str, "fromColumn": str, "toTable": str, "toColumn": str,
                           "fromCardinality": str, "toCardinality": str}],
        "tables": [{
            "name": str, "description": _TEXT, "annotations": _ANNOTATIONS, "hierarchies": _NAMED,
            "calculationGroup": dict,
            "columns": [{"name": str, "type": str, "dataType": str, "sourceColumn": str, "expression": _TEXT,
                         "description": _TEXT, "formatString": str, "dataCategory": str, "annotations": _ANNOTATIONS}],
            "measures": [{"name": str, "expression": _TEXT, "description": _TEXT, "dataType": str,
                          "formatString": str, "annotations": _ANNOTATIONS}],
            "partitions": [{"name": str, "mode": str,
                            "source": {"type": str, "query": _TEXT, "expression": _TEXT, "entityName": str,
                                       "schemaName": str, "expressionSource": str}}],
        }],
    },
}
_WORDS = {str: "text", int: "a whole number", float: "a number", bool: "true or false", list: "a list",
          dict: "an object", type(None): "empty"}


# How a refusal starts, by what was refused; the first two as the converter's own refusals start.
_SHAPE_LEADS = {
    "ossie": "The Ossie file couldn't be converted, because part of it isn't the shape Ossie gives it",
    "export": "This model couldn't be converted to Ossie, because part of it isn't the shape a semantic model's part has",
    "update": "The model couldn't be updated, because part of it isn't the shape a semantic model's part has",
}


def _what(value: Any) -> str:
    """What a value is, in words, never the value itself."""
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, (int, float)):
        return "a number"
    return _WORDS.get(type(value), "a " + type(value).__name__)


def _wanted(spec: Any) -> str:
    if isinstance(spec, dict):
        return "an object"
    if isinstance(spec, list):
        return "a list"
    return " or ".join(_WORDS.get(t, t.__name__) for t in (spec if isinstance(spec, tuple) else (spec,)))


def _fits(value: Any, spec: Any) -> bool:
    if isinstance(spec, dict):
        return isinstance(value, dict)
    if isinstance(spec, list):
        return isinstance(value, list)
    return isinstance(value, spec) and not (isinstance(value, bool) and bool not in
                                            (spec if isinstance(spec, tuple) else (spec,)))


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _refuse_shape(doc: Any, shape: Shape, kind: Literal["ossie", "export", "update"]) -> None:
    """Refuse ``doc`` (ValueError naming the path, never a value) when a key ``shape`` names has a value of
    another type, such as text where a list of datasets goes or a list where an object goes. One pass, without
    recursion: only as deep as ``shape`` goes, so a deeply nested value is never walked."""
    todo: collections.deque[tuple[Any, Any, str]] = collections.deque([(doc, shape, "$")])
    while todo:
        value, spec, path = todo.popleft()
        if not _fits(value, spec):
            hint = " (a name that is a number goes in quotes)" if isinstance(value, (int, float)) and \
                not isinstance(value, bool) and _fits("", spec) else ""
            raise ValueError(f"{_SHAPE_LEADS[kind]}: {path} is {_what(value)} where it should be {_wanted(spec)}{hint}. "
                             "Fix that part, then try again.")
        if isinstance(spec, dict):
            todo.extend((value[key], want, f"{path}.{key}")
                        for key, want in spec.items() if not _empty(value.get(key)))
        elif isinstance(spec, list):
            for i, item in enumerate(value):
                name = item.get("name") if isinstance(item, dict) else None
                todo.append((item, spec[0], f"{path}[{_q(name) if isinstance(name, str) else i}]"))


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


def stashed(text: str | dict[str, Any]) -> set[str]:
    """The tables (``"table"``), columns and measures (``"table[name]"``) the Ossie document gives a Power BI section
    (a POWER_BI custom extension, as Export to Ossie writes for each object with Fabric-only properties). For those
    the file speaks for every property, so ``merge`` takes the file's set; for the rest it carries the model's
    Fabric-only properties over.

    A measure is named by the table the converter puts it on: a dataset's metric is lifted with that dataset as
    its table unless its section names one (``_lift_dataset_metrics``, which leaves out one a model-level metric
    already names); the section's ``table`` and ``name`` stand when that table is in the model (a dataset or
    one of the model section's excluded tables), and otherwise the first dataset is its home. ``text`` may be
    what ``parse`` made of the file; it is only read."""
    doc = _parsed(text)
    _refuse_shape(doc, _OSSIE_SHAPE, "ossie")
    datasets = [d for d in doc.get("datasets") or [] if isinstance(d, dict) and d.get("name")]
    if not datasets:
        return set()
    tables = {d["name"] for d in datasets} | {t.get("name") for t in _stash_data(doc).get("excludedTables") or []
                                             if isinstance(t, dict)}
    found: set[str] = set()

    def measure(m: dict[str, Any], default: str | None) -> None:
        data = _stash_data(m)
        home = data["table"] if "table" in data else default
        found.add(f"{home if home in tables else datasets[0]['name']}[{data.get('name', m['name'])}]")

    taken = {m.get("name") for m in doc.get("metrics") or [] if isinstance(m, dict)}
    for m in doc.get("metrics") or []:
        if isinstance(m, dict) and m.get("name") and _has_stash(m):
            measure(m, None)
    for d in datasets:
        if _has_stash(d):
            found.add(str(d["name"]))
        for f in d.get("fields") or []:
            if isinstance(f, dict) and _has_stash(f) and f.get("name"):
                found.add(f"{d['name']}[{f['name']}]")
        for m in d.get("metrics") or []:
            if not isinstance(m, dict) or not m.get("name") or m["name"] in taken:
                continue
            taken.add(m["name"])
            if _has_stash(m):
                measure(m, d["name"])
    return found


def _annotation_names(obj: dict[str, Any]) -> set[Any]:
    return {a.get("name") for a in obj.get("annotations") or [] if isinstance(a, dict)}


def _carry(old: dict[str, Any], new: dict[str, Any], kind: str, ref: str, stashed: set[str],
           report: list[dict[str, str]], why_not: str = "") -> list[str]:
    """The properties ``old`` (the model's) has that ``new`` (the file's) doesn't set and the file doesn't own,
    carried over into ``new``. Over an object with a Power BI section the file's set stands, and each property
    it leaves out is named in ``report``; with ``why_not`` nothing is carried and each is named with that reason.
    The keys carried, for ``_check_references``."""
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
        return keys
    report += [{"object": f"{ref} {k}", "kind": "lost", "message": message} for k in keys]
    report += [{"object": f"{ref} annotation {a.get('name')}", "kind": "lost", "message": message} for a in notes]
    return []


def _column_kind(c: dict[str, Any]) -> str:
    return str(c.get("type") or "data")


# Column properties that describe the source column it reads, so they don't follow it to another source column.
_FROM_SOURCE = ("sourceLineageTag", "sourceProviderType")
# Carried properties holding DAX, checked for the names of tables, columns and measures the merge took out.
_DAX_KEYS = ("kpi", "formatStringDefinition", "detailRowsDefinition", "defaultDetailRowsDefinition")
_QUOTED = r"'((?:[^']|'')*)'"
# A bare name starts and ends with a character that isn't a space, so it never overlaps the spaces around it
# (no slow backtracking on a crafted name); the value is stripped first and capped at MAX_REF characters.
_TMDL_REF = re.compile(rf"^(?:{_QUOTED}|([^\['.\s](?:[^\['.]*[^\['.\s])?))\s*(?:\[(.+)\]|\.\s*(?:{_QUOTED}|(.+)))?$")
MAX_REF = 512  # longer than any real Table[Column] or Table.Hierarchy: not read as a reference, so not carried


def _parse_ref(value: Any, table: str) -> tuple[str, str] | None:
    """``'Table'[Column]``, ``Table.Hierarchy`` or a bare name (on ``table``) as (table, name), casefolded;
    None when it isn't one of those."""
    if isinstance(value, dict):
        t, n = value.get("table"), value.get("column", value.get("hierarchy"))
        return (str(t).casefold(), str(n).casefold()) if isinstance(t, str) and isinstance(n, str) else None
    if not isinstance(value, str) or len(value) > MAX_REF:
        return None
    m = _TMDL_REF.match(value.strip())
    if m is None:
        return None
    first = (m.group(1).replace("''", "'") if m.group(1) is not None else m.group(2)).strip()
    second = m.group(3) or (m.group(4).replace("''", "'") if m.group(4) is not None else m.group(5))
    return (table.casefold(), first.casefold()) if second is None else (first.casefold(), second.strip().casefold())


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _check_references(current: dict[str, Any], model: dict[str, Any],
                      carried: list[tuple[str, dict[str, Any], str, list[str]]], report: list[dict[str, str]]) -> None:
    """Carried properties that name another object (a sort-by or group-by column, a variation's relationship or
    default hierarchy, the column an aggregation is an alternate of) are taken back out when that object isn't in
    the merged model, and so is DAX (a KPI, a format string or detail rows expression) naming a table, column or
    measure the merge took out. Each is named in ``report``. A reference that can't be read is not carried."""
    tables = {str(t.get("name")).casefold(): t for t in model.get("tables") or [] if isinstance(t, dict)}
    members = {(n, str(c.get("name")).casefold()) for n, t in tables.items() for key in ("columns", "measures")
               for c in t.get(key) or [] if isinstance(c, dict)}
    hierarchies = {(n, str(h.get("name")).casefold()) for n, t in tables.items() for h in t.get("hierarchies") or []
                   if isinstance(h, dict)}
    relationships = {str(r.get("name")).casefold() for r in model.get("relationships") or [] if isinstance(r, dict)}
    cur_tables = [t for t in current.get("tables") or [] if isinstance(t, dict)]
    gone_tables = [str(t.get("name")) for t in cur_tables if str(t.get("name")).casefold() not in tables]
    gone = [(str(t.get("name")), str(c.get("name"))) for t in cur_tables for key in ("columns", "measures")
            for c in t.get(key) or [] if isinstance(c, dict)
            and (str(t.get("name")).casefold(), str(c.get("name")).casefold()) not in members]
    names_left = {n for _, n in members}

    def shown(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)

    def has(value: Any, table: str, pool: set[tuple[str, str]]) -> bool:
        """``value`` names an object in ``pool``: as written (a name on ``table`` with a dot or a bracket in
        it), or read as a reference."""
        return (isinstance(value, str) and (table.casefold(), value.casefold()) in pool) or \
            _parse_ref(value, table) in pool

    def missing(table: str, key: str, value: Any) -> str | None:
        """What ``value`` (``key``'s, on ``table``) names that the merged model doesn't have, else None."""
        if key == "sortByColumn":
            return None if has(value, table, members) else f"the column {shown(value)}"
        if key == "variations":
            for v in value if isinstance(value, list) else [value]:
                if not isinstance(v, dict):
                    return f"what {shown(v)} names, which KratosForge can't tell"
                rel, hier = v.get("relationship"), v.get("defaultHierarchy")
                if rel is not None and str(rel).casefold() not in relationships:
                    return f"the relationship {shown(rel)}"
                if hier is not None and not has(hier, table, hierarchies):
                    return f"the hierarchy {shown(hier)}"
            return None
        if key in ("relatedColumnDetails", "alternateOf") and not isinstance(value, dict):
            return f"what {shown(value)} names, which KratosForge can't tell"
        if key == "relatedColumnDetails":
            refs = [g.get("groupingColumn") if isinstance(g, dict) else g for g in value.get("groupByColumns") or []]
            for r in refs + _strings(value.get("groupByColumn")):
                if not has(r, table, members):
                    return f"the column {shown(r)}"
            return None
        if key == "alternateOf":
            base_table, base_column = value.get("baseTable"), value.get("baseColumn")
            if base_table is not None:
                ref = _parse_ref(base_table, "")
                if str(base_table).casefold() not in tables and (ref is None or ref[0] != "" or ref[1] not in tables):
                    return f"the table {shown(base_table)}"
            if base_column is not None and not has(base_column, table, members):
                return f"the column {shown(base_column)}"
            return None
        # DAX: a name the merge took out, as 'Table', Table, Table[Name] or [Name] when nothing left has that name.
        text = "\n".join(_strings(value)).casefold()
        bare = re.sub(r"\[[^\]]*\]|\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'", " ", text)
        for t in gone_tables:
            quoted = "'" + t.replace("'", "''").casefold() + "'"
            if quoted in text or re.search(rf"(?<![\w.]){re.escape(t.casefold())}(?!\w)", bare):
                return f"the table {t}"
        for t, n in gone:
            bracket = "[" + n.replace("]", "]]").casefold() + "]"
            quoted = "'" + t.replace("'", "''").casefold() + "'"
            qualified = re.search(rf"(?:{re.escape(quoted)}|(?<![\w']){re.escape(t.casefold())})\s*{re.escape(bracket)}", text)
            if qualified or (bracket in text and n.casefold() not in names_left):
                return f"{t}[{n}]"
        return None

    for table, item, ref, keys in carried:
        for key in keys:
            if key not in ("sortByColumn", "variations", "relatedColumnDetails", "alternateOf", *_DAX_KEYS) or key not in item:
                continue
            what = missing(table, key, item[key])
            if what is not None:
                del item[key]
                report.append({"object": f"{ref} {key}", "kind": "lost",
                               "message": f"not kept, because the model no longer has {what}"})


def _by_name(items: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in items or [] if isinstance(i, dict) and i.get("name")}


def _rel_key(r: dict[str, Any]) -> str:
    return f"{r.get('fromTable')}.{r.get('fromColumn')} -> {r.get('toTable')}.{r.get('toColumn')}"


def ends_key(a_table: Any, a_columns: Any, b_table: Any, b_columns: Any) -> tuple[str, str]:
    """A relationship's two ends (each a table and its list of columns) as one key, the same whichever end is
    written first: an Ossie relationship and the TMSL relationship it converts to share it."""
    def end(table: Any, columns: Any) -> str:
        return _canon([table, columns if isinstance(columns, list) else [columns]])
    a, b = end(a_table, a_columns), end(b_table, b_columns)
    return (a, b) if a <= b else (b, a)


def tmsl_ends_key(r: dict[str, Any]) -> tuple[str, str]:
    """``ends_key`` of a TMSL relationship."""
    return ends_key(r.get("fromTable"), [r.get("fromColumn")], r.get("toTable"), [r.get("toColumn")])


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
    nothing is carried, and over one it maps to another source column, nothing describing the old source
    (``_FROM_SOURCE``). A carried property naming an object the merged model doesn't have, or DAX naming one
    the merge took out, is taken back out (``_check_references``). Each property not carried is named in
    ``report``, so nothing is dropped silently."""
    _refuse_shape(current, _BIM_SHAPE, "update")
    _refuse_shape(incoming, _BIM_SHAPE, "update")
    remove_set = set(remove or [])
    stashed = stashed or set()
    report = report if report is not None else []
    carried: list[tuple[str, dict[str, Any], str, list[str]]] = []  # (table, object, ref, keys carried)
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
        carried.append((t["name"], t, t["name"], _carry(old, t, "table", t["name"], stashed, report)))
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
                elif kind == "column" and was.get("sourceColumn") and item.get("sourceColumn") not in (
                        None, was["sourceColumn"]) and ref not in stashed:
                    for k in (k for k in _FROM_SOURCE if k in was and k not in item):
                        report.append({"object": f"{ref} {k}", "kind": "lost", "message": (
                            f"not kept, because the file maps it to source column {item['sourceColumn']}, "
                            f"not {was['sourceColumn']}")})
                    was = {k: v for k, v in was.items() if k not in _FROM_SOURCE}
                carried.append((t["name"], item, ref, _carry(was, item, kind, ref, stashed, report, why_not)))
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
    _check_references(cur_model, model, carried, report)
    for key in ("compatibilityLevel", "name"):
        if current.get(key):
            out[key] = current[key]
    return out


def plan(current: dict[str, Any] | None, final: dict[str, Any]) -> list[dict[str, str]]:
    """What writing ``final`` changes in ``current`` (None: a new model), one row per object:
    ``change`` is add, change, remove or needs_dax (a BLANK() placeholder someone has to write in DAX)."""
    if current is not None:
        _refuse_shape(current, _BIM_SHAPE, "update")
    _refuse_shape(final, _BIM_SHAPE, "update")
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
    out = export(bim, translate_dax=translate_dax, read_report=read_report, validate=True)
    out["report"] = out["report"] + out.pop("round_trip")
    return out


def ossie_to_tmdl(text: str, source: dict[str, str] | None = None) -> tuple[dict[str, str], list[dict[str, str]]]:
    """An Ossie document as TMDL files (path -> text, as in a ``.SemanticModel`` folder) and the report, on this
    PC with no Fabric call. The files are read back before they are returned; whatever wouldn't survive is
    reported by name, and a name or expression that would break TMDL's layout is refused (ValueError)."""
    from . import tmdl
    bim, report = to_tmsl(text, source)
    files, written = tmdl.from_bim(bim)
    return files, report + written


# ---- the fidelity report (OSSIE-004a): every object a conversion reads, in one of five classes

# The vendored converter (see _vendor/ossie_microsoft/README.md; tests check the two agree).
CONVERTER = {"name": "apache/ossie-converters", "path": "microsoft/src/ossie_microsoft", "version": "0.2.0.dev0",
             "commit": "82b9eff619c5b701de720b1bb0f02b23ea2c64bf"}  # pragma: allowlist secret (upstream git commit sha)
CLASSES = {"fully_preserved": "Fully preserved", "preserved_through_extensions": "Preserved through extensions",
           "partially_converted": "Partially converted", "unsupported": "Unsupported", "unverified": "Unverified"}
# Model collections Ossie has no place for: they travel only in the model's POWER_BI extension.
_STASHED_OBJECTS = (("perspectives", "perspective"), ("roles", "role"), ("cultures", "culture"),
                    ("expressions", "expression"))
# Extension keys the converter reads as instructions rather than as properties of the object.
_STASH_CONTROL = frozenset({"_v", "excludedTables", "excludedMeasures", "excludedRelationships", "excludedColumns",
                            "document", "descriptionSource", "table", "name", "flipped", "normalizedEndpoints",
                            "sourceColumn", "dataType", *(key for key, _ in _STASHED_OBJECTS), "hierarchies"})
_DIALECT_ANNOTATION = "OssieExpressionDialect"
RESTORED = "restored from the POWER_BI extension"  # the note on an object that lives only in that extension
_COLLECTION_ROWS = (*_STASHED_OBJECTS, ("hierarchies", "hierarchy"))


MAX_NAME = 1024  # longer than any real object name: a report names it cut short, so no row costs more than this


def _short(name: Any) -> str:
    """``name`` as text, cut short with its length past ``MAX_NAME`` characters."""
    text = str(name)
    return text if len(text) <= MAX_NAME else f"{text[:MAX_NAME]}... ({len(text)} characters)"


def _q(name: Any) -> str:
    return f"'{_short(name)}'"


def _canon(obj: Any) -> str:
    """One object as comparable text, so lists of them compare as key sets rather than item by item."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _text(value: Any) -> str:
    """A TMSL expression (a string or a list of lines) as one string with \\n line ends."""
    text = "\n".join(str(v) for v in value) if isinstance(value, list) else "" if value is None else str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _role_shape(role: dict[str, Any]) -> dict[str, Any]:
    """What a security role does: its model permission, each table's filter and column permissions, its members.
    Keys use the full names, so two long names sharing a prefix stay apart; only messages cut them short."""
    perms = _dicts(role.get("tablePermissions"))
    return {"modelPermission": role.get("modelPermission"),
            "filters": {(str(p.get("name")), _text(p.get("filterExpression"))) for p in perms},
            "tables": {(str(p.get("name")), str(p.get("metadataPermission"))) for p in perms
                       if "metadataPermission" in p},
            "columns": {(str(p.get("name")), str(c.get("name")), str(c.get("metadataPermission")))
                        for p in perms for c in _dicts(p.get("columnPermissions"))},
            # a member is a principal: the same name under another id or identity provider is someone else
            "members": {(str(m.get("memberName", m.get("name"))), str(m.get("memberId", "")),
                         str(m.get("identityProvider", ""))) for m in _dicts(role.get("members"))}}


def _shown_key(key: tuple[str, ...]) -> str:
    return "(" + ", ".join(_q(k) for k in key if k) + ")"


def _shown(value: str) -> str:
    return repr(value if len(value) <= 80 else value[:80] + "...")


def _role_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """How the role ``after`` differs from ``before`` in what it lets people see, one line each."""
    a, b = _role_shape(before), _role_shape(after)
    out = []
    if a["modelPermission"] != b["modelPermission"]:
        out.append(f"model permission {_shown(str(a['modelPermission']))} became {_shown(str(b['modelPermission']))}")
    old, new = dict(sorted(a["filters"] - b["filters"])), dict(sorted(b["filters"] - a["filters"]))
    for table in sorted(old.keys() | new.keys()):
        if table not in new:
            out.append(f"the filter on table {_q(table)} is gone (it was {_shown(old[table])})")
        elif table not in old:
            out.append(f"a filter on table {_q(table)} was added: {_shown(new[table])}")
        elif not new[table].strip():
            out.append(f"the filter on table {_q(table)} came back empty (it was {_shown(old[table])})")
        else:
            out.append(f"the filter on table {_q(table)} changed from {_shown(old[table])} to {_shown(new[table])}")
    for key, what in (("tables", "table permission"), ("columns", "column permission"), ("members", "member")):
        gone, added = sorted(a[key] - b[key]), sorted(b[key] - a[key])
        if gone:
            out.append(f"{len(gone)} {what}(s) gone: {', '.join(map(_shown_key, gone[:5]))}")
        if added:
            out.append(f"{len(added)} {what}(s) added: {', '.join(map(_shown_key, added[:5]))}")
    return out


def _calc_items(table: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    group: dict[str, Any] = table["calculationGroup"] if isinstance(table.get("calculationGroup"), dict) else {}
    return {i["name"]: (_text(i.get("expression")), str(i.get("ordinal", "")),
                                _text((i.get("formatStringDefinition") or {}).get("expression")
                                      if isinstance(i.get("formatStringDefinition"), dict) else i.get("formatStringDefinition")))
            for i in _named_items(group.get("calculationItems"))}


def _calc_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """How calculation group ``after`` differs from ``before``: each item's name, expression, ordinal and format
    string expression."""
    a, b = _calc_items(before), _calc_items(after)
    out = [f"calculation item {_q(n)} is gone" for n in a if n not in b]
    out += [f"calculation item {_q(n)} was added" for n in b if n not in a]
    for n in (n for n in a if n in b):
        for i, what in enumerate(("expression", "ordinal", "format string expression")):
            if a[n][i] != b[n][i]:
                out.append(f"calculation item {_q(n)} {what} changed from {_shown(a[n][i])} to {_shown(b[n][i])}")
    return out


def _dicts(items: Any) -> list[dict[str, Any]]:
    """The objects of a list, nothing for what isn't one (a file can say anything)."""
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _named_items(items: Any) -> list[dict[str, Any]]:
    return [i for i in _dicts(items) if isinstance(i.get("name"), str)]


def _names(items: Any) -> set[str]:
    return {i["name"] for i in _named_items(items)}


class _Objects:
    """The report's objects in input order, each once, and the names report rows use for them."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.ids: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, dict[str, Any] | None] = {}  # None: more than one object goes by it
        self.children: dict[tuple[str, str], list[dict[str, Any]]] = {}  # (parent id, kind) -> objects
        self.lengths: set[int] = set()  # of the ids and aliases, so find tries only cuts that can match
        self._next: dict[str, int] = {}  # the next number to try for a name used again
        self._cuts: list[int] | None = None  # lengths, longest first, sorted once for every find
        # id -> the object's structural key (its full names, never cut short), for the enumerations that set one;
        # kept beside the report's objects, not in them, so the report's shape is unchanged (structural parity)
        self.keys: dict[str, tuple[Any, ...]] = {}

    def add(self, kind: str, ident: str, cls: str, changes: list[str] | None = None, notes: list[str] | None = None,
            aliases: tuple[str, ...] = (), in_extension: list[str] | None = None, parent: str = "model",
            key: tuple[Any, ...] | None = None) -> dict[str, Any]:
        base = ident
        if ident in self.ids:  # the same name twice (a file can say anything): still one row each
            n = self._next.get(base, 2)
            while f"{base} #{n}" in self.ids:
                n += 1
            ident = f"{base} #{n}"
            self._next[base] = n + 1
        changes = list(changes or [])
        if changes and cls in ("fully_preserved", "preserved_through_extensions", "unverified"):
            cls = "partially_converted"
        obj: dict[str, Any] = {"object": ident, "kind": kind, "class": cls, "changes": changes,
                               "notes": list(notes or []), "in_extension": sorted(in_extension or []),
                               "evidence": []}
        if kind == "role":
            obj["flag"] = "security"
        elif kind == "calculation_group":
            obj["flag"] = "calculation_group"
        self.items.append(obj)
        self.ids[ident] = obj
        self.lengths.add(len(ident))
        self._cuts = None
        self.children.setdefault((parent, kind), []).append(obj)
        if key is not None:
            self.keys[ident] = key
        for alias in (base, *aliases):
            if alias != ident:
                self.aliases[alias] = obj if self.aliases.get(alias, obj) is obj else None
                self.lengths.add(len(alias))
        return obj

    def find(self, where: str) -> dict[str, Any] | None:
        """The object a report row names: the longest id or alias ``where`` starts with, whole words only. Only
        cuts as long as some id or alias are tried, so a row naming a huge name costs no more than the ids do."""
        if self._cuts is None:
            self._cuts = sorted(self.lengths, reverse=True)
        for cut in self._cuts:
            if cut > len(where) or (cut < len(where) and where[cut] != " "):
                continue
            prefix = where[:cut]
            hit = self.ids.get(prefix) or self.aliases.get(prefix)
            if hit is not None:
                return hit
        return None


def _extension_keys(data: dict[str, Any]) -> list[str]:
    return [k for k in data if k not in _STASH_CONTROL]


def _tmsl_to_ossie(bim: dict[str, Any], doc: dict[str, Any], objs: _Objects) -> None:
    """Classify each object of ``bim`` by where it is in ``doc`` (the Ossie document Export wrote): in the
    Ossie core, only in a POWER_BI extension, or nowhere."""
    model: dict[str, Any] = bim["model"] if isinstance(bim.get("model"), dict) else {}
    datasets = {d["name"]: d for d in _named_items(doc.get("datasets"))}
    stash = _stash_data(doc)
    excluded = {t["name"]: t for t in _named_items(stash.get("excludedTables"))}
    metrics: dict[tuple[Any, Any], dict[str, Any]] = {}
    for m in _named_items(doc.get("metrics")):
        data = _stash_data(m)
        metrics.setdefault((data.get("table"), data.get("name", m["name"])), m)
    excluded_measures = {(e.get("table"), e["measure"].get("name")) for e in _dicts(stash.get("excludedMeasures"))
                         if isinstance(e.get("measure"), dict)}
    named = isinstance(bim.get("name"), str) and bool(bim["name"])
    objs.add("model", "model", "fully_preserved",
             [] if named else [f"the model has no name; written as {_q(doc.get('name'))}"],
             in_extension=_extension_keys(stash))
    for t in _named_items(model.get("tables")):
        name, tid = t["name"], f"table {_q(t['name'])}"
        kind = "calculation_group" if t.get("calculationGroup") else "table"
        ds, ex = datasets.get(name), excluded.get(name)
        ds_stash = _stash_data(ds) if ds is not None else {}
        if ds is not None:
            objs.add(kind, tid, "fully_preserved", in_extension=_extension_keys(ds_stash))
        else:
            objs.add(kind, tid, "preserved_through_extensions" if ex is not None else "unsupported",
                     _calc_changes(t, ex) if ex is not None and kind == "calculation_group" else [])
        fields = {f["name"]: f for f in _named_items((ds or {}).get("fields"))}
        set_aside = _names(ds_stash.get("excludedColumns"))
        ex_columns, ex_measures = _names((ex or {}).get("columns")), _names((ex or {}).get("measures"))
        for c in _named_items(t.get("columns")):
            cid = f"{tid} column {_q(c['name'])}"
            field = fields.get(c["name"])
            if field is not None:
                changes, dt = [], c.get("dataType")
                if field.get("datatype") == "Opaque":
                    changes.append(f"data type {dt} has no Ossie type; written as Opaque (kept in the POWER_BI extension)")
                elif isinstance(dt, str) and dt and not field.get("datatype"):
                    changes.append(f"data type {dt} has no Ossie type; left out (kept in the POWER_BI extension)")
                objs.add("column", cid, "fully_preserved", changes, aliases=(f"{_short(name)}[{_short(c['name'])}]",),
                         in_extension=_extension_keys(_stash_data(field)))
            else:
                kept = c["name"] in set_aside or c["name"] in ex_columns
                objs.add("column", cid, "preserved_through_extensions" if kept else "unsupported",
                         aliases=(f"{_short(name)}[{_short(c['name'])}]",))
        for m in _named_items(t.get("measures")):
            mid = f"{tid} measure {_q(m['name'])}"
            metric = metrics.get((name, m["name"]))
            if metric is not None:
                dialects = [str(d.get("dialect")) for d in (metric.get("expression") or {}).get("dialects") or []
                            if isinstance(d, dict)]
                changes = [] if metric["name"] == m["name"] else [f"renamed to {_q(metric['name'])} (Ossie metric names are model-wide)"]
                translated = [d for d in dialects if d != "DAX"]
                cls = "unverified" if translated else "fully_preserved"
                notes = [(f"translated from DAX to {', '.join(translated)}; the DAX is kept beside it, and the two "
                          "aren't compared")] if translated else []
                objs.add("measure", mid, cls, changes, notes, aliases=(f"{_short(name)}[{_short(m['name'])}]",),
                         in_extension=_extension_keys(_stash_data(metric)))
            else:
                kept = (name, m["name"]) in excluded_measures or m["name"] in ex_measures
                objs.add("measure", mid, "preserved_through_extensions" if kept else "unsupported",
                         aliases=(f"{_short(name)}[{_short(m['name'])}]",))
        stashed_hierarchies = _names(ds_stash.get("hierarchies")) | _names((ex or {}).get("hierarchies"))
        for h in _named_items(t.get("hierarchies")):
            objs.add("hierarchy", f"{tid} hierarchy {_q(h['name'])}",
                     "preserved_through_extensions" if h["name"] in stashed_hierarchies else "unsupported", parent=tid)
    by_name: dict[Any, dict[str, Any]] = {}
    by_ends: dict[tuple[Any, ...], collections.deque[dict[str, Any]]] = {}
    for r in _named_items(doc.get("relationships")):
        data = _stash_data(r)
        ends = (r.get("from"), (r.get("from_columns") or [None])[0], r.get("to"), (r.get("to_columns") or [None])[0])
        if "name" in data:
            by_name.setdefault(_canon(data["name"]), r)
        for pair in {ends, (ends[2], ends[3], ends[0], ends[1])}:
            by_ends.setdefault(tuple(map(_canon, pair)), collections.deque()).append(r)
    set_aside_rels = collections.Counter(_canon(r) for r in _dicts(stash.get("excludedRelationships")))
    used: set[int] = set()  # each core relationship matches one relationship of the model, never two
    unnamed = len([r for r in _dicts(model.get("relationships")) if "name" not in r])
    for r in _dicts(model.get("relationships")):
        label = r.get("name") if isinstance(r.get("name"), str) and r.get("name") else _rel_key(r)
        rid = f"relationship {_q(label)}"
        aliases = (f"relationship {_short(label)!r}",) + (("relationship '<unnamed>'",) if "name" not in r and unnamed == 1 else ())
        if set_aside_rels[_canon(r)] > 0:  # what the converter set aside (inactive, many to many, a duplicate)
            set_aside_rels[_canon(r)] -= 1
            objs.add("relationship", rid, "preserved_through_extensions", aliases=aliases)
            continue
        hit = by_name.get(_canon(r["name"])) if r.get("name") else None
        if hit is None or id(hit) in used:
            queue = by_ends.get(tuple(map(_canon, (r.get("fromTable"), r.get("fromColumn"), r.get("toTable"),
                                                   r.get("toColumn")))), collections.deque())
            while queue and id(queue[0]) in used:
                queue.popleft()
            hit = queue.popleft() if queue and not r.get("name") else None
        if hit is not None:
            used.add(id(hit))
            objs.add("relationship", rid, "fully_preserved", aliases=aliases, in_extension=_extension_keys(_stash_data(hit)))
        else:
            objs.add("relationship", rid, "unsupported", aliases=aliases)
    for key, kind in _STASHED_OBJECTS:
        in_stash = {i["name"]: i for i in _named_items(stash.get(key))}
        for item in _named_items(model.get(key)):
            stashed_item = in_stash.get(item["name"])
            objs.add(kind, f"{kind} {_q(item['name'])}", "preserved_through_extensions" if stashed_item else "unsupported",
                     _role_changes(item, stashed_item) if stashed_item is not None and kind == "role" else [],
                     aliases=(f"{kind} {_short(item['name'])!r}",))


def _ossie_drops(obj: dict[str, Any]) -> list[str]:
    """What the converter drops from an Ossie object because Power BI has nowhere to put it."""
    out = ["display label dropped: Power BI has nowhere to record it"] if "label" in obj else []
    out += [f"custom_extensions for vendor {_q(e.get('vendor_name'))} dropped" for e in _dicts(obj.get("custom_extensions"))
            if e.get("vendor_name") != "POWER_BI"]
    return out


def _expression_class(target: dict[str, Any] | None, changes: list[str], notes: list[str]) -> str:
    """Fully preserved, Partially converted (a BLANK() placeholder for SQL that wasn't translated) or Unverified
    (SQL translated to DAX) for a measure or column the converter wrote, or Unsupported when it wrote none."""
    if target is None:
        return "unsupported"
    if _is_placeholder(target):
        changes.append("needs_dax: the SQL wasn't translated to DAX; written as BLANK() until it's written in DAX")
        return "partially_converted"
    dialect = next((a.get("value") for a in _dicts(target.get("annotations")) if a.get("name") == _DIALECT_ANNOTATION),
                   None)
    if dialect:
        notes.append(f"translated from {dialect} to DAX; the two aren't compared, and the original is kept as annotations")
        return "unverified"
    return "fully_preserved"


def _ossie_to_tmsl(doc: dict[str, Any], bim: dict[str, Any], objs: _Objects) -> None:
    """Classify each object of ``doc`` (an Ossie document, as written, with its POWER_BI extensions) by
    what ``bim`` (the model the converter wrote from it) has."""
    model: dict[str, Any] = bim["model"] if isinstance(bim.get("model"), dict) else {}
    tables = {t["name"]: t for t in _named_items(model.get("tables"))}
    stash = _stash_data(doc)

    index: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def member(table: Any, key: str, name: Any) -> dict[str, Any] | None:
        """The table's column, measure or hierarchy ``name``, from an index built once per table and key."""
        if not isinstance(table, str) or not isinstance(name, str):
            return None
        if (table, key) not in index:
            found = index[(table, key)] = {}
            for i in _named_items((tables.get(table) or {}).get(key)):
                found.setdefault(i["name"], i)
        return index[(table, key)].get(name)

    def placed(name: Any, recorded: Any) -> tuple[Any, dict[str, Any] | None]:
        """Where the converter puts a metric's measure ``name`` (its recorded home table when the model has it,
        else the first table) and the measure there, if it wrote one."""
        home = recorded if recorded in tables else next(iter(tables), None)
        return home, member(home, "measures", name)

    added = sorted(_names(model.get("expressions")) - _names(stash.get("expressions")))
    objs.add("model", "model", "fully_preserved", _ossie_drops(doc), aliases=("document",), key=("model",),
             notes=[f"the converter added the shared expression {_q(n)} (the Direct Lake source)" for n in added])
    datasets = _named_items(doc.get("datasets"))
    model_metrics = _named_items(doc.get("metrics"))
    taken = {m["name"] for m in model_metrics}
    lifted: dict[int, str] = {}  # id(metric) -> its home table, for the dataset metrics _lift_dataset_metrics moves
    for d in datasets:
        for m in _named_items(d.get("metrics")):
            if m["name"] not in taken:
                taken.add(m["name"])
                lifted[id(m)] = d["name"]
    for d in datasets:
        name, did = d["name"], f"dataset {_q(d['name'])}"
        ds_stash = _stash_data(d)
        changes = _ossie_drops(d)
        if len(d.get("primary_key") or []) > 1:
            changes.append("composite primary key not marked: Power BI has no composite key")
        changes += ["composite unique key not marked: Power BI has no composite unique constraint"
                    for k in d.get("unique_keys") or [] if isinstance(k, list) and len(k) > 1]
        objs.add("table", did, "fully_preserved" if name in tables else "unsupported",
                 changes if name in tables else [], aliases=(f"table {_q(name)}",), key=("table", name))
        for f in _named_items(d.get("fields")):
            col = member(name, "columns", f["name"])
            changes, notes = _ossie_drops(f), list[str]()
            dt = f.get("datatype")
            if col is not None and dt in ("Time", "DateTimeTz"):
                changes.append(f"data type {dt} stored as dateTime: Power BI has no "
                               + ("time-only type" if dt == "Time" else "timezone-aware type; the UTC offset is lost"))
            elif col is not None and dt and not col.get("dataType"):
                changes.append(f"data type {dt} has no Power BI type; left unspecified")
            cls = _expression_class(col, changes, notes)
            objs.add("column", f"{did} field {_q(f['name'])}", cls, changes if col is not None else [], notes,
                     aliases=(f"table {_q(name)} column {_q(f['name'])}", f"{_short(name)}[{_short(f['name'])}]"),
                     key=("column", name, f["name"]))
        for c in _named_items(ds_stash.get("excludedColumns")):
            objs.add("column", f"table {_q(name)} column {_q(c['name'])}",
                     "fully_preserved" if member(name, "columns", c["name"]) else "unsupported",
                     notes=[RESTORED], key=("column", name, c["name"]))
        for h in _named_items(ds_stash.get("hierarchies")):
            objs.add("hierarchy", f"table {_q(name)} hierarchy {_q(h['name'])}",
                     "fully_preserved" if member(name, "hierarchies", h["name"]) else "unsupported",
                     notes=[RESTORED], parent=did, key=("hierarchy", name, h["name"]))
        for m in _named_items(d.get("metrics")):
            mid = f"{did} metric {_q(m['name'])}"
            if id(m) not in lifted:
                objs.add("measure", mid, "unsupported",
                         notes=["a model-level metric has the same name, so this one was left out"],
                         key=("measure", m["name"]))
                continue
            _metric(objs, m, mid, lifted[id(m)], placed, (f"metric {_q(m['name'])}",))
    for m in model_metrics:
        _metric(objs, m, f"metric {_q(m['name'])}", None, placed, ())
    rels = _dicts(model.get("relationships"))
    used: set[int] = set()  # each relationship of the model matches one of the file's, never two
    by_canon: dict[str, collections.deque[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_ends: dict[str, collections.deque[dict[str, Any]]] = {}
    for r in rels:
        by_canon.setdefault(_canon(r), collections.deque()).append(r)
        by_name.setdefault(_canon(r.get("name")), []).append(r)
        by_ends.setdefault(_canon((r.get("fromTable"), r.get("fromColumn"), r.get("toTable"), r.get("toColumn"))),
                           collections.deque()).append(r)

    def claim(queue: collections.deque[dict[str, Any]] | None) -> dict[str, Any] | None:
        while queue:
            candidate = queue.popleft()
            if id(candidate) not in used:
                used.add(id(candidate))
                return candidate
        return None

    # The extension's set-aside relationships come back verbatim; matched first, so one never also counts as
    # a converted relationship.
    set_aside = [(r, claim(by_canon.get(_canon(r))) is not None) for r in _dicts(stash.get("excludedRelationships"))]
    for r in _dicts(doc.get("relationships")):
        data, label = _stash_data(r), r.get("name") or "<unnamed>"
        ends = (r.get("from"), (r.get("from_columns") or [None])[0], r.get("to"), (r.get("to_columns") or [None])[0])
        hit = None
        if len(r.get("from_columns") or []) == 1 and len(r.get("to_columns") or []) == 1:  # composite never converts
            hit = next((x for n in (data.get("name", r.get("name")), r.get("name")) if n
                        for x in by_name.get(_canon(n), [])[:1] if id(x) not in used), None)
            if hit is not None:
                used.add(id(hit))
            for pair in (ends, (ends[2], ends[3], ends[0], ends[1])):
                hit = hit or claim(by_ends.get(_canon(pair)))
        objs.add("relationship", f"relationship {_q(label)}", "fully_preserved" if hit else "unsupported",
                 _ossie_drops(r) if hit else [],
                 key=("relationship", ends_key(r.get("from"), r.get("from_columns"), r.get("to"), r.get("to_columns"))))
    for key, kind in _STASHED_OBJECTS:
        have: dict[str, dict[str, Any]] = {}
        for i in _named_items(model.get(key)):
            have.setdefault(i["name"], i)
        for item in _named_items(stash.get(key)):
            restored_item = have.get(item["name"])
            objs.add(kind, f"{kind} {_q(item['name'])}", "fully_preserved" if restored_item is not None else "unsupported",
                     _role_changes(item, restored_item) if restored_item is not None and kind == "role" else [],
                     notes=[RESTORED], aliases=(f"{kind} {_short(item['name'])!r}",), key=(kind, item["name"]))
    for t in _named_items(stash.get("excludedTables")):
        tid = f"table {_q(t['name'])}"
        out_table = tables.get(t["name"])
        group = bool(t.get("calculationGroup"))
        objs.add("calculation_group" if group else "table", tid, "fully_preserved" if out_table else "unsupported",
                 _calc_changes(t, out_table) if out_table is not None and group else [],
                 notes=[RESTORED], key=("table", t["name"]))
        for key, kind in (("columns", "column"), ("measures", "measure"), ("hierarchies", "hierarchy")):
            for i in _named_items(t.get(key)):
                objs.add(kind, f"{tid} {kind} {_q(i['name'])}",
                         "fully_preserved" if member(t["name"], key, i["name"]) else "unsupported",
                         notes=[RESTORED], parent=tid,
                         aliases=(f"{_short(t['name'])}[{_short(i['name'])}]",),
                         key=("measure", i["name"]) if kind == "measure" else (kind, t["name"], i["name"]))
    for e in _dicts(stash.get("excludedMeasures")):
        kept = e.get("measure")
        if not isinstance(kept, dict) or not isinstance(kept.get("name"), str):
            continue
        objs.add("measure", f"table {_q(e.get('table'))} measure {_q(kept['name'])}",
                 "fully_preserved" if member(e.get("table"), "measures", kept["name"]) else "unsupported",
                 notes=[RESTORED],
                 aliases=(f"excluded measure {_q(kept['name'])}", f"{_short(e.get('table'))}[{_short(kept['name'])}]"),
                 key=("measure", kept["name"]))
    for r, back in set_aside:
        label = r.get("name") if isinstance(r.get("name"), str) and r.get("name") else _rel_key(r)
        objs.add("relationship", f"relationship {_q(label)}", "fully_preserved" if back else "unsupported",
                 notes=[RESTORED], key=("relationship", tmsl_ends_key(r)))


def _metric(objs: _Objects, m: dict[str, Any], mid: str, home: Any, placed: Any, aliases: tuple[str, ...]) -> None:
    """One Ossie metric, found as the measure the converter wrote for it."""
    data = _stash_data(m)
    recorded = data.get("table", home)
    table, measure = placed(data.get("name", m["name"]), recorded)
    changes, notes = _ossie_drops(m) if measure is not None else [], []
    if measure is not None and recorded is None:
        notes.append(f"no home table recorded; placed on {_q(table)}")
    elif measure is not None and table != recorded:
        changes.append(f"placed on {_q(table)}, not {_q(recorded)}, which isn't in the model")
    if measure is not None and m.get("datatype") and "dataType" not in data:
        notes.append(f"datatype {m['datatype']} not applied: Power BI infers a measure's data type from its DAX")
    cls = _expression_class(measure, changes, notes)
    if measure is None and not _dialect_entries(m):
        notes.append("the metric has no expression")
    objs.add("measure", mid, cls, changes, notes,
             aliases=aliases + ((f"{_short(table)}[{_short(measure['name'])}]",) if measure else ()),
             key=("measure", data.get("name", m["name"])))


def _dialect_entries(m: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in (m.get("expression") or {}).get("dialects") or [] if isinstance(d, dict) and d.get("expression")]


def _remediation(obj: dict[str, Any], to_ossie: bool) -> str:
    """What to do about an object that didn't come through fully preserved, in one line."""
    cls, kind = obj["class"], obj["kind"]
    if cls == "preserved_through_extensions":
        if kind == "role":
            return ("Kept for Power BI only, in the POWER_BI extension: a tool that reads this file won't apply this "
                    "row-level security. Define the same security in that tool before sharing data through it.")
        return ("Kept for Power BI only, in the POWER_BI extension, so it comes back on import. Another tool won't see "
                f"this {kind.replace('_', ' ')}; recreate it there if that tool needs it.")
    if cls == "unverified":
        return ("Check that the translated expression gives the same results as the original before relying on it; "
                "KratosForge hasn't compared them.")
    if cls == "partially_converted":
        if kind == "role":
            return ("This security role came through changed (see changes). Check its filters, permission and "
                    "members by hand and put them right before anyone uses the model.")
        if kind == "calculation_group":
            return ("This calculation group came through changed (see changes). Check each calculation item's "
                    "expression, order and format string by hand before relying on it.")
        if any(c.startswith("needs_dax") for c in obj["changes"]):
            return "Write this measure in DAX in Fabric or the TMDL; it is BLANK() until then."
        return "Review the changes listed and set them by hand in the destination if they matter."
    if kind == "role":
        return ("This security role isn't in the result. Recreate it by hand before anyone uses the model, or the "
                "data it restricts is open to everyone with read access.")
    if kind == "relationship":
        return ("Recreate it by hand in the destination; "
                + ("an Ossie relationship joins one column to one column, many to one" if not to_ossie
                   else "check that both tables are exported"))
    if kind == "measure" and not to_ossie:
        return "Give the metric an expression (DAX converts as written), or recreate the measure by hand."
    return f"Recreate this {kind.replace('_', ' ')} by hand in the destination; it has no equivalent there."


def fidelity(source: dict[str, Any], destination: dict[str, Any], rows: list[dict[str, str]] | None = None,
             validation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The fidelity report of one conversion: every object of ``source`` once, in one of ``CLASSES``.

    ``source`` and ``destination`` are a parsed ``model.bim`` and the parsed Ossie document converted from it
    (Export), or the other way round (Import, Ossie to TMDL). Each object is classed by structure: what reached
    the destination, what sits only in a POWER_BI extension, what is missing. ``rows`` (the converter's, the TMDL
    reader's and the merge's report rows) are attached to the object they name as ``evidence``, never read to
    decide a class; a row that names no object is a warning. ``validation`` is ``round_trip_check``'s result.
    Security roles and calculation groups are flagged, and a role that doesn't come through is a security warning
    at the top of ``warnings``."""
    objs = _Objects()
    if not isinstance(source, dict) or not isinstance(destination, dict):
        raise ValueError("fidelity compares a model.bim with the Ossie document converted from it, or the other way round")
    if isinstance(source.get("model"), dict) and isinstance(destination.get("datasets"), list):
        to_ossie = True
        _tmsl_to_ossie(source, destination, objs)
        src = {"format": "TMSL", "version": str(source.get("compatibilityLevel", ""))}
        dst = {"format": "Apache Ossie", "version": str(destination.get("version", ""))}
    elif isinstance(source.get("datasets"), list) and isinstance(destination.get("model"), dict):
        to_ossie = False
        _ossie_to_tmsl(source, destination, objs)
        src = {"format": "Apache Ossie", "version": str(source.get("version", ""))}
        dst = {"format": "TMSL", "version": str(destination.get("compatibilityLevel", ""))}
    else:
        raise ValueError("fidelity compares a model.bim with the Ossie document converted from it, or the other way round")
    warnings_: list[dict[str, Any]] = []
    for row in rows or []:
        hit = objs.find(str(row.get("object", "")))
        # A row about a whole collection ("row-level security roles ('roles') ...") is evidence for its members.
        kind = next((k for key, k in _COLLECTION_ROWS if f"('{key}')" in str(row.get("message", ""))), None)
        members = [] if hit is None or kind is None else \
            objs.children.get(("model" if hit["kind"] == "model" else hit["object"], kind), [])
        for target in members or ([hit] if hit is not None else []):
            target["evidence"].append(dict(row))
        if hit is None:
            warnings_.append({"object": row.get("object", ""), "message": row.get("message", ""), "severity": "warning"})
    check = dict(validation or {"check": "round_trip", "ran": False, "passed": None, "error": "",
                                "reason": "not run for this conversion", "rows": []})
    differences = list(check.pop("rows", []) or [])
    affected = []
    for row in differences:
        hit = objs.find(str(row.get("object", "")))
        if hit is not None:
            hit["evidence"].append(dict(row) | {"check": "round_trip"})
            affected.append(hit["object"])
        else:
            warnings_.append({"object": row.get("object", ""), "message": row.get("message", ""), "severity": "warning"})
    check |= {"differences": [r.get("object", "") for r in differences], "objects": list(dict.fromkeys(affected))}
    security: list[dict[str, Any]] = []
    for obj in objs.items:
        obj["label"] = CLASSES[obj["class"]]
        obj["remediation"] = "" if obj["class"] == "fully_preserved" else _remediation(obj, to_ossie)
        if obj["kind"] == "role" and obj["class"] == "partially_converted":
            security.append({"object": obj["object"], "severity": "security", "prominent": True,
                             "message": "This security role came through changed: " + "; ".join(obj["changes"])})
        elif obj["kind"] == "role" and obj["class"] == "unsupported":
            security.append({"object": obj["object"], "severity": "security", "prominent": True,
                             "message": "This security role is lost: the result has no row-level security for it"})
        elif obj["kind"] == "role" and obj["class"] == "preserved_through_extensions":
            security.append({"object": obj["object"], "severity": "security", "prominent": True,
                             "message": "This security role is only in the POWER_BI extension: a tool other than "
                                        "Power BI that reads the file won't apply it"})
        elif obj["kind"] == "calculation_group" and obj["class"] == "unsupported":
            security.append({"object": obj["object"], "severity": "warning", "prominent": True,
                             "message": "This calculation group is lost"})
        elif obj["kind"] == "calculation_group" and obj["class"] == "partially_converted":
            security.append({"object": obj["object"], "severity": "warning", "prominent": True,
                             "message": "This calculation group came through changed: " + "; ".join(obj["changes"])})
    security.sort(key=lambda w: w["severity"] != "security")  # security roles first, in input order
    by_class: dict[str, list[str]] = {c: [] for c in CLASSES}
    for obj in objs.items:
        by_class[obj["class"]].append(obj["object"])
    return {
        "status": "complete", "error": "", "source": src, "destination": dst, "converter": dict(CONVERTER),
        "objects": objs.items,
        "counts": {c: len(v) for c, v in by_class.items()},
        "by_class": by_class,
        "excluded": [{"object": o["object"], "kind": o["kind"], "class": o["class"]} for o in objs.items
                     if o["class"] in ("preserved_through_extensions", "unsupported")],
        "metadata_changes": [{"object": o["object"], "change": c} for o in objs.items for c in o["changes"]]
        + [{"object": o["object"], "change": n, "informational": True} for o in objs.items for n in o["notes"]],
        "flagged": {"roles": [{"object": o["object"], "class": o["class"]} for o in objs.items if o["kind"] == "role"],
                    "calculation_groups": [{"object": o["object"], "class": o["class"]} for o in objs.items
                                           if o["kind"] == "calculation_group"]},
        "warnings": security + warnings_,
        "validation": check,
        "remediation": [{"object": o["object"], "class": o["class"], "action": o["remediation"]} for o in objs.items
                        if o["remediation"]],
    }


# What a report can't be built from (a document of the wrong shape): the conversion still stands, reported as such.
_SHAPE_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError)


def safe_fidelity(source: dict[str, Any], destination: dict[str, Any], rows: list[dict[str, str]] | None = None,
                  validation: dict[str, Any] | None = None) -> dict[str, Any]:
    """``fidelity``, or when it can't be built, a report with ``status`` "unverified", the reason in ``error`` and
    the model as one Unverified object, so the conversion it describes still goes ahead and nothing claims to
    be preserved."""
    try:
        return fidelity(source, destination, rows, validation)
    except _SHAPE_ERRORS as exc:  # narrow: the shape errors a hostile document causes; the reason is in the report
        reason = f"The fidelity report couldn't be built: {type(exc).__name__}: {_short(exc)}"
    model = {"object": "model", "kind": "model", "class": "unverified", "label": CLASSES["unverified"], "changes": [],
             "notes": [reason], "in_extension": [], "evidence": [],
             "remediation": "Check the converted model by hand: KratosForge couldn't compare it with the file."}
    return {"status": "unverified", "error": reason, "source": {}, "destination": {}, "converter": dict(CONVERTER),
            "objects": [model], "counts": {c: int(c == "unverified") for c in CLASSES},
            "by_class": {c: ["model"] if c == "unverified" else [] for c in CLASSES}, "excluded": [],
            "metadata_changes": [], "flagged": {"roles": [], "calculation_groups": []},
            "warnings": [{"object": "model", "severity": "warning", "prominent": True, "message": reason}],
            "validation": {"check": "round_trip", "ran": False, "passed": None, "error": "", "reason": reason,
                           "differences": [], "objects": []},
            "remediation": [{"object": "model", "class": "unverified", "action": model["remediation"]}]}


MAX_ROUND_TRIP_OBJECTS = 20_000  # columns plus measures; above it the check (two more conversions) is skipped


def round_trip_check(bim: dict[str, Any]) -> dict[str, Any]:
    """``round_trip`` as the fidelity report's validation result: ``ran``, ``passed`` and the ``rows``. A model
    the check can't send to Ossie and back is a result too (``ran`` false, with the ``error``), not a failure
    of the conversion it validates. A model with more than ``MAX_ROUND_TRIP_OBJECTS`` columns and measures
    isn't checked (``reason`` says so); a check that didn't run never passes."""
    model = bim.get("model") if isinstance(bim, dict) else None
    size = sum(len(_dicts(t.get(key))) for t in _dicts(model.get("tables") if isinstance(model, dict) else None)
               for key in ("columns", "measures"))
    if size > MAX_ROUND_TRIP_OBJECTS:
        return {"check": "round_trip", "ran": False, "passed": None, "error": "",
                "reason": "model too large for round-trip check", "rows": []}
    try:
        rows = round_trip(bim)
    except (*_SHAPE_ERRORS, RecursionError) as exc:  # narrow: what the converter refuses or trips on (or nesting too
        # deep to compare), kept in the result and shown
        return {"check": "round_trip", "ran": False, "passed": None, "error": f"{type(exc).__name__}: {exc}",
                "reason": "the round trip couldn't run", "rows": []}
    return {"check": "round_trip", "ran": True, "passed": not rows, "error": "", "reason": "", "rows": rows}
