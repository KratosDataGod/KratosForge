# Remediation backlog

The audit's findings, highest impact first. Ids are `EA-nnn`. The audit lead owns this list, and the fixes go to the threads that already own each area. Where an item repeats an earlier finding, it cites the existing id rather than restating it: `HD-` ids are in the [hardening register](../hardening/findings.md), `QA-` ids in the [quality register](../quality/findings.md).

Security items with exploit detail are not listed here. They are tracked privately with the security owner, and this page only counts them (see [Security and data integrity](SECURITY_AND_DATA_INTEGRITY.md)).

**Priorities:**
- **P0:** a confirmed critical security, data-loss or release-blocking defect.
- **P1:** a high-impact weakness.
- **P2:** an important gap.
- **P3:** a lower-impact improvement.

**Status:**
- **DEFECT:** reproduced.
- **RISK:** a credible failure mode, not reproduced.
- **GAP:** a missing capability.
- **UNVERIFIED:** couldn't be established here.

Baseline: `main` at `b75dcf5`, 2026-11-02.

## Top ten

### EA-001: Appends quietly convert values to fit the table's column types (P1, DEFECT, confirmed)

- **Category:** data integrity.
- **Evidence:** appending `qty = 2.75` to an `int` column and `2026-01-02 23:30` to a `date` column through `lakehouse.write_table(..., mode="append")` reports `Wrote 1 rows to dbo.orders (Delta version 1)`, and the table then holds `qty = 2` and `2026-01-02`. delta-rs on its own does the same: 19.99 becomes 19, `'12'` becomes 12, `True` becomes 1, and a timestamp becomes a date.
- **Where:** `sidecar/fabric_desktop/lakehouse.py`, `write_table` and `_delta_safe`; `sidecar/fabric_desktop/publish.py`, where `write_deltalake` is called. The same path serves notebook code, wizard-generated code, the SQL page's Save as table in append mode, and job runs.
- **Reproduce:** write a local Delta table with an `int` column, append a float, then read the table back.
- **Root cause:** for an append, `schema_mode` is `None`, and delta-rs casts to the table's types. The type check that HD-040 added (`spark_local._check_types`) runs only for `df.write`.
- **Impact:** numbers and dates in a lakehouse table change without any error.
- **Fix:** run the kind check in `write_table` and `publish` for every append, and for writes with `schema_mode` `None` or `merge`, against the table's current schema. Refuse a narrowing or a change of kind. `localdata.delta.fits` already has the rule.
- **Acceptance:** each conversion above stops with a clear message and leaves the Delta version unchanged. Widening of the same kind (`int` into `bigint`) still writes.
- **Tests:** append float to int, timestamp to date, and string to int through `write_table` and `publish`.
- **Owner:** Local TPC-H (OneLake write path).
- **Complexity:** S to M.
- **Regression risk:** medium. Some appends that "worked" will now stop, and the release note must say so.

### EA-002: Notebook Parquet and CSV file writes ignored the save mode (P1, DEFECT, confirmed, fix in review)

- **Category:** data integrity and interoperability.
- **Evidence:** `df.write.parquet(p)`, then `df2.write.mode("append").parquet(p)`, then reading `p` back gives only `df2`'s rows. The default mode and `ignore` also overwrote a file that was already there.
- **Where:** `sidecar/fabric_desktop/spark_local.py`, `LakehouseWriter.save`.
- **Root cause:** `save()` called DuckDB's `write_parquet` and `write_csv` without reading the mode.
- **Fix:** follow Spark's save modes for files. Error refuses a path that exists; ignore leaves it with a note; overwrite replaces it; append is refused, because one file can't take more rows, and the message points to `saveAsTable`.
- **Tests:** four new cases in `tests/test_spark_writer.py`.
- **Owner:** this audit (first remediation PR).
- **Complexity:** S.
- **Regression risk:** low. A notebook that wrote the same file twice without `mode("overwrite")` now stops, as it would in Spark.

