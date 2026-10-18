# Fabric pipelines on this PC: continuation note

The working state of the [orchestration plan](plan.md). Whoever picks this up next starts here. Updated with every PR.

Last updated: 2026-10-18.

## Branches and PRs

| Feature | Branch | PR | State |
| --- | --- | --- | --- |
| ORCH-001 Pipeline definition engine | `claude/orch-001-pipeline-definitions-b6ql2h` | #130 | Merged 2026-10-17 |
| ORCH-002 Activity compatibility registry | `claude/orch-001-pipeline-definitions-b6ql2h` (restarted from main) | #150 | Open for review |
| ORCH-003 Local pipeline execution engine | built on ORCH-002 | see GitHub | Open for review |

Chris authorized ORCH-002 on 2026-10-18 and ORCH-003 the same day ("Good idea. Do this", answering the recommended next PR). The next stage needs his go. Chris merges PRs himself.

## Where things are

- Code: `sidecar/fabric_desktop/orchestration/` (ORCH-002: `registry.py`, `activities.py`; ORCH-003: `evaluate.py`, `adapters.py`, `runner.py`, `runs.py`), `sidecar/fabric_desktop/pipeline_service.py`.
- Tests and fixtures: `sidecar/tests/test_pipeline_definitions.py`, `sidecar/tests/test_activity_registry.py`, `sidecar/tests/test_pipeline_runs.py`, `sidecar/tests/pipelines/` (`reference_local.json` is the ORCH-003 reference pipeline).
- Run records: `<data>/pipeline_runs/<run id>.json`.
- Example compatibility report: `docs/orchestration/examples/orders-daily-compatibility.json` (a test keeps it current).
- External tools: the `_pipelines()` group in `sidecar/fabric_desktop/integrations/capabilities.py`.

## Decisions and assumptions

- 2026-10-17, Chris approved the ORCH-001 plan ("go") with these defaults: files and pasted JSON only, no `getDefinition` calls to the tenant; hand-built fixtures until he exports real ones from his non-prod workspace (secrets removed); no UI in ORCH-001, and any pipeline screen gets a mockup first.
- The import is not stored anywhere in ORCH-001. ORCH-003 decides where imported pipelines and their runs live.
- ORCH-002: nothing is `local` or `simulated` until an adapter that executes has tests; Fabric-only type names are unverified until a tenant export confirms them; changing an entry needs a `REGISTRY_VERSION` bump, a changelog line in `activity-registry.md` and the new fingerprint in the test.
- ORCH-003: the agent job queue isn't used (it can't schedule activities); a whole pipeline run could become one agent job later, with the Workload program. Notebook stays `static` until Prove's execution path takes parameters outside a proof. Expressions use only the functions in `evaluate.FUNCTIONS`; add one with a test and a line in local-runs.md. Timeouts are real time; `wait_scale` shortens waits only.
- Diagnostics have `code`, `severity` (error, warning, info), `message`, `path` (JSON path), `activity` and `hint`. Codes are stable: tools and later stages may match on them.

## Overlaps with other programs

- Scale and operations (`docs/scale-and-operations-plan.md`): the pipeline mailbox (`mailbox.py`) and agent status records are the hand-off from a pipeline running in Fabric; ORCH-003's run records extend those rather than replacing them.
- Workload operations: owns `jobqueue.py`, `agent.py`, Monitor and Home. Coordinate before ORCH-003 changes them.
- Prove this notebook: owns notebook execution; a Notebook adapter calls its execution path.
- Bring your own AI: owns the capability registry; ORCH capabilities register there.
- Administration and FinOps: owns CU measurement and the execution advisor; ORCH-002 only flags activities that need Fabric capacity.
- Git-native development: owns everything Git.

## Open questions for Chris

- Real pipeline exports for fixtures (two or three, secrets removed). They would also confirm the Fabric-only activity type names.
- Whether ORCH-001 may later read a pipeline straight from a workspace with `getDefinition` (a read; no capacity use).
