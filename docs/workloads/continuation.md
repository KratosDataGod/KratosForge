# Workload operations: continuation note

The handoff note for the workload operations program ([plan.md](plan.md)). Every PR in the program updates it. Read it first when resuming.

## State

| Field | Value |
| --- | --- |
| Last updated | 2026-10-16 |
| Base | `main` at `a471c8b` |
| Stage | 0 (plan) merged (#60); stage 1 engine merged (#63); stage 1 UI open as draft #72, waiting on Chris's reaction to the mockup |

## Active branches and PRs

| Branch | PR | What | Status |
| --- | --- | --- | --- |
| `claude/workload-management-plan-4su03b` | #60 | This plan and note | Merged |
| `claude/workload-inventory-4su03b` | #63 | Stage 1 PR 1A: engine, RPCs, tests | Merged |
| `claude/workload-ui-4su03b` | #72 | Stage 1 PR 1B: Monitor > Workloads, workload page, Home card, guide | Draft |

## Completed backlog items

None yet.

## Work in progress

- Stage 1 UI is draft #72. Together with #63 it covers in part: inventory, ownership, health, readiness, workload page, actions with preflight, Home.
- Open product question in #72: Home now has both "Needs your attention" (everything) and "Your workloads" (what you own); whether the new card replaces the old list is Chris's call.
- How the screens were checked: a scratch harness serves `app/ui` with a fake shell whose `rpc` calls a real `Service` seeded with a project, an injected failed Test run and a fake Fabric, driven by Playwright with `/opt/pw-browsers` Chromium. Not in the repo; rebuild it from `tests/test_workloads.py` helpers if needed.
- Mockup of Monitor > Workloads, the workload page, Home's "Your workloads" and the Retry confirmation: https://claude.ai/artifact/MYaQsSXDJ9iAmpo6nYePJf (waiting on Chris).

## Assumptions and decisions

- Workload kinds in stage 1: metadata entities (open project), notebooks on this PC, Fabric notebooks, pipelines and Spark job definitions in the workspaces this app works in.
- Management records (assignments, criticality, tags, action log) are local in `workloads.sqlite` until stage 2B (decision W1, default platform catalog).
- Incidents will live in `meta.incidents` per environment (decision W2).
- Ownership coordination (from the project's coordinator, 2026-10-16): the roadmap thread owns agent status records, the pipeline job contract, release records, run correlation (`ran_on`, PR #58); Prove owns receipts, the planner, placement and the cost model. This program owns the desktop UI over them and always-on monitoring and incidents. Shared files to coordinate before changing: Monitor and Home in `app/ui/app.js`, Data mesh and Platform pages, `agent.py`, `jobqueue.py`.

## Validation evidence

- 2026-10-16: `python -m pytest -q` in `sidecar/` on `af11938`: 322 passed, 2 skipped (Linux, Python 3.13, pinned constraints).
- 2026-10-16, #72 after merging main: 456 passed, 2 skipped; UI tests 6 passed.
- 2026-10-16, #63: 348 passed, 2 skipped. 10,000 synthetic workloads: build and assess 0.9 s, each list query 70 to 240 ms (a quadratic join at 4.1 s was fixed first).

## External dependencies and blockers

- None block stage 1. Stage 2B needs a UDF_Platform update path. Stage 5 needs the roadmap's agent records. Stage 7 needs the roadmap's release records and Prove's receipts.
- Everything is Preview until tried on a tenant; code signing and a tenant smoke test remain Chris's (project memory).

## Next action

Keep #72 green and mergeable (main moves often; merge it in). When Chris reacts to the mockup, apply his changes and mark #72 ready. Then start stage 2 (incidents and objectives on the agent, decision W2) on a new branch from main, telling the coordinator before touching `agent.py` or `jobqueue.py`.

## Resume prompt

> Continue the workload operations program in KratosDataGod/fabric-desktop. Read `docs/workloads/continuation.md` and `docs/workloads/plan.md` on the newest program branch, check the open PRs it lists, then do its "Next action".
