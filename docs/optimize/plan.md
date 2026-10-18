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

One Lakehouse, default `FabricDesktop_Optimization`, separate from LH_Meta. Tables are named `fdopt_<name>` and sit in `dbo` (or directly under Tables in a lakehouse without schemas); no other table is ever touched.

Rules that make many installations safe:
- **Append-only facts.** Every table is insert-only. State changes (a recommendation accepted, applied, dismissed) are new event rows; the current state is the latest event. No shared row is ever updated or deleted by a client.
- **Idempotent ids.** Each record has a deterministic `record_id` (hash of installation id + local sequence, or of the finding fingerprint). Writes are `merge_insert` on `record_id`, so a retry or a resync never duplicates.
- **Bounded batches.** At most N rows (default 5,000) per commit, one commit per table per sync, retried with backoff on a lost race.
- **Provenance on every row.** `installation_id`, `app_version`, `rule_version`, `source`, `created_at`, `schema_version`, and Fabric ids (workspace_id, item_id), never display names as keys.
- **Schema version.** `fdopt_schema_version` holds one row per applied migration. Migrations only add tables or nullable columns; a client that finds a newer major version opens read-only and says why.

Tables (columns abbreviated): `installations`, `analysis_runs`, `artifacts`, `findings` (fingerprint = rule id + artifact id + target), `recommendations` + lifecycle events, `actions`, `measurements` (measurement.py shape), `cu_usage`, `savings` (classification: verified performance / measured CU / estimated CU / potential), `local_execution`, `dependencies`, `rules`, `audit`, `config` (settings shared by everyone using the lakehouse, as events) and `schema_version`.

## 4. Proposed PR sequence

| PR | Content | UI? |
| --- | --- | --- |
| OPT-001a (built) | `optimize/` package: schema, local store (works offline, tests on local Delta), migrations, initialize/attach/validate against a Lakehouse path. Engine only. | No |
| OPT-001b | Set up Optimize screen + Optimize page shell, after the mockup | Yes, mockup first |
| OPT-002 | Shared read/write service, dedup, concurrency tests (two writers on one table) | No |
| OPT-006 | Offline queue and sync status (SQLite queue, backoff, partial sync) | Small |
| OPT-007 | Multiple Lakehouses: register, switch, validate, upgrade, disconnect | Settings |
| OPT-003 | Local execution recording from the engines in section 1 | No |
| OPT-004 | Before/after measurement and savings classification | No |
| OPT-005 | Dashboard and reports; optional Power BI model | Yes |
| OPT-008, 009 | Security docs, retention, overhead accounting (folded into the PRs above where they belong, plus one docs PR) | No |

Order follows the brief's sequence; OPT-001 is split in two so the engine can merge while you review the mockup.

## 5. Agreed with the other programs (2026-10-18)

- Enterprise readiness: Optimize is not tied to the telemetry opt-in (data stays in the org's own tenant). Add an IT policy switch in `app_settings` (DEFAULTS, TYPES, POLICY_KEYS; RESTRICTED only if it must fail closed), documented in azure-setup.md's policy table. Installation id = local uuid4 with a reset, never derived from user, PC or tenant; list every field written, modelled on docs/telemetry.md. Use `audit_chain.py` (#182) for any chained log and #181's `redact.secrets`. Recorded table or workspace names are called out in the docs as identifying.
- Prove: reuse `RunMeter` and `fabric_job_cu` as is.
- UX oversight: will review the rail proposal in the mockup.
- FinOps: deciding who builds the calibration function.
- FinOps calibration contract (for PR-OPT-003, built here under FinOps review unless FinOps starts first): new file `sidecar/fabric_desktop/calibration.py`, no edits to costs.py. `calibrate(workload_key, local_run, baselines, now)` is pure and returns a measurement (metric `fabric_job_cu_avoided`, CU_SECONDS). Only MEASURED Fabric job CU baselines for the same workload count: Prove RunMeter, receipts, side-by-side, FINOPS-003 usage matched by run, or the workload's own Fabric history, each with a source and collected_at. With a baseline the result is ESTIMATED ("measured Fabric CU of N earlier runs of this workload"), and its assumptions say whether it was scaled by rows or bytes, how old the baseline is, and that OneLake I/O isn't counted. With no baseline, one that's too old, or a code version change, the result is UNKNOWN (None, "no measured Fabric baseline"). The costs.py seconds x CU scenario is never shown as "Estimated Fabric CU avoided". Categories: verified/measured = MEASURED, estimated = ESTIMATED, local avoided with no baseline = SCENARIO or UNKNOWN, potential = UNKNOWN.
