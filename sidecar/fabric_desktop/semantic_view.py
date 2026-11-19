"""Fabric Semantic View files (OSSIE-005a, Preview): a semantic model as the YAML of a Semantic View folder.

``semantic_view(source, view_name=..., split=...)`` takes a TMSL model (a parsed ``model.bim`` or its JSON text), a
TMDL file set (path -> text) or an Ossie 0.2.0.dev0 document (a dict or its YAML/JSON text), and returns the text of
the ``.yaml`` files of one Semantic View folder, with a report. It is the "Semantic View YAML" kind of plan D.1: a
local copy KratosForge writes and checks. It never writes a file, never calls Fabric and never publishes;
publishing is done by a person in Fabric (Upload, Save, Publish), and Fabric then generates the read-only Direct
Lake model itself. The service's ``meta_model_semantic_view_save`` writes the files into the project folder.

What goes into the files is what the YAML reference documents as supported, and nothing else (research section C,
"Supported YAML"): root ``version``, ``name``, ``description``, ``datasets``, ``relationships``, ``metrics``; a
dataset's ``name``, ``source``, ``description``; a relationship's ``name``, ``from``, ``to``, ``from_columns``,
``to_columns``; a metric's ``name``, ``description`` and ``expression``. Everything else is left out of the files
and reported, each construct as Unsupported for this target with the rule and page it comes from (``STRIPPED``):

- ``custom_extensions`` anywhere: "omitted with warnings", including Microsoft's own. What lives only in the
  ``POWER_BI`` extension (security roles, calculation groups, perspectives, cultures, shared expressions,
  hierarchies, tables, columns, measures and relationships the converter set aside) is listed object by object,
  and every security role is a prominent security warning: the published view has no row-level security for it;
- dataset ``fields``, ``primary_key``, ``unique_keys`` and every ``ai_context``: "omitted with warnings";
- a metric's ``datatype``: "ignored";
- a dataset whose source is a query: "Query sources are not supported" (the dataset is excluded);
- any other key outside the supported YAML.

Every metric needs DAX ("Published metrics require DAX"). A metric with DAX keeps it (Fully preserved). A metric
with only SQL is translated by the vendored converter's SQL-to-DAX (the same translation Import uses), and the DAX
is added beside the SQL (Unverified: the two aren't compared, as in the fidelity report). A metric the converter
can't translate is flagged before any file is written: Unsupported with the converter's reason, left out of the
files and listed in ``excluded``. A relationship that isn't single-column is Unsupported and excluded too.

``split="single"`` writes one file. ``split="by_dataset"`` writes the model-level parts (description,
relationships, metrics with no home dataset) in the first file and one dataset, with the metrics whose home it
is, in each other file, as the YAML reference's "Validation across files" allows: every file has ``version`` and
``name``, ``datasets`` is never an empty list, and the top-level description is in one file only. A view never
has more than ``ossie_profiles.FABRIC_MAX_FILES`` files: past that many datasets, datasets share files, and the
report says so.

Pure: nothing is fetched, written or executed.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

import sqlglot

from . import model_ossie as mo, ossie_profiles as op, tmdl

SPLITS = ("single", "by_dataset")
KIND = "Semantic View YAML"  # plan D.1's name for this kind of object
MODEL_FILE = "00_model.yaml"
_ROOT_KEYS = ("version", "name", "description", "datasets", "relationships", "metrics")
_DATASET_KEYS = ("name", "source", "description")
_RELATIONSHIP_KEYS = ("name", "from", "to", "from_columns", "to_columns")
_METRIC_KEYS = ("name", "description", "expression")
_QUERY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)  # the vendored converter's test for a query source
_SQL_DIALECTS = mo._SQL_DIALECTS
_YAML_REF, _REF_DATE = op.FABRIC_YAML_REFERENCE, op.FABRIC_YAML_REFERENCE_DATE
PUBLISH = ("KratosForge doesn't publish Semantic Views and never writes a lakehouse's Context/ folder. Publishing is "
           "manual, in Fabric: upload these files to the view's folder in the lakehouse, then Save and Publish.")


def _source(rule: str, says: str, url: str = _YAML_REF) -> dict[str, str]:
    return {"rule": rule, "says": says, "source": url, "page_date": _REF_DATE}


# Each construct the emitter leaves out of the files, with the documented rule it follows.
STRIPPED: dict[str, dict[str, str]] = {
    "custom_extensions": _source("fabric/extensions-omitted",
                                 "All custom_extensions, including Microsoft's own, are omitted with warnings."),
    "fields": _source("fabric/omitted", "Dataset fields are omitted with warnings."),
    "primary_key": _source("fabric/omitted", "Dataset primary_key is omitted with warnings."),
    "unique_keys": _source("fabric/omitted", "Dataset unique_keys is omitted with warnings."),
    "ai_context": _source("fabric/omitted", "All ai_context is omitted with warnings."),
    "datatype": _source("semantic-view/metric-datatype", "Metric datatype is ignored."),
    "query_source": _source("semantic-view/query-source", "Query sources are not supported."),
    "unsupported_key": _source("semantic-view/supported-yaml",
                               "Supported YAML: root version, name, description, datasets, relationships, metrics; "
                               "dataset name, source, description; relationship name, from, to, from_columns, "
                               "to_columns."),
    "metric_dax": _source("fabric/metric-dax", "Published metrics require DAX."),
    "relationship_single_column": _source("fabric/relationship-single-column", "Relationships are single-column only."),
}
# Rule id -> where it is documented, for every rule a row can name: the emitter's own and the profile's.
_RULE_SOURCES: dict[str, dict[str, str]] = {
    **{r["id"]: {"rule": r["id"], "says": r["says"], "source": r["source"], "page_date": r["page_date"]}
       for r in op.RULES if r["profile"] == "fabric-semantic-view"},
    **{s["rule"]: s for s in STRIPPED.values() if s["rule"].startswith("semantic-view/")},
}
# Model collections that live only in the POWER_BI extension, and the kind each item is reported as.
_STASH_COLLECTIONS = (("roles", "role"), ("perspectives", "perspective"), ("cultures", "culture"),
                      ("expressions", "expression"))


# ---- the view name

def folder_name(view_name: Any) -> str:
    """The view's folder name (Fabric names the view and its model after the folder) for ``view_name``: never a
    path. A name with a path separator, ``..``, a device name such as ``CON`` or nothing in it is refused
    (ValueError); other characters Windows doesn't allow become ``_``, as ``tmdl.file_name`` writes them."""
    if not isinstance(view_name, str) or not view_name.strip():
        raise ValueError("Give the semantic view a name")
    name = view_name.strip()
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("A semantic view's name is a folder name, not a path: leave out /, \\ and ..")
    if not name.strip(". "):
        raise ValueError("A semantic view's name needs a letter or digit")
    if name.split(".")[0].upper().rstrip() in tmdl._RESERVED:
        raise ValueError(f"{name} is a device name on Windows, so it can't name the view's folder; pick another name")
    return tmdl.file_name(name, set())


