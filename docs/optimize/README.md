# Optimize and the central lakehouse

Optimize keeps optimization results in one Microsoft Fabric lakehouse that every KratosForge installation in your organization can write to and read from. That includes findings, recommendations, applied fixes, measurements, local runs and savings. The lakehouse is the system of record; nothing about it lives only on one PC.

> **Status: Preview.** None of this has been tried on a live Fabric tenant yet. The plan and what is left is in [plan.md](plan.md#4-pr-sequence-and-status).

## One lakehouse for the organization

- One lakehouse is enough for a whole organization. You don't need one per developer, project or workspace.
- If you need separate boundaries, such as business units, regulated data or separate environments, use separate optimization lakehouses. Each installation is connected to one at a time and can switch between the ones it knows.
- The default name is `FabricDesktop_Central`: it is KratosForge's central lakehouse, where other features keep their organization-wide data too ([Table families](#table-families)). It is a separate item from the platform's `LH_Meta`, which is per environment. A lakehouse created before this release under the old default name, `FabricDesktop_Optimization`, keeps working as it is.

## Where it is in the app

**Optimize** in the left rail (Preview), in the place the Fabric page had; the Fabric portal is now a tab of Workspaces. It has five tabs: **Overview**, **Opportunities**, **Assessment** (shown only while Preview features are shown), **Cost and savings** and **Settings history** ([user guide](../user-guide.md#optimize-preview)). The lakehouses this PC uses are in **Settings > Optimize lakehouses**.

## Setting it up

The first person creates the lakehouse: they pick a workspace and a name, and the app does the rest. It creates the lakehouse, creates the tables, records the schema version and default settings, checks it can read and write, and remembers the connection on that PC. Nobody runs SQL or creates tables by hand.

Everyone else connects to the existing lakehouse, and nothing is created again. When the app connects to a lakehouse, the outcome depends on what is already in it:

| The lakehouse holds | What happens |
| --- | --- |
| Optimize's tables at the current version | It connects |
| Nothing | It asks before creating Optimize's tables |
| Other tables, none of Optimize's | It asks, then adds Optimize's tables beside them. The other tables are never read, changed or replaced |
| An unfinished setup or upgrade | It asks, then adds only what is missing. Every row is kept |
| An older Optimize schema | It asks, then upgrades. Upgrades only add tables and columns, and every row is kept. Until someone upgrades, newer installations keep writing with the older version's columns, and other features' tables keep working |
| A newer Optimize schema | It opens read-only and asks you to update the app |
| A table named `fdopt_...` that isn't Optimize's | It refuses to write and leaves the table alone |

Disconnecting only removes the lakehouse from this PC's list. The lakehouse and its data stay as they are, and connecting again picks up where you left off. The app never deletes an optimization lakehouse.

## Permissions

Optimize signs in as you, the same way as the rest of the app (see [Permissions](../permissions.md)). It needs no new permissions and no Fabric administrator role.

| To | You need |
| --- | --- |
| Create the lakehouse | Contributor or higher on the workspace |
| Add results | Contributor or higher on the workspace |
| Read shared results only | Read all data (ReadAll) on the lakehouse. A workspace Viewer reads lakehouses through the SQL analytics endpoint, not OneLake files, so a Viewer needs the lakehouse shared with Read all data |

The app finds out what you can do by trying it. It reads the schema version, then registers this installation (a row it would write anyway). With read access only, it opens the lakehouse read-only and says so. It doesn't read the workspace's role assignments, which would need admin rights.

## What each installation writes

Each installation writes rows into the shared tables. Every row records which installation wrote it, when, which app version wrote it and which schema version it was written against.

| Field | Example | What it is |
| --- | --- | --- |
| `installation_id` | `3b1f6c2e-...` | A random id made on this PC the first time Optimize needs it. It isn't worked out from you, your account, the PC or your tenant, and it isn't the [usage data](../telemetry.md) install id. **New installation ID** replaces it; rows already written keep the old one |
| `label` | `Build server` | Empty unless someone names the installation |
| `os` | `Windows` | The operating system family, without a version or machine name |
| `app_version` | `0.20.0` | The engine version that wrote the row |
| `created_at`, `registered_at` | `2026-10-18T09:30:00+00:00` | When the row was written |

Analysis runs add the Fabric items they looked at, the problems found and the recommendations made; later releases add measurements and local run records. Those rows hold Fabric workspace and item ids, and the workspace, lakehouse, table and item names they are about. **Names are identifying**: anyone who can read the lakehouse can read them. **SQL text and code are never written.** Only fingerprints of them are stored (the `store_sql_text` setting is `never`).

## Who can see what

Optimize relies on Fabric's own security. It adds no access layer of its own.

- Anyone who can read the optimization lakehouse can read every row in it, including which installation wrote what. OneLake security roles can limit access by table or folder ([OneLake security model](https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model)). Optimize does not rely on row-level filtering for people reading the Delta files directly.
- When groups mustn't see each other's diagnostics, give each group its own optimization lakehouse in a workspace only they can reach.
- Access tokens are never written to the lakehouse.

What each table holds, what in it is identifying, what isn't enforced yet (such as retention) and what hasn't been tried on a live tenant are in [Optimize: security and retention](security.md).

## How several installations write safely

- **Insert only.** No installation updates or deletes a shared row. A change, such as a recommendation being accepted, is a new row, and the latest row wins.
- **No duplicates.** Every row has a `record_id` worked out from what it records. Rows are written with an insert-only Delta MERGE on it, so a retry or a second sync adds nothing.
- **Bounded batches.** Rows go in batches of at most 5,000, one commit each. A batch that fails after its retries stops the write; the batches before it stay, and writing again adds only what's missing.
- **Bounded retries.** A commit that loses a race with another installation, throttling or a network blip is tried up to five times in all, with growing waits. A permission error is never retried.
- **Race-safe setup.** Two installations setting up the same lakehouse at once end up with one set of tables and one schema version row.
- **Recoverable migrations.** A migration only adds tables or columns, and each step can safely run again. Its version is recorded only after every step succeeded, so a migration that stopped part way finishes the next time.

## Before a lakehouse is connected

Overview lists this PC's last Estate X-ray findings, most severe first, each with its fix. **Effort** says what the fix asks of you: **Low** is one button that asks first and then runs in Fabric (Optimize, Vacuum, Remove staging), **Medium** is steps you take yourself (open the lakehouse, review access, open the notebook). **Dismiss** asks why and hides the finding on this PC only; it stays dismissed when the next scan finds it again, and **Show dismissed** lets you reopen it. Dismissing changes nothing in Fabric.

## When the lakehouse can't be reached

When a write to the central lakehouse fails for a reason that passes (no network, throttling, a commit that lost a race with another installation, a dropped upload), what it was writing waits on this PC instead of being lost. Today that covers Estate X-ray findings and shared runs on this PC.

Sharing runs normally reads which runs the lakehouse already has. With no network at all it can't, so every run not already waiting on this PC is queued; any that turn out to be in the lakehouse already are skipped when they're sent.

- **Where it waits.** In `optimize\outbox.sqlite` in the app's data folder, kept for the lakehouse it was meant for. Errors are stored with secrets removed. Nothing in it is sent anywhere else. If the file is damaged, it's moved aside (`outbox.sqlite.corrupt-<time>`) and a new one starts, so Optimize keeps working; what waited in the old file isn't sent, and running the X-ray or sharing runs again records it.
- **When it's sent.** The next time Optimize writes to that lakehouse (a finished X-ray scan, sharing runs), and when you connect or switch to it. What waits goes first, oldest first. There's no timer: nothing is sent in the background.
- **No duplicates.** Every row has its `record_id`, so sending something twice, or finishing a batch that was half sent, adds nothing. Queuing the same thing again doesn't add it twice either: a scan shared again replaces the waiting one, and runs already waiting aren't queued again, so repeats don't fill the limit below.
- **What isn't kept.** A permission error, a lakehouse open read-only, or the policy switch being off isn't a passing failure: it's shown as before and nothing waits. A waiting item the lakehouse refuses as it is gets dropped, so it can't hold up the rest.
- **Bounded.** At most 200 items per lakehouse, none older than 30 days. Past either, the oldest go. Every dropped item is counted.
- **Disconnecting** keeps what waits for that lakehouse on this PC, to be sent if you connect to it again.
- **Status.** `optimize_status` returns `outbox` for the active lakehouse: `pending` (items), `pending_rows`, `oldest`, `last_sync`, `last_error` (cleared by the next send that works) and `dropped`. Optimize shows it as one line under the lakehouse name: everything sent, results waiting on this PC with why, or how many were dropped, with **Details** for the figures. The line only reads; it never sends.
- **Sync state.** `optimize_status` also returns `sync` for the active lakehouse: a `state` for the lakehouse and one per kind of result (`kinds`: `analysis`, `local_executions`), each one of **synced** (nothing waiting), **synchronizing** (a send is running now), **pending** (waiting to try again after throttling or a lost commit race), **offline** (the lakehouse couldn't be reached), **failed** (the lakehouse refused this person: no permission, or read-only) or **conflict** (its schema version doesn't fit this app: a newer one, so someone has to update the app, or an older one without the table being written, so someone has to upgrade the lakehouse). A lakehouse whose fdopt_ tables aren't Optimize's isn't written to at all, so nothing waits for it. The lakehouse's state is the worst of its kinds, and synchronizing while a send runs (`waiting_state` keeps the worst underneath). Each kind has `pending` and `pending_rows`; `dropped` and `last_sync` are as above. A send that never said it ended (the app closed mid-send) stops counting as running after 10 minutes. Engine only: no screen shows these yet.

## What syncing costs

Each time Optimize writes results to the central lakehouse, it records what that write took, as three **Measured** rows in `fdopt_measurements`, all with the same `run_id` and with `workload_key` `optimize.sync.analysis` or `optimize.sync.local_executions`:

| Metric | Unit | What it is |
| --- | --- | --- |
| `optimize_sync_seconds` | seconds | How long the write took on this PC |
| `optimize_sync_rows` | count | Rows handed to the lakehouse, including rows it already had (it adds those once) |
| `optimize_sync_commits` | count | Delta commits made |

The commit that writes these three rows isn't counted in them. Bytes written aren't measured yet. OneLake reads and writes use capacity, but no CU figure is given for them until that use is measured. If these rows can't be written, the results still count as sent.

## Findings and recommendations

Every installation adds what its analysis finds to the shared lakehouse, and everyone connected sees the combined picture.

Today the analysis is the [Estate X-ray](../user-guide.md#estate-x-ray-preview): every finished scan on a PC connected with write access adds its lakehouse and table findings as one run (rule ids `xray.small_files`, `xray.unidentified`, `xray.stale`, `xray.vacuum`, `xray.empty`, `xray.staging`; the target is `schema.table`, or empty for a finding about a whole lakehouse), with an `optimize` or `vacuum` recommendation where X-ray has that fix. Access findings name people and cost findings are estimates for one PC, so neither is added. The rows name the workspaces, lakehouses and tables the scan reached, and anyone who can read the central lakehouse can read them all, whatever they can open in Fabric ([Who can see what](#who-can-see-what)). Give the central lakehouse's readers no more than they may know about those workspaces, or use separate optimization lakehouses. A scan that can't write keeps its results on the PC and says why.

Other features can add their own analysis the same way, through Optimize's one writer: they shape the run, items, findings and recommendations, and Optimize asks first. The ask says how many findings and recommendations, about how many items in how many workspaces, and that everyone who can read the lakehouse sees them. Writing needs the person's confirmation of exactly those rows: if anything changed since they looked, it asks again. Titles, details, evidence, reasons and impact are written with anything credential-shaped removed, whichever feature wrote them. The optimization engine's assessment (rule ids `oe.*`) is the first to use this. Its Share button comes after a mockup.

- **One finding per problem.** A problem is identified by the rule that found it, the Fabric item (by workspace and item id) and what in it the rule looked at, such as a table. When several PCs find the same problem it shows once, with when it was first and last seen, in how many analysis runs and by how many installations.
- **A run counts once it's complete.** An analysis run's own row is written after its findings. If a PC loses its connection part way, nothing from that run shows until it's recorded again, and recording it again adds only the rows that are missing.
- **One recommendation per problem and kind of fix**, opened by whichever installation sees it first. Accepting, applying, dismissing or reopening it is recorded as a change with who made it, when and why, and everyone sees the latest. A dismissed recommendation stays dismissed when the problem is seen again; someone has to reopen it.
- **Allowed changes.** Open can become accepted, applied, dismissed or superseded. Accepted can become applied, dismissed or open again. Applied can become verified or open again. Dismissed and verified can only be reopened. Every state except superseded can also become superseded, and superseded is final.
- **Only items you can open.** Findings and recommendations about items in workspaces you can't open in Fabric aren't shown to you. This is a courtesy, not security: anyone who can read the lakehouse can read every row ([Who can see what](#who-can-see-what)).

## Runs on this PC

Optimize can count the notebook work that ran on a PC instead of in Fabric. Sharing it adds one row per run to `fdopt_local_execution`:

- **Notebook writes to OneLake tables**, from this PC's write history. Each row names the lakehouse (by workspace and item id) and the table, and records when the write finished, how long it took and how many rows it wrote. Runs of a [job file](../notebooks.md#job-files) (`fabric-desktop run job.toml`) are kept in the same history and are shown as **Job run**, named after the job.
- **Scheduled notebook runs on this PC**, from **Schedules**. Each row names the notebook and records when the run started and ended, how long it took and whether it succeeded. Metadata schedules aren't counted here; their runs come from the run log, below.
- **Metadata runs on this PC**, from the open metadata project's run log in each environment kept on this PC (Dev by default). Each row names the project and table, and records when the run started and ended, how long it took, rows written and whether it succeeded. Runs in Fabric or by an agent are left out, and an environment in Fabric isn't read, so sharing never reads OneLake for them.
- **Proof runs**, from **Prove**. The PC side of each finished proof, with the CPU time and peak memory Prove measured. A proof doesn't record when its PC side began, so only the proof's end is given.

CPU time and peak memory are filled in only where they were measured (a proof's run). Everywhere else they're empty, never 0.

Code and table contents are never written. A run is added once: sharing again adds only the runs that are new since the last time, and two PCs never share each other's runs. The engine call is `optimize_share_local_runs`. Without `approve=True` it writes nothing and says how many runs it would add and what they contain, with a `confirm` value for exactly those runs. It writes only with `approve=True` and that `confirm`. If a run finished in between, it asks again rather than share a run nobody was shown. The button that calls it, in **Optimize > Cost and savings**, comes after a mockup. A run on a PC starts no Fabric job, but its OneLake reads and writes still use capacity, so these rows count work, not savings.

## How one run's savings are split

When a workload has been optimized and also runs on a PC, its savings could be counted twice. `sidecar/fabric_desktop/savings/attribution.py` splits one run into layers that never add up to more than the workload's baseline (the Fabric CU one run used before any optimization):

1. Each optimization in effect when the run happened, in the order they took effect, takes its measured reduction from what was left. A notebook optimized from 10 to 6 CU hours has an optimization layer of 4.
2. A run on a PC avoids at most what the optimized workload still needs (6 in that example), taken from [the calibrated estimate](../finops/cost-evidence.md#how-savings-on-this-pc-are-estimated). So the run avoids at most 10 in total, never 14.
3. Fabric use the run still caused (OneLake reads and writes, sync) is subtracted for the net figure. When that isn't known, the net figure is marked incomplete.

An optimization that made things worse has a negative layer. One whose after isn't measured yet can't be split from the ones after it, so the run's total goes to one unattributed layer until it is. Missing evidence stays Unknown, never 0. Each layer's id is the same on every PC, so a second desktop syncing the same run adds nothing. Nothing is written to the lakehouse yet: savings rows wait for a measured Fabric baseline.

Savings rows are never edited or deleted. When a run's figures change (an after that was Unknown is measured, or a Capacity Metrics export is revised), the run is attributed again and `savings/corrections.py` writes the difference for each layer as a correction that points at the row it corrects. A layer that is no longer part of the run gets a reversal of its full amount, and a run that should never have counted, such as one credited to a change that was never deployed, gets reversals that bring each of its rows back to zero. A layer that becomes Unknown withdraws what it counted and shows as Unknown, not 0. Doing this twice, or on two PCs, gives the same rows. The reconciliation report adds up every run from these rows alone and compares the result with the latest attribution, listing each difference with its amounts and anything that doesn't fit, such as two corrections of the same row. It fixes nothing itself. A difference is fixed by writing a correction.

Savings are counted only per run. A day, week or month figure (`savings/recurring.py`, `by_period`, cut in UTC) is the runs in it added up, with each run counted once even if two PCs synced it, and runs whose figure is unknown counted beside the total, never as 0.

The **recurring monthly benefit** (`project`) is the average net CU avoided per run times how many runs with a known figure the workload had in the last 30 days, scaled to an average month (365.25 / 12 days). It is always Estimated, because it is about runs that haven't happened, and it is never written. It stops when the workload is retired or hasn't run in the window, and it uses only runs under the workload's current baseline, so a re-baselined workload needs new runs first. Fewer than three runs with a known figure give Unknown with the reason. A run whose figure is unknown is left out, never filled in with an average, so the projection may be low; it says how many were left out and is marked incomplete.

**What avoided CU is worth** (`savings/pricing.py`) is priced under a named pricing scenario: a rate per CU hour with its SKU, region, currency, pricing model (pay as you go, reserved, actual billing or an assumption), the dates it applies, its source and how confident it is. Each run is priced at the version in effect when it ran, so a price change applies only from its date and earlier runs keep their price. Today's single price (Optimize > Cost and savings > Prices) is the first scenario, labelled an assumption. The figure is always a Scenario, a price applied to CU and not a change in what was billed, so it is never added to measured or estimated figures. Two versions of one scenario whose dates overlap, or one scenario in two currencies, are refused. Nothing is written to the lakehouse yet.

What a run on a PC still cost (`savings/overhead.py`) is kept as separate figures: transfer seconds, bytes read and written, OneLake read and write operations, and its share of the sync that carried it (seconds, rows, commits). Each is Measured when it was recorded and Unknown when it wasn't, never 0. A sync that carried several runs has its seconds shared by the rows each run sent, labelled Estimated, adding up to exactly the sync's seconds; its commits aren't split. None of these is Fabric CU, and nothing turns them into CU, because a PC doesn't measure the capacity OneLake reads, writes and sync use, so the overhead subtracted for net stays Unknown, and net incomplete, until a measured or estimated CU figure is supplied, such as OneLake operations from an imported Capacity Metrics export.

**Baselines and validation** (`savings/baselines.py`): a workload's baseline is the median Fabric CU per run of its runs in a measurement window, counting only runs of the same workload, definition version and input scope with a valid CU figure. The others are listed with the reason. Fewer than three usable runs give Unknown, never 0, and a baseline of measured and estimated runs is Estimated. A new baseline (for example after the definition changes) is a new version that supersedes the old one without editing it. An optimization is checked by comparing runs after it with that baseline on the same inputs. It is Verified only when both sides are measured, the CU didn't go up and the outputs match (or it is stated why they can't be compared). Outputs that differ fail it, a higher measured CU is a regression that moves it back to Validating, and an estimate, too few runs or a figure in seconds never verifies it, because how long a run took isn't how much capacity it used. Nothing is written to the lakehouse yet.

## The tables

All are Delta tables in `dbo` (or directly under Tables in a lakehouse without schemas), named `fdopt_` so they can't collide with yours: `installations`, `analysis_runs`, `artifacts`, `findings`, `recommendations`, `actions`, `measurements`, `cu_usage`, `savings`, `local_execution`, `dependencies`, `rules`, `audit`, `config` and `schema_version`, and from schema version 2 `pricing`, `optimization_link`, `baseline`, `validation`, `attribution` and `sync`. Columns are in [`sidecar/fabric_desktop/optimize/schema.py`](https://github.com/KratosDataGod/KratosForge/blob/main/sidecar/fabric_desktop/optimize/schema.py). You never need to query them yourself.

## Table families

The central lakehouse holds more than Optimize's tables. Each feature that keeps organization-wide data there has its own family of tables, with its own prefix, schema version and upgrades:

| Prefix | Owner | Holds |
| --- | --- | --- |
| `fdopt_` | Optimize | Findings, recommendations, measurements and savings, plus the installations, audit and settings every family shares |
| `fdacc_` | Governance (Preview) | The access cache: who can reach what, updated with changes only ([below](#the-access-cache-fdacc_)) |
| `fdcap_` | FinOps (Preview) | [Capacity settings history](#capacity-settings-history) |
| `fdten_` | Enterprise readiness (Preview) | [Tenant settings history](#tenant-settings-history) |
| `fdinc_` | Incident management (planned) | Incident history |

Every family follows the same rules: insert-only rows with a `record_id`, provenance on every row, upgrades that only add, and a newer schema opening that family read-only (the others keep working). A family never writes to or migrates another family's tables; reading them, for example a view that combines two families, is fine. Each family has one owner, so two features that store rows of the same shape still use two families (and can share the column definitions). The first time a feature writes to a connected central lakehouse, it asks you, then adds its own tables beside the others; nothing that is already there changes. Tables that name people, such as the access cache, also get their own OneLake security role at table level, set up by the feature that adds them.

### Tables only some people may read

A family whose tables name people keeps them in a schema of its own (for example `access` instead of `dbo`). When its owner sets up who may read them, the app asks first, then:

- changes **DefaultReader**, the role that lets everyone with read permission see every table, so it no longer reads that schema. It keeps DefaultReader's members and every path an admin gave it. "Everything" becomes every schema that isn't a family's own, plus Files, so tables added to `dbo` later stay readable without another change. Every family's own schema is left out, not just this one, so another family can be kept from DefaultReader later too;
- adds one role, or updates it, that reads only that schema, for the people chosen. Running it again with the same people changes nothing;
- leaves every other role as it is, and records the change in `fdopt_audit`.

It reads the roles again just before it writes, and refuses if what it would change is no longer what you confirmed; Fabric also refuses the write if someone changes the roles in between. It refuses, and changes nothing, when the central lakehouse has no schemas (hiding the tables would mean listing every other table, which would hide tables added later), when OneLake security is off or there is no DefaultReader, when DefaultReader has more than one rule or was given part of that schema on purpose, or when the role name is already used for something else. A schema created later stays hidden from DefaultReader until someone adds it in OneLake > Access; the confirmation says so. It uses the `OneLake.ReadWrite.All` permission that OneLake > Access already uses ([Permissions](../permissions.md)), and the right in Fabric to manage the lakehouse's roles.

### The access cache (`fdacc_`)

The access cache lets OneLake > Access show every workspace at once instead of asking Fabric about each one each time. A refresh asks Fabric, as you, for each workspace's roles and each lakehouse's OneLake data access roles, and writes to the central lakehouse only, in its own `access` schema:

- `fdacc_scans`: one row per workspace and lakehouse looked at, with when, whether the roles could be read, and why not;
- `fdacc_workspace_roles` and `fdacc_data_access_roles`: what was seen, only when it changed since the last refresh. An unchanged workspace adds one scan row and nothing else.

A refresh changes nothing in the workspaces it looks at. When a workspace's roles can't be read, the cache keeps the last list it could read and marks it out of date, never an empty list. You only see cached workspaces you can open in Fabric. The cache keeps names, ids, types (person, group, app), whether someone is a guest, and roles; it keeps no email addresses. The first refresh asks before adding the tables. Refreshing needs write access to the central lakehouse; reading needs read access.

Keeping the cache to some people: **Who may read it** (`access_cache_protect`) uses the central lakehouse's `central_restrict`. DefaultReader stops reading the `access` schema and keeps its members and everything else it read. A new role, `AccessCacheReaders`, lets the people and groups you pick read it. It shows the planned change and asks first, and the change is recorded in `fdopt_audit`. It needs OneLake security turned on for the central lakehouse, and a lakehouse with schemas. Until you protect it, anyone who can read the central lakehouse's tables can read the cache.

The cache is shown in OneLake > Access > **All workspaces** ([user guide](../user-guide.md#access-preview)).

### Tenant settings history

A Fabric administrator's read of Fabric's tenant settings (`GET /v1/admin/tenantsettings`) is kept in three `fdten_` tables, in the same row shape as FinOps' capacity settings (`fdcap_`), so one view reads both:

| Table | One row per | Columns besides provenance |
| --- | --- | --- |
| `fdten_snapshot` | Setting whose value differs from its last row | `taken_at`, `scope_type` (`tenant`), `scope_id` (tenant id), `setting`, `value_json`, `value_hash` (SHA-256 of the canonical JSON), `source`, `read_by` |
| `fdten_change` | Difference found | `changed_after` and `changed_by_time` (the two reads it happened between), `scope_type`, `scope_id`, `setting`, `old_json` (empty the first time), `new_json` (empty when a setting disappears), `source`, `read_by` |
| `fdten_read` | Read attempt | `read_at`, `scope_type`, `scope_id`, `source`, `outcome` (`read`, `unreadable` or `failed`), `reason` (with secrets removed), `read_by` |

- **What counts as a value.** A setting's value is everything Fabric returns for it except its name, title and group, with security groups and properties in a fixed order. Microsoft renaming a setting isn't a change.
- **When and by whom.** A change is only ever "between these two reads". Fabric's tenant settings API says neither when a setting changed nor who changed it, so the history doesn't claim either.
- **People.** `read_by` holds the reader's Microsoft Entra object id, never a name or email.
- **Who sees it.** Anyone who can read the central lakehouse can see the tenant settings an administrator read, and their history.
- **Only reads.** It never changes a setting. A read by someone who isn't a Fabric administrator is recorded as `unreadable`, so a gap in the history shows as a gap, not as "nothing changed".
- **What's built.** The engine calls `tenant_settings_read` and `tenant_settings_history`, untested on a live tenant, and the **Settings history** tab (Preview) below.

### Capacity settings history

A read of the capacities you can see (`GET /v1/capacities`, `Capacity.Read.All`, no Fabric administrator role) is kept in three `fdcap_` tables with the same columns as the tenant settings history, so its history shows both:

| Table | One row per |
| --- | --- |
| `fdcap_snapshot` | Capacity setting (`displayName`, `sku`, `region`, `state`) whose value differs from its last row; `scope_type` is `capacity` and `scope_id` the capacity id |
| `fdcap_change` | Difference found, between two reads |
| `fdcap_read` | Capacity read, or one row for a read that failed (`unreadable` without `Capacity.Read.All`, otherwise `failed`) |

- **Only what you can see.** People see different capacities, so a capacity missing from your read isn't recorded as changed or deleted. It just isn't compared, and its last read shows how old its history is.
- **A missing field isn't a change.** If Fabric leaves a field out of one response, that setting isn't compared in that read, so a hiccup doesn't show as the setting being removed and added back.
- **Overrides, for Fabric administrators.** With `admin=True`, a Fabric administrator's read also records the tenant settings each capacity overrides (`GET /v1/admin/capacities/delegatedTenantSettingOverrides`, `Tenant.Read.All`), as settings named `override:<settingName>` with `source` ending in `(admin)`. That read covers every capacity, so an override that disappears is recorded as removed. It's only asked for when chosen, so nobody else is prompted for `Tenant.Read.All`; anyone else's attempt is recorded as `unreadable`, and their capacities are still read.
- **How big it gets.** `fdcap_read` adds one row per capacity per read, and `fdcap_snapshot` and `fdcap_change` grow only when something changes. Nothing is deleted: tables are insert-only, like every central family, so read on a schedule that matches how closely you need to follow changes (daily is plenty for most).
- **When and by whom.** As for tenant settings, a change is only ever "between these two reads", and `read_by` is an Entra object id.
- **Only reads.** It never resizes, pauses or changes a capacity.
- **What's built.** The engine call `capacity_settings_read`, untested on a live tenant; `tenant_settings_history` returns its changes and gaps beside the tenant's.

### Settings history tab

**Optimize > Settings history** (Preview) shows both histories in one list. It only reads the six tables above until you press a button:

- **Capacities last read** and **Tenant settings last read**: the newest successful read of each.
- **Read capacities now** calls `capacity_settings_read`, with `admin=True` when **Include capacity overrides (administrators)** is ticked (off each time the app starts). When the capacities were read but the overrides weren't, the message says so instead of only "nothing changed"; **Read tenant settings (administrators)** calls `tenant_settings_read`. Each reads Fabric once and adds rows. The first read in a lakehouse asks once before it creates the tables. One read runs at a time: the buttons are off until it ends. Cancel is remembered on this PC for that lakehouse: later reads don't ask again, and the tab offers **Set up history** inline, which creates the tables and reads.
- A warning lists reads that failed since the last good one, one line per kind with how many and the latest reason: changes made then weren't seen.
- Anyone who can read the central lakehouse sees the tab's history, including tenant settings an administrator read; the tab says so.
- **Changes** lists each change as "between" two reads, with the scope (Tenant, or the capacity's latest name), the setting and its old and new values. **Show** filters to the tenant, all capacities or one capacity.
- Nothing reads on a schedule.

For developers: a family is a `schema.Family` registered with `schema.register` at import of the module that owns it. Its prefix is `fd`, 2 to 8 lowercase letters and `_`; its migrations are numbered from 1, and the first creates `schema_version`. `OptimizeMixin.central_family(family, approve=...)` returns the connected lakehouse with the family's tables in place; it creates or upgrades tables only with `approve=True`, passed after the person confirmed, and otherwise raises with what it would do. Write with `setup.write(..., family=family)`. A family's `default_config` keys are stored in `fdopt_config` with the family's prefix. `Family(..., schema="access")` puts its tables in a schema of its own (lowercase, not another family's); then `OptimizeMixin.central_restrict(family, role_name, members, approve=...)` keeps them from DefaultReader as above, raising with what it would change (as a sentence, and as a list in the error's `changes`) unless `approve=True`; pass that list back as `shown=`, which `approve=True` requires, so it refuses if they no longer match. It is untested on a live tenant: its ETag and If-Match handling follow Fabric's documentation only. Families never PUT data access roles themselves.

## For IT admins

Set this in `%ProgramData%\FabricDesktop\policy.json` ([Configure Azure and Fabric](../azure-setup.md#4-push-settings-to-everyone)):

| Key | Effect |
| --- | --- |
| `allow_optimize_lakehouse: false` | The app doesn't create, connect to or write to the central lakehouse, for Optimize or any other table family. Analysis on the PC is not affected |

Optimize is separate from usage data. Its rows go to a lakehouse in your own tenant, never to KratosBI, so the usage data switch doesn't affect it.
