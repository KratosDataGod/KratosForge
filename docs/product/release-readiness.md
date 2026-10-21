# Release readiness

What must be true before KratosForge goes to an enterprise pilot, and where each piece stands. Updated as items in the [enterprise backlog](enterprise-backlog.md) land.

Last updated 2026-10-18 at `main` a346145. The current gate status, with acceptance criteria and evidence, is the [readiness checklist](../hardening/readiness-checklist.md) from the hardening audit.

## Verdict today

**Not ready for an enterprise pilot.** Two gates need Chris: a run of the live test harness on a non-production workspace (G1) and a code signing account (G2). The [hardening audit](../hardening/findings.md) defects are being fixed in their own PRs; the readiness checklist says which are still open.

## What's missing for an enterprise-approved app

An enterprise security or IT team approving KratosForge would ask for each of these. Asked by Chris in unit testing on 2026-10-18 (item 38).

| # | Missing | Why an enterprise needs it | Waits on |
| --- | --- | --- | --- |
| 1 | Code signing | Smart App Control, App Control and many EDR tools block or flag the unsigned exe and engine | Chris: Azure Trusted Signing account (D-C3), then signing in CI |
| 2 | Live tenant verification | Nothing has been proven on a real tenant, including sign-in with no app registration | Chris: one run of the FD-101 harness on a non-production workspace |
| 3 | Token cache encryption | The MSAL token cache is a plain file in the user's profile; security reviews expect it protected with Windows DPAPI | Chris: decision D-C2 |
| 4 | Pin a build and roll back | Each channel keeps only its newest build, so IT can't hold a known build or go back one | Release management: keep every build as its own release asset |
| 5 | Managed install package | Intune and Configuration Manager deploy MSI or MSIX; today it's one exe plus a policy file copied by script | A packaging PR, which needs signing (1) first |
| 6 | Security evidence pack | Reviewers want one place with the security model, data flows, permissions, SBOM and provenance, plus a privacy statement and support terms. The pieces exist ([security](../security.md), [permissions](../permissions.md), [telemetry](../telemetry.md); SBOMs and build provenance attestations are made for each Preview build, but the Stable release carries only the exe, engine and version.json) but aren't collected, and there is no privacy statement or support terms | Enterprise readiness can assemble the pack; privacy statement and support terms need Chris as publisher |
| 7 | Open High security findings | A security review will find them: no Content Security Policy ([SH-1](../hardening/findings.md#sh-1-high-no-content-security-policy-tauri-api-on-the-page)), and updates prove integrity but not who published them ([SH-2](../hardening/findings.md#sh-2-high-updates-prove-integrity-not-authenticity)). The third, run-notebook approvals not bound to the code (EN-1), was fixed in #156 | Hardening: HD-020; HD-021 also needs signing (1) |
| 8 | Tested features | Run in Fabric, schedules, Prove, the metadata framework against OneLake and the agent are Preview: built to documented APIs, untested on a tenant | Live tenant runs (2), feature by feature |

Settings could show the parts of this a person can see on their own PC (signed build, policy applied, token cache protected, updates managed by IT). That is a UI change, so it starts with a mockup.

## Pilot gates

| Gate | Needs | State |
| --- | --- | --- |
| G1 Live verification | The FD-101 harness run on a non-production workspace with every check passing, results attached below | Harness merged (#80); not run yet |
| G2 Signed builds | Azure Trusted Signing account (D-C3), signing in CI (PR #28) | Blocked on Chris |
| G3 Update integrity | FD-102a (checksums enforced), FD-103 (scanning, SBOM, provenance) | Merged (#82, #86, #88) |
| G4 Admin controls | FD-105 policies, FD-104 deployment guide | Merged (#90). A broken policy file keeps the last good copy (#165, merged). Pinning a build and rollback still need per-build releases |
| G5 Data protection | FD-131a redaction, FD-138 safe support report, FD-131b local retention | Merged (#84, #95, and more secret patterns in #181). Token cache encryption waits on D-C2 |
| G6 Audit | FD-125 audit trail | Merged (#89, and one chain across processes in #182) |
| G7 Recovery | FD-126 backup and restore | Merged (#91) |
| G8 Documentation | This page, the security model, install, enterprise install, backup, troubleshooting | Written; a security evidence pack and privacy statement are missing (row 6 above) |

## Supported capability matrix

"Supported" means tested on a live tenant and covered in CI. Nothing is in that column yet; the [docs home](../README.md#what-is-tested-and-what-isnt) has the detailed list.

| Area | Level |
| --- | --- |
| Notebooks and SQL on this PC (local Delta tables) | Tested in CI on Windows and Linux |
| Writing tables to OneLake from the PC | Used on the maintainer's tenant with an own app registration |
| Sign-in with no app registration | Not yet tried on a tenant. The FD-101 harness checks it |
| Lakehouse create, OneLake files, Delta write, append, merge and time travel | Checked by the FD-101 harness once it is run; until then untested |
| Run in Fabric, schedules, side by side, Prove | Preview (built to documented APIs, untested) |
| Metadata framework against OneLake | Preview |
| Headless agent | Preview |
| Policy controls, audit trail, backup and restore, support bundle | Tested in CI; not yet used by an organization |

## Security architecture summary

The full threat model is in `docs/security.md` (PR #82). In short:

| Boundary | How it is protected |
| --- | --- |
| Sign-in | Microsoft's Fabric CLI public client with Windows' account broker; no app registration and no secrets in the app. Tokens stay in the user's MSAL cache. An organization can limit sign-in to its own tenants (`allowed_tenants`) |
| Access | Every call uses the person's own token, so the app can do no more than their Fabric roles allow. Scopes are listed in `docs/permissions.md` |
| UI to engine | The engine is a child process on stdio with a fixed method allowlist. It listens on no port by default. A browser sign-in with an own app registration briefly listens on `localhost` for Microsoft's redirect. If someone switches on **Settings > Integrations > Local HTTP**, a separate process serves MCP and the REST API on `127.0.0.1:47615` only, with a bearer token per client, `Host` and `Origin` checks, rate limits and an audit line per call ([AI integration](../architecture/ai-integration.md)) |
| Updates | The exe and engine come from the repository's GitHub releases. Their SHA-256 must match `version.json` and the engine's build must match the release. IT can turn self-updates off |
| Build | Actions pinned by SHA, dependency scanning, SBOMs and build provenance attestations (PR #88). Not code signed yet (G2) |
| Data leaving the PC | Usage data only if a person turns it on, or their organization does: daily counts of actions, failures and timings, with no data, names, account or tenant ([telemetry.md](../telemetry.md)). Policy can turn it off for everyone. Error messages and logs have secrets removed; problem reports and support bundles also remove personal details |
| Data in OneLake | Writes go through the person's token. Preview before write and promote. The live harness only writes to a workspace marked for testing, and only to items it created |
| Records | A hash-chained local audit log of every run, write and Fabric job start |

## Operational requirements

| Requirement | Detail |
| --- | --- |
| PCs | Windows 10 or 11, 64-bit, with WebView2. Several hundred MB per user for the engine |
| Network | The hosts in [Enterprise install: Network access](https://github.com/KratosDataGod/KratosForge/blob/main/docs/enterprise-install.md#network-access) |
| Fabric | A capacity for lakehouses and anything run in Fabric. Tenant settings as in [Configure Azure and Fabric](../azure-setup.md) |
| Releases | A maintainer promotes a Preview build to Stable with release notes. Under managed updates, IT deploys each Stable exe |
| Support | A `support_url` in policy, and support bundles for diagnosis |
| Backups | People's local notebooks and settings: [Back up and restore](https://github.com/KratosDataGod/KratosForge/blob/main/docs/backup.md) |

## Validated scenarios

None yet. When the FD-101 harness is run, its `summary.md` goes here with the date, build and workspace type (never ids or names). A scenario counts as validated only from a harness result or a recorded run on a real tenant, never from a unit test.

| Date | Build | Scenario | Result | Evidence |
| --- | --- | --- | --- | --- |
| | | | | |

## Known limitations

- The desktop app is Windows only; the engine and agent also run on Linux.
- It isn't code signed, so Smart App Control can block the engine.
- Spark runs on the PC through DuckDB's PySpark API and Spark SQL translation, so not every Spark API works. The compatibility check says which cells won't.
- Concurrent writers to one table are protected by leases and version-checked commits. That is untested on OneLake.
- Releases keep only the newest build of each channel, so a build can't be pinned and there is no one-step rollback.
- The update check uses GitHub, which limits checks to 60 an hour per internet address.
- No MSI or MSIX package yet. Deployment copies one exe.

## Pilot acceptance checklist

To be completed with the pilot organization. Each line needs a date and a name.

1. The fixes for gates G3 to G8 are merged and promoted to Stable.
2. Code signing is in place (G2), or the pilot PCs don't run Smart App Control or App Control.
3. Tenant settings and sign-in checked as in [azure-setup.md](../azure-setup.md).
4. The live test harness passes on the pilot's non-production workspace, and its summary is recorded above.
5. The policy file is deployed with the organization's channel, `support_url`, update model and, if wanted, `allowed_tenants` and `allow_fabric_runs`. **Settings > Help and about** shows nothing ignored.
6. The network hosts are allowed through the organization's proxy, checked from one pilot PC.
7. The ten core flows from the launch review pass on the pilot tenant.
8. The support route is agreed: who receives reports, the response time, and that support bundles are the way to send logs.
9. Rollback plan: under managed updates, IT keeps the previous exe; everyone knows rollback works only while the engine is still installed (see Known limitations).
10. Users have a backup routine for local notebooks, or keep them in Git.
11. Someone in the pilot organization has read `docs/security.md` and `docs/permissions.md` and accepted the scopes.
