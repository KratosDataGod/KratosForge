# Scale and operations plan

How Fabric Desktop grows from "runs on my PC" to "runs large tables, ingests sources, releases from Git and takes work from Fabric pipelines". It covers ten capabilities Chris asked for on 2026-10-16. Five are planned here; the other five belong to two sister programs and are only cross-referenced.

Assessed against `main` at `af11938` (PR #53 merged, sidecar 0.19.0, 322 tests passing on Linux). Nothing here has run against a live Fabric tenant unless it says so.

## Status at a glance

| # | Capability | Today | Owner | Next PR |
| --- | --- | --- | --- | --- |
| 1 | Large-table execution within machine limits | Partial | This plan | Stage 1.1 (MERGE writes, pushdown reads): [PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56), in review |
| 2 | Reproducible project environments | Missing | Notebook program | See its plan |
| 3 | Isolated local-versus-Fabric comparisons | Partial | Notebook program | See its plan |
| 4 | Guided source ingestion | Missing (registry only) | This plan | Stage 4.1, after decision D2 |
| 5 | Integrated Git and release workflow | Partial (promote gates), Git missing | This plan | Stage 5.1 |
| 6 | Explainable workload compatibility | Partial (regex check) | Notebook program | See its plan |
| 7 | Fabric pipeline handoff and remote agent control | Partial (agent built, no mailbox) | This plan | Stage 7.1 |
| 8 | Cross-project lineage and product compatibility | Partial (products, links, consumers) | This plan | Stage 8.1 |
| 9 | Always-on monitoring and incident handling | Partial (in-app freshness alerts) | Workload management program | Run correlation (9.3): [PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58); the rest there |
| 10 | Isolated notebook kernels and debugging | Partial (in-process kernel) | Notebook program | See its plan |

Capabilities 2, 3, 6 and 10, and the run receipt that records what ran, are owned by the **Prove this notebook** program, which keeps its own plan in `docs/prove/plan.md` ([PR #55](https://github.com/KratosDataGod/fabric-desktop/pull/55)).

Capability 9 (monitoring and incidents) and the desktop views for agent availability, execution locations, queues, release inventory, readiness and impact belong to the **Workload management** program. This plan delivers the records those views read: the pipeline job contract and agent status files (7.1), release records (5.1) and lineage and contract data (8.x), each in a documented shape. Its findings for them are summarised under [Owned elsewhere](#owned-elsewhere) so this page stays complete; its plan is the source of truth.

## Assessment

Paths are under `sidecar/fabric_desktop/` unless they start with `app/`, `docs/` or `deploy/`.

### 1. Large-table execution: partial

What exists:

- Incremental loads by watermark: the last value is kept in `meta.watermarks` and only later rows are kept (`meta/engine.py` `_read`, `_watermark`).
- Keyed merge for incremental tables, Type 1 and Type 2 dimensions, quarantine, replay, surrogate keys from LH_Keys, per-table leases for the agent (`table_lock`), commit-race retry (`WRITE_ATTEMPTS`), Fabric-safe profile check after every write (`delta_profile.check_table`), run history in `meta.run_log`.
- `Store.merge_insert` already uses a Delta MERGE, for key tables only (`meta/store.py`).

Gaps before stage 1.1:

- **Every source read loaded the whole table** and filtered afterwards (`_read` called `read_entity` / `read_source_table`, which call `to_pyarrow_table()`); the watermark never reached the scan, and unused columns were read too.
- **Every write read the whole target table, merged in memory and overwrote it** (`_write_once`), so a 1,000-row change to a 5-million-row table rewrote all of it and held two copies in memory.
- Landing-zone and file sources re-read every matching file on every run (`Store.read_files`); there is no record of which files were already loaded.
- No preflight: nothing estimates rows, bytes or memory before a run, and a run that doesn't fit fails with an out-of-memory error, or worse, freezes the PC.
- No partitioning option for entity tables; a full rebuild is always a single in-memory overwrite.
- Dimensions (`scd1`, `scd2`) still need the whole table to issue keys and version rows.

### 4. Guided source ingestion: missing (registry and landing zone partial)

What exists: the source-system registry with per-environment connection fields and a secret-field refusal (`meta/keys.py`), VL_Sources variable library (`meta/varlib.py`), the central catalog's `catalog.source` with admin-only edits (`central/core.py` `save_source`), the landing-zone tab and late-file flag (`central/service.py`, `app/ui/platform.js`), and entities that read `[source] landing = "<folder>/<pattern>"` (`meta/engine.py` `_read`). `fabric_api.list_connections` lists Fabric connections.

Gaps: no extractor or connector for SQL Server, PostgreSQL or REST (no pyodbc, psycopg or sqlalchemy); no Fabric Copy job or pipeline creation in `fabric_api.py`; nothing writes into LH_Landing; no restartable extraction, checkpoints or batch manifests; the strict `>` watermark drops late-arriving rows; no source-delete detection; no wizard.

### 5. Integrated Git and release workflow: partial for promote, missing for Git

What exists: the metadata version is a hash of every config and code file (`meta/project.py` `version`), each run records the per-entity logic fingerprint, promote gates check that every entity ran and passed on the current logic (`meta/promote.py`, `meta_service.py`), project tests gate promote (`meta/testing.py`), promote impact lists changed and new entities (`dataops_service.py`), and `meta.deployments` records environment, version, source environment and time.

Gaps: no Git awareness anywhere (branch, commit, dirty state, remote, PR); deployments don't record who, which commit, which app and engine version or which dependency lock; no rollback of a release (only per-table restore, `meta/restore.py`); no TOML or notebook diff.

### 7. Fabric pipeline handoff and remote agent control: partial

What exists: the headless agent (`agent.py`) with a SQLite job queue, dedupe keys, retries, dead-lettering, recovery of jobs whose worker died (`jobqueue.py`), schedules, SIGTERM handling and `status`; per-table leases in OneLake using conditional requests (`leases.py`, unverified on OneLake); the Fabric runner's result files in `LH_Meta/Files/_fabric_desktop/results/` (`fabric_compute.py`).

Gaps: no request mailbox for pipelines to submit work (FD-13 in [enterprise-roadmap.md](enterprise-roadmap.md)), no cancel or status files, no agent heartbeat that others can read, no agent availability or queue view in the desktop app, no audit of who submitted a job (FD-11).

### 8. Cross-project lineage and product compatibility: partial

What exists: data products with ports, status draft, published or deprecated and a `deprecated_on` date (`meta/mesh.py`); links that can declare the `columns` a consumer relies on; the central catalog stores each port's columns per environment and lists consumers (`central/core.py` `register_product`, `list_products`); a mesh map (`central/service.py`); entity-level lineage inside a project (`meta/project.py` `impact`); drift review and compare (`meta/drift.py`, `meta/compare.py`).

Gaps: no column-level lineage; promote never checks a producer's change against its consumers' declared columns; product `version` is a free integer with no schema history, compatibility ranges or breaking-change classification; deprecation sends nothing and enforces nothing.

### 9. Always-on monitoring and incident handling: partial (owned by the Workload management program)

What exists: freshness promises and status (`meta/freshness.py`), Teams alerts with send-once dedup kept in a per-PC `freshness_alerts.json` (`dataops_service.py` `meta_freshness`), the agent's own job-failure alert (`agent.py`), the late-landing-file flag, and run correlation fields `worker`, `host`, `attempt`, `job_id` in `run_log`.

Gaps: freshness is only evaluated while the desktop app is open (`app/ui/dataops.js` polls every 10 minutes); no incident record, acknowledgement, escalation, recovery notice or shared dedup state; no heartbeat or missing-heartbeat alert; Fabric runs don't write their request id into `run_log`, and Monitor doesn't show worker, host or job.

### Owned elsewhere

| # | Today | Key facts from this assessment |
| --- | --- | --- |
| 2 Environments | Missing | The notebook kernel runs inside the engine's own interpreter; `%pip` is skipped (`notebook.py`); engine pins live only in `sidecar/constraints.txt`; the Fabric runner installs `deltalake>=0.25`/`polars>=1.20` by lower bound (`fabric_compute.py`), so local and Fabric versions can differ. |
| 3 Isolated comparisons | Partial | Side by side runs both sides against the same attached lakehouse and writes real tables twice; inputs are not pinned; MERGE/UPDATE/DELETE tables can only run in Fabric (`side_by_side_service.py`, [side-by-side.md](side-by-side.md)). |
| 6 Compatibility | Partial | `fabric_run.compute_check` is a regex scan returning `{local, fabric: {ok, reasons[{cell, text, code}]}}`; consumers are `service.py`, `side_by_side_service.py` and `app/ui/app.js`. |
| 10 Kernels | Partial | One in-process kernel shared by every notebook; cancel is an async exception, hard stop kills the whole engine (`app/src-tauri/src/main.rs` `stop_kernel`). |

## Foundations shared by several capabilities

| Foundation | Used by | Owner and state |
| --- | --- | --- |
| **Version-checked commits on every write path** (the desktop app and Fabric runs included) and the Windows lease-read fix in `leases.py` | 1, 4, 7, 9 | The "Honest review" thread, [PR #54](https://github.com/KratosDataGod/fabric-desktop/pull/54). Stage 1.1 and #54 both change `Engine._write_once`; whichever merges second takes the other in, and the rewrite fallback in 1.1 then commits against the version it read. No stage here changes `leases.py`. |
| **Control files in LH_Meta** (`Files/_fabric_desktop/`): one place for agent heartbeats, pipeline requests, results and cancel markers, written with the conditional requests `leases.py` already uses; the Workload management program reads them | 7, 9 | This plan, stage 7.1. Needs live check FD-2 (conditional requests on OneLake). |
| **Run receipt**: what ran (code, config, dependencies, engine, inputs) | 3, 5 | Notebook program owns the schema; stage 5.1 records promotions with it rather than inventing a second format. |
| **Scan-based reads** (`Store.scan`, `scan_table_uri`) and **MERGE writes** (`Store.upsert`) | 1, 4, 8 | Stage 1.1. |
| **Code signing, a tenant smoke test** | every live claim | Chris (signing account) and the review thread. Until then everything new is Preview. |

## Stages

Effort: S (a day or less), M (a few days), L (a week or more). Risk is the chance the approach has to change after live validation.

### Capability 1: large tables

**1.1 Incremental writes without rewriting the table: built, [PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56).** Effort M, risk low locally, medium live.

- Outcome: an incremental run of a large table reads only the new rows and the columns it uses, and rewrites only the Parquet files that hold changed rows.
- Change: source reads go through a lazy scan with the watermark filter and column list pushed down (Polars `scan_delta` on this PC, delta-rs's own file system for OneLake); incremental and replay writes become a Delta MERGE on the keys (or an append without keys); a rewrite still happens, and the run says why, when new rows repeat a key or a column's type changed. Full loads, rebuilds and dimensions are unchanged.
- Acceptance: every existing test passes unchanged; new tests prove the MERGE path and the old rewrite path leave identical tables for updates, new rows, null keys, keyless appends, repeated keys, new columns, replay, a lost commit race and a full rebuild; the Fabric-safe profile still holds; a measurement on 5 million rows shows time, memory and files rewritten.
- Migration and rollback: no table, schema or config change, and old tables are read as they are. `Engine(merge_writes=False)` restores the old path in code; reverting the PR restores it entirely.
- Measured (local Delta tables, 4 cores, 16 GB, 1,000 changed and 1,000 new rows): 20 million rows in 40 files went from 28.5 s, 9.3 GB peak and 124 MB rewritten to 1.4 s, 0.8 GB and 3.3 MB; with the changes spread over every file of a 5-million-row table, 7.5 s and 2.6 GB became 2.3 s and 1.2 GB. Both ways ended with identical rows. 333 tests pass.
- Learned: delta-rs MERGE scans every file unless the predicate bounds the keys; adding the incoming key range halved its memory. Changes spread over every file still rewrite every file, so 1.5's partitioning matters for tables updated everywhere.
- Live validation: see [Live verification for stage 1.1](#live-verification-for-stage-11).

**1.2 Dimensions without full reads.** Effort M, risk medium. Type 1 and Type 2 read only key, surrogate-key and tracked columns of the current rows whose business keys arrive (a filtered scan), then write one MERGE that closes changed versions and inserts new ones. Acceptance: `scd2_invariants` hold, equivalence with the rewrite path across the existing dimension stories (`tests/test_meta.py` 04 to 04d), and a measured 1-million-row dimension update. Depends on 1.1.

**1.3 Preflight estimate and memory guard.** Effort M, risk low. Before a run, estimate files, bytes and rows to read from the Delta log (add-action sizes, row counts and min/max statistics against the watermark), compare with the PC's free memory, and warn or stop with a plain explanation; record rows and bytes read in the run's steps. UI goes in the existing entity run dialog, after a mockup. Depends on 1.1.

**1.4 Read only new landing files.** Effort M, risk low. A per-entity manifest in `meta` of files already loaded (path, size, modified time), so a landing or file source reads only new or changed files; idempotent on retry because the manifest is written after the table commit. Shared with ingestion (4.x). Migration: the first run after upgrade records every file already present as loaded only if the entity has run before, so nothing loads twice.

**1.5 Bounded memory for full loads and partitions.** Effort L, risk medium. Optional `[load] partition_by`, full rebuilds written per partition with a replace predicate, Polars streaming collection and delta-rs MERGE spill limits for loads bigger than memory. Measured against TPC-H scale factor 10. Depends on 1.3 (to know when to stream). Fabric guidance on partition sizes goes in the docs.

### Capability 9: always-on monitoring (moved to the Workload management program)

Kept for the record; that program's plan is the source of truth and supersedes 9.1 and 9.2 below. 9.3 was built here before the move ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)).

**9.1 Freshness and incidents on the agent.** Effort M, risk low. The agent evaluates freshness promises on a timer (same `meta/freshness.py` rules), and incidents become rows in `meta.incidents` (key, entity, environment, kind, opened, last seen, count, acknowledged by, resolved) instead of a per-PC JSON file. One Teams message when an incident opens, one when it recovers, a digest instead of a flood when more than a set number open at once. The desktop app reads the same table, so both never alert twice. Acceptance: tests for dedup across two evaluators, recovery, storm digest, and network loss (alerts queue in the agent's SQLite and go out marked delayed). Migration: open entries in `freshness_alerts.json` are imported once.

**9.2 Heartbeats, acknowledgement and escalation.** Effort M, risk medium. The agent writes a heartbeat file to the control area; a missing heartbeat opens an incident (raised by any other agent or the desktop app). Acknowledge from the Monitor page; an unacknowledged incident escalates to a second webhook after a set time. Depends on 9.1 and the control-file foundation.

**9.3 Correlate local and Fabric runs: built, [PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58).** Effort S, risk low. The Fabric runner passes its request id and job instance into `run_log` through `run_context`, and Monitor shows where each run ran (PC, agent and host, or Fabric job). Independent; can go any time.

### Capability 7: pipeline handoff

**7.1 Request mailbox and status contract (FD-13).** Effort L, risk medium (needs FD-2 live). A Fabric pipeline writes `requests/<id>.json` to `LH_Meta/Files/_fabric_desktop/` (a notebook or Copy activity); the agent claims it with a conditional create, enqueues it with the request id as the dedupe key, writes `status/<id>.json` (accepted, running, succeeded, failed, cancelled, rejected with reason), honours `cancel/<id>` markers, and expires requests past their deadline. Authorization: who can write the folder is governed by OneLake permissions on LH_Meta; the agent also checks an allowlist of environments and entities in `agent.toml` and records the submitter (from the file's owner property, if OneLake returns it: to verify). No inbound port. A sample pipeline (Until loop over a Lookup of the status file) ships in `examples/`. Acceptance: tests for duplicate, malformed, expired, cancelled and crashed-mid-run requests; documented contract version.

**7.2 Agent status records.** Effort S, risk low. Each agent writes `agents/<name>.json` in the control area on every loop (version, host, last heartbeat, jobs running, queued, dead), in a documented, versioned shape. The desktop panels that show it belong to the Workload management program. Depends on 7.1.

**7.3 Multiple workers (FD-12).** Deferred until 7.1 has run on a tenant.

### Capability 5: Git and release

**5.1 Git status and recorded releases.** Effort M, risk low. The metadata page shows branch, commit, ahead/behind and uncommitted files, read with the user's own `git` (no new sign-in or scope). Promote records commit, dirty flag, app and engine versions and the dependency lock hash into `meta.deployments` (new nullable columns; old rows read as empty), using the notebook program's receipt fields. Promote to Test or Prod from uncommitted changes is blocked, depending on decision D1. Existing gates are unchanged.

**5.2 Meaningful diffs.** Effort M, risk low. Entity TOML diffs by meaning (contract columns added, removed or retyped, checks and keys changed) and notebook diffs by cell source with outputs stripped, shown in promote impact and before commit.

**5.3 Branches, pull requests and rollback.** Effort M, risk medium. Commit and push a branch with the user's Git credentials and open the repository's compare page to create the pull request (no GitHub app or token stored by Fabric Desktop). Rollback promotes a recorded earlier commit from a temporary worktree, through the same gates.

### Capability 8: lineage and compatibility

**8.1 Breaking-change check at promote.** Effort M, risk low. When a promotion changes a published port, compare its new columns and types with every consumer's declared link `columns` in the central catalog; removed, renamed or narrowed columns that a consumer uses block the promote (or need an exception), and the result names the consumers. Added columns pass.

**8.2 Versioned schema contracts.** Effort L, risk medium. Ports carry a schema version and history in the catalog; consumers declare a compatible range; deprecation sets a date, notifies consumers through the 9.1 alert path and is enforced after it. Depends on 8.1 and 9.1.

**8.3 Column lineage from configuration.** Effort M, risk low. Column-level lineage derived from what the config states (renames, derive expressions, lookups, keys, hook `adds`), labelled declared or inferred; columns produced by custom Python are marked opaque rather than guessed.

### Capability 4: ingestion

**4.1 SQL Server to the landing zone through a Fabric Copy job.** Effort L, risk high until D2 is decided and the Copy job API is checked on a tenant. Connect (pick or create a Fabric connection, so credentials stay in Fabric or Key Vault), Preview (a row-limited copy to a scratch folder), Map (columns and types into a Bronze entity draft), Load (create and run a Copy job writing to `LH_Landing/Files/<source>/<table>/`), Schedule (the Copy job's own schedule). Fabric's Copy job already handles connectors, gateways, incremental copy and retries, so Fabric Desktop does not build its own extractors. Bronze reads new files only (1.4). Depends on D2, 1.4.

**4.2 Late rows and source deletes.** Effort M. A watermark lookback window for late-arriving rows (safe because 1.1's MERGE is idempotent per key) and an optional periodic key snapshot that marks deleted keys. Depends on 4.1.

**4.3 PostgreSQL and REST.** After 4.1 works on a tenant; same flow, different connection type.

## Delivery sequence

1. **1.1** MERGE writes and pushdown reads ([PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56), in review) and **9.3** run correlation ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58), in review).
2. **1.2** dimensions (stacked on 1.1), then **1.3** preflight.
3. **7.1** pipeline job contract and **7.2** agent status records, built and tested locally; live use waits for FD-2 (conditional requests on OneLake).
4. **5.1** Git status and recorded releases, after the notebook program publishes its receipt schema; then **5.2**, **8.1**.
5. **1.4** landing manifest, then **4.1** once D2 is answered.
6. **1.5**, **5.3**, **8.2**, **8.3**, **4.2**, **4.3**, **7.3**.

Large tables come first because every later stage runs through the same write path. The pipeline contract comes before Git because the Workload management program's queue and location views read its records. Git comes after the receipt schema so promotions and proofs share one record.

## Decisions needed

- **D1.** Should promotion to Test and Prod require a committed, pushed revision (recommended), or only warn? Blocks the gate part of 5.1, not the status view.
- **D2.** Who creates landing-zone writers (Copy jobs into LH_Landing): only the central platform team (current assumption, matching [platform.md](platform.md)), or domain teams through a request the central team approves? Blocks 4.1.

## Dependencies outside this plan

- Concurrent writes from the desktop app and Fabric runs are made safe by version-checked commits in [PR #54](https://github.com/KratosDataGod/fabric-desktop/pull/54) (review thread). Stage 1.1 keeps the agent's lease and the commit retry; its MERGE is itself a conflict-checked commit, and its rewrite fallback adopts #54's version check when both are merged.
- Code signing and a tenant smoke test gate every move from Preview to tested.

## Live verification for stage 1.1

On a non-production workspace with an environment set to "Tables in: Workspace":

1. Run an incremental entity with keys twice, the second time with a few changed and a few new source rows. The run's write step should say "Delta MERGE rewrote N files". Expect the same row count as before plus the new rows.
2. In the lakehouse, open the table: the SQL analytics endpoint and a Direct Lake model should show the new values (Delta history shows a MERGE operation).
3. In the entity's **History**, restore the version before the MERGE; the table goes back, and the next run merges again.
4. Set the environment to **Runs on: Fabric** and repeat step 1: the runner uses the same code path.
5. Run the same entity from two agents at once: one waits for the lease, both succeed, and no rows are lost.

## Change log

| Date | Change |
| --- | --- |
| 2026-10-16 | Capability 9 and the agent, queue and release views moved to the Workload management program; 9.3 built ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)). |
| 2026-10-16 | Plan written; capabilities 2, 3, 6, 10 assigned to the notebook program; stage 1.1 built and measured ([PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56)). |
