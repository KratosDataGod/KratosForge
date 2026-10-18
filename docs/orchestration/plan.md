# Fabric pipelines on this PC: orchestration plan

Fabric Desktop by KratosBI's program for importing, checking and, later, running Microsoft Fabric Data Factory pipelines on your own PC, so developing and testing a pipeline doesn't spend Fabric capacity. Feature IDs ORCH-001 to ORCH-010. The working state for whoever continues it is in the [continuation note](continuation.md); how to use what exists is in [Pipeline definitions](pipeline-definitions.md).

## Rules for the whole program

- Nothing here consumes Fabric capacity or calls a tenant without the person's explicit go. ORCH-001 to ORCH-003 make no Fabric calls at all.
- No claim that a run on this PC behaves like Fabric unless that has been tested against Fabric. Anything untested is labelled Preview.
- Expressions are read, never evaluated by a general-purpose evaluator, and imported code is never run while reading a definition.
- Reuse what exists: the Service methods (desktop, CLI), the capability registry for external tools (`integrations/`), the job queue and run history for runs, Prove's execution path for notebooks.
- No AI in the app. External tools reach these features through the capability registry (MCP and the local API).
- Git belongs to the Git-native program; this program reads the Fabric Git folder shape but never writes to Git or a workspace.

## Status

| ID | Feature | Status | PR | Depends on |
| --- | --- | --- | --- | --- |
| ORCH-001 | Pipeline definition engine: import, validate, inspect, export | Merged | #130 | none |
| ORCH-002 | Activity compatibility registry | In review | #150 | ORCH-001 |
| ORCH-003 | Local pipeline execution engine (DAG, states, cancel, timeouts, run records) | Planned, waits for ORCH-002 approval | | ORCH-001, ORCH-002 |
| ORCH-004 to ORCH-010 | Later stages (breakpoints, step and replay, more adapters, Fabric hand-off) | Not defined yet | | ORCH-003 |

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
- `fabric_capacity` says whether an activity can only run on Fabric capacity. It doesn't estimate capacity units; that belongs to the FinOps program.
- Activity policy (timeout, retry, secure input and output) is read but not applied.

**Rollback.** Revert the PR. It adds two modules, two Service methods (and their names in `server.METHODS` and `audit.READ_ONLY`), two capabilities and two CLI subcommands; nothing is stored.

## ORCH-003: local pipeline execution engine (planned)

A DAG scheduler over ORCH-001's model with adapters declared in ORCH-002: dependency order, bounded parallel branches, the states pending, ready, running, succeeded, failed, skipped and cancelled, dependency conditions, cancellation, timeouts, stable run and activity run ids, and run records that survive a restart. It reuses the job queue (`jobqueue.py`) and run history rather than adding a scheduler (coordinated with the Workload program, which owns them), calls Prove's execution path for notebooks, never runs unsupported or cloud-required activities, never submits work to Fabric, and never replays a non-idempotent activity after a restart without asking. A fixture-only reference pipeline (checkpoint, mock ingest, mock transform, validate, record) runs with no Fabric connectivity.

## Next recommended PR

ORCH-003, once ORCH-002 is reviewed and Chris authorizes it. It adds the first executing adapters (control flow and the mock activities of the reference pipeline) and moves them to `local` in the registry with their tests.
