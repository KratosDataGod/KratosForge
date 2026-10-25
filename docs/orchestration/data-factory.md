# Data Factory in KratosForge (plan)

Status: Preview. Chris said go on 2026-10-24 and approved the mockup on 2026-10-25. This page is for contributors. It extends the [orchestration plan](plan.md) from pipelines to the rest of Fabric Data Factory: Dataflow Gen2, copy jobs, connections and gateways, and monitoring of Fabric runs.

## Rules for every story

- Local first: anything that can run on this PC does, and its output stays in the run's sandbox. Copy jobs whose source and destination are OneLake or local files reuse the pipeline Copy data engine (P5).
- Fabric-only items (Dataflow Gen2, copy jobs with external sources) run in Fabric. Starting one asks first and runs the risk check, because it uses capacity.
- Reading definitions, run history and connections is a read. It starts no Fabric job and uses no capacity.
- Connections are read-only in KratosForge. The app never stores, creates or changes a connection or its credentials. Changes happen on Fabric's own Manage connections page, opened inside the app.
- No new rail item. The Pipelines page grows into a Data Factory page with tabs: Pipelines, Copy jobs, Dataflows, Connections. Runs stay in Monitor > Runs.
- Each screen gets a mockup first, and new screens are tagged Preview.

## Stories

| # | Story | Status |
| --- | --- | --- |
| DF-1 | Fabric pipeline runs in Monitor > Runs, with activity runs when a row opens | Done, Preview (#640 engine, #653 Monitor rows) |
| DF-2 | Open a pipeline from a workspace (read its definition), Open in Fabric embedded | Done, Preview (#665 engine, then the open dialog's workspace list) |
| DF-3 | Copy data inside OneLake and local files (orchestration plan P5) | Not started |
| DF-4 | Schedule a pipeline on this PC (P6), rerun from the failed activity (P7) | Not started |
| DF-5 | Connections tab: connections and gateways, what uses each one, and a check before a platform deploy | Not started |
| DF-6 | Copy jobs: definition, diff, local run inside OneLake, run in Fabric, run history | Not started |
| DF-7 | Dataflow Gen2: M code and destinations, diff, refresh in Fabric, refresh history | Not started |
| DF-8 | Deploy Data Factory items through the platform repository deploy, with connection and parameter mapping | Not started |
| DF-9 | Spark notebook steps (P8), Delete, Web and the smaller rows (P9) | Not started |
| DF-10 | Edit a pipeline in the app (P11) | Later |

## DF-1 engine: Fabric pipeline runs

Two engine calls, both reads (listed in `audit.READ_ONLY`):

- `fabric_pipeline_runs(refresh=False, all_workspaces=False, workspace_ids=None)` lists the job history of every pipeline in the chosen workspaces, newest first. With no `workspace_ids` it uses the workspaces Monitor > Schedules lists (pinned workspaces, environment workspaces and notebooks' workspaces), or every workspace with `all_workspaces`. The scan runs in the background. It returns `{"pending": true}` until it's done, then serves the result from memory until `refresh`. It keeps up to 20 runs per pipeline and 300 in all. A pipeline whose runs can't be read is counted in `missed` and logged once per scan.
- `fabric_pipeline_activity_runs(workspace_id, job_id, started, ended)` reads one run's activities from Fabric's activity-run query when its row is opened, never in the background. Fabric needs a time window, so the query covers the run's start and end with five minutes either side (up to now while the run is going). Each activity's input, output and error pass through `redact.secrets` and are capped at 16,000 characters.

Run states use the names local pipeline runs use (`pending`, `running`, `succeeded`, `failed`, `cancelled`, plus `skipped` for activities), so Monitor > Runs can show both kinds the same way.

Not tested yet: a live tenant. The activity-run query follows Microsoft's [pipeline REST API page](https://learn.microsoft.com/fabric/data-factory/pipeline-rest-api).

## DF-2 engine: pipelines from a workspace

Two engine calls, both reads (listed in `audit.READ_ONLY`):

- `workspace_pipelines(workspace_id)` lists a workspace's pipelines by name: item id, name, description and folder.
- `workspace_pipeline_definition(workspace_id, item_id, name)` reads one pipeline with getDefinition. It returns `text`, the getDefinition result, which the existing pipeline calls take as they are (`pipeline_inspect`, `pipeline_compatibility`, `pipeline_run_check`, `pipeline_run_start`). It also returns `pipeline`, which is what `pipeline_import` says about it. A SecureString default shows as `(hidden)`. A definition this version can't read comes back with `ok: false` and the reason, not an error.

Both calls take Fabric GUIDs only. Nothing is written to disk: the definition stays in memory, as a pasted one does.

## DF-2 screen: In a Fabric workspace

The Pipelines page's open dialog has an **In a Fabric workspace** section (Preview) under the repository list. Signed out, it offers sign-in. Signed in, it lists the workspaces; picking one lists its pipelines with `workspace_pipelines`, and clicking one reads it with `workspace_pipeline_definition` and opens its `text` on the Pipelines page, the same way a file opens. The page header then has **Open in Fabric**.

The definition text holds SecureString defaults, so the page keeps it in memory only: it is never written to localStorage or the console. Only the picked workspace id is remembered (`fd.pipelines.workspace`). The Copy jobs, Dataflows and Connections tabs from the mockup come with their stories (DF-5, DF-6, DF-7), so the page shows no empty tabs until then.
