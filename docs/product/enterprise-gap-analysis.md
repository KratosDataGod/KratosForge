# Enterprise gap analysis

This is the Phase 0 assessment for the enterprise product program (items FD-101 to FD-140). It was written on 2026-10-17 against `main` at `07e21c8`. The ids FD-101 and up are separate from the FD-1 to FD-15 work items in the [enterprise roadmap](../enterprise-roadmap.md); where an item continues one of those, the table says so.

Git, source control and Fabric Git synchronization are out of scope for this program and are not changed by it. They belong to the Git-native development program.

## How this was assessed

- Read the code under `sidecar/fabric_desktop/`, `app/ui/`, `app/src-tauri/` and `.github/workflows/`, and the plans in [scale-and-operations-plan.md](../scale-and-operations-plan.md), [prove/plan.md](../prove/plan.md), [workloads/plan.md](../workloads/plan.md) and [enterprise-roadmap.md](../enterprise-roadmap.md).
- Ran the engine tests: 524 passed, 2 skipped, on Linux with Python 3.13 and `constraints.txt`.
- Checked the Fabric REST calls the harness needs against Microsoft's documentation: [Create Lakehouse](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items/create-lakehouse), [Delete Item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/delete-item) and [Manage a lakehouse with the REST API](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-api). Nothing here has run against a live tenant from this program; every such behavior is marked UNVERIFIED until Chris runs the harness (FD-101).

## Status key

| Status | Meaning |
| --- | --- |
| COMPLETE | Built, tested in CI, and documented. May still be Preview (untested on a tenant). |
| PARTIAL | Some of the requirement exists; the gap column says what is missing. |
| MISSING | Nothing meaningful exists yet. |
| BLOCKED | Needs a decision or an external dependency first. |
| DUPLICATE | Owned and being built by another program; this program does not build it. |
| UNVERIFIED | Built to documented Fabric behavior that nobody has checked on a tenant. |

## Ownership

Four programs already run in parallel. To avoid two implementations of one thing, items they own are classified here and tracked in the [backlog](enterprise-backlog.md), but built by their owner. Gaps found in an owned area are sent to the owner.

| Owner | Plan | FD items it owns |
| --- | --- | --- |
| Prove this notebook | [prove/plan.md](../prove/plan.md) | 109, 110, 111, 112, 119 |
| Staged roadmap (scale and operations) | [scale-and-operations-plan.md](../scale-and-operations-plan.md) | 114, 115, 116, 117, 118, 121, 127, 128 |
| Workload management | [workloads/plan.md](../workloads/plan.md) | 122, 123, 124 |
| Git-native development | (its own plan) | 113, 120, 132, 133, 134, 135 |
| This program (enterprise readiness) | [enterprise-backlog.md](enterprise-backlog.md) | 101, 102, 103, 104, 105, 125, 126, 129, 130, 131, 136, 137, 138, 139, 140 |

## Findings

Paths are under `sidecar/fabric_desktop/` unless they start with `app/`, `docs/` or `.github/`.

### Workstream A: security and reliability