# ---- reading the source

def _read(source: Any, view: str) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """The source as an Ossie 0.2.0.dev0 document, its kind (tmsl, tmdl or ossie) and, for a model, the fidelity
    report of its conversion to Ossie."""
    mo.require()
    if isinstance(source, str):
        text = source.lstrip("﻿ \t\r\n")
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"This isn't valid JSON: {exc}") from None
            except RecursionError:
                raise ValueError("This JSON is nested too deeply to be a semantic model") from None
            if isinstance(parsed, dict) and isinstance(parsed.get("model"), dict):
                source = parsed
            else:
                source = mo.parse(source)
        else:
            source = mo.parse(source)
    if not isinstance(source, Mapping):
        raise TypeError("A semantic view is made from a model.bim, a TMDL folder's files or an Ossie document")
    source = dict(source)
    if isinstance(source.get("model"), dict) and not isinstance(source.get("version"), str):
        out = mo.export(source, translate_dax=True)
        return mo.yaml.safe_load(out["yaml"]), "tmsl", out["fidelity"]
    if source and all(isinstance(k, str) and isinstance(v, str) for k, v in source.items()) \
            and any(k.endswith(".tmdl") for k in source):
        bim, read_report = tmdl.to_bim(source, view)
        out = mo.export(bim, translate_dax=True, read_report=read_report)
        return mo.yaml.safe_load(out["yaml"]), "tmdl", out["fidelity"]
    if "version" not in source:
        raise ValueError("This isn't a semantic model KratosForge can make a view from: a model.bim has a model "
                         "object, a TMDL folder is its .tmdl files, and an Ossie document has a version")
    refusal = mo.not_convertible(source)  # UnknownProfile (a ValueError naming the version) for an unknown one
    if refusal:
        raise ValueError(refusal)
    if not source.get("name") or not isinstance(source.get("datasets"), list) or not source["datasets"]:
        raise ValueError("An Ossie model needs a name and at least one dataset")
    return copy.deepcopy(source), "ossie", None


