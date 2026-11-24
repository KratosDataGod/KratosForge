"""Ossie version profiles (OSSIE-003b): which Ossie a file is, and the rules each target adds.

Four profiles, one per target (semantic-engineering plan D.3):

- ``ossie-0.3``: Ossie 0.3.0.dev0, checked by the pinned schema at ``aa500d3``.
- ``ossie-0.2``: Ossie 0.2.0.dev0, checked by the pinned schema at ``21d14d7a``. The vendored converter reads and
  writes this version, and it is the only one Import converts.
- ``fabric-semantic-view``: 0.2.0.dev0 plus what Microsoft documents for Fabric Semantic Views (Preview): DAX
  metrics, single-column relationships, at most 20 ``.yaml`` files, 16 MB of YAML and 30 MB of generated TMDL.
  The file-set limits are checked by ``validate_files``.
- ``snowflake-ossie``: Snowflake's Ossie 0.1.1 in the ``semantic_model`` wrapper. No 0.1.1 schema is pinned
  (none could be found), so only the documented shape is checked and every result is Unverified, never valid.

Each rule in ``RULES`` names its source page and the page date the phase 0 research recorded (2026-11-18), and
its basis: "pinned schema", "documented" or "derived". ``detect_profile`` replaces the old strict version check
(risk R1): a known profile, or a refusal that names the version. Everything here reads values only: nothing is
fetched and nothing is executed. The results inform and never block (``ossie_validate.EFFECT``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from . import model_ossie, ossie_validate as ov

_RAW = "https://raw.githubusercontent.com/apache/ossie/{commit}/core-spec/ossie-schema.json"
_LEARN = "https://learn.microsoft.com/en-us/fabric/fundamentals/"
FABRIC_YAML_REFERENCE = _LEARN + "semantic-views-yaml-reference"
FABRIC_YAML_REFERENCE_DATE = "ms.date 2026-10-20, updated 2026-11-17"
FABRIC_CREATE = _LEARN + "semantic-views-create"
SNOWFLAKE_IMPORT = ("https://docs.snowflake.com/en/sql-reference/stored-procedures/"
                    "system_create_semantic_view_from_ossie_yaml")
SNOWFLAKE_EXPORT = "https://docs.snowflake.com/en/sql-reference/functions/system_read_ossie_yaml_from_semantic_view"
NO_PAGE_DATE = "no page date"  # Snowflake reference pages show none (research, "Page date")

V02, V03, V011 = "0.2.0.dev0", "0.3.0.dev0", "0.1.1"
# Fabric's limits. The page says "MB" without saying which; 1 MB is read as 1,000,000 bytes, the stricter reading
# (derived), so a set this check passes is within the limit either way.
MB = 1_000_000
FABRIC_MAX_FILES = 20
FABRIC_MAX_YAML_BYTES = 16 * MB
FABRIC_MAX_TMDL_BYTES = 30 * MB
_SF_DIALECTS = ("SNOWFLAKE", "ANSI_SQL")

PROFILES: dict[str, dict[str, Any]] = {
    "ossie-0.3": {"version": V03, "root": "flat", "schema": V03, "extensions": "nonportable_extensions",
                  "convertible": False, "unverified": False, "label": "Ossie 0.3.0.dev0"},
    "ossie-0.2": {"version": V02, "root": "flat", "schema": V02, "extensions": "custom_extensions",
                  "convertible": True, "unverified": False, "label": "Ossie 0.2.0.dev0"},
    "fabric-semantic-view": {"version": V02, "root": "flat", "schema": V02, "extensions": None,
                             "convertible": True, "unverified": False,
                             "label": "Ossie 0.2.0.dev0 for Fabric Semantic Views"},
    "snowflake-ossie": {"version": V011, "root": "wrapper", "schema": None, "extensions": "custom_extensions",
                        "convertible": False, "unverified": True,
                        "label": "Snowflake's Ossie 0.1.1 (the semantic_model wrapper)"},
}


def _rule(rule_id: str, profile: str, source: str, page_date: str, basis: str, scope: str, severity: str,
          says: str, derived: str = "") -> dict[str, str]:
    """One rule. ``derived`` names the part of a documented rule that KratosForge reads into it because the page
    doesn't say (empty when nothing is)."""
    return {"id": rule_id, "profile": profile, "source": source, "page_date": page_date, "basis": basis,
            "scope": scope, "severity": severity, "says": says, "derived": derived}


