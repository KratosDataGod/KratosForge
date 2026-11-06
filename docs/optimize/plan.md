# Optimize program plan (PR-OPT-001 to 009)

The centralized optimization lakehouse: what already existed when the program started (audit of `main` at d5faebb, 2026-10-18), the risks found, the data model and the PR order. How it works for people using it: [README.md](README.md).

## 1. What exists today

### There is no "Home optimization tray"
Neither `main` nor any open or closed PR has an optimization tray or an Optimize page. The pieces that do optimization work today are spread over four places:

| Where | What it does | Code |
| --- | --- | --- |
| Home > Estate X-ray | Scans every reachable lakehouse, ranks findings (small files, stale tables, unidentified folders, vacuum due, costly runs) and offers fixes (Optimize, Vacuum with dry run, Adopt). Results kept only on this PC in `xray.json`. | `xray.py`, `xray_service.py`, `app/ui/xray.js` |
| Data mesh > table health | Per-table files, size, V-Order, versions since VACUUM, "Optimize all in Fabric", optimize-after-run switch. | `maintenance.py`, `meta.js` ~line 1040 |
| Monitor > Cost | Every run as a row with classified CU evidence; "Fabric use avoided on this PC". | `costs.py`, `measurement.py`, `estate_service.cost_report` |
| Prove | Side-by-side runs, scorecards, receipts, CPU and memory via `RunMeter`, measured `fabric_job_cu` (#175). | `prove_service.py`, `scorecard.py`, `receipts.py`, `side_by_side*.py` |

The bell icon's "Activity tray" (`activity.js`) is a run/problem feed, not optimization.

### Local execution instrumentation
| Engine | What is recorded | Where |
| --- | --- | --- |
| Metadata runs (This PC) | run_id, entity, seconds, rows read/written, worker/host | `meta` schema `run_log` (Delta, LH_Meta or local) |
| SQL (managed execution, SQL-003) | state, permission class, rows, seconds. Never SQL text | `sql_exec.py` (in memory + audit) |
| Notebook writes to OneLake | job, seconds | feeds `costs.activities(notebook_writes)` |
| Prove runs | wall time, CPU time, peak memory, receipts | `measure.RunMeter`, `proofs.jsonl` |
| Side by side | PC vs Fabric time for the same notebook | `side_by_side.jsonl` |
| PC schedules | last result per schedule | `schedule_runs.jsonl` |
| Notebook cells, local data engine queries, pipeline simulation (ORCH), data quality tests | audit line only (action, outcome, seconds), no rows/bytes/CPU | `audit.py` |

Gaps: CPU and memory are measured only inside Prove; bytes processed is recorded nowhere; no run carries an installation id that is safe to share.

### Lakehouse connectivity and storage services to reuse
- **Create lakehouse**: `FabricClient.create_lakehouse` (`fabric_api.py:141`), schema-enabled, waits on the long-running operation. List lakehouses: `fabric_api.py:132`.
- **Delta on OneLake**: `meta/store.py` already has the patterns the brief asks for: `create_if_absent` (put-if-absent version 0, safe when two writers race), `read_at` + write against a read version (optimistic concurrency, `CommitFailedError` on a lost race), `merge_insert` (insert-only MERGE for dedup by key), custom commit metadata. `onelake.storage_options` builds the OneLake addressing.
- **Retries**: `retrying.py` classifies transient errors (408/429/5xx, lost commit race). `leases.py` gives per-table write leases in LH_Meta.
- **Durable local queue**: `jobqueue.py` (SQLite, lease, exponential backoff, dead-letter) and `workloads/store.py` (SQLite with `SCHEMA_VERSION` migrations) are the models for the offline queue.
- **Auth**: `auth.py` (signed-in user via Microsoft's Fabric CLI public client + WAM, Fabric and Storage tokens) and `credentials.py` (service principal by certificate or secret env var, managed identity) already exist. No new scopes appear to be needed: Lakehouse create uses Workspace.ReadWrite.All, Delta writes use the Storage token.
- **Evidence model**: `measurement.py` (measured / estimated / scenario / unknown, never 0 for unknown, a total is as weak as its weakest part) and `costs.py`.
- **Governance**: `audit.py` (hash-chained local audit), `redact.py` (secret removal), `telemetry.py` (opt-in), policy.json, `retention.py`.

## 2. Conflicts and risks found

1. **Local savings method conflicts with the brief.** `costs.py` prices a local run as "local seconds x configured CU" (classified *scenario*, never added to totals). The brief forbids converting local time directly into CU. Optimize will show that scenario figure only as context and will report **Estimated Fabric CU avoided** only when a calibrated baseline exists (a measured Fabric run of the same workload from Prove/side-by-side, or the workload's own Fabric history). Otherwise it shows local activity with "No baseline yet". This keeps FinOps' model intact; FinOps owns the CU model, so the calibration lives next to `costs.py`.
2. **Read-only users.** Per Microsoft's lakehouse role docs (https://learn.microsoft.com/en-us/fabric/data-engineering/workspace-roles-lakehouse), a workspace Viewer reads through the SQL endpoint, not OneLake files; direct OneLake reads need Contributor or the item's ReadAll permission. So read-only reporting users need ReadAll on the optimization Lakehouse. To be verified on a tenant and documented, not worked around. The app will detect write access with a harmless probe (create-if-absent of a marker row) rather than by reading role assignments, which needs Admin.
3. **Isolation inside one Lakehouse.** OneLake security roles can limit access by table and folder (https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model); row-level filtering for a client reading Delta files directly is not something I can rely on without testing. So the design assumes anyone who can read the Lakehouse can read every row. Plan: never store SQL text by default (fingerprint + redacted shape only), store installation ids that are random and not tied to a person, and support separate Lakehouses for separate security boundaries, as the brief allows. To be documented in docs/optimize/security.md.
4. **Concurrent writers on Delta.** Every writer appending to the same table produces commit races and small files. Plan below avoids update-in-place on shared tables entirely.
5. **Installation id.** `telemetry.install_id` is opt-in and resettable, so it can't be reused. Optimize gets its own random id.
6. **Left rail is full** (Home, Create, Workspaces, OneLake, Data mesh, Platform, Fabric, Monitor). Promoting Optimize means one of them merges elsewhere; the mockup will propose which.
7. **Nothing touches a live tenant until you say so.** Creating the Lakehouse is a Fabric write; all tests use local Delta folders.

## 3. Proposed data model (PR-OPT-002)

One Lakehouse, default `FabricDesktop_Central` (Chris, 2026-10-18: Optimize's lakehouse is KratosForge's central lakehouse; other features add table families such as `fdacc_` and `fdset_`), separate from LH_Meta. Tables are named `fdopt_<name>` and sit in `dbo` (or directly under Tables in a lakehouse without schemas); no other table is ever touched.

Rules that make many installations safe:
- **Append-only facts.** Every table is insert-only. State changes (a recommendation accepted, applied, dismissed) are new event rows; the current state is the latest event. No shared row is ever updated or deleted by a client.
- **Idempotent ids.** Each record has a deterministic `record_id` (hash of installation id + local sequence, or of the finding fingerprint). Writes are `merge_insert` on `record_id`, so a retry or a resync never duplicates.
- **Bounded batches.** At most N rows (default 5,000) per commit, one commit per table per sync, retried with backoff on a lost race.
- **Provenance on every row.** `installation_id`, `app_version`, `rule_version`, `source`, `created_at`, `schema_version`, and Fabric ids (workspace_id, item_id), never display names as keys.
- **Schema version.** `fdopt_schema_version` holds one row per applied migration. Migrations only add tables or nullable columns; a client that finds a newer major version opens read-only and says why.

Tables (columns abbreviated): `installations`, `analysis_runs`, `artifacts`, `findings` (fingerprint = rule id + artifact id + target), `recommendations` + lifecycle events, `actions`, `measurements` (measurement.py shape), `cu_usage`, `savings` (classification: verified performance / measured CU / estimated CU / potential), `local_execution`, `dependencies`, `rules`, `audit`, `config` (settings shared by everyone using the lakehouse, as events) and `schema_version`.

## 4. PR sequence and status

Status checked against `main` at 7f8badf (2026-11-06). Merged PRs are listed so the remaining work is clear; nothing below has been tried on a live Fabric tenant.

| PR | Content | Status |
| --- | --- | --- |
| OPT-001a | `optimize/` package: schema, local store, migrations, initialize/attach/validate | Merged (#186) |
| OPT-001b | Optimize rail page: set-up card, Overview, Opportunities, Cost and savings, Settings > Optimize lakehouses | Merged (#200, #219, #825, #827, #1472) |
| OPT-002 | Shared read/write service (`optimize/shared.py`): stable ids, bounded batches, run row last, findings by fingerprint, recommendation events, workspace-scoped reads | Merged (#189) |
| Central lakehouse | `FabricDesktop_Central` with table families, a family's own schema, `central_restrict` | Merged (#228, #262) |
| X-ray into Optimize | X-ray findings written to the central lakehouse; Optimize acts on this PC's X-ray | Merged (#343, #865, #1454) |
| OPT-003 (part 1) | `optimize/local_runs.py` and `optimize_share_local_runs`: notebook writes to OneLake tables and scheduled notebook runs | Merged (#356), engine only, no button yet |
| FINOPS-005 | `calibration.py`: Fabric CU avoided only from Fabric runs of the same workload, else Unknown | Merged (owned by FinOps) |
| OPT-006 | Offline queue and sync status | Merged (#1726, #1733) |
| OPT-010 | Opportunities: Effort column and Dismiss for this PC's findings (approved action-first mockup) | Merged (#1834, #1876) |
| OPT-011 | Estate X-ray: an action for every finding, and empty, DataflowsStaging and FUAM lakehouses handled | Planned, mockup first (new guides on the page) |
| OPT-007 | Multiple lakehouses: the rest | Planned |
| OPT-003 (part 2) | Local execution: metadata runs and measured proof runs (engine) | Merged (#2040); the Share button waits for a mockup |
| OPT-004 | Measurements and savings rows | Planned |
| OPT-012 | Optimize motion events | Merged (#2147); the savings effect waits for Measured savings |
| OPT-013 | Local vs Fabric advisor screen (FinOps advisor) | Planned, mockup first |
| OPT-005 | Dashboard and reports | Planned, mockup first |
| OPT-008 | Security and retention | Part 1 merged (#2147); the retention action needs a mockup first |
| OPT-009 | Optimize's own overhead | In review (seconds, rows and commits; bytes not yet) |

OPT-010 to 013 were found missing in the closed-thread audit of 2026-11-03: Chris asked for them (unit testing items 9 and 10 on 2026-10-18, the action-first go on 2026-10-26, the FinOps brief's FINOPS-005 screen, the motion brief's Optimization experience) and the threads that held them closed without building them.

One PR per row, in this order. Each engine PR comes with tests on local Delta folders; each UI PR waits for a mockup Chris has reacted to.

### OPT-006: Offline queue and sync status
What's wrong today: when the lakehouse can't be reached, a finished X-ray scan keeps the error with its result and its findings never reach the lakehouse (`xray_service.py`, "never fails the scan"). Shared local runs are only sent when someone asks.
- A local SQLite outbox under the app's data folder, modelled on `jobqueue.py`: one row per pending batch, keyed by lakehouse id, with attempts, next try and the last error (secrets removed with `redact.py`).
- A write that fails for a transient reason (`retrying.py`'s classes: network, throttling, a lost commit race) goes in the outbox instead of being dropped. A permission error, a read-only lakehouse or the policy switch being off is not queued: it's shown, as today.
- The outbox is drained before the next write to that lakehouse (a finished scan, sharing runs) and when a lakehouse is connected or switched to. Not at app start: sending needs a sign-in, and nothing sends in the background. Rows already carry a `record_id` and are written with insert-only MERGE, so sending twice adds nothing and a part-sent batch finishes cleanly.
- Bounded: a cap on rows and age per lakehouse; past the cap the oldest pending batch is dropped and that is recorded and shown, never silent.
- `optimize_status` gains `outbox`: `pending` (batches and rows), `last_sync`, `last_error`, `dropped`. Engine only in this PR; a status line on Optimize > Overview follows after a mockup.
- Disconnecting a lakehouse keeps its pending rows until it's connected again. Discarding them (with a confirm) comes with the OPT-007 Settings work.

### OPT-010: Effort column and Dismiss for this PC's findings
Optimize > Opportunities already shows Effort and Dismiss for shared recommendations. The table of this PC's X-ray findings (AF-5, #865) has Improvement, Impact and State only. Add the Effort column and a Dismiss with a reason, as in the action-first mockup Chris approved on 2026-10-26. A dismissal is kept on this PC with its reason (`xray_dismiss`, secrets removed) and can be undone; a later scan finds it still dismissed. Effort comes from the fix, never the data: Low is one button that asks first and runs in Fabric (Optimize, Vacuum), Medium is steps the person takes. Shared recommendations already have their own Dismiss; carrying a PC's dismissal into the shared lakehouse is left for later.

### OPT-011: An action for every X-ray finding
Chris's unit testing (2026-10-18, items 9 and 10): every finding gets an in-app action that follows best practice and is documented. Today an empty lakehouse only offers Open in Fabric and "Delete it if nothing will use it".
- **Empty lakehouse:** the steps to check nothing uses it (shortcuts, notebooks, pipelines, semantic models) before deleting it in Fabric. The app doesn't delete it.
- **DataflowsStagingLakehouse:** recognized by name and shown as Dataflow Gen2's own staging, with a guide to removing leftovers safely. The cleaner itself is PR #799 (Leftover dataflow staging lakehouses), owned by its own thread; this PR links to it and doesn't duplicate it.
- **FUAM and other monitoring solutions' lakehouses:** recognized and marked "leave alone", with no fix offered, because they belong to that solution.
- Every finding kind (small files, not changing, needs vacuum, empty, unidentified folders, access, cost, adopt) has an action or a "How to fix this" guide; a test fails if a kind has neither.

### OPT-012: Optimize motion events
Built in part: `optimize.analysis.finished` and `optimize.recommendation.applied`; `optimize.savings.verified` waits for Measured savings rows (OPT-004) and the screen that shows them. Optimize had no events in the motion system (`FORGE_EVENTS` in `app/ui/motion.js`). Add `optimize.analysis.finished`, `optimize.recommendation.applied` and `optimize.savings.verified`, on finished state changes only, using the existing effects and settings. Per the motion brief, savings figures are never animated in a way that makes them look bigger, and only a Measured result gets the verified effect.

### OPT-013: Local vs Fabric advisor screen
FINOPS-005's placement advice (RECOMMEND LOCAL, RECOMMEND FABRIC, EITHER, INSUFFICIENT INFORMATION) exists only for AI tools (`fabric_desktop_placement_advice`); PB-017 was accepted with "has no screen" as its limitation. Show it next to the notebook's Runs on control, with its evidence one click away, never switching the choice for the person. Mockup first.

### OPT-007: Multiple lakehouses, the rest
Register, switch and disconnect are in (#186). Left: an explicit **Upgrade** for a lakehouse on an older schema (asks first; upgrades only add), **Check all** in Settings that validates each registered lakehouse read-only, and showing each one's pending outbox rows from OPT-006.

### OPT-003 part 2: Local execution, the rest
- Metadata runs from `run_log` (seconds, rows written), from environments kept on this PC only. Built.
- Proof runs, with CPU time and peak memory only where they were measured (`measure.RunMeter` in Prove); empty, never 0, elsewhere. Built.
- A **Share runs from this PC** button on Optimize (mockup first), using the existing confirm text.

### OPT-004: Measurements and savings
- `fdopt_measurements` rows from `calibration.calibrate` and from Prove's measured `fabric_job_cu`, with the classification, source, method and assumptions they already carry.
- `fdopt_savings` rows, each with one classification: **Measured** (a measured Fabric CU baseline and a measured comparison), **Estimated** (calibrated from measured Fabric runs of the same workload), **Potential** (a recommendation not yet applied) or **Unknown** (no baseline). Figures of different classifications are never added together.
- Before and after for an applied recommendation (for example file count and table size around an X-ray Optimize): a measured performance change, never turned into CU.

### OPT-005: Dashboard and reports
Overview totals read from the shared tables, grouped by classification, with each figure's evidence one click away. An optional Power BI model over the `fdopt_` tables is a separate decision for Chris. Mockup first.

### OPT-008: Security and retention
- Built: `docs/optimize/security.md`, and a recommendation's reason is shared with secrets removed.
- `docs/optimize/security.md`: who can read what, what each table holds, what counts as identifying (table and workspace names), the read-only reporting role (ReadAll), and what's verified on a tenant versus not.
- Retention: `retention_days` exists as a setting but nothing enforces it. Deleting shared rows is destructive, so retention will be an administrator action with a dry run that lists what would go, never automatic, and recorded in `fdopt_audit`.

### OPT-009: Optimize's own overhead
Built, except bytes: seconds, rows and commits per sync, using `measurement.py`'s new `count` unit. Bytes written aren't measured yet.
Each sync records what it cost on the PC and in OneLake: rows, commits, bytes written and seconds taken, as Measured local figures in `fdopt_measurements`. OneLake reads and writes use capacity, so the docs say that, but no CU figure is given for them until it's measured.

### Savings claims
No CU savings figure has been measured for Optimize yet, and none of these PRs claims one. A CU saving is shown only when there is a measured Fabric baseline for the same workload (Prove, side by side, measured CU import from PB-089, or the workload's own Fabric history); otherwise it shows **Unknown, no measured Fabric baseline**. Run time on this PC is never converted into CU. Before any savings figure appears in release notes or docs, a baseline run on the test workspace is recorded with its source and date, which needs Chris's go because it uses capacity.

## 5. Agreed with the other programs (2026-10-18)

- Enterprise readiness: Optimize is not tied to the telemetry opt-in (data stays in the org's own tenant). Add an IT policy switch in `app_settings` (DEFAULTS, TYPES, POLICY_KEYS; RESTRICTED only if it must fail closed), documented in azure-setup.md's policy table. Installation id = local uuid4 with a reset, never derived from user, PC or tenant; list every field written, modelled on docs/telemetry.md. Use `audit_chain.py` (#182) for any chained log and #181's `redact.secrets`. Recorded table or workspace names are called out in the docs as identifying.
- Prove: reuse `RunMeter` and `fabric_job_cu` as is.
- UX oversight: will review the rail proposal in the mockup.
- FinOps: deciding who builds the calibration function.
- FinOps calibration contract (for PR-OPT-003, built here under FinOps review unless FinOps starts first): new file `sidecar/fabric_desktop/calibration.py`, no edits to costs.py. `calibrate(workload_key, local_run, baselines, now)` is pure and returns a measurement (metric `fabric_job_cu_avoided`, CU_SECONDS). Only MEASURED Fabric job CU baselines for the same workload count: Prove RunMeter, receipts, side-by-side, FINOPS-003 usage matched by run, or the workload's own Fabric history, each with a source and collected_at. With a baseline the result is ESTIMATED ("measured Fabric CU of N earlier runs of this workload"), and its assumptions say whether it was scaled by rows or bytes, how old the baseline is, and that OneLake I/O isn't counted. With no baseline, one that's too old, or a code version change, the result is UNKNOWN (None, "no measured Fabric baseline"). The costs.py seconds x CU scenario is never shown as "Estimated Fabric CU avoided". Categories: verified/measured = MEASURED, estimated = ESTIMATED, local avoided with no baseline = SCENARIO or UNKNOWN, potential = UNKNOWN.