# ---- the report's rows

class _Rows:
    """The report's per-object rows, each once, named as the fidelity report names objects."""

    def __init__(self) -> None:
        self.objs = mo._Objects()
        self.rows: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def add(self, kind: str, ident: str, cls: str, *, reason: str = "", rule: str = "", key: tuple[Any, ...] | None = None,
            path: str = "", notes: list[str] | None = None, changes: list[str] | None = None, excluded: bool = False,
            stripped: str = "", in_extension: list[str] | None = None) -> dict[str, Any]:
        obj = self.objs.add(kind, ident, cls)
        src = _RULE_SOURCES.get(rule, {})
        row = {"object": obj["object"], "kind": kind, "class": cls, "label": mo.CLASSES[cls], "reason": reason,
               "rule": src.get("rule", rule), "source": src.get("source", ""), "says": src.get("says", ""),
               "key": list(key) if key is not None else None, "path": path, "changes": list(changes or []),
               "notes": list(notes or []), "excluded": excluded, "stripped": stripped,
               "in_extension": sorted(in_extension or [])}
        if "flag" in obj:
            row["flag"] = obj["flag"]
        self.rows.append(row)
        return row

    def strip(self, construct: str, owner: str, path: str, *, detail: str = "", key: tuple[Any, ...] | None = None,
              kind: str = "", in_extension: list[str] | None = None, ident: str = "") -> dict[str, Any]:
        """One construct left out of the files, as Unsupported for this target."""
        src = STRIPPED[construct if construct in STRIPPED else "unsupported_key"]
        shown = construct if construct in STRIPPED else f"key {mo._q(construct)}"
        reason = f"left out of the view: {src['says']}" + (f" {detail}" if detail else "")
        ident = ident or (f"{owner} {shown}" if owner else shown)
        return self.add(kind or construct, ident, "unsupported", reason=reason, rule=src["rule"], key=key, path=path,
                        stripped=construct, in_extension=in_extension)


