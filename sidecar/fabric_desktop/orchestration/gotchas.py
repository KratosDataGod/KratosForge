"""Known differences between a pipeline that passes here and one Fabric accepts (FS-04).

A local run can pass while the same definition fails in Fabric: a notebook found by name here
that Fabric can only find by item id, a timeout Fabric caps, an expression Fabric can't read.
Each ``Gotcha`` checks a definition for one such case and reports a warning, never an error, so
it never stops a local run. The list follows Microsoft's pipeline-migration gotchas PG1-PG10
(SOURCE, MIT licensed), written here in our own words, plus the activity types that guide says
have no Fabric equivalent, and Data Factory's documented ForEach limits (FOREACH_LIMITS, ORCH-H2).
The registry runs these in ``compatibility`` and lists them in
``describe``, so a change here changes REGISTRY_VERSION like any entry change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .adapters import timespan
from .definition import CHILD_LISTS, Activity, ImportedPipeline
from .validate import Diagnostic

SOURCE = ("https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/pipeline-migration/resources/"
          "pipeline-gotchas.md")
FOREACH_LIMITS = ("https://learn.microsoft.com/en-us/azure/data-factory/control-flow-for-each-activity"
                  "#limitations-and-workarounds")

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_GLOBAL = re.compile(r"pipeline\(\)\s*\.\s*globalParameters\s*\.\s*(\w+)(\s*[\[.])?")
NOTEBOOK_TYPES = ("TridentNotebook", "Notebook")
NOTEBOOK_MAX_SECONDS = 12 * 3600
# Data Factory and Synapse activity types Fabric has no activity for, with what to use instead.
NO_FABRIC_EQUIVALENT = {
    "Validation": "Use Get Metadata to check the file or folder and an If Condition on its output.",
    "ExecuteSSISPackage": "Fabric has no SSIS runtime. Keep the package in Azure Data Factory and start it with a "
                          "Web activity, or rewrite it as a notebook or dataflow.",
    "Custom": "Fabric has no Azure Batch activity. Start the Batch job with a Web activity, or rewrite it as a "
              "notebook.",
    "HDInsightHive": "Fabric has no HDInsight activities. Rewrite the Hive query as Spark SQL in a notebook or as "
                     "T-SQL in a warehouse.",
    "HDInsightPig": "Fabric has no HDInsight activities. Rewrite the script as PySpark in a notebook.",
    "HDInsightMapReduce": "Fabric has no HDInsight activities. Rewrite the job as PySpark in a notebook.",
}


def _is_expression_text(value: Any) -> bool:
    return isinstance(value, str) and (value.startswith("@") and not value.startswith("@@") or "@{" in value)


def _values(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    """Every value under ``value`` with its path, not going into child activity lists (they are
    checked as activities of their own)."""
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            if k in CHILD_LISTS and isinstance(v, list):
                continue
            yield from _values(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _values(v, f"{path}[{i}]")


def _expressions(a: Activity) -> Iterator[tuple[str, str]]:
    for path, v in _values(a.type_properties, f"{a.path}.typeProperties"):
        if isinstance(v, dict) and v.get("type") == "Expression" and isinstance(v.get("value"), str):
            yield f"{path}.value", v["value"]
        elif _is_expression_text(v):
            yield path, v


def _refs(a: Activity, kind: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every {"type": kind, ...} reference the activity holds, outside its child activities."""
    for path, v in _values({k: v for k, v in a.raw.items() if k not in CHILD_LISTS}, a.path):
        if isinstance(v, dict) and v.get("type") == kind:
            yield path, v


def _warn(a: Activity, code: str, message: str, path: str, hint: str) -> Diagnostic:
    return Diagnostic(code, "warning", message, path, a.name, hint)


# ---- the checks, one per gotcha (each has tests in tests/test_pipeline_gotchas.py)