| Id | Capability | Status | What exists | Gap |
| --- | --- | --- | --- | --- |
| FD-101 | Live Fabric integration test harness | MISSING | Unit tests fake every HTTP call (`tests/test_fabric_api.py`). FD-1 and FD-2 in the enterprise roadmap describe live checks but nothing runs them. | A repeatable, guarded harness that exercises auth, the REST API, OneLake files and Delta against a non-production workspace and cleans up. |
| FD-102 | Threat model and hardening | PARTIAL | PR #51 fixed notebook Markdown XSS, silent overwrites and updater checksums. IPC is stdio only with a method allowlist (`server.py`); subprocesses use argument lists; Tauri commands validate input. | No written threat model. The engine download is never checked against its published SHA-256 (`app/src-tauri/src/main.rs`, `install_engine`). The exe update accepts a missing checksum (`app/src-tauri/src/updater.py`, `download_app`). No CSP with `withGlobalTauri: true` (`app/src-tauri/tauri.conf.json`). Helper tools run by bare name (`curl.exe`, `tar.exe`). Token cache is plaintext on Windows (`auth.py`). |
| FD-103 | Supply chain security | PARTIAL | Python packages pinned in `sidecar/constraints.txt`; `cargo build --locked`; `version.json` carries SHA-256 for the exe and engine. | No dependency vulnerability scan, no SBOM, no build provenance attestation, actions pinned by tag not SHA, signing secrets reachable from same-repo pull request builds, embeddable Python downloaded without a hash check. |
| FD-104 | Enterprise installation and updates | PARTIAL | Single exe that installs its engine and self-updates (`main.rs`, `updater.py`); Stable and Preview channels; the policy file can fix the channel (`app_settings.py`); [install.md](../install.md) covers uninstalling. | No silent or per-machine install, no update-off or pinned-version policy, no documented rollback, no MSI or Intune package. Smart App Control blocks the engine until code signing exists (BLOCKED on the Trusted Signing account, PR #28). |
| FD-105 | Enterprise configuration policies | PARTIAL | `%ProgramData%\FabricDesktop\policy.json` sets `client_id`, `channel`, `show_preview`, `support_url`, `platform`, `kernel_process`; the user can't override them (`app_settings.py`). | No policy for approved endpoints, execution location (this PC, Fabric), automatic updates, local data retention or telemetry. Unknown keys and a malformed file are silently ignored. |

### Workstream B: local development and execution

| Id | Capability | Status | What exists | Gap | Owner |
| --- | --- | --- | --- | --- | --- |
| FD-109 | Reproducible environments | DUPLICATE (PARTIAL) | `envprofile.py` finds imports, checks them on the PC and in Fabric, records and compares runtimes. | Per-project environments and lock files; package source decision pending with Chris. | Prove |
| FD-110 | Notebook execution isolation | DUPLICATE (PARTIAL) | `kernel_proc.py` runs cells in a child process with crash recovery, stop and restart (PR #67, Preview, off by default). | No memory or CPU limits, no per-cell timeout. The shell's `rpc` call has no timeout behind one mutex (review item 4). | Prove |
| FD-111 | Workload compatibility analyzer | DUPLICATE (PARTIAL) | Regex `compute_check` (`fabric_run.py`); proof blockers and warnings (`proof.py`). | AST and SQL analysis with a verdict per workload (roadmap FD-14). | Prove |
| FD-112 | Safe side-by-side execution | DUPLICATE (PARTIAL) | Proofs run in sandboxes with pinned inputs (`prove_service.py`, `proof_runtime.py`). | The older Side by side (`side_by_side.py`) still writes the real tables twice (review item 6). Sent to the owner. | Prove |
| FD-113 | Integrated testing framework | DUPLICATE (PARTIAL) | Entity tests and the promote gate (`meta/testing.py`), comparisons (`verify.py`), data quality results. | Python, notebook and configuration validation in one place. | Git-native (validation framework) |

### Workstream C: data engineering and performance

| Id | Capability | Status | What exists | Gap | Owner |
| --- | --- | --- | --- | --- | --- |
| FD-114 | Bounded-memory processing | DUPLICATE (PARTIAL) | Preflight estimate and memory guard (`meta/preflight.py`, PR #69). | Partitioned and streamed full loads (stage 1.5). | Staged roadmap |
| FD-115 | Surrogate key performance | DUPLICATE (PARTIAL) | Key MERGE with retry (`meta/keys.py`), dimension MERGE of arriving keys (PR #61). | `read_keys` still reads the whole key table. | Staged roadmap |
| FD-116 | Guided ingestion | BLOCKED | Source registry and landing zone (`central/`). | Waiting on decision D2 (who creates landing Copy jobs). | Staged roadmap |
| FD-117 | Incremental improvements | DUPLICATE (PARTIAL) | Watermarks, MERGE writes, landing manifest (PR #70). | Late-arriving rows and source deletes. | Staged roadmap |
| FD-118 | Schema evolution | DUPLICATE (PARTIAL) | Drift policy, schema history, breaking-change check at promote (PR #71). | Type changes still rewrite; versioned port schemas. | Staged roadmap |
| FD-119 | Local performance profiler | DUPLICATE (PARTIAL) | `measure.py` (wall, CPU, peak memory), scorecards label measured vs estimated (`scorecard.py`). | Kernel child process not measured; disk I/O not measured. | Prove |

### Workstream D: deployment and operations

| Id | Capability | Status | What exists | Gap | Owner |
| --- | --- | --- | --- | --- | --- |
| FD-120 | Deployment preview | DUPLICATE (PARTIAL) | Promote impact and checks (`dataops_service.py`, `meta_service.py`). | Per-environment configuration diff, artifact compatibility. | Git-native |
| FD-121 | Promotion and recovery | DUPLICATE (PARTIAL) | Release records in `meta.deployments` (PR #73), per-table restore (`meta/restore.py`). | Release rollback (stage 5.3), decision D1. | Staged roadmap |
| FD-122 | Agent dashboard | DUPLICATE (PARTIAL) | Agent status records (`agent_status.py`), workloads view. | Queue and availability view. | Workloads |
| FD-123 | Incident management | DUPLICATE (PARTIAL) | Incident engine (`workloads/incidents.py`, PR #74). | Screens, escalation. | Workloads |
| FD-124 | Multi-worker execution | DUPLICATE (MISSING) | Leases (`leases.py`), per-machine queue (`jobqueue.py`). | Worker registry and shared claims (roadmap FD-12). | Workloads |
| FD-125 | Execution auditing | PARTIAL | `run_log` (run, env, worker, host, timings, rows), proof receipts with identity (`receipts.py`), release records with `by`. | No single audit trail of who ran what, where, against which workspace; no identity on `run_log` or agent enqueues; no record of Fabric API operations. | This program |
| FD-126 | Backup and disaster recovery | MISSING | Delta time travel and table restore only. | Nothing backs up app settings, schedules, the agent queue, workloads and incidents, or receipts. | This program |

### Workstream E: governance

| Id | Capability | Status | What exists | Gap | Owner |
| --- | --- | --- | --- | --- | --- |
| FD-127 | Versioned data contracts | DUPLICATE (PARTIAL) | Entity contracts, mesh ports, breaking-change check. | Consumer declarations, deprecation (stage 8.2). | Staged roadmap |
| FD-128 | Lineage and impact | DUPLICATE (PARTIAL) | Entity impact (`meta/project.py`), mesh map; column lineage in open PR #76. | Merge of #76. | Staged roadmap |
| FD-129 | Governance exceptions | PARTIAL | `exceptions.toml` with reason, approver and expiry (`meta/project.py`). | No request and approval flow, no audit history, no view of what is about to expire. | This program |
| FD-130 | Multi-team permissions | PARTIAL | OneLake access review (`access.py`), Platform admin checks (`central/`), workload owners. | No check of a person's Fabric role before a promote or write, no separation-of-duties rule (author can promote their own change to Prod). | This program |
| FD-131 | Sensitive data protection | PARTIAL | Secret-looking fields refused in config; `getSecret` values hidden; receipt exports can redact. | No redaction in logs, error messages or Report a problem; no retention for local caches, proof sandboxes and downloads; token cache unencrypted on Windows. | This program |

### Workstream F: extensibility

| Id | Capability | Status | What exists | Gap | Owner |
| --- | --- | --- | --- | --- | --- |
| FD-132 | Extension SDK | DUPLICATE (MISSING) | None. | Everything. | Git-native |
| FD-133 | Custom commands | DUPLICATE (PARTIAL) | Command bar registry (`app/ui/features.js`). | Third-party commands. | Git-native |
| FD-134 | Project templates | DUPLICATE (PARTIAL) | Notebook templates (`wizard.py`), project templates (`meta/templates.py`). | Organization-supplied templates. | Git-native |
| FD-135 | Extension packaging | DUPLICATE (MISSING) | None. | Everything. | Git-native |

### Workstream G: user experience and adoption

| Id | Capability | Status | What exists | Gap |
| --- | --- | --- | --- | --- |
| FD-136 | First-run experience | PARTIAL | Component check at startup, welcome page, sign-in callout (`app/ui/app.js`). | No readiness checklist (sign-in, capacity, workspace access, OneLake reach) with fixes. |
| FD-137 | Opt-in telemetry | PARTIAL | Off by default; Settings switch with the exact text sent; `telemetry` and `telemetry_connection_string` policy keys; daily counts to Application Insights (`telemetry.py`, docs/telemetry.md). | KratosBI's Application Insights resource and the `TELEMETRY_CONNECTION_STRING` variable don't exist yet, so builds send nothing. Not tried against a real Application Insights resource (UNVERIFIED). |
| FD-138 | Diagnostic support bundles | PARTIAL | Report a problem copies build, Python, tenant, page and recent errors (`app/ui/settings.js`). | No redaction (paths with user names, emails, GUIDs can leak), no file bundle with logs and settings summary. |
| FD-139 | Accessibility | PARTIAL | Dialog roles and labels, about 140 ARIA attributes. | No focus return after dialogs, no skip link, no reduced-motion handling, `.cell:focus { outline: none }`, no audit. |
| FD-140 | Pilot readiness | PARTIAL | Install, Azure setup, permissions, troubleshooting, "What is tested and what isn't". | No capability matrix with support levels, no security architecture page, no pilot acceptance checklist. |

## Highest risks found

1. **Engine download is not integrity-checked** (FD-102, FD-103). The app downloads and runs `fabric-desktop-engine.zip` checking only that it names a build commit; CI already publishes `engine_sha256` but the app never reads it. Fixed first.
2. **The exe update skips its checksum when `version.json` has none** (FD-102). A missing checksum should refuse the update.
3. **No live verification of anything** (FD-101). Every Fabric integration is Preview; the harness is the way to change that.
4. **Report a problem can leak personal data** (FD-131, FD-138) into a public GitHub issue.
5. **No CSP** (FD-102). No injection bug is known, but any future one becomes code execution. Notebook HTML output uses `srcdoc` iframes, so a CSP needs those reviewed first (review item 7).

## Smallest useful implementation per item

The [backlog](enterprise-backlog.md) carries acceptance criteria. The order and why is in [implementation-decisions.md](implementation-decisions.md).