def _path(base: str, *parts: Any) -> str:
    out = base
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def _stash_rows(rows: _Rows, stash: dict[str, Any], owner: str, path: str, dataset: str | None) -> None:
    """What lives only in a POWER_BI extension, object by object: Fabric omits the extension, so none of it is in
    the view."""
    why = "it lives only in the POWER_BI extension, which Fabric omits"
    if dataset is None:
        for key, kind in _STASH_COLLECTIONS:
            for item in mo._named_items(stash.get(key)):
                row = rows.add(kind, f"{kind} {mo._q(item['name'])}", "unsupported", key=(kind, item["name"]),
                               reason=f"can't go into a Semantic View: {why}", rule="fabric/extensions-omitted",
                               path=path, stripped="custom_extensions")
                if kind == "role":
                    rows.warnings.append({
                        "object": row["object"], "severity": "security", "prominent": True,
                        "message": "This security role can't go into a Semantic View: it lives only in the POWER_BI "
                                   "extension, which Fabric omits, so the published view's model has no row-level "
                                   "security for it. Anyone who can read that model sees the data this role "
                                   "restricts. Set up the same restriction in Fabric (OneLake security) before "
                                   "sharing the view."})
        for t in mo._named_items(stash.get("excludedTables")):
            group = bool(t.get("calculationGroup"))
            kind = "calculation_group" if group else "table"
            tid = f"table {mo._q(t['name'])}"
            row = rows.add(kind, tid, "unsupported", key=("table", t["name"]), path=path,
                           reason=f"can't go into a Semantic View: {why}" + (
                               "; a view has no calculation groups" if group else ""),
                           rule="fabric/extensions-omitted", stripped="custom_extensions")
            if group:
                rows.warnings.append({"object": row["object"], "severity": "warning", "prominent": True,
                                      "message": "This calculation group can't go into a Semantic View: it lives "
                                                 "only in the POWER_BI extension, which Fabric omits"})
            for key, sub in (("columns", "column"), ("measures", "measure"), ("hierarchies", "hierarchy")):
                for i in mo._named_items(t.get(key)):
                    k = ("measure", i["name"]) if sub == "measure" else (sub, t["name"], i["name"])
                    rows.add(sub, f"{tid} {sub} {mo._q(i['name'])}", "unsupported", key=k, path=path,
                             reason="can't go into a Semantic View: its table lives only in the POWER_BI extension, "
                                    "which Fabric omits",
                             rule="fabric/extensions-omitted", stripped="custom_extensions")
        for e in mo._dicts(stash.get("excludedMeasures")):
            m = e.get("measure")
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                rows.add("measure", f"table {mo._q(e.get('table'))} measure {mo._q(m['name'])}", "unsupported",
                         key=("measure", m["name"]), path=path, reason=f"can't go into a Semantic View: {why}",
                         rule="fabric/extensions-omitted", stripped="custom_extensions")
        for r in mo._dicts(stash.get("excludedRelationships")):
            label = r["name"] if isinstance(r.get("name"), str) and r["name"] else mo._rel_key(r)
            rows.add("relationship", f"relationship {mo._q(label)}", "unsupported",
                     key=("relationship", mo.tmsl_ends_key(r)), path=path,
                     reason=f"can't go into a Semantic View: {why}", rule="fabric/extensions-omitted",
                     stripped="custom_extensions")
        return
    for h in mo._named_items(stash.get("hierarchies")):
        rows.add("hierarchy", f"{owner} hierarchy {mo._q(h['name'])}", "unsupported",
                 key=("hierarchy", dataset, h["name"]), path=path, reason=f"can't go into a Semantic View: {why}",
                 rule="fabric/extensions-omitted", stripped="custom_extensions")
    for c in mo._named_items(stash.get("excludedColumns")):
        rows.add("column", f"table {mo._q(dataset)} column {mo._q(c['name'])}", "unsupported",
                 key=("column", dataset, c["name"]), path=path, reason=f"can't go into a Semantic View: {why}",
                 rule="fabric/extensions-omitted", stripped="custom_extensions")


def _strip_extras(rows: _Rows, obj: dict[str, Any], keep: tuple[str, ...], owner: str, path: str,
                  dataset: str | None = None, skip: tuple[str, ...] = ()) -> list[str]:
    """Every key of ``obj`` outside ``keep`` reported as left out (with the POWER_BI extension's contents); the
    changes line for each."""
    changes = []
    for k in obj:
        if k in keep or k in skip:
            continue
        if k == "custom_extensions":
            stash = mo._stash_data(obj)
            vendors = [str(e.get("vendor_name")) for e in mo._dicts(obj.get(k))]
            rows.strip(k, owner, _path(path, k), detail=f"Vendors: {', '.join(vendors) or 'none'}.",
                       in_extension=mo._extension_keys(stash))
            _stash_rows(rows, stash, owner, _path(path, k), dataset if owner != "model" else None)
        else:
            rows.strip(k, owner, _path(path, k))
        changes.append(f"{k} left out")
    return changes


# ---- metrics: DAX for each, or excluded

