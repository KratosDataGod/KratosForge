# Security evidence pack

Everything a security or IT team needs to review KratosForge before approving it, in one place. Each row links to the page or file that holds the evidence. Where something is missing or unverified, the row says so.

Last checked against `main` c9874896 (2026-10-23). Nothing in this pack has been verified on a live Fabric tenant yet. That is gate 3 in the [readiness checklist](hardening/readiness-checklist.md).

## At a glance

| Question a reviewer asks | Answer | Evidence |
| --- | --- | --- |
| What is it? | A Windows desktop app that runs Fabric notebooks and SQL on the PC and reads and writes OneLake with the person's own sign-in | [Architecture](architecture.md) |
| Does it need an app registration or secrets? | No. It signs in with Microsoft's Fabric CLI public client and the Windows account broker | [Permissions](permissions.md) |
| What can it do in Fabric? | Only what the signed-in person's Fabric roles allow. Every call uses their delegated token | [Permissions: what each feature uses](permissions.md#what-each-feature-uses) |
| Does it listen on the network? | Not beyond this PC. The running app listens on `127.0.0.1`, on a port it picks, for `kratosforge://` links handed over by another copy of itself; a link needs a random token kept in the person's data folder and is only shown, never run. Only if someone turns them on: **Settings > Integrations > Local HTTP** (a token per tool) and **Settings > Integrations > VS Code > Let VS Code run notebooks** (pairing the person approves), both on `127.0.0.1` only | [Security model: trust boundaries](security.md#pieces-and-trust-boundaries) |
| What leaves the PC? | Calls to Microsoft (sign-in, Fabric, OneLake, Power BI, Graph), update checks to GitHub, and usage counts only if turned on | [Data flows](#data-flows) below |
| Is customer data collected? | No. Usage data is off by default and never holds data, names, accounts or tenants | [Usage data](telemetry.md) |
| Can IT control it? | Yes. A policy file fixes settings for everyone, including tenants allowed, runs in Fabric, updates, telemetry and AI tools | [Configure Azure and Fabric: policy file](azure-setup.md#4-push-settings-to-everyone) |
| Is it signed? | **Not yet.** Waiting on a code signing account | [Release readiness, row 1](product/release-readiness.md#whats-missing-for-an-enterprise-approved-app) |
| Is the sign-in cache encrypted? | Yes, on Windows: Windows DPAPI for the person's own Windows account, so a copied file is useless on another account or PC. Programs running as that person can still read it | [Security model: threats](security.md#threats-and-what-stops-them) |

## Documents

| Area | Document |
| --- | --- |
| Threat model, trust boundaries, open risks | [Security model](security.md) |
| Sign-in, scopes and roles | [Permissions](permissions.md) |
| What is installed and where files live | [Install: where things are kept](install.md#where-things-are-kept-on-your-pc) |
| Deployment, update models, rollback, network hosts | [Enterprise install](enterprise-install.md) |
| Tenant settings and the policy file | [Configure Azure and Fabric](azure-setup.md) |
| Usage data, field by field | [Usage data](telemetry.md) |
| External AI tools: local API, approvals, audit | [AI integration security](product/ai-integration-security.md) |
| Audit trail | [User guide: audit trail](user-guide.md#audit-trail) |
| Requests, decisions and access given or taken away | [The platform's audit trail](platform-audit.md) |
| Backup and restore | [Back up and restore](backup.md) |
| Support bundles and logs | [Troubleshooting](troubleshooting.md) |
| Independent code audit, findings and fixes | [Hardening audit](hardening/README.md), [findings](hardening/findings.md), [release gates](hardening/readiness-checklist.md) |
| What an approver will still find missing | [Release readiness](product/release-readiness.md) |
| Live test harness, for a pilot tenant | [Live testing](live-testing.md) |

## Data flows

| From | To | What | When | Protection |
| --- | --- | --- | --- | --- |
| The app | `login.microsoftonline.com` | Sign-in | When someone signs in or a token renews | Microsoft identity platform; Windows account broker |
| The app | `api.fabric.microsoft.com`, `onelake.dfs.fabric.microsoft.com`, `api.powerbi.com`, `graph.microsoft.com` | Fabric items, jobs, OneLake files and tables, semantic models, people lookups | When someone uses those features | HTTPS with the person's token; writes are version-checked and ask first where they replace data |
| The app | `app.fabric.microsoft.com`, `app.powerbi.com`, Microsoft sign-in hosts, and any identity provider hosts IT lists in `fabric_view_sign_in_hosts` | Fabric's own pages, shown inside the app | When someone opens them | The embedded view opens only those hosts over `https`; every other link opens in the default browser. SmartScreen stays on. Its cookies are encrypted with Windows DPAPI |
| The app | `pypi.org`, `files.pythonhosted.org` (or the organization's own package index) | Python packages | When someone runs `%pip install` or sets up an environment, or a build without a bundled engine installs its engine | pip's HTTPS; the engine's own packages are pinned to the versions in `sidecar/constraints.txt` |
| The app | `github.com` and its release hosts (`objects.githubusercontent.com`, `release-assets.githubusercontent.com`; `codeload.github.com` for a build without a bundled engine), or IT's own engine folder (`engine_download_url`) | Update check and downloads | Each time the app starts, unless the policy turns updates off | SHA-256 checked against the release's `version.json`; not yet signed |
| The app | The organization's central lakehouse (`FabricDesktop_Central`) | Optimization results and other organization-wide tables | Only after someone connects it and agrees to each first write | The person's token; `allow_optimize_lakehouse: false` turns it off |
| The app | `onelake.table.fabric.microsoft.com` | Lists of OneLake tables and their schemas | When someone browses lakehouse tables | HTTPS with the person's token; reads only |
| The app | `*.kusto.fabric.microsoft.com` (the workspace's monitoring Eventhouse) | Fixed read-only KQL queries for one warehouse | Only when someone opens a warehouse's Monitoring tab | The person's Kusto token, sent only to an address of exactly that form read from Fabric |
| The app | The person's Databricks workspace (`*.azuredatabricks.net`) | SQL statements, cancel, EXPLAIN, Unity Catalog lists | Only after the person confirms a session on that warehouse | A token from the Databricks CLI or a service principal secret reference, held in memory only |
| The app | The person's own Git remotes (Azure DevOps, GitHub, GitLab, Bitbucket or another host they add) | Notebook and item source they push or pull | Only when someone connects a repository and pushes, pulls or clones | Git's own HTTPS sign-in to that host |
| Notebooks run on the PC | `{vault}.vault.azure.net`, plus Kusto, storage and Power BI token audiences | Secrets and tokens the notebook asks for (`notebookutils.credentials`) | Only when notebook code calls `getSecret` or `getToken` | The person's own token for that resource |
| Notebooks run on the PC | Any host the notebook's own code calls | Whatever that code sends | When the notebook runs | Same as any script the person runs; the app doesn't filter it |
| The app | A Teams incoming webhook | Alerts someone set up | Only if configured | Webhook address redacted from every log and error |
| The app | Application Insights (KratosBI's, or the organization's own) | One daily record of action counts, failures and timings | Only if usage data is on | No data, names, accounts or tenants; policy can force it off |
| External AI tools | The app's local API on `127.0.0.1` | Requests the person approved | Only if Integrations are turned on | Token per tool, loopback only, no browser requests, every call audited |
| VS Code windows | The engine's editor channel on `127.0.0.1` | Cells to run in a notebook's session, and their results | Only while **Let VS Code run notebooks** is on, from a window the person paired | Pairing needs a value from the person's data folder and their yes; a token per window, stored only as a hash; loopback `Host` only, no browser requests ([Run cells from VS Code](vscode.md#run-cells-from-vs-code)) |
| Another copy of the app | The running app on `127.0.0.1` | A `kratosforge://` link to show | When someone opens such a link while the app is open | A random token from the person's data folder; the link is only shown, never run ([Links into KratosForge](vscode.md#links-into-kratosforge)) |

These are the flows the app itself starts, from the hosts named in its source code. Notebook code can reach other hosts, as it would anywhere else it runs, and Microsoft can redirect sign-in or downloads. Confirm the list from one pilot PC behind your proxy ([network access](enterprise-install.md#network-access)).

## Build and release evidence

| Evidence | Where | State |
| --- | --- | --- |
| SBOMs (CycloneDX) for the shell and the engine | `sbom-app.cdx.json` and `sbom-engine.cdx.json` on each Preview release (`latest`) | Made for every Preview build. The Stable release carries only the exe, engine and `version.json` |
| Build provenance attestations | Checked with `gh attestation verify <file> -R KratosDataGod/KratosForge` | Made for every build. Nothing on the PC checks them yet |
| SHA-256 checksums | `version.json` on each release | Enforced by the app for the exe and the engine |
| Dependency scanning | `pip-audit` and `cargo audit` in CI on every pull request | In place |
| Pinned dependencies | `sidecar/constraints.txt`, `Cargo.lock` with `--locked` | In place |
| Pinned CI actions | Every action in every workflow pinned by commit | In place (SH-8 closed) |
| Code signing | Authenticode via Azure Trusted Signing | **Not in place**: needs the signing account |

## Still open

These are the items a reviewer will raise. They are tracked in [release readiness](product/release-readiness.md#whats-missing-for-an-enterprise-approved-app):

- unsigned builds;
- no live tenant run;
- no MSI or MSIX, so the engine is installed in each person's own profile;
- Preview features.

Closed since the first version of this page: the app has a Content Security Policy, the sign-in cache is encrypted on Windows, and earlier builds are kept so IT can promote or reinstall a named build ([rollback](release-management/README.md#rollback)).

A privacy statement and support terms are being drafted for KratosBI to approve as the publisher.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** on the repository's Security tab, or contact KratosBI directly. Please don't open a public issue.
