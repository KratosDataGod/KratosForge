"""Fabric Data Factory pipeline definitions: read any supported format, look inside, write it back.

Formats ``load`` and ``loads`` accept (recorded as ``source_format``):

    pipeline_content   pipeline-content.json as Fabric keeps it: {"name"?, "properties": {"activities", ...}}
                       (the same shape as an Azure Data Factory or Synapse pipeline's JSON)
    item_definition    a getDefinition result or create/update body: {"definition": {"parts": [...]}},
                       or the bare {"parts": [...]}, each part base64 ("InlineBase64")
    git_folder         a Fabric Git folder, <name>.DataPipeline/ with pipeline-content.json and .platform

Nothing is lost. The parsed JSON stays the source of truth and ``Pipeline``/``Activity`` are a
view over it, each with the JSON path it came from; keys this module doesn't know, unknown activity
types and every other definition part are kept as they are. ``export`` of a definition nobody
changed gives back the exact text (or files) it was read from; a changed one is serialized from
the JSON, keeping key order.

Reading never runs anything: no expression is evaluated (expressions.py only reads them), no
notebook or script is executed, and nothing calls Fabric.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .expressions import key_path

# Version of this reader. Recorded with every import so a compatibility report (ORCH-002) can tell
# which rules a definition was read under.
DEFINITION_VERSION = 1

CONTENT_PART = "pipeline-content.json"
PLATFORM_PART = ".platform"
FORMATS = ("pipeline_content", "item_definition", "git_folder")
MAX_BYTES = 20 * 1024 * 1024
MAX_DEPTH = 40  # containers inside containers; Fabric's own limits are far lower

# Where control activities keep the activities they contain. A Switch keeps one list per case.
CHILD_LISTS = ("ifTrueActivities", "ifFalseActivities", "activities", "defaultActivities")
CONDITIONS = ("Succeeded", "Failed", "Skipped", "Completed")
# Keys this reader understands at each level; anything else is listed as unrecognized (and kept).
PIPELINE_KEYS = {"activities", "parameters", "variables", "annotations", "description", "concurrency", "folder",
                 "lastModifiedByObjectId", "lastPublishTime", "libraryVariables", "policy", "runDimensions", "logicAppsSettings"}
ACTIVITY_KEYS = {"name", "type", "dependsOn", "typeProperties", "policy", "userProperties", "description", "state",
                 "onInactiveMarkAs", "externalReferences", "linkedService", "linkedServiceName", "inputs", "outputs"}
# Hints at which product wrote a definition. A hint, not proof: the two share most of the format.
_FABRIC_TYPES = {"TridentNotebook", "InvokePipeline", "RefreshDataflow", "PBISemanticModelRefresh", "Office365Outlook",
                 "Teams", "FabricSparkJobDefinition"}
_ADF_TYPES = {"DatabricksNotebook", "DatabricksSparkJar", "DatabricksSparkPython", "SynapseNotebook",
              "SparkJob", "ExecuteSSISPackage", "HDInsightHive", "HDInsightSpark", "AzureMLExecutePipeline",
              "ExecuteDataFlow", "ExecuteWranglingDataflow"}


class DefinitionError(ValueError):
    """A definition that can't be read at all. ``diagnostics`` says where and why."""

    def __init__(self, message: str, code: str, path: str = "$", hint: str = "", activity: str | None = None):
        super().__init__(message)
        self.code, self.path, self.hint, self.activity = code, path, hint, activity

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "severity": "error", "message": str(self), "path": self.path,
                "activity": self.activity, "hint": self.hint}


@dataclass
class Dependency:
    activity: str
    conditions: list[str]
    path: str


@dataclass
class Activity:
    name: str
    type: str
    path: str                      # JSON path inside the pipeline content, e.g. $.properties.activities[2]
    raw: dict[str, Any]            # the activity's own JSON, every key kept
    container: str | None = None   # name of the activity this one is inside, if any
    branch: str | None = None      # which list of the container: activities, ifTrueActivities, cases[0] ...
    depends_on: list[Dependency] = field(default_factory=list)
    children: dict[str, list["Activity"]] = field(default_factory=dict)

    @property
    def type_properties(self) -> dict[str, Any]:
        tp = self.raw.get("typeProperties")
        return tp if isinstance(tp, dict) else {}

    @property
    def inactive(self) -> bool:
        return self.raw.get("state") == "Inactive"

    def walk(self):
        yield self
        for kids in self.children.values():
            for kid in kids:
                yield from kid.walk()


