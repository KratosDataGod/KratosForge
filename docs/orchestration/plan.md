# Fabric pipelines on this PC: orchestration plan

KratosForge's program for importing, checking and, later, running Microsoft Fabric Data Factory pipelines on your own PC, so developing and testing a pipeline doesn't spend Fabric capacity. Feature IDs ORCH-001 to ORCH-010. The working state for whoever continues it is in the [continuation note](continuation.md); how to use what exists is in [Pipeline definitions](pipeline-definitions.md).

## Rules for the whole program

- Nothing here consumes Fabric capacity or calls a tenant without the person's explicit go. ORCH-001 to ORCH-003 make no Fabric calls at all. ORCH-004's notebook steps read and write OneLake (in the run's own folder), which uses some capacity, and start no Fabric job.
- No claim that a run on this PC behaves like Fabric unless that has been tested against Fabric. Anything untested is labelled Preview.
- Expressions are read, never evaluated by a general-purpose evaluator, and imported code is never run while reading a definition.
- Reuse what exists: the Service methods (desktop, CLI), the capability registry for external tools (`integrations/`), the job queue and run history for runs, Prove's execution path for notebooks.
- No AI in the app. External tools reach these features through the capability registry (MCP and the local API).
- Git belongs to the Git-native program; this program reads the Fabric Git folder shape but never writes to Git or a workspace.

## Status

| ID | Feature | Status | PR | Depends on |
| --- | --- | --- | --- | --- |
| ORCH-001 | Pipeline definition engine: import, validate, inspect, export | Merged | #130 | none |
| ORCH-002 | Activity compatibility registry | Merged | #150 | ORCH-001 |
| ORCH-003 | Local pipeline execution engine (DAG, states, cancel, timeouts, run records) | Merged | #218 | ORCH-001, ORCH-002 |
| ORCH-004 | Notebook steps: a Notebook activity runs a notebook saved on this PC | Merged | #319 | ORCH-003, Validate notebook (#282) |
| ORCH-005 to ORCH-010 | Later stages (breakpoints, step and replay, more adapters, Fabric hand-off) | Not defined yet | | ORCH-003 |

## ORCH-001: pipeline definition engine

**Source files.** `sidecar/fabric_desktop/orchestration/` (`definition.py` formats, typed view and export; `expressions.py` reference scanner; `validate.py` diagnostics), `sidecar/fabric_desktop/pipeline_service.py` (Service methods `pipeline_import`, `pipeline_validate`, `pipeline_inspect`, `pipeline_export`), CLI `fabric-desktop pipeline validate|inspect|export`, capabilities `fabric_desktop_validate_pipeline`, `fabric_desktop_inspect_pipeline` and `fabric_desktop_export_pipeline` (Preview) in `integrations/capabilities.py`.

**Design.** The parsed JSON is the source of truth; `Pipeline` and `Activity` are a view over it with JSON paths back into it. Unknown keys, unknown activity types and every other definition part pass through untouched. An unchanged definition exports to exactly the text (or Git folder files) it was read from; a changed one is serialized from the JSON with key order kept. Every import records a schema record: reader version (`DEFINITION_VERSION`), source format, a dialect hint (Fabric or Data Factory, with the evidence), the `.platform` schema and version, the activity types found and their `typeProperties` keys, and keys the reader doesn't recognize. ORCH-002 reads this record.

**Test evidence.** `sidecar/tests/test_pipeline_definitions.py` (52 tests) over the fixtures in `sidecar/tests/pipelines/`, run with no network (sockets are refused in the tests). The full suite results are in the PR.

**Compatibility claims.** The formats follow Microsoft's documented shapes for a pipeline item definition (`pipeline-content.json` and `.platform` parts, `InlineBase64` payloads) and the Fabric Git folder. The fixtures are hand-built in those shapes; none is yet exported from a live tenant. Reading a real export is unverified until one is added as a fixture.

**Known limitations.**

- Reads files and pasted text only; it doesn't fetch a pipeline from Fabric (`getDefinition`). That would be a read with no capacity use, and needs Chris's OK.
- Export of an unchanged file drops a UTF-8 byte order mark if the file had one; the content is otherwise identical.
- The typed view is built at import. Code that edits `pipeline.content` and wants a fresh view calls `definition.build` again; `export` always uses the JSON, so edits are never lost.
- Validation checks structure and references. It doesn't check `typeProperties` against each activity type's schema, connections, or whether Fabric would accept a property; that is ORCH-002's registry.
- Dependencies are checked within the list an activity is in, as Fabric scopes them. Rules Fabric may add on nesting control activities (for example a ForEach inside a ForEach) aren't checked, because they aren't verified.
- Expression references are found by reading the expression. Dynamic names built at run time (`activity(concat('a', 'b'))`) can't be followed and are ignored.
- External tools can pass definitions up to 1 MB (the local API's request limit) and can't name files on the PC.

**Rollback.** Revert the PR. It adds new files and adds one mixin to `Service`, four names to `server.METHODS` and `audit.READ_ONLY`, three capabilities and one CLI command; nothing stores data, so nothing needs cleaning up.

## ORCH-002: activity compatibility registry

**Source files.** `sidecar/fabric_desktop/orchestration/registry.py` (entry model, overclaim rules, `StaticAdapter`, pipeline reports), `orchestration/activities.py` (the 28 entries and `default_registry()`), Service methods `pipeline_compatibility` and `pipeline_activity_registry` in `pipeline_service.py`, CLI `fabric-desktop pipeline compat|activities`, capabilities `fabric_desktop_check_pipeline_compatibility` and `fabric_desktop_get_activity_registry` (Preview). User guide: [Pipeline activity compatibility](activity-registry.md).

**Design.** One entry per activity type, with aliases. Classifications are `local`, `simulated`, `static`, `cloud` and `unsupported`; `planned` support (with the stage) is reported apart from what is implemented. The registry rejects an entry that claims `local` or `simulated` without an executing adapter and tests, or `cloud` without capacity. Adapters share one interface: `validate(activity, ctx)` for static checks and, from ORCH-003, `execute(activity, run)`. Reports run ORCH-001's validation, then each activity's adapter checks, and summarize the pipeline by its least capable activity. The registry has a version and a fingerprint test, so an entry can't change without a version bump and a changelog line.

**Test evidence.** `sidecar/tests/test_activity_registry.py` (74 tests, no network): coverage of every activity type in the brief, the overclaim rules, unknown types (kept, reported, never a parse failure), property reporting, each static check, fixture reports, the example report in [examples/](examples/orders-daily-compatibility.json), the APIs, the allowlists and the CLI.

**Compatibility claims.** None against Fabric. Data Factory type names and limits (ForEach `batchCount` 1 to 50, timespan format) follow Microsoft's documentation; Fabric-only type names are marked unverified until a tenant export confirms them.

**Known limitations.**

- No activity runs on this PC; nothing is `local` or `simulated` until ORCH-003 adds executing adapters with tests.
- Source, sink and connection settings are carried, not checked against each connector's schema.
- `fabric_capacity` says whether an activity can only run in Fabric (`fabric_only`) or is metered when it runs there (`metered_in_fabric`); Fabric meters every activity run. A report counts every activity that can't run on this PC as needing capacity. It doesn't estimate capacity units; that belongs to the FinOps program.
- Activity policy (timeout, retry, secure input and output) is read but not applied.

**Rollback.** Revert the PR. It adds two modules, two Service methods (and their names in `server.METHODS` and `audit.READ_ONLY`), two capabilities and two CLI subcommands; nothing is stored.

## ORCH-003: local pipeline execution engine

**Source files.** `sidecar/fabric_desktop/orchestration/evaluate.py` (expressions, a fixed function list), `adapters.py` (the control-flow and child pipeline adapters), `runner.py` (`PipelineRun`: checks, DAG scheduling, mocks, policy, cancel, timeouts, the record), `runs.py` (`RunStore`), Service methods `pipeline_run_check`, `pipeline_run_start`, `pipeline_run_status`, `pipeline_run_cancel` and `pipeline_runs` (`PipelineRunMixin` in `pipeline_service.py`), CLI `fabric-desktop pipeline run`, five Preview capabilities. Reference pipeline `sidecar/tests/pipelines/reference_local.json`. User guide: [Running pipelines on your PC](local-runs.md).

**Design.** A run checks every reachable activity first and refuses the whole run if one isn't `local` in the registry and has no mock, so nothing that needs Fabric runs. Each list of sibling activities is scheduled by its dependencies and conditions, independent activities in parallel (up to `max_parallel`, containers excluded); containers run their lists through the same scheduler, ForEach iterations in parallel up to `batchCount` with results of their own. Outcomes follow Data Factory's documented leaf rule. Expressions use a fixed list of functions written here, never Python's `eval`. Records are one JSON file per run, written as the run goes; a run left going by an app that stopped is marked interrupted and never resumed.

**What was reused, and what wasn't.** The ORCH-001 model and validation, the ORCH-002 registry (adapters register there and classify themselves `local`), the background-run pattern the Prove and SQL runs use (a record, a cancel flag, a status call), and the capability registry. Not reused, on purpose: the agent's job queue (`jobqueue.py`) is a durable, single-claimer SQLite queue for the headless agent, with no dependencies between jobs and no way to stop a running job, so it can't schedule activities; a pipeline run could become one job there when the agent runs pipelines (the Workload program owns that). Prove's notebook execution isn't callable with parameters outside a proof yet, so Notebook stays `static` and needs a mock. No existing run history has runs with child activity runs, so local runs keep their own records; Monitor doesn't show them yet.

**Test evidence.** `sidecar/tests/test_pipeline_runs.py` (101 tests, no network): expressions and their errors, dependency conditions, parallelism, the leaf rule (try-catch and if-else patterns), every adapter, inactive activities, mocks, retries, policy and run timeouts, cancel, stable ids, secure values, refusals, child pipelines, the reference pipeline (success, validation failure, refused without mocks), the store (atomic files, trimming, interrupted runs), the Service methods, allowlists, capabilities and the CLI. The ORCH-002 tests cover the registry changes (version 2).

**Compatibility claims.** None against Fabric. Semantics follow Microsoft's Data Factory documentation (dependency conditions, the leaf rule, ForEach defaults, Until timeout, activity policy); the differences known so far are listed in the user guide.

**Known limitations.**

- Data and compute activities (Copy, Notebook, Lookup and the rest) need mocks; nothing reads or writes data.
- Execute and Invoke pipeline waited for the child even when `waitOnCompletion` was false (fixed in registry version 5: the activity succeeds once the child starts).
- Timeouts can't stop an adapter mid-step; they end waits and mocks, which are the only steps that take time today.
- Runs live in the engine process: at most two at once, and an app restart interrupts them.
- No UI; Monitor doesn't list local pipeline runs. Both need a mockup first.

**Rollback.** Revert the PR. It adds four modules, one mixin with five Service methods (names in `server.METHODS`, the three reads in `audit.READ_ONLY`), five capabilities and one CLI subcommand, and moves eleven registry entries to `local` (registry version 2). Run records in `pipeline_runs/` can be deleted.

## ORCH-004: notebook steps

Chris picked this stage on 2026-10-20 ("Notebook steps").

**Source files.** `sidecar/fabric_desktop/pipeline_notebooks.py` (`LocalNotebooks`, the run's notebook runner), the `Notebook` adapter in `orchestration/adapters.py`, the `NotebookRunner` protocol and checks in `runner.py`, the `notebooks`, `allow_uncontained` and `keep_outputs` options of `pipeline_run_check` and `pipeline_run_start`, and `--notebook`, `--allow-uncontained` and `--keep-outputs` on `fabric-desktop pipeline run`. User guide: [Notebook steps](local-runs.md#notebook-steps).

**Design.** A Notebook activity names a notebook saved in KratosForge (by activity name or `notebookId`). Each step runs the way Validate notebook's clean run does: a fresh kernel process (`_CleanKernel`), the proof runtime bound with pinned table versions and writes redirected to `Files/_fabric_desktop/proofs/pipeline-<run id>/pc/`. Pins are taken once per lakehouse per run, and later steps are seeded with the tables earlier steps wrote, so they read each other's output. Parameters go in a cell after the parameter cell (first when there is none); `notebookutils.notebook.exit` becomes `output.result.exitValue`. The checks before the run reuse Validate's run plan, so the run is refused for Spark cells and writes the folder can't contain, and asks for `allow_uncontained` for network and local file access. The folder is deleted when the run ends unless `keep_outputs`.

**What was reused, and what wasn't.** Validate notebook's clean kernel, run plan and secret removal; Prove's proof runtime (pins, sandbox, seeded tables) and folder deletion; the saved notebooks. Prove's own proof path isn't used: it also runs the notebook in Fabric, which uses capacity, and it uses the person's kernel.

**Test evidence.** `sidecar/tests/test_pipeline_notebooks.py` (25 tests, a local folder as the lakehouse, no network): two notebook steps passing a table and exit values to a Set Variable, parameters from pipeline expressions, the lakehouse's tables untouched and the folder deleted, keep_outputs, every refusal, a failing cell with secrets removed, cancel and policy timeout stopping the kernel (and the folder deleted after a step stopped mid-write), parameter placement and types (NaN and infinity included), the CLI, and external tools' runs still needing mocks. The ORCH-002 and ORCH-003 tests cover the registry change (version 4).

**Compatibility claims.** None against Fabric. The output shape (`result.exitValue`) and parameter placement follow Microsoft's documentation and haven't been compared with a Fabric run.

**Known limitations.**

- Python notebooks only; Spark notebooks need a mock.
- External AI tools can't run notebook steps: the approval shows the pipeline, not the notebooks' code, so their runs still need mocks. Binding the approval to each notebook's code (as `fabric_desktop_run_notebook` does) would lift this.
- Two notebook steps writing the same table at once (a parallel ForEach) write the same folder; Fabric would write the same table too, but the result isn't defined here.
- No UI yet.

**Rollback.** Revert the PR. It adds one module and three options; the registry goes back to version 3. Folders kept with `keep_outputs` stay in the lakehouse until deleted in Fabric.

## Next recommended PR

A mockup for a pipeline runs page (needs Chris's go), or Lookup and Get Metadata as `simulated` against a local table.
