# Scale and operations plan

How Fabric Desktop by KratosBI grows from "runs on my PC" to "runs large tables, ingests sources, releases from Git and takes work from Fabric pipelines". It covers ten capabilities Chris asked for on 2026-10-16. Five are planned here; the other five belong to two sister programs and are only cross-referenced.

Assessed against `main` at `af11938` (PR #53 merged, sidecar 0.19.0, 322 tests passing on Linux). Nothing here has run against a live Fabric tenant unless it says so.

## Status at a glance

| # | Capability | Today | Owner | Next PR |
| --- | --- | --- | --- | --- |
| 1 | Large-table execution within machine limits | Partial: 1.1 to 1.5 merged | This plan | 1.3's run dialog after a mockup |
| 2 | Reproducible project environments | Missing | Notebook program | See its plan |
| 3 | Isolated local-versus-Fabric comparisons | Partial | Notebook program | See its plan |
| 4 | Guided source ingestion | Missing (registry only) | This plan | Stage 4.1, after decision D2 |
| 5 | Integrated Git and release workflow | Partial: release records and meaningful diffs merged | This plan, with the Git-native program | Promote rollback (5.3); Git and Source Control screens are the Git-native program's |
| 6 | Explainable workload compatibility | Partial (regex check) | Notebook program | See its plan |
| 7 | Fabric pipeline handoff and remote agent control | Partial: 7.1 and 7.2 merged, untested on a tenant | This plan | 7.3 after a live run |
| 8 | Cross-project lineage and product compatibility | Partial: 8.1 and 8.3 engine merged | This plan | Lineage view after a mockup; 8.2 (platform owner decision) |
| 9 | Always-on monitoring and incident handling | Partial (in-app freshness alerts) | Workload management program | Run correlation (9.3) merged ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)); the rest there |
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

**1.1 Incremental writes without rewriting the table: merged, [PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56).** Effort M, risk low locally, medium live.

