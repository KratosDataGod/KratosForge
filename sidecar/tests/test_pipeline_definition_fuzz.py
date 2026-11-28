"""Fuzz the pipeline definitions a person imports (orchestration/definition.py): pipeline-content.json
pasted or opened as a file, a getDefinition result with base64 parts, and a Fabric Git .DataPipeline
folder. Each case is a seeded mutation of a valid definition: torn, not UTF-8, a BOM, NUL bytes, wrong
JSON types at any depth, deep nesting, huge strings and numbers, duplicate keys, missing fields.

Every case must be read or refused with DefinitionError. One that is read must also survive what the
app does next with it: the schema record, validation, the Pipelines page's description and an export
to each format, which must give back the exact text of a definition nobody changed."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from fabric_desktop import pipeline_service
from fabric_desktop.orchestration import definition
from fabric_desktop.orchestration.validate import validate

from tests.settings_fuzz import deep_mutations, mutations

CONTENT = {"name": "p", "properties": {
    "activities": [
        {"name": "A", "type": "SetVariable", "dependsOn": [],
         "typeProperties": {"variableName": "v", "value": "@pipeline().parameters.x"}},
        {"name": "B", "type": "IfCondition", "dependsOn": [{"activity": "A", "dependencyConditions": ["Succeeded"]}],
         "typeProperties": {"expression": {"value": "@true", "type": "Expression"},
                            "ifTrueActivities": [{"name": "C", "type": "Wait", "typeProperties": {"waitTimeInSeconds": 1}}]}},
        {"name": "S", "type": "Switch", "typeProperties": {"on": "@x", "cases": [
            {"value": "a", "activities": [{"name": "D", "type": "Wait"}]}]}}],
    "parameters": {"x": {"type": "string", "defaultValue": "a"}},
    "variables": {"v": {"type": "String"}}}}
PLATFORM = {"metadata": {"type": "DataPipeline", "displayName": "p"}, "config": {"version": "2.0"}}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _wrapper(content: bytes, platform: bytes) -> dict:
    return {"definition": {"parts": [
        {"path": definition.CONTENT_PART, "payload": _b64(content), "payloadType": "InlineBase64"},
        {"path": definition.PLATFORM_PART, "payload": _b64(platform), "payloadType": "InlineBase64"}]}}


def _use(label: str, read, arg) -> None:
    try:
        imp = read(arg)
    except definition.DefinitionError as exc:
        assert str(exc) and exc.code, label
        return
    imp.schema()
    validate(imp)
    pipeline_service.describe(imp, deep=True)
    for fmt in definition.FORMATS:
        definition.export(imp, fmt)
    assert definition.export(imp) == imp.original, label


@pytest.mark.parametrize("seed", [1, 2])
def test_any_pasted_definition_is_read_or_refused_by_name(seed):
    for label, raw in list(mutations(CONTENT, seed, 120)) + list(deep_mutations(CONTENT, seed + 10, 600)):
        _use(label, definition.loads, raw.decode("utf-8", "replace"))


@pytest.mark.parametrize("seed", [3])
def test_any_item_definition_is_read_or_refused_by_name(seed):
    good = json.dumps(CONTENT).encode()
    platform = json.dumps(PLATFORM).encode()
    cases = [(f"content {label}", _wrapper(raw, platform)) for label, raw in mutations(CONTENT, seed, 120)]
    cases += [(f"platform {label}", _wrapper(good, raw)) for label, raw in mutations(PLATFORM, seed, 120)]
    for label, wrapper in cases:
        _use(label, definition.loads, json.dumps(wrapper))
    for label, raw in deep_mutations(_wrapper(good, platform), seed, 300):
        _use(label, definition.loads, raw.decode("utf-8"))


@pytest.mark.parametrize("seed", [4])
def test_any_definition_file_or_git_folder_is_read_or_refused_by_name(tmp_path, seed):
    platform = json.dumps(PLATFORM).encode()
    for n, (label, raw) in enumerate(mutations(CONTENT, seed, 120)):
        file = tmp_path / f"p{n}.json"
        file.write_bytes(raw)
        _use(f"file {label}", definition.load, file)
        folder = tmp_path / f"p{n}.DataPipeline"
        folder.mkdir()
        (folder / definition.CONTENT_PART).write_bytes(raw)
        (folder / definition.PLATFORM_PART).write_bytes(platform)
        _use(f"folder {label}", definition.load, folder)
    for n, (label, raw) in enumerate(mutations(PLATFORM, seed, 120)):
        folder = tmp_path / f"q{n}.DataPipeline"
        folder.mkdir()
        (folder / definition.CONTENT_PART).write_text(json.dumps(CONTENT), encoding="utf-8")
        (folder / definition.PLATFORM_PART).write_bytes(raw)
        _use(f"platform {label}", definition.load, folder)


HUGE_INT = b'{"properties": {"activities": [], "x": ' + b"9" * 5000 + b"}}"
DEEP = b"[" * 100_000 + b"]" * 100_000


def test_a_number_too_long_to_read_is_refused_by_name():
    with pytest.raises(definition.DefinitionError, match="holds a number too long") as got:
        definition.loads(HUGE_INT.decode())
    assert got.value.code == "invalid_json"


def test_a_definition_file_that_isnt_utf8_is_refused_by_name(tmp_path):
    file = tmp_path / "p.json"
    file.write_bytes(json.dumps(CONTENT).encode()[:20] + b"\xff\xfe" + json.dumps(CONTENT).encode()[20:])
    with pytest.raises(definition.DefinitionError, match="p.json can't be read because it isn't UTF-8 text") as got:
        definition.load(file)
    assert got.value.code == "invalid_encoding"


def _folder(tmp_path: Path, content: bytes, platform: bytes) -> Path:
    folder = tmp_path / "p.DataPipeline"
    folder.mkdir()
    (folder / definition.CONTENT_PART).write_bytes(content)
    (folder / definition.PLATFORM_PART).write_bytes(platform)
    return folder


@pytest.mark.parametrize("part", [definition.CONTENT_PART, definition.PLATFORM_PART])
def test_a_git_folder_part_that_isnt_utf8_is_refused_not_kept_to_fail_on_export(tmp_path, part):
    good = {definition.CONTENT_PART: json.dumps(CONTENT).encode(), definition.PLATFORM_PART: json.dumps(PLATFORM).encode()}
    good[part] = good[part].replace(b'"p"', b'"p\xe9"')
    folder = _folder(tmp_path, good[definition.CONTENT_PART], good[definition.PLATFORM_PART])
    with pytest.raises(definition.DefinitionError, match=f"{part} can't be read because it isn't UTF-8 text") as got:
        definition.load(folder)
    assert got.value.code == "invalid_encoding"


@pytest.mark.parametrize("platform", [DEEP, b'{"config": {"version": ' + b"9" * 5000 + b"}}"], ids=["deep", "huge int"])
def test_a_platform_part_that_cant_be_parsed_is_kept_as_it_is(tmp_path, platform):
    imp = definition.load(_folder(tmp_path, json.dumps(CONTENT).encode(), platform))
    assert imp.platform is None and imp.original[definition.PLATFORM_PART] == platform.decode()
    imp = definition.loads(json.dumps(_wrapper(json.dumps(CONTENT).encode(), platform)))
    assert imp.platform is None and imp.schema()["platform_version"] is None


def test_a_git_folder_whose_content_is_too_large_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setattr(definition, "MAX_BYTES", 100)
    folder = _folder(tmp_path, json.dumps(CONTENT).encode(), b"{}")
    with pytest.raises(definition.DefinitionError, match="larger than") as got:
        definition.load(folder)
    assert got.value.code == "too_large"
