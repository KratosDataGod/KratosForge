"""Reading pipeline definitions: the dialect evidence, damaged parts, size limits and malformed activity
lists the tests in test_pipeline_definitions don't reach. Nothing here opens a connection."""

import base64
import json

import pytest

from fabric_desktop.orchestration import DefinitionError, definition, load, loads


def _part(path, text):
    return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}


def _wrapped(*parts):
    return json.dumps({"definition": {"parts": list(parts)}})


CONTENT = json.dumps({"name": "p", "properties": {"activities": [{"name": "w", "type": "Wait", "typeProperties": {}}]}})


def test_library_variables_and_a_data_factory_activity_read_as_mixed():
    content = {"properties": {"libraryVariables": {"lib": {}},
                              "activities": [{"name": "nb", "type": "DatabricksNotebook", "typeProperties": {}}]}}
    schema = loads(json.dumps(content)).schema()
    assert schema["dialect"] == "mixed"
    assert schema["dialect_evidence"] == ["uses libraryVariables", "activity type DatabricksNotebook"]


def test_a_part_that_is_not_an_object_is_refused_with_its_place():
    with pytest.raises(DefinitionError) as raised:
        loads(json.dumps({"definition": {"parts": ["not a part"]}}))
    assert (raised.value.code, raised.value.path) == ("invalid_part", "$.definition.parts[0]")


def test_a_platform_part_that_is_not_json_is_kept_but_not_read():
    imp = loads(_wrapped(_part("pipeline-content.json", CONTENT), _part(".platform", "not json")))
    assert imp.platform is None and imp.schema()["platform_schema"] is None


def test_content_that_is_not_an_object_is_refused_in_parts_and_in_a_git_folder(tmp_path):
    with pytest.raises(DefinitionError) as raised:
        loads(_wrapped(_part("pipeline-content.json", "[1, 2]")))
    assert raised.value.code == "not_an_object"
    folder = tmp_path / "pl.DataPipeline"
    folder.mkdir()
    (folder / "pipeline-content.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(DefinitionError, match="must be a JSON object"):
        load(folder)


def test_a_git_folder_with_a_damaged_platform_file_is_named_after_its_folder(tmp_path):
    folder = tmp_path / "pl_orders.DataPipeline"
    folder.mkdir()
    (folder / "pipeline-content.json").write_text(CONTENT, encoding="utf-8")
    (folder / ".platform").write_text("{ torn", encoding="utf-8")
    imp = load(folder)
    assert imp.platform is None and imp.pipeline.name == "p"
    assert imp.schema()["parts"] == [".platform", "pipeline-content.json"]


def test_definitions_over_the_size_limit_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(definition, "MAX_BYTES", 10)
    with pytest.raises(DefinitionError) as raised:
        loads(CONTENT)
    assert raised.value.code == "too_large"
    big = tmp_path / "big.json"
    big.write_text(CONTENT, encoding="utf-8")
    with pytest.raises(DefinitionError, match="big.json is larger than"):
        load(big)


def test_activities_nested_too_deep_and_bad_dependencies_are_problems_not_exceptions(monkeypatch):
    monkeypatch.setattr(definition, "MAX_DEPTH", 0)
    inner = {"name": "inner", "type": "Wait", "typeProperties": {}}
    outer = {"name": "loop", "type": "ForEach", "dependsOn": "w", "typeProperties": {"activities": [inner]}}
    bad_dep = {"name": "b", "type": "Wait", "dependsOn": [{"activity": 5}], "typeProperties": {}}
    p = definition.build({"properties": {"activities": [outer, bad_dep]}})
    got = [(e.code, e.path) for e in p.problems]
    assert ("invalid_dependency", "$.properties.activities[0].dependsOn") in got
    assert ("too_deep", "$.properties.activities[0].typeProperties.activities") in got
    assert ("invalid_dependency", "$.properties.activities[1].dependsOn[0]") in got
    assert [a.name for a in p.all_activities()] == ["loop", "b"]
