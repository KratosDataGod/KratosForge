"""FS-04: known places where a pipeline that passes here fails in Fabric (orchestration/gotchas.py),
one test per gotcha PG1-PG10. They are warnings: none stops a local run. No network, no Fabric."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fabric_desktop.orchestration import load, loads
from fabric_desktop.orchestration.activities import default_registry
from fabric_desktop.cli import main as cli_main
from fabric_desktop.orchestration.gotchas import CODES, FOREACH_LIMITS, SOURCE
from fabric_desktop.pipeline_service import PipelineMixin, PipelineRunMixin

FIXTURES = Path(__file__).parent / "pipelines"
GUID = "22222222-2222-2222-2222-222222222222"
WS = "00000000-0000-0000-0000-000000000000"


def pipeline(*activities):
    return json.dumps({"name": "p", "properties": {"activities": list(activities)}})


def act(name, typ, policy=None, **type_props):
    a = {"name": name, "type": typ, "dependsOn": [], "typeProperties": type_props}
    if policy is not None:
        a["policy"] = policy
    return a


def found(text):
    """{code: [diagnostic, ...]} for the gotcha warnings in a compatibility report."""
    out: dict[str, list[dict]] = {}
    for d in default_registry().compatibility(loads(text))["diagnostics"]:
        if d["code"] in CODES:
            assert d["severity"] == "warning", d
            out.setdefault(d["code"], []).append(d)
    return out


class Svc(PipelineMixin, PipelineRunMixin):
    def __init__(self, root):
        self.root = root


def test_every_gotcha_is_listed_with_its_source():
    described = default_registry().describe()["fabric_differences"]
    assert [g["id"] for g in described] == [f"PG{i}" for i in range(1, 11)] + ["FOREACH"]
    assert all(g["source"] == SOURCE and g["differs"] and g["codes"] for g in described[:10])
    assert "bc42aaf" in SOURCE and described[10]["source"] == FOREACH_LIMITS


def test_pg1_notebook_named_instead_of_its_item_id():
    hit = found(pipeline(act("Transform", "TridentNotebook", notebookId="transform_orders")))
    assert "Transform" == hit["notebook_not_an_item_id"][0]["activity"]
    assert hit["notebook_not_an_item_id"][0]["path"].endswith("typeProperties.notebookId")
    assert not found(pipeline(act("T", "TridentNotebook", notebookId=GUID, workspaceId=WS)))
    assert not found(pipeline(act("T", "TridentNotebook", notebookId="@pipeline().parameters.nb", workspaceId=WS)))


def test_pg2_notebook_timeout_over_twelve_hours():
    hit = found(pipeline(act("Long", "TridentNotebook", policy={"timeout": "1.00:00:00"}, notebookId=GUID,
                             workspaceId=WS)))
    assert "1.00:00:00" in hit["notebook_timeout_over_12_hours"][0]["message"]
    assert not found(pipeline(act("Ok", "TridentNotebook", policy={"timeout": "0.12:00:00"}, notebookId=GUID,
                                  workspaceId=WS)))
    # Only Notebook activities have the 12-hour cap.
    assert not found(pipeline(act("W", "Wait", policy={"timeout": "7.00:00:00"}, waitTimeInSeconds=1)))


def test_pg3_global_parameters():
    hit = found(pipeline(act("S", "SetVariable", variableName="v",
                             value={"value": "@pipeline().globalParameters.region", "type": "Expression"})))
    assert "globalParameters.region" in hit["global_parameters"][0]["message"]
    assert "global_parameter_array_or_object" not in hit
    # Interpolated text counts; prose that only mentions it doesn't.
    assert "global_parameters" in found(pipeline(act("S", "SetVariable", variableName="v",
                                                     value="Region @{pipeline().globalParameters.region}")))
    assert not found(pipeline(act("S", "SetVariable", variableName="v", value="see pipeline().globalParameters.x")))


def test_pg4_dataset_references():
    hit = found((FIXTURES / "adf_style.json").read_text(encoding="utf-8"))
    assert "SqlSales" in hit["dataset_reference"][0]["message"]
    lookup = act("L", "Lookup", source={"type": "AzureSqlSource"},
                 dataset={"referenceName": "Sales", "type": "DatasetReference"})
    assert found(pipeline(lookup))["dataset_reference"][0]["path"].endswith("typeProperties.dataset")


def test_pg5_integration_runtime():
    copy = act("C", "Copy", source={"type": "SqlServerSource"}, sink={"type": "ParquetSink"})
    copy["linkedServiceName"] = {"referenceName": "OnPremSql", "type": "LinkedServiceReference",
                                 "connectVia": {"referenceName": "SelfHosted", "type": "IntegrationRuntimeReference"}}
    hit = found(pipeline(copy))
    assert "'SelfHosted'" in hit["integration_runtime"][0]["message"]
    assert "gateway" in hit["integration_runtime"][0]["message"]


def test_pg6_activities_with_no_fabric_equivalent():
    hit = found(pipeline(act("Check file", "Validation", timeout="0.00:10:00"),
                         act("Ssis", "ExecuteSSISPackage", packageLocation={})))
    assert [d["activity"] for d in hit["no_fabric_equivalent"]] == ["Check file", "Ssis"]
    assert "Get Metadata" in hit["no_fabric_equivalent"][0]["hint"]


def test_pg7_child_pipeline_needs_its_id_and_workspace():
    hit = found((FIXTURES / "adf_style.json").read_text(encoding="utf-8"))
    assert "'Child'" in hit["child_pipeline_by_name"][0]["message"]
    by_id = act("R", "ExecutePipeline", pipeline={"referenceName": GUID, "type": "PipelineReference"}, workspaceId=WS)
    assert not found(pipeline(by_id))
    hit = found(pipeline(act("I", "InvokePipeline", pipelineId=GUID, operationType="InvokeFabricPipeline")))
    assert hit["child_pipeline_no_workspace"][0]["activity"] == "I"
    assert not found(pipeline(act("I", "InvokePipeline", pipelineId=GUID, workspaceId=WS)))


def test_pg8_key_vault_linked_service_and_secret_in_run_history():
    web = act("Call", "WebActivity", method="GET", url="https://api.example.com",
              authentication={"type": "Basic", "username": "u", "password": {
                  "type": "AzureKeyVaultSecret", "secretName": "pw",
                  "store": {"referenceName": "Vault", "type": "LinkedServiceReference"}}})
    assert "key_vault_linked_service" in found(pipeline(web))
    get = act("Get secret", "WebActivity", method="GET", url="https://kv.vault.azure.net/secrets/token?api-version=7.4")
    assert "secret_in_run_history" in found(pipeline(get))
    get["policy"] = {"secureInput": True, "secureOutput": True}
    assert not found(pipeline(get))


def test_pg9_array_or_object_global_parameter():
    hit = found(pipeline(act("S", "SetVariable", variableName="v",
                             value={"value": "@pipeline().globalParameters.regions[0]", "type": "Expression"}),
                         act("T", "SetVariable", variableName="w",
                             value="@pipeline().globalParameters.config.server")))
    assert [d["activity"] for d in hit["global_parameter_array_or_object"]] == ["S", "T"]
    assert "json(pipeline().libraryVariables.regions)" in hit["global_parameter_array_or_object"][0]["hint"]


def test_pg10_item_type_must_be_data_pipeline(tmp_path):
    folder = tmp_path / "pl.DataPipeline"
    shutil.copytree(FIXTURES / "pl_orders_daily.DataPipeline", folder)
    assert not [d for d in default_registry().compatibility(load(folder))["diagnostics"]
                if d["code"] == "item_type_not_data_pipeline"]
    platform = json.loads((folder / ".platform").read_text(encoding="utf-8"))
    platform["metadata"]["type"] = "Pipeline"
    (folder / ".platform").write_text(json.dumps(platform), encoding="utf-8")
    hit = [d for d in default_registry().compatibility(load(folder))["diagnostics"]
           if d["code"] == "item_type_not_data_pipeline"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning" and "'Pipeline'" in hit[0]["message"]


def test_a_gotcha_never_stops_a_local_run(tmp_path):
    text = pipeline(act("Transform", "TridentNotebook", policy={"timeout": "2.00:00:00"}, notebookId="transform"),
                    act("Wait", "Wait", waitTimeInSeconds=0))
    checked = Svc(tmp_path).pipeline_run_check(text=text, mocks={"Transform": {"status": "Succeeded"}})
    assert checked["ok"] is True and checked["problems"] == []
    codes = [d["code"] for d in checked["fabric_differences"]]
    assert codes == ["notebook_not_an_item_id", "notebook_timeout_over_12_hours"]


def test_the_report_row_names_the_gotchas_it_found():
    rows = default_registry().compatibility(loads((FIXTURES / "adf_style.json").read_text(encoding="utf-8")))
    by_name = {r["name"]: r["problems"] for r in rows["activities"]}
    assert "global_parameters" in by_name["RunDatabricks"] and "child_pipeline_by_name" in by_name["RunChild"]


def test_the_cli_says_what_differs_in_fabric(capsys, tmp_path):
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "compat", str(FIXTURES / "adf_style.json")])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "in Fabric: RunChild: names its child pipeline by 'Child'" in out
    mocks = tmp_path / "mocks.json"
    mocks.write_text(json.dumps({"Transform": {"status": "Succeeded"}}), encoding="utf-8")
    text = tmp_path / "p.json"
    text.write_text(pipeline(act("Transform", "TridentNotebook", notebookId="transform")), encoding="utf-8")
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "run", str(text), "--mocks", str(mocks)])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "in Fabric: Transform: notebookId 'transform' isn't a Fabric item id" in out and "succeeded" in out


# ---- ORCH-H2 (#1227): Data Factory's ForEach limits

def foreach(name, inner, sequential=None):
    tp = {"items": "@range(1, 3)", "activities": inner}
    if sequential is not None:
        tp["isSequential"] = sequential
    return act(name, "ForEach", **tp)


def setvar(name):
    return act(name, "SetVariable", variableName="v", value="@string(item())")


def test_a_foreach_inside_a_foreach_or_until_is_warned_about():
    wait = act("W", "Wait", waitTimeInSeconds=0)
    hit = found(pipeline(foreach("Outer", [foreach("Inner", [wait])])))
    assert [d["activity"] for d in hit["nested_foreach"]] == ["Inner"] and "child pipeline" in \
        hit["nested_foreach"][0]["hint"]
    until = act("U", "Until", expression="@true", activities=[foreach("InUntil", [wait])])
    assert found(pipeline(until))["nested_foreach"][0]["activity"] == "InUntil"
    # Through an If Condition or Switch branch, still inside the loop.
    branch = act("If", "IfCondition", expression="@true", ifTrueActivities=[foreach("Deep", [wait])])
    assert found(pipeline(foreach("Outer", [branch])))["nested_foreach"][0]["activity"] == "Deep"
    case = act("Sw", "Switch", on="@item()", cases=[{"value": "1", "activities": [foreach("InCase", [wait])]}])
    assert found(pipeline(foreach("Outer", [case])))["nested_foreach"][0]["activity"] == "InCase"
    # A ForEach inside an If Condition alone, or two ForEach side by side, are fine.
    side = pipeline(act("If", "IfCondition", expression="@true", ifTrueActivities=[foreach("A", [wait])]),
                    foreach("B", [act("W2", "Wait", waitTimeInSeconds=0)]))
    assert "nested_foreach" not in found(side)


def test_set_variable_inside_a_parallel_foreach_is_warned_about():
    hit = found(pipeline(foreach("Loop", [setvar("S")])))
    assert hit["set_variable_in_parallel_foreach"][0]["activity"] == "S"
    assert "Append Variable" in hit["set_variable_in_parallel_foreach"][0]["hint"]
    branch = act("If", "IfCondition", expression="@true", ifTrueActivities=[setvar("Deep")])
    assert found(pipeline(foreach("Loop", [branch], sequential=False)))["set_variable_in_parallel_foreach"]
    assert "set_variable_in_parallel_foreach" not in found(pipeline(foreach("Loop", [setvar("S")], sequential=True)))
    assert "set_variable_in_parallel_foreach" not in found(pipeline(setvar("Top")))


def test_foreach_limits_never_stop_a_local_run(tmp_path):
    text = json.dumps({"name": "p", "properties": {"variables": {"v": {"type": "String"}}, "activities": [
        foreach("Outer", [foreach("Inner", [setvar("S")])])]}})
    checked = Svc(tmp_path).pipeline_run_check(text=text)
    assert checked["ok"] is True
    assert {d["code"] for d in checked["fabric_differences"]} == {"nested_foreach", "set_variable_in_parallel_foreach"}
