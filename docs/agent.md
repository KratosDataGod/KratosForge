# Headless agent

The Fabric Desktop by KratosBI agent runs your metadata project on a server, unattended. It runs on Windows or Linux, signs in as a service principal or managed identity, and writes to OneLake exactly as the desktop app does. **Develop with Fabric Desktop by KratosBI. Execute on your infrastructure. Store and consume in Microsoft Fabric.**

The agent is the first milestone (M1) of the [enterprise local execution roadmap](enterprise-roadmap.md). It runs one machine's worth of work. Several agents sharing one queue comes in M3.

> **Status:** built and tested against local Delta tables on Linux and Windows CI. It has **not yet run against a live Fabric tenant.** Before you trust it with Prod, do the M0 checks in [Verify on your tenant](#verify-on-your-tenant).

## What it does

```
schedules in agent.toml ─┐
fabric-desktop agent enqueue ─┼─> durable queue (SQLite) ─> worker ─> metadata engine ─> OneLake
                          │        retries, crash recovery     │        (the same code the app runs)
                          │                                    └─> a write lease per table (LH_Meta)
                          └─ JSON logs, run_log rows with worker, host, attempt and job id
```

- **Same engine as the app.** A job runs an environment's entities through `fabric_desktop.meta.Engine`, in dependency order, with the same checks, quarantine, SCD, surrogate keys and run log. Runs show in the app's Monitor as *Agent \<name\> on \<host\>*, with the job id and try in their details.
- **No one at the keyboard.** The agent signs in as an app identity. Nothing ever opens a sign-in window.
- **Durable queue.** Jobs are kept in `queue.sqlite3` in the agent's home folder. A job is `queued`, then `running`, then `succeeded`, `failed` or `dead`. A running job holds a lease that its worker renews; if the worker dies (crash, reboot, killed service), the lease runs out and the job is put back in the queue on the next start.
- **Retries that make sense.** Network errors, throttling (HTTP 429 and 5xx), a lost Delta commit race and a table busy with another writer are retried, waiting `backoff_seconds`, then twice that, up to `max_backoff_seconds`, for `max_attempts` tries in all. A retry runs only the tables that didn't finish. A job that still fails is `dead` and posts to Teams. A table that fails its checks is `failed` straight away, since running it again would fail the same way.
- **Safe concurrent writes.** The engine writes a table by reading it, merging new rows and committing the result. Before it starts an entity, the agent takes a **write lease** on that table: a small file in the environment's `LH_Meta` lakehouse under `Files/_fabric_desktop/leases/`, created with `If-None-Match: *`, renewed with `If-Match`, and deleted when done. A second writer waits for it (up to `table_lease_seconds`), then retries later. An abandoned lease expires after `table_lease_seconds`. Every write, from the agent, the desktop app or a run in Fabric, also commits against the Delta version it read: if anyone committed to the table since, Delta refuses the commit and the engine re-reads the table and merges again (up to 3 times), so no writer's rows are lost even without a lease.
- **Schedules.** `[[schedule]]` entries fire in the machine's local time: every N minutes, daily at set times, or weekly. A schedule that comes due while its last run is still queued or running doesn't add a second job. A schedule missed while the agent was down runs once when it starts.
- **Fabric pipelines can ask for runs.** With `[pipeline]` turned on, a pipeline writes a run request to the environment's `LH_Meta` and waits for the agent's answer next to it. See [Run from a Fabric pipeline](#run-from-a-fabric-pipeline).
- **Guard rails.** The agent runs only the environments listed in `agent.environments`, writes only to the workspaces in `agent.workspaces` when you set it, and never creates lakehouses: an environment is set up by promoting to it from the app.
- **Logs.** One JSON object per line on stdout (journald, `docker logs`) and in `<home>/logs/agent.log` (20 MB, 10 files kept). Every line has the worker and host; job lines add `job_id`, `env`, `attempt` and `source`. Every `run_log` row the agent writes has `worker`, `host`, `attempt` and `job_id`.

## Before you start

You need:

1. **A metadata project** in a folder the agent can read, usually a Git checkout of the project you develop in the app. The agent reads `layers.toml`, `environments.toml` and the entity files from it, so check out the version you promoted.
2. **Environments set up in Fabric.** Promote to each environment from the app first. The agent checks that the environment's deployed metadata version matches the project, as the app does.
3. **An identity for the agent.** One of:
   - **Service principal with a certificate** (any server). Recommended for on-premises.
   - **Managed identity** (Azure VM, or an Arc-enabled server). Recommended on Azure: there is no secret on the machine.
   - `user`: the desktop sign-in cached for the account the agent runs as. For trying it out only; the refresh token expires.

### Set up the identity

1. **Create an app registration** for the agent (keep it separate from the desktop app's registration): Entra admin center > **App registrations > New registration**, no redirect URI.
2. **Give it a certificate.** For example, on Linux:

   ```bash
   openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=fabric-desktop-agent" \
     -keyout key.pem -out cert.pem
   cat key.pem cert.pem > /etc/fabric-desktop/agent.pem && chmod 600 /etc/fabric-desktop/agent.pem
   ```

   Upload `cert.pem` under **Certificates & secrets > Certificates**. A `.pfx` works too (`certificate_path` ending `.pfx`, with `certificate_password_env` naming the variable that holds its password). A client secret works (`client_secret_env`), but a certificate is safer.
3. **Let service principals use Fabric.** In the Fabric admin portal, **Tenant settings > Developer settings > Service principals can use Fabric APIs**: enable it for a security group, and add the agent's app (or managed identity) to that group.
4. **Add the identity to each workspace** it runs, as **Contributor** or higher: the environment's workspace (its layer lakehouses and `LH_Meta`) and, if the project uses surrogate keys, the Keys workspace (`LH_Keys`). It needs no Entra API permissions: an app identity gets what its workspace roles allow.

The full list, in alphabetical order, is in [permissions.md](permissions.md#the-headless-agent).

## Configure

Copy [deploy/agent.example.toml](https://github.com/KratosDataGod/fabric-desktop/blob/main/deploy/agent.example.toml) to:

| OS | Config | Home (queue, logs, caches) |
| --- | --- | --- |
| Linux | `/etc/fabric-desktop/agent.toml` | `/var/lib/fabric-desktop` |
| Windows | `C:\ProgramData\FabricDesktop\agent.toml` | `C:\ProgramData\FabricDesktop\agent` |

Set `FABRIC_DESKTOP_AGENT_CONFIG` or pass `--config` to use another file; set `FABRIC_DESKTOP_HOME` or `agent.home` for another home.

Secrets never go in `agent.toml`, and the agent refuses to start if one does. A certificate is a path; a client secret, certificate password or Teams webhook address is the **name of an environment variable** (`client_secret_env`, `certificate_password_env`, `teams_webhook_env`).

| Setting | Default | What it does |
| --- | --- | --- |
| `agent.name` | host name | The worker's name in logs and run rows |
| `agent.project` | required | The metadata project folder |
| `agent.environments` | required | The environments this agent may run |
| `agent.workspaces` | any | Workspace ids this agent may write to |
| `agent.concurrency` | 1 | Jobs at once |
| `agent.poll_seconds` | 5 | How often the loop looks for work |
| `agent.job_lease_seconds` | 300 | How long a silent worker keeps a job before it runs again |
| `agent.table_lease_seconds` | 120 | Write lease length, and how long to wait for a busy table |
| `agent.status_seconds` | 60 | How often to publish the [status record](#status-records) to each environment |
| `auth.type` | required | `service_principal`, `managed_identity` or `user` |
| `retry.max_attempts` | 3 | Tries for retryable errors |
| `retry.backoff_seconds` | 60 | First wait; doubles each try |
| `retry.max_backoff_seconds` | 1800 | Longest wait |
| `alerts.teams_webhook_env` | none | Variable holding a Teams Workflows webhook address |
| `pipeline.enabled` | false | Take run requests from Fabric pipelines ([below](#run-from-a-fabric-pipeline)) |
| `pipeline.environments` | `agent.environments` | Whose `LH_Meta` mailbox to read |
| `pipeline.entities` | any | Entity names or patterns (`silver.*`) a pipeline may run |
| `pipeline.allow_backfill` | false | Let pipelines ask for full rebuilds and cascades |
| `pipeline.poll_seconds` | 30 | How often to look for new requests |
| `pipeline.retention_days` | 14 | When finished requests are removed |
| `incidents.enabled` | false | Keep each environment's [incidents](#incidents-preview) (Preview) |
| `incidents.seconds` | 300 | How often to evaluate (at least 30) |
| `incidents.digest_over` | 5 | More changes than this in one evaluation go to Teams as one digest |
| `incidents.quiet_hours` | none | `"22:00-06:00"` on this machine's clock: messages wait until the hours end |
| `incidents.quiet_except` | `["critical"]` | Criticalities that are sent even in quiet hours |
| `incidents.escalate_after_minutes` | 0 (never) | Post incidents still open and unacknowledged after this long to `escalate_to` |
| `incidents.escalate_to` | none | The name of the route escalations go to; required with `escalate_after_minutes` |
| `[[incidents.route]]` | none | A Teams channel for some incidents: `name`, `webhook_env`, and filters (see below) |

Then check it. This validates the file and the project and gets a token for Fabric and for OneLake:

```bash
fabric-desktop agent check
```

## Install

### Linux (systemd)

```bash
sudo useradd --system --home /var/lib/fabric-desktop fabric-desktop
sudo python3.12 -m venv /opt/fabric-desktop/venv
sudo /opt/fabric-desktop/venv/bin/pip install ./sidecar          # from a checkout of this repository
sudo mkdir -p /etc/fabric-desktop && sudo cp deploy/agent.example.toml /etc/fabric-desktop/agent.toml
sudo chown root:fabric-desktop /etc/fabric-desktop/agent.toml /etc/fabric-desktop/agent.pem
sudo chmod 640 /etc/fabric-desktop/agent.toml /etc/fabric-desktop/agent.pem
sudo cp deploy/linux/fabric-desktop-agent.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fabric-desktop-agent
journalctl -u fabric-desktop-agent -f
```

Variables named by `*_env` settings go in `/etc/fabric-desktop/agent.env` (`NAME=value` lines, mode 600). The unit file is [deploy/linux/fabric-desktop-agent.service](https://github.com/KratosDataGod/fabric-desktop/blob/main/deploy/linux/fabric-desktop-agent.service).

### Linux (container)

```bash
docker build -f deploy/linux/Dockerfile -t fabric-desktop-agent .
docker run -d --name fd-agent --restart unless-stopped --stop-timeout 900 \
  -v /etc/fabric-desktop:/etc/fabric-desktop:ro \
  -v /srv/fabric/sales-metadata:/srv/fabric/sales-metadata:ro \
  -v fd-agent-home:/var/lib/fabric-desktop \
  fabric-desktop-agent
docker exec fd-agent fabric-desktop agent status
```

See [deploy/linux/Dockerfile](https://github.com/KratosDataGod/fabric-desktop/blob/main/deploy/linux/Dockerfile).

### Windows

From an elevated PowerShell in a checkout of this repository, with Python 3.12 installed:

```powershell
.\deploy\windows\install-agent.ps1 -Account 'CONTOSO\fd-agent$'
notepad C:\ProgramData\FabricDesktop\agent.toml
& "C:\Program Files\FabricDesktopAgent\venv\Scripts\fabric-desktop.exe" agent check
Start-ScheduledTask -TaskPath '\FabricDesktop\' -TaskName Agent
```

[The script](https://github.com/KratosDataGod/fabric-desktop/blob/main/deploy/windows/install-agent.ps1) installs the agent into a virtual environment and registers the Task Scheduler task `\FabricDesktop\Agent`. The task starts at boot with no one signed in and restarts every minute if it stops. A group managed service account (gMSA) is best, since there is no password to manage. Set `*_env` variables as machine environment variables.

## Operate

```bash
fabric-desktop agent status                          # queue counts, next schedule times, the last 20 jobs
fabric-desktop agent enqueue prod                     # run every Prod entity now
fabric-desktop agent enqueue prod silver.orders gold.fact_sales
fabric-desktop agent enqueue prod silver.orders --cascade --backfill full   # rebuild it and everything downstream
```

`enqueue` and `status` work while the service runs; they share the queue file. Stopping the service (SIGTERM, `systemctl stop`, `docker stop`, ending the task) stops taking new jobs and waits for running ones. A job cut off anyway runs again at the next start.

| Job state | Meaning | What to do |
| --- | --- | --- |
| `queued` | Waiting, or waiting to retry (`error` says why) | Nothing |
| `running` | A worker holds it | Nothing |
| `succeeded` | Every table ran | Nothing |
| `failed` | A table failed its checks, its config, or permissions | Read `error`, fix, enqueue again |
| `dead` | Retryable errors on every try | Read `error`; usually network, throttling or a table held by another writer |
| `cancelled` | A pipeline cancelled it, or its deadline passed, before it started | Nothing |

### Status records

Every `agent.status_seconds`, the agent writes `agents/<name>.json` to each environment it runs, in `LH_Meta/Files/_fabric_desktop/` (a local environment keeps it in its `.local/<env>/_control` folder). Anyone who can read the environment's `LH_Meta` can see which agents serve it, whether they are alive and what they are doing, without access to the agent's machine. A record describes only that environment's jobs. Fabric Desktop by KratosBI's workload views read these files.

| Field | Meaning |
| --- | --- |
| `contract`, `kind` | `1` and `agent_status` |
| `agent`, `host`, `pid`, `version` | Which agent, where, and which Fabric Desktop by KratosBI version |
| `state` | `running`, `stopping` (waiting for running jobs) or `stopped` (shut down cleanly) |
| `env`, `environments` | The environment this record is for, and every environment the agent runs |
| `started_at`, `heartbeat_at`, `stale_after` | UTC times. Treat an agent as **offline** once `stale_after` (three beats after `heartbeat_at`) has passed without a newer record |
| `status_seconds`, `concurrency` | Its settings |
| `pipeline` | Whether it takes pipeline requests here, its entity allowlist, and whether it allows backfills |
| `incidents` | Whether it checks [incidents](#incidents-preview) and how often, its quiet hours, escalation, and each route's name and filters. Webhook addresses and the variables holding them are never included |
| `counts` | This environment's jobs in each state: `queued`, `running`, `succeeded`, `failed`, `dead`, `cancelled` |
| `running` | Each running job: `job_id`, `source`, `attempts`, `entities`, `started_at` |
| `schedules` | This environment's schedules and when each fires `next` |
| `recent` | The last 10 finished jobs: `job_id`, `state`, `source`, `finished_at`, `error` (first 300 characters) |

Fields may be added within contract 1; readers should ignore fields they don't know. A record that can't be written (OneLake unreachable, `LH_Meta` missing) is logged and tried again at the next beat; it never stops the agent.

### Incidents (Preview)

With `[incidents] enabled = true`, every `incidents.seconds` the agent reads each environment's run log (the last 2,000 runs) and its entity files, and keeps one incident per problem in `incidents/incidents.json` in the same control area as the status records. The desktop app evaluates the same way when someone asks it to and writes to the same file, so the app and any number of agents never open two incidents for one problem: every change is a conditional write that only lands if nobody wrote in between, and is redone otherwise.

| Kind | Opens when | Recovers when |
| --- | --- | --- |
| Run failed | A table's newest finished run failed | Its newest finished run succeeded |
| Needs a decision | A table's newest finished run stopped for schema drift or quarantined rows | Its newest finished run went through |
| Late | Its [freshness promise](metadata-guide.md#freshness-promises) is missed in this environment | A successful run is recent enough |
| Objective missed | Fewer of its recent runs succeeded than its [objective](metadata-guide.md#reliability-objectives-preview) asks | The rate is back up |
| Agent offline | Another agent's status record (`agents/<name>.json`) went past its `stale_after` without saying it stopped | It reports again, or says it stopped |
| Can't evaluate | The run log couldn't be read (offline, no access, `LH_Meta` missing) | It can be read again |

"Can't evaluate" is about reading, never about a workload: while it is open, workload incidents in that environment stay as they were instead of being marked recovered. The same holds for agent records: if `agents/` can't be listed, agent-offline incidents stay as they are. An agent never reports itself offline; another agent or the app has to notice it. An agent stopped cleanly (`state = "stopped"`) isn't offline.

A failed run's incident carries a **likely category** matched from the error text: Access, Throttled or capacity, Network, Table busy, Source missing, Code or schema, Data quality, Code, or Unrecognized, with whether a retry alone usually helps. It is a hint for where to look, never a diagnosis; the run's own error is the evidence. A new failure of a table with an open incident counts as another occurrence of it. Someone who resolves an incident by hand doesn't see it again for the same failed run, only for a new one.

With `alerts.teams_webhook_env` set, each change goes to Teams: one message when an incident opens or opens again, one when it recovers, and one digest when an evaluation changes more than `incidents.digest_over`. A message Teams doesn't take waits in `alerts.sqlite3` in the agent's home and goes with the next evaluation, marked **Delayed** with when it happened; after three days it is dropped. The agent's own "a job didn't finish" alert is unchanged.

Routes send some incidents to their own channel. Each `[[incidents.route]]` has a `name`, `webhook_env` (the name of the environment variable holding the webhook; the address itself never goes in the file), and any of `teams`, `owners`, `criticality`, `kinds` and `envs`, each a list; an empty filter matches everything, and matching ignores case. A change goes to every route that matches it, and to `alerts.teams_webhook_env` when none does. A route whose variable isn't set is logged and skipped.

```toml
[incidents]
enabled = true
quiet_hours = "22:00-06:00"
escalate_after_minutes = 60
escalate_to = "on-call"

[[incidents.route]]
name = "sales"
webhook_env = "SALES_TEAMS_WEBHOOK"
teams = ["sales"]

[[incidents.route]]
name = "on-call"
webhook_env = "ONCALL_TEAMS_WEBHOOK"
criticality = ["critical"]
```

During quiet hours, messages for incidents whose criticality isn't in `quiet_except` wait in the outbox and go out when the hours end, marked **Held during quiet hours since** when they happened. Escalations are never held. An incident still `open` (nobody acknowledged it) `escalate_after_minutes` after it opened is marked escalated once, with a note, however many agents evaluate; the escalation route gets one **Escalated** message. Escalation doesn't page anyone by itself: it is a Teams post to the channel you name.

The file keeps every open incident and the last 200 resolved ones. Each has `id`, `key` (kind, workload, environment), `kind`, `workload`, `name`, `env`, `title`, `text`, `evidence` (run id and start time, or the freshness or objective numbers), `state` (`open`, `acknowledged` or `resolved`), `opened_at`, `last_seen_at`, `occurrences`, `opened_by`, `owner` (the entity's declared owner), `team`, `criticality`, `classification` (failed runs), `escalated_at` and `escalated_to`, who acknowledged and resolved it and when, `resolution` (`recovered`, `cleared` or `resolved` by hand) and `notes`. Contract 1; fields may be added.

## Run from a Fabric pipeline

> **Preview.** Built and tested against local folders and a mocked OneLake. It has not yet run on a live tenant; see step 6 of [Verify on your tenant](#verify-on-your-tenant).

A Fabric pipeline can ask the agent to run an environment and wait for the result, so the agent's runs fit into pipelines you already orchestrate in Fabric (after a Copy job lands files, before a semantic model refresh). The pipeline and the agent talk through files in the environment's `LH_Meta` lakehouse. Nothing listens on a network port, and the agent needs no inbound firewall rule.

```
pipeline ── writes ──> LH_Meta/Files/_fabric_desktop/requests/<id>.json
agent    ── claims, queues and runs it, then writes ──> status/<id>.json
pipeline ── reads status/<id>.json until it is final; writes cancel/<id>.json to cancel
```

**Turn it on** in `agent.toml`:

```toml
[pipeline]
enabled = true
entities = ["silver.*", "gold.*"]   # optional: what pipelines may run
```

**Add the sample notebook.** Import [examples/pipeline/run_on_agent.ipynb](https://github.com/KratosDataGod/fabric-desktop/blob/main/examples/pipeline/run_on_agent.ipynb) into the environment's workspace as a Python notebook and set **LH_Meta** as its default lakehouse. In your pipeline, add a **Notebook** activity for it with these base parameters:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `env` | `prod` | The environment. It must be the one whose `LH_Meta` the notebook writes to |
| `entities` | `silver.orders,gold.fact_sales` | Comma-separated; empty runs every entity |
| `request_id` | `@pipeline().RunId` | Makes a retried activity wait for the same run instead of asking twice |
| `submitted_by` | `@concat(pipeline().Pipeline, ' ', pipeline().RunId)` | Recorded in the status, as given |
| `start_within_minutes` | `60` | The request expires if no agent has started it by then |
| `timeout_minutes` | `240` | Stop waiting, cancel the request if it hasn't started, and fail |

The activity succeeds when the run succeeds and fails otherwise, with the reason in its error. Its output (`notebookutils.notebook.exit`) is the final status, so later activities can read the run ids and row counts.

### The contract (version 1)

A **request** is a JSON object in `requests/<id>.json`. The id is 1 to 64 letters, digits, `.`, `_` or `-`. Write the file under another name first and rename it, as the sample does; the agent gives a file it can't read 60 seconds before rejecting it.

| Field | Required | Meaning |
| --- | --- | --- |
| `contract` | no (1) | The contract version. An agent refuses a version it doesn't speak |
| `env` | yes | The environment to run. It must match the mailbox it is written to |
| `entities` | no | Entity names; empty or missing runs every entity in dependency order |
| `backfill` | no | `""` or `"full"`. Needs `pipeline.allow_backfill` |
| `cascade` | no | `true` rebuilds one entity and everything downstream of it. Needs `pipeline.allow_backfill` |
| `deadline` | no | ISO 8601 time with a zone (`2026-10-17T03:00:00Z`): the latest time the run may start |
| `submitted_by` | no | Free text up to 200 characters, recorded in the status |

The agent answers in **`status/<id>.json`**:

| Field | Meaning |
| --- | --- |
| `state` | `accepted`, `running`, then one final state: `succeeded`, `failed`, `cancelled`, `expired` or `rejected` |
| `reason` | Why it was rejected, cancelled or expired |
| `error` | Why the run failed (`last_error` while a retry waits) |
| `agent`, `host` | Which agent took it |
| `job_id`, `attempts` | The agent's job; `job_id` is also on every `run_log` row the run writes |
| `received_at`, `started_at`, `finished_at`, `updated_at` | UTC times |
| `result` | `tables`, `ok`, `failed`, `rows_written` and `run_ids` |
| `cancel` | Set when a cancel arrived while the run was going |
| `contract`, `request_id`, `env`, `entities`, `backfill`, `cascade`, `deadline`, `submitted_by` | As requested |

A final status never changes again. Fields may be added within version 1; readers should ignore fields they don't know.

**How it stays safe:**

- **Each request runs once.** The agent claims a request by creating its status file with `If-None-Match: *`, so when several agents read one mailbox only one takes it. The request id is the job's dedupe key in the agent's queue, so an agent that crashes after claiming queues it when it starts again, and never twice.
- **Only the environment's own people can ask.** A request runs the environment whose `LH_Meta` it is written to, and only identities that can write that lakehouse can write it. A Test pipeline can't run Prod. The agent also checks `pipeline.environments`, `pipeline.entities` and `pipeline.allow_backfill`, and that each entity exists in the project.
- **Cancelling.** Write any file to `cancel/<id>.json`. A request that hasn't started is `cancelled`. A run already going finishes its tables (stopping mid-write could leave a table half done) and isn't retried if it fails.
- **Deadlines.** A request not started by its `deadline`, including one waiting for a retry, is `expired` and its job is cancelled.
- **Housekeeping.** Finished requests and their status and cancel files are removed after `pipeline.retention_days`.

## Verify on your tenant

Nothing here has run against a live Fabric tenant yet. Before Prod, check these on a Dev workspace (roadmap M0):

1. `fabric-desktop agent check` succeeds with your service principal (tokens for Fabric and OneLake).
2. `fabric-desktop agent enqueue dev` writes the Dev tables, and they show in the lakehouse after a refresh.
3. A lease file appears in `LH_Meta/Files/_fabric_desktop/leases/` while a table runs and is gone after. OneLake's support for `If-None-Match` and `If-Match` on these requests is the key thing to confirm.
4. Two agents, or the agent and the app, running the same table at once: one waits for the other and no rows are lost.
5. Status records: `LH_Meta/Files/_fabric_desktop/agents/<name>.json` appears within `status_seconds` of starting the agent, its `heartbeat_at` moves on, and `state` is `stopped` after `systemctl stop` (or stopping the Windows service).
6. Pipeline requests: with `[pipeline] enabled = true`, run a pipeline whose Notebook activity runs `run_on_agent.ipynb` with `entities` set to one bronze entity. The activity succeeds, `status/<RunId>.json` in `LH_Meta/Files/_fabric_desktop/` ends `succeeded` with the agent's `job_id`, and the Monitor shows the run. Then run it with a pipeline identity that can't write `LH_Meta` and check the activity fails to write the request. Then write `cancel/<id>.json` before the agent's next poll and check the status ends `cancelled`.

7. Incidents: with `[incidents] enabled = true` and a table that fails in Dev, `LH_Meta/Files/_fabric_desktop/incidents/incidents.json` appears within `incidents.seconds` with one open incident, Teams gets one message, and a second agent (or the app) evaluating at the same time doesn't add another. After a good run the incident is resolved as recovered. Turn the network off for one evaluation and check Teams gets the messages marked Delayed afterwards. Stop a second agent with Task Manager (not cleanly) and check the first opens "Agent … stopped reporting" after its `stale_after`, and recovers it when the second is back. With a route and `quiet_hours` covering now, check a non-critical failure waits and a critical one doesn't.

## Limits in this version

- Pipeline requests don't record who wrote the request file, only the `submitted_by` text the pipeline gives. Whether OneLake reports a file's writer to the agent is still to verify.
- One agent per queue. Two agents on different machines each keep their own queue; table leases still stop them from writing the same table at once, but nothing stops both from running the same schedule. Give each environment's schedules to one agent. A shared queue is roadmap M3.
- The desktop app and runs in Fabric don't take write leases. Their writes are still safe (each commits against the version it read and merges again on a conflict), but a busy table means retries instead of waiting in turn.
- Notebooks and job files don't run in the agent yet; it runs metadata environments.
- Agent environments run on the machine, whatever their **Runs on** setting in the app.
- Lease expiry uses the holder's clock. Keep agent clocks in sync (NTP).
- Incidents (Preview) cover metadata tables and agents only. The app shows them in Monitor > Incidents ([user guide](user-guide.md#incidents-preview)). Late tables can also still be posted by the app's own freshness alerts, so a team using both may see a late table twice. Quiet hours use the agent machine's clock, and routes are set per agent: two agents with different routes post the same incident to different channels.
- The agent doesn't update itself. Install a new version with `pip install` and restart the service.