def _translations(doc: dict[str, Any]) -> tuple[dict[str, tuple[str | None, str]], str]:
    """Measure name -> (its DAX, or None, and why not) from the vendored converter's SQL-to-DAX, the translation
    Import uses; and why the converter couldn't run at all (else "")."""
    try:
        bim, report = mo.to_tmsl(doc)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:  # narrow: the converter's refusals; the reason goes on each metric
        return {}, f"the converter couldn't read the document ({type(exc).__name__}: {mo._short(exc)})"
    reasons = {str(r.get("object")): str(r.get("message")) for r in report if "could not be translated" in
               str(r.get("message"))}
    found: dict[str, tuple[str | None, str]] = {}
    for t in mo._named_items((bim.get("model") or {}).get("tables")):
        for m in mo._named_items(t.get("measures")):
            if m["name"] in found:
                continue
            if mo._is_placeholder(m):
                why = reasons.get(f"metric '{m['name']}'", "")
                detail = re.search(r"could not be translated to DAX \((.*?)\), and Power BI", why, re.S)
                found[m["name"]] = (None, detail[1] if detail else "the vendored converter can't translate it to DAX")
            elif any(a.get("name") == mo._DIALECT_ANNOTATION for a in mo._dicts(m.get("annotations"))):
                found[m["name"]] = (mo._text(m.get("expression")), "")
    return found, ""


def _entries(m: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(d) for d in mo._dialect_entries(m)]


# ---- the emitter

