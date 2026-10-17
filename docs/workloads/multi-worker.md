# Multiple workers (FD-124): design for review

Status: proposal. Nothing here is built. The staged roadmap thread owns the pipeline mailbox (`mailbox.py`, roadmap 7.1) and the status records (7.2). It agreed to this design on 2026-10-17, within mailbox contract 1, and will review the engine PR. This program owns FD-124 and that PR, and roadmap 7.3 (FD-12) points here. The PR still needs Chris's go. None of this has run on a tenant: conditional requests on OneLake (FD-2) are unproven there. Until roadmap 7.1 has run on a tenant, the docs and the PR say so.

Part of the [workload operations plan](plan.md). Fabric Desktop by KratosBI identifiers are unchanged.

## Problem

Each agent keeps its own queue (`jobqueue.py`, SQLite on its machine). So:

- A schedule runs on the one agent whose `agent.toml` names it. If that agent is down, the schedule waits for it, however many other agents are idle.
- Two agents given the same schedule both run it. Table leases stop them writing at the same moment, but not the second run. [Agents](../user-guide.md#agents-preview) now warns about this as "Scheduled in two places".
- Pipeline requests already go to exactly one agent: the claim is a conditional create of `status/<id>.json`. But an agent claims every request it sees, busy or not, and a request whose agent dies stays with that agent until it is back.

## What already exists to build on

| Piece | What it gives |
| --- | --- |
| Mailbox claim (`If-None-Match: *` on `status/<id>.json`) | Exactly one agent takes a request, however many poll |
| Status records with `stale_after` | Any reader can tell an agent is gone without reaching its machine |
| Table leases | Two writers never write one table at once |
| `jobqueue.recover` | An agent re-runs a job its own dead worker held, when it restarts |
| Job id as dedupe key | A crash between claim and queue doesn't run a request twice |

## Proposal

Shared work goes through the environment's mailbox, which every agent serving that environment already reads. No new service, no new store, no network port.

1. **Shared schedules.** A schedule in `agent.toml` can say `shared = true`. Give the same schedule (same `id`, `env`, entities and times) to several agents. When it is due, each of them writes `requests/sched-<id>-<slot>.json` with `If-None-Match: *`. Only the first write lands, so each due time makes one request. The agents' configs must match, so the app warns when two agents' copies of a shared schedule differ.
   - The slot comes from the due time in UTC, never from when an agent noticed it. A `cron` schedule's slot is `floor(epoch / (every_minutes * 60))`. A daily or weekly schedule's slot is the date and time it was due, for example `20270115T0200`. Agents whose clocks differ by seconds still name the same file.
   - A slot's request may be created only until the slot's deadline: the next slot, or 6 hours for daily and weekly schedules. The request carries that deadline, so the existing `expired` state covers a late claim. An agent that comes up after the deadline doesn't create the request, so a run that is over, or has expired, never comes back.
2. **Claim only with room.** An agent claims a request only while it has a free worker (`running < concurrency`). It leaves the rest for an agent that does. Today's behavior stays the default for unshared requests; this is `[pipeline] claim = "when_free"`, with `"always"` the default until it has run on a tenant.
3. **Take over from a lost agent.** A request whose status is `accepted` or `running` belongs to the agent named in it. Once that agent's own status record is past `stale_after`, another agent may take the request over. It does this by replacing the status with `If-Match` on the etag it read (`leases.py` already supports this), so exactly one agent takes it over.
   - The new agent runs the request through its normal path. Before each table it takes the table's lease in `leases.py`, so it waits for a lease the lost agent still holds to run out. Two agents never write one table, even for one table's worth of time.
   - A takeover counts as one attempt, using the existing `max_attempts`. After that many, the request is `failed`, with every agent that tried it named, so a request that crashes every agent doesn't move round the pool forever.
4. **What the app shows.** [Queue](../user-guide.md#queue-preview) labels a shared schedule's row "shared by etl-01, etl-02". A run that was taken over names both agents. "Scheduled in two places" no longer flags agents that share a schedule this way.

## What it does not do

- It is not one global queue. Each environment's mailbox is its own, so a workspace's readers still see only their own environment's work.
- It doesn't move a running run while its agent is alive and reporting. It takes a run over only from an agent that stopped reporting.
- It doesn't share this PC's schedules with agents. Windows Task Scheduler stays separate.

## Risks to settle before building

- **Clock skew.** Slot ids come from the due time, not from the time an agent noticed it (point 1). Skew only changes which agent writes the request first. Still to check with real machines: that an agent that is a whole interval behind doesn't create the next slot early.
- **A paused agent that comes back.** An agent can freeze longer than `stale_after` and then wake up still running. Its next status update in `Mailbox._sync` is an `If-Match` replace, and after a takeover it fails on the etag. The agent treats that failure as "this is no longer mine" and stops, rather than as a write to retry. That is stronger than re-reading the status before each table. A table write already under way can't be stopped by a check, so the table lease is what keeps the two agents apart (see point 3).
- **OneLake conditional writes.** These are proven for create (7.1). Replace with `If-Match` is used by the incident log, but hasn't run on a tenant for takeover.
- **Cost.** Each agent already polls the mailbox every `poll_seconds`. Shared schedules add one conditional write per due slot per agent, and most of those writes fail fast.

## Acceptance (when built)

- With three agents sharing an hourly schedule, each hour has exactly one request and one run. This must hold over 1,000 simulated slots with random agent restarts, using the same race harness as the incident log, where 8 evaluators open one incident.
- Kill the agent running a shared run. Another agent takes it over within `stale_after` plus one poll. No table gets two writers, and the run log shows one attempt per agent.
- Pause an agent past `stale_after`, then resume it. It stops at its next status update and writes no table that the agent that took over holds.
- A request that crashes every agent ends `failed` after `max_attempts`.
- With `claim = "when_free"`, a busy agent never holds an unstarted request while another agent has a free worker.
- Every new field is optional within status contract 1 and mailbox contract 1. An older agent reading these files ignores them and behaves as today.

## Order

1. The roadmap thread agreed on 2026-10-17. Chris says go.
2. Engine PR: shared schedules and `when_free`, behind config, with the race tests. Takeover follows in a separate PR. Both are labelled untested on a tenant until 7.1 has run there.
3. UI PR: the Queue and Agents labels in step 4 of the proposal.