### EA-003: Float-to-integer casts round where Spark truncates (P1, DEFECT, confirmed)

- **Category:** interoperability, wrong values.
- **Evidence:** `spark.sql("select cast(3.7 as int), cast(-3.7 as int)")` gives `4, -4`, where Spark gives `3, -3`. `CAST('1.9' AS INT)` and `F.col(x).cast("int")` behave the same way.
- **Where:** the Spark SQL translation (`sql.py`) and the Column cast patch (`spark_compat.py`). DuckDB rounds.
- **Fix:** when casting from a float or decimal to an integer type, translate to `CAST(TRUNC(x) AS <int>)`.
- **Tests:** a cast matrix through both SQL and DataFrame paths, plus a T-SQL contract case.
- **Owner:** Fabric and Databricks parity.
- **Complexity:** S.
- **Regression risk:** low.

### EA-004: Aggregates and decimal arithmetic write different Delta types than Spark (P1, DEFECT, confirmed for `sum`)

- **Category:** interoperability and data integrity (schema drift).
- **Evidence:** `groupBy(k).agg(F.sum(n))` on an `int` column, then `saveAsTable`, writes the column as `Decimal128(38, 0)`, where Spark writes `bigint`. The audit's script also showed:
  - `decimal(10,2) / decimal(10,2)` returns `double`, which is written as `Float64`;
  - decimal multiplication uses DuckDB's precision, not Spark's.
- **Where:** `spark_local._arrow` and the write path. DuckDB's `HUGEINT` and decimal rules.
- **Impact:** a table created on this PC has a different schema from the one Fabric would create, and later writes from Fabric then mismatch.
- **Fix:** cast `HUGEINT` to `int64` before writing (and refuse a value that overflows). Apply Spark's decimal result types in the translation.
- **Tests:** schema assertions after `sum` and after decimal division and multiplication.
- **Owner:** Fabric and Databricks parity, with Local data engine.
- **Complexity:** M.
- **Regression risk:** medium.

### EA-005: An overwrite through `publish` keeps rows another writer appended during the upload (P2, DEFECT, confirmed)

- **Category:** data integrity, concurrency.
- **Evidence:** while `publish(mode="overwrite")` with `[10, 11]` is uploading, another process appends `99`. The table history then reads Overwrite, Append, Overwrite, the table ends as `[10, 11, 99]`, and the overwrite is reported as a success.
- **Where:** `sidecar/fabric_desktop/publish.py`, the overwrite branch.
- **Root cause:** a plain delta-rs overwrite removes only the files in its own snapshot. KratosForge's lease holds off its own writers, not Fabric Spark or pipelines. `meta/store.py` already passes `predicate="true"`, which makes the conflicting commit fail.
- **Fix:** pass `predicate="true"` on overwrites in `publish`, and treat `CommitFailedError` as a write to try again.
- **Tests:** a concurrent append during an overwrite raises a conflict, or ends with only the overwrite's rows.
- **Owner:** Local TPC-H.
- **Complexity:** S.
- **Regression risk:** low.

### EA-006: A lease lost after the commit is reported as a failure, so a rerun appends twice (P2, DEFECT, confirmed by audit script)

- **Category:** data integrity, idempotency.
- **Evidence:** renewals fail during a 1.5-second upload with a 0.9-second lease. The append is reported FAILED with `LeaseLost`, but the table holds the row. A rerun then gives `[1, 2, 2]`.
- **Where:** `sidecar/fabric_desktop/leases.py`, where `hold()` raises after the block. This is the post-commit half of an earlier finding; HD-016 fixed the check before the write.
- **Fix:** when the commit succeeded, return the result with a warning instead of raising.
- **Tests:** renewals failing mid-write end as "committed with a warning", with one row.
- **Owner:** Local TPC-H.
- **Complexity:** S.
- **Regression risk:** low.

