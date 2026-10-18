# Incidents and your service desk (Preview)

Fabric Desktop by KratosBI finds problems by itself: a failed run, a late table, a run waiting on a decision, a missed reliability objective, an agent that stopped reporting, an environment it couldn't read. Each problem becomes one incident that everyone who can read the environment sees. This page covers what an incident holds, how priorities and SLAs work, how people report problems the app can't see, and where this is going. Day-to-day use of the list is in the [user guide](user-guide.md#incidents-preview); agents and their Teams routes are in the [agent guide](agent.md#incidents-preview).

## What an incident holds

| Field | What it is |
| --- | --- |
| **Number** | `INC-PROD-0042`: counted per environment and never changes. Incidents opened before numbers existed get one, oldest first, the next time the list is written. |
| **Priority** | P1 to P4 (below). |
| **Source** | **Detected** (the app or an agent found it) or **Reported** (a person reported it). |
| **State** | Open, Acknowledged or Resolved, with how it was resolved: **Recovered** (the problem cleared), **Cleared** (the environment could be read again) or **Resolved** (by a person, with a note). |
| **Timeline** | Every change with who made it and when: opened, seen again, priority changed, acknowledged, assigned, notes, escalated, recovered, resolved. The first entry and the newest 99 are kept. |
| **Owner, team, criticality** | From the workload's assignment when it opened; the owner can be changed. |
| **Evidence** | The run, table or agent it's about, and a likely cause for failed runs. |

Each environment keeps its incidents in one file, `incidents/incidents.json`, in its `LH_Meta` (open ones plus the last 200 resolved). Every change is a conditional write, so two agents or PCs that see the same failure at the same moment open one incident, and two people can't both take the same number. Moving this file, and a full history, to the central Fabric Desktop lakehouse is the next stage (see [What's coming](#whats-coming)).

## Priorities

| Priority | Means | Detected incidents that get it |
| --- | --- | --- |
| **P1** | Many people blocked, no workaround | A critical workload's run failing 3 times or more |
| **P2** | A team blocked, or due soon | Any other problem with a critical workload, or any problem with a high one |
| **P3** | Wrong or slow, with a workaround | Standard workloads, and anything without a criticality (an agent going offline, say) |
| **P4** | Minor, or a question | Low workloads, anything in Dev, and "can't evaluate" |

A detected incident follows these rules as it changes (a critical failure becomes P1 on its third failed run). Once a person sets the priority, with a reason, the rules stop changing it. A reported incident keeps the priority the reporter chose until someone changes it.

## SLAs

Each priority has a target time to acknowledge and a target time to resolve, counted from when the incident opened, all day.

| Priority | Acknowledge within | Resolve within |
| --- | --- | --- |
| P1 | 15 minutes | 4 hours |
| P2 | 30 minutes | 4 hours |
| P3 | 4 hours | 2 days |
| P4 | No target | No target |

Each clock is **due**, **at risk** (less than a quarter of the time left), **breached** (still open and late), **met**, or **missed** (done, but late). The targets are applied when incidents are read and aren't stored on them, so changing them changes every open incident's due times at once.

**Your own targets.** IT sets them with the `incident_sla` key in `policy.json` ([push settings to everyone](azure-setup.md#4-push-settings-to-everyone)), in minutes, with `0` for no target. Priorities you leave out keep the defaults above:

```json
{
  "incident_sla": {
    "P1": {"acknowledge_minutes": 10, "resolve_minutes": 120},
    "P4": {"acknowledge_minutes": 0, "resolve_minutes": 0}
  }
}
```

A value that isn't a whole number of minutes is ignored and named in **Settings > Help and about**, and the default applies for that value.

## Reporting a problem the app can't see

Wrong numbers in a report, a dashboard nobody can open, a source a team says is missing: a person reports it with what's wrong, details, what it affects and how bad it is. When it names a workload, the workload's team, technical owner and criticality come with it. A reported incident never closes by itself: only a person resolves it, with a note.

**Report a problem** in the account menu is different: it reports a problem with the Fabric Desktop app itself.

## Where incidents are kept

There will be two choices, set by IT:

- **Fabric Desktop** (default): incidents are kept in the central Fabric Desktop lakehouse with numbers, priorities, SLAs and full history, for teams without a service desk.
- **Your service desk**: Fabric Desktop is pointed at ServiceNow, Jira Service Management or another tool. A problem it finds opens a ticket there, with its number and link kept here so the same problem never opens two tickets, and recovery adds a note to the ticket. **Monitor > Operations > Incidents** shows your service desk's own page inside the app, signed in through its single sign-on, the way Fabric's own pages are shown today.

Fabric Desktop doesn't try to be a full service desk: change management, problem records, approvals and the service catalog stay in your service desk.

## What's coming

| Stage | What it adds | State |
| --- | --- | --- |
| 1 | Numbers, priorities, SLA targets, the timeline and reported incidents, in the engine | This release (no screens yet) |
| 1b | Incidents kept in the central Fabric Desktop lakehouse | Next |
| 2 | Screens: the list with number, priority and SLA, the incident page with its timeline and what to do, **Report an incident** | Planned |
| 3 | Incidents and requests that need you in the bell; Teams or email delivery | Planned |
| 4 | Full history and reports: time to acknowledge, time to resolve, SLAs met | Planned |
| 5 | Your service desk: ServiceNow, Jira Service Management or another tool | Planned |

Each stage is tested without Fabric or a service desk. Nothing here has run against a live tenant or a real service desk yet, which is why it's Preview.

## For developers

The engine is `sidecar/fabric_desktop/workloads/incidents.py` (the document and its conditional writes) and `workloads/sla.py` (targets and clocks). The app's calls are in `incident_service.py` and `incident_desk.py`:

| Call | What it does |
| --- | --- |
| `incidents_board(env, include_resolved)` | The list with each incident's priority and SLA state, and counts: open, mine, unassigned, at risk, breached |
| `incident_report(env, title, text, priority, workload, name)` | Reports a problem |
| `incident_set_priority(env, id, priority, note)` | Sets the priority; the note is required |
| `incident_sla_policy()` | The targets in effect, where they come from, and what in the policy was ignored |
