# Workload operations: continuation note

The handoff note for the workload operations program ([plan.md](plan.md)). Every PR in the program updates it. Read it first when resuming.

## State

| Field | Value |
| --- | --- |
| Last updated | 2026-10-17 |
| Base | `main` at `f16ba6a` |
| Stage | Stages 0, 1, 2.1/2.2 and 2.3A (#79) merged. Stage 2.3B (incident screens, #125) ready for review; 2.3C (the app's freshness alerts as incidents) open after it. |

## Active branches and PRs

| Branch | PR | What | Status |
| --- | --- | --- | --- |
| `claude/workload-incident-screens-4su03b` | #125 (2.3B) | Monitor > Incidents, incident page, workload page card, agent incident settings in status records | Ready for review |
| `claude/workload-freshness-incidents-4su03b` | 2.3C | The app checks incidents every 10 minutes for environments no running agent checks; bell and Teams alerts come from incidents | Open, merge after #125 |

## Completed backlog items

44 (agent evaluates on a timer; the app evaluates on request), 45 (lifecycle in engine and screens; mitigation is a note). In part: 38 (agent-offline incident; version and capability views in stage 5), 43 (objectives: success rate; completion time is the freshness `by`), 47 (dedup, digest, delayed delivery, routes, quiet hours, escalation; routes are per agent, not shared).

## Work in progress

- 2.3B (#125) ready for review; merge main into it once #122 lands. 2.3C built on top of it: `incidents_watch` checks each environment in one of three modes (agent: a live agent with incidents on checks it, the app only reads; app: the app evaluates and posts to Teams; unreadable). With Preview features hidden the old per-table freshness alerts stay as they were.
- Known gap: an environment that isn't set up can show a "Can't check" incident in the harness; check on a real project whether the app should skip it.
- Home question still open for Chris: Home has both "Needs your attention" and "Your workloads"; whether the card replaces the list.
- How the screens were checked: a scratch harness serves `app/ui` with a fake shell whose `rpc` calls a real `Service` seeded with a project, a failed Test run, two agent status records (one live with incident settings, one offline) and an escalated incident, driven by Playwright with `/opt/pw-browsers` Chromium. Not in the repo; rebuild it from `tests/test_workloads.py` helpers if needed.
- Engine map: `workloads/incidents.py` (log, escalate), `workloads/evaluate.py` (signals, `agent_signals`), `workloads/classify.py`, `workloads/agents.py`, `workloads/notify.py`, `meta/objectives.py`, `incident_service.py` (RPCs incl. `incidents_summary`), `Agent.evaluate_incidents`. UI: `app/ui/incidents.js`.

## Assumptions and decisions

- Workload kinds in stage 1: metadata entities (open project), notebooks on this PC, Fabric notebooks, pipelines and Spark job definitions in the workspaces this app works in.
- Management records (assignments, criticality, tags, action log) are local in `workloads.sqlite` until stage 2B (decision W1, default platform catalog).
- Incidents (decision W2, revised 2026-10-16): one JSON document per environment, `incidents/incidents.json` in the control area of LH_Meta (beside `agents/` and the pipeline mailbox), changed only by etag-conditional writes. Chosen over a `meta.incidents` Delta table because a small document with If-Match gives exact once-only opening across evaluators, needs no new table, and the agent already writes there. Keeps open incidents plus the last 200 resolved, not 400 days.
- `[incidents]` on the agent is off unless enabled, so upgrading an agent doesn't start new writes or Teams messages. The app evaluates only when asked (`incidents_evaluate`), never on a timer.
- `freshness_alerts.json` is not imported: late incidents are derived again from the run log, so importing would only suppress one Teams message. The app's own freshness alerts stay until 2.3 moves them onto incidents.
- The local control backend's `create` now writes aside and hard-links into place (falls back to create-in-place without hard links): readers could see a half-written file and win a race. Its lock is per process, so two processes on one PC racing on a local environment is still weaker than OneLake's If-Match (pre-existing).
- Ownership coordination (from the project's coordinator, 2026-10-16): the roadmap thread owns agent status records, the pipeline job contract, release records, run correlation (`ran_on`, PR #58); Prove owns receipts, the planner, placement and the cost model. This program owns the desktop UI over them and always-on monitoring and incidents. Shared files to coordinate before changing: Monitor and Home in `app/ui/app.js`, Data mesh and Platform pages, `agent.py`, `jobqueue.py`. The coordinator was told about the `Agent.step` evaluation before it was written.

- Recovery is scoped by kind (stage 2.3A): `sync(evaluated=...)` takes the set of kinds that were actually read, so unreadable agent records never recover agent-offline incidents and an unreadable run log never recovers workload incidents.
- Routes live in each agent's `agent.toml`, with webhooks only in environment variables. The app keeps its single Teams webhook setting. Sharing routes across agents waits for stage 2B's platform catalog.
- Escalation is a Teams post to a named route, once per incident, only while nobody acknowledged it. No paging service.
- Ownership (from the coordinator, 2026-10-17): the Git-native program owns Source Control, Fabric Git sync, deployment workflow and validation, and later environment profiles; coordinate before release/deploy screens or environment config UI. The enterprise program assigned FD-122 (agent operations dashboard), FD-123 (incidents), FD-124 (multi-worker) and governance views here; it owns FD-129 (exceptions) and FD-130 (multi-team permissions). Name in docs and UI: "Fabric Desktop by KratosBI"; identifiers unchanged.

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

Keep #125 and 2.3C green and merge main into them once #122 lands. Then FD-122 agent operations dashboard (stage 5, mockup first; the roadmap thread owns agent status records), FD-124 multi-worker, and governance views; check with the enterprise thread on FD-129/130 first. Cost allocation consumes the FinOps thread's measurements.

## Resume prompt

> Continue the workload operations program in KratosDataGod/fabric-desktop. Read `docs/workloads/continuation.md` and `docs/workloads/plan.md` on the newest program branch, check the open PRs it lists, then do its "Next action".
