# Pipeline activity compatibility (Preview)

KratosForge keeps a versioned registry of Fabric Data Factory pipeline activity types. For each type it says what this version can do with it on your PC, what is only planned, which properties it can't use, what running it needs and changes, and whether it can only ever run in Fabric. Fabric meters every activity run, control flow included, so any activity that can't run on your PC uses capacity when the pipeline runs in Fabric. A compatibility report applies the registry to one pipeline. Producing the registry or a report reads the definition only: nothing runs, no Fabric call is made and no capacity is used.

This is Preview. Nothing in the registry has been compared against a run in Fabric yet. Control-flow activities and child pipelines run on your PC in a [local run](local-runs.md); data and compute activities need a mock there.

## Classifications

| Classification | Meaning | Activities today |
| --- | --- | --- |
| `local` | Runs on this PC with a tested adapter. | Set Variable, Append Variable, If Condition, Switch, ForEach, Until, Wait, Fail, Filter, Execute and Invoke pipeline (with the child's definition), Notebook (Python notebooks saved in KratosForge), Lookup and Get Metadata (on a lakehouse), Script (on a lakehouse or a local data project) |
| `simulated` | Runs on this PC against fixtures or a stand-in, with no real side effects. | None yet |
| `static` | Checked on this PC (structure, references, required properties, documented limits) but not run here. | Copy, Stored procedure, Delete, Web, Webhook |
| `cloud` | Only runs in Fabric or the Azure service it calls. Always uses Fabric capacity. | Dataflow Gen2, Spark job definition, Semantic model refresh, Outlook, Teams, KQL, Azure Databricks notebook, Azure Function |
| `unsupported` | A type this version doesn't know. It is kept exactly as it is and listed, but not checked or run. | Every unknown type |

The registry refuses an entry that overclaims: `local` or `simulated` needs an adapter that can execute and automated tests and can't be `fabric_only`, a `cloud` entry must be `fabric_only`, and planned support must be more capable than today's and name the stage that builds it. A pipeline's overall classification is its least capable activity.

## What each entry says

| Field | Meaning |
| --- | --- |
| `classification`, `implemented` | What works today. |
| `planned` | What a later stage aims for (`classification`, `stage`, `note`), always with `implemented: false`. Planned support is never counted as support. |
| `evidence` | `unit_tests` (automated tests in this repository, no Fabric), `fabric_tested` (compared against Fabric), or `none`. |
| `type_name_verified` | `true` when the type string is in Microsoft's Data Factory documentation. Types seen only in Fabric (`TridentNotebook`, `InvokePipeline`, `RefreshDataflow`, `SparkJobDefinition` and the Fabric item activities) are `false` until a pipeline exported from a tenant confirms them. |
| `fabric_capacity` | `fabric_only` (can only run in Fabric or the service it calls) or `metered_in_fabric` (uses capacity whenever it runs in Fabric; your PC may run it instead once an adapter can). No activity is free in Fabric: control flow, Wait and Web activities are metered too. |
| `auth` | What running it needs: `fabric` sign-in, a `connection`, or a `workspace_role`. |
| `side_effects` | `pipeline_state`, `reads_onelake`, `writes_onelake`, `reads_external`, `writes_external`, `calls_http`, `runs_code`, `sends_message`, `starts_fabric_job`, `deletes_data`, or `none`. |
| `external_dependencies` | Items and connections it relies on. |
| `properties` | Per `typeProperties` key: `checked` here, `carried` unchanged, or `unsupported_locally` with the reason. |
| `limitations` | What the entry doesn't cover. |

## Compatibility reports

A report has one row per activity, nested ones included, with its classification, planned stage, capacity need, sign-in, side effects, and its properties that are `unsupported_locally` or `unknown` (not in the registry for that type). Required properties that are missing are errors, and documented limits are checked: ForEach `items` must be an expression and `batchCount` between 1 and 50, Wait seconds a whole number, Until `timeout` in `d.hh:mm:ss`, If Condition and Until conditions must be expressions, and Switch case values unique. A Delete activity gets a note that it deletes data. The ORCH-001 [definition checks](pipeline-definitions.md) are included.

The summary gives the counts by classification, the overall classification, whether the whole pipeline runs on this PC, the activities that need Fabric capacity (every one this version can't run on your PC), those that can only ever run in Fabric, the unknown types, the activities planned to run here (with the stage), and the activities verified against Fabric (none yet).

The [example report](examples/orders-daily-compatibility.json) is for the `orders_daily` test pipeline (18 activities, one of an unknown type). A test regenerates it and fails if the file is out of date. Its summary:

| | |
| --- | --- |
| Overall | `unsupported` (the unknown `ContosoPagerNotify` activity) |
| By classification | 11 local, 6 static, 1 unsupported |
| Runs on this PC | No: six activities need mocks and one is unknown |
| Needs Fabric capacity | 7: the activities that can't run on this PC yet. Fabric meters every activity run, so running the whole pipeline in Fabric meters all 18 |
| Fabric only | None |
| Planned to run on this PC | 6 more, in a later, unscheduled stage |
| Properties it can't use | `enableStaging` on the Copy activity, `sessionTag` on the Notebook, two unknown properties on the unknown activity |

## Where Fabric differs

Some pipelines pass every check here and still fail in Fabric. The report warns about the cases below, which follow Microsoft's pipeline migration gotchas PG1 to PG10 ([skills-for-fabric](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/pipeline-migration/resources/pipeline-gotchas.md), MIT licensed). They are warnings only: none of them stops a local run. `fabric-desktop pipeline activities` lists them under `fabric_differences`, and a run check returns the ones a pipeline has as `fabric_differences`.

| Gotcha | Warning | What differs |
| --- | --- | --- |
| PG1 | `notebook_not_an_item_id` | A local run finds a notebook by the name you give it. Fabric looks it up by item id, so the notebook must be in the workspace first. |
| PG2 | `notebook_timeout_over_12_hours` | Fabric stops a Notebook activity after 12 hours at most. A local run uses a longer timeout as written. |
| PG3 | `global_parameters` | Fabric has no `pipeline().globalParameters`; use a variable library (`pipeline().libraryVariables`). A local run can't read them either. |
| PG4 | `dataset_reference` | Fabric has no dataset items. Their settings go in the activity. |
| PG5 | `integration_runtime` | Fabric has no integration runtimes. Data on your own network goes through an on-premises data gateway connection. |
| PG6 | `no_fabric_equivalent` | Validation, SSIS, Azure Batch (`Custom`) and HDInsight Hive, Pig and MapReduce activities can't run in Fabric. The hint says what to use instead. |
| PG7 | `child_pipeline_by_name`, `child_pipeline_no_workspace` | A local run finds a child pipeline by name. Fabric needs its item id and `workspaceId`, even in the same workspace. |
| PG8 | `key_vault_linked_service`, `secret_in_run_history` | Fabric has no Key Vault linked service. A Web activity that reads Key Vault without `secureOutput` shows the secret in run history, in Fabric and in a local run. |
| PG9 | `global_parameter_array_or_object` | A variable library holds only text, numbers and true or false, so an array or object is kept as JSON text and read with `json(...)`. |
| PG10 | `item_type_not_data_pipeline` | Fabric only accepts a pipeline as a `DataPipeline` item (the `.platform` part's type). |

Data Factory's [ForEach limitations](https://learn.microsoft.com/en-us/azure/data-factory/control-flow-for-each-activity#limitations-and-workarounds) add two more (`FOREACH` in the list). Fabric's ForEach page doesn't state them, so the warnings say they aren't verified in Fabric yet:

| Warning | What differs |
| --- | --- |
| `nested_foreach` | Data Factory doesn't allow a ForEach inside another ForEach or an Until, at any depth (through If Condition and Switch branches too). A local run allows it. Use a child pipeline for the inner loop. |
| `set_variable_in_parallel_foreach` | Data Factory doesn't allow Set Variable inside a ForEach that runs in parallel, because pipeline variables are shared by every iteration. A local run allows it, and the last iteration to finish wins. Make the ForEach sequential, or use Append Variable or a child pipeline. |

## Using it

```powershell
fabric-desktop pipeline compat .\pl_orders_daily.DataPipeline         # one line per activity; exit 1 on errors
fabric-desktop pipeline compat .\pipeline-content.json --json         # the whole report
fabric-desktop pipeline activities                                    # the whole registry
fabric-desktop pipeline activities Copy                               # one entry
```

From your own AI tool, through MCP or the local API ([connect your AI tool](../ai/README.md)): `fabric_desktop_check_pipeline_compatibility` takes a definition as text (up to 1 MB), and `fabric_desktop_get_activity_registry` takes an optional activity type. Both need only the read metadata permission. In the engine they are `pipeline_compatibility` and `pipeline_activity_registry`.

There is no page in the app for this yet.

## For contributors

Entries are in `sidecar/fabric_desktop/orchestration/activities.py`; the rules and the report are in `registry.py`. An adapter implements `validate(activity, ctx)` and, for `local` or `simulated`, `can_execute = True` and `execute(activity, run)`. Changing any entry changes the registry's fingerprint, and `tests/test_activity_registry.py` fails until you bump `REGISTRY_VERSION`, add a line below and record the new fingerprint in the test.

## Changelog

| Version | Change |
| --- | --- |
| 1 | First registry (ORCH-002): 28 types, all `static` or `cloud`. Unknown types are `unsupported`. |
| 2 | ORCH-003: Set Variable, Append Variable, If Condition, Switch, ForEach, Until, Wait, Fail, Filter, Execute Pipeline and Invoke Pipeline are `local`, with adapters and tests. Notebook's planned local support moved to a later stage. |
| 3 | Wording only: the product is now called KratosForge in limitations and notes. No classification changed. |
| 4 | ORCH-004: Notebook (`TridentNotebook`) is `local`. It runs a Python notebook saved in KratosForge, with its parameters, writing to the run's own folder; Spark notebooks still need a mock. Its `parameters` are checked. |
| 5 | Execute and Invoke pipeline with `waitOnCompletion: false` succeed once the child starts and no longer wait for it; the local run still waits for the child before it ends, so the child's result is in the record. |
| 6 | Pipelines plan P1: Lookup and Get Metadata are `local`. They read the lakehouse their dataset names, at the run's pinned table versions, when the run is started with lakehouse reads on. Their `datasetSettings` (and Lookup's `firstRowOnly`) are checked. |
| 7 | Pipelines plan P2: Script is `local`. Each script runs through the SQL page's runner and policy: read-only T-SQL on a lakehouse (with lakehouse reads on), or in a local data project named for it. Warehouse and other connections, and script parameters, need a mock. `scripts` and `scriptBlockExecutionTimeout` are checked; `logSettings` isn't used here. |
| 8 | FS-04: the report warns where a pipeline that passes here fails in Fabric, following Microsoft's pipeline gotchas PG1 to PG10 (see [Where Fabric differs](#where-fabric-differs)). Warnings only; no entry or classification changed. |
| 9 | ORCH-H2: warns about Data Factory's ForEach limits, a ForEach inside a ForEach or Until and Set Variable inside a parallel ForEach (see [Where Fabric differs](#where-fabric-differs)). Warnings only; no entry or classification changed. |
