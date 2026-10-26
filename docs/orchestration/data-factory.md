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
| DF-5 | Connections tab: connections and gateways, what uses each one, and a check before a platform deploy | Connections tab in review; the deploy check comes with DF-8 |
| DF-6 | Copy jobs: definition, diff, local run inside OneLake, run in Fabric, run history | Not started |
| DF-7 | Dataflow Gen2: M code and destinations, diff, refresh in Fabric, refresh history | Engine and Dataflows tab (reads); refresh next, behind a confirm and the risk check |
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

## DF-5: Connections tab

One engine call, a read (listed in `audit.READ_ONLY`):

- `fabric_connections(refresh=False)` lists the connections the signed-in person can use (Fabric's `GET /connections`, scope `Connection.Read.All`, asked for once) and reads the pipeline and copy job definitions in each environment's workspace (Dev, Test and Prod first). Without environments, it reads the workspaces Monitor > Schedules lists. Every `externalReferences.connection` in a definition, nested activities included, counts as a use. A connection id that's used but isn't in the person's list goes under `missing`, because that step fails when this person runs it. The scan runs in the background and returns `{"pending": true}` until it's done, then serves the result from memory until `refresh`. It reads up to 500 definitions per scan.

Nothing from a credential is read: Fabric's list has only the credential type, and the connection's path passes through `redact.secrets`. The tab sits beside Pipelines on the Pipelines page. **Manage connections in Fabric** opens Fabric's page.

Gateway names come from `GET /gateways` with `Gateway.Read.All` (Chris said yes on 2026-10-25), asked for once when the tab first opens. If it is declined or fails, the connections still list, and `gateways_error` says why the names are missing. Fabric's gateway API has no online/offline status, so the tab shows the name and member count, and status stays on Fabric's Manage connections page. Dataflows count too: a dataflow uses the connections its `queryMetadata.json` names by `DatasourceId`. It hasn't been checked against a real export that a `DatasourceId` is always the connection's id, so a dataflow's id that matches none of your connections isn't listed as missing. Checking that each target environment has the connections a deploy needs comes with DF-8's connection mapping.

## DF-7 engine: Dataflow Gen2 from a workspace

Three engine calls, all reads (listed in `audit.READ_ONLY`). None of them starts a refresh or uses capacity:

- `workspace_dataflows(workspace_id)` lists a workspace's Dataflow Gen2 items by name: item id, name, description and folder.
- `workspace_dataflow_definition(workspace_id, item_id, name)` reads one dataflow with getDefinition. It returns each Power Query (M) query from `mashup.pq` in document order, with its M code, whether it loads, whether it's hidden, and its destination. A destination has the kind (Lakehouse, Warehouse, Azure SQL database, KQL database), workspace, item, schema, table, update method (Replace or Append) and whether it makes a new table. These come from the query's `DataDestinations` attribute and the `_DataDestination` query that attribute names. The call also returns the connections from `queryMetadata.json`, with each `DatasourceId`, so they can be matched with the Connections tab, and the whole `mashup.pq`. A definition without `mashup.pq` (a Gen1 dataflow, say) comes back with `ok: false` and the reason, not an error.
- `fabric_dataflow_refreshes(workspace_id, item_id, name)` lists the dataflow's newest 50 refreshes in the shape Monitor > Runs uses for pipeline runs.

The M text is only read, never evaluated: there's no local M engine. It passes through `redact.secrets`, because a query can carry a key inline (a `Web.Contents` header, say). Nothing is written to disk. The parser lives in `orchestration/mashup.py`. It splits the section on top-level semicolons and skips strings, comments and nested brackets.

## DF-7 screen: Dataflows tab

The Pipelines page's **Dataflows** tab (Preview) sits between Pipelines and Connections. You pick a workspace (the same one the open dialog remembers, `fd.pipelines.workspace`) and its dataflows are listed with `workspace_dataflows`. Opening one reads `workspace_dataflow_definition` and `fabric_dataflow_refreshes` together. Its views are **Queries (M)**, with hidden destination and helper queries folded away, **Destinations** (and the connections the dataflow names) and **Refresh history**. **Open in Fabric** opens the workspace, because the address of a dataflow's own page isn't confirmed yet. The mockup's "Changes since Git" view comes with the Git diff work (DF-8).

**Refresh in Fabric…** starts the dataflow's refresh through Workloads (see Run in Fabric below). No live refresh has run yet: the first needs Chris's OK.

## DF-6 engine: copy jobs from a workspace

Three engine calls, all reads (listed in `audit.READ_ONLY`). None of them starts a run or uses capacity.

- `workspace_copy_jobs(workspace_id)` lists a workspace's copy jobs by name: item id, name, description and folder.
- `workspace_copy_job_definition(workspace_id, item_id, name)` reads one copy job with getDefinition (`copyjob-content.json`) and returns:
  - the job mode (Batch or CDC), timeout and retry
  - the job's source and destination: type, connection type, connection id (lowercased, so it matches the Connections tab), and the lakehouse workspace, item and root folder when there is one
  - one row per table: source and destination schema, table or folder, write behavior (Append, Overwrite, Upsert, ...), upsert keys, table option, incremental read method and columns, how many columns are mapped, and staging
  - `local_check`, which says whether a run on this PC could take the job, with the reasons when it can't. That's only when both ends are a lakehouse (OneLake) and the job is Batch. An external source needs Fabric's connections and gateways, and CDC runs in Fabric only.
  - A definition it can't read (no `copyjob-content.json`, an unknown job mode, `activities` that isn't a list) comes back with the reason under `problems`, not an error.
