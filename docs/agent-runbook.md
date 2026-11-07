# Runbook: agent alerts and incidents

This page is for the admin who runs [headless agents](agent.md) and looks after [incidents](incidents.md). Each entry starts with the text KratosForge actually writes: a Teams message, an agent log line, or an incident title. After that it says what the message means, what to check first, and how to recover.

Text in `<angle brackets>` stands for a value filled in when the message is written. The agent's log has one JSON object per line, on stdout (journald, `docker logs`) and in `<home>/logs/agent.log`. To find a message, search the `message` field; the rest of the line carries `job_id`, `env`, `error` and similar fields. `fabric-desktop agent status` shows the queue, the next schedule times and the last 20 jobs.

!!! note "Checked against the code"
    Every quoted message below is checked against the source by `sidecar/tests/test_agent_runbook.py`. If someone changes a message's wording without updating this page, that test fails.

## Jobs

### A job didn't finish

- **Message** (`agent.py`, Teams title): `KratosForge agent <name>: a <Env> job didn't finish`
- **Message** (`agent.py`, log): `job failed`
- **Message** (`agent.py`, log): `job gave up`

**Means.** One of two things happened. A job **failed**: a table failed its checks, its configuration or its permissions, and retrying won't help. Or a job **gave up** (state `dead`): it hit a retryable error, such as the network, throttling or a table held by another writer, on every attempt. The Teams message names the job and its source and gives the error, up to 1,500 characters. Its last line says how to run the job again: the `requeue` command with the job's id, or, for a job from a pipeline, to submit the request again from the pipeline. It goes to `alerts.teams_webhook_env`.

**Check first.**

