# Production readiness checklist

Release gates for Fabric Desktop by KratosBI going to an enterprise pilot. Each gate has measurable acceptance criteria and the evidence that exists today. Reviewed at `main` **1248ccd** (2026-10-17); gate states re-checked at **a346145** (2026-10-18 16:35Z). This page replaces the gate table in [release-readiness.md](../product/release-readiness.md), whose "open PR" entries have since merged.

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

**Not ready for an enterprise pilot.** Three gates are met (7, 8, 9). Two are blocked on Chris (signing, live verification), and two High findings are still open (SH-1, SH-2). Nothing is live-tenant verified.

## Gates

| # | Gate | Acceptance criteria | Evidence today | State |
| --- | --- | --- | --- | --- |
| 1 | Security verification | No open High finding in the [register](findings.md); CSP set; external-tool approvals bound to the code they run; token cache encrypted on Windows; security docs match the code | 2 High findings open: no CSP (SH-1), unsigned updates (SH-2). EN-1 fixed (#156); security docs corrected (#137); token cache plaintext (awaits D-C2) | **Failing** |
| 2 | Automated tests | Engine, UI, browser and Rust suites all run in CI on every PR and all pass; failure-injection tests for post-commit failures, restore and leases exist | Engine (1,915 pass, 2 skipped) and UI suites run in CI; failure-injection tests added (#138, #152, #155, #159, #180). Rust tests (SH-6, HD-011) and browser tests (UI-8, HD-052) don't run in CI | **Partly met** |
| 3 | Live integration verification | FD-101 harness passes on a designated non-production workspace; summary recorded in release-readiness.md with date and build | Harness merged (#80), never run | **Blocked: Chris (HD-029)** |
| 4 | Build signing and integrity | Exe and engine DLLs Authenticode-signed; release fails if unsigned; checksums required; provenance attested | Signing steps built but skipped (no account); unsigned releases publish without failing (SH-2); checksums enforced but missing ones accepted (SH-3); attestations produced, not verified | **Blocked: Chris (D-C3)** |
| 5 | Installer and update reliability | Stable never installs a Preview engine; downgrades refused; failed update restores the previous exe; agent installers use pinned dependencies | Stable never takes a Preview engine after a network error and checksums are required (#158). Out-of-date channel releases refused and the previous exe kept until the new one starts (SH-5, HD-022; Windows check pending); agent installer unpinned (SH-9); no MSI | **Partly met** |
| 6 | Rollback capability | A named earlier build can be reinstalled for app and engine; documented and tested | Releases keep only the newest build per channel; engine always from the channel's current release | **Not met (HD-028)** |
| 7 | Backup and restore | Restore yields exactly the backed-up state, verified by hash; refuses while in use; runbook in docs | Restore removes stale WAL files, refuses databases in use and verifies each file's SHA-256 (#155); docs (#91) | **Met** |
| 8 | Enterprise configuration policies | Invalid or wrongly typed policy fails closed and is shown in Settings; every documented key tested | Fails closed and keeps the last good copy, shown in Settings (#165) | **Met** |
| 9 | Operational logging and diagnostics | Logs and bundles never contain secrets (shared redaction cases pass through every writer); audit chain verifies with concurrent writers | Agent log fields redacted before encoding (#157); audit chain holds across processes (#182); more secret patterns (#181, #215) | **Met** |
| 10 | Documentation and support | Install, enterprise install, security, permissions, backup, troubleshooting accurate at the release commit; support route agreed with pilot | False statements corrected (#137); readiness pages refreshed. No security evidence pack, privacy statement or support terms yet | **Partly met** |

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
