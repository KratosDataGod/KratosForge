# Fabric pipelines on this PC: continuation note

The working state of the [orchestration plan](plan.md). Whoever picks this up next starts here. Updated with every PR.

Last updated: 2026-10-17.

## Branches and PRs

| Feature | Branch | PR | State |
| --- | --- | --- | --- |
| ORCH-001 Pipeline definition engine | `claude/orch-001-pipeline-definitions-b6ql2h` | see GitHub | Open for review |

ORCH-002 and ORCH-003 start only after Chris reviews and authorizes the PR before them. Chris merges PRs himself.

## Where things are

- Code: `sidecar/fabric_desktop/orchestration/`, `sidecar/fabric_desktop/pipeline_service.py`.
- Tests and fixtures: `sidecar/tests/test_pipeline_definitions.py`, `sidecar/tests/pipelines/`.
- External tools: the `_pipelines()` group in `sidecar/fabric_desktop/integrations/capabilities.py`.

## Decisions and assumptions

- 2026-10-17, Chris approved the ORCH-001 plan ("go") with these defaults: files and pasted JSON only, no `getDefinition` calls to the tenant; hand-built fixtures until he exports real ones from his non-prod workspace (secrets removed); no UI in ORCH-001, and any pipeline screen gets a mockup first.
- The import is not stored anywhere in ORCH-001. ORCH-003 decides where imported pipelines and their runs live.
- Diagnostics have `code`, `severity` (error, warning, info), `message`, `path` (JSON path), `activity` and `hint`. Codes are stable: tools and later stages may match on them.

## Overlaps with other programs

- Scale and operations (`docs/scale-and-operations-plan.md`): the pipeline mailbox (`mailbox.py`) and agent status records are the hand-off from a pipeline running in Fabric; ORCH-003's run records extend those rather than replacing them.
- Workload operations: owns `jobqueue.py`, `agent.py`, Monitor and Home. Coordinate before ORCH-003 changes them.
- Prove this notebook: owns notebook execution; a Notebook adapter calls its execution path.
- Bring your own AI: owns the capability registry; ORCH capabilities register there.
- Administration and FinOps: owns CU measurement and the execution advisor; ORCH-002 only flags activities that need Fabric capacity.
- Git-native development: owns everything Git.

## Open questions for Chris

- Real pipeline exports for fixtures (two or three, secrets removed).
- Whether ORCH-001 may later read a pipeline straight from a workspace with `getDefinition` (a read; no capacity use).