@dataclass
class Pipeline:
    name: str
    content: dict[str, Any]   # the whole pipeline-content.json, the source of truth
    activities: list[Activity]
    problems: list[DefinitionError] = field(default_factory=list)  # structure that couldn't be read

    @property
    def properties(self) -> dict[str, Any]:
        props = self.content.get("properties")
        return props if isinstance(props, dict) else {}

    @property
    def parameters(self) -> dict[str, Any]:
        p = self.properties.get("parameters")
        return p if isinstance(p, dict) else {}

    @property
    def variables(self) -> dict[str, Any]:
        v = self.properties.get("variables")
        return v if isinstance(v, dict) else {}

    def all_activities(self) -> list[Activity]:
        return [a for top in self.activities for a in top.walk()]

    def find(self, name: str) -> Activity | None:
        return next((a for a in self.all_activities() if a.name == name), None)


@dataclass
class ImportedPipeline:
    source_format: str
    pipeline: Pipeline
    # What surrounded the content, so export can rebuild it: the item_definition wrapper (with the
    # content part's payload left in place) or the Git folder's other files.
    wrapper: dict[str, Any] | None = None
    platform: dict[str, Any] | None = None
    original: str | dict[str, str] | None = None  # text (or folder files) as read, for exact export
    duplicate_keys: list[str] = field(default_factory=list)
    _snapshot: str = ""

    @property
    def changed(self) -> bool:
        return _dump(self.pipeline.content) != self._snapshot

    def schema(self) -> dict[str, Any]:
        """What the definition is and which rules read it, for compatibility checks (ORCH-002)."""
        acts = self.pipeline.all_activities()
        types: dict[str, int] = {}
        props: dict[str, set[str]] = {}
        for a in acts:
            types[a.type] = types.get(a.type, 0) + 1
            props.setdefault(a.type, set()).update(a.type_properties)
        meta = (self.platform or {}).get("metadata") if isinstance(self.platform, dict) else None
        config = (self.platform or {}).get("config") if isinstance(self.platform, dict) else None
        dialect, evidence = _dialect(self, acts)
        return {"parser_version": DEFINITION_VERSION, "source_format": self.source_format,
                "dialect": dialect, "dialect_evidence": evidence,
                "platform_schema": (self.platform or {}).get("$schema") if isinstance(self.platform, dict) else None,
                "platform_version": config.get("version") if isinstance(config, dict) else None,
                "item_type": meta.get("type") if isinstance(meta, dict) else None,
                "parts": _part_paths(self),
                "activity_types": dict(sorted(types.items())),
                "type_properties": {t: sorted(keys) for t, keys in sorted(props.items())},
                "unrecognized_keys": _unrecognized(self.pipeline)}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _part_paths(imp: ImportedPipeline) -> list[str]:
    if imp.source_format == "item_definition" and imp.wrapper is not None:
        return [str(p.get("path")) for p in _parts(imp.wrapper) if isinstance(p, dict)]
    if imp.source_format == "git_folder" and isinstance(imp.original, dict):
        return sorted(imp.original)
    return [CONTENT_PART]


def _dialect(imp: ImportedPipeline, acts: list[Activity]) -> tuple[str, list[str]]:
    fabric, adf = [], []
    if imp.platform is not None:
        fabric.append("has a .platform part")
    for a in acts:
        if a.type in _FABRIC_TYPES:
            fabric.append(f"activity type {a.type}")
        if a.type in _ADF_TYPES:
            adf.append(f"activity type {a.type}")
        if "externalReferences" in a.raw:
            fabric.append("activity uses externalReferences (Fabric connections)")
        if "linkedServiceName" in a.raw:
            adf.append("activity uses linkedServiceName")
    if "libraryVariables" in imp.pipeline.properties:
        fabric.append("uses libraryVariables")
    if str(imp.pipeline.content.get("type", "")).startswith("Microsoft.DataFactory") or \
            str(imp.pipeline.content.get("type", "")).startswith("Microsoft.Synapse"):
        adf.append(f"resource type {imp.pipeline.content['type']}")
    fabric, adf = sorted(set(fabric)), sorted(set(adf))
    if fabric and adf:
        return "mixed", fabric + adf
    if fabric:
        return "fabric", fabric
    if adf:
        return "data_factory", adf
    return "unknown", []


