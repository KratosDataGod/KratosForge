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

The harness and redaction use the existing packages (`requests`, `deltalake`, `pyarrow`, `msal`). Encrypting the token cache would add `msal-extensions` and is held for a decision (D-C2).

## D-E6: One branch per pull request (2026-10-17)

Each capability gets its own branch named `claude/enterprise-readiness-1imoje-<item>` and its own pull request, so each can be reviewed and merged alone. The backlog is updated in each pull request; when two are open at once the later one is merged with main after the first is merged.

## D-E7: Checksums come from version.json, signing remains the real fix (2026-10-17)

FD-102a makes the app check the engine and exe against the SHA-256 values in `version.json`. That catches corruption and mixed-up files, not an attacker who can replace every file in a release. The real protection against that is code signing (PR #28, waiting on the Trusted Signing account) plus build provenance (FD-103). This is said plainly in `docs/security.md`.

## D-E8: Opt-in usage data goes to Application Insights (2026-10-17)

Chris chose telemetry over none (D-C1). It goes to Azure Monitor Application Insights because the app already lives in Microsoft's cloud and IT can point it at their own resource with one policy key. It is one custom event per day with counts of audited actions, failures by error type and seconds, and nothing that names data, people or tenants (docs/telemetry.md). It is off until a person turns it on. A build only sends when the `TELEMETRY_CONNECTION_STRING` repository variable is set, so nothing leaves any PC until Chris creates the resource. The engine sends rather than the Rust shell, so proxies and the existing `requests` stack apply.

## Decisions waiting on Chris

| Id | Question | Recommendation | Blocks |
| --- | --- | --- | --- |
| D-C2 | Add `msal-extensions` to encrypt the token cache with Windows DPAPI | Yes; it is Microsoft's own package for this | FD-131b |
| D-C3 | Create an Azure Trusted Signing account | Yes; it unblocks signing, MSI packaging and Smart App Control | FD-103, FD-104 |