1. Run `fabric-desktop agent status` on the agent's machine and find the job. Its `error` names each table that failed and why.
2. Open the incident for the table, if one was opened. Its *likely category* (Access, Throttled or capacity, Network, Table busy, and so on) says where to look. Treat it as a hint, never a diagnosis. See [Run failed](#run-failed).

**Recover.** Fix the cause. Then run `fabric-desktop agent requeue <job id>`, which puts the job back with its attempts reset. A job from a Fabric pipeline can't be requeued: submit it again from the pipeline. A job whose schedule has already queued a newer run is refused too, because the newer run covers it.

### A job's worker keeps stopping (crash loop)

- **Message** (`agent.py`, log): `job gave up: its worker stopped on every attempt`
- **Message** (`agent.py`, log): `job put back in the queue: its worker stopped`
- **Message** (`jobqueue.py`, job error): `<agent> stopped while running it`

**Means.** The agent process ended while it was running the job. Something killed it, it ran out of memory, or it crashed. When the agent starts again, it puts the job back and counts the lost run as an attempt. A single "put back" is normal after a restart, a reboot or `kill -9`. "Gave up" means the job took the agent down on every attempt, and the job is now `dead`. It also sends the [job didn't finish](#a-job-didnt-finish) Teams message, whose error line is `<agent> stopped while running it`.

**Check first.**

1. Look in the service manager's log around the job's start time: `journalctl -u <service>`, `docker inspect` (`OOMKilled`), or the Windows Event Viewer. Look for an out-of-memory kill or a crash.
2. Check which tables the job runs (`entities` in `agent status`). A large backfill or a full rebuild is the usual cause.

**Recover.** Give the machine more memory, lower `agent.concurrency`, or run the large tables on their own (`agent enqueue <env> <table>`). Then `agent requeue` the job.

### A job's lease ran out

- **Message** (`agent.py`, log): `couldn't renew the job's lease; trying again`
- **Message** (`agent.py`, log): `lost the job's lease`
- **Message** (`agent.py`, log): `job stopped: its lease ran out`
- **Message** (`agent.py`, log): `job finished, but its lease ran out`
- **Message** (`agent.py`, job error): `this run's lease on the job ran out, so the job was put back to run again`

**Means.** A worker holds a job only while it keeps renewing the job's lease in `queue.sqlite3`. When renewal fails for longer than `agent.job_lease_seconds`, the job goes back in the queue. The run stops before its next table, so two runs never write the same job. "Finished, but" means every table ran but the result couldn't be recorded, so the job runs again and its next attempt records the outcome.

**Check first.** Did the machine sleep or hibernate? Did its clock jump? Is the disk holding the agent's home slow or full? Was another process holding `queue.sqlite3` open?

**Recover.** Usually nothing: the next attempt runs the job. If it keeps happening, keep the machine awake, move the agent's home to a local disk, or raise `agent.job_lease_seconds`.

### A table's write lease was lost

- **Message** (`leases.py`, run error): `The lease on <table> expired while writing, so another writer may have overlapped. Check the table's history before running it again.`
- **Message** (`leases.py`, run error): `The lease on <table> couldn't be renewed, so another writer could take it over during the commit. Nothing was written; run it again.`

**Means.** Before writing a table, KratosForge takes a lease on it so that two writers never commit at once. The second message is the safe case: the commit was stopped before anything was written. The first message means the lease lapsed while the write was running.

**Check first.** For the first message, open the table's Delta history (`DESCRIBE HISTORY`, or the table's history in the app). Look for a second commit from another writer in the same minutes.

**Recover.** For the second message, run the job again. For the first, run it again only once the history shows one writer. If two commits overlapped, restore the table to the version before them, then run it again.

### Another agent took the request over

- **Message** (`agent.py`, log): `job stopped: another agent took its request over`

**Means.** This was a pipeline or shared-schedule request. Another agent with `take_over = true` decided this one was lost and took the request, so this agent stopped and leaves it alone.

**Check first.** Why did this agent look lost? Look for a gap in its status record's `heartbeat_at`, often from a sleep or a network outage.

**Recover.** Nothing. The other agent finishes the request.

## Starting the agent

### The agent's settings are refused

- **Message** (`agent_config.py`, at start): `<path> has <n> problem<s>:`
- **Message** (`agent_config.py`): `give exactly one of every_minutes, daily or weekly`
- **Message** (`agent_config.py`): `every_minutes is a whole number of at least 1, like 60`
- **Message** (`agent_config.py`): `every_minutes is a whole number from 1 to <max>, like 60`
- **Message** (`agent_config.py`): `daily needs at least one time, like ["06:00"]`
- **Message** (`agent_config.py`): `weekly needs at least one day and one time, like { days = ["Monday"], times = ["06:00"] }`
- **Message** (`agent_config.py`): `times are a list, like ["06:00"]`
- **Message** (`fabric_run.py`): `Times are HH:MM in 24-hour form, like 06:00 (got <value>)`
- **Message** (`fabric_run.py`): `Days are Monday to Sunday (got <value>)`

**Means.** `agent.toml` has a problem, so the agent didn't start. It lists every problem at once. Each line begins with where the problem is, for example `schedules[2]`. These are the schedule checks; other sections are checked the same way.

**Check first.** Read every line of the message; it names the key and shows a valid value.

**Recover.** Fix `agent.toml` and start the agent again. Until it starts, its schedules don't run. Other agents and the app report it as [offline](#agent-offline) once its last status record goes stale.

### The job queue file couldn't be read

- **Message** (`agent.py`, log): `the job queue file couldn't be read; started with an empty queue`
- **Message** (`agent.py`, Teams title): `KratosForge agent <name>: its job queue file couldn't be read`

**Means.** `queue.sqlite3` in the agent's home was damaged, usually by a power loss or a full disk. The agent moved it, with its `-wal` and `-shm` files, to `queue.sqlite3.corrupt-<time>` (the `moved_to` field). Then it started with an empty queue rather than failing at every start. Schedules add their next runs again on their own. On its first pass it sends one Teams message to `alerts.teams_webhook_env`, naming the moved file.

**Check first.** Check the disk's health and free space. Look in the moved file to see which jobs were queued; SQLite can often still read most of it.

**Recover.** Enqueue again any job that was waiting (`agent enqueue`). Keep the moved file until you've checked it, then delete it.

## Status and shared schedules

### The agent's status record wasn't written

- **Message** (`agent.py`, log): `couldn't publish the agent status`

**Means.** The agent couldn't write `agents/<name>.json` for an environment, because OneLake was unreachable, its identity lost access, or `LH_Meta` was missing. It tries again at the next beat. If it keeps failing past `stale_after`, other agents and the app report this agent as [offline](#agent-offline) even though it is running.

**Check first.** The `error` field. Can the agent's identity still write to the environment's `LH_Meta`?

**Recover.** Restore access or the network. The next beat writes the record, and the offline incident recovers.

### The machine slept or its clock changed

- **Message** (`agent.py`, log): `the clock moved more than a status beat since the last pass (sleep, or a clock change); publishing the status first`
- **Message** (`agent.py`, log): `schedule's next run was further off than it can be; the clock went back, so it is worked out again`

**Means.** The first message means the time between two passes was longer than a status beat, or the clock went back: the machine slept or hibernated, or the clock was changed (NTP, a time-zone change, or by hand). The agent renews the leases of the jobs still running on it, so they aren't started a second time, and writes its status record before anything slower. Peers may already have taken over its pipeline requests while it slept. If so, it stops its copy before the next table. The second message means a schedule's next run was further off than its interval allows, because the clock went back. The agent works it out again from now. Nothing runs twice for one due time.

**Check first.** Does the machine sleep? A server running the agent shouldn't. Check its power plan, and whether its clock syncs with NTP.

**Recover.** Nothing for a single message. If the first one is frequent, turn off sleep on the machine.

### A shared schedule's request wasn't written

- **Message** (`agent.py`, log): `couldn't write the shared schedule's request`
- **Message** (`agent.py`, log): `shared schedule past its deadline; not requested`
- **Message** (`agent.py`, log): `shared scheduled run missed: no agent requested it before its deadline`
- **Message** (`agent.py`, log): `couldn't check whether another agent requested the shared schedule`
- **Message** (`agent.py`, Teams title): `KratosForge agent <name>: a shared <Env> run was missed`

**Means.** A shared schedule goes through the environment's pipeline mailbox, so that one agent runs each due time. The first message means this agent couldn't reach the mailbox; any agent that can reach it still writes the request and runs it. The others mean this agent found a due time already past its deadline, for example after it was down. That due time is never run late. The agent then looks for the request in the mailbox:

- If another agent wrote it, the run happened there, and the log says `not requested` at info level.
- If no agent wrote it, nobody ran it. The agent logs that the run was missed and sends one Teams message to `alerts.teams_webhook_env`. It names the schedule and its due time, and gives the `agent enqueue` command that runs it now.
- If the mailbox can't be read, the agent can't tell, so it sends the same Teams message saying it couldn't check.

Each agent sends this message once for a due time. If several agents sharing the schedule were all away, each one sends it.

**Check first.** Can any agent reach the mailbox? Check `agent status` on the others, and whether they were running at the due time.

**Recover.** Restore access. To make up a missed run, run the command the Teams message gives, or `agent enqueue` the schedule's tables.

## Messages to Teams or a flow

### A message to Teams didn't go

- **Message** (`agent.py`, log): `couldn't post the alert to Teams; it goes with the next try`
- **Message** (`agent.py`, log): `couldn't post waiting alerts to Teams; they'll go with the next try`
- **Message** (`agent.py`, log): `couldn't post incidents to Teams; they'll go with the next try`

**Means.** Teams or the flow didn't accept a message, because of the network, an outage, or a webhook that was removed. The message waits in the agent's home: `job_alerts.sqlite3` for job alerts, `alerts.sqlite3` for incidents. It is sent with the next try, marked **Delayed**. After three days it is dropped; see [A message was dropped](#a-message-was-dropped). The webhook address is never written to the log.

A route that keeps failing rests between tries, so an outage doesn't cost a request, or a stalled loop, every pass. The first rest is a minute, and each failure after that doubles it, up to 30 minutes. While a route rests, its new messages go straight to the outbox, logged at info level (`alert waits: Teams failed recently, so it goes with the next try`). The warnings above appear only when a request was sent and failed. A throttled post (429) is retried at most twice, each wait capped at 10 seconds. A webhook that doesn't answer gives up after 5 seconds to connect or 15 to read. A route rests until its next try even after the webhook is fixed, unless its address changed: a new address is tried at once. When it recovers, it sends up to 25 waiting messages each pass, oldest first. The status record's `messages_waiting` shows how many are waiting.

**Check first.** Can the machine reach the webhook's host? Does the Teams channel still have its workflow or connector?

**Recover.** If the webhook was replaced, set the new address in the environment variable that `alerts.teams_webhook_env` (or the route's `webhook_env`) names. Then restart the agent, since it reads its environment at start. Waiting messages go with the next try.

### A message was dropped

- **Message** (`agent.py`, log): `Teams or flow messages waited three days without being sent and were dropped`

**Means.** A job alert, incident message or shortcut break was removed unsent. Either it waited three days and Teams or the flow never took it, or its outbox already held 500 messages and it was among the oldest. The `outbox` field names where it waited (`job_alerts.sqlite3`, `alerts.sqlite3` or `shortcut_alerts.sqlite3`), and `dropped` says how many. The agent's [status record](agent.md#status-records) counts them in `messages_dropped` since its start.

**Check first.** The [message to Teams didn't go](#a-message-to-teams-didnt-go) warnings before it, which give the error. Then open Monitor and the incident list to see what the dropped messages would have said.

**Recover.** Fix the webhook or the network, as for a message that didn't go. Dropped messages don't come back.

### A route sends nowhere

- **Message** (`agent.py`, log): `an incident route's webhook variable isn't set; its messages go nowhere`
- **Message** (`agent.py`, log): `an incident route's address can't be used; its messages go nowhere`
- **Message** (`agent.py`, log): `Email routes are sent from the KratosForge app; on an agent use a Power Automate flow (flow:https://...). This route's messages go nowhere`
- **Message** (`agent.py`, log): `shortcut_health is on but its webhook variable isn't set; shortcuts aren't checked`

**Means.** A route in `agent.toml` names an environment variable that is empty, or holds an address the agent can't use. An agent has no company mail server, so it can't send `mailto:`. Messages that only this route matches go to `alerts.teams_webhook_env` instead, or nowhere when that isn't set either.

**Check first.** The `route` and `variable` fields name the route and the variable. Check the variable is set in the service's own environment (the systemd unit, the container, or the scheduled task), not only in your shell.

**Recover.** Set the variable to a Teams webhook or `flow:https://...`, then restart the agent. For email, send it through a flow or from the app ([incidents](incidents.md#teams-a-flow-or-email)).

## Incidents

The agent opens one incident per problem in each environment's `incidents/incidents.json`. [Incidents (Preview)](agent.md#incidents-preview) lists when each kind opens and recovers. These entries cover what to do.

### Run failed

- **Message** (`workloads/evaluate.py`, incident title): `<table> failed in <Env>`

**Means.** The table's newest finished run failed. The incident text is the run's own error. Its likely category comes from the error text:

| Likely | Retry helps | Look at |
| --- | --- | --- |
| Access | No | The identity's workspace role or item permissions |
| Throttled or capacity | Yes | The capacity: paused, or over its limit |
| Network | Yes | The machine's network, proxy and DNS |
| Table busy | Yes | Another writer holding the table |
| Source missing | No | A source file, table or shortcut that moved or was removed |
| Code or schema, Code, Data quality | No | The table's definition, its code, or its incoming data |
| Unrecognized | Unknown | The run's own error |

**Check first.** Open the incident's run, then read the error and the category's hint.

**Recover.** Fix the cause, then run the table again. The incident recovers when the table's newest run succeeds. A new failure of the same table adds an occurrence to the open incident instead of opening another.

### Needs a decision

- **Message** (`workloads/evaluate.py`, incident title): `<table> needs a decision in <Env>`

**Means.** The table's newest run stopped and is waiting for a person to choose. The usual cause is a logic change on a table that loads incrementally. The incident text says what the run is waiting on.

**Check first.** The table on Home or in Runs, which shows **Needs a decision**, and the choices in [load modes and decisions](metadata-guide.md#5-load-modes-and-decisions).

**Recover.** Choose in the app: **Apply to new rows only**, or **Rebuild it and everything downstream** (recommended). The incident recovers when the table's next run goes through.

### Late

- **Message** (`workloads/evaluate.py`, incident title): `<table> is late in <Env>`

**Means.** The table missed its [freshness promise](metadata-guide.md#freshness-promises): no successful run is recent enough. The incident's evidence gives `last_ok`, `expected` and `due_at`.

**Check first.** Is its schedule running? Look at `agent status`, then [agent offline](#agent-offline) incidents, then failed runs of the table or of the tables it reads from.

**Recover.** Get a successful run. The incident recovers on its own.

### Objective missed

- **Message** (`workloads/evaluate.py`, incident title): `<table> is below its success objective in <Env>`

**Means.** Fewer of the table's recent runs succeeded than its [objective](metadata-guide.md#reliability-objectives-preview) asks. The text gives the count and the rate.

**Check first.** The table's recent failed runs. Look for a pattern, such as one time of day or one category.

**Recover.** Fix the recurring failure. The incident recovers once the rate is back up.

### Promise broken

- **Message** (`workloads/evaluate.py`, incident title): `<products> broke <its or their> promise in <Env> (<table>)`

**Means.** A data product built on this table broke its promise to readers. Either the table is late against the product's freshness, or its newest load rejected more rows than the product allows. When the table's own freshness promise is also missed, the [Late](#late) incident names the product instead, so one cause opens one incident.

**Check first.** The reasons in the text. For rejected rows, look at the load's quarantine.

**Recover.** Fix the load and run it again. Tell the product's readers if they rely on the data in the meantime.

### Agent offline

- **Message** (`workloads/evaluate.py`, incident title): `Agent <name> stopped reporting in <Env>`

**Means.** Another agent's status record went past its `stale_after` without saying it stopped. While it is down, its schedules in that environment don't run, and the text lists them. An agent never reports itself; another agent or the app notices.

**Check first.** Is the process running on its host (the record's `host`)? If it is, see [the status record wasn't written](#the-agents-status-record-wasnt-written). If it isn't, check the service manager's log for why it stopped.

**Recover.** Start the agent. The incident recovers at its next status record. If the agent was retired on purpose, stop it cleanly so its record says `stopped`, or resolve the incident by hand.

### Can't evaluate

- **Message** (`workloads/evaluate.py`, incident title): `Can't check <Env>: <state>`
- **Message** (`workloads/evaluate.py`): `Nothing is known about its workloads until it can be read again.`
- **Message** (`workloads/model.py`): `Your sign-in can't read this. Ask for access, or sign in with an account that has it.`
- **Message** (`workloads/model.py`): `Couldn't reach Fabric. Check the network and try again.`
- **Message** (`workloads/model.py`): `Fabric asked to slow down. Try again in a few minutes.`

**Means.** The environment's run log couldn't be read. This is a problem with reading, not with any workload. While it is open, the environment's other incidents stay as they were instead of being marked recovered. An environment with no workspace assigned is a choice, not a fault, and opens no incident.

**Check first.** The state in the title: no access, offline, slowed down, or couldn't read.

**Recover.** Restore access or the network. The incident recovers at the next evaluation that can read the run log.

### The incident list can't be read or written

- **Message** (`workloads/incidents.py`): `The <Env> incident list (<file> in its control area) can't be read (<reason>). It is left as it is, so no incident in it is lost; restore it from a backup, or move it away to start a new list.`
- **Message** (`workloads/incidents.py`): `The incident list kept changing while it was being updated; try again.`
- **Message** (`agent.py`, log): `couldn't open the incident list`
- **Message** (`agent.py`, log): `couldn't update the incident list`
- **Message** (`agent.py`, log): `couldn't read the agent records`

**Means.** The first message: `incidents/incidents.json` is damaged (torn, empty, or not an incident list). KratosForge never writes over it, because it may hold incidents nobody has resolved, so no evaluation can update it until someone acts. The second message: many writers changed the list at the same moment. The next evaluation tries again on its own. The agent log lines carry either message in their `error` field. "Couldn't read the agent records" leaves [agent offline](#agent-offline) incidents as they were.

**Check first.** For a damaged list, open the file in the environment's control area: `LH_Meta/Files/_fabric_desktop/incidents/incidents.json`, or `.local/<env>/_control/incidents/incidents.json` for a local environment.

**Recover.** Restore the file from a [backup](backup.md), or rename it (for example `incidents.damaged.json`) to start a new list. The next evaluation opens fresh incidents for every problem that still exists, and the renamed file keeps the old ones for reference.
