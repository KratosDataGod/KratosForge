# Enterprise readiness scorecard

The enterprise engineering audit's scorecard: ten categories, each scored 0 to 5 from evidence, weighted to a score out of 100. It sits on top of the existing readiness material and doesn't replace it:

- [Production readiness checklist](../hardening/readiness-checklist.md): the release gates and their state.
- [Release readiness](../product/release-readiness.md): what an enterprise IT team would ask for.
- [Engineering quality baseline](../quality/README.md): the per-PR quality gate.

The other audit pages are the [executive summary](EXECUTIVE_SUMMARY.md), [security and data integrity](SECURITY_AND_DATA_INTEGRITY.md), [engine compatibility](ENGINE_COMPATIBILITY.md), [quality gates](QUALITY_GATES.md), the [remediation backlog](REMEDIATION_BACKLOG.md) and [architecture decisions](ARCHITECTURE_DECISIONS.md).

## Baseline

| | |
| --- | --- |
| Commit | `main` at `b75dcf5` (2026-11-02) |
| Version | 0.20.0 (`sidecar/pyproject.toml`) |
| Stable build | `3633b4a`. Promoting a newer build needs the maintainer's approval in Actions |
| Code | Python engine: 359 modules, about 111,000 lines (`sidecar/fabric_desktop`, vendored code not counted). UI: about 35,000 lines of plain JavaScript (`app/ui`). Tauri shell: about 5,500 lines of Rust (`app/src-tauri/src`) |
| Open PRs | 17. Five are typing work from the mypy ratchet, three are long-held forks |

## Checks run for this baseline

All of these ran on Linux with Python 3.12.3. CI uses 3.12.10 on Windows.

| Check | Command | Result |
| --- | --- | --- |
| Engine tests | `python -m pytest -q -n auto --dist loadfile` | 8,656 passed, 85 skipped, 1 failed. The failure was a test that depended on which packages the test's Python had installed. It is fixed in the first remediation PR ([REMEDIATION_BACKLOG](REMEDIATION_BACKLOG.md), EA-019) |
| UI unit tests | `node --test app/tests/ui.test.cjs` | 315 passed |
| Lint | `ruff check .` (rules E9, F) | Passed |
| Wider lint, advisory only | `ruff check --select B,S,BLE fabric_desktop` | 235 hits; see Architecture below |
| Types | `.github/scripts/mypy_ratchet.py` | No new errors: 513 now, against a baseline of 525 |
| Python advisories | `pip-audit -r sidecar/constraints.txt` | No known vulnerabilities |
| Browser tests, Rust tests, Windows build, cargo audit | Not run here; they need Windows or GTK | The CI run for `aed09ee2` passed all of them. **UNVERIFIED locally** |
| Live Fabric tenant | None | **UNVERIFIED.** No harness run has been recorded |

The four focused reviews (security, data integrity, engine parity, reliability) reproduced each defect with a local script. The scripts are kept with the project's private audit files, not in the repository, because some of them exercise security paths. Defects marked *confirmed* below were re-run by the audit lead.

## Scorecard

Each category is scored on the brief's scale: 0 none, 1 experimental, 2 functional but materially incomplete, 3 reliable for bounded production use, 4 enterprise-ready with verified controls, 5 mature and independently validated.

| Category | Weight | Score | Weighted | Target |
| --- | --- | --- | --- | --- |
| Security and supply chain | 20% | 3.0 | 12.0 | 4.5 |
| Data integrity | 15% | 2.5 | 7.5 | 4.5 |
| Reliability | 15% | 3.0 | 9.0 | 4.0 |
| Architecture | 10% | 2.5 | 5.0 | 3.5 |
| Testing | 10% | 3.0 | 6.0 | 4.0 |
| Enterprise deployment | 10% | 2.0 | 4.0 | 4.0 |
| Performance | 5% | 2.0 | 2.0 | 3.5 |
| Interoperability | 5% | 2.5 | 2.5 | 3.5 |
| UX and accessibility | 5% | 3.0 | 3.0 | 4.0 |
| Documentation and support | 5% | 3.0 | 3.0 | 4.0 |
| **Total** | 100% | | **54 / 100** | about 82 at these targets; 90 is the long-term aim |

