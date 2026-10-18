"""The activity types Fabric Desktop by KratosBI knows (ORCH-002), as ``default_registry()``.

Control-flow activities and child pipelines are ``local`` (ORCH-003): adapters.py runs them, with
tests in tests/test_pipeline_runs.py. Data and compute activities are ``static`` (checked here:
required properties and a few documented limits; a local run needs a mock for them), and Fabric
items are ``cloud`` (only Fabric can run them). Data Factory type names are in Microsoft's documentation;
names seen only in Fabric are marked ``type_name_verified=False`` until a pipeline exported from
a tenant confirms them. Change ``REGISTRY_VERSION`` and the changelog in
docs/orchestration/activity-registry.md with every change here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from . import adapters as A
from .definition import Activity
from .registry import (ActivityRegistry, ActivitySupport, CheckContext, Planned, PropertySupport as P,
                       StaticAdapter)
from .validate import Diagnostic

LATER = "later (not scheduled)"

CHECKED = P("checked", "Checked on this PC.")
CARRIED = P("carried")


def _is_expression(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("type") == "Expression"
    return isinstance(value, str) and value.startswith("@") and not value.startswith("@@")


def _diag(a: Activity, code: str, severity: str, message: str, key: str, hint: str = "") -> Diagnostic:
    return Diagnostic(code, severity, message, f"{a.path}.typeProperties.{key}", a.name, hint)


# ---- checks for documented limits (each has a test in tests/test_activity_registry.py)

def _foreach(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    tp, out = a.type_properties, []
    if "items" in tp and not _is_expression(tp["items"]):
        out.append(_diag(a, "items_not_expression", "error", f"{a.name}: ForEach items must be an expression "
                         "that returns an array.", "items", "Write it as @pipeline().parameters.files or similar."))
    batch = tp.get("batchCount")
    if batch is not None and not _is_expression(batch) and (not isinstance(batch, int) or not 1 <= batch <= 50):
        out.append(_diag(a, "batch_count_range", "error", f"{a.name}: batchCount must be between 1 and 50.",
                         "batchCount", "Data Factory runs at most 50 iterations at once."))
    if batch is not None and tp.get("isSequential") is True:
        out.append(_diag(a, "batch_count_ignored", "info", f"{a.name}: batchCount has no effect when isSequential "
                         "is true.", "batchCount"))
    return out


def _wait(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    secs = a.type_properties.get("waitTimeInSeconds")
    if secs is None or _is_expression(secs) or (isinstance(secs, int) and not isinstance(secs, bool) and secs >= 0):
        return []
    return [_diag(a, "wait_time", "error", f"{a.name}: waitTimeInSeconds must be a whole number of seconds.",
                  "waitTimeInSeconds")]


_TIMESPAN = re.compile(r"^(\d+\.)?\d{1,2}:\d{2}:\d{2}$")


def _until(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    tp, out = a.type_properties, []
    if "expression" in tp and not _is_expression(tp["expression"]):
        out.append(_diag(a, "condition_not_expression", "error", f"{a.name}: the Until condition must be an "
                         "expression.", "expression"))
    t = tp.get("timeout")
    if t is not None and not _is_expression(t) and not (isinstance(t, str) and _TIMESPAN.match(t)):
        out.append(_diag(a, "timeout_format", "error", f"{a.name}: timeout must look like d.hh:mm:ss.", "timeout",
                         "For example 0.12:00:00 for twelve hours."))
    return out


def _if(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    if "expression" in a.type_properties and not _is_expression(a.type_properties["expression"]):
        return [_diag(a, "condition_not_expression", "error", f"{a.name}: the If Condition must be an expression "
                      "that returns true or false.", "expression")]
    return []


def _switch(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    seen: set[str] = set()
    out = []
    for i, case in enumerate(a.type_properties.get("cases") or []):
        value = case.get("value") if isinstance(case, dict) else None
        if not isinstance(value, str):
            out.append(_diag(a, "case_value", "error", f"{a.name}: case {i + 1} needs a text value.", f"cases[{i}]"))
        elif value in seen:
            out.append(_diag(a, "duplicate_case", "error", f"{a.name}: case {value!r} appears twice.",
                             f"cases[{i}]"))
        else:
            seen.add(value)
    return out


def _variable(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    tp = a.type_properties
    if "value" not in tp:
        return [_diag(a, "missing_property", "error", f"{a.name} ({a.type}) needs typeProperties.value.", "value")]
    return []


def _delete(a: Activity, ctx: CheckContext) -> list[Diagnostic]:
    return [_diag(a, "deletes_data", "info", f"{a.name} deletes files when it runs in Fabric.",
                  "datasetSettings", "Fabric Desktop by KratosBI will never run it outside an isolated test location.")]


# ---- entries

def _static(type_: str, title: str, required: tuple[str, ...] = (), check=None, **kw: Any) -> ActivitySupport:
    props = {k: CHECKED for k in required} | kw.pop("properties", {})
    return ActivitySupport(type_, title, "static", "unit_tests", adapter=StaticAdapter(required, check),
                           properties=props, **kw)


def _local(type_: str, title: str, adapter: type, required: tuple[str, ...] = (), check=None,
           **kw: Any) -> ActivitySupport:
    props = {k: CHECKED for k in required} | kw.pop("properties", {})
    kw.setdefault("fabric_capacity", "metered_in_fabric")
    kw.setdefault("side_effects", ("pipeline_state",))
    return ActivitySupport(type_, title, "local", "unit_tests", adapter=adapter(required, check), properties=props,
                           **kw)


CHILD_LIMIT = ("In Fabric this starts the child pipeline's own run, which uses capacity. On this PC it runs the "
               "child's definition when the run is given it (by referenceName or pipelineId), and waits for it "
               "even when waitOnCompletion is false.")


def _cloud(type_: str, title: str, required: tuple[str, ...] = (), **kw: Any) -> ActivitySupport:
    props = {k: CHECKED for k in required} | kw.pop("properties", {})
    return ActivitySupport(type_, title, "cloud", "unit_tests", "fabric_only", adapter=StaticAdapter(required),
                           properties=props, **kw)


CONTROL = P("checked", "Its child activities are checked like any other.")

ENTRIES: tuple[ActivitySupport, ...] = (
    # Control flow: no connection, no side effect outside the pipeline run. ORCH-003 runs these here.
    _local("SetVariable", "Set Variable", A.SetVariable, ("variableName",), _variable,
           properties={"value": CHECKED, "setSystemVariable": P("checked", "Sets the pipeline return value.")}),
    _local("AppendVariable", "Append Variable", A.AppendVariable, ("variableName",), _variable,
           properties={"value": CHECKED}),
    _local("IfCondition", "If Condition", A.IfCondition, ("expression",), _if, container=True,
           properties={"ifTrueActivities": CONTROL, "ifFalseActivities": CONTROL}),
    _local("Switch", "Switch", A.Switch, ("on",), _switch, container=True,
           properties={"cases": CHECKED, "defaultActivities": CONTROL},
           limitations=("Case values are compared exactly, including upper and lower case; not compared with "
                        "Fabric.",)),
    _local("ForEach", "ForEach", A.ForEach, ("items", "activities"), _foreach, container=True,
           properties={"activities": CONTROL, "batchCount": CHECKED, "isSequential": CHECKED},
           limitations=("Iterations run in parallel up to batchCount (20 when it is left out), as Data Factory "
                        "documents.",)),
    _local("Until", "Until", A.Until, ("expression", "activities"), _until, container=True,
           properties={"activities": CONTROL, "timeout": CHECKED},
           limitations=("Stops after 10,000 iterations as well as at its timeout (7 days when left out).",)),
    _local("Wait", "Wait", A.Wait, ("waitTimeInSeconds",), _wait, side_effects=("none",),
           limitations=("A local run can shorten every wait with wait_scale, so tests don't wait.",)),
    _local("Fail", "Fail", A.Fail, ("message", "errorCode")),
    _local("Filter", "Filter", A.Filter, ("items", "condition")),
    # Data movement and compute: read or write data through connections.
    _static("Copy", "Copy data", ("source", "sink"), fabric_capacity="metered_in_fabric", auth=("fabric", "connection"),
            side_effects=("reads_external", "reads_onelake", "writes_onelake", "writes_external"),
            external_dependencies=("source and sink connections",),
            planned=Planned("simulated", LATER, "Copies between local files and a local lakehouse copy; "
                            "other connectors stay in Fabric."),
            properties={"translator": CARRIED, "parallelCopies": CARRIED, "enableStaging":
                        P("unsupported_locally", "Staging is a Fabric or Azure service feature."),
                        "stagingSettings": P("unsupported_locally", "Staging is a Fabric or Azure service feature."),
                        "dataIntegrationUnits": P("unsupported_locally", "Fabric compute sizing; no meaning here."),
                        "enableSkipIncompatibleRow": CARRIED, "logSettings": CARRIED},
            limitations=("Source and sink settings are carried, not checked against each connector's schema.",)),
    _static("TridentNotebook", "Notebook", ("notebookId",), aliases=("Notebook",), type_name_verified=False,
            fabric_capacity="metered_in_fabric", auth=("fabric", "workspace_role"),
            side_effects=("runs_code", "reads_onelake", "writes_onelake"),
            external_dependencies=("the notebook item", "its default lakehouse"),
            planned=Planned("local", LATER, "Runs a local copy through Prove's notebook execution path, once it "
                            "can be called with parameters."),
            properties={"workspaceId": CARRIED, "parameters": CARRIED,
                        "sessionTag": P("unsupported_locally", "High-concurrency session sharing is a Fabric Spark "
                                        "feature."),
                        "configuration": P("unsupported_locally", "Spark pool settings apply to Fabric only.")},
            limitations=("Running on this PC uses a local Python kernel, not Fabric Spark; results can differ.",)),
    _cloud("SparkJobDefinition", "Spark job definition", ("sparkJobDefinitionId",), type_name_verified=False,
           auth=("fabric", "workspace_role"), side_effects=("runs_code", "starts_fabric_job", "writes_onelake"),
           external_dependencies=("the Spark job definition item",),
           limitations=("Runs only on Fabric Spark.",)),
    _static("Lookup", "Lookup", ("source",), fabric_capacity="metered_in_fabric", auth=("fabric", "connection"),
            side_effects=("reads_external", "reads_onelake"), external_dependencies=("source connection",),
            planned=Planned("simulated", LATER, "Returns rows from a fixture or a local table."),
            properties={"firstRowOnly": CARRIED, "datasetSettings": CARRIED},
            limitations=("Fabric returns at most 5,000 rows or 4 MB; not checked here.",)),
    _static("GetMetadata", "Get Metadata", ("fieldList",), fabric_capacity="metered_in_fabric", auth=("fabric", "connection"),
            side_effects=("reads_external", "reads_onelake"), external_dependencies=("dataset connection",),
            planned=Planned("simulated", LATER, "Reads metadata of local files."),
            properties={"datasetSettings": CARRIED, "storeSettings": CARRIED, "formatSettings": CARRIED}),
    _static("Script", "Script", ("scripts",), fabric_capacity="metered_in_fabric", auth=("connection",),
            side_effects=("runs_code", "reads_external", "writes_external"),
            external_dependencies=("database connection",),
            planned=Planned("simulated", LATER, "Runs against a local DuckDB copy only, never the real database."),
            properties={"scriptBlockExecutionTimeout": CARRIED, "logSettings": CARRIED},
            limitations=("SQL is carried as written; its dialect is the target database's.",)),
    _static("SqlServerStoredProcedure", "Stored procedure", ("storedProcedureName",), fabric_capacity="metered_in_fabric",
            auth=("connection",), side_effects=("runs_code", "writes_external"),
            external_dependencies=("database connection", "the stored procedure"),
            properties={"storedProcedureParameters": CARRIED},
            limitations=("Runs in the target database; Fabric Desktop by KratosBI has no local stand-in planned.",)),
    _static("Delete", "Delete data", ("datasetSettings",), _delete, fabric_capacity="metered_in_fabric",
            auth=("fabric", "connection"), side_effects=("deletes_data",), external_dependencies=("dataset connection",),
            planned=Planned("simulated", LATER, "Lists what would be deleted; deletes only in an isolated test "
                            "location."),
            properties={"storeSettings": CARRIED, "enableLogging": CARRIED, "logStorageSettings": CARRIED}),
    _local("InvokePipeline", "Invoke pipeline", A.ExecutePipeline, ("pipelineId",), type_name_verified=False,
           fabric_capacity="metered_in_fabric", external_dependencies=("the child pipeline's definition",), container=True,
           properties={"workspaceId": CARRIED, "parameters": CHECKED, "waitOnCompletion": CARRIED,
                       "operationType": CARRIED},
           limitations=(CHILD_LIMIT,)),
    _local("ExecutePipeline", "Execute pipeline (Data Factory)", A.ExecutePipeline, ("pipeline",),
           fabric_capacity="metered_in_fabric", external_dependencies=("the child pipeline's definition",), container=True,
           properties={"parameters": CHECKED, "waitOnCompletion": CARRIED}, limitations=(CHILD_LIMIT,)),
    _static("WebActivity", "Web", ("method",), fabric_capacity="metered_in_fabric", auth=("connection",),
            side_effects=("calls_http",), external_dependencies=("the web endpoint",),
            planned=Planned("simulated", LATER, "Returns a recorded response; calls a real URL only when allowed."),
            properties={"url": CARRIED, "relativeUrl": CARRIED, "headers": CARRIED, "body": CARRIED,
                        "authentication": CARRIED},
            limitations=("A real call can change data in the called service; ORCH-003 will not make it.",)),
    _static("WebHook", "Webhook", ("method",), fabric_capacity="metered_in_fabric", auth=("connection",),
            side_effects=("calls_http",), external_dependencies=("the web endpoint", "its callback"),
            planned=Planned("simulated", LATER, "Simulated callback."),
            properties={"url": CARRIED, "relativeUrl": CARRIED, "headers": CARRIED, "body": CARRIED,
                        "timeout": CARRIED}),

    # Fabric items and services: only Fabric (or the service) can run them.
    _cloud("RefreshDataflow", "Dataflow Gen2", ("dataflowId",), type_name_verified=False,
           auth=("fabric", "workspace_role"), side_effects=("starts_fabric_job", "writes_onelake"),
           external_dependencies=("the dataflow item",), properties={"workspaceId": CARRIED, "notifyOption": CARRIED},
           limitations=("Dataflow Gen2 runs on Fabric's mashup engine.",)),
    _cloud("PBISemanticModelRefresh", "Semantic model refresh", type_name_verified=False,
           auth=("fabric", "workspace_role"), side_effects=("starts_fabric_job",),
           external_dependencies=("the semantic model",)),
    _cloud("Office365Outlook", "Office 365 Outlook", type_name_verified=False, auth=("connection",),
           side_effects=("sends_message",), external_dependencies=("Outlook connection",),
           limitations=("Sends real email; never run on this PC.",)),
    _cloud("Teams", "Teams", type_name_verified=False, auth=("connection",), side_effects=("sends_message",),
           external_dependencies=("Teams connection",), limitations=("Posts a real message; never run on this PC.",)),
    _cloud("AzureDataExplorerCommand", "KQL", type_name_verified=False, auth=("connection",),
           side_effects=("runs_code", "writes_external"), external_dependencies=("KQL database",)),
    _cloud("DatabricksNotebook", "Azure Databricks notebook", auth=("connection",),
           side_effects=("runs_code", "writes_external"), external_dependencies=("Databricks workspace",)),
    _cloud("AzureFunctionActivity", "Azure Function", auth=("connection",), side_effects=("runs_code", "calls_http"),
           external_dependencies=("the function app",)),
)


@lru_cache(maxsize=1)
def default_registry() -> ActivityRegistry:
    reg = ActivityRegistry()
    for entry in ENTRIES:
        reg.register(entry)
    return reg
