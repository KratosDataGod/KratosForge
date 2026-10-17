"""ORCH-001: reading, checking and writing back Fabric pipeline definitions. No network, no Fabric.

The fixtures in tests/pipelines/ are hand-built in the shapes Microsoft documents for Fabric
(pipeline-content.json, getDefinition parts, a Git .DataPipeline folder) and for Azure Data
Factory. They aren't exported from a live tenant; see docs/orchestration/plan.md.
"""

from __future__ import annotations

import base64
import copy
import json
import socket
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.cli import main as cli_main
from fabric_desktop.orchestration import DEFINITION_VERSION, DefinitionError, export, load, loads, validate
from fabric_desktop.orchestration import expressions
from fabric_desktop.pipeline_service import PipelineMixin

HERE = Path(__file__).parent / "pipelines"
CONTENT = HERE / "orders_daily.json"
WRAPPED = HERE / "orders_daily.item-definition.json"
FOLDER = HERE / "pl_orders_daily.DataPipeline"
ADF = HERE / "adf_style.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Reading a definition must never reach the network."""
    def refuse(*args, **kwargs):
        raise AssertionError("pipeline definitions must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def codes(diags, severity=None):
    return [d.code for d in diags if severity is None or d.severity == severity]


def pipeline(activities, parameters=None, variables=None):
    props = {"activities": activities}
    if parameters is not None:
        props["parameters"] = parameters
    if variables is not None:
        props["variables"] = variables
    return json.dumps({"name": "p", "properties": props})


def act(name, typ="Wait", depends=(), **type_props):
    return {"name": name, "type": typ,
            "dependsOn": [{"activity": d, "dependencyConditions": ["Succeeded"]} for d in depends],
            "typeProperties": type_props or {"waitTimeInSeconds": 1}}


# ---- formats

def test_reads_pipeline_content():
    imp = load(CONTENT)
    assert imp.source_format == "pipeline_content"
    p = imp.pipeline
    assert p.name == "pl_orders_daily"
    assert [a.name for a in p.activities][:3] == ["Get watermark", "List new files", "Any files"]
    assert len(p.all_activities()) == 18
    assert set(p.parameters) == {"run_date", "region", "api_key"}
    assert set(p.variables) == {"loaded_files", "status"}
    assert codes(validate(imp), "error") == []


def test_reads_item_definition_and_git_folder_the_same():
    a, b, c = load(CONTENT), load(WRAPPED), load(FOLDER)
    assert (b.source_format, c.source_format) == ("item_definition", "git_folder")
    assert a.pipeline.content == b.pipeline.content == c.pipeline.content
    assert b.platform["metadata"]["type"] == c.platform["metadata"]["type"] == "DataPipeline"
    assert c.pipeline.name == "pl_orders_daily"


def test_reads_bare_parts_and_create_body():
    wrapped = json.loads(WRAPPED.read_text())
    bare = loads(json.dumps({"parts": wrapped["definition"]["parts"]}))
    body = loads(json.dumps({"displayName": "From body", "type": "DataPipeline", "definition": wrapped["definition"]}))
    assert bare.source_format == body.source_format == "item_definition"
    assert bare.pipeline.content == body.pipeline.content == load(CONTENT).pipeline.content


def test_adf_style_is_read_and_flagged_as_data_factory():
    imp = load(ADF)
    schema = imp.schema()
    assert schema["dialect"] == "data_factory"
    assert any("DatabricksNotebook" in e for e in schema["dialect_evidence"])
    assert codes(validate(imp), "error") == []


def test_schema_record():
    schema = load(WRAPPED).schema()
    assert schema["parser_version"] == DEFINITION_VERSION
    assert schema["source_format"] == "item_definition"
    assert schema["dialect"] == "fabric"
    assert schema["platform_version"] == "2.0"
    assert schema["platform_schema"].endswith("platformProperties/2.0.0/schema.json")
    assert schema["item_type"] == "DataPipeline"
    assert schema["parts"] == ["pipeline-content.json", ".platform"]
    assert schema["activity_types"]["SetVariable"] == 2
    assert "notebookId" in schema["type_properties"]["TridentNotebook"]
    keys = {(u["path"], u["key"]) for u in schema["unrecognized_keys"]}
    assert ("$.properties['x-future-pipeline-setting']", "x-future-pipeline-setting") in keys
    assert ("$.properties.activities[8]['x-contoso-trace']", "x-contoso-trace") in keys


# ---- nothing lost

@pytest.mark.parametrize("path", [CONTENT, WRAPPED, ADF])
def test_unchanged_file_exports_byte_for_byte(path):
    assert export(load(path)) == path.read_bytes().decode("utf-8")


def test_unchanged_folder_exports_its_files():
    files = export(load(FOLDER))
    assert files == {f.name: f.read_text(encoding="utf-8") for f in FOLDER.iterdir()}


@pytest.mark.parametrize("source", [CONTENT, WRAPPED, FOLDER])
@pytest.mark.parametrize("fmt", ["pipeline_content", "item_definition", "git_folder"])
def test_every_format_converts_without_loss(source, fmt):
    original = load(CONTENT).pipeline.content
    out = export(load(source), fmt)
    if fmt == "git_folder":
        again = loads(out["pipeline-content.json"])
    else:
        again = loads(out)
    assert again.pipeline.content == original
    # Unchanged content keeps its own bytes even inside another format.
    content_bytes = CONTENT.read_text(encoding="utf-8")
    if fmt == "pipeline_content":
        assert out == content_bytes
    elif fmt == "git_folder":
        assert out["pipeline-content.json"] == content_bytes


def test_platform_part_survives_conversion():
    out = json.loads(export(load(FOLDER), "item_definition"))
    paths = [p["path"] for p in out["definition"]["parts"]]
    assert paths == ["pipeline-content.json", ".platform"]
    assert loads(json.dumps(out)).platform["config"]["logicalId"] == "88888888-8888-8888-8888-888888888888"
    folder = export(load(WRAPPED), "git_folder")
    assert json.loads(folder[".platform"])["metadata"]["displayName"] == "pl_orders_daily"


def test_unknown_types_and_properties_survive_a_change():
    imp = load(WRAPPED)
    assert not imp.changed
    imp.pipeline.content["properties"]["description"] = "changed"
    assert imp.changed
    out = json.loads(export(imp))
    parts = {p["path"]: p for p in out["definition"]["parts"]}
    content = json.loads(base64.b64decode(parts["pipeline-content.json"]["payload"]))
    assert content["properties"]["description"] == "changed"
    alert = content["properties"]["activities"][8]
    assert alert["type"] == "ContosoPagerNotify"
    assert alert["x-contoso-trace"] == "kept"
    assert alert["typeProperties"]["futureSetting"] == {"nested": [1, 2.5, True, None]}
    assert content["properties"]["x-future-pipeline-setting"] == {"enabled": True}
    assert parts[".platform"] == json.loads(WRAPPED.read_text())["definition"]["parts"][1]
    # Key order is kept.
    assert list(content["properties"]) == list(load(CONTENT).pipeline.content["properties"])


def test_changed_content_changes_semantically_only_where_edited():
    imp = load(CONTENT)
    before = copy.deepcopy(imp.pipeline.content)
    imp.pipeline.content["properties"]["annotations"].append("new")
    after = json.loads(export(imp))
    before["properties"]["annotations"].append("new")
    assert after == before


# ---- nested activities

def test_nested_containers():
    p = load(CONTENT).pipeline
    if_cond = p.find("Any files")
    assert set(if_cond.children) == {"ifTrueActivities", "ifFalseActivities"}
    foreach = p.find("Each file")
    assert (foreach.container, foreach.branch) == ("Any files", "ifTrueActivities")
    copy_act = p.find("Copy file to bronze")
    assert copy_act.container == "Each file"
    assert copy_act.path == "$.properties.activities[2].typeProperties.ifTrueActivities[0].typeProperties.activities[0]"
    switch = p.find("Pick region job")
    assert list(switch.children) == ["defaultActivities", "cases[0]", "cases[1]"]
    assert p.find("Run AMER script").branch == "cases[1]"
    assert [a.name for a in p.find("Wait for silver").children["activities"]] == ["Check silver", "Pause"]


# ---- expressions

def test_expression_references():
    refs = expressions.references("concat(activity('Get list').output.value[0], variables('v'), "
                                  "pipeline().parameters.day, pipeline().parameters['odd name'], item().name, "
                                  "pipeline().RunId, pipeline().libraryVariables.conn)")
    got = {(r.kind, r.name, r.member) for r in refs}
    assert got == {("activity", "Get list", "output.value.0"), ("variable", "v", ""), ("parameter", "day", ""),
                   ("parameter", "odd name", ""), ("item", "item", "name"), ("system", "RunId", ""),
                   ("library_variable", "conn", "")}


def test_expression_forms():
    found = list(expressions.scan({
        "a": {"value": "@pipeline().parameters.x", "type": "Expression"},
        "b": "@variables('y')",
        "c": "run @{pipeline().parameters.z} and @{activity('A').output.n}",
        "d": "@@not an expression",
        "e": "mail me@example.com",
        "f": "price: 'it''s @{concat('a}', 'b')}'",
    }))
    by_path = {}
    for e in found:
        by_path.setdefault(e.path, []).append(e)
    assert set(by_path) == {"$.a.value", "$.b", "$.c", "$.f"}
    assert [e.interpolated for e in by_path["$.c"]] == [True, True]
    assert by_path["$.f"][0].text == "concat('a}', 'b')"
    assert not any(e.problem for e in found)


def test_expressions_are_never_evaluated(monkeypatch):
    called = []
    monkeypatch.setattr("builtins.eval", lambda *a, **k: called.append(a))
    monkeypatch.setattr("builtins.exec", lambda *a, **k: called.append(a))
    text = pipeline([act("A", "WebActivity", url="@{__import__('os').system('echo hacked')}")])
    imp = loads(text)
    validate(imp)
    PipelineMixin().pipeline_inspect(text=text)
    assert called == []


def test_unreadable_expression_is_a_warning():
    imp = loads(pipeline([act("A", "SetVariable", variableName="v", value="@concat('a', ")], variables={"v": {"type": "String"}}))
    diags = validate(imp)
    assert codes(diags, "warning") == ["unreadable_expression"]
    assert diags[0].path == "$.properties.activities[0].typeProperties.value"


# ---- validation

def test_missing_dependency_with_path_and_activity():
    diags = validate(loads(pipeline([act("A"), act("B", depends=["Nope"])])))
    d = next(d for d in diags if d.code == "missing_dependency")
    assert (d.severity, d.activity, d.path) == ("error", "B", "$.properties.activities[1].dependsOn[0].activity")
    assert "Nope" in d.message and d.hint


def test_dependency_across_containers_is_missing():
    inner = act("Inner", depends=["Outer"])
    text = pipeline([act("Outer"), {"name": "Loop", "type": "ForEach", "dependsOn": [],
                                     "typeProperties": {"items": "@createArray(1)", "activities": [inner]}}])
    d = next(d for d in validate(loads(text)) if d.code == "missing_dependency")
    assert d.activity == "Inner" and "inside Loop" in d.message


def test_cycles():
    text = pipeline([act("A", depends=["C"]), act("B", depends=["A"]), act("C", depends=["B"]), act("D", depends=["D"])])
    diags = validate(loads(text))
    cycles = [d for d in diags if d.code == "dependency_cycle"]
    assert len(cycles) == 1
    assert all(n in cycles[0].message for n in ("A", "B", "C"))
    assert "self_dependency" in codes(diags)


def test_nested_cycle():
    kids = [act("X", depends=["Y"]), act("Y", depends=["X"])]
    text = pipeline([{"name": "If", "type": "IfCondition", "dependsOn": [],
                      "typeProperties": {"expression": "@true", "ifTrueActivities": kids}}])
    d = next(d for d in validate(loads(text)) if d.code == "dependency_cycle")
    assert d.path.startswith("$.properties.activities[0].typeProperties.ifTrueActivities")


def test_duplicate_names_including_nested():
    text = pipeline([act("A"), {"name": "Loop", "type": "Until", "dependsOn": [],
                                "typeProperties": {"expression": "@true", "activities": [act("A")]}}])
    d = next(d for d in validate(loads(text)) if d.code == "duplicate_activity_name")
    assert d.activity == "A" and d.path.endswith("activities[0].name") and "typeProperties" in d.path


def test_required_fields():
    text = json.dumps({"properties": {"activities": [{"type": "Wait"}, {"name": "B"}, "nope",
                                                      {"name": "C", "type": "Wait", "dependsOn": [{"activity": "B"}]},
                                                      {"name": "D", "type": "Wait", "dependsOn": [{"activity": "C",
                                                       "dependencyConditions": ["Succeded"]}]}]}})
    found = set(codes(validate(loads(text)), "error"))
    assert {"missing_name", "missing_type", "invalid_activity", "missing_dependency_condition",
            "unknown_dependency_condition"} <= found


def test_missing_properties_and_activities():
    assert codes(validate(loads('{"name": "x"}'))) == ["missing_properties"]
    assert codes(validate(loads('{"properties": {}}'))) == ["missing_activities"]
    assert codes(validate(loads('{"properties": {"activities": {}}}'))) == ["invalid_activities"]


def test_undeclared_references():
    text = pipeline([act("Get", "Lookup", source={"type": "x"}),
                     act("Set", "SetVariable", depends=["Get"], variableName="missing",
                         value="@concat(pipeline().parameters.nope, variables('alsomissing'), activity('Ghost').output)"),
                     act("Early", "SetVariable", variableName="v", value="@activity('Get').output.firstRow"),
                     act("Out", "Wait", waitTimeInSeconds="@item().n")],
                    parameters={}, variables={"v": {"type": "String"}})
    diags = validate(loads(text))
    found = {(d.code, d.activity) for d in diags}
    assert ("undeclared_variable", "Set") in found
    assert ("undeclared_parameter", "Set") in found
    assert ("unknown_activity_reference", "Set") in found
    assert ("activity_not_upstream", "Early") in found
    assert ("item_outside_foreach", "Out") in found
    assert sum(1 for d in diags if d.code == "undeclared_variable") == 2


def test_upstream_through_containers_is_fine():
    text = load(CONTENT)
    assert not [d for d in validate(text) if d.code in ("activity_not_upstream", "item_outside_foreach")]


def test_unknown_activity_type_is_not_a_problem():
    imp = loads(pipeline([{"name": "Future", "type": "SomethingMicrosoftAddsNextYear", "dependsOn": [],
                           "typeProperties": {"whatever": {"deep": [1]}}}]))
    assert validate(imp) == []
    assert imp.schema()["activity_types"] == {"SomethingMicrosoftAddsNextYear": 1}


def test_declaration_problems():
    text = pipeline([act("A")], parameters={"p": {"type": "Weird"}, "q": "nope"}, variables={"v": {}})
    found = set(codes(validate(loads(text))))
    assert {"unknown_parameter_type", "invalid_parameter", "invalid_variable"} <= found


def test_duplicate_json_keys_are_reported_and_kept():
    text = '{"properties": {"activities": [], "annotations": ["a"], "annotations": ["b"]}}'
    imp = loads(text)
    assert "duplicate_json_key" in codes(validate(imp))
    assert export(imp) == text  # the exact text, both keys included


# ---- malformed input

@pytest.mark.parametrize("text,code", [
    ("{not json", "invalid_json"),
    ("[1, 2]", "not_an_object"),
    ('{"definition": {"parts": []}}', "missing_content_part"),
    ('{"definition": {"parts": [{"path": "pipeline-content.json", "payload": "!!!", "payloadType": "InlineBase64"}]}}',
     "invalid_payload"),
    ('{"definition": {"parts": [{"path": "pipeline-content.json", "payload": "e30=", "payloadType": "SurpriseFormat"}]}}',
     "unsupported_payload_type"),
    ('{"definition": {"parts": [{"path": "pipeline-content.json"}]}}', "missing_payload"),
])
def test_malformed_definitions_say_why(text, code):
    with pytest.raises(DefinitionError) as info:
        loads(text)
    assert info.value.code == code
    assert str(info.value)
    result = PipelineMixin().pipeline_validate(text=text)
    assert result["ok"] is False and result["diagnostics"][0]["code"] == code


def test_invalid_json_says_where():
    with pytest.raises(DefinitionError) as info:
        loads('{\n  "properties": {\n    "activities": [,]\n  }\n}')
    assert "line 3" in str(info.value)


def test_folder_without_content(tmp_path):
    (tmp_path / "x.DataPipeline").mkdir()
    with pytest.raises(DefinitionError) as info:
        load(tmp_path / "x.DataPipeline")
    assert info.value.code == "missing_content_part"


def test_unknown_export_format():
    with pytest.raises(DefinitionError):
        export(load(CONTENT), "yaml")


# ---- service, server, external tools, CLI

def test_service_calls():
    svc = PipelineMixin()
    imported = svc.pipeline_import(path=str(CONTENT))
    assert imported["ok"] and imported["name"] == "pl_orders_daily" and imported["activity_count"] == 18
    secure = next(p for p in imported["parameters"] if p["name"] == "api_key")
    assert secure["default"] == "(hidden)"
    assert "not-a-real-secret" not in json.dumps(svc.pipeline_inspect(path=str(CONTENT)))
    inspected = svc.pipeline_inspect(text=CONTENT.read_text(encoding="utf-8"))
    notebook = next(a for a in inspected["activities"] if a["name"] == "Bronze to silver")
    refs = {(r["kind"], r["name"]) for e in notebook["expressions"] for r in e["references"]}
    assert refs == {("parameter", "run_date"), ("variable", "loaded_files")}
    assert svc.pipeline_validate(path=str(FOLDER))["ok"]
    out = svc.pipeline_export(path=str(CONTENT), format="git_folder")
    assert out["ok"] and set(out["files"]) == {"pipeline-content.json"}
    assert svc.pipeline_export(path=str(CONTENT))["text"] == CONTENT.read_text(encoding="utf-8")
    assert svc.pipeline_import()["ok"] is False  # neither text nor path


def test_server_and_audit_know_the_calls():
    names = {"pipeline_import", "pipeline_validate", "pipeline_inspect", "pipeline_export"}
    assert names <= server.METHODS
    assert not any(audit.audited(n) for n in names)  # read-only: nothing is written


def test_registered_for_external_tools():
    from fabric_desktop.integrations.core import CallContext, Caller
    from fabric_desktop.integrations.registry import default_registry
    reg = default_registry()
    for cap_id in ("fabric_desktop_validate_pipeline", "fabric_desktop_inspect_pipeline", "fabric_desktop_export_pipeline"):
        cap = reg.get(cap_id)
        assert cap.read_only and cap.permission == "READ_METADATA" and cap.status == "preview"
        assert "path" not in cap.input_schema["properties"]  # external tools can't name files on this PC
    # Through invoke, the one way external tools come in (schema check, authorizer, timeout).
    result = reg.invoke(PipelineMixin(), "fabric_desktop_validate_pipeline",
                        {"definition": pipeline([act("A", depends=["B"])])}, CallContext(Caller("cl_test", "test"), timeout=5))
    assert result["ok"] is False and result["diagnostics"][0]["code"] == "missing_dependency"


def test_cli(capsys):
    with pytest.raises(SystemExit) as code:
        cli_main(["pipeline", "validate", str(CONTENT)])
    assert code.value.code == 0
    assert "0 errors" in capsys.readouterr().out
    with pytest.raises(SystemExit) as code:
        cli_main(["pipeline", "export", str(CONTENT), "--format", "item_definition"])
    assert code.value.code == 0
    assert loads(capsys.readouterr().out).pipeline.content == load(CONTENT).pipeline.content


def test_cli_validate_fails_on_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(pipeline([act("A", depends=["A"])]))
    with pytest.raises(SystemExit) as code:
        cli_main(["pipeline", "validate", str(bad)])
    assert code.value.code == 1
    assert "self_dependency [A] at $.properties.activities[0].dependsOn[0].activity" in capsys.readouterr().out
