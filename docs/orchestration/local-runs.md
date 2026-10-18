# Running pipelines on your PC (Preview)

Fabric Desktop by KratosBI can run a Microsoft Fabric Data Factory pipeline on your PC, so you can test its control flow (dependencies, conditions, loops, variables, failure handling and child pipelines) without a Fabric run. A local run never calls Fabric or OneLake, so it starts no Fabric job and meters no activity runs.

This is Preview. The behaviour follows Microsoft's Data Factory documentation and is tested with hand-built pipelines; it hasn't been compared with a run in Fabric yet. Every run record says `verified_against_fabric: false`.

## What runs here

| Activities | In a local run |
| --- | --- |
| Set Variable, Append Variable, If Condition, Switch, ForEach, Until, Wait, Fail, Filter | Run on this PC (classification `local` in the [activity registry](activity-registry.md)). |
| Execute Pipeline and Invoke Pipeline | Run the child pipeline on this PC when you give its definition, by `referenceName` or `pipelineId`. |
| Copy, Notebook, Lookup, Get Metadata, Script, Stored procedure, Delete, Web, Webhook | Don't run. Give a mock for each one the run reaches. |
| Dataflow Gen2, Spark job definition, semantic model refresh, Outlook, Teams, KQL and other Fabric-only activities, and unknown types | Don't run. Give a mock, or run the pipeline in Fabric. |

Before anything starts, the run checks every activity it could reach, nested ones and child pipelines included. One activity that can't run here and has no mock refuses the whole run, with the activity and the reason. The pipeline's own checks ([pipeline definitions](pipeline-definitions.md)) must pass too, and every parameter without a default needs a value.

## Mocks

A mock stands in for one activity, by name:

```json
{
  "Ingest orders": {"status": "Succeeded", "output": {"rowsCopied": 120}, "seconds": 2},
  "Transform": {"status": "Failed", "error": {"errorCode": "2011", "message": "Notebook failed"}}
}
```

`status` is `Succeeded` (the default) or `Failed`. Later activities read the mock's `output` with `activity('Ingest orders').output`, as they would in Fabric. A mocked activity does nothing else, and the run record marks it `executed_by: mock`. A mocked container's inner activities don't run.

## How a run behaves

- An activity starts when every activity it depends on has finished and each one's status matches the dependency's conditions: `Succeeded`, `Failed`, `Skipped`, or `Completed` (succeeded or failed). Otherwise it is skipped.
- Activities that don't depend on each other run in parallel, up to `max_parallel` at once (4 by default; containers don't count). ForEach runs iterations in parallel up to `batchCount` (20 when it is left out), or one at a time when `isSequential` is true, over at most 100,000 items. Each iteration's activities have results of their own.
- A run succeeds when every leaf activity succeeds; a skipped leaf is judged by the activities it depends on. This is Data Factory's documented rule, so a failure handled by an "on failure" path succeeds, and an if-else made of success and failure paths fails.
- Activity policy applies: `retry` and `retryIntervalInSeconds`, `timeout`, and `secureOutput` (the output is shown as `(hidden)`). Deactivated activities aren't run and take the status in `onInactiveMarkAs`.
- `wait_scale` multiplies every wait (Wait activities, mock `seconds`, retry intervals), so `0` runs a pipeline with long waits at once. Timeouts stay in real time.
- Until stops at its timeout (7 days when left out) or after 10,000 iterations.
- Cancel stops the run: nothing new starts, waits end at once, and activities that hadn't started are `cancelled`. A run `timeout` does the same and ends the run `failed`.
- Run and activity run ids are GUIDs. Activity run ids are derived from the run id, so they are stable for a run.

The states are pending, running, succeeded, failed, skipped and cancelled. Each activity run also has its Fabric-style `status` (`Succeeded`, `Failed`, `Skipped`, `Cancelled`).

### Expressions

Expressions are evaluated by a fixed list of functions written for this purpose, never by a general-purpose evaluator. An expression that uses anything else fails that activity with `LocalExpressionError` and the function's name. The functions are:

- Logic: `equals`, `not`, `and`, `or`, `if`, `greater`, `greaterOrEquals`, `less`, `lessOrEquals`.
- Math: `add`, `sub`, `mul`, `div`, `mod`, `min`, `max`, `range` (at most 100,000 numbers, ForEach's limit in Fabric).
- Text: `concat`, `substring`, `replace`, `toLower`, `toUpper`, `trim`, `indexOf`, `lastIndexOf`, `startsWith`, `endsWith`, `split`, `guid`.
- Collections: `length`, `empty`, `contains`, `first`, `last`, `take`, `skip`, `union`, `intersection`, `createArray`, `join`, `coalesce`.
- Conversion: `string`, `int`, `float`, `bool`, `json`, `array`.
- Dates (UTC): `utcNow`, `formatDateTime`, `addDays`, `addHours`, `addMinutes`, `addSeconds`, `startOfDay`.
- Pipeline: `pipeline()` (`parameters`, `RunId`, `Pipeline`, `TriggerTime`, `TriggerType`, `TriggerId`, `TriggerName`, `GroupId`), `variables()`, `activity()` (`output`, `status`, `error`, `activityRunId`), `item()`.

`@@` escapes a literal `@`, `@{...}` interpolates into text, and `?.` reads a property that may be missing. Property names are matched without regard to case.

Known differences, or things not yet compared with Fabric:

- Date formats support the common .NET patterns (`yyyy`, `MM`, `dd`, `HH`, `mm`, `ss`, `fff`, `tt`, quoted text) and the standard formats `o`, `s` and `u`. Time zones other than UTC aren't supported.
- `equals` compares exactly, including case. `startsWith`, `endsWith` and `indexOf` ignore case.
- Library variables, global parameters and `pipeline().DataFactory` aren't available.

## Run records

Each run is saved as one file under `pipeline_runs` in the app's data folder (`%LOCALAPPDATA%\FabricDesktop` on Windows), written as the run goes, and the last 200 are kept. A record has the run's state, parameters (secure ones hidden), options, every activity run (state, status, how it ran, timings, attempts, output, error, the branch an If Condition or Switch took, ForEach and Until iteration counts, and a child pipeline's whole run), the final variables and the pipeline return value.

If Fabric Desktop by KratosBI stops while a run is going, the record is marked `interrupted` the next time it opens. The run isn't resumed: activities that were running may have done part of their work, so running it again is your decision.

## Using it

```powershell
fabric-desktop pipeline run .\pl_orders_daily.DataPipeline --mocks .\mocks.json --param day=2026-10-17
fabric-desktop pipeline run .\parent.json --child child_pipeline=.\child.json --wait-scale 0 --json
```

The command prints each activity run and the result. It exits 0 when the run succeeds, 1 when it fails and 2 when it is refused. It doesn't save a record.

The engine methods are `pipeline_run_check`, `pipeline_run_start` (runs in the background, at most two at once), `pipeline_run_status`, `pipeline_run_cancel` and `pipeline_runs`. From your own AI tool, through MCP or the local API ([connect your AI tool](../ai/README.md)):

| Tool | Permission |
| --- | --- |
| `fabric_desktop_check_pipeline_run` | Read metadata |
| `fabric_desktop_start_pipeline_run` | Run code on this PC; you approve every run |
| `fabric_desktop_get_pipeline_run`, `fabric_desktop_list_pipeline_runs` | Read metadata. Outputs, variable values and parameter values are left out. |
| `fabric_desktop_cancel_pipeline_run` | Run code on this PC; you approve it |

They take definitions as text, up to 1 MB. There is no page in the app for local runs yet.

## Try it

`sidecar/tests/pipelines/reference_local.json` is a reference pipeline: a checkpoint, an ingest (Copy), a loop over regions, a transform (Notebook), a validation that fails the run when too few rows arrived, and a record of the result as the pipeline return value. With the ingest and transform mocked, it runs in full on your PC:

```powershell
'{"Ingest orders": {"output": {"rowsCopied": 120}}, "Transform": {}}' | Set-Content mocks.json
fabric-desktop pipeline run .\sidecar\tests\pipelines\reference_local.json --mocks .\mocks.json --wait-scale 0
```