def _unrecognized(p: Pipeline, limit: int = 200) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for key in p.properties:
        if key not in PIPELINE_KEYS:
            out.append({"path": f"$.properties{key_path(key)}", "key": key})
    for a in p.all_activities():
        for key in a.raw:
            if key not in ACTIVITY_KEYS:
                out.append({"path": f"{a.path}{key_path(key)}", "key": key, "activity": a.name})
    return out[:limit]


# ---- reading

def _object_pairs(duplicates: list[str]):
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out and k not in duplicates:
                duplicates.append(k)
            out[k] = v
        return out
    return hook


def _parse(text: str, what: str, duplicates: list[str]) -> Any:
    if len(text.encode("utf-8", "surrogatepass")) > MAX_BYTES:
        raise DefinitionError(f"{what} is larger than {MAX_BYTES // (1024 * 1024)} MB.", "too_large")
    try:
        return json.loads(text, object_pairs_hook=_object_pairs(duplicates))
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"{what} isn't valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
                              "invalid_json", hint="Check for a missing comma, quote or bracket near that place.") from None


def _parts(wrapper: dict[str, Any]) -> list[Any]:
    definition = wrapper.get("definition") if isinstance(wrapper.get("definition"), dict) else wrapper
    parts = definition.get("parts")
    return parts if isinstance(parts, list) else []


def _decode(part: dict[str, Any], index: int, prefix: str) -> str:
    kind = part.get("payloadType", "InlineBase64")
    where = f"{prefix}.parts[{index}]"
    if kind != "InlineBase64":
        raise DefinitionError(f"The {part.get('path')} part is {kind!r}; only InlineBase64 parts can be read.",
                              "unsupported_payload_type", f"{where}.payloadType")
    payload = part.get("payload")
    if not isinstance(payload, str):
        raise DefinitionError(f"The {part.get('path')} part has no payload.", "missing_payload", f"{where}.payload")
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8-sig")
    except (binascii.Error, ValueError) as exc:
        raise DefinitionError(f"The {part.get('path')} part's payload isn't base64 UTF-8 text ({exc}).",
                              "invalid_payload", f"{where}.payload") from None


def loads(text: str, name_hint: str = "") -> ImportedPipeline:
    """Read a definition from text: pipeline-content.json or an item definition with parts."""
    duplicates: list[str] = []
    data = _parse(text, "The definition", duplicates)
    if not isinstance(data, dict):
        raise DefinitionError("The definition must be a JSON object.", "not_an_object",
                              hint="Paste pipeline-content.json or the result of getDefinition.")
    if isinstance(data.get("definition"), dict) or isinstance(data.get("parts"), list):
        return _from_parts(data, text, duplicates, name_hint)
    return _finish("pipeline_content", data, name_hint, original=text, duplicates=duplicates)


def _from_parts(wrapper: dict[str, Any], text: str, duplicates: list[str], name_hint: str) -> ImportedPipeline:
    prefix = "$.definition" if isinstance(wrapper.get("definition"), dict) else "$"
    parts = _parts(wrapper)
    content_text = platform = None
    for i, part in enumerate(parts):
        if not isinstance(part, dict):
            raise DefinitionError("Each definition part must be an object.", "invalid_part", f"{prefix}.parts[{i}]")
        path = str(part.get("path", ""))
        if path == CONTENT_PART:
            content_text = _decode(part, i, prefix)
        elif path == PLATFORM_PART:
            try:
                platform = json.loads(_decode(part, i, prefix))
            except json.JSONDecodeError:
                platform = None  # kept as the raw part regardless; only the schema record misses it
    if content_text is None:
        raise DefinitionError(f"The definition has no {CONTENT_PART} part.", "missing_content_part", f"{prefix}.parts",
                              hint="Fabric keeps a pipeline in a part whose path is pipeline-content.json.")
    content = _parse(content_text, CONTENT_PART, duplicates)
    if not isinstance(content, dict):
        raise DefinitionError(f"{CONTENT_PART} must be a JSON object.", "not_an_object")
    name = name_hint or str(wrapper.get("displayName") or "")
    if not name and isinstance(platform, dict) and isinstance(platform.get("metadata"), dict):
        name = str(platform["metadata"].get("displayName") or "")
    return _finish("item_definition", content, name, wrapper=wrapper, platform=platform if isinstance(platform, dict) else None,
                   original=text, duplicates=duplicates)


