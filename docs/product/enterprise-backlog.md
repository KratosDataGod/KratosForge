# Enterprise backlog

Every FD item from the enterprise product program, with its owner and status. Items this program owns have acceptance criteria below; items another program owns link to that program's plan, where their stages and criteria live. Status words are defined in the [gap analysis](enterprise-gap-analysis.md#status-key).

Last updated 2026-10-17.

## Order of work

Highest priority first, by risk and by what unblocks other items. Owned-elsewhere items are not in this list.

| # | Item | Why now | Depends on |
| --- | --- | --- | --- |
| 1 | FD-101 Live test harness | Nothing is verified on a tenant; every later item needs a way to prove itself | none |
| 2 | FD-102a Update and engine download integrity, threat model | Highest confirmed security risk | none |
| 3 | FD-131a + FD-138 Redaction and a safe support report | Report a problem can post personal data publicly | none |
| 4 | FD-103 Supply chain: scan, SBOM, provenance | Release integrity for 5,000 people | FD-102a |
| 5 | FD-125 Execution audit trail | Enterprise pilots ask "who ran what, where" first | FD-131a (redaction) |
| 6 | FD-105 Configuration policies | Admins need update, execution and retention controls before a pilot | none |
| 7 | FD-104 Enterprise install and updates | Builds on FD-105's update policy | FD-105 |
| 8 | FD-140 Pilot readiness pack | Collects the above for a pilot | 101 to 105 |
| 9 | FD-126 Backup and recovery | Local state is lost with a PC | FD-131a |
| 10 | FD-136 First-run readiness checklist | Adoption; needs a mockup first | FD-101 checks reused |
| 11 | FD-139 Accessibility fixes | Enterprise procurement requirement | none |
| 12 | FD-130 Permission checks before writes | Separation of duties | FD-125 |
| 13 | FD-129 Exception requests and history | Builds on exceptions.toml | FD-125 |
| 14 | FD-137 Opt-in telemetry | Needs a decision on the destination | Decision T1 |
| 15 | FD-102b Content Security Policy | Defense in depth; notebook output frames need care | FD-102a |

## All items