def semantic_view(source: Any, *, view_name: str, split: str = "single") -> dict[str, Any]:
    """``source`` (a TMSL model, a TMDL file set or an Ossie 0.2.0.dev0 document) as a Semantic View folder's files
    (relative path -> YAML text) and the report (module docstring). Writes nothing.

    Each object's row is classed for this target: Fully preserved when what Fabric publishes of it is intact (what
    was left out of it is a row of its own, named in its ``notes``), Unverified for a metric whose DAX was
    translated from SQL, Unsupported for every construct left out and every object excluded."""
    if split not in SPLITS:
        raise ValueError(f"split is one of {', '.join(SPLITS)}")
    folder = folder_name(view_name)
    doc, kind, conversion = _read(source, folder)
    rows = _Rows()

    if conversion is not None:  # what was lost before the model reached Ossie is lost to the view too
        for o in conversion.get("objects") or []:
            if o.get("class") == "unsupported":
                rows.add(o["kind"], o["object"], "unsupported", excluded=True,
                         reason="lost converting the model to Ossie, before the view: " + str(o.get("remediation", "")))

    model_row = rows.add("model", "model", "fully_preserved", key=("model",), notes=[
        f"named {mo._q(folder)} after the view's folder: Fabric names the view and its model after the folder"])
    model_row["notes"] += _strip_extras(rows, doc, _ROOT_KEYS, "model", "$")
    root: dict[str, Any] = {"version": op.V02, "name": folder}
    if "description" in doc:
        root["description"] = doc["description"]

    datasets: list[dict[str, Any]] = []
    kept: set[str] = set()
    query_sources: set[str] = set()
    metrics: list[tuple[dict[str, Any], str | None, str, str]] = []  # (metric, its dataset, path, row id)
    taken = {m["name"] for m in mo._named_items(doc.get("metrics"))}
    for di, d in enumerate(doc.get("datasets") or []):
        dpath = _path("$", "datasets", di)
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"]:
            rows.add("table", f"dataset #{di + 1}", "unsupported", path=dpath, excluded=True,
                     rule="semantic-view/supported-yaml", reason="excluded: it isn't an object with a name")
            continue
        name, did = d["name"], f"dataset {mo._q(d['name'])}"
        src = d.get("source")
        query = isinstance(src, str) and bool(_QUERY.match(src))
        if query:
            query_sources.add(name)
        for fi, f in enumerate(d.get("fields") or []):
            fname = f.get("name") if isinstance(f, dict) else None
            rows.strip("fields", did, _path(dpath, "fields", fi), kind="column",
                       key=("column", name, fname) if isinstance(fname, str) else None,
                       ident=f"{did} field {mo._q(fname)}" if isinstance(fname, str) else f"{did} field #{fi + 1}",
                       detail=(f"Field {mo._q(fname)}; " if isinstance(fname, str) else "")
                       + "Fabric reads the columns from the lakehouse table.")
        notes = ["fields left out"] if d.get("fields") else []
        notes += _strip_extras(rows, d, _DATASET_KEYS, did, dpath, dataset=name, skip=("fields", "metrics"))
        for mi, m in enumerate(d.get("metrics") or []):
            mpath = _path(dpath, "metrics", mi)
            if not isinstance(m, dict) or not isinstance(m.get("name"), str) or not m["name"]:
                rows.add("measure", f"{did} metric #{mi + 1}", "unsupported", path=mpath, excluded=True,
                         rule="semantic-view/supported-yaml", reason="excluded: it isn't an object with a name")
                continue
            if m["name"] in taken:
                rows.add("measure", f"{did} metric {mo._q(m['name'])}", "unsupported", excluded=True, path=mpath,
                         key=("measure", mo._stash_data(m).get("name", m["name"])), rule="fabric/duplicate-name",
                         reason="excluded: a model-level metric has the same name, and a view's names are unique")
                continue
            taken.add(m["name"])
            metrics.append((m, name, mpath, f"{did} metric {mo._q(m['name'])}"))
        if query:
            rows.add("table", did, "unsupported", key=("table", name), path=_path(dpath, "source"), excluded=True,
                     rule="semantic-view/query-source", notes=notes,
                     reason="excluded: its source is a query, and Fabric doesn't support query sources")
            continue
        kept.add(name)
        datasets.append({k: d[k] for k in _DATASET_KEYS if k in d})
        rows.add("table", did, "fully_preserved", key=("table", name), path=dpath, notes=notes)
    for mi, m in enumerate(doc.get("metrics") or []):
        mpath = _path("$", "metrics", mi)
        if isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"]:
            metrics.append((m, None, mpath, f"metric {mo._q(m['name'])}"))
        else:
            rows.add("measure", f"metric #{mi + 1}", "unsupported", path=mpath, excluded=True,
                     rule="semantic-view/supported-yaml", reason="excluded: it isn't an object with a name")

    need = [m for m, *_ in metrics if not any(e.get("dialect") == "DAX" for e in _entries(m))]
    translated, broken = _translations(doc) if need else ({}, "")
    out_metrics: list[tuple[dict[str, Any], str | None]] = []
    for m, home_ds, mpath, mid in metrics:
        stash = mo._stash_data(m)
        placed = stash.get("name", m["name"])
        key = ("measure", placed)
        home = home_ds if home_ds is not None else stash.get("table")
        if home in query_sources:
            rows.add("measure", mid, "unsupported", key=key, path=mpath, excluded=True,
                     rule="semantic-view/query-source",
                     reason=f"excluded: its dataset {mo._q(home)} is a query source, which Fabric doesn't support")
            continue
        entries = _entries(m)
        dax = next((e for e in entries if e.get("dialect") == "DAX"), None)
        cls, notes = "fully_preserved", []
        if dax is None:
            if not entries:
                rows.add("measure", mid, "unsupported", key=key, path=_path(mpath, "expression"), excluded=True,
                         rule="fabric/metric-dax",
                         reason="excluded before export: it has no expression, and published metrics require DAX")
                continue
            text, why = translated.get(placed, (None, broken or "the vendored converter can't translate it to DAX"))
            if text is None:
                dialects = ", ".join(str(e.get("dialect")) for e in entries)
                rows.add("measure", mid, "unsupported", key=key, path=_path(mpath, "expression"), excluded=True,
                         rule="fabric/metric-dax",
                         reason=f"excluded before export: it has only {dialects}, which couldn't be translated to DAX "
                                f"({why}), and published metrics require DAX. Add a DAX expression to include it.")
                continue
            dax = {"dialect": "DAX", "expression": text}
            cls = "unverified"
            notes.append("translated from SQL to DAX by the vendored converter; the DAX is added beside the SQL, "
                         "and the two aren't compared")
        others = [e for e in entries if e is not dax and e.get("dialect") != "DAX"]
        if home_ds is not None:  # a dataset's metric moves to the root, so its SQL names its fields by dataset
            for e in others:
                if e.get("dialect") in _SQL_DIALECTS:
                    try:
                        e["expression"] = mo._qualified(str(e.get("expression") or ""), home_ds,
                                                        _SQL_DIALECTS[e["dialect"]])
                    except sqlglot.errors.SqlglotError:  # ParseError or TokenError: kept as written; only DAX publishes
                        notes.append(f"its {e.get('dialect')} expression is kept as written")
            notes.append(f"written as a view-level metric (Fabric's supported YAML has no dataset metrics); its "
                         f"dataset {mo._q(home_ds)} is its home in the file split")
        if others:
            notes.append("its other dialects are kept: Fabric recognizes them but publishes only DAX")
        notes += _strip_extras(rows, m, _METRIC_KEYS, mid, mpath)
        metric: dict[str, Any] = {"name": m["name"]}
        if "description" in m:
            metric["description"] = m["description"]
        metric["expression"] = {"dialects": [{"dialect": "DAX", "expression": dax.get("expression")}] + others}
        out_metrics.append((metric, home if home in kept else None))
        rows.add("measure", mid, cls, key=key, path=mpath, notes=notes)

    relationships = []
    for ri, r in enumerate(doc.get("relationships") or []):
        rpath = _path("$", "relationships", ri)
        if not isinstance(r, dict):
            rows.add("relationship", f"relationship #{ri + 1}", "unsupported", path=rpath, excluded=True,
                     rule="semantic-view/supported-yaml", reason="excluded: it isn't an object")
            continue
        label = r.get("name") if isinstance(r.get("name"), str) and r.get("name") else "<unnamed>"
        rid = f"relationship {mo._q(label)}"
        fc, tc = r.get("from_columns"), r.get("to_columns")
        key = ("relationship", mo.ends_key(r.get("from"), fc, r.get("to"), tc))
        if r.get("from") in query_sources or r.get("to") in query_sources:
            rows.add("relationship", rid, "unsupported", key=key, path=rpath, excluded=True,
                     rule="semantic-view/query-source",
                     reason="excluded: it joins a dataset whose source is a query, which Fabric doesn't support")
            continue
        if not (isinstance(fc, list) and isinstance(tc, list) and len(fc) == 1 and len(tc) == 1):
            shown = [len(c) if isinstance(c, list) else "?" for c in (fc, tc)]
            rows.add("relationship", rid, "unsupported", key=key, path=rpath, excluded=True,
                     rule="fabric/relationship-single-column",
                     reason=f"excluded: it joins {shown[0]} column(s) to {shown[1]}, and Fabric relationships are "
                            "single-column only. Join on one key column built from the others.")
            continue
        notes = _strip_extras(rows, r, _RELATIONSHIP_KEYS, rid, rpath)
        relationships.append({k: r[k] for k in _RELATIONSHIP_KEYS if k in r})
        rows.add("relationship", rid, "fully_preserved", key=key, path=rpath, notes=notes)

    files, split_note = _files(folder, root, datasets, relationships, out_metrics, split)
    if len(files) > op.FABRIC_MAX_FILES:  # never: _files merges first; checked so no caller ever gets more
        raise RuntimeError(f"The view came to {len(files)} files; Fabric takes at most {op.FABRIC_MAX_FILES}")
    validation = op.validate_files(files, "fabric-semantic-view")
    by_class: dict[str, list[str]] = {c: [] for c in mo.CLASSES}
    for row in rows.rows:
        by_class[row["class"]].append(row["object"])
    rows.warnings.sort(key=lambda w: w["severity"] != "security")  # security roles first, in input order
    report = {
        "kind": KIND, "profile": "fabric-semantic-view", "version": op.V02, "preview": True,
        "view": folder, "source_kind": kind, "split": split_note,
        "files": sorted(files), "validation": validation, "valid": validation["valid"],
        "objects": rows.rows, "counts": {c: len(v) for c, v in by_class.items()}, "by_class": by_class,
        "omitted": [{"object": r["object"], "construct": r["stripped"], "rule": r["rule"], "source": r["source"],
                     "path": r["path"]} for r in rows.rows if r["stripped"]],
        "excluded": [{"object": r["object"], "kind": r["kind"], "reason": r["reason"], "rule": r["rule"],
                      "path": r["path"]} for r in rows.rows if r["excluded"]],
        "flagged": {"roles": [r["object"] for r in rows.rows if r["kind"] == "role"],
                    "calculation_groups": [r["object"] for r in rows.rows if r["kind"] == "calculation_group"]},
        "warnings": rows.warnings, "conversion": conversion, "publish": PUBLISH,
        "rules": {k: dict(v) for k, v in STRIPPED.items()},
    }
    return {"files": files, "report": report}