**Verdict: not enterprise-ready.** Two release-critical requirements are still unverified: signed builds, and a live tenant run of the integration harness. On top of that, one confirmed data-integrity defect is open: appends quietly convert values to fit the table's column types (EA-001). No category scores 4 while those stay open.

## Evidence per category

### Security and supply chain (20%): 3.0

The controls verified on current code, with their tests:

- **Local API.** Only programs on this PC can call it, and only from the same Windows account. It checks the Host header, refuses requests that carry an `Origin`, requires a bearer token stored as a hash, limits the body size and rate-limits callers (`integrations/http_server.py`). Tests: `test_integrations_http.py`, `test_integrations_peer.py`.
- **Engine calls.** The engine runs only methods on its allowlist (`server.py`).
- **Execute SQL.** It accepts one read-only statement. External access is then turned off and the settings locked (`sql.py`).
- **Approvals.** Running code always asks. An approval is tied to a hash of the exact arguments (`integrations/registry.py`).
- **Deep links.** Paths must be contained, and reparse points are refused (`deep_link.rs`, `test_notebook_files.py`).
- **Shell.** A strict CSP is set (`tauri.conf.json`).
- **Token cache.** Encrypted with DPAPI (`test_token_cache_dpapi.py`).
- **Updates.** Downgrades are refused (`update_guard.rs`).
- **CI supply chain.** Every action is pinned by SHA, the top-level token is `contents: read`, and the `pull_request_target` job never checks out PR code. `pip-audit` and `cargo audit` both block the build, and SBOMs and build-provenance attestations are made (`.github/workflows/ci.yml`).

Why the score isn't higher:

- **Builds aren't signed yet**, so update integrity rests on GitHub (SH-2, blocked on Trusted Signing).
- The embedded Python is checked by MD5.
- Engine wheels are pinned but not hash-locked.
- The Preview `latest` publish has no environment gate.
- Four lower-severity hardening items are tracked privately with the security owner.

No P0 or P1 security defect was found on the inbound surfaces.

### Data integrity (15%): 2.5

Well covered, with tests:

- Handling of a failure after the commit, and idempotent appends after the commit (`test_write_failures.py`).
- The Delta profile check (`test_delta_profile_writes.py`).
- Refusing writer options the engine can't honour, and the type rules for `df.write` (`test_spark_writer.py`).
- Write leases (`test_leases_local.py`, `test_lease_before_commit.py`).
- Dry runs that change nothing (`test_local_delta.py`).

The earlier High items (DI-1, DI-2, DI-4, QA-001, QA-033, HD-040) are still fixed.

Against that, there are four confirmed defects (EA-001, EA-005, EA-006, EA-007):

- An append through `lakehouse.write_table` or `publish` converts values to the table's types without saying so. For example, 2.75 is written as 2, and a timestamp loses its time.
- An overwrite can keep rows that another writer appended during the upload.
- A lease lost after the commit is reported as a failure, so a rerun appends the data twice.
- A failed file upload empties the existing file.

Nothing has been verified against OneLake itself (**UNVERIFIED**).

### Reliability (15%): 3.0

Verified and working:

- **Engine watchdog.** A crash mid-call returns an error, and a stuck quick call is ended (`watchdog.rs` tests).
- **Request lanes.** `test_server_lanes.py`.
- **Durable job queue.** WAL, full sync, leases and a dead state (`test_agent.py`).
- **Bounded retry.** It honours `Retry-After` and never retries a write on a 5xx (`test_retrying.py`, `test_retry_after.py`).
- **Atomic JSON writes.** A torn file is set aside (`test_json_files.py`).
- **Pipeline runs.** Runs left going are marked interrupted.
- **Memory and disk caps.** Set on DuckDB.

Confirmed gaps:

- A killed scheduled run on this PC blocks that schedule for 12 hours and leaves no record (EA-008).
- The engine log is truncated by the restart that follows a crash (EA-010).
- A failed heartbeat can let the agent run a job twice (EA-011).

Update rollback only covers a start that fails before the window loads (**UNVERIFIED** on Windows).

### Architecture (10%): 2.5