### EA-007: A failed file upload empties the file it was replacing (P2, DEFECT, confirmed by audit script against a fake ADLS)

- **Category:** data integrity.
- **Evidence:** the create step (`PUT ?resource=file`) truncates the existing file before any data is sent. When the append then fails, readers see an empty file, although the code comment says no half file is seen.
- **Where:** `sidecar/fabric_desktop/onelake.py`, `FileStore.upload`. It affects `notebookutils.fs.put(overwrite=True)`, `cp` onto an existing file, and bundle uploads.
- **Fix:** upload to a temporary name in the same folder, flush, then rename over the target.
- **Tests:** with a fake DFS, a failed append leaves the original bytes in place.
- **Live behaviour:** the truncation on create is what the existing OneLake contract test describes; it is not confirmed on a tenant (**UNVERIFIED**).
- **Owner:** Local TPC-H.
- **Complexity:** M.
- **Regression risk:** low.

### EA-008: A killed scheduled run on this PC blocks that schedule for 12 hours and leaves no record (P2, DEFECT, confirmed by code reading and audit script)

- **Category:** reliability.
- **Evidence:** a run killed with exit code 137 leaves the lock file behind (holding its PID, which is no longer alive) and no run row. The next run is logged "skipped: The previous run of this schedule was still going".
- **Where:** `sidecar/fabric_desktop/schedule_service.py`: `_lock` (`STALE_LOCK_SECONDS = 12 * 3600`) and `run_schedule`, which writes the run row only at the end.
- **Fix:**
  - If the lock's PID isn't alive, take over the lock.
  - Write a "running" row at the start, and turn leftover ones into "interrupted", as `orchestration/runs.py` does for pipeline runs.
- **Tests:** kill a child mid-run, then assert the next run starts and the history shows the earlier run as interrupted.
- **Owner:** the schedules owner (to be routed).
- **Complexity:** S.
- **Regression risk:** low.

### EA-009: Builds are unsigned and nothing has run against a live tenant (P1, GAP, release-critical, blocked)

- **Category:** security and supply chain; testing.
- **Evidence:** the signing steps in `ci.yml` run only when `vars.TRUSTED_SIGNING_ACCOUNT` is set; without it, an unsigned build accepts any update whose checksum matches the GitHub release. The FD-101 live harness has never been run ([readiness checklist](../hardening/readiness-checklist.md), gates 3 and 4).
- **Impact:** App Control, Smart App Control and EDR will block or flag the app. No Fabric integration has live evidence.
- **Fix:** finish the Trusted Signing identity check. Then run the harness once on the non-production test workspace and record the result.
- **Owner:** the maintainer (Chris). Both steps need him.
- **Complexity:** S, once unblocked.

### EA-010: The engine log is wiped by the restart that follows a crash (P2, GAP, confirmed by code reading)

- **Category:** reliability, supportability.
- **Evidence:** `app/src-tauri/src/main.rs`, `Sidecar::start`, opens `sidecar.log` with `File::create`, which truncates it. The engine is started again on the next call after a crash, so the "Full log" path and the support bundle show the new engine's log, not the crash.
- **Fix:** before each start, rename the old log to `sidecar.prev.log` (or append with a size cap), and include both logs in the support bundle.
- **Tests:** a Rust unit test that two starts keep the earlier contents; a support bundle test.
- **Owner:** Enterprise readiness (support diagnostics).
- **Complexity:** S.
- **Regression risk:** low.

## Further findings

