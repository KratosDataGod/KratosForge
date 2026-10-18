# Pipeline activity compatibility (Preview)

Fabric Desktop by KratosBI keeps a versioned registry of Fabric Data Factory pipeline activity types. For each type it says what this version can do with it on your PC, what is only planned, which properties it can't use, what running it needs and changes, and whether it can only ever run in Fabric. Fabric meters every activity run, control flow included, so any activity that can't run on your PC uses capacity when the pipeline runs in Fabric. A compatibility report applies the registry to one pipeline. Producing the registry or a report reads the definition only: nothing runs, no Fabric call is made and no capacity is used.

This is Preview. Nothing in the registry has been compared against a run in Fabric yet, and no activity runs on your PC yet. Running activities locally starts with [ORCH-003](plan.md).

## Classifications

| Classification | Meaning | Activities today |
| --- | --- | --- |
| `local` | Runs on this PC with a tested adapter. | None yet |
| `simulated` | Runs on this PC against fixtures or a stand-in, with no real side effects. | None yet |
| `static` | Checked on this PC (structure, references, required properties, documented limits) but not run here. | Control flow, Copy, Notebook, Lookup, Get Metadata, Script, Stored procedure, Delete, Invoke and Execute pipeline, Web, Webhook |
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
| By classification | 17 static, 1 unsupported |
| Runs on this PC | No |
| Needs Fabric capacity | All 18: none can run on this PC yet, and Fabric meters every activity run |
| Fabric only | None |
| Planned to run on this PC | 17, of which 12 in ORCH-003 and 5 in a later, unscheduled stage |
| Properties it can't use | `enableStaging` on the Copy activity, `sessionTag` on the Notebook, two unknown properties on the unknown activity |

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