def load(path: str | Path) -> ImportedPipeline:
    """Read a definition from a file, or from a Fabric Git folder (<name>.DataPipeline)."""
    p = Path(path)
    if p.is_dir():
        content_file = p / CONTENT_PART
        if not content_file.is_file():
            raise DefinitionError(f"{p.name} has no {CONTENT_PART}.", "missing_content_part",
                                  hint="Choose the pipeline's .DataPipeline folder from a Fabric Git repository.")
        files: dict[str, str] = {}
        for f in sorted(p.iterdir()):
            if f.is_file() and f.stat().st_size <= MAX_BYTES:
                files[f.name] = f.read_bytes().decode("utf-8", "surrogateescape")
        duplicates: list[str] = []
        content = _parse(files[CONTENT_PART], CONTENT_PART, duplicates)
        if not isinstance(content, dict):
            raise DefinitionError(f"{CONTENT_PART} must be a JSON object.", "not_an_object")
        platform = None
        if PLATFORM_PART in files:
            try:
                platform = json.loads(files[PLATFORM_PART])
            except json.JSONDecodeError:
                platform = None
        name = p.name[:-len(".DataPipeline")] if p.name.endswith(".DataPipeline") else p.name
        if isinstance(platform, dict) and isinstance(platform.get("metadata"), dict):
            name = str(platform["metadata"].get("displayName") or name)
        return _finish("git_folder", content, name, platform=platform if isinstance(platform, dict) else None,
                       original=files, duplicates=duplicates)
    if p.stat().st_size > MAX_BYTES:
        raise DefinitionError(f"{p.name} is larger than {MAX_BYTES // (1024 * 1024)} MB.", "too_large")
    return loads(p.read_bytes().decode("utf-8-sig"), name_hint=p.stem if p.name != CONTENT_PART else "")


def _finish(fmt: str, content: dict[str, Any], name_hint: str, wrapper: dict[str, Any] | None = None,
            platform: dict[str, Any] | None = None, original: Any = None,
            duplicates: list[str] | None = None) -> ImportedPipeline:
    pipeline = build(content, name_hint)
    imp = ImportedPipeline(fmt, pipeline, wrapper=wrapper, platform=platform, original=original,
                           duplicate_keys=list(duplicates or []))
    imp._snapshot = _dump(content)
    return imp


def build(content: dict[str, Any], name_hint: str = "") -> Pipeline:
    """The typed view over pipeline-content.json. Structure it can't read becomes a problem on the
    pipeline (the validator reports it), never an exception, so the rest can still be inspected."""
    problems: list[DefinitionError] = []
    name = content.get("name") if isinstance(content.get("name"), str) else name_hint
    props = content.get("properties")
    if not isinstance(props, dict):
        problems.append(DefinitionError("The pipeline has no properties object.", "missing_properties", "$.properties",
                                        hint='A pipeline is {"properties": {"activities": [...]}}.'))
        return Pipeline(name or "", content, [], problems)
    acts = props.get("activities")
    if acts is None:
        problems.append(DefinitionError("The pipeline has no activities list.", "missing_activities",
                                        "$.properties.activities"))
        acts = []
    elif not isinstance(acts, list):
        problems.append(DefinitionError("activities must be a list.", "invalid_activities", "$.properties.activities"))
        acts = []
    return Pipeline(name or "", content, _activities(acts, "$.properties.activities", None, None, problems, 0), problems)


def _activities(items: list[Any], path: str, container: str | None, branch: str | None,
                problems: list[DefinitionError], depth: int) -> list[Activity]:
    out: list[Activity] = []
    if depth > MAX_DEPTH:
        problems.append(DefinitionError(f"Activities are nested more than {MAX_DEPTH} deep.", "too_deep", path))
        return out
    for i, raw in enumerate(items):
        apath = f"{path}[{i}]"
        if not isinstance(raw, dict):
            problems.append(DefinitionError("An activity must be a JSON object.", "invalid_activity", apath))
            continue
        name = raw.get("name") if isinstance(raw.get("name"), str) else ""
        typ = raw.get("type") if isinstance(raw.get("type"), str) else ""
        act = Activity(name, typ, apath, raw, container, branch)
        deps = raw.get("dependsOn", [])
        if isinstance(deps, list):
            for j, d in enumerate(deps):
                dpath = f"{apath}.dependsOn[{j}]"
                if isinstance(d, dict) and isinstance(d.get("activity"), str):
                    conds = d.get("dependencyConditions")
                    act.depends_on.append(Dependency(d["activity"], conds if isinstance(conds, list) else [], dpath))
                else:
                    problems.append(DefinitionError("A dependency must name the activity it waits for.",
                                                    "invalid_dependency", dpath, activity=name))
        else:
            problems.append(DefinitionError("dependsOn must be a list.", "invalid_dependency", f"{apath}.dependsOn",
                                            activity=name))
        tp = raw.get("typeProperties")
        if isinstance(tp, dict):
            for key in CHILD_LISTS:
                if isinstance(tp.get(key), list):
                    act.children[key] = _activities(tp[key], f"{apath}.typeProperties.{key}", name, key, problems, depth + 1)
            cases = tp.get("cases")
            if isinstance(cases, list):
                for c, case in enumerate(cases):
                    if isinstance(case, dict) and isinstance(case.get("activities"), list):
                        label = f"cases[{c}]"
                        act.children[label] = _activities(case["activities"], f"{apath}.typeProperties.cases[{c}].activities",
                                                          name, label, problems, depth + 1)
        out.append(act)
    return out


