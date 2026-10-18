# Production readiness checklist

Release gates for Fabric Desktop by KratosBI going to an enterprise pilot. Each gate has measurable acceptance criteria and the evidence that exists today. Reviewed at `main` **1248ccd** (2026-10-17). This page replaces the gate table in [release-readiness.md](../product/release-readiness.md), whose "open PR" entries have since merged.

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

**Not ready for an enterprise pilot.** Two gates are blocked on Chris (signing, live verification), and five confirmed High defects in data writes, restore and approvals need their fixes merged first (HD-001 to HD-006). Nothing is live-tenant verified.

## Gates

| # | Gate | Acceptance criteria | Evidence today | State |
| --- | --- | --- | --- | --- |
| 1 | Security verification | No open High finding in the [register](findings.md); CSP set; external-tool approvals bound to the code they run; token cache encrypted on Windows; security docs match the code | 3 High security findings open (SH-1, SH-2, EN-1); token cache plaintext (awaits D-C2); `security.md` has false claims (EN-7) | **Failing** |
| 2 | Automated tests | Engine, UI, browser and Rust suites all run in CI on every PR and all pass; failure-injection tests for post-commit failures, restore and leases exist | Engine 1,345 pass, 2 skipped, 87% line coverage (Linux, Python 3.13, this audit); UI 45 pass; Rust tests (12) not run by CI (SH-6); 24 browser tests not run by CI; no post-commit failure tests | **Partly met** |
| 3 | Live integration verification | FD-101 harness passes on a designated non-production workspace; summary recorded in release-readiness.md with date and build | Harness merged (#80), never run | **Blocked: Chris (HD-029)** |
| 4 | Build signing and integrity | Exe and engine DLLs Authenticode-signed; release fails if unsigned; checksums required; provenance attested | Signing steps built but skipped (no account); unsigned releases publish without failing (SH-2); checksums enforced but missing ones accepted (SH-3); attestations produced, not verified | **Blocked: Chris (D-C3)** |
| 5 | Installer and update reliability | Stable never installs a Preview engine; downgrades refused; failed update restores the previous exe; agent installers use pinned dependencies | Engine falls back to Preview on any network error (SH-4); downgrades accepted, `exe.old` deleted at startup (SH-5); agent installer unpinned (SH-9); no MSI | **Failing** |
| 6 | Rollback capability | A named earlier build can be reinstalled for app and engine; documented and tested | Each published build is kept as a `build-<sha>` release (newest 20, plus every Stable build); Promote to stable can promote a named earlier build, after checking its files against its `version.json`; documented in [rollback](../release-management/README.md#rollback). Not yet: the app can't be pointed at a build for its engine download, so a pinned exe on a new PC gets the channel's current engine; no rollback drill run; the newer build's local data isn't reverted | **Partly met (HD-028)** |
| 7 | Backup and restore | Restore yields exactly the backed-up state, verified by hash; refuses while in use; runbook in docs | Backup command and docs merged (#91); restore brings back post-backup data (EP-2) | **Failing until HD-005** |
| 8 | Enterprise configuration policies | Invalid or wrongly typed policy fails closed and is shown in Settings; every documented key tested | Keys implemented and tested (#90); malformed file fails open (EP-4); valid keys reported unknown | **Failing until HD-010** |
| 9 | Operational logging and diagnostics | Logs and bundles never contain secrets (shared redaction cases pass through every writer); audit chain verifies with concurrent writers | Redaction in errors, audit and bundles (#84, #89, #95); JSON agent log leaks quoted secrets (EN-5); audit chain forks across processes (EP-3) | **Failing until HD-007, HD-017** |
| 10 | Documentation and support | Install, enterprise install, security, permissions, backup, troubleshooting accurate at the release commit; support route agreed with pilot | Extensive docs; four false statements in security and AI docs (DOC-1); readiness page stale | **Partly met (HD-009)** |

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
