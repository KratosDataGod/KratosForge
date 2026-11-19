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

from collections.abc import Mapping
from typing import Any

from . import ossie_validate as ov

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
          says: str) -> dict[str, str]:
    return {"id": rule_id, "profile": profile, "source": source, "page_date": page_date, "basis": basis,
            "scope": scope, "severity": severity, "says": says}


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
          "documented", "files", "error", f"At most {FABRIC_MAX_FILES} .yaml files in a view."),
    _rule("fabric/yml-ignored", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "warning", ".yml files are ignored; only .yaml files count."),
    _rule("fabric/yaml-size", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "At most 16 MB of YAML combined (1 MB read as 1,000,000 bytes, the stricter reading)."),
    _rule("fabric/tmdl-size", "fabric-semantic-view", FABRIC_YAML_REFERENCE, FABRIC_YAML_REFERENCE_DATE,
          "documented", "files", "error",
          "At most 30 MB of generated TMDL (1 MB read as 1,000,000 bytes); checked only when the TMDL is given."),
    _rule("snowflake/version", "snowflake-ossie", SNOWFLAKE_IMPORT, NO_PAGE_DATE, "documented", "document",
          "error", "Only Ossie 0.1.1 is supported."),
    _rule("snowflake/wrapper", "snowflake-ossie", SNOWFLAKE_EXPORT, NO_PAGE_DATE, "documented", "document",
          "error", "Ossie 0.1.1 uses the document wrapper format: version and semantic_model at the top."),
    _rule("snowflake/dialect", "snowflake-ossie", SNOWFLAKE_IMPORT, NO_PAGE_DATE, "derived", "document",
          "warning",
          "Fields and metrics without a SNOWFLAKE or ANSI_SQL expression are silently skipped. Documented; where "
          "fields and metrics sit inside the wrapper is assumed from 0.2.0.dev0's names, so the rule is derived."),
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
    if profile == "fabric-semantic-view":
        _fabric(doc, extra, warnings)
    elif profile == "snowflake-ossie":
        extra.append(ov._diag(("version",), "version", ov.short(raw),
                              f"no pinned schema for {ov.short(raw)}: Snowflake's {V011} wrapper is checked for its "
                              "documented shape only, so the result is Unverified",
                              "Nothing to change for this; Snowflake checks the file itself when "
                              "SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSSIE_YAML imports it."))
        _snowflake(doc, extra, warnings)
    total = (total if checked else len(errors)) + len(extra)
    return ov._result(p["schema"] or version, errors + extra, checked, total, stopped, profile=profile,
                      warnings=warnings, unverified=p["unverified"],
                      rules=[r["id"] for r in RULES if r["profile"] == profile and r["scope"] == "document"])


# ---- a set of files (Fabric's limits apply to a view's folder, not to one file)

def _size(name: str, value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    raise TypeError(f"{name} should be text or bytes")


def validate_files(files: Mapping[str, str | bytes], profile: str = "fabric-semantic-view",
                   tmdl: Mapping[str, str | bytes] | None = None) -> dict[str, Any]:
    """A view's files (name -> YAML text or bytes) checked by ``profile``'s file-set rules, and each ``.yaml``
    file by its document rules. ``.yml`` files are ignored, as Fabric ignores them, with one warning. ``tmdl``
    (path -> text) is the TMDL generated for the view; its combined size is checked only when it is given.

    When the YAML is over the combined limit the files aren't checked one by one (``files_checked`` false), so
    the cost stays bounded. ``valid`` is true only when no set rule failed and every file was checked and valid."""
    if profile != "fabric-semantic-view":
        raise ValueError(f"Only fabric-semantic-view has rules for a set of files; {profile!r} checks one document "
                         "at a time (ossie_validate.validate)")
    names = sorted(files)
    yaml_names = [n for n in names if n.lower().endswith(".yaml")]
    ignored = [n for n in names if n.lower().endswith(".yml")]
    yaml_bytes = sum(_size(n, files[n]) for n in yaml_names)
    tmdl_bytes = None if tmdl is None else sum(_size(n, v) for n, v in tmdl.items())
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
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
                                 f"Fabric ignores .yml files, so these are left out: {ov._short_text(listed)}",
                                 "Rename them to .yaml if they belong to the view."))
    checked = yaml_bytes <= FABRIC_MAX_YAML_BYTES
    # past the file-count limit only the first FABRIC_MAX_FILES are checked one by one, so many tiny files
    # can't cost a schema check each; the set has already failed fabric/file-count
    to_check = yaml_names[:FABRIC_MAX_FILES]
    if checked and len(yaml_names) > FABRIC_MAX_FILES:
        warnings.append(ov._diag((), "fabric/not-checked", str(len(yaml_names) - FABRIC_MAX_FILES),
                                 f"only the first {FABRIC_MAX_FILES} .yaml files were checked one by one; "
                                 f"{len(yaml_names) - FABRIC_MAX_FILES} more weren't",
                                 "Bring the view to 20 files or fewer, then check it again."))
    results = {n: ov.validate(files[n], profile) for n in to_check} if checked else {}
    for group in (errors, warnings):
        for d in group:
            d.pop("_parts", None)
    checked = checked and len(yaml_names) <= FABRIC_MAX_FILES
    valid = not errors and checked and all(r["valid"] for r in results.values())
    status = "Valid" if valid else "Invalid" if errors or results else "Not checked"
    return {"valid": valid, "effect": ov.EFFECT, "profile": profile, "status": status, "diagnostics": errors,
            "warnings": warnings, "files": results, "files_checked": checked,
            "counts": {"yaml_files": len(yaml_names), "yaml_bytes": yaml_bytes, "tmdl_bytes": tmdl_bytes,
                       "ignored": ignored},
            "rules": [r["id"] for r in RULES if r["profile"] == profile and r["scope"] == "files"]}