| Id | Pri | Status | Finding | Where | Proposed owner |
| --- | --- | --- | --- | --- | --- |
| EA-011 | P2 | RISK (audit script) | One failed heartbeat (a SQLite "database is locked" error) silently ends the heartbeat thread. The job can then be claimed again, and the old run's finish marks the new attempt done (no fencing token) | `agent.py` `_heartbeat`, `jobqueue.py` `_finish` | Workload management |
| EA-012 | P2 | DEFECT (audit script) | DataFrame division by zero gives `inf`; the SQL path correctly gives null | `spark_compat` Column division | Parity |
| EA-013 | P2 | DEFECT (audit script) | The CSV reader drops options other than `header` and `sep` (`inferSchema=false`, `nullValue`, `mode`) instead of honouring or refusing them | `spark_local.py` reader | Parity |
| EA-014 | P2 | DEFECT (audit script) | `CAST` between `TIMESTAMP` and `BIGINT` silently gives null | translation, `duckdb_rows.keep_try_cast` | Parity |
| EA-015 | P2 | DEFECT (audit script) | A bad string-to-date cast errors in the DataFrame API; Spark (ANSI off) and the SQL path give null | `spark_compat` cast, `to_date` | Parity |
| EA-016 | P2 | DEFECT (audit script) | Cancel interrupts the session's DuckDB and the default connection, but not a `duckdb.connect()` the user opened, or Polars. engines.md says "Native" | `spark_local.py` cancel, `docs/engines.md` | Parity, then docs |
| EA-017 | P2 | DEFECT (audit script) | `hash()` returns a 64-bit DuckDB hash, where Spark returns a 32-bit Murmur3, so values differ silently | translation | Parity |
| EA-018 | P3 | DEFECT | CONTRIBUTING.md says "Python 3.11 or later"; `pyproject.toml` requires 3.12 | `CONTRIBUTING.md` | This audit (docs PR) |
| EA-019 | P3 | DEFECT, fixed in review | `test_fabric_environment` failed whenever the test's Python had `rich` installed | `tests/test_fabric_environment.py` | This audit |
| EA-020 | P2 | GAP | Supply chain hardening: check the embedded Python with SHA-256 instead of MD5; install engine wheels with `--require-hashes`; gate the Preview `latest` publish with an environment; the legacy non-bundled engine update installs a commit-pinned source archive over HTTPS, without checking a content hash (SHA-256); planned fix: pin a SHA-256 for the archive | `ci.yml`, `updater.py` | Release management (workflow changes need maintainer review) |
| EA-021 | P3 | RISK | `sidecar.log` and `schedule_runs.jsonl` have no size bound, and the support bundle reads a whole file to keep its last 2,000 lines | `main.rs`, `local_schedules.py`, `support_bundle.py` | Enterprise readiness |
| EA-022 | P3 | RISK, power loss UNVERIFIED | Notebook saves and the token cache are replaced without `fsync`. Only `jsonfile.write_text` and receipts flush | `notebook.py` `_write_atomic`, `auth.py` | Codebase audit |
| EA-023 | P3 | GAP | A corrupt SQLite file (job queue) stops the agent from starting, and the service manager restarts it in a loop; JSON files are set aside instead | `jobqueue.py` | Workload management |
| EA-024 | P3 | GAP, UNVERIFIED (Windows) | Automatic update rollback covers only a start that fails before the window loads; `exe.old` is deleted once the UI calls `check_components` | `main.rs` | Release management |
| EA-025 | P3 | RISK | Lease expiry compares wall clocks across PCs, so clock skew can let another PC take a live lease. Delta's version check still applies | `leases.py` | Local TPC-H |
| EA-026 | n/a | Not a defect (withdrawn) | The audit reported that the quick-call lists in `watchdog.rs` and `server.py` had no matching test. They do: `tests/test_server_lanes.py::test_quick_calls_match_the_shell_time_limit_list` parses `QUICK` from `watchdog.rs`, asserts it equals `server.QUICK`, and checks `QUICK` is a subset of `METHODS` | `tests/test_server_lanes.py` | Checked by WS6 and the audit lead |
| EA-027 | P3 | GAP | Ruff runs only E9 and F. Proposal: add `B` (except the reviewed false positives such as B023) and `BLE`, a few rules at a time, with the existing silent-`except` gate | `pyproject.toml` | Codebase audit |
| EA-028 | P2 | GAP | Shared plumbing is still copied: 9 atomic-write implementations, 7 SQLite connection sites with no corruption handling, about 8 hand-written retry loops, 5 redactors. Characterize, then move callers to the shared helpers one at a time | many | Codebase audit |
| EA-029 | P2 | GAP | No benchmarks for app start, notebook start or SQL latency, and no performance check in CI | `examples/benchmark` | Local data engine |
| EA-030 | P3 | GAP | Narrator and keyboard accessibility pass on Windows (PB-063 part 2) | `docs/ux` | UX |