- **Size.** One `Service` class carries 637 engine methods across 62 mixins. 15 modules are over 1,000 lines, led by `central/core.py` (3,267) and `notebook.py` (2,858), and `main.rs` is 2,715 lines.
- **Duplicated plumbing.** Shared helpers exist for each of these, but local copies remain:
  - 9 atomic-write implementations, and only the shared one calls `fsync`;
  - 7 SQLite connection sites, none handling a corrupt file;
  - about 8 hand-written retry loops;
  - 5 redactors.
- **Lint.** Ruff runs only E9 and F. An advisory run of B, S and BLE on the engine found 235 hits, mostly reviewed `except` handlers (92) and `zip` without `strict` (45). The 23 B023 hits are false positives: closures that are called inside the loop.
- **Types.** The mypy ratchet is moving down (525 to 513).

### Testing (10%): 3.0

CI runs, on every PR, 8,700 engine tests, 315 UI tests, browser tests in Chromium (axe accessibility among them) and Rust tests. There are failure-injection tests for writes and leases, T-SQL contract cases, and a silent-`except` gate. Coverage isn't measured.

No integration test runs against a live tenant: every Fabric test uses fakes. By the brief's own rule, Fabric integration can't be called production-ready on that evidence. Known flaky tests are tracked in a ledger rather than skipped.

### Enterprise deployment (10%): 2.0

What works today:

- It installs per user, with no administrator rights.
- Sign-in uses WAM, which passes Conditional Access device checks.
- HTTPS uses the Windows certificate store, so TLS-inspecting proxies work (`net_trust.py`).
- IT can supply a policy file, pin a build, and host the engine itself ([enterprise install](../enterprise-install.md)).

What's missing:

- The builds are unsigned.
- There is no MSI or MSIX package for Intune or Configuration Manager.
- The policy file is advisory against a determined local user.
- Privacy statement and support terms aren't published yet.

### Performance (5%): 2.0

There are reproducible benchmark scripts with recorded results for large-table metadata runs and batched loads (`examples/benchmark/`, [Large tables](../metadata-guide.md#large-tables)). There are also motion benchmarks for the UI.

Missing:

- No benchmark exists for app start, notebook start or SQL latency.
- No performance check runs in CI.
- No local-versus-Fabric comparison has been recorded under documented, comparable conditions.

Capacity-unit savings are not claimed as measured anywhere in this audit.

### Interoperability (5%): 2.5

Writing tables is carefully guarded: options the engine can't honour are refused, and so are dynamic partition overwrites. Null and NaN ordering, string functions and session time zones match Spark.

Confirmed silent differences:

- Float-to-integer casts round where Spark truncates.
- `sum` of integers is written to Delta as `decimal(38,0)`.
- Decimal division returns a double.
- File writes used to ignore the save mode; this is fixed by the first remediation PR.

See [Engine compatibility](ENGINE_COMPATIBILITY.md).

### UX and accessibility (5%): 3.0

The automated accessibility audit (axe-core, WCAG 2.1 AA, light and dark) reports 0 serious or critical violations on every page it opens ([accessibility audit](../ux/accessibility-audit.md)). Destructive actions use a danger confirmation, and animations have an Off setting.

The Narrator and keyboard pass on Windows hasn't been done (**UNVERIFIED**). The last simplicity score was 64/100 (2026-10-18, [UX scorecard](../ux/scorecard.md)).

### Documentation and support (5%): 3.0

What exists: user, admin, security, permissions, troubleshooting and engine documentation; a support bundle that runs through redaction; and a security policy with private reporting.

Defects found:

- CONTRIBUTING.md says Python 3.11 or later, but the engine requires 3.12 (EA-018).
- engines.md calls cancel "Native", but cancel doesn't reach DuckDB connections the user opens, or Polars (EA-016).
- Privacy statement and support terms aren't published.

## How this page is kept

At the start of each audit cycle:

1. Refresh `main`.
2. Re-run the checks above.
3. Update the scores whose evidence changed.
4. Add a row to the trend table below.

A score only goes up with new evidence: a merged fix with its test, or a recorded live run.

| Date | Commit | Score | Change |
| --- | --- | --- | --- |
| 2026-11-02 | `b75dcf5` | 54 | Baseline |
