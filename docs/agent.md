# Headless agent

The KratosForge agent runs your metadata project on a server, unattended. It runs on Windows or Linux, signs in as a service principal or managed identity, and writes to OneLake exactly as the desktop app does. **Develop with KratosForge. Execute on your infrastructure. Store and consume in Microsoft Fabric.**

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
- **Safe concurrent writes.** The engine writes a table by reading it, merging new rows and committing the result. Before it starts an entity, the agent takes a **write lease** on that table: a small file in the environment's `LH_Meta` lakehouse under `Files/_fabric_desktop/leases/`, created with `If-None-Match: *`, renewed with `If-Match`, and deleted when done. A second writer waits for it (up to `table_lease_seconds`), then retries later. An abandoned lease expires after `table_lease_seconds`. The desktop app takes the same lease when you run a metadata entity on this PC or replay quarantined rows. Every OneLake table write, from those runs, the agent, a notebook's `write_table` or a job's publish, also holds a second lease in the table's own lakehouse (`Files/_fabric_desktop/leases/<schema>.<table>.json`), so a notebook and a metadata run writing the same table wait for each other; a notebook or job waits up to 90 seconds (a metadata run `table_lease_seconds`), then stops with "<table> is being written by <holder> on <machine>". A notebook killed mid-write leaves its lease for at most a minute, so the next write waits it out and goes ahead. Whether OneLake honours these conditional requests between two machines is not yet verified on a live tenant. For a local environment the lease files are in its `.local` folder, and taking one over is a single step across every process on the PC, so two writers never both win an expired lease. A worker that can't renew its lease for a whole `table_lease_seconds` (OneLake unreachable, say) treats it as lost and reports the run that way. Before each write starts, the writer also checks its leases: if one was taken over, or hasn't been renewed for two thirds of its length, it stops with nothing written ("Nothing was written; run it again"). A Delta write or MERGE uploads its files first and commits at the end, so a lease lost during the upload is still reported afterwards, as before. The check covers every table write made while the lease is held, so quarantine and business-key writes made during an entity's run also stop if that entity's lease lapses. Every write, from the agent, the desktop app or a run in Fabric, also commits against the Delta version it read: if anyone committed to the table since, Delta refuses the commit and the engine re-reads the table and merges again (up to 3 times), so no writer's rows are lost even without a lease.
- **Schedules.** `[[schedule]]` entries fire in the machine's local time: every N minutes (counted from local midnight, so `every_minutes = 60` runs on the hour and a restart doesn't move it), daily at set times, or weekly. A schedule that comes due while its last run is still queued or running doesn't add a second job. A schedule missed while the agent was down runs once when it starts.
- **Fabric pipelines can ask for runs.** With `[pipeline]` turned on, a pipeline writes a run request to the environment's `LH_Meta` and waits for the agent's answer next to it. See [Run from a Fabric pipeline](#run-from-a-fabric-pipeline).
- **Shortcut health on a schedule (Preview).** With `[shortcut_health]` turned on, the agent checks the environments' shortcuts and posts each new break to Teams once. It only reads. See [Shortcut health](#shortcut-health).
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

Copy [deploy/agent.example.toml](https://github.com/KratosDataGod/KratosForge/blob/main/deploy/agent.example.toml) to:

| OS | Config | Home (queue, logs, caches) |
| --- | --- | --- |
| Linux | `/etc/fabric-desktop/agent.toml` | `/var/lib/fabric-desktop` |
| Windows | `C:\ProgramData\FabricDesktop\agent.toml` | `C:\ProgramData\FabricDesktop\agent` |

Set `FABRIC_DESKTOP_AGENT_CONFIG` or pass `--config` to use another file; set `FABRIC_DESKTOP_HOME` or `agent.home` for another home.

Secrets never go in `agent.toml`, and the agent refuses to start if one does. A certificate is a path; a client secret, certificate password or Teams webhook address is the **name of an environment variable** (`client_secret_env`, `certificate_password_env`, `teams_webhook_env`).

| Setting | Default | What it does |
| --- | --- | --- |
| `agent.name` | host name | The worker's name in logs, run rows and `agents/<name>.json`: 1 to 64 letters, digits, `.`, `_` or `-` (other characters in the host name become `-`) |
| `agent.owner` | none | Who looks after this agent (a team or contact), shown in the app as written |
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
| `pipeline.claim` | `"always"` | `"when_free"` (Preview): take a new request only while a worker is free, so a busy agent leaves it for one that isn't. See [Shared schedules](#shared-schedules-preview) |
| `pipeline.take_over` | false | (Preview) Take over requests held by an agent that stopped reporting. See [Taking over from a lost agent](#taking-over-from-a-lost-agent-preview) |
| `pipeline.require_run` | false | (Preview) Only take requests whose `pipeline_run` checks out in Fabric. See [Which pipeline asked](#which-pipeline-asked-preview). Can't be used with shared schedules |
| `schedule.shared` | false | (Preview) Share this schedule with other agents through the mailbox, so each due time runs once. See [Shared schedules](#shared-schedules-preview) |
| `incidents.enabled` | false | Keep each environment's [incidents](#incidents-preview) (Preview) |
| `incidents.seconds` | 300 | How often to evaluate (at least 30) |
| `incidents.digest_over` | 5 | More changes than this in one evaluation go to Teams as one digest |
| `incidents.quiet_hours` | none | `"22:00-06:00"` on this machine's clock: messages wait until the hours end |
| `incidents.quiet_except` | `["critical"]` | Criticalities that are sent even in quiet hours |
| `incidents.escalate_after_minutes` | 0 (never) | Post incidents still open and unacknowledged after this long to `escalate_to` |
| `incidents.escalate_to` | none | The name of the route escalations go to; required with `escalate_after_minutes` |
| `[[incidents.route]]` | none | A Teams channel for some incidents: `name`, `webhook_env`, and filters (see below) |
| `shortcut_health.enabled` | false | (Preview) Check the environments' [shortcuts](#shortcut-health) on a schedule and post each break to Teams once |
| `shortcut_health.minutes` | 60 | (Preview) How often to check (at least 15) |
| `shortcut_health.environments` | `agent.environments` | (Preview) Whose workspaces' shortcuts to check |
| `shortcut_health.webhook_env` | `alerts.teams_webhook_env` | (Preview) Variable holding the Teams webhook breaks go to; one of the two is required when it's on |

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

Variables named by `*_env` settings go in `/etc/fabric-desktop/agent.env` (`NAME=value` lines, mode 600). The unit file is [deploy/linux/fabric-desktop-agent.service](https://github.com/KratosDataGod/KratosForge/blob/main/deploy/linux/fabric-desktop-agent.service).

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

See [deploy/linux/Dockerfile](https://github.com/KratosDataGod/KratosForge/blob/main/deploy/linux/Dockerfile).

### Windows

From an elevated PowerShell in a checkout of this repository, with Python 3.12 installed:

```powershell
.\deploy\windows\install-agent.ps1 -Account 'CONTOSO\fd-agent$'
notepad C:\ProgramData\FabricDesktop\agent.toml
& "C:\Program Files\FabricDesktopAgent\venv\Scripts\fabric-desktop.exe" agent check
Start-ScheduledTask -TaskPath '\FabricDesktop\' -TaskName Agent
```

[The script](https://github.com/KratosDataGod/KratosForge/blob/main/deploy/windows/install-agent.ps1) installs the agent into a virtual environment and registers the Task Scheduler task `\FabricDesktop\Agent`. The task starts at boot with no one signed in and restarts every minute if it stops. A group managed service account (gMSA) is best, since there is no password to manage. Set `*_env` variables as machine environment variables.

## Operate

```bash
fabric-desktop agent run                             # the service: schedules, queue, retries
fabric-desktop agent status                          # queue counts, next schedule times, the last 20 jobs
fabric-desktop agent enqueue prod                     # run every Prod entity now
fabric-desktop agent enqueue prod silver.orders gold.fact_sales
fabric-desktop agent enqueue prod silver.orders --cascade --backfill full   # rebuild it and everything downstream
fabric-desktop agent requeue job-20261025-020000-ab12cd  # run a dead or failed job again, attempts reset
fabric-desktop agent prune --dry-run                  # list the finished jobs prune would remove
```

`enqueue`, `status`, `requeue` and `prune` work while the service runs; they share the queue file. Stopping the service (SIGTERM, `systemctl stop`, `docker stop`, ending the task) stops taking new jobs and waits for running ones. A job cut off anyway runs again at the next start.

| Job state | Meaning | What to do |
| --- | --- | --- |
| `queued` | Waiting, or waiting to retry (`error` says why) | Nothing |
| `running` | A worker holds it | Nothing |
| `succeeded` | Every table ran | Nothing |
| `failed` | A table failed its checks, its config, or permissions | Read `error`, fix, enqueue again |
| `dead` | Retryable errors on every try | Read `error`; usually network, throttling or a table held by another writer |
| `cancelled` | A pipeline cancelled it, or its deadline passed, before it started | Nothing |

**Requeue.** Once you've fixed what made a job `dead` or `failed`, `agent requeue <job id>` puts it back in the queue with its attempts reset to 0. The job records who requeued it (`requeued_by`, your OS user name), and the agent's audit log gets an `agent_requeue` line. A job that is queued, running, succeeded or cancelled is refused. So is one from a Fabric pipeline, since its request already says it finished: submit it again from the pipeline. A job whose schedule has already queued a newer run is refused too.

**Pruning.** At start and then once a day, the agent removes `succeeded`, `failed` and `cancelled` jobs that finished more than `agent.queue_retention_days` days ago (default 30). `dead` jobs stay until you requeue them. Queued and running jobs are never removed. `agent prune` does the same now; `--days N` picks another age and `--dry-run` lists the jobs without removing them. Pruning removes rows from the agent's own `queue.sqlite3` only, never anything in OneLake, and it runs in one short transaction.

### Status records

Every `agent.status_seconds`, the agent writes `agents/<name>.json` to each environment it runs, in `LH_Meta/Files/_fabric_desktop/` (a local environment keeps it in its `.local/<env>/_control` folder). Anyone who can read the environment's `LH_Meta` can see which agents serve it, whether they are alive and what they are doing, without access to the agent's machine. A record describes only that environment's jobs. KratosForge's workload views and Monitor > KratosForge > Operations > Agents ([user guide](user-guide.md#agents-preview)) read these files.

| Field | Meaning |
| --- | --- |
| `contract`, `kind` | `1` and `agent_status` |
| `agent`, `host`, `pid`, `version` | Which agent, where, and which KratosForge version |
| `owner`, `identity` | `agent.owner` as written (`null` when not set), and `auth.type`: `service_principal`, `managed_identity` or `user`. No ids, certificate paths or secrets |
| `credential` | Facts about the agent's credential, never the credential itself: `kind` (`certificate`, `secret`, `managed_identity` or `user`), `expires_at` (a certificate's end date, read from `auth.certificate_path`; `null` for a secret or managed identity, whose expiry the agent can't see), `last_token_at`, and `last_token_error` (`at` and the error, with secrets, ids, emails and user paths removed). The path, thumbprint, key and password are never published |
| `state` | `running`, `stopping` (waiting for running jobs; still written every `status_seconds`, so no other agent takes over its requests) or `stopped` (shut down cleanly) |
| `env`, `environments` | The environment this record is for, and every environment the agent runs |
| `started_at`, `heartbeat_at`, `stale_after` | UTC times. Treat an agent as **offline** once `stale_after` (three beats after `heartbeat_at`) has passed without a newer record |
| `status_seconds`, `concurrency` | Its settings |
| `pipeline` | Whether it takes pipeline requests here, its entity allowlist, and whether it allows backfills |
| `incidents` | Whether it checks [incidents](#incidents-preview) and how often, its quiet hours, escalation, and each route's name and filters. Webhook addresses and the variables holding them are never included |
| `shortcut_health` | `enabled` (whether it checks this environment's [shortcuts](#shortcut-health)) and `minutes`. Other agents read it so that one of them posts each break. No webhook |
| `counts` | This environment's jobs in each state: `queued`, `running`, `succeeded`, `failed`, `dead`, `cancelled` |
| `running` | Each running job: `job_id`, `source`, `attempts`, `entities`, `started_at` |
| `schedules` | This environment's schedules: `id`, when each fires `next`, the `entities` it runs (an empty list means every entity) and `when` as written in agent.toml (`{"kind": "cron", "every_minutes"}`, `{"kind": "daily", "times"}` or `{"kind": "weekly", "times", "weekdays"}`) |
| `recent` | The last 10 finished jobs: `job_id`, `state`, `source`, `finished_at`, `error` (first 300 characters) |

Fields may be added within contract 1; readers should ignore fields they don't know. Agents built before these fields were added leave out `owner`, `identity`, `credential` and each schedule's `entities` and `when`; read a missing field as unknown, never as "all" or "none". A record that can't be written (OneLake unreachable, `LH_Meta` missing) is logged and tried again at the next beat; it never stops the agent.

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

With `alerts.teams_webhook_env` set, each change goes to Teams: one message when an incident opens or opens again, one when it recovers, and one digest when an evaluation changes more than `incidents.digest_over`. A message Teams doesn't take waits in `alerts.sqlite3` in the agent's home and goes with the next evaluation, marked **Delayed** with when it happened; after three days it is dropped. The agent's own "a job didn't finish" alert waits the same way, in `job_alerts.sqlite3`: it goes before the next such alert, or with the next status update (`status_seconds`), marked **Delayed**, and is dropped after three days.

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

### Shortcut health

> **Preview.** Built and tested against a mocked Fabric and OneLake. It has not yet run on a live tenant; see step 10 of [Verify on your tenant](#verify-on-your-tenant).

Broken [shortcuts](shortcuts.md) otherwise show only when someone opens **Shortcuts > Health**. With `[shortcut_health] enabled = true`, the agent checks them itself, at start and then every `shortcut_health.minutes`:

```toml
[shortcut_health]
enabled = true
minutes = 60
environments = ["prod"]
webhook_env = "SHORTCUTS_TEAMS_WEBHOOK"   # the variable holding the webhook, never the address
```

**What it reads.** For each environment, the lakehouses in its workspaces (the environment's `workspace_id` and each designation's workspace) and their shortcuts, through the Fabric REST API; then, for each shortcut, one entry of the folder it sits in and one entry of the shortcut itself, through OneLake, as **Check now** does in the app. Only reads, with the agent's identity: no job runs, so the check uses no capacity, and no table data is read. A shortcut is broken, healthy, unreachable or not checked exactly as on [Health](shortcuts.md#health); the agent's identity needs read access to the lakehouses (and, for a OneLake shortcut, to its target) to tell.

**How it runs.** On its own thread beside the agent's loop, so a long scan doesn't hold up schedules, the pipeline mailbox or jobs. A check that is still running when the next one is due isn't doubled: that one is skipped. Each request takes a current token, so a long scan doesn't fail on one that expired.

**What it writes.** Nothing in Fabric, OneLake or the platform. On the agent's machine, `shortcut_alerts.sqlite3` in its home keeps which breaks were posted, and the messages Teams didn't take yet. Its status record gains the `shortcut_health` field.

**What it posts.** A `shortcut.broken` message ("Shortcut broken: orders in LH_Gold", the environment, what's wrong and where to open it in the app) the first time a check finds a shortcut broken, and a `shortcut.fixed` message when a later check finds it working again. A shortcut that stays broken isn't posted again, across checks and restarts. Once it's fixed, a new break is posted again. A shortcut that is unreachable or not checked posts nothing. A message Teams doesn't take waits in the outbox and goes with the next check, marked **Delayed**; after three days it is dropped.

**Several agents.** When more than one agent checks the same environment, only one posts: the agent whose name sorts first among the live agents whose status records say they check it. The others skip the check. If that agent stops reporting, the next one takes over and posts the breaks still open once more, since its own record of what was posted starts empty. An agent that can't read the status records checks and posts anyway: a second message is better than none.

The desktop app still posts the breaks its own checks find to the platform's Teams routes (**Settings > Notifications**, see [the governance guide](governance-guide.md#teams-notices-for-requests-and-shortcuts)). To get each break once, send the agent's messages to a different channel, or leave `shortcut.broken` and `shortcut.fixed` off the app's routes.

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

**Add the sample notebook.** Import [examples/pipeline/run_on_agent.ipynb](https://github.com/KratosDataGod/KratosForge/blob/main/examples/pipeline/run_on_agent.ipynb) into the environment's workspace as a Python notebook and set **LH_Meta** as its default lakehouse. In your pipeline, add a **Notebook** activity for it with these base parameters:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `env` | `prod` | The environment. It must be the one whose `LH_Meta` the notebook writes to |
| `entities` | `silver.orders,gold.fact_sales` | Comma-separated; empty runs every entity |
| `request_id` | `@pipeline().RunId` | Makes a retried activity wait for the same run instead of asking twice |
| `submitted_by` | `@concat(pipeline().Pipeline, ' ', pipeline().RunId)` | Recorded in the status, as given |
| `pipeline_id` | `@pipeline().Pipeline` | With `pipeline_run_id`: lets the agent check this run in Fabric. Needed when the agent sets `require_run` |
| `pipeline_run_id` | `@pipeline().RunId` | The run to check |
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
| `submitted_by` | no | Free text up to 200 characters, recorded in the status. Nothing checks it |
| `pipeline_run` | no (yes with `require_run`) | `{"item_id": <pipeline item id>, "run_id": <run id>}`, both GUIDs: the run that wrote the request, for the agent to check |

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
| `pipeline_run` | The run the request named, with `verified`, `status`, `started_at` and `detail` (what Fabric said, or why it couldn't be checked) |
| `contract`, `request_id`, `env`, `entities`, `backfill`, `cascade`, `deadline`, `submitted_by` | As requested |

A final status never changes again. Fields may be added within version 1; readers should ignore fields they don't know.

### Which pipeline asked (Preview)

Anyone who can write the environment's `LH_Meta` can write a request, and OneLake doesn't say who wrote a file: its owner is always `$superuser` ([OneLake API parity](https://learn.microsoft.com/fabric/onelake/onelake-api-parity)). So `submitted_by` is only what the pipeline says.

What the agent can check is the run. When a request names `pipeline_run`, the agent looks that job up in the environment's workspace with the Fabric API, as its own identity. It records the answer in the status's `pipeline_run`. `verified` is true only when Fabric has a **Pipeline** job with that id, for that pipeline item, that started within the last 24 hours and not in the future. A local environment has no workspace to look in, so its runs are never verified.

By default an unverified run is recorded and the request still runs. With `[pipeline] require_run = true`, a request without `pipeline_run`, or whose run doesn't check out, is rejected with the reason. So is one whose run has already finished: the notebook that writes a request waits for its answer, so its run is still in progress. When Fabric can't answer the lookup just then (it's busy, or the connection drops), the agent asks again on its next polls, for up to 15 minutes, before it rejects the request. Shared schedules write requests that name no run, so they can't be combined with `require_run`.

This proves that a real pipeline run in the workspace is behind the request id. Each run vouches for one request only: the agent keeps the runs it accepted for a day (in `pipeline-runs.json` in its home folder, so a restart keeps them), and rejects a second request naming the same run with "pipeline run <id> already asked for request <other id>". The same request asked again is still one request. Each agent keeps its own list, so with several agents taking requests for one environment, keep write access to `LH_Meta` as narrow as the agents' own.

**How it stays safe:**

- **Each request runs once.** The agent claims a request by creating its status file with `If-None-Match: *`, so when several agents read one mailbox only one takes it. The request id is the job's dedupe key in the agent's queue, so an agent that crashes after claiming queues it when it starts again, and never twice.
- **Only the environment's own people can ask.** A request runs the environment whose `LH_Meta` it is written to, and only identities that can write that lakehouse can write it. A Test pipeline can't run Prod. The agent also checks `pipeline.environments`, `pipeline.entities` and `pipeline.allow_backfill`, and that each entity exists in the project.
- **Cancelling.** Write any file to `cancel/<id>.json`. A request that hasn't started is `cancelled`. A run already going finishes its tables (stopping mid-write could leave a table half done) and isn't retried if it fails.
- **Deadlines.** A request not started by its `deadline`, including one waiting for a retry, is `expired` and its job is cancelled.
- **Housekeeping.** Finished requests and their status and cancel files are removed after `pipeline.retention_days`.

### Shared schedules (Preview)

Give the same schedule to several agents and set `shared = true` on it. Each due time then runs once, on whichever agent claims it, rather than once per agent. If one agent is down, the others still run the schedule. The design, and what is still to come, is in [Multiple workers](workloads/multi-worker.md). Sharing is a setting in the agents' schedules, not a screen, so **Settings > Preview features** doesn't turn it off.

```toml
[pipeline]
enabled = true
environments = ["prod"]
claim = "when_free"          # optional: a busy agent leaves new requests for one with a free worker

[[schedule]]
id = "prod-hourly"           # the same id, env, entities and times on every agent that shares it
env = "prod"
entities = ["silver.orders"]
every_minutes = 60
shared = true
```

- **How it works.** When a shared schedule is due, each agent tries to create `requests/sched-<id>-<slot>.json` in the environment's mailbox with `If-None-Match: *`. Only the first create lands. The request is then claimed like any pipeline request, so one agent runs it. The `<slot>` comes from the due time in UTC, for example `sched-prod-hourly-20270115T0900Z`.
- **Same times on every agent.** An every-N-minutes shared schedule runs on multiples of its interval, counted in UTC (every hour on the hour, for example), so every agent computes the same due times. Daily and weekly times are each machine's local time, so agents sharing them must use the same time zone.
- **No late runs.** A slot's request has a deadline: its next slot, at most 6 hours on. An agent that comes up after that doesn't create it. An unclaimed request past its deadline is `expired`. With `claim = "when_free"`, if every agent stays busy until the deadline, that due time expires without running. That is by design, not a lost run: the next due time runs as normal.
- **Requirements.** A shared schedule needs `[pipeline] enabled = true` for its environment, its id is at most 40 characters, and an every-N-minutes shared schedule uses whole minutes, at least 1. With `pipeline.entities`, it must name entities the allowlist takes. A shared backfill needs `pipeline.allow_backfill`.
- **Not yet.** A request claimed by an agent that then stops reporting waits for that agent, unless the others have `take_over = true` (below). Monitor > KratosForge > Operations > Agents doesn't warn about a schedule that agents share this way. A late or expired shared due time doesn't open an incident yet.

### Taking over from a lost agent (Preview)

With `[pipeline] take_over = true`, an agent takes over a pipeline request (a shared schedule's, or any other) that another agent accepted and then stopped reporting on. That agent counts as lost once its status record in `agents/<name>.json` is past its `stale_after`, plus a minute for clock differences.

- **One agent takes it.** The takeover replaces the request's status file only if it hasn't changed since it was read (`If-Match`), so when several agents try at once, one wins. The status then names the new agent, `taken_over_from`, `taken_over_at`, and `agents`, every agent that has held it.
- **The whole request runs again.** Tables the lost agent finished are run again. A request whose `deadline` has passed ends `expired` instead of starting again. Before it writes each table, the new agent takes that table's write lease, so it waits for a lease the lost agent still holds to run out.
- **The lost agent stands down.** An agent that comes back after its request was taken over (it was paused, or its network was down) cancels its own copy. A run it is still in stops before its next table, without writing or recording it, and its audit log says `handed_over`. A table write already under way when it comes back can't be stopped; the table lease is what keeps the two writers apart then.
- **Not forever.** A request that `retry.max_attempts` agents have held ends `failed`, with each of them named, so a request that crashes every agent doesn't go round them all for ever.
- **What isn't taken over.** An agent without a status record, or with one that can't be read, keeps its requests, because nothing shows it is lost. An agent doesn't take over a request it would refuse as new (its `pipeline.entities`, `allow_backfill` or environments). With `claim = "when_free"`, it takes one over only while a worker is free.

## Verify on your tenant

Nothing here has run against a live Fabric tenant yet. Before Prod, check these on a Dev workspace (roadmap M0):

1. `fabric-desktop agent check` succeeds with your service principal (tokens for Fabric and OneLake).
2. `fabric-desktop agent enqueue dev` writes the Dev tables, and they show in the lakehouse after a refresh.
3. A lease file appears in `LH_Meta/Files/_fabric_desktop/leases/` while a table runs and is gone after. OneLake's support for `If-None-Match` and `If-Match` on these requests is the key thing to confirm.
4. Two agents, or the agent and the app, running the same table at once: one waits for the other and no rows are lost.
5. Status records: `LH_Meta/Files/_fabric_desktop/agents/<name>.json` appears within `status_seconds` of starting the agent, its `heartbeat_at` moves on, and `state` is `stopped` after `systemctl stop` (or stopping the Windows service).
6. Pipeline requests: with `[pipeline] enabled = true`, run a pipeline whose Notebook activity runs `run_on_agent.ipynb` with `entities` set to one bronze entity. The activity succeeds, `status/<RunId>.json` in `LH_Meta/Files/_fabric_desktop/` ends `succeeded` with the agent's `job_id`, and the Monitor shows the run. Then run it with a pipeline identity that can't write `LH_Meta` and check the activity fails to write the request. Then write `cancel/<id>.json` before the agent's next poll and check the status ends `cancelled`. Then pass `pipeline_id` and `pipeline_run_id` to the notebook and check the status's `pipeline_run` says `verified: true`. The live test's `pipeline.mailbox` check covers the OneLake part of this without a pipeline (see [Live testing](live-testing.md)).

7. Incidents: with `[incidents] enabled = true` and a table that fails in Dev, `LH_Meta/Files/_fabric_desktop/incidents/incidents.json` appears within `incidents.seconds` with one open incident, Teams gets one message, and a second agent (or the app) evaluating at the same time doesn't add another. After a good run the incident is resolved as recovered. Turn the network off for one evaluation and check Teams gets the messages marked Delayed afterwards. Stop a second agent with Task Manager (not cleanly) and check the first opens "Agent … stopped reporting" after its `stale_after`, and recovers it when the second is back. With a route and `quiet_hours` covering now, check a non-critical failure waits and a critical one doesn't.
8. Shared schedules (Preview): give two agents the same `shared = true` schedule every 15 minutes. Over an hour, `requests/` gets one `sched-…` file per quarter hour, and each runs once, on one agent. Stop one agent and check the other runs every slot.
9. Takeover (Preview): with `take_over = true` on two agents, end one agent's process with Task Manager or `kill -9` while it runs a pipeline request. After its `stale_after` and one poll, the other agent's name is in `status/<id>.json`, `agents` lists both, and the run ends `succeeded`. Start the first agent again and check its log says the request was taken over and it writes nothing for it. This is the first time `If-Match` replace on OneLake is used for takeover.
10. Shortcut health: with `[shortcut_health] enabled = true` and `minutes = 15` on a Test agent, make a shortcut in a Test lakehouse and remove its target. Within 15 minutes Teams gets one **Shortcut broken** message, and no second one 15 minutes later or after restarting the agent. Put the target back: one **Shortcut fixed**. Remove it again: one new **Shortcut broken**. With a second agent checking Test, still one message per break. Check the Fabric capacity metrics show no job from the agent.

## Job files

A job file runs DuckDB SQL or a Polars script and publishes the result, with `fabric-desktop preview job.toml` and `fabric-desktop run job.toml`. The agent doesn't run job files yet; see [Limits in this version](#limits-in-this-version).

```toml
name = "sales_by_region"
engine = "duckdb"            # or "polars" with script = "file.py" defining transform()
sql = "select region, sum(amount) as total from read_csv_auto('data/sales.csv') group by region"

[target]
workspace_id = "<workspace guid>"
lakehouse = "Sales"          # created as a schema-enabled lakehouse if missing
schema = "dbo"
table = "sales_by_region"
mode = "overwrite"           # or "append"
```

Relative paths in a job resolve from the job file's folder.

## Limits in this version

- OneLake doesn't report who wrote a request file, so the agent can't record the writer. It can check the pipeline run a request names ([Which pipeline asked](#which-pipeline-asked-preview)). That `@pipeline().Pipeline` and `@pipeline().RunId` are the item id and job instance id the Fabric API takes is still to confirm on a tenant.
- One agent per queue. Two agents on different machines each keep their own queue. Table leases still stop them writing the same table at once, but nothing stops both from running the same schedule, unless it is a [shared schedule](#shared-schedules-preview) (Preview, untested on a tenant). Otherwise, give each environment's schedules to one agent.
- Runs in Fabric don't take write leases. Their writes are still safe (each commits against the version it read and merges again on a conflict), but a busy table means retries instead of waiting in turn. The desktop app's metadata runs, notebook writes and job publishes do take them (see Safe concurrent writes).
- An agent older than the desktop app's table leases takes only its `LH_Meta` lease, so it doesn't wait for a notebook writing the same table. Update agents with the app.
- Notebooks and job files don't run in the agent yet; it runs metadata environments.
- Agent environments run on the machine, whatever their **Runs on** setting in the app.
- Lease expiry uses the holder's clock. Keep agent and PC clocks in sync (NTP; Windows does it by default). A notebook's or job's table lease lasts 60 seconds and is renewed every 20, so two machines whose clocks differ by more than about 40 seconds can take over each other's live lease. Each lease request gives up after a ninth of the lease's length, so a hung renewal is retried, or reported lost, before the lease runs out.
- Incidents (Preview) cover metadata tables and agents only. The app shows them in Monitor > KratosForge > Operations > Incidents ([user guide](user-guide.md#incidents-preview)). The app leaves an environment to the agent while a running agent checks its incidents; when no agent does, the app checks it while open and posts to its own webhook. Quiet hours use the agent machine's clock, and routes are set per agent: two agents with different routes post the same incident to different channels.
- The agent doesn't update itself. Install a new version with `pip install` and restart the service.