_CASE = ("Matching the .yaml extension in any case (.YAML too) is derived: the page names .yaml and doesn't say "
         "whether the match is case-sensitive.")
_MB = "1 MB is read as 1,000,000 bytes, the stricter reading (derived: the page says MB and doesn't say which)."


RULES: tuple[dict[str, str], ...] = (
    _rule("ossie-0.3/schema", "ossie-0.3", _RAW.format(commit=ov.SCHEMAS[V03]["commit"]),
          "file version 0.3.0.dev0, commit of 2026-11-15", "pinned schema", "document", "error",
          "The document matches the Apache Ossie 0.3.0.dev0 JSON Schema at aa500d3."),
    _rule("ossie-0.2/schema", "ossie-0.2", _RAW.format(commit=ov.SCHEMAS[V02]["commit"]),
          "file version 0.2.0.dev0, commit of 2026-11-09", "pinned schema", "document", "error",
          "The document matches the Apache Ossie 0.2.0.dev0 JSON Schema at 21d14d7a."),
    _rule("fabric/version", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "document", "error", "Set version to 0.2.0.dev0 in every YAML file."),
    _rule("fabric/schema", "fabric-semantic-view", _RAW.format(commit=ov.SCHEMAS[V02]["commit"]),
          "file version 0.2.0.dev0, commit of 2026-11-09", "pinned schema", "document", "error",
          "The document matches the Apache Ossie 0.2.0.dev0 JSON Schema at 21d14d7a."),
    _rule("fabric/metric-dax", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "document", "error",
          "Published metrics require DAX: only the DAX dialect is published; other dialects are recognized but "
          "not published."),
    _rule("fabric/relationship-single-column", "fabric-semantic-view", FABRIC_YAML_REFERENCE,
          FABRIC_YAML_REFERENCE_DATE, "documented", "document", "error", "Relationships are single-column only."),
    _rule("fabric/extensions-omitted", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "document", "warning",
          "All custom_extensions, including Microsoft's own, are omitted with warnings."),
    _rule("fabric/omitted", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "document", "warning",
          "Dataset fields, primary_key, unique_keys and all ai_context are omitted with warnings."),
    _rule("fabric/file-count", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          f"At most {FABRIC_MAX_FILES} .yaml files in a view, including subfolders and shortcut content. "
          + _CASE, _CASE),
    _rule("fabric/yml-ignored", "fabric-semantic-view", FABRIC_CREATE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "warning",
          "Files with other extensions, including .yml, are ignored (Fabric gives no warning; this check does) and "
          "don't count toward the limits. " + _CASE, _CASE),
    _rule("fabric/yaml-size", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "At most 16 MB of YAML combined: the total of the .yaml files, not a per-file allowance. " + _MB, _MB),
    _rule("fabric/tmdl-size", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "At most 30 MB of generated TMDL; checked only when the TMDL is given. " + _MB, _MB),
    _rule("fabric/fragment", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "A view can be split across files, and each YAML file requires version and name. A file may leave out "
          "datasets, but datasets it declares are a nonempty array. Empty or comment-only files aren't valid."),
    _rule("fabric/merged", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "Publishing merges the files into one view (file order doesn't matter), so the merged view is checked "
          "by this profile's document rules, and each problem is shown in the file it came from."),
    _rule("fabric/duplicate-name", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error", "Resolve duplicate names and conflicting definitions across files."),
    _rule("fabric/description", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "Define the top-level description in one file, or repeat the same text; different ones fail publishing."),
    _rule("fabric/datasets-inferred", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "warning",
          "Metrics or relationships can be published without datasets, which are then inferred; relationship "
          "table references then resolve only in dbo."),
    _rule("fabric/conflict", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "\"Resolve duplicate names and conflicting definitions across files.\" Two files that give different "
          "values for the same top-level key (other than description, which has its own rule, and version and "
          "name) conflict; the merged check uses the first file's, in sorted order."),
    _rule("snowflake/version", "snowflake-ossie", SNOWFLAKE_IMPORT, NO_PAGE_DATE, "documented", "document",
          "error", "Only Ossie 0.1.1 is supported."),
    _rule("snowflake/wrapper", "snowflake-ossie", SNOWFLAKE_EXPORT, NO_PAGE_DATE, "documented", "document",
          "error", "Ossie 0.1.1 uses the document wrapper format: version and semantic_model at the top."),
    _rule("snowflake/dialect", "snowflake-ossie", SNOWFLAKE_IMPORT, NO_PAGE_DATE, "derived", "document",
          "warning",
          "Fields and metrics without a SNOWFLAKE or ANSI_SQL expression are silently skipped. Documented; where "
          "fields and metrics sit inside the wrapper is assumed from 0.2.0.dev0's names, so the rule is derived.",
          "where fields and metrics sit inside the 0.1.1 wrapper"),
)
BASES = ("pinned schema", "documented", "derived")


def rules(profile: str | None = None, scope: str | None = None) -> list[dict[str, str]]:
    """The rules of ``profile`` (all when None), optionally only those for one ``scope`` ("document" or "files")."""
    if profile is not None and profile not in PROFILES:
        raise ValueError(f"There's no Ossie profile {profile!r}; the profiles are {', '.join(PROFILES)}")
    return [dict(r) for r in RULES if (profile is None or r["profile"] == profile)
            and (scope is None or r["scope"] == scope)]


class UnknownProfile(ValueError):
    """A document whose version and layout match no profile. The message names the version; ``parts`` and
    ``rule`` say where a diagnostic for it goes."""

    def __init__(self, message: str, parts: tuple[Any, ...] = ("version",), rule: str = "version") -> None:
        super().__init__(message)
        self.parts, self.rule = parts, rule


_KNOWN = (f"It reads Ossie {V02} (ossie-0.2), {V03} (ossie-0.3) and Snowflake's {V011} with the semantic_model "
          "wrapper (snowflake-ossie).")


def detect_profile(doc: Any) -> str:
    """``ossie-0.2`` for a flat 0.2.0.dev0 document, ``ossie-0.3`` for a flat 0.3.0.dev0 one and
    ``snowflake-ossie`` for a 0.1.1 document in the ``semantic_model`` wrapper. Anything else raises
    UnknownProfile naming its version. ``fabric-semantic-view`` is never detected: it is the same version as
    ``ossie-0.2`` and is chosen by the caller."""
    if not isinstance(doc, dict):
        raise UnknownProfile("This isn't an Ossie semantic model: it should be one object with version, name and "
                             "datasets", (), "type")
    raw = doc.get("version")
    version = raw if isinstance(raw, str) else None
    shown = ov.short(raw) if raw is not None and version is None else (version or "(none)")
    wrapped = "semantic_model" in doc
    if version == V011:
        if wrapped:
            return "snowflake-ossie"
        raise UnknownProfile(f"This file is Ossie version {V011} but has no semantic_model wrapper, which {V011} "
                             f"files (Snowflake's) have. {_KNOWN}")
    if version in (V02, V03):
        if not wrapped:
            return "ossie-0.2" if version == V02 else "ossie-0.3"
        raise UnknownProfile("This file uses the older Ossie layout with a semantic_model list. "
                             f"Ossie {version} puts one model's name and datasets at the top of the file.",
                             ("semantic_model",), "profile")
    if wrapped:
        raise UnknownProfile("This file uses the older Ossie layout with a semantic_model list, and its version is "
                             f"{shown}, not {V011}. {_KNOWN}")
    raise UnknownProfile(f"This file is Ossie version {shown}, which KratosForge doesn't know. {_KNOWN}")


# ---- the rules beyond the schema, each adding diagnostics (errors) or warnings

def _dialects(expression: Any) -> list[str]:
    if not isinstance(expression, dict):
        return []
    entries = expression.get("dialects")
    return [str(e.get("dialect")) for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _dicts(value: Any) -> list[tuple[int, dict[str, Any]]]:
    return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)] if isinstance(value, list) else []


