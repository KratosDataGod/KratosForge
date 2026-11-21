"""orchestration/definition.py paths that mutation testing (batch 22, docs/quality/mutation-testing.md)
showed no test checked: the reader version and limits, the size messages, names taken from the wrapper,
the .platform part or the file, Git folders with folders or large files inside, Switch cases, and how
export rebuilds each format. Nothing here opens a connection."""

from __future__ import annotations

import base64
import json

import pytest

from fabric_desktop.orchestration import DefinitionError, definition, export, load, loads
from fabric_desktop.orchestration.definition import CONTENT_PART, PLATFORM_PART, ImportedPipeline, build


@pytest.fixture(autouse=True)
def _own_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _part(path, text):
    return {"path": path, "payload": _b64(text), "payloadType": "InlineBase64"}


def _content(name=None, acts=None):
    body = {"properties": {"activities": acts if acts is not None else [{"name": "w", "type": "Wait"}]}}
    if name is not None:
        body["name"] = name
    return body


def _nested(levels, key="activities"):
    """One Wait inside ``levels`` ForEach containers (or Switch cases when key is 'cases')."""
    act = {"name": "leaf", "type": "Wait"}
    for i in range(levels):
        if key == "cases":
            act = {"name": f"s{i}", "type": "Switch", "typeProperties": {"cases": [{"value": "a", "activities": [act]}]}}
        else:
            act = {"name": f"f{i}", "type": "ForEach", "typeProperties": {"activities": [act]}}
    return {"properties": {"activities": [act]}}


# ---- the reader's version and limits

def test_the_schema_records_reader_version_one():
    assert definition.DEFINITION_VERSION == 1
    assert loads(json.dumps(_content())).schema()["parser_version"] == 1


def test_the_size_limit_is_twenty_megabytes():
    assert definition.MAX_BYTES == 20 * 1024 * 1024


@pytest.mark.parametrize("key", ["activities", "cases"])
def test_forty_containers_deep_is_read_and_forty_one_is_too_deep(key):
    ok = build(_nested(40, key))
    assert not ok.problems
    assert any(a.name == "leaf" for a in ok.all_activities())
    deep = build(_nested(41, key))
    assert [p.code for p in deep.problems] == ["too_deep"]
    assert not any(a.name == "leaf" for a in deep.all_activities())


def test_text_over_the_limit_names_the_limit_in_megabytes(monkeypatch):
    monkeypatch.setattr(definition, "MAX_BYTES", 3 * 1024 * 1024)
    with pytest.raises(DefinitionError) as caught:
        loads(" " * (3 * 1024 * 1024 + 1))
    assert caught.value.code == "too_large"
    assert str(caught.value) == "The definition is larger than 3 MB."


def test_a_file_over_the_limit_names_the_limit_in_megabytes(monkeypatch, tmp_path):
    monkeypatch.setattr(definition, "MAX_BYTES", 3 * 1024 * 1024)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * (3 * 1024 * 1024 + 1))
    with pytest.raises(DefinitionError) as caught:
        load(big)
    assert caught.value.code == "too_large"
    assert str(caught.value) == "big.json is larger than 3 MB."


# ---- activities

def test_only_the_inactive_state_marks_an_activity_inactive():
    acts = [{"name": "off", "type": "Wait", "state": "Inactive"}, {"name": "on", "type": "Wait", "state": "Active"},
            {"name": "plain", "type": "Wait"}]
    p = build(_content(acts=acts))
    assert [a.inactive for a in p.activities] == [True, False, False]


def test_a_switch_case_that_is_not_an_object_is_skipped_and_the_rest_are_read():
    acts = [{"name": "sw", "type": "Switch", "typeProperties": {"cases": [
        "junk", {"value": "a"}, {"value": "b", "activities": [{"name": "inner", "type": "Wait"}]}]}}]
    p = build(_content(acts=acts))
    sw = p.find("sw")
    assert list(sw.children) == ["cases[2]"]
    assert sw.children["cases[2]"][0].path == "$.properties.activities[0].typeProperties.cases[2].activities[0]"
    assert sw.children["cases[2]"][0].container == "sw"
    assert not p.problems


# ---- schema: parts and dialect

def test_a_pipeline_content_import_lists_one_part_even_with_a_wrapper_attached():
    content = _content(name="p")
    imp = ImportedPipeline("pipeline_content", build(content),
                           wrapper={"definition": {"parts": [_part("other.json", "{}")]}})
    assert imp.schema()["parts"] == [CONTENT_PART]


def test_an_item_definition_lists_its_parts_in_order():
    text = json.dumps({"definition": {"parts": [_part(PLATFORM_PART, "{}"), _part(CONTENT_PART, json.dumps(_content()))]}})
    assert loads(text).schema()["parts"] == [PLATFORM_PART, CONTENT_PART]


def test_a_synapse_resource_type_alone_reads_as_data_factory():
    content = {"type": "Microsoft.Synapse/workspaces/pipelines", "properties": {"activities": []}}
    schema = loads(json.dumps(content)).schema()
    assert schema["dialect"] == "data_factory"
    assert schema["dialect_evidence"] == ["resource type Microsoft.Synapse/workspaces/pipelines"]


# ---- names

def test_an_item_definition_without_a_name_takes_the_wrapper_display_name():
    text = json.dumps({"displayName": "Daily", "definition": {"parts": [_part(CONTENT_PART, json.dumps(_content()))]}})
    assert loads(text).pipeline.name == "Daily"
    assert loads(text, name_hint="given").pipeline.name == "given"


