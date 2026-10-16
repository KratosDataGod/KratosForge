# Workload operations: continuation note

The handoff note for the workload operations program ([plan.md](plan.md)). Every PR in the program updates it. Read it first when resuming.

## State

| Field | Value |
| --- | --- |
| Last updated | 2026-10-16 |
| Base | `main` at `af11938` |
| Stage | 0 (plan) done in this PR; stage 1 next |

## Active branches and PRs

| Branch | PR | What | Status |
| --- | --- | --- | --- |
| `claude/workload-management-plan-4su03b` | planning PR | This plan and note | Open |

## Completed backlog items

None yet.

## Work in progress

- Stage 1 PR 1A (engine): `sidecar/fabric_desktop/workloads/`, `workload_service.py`, RPCs, tests.
- Mockup of Monitor > Workloads, the workload page and Home's "Your workloads" for Chris.

## Assumptions and decisions

- Workload kinds in stage 1: metadata entities (open project), notebooks on this PC, Fabric notebooks, pipelines and Spark job definitions in the workspaces this app works in.
- Management records (assignments, criticality, tags, action log) are local in `workloads.sqlite` until stage 2B (decision W1, default platform catalog).
- Incidents will live in `meta.incidents` per environment (decision W2).
- Ownership coordination (from the project's coordinator, 2026-10-16): the roadmap thread owns agent status records, the pipeline job contract, release records, run correlation (`ran_on`, PR #58); Prove owns receipts, the planner, placement and the cost model. This program owns the desktop UI over them and always-on monitoring and incidents. Shared files to coordinate before changing: Monitor and Home in `app/ui/app.js`, Data mesh and Platform pages, `agent.py`, `jobqueue.py`.

## Validation evidence

- 2026-10-16: `python -m pytest -q` in `sidecar/` on `af11938`: 322 passed, 2 skipped (Linux, Python 3.13, pinned constraints).

## External dependencies and blockers

- None block stage 1. Stage 2B needs a UDF_Platform update path. Stage 5 needs the roadmap's agent records. Stage 7 needs the roadmap's release records and Prove's receipts.
- Everything is Preview until tried on a tenant; code signing and a tenant smoke test remain Chris's (project memory).

## Next action

Build stage 1 PR 1A on a new branch from `main`, publish the mockup, then PR 1B.

## Resume prompt

> Continue the workload operations program in KratosDataGod/fabric-desktop. Read `docs/workloads/continuation.md` and `docs/workloads/plan.md` on the newest program branch, check the open PRs it lists, then do its "Next action".
