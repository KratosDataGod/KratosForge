# Workload operations: continuation note

The handoff note for the workload operations program ([plan.md](plan.md)). Every PR in the program updates it. Read it first when resuming.

## State

| Field | Value |
| --- | --- |
| Last updated | 2026-10-16 |
| Base | `main` at `f456edd` |
| Stage | 0 (plan) merged (#60); stage 1 engine merged (#63); stage 1 UI draft #72 (waiting on Chris's mockup reaction); stage 2.1/2.2 engine open (incidents and objectives) |

## Active branches and PRs

| Branch | PR | What | Status |
| --- | --- | --- | --- |
| `claude/workload-management-plan-4su03b` | #60 | This plan and note | Merged |
| `claude/workload-inventory-4su03b` | #63 | Stage 1 PR 1A: engine, RPCs, tests | Merged |
| `claude/workload-ui-4su03b` | #72 | Stage 1 PR 1B: Monitor > Workloads, workload page, Home card, guide | Draft |
| `claude/workload-incidents-4su03b` | #74 | Stage 2.1/2.2: incident log, evaluation on the agent and in the app, Teams outbox, objectives | Open |

The incidents PR and #72 touch different files except this note and plan.md; whichever merges second takes both sets of rows.

## Completed backlog items

None fully yet. In part: 43 (objectives: success rate; completion time is the freshness `by`), 44 (agent evaluates on a timer; the app evaluates on request, no screens yet), 45 (detect, acknowledge, assign, note, resolve, recover; no UI), 47 (dedup across evaluators, digest, delayed delivery; no per-team routes).

## Work in progress

- Stage 1 UI is draft #72. Open product question there: Home has both "Needs your attention" and "Your workloads"; whether the new card replaces the old list is Chris's call.
- How the screens were checked: a scratch harness serves `app/ui` with a fake shell whose `rpc` calls a real `Service` seeded with a project, an injected failed Test run and a fake Fabric, driven by Playwright with `/opt/pw-browsers` Chromium. Not in the repo; rebuild it from `tests/test_workloads.py` helpers if needed.
- Mockup: https://claude.ai/artifact/MYaQsSXDJ9iAmpo6nYePJf (waiting on Chris).
- Stage 2 engine: `workloads/incidents.py` (log), `workloads/evaluate.py` (signals), `workloads/notify.py` (Teams outbox), `meta/objectives.py`, `incident_service.py` (RPCs), `Agent.evaluate_incidents` (`[incidents]`, opt-in).

## Assumptions and decisions

- Workload kinds in stage 1: metadata entities (open project), notebooks on this PC, Fabric notebooks, pipelines and Spark job definitions in the workspaces this app works in.
- Management records (assignments, criticality, tags, action log) are local in `workloads.sqlite` until stage 2B (decision W1, default platform catalog).
- Incidents (decision W2, revised 2026-10-16): one JSON document per environment, `incidents/incidents.json` in the control area of LH_Meta (beside `agents/` and the pipeline mailbox), changed only by etag-conditional writes. Chosen over a `meta.incidents` Delta table because a small document with If-Match gives exact once-only opening across evaluators, needs no new table, and the agent already writes there. Keeps open incidents plus the last 200 resolved, not 400 days.
- `[incidents]` on the agent is off unless enabled, so upgrading an agent doesn't start new writes or Teams messages. The app evaluates only when asked (`incidents_evaluate`), never on a timer.
- `freshness_alerts.json` is not imported: late incidents are derived again from the run log, so importing would only suppress one Teams message. The app's own freshness alerts stay until 2.3 moves them onto incidents.
- The local control backend's `create` now writes aside and hard-links into place (falls back to create-in-place without hard links): readers could see a half-written file and win a race. Its lock is per process, so two processes on one PC racing on a local environment is still weaker than OneLake's If-Match (pre-existing).
- Ownership coordination (from the project's coordinator, 2026-10-16): the roadmap thread owns agent status records, the pipeline job contract, release records, run correlation (`ran_on`, PR #58); Prove owns receipts, the planner, placement and the cost model. This program owns the desktop UI over them and always-on monitoring and incidents. Shared files to coordinate before changing: Monitor and Home in `app/ui/app.js`, Data mesh and Platform pages, `agent.py`, `jobqueue.py`. The coordinator was told about the `Agent.step` evaluation before it was written.

## Validation evidence

- 2026-10-16: `python -m pytest -q` in `sidecar/` on `af11938`: 322 passed, 2 skipped (Linux, Python 3.13, pinned constraints).
- 2026-10-16, #63: 348 passed, 2 skipped. 10,000 synthetic workloads: build and assess 0.9 s, each list query 70 to 240 ms (a quadratic join at 4.1 s was fixed first).
- 2026-10-16, #72 after merging main: 456 passed, 2 skipped; UI tests 6 passed.
- 2026-10-16, stage 2: `tests/test_incidents.py` 20 passed, five runs in a row (8 evaluators racing on one backend open one incident). Full suite after merging main at `f456edd`: 505 passed, 2 skipped.

## External dependencies and blockers

- None block stages 1 and 2. Stage 2B needs a UDF_Platform update path. Stage 5 needs the roadmap's agent records. Stage 7 needs the roadmap's release records and Prove's receipts.
- Everything is Preview until tried on a tenant; code signing and a tenant smoke test remain Chris's (project memory). OneLake If-Match on control files is the thing to confirm live (agent.md, Verify step 7).

## Next action

Keep #72 and the incidents PR green and mergeable. When Chris reacts to the mockup, apply his changes and mark #72 ready. Then stage 2.3 on a new branch from main once both are in: incident screens in Monitor and the workload page (mockup first), the app's freshness alerts moved onto incidents, per-team routes, quiet periods, escalation, and a missing-heartbeat incident from `agents/<name>.json`.

## Resume prompt

> Continue the workload operations program in KratosDataGod/fabric-desktop. Read `docs/workloads/continuation.md` and `docs/workloads/plan.md` on the newest program branch, check the open PRs it lists, then do its "Next action".
