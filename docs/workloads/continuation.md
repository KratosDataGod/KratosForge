# Workload operations: continuation note

The handoff note for the workload operations program ([plan.md](plan.md)). Every PR in the program updates it. Read it first when resuming.

## State

| Field | Value |
| --- | --- |
| Last updated | 2026-10-23 |
| Base | `main` after #569 |
| Stage | Stages 0 to 5, FD-124 parts 1 and 2 with labels, 9A (engine and Run as screen), UX-010 and UX-014 merged, plus #205 (platform notices to Teams), #208, #215 and #216. Monitor has three tabs: Runs, Schedules, Operations. Cost moved to Optimize > Cost and savings (testing item 36). Since then: item 76 stage 2B (#432), platform notices UI (#450, #452), and P3 pipeline runs in Monitor > Runs (this PR). |

## Active branches and PRs

| Branch | PR | What | Status |
| --- | --- | --- | --- |
| `claude/workload-management-plan-4su03b` | new | P3: local pipeline runs in Monitor > Runs, opening to their activities (Preview), per the Pipelines P3+P4 mockup Chris approved 2026-10-23 | Draft; with quality and UX oversight |

Out of P3 (Chris's scope, 2026-10-23): Fabric pipeline runs in Monitor, rerun from failed, Open in KratosForge from Workspaces. The run view (artboard 4) belongs to the Local Fabric pipeline execution thread; Monitor's **Details** calls its `openPipelineRun(run_id)` (#578) and shows only once that is in the build.

## Completed backlog items

38 (agent-offline incident, Monitor > Agents with version and state), 44 (agent evaluates on a timer; the app checks environments no running agent checks), 45 (lifecycle in engine and screens; mitigation is a note). In part: 31 and 37 (agent page shows what each agent runs and its last jobs; no owner yet), 43 (objectives: success rate; completion time is the freshness `by`), 47 (dedup, digest, delayed delivery, routes, quiet hours, escalation; routes are per agent, not shared).

## Work in progress

- Chris chose "Build now" for FD-124 (2026-10-18). Parts 1 and 2 are built. Step 4 labels are built too (no "taken over from" label: it would need the app to read mailbox status files). Governance split agreed 2026-10-18: this program has items 71, 78 and 79; the governance thread has 72 to 75 and 80, and wants 71 and 78 as queryable data (`run_as_list`). Chris chose "In Operations" for Run as (2026-10-18), so this program owns UX-010, the Monitor restructure (UX thread mockup https://claude.ai/artifact/YDTVabyWUFLNLMDaNLWRJ3, artboard 2). Cost allocation is #353.
- The roadmap thread (owner of `agent_status.py`) agreed on 2026-10-17 to `owner`, `identity`, and each schedule's `entities` and `when`, all within contract 1. A schedule's empty `entities` means every table; a missing one means an older agent, shown as unknown.
- Boards 7 and 8 shipped as built in #133; Chris hasn't commented on them. Changes he asks for go in a follow-up PR.
- Fixed (2026-10-25): an environment that isn't set up (no workspace assigned) opens no "Can't check" incident on the app or the agent, and clears one left from before; its workload incidents stay as they are.
- Home question still open for Chris: Home has both "Needs your attention" and "Your workloads"; whether the card replaces the list.
- How the screens were checked: a scratch harness serves `app/ui` with a fake shell whose `rpc` calls a real `Service` seeded with a project, a failed Test run, two agent status records (one live with incident settings, one offline) and an escalated incident, driven by Playwright with `/opt/pw-browsers` Chromium. Not in the repo; rebuild it from `tests/test_workloads.py` helpers if needed.
- Engine map: `workloads/incidents.py` (log, escalate), `workloads/evaluate.py` (signals, `agent_signals`), `workloads/classify.py`, `workloads/agents.py`, `workloads/notify.py`, `workloads/platform_notify.py` + `platform_notify_service.py` (governance's request/shortcut notices), `meta/objectives.py`, `incident_service.py` (RPCs incl. `incidents_summary`), `Agent.evaluate_incidents`. UI: `app/ui/incidents.js`.

## Assumptions and decisions

- Workload kinds in stage 1: metadata entities (open project), notebooks on this PC, Fabric notebooks, pipelines and Spark job definitions in the workspaces this app works in.
- Management records (assignments, criticality, tags, action log) are local in `workloads.sqlite` until stage 2B (decision W1, default platform catalog).
- Incidents (decision W2, revised 2026-10-16): one JSON document per environment, `incidents/incidents.json` in the control area of LH_Meta (beside `agents/` and the pipeline mailbox), changed only by etag-conditional writes. Chosen over a `meta.incidents` Delta table because a small document with If-Match gives exact once-only opening across evaluators, needs no new table, and the agent already writes there. Keeps open incidents plus the last 200 resolved, not 400 days.
- `[incidents]` on the agent is off unless enabled, so upgrading an agent doesn't start new writes or Teams messages. The app evaluates only when asked (`incidents_evaluate`), never on a timer.
- `freshness_alerts.json` is not imported: late incidents are derived again from the run log, so importing would only suppress one Teams message. The app's own freshness alerts moved onto incidents: Teams hears about a table only from its incident, with Preview on or off. An incident opening that an older app already posted (a key still in `freshness_alerts.json`, which is no longer written) is not posted again.
- The local control backend's `create` now writes aside and hard-links into place (falls back to create-in-place without hard links): readers could see a half-written file and win a race. Its lock is per process, so two processes on one PC racing on a local environment is still weaker than OneLake's If-Match (pre-existing).
- Ownership coordination (from the project's coordinator, 2026-10-16): the roadmap thread owns agent status records, the pipeline job contract, release records, run correlation (`ran_on`, PR #58); Prove owns receipts, the planner, placement and the cost model. This program owns the desktop UI over them and always-on monitoring and incidents. Shared files to coordinate before changing: Monitor and Home in `app/ui/app.js`, Data mesh and Platform pages, `agent.py`, `jobqueue.py`. The coordinator was told about the `Agent.step` evaluation before it was written.

- Recovery is scoped by kind (stage 2.3A): `sync(evaluated=...)` takes the set of kinds that were actually read, so unreadable agent records never recover agent-offline incidents and an unreadable run log never recovers workload incidents.
- Routes live in each agent's `agent.toml`, with webhooks only in environment variables. The app keeps its single Teams webhook setting. Sharing routes across agents waits for stage 2B's platform catalog.
- Escalation is a Teams post to a named route, once per incident, only while nobody acknowledged it. No paging service.
- Ownership (from the coordinator, 2026-10-17): the Git-native program owns Source Control, Fabric Git sync, deployment workflow and validation, and later environment profiles; coordinate before release/deploy screens or environment config UI. The enterprise program assigned FD-122 (agent operations dashboard), FD-123 (incidents), FD-124 (multi-worker) and governance views here; it owns FD-129 (exceptions) and FD-130 (multi-team permissions). Name in docs and UI: "KratosForge"; identifiers unchanged.

## Validation evidence

- 2026-10-16: `python -m pytest -q` in `sidecar/` on `af11938`: 322 passed, 2 skipped (Linux, Python 3.13, pinned constraints).
- 2026-10-16, #63: 348 passed, 2 skipped. 10,000 synthetic workloads: build and assess 0.9 s, each list query 70 to 240 ms (a quadratic join at 4.1 s was fixed first).
- 2026-10-16, #72 after merging main: 456 passed, 2 skipped; UI tests 6 passed.
- 2026-10-16, stage 2: `tests/test_incidents.py` 20 passed, five runs in a row (8 evaluators racing on one backend open one incident). Full suite after merging main at `f456edd`: 505 passed, 2 skipped.

- 2026-10-17, stage 2.3C: 982 passed, 2 skipped; UI tests 31 passed; incident tests 42. Harness bell showed one item per open incident and modes dev app, test agent, prod app.
- 2026-10-17, stage 2.3B on `cedcc56`: 982 passed, 2 skipped; UI tests 31 passed. Screens checked in the harness (list, detail, acknowledge, resolve dialog, workload card, empty Dev). Times show "Invalid Date" until #122 (the AI integration thread's fix for a duplicate global `when()`) is merged.
- 2026-10-17, stage 2.3A: `tests/test_incidents.py` 39 passed. Full suite on `6919e40`: 543 passed, 2 skipped; UI tests pass.

## External dependencies and blockers

- None block stages 1 and 2. Stage 2B needs a UDF_Platform update path. Stage 5 needs the roadmap's agent records. Stage 7 needs the roadmap's release records and Prove's receipts.
- Everything is Preview until tried on a tenant; code signing and a tenant smoke test remain Chris's (project memory). OneLake If-Match on control files is the thing to confirm live (agent.md, Verify step 7).

## Next action

Item 76 stage 2B (workload actions on the platform's audit) is up as a draft: `log_workload_action` in `central/core.py` and `udf.py`, the `audit_out` outbox in `workloads.sqlite`, and the line under Actions from KratosForge. In Fabric it needs Platform > Settings > Finish or repair once. Next: the bell and settings UI for platform notices, built after Chris reacts to the mockup (https://claude.ai/artifact/51h5YXyrhvEXoDJhyWEUdw; quiet-hours exemptions are by severity, and the ledger needs a title column), and Chris's open cards: what counts as a workload, and whether Runs and Schedules merge. Notebook run history and job progress inside the notebook belong to the notebook threads, not this program.

## Resume prompt

> Continue the workload operations program in KratosDataGod/KratosForge. Read `docs/workloads/continuation.md` and `docs/workloads/plan.md` on the newest program branch, check the open PRs it lists, then do its "Next action".