def _notebook_by_name(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    nb = a.type_properties.get("notebookId")
    if a.type not in NOTEBOOK_TYPES or not isinstance(nb, str) or not nb or _is_expression_text(nb) or _GUID.match(nb):
        return []
    return [_warn(a, "notebook_not_an_item_id", f"{a.name}: notebookId {nb!r} isn't a Fabric item id. A local run "
                  "finds the notebook by the name you give it, but Fabric looks it up by id in the workspace, so this "
                  "fails there.", f"{a.path}.typeProperties.notebookId",
                  "Put the notebook in the Fabric workspace before the pipeline, then use its item id here.")]


def _notebook_timeout(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    raw_policy = a.raw.get("policy")
    policy: dict[str, Any] = raw_policy if isinstance(raw_policy, dict) else {}
    secs = timespan(policy.get("timeout"))
    if a.type not in NOTEBOOK_TYPES or secs is None or secs <= NOTEBOOK_MAX_SECONDS:
        return []
    return [_warn(a, "notebook_timeout_over_12_hours", f"{a.name}: its timeout is {policy['timeout']}, but Fabric "
                  "allows a Notebook activity 12 hours at most. A local run uses the timeout as written.",
                  f"{a.path}.policy.timeout", "Use 0.12:00:00 or less. For longer work use a Spark job definition, or "
                  "split the notebook into steps.")]


def _global_parameters(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for path, text in _expressions(a):
        found = list(_GLOBAL.finditer(text))
        if not found:
            continue
        out.append(_warn(a, "global_parameters", f"{a.name}: uses pipeline().globalParameters.{found[0].group(1)}. "
                         "Fabric pipelines have no global parameters, so the value is missing there, and a local run "
                         "can't read it either.", path, "Put the value in a variable library and read it as "
                         "pipeline().libraryVariables.<name>."))
        nested = next((m for m in found if m.group(2)), None)
        if nested is not None:
            name = nested.group(1)
            out.append(_warn(a, "global_parameter_array_or_object", f"{a.name}: reads into global parameter {name} "
                             "as an array or object. A variable library only holds text, numbers and true or "
                             "false, so the value has to be kept as JSON text.", path,
                             f"Read it as json(pipeline().libraryVariables.{name}) before indexing into it."))
        break   # one of each per activity is enough to act on
    return out


def _datasets(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    refs = list(_refs(a, "DatasetReference"))
    if not refs:
        return []
    path, ref = refs[0]
    return [_warn(a, "dataset_reference", f"{a.name}: refers to dataset {ref.get('referenceName')!r}. Fabric has no "
                  "dataset items, so the reference can't be found there.", path,
                  "Move the dataset's settings into the activity (datasetSettings, or source and sink).")]


def _integration_runtime(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    refs = list(_refs(a, "IntegrationRuntimeReference"))
    if not refs:
        return []
    path, ref = refs[0]
    return [_warn(a, "integration_runtime", f"{a.name}: connects through integration runtime "
                  f"{ref.get('referenceName')!r}. Fabric has no integration runtimes; data on your own network is "
                  "reached through an on-premises data gateway connection instead.", path,
                  "Set up the gateway and a Fabric connection that uses it before the pipeline runs in Fabric.")]


def _no_fabric_equivalent(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    advice = NO_FABRIC_EQUIVALENT.get(a.type)
    if advice is None:
        return []
    return [_warn(a, "no_fabric_equivalent", f"{a.name}: Fabric has no {a.type} activity, so it can't run there.",
                  f"{a.path}.type", advice)]


def _child_pipeline(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    tp = a.type_properties
    if a.type == "ExecutePipeline":
        raw_ref = tp.get("pipeline")
        ref: dict[str, Any] = raw_ref if isinstance(raw_ref, dict) else {}
        name = ref.get("referenceName")
        if isinstance(name, str) and _GUID.match(name) and isinstance(tp.get("workspaceId"), str):
            return []
        label = repr(name) if name else "nothing"
        return [_warn(a, "child_pipeline_by_name", f"{a.name}: names its child pipeline by {label}. A local run finds the child by the name you give it, but "
                      "Fabric needs the child's item id and its workspaceId, even in the same workspace.",
                      f"{a.path}.typeProperties.pipeline", "Use Invoke pipeline with the child's pipelineId and "
                      "workspaceId once the child is in Fabric.")]
    if a.type == "InvokePipeline" and not tp.get("workspaceId"):
        return [_warn(a, "child_pipeline_no_workspace", f"{a.name}: has no workspaceId. Fabric needs it to find the "
                      "child pipeline, even in the same workspace; a local run doesn't.",
                      f"{a.path}.typeProperties.workspaceId", "Add the child pipeline's workspace id.")]
    return []


def _key_vault(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    refs = list(_refs(a, "AzureKeyVaultSecret"))
    linked = next(((p, t) for p, t in _expressions(a) if "linkedService()" in t.replace(" ", "")), None)
    if refs or linked:
        path = refs[0][0] if refs else linked[0] if linked else a.path
        out.append(_warn(a, "key_vault_linked_service", f"{a.name}: reads a secret through a Key Vault linked "
                         "service. Fabric has no linked services, so the secret can't be read that way there.", path,
                         "Read the secret with a Web activity that calls Key Vault, with secure input and output on."))
    url = a.type_properties.get("url")
    url = url.get("value") if isinstance(url, dict) else url
    raw_policy = a.raw.get("policy")
    policy: dict[str, Any] = raw_policy if isinstance(raw_policy, dict) else {}
    if a.type == "WebActivity" and isinstance(url, str) and "vault.azure.net" in url.lower() \
            and policy.get("secureOutput") is not True:
        out.append(_warn(a, "secret_in_run_history", f"{a.name}: calls Key Vault without secure output. Fabric keeps "
                         "each activity's output in its run history, and so does a local run, so the secret would be "
                         "shown there.", f"{a.path}.policy",
                         "Set policy.secureOutput to true, and secureInput on the activities that use the secret."))
    return out


def _containers(a: Activity, imp: ImportedPipeline) -> list[Activity]:
    """The activities ``a`` is inside, innermost first."""
    out: list[Activity] = []
    name = a.container
    while name is not None and len(out) < 100:
        parent = imp.pipeline.find(name)
        if parent is None:
            break
        out.append(parent)
        name = parent.container
    return out


def _foreach_limits(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    """Data Factory's documented ForEach limits. The Fabric ForEach page doesn't state them, so the
    warnings say they aren't verified in Fabric yet."""
    if a.type not in ("ForEach", "SetVariable"):
        return []
    outer = _containers(a, imp)
    if a.type == "ForEach":
        loop = next((c for c in outer if c.type in ("ForEach", "Until")), None)
        if loop is None:
            return []
        return [_warn(a, "nested_foreach", f"{a.name}: is a ForEach inside {loop.type} {loop.name}. Data Factory "
                      "doesn't allow a ForEach inside another ForEach or an Until (not yet verified in Fabric); a "
                      "local run allows it.", f"{a.path}.type",
                      "Move the inner loop into a child pipeline and call it with Invoke pipeline.")]
    parallel = next((c for c in outer if c.type == "ForEach" and c.type_properties.get("isSequential") is not True),
                    None)
    if parallel is None:
        return []
    return [_warn(a, "set_variable_in_parallel_foreach", f"{a.name}: sets a variable inside ForEach {parallel.name}, "
                  "which runs its iterations in parallel. Pipeline variables are shared by every iteration, so Data "
                  "Factory doesn't allow it (not yet verified in Fabric); here, as there, the last iteration to "
                  "finish wins.", f"{a.path}.typeProperties.variableName",
                  "Make the ForEach sequential, use Append Variable, or move the iteration into a child pipeline.")]


@dataclass(frozen=True)
class Gotcha:
    id: str          # Microsoft's number for it, PG1-PG10 (PG6 also covers the other types with no equivalent),
                     # or FOREACH for Data Factory's ForEach limits
    title: str
    codes: tuple[str, ...]
    differs: str     # what differs between a local run and Fabric, in a sentence
    check: Callable[[Activity, ImportedPipeline], list[Diagnostic]] | None = None
    source: str = SOURCE

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "codes": list(self.codes), "differs": self.differs,
                "source": self.source}


GOTCHAS: tuple[Gotcha, ...] = (
    Gotcha("PG1", "Notebooks must be in Fabric before the pipeline", ("notebook_not_an_item_id",),
           "A local run finds a notebook by name; Fabric finds it by item id in the workspace.", _notebook_by_name),
    Gotcha("PG2", "Notebook activities stop at 12 hours", ("notebook_timeout_over_12_hours",),
           "A local run uses a longer timeout as written; Fabric allows 12 hours at most.", _notebook_timeout),
    Gotcha("PG3", "No global parameters", ("global_parameters",),
           "Fabric has no pipeline().globalParameters; it uses a variable library.", _global_parameters),
    Gotcha("PG4", "No dataset items", ("dataset_reference",),
           "Fabric has no datasets; their settings go in the activity.", _datasets),
    Gotcha("PG5", "Integration runtimes become gateways", ("integration_runtime",),
           "Fabric reaches data on your own network through an on-premises data gateway, not an integration runtime.",
           _integration_runtime),
    Gotcha("PG6", "Activities with no Fabric equivalent", ("no_fabric_equivalent",),
           "Validation, SSIS, Azure Batch and HDInsight activities can't run in Fabric.", _no_fabric_equivalent),
    Gotcha("PG7", "Child pipelines need their id and workspaceId", ("child_pipeline_by_name",
                                                                     "child_pipeline_no_workspace"),
           "A local run finds a child pipeline by name; Fabric needs its item id and workspaceId.", _child_pipeline),
    Gotcha("PG8", "No Key Vault linked service", ("key_vault_linked_service", "secret_in_run_history"),
           "Fabric has no linked services; a secret read with a Web activity shows in run history unless its output "
           "is secure.", _key_vault),
    Gotcha("PG9", "Array and object global parameters", ("global_parameter_array_or_object",),
           "A variable library holds only text, numbers and true or false; arrays and objects are kept as JSON text. "
           "Checked with PG3."),
    Gotcha("PG10", "The item type is DataPipeline", ("item_type_not_data_pipeline",),
           "Fabric only accepts a pipeline as a DataPipeline item."),
    Gotcha("FOREACH", "ForEach limits", ("nested_foreach", "set_variable_in_parallel_foreach"),
           "Data Factory doesn't allow a ForEach inside a ForEach or Until, or Set Variable inside a parallel "
           "ForEach; a local run allows both.", _foreach_limits, FOREACH_LIMITS),
)

CODES = frozenset(c for g in GOTCHAS for c in g.codes)


def for_item(imp: ImportedPipeline) -> list[Diagnostic]:
    """PG10: the item's own type, from its .platform part when it has one."""
    meta = (imp.platform or {}).get("metadata")
    kind = meta.get("type") if isinstance(meta, dict) else None
    if kind is None or kind == "DataPipeline":
        return []
    return [Diagnostic("item_type_not_data_pipeline", "warning", f"The .platform part says this item is a {kind!r}. "
                       "Fabric only accepts a pipeline as a DataPipeline item, so it would refuse it.",
                       "$.platform.metadata.type", None, 'Set "type": "DataPipeline" in the .platform part.')]


def for_activity(a: Activity, imp: ImportedPipeline) -> list[Diagnostic]:
    """Every gotcha one activity has, as warnings. Reads the definition only."""
    return [d for g in GOTCHAS if g.check is not None for d in g.check(a, imp)]


def check(imp: ImportedPipeline) -> list[dict[str, Any]]:
    """Every gotcha in the pipeline, as warnings (Diagnostic JSON). Reads the definition only."""
    return [d.to_json() for d in for_item(imp) + [d for a in imp.pipeline.all_activities() for d in for_activity(a, imp)]]