Four further P3 security hardening items are tracked privately and have been routed to their owners. None is P0 or P1.

## Shared root causes

- **The engine trusts DuckDB's and delta-rs's defaults** where Spark's semantics differ: casts, decimal types, appends that convert values, and overwrites without a predicate. The pattern that works is already in the code: refuse or translate explicitly, as the writer does for options (`_REFUSED`) and `meta/store.py` does for overwrites. EA-001, EA-003, EA-004, EA-005 and EA-012 to EA-017 share this cause.
- **Run state is written only when a run ends.** EA-008 and EA-011 come from this; `orchestration/runs.py` already shows the start-row-then-interrupted pattern.
- **Diagnostics are overwritten or unbounded.** EA-010 and EA-021.

## Progress

As of 2026-11-02 07:00 UTC. "Open PR" means the PR is up and in review; a draft is still being written.

| Id | State | PR |
| --- | --- | --- |
| EA-001 | Part merged (notebook and job appends); metadata runs and published notebooks open | #1576 merged, #1600 open |
| EA-002, EA-019 | Open PR | #1572 |
| EA-003 | Merged | #1579. Related local T-SQL casts: #1586 merged; #1588, #1591, #1598, #1599 open |
| EA-004 | Draft, part 1 (sums as `bigint`) | #1595. Decimal result types still to do |
| EA-005 | Open PR | #1582 |
| EA-006 | Open PRs | #1585, #1605 |
| EA-007 | Open PRs | #1594, #1604 |
| EA-008 | Merged; follow-up open | #1574 merged, #1617 open |
| EA-009 | Blocked on the maintainer (signing identity check, live harness run) | |
| EA-010 | Open PR | #1575 |
| EA-011 | Open PR | #1578 |
| EA-012 | Open PR | #1601 |
| EA-013 | Draft | #1626 |
| EA-014 | Draft | #1609 |
| EA-015 | Draft | #1607 |
| EA-016 | Draft | #1624 |
| EA-017 | Open PR | #1625 |
| EA-018 | Done | #1571 |
| EA-020 | Part merged (embedded Python SHA-256, Preview environment) | #1573 merged. #1637 (legacy archive hash) open, Quality passed. Hash-locked engine wheels are queued |
| EA-021 | Part merged (schedule log bound, log tails); engine log cap open | #1581 merged; #1584 open, #1589 draft |
| EA-022 | Part merged (notebook saves, sign-in cache flushed) | #1590, #1593 merged; #1592 open |
| EA-023 | Draft | #1580 |
| EA-024 | Draft | #1596 |
| EA-025 | Open PRs | #1613, #1614, #1615 |
| EA-026 | Withdrawn: already covered by `test_server_lanes.py` | |
| EA-027 | Open PR (first slice) | #1597 |
| EA-028 | Open PRs (callers moved one at a time) | #1602, #1606, #1627, #1630 |
| EA-029 | Open PR | #1608 |
| EA-030 | Parked: needs a Windows PC | |

The scorecard stays at 54 until EA-001, EA-002 and EA-004 are all merged; see the trend table in [Enterprise readiness](ENTERPRISE_READINESS.md).

The private security items: three are fixed, and one is waiting on a step from the maintainer.