# ---- writing

def export(imp: ImportedPipeline, fmt: str | None = None, indent: int = 2) -> str | dict[str, str]:
    """The definition as ``fmt`` (default: the format it was read in). A Git folder is a dict of
    file name to text. An unchanged definition in its own format comes back exactly as read."""
    fmt = fmt or imp.source_format
    if fmt not in FORMATS:
        raise DefinitionError(f"Unknown format {fmt!r}; use one of {', '.join(FORMATS)}.", "unknown_format")
    unchanged = not imp.changed
    if unchanged and fmt == imp.source_format and imp.original is not None:
        return copy.copy(imp.original)
    content_text = _content_text(imp, unchanged, indent)
    if fmt == "pipeline_content":
        return content_text
    platform_text = _platform_text(imp)
    if fmt == "git_folder":
        files = dict(imp.original) if imp.source_format == "git_folder" and isinstance(imp.original, dict) else {}
        files[CONTENT_PART] = content_text
        if platform_text is not None:
            files.setdefault(PLATFORM_PART, platform_text)
        return files
    # item_definition: keep every other part (and the wrapper's other keys) as it was
    payload = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
    if imp.source_format == "item_definition" and imp.wrapper is not None:
        wrapper = copy.deepcopy(imp.wrapper)
        for part in _parts(wrapper):
            if isinstance(part, dict) and part.get("path") == CONTENT_PART:
                part["payload"] = payload
                part.setdefault("payloadType", "InlineBase64")
        return json.dumps(wrapper, ensure_ascii=False, indent=indent)
    parts = [{"path": CONTENT_PART, "payload": payload, "payloadType": "InlineBase64"}]
    if platform_text is not None:
        parts.append({"path": PLATFORM_PART, "payload": base64.b64encode(platform_text.encode("utf-8")).decode("ascii"),
                      "payloadType": "InlineBase64"})
    return json.dumps({"definition": {"parts": parts}}, ensure_ascii=False, indent=indent)


def _content_text(imp: ImportedPipeline, unchanged: bool, indent: int) -> str:
    """pipeline-content.json's text: the original bytes when nothing changed, else serialized."""
    if unchanged:
        if imp.source_format == "pipeline_content" and isinstance(imp.original, str):
            return imp.original
        if imp.source_format == "git_folder" and isinstance(imp.original, dict):
            return imp.original[CONTENT_PART]
        if imp.source_format == "item_definition" and imp.wrapper is not None:
            prefix = "$.definition" if isinstance(imp.wrapper.get("definition"), dict) else "$"
            for i, part in enumerate(_parts(imp.wrapper)):
                if isinstance(part, dict) and part.get("path") == CONTENT_PART:
                    return _decode(part, i, prefix)
    return json.dumps(imp.pipeline.content, ensure_ascii=False, indent=indent)


def _platform_text(imp: ImportedPipeline) -> str | None:
    if imp.source_format == "git_folder" and isinstance(imp.original, dict) and PLATFORM_PART in imp.original:
        return imp.original[PLATFORM_PART]
    if imp.source_format == "item_definition" and imp.wrapper is not None:
        prefix = "$.definition" if isinstance(imp.wrapper.get("definition"), dict) else "$"
        for i, part in enumerate(_parts(imp.wrapper)):
            if isinstance(part, dict) and part.get("path") == PLATFORM_PART:
                return _decode(part, i, prefix)
    return None
