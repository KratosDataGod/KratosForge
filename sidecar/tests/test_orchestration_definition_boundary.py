"""Boundary kill tests for orchestration/definition (mutation testing, batch 166): a definition of
exactly MAX_BYTES is still read; only a larger one is refused."""
from __future__ import annotations

import json

from fabric_desktop.orchestration import definition, load, loads

CONTENT = json.dumps({"properties": {"activities": [{"name": "Wait1", "type": "Wait",
                                                     "typeProperties": {"waitTimeInSeconds": 1}}]}})


def test_text_of_exactly_the_limit_is_read(monkeypatch):
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in loads(CONTENT).pipeline.activities] == ["Wait1"]


def test_a_file_of_exactly_the_limit_is_read(tmp_path, monkeypatch):
    f = tmp_path / "p.json"
    f.write_bytes(CONTENT.encode())
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in load(f).pipeline.activities] == ["Wait1"]


def test_a_git_folder_part_of_exactly_the_limit_is_read(tmp_path, monkeypatch):
    d = tmp_path / "Load.DataPipeline"
    d.mkdir()
    (d / definition.CONTENT_PART).write_bytes(CONTENT.encode())
    monkeypatch.setattr(definition, "MAX_BYTES", len(CONTENT.encode()))
    assert [a.name for a in load(d).pipeline.activities] == ["Wait1"]


def _deep_value(levels):
    value = {"type": "Expression", "value": "@pipeline().RunId"}
    for _ in range(levels):
        value = {"a": [value]}
    return value


def _with_value(value):
    return json.dumps({"name": "p", "properties": {"activities": [
        {"name": "set", "type": "SetVariable", "typeProperties": {"variableName": "v", "value": value}}],
        "variables": {"v": {"type": "String"}}}})


def test_a_definition_nested_as_deep_as_the_limit_is_read_and_checked():
    """Every pass over the definition (reading, checking, listing expressions, compatibility) copes with
    the deepest definition the reader takes."""
    from fabric_desktop.pipeline_service import PipelineMixin
    levels = (definition.MAX_NESTING - 8) // 2  # each level is an object and a list
    text = _with_value(_deep_value(levels))
    svc = PipelineMixin()
    out = svc.pipeline_inspect(text=text)
    assert out["ok"], out
    [expr] = out["activities"][0]["expressions"]
    assert expr["text"] == "pipeline().RunId" and expr["path"].count("[0]") == levels + 1
    assert svc.pipeline_validate(text=text)["ok"] and svc.pipeline_compatibility(text=text)["activities"]


def test_a_definition_nested_past_the_limit_is_refused_with_the_limit():
    """Thousands of levels used to raise RecursionError from the expression scan; now the reader
    refuses the text, naming the limit, as it does one too large."""
    import pytest
    from fabric_desktop.orchestration import DefinitionError
    from fabric_desktop.pipeline_service import PipelineMixin
    text = _with_value(_deep_value(2500))
    with pytest.raises(DefinitionError) as caught:
        loads(text)
    assert caught.value.code == "too_deep"
    assert str(caught.value) == f"The definition is nested more than {definition.MAX_NESTING} levels deep."
    out = PipelineMixin().pipeline_import(text=text)
    assert not out["ok"] and out["diagnostics"][0]["code"] == "too_deep"
