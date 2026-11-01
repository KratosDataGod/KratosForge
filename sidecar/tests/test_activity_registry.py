"""ORCH-002 activity compatibility registry: classifications, overclaim rules, property support,
pipeline reports and the APIs. Everything runs with no network and no Fabric."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from fabric_desktop import audit, server
from fabric_desktop.cli import main as cli_main
from fabric_desktop.integrations.core import CallContext, Caller
from fabric_desktop.integrations.registry import default_registry as capability_registry
from fabric_desktop.orchestration import loads
from fabric_desktop.orchestration.activities import ENTRIES, default_registry
from fabric_desktop.orchestration.registry import (CLASSIFICATIONS, REGISTRY_VERSION, UNKNOWN, ActivityRegistry,
                                                   ActivitySupport, Planned, PropertySupport, StaticAdapter,
                                                   check_entry)
from fabric_desktop.pipeline_service import PipelineMixin

HERE = Path(__file__).parent
FIXTURES = HERE / "pipelines"
EXAMPLE = HERE.parent.parent / "docs" / "orchestration" / "examples" / "orders-daily-compatibility.json"

# Fingerprint of every entry. A change to an entry changes it: bump REGISTRY_VERSION, add a line to
# the changelog in docs/orchestration/activity-registry.md, then record the new fingerprint here.
FINGERPRINTS = {"1": "2949500357d2", "2": "24ea20e1e271", "3": "d5a083b3b021", "4": "d008b0f371dc", "5": "9e1f0c6f7840", "6": "907754475559", "7": "e6891e3c38f8", "8": "0d25c3ae34ce", "9": "9d91e292a147"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the activity registry must not open network connections")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def pipeline(*activities, variables=None):
    props = {"activities": list(activities)}
    if variables is not None:
        props["variables"] = variables
    return json.dumps({"name": "p", "properties": props})


def act(name, typ, **type_props):
    return {"name": name, "type": typ, "dependsOn": [], "typeProperties": type_props}


def report(text):
    return default_registry().compatibility(loads(text))


def problems(text):
    return [d["code"] for d in report(text)["diagnostics"]]


# ---- coverage of the brief's activity list

BRIEF = {
    "Copy Data": "Copy", "Notebook": "TridentNotebook", "Lookup": "Lookup", "Get Metadata": "GetMetadata",
    "Set Variable": "SetVariable", "Append Variable": "AppendVariable", "If Condition": "IfCondition",
    "Switch": "Switch", "ForEach": "ForEach", "Until": "Until", "Wait": "Wait", "Web": "WebActivity",
    "Webhook": "WebHook", "Script": "Script", "Stored Procedure": "SqlServerStoredProcedure",
    "Execute Pipeline": "ExecutePipeline", "Invoke Pipeline": "InvokePipeline", "Fail": "Fail", "Delete": "Delete",
    "Dataflow Gen2": "RefreshDataflow", "Spark Job Definition": "SparkJobDefinition",
    "Semantic model refresh": "PBISemanticModelRefresh", "Outlook": "Office365Outlook", "Teams": "Teams",
    "KQL": "AzureDataExplorerCommand",
}


@pytest.mark.parametrize("label,type_", sorted(BRIEF.items()))
def test_every_activity_in_the_brief_is_classified(label, type_):
    reg = default_registry()
    assert reg.known(type_), label
    assert reg.get(type_).classification in CLASSIFICATIONS


def test_nothing_is_locally_executable_without_an_adapter_and_tests():
    for e in ENTRIES:
        if e.classification in ("local", "simulated"):
            assert e.adapter.can_execute and e.evidence != "none", e.type
    # ORCH-003's control-flow adapters, ORCH-004's Notebook and P1-P2 data activities are the only ones
    # that run here.
    local = {e.type for e in ENTRIES if e.classification in ("local", "simulated")}
    assert local == {"SetVariable", "AppendVariable", "IfCondition", "Switch", "ForEach", "Until", "Wait", "Fail",
                     "Filter", "ExecutePipeline", "InvokePipeline", "TridentNotebook", "Lookup", "GetMetadata", "Script"}


def test_cloud_activities_need_capacity_and_fabric_items_are_cloud():
    reg = default_registry()
    for type_ in ("RefreshDataflow", "SparkJobDefinition", "PBISemanticModelRefresh", "Office365Outlook", "Teams",
                  "AzureDataExplorerCommand"):
        assert reg.get(type_).classification == "cloud"
        assert reg.get(type_).fabric_capacity == "fabric_only"


def test_planned_support_is_separate_from_implemented():
    copy = default_registry().get("Copy").to_json()
    assert copy["classification"] == "static" and copy["implemented"] is True
    assert copy["planned"] == {"classification": "simulated", "stage": "later (not scheduled)", "implemented": False,
                               "note": "Copies between local files and a local lakehouse copy; other connectors stay "
                                       "in Fabric."}


def test_aliases_and_unverified_names():
    reg = default_registry()
    assert reg.get("Notebook") is reg.get("TridentNotebook")
    assert reg.get("TridentNotebook").type_name_verified is False
    assert reg.get("Copy").type_name_verified is True


def test_every_entry_declares_side_effects_and_capacity():
    for e in ENTRIES:
        assert not check_entry(e), e.type
        assert e.side_effects and e.title
    deletes = default_registry().get("Delete")
    assert deletes.side_effects == ("deletes_data",)


# ---- overclaim rules

def entry(**kw):
    base = dict(type="X", title="X", classification="static", evidence="unit_tests", fabric_capacity="metered_in_fabric",
                adapter=StaticAdapter())
    return ActivitySupport(**(base | kw))


class Runner:
    can_execute = True

    def validate(self, activity, ctx):
        return []

    def execute(self, activity, run):
        return None


@pytest.mark.parametrize("kw,needle", [
    ({"classification": "local"}, "adapter that can execute"),
    ({"classification": "simulated", "adapter": Runner(), "evidence": "none"}, "needs tests"),
    ({"classification": "static", "adapter": None}, "adapter that checks"),
    ({"classification": "cloud", "fabric_capacity": "metered_in_fabric"}, "needs Fabric capacity"),
    ({"classification": "local", "adapter": Runner(), "fabric_capacity": "fabric_only"}, "can't be fabric_only"),
    ({"fabric_capacity": "not_required"}, "fabric_capacity must be"),
    ({"planned": Planned("static", "ORCH-003")}, "more capable"),
    ({"planned": Planned("local", "")}, "stage"),
    ({"side_effects": ("launches_rockets",)}, "unknown side effect"),
    ({"properties": {"a": PropertySupport("maybe")}}, "property a"),
    ({"classification": "magic"}, "classification must be"),
    ({"evidence": "vibes"}, "evidence must be"),
])
def test_register_refuses_overclaims(kw, needle):
    with pytest.raises(ValueError, match=needle):
        ActivityRegistry().register(entry(**kw))


def test_local_entry_is_allowed_with_an_executing_adapter_and_tests():
    reg = ActivityRegistry()
    reg.register(entry(type="Local", classification="local", adapter=Runner()))
    assert reg.get("Local").to_json()["can_execute"] is True


def test_duplicate_types_and_aliases_are_refused():
    reg = ActivityRegistry()
    reg.register(entry(type="A", aliases=("B",)))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(entry(type="B"))


def test_registry_version_changes_with_entries():
    blob = json.dumps(default_registry().describe(), sort_keys=True)
    fingerprint = hashlib.sha256(blob.encode()).hexdigest()[:12]
    assert FINGERPRINTS.get(REGISTRY_VERSION) == fingerprint, (
        f"Entries changed (fingerprint {fingerprint}): bump REGISTRY_VERSION, note it in "
        "docs/orchestration/activity-registry.md and record the fingerprint in this test.")


# ---- unknown activities

def test_unknown_activity_never_fails_parsing_and_is_kept():
    text = pipeline(act("Pager", "ContosoPagerNotify", severity="high"), act("W", "Wait", waitTimeInSeconds=1))
    r = report(text)
    row = r["activities"][0]
    assert row["known"] is False and row["classification"] == "unsupported"
    assert row["properties"] == [{"property": "severity", "status": "unknown",
                                  "note": "Not in the registry for this type: carried unchanged, not checked."}]
    assert r["summary"]["unknown_types"] == ["ContosoPagerNotify"]
    assert r["summary"]["overall"] == "unsupported"
    assert "unknown_activity_type" in problems(text)
    assert r["counts"]["error"] == 0
    assert default_registry().get("ContosoPagerNotify") is UNKNOWN


def test_unknown_activity_is_preserved_by_export():
    text = pipeline(act("Pager", "ContosoPagerNotify", severity="high"))
    out = PipelineMixin().pipeline_export(text=text, format="pipeline_content")
    assert out["text"] == text


# ---- property support

def test_unsupported_and_unknown_properties_are_reported():
    text = pipeline(act("C", "Copy", source={}, sink={}, enableStaging=True, translator={}, newThing=1))
    props = {p["property"]: p["status"] for p in report(text)["activities"][0]["properties"]}
    assert props == {"enableStaging": "unsupported_locally", "newThing": "unknown"}
    assert report(text)["activities"][0]["checked_properties"] == ["source", "sink"]


def test_missing_required_property_is_an_error():
    r = report(pipeline(act("N", "TridentNotebook", workspaceId="w")))
    d = [d for d in r["diagnostics"] if d["code"] == "missing_property"][0]
    assert d["path"] == "$.properties.activities[0].typeProperties.notebookId" and d["activity"] == "N"
    assert r["activities"][0]["problems"] == ["missing_property"]


@pytest.mark.parametrize("activity,code", [
    (act("F", "ForEach", items=["a"], activities=[]), "items_not_expression"),
    (act("F", "ForEach", items="@variables('x')", activities=[], batchCount=51), "batch_count_range"),
    (act("F", "ForEach", items="@variables('x')", activities=[], batchCount=4, isSequential=True),
     "batch_count_ignored"),
    (act("W", "Wait", waitTimeInSeconds="ten"), "wait_time"),
    (act("W", "Wait", waitTimeInSeconds=-1), "wait_time"),
    (act("U", "Until", expression="@true", activities=[], timeout="12 hours"), "timeout_format"),
    (act("U", "Until", expression=True, activities=[]), "condition_not_expression"),
    (act("I", "IfCondition", expression="yes"), "condition_not_expression"),
    (act("S", "Switch", on="@variables('x')", cases=[{"value": "a", "activities": []},
                                                      {"value": "a", "activities": []}]), "duplicate_case"),
    (act("S", "Switch", on="@variables('x')", cases=[{"activities": []}]), "case_value"),
    (act("V", "SetVariable", variableName="x"), "missing_property"),
    (act("D", "Delete", datasetSettings={}), "deletes_data"),
])
def test_static_checks(activity, code):
    assert code in problems(pipeline(activity, variables={"x": {"type": "String"}}))


@pytest.mark.parametrize("activity", [
    act("F", "ForEach", items={"value": "@variables('x')", "type": "Expression"}, activities=[], batchCount=20),
    act("W", "Wait", waitTimeInSeconds="@variables('x')"),
    act("U", "Until", expression={"value": "@true", "type": "Expression"}, activities=[], timeout="0.12:00:00"),
    act("S", "Switch", on="@variables('x')", cases=[{"value": "a", "activities": []}]),
])
def test_valid_activities_pass_their_checks(activity):
    assert report(pipeline(activity, variables={"x": {"type": "String"}}))["counts"]["error"] == 0


# ---- pipeline reports

def test_fixture_report():
    r = PipelineMixin().pipeline_compatibility(path=str(FIXTURES / "orders_daily.json"))
    s = r["summary"]
    assert r["ok"] is True and s["activities"] == 18
    assert s["by_classification"] == {"local": 15, "simulated": 0, "static": 2, "cloud": 0, "unsupported": 1}
    assert s["runs_on_this_pc"] is False and s["verified_against_fabric"] == []
    assert {"name": "Copy file to bronze", "classification": "simulated", "stage": "later (not scheduled)"} in \
        s["planned_to_run_on_this_pc"]
    assert "Bronze to silver" not in s["needs_fabric_capacity"]  # a Notebook runs here (ORCH-004)
    nested = {a["name"]: a for a in r["activities"]}
    assert nested["Copy file to bronze"]["path"].endswith("typeProperties.activities[0]")


def test_report_lists_activities_that_need_capacity():
    text = pipeline(act("DF", "RefreshDataflow", dataflowId="d"), act("W", "Wait", waitTimeInSeconds=1))
    s = report(text)["summary"]
    # Wait runs on this PC, so only the Dataflow needs Fabric.
    assert s["needs_fabric_capacity"] == ["DF"] and s["fabric_only"] == ["DF"] and s["overall"] == "cloud"


def test_activities_that_cant_run_here_are_never_reported_free():
    # QA review of #150: Fabric meters every activity run, control flow included, so an activity that can't run
    # on this PC yet must count as needing capacity, and no entry may say it doesn't.
    assert {e.fabric_capacity for e in ENTRIES} <= {"fabric_only", "metered_in_fabric"}
    r = PipelineMixin().pipeline_compatibility(path=str(FIXTURES / "orders_daily.json"))
    s = r["summary"]
    assert s["runs_on_this_pc"] is False
    assert s["activities"] == 18 and len(s["needs_fabric_capacity"]) == 3
    assert "Alert on failure" in s["needs_fabric_capacity"]
    assert any("meters every activity run" in n for n in r["notes"])


def test_adf_fixture_names_databricks_as_cloud():
    r = PipelineMixin().pipeline_compatibility(path=str(FIXTURES / "adf_style.json"))
    assert r["summary"]["fabric_only"] == ["RunDatabricks"]


def test_report_carries_orch_001_diagnostics():
    text = pipeline({"name": "A", "type": "Wait", "dependsOn": [{"activity": "Nope", "dependencyConditions":
                                                                  ["Succeeded"]}],
                     "typeProperties": {"waitTimeInSeconds": 1}})
    r = PipelineMixin().pipeline_compatibility(text=text)
    assert r["ok"] is False and "missing_dependency" in [d["code"] for d in r["diagnostics"]]


def test_unreadable_definition_is_a_result_not_an_exception():
    r = PipelineMixin().pipeline_compatibility(text="{not json")
    assert r["ok"] is False and r["registry_version"] == REGISTRY_VERSION


def test_example_report_in_docs_is_current():
    r = PipelineMixin().pipeline_compatibility(path=str(FIXTURES / "orders_daily.json"))
    assert json.loads(EXAMPLE.read_text(encoding="utf-8")) == r, (
        "Regenerate it: fabric-desktop pipeline compat sidecar/tests/pipelines/orders_daily.json --json")


# ---- APIs

def test_registry_lookup():
    svc = PipelineMixin()
    assert svc.pipeline_activity_registry("Copy")["activity"]["type"] == "Copy"
    unknown = svc.pipeline_activity_registry("Nope")
    assert unknown["known"] is False and unknown["activity"]["classification"] == "unsupported"
    everything = svc.pipeline_activity_registry()
    assert everything["registry_version"] == REGISTRY_VERSION and len(everything["activities"]) == len(ENTRIES)


def test_methods_are_allowlisted_and_read_only():
    assert "pipeline_compatibility" in server.METHODS and "pipeline_compatibility" in audit.READ_ONLY
    # The registry is read in-process by the MCP tool only (HD-050 slice 4).
    assert "pipeline_activity_registry" not in server.METHODS


def test_capabilities():
    reg = capability_registry()
    ctx = CallContext(Caller("cl_test", "test"), timeout=5)
    text = (FIXTURES / "orders_daily.json").read_text(encoding="utf-8")
    out = reg.invoke(PipelineMixin(), "fabric_desktop_check_pipeline_compatibility", {"definition": text}, ctx)
    assert out["summary"]["unknown_types"] == ["ContosoPagerNotify"]
    one = reg.invoke(PipelineMixin(), "fabric_desktop_get_activity_registry", {"activity_type": "Wait"}, ctx)
    assert one["activity"]["classification"] == "local" and one["activity"]["can_execute"] is True
    for cap_id in ("fabric_desktop_check_pipeline_compatibility", "fabric_desktop_get_activity_registry"):
        cap = reg.get(cap_id)
        assert cap.read_only and cap.status == "preview" and cap.permission == "READ_METADATA"


def test_cli_compat(capsys):
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "compat", str(FIXTURES / "orders_daily.json")])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "unsupported Alert on failure [ContosoPagerNotify]" in out
    assert "overall: unsupported; runs on this PC: no; needs Fabric capacity: 3 of 18 activities" in out
    assert "these can't run on this PC yet, and Fabric meters every activity run" in out


def test_cli_activities(capsys):
    with pytest.raises(SystemExit) as done:
        cli_main(["pipeline", "activities", "Delete"])
    assert done.value.code == 0
    assert json.loads(capsys.readouterr().out)["activity"]["side_effects"] == ["deletes_data"]
