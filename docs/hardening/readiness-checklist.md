# Production readiness checklist

Release gates for KratosForge going to an enterprise pilot. Each gate has measurable acceptance criteria and the evidence that exists today. Reviewed at `main` **1248ccd** (2026-10-17); gate states re-checked at **a346145** (2026-10-18 16:35Z). This page replaces the gate table in [release-readiness.md](../product/release-readiness.md), whose "open PR" entries have since merged.

## Evidence levels

A capability is only as far along as its weakest evidence. A passing unit test is not evidence of live Fabric compatibility.

| Level | Means |
| --- | --- |
| Implemented | Code is on `main` |
| Unit-tested | Automated tests with fakes run in CI |
| Integration-tested | Automated tests against real local components (DuckDB, delta-rs, SQLite, the engine process) run in CI |
| Live-tenant verified | Run against a real Fabric tenant, with a recorded result |
| Released | In a Stable build |
| Enterprise validated | Used by a pilot organization under its own policy |

## Verdict

**Not ready for an enterprise pilot.** Four gates are met (2, 7, 8, 9). Two are blocked on Chris (signing, live verification), and one High finding is still open (SH-2, unsigned updates, which signing closes). Nothing is live-tenant verified.

## Gates

| # | Gate | Acceptance criteria | Evidence today | State |
| --- | --- | --- | --- | --- |
| 1 | Security verification | No open High finding in the [register](findings.md); CSP set; external-tool approvals bound to the code they run; token cache encrypted on Windows; security docs match the code | CSP set in `tauri.conf.json` (SH-1 closed); the Fabric view opens only Fabric and sign-in hosts (SH-12, #259); RPC replies matched by id (SH-11). Still open: unsigned updates (SH-2, High) and the plain-text token cache (awaits D-C2) | **Failing: Chris (D-C2, D-C3)** |
| 2 | Automated tests | Engine, UI, browser and Rust suites all run in CI on every PR and all pass; failure-injection tests for post-commit failures, restore and leases exist | Engine, UI, browser (`ui-browser` job, headless Chromium) and Rust (`cargo test --release --locked`) suites run in `ci.yml` on every PR; failure-injection tests added (#138, #152, #155, #159, #180) | **Met** |
| 3 | Live integration verification | FD-101 harness passes on a designated non-production workspace; summary recorded in release-readiness.md with date and build | Harness merged (#80), never run | **Blocked: Chris (HD-029)** |
| 4 | Build signing and integrity | Exe and engine DLLs Authenticode-signed; release fails if unsigned; checksums required; provenance attested | Signing steps built but skipped (no account); unsigned releases publish without failing (SH-2); checksums required (SH-3, #158); attestations produced, not verified | **Blocked: Chris (D-C3)** |
| 5 | Installer and update reliability | Stable never installs a Preview engine; downgrades refused; failed update restores the previous exe; agent installers use pinned dependencies | Stable never takes a Preview engine after a network error and checksums are required (#158). Out-of-date channel releases refused and the previous exe kept until the new one starts (SH-5, HD-022; Windows check pending); agent installer unpinned (SH-9); no MSI | **Partly met** |
| 6 | Rollback capability | A named earlier build can be reinstalled for app and engine; documented and tested | Each published build is kept as a `build-<sha>` release (newest 20, plus every Stable build); Promote to stable can promote a named earlier build, after checking its files against its `version.json`; documented in [rollback](../release-management/README.md#rollback). Not yet: the app can't be pointed at a build for its engine download, so a pinned exe on a new PC gets the channel's current engine; no rollback drill run; the newer build's local data isn't reverted | **Partly met (HD-028)** |
| 7 | Backup and restore | Restore yields exactly the backed-up state, verified by hash; refuses while in use; runbook in docs | Restore removes stale WAL files, refuses databases in use and verifies each file's SHA-256 (#155); docs (#91) | **Met** |
| 8 | Enterprise configuration policies | Invalid or wrongly typed policy fails closed and is shown in Settings; every documented key tested | Fails closed and keeps the last good copy, shown in Settings (#165) | **Met** |
| 9 | Operational logging and diagnostics | Logs and bundles never contain secrets (shared redaction cases pass through every writer); audit chain verifies with concurrent writers | Agent log fields redacted before encoding (#157); audit chain holds across processes (#182); more secret patterns (#181, #215) | **Met** |
| 10 | Documentation and support | Install, enterprise install, security, permissions, backup, troubleshooting accurate at the release commit; support route agreed with pilot | False statements corrected (#137); [security evidence pack](../evidence-pack.md) (#236) with every outbound host; policy file permissions documented (#261). Privacy statement and support terms drafted, waiting on KratosBI's details | **Partly met: Chris** |

## Capability evidence

| Capability | Implemented | Unit-tested | Integration-tested | Live-tenant verified | Released |
| --- | --- | --- | --- | --- | --- |
| Notebooks and SQL on this PC (local Delta) | Yes | Yes | Yes (DuckDB, delta-rs) | n/a | Yes (Preview, Stable) |
| Concurrent writes to one table | Yes | Yes | Yes (local Delta, 6 writers) | No | Yes |
| Writing tables to OneLake | Yes | Yes (fakes) | No | Maintainer's tenant only, with an own app registration; not recorded | Yes |
| Sign-in with no app registration (Fabric CLI client, WAM) | Yes | Yes | No | No | Yes |
| Lakehouse create, OneLake files, Delta append, merge, time travel | Yes | Yes | Local only | No (harness not run) | Yes |
| Metadata framework against OneLake | Yes | Yes | Local only | No | Yes (Preview label) |
| Run in Fabric, schedules, side by side, Prove | Yes | Yes | No | No | Yes (Preview label) |
| Headless agent and leases | Yes | Yes | Local only | No (OneLake leases unverified) | Engine only |
| External AI tools (MCP, local API) | Yes | Yes | Yes (stdio, HTTP transports) | n/a; named tools Untested | Yes |
| Policies, audit, backup, support bundle | Yes | Yes | Partly | n/a | Yes |
| Business key service (SQL_Platform, T-SQL) | Yes | SQLite only | No | No | Yes |

Nothing is enterprise validated.

## Blockers that need Chris

| Blocker | Unblocks | Ask |
| --- | --- | --- |
| Azure Trusted Signing account (D-C3) | Gate 4, HD-021 | Create the account and set the four repository variables |
| `msal-extensions` dependency (D-C2) | Gate 1 token encryption, HD-013 | Yes or no on adding it (recommended yes) |
| A non-production Fabric workspace marked for testing, and an OK to run the harness | Gate 3, HD-029, HD-054 | Name the workspace; the harness refuses anything not marked |
| Review of workflow changes | HD-011, HD-023, HD-024, HD-028 | Repository policy: workflow changes need maintainer review |
