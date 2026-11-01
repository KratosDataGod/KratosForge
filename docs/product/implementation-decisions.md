# Implementation decisions

Decisions made in the enterprise product program, newest last. Each says what was decided, why, and what would change it. Decisions only Chris can make are listed at the end until he makes them.

## D-E1: Items owned elsewhere are classified, not built (2026-10-17)

Four programs were already building parts of FD-109 to FD-135. This program classifies those items as DUPLICATE with their owner and sends gaps to the owner instead of building a second version. **Why:** two implementations of kernel isolation or lineage would conflict in the same files. **Revisit if** an owner stops work on an item.

## D-E2: Git is untouched (2026-10-17)

No change to Git operations, source-control UI, Git service interfaces or Fabric Git synchronization. `meta/release.py` (which reads the Git commit for release records) is read, not changed. CI workflow changes in FD-103 touch build and release jobs only.

## D-E3: The live test harness is a CLI command Chris runs, not a CI job (2026-10-17)

`fabric-desktop live-test` runs on a person's PC with their own sign-in, or with a service principal from an `agent.toml`. **Why:** a CI job would need a tenant, a capacity and a stored credential, which are cloud resources nobody has approved. **Revisit when** a dedicated test tenant and service principal exist; the same command can then run from a scheduled workflow.

## D-E4: Live tests refuse anything that might be production (2026-10-17)

A workspace qualifies only when its description contains `fabric-desktop-live-test`, which a workspace admin has to add on purpose. Even then the harness refuses a workspace whose name contains "prod" or "production" as a word, and any workspace the organization's policy file names as a Platform workspace. It only creates new items named `fdlt_<run id>` and only deletes what it created in that run. **Why:** the requirement is that destructive tests never run against production, and a name check alone is too easy to get wrong.

## D-E5: No new dependencies without a reason in the pull request (2026-10-17)

The harness and redaction use the existing packages (`requests`, `deltalake`, `pyarrow`, `msal`). Encrypting the token cache (D-C2, approved 2026-10-23) calls Windows DPAPI through ctypes, so it adds no package.

## D-E6: One branch per pull request (2026-10-17)

Each capability gets its own branch named `claude/enterprise-readiness-1imoje-<item>` and its own pull request, so each can be reviewed and merged alone. The backlog is updated in each pull request; when two are open at once the later one is merged with main after the first is merged.

## D-E7: Checksums come from version.json, signing remains the real fix (2026-10-17)

FD-102a makes the app check the engine and exe against the SHA-256 values in `version.json`. That catches corruption and mixed-up files, not an attacker who can replace every file in a release. The real protection against that is code signing (PR #28, waiting on the Trusted Signing account) plus build provenance (FD-103). This is said plainly in `docs/security.md`.

## D-E8: Opt-in usage data goes to Application Insights (2026-10-17)

Chris chose telemetry over none (D-C1). It goes to Azure Monitor Application Insights because the app already lives in Microsoft's cloud and IT can point it at their own resource with one policy key. It is one custom event per day with counts of audited actions, failures by error type and seconds, and nothing that names data, people or tenants (docs/telemetry.md). It is off until a person turns it on. A build only sends when the `TELEMETRY_CONNECTION_STRING` repository variable is set, so nothing leaves any PC until Chris creates the resource. The engine sends rather than the Rust shell, so proxies and the existing `requests` stack apply.

## D-E9: A broken policy file keeps the last copy that could be read (2026-10-18)

Chris chose "last known good" for HD-010. A policy file that exists but can't be read is replaced by the last copy each PC read successfully, as Windows Group Policy keeps its last applied settings, so one bad push by IT doesn't stop sign-in for everyone. Only a PC that has never read a valid file fails closed (no runs in Fabric, Preview, telemetry and AI tools off). On 2026-10-18 Chris chose that sign-in still works there, so one typo never stops people working on their PCs; `allowed_tenants` isn't enforced on such a PC until the file is fixed. Settings > Help and about says when the copy is in use. The copy lives in the person's profile; editing it only matters while the real file is broken.

## D-E10: The engine allows DuckDB 2, and the app keeps its 1.x pin (2026-11-01)

`sidecar/pyproject.toml` allows `duckdb>=1.1,<3`, so anyone who installs the engine into their own Python can use DuckDB 2. The app's own engine stays pinned to `duckdb==1.5.6` in `constraints.txt`. **Why:** the whole engine suite passes on 2.0 (7,730 tests, the same as on 1.5.6, #1401), and CI runs it on both on every change (#1355). Before that, 2.0 failed 79 engine tests, so #1342 capped it below 2 while #1341's fixes landed. `tests/test_duckdb_version.py` checks that pyproject allows the pinned version and the next major CI tests, and nothing beyond. **Revisit when** moving the app's pin to 2.x, which needs a Windows run, a Preview build and the TPC-H comparison on Chris's PC, or when CI on 2.x fails.

## Decisions waiting on Chris

| Id | Question | Recommendation | Blocks |
| --- | --- | --- | --- |
| D-C3 | Create an Azure Trusted Signing account | Yes; it unblocks signing, MSI packaging and Smart App Control | FD-103, FD-104 |