def test_an_item_definition_without_a_display_name_takes_the_platform_display_name():
    platform = json.dumps({"metadata": {"type": "DataPipeline", "displayName": "From platform"}})
    text = json.dumps({"definition": {"parts": [_part(CONTENT_PART, json.dumps(_content())), _part(PLATFORM_PART, platform)]}})
    assert loads(text).pipeline.name == "From platform"


def test_a_file_without_a_name_is_named_after_the_file_unless_it_is_pipeline_content(tmp_path):
    (tmp_path / "orders.json").write_text(json.dumps(_content()), encoding="utf-8")
    assert load(tmp_path / "orders.json").pipeline.name == "orders"
    (tmp_path / CONTENT_PART).write_text(json.dumps(_content()), encoding="utf-8")
    assert load(tmp_path / CONTENT_PART).pipeline.name == ""


def test_a_git_folder_takes_the_platform_display_name_over_the_folder_name(tmp_path):
    folder = tmp_path / "raw.DataPipeline"
    folder.mkdir()
    (folder / CONTENT_PART).write_text(json.dumps(_content()), encoding="utf-8")
    (folder / PLATFORM_PART).write_text(json.dumps({"metadata": {"displayName": "Pretty"}}), encoding="utf-8")
    assert load(folder).pipeline.name == "Pretty"


def test_a_git_folder_skips_sub_folders_and_files_over_the_limit(monkeypatch, tmp_path):
    folder = tmp_path / "p.DataPipeline"
    folder.mkdir()
    (folder / CONTENT_PART).write_text(json.dumps(_content()), encoding="utf-8")
    (folder / "nested").mkdir()
    (folder / "huge.bin").write_bytes(b"x" * 2048)
    monkeypatch.setattr(definition, "MAX_BYTES", 1024)
    imp = load(folder)
    assert sorted(imp.original) == [CONTENT_PART]


# ---- export

def test_a_changed_definition_exports_with_two_space_indent_and_its_own_characters():
    imp = loads(json.dumps(_content(name="Café")))
    imp.pipeline.content["properties"]["description"] = "changed"
    out = export(imp)
    assert out == json.dumps(imp.pipeline.content, ensure_ascii=False, indent=2)
    assert "Café" in out


def test_a_changed_item_definition_keeps_its_own_characters_in_the_wrapper():
    text = json.dumps({"displayName": "Café", "definition": {"parts": [_part(CONTENT_PART, json.dumps(_content()))]}},
                      ensure_ascii=False)
    imp = loads(text)
    imp.pipeline.content["properties"]["description"] = "changed"
    out = export(imp)
    assert '"displayName": "Café"' in out


def test_a_pipeline_content_import_exports_a_fresh_item_definition_even_with_a_wrapper_attached():
    content = _content(name="p")
    imp = ImportedPipeline("pipeline_content", build(content),
                           wrapper={"keep": "me", "definition": {"parts": [_part(CONTENT_PART, "{}")]}})
    out = json.loads(export(imp, "item_definition"))
    assert "keep" not in out
    assert [p["path"] for p in out["definition"]["parts"]] == [CONTENT_PART]


def test_unchanged_content_is_rebuilt_from_the_content_not_a_wrapper_it_did_not_come_from():
    content = _content(name="p")
    imp = ImportedPipeline("pipeline_content", build(content),
                           wrapper={"definition": {"parts": [_part(CONTENT_PART, '{"other": 1}')]}})
    imp._snapshot = definition._dump(content)
    assert export(imp, "git_folder")[CONTENT_PART] == json.dumps(content, ensure_ascii=False, indent=2)


def test_an_item_definition_with_folder_files_still_takes_its_content_from_the_wrapper():
    content_text = '{"properties": {"activities": []},   "name": "spaced"}'
    wrapper = {"definition": {"parts": [_part(CONTENT_PART, content_text)]}}
    imp = loads(json.dumps(wrapper))
    imp.original = {"something.txt": "x"}
    assert export(imp, "pipeline_content") == content_text


def test_an_unchanged_item_definition_exports_its_content_part_not_the_part_before_it():
    content_text = '{"name": "p",  "properties": {"activities": []}}'
    text = json.dumps({"definition": {"parts": [_part(PLATFORM_PART, '{"metadata": {}}'), _part(CONTENT_PART, content_text)]}})
    assert export(loads(text), "pipeline_content") == content_text


def test_a_pipeline_content_import_exports_no_platform_file_from_a_wrapper_it_did_not_come_from():
    content = _content(name="p")
    imp = ImportedPipeline("pipeline_content", build(content),
                           wrapper={"definition": {"parts": [_part(PLATFORM_PART, '{"metadata": {}}')]}})
    imp._snapshot = definition._dump(content)
    assert sorted(export(imp, "git_folder")) == [CONTENT_PART]


@pytest.mark.parametrize("wrapped, prefix", [(True, "$.definition"), (False, "$")])
def test_a_platform_part_that_breaks_after_reading_names_its_place(wrapped, prefix):
    parts = [_part(CONTENT_PART, json.dumps(_content())), _part(PLATFORM_PART, "{}")]
    imp = loads(json.dumps({"definition": {"parts": parts}} if wrapped else {"parts": parts}))
    definition._parts(imp.wrapper)[1]["payload"] = None
    imp.pipeline.content["properties"]["description"] = "changed"
    with pytest.raises(DefinitionError) as caught:
        export(imp, "git_folder")
    assert caught.value.code == "missing_payload"
    assert caught.value.path == f"{prefix}.parts[1].payload"
