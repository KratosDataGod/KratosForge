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
| ORCH-001 | Pipeline definition engine: import, validate, inspect, export | In review | see [continuation](continuation.md) | none |
| ORCH-002 | Activity compatibility registry | Planned, waits for ORCH-001 approval | | ORCH-001 |
| ORCH-003 | Local pipeline execution engine (DAG, states, cancel, timeouts, run records) | Planned | | ORCH-001, ORCH-002 |
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

## ORCH-002: activity compatibility registry (planned)

A versioned registry describing, per activity type and property, whether Fabric Desktop can run it on this PC, simulate it, only check it statically, or must leave it to Fabric, plus implemented versus planned, the sign-in it needs, its side effects and external dependencies, and whether it needs paid Fabric capacity. Every imported activity gets a classification; unknown types are `unsupported` and kept. No activity is labelled locally executable until ORCH-003 ships a working adapter with tests. Reports come from ORCH-001's schema record, with no cloud calls. Registered in the capability registry for external tools.

## ORCH-003: local pipeline execution engine (planned)

A DAG scheduler over ORCH-001's model with adapters declared in ORCH-002: dependency order, bounded parallel branches, the states pending, ready, running, succeeded, failed, skipped and cancelled, dependency conditions, cancellation, timeouts, stable run and activity run ids, and run records that survive a restart. It reuses the job queue (`jobqueue.py`) and run history rather than adding a scheduler (coordinated with the Workload program, which owns them), calls Prove's execution path for notebooks, never runs unsupported or cloud-required activities, never submits work to Fabric, and never replays a non-idempotent activity after a restart without asking. A fixture-only reference pipeline (checkpoint, mock ingest, mock transform, validate, record) runs with no Fabric connectivity.

## Next recommended PR

ORCH-002, once ORCH-001 is reviewed and Chris authorizes it.