- `fabric_copy_job_runs(workspace_id, item_id, name)` lists the copy job's newest 50 runs, in the shape Monitor > Runs uses for pipeline runs.

The field names follow the examples in Microsoft's Copy job definition article. They haven't been checked against a real export yet (plan decision 4). Database names and folders pass through `redact.secrets`, and nothing is written to disk. The reader lives in `orchestration/copyjob.py`. Running a copy job on this PC needs the pipeline thread's copy module (DF-3), and running one in Fabric needs a confirm and the risk check.

## DF-6 screen: Copy jobs tab

The Pipelines page's **Copy jobs** tab (Preview) sits between Pipelines and Dataflows. It works like the Dataflows tab: you pick a workspace (the same remembered `fd.pipelines.workspace`), its copy jobs are listed with `workspace_copy_jobs`, and opening one reads `workspace_copy_job_definition` and `fabric_copy_job_runs` together. The summary shows source, destination, mode, timeout, connection count and the `local_check` line. Its views are **Tables** and **Run history**. **Open in Fabric** opens the workspace until a copy job's own page address is confirmed. **Run in Fabric…** starts it through Workloads (below). Run on this PC needs the copy engine (DF-3).

## DF-6 engine: Run in Fabric for copy jobs and dataflows

Copy jobs (`CopyJob`) and Dataflow Gen2 items (`Dataflow`) are Fabric workloads now, beside notebooks, pipelines and Spark job definitions, labelled **Copy job** and **Dataflow Gen2**. Their one action is `run_item`, so starting one goes through `workload_preflight` (the risk check and the consequence line "Starts copy job ... in Fabric now, on its workspace's capacity") and then `workload_act`, which records the action. `run_fabric_item_now` takes the item type: for these two it calls `FabricClient.run_item_execute`, which posts to the item's own path, `/workspaces/{ws}/copyJobs/{id}/jobs/execute/instances` or `/workspaces/{ws}/dataflows/{id}/jobs/execute/instances`, once (a 5xx is never resent), and logs the run with engine `data_factory`. Pipelines, notebooks and Spark job definitions keep the `jobType` path. The `allow_fabric_runs` policy blocks it like any other Fabric job. It uses the existing `Item.Execute.All` sign-in; Microsoft lists it for copy jobs, and for dataflows it is still to be confirmed on a real run. Only mocked tests exist so far: nothing has run against a tenant. 

**The buttons.** **Run in Fabric…** on the Copy jobs tab and **Refresh in Fabric…** on the Dataflows tab (mockup board 2) call `dfRunInFabric`. It asks `workload_preflight` for the workload `fabric:{workspace}:{item}` and opens the same confirm as Monitor > Workloads, through `confirmWorkloadAction`. That confirm shows where it runs, as whom, the target, what happens and the risk check, and a blocked check offers no start. Confirming sends `workload_act` with `run_item`, then reads the item's runs again. Workloads lists only the items of the workspaces this app works in. When it doesn't list the item yet, the button says so and offers **Pin and read again**. That pins the workspace on the Workspaces page (kept on this PC; nothing changes in Fabric) and starts a fresh read with `workloads_list` `refresh`, and nothing is started.

## Copy job and dataflow runs in Monitor

`fabric_pipeline_runs` reads the run history of copy jobs (`CopyJob`) and Dataflow Gen2 items (`Dataflow`) as well as pipelines, in the same background scan and the same workspaces, with the same 20 newest runs per item. Each row carries `kind` (`pipeline`, `copy job` or `dataflow`). The reply adds `items`, the count of all three, and `pipelines` still counts pipelines alone. Monitor > Runs labels the rows **Copy job run in Fabric** and **Dataflow refresh in Fabric**. Only pipeline runs open to their activities. Still reads only: no job starts and no capacity is used.