def _dump(doc: dict[str, Any]) -> str:
    text: str = mo.yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
    return text


def _files(folder: str, root: dict[str, Any], datasets: list[dict[str, Any]], relationships: list[dict[str, Any]],
           metrics: list[tuple[dict[str, Any], str | None]], split: str) -> tuple[dict[str, str], dict[str, Any]]:
    head = {"version": root["version"], "name": root["name"]}
    if split == "single" or not datasets:
        doc = dict(root)
        if datasets:
            doc["datasets"] = datasets
        if relationships:
            doc["relationships"] = relationships
        if metrics:
            doc["metrics"] = [m for m, _ in metrics]
        used = "single" if split == "single" else "single (no datasets to split by)"
        return {f"{folder}.yaml": _dump(doc)}, {"requested": split, "used": used, "message": ""}
    model = dict(root)
    if relationships:
        model["relationships"] = relationships
    unhomed = [m for m, home in metrics if home is None]
    if unhomed:
        model["metrics"] = unhomed
    slots = op.FABRIC_MAX_FILES - 1
    groups = [[d] for d in datasets]
    message = ""
    if len(groups) > slots:  # merge: never more files than Fabric takes
        size = -(-len(datasets) // slots)
        groups = [datasets[i:i + size] for i in range(0, len(datasets), size)]
        message = (f"{len(datasets)} datasets don't fit one file each in Fabric's {op.FABRIC_MAX_FILES} files, so "
                   f"they're merged, up to {size} datasets a file, into {len(groups)} dataset files")
    files = {MODEL_FILE: _dump(model)}
    taken: set[str] = {MODEL_FILE.casefold().removesuffix(".yaml")}
    width = 2
    for n, group in enumerate(groups, 1):
        names = {d["name"] for d in group}
        doc = dict(head)
        doc["datasets"] = group
        homed = [m for m, home in metrics if home in names]
        if homed:
            doc["metrics"] = homed
        label = tmdl.file_name(f"{n:0{width}d}_{group[0]['name']}", taken)
        files[f"{label}.yaml"] = _dump(doc)
    used = "by_dataset (merged)" if message else "by_dataset"
    return files, {"requested": split, "used": used, "message": message}


def merged(files: Mapping[str, str]) -> dict[str, Any]:
    """A view's ``.yaml`` files merged into one document as publishing merges them (datasets, relationships and
    metrics from every file, in sorted file order; other top-level keys from the first file that has them), for a
    comparison such as ``semantic_parity`` with ``kind="semantic_view"``. Not a check: ``validate_files`` is."""
    out: dict[str, Any] = {}
    for name in sorted(files):
        if not name.lower().endswith(".yaml"):
            continue
        doc = mo.load_text(files[name])
        if not isinstance(doc, dict):
            raise ValueError(f"{name} isn't one object with version and name")
        for key, value in doc.items():
            if key in ("datasets", "relationships", "metrics") and isinstance(value, list):
                out.setdefault(key, []).extend(value)
            else:
                out.setdefault(key, value)
    if not out:
        raise ValueError("A semantic view needs at least one .yaml file")
    return out