- Outcome: an incremental run of a large table reads only the new rows and the columns it uses, and rewrites only the Parquet files that hold changed rows.
- Change: source reads go through a lazy scan with the watermark filter and column list pushed down (Polars `scan_delta` on this PC, delta-rs's own file system for OneLake); incremental and replay writes become a Delta MERGE on the keys (or an append without keys); a rewrite still happens, and the run says why, when new rows repeat a key or a column's type changed. Full loads, rebuilds and dimensions are unchanged.
- Acceptance: every existing test passes unchanged; new tests prove the MERGE path and the old rewrite path leave identical tables for updates, new rows, null keys, keyless appends, repeated keys, new columns, replay, a lost commit race and a full rebuild; the Fabric-safe profile still holds; a measurement on 5 million rows shows time, memory and files rewritten.
- Migration and rollback: no table, schema or config change, and old tables are read as they are. `Engine(merge_writes=False)` restores the old path in code; reverting the PR restores it entirely.
- Measured (local Delta tables, 4 cores, 16 GB, 1,000 changed and 1,000 new rows): 20 million rows in 40 files went from 28.5 s, 9.3 GB peak and 124 MB rewritten to 1.4 s, 0.8 GB and 3.3 MB; with the changes spread over every file of a 5-million-row table, 7.5 s and 2.6 GB became 2.3 s and 1.2 GB. Both ways ended with identical rows. 333 tests pass.
- Learned: delta-rs MERGE scans every file unless the predicate bounds the keys; adding the incoming key range halved its memory. Changes spread over every file still rewrite every file, so 1.5's partitioning matters for tables updated everywhere.
- Live validation: see [Live verification for stage 1.1](#live-verification-for-stage-11).

**1.2 Dimensions without full reads: merged, [PR #61](https://github.com/KratosDataGod/fabric-desktop/pull/61).** Effort M, risk medium.

- Built: only the versions of the arriving business keys are read; the existing `scd1_merge`/`scd2_merge` run on that subset with the table's next key, `scd2_invariants` check it, and one MERGE on the surrogate key writes it. A Type 2 full snapshot first drops rows that change nothing. Lookups and `exists_in` checks read only their columns. Falls back to a rewrite, saying why, when most of the table's keys arrived, keys repeat or types changed.
- Measured: a 5-million-key Type 2 dimension fed a full snapshot with 1,000 changed and 1,000 new keys went from 24.1 s, 3.7 GB and 52 MB written to 6.5 s, 2.1 GB and 5.5 MB; identical rows. 336 tests pass.
- Discovered: a Type 2 run whose source repeats a key fails with a Polars `ShapeError`, before and after; a clear error (or a required `unique` check for dimensions) is a follow-up. Key assignment (`meta/keys.py`, data-mesh work) still reads each whole key table, which now dominates keyed runs on large key tables; raise with that owner.

**1.3 Preflight estimate and memory guard.** Effort M, risk low. Before a run, estimate files, bytes and rows to read from the Delta log (add-action sizes, row counts and min/max statistics against the watermark), compare with the PC's free memory, and warn or stop with a plain explanation; record rows and bytes read in the run's steps. UI goes in the existing entity run dialog, after a mockup. Depends on 1.1.

*Engine part merged in [PR #69](https://github.com/KratosDataGod/fabric-desktop/pull/69).* Delta sources only; file sources wait for 1.4. The guard stops a read when three times the estimate exceeds free memory: on a 5M-row first load the peak was 2.5 times the estimate. `[load] memory_guard = false` opts out, and `meta_preflight` serves the run dialog. The dialog itself waits for a mockup.

**1.4 Read only new landing files.** Effort M, risk low. A per-entity manifest in `meta` of files already loaded (path, size, modified time), so a landing or file source reads only new or changed files; idempotent on retry because the manifest is written after the table commit. Shared with ingestion (4.x). Migration: the first run after upgrade records every file already present as loaded only if the entity has run before, so nothing loads twice.

*Merged in [PR #70](https://github.com/KratosDataGod/fabric-desktop/pull/70).* The migration turned out simpler than planned. Before this, every run read every file, so the first run after upgrading reads them all once more, as it would have anyway, and records them; no file is marked loaded without being read. Incremental and dimension modes use the list. Full loads and full backfills read every file, and the backfill records them all. No new files is a successful run that writes nothing. `[source] new_files_only = false` opts out. Read settings (`new_files_only`, `memory_guard`) no longer count as logic changes. Measured locally with 31 files of 100,000 rows plus one new one: 2.8 s reading 100,000 rows, against 7.3 s reading 3.1M.

**1.5 Bounded memory for full loads and partitions.** Effort L, risk medium. Optional `[load] partition_by`, full rebuilds written per partition with a replace predicate, Polars streaming collection and delta-rs MERGE spill limits for loads bigger than memory. Measured against TPC-H scale factor 10. Depends on 1.3 (to know when to stream). Fabric guidance on partition sizes goes in the docs.

*Built* (narrower than planned; the differences are listed below). `[load] batch_by = "<column>"` reads a Delta source in batches of that column's values: consecutive values, sorted, with as many rows in a batch as fit in half the free memory after the 3x headroom of 1.3 (`meta/batches.py`); when everything fits in one batch the ordinary single read runs instead. Every step runs per batch on a worker thread, and the batches stream into `write_deltalake` through a one-slot queue, so the table is replaced in **one Delta commit** and a load that stops changes nothing. Applies to full loads and to an incremental entity's first load or full rebuild (which keeps rows whose keys the source lost, as before). The memory guard checks the largest batch. `unique` checks compare with earlier batches' values. Not done, with reasons:
- No `partition_by` and no physical partitioning: the target table's layout is left alone, because changing a table's partitioning means rewriting all of it. Batching is a read strategy only.
- No per-partition replace predicate: one streamed commit is atomic, which a commit per partition isn't.
- No delta-rs MERGE spill limit: incremental MERGEs already read only new rows (1.1), so nothing to bound yet.
- Measured on lineitem-shaped generated data, not TPC-H dbgen output: the DuckDB tpch extension needs a download this environment can't make.
- A stopped load leaves Parquet files no table version uses; the app's Vacuum doesn't remove them yet (it would need delta-rs `vacuum(full=True)`).

Measured with `examples/benchmark/batched_loads.py` on a 4-core, 15 GB machine (a full load with a derived column and a not-null check; local Delta tables, nothing on OneLake):

| Rows | Read | Batches | Seconds | Peak memory |
| --- | --- | --: | --: | --: |
| 6M (scale 1) | whole source at once | - | 4.8 | 1,964 MB |
| 6M | `batch_by`, batch size from free memory | 1, so one ordinary read | 4.7 | 1,949 MB |
| 6M | `batch_by`, `--batch-rows 1000000` | 7 | 3.8 | 1,556 MB |
| 60M (scale 10) | `batch_by`, batch size from free memory | 6, the largest 11.5M rows | 51.9 | 7,206 MB |

At 60M rows the plan estimates the whole read at 13.0 GB in memory, so with the 3x headroom the memory guard stops a single read on this machine; it wasn't run. Both 6M-row ways wrote the same rows (a checksum over every row). Learned: delta-rs holds each record batch it is handed until it has written it, so handing it a whole batch as one record batch doubled peak memory (9.2 GB at 60M rows with 6.9M-row batches); 131,072-row record batches brought that to 4.6 GB.

### Capability 9: always-on monitoring (moved to the Workload management program)

Kept for the record; that program's plan is the source of truth and supersedes 9.1 and 9.2 below. 9.3 was built here before the move ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)).

**9.1 Freshness and incidents on the agent.** Effort M, risk low. The agent evaluates freshness promises on a timer (same `meta/freshness.py` rules), and incidents become rows in `meta.incidents` (key, entity, environment, kind, opened, last seen, count, acknowledged by, resolved) instead of a per-PC JSON file. One Teams message when an incident opens, one when it recovers, a digest instead of a flood when more than a set number open at once. The desktop app reads the same table, so both never alert twice. Acceptance: tests for dedup across two evaluators, recovery, storm digest, and network loss (alerts queue in the agent's SQLite and go out marked delayed). Migration: open entries in `freshness_alerts.json` are imported once.

**9.2 Heartbeats, acknowledgement and escalation.** Effort M, risk medium. The agent writes a heartbeat file to the control area; a missing heartbeat opens an incident (raised by any other agent or the desktop app). Acknowledge from the Monitor page; an unacknowledged incident escalates to a second webhook after a set time. Depends on 9.1 and the control-file foundation.

**9.3 Correlate local and Fabric runs: merged, [PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58).** Effort S, risk low. The Fabric runner passes its request id and job instance into `run_log` through `run_context`, and Monitor shows where each run ran (PC, agent and host, or Fabric job). Independent; can go any time.

### Capability 7: pipeline handoff

**7.1 Request mailbox and status contract (FD-13).** Effort L, risk medium (needs FD-2 live). A Fabric pipeline writes `requests/<id>.json` to `LH_Meta/Files/_fabric_desktop/` (a notebook or Copy activity); the agent claims it with a conditional create, enqueues it with the request id as the dedupe key, writes `status/<id>.json` (accepted, running, succeeded, failed, cancelled, rejected with reason), honours `cancel/<id>` markers, and expires requests past their deadline. Authorization: who can write the folder is governed by OneLake permissions on LH_Meta; the agent also checks an allowlist of environments and entities in `agent.toml` and records the submitter (from the file's owner property, if OneLake returns it: to verify). No inbound port. A sample pipeline (Until loop over a Lookup of the status file) ships in `examples/`. Acceptance: tests for duplicate, malformed, expired, cancelled and crashed-mid-run requests; documented contract version.

*Merged in [PR #64](https://github.com/KratosDataGod/fabric-desktop/pull/64).* The claim is the conditional create of the status file itself, so no separate claim file is needed. A request runs only the environment whose `LH_Meta` it is written to, so OneLake permissions on that lakehouse decide who may submit. Off unless `[pipeline] enabled = true`, with an entity allowlist and a backfill opt-in. `deadline` is the latest start time. A cancel stops a request before it starts or before a retry, and a run already writing finishes. Instead of an Until/Lookup pipeline, the sample is one Python notebook (`examples/pipeline/run_on_agent.ipynb`) for a Notebook activity: it submits, waits, and fails the activity unless the run succeeded. Two things changed from this plan. The submitter is the pipeline's `submitted_by` text, because reading the file's writer from OneLake is unverified. Writing through the `/lakehouse/default` mount also needs a live check. 34 tests; 356 passed in all.

**7.2 Agent status records.** Effort S, risk low. Each agent writes `agents/<name>.json` in the control area on every loop (version, host, last heartbeat, jobs running, queued, dead), in a documented, versioned shape. The desktop panels that show it belong to the Workload management program. Depends on 7.1.

*Merged in [PR #66](https://github.com/KratosDataGod/fabric-desktop/pull/66).* Contract 1 records go to `agents/<name>.json` every `agent.status_seconds`. Each record has `stale_after` (three beats) for offline detection and `running`/`stopping`/`stopped`, and it covers only its own environment's jobs, so one workspace's readers never see another's work. 6 tests; 362 passed in all.

**7.3 Multiple workers (FD-12).** Deferred until 7.1 has run on a tenant.

### Capability 5: Git and release

**5.1 Git status and recorded releases.** Effort M, risk low. The metadata page shows branch, commit, ahead/behind and uncommitted files, read with the user's own `git` (no new sign-in or scope). Promote records commit, dirty flag, app and engine versions and the dependency lock hash into `meta.deployments` (new nullable columns; old rows read as empty), using the notebook program's receipt fields. Promote to Test or Prod from uncommitted changes is blocked, depending on decision D1. Existing gates are unchanged.

*Engine part merged in [PR #73](https://github.com/KratosDataGod/fabric-desktop/pull/73).* `meta/release.py` reads branch, commit, upstream, ahead/behind and uncommitted files with the PC's `git` (never prompts, never fetches), counting only files that make up the metadata version. Every promote writes a release receipt (the notebook program's `run-receipt@1`, kind `release`: commit, dirty, pushed, a hash per entity file, engine version, a lock hash over Polars, delta-rs, DuckDB and PyArrow versions, the login name) into new nullable `meta.deployments` columns, with its id, so the Workload management views and anyone else can check it. No remote URL is recorded, since it can hold credentials. Until D1 is answered, files no pushed commit holds are a note on the promote card, and `require_commit = true` per environment in `environments.toml` turns the note into a block. `meta_git_status` serves the status view, which moved to the Git-native development program. The "app version" became the engine version: the sidecar is versioned with the app. 10 tests.

**5.2 Meaningful diffs.** Effort M, risk low. Entity TOML diffs by meaning (contract columns added, removed or retyped, checks and keys changed) and notebook diffs by cell source with outputs stripped, shown in promote impact and before commit.

*Engine part merged in [PR #75](https://github.com/KratosDataGod/fabric-desktop/pull/75).* `meta/diff.py` compares parsed TOML. It reports contract columns, contract keys, list items (cleaning, derived columns, checks, surrogate keys, lookups), which it matches by identity so an edit reads as "changed", and any other setting by path. Each change says whether it changes the table's logic. `meta_diff` compares against the last commit or the version promoted to an environment. Promote impact lists each changed table's changes under it. `notebook_diff` compares cells by id, on source only, against another notebook or the notebook's last commit when the notebooks folder is in Git. The before-commit view moved to the Git-native development program with the other Source Control screens.

**5.3 Branches, pull requests and rollback.** *Branches and pull requests moved to the Git-native development program on 2026-10-17, which owns all Git operations and Source Control screens; rollback stays here and will use its Git layer.* Effort M, risk medium. Commit and push a branch with the user's Git credentials and open the repository's compare page to create the pull request (no GitHub app or token stored by Fabric Desktop by KratosBI). Rollback promotes a recorded earlier commit from a temporary worktree, through the same gates.

### Capability 8: lineage and compatibility

**8.1 Breaking-change check at promote.** Effort M, risk low. When a promotion changes a published port, compare its new columns and types with every consumer's declared link `columns` in the central catalog; removed, renamed or narrowed columns that a consumer uses block the promote (or need an exception), and the result names the consumers. Added columns pass.

*Merged in [PR #71](https://github.com/KratosDataGod/fabric-desktop/pull/71).* The catalog has no per-consumer column lists yet, so the check is per port: dropping a column, or changing its type other than by widening, blocks promotion to an environment another project reads, and names those projects. Raising the product's `version` lets the change through with a note, which is the agreed way to publish a breaking version. An unreachable catalog is a note, not a block. No platform schema or function change; per-consumer column lists belong to 8.2, which needs a catalog change and a function redeploy by the platform owner. 5 tests.

**8.2 Versioned schema contracts.** Effort L, risk medium. Ports carry a schema version and history in the catalog; consumers declare a compatible range; deprecation sets a date, notifies consumers through the 9.1 alert path and is enforced after it. Depends on 8.1 and 9.1.

**8.3 Column lineage from configuration.** Effort M, risk low. Column-level lineage derived from what the config states (renames, derive expressions, lookups, keys, hook `adds`), labelled declared or inferred; columns produced by custom Python are marked opaque rather than guessed.

*Engine part merged in [PR #76](https://github.com/KratosDataGod/fabric-desktop/pull/76).* `meta/lineage.py` gives each column one record: how it's made (source, derived, lookup, key, framework, custom), a declared, inferred or opaque label, and its inputs. A derived column's inputs are read from its expression by Polars' SQL parser. Inputs a column uses from its own table point at that table's column, so cleaning and renames sit on the path. Columns a hook runs after carry `custom_may_change`. `meta_lineage` returns one entity's columns, or one column's full upstream path and every downstream column in the project. Lineage across projects (through mesh links) and the view wait for 8.2 and a mockup. 7 tests.

### Capability 4: ingestion

**4.1 SQL Server to the landing zone through a Fabric Copy job.** Effort L, risk high until D2 is decided and the Copy job API is checked on a tenant. Connect (pick or create a Fabric connection, so credentials stay in Fabric or Key Vault), Preview (a row-limited copy to a scratch folder), Map (columns and types into a Bronze entity draft), Load (create and run a Copy job writing to `LH_Landing/Files/<source>/<table>/`), Schedule (the Copy job's own schedule). Fabric's Copy job already handles connectors, gateways, incremental copy and retries, so Fabric Desktop by KratosBI does not build its own extractors. Bronze reads new files only (1.4). Depends on D2, 1.4.

**4.2 Late rows and source deletes.** Effort M. A watermark lookback window for late-arriving rows (safe because 1.1's MERGE is idempotent per key) and an optional periodic key snapshot that marks deleted keys. Depends on 4.1.

**4.3 PostgreSQL and REST.** After 4.1 works on a tenant; same flow, different connection type.

## Delivery sequence

1. Merged: **1.1** (#56), **9.3** (#58), **1.2** (#61), **1.3** engine (#69), **7.1** (#64), **7.2** (#66), **1.4** (#70), **8.1** (#71), **5.1** engine (#73), **5.2** engine (#75), **8.3** engine (#76). Live use of 7.1 and 7.2 waits for FD-2 (conditional requests on OneLake).
2. **1.5** (merged in #87).
3. **4.1** once D2 is answered.
4. **5.3** rollback, **8.2**, **4.2**, **4.3**, **7.3**.

Large tables come first because every later stage runs through the same write path. The pipeline contract comes before Git because the Workload management program's queue and location views read its records. Git comes after the receipt schema so promotions and proofs share one record.

## Decisions needed

- **D1.** Should promotion to Test and Prod require a committed, pushed revision (recommended), or only warn? 5.1 ships with a warning and a per-environment `require_commit` setting; the answer decides the default.
- **D2.** Who creates landing-zone writers (Copy jobs into LH_Landing): only the central platform team (current assumption, matching [platform.md](platform.md)), or domain teams through a request the central team approves? Blocks 4.1.

## Dependencies outside this plan

- Concurrent writes from the desktop app and Fabric runs are made safe by version-checked commits in [PR #54](https://github.com/KratosDataGod/fabric-desktop/pull/54) (merged). #56 and #61 now carry it. The rewrite fallback commits against the version it read. A dimension MERGE loads the table at the version it read and also reads rows from the first new surrogate key up, so another writer's new keys make it conflict and run again, instead of handing out a key twice (found by a test while merging #54).
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
| 2026-10-17 | #73, #75 and #76 merged. Stage 1.5 built: full loads in batches (`[load] batch_by`), one commit. Git operations, Source Control screens and branch and pull request work (5.3's Git part) moved to the Git-native development program; promote rollback stays here. |
| 2026-10-17 | #87 (stage 1.5) merged. Release records read Git through the Git-native program's layer (`git.repo.Repository`, #85) instead of their own `git` calls; nothing a person sees changes. |
| 2026-10-16 | Stage 8.3 engine part built: column lineage from configuration. |
| 2026-10-16 | Stage 5.2 engine part built: meaningful entity and notebook diffs, changes in promote impact. |
| 2026-10-16 | #56, #58, #61, #64, #66, #69, #70 and #71 merged; stage 8.1 built ([PR #71](https://github.com/KratosDataGod/fabric-desktop/pull/71)). |
| 2026-10-16 | Stage 5.1 engine part built: Git status, release records in `meta.deployments`, `require_commit`. |
| 2026-10-16 | Stage 1.4 built ([PR #70](https://github.com/KratosDataGod/fabric-desktop/pull/70)). |
| 2026-10-16 | Stage 1.3 engine part built ([PR #69](https://github.com/KratosDataGod/fabric-desktop/pull/69)). #54 merged into #56 and #61; fixed a surrogate-key race in #61's dimension MERGE. |
| 2026-10-16 | Stages 7.1 and 7.2 built ([PR #64](https://github.com/KratosDataGod/fabric-desktop/pull/64), [PR #66](https://github.com/KratosDataGod/fabric-desktop/pull/66)). |
| 2026-10-16 | Stage 1.2 built and measured ([PR #61](https://github.com/KratosDataGod/fabric-desktop/pull/61)). |
| 2026-10-16 | Capability 9 and the agent, queue and release views moved to the Workload management program; 9.3 built ([PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)). |
| 2026-10-16 | Plan written; capabilities 2, 3, 6, 10 assigned to the notebook program; stage 1.1 built and measured ([PR #56](https://github.com/KratosDataGod/fabric-desktop/pull/56)). |