def _metrics(model: dict[str, Any], base: tuple[Any, ...] = ()) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """The model's metrics and its datasets' metrics, with their paths."""
    found = [((*base, "metrics", i), m) for i, m in _dicts(model.get("metrics"))]
    for di, d in _dicts(model.get("datasets")):
        found += [((*base, "datasets", di, "metrics", i), m) for i, m in _dicts(d.get("metrics"))]
    return found


def _summary(rule: str, places: list[tuple[Any, ...]], what: str, fix: str) -> list[dict[str, Any]]:
    """One warning for every place a rule found, so a large model gives one line, not thousands."""
    if not places:
        return []
    others = len(places) - 1
    where = f" (and in {others} other place{'s' if others != 1 else ''})" if others else ""
    return [ov._diag(places[0], rule, str(len(places)), f"{what}{where}", fix)]


def _fabric(doc: dict[str, Any], errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    for parts, m in _metrics(doc):
        if "DAX" not in _dialects(m.get("expression")):
            errors.append(ov._diag((*parts, "expression"), "fabric/metric-dax", ov.short(m.get("name")),
                                   "has no DAX expression, so Fabric won't publish this metric: published metrics "
                                   "require DAX",
                                   "Add a dialects entry with dialect: DAX and the measure's DAX expression."))
    for i, r in _dicts(doc.get("relationships")):
        for side in ("from_columns", "to_columns"):
            cols = r.get(side)
            if isinstance(cols, list) and len(cols) != 1:
                errors.append(ov._diag(("relationships", i, side), "fabric/relationship-single-column",
                                       ov.short(cols), f"has {len(cols)} columns; Fabric relationships are "
                                       "single-column only",
                                       "Join on one column, for example a key column built from the others."))
    extensions: list[tuple[Any, ...]] = []
    omitted: dict[str, list[tuple[Any, ...]]] = {"fields": [], "primary_key": [], "unique_keys": [], "ai_context": []}
    stack: list[tuple[tuple[Any, ...], Any]] = [((), doc)]
    while stack:  # no recursion: ossie_validate._shape already bounded the depth
        parts, value = stack.pop()
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "custom_extensions":
                    extensions.append((*parts, k))
                elif k == "ai_context":
                    omitted[k].append((*parts, k))
                elif len(parts) == 2 and parts[0] == "datasets" and k in omitted:
                    omitted[k].append((*parts, k))
                if isinstance(v, (dict, list)):
                    stack.append(((*parts, k), v))
        elif isinstance(value, list):
            stack.extend(((*parts, i), v) for i, v in enumerate(value) if isinstance(v, (dict, list)))
    sort = ov._sort_key
    extensions.sort(key=lambda p: sort({"_parts": p, "rule": "", "message": ""}))
    warnings += _summary("fabric/extensions-omitted", extensions,
                         "custom_extensions is left out by Fabric, so this vendor data is dropped",
                         "Nothing to change for Fabric; keep the file with its extensions if another tool needs them.")
    for key, places in omitted.items():
        places.sort(key=lambda p: sort({"_parts": p, "rule": "", "message": ""}))
        warnings += _summary("fabric/omitted", places, f"{key} is left out by Fabric",
                             "Nothing to change for Fabric; it publishes the view without it.")


def _fabric_version(doc: dict[str, Any]) -> list[dict[str, Any]]:
    if doc.get("version") == V02:
        return []
    return [ov._diag(("version",), "fabric/version", ov.short(doc.get("version")),
                     f"should be {V02}: Fabric Semantic Views take Ossie {V02} only",
                     f"Set version: {V02} (and write the file as Ossie {V02}).")]


def _snowflake(doc: dict[str, Any], errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    if doc.get("version") != V011:
        errors.append(ov._diag(("version",), "snowflake/version", ov.short(doc.get("version")),
                               f"should be {V011}: Snowflake's Ossie functions support {V011} only",
                               f"Set version: \"{V011}\" and use the semantic_model wrapper."))
    wrapper = doc.get("semantic_model")
    if "semantic_model" not in doc:
        errors.append(ov._diag(("semantic_model",), "snowflake/wrapper", "(missing)",
                               "is required: Ossie 0.1.1 uses the document wrapper format",
                               "Put the model under semantic_model, as SYSTEM$READ_OSSIE_YAML_FROM_SEMANTIC_VIEW "
                               "writes it."))
    models = [((), wrapper)] if isinstance(wrapper, dict) else [((i,), m) for i, m in _dicts(wrapper)]
    skipped: list[tuple[Any, ...]] = []
    for at, model in models:
        base = ("semantic_model", *at)
        things = _metrics(model, base)
        for di, d in _dicts(model.get("datasets")):
            things += [((*base, "datasets", di, "fields", i), f) for i, f in _dicts(d.get("fields"))]
        skipped += [(*parts, "expression") for parts, obj in things
                    if not any(x in _SF_DIALECTS for x in _dialects(obj.get("expression")))]
    skipped.sort(key=lambda p: ov._sort_key({"_parts": p, "rule": "", "message": ""}))
    warnings += _summary("snowflake/dialect", skipped,
                         "has no SNOWFLAKE or ANSI_SQL expression, so Snowflake silently skips this field or metric",
                         "Add a dialects entry with dialect: SNOWFLAKE or ANSI_SQL.")


def check(doc: dict[str, Any], profile: str) -> dict[str, Any]:
    """``doc`` (already parsed, and past ``ossie_validate``'s depth and key checks) checked by ``profile``'s
    document rules: its pinned schema when it has one, then the rules beyond it."""
    p = PROFILES[profile]
    raw = doc.get("version")
    version = raw if isinstance(raw, str) else ""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    total, stopped, checked = 0, False, False
    if profile == "fabric-semantic-view":
        errors += _fabric_version(doc)
    if p["schema"] and not errors:
        errors, total, stopped = ov.schema_errors(doc, ov._validator(p["schema"]), p["schema"])
        checked = True
    extra: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    if profile == "fabric-semantic-view":
        _fabric(doc, extra, warnings)
    elif profile == "snowflake-ossie":
        notes.append(ov._diag(("version",), "unverified", ov.short(raw),
                              f"no pinned schema for {V011}: Snowflake's {V011} wrapper is checked for its "
                              "documented shape only, so the result is Unverified",
                              "Nothing to change for this; Snowflake checks the file itself when "
                              "SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML imports it."))
        _snowflake(doc, extra, warnings)
    total = (total if checked else len(errors)) + len(extra)
    return ov._result(p["schema"] or version, errors + extra, checked, total, stopped, profile=profile,
                      warnings=warnings, notes=notes, unverified=p["unverified"],
                      rules=[r["id"] for r in RULES if r["profile"] == profile and r["scope"] == "document"])


# ---- a set of files (Fabric's limits apply to a view's folder, not to one file)

def _size(name: str, value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    raise TypeError(f"{name} should be text or bytes")


_LISTS = ("datasets", "relationships", "metrics")  # what publishing merges from every file
_LISTED = re.compile(r"\$\.(datasets|relationships|metrics)\[(\d+)\](.*)", re.S)
_ROOT_KEY = re.compile(r"\$\.([A-Za-z_][A-Za-z0-9_]*)(.*)", re.S)


def _fragment(name: str, value: str | bytes) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """One view file read and checked as a fragment (fabric/fragment, fabric/version): what it holds, or None
    when it can't be merged, and its problems."""
    try:
        doc = model_ossie.load_text(value.decode("utf-8") if isinstance(value, bytes) else value)
    except (ValueError, TypeError) as exc:  # narrow: load_text's plain refusals (and bad UTF-8), shown as the diagnostic
        return None, [ov._diag((), "parse", "", str(exc), "Open the file and fix what the message names.")]
    if doc is None:
        return None, [ov._diag((), "fabric/fragment", "", "is empty or holds only comments, which isn't a valid "
                               "definition", "Write version and name and what this file adds, or delete the file.")]
    if not isinstance(doc, dict):
        return None, [ov._diag((), "fabric/fragment", ov.short(doc), "should be an object with version and name",
                               "Write version: 0.2.0.dev0, name and what this file adds at the top.")]
    problems = [ov._diag((key,), "fabric/fragment", "(missing)", "is required in every file of a view",
                         f"Add {key}." if key == "name" else f"Add version: {V02}.")
                for key in ("version", "name") if doc.get(key) in (None, "")]
    if "datasets" in doc and (not isinstance(doc["datasets"], list) or not doc["datasets"]):
        problems.append(ov._diag(("datasets",), "fabric/fragment", ov.short(doc["datasets"]),
                                 "should be a nonempty list when a file declares it",
                                 "Add the datasets, or leave datasets out of this file."))
    if doc.get("version") not in (None, "", V02):
        problems += _fabric_version(doc)
    return doc, problems


def _merge(docs: list[tuple[str, dict[str, Any]]], per_file: dict[str, list[dict[str, Any]]]) -> tuple[
        dict[str, Any], dict[str, list[tuple[str, int]]], dict[str, str]]:
    """The files merged into one view as publishing does: datasets, relationships and metrics from every file,
    one description (fabric/description), names that are unique (fabric/duplicate-name). Also where each merged
    item and each other top-level key came from, so a problem is shown in its own file.

    ``docs`` is in sorted file order. The view's ``name`` is the first file's, and files may give different
    names: the YAML reference says ``name`` is "Required, but the value isn't used. The view folder name is used
    as the view and semantic model name." Every other top-level key (``ai_context``, ``custom_extensions``, a key
    the schema doesn't know) is taken from the first file that has it, and a different value in a later file is
    an error (fabric/conflict): the reference says "Resolve duplicate names and conflicting definitions across
    files." Values are never merged."""
    # name: required in each file (fabric/fragment) but unused by Fabric, so different names aren't a problem
    merged: dict[str, Any] = {"version": V02, "name": docs[0][1].get("name")}
    origins: dict[str, list[tuple[str, int]]] = {k: [] for k in _LISTS}
    owner = {"name": docs[0][0], "version": docs[0][0]}
    for name, doc in docs:
        for key, value in doc.items():
            if key in ("version", "name"):
                continue
            if key in _LISTS:
                if not isinstance(value, list):  # never merged; datasets already has its fabric/fragment row
                    if key != "datasets":
                        per_file[name].append(ov._diag((key,), "type", ov.short(value), "should be a list",
                                                       "Write it as a list."))
                    continue
                if not value:  # an empty list is the file's own problem (fabric/fragment), not the view's
                    continue
                merged.setdefault(key, []).extend(value)
                origins[key] += [(name, i) for i in range(len(value))]
            elif key == "description" and key in merged and value != merged[key]:
                per_file[name].append(ov._diag(("description",), "fabric/description", ov.short(value),
                                               f"differs from the description in {owner[key]}, so publishing fails",
                                               "Define the description in one file, or repeat the same text."))
            elif key not in merged:
                merged[key], owner[key] = value, name
            elif key != "description" and not _same(value, merged[key]):  # "Resolve ... conflicting definitions"
                per_file[name].append(ov._diag((key,), "fabric/conflict", ov.short(value),
                                               f"conflicts with {key} in {owner[key]}; Fabric asks for conflicting "
                                               f"definitions across files to be resolved (the check used "
                                               f"{owner[key]}'s)", f"Give {key} in one file only, or the same value "
                                               "in each."))
    for key in _LISTS:
        seen: dict[Any, str] = {}
        for (name, i), item in zip(origins[key], merged.get(key) or [], strict=True):
            label = item.get("name") if isinstance(item, dict) else None
            if not isinstance(label, str):
                continue
            if label in seen:
                per_file[name].append(ov._diag((key, i, "name"), "fabric/duplicate-name", ov.short(label),
                                               f"is also the name of one of the {key} in {seen[label]}",
                                               "Give it another name, or keep one definition."))
            else:
                seen[label] = name
    return merged, origins, owner


def _same(a: Any, b: Any) -> bool:
    try:
        return bool(a == b)
    except RecursionError:  # nested past what == can compare: the depth check refuses it afterwards anyway
        return False


def _home(d: dict[str, Any], origins: dict[str, list[tuple[str, int]]], owner: dict[str, str]) -> tuple[str | None,
                                                                                                        dict[str, Any]]:
    """The file a merged view's problem came from (None for the view as a whole), with its path in that file."""
    m = _LISTED.fullmatch(d["path"])
    if m and int(m[2]) < len(origins[m[1]]):
        name, i = origins[m[1]][int(m[2])]
        return name, d | {"path": f"$.{m[1]}[{i}]{m[3]}"}
    r = _ROOT_KEY.fullmatch(d["path"])
    return (owner.get(r[1]) if r else None), d


def validate_files(files: Mapping[str, str | bytes], profile: str = "fabric-semantic-view",
                   tmdl: Mapping[str, str | bytes] | None = None) -> dict[str, Any]:
    """A view's files (name -> YAML text or bytes) checked as Fabric reads them. Publishing merges every
    ``.yaml`` file into one view, and a file may hold only part of it (fabric/fragment), so each file is checked
    as a fragment, and the merged view by the profile's document rules (fabric/merged), each problem shown in the
    file it came from (``files``; ``diagnostics`` holds the set's own). Files without the ``.yaml`` extension,
    ``.yml`` among them, are ignored as Fabric ignores them, with one warning. ``tmdl`` (path -> text) is the TMDL
    generated for the view; its combined size is checked only when it is given.

    When the YAML is over the combined limit nothing is read (``files_checked`` false), and past the file-count
    limit only the first FABRIC_MAX_FILES are, so the cost stays bounded. ``valid`` is true only when no rule
    failed and every file was checked."""
    if profile != "fabric-semantic-view":
        raise ValueError(f"Only fabric-semantic-view has rules for a set of files; {profile!r} checks one document "
                         "at a time (ossie_validate.validate)")
    names = sorted(files)
    yaml_names = [n for n in names if n.lower().endswith(".yaml")]  # any case: derived (_CASE)
    ignored = [n for n in names if not n.lower().endswith(".yaml")]
    yaml_bytes = sum(_size(n, files[n]) for n in yaml_names)
    tmdl_bytes = None if tmdl is None else sum(_size(n, v) for n, v in tmdl.items())
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not yaml_names:
        errors.append(ov._diag((), "fabric/fragment", "0", "has no .yaml files, so the view fails to publish",
                               "Add the view's .yaml files."))
    if len(yaml_names) > FABRIC_MAX_FILES:
        errors.append(ov._diag((), "fabric/file-count", str(len(yaml_names)),
                               f"has {len(yaml_names)} .yaml files; a Fabric semantic view takes at most "
                               f"{FABRIC_MAX_FILES}", "Merge files, or split the view into more than one."))
    if yaml_bytes > FABRIC_MAX_YAML_BYTES:
        errors.append(ov._diag((), "fabric/yaml-size", str(yaml_bytes),
                               f"has {yaml_bytes:,} bytes of YAML; Fabric takes at most 16 MB combined",
                               "Take out what the view doesn't need, or split it into more than one."))
    if tmdl_bytes is not None and tmdl_bytes > FABRIC_MAX_TMDL_BYTES:
        errors.append(ov._diag((), "fabric/tmdl-size", str(tmdl_bytes),
                               f"generates {tmdl_bytes:,} bytes of TMDL; Fabric takes at most 30 MB",
                               "Make the view smaller, or split it into more than one."))
    if ignored:
        listed = ", ".join(ignored[:5]) + (", …" if len(ignored) > 5 else "")
        warnings.append(ov._diag((), "fabric/yml-ignored", str(len(ignored)),
                                 f"Fabric ignores files without the .yaml extension, so these are left out: "
                                 f"{ov._short_text(listed)}", "Rename them to .yaml if they belong to the view."))
    checked = yaml_bytes <= FABRIC_MAX_YAML_BYTES
    # past the file-count limit only the first FABRIC_MAX_FILES are checked one by one, so many tiny files
    # can't cost a schema check each; the set has already failed fabric/file-count
    to_check = yaml_names[:FABRIC_MAX_FILES] if checked else []
    if checked and len(yaml_names) > FABRIC_MAX_FILES:
        warnings.append(ov._diag((), "fabric/not-checked", str(len(yaml_names) - FABRIC_MAX_FILES),
                                 f"only the first {FABRIC_MAX_FILES} .yaml files were checked one by one; "
                                 f"{len(yaml_names) - FABRIC_MAX_FILES} more weren't",
                                 "Bring the view to 20 files or fewer, then check it again."))
    per_file: dict[str, list[dict[str, Any]]] = {n: [] for n in to_check}
    per_warn: dict[str, list[dict[str, Any]]] = {n: [] for n in to_check}
    docs = []
    unread = []
    for n in to_check:
        doc, problems = _fragment(n, files[n])
        per_file[n] += problems
        if doc is not None:
            docs.append((n, doc))
        elif any(d["rule"] == "parse" for d in problems):
            unread.append(n)
    merge_notes = []
    if unread:
        listed = ", ".join(unread[:5]) + (", …" if len(unread) > 5 else "")
        merge_notes.append(ov._diag((), "fabric/merged", str(len(unread)),
                                    f"{len(unread)} file(s) couldn't be read and aren't in the merged check; "
                                    f"references to them may show as unresolved: {ov._short_text(listed)}",
                                    "Fix those files (each says why), then check again."))
        merge_notes[-1].pop("_parts", None)
    merged_result: dict[str, Any] | None = None
    if docs:
        merged, origins, owner = _merge(docs, per_file)
        merged_result = ov.validate(merged, profile)
        found = merged_result["diagnostics"]
        if "datasets" not in merged:  # inferred by Fabric, or a file's empty list (fabric/fragment): not missing
            found = [d for d in found if (d["path"], d["rule"]) != ("$.datasets", "required")]
        if not any("datasets" in doc for _, doc in docs):
            warnings.append(ov._diag(("datasets",), "fabric/datasets-inferred", "(none)",
                                     "no file declares datasets, so Fabric infers them; relationship table "
                                     "references then resolve only in dbo",
                                     "Declare the datasets with schema-qualified sources if they aren't in dbo."))
        for group, target, rest in ((found, per_file, errors), (merged_result["warnings"], per_warn, warnings)):
            for d in group:
                home, moved = _home(d, origins, owner)
                (target[home] if home in target else rest).append(moved)
    for group in (errors, warnings, *per_file.values(), *per_warn.values()):
        for d in group:
            d.pop("_parts", None)
    results = {n: {"valid": not per_file[n], "status": "Invalid" if per_file[n] else "Valid",
                   "diagnostics": sorted(per_file[n], key=lambda d: (d["path"], d["rule"])),
                   "warnings": sorted(per_warn[n], key=lambda d: (d["path"], d["rule"]))} for n in to_check}
    checked = checked and len(yaml_names) <= FABRIC_MAX_FILES
    stopped = bool(merged_result and (merged_result["stopped"] or merged_result["more"]))
    valid = (not errors and checked and not stopped and merged_result is not None
             and all(r["valid"] for r in results.values()))
    status = "Valid" if valid else "Invalid" if errors or results else "Not checked"
    return {"valid": valid, "effect": ov.EFFECT, "profile": profile, "status": status, "diagnostics": errors,
            "warnings": warnings, "files": results, "files_checked": checked,
            "merged": None if merged_result is None else {
                "status": merged_result["status"], "checked": merged_result["checked"],
                "schema": merged_result["schema"], "more": merged_result["more"], "stopped": merged_result["stopped"],
                "notes": merge_notes},
            "counts": {"yaml_files": len(yaml_names), "yaml_bytes": yaml_bytes, "tmdl_bytes": tmdl_bytes,
                       "ignored": ignored},
            "rules": [r["id"] for r in RULES if r["profile"] == profile and r["scope"] == "files"]}
