# Local data engine and OneLake publishing: audit and plan

The program FD-LDE-001 to FD-LDE-020 makes KratosForge a local-first SQL development environment: register a folder of data files, query them as tables on this PC without starting a Fabric job, build persistent transformed datasets and local Delta tables, validate them, and publish them to OneLake through a verified mechanism. A run on this PC starts no Fabric job, but its OneLake reads and writes, including publishing, still use capacity. It takes ideas from [DaffyDB](https://www.nuget.org/packages/DaffyDB/) (a folder of files served as SQL tables over TDS) without making it a dependency.

Status: audit done (main at `7f51026`, 2026-10-17); the first PR carries this plan together with local projects, the engine and file discovery. Everything here is Preview until it has run on a real tenant.

## 1. Repository audit

### Architecture

| Layer | What it is | Notes for this program |
| --- | --- | --- |
| Desktop shell | Tauri 2 (Rust), `app/src-tauri` | Starts the engine and relays JSON lines. No SQL in Rust. |
| UI | Plain HTML and JavaScript, no build step, `app/ui` | Pages register in `features.js`; the rail is full (8 items), so new screens go inside existing pages. CodeMirror is vendored for editors. |
| Engine | Python 3.12 sidecar, `sidecar/fabric_desktop` | JSON-lines RPC (`server.py`), one request at a time, so long work uses start, poll and cancel calls. `Service` is built from per-feature mixins (`*_service.py`). |
| Compute | DuckDB 1.5.6, Polars, PyArrow, sqlglot, delta-rs (`deltalake` 1.6.6) | Pinned in `sidecar/constraints.txt`; moving or adding a package is its own PR (CONTRIBUTING). DuckDB extensions that download at runtime (excel, delta, ICU) are avoided: they fail offline and bypass the pinned supply chain. |
| Storage | OneLake through the DFS (ADLS Gen2) API and delta-rs `abfss://` | `onelake.py` (`FileStore`, `TableRef`, `TableCatalog`), `publish.py`, `meta/store.py` (`LocalStore`, `OneLakeStore`). |
| Sign-in | MSAL with Microsoft's Fabric CLI public client and WAM (`auth.py`, `credentials.py`) | No app registration. Tokens stay in MSAL's encrypted cache. |
| External tools | MCP server and loopback HTTP API (`integrations/`) | Capability registry, per-client permissions, approvals, audit. A read-only SQL capability already exists (`sql.run(sandbox=True)`). |
| Audit | `audit.py`, hash-chained JSONL | Every call that runs or changes something is recorded; query text is never recorded. |

Baseline: `python -m pytest -q` in `sidecar/` on Linux, Python 3.13, with the pinned constraints: **961 passed, 2 skipped** before any change. CI also runs the UI syntax tests, a packaged-install import check, `pip-audit` and the Windows build.

### What already exists

| Area | Where | What it does |
| --- | --- | --- |
| SQL over lakehouse tables | `sql.py`, `Service.run_sql`, SQL page in `app.js` | Spark SQL (DuckDB SQL accepted) translated by sqlglot, tables read from OneLake as Delta datasets; sandboxed read-only mode for external tools; save result as a table; CSV export. Needs an attached lakehouse and a sign-in. |
| SQL editor | Draft PR #118 (SQL-001) | CodeMirror editor with completion and formatting for the SQL page. |
| Fabric SQL dialect analyzer | Draft PR #120 (SQL-002) | Versioned profiles and rules (`sqlcompat/`), compatibility report, 193 regression cases. This is most of FD-LDE-014. |
| Managed execution | SQL-003, in progress in the SQL thread | Cancel, timeouts, paging and streamed export for the SQL page. |
| Notebook reads and writes | `lakehouse.py` | `read_csv/parquet/json`, `read_table`, `write_table` (overwrite or append) to OneLake Delta, with a local staging copy checked against the Fabric-safe Delta profile. |
| Local Delta tables | `meta/store.py` `LocalStore`, metadata projects with "Tables in: This PC only" | Bronze, Silver and Gold as local Delta folders, with MERGE (`merge_insert`, `upsert`) and streaming writes. |
| Fabric-safe Delta profile | `delta_profile.py` | Reader 1, writer 2, no deletion vectors, column mapping or V2 checkpoints, so Fabric readers handle what we write. |
| OneLake Files | `onelake.py` `FileStore` | List, download, read, upload (create, append, flush), delete. Upload is a single append with no conflict check or chunking. |
| Concurrency | `leases.py`, `retrying.py`, commit retries in `meta/engine.py` | Per-table write leases in LH_Meta (conditional DFS requests, unverified on OneLake); retries on `CommitFailedError`. |
| Validation and quality | Metadata framework: gates, tests gate promote, drift review, quarantine and replay, compare before promote (`meta/`) | Built for metadata entities, not yet for an arbitrary local dataset. |
| Run history | `history.jsonl`, `meta.runs`, receipts (`receipts.py`), release records | Publish history exists per feature, not as one service. |
| Measurement | `measure.py`, `costs.py`, draft PR #117 (FINOPS-001) | Measured, estimated, scenario and unknown classification for every cost figure. |

No SQLite engine is used for data (SQLite holds the job queue, workloads and integrations state). No TDS listener, no local file catalog, no persistent local SQL database and no Excel reader exist.

### Gaps that matter for safety, found during the audit

- `publish.publish` and `lakehouse.write_table` overwrite with `schema_mode="overwrite"` and no preview; they rely on callers (the metadata gates, the SQL page's "replace?" question) for confirmation. FD-LDE-009 and 010 add a publish path with a dry run and explicit confirmation instead of reusing these as they are.
- `FileStore.upload` creates with a plain `PUT`, which replaces an existing file. FD-LDE-008 needs `If-None-Match: *` for "don't replace" and a size and MD5 check after the flush.
- delta-rs commits to OneLake rely on the store refusing a second writer of the same `_delta_log` entry. Whether OneLake honours that conditional write is not verified on a tenant (the same open question as `leases.py`). Until it is, FD-LDE-009 appends and FD-LDE-011 MERGE take the existing per-table lease and are labelled Preview.

## 2. Logical services and where they live

| Logical service | Module | State |
| --- | --- | --- |
| LocalExecutionService | `localdata/engine.py` `LocalEngine`, `localdata/manager.py` | PR 2 (built) |
| LocalDataCatalog | `localdata/projects.py` (catalog in `project.json`) + DuckDB's own catalog | PR 2 |
| LocalFileDiscoveryService | `localdata/discovery.py` | PR 2 (Excel reading in PR 3) |
| LocalSqlExecutionService | `LocalEngine.execute`; the SQL page target comes with SQL-003 | PR 2 engine, UI with the SQL thread |
| SqlDialectCompatibilityService | `sqlcompat/` from SQL-002 | Owned by the SQL thread; T-SQL translation (LDE-004) builds on its profiles |
| LocalDeltaLakeService | `localdata/engine.py` (read), `meta/store.py` `LocalStore` (write) | Read in PR 2; create, append, overwrite, time travel in PR 5 |
| OneLakeConnectionService | `auth.py`, `credentials.py`, `onelake.py`, `fabric_api.py` | Exists; destinations browser in PR 7 |
| OneLakeFileTransferService | `onelake.py` `FileStore` | Exists; chunked, resumable, verified, no-replace in PR 7 |
| OneLakeDeltaPublishService | `publish.py`, `meta/store.py` `OneLakeStore`, `leases.py` | Exists for notebooks and metadata; a guarded publish path in PR 8 |
| SchemaValidationService | `meta/drift.py`, `meta/compare.py`, `delta_profile.py` | Reused in PR 8 |
| DataQualityValidationService | metadata tests and gates (`meta/`) | Reused and generalised in PR 12 |
| PublishHistoryService | `history.jsonl`, `meta.runs`, release records, audit | One history for local publishes in PR 12 |
| ExecutionDiagnosticsService | engine timings now; `measure.py` and FINOPS-001 classification | PR 12, using FINOPS-001's classes |

Adapters: discovery turns files into SELECT statements (`file_select`, `delta_select`), so the engine can change without changing discovery; dialects go through sqlglot today and the SQL-002 profiles later; cloud publishing goes through the existing `Store` classes.

## 3. Implementation matrix

Owner "this thread" means this program builds it. Other owners are threads already working on that area; this program uses what they build and doesn't edit their branches.

| Story | Exists | Needs enhancement | Missing | Owner | PR |
| --- | --- | --- | --- | --- | --- |
| LDE-001 Local execution engine | DuckDB in-memory per query (`sql.py`) | | Persistent projects, memory and thread limits, timeouts, cancel, structured results, file lock handling | This thread | 2 (done) |
| LDE-002 File discovery | | | Folder sources, naming rules, partitions, conflicts, refresh on change, exclusions, type overrides | This thread | 2 (done); Excel in 3 |
| LDE-003 Local SQL in the editor | SQL page, editor (#118) | "Local engine" target, local schema tree, history | | SQL thread for the editor and execution service (SQL-001, SQL-003); this thread supplies the engine calls | With SQL-003 |
| LDE-004 T-SQL compatibility | sqlglot reads T-SQL | Translation on SQL-002's profiles, explicit errors for transactions, locking and isolation hints | Regression suite for types, NULLs, collation, decimals, dates | This thread, on SQL-002's profiles | 4 |
| LDE-005 Local Delta | `LocalStore` writes; PR 2 reads current versions | Create, append, overwrite from SQL; time travel; protocol checks before writes | | This thread | 5 |
| LDE-006 Local transformations | Metadata framework layers; notebooks | | SQL scripts with parameters, ordering, logs and kept intermediates | This thread; pipeline orchestration stays with the ORCH thread | 6 |
| LDE-007 OneLake connection | Sign-in, workspaces, lakehouses, catalog | Read and write permission check per destination | | This thread (reusing `auth.py`) | 7 |
| LDE-008 OneLake Files transfer | `FileStore` | Chunks, progress, cancel, retry with backoff, resume, no-replace, integrity check | | This thread | 7 |
| LDE-009 Delta publishing | `publish.py`, `OneLakeStore`, leases | Protocol checks, concurrent-change detection, snapshot check after commit | Verified commit safety on OneLake | This thread; needs a tenant test with Chris's OK | 8 |
| LDE-010 Publish validation | Drift, compare, profile check | One preview: destination, mode, schema changes, rows, bytes, dry run | | This thread | 8 |
| LDE-011 Incremental and MERGE | `merge_insert`, `upsert`, watermarks in metadata | | Watermarks and keys for a local dataset, idempotent job ids, counts | Staged roadmap thread owns FD-114/117; this thread builds the dataset-level publish on them | 9 |
| LDE-012 Local TDS endpoint | | | Everything | This thread, only after Chris's go (security-sensitive) | 10 |
| LDE-013 Power BI Desktop | | | Depends on 012 | This thread, after 012 | 11 |
| LDE-014 SQL compatibility validation | SQL-002 analyzer (#120) | Run it on local scripts before publish | | SQL thread | With SQL-002 |
| LDE-015 OneLake sampling | `read_table`, X-ray profiling | | Row and size limits, deterministic samples, masking, labels | This thread | After 9 |
| LDE-016 Data quality | Metadata tests and gates | Rules for any local dataset, enforced on publish | | This thread, reusing metadata tests; governance policies stay with the governance thread | 12 |
| LDE-017 Diagnostics | Timings | Peak memory, bytes, plans, publish duration | | This thread, with FINOPS-001 classes for any CU figure | 12 |
| LDE-018 Publish history and recovery | Run history, Delta history and restore | One publish history with versions and recovery guidance | | This thread; audit log stays with enterprise readiness | 12 |
| LDE-019 Large datasets | Spill-to-disk now; streaming in SQL-003 | Streamed export, progress, disk-space checks, benchmarks | | This thread with the SQL thread (SQL-003) | 13 |
| LDE-020 Developer tool interfaces | MCP and local API, read-only SQL capability | Local engine capabilities, scoped to registered folders | | This thread registers capabilities in the BYO-AI registry | 13 |

## 4. PR plan

Each PR is independent, tested without Fabric, and reported before the next starts.

1. and 2. **This plan, with local projects, engine and file discovery** (LDE-001, LDE-002), in one pull request. Engine calls only; no UI. *Built.*
3. **Excel workbooks** (LDE-002 remainder): add a pinned Excel reader in its own PR (candidates: `openpyxl`, MIT; or `fastexcel`, MIT, via Polars), sheets imported into the project database and re-imported when the file changes.
4. **T-SQL compatibility** (LDE-004) on SQL-002's profiles, once #120 merges.
5. **Local Delta** (LDE-005): `CREATE TABLE ... AS` to a local Delta folder through delta-rs, append and overwrite, time travel, protocol checks before every write. *Built:* `CREATE TABLE ... USING DELTA AS`, `INSERT INTO`, `INSERT OVERWRITE`, `VERSION AS OF` / `AT (VERSION => n)`, history, dry run and confirmed drop, in the Fabric-safe profile. `MERGE`, `UPDATE` and `DELETE` come with LDE-011.
6. **Transformations** (LDE-006): SQL scripts with parameters and dependency order, each step's output kept as a table for inspection.
7. **OneLake destinations and Files transfer** (LDE-007, LDE-008).
8. **Delta publish with validation** (LDE-009, LDE-010): dry run, preview, confirm, commit, check the committed snapshot.
9. **Incremental and MERGE** (LDE-011).
10. **Local TDS endpoint** (LDE-012): needs Chris's go first.
11. **Power BI Desktop** (LDE-013).
12. **Quality, diagnostics and publish history** (LDE-016, 017, 018).
13. **Large datasets and developer tool interfaces** (LDE-019, 020).

UI for the Local Data Explorer, Delta browser, destination picker, publish preview, progress and history comes from a mockup Chris approves first. The likely home is the OneLake page (a "This PC" section beside the OneLake catalog) and the SQL page's target picker, since the rail is full.

## 5. Decisions and why

- **DuckDB stays the engine.** It is already pinned, used by the SQL page and notebooks, spills to disk beyond its memory limit, and reads Parquet, CSV and JSON natively. No second engine.
- **Discovered files are views, not imports.** DuckDB reads only the columns and row groups a query needs, and a changed file is seen at the next query. Delta tables become views over the files of their current version, read from the Delta log with delta-rs, so the DuckDB `delta` extension (a runtime download) isn't needed.
- **DaffyDB's naming rules** (dotted names and folders set the schema, part-file folders are one table, Parquet beats CSV beats JSON) because people coming from it expect them; `naming = "flat"` and per-file names cover the rest. A name clash is reported, never resolved silently.
- **`dbo` is the default schema**, as in Fabric's SQL endpoints: `CREATE TABLE t` makes `dbo.t`.
- **One connection per project, one request at a time on it.** Temporary tables last the session; catalog reads use their own cursor. DuckDB's file lock stops a second process from writing the same database; that is reported in words, not retried.
- **Start, poll and cancel calls** for long queries, because the engine answers the app one request at a time.
- **No DuckDB runtime extension downloads**, so the engine works offline and everything it runs comes from the pinned, audited packages.

## 6. Known limits and open questions

- Excel files are found and listed but not readable until PR 3.
- Delta tables with deletion vectors, column mapping or other reader features that a Parquet read would get wrong are listed with the reason and not queryable locally yet.
- Partitioned Delta tables are read when their files sit in `column=value` folders (as Spark and delta-rs write them); others are reported.
- File watching is a cheap check before each query (at most every 2 seconds), not a background watcher; switch it off with `auto_refresh = false` and refresh explicitly.
- A notebook kernel can't open a project's database while the app has it open for writing; `local_close_project` releases it. Read-only sharing comes with the notebook integration.
- Whether OneLake honours delta-rs's conditional commit (needed for safe appends and MERGE from two writers) must be checked on a tenant before LDE-009 and 011 leave Preview. That test needs Chris's OK and a non-production workspace.

See [local-data.md](local-data.md) for how local data projects work.