| Id | Priority | Capability | Status | Owner | PR |
| --- | --- | --- | --- | --- | --- |
| FD-101 | P0 | Live Fabric integration test harness | MISSING | This program | [#80](https://github.com/KratosDataGod/fabric-desktop/pull/80) |
| FD-102 | P0 | Threat model and hardening | PARTIAL | This program | [#82](https://github.com/KratosDataGod/fabric-desktop/pull/82) (102a) |
| FD-103 | P0 | Supply chain security | PARTIAL | This program | [#88](https://github.com/KratosDataGod/fabric-desktop/pull/88), [#86](https://github.com/KratosDataGod/fabric-desktop/pull/86) |
| FD-104 | P0 | Enterprise installation and updates | PARTIAL (signing BLOCKED) | This program | [#90](https://github.com/KratosDataGod/fabric-desktop/pull/90) (guide) |
| FD-105 | P0 | Configuration policies | PARTIAL | This program | [#90](https://github.com/KratosDataGod/fabric-desktop/pull/90) |
| FD-109 | P0 | Reproducible environments | DUPLICATE | [Prove](../prove/plan.md) | |
| FD-110 | P0 | Notebook execution isolation | DUPLICATE | [Prove](../prove/plan.md) | #67 |
| FD-111 | P0 | Compatibility analyzer | DUPLICATE | [Prove](../prove/plan.md) | |
| FD-112 | P0 | Safe side by side | DUPLICATE | [Prove](../prove/plan.md) | #62, #65 |
| FD-113 | P1 | Testing framework | DUPLICATE | Git-native | |
| FD-114 | P1 | Bounded memory | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #69 |
| FD-115 | P1 | Surrogate key performance | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #61 |
| FD-116 | P1 | Guided ingestion | BLOCKED (D2) | [Staged roadmap](../scale-and-operations-plan.md) | |
| FD-117 | P1 | Incremental improvements | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #56, #70 |
| FD-118 | P1 | Schema evolution | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #71 |
| FD-119 | P1 | Performance profiler | DUPLICATE | [Prove](../prove/plan.md) | |
| FD-120 | P0 | Deployment preview | DUPLICATE | Git-native | |
| FD-121 | P0 | Promotion and recovery | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #73 |
| FD-122 | P1 | Agent dashboard | DUPLICATE | [Workloads](../workloads/plan.md) | |
| FD-123 | P1 | Incident management | DUPLICATE | [Workloads](../workloads/plan.md) | #74 |
| FD-124 | P1 | Multi-worker execution | DUPLICATE | [Workloads](../workloads/plan.md) | |
| FD-125 | P0 | Execution auditing | PARTIAL | This program | [#89](https://github.com/KratosDataGod/fabric-desktop/pull/89) |
| FD-126 | P1 | Backup and disaster recovery | MISSING | This program | [#91](https://github.com/KratosDataGod/fabric-desktop/pull/91) |
| FD-127 | P1 | Data contracts | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | |
| FD-128 | P1 | Lineage and impact | DUPLICATE | [Staged roadmap](../scale-and-operations-plan.md) | #76 (open) |
| FD-129 | P1 | Governance exceptions | PARTIAL | This program | |
| FD-130 | P1 | Multi-team permissions | PARTIAL | This program | [#93](https://github.com/KratosDataGod/fabric-desktop/pull/93) |
| FD-131 | P0 | Sensitive data protection | PARTIAL | This program | [#84](https://github.com/KratosDataGod/fabric-desktop/pull/84) (131a), [#95](https://github.com/KratosDataGod/fabric-desktop/pull/95) (131b retention) |
| FD-132 | P2 | Extension SDK | DUPLICATE | Git-native | |
| FD-133 | P2 | Custom commands | DUPLICATE | Git-native | |
| FD-134 | P2 | Project templates | DUPLICATE | Git-native | |
| FD-135 | P2 | Extension packaging | DUPLICATE | Git-native | |
| FD-136 | P1 | First-run experience | PARTIAL | This program | [#94](https://github.com/KratosDataGod/fabric-desktop/pull/94) (draft) |
| FD-137 | P1 | Opt-in telemetry | MISSING (decision T1) | This program | |
| FD-138 | P1 | Diagnostic support bundles | PARTIAL | This program | |
| FD-139 | P1 | Accessibility | PARTIAL | This program | [#92](https://github.com/KratosDataGod/fabric-desktop/pull/92) |
| FD-140 | P0 | Pilot readiness | PARTIAL | This program | this PR ([release-readiness.md](release-readiness.md)) |

## Items this program owns

### FD-101 Live Fabric integration test harness (P0)

- **Existing code:** `fabric_api.py`, `onelake.py`, `credentials.py`, `auth.py`; roadmap FD-1 and FD-2.
- **Dependencies:** none. Running it needs a non-production workspace on an active capacity.
- **Acceptance criteria:**
  - `fabric-desktop live-test --workspace <id>` checks sign-in, the Fabric REST API, lakehouse creation, OneLake file write/read/list/delete, Delta write, read, append, merge and time travel, and error handling for a missing item and a bad token.
  - Refuses to write to a workspace that isn't marked for testing, whose name looks like production, or that the organization's policy names as a Platform workspace.
  - `--dry-run` lists what it would create and writes nothing.
  - Everything it creates is named for the run and deleted at the end, including after a failure; a ledger lets `--cleanup` finish a cleanup that was cut off. It never deletes anything it didn't create.
  - Writes a JSON result and a Markdown summary with no tokens in them.
  - Unit tests cover the guards, cleanup after failure, the dry run and the report, with no network.
- **Known limitations:** deleting a lakehouse may be a soft delete where the tenant has item recovery on. It doesn't test Spark in Fabric, schedules or pipelines yet.
- **Risks:** Fabric API behavior differs by tenant settings; results are per tenant.

### FD-102 Threat model and hardening (P0)

Split in two so each change is reviewable on its own.

- **FD-102a (update integrity and threat model).** The app refuses an engine download whose SHA-256 doesn't match `version.json`, refuses an exe update with no checksum, and runs Windows tools by full path. Adds `docs/security.md`, the threat model.
  - Acceptance: a tampered or cut-off engine zip is refused before it is unpacked; a `version.json` without `exe_sha256` refuses the update; tests in `tests/test_updater.py` cover both.
- **FD-102b (Content Security Policy).** A CSP for the main window that keeps notebook HTML output working in its sandboxed frames.
  - Acceptance: CSP set in `tauri.conf.json`; notebook HTML, charts and the Fabric view still work; tested on Windows by hand (no automated WebView2 test exists).
- **Existing code:** `app/src-tauri/src/main.rs`, `app/src-tauri/src/updater.py`, `tauri.conf.json`.
- **Risks:** a wrong CSP shows a blank page to everyone on the next Preview build; it must be tried on Windows before merging.

### FD-103 Supply chain security (P0)

- **Acceptance criteria:** CI scans the pinned Python packages (`pip-audit` against `constraints.txt`) and the Rust lock file (`cargo audit`) on every pull request; releases carry a CycloneDX SBOM; release builds have a GitHub build provenance attestation that `gh attestation verify` accepts; third-party actions are pinned by commit SHA; signing runs only for pushes to `main`; the embeddable Python download is checked against a pinned hash.
- **Dependencies:** FD-102a. Workflow changes need Chris's review (AGENTS.md).
- **Limitations:** attestations prove where a build came from, not that the code is safe. Signing stays blocked on the Trusted Signing account.

### FD-104 Enterprise installation and updates (P0)

- **Acceptance criteria:** a documented per-machine and silent deployment path (copy the exe and policy file with Intune or Group Policy); policy settings to turn automatic updates off or pin a version; documented rollback and recovery from a failed engine install; uninstall steps that list every folder.
- **Remaining after #90:** releases keep only the newest build per channel and the engine always comes from the channel's current release, so pinning a build and real rollback need per-build release assets (a release workflow change for maintainer review). The app deletes `fabric-desktop.exe.old` at startup, so it is not a rollback path.
- **Blocked part:** an MSI or MSIX package and a quiet Smart App Control experience need code signing (PR #28).

### FD-105 Configuration policies (P0)

- **Built in #90:** `auto_update`, `allow_fabric_runs` (the `allowed_compute` criterion), `allowed_tenants`, and reporting of ignored keys in Settings. Still open: `allowed_hosts`, `local_retention_days` (with FD-131b), `telemetry` (waits on D-C1).
- **Acceptance criteria:** new policy keys, each with a test: `auto_update` (on/off), `allowed_compute` (this PC, Fabric or both), `allowed_hosts` (endpoints the app may call beyond Microsoft's), `local_retention_days`, `telemetry` (off by default, FD-137). A malformed policy file or unknown key is reported in Settings instead of silently ignored. Documented in [azure-setup.md](../azure-setup.md).
- **Existing code:** `app_settings.py`.

### FD-125 Execution auditing (P0)

- **Acceptance criteria:** one append-only audit record per run, write, promote and Fabric job start, with who (user or principal), where (this PC, agent name, Fabric), what (workspace, item, entity or notebook), when, outcome and resource use. Stored locally as JSON lines and, for metadata environments, in LH_Meta. Values pass through the FD-131 redaction. Readable from Monitor; exportable.
- **Existing code:** `meta/store.py` (`run_log`), `receipts.py`, `logs.py`, `meta/release.py`.
- **Coordination:** `run_log` and `meta/store.py` are shared with the staged roadmap and workloads programs; the audit trail is a new table and module so it doesn't change theirs.

### FD-126 Backup and disaster recovery (P1)

- **Acceptance criteria:** `fabric-desktop backup` and `restore` for app settings, schedules, metadata project settings, the agent queue and workloads database; a backup is validated before restore; tokens are never backed up; recovery runbook in docs.

### FD-129 Governance exceptions (P1)

- **Acceptance criteria:** exceptions in `exceptions.toml` show in the app with owner, reason, approver and expiry; expiring ones raise a notice; adding one records who asked and who approved in the audit trail (FD-125).
- **In the app:** Data mesh > Quality > Exceptions lists each exception with the table's owner, the reason, the approver and the expiry. One that ends within 14 days raises a notice on the bell and a banner on Data mesh. Still open: asking for an exception in the app, and recording who asked and who approved in the audit trail.

### FD-130 Multi-team permissions (P1)

- **Acceptance criteria:** before a promote or a write to Test or Prod, the app checks the person's Fabric workspace role and says what's missing; an optional policy rule stops the author of a change from promoting it to Prod; no app-level identity store (Fabric and Entra stay the source of truth).

### FD-131 Sensitive data protection (P0)

- **FD-131a (redaction).** A `redact` module that removes tokens, SAS signatures, emails, user names in paths and secrets from text; used by error messages returned to the UI, JSON logs and Report a problem.
- **FD-131b (local data).** Retention for previews, staged tables and downloads (`local_retention_days`, #95; proof sandboxes live in OneLake and have the Prove program's own delete); encrypt the token cache with DPAPI on Windows through `msal-extensions` (a new dependency, so a decision first).

### FD-136 First-run experience (P1)

- **Acceptance criteria:** a readiness checklist on Home (engine, sign-in, Fabric reachable, a workspace with capacity, OneLake write) each with a fix; reuses FD-101 checks in read-only form. Mockup first.

### FD-137 Opt-in telemetry (P1)

- **Decision T1 (Chris):** where telemetry goes (Azure Monitor Application Insights in a KratosBI subscription, or none). Until then, nothing is built. Defaults: off, policy can force off, no customer data, the payload is shown before sending.

### FD-138 Diagnostic support bundles (P1)

- **Acceptance criteria:** Report a problem redacts paths, emails, GUIDs and tokens before copying; a "Save support bundle" writes a zip with version, runtime, redacted logs, a settings summary and recent errors, and lists what's inside before saving.

### FD-139 Accessibility (P1)

- **Acceptance criteria:** focus returns to the opener when a dialog closes, Tab stays inside an open dialog, a visible focus ring on every focusable element, a skip link, reduced-motion support, and an automated check for missing labels in `app/tests/ui.test.cjs`.

### FD-140 Pilot readiness (P0)

- **Acceptance criteria:** [release-readiness.md](release-readiness.md) with a supported capability matrix, the security architecture summary, operational requirements, known limitations, validated scenarios (from FD-101 results) and a pilot acceptance checklist.

## Gaps sent to other programs

| Gap | Sent to | Date |
| --- | --- | --- |
| The older Side by side (`side_by_side.py`) writes real tables twice with no overwrite protection (review item 6) | Prove | 2026-10-17 |
| The shell's `rpc` call has no timeout behind one mutex, so a hung engine call freezes the UI (review item 4) | Prove (kernel isolation) | 2026-10-17 |
