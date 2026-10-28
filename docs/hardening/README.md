# Enterprise hardening audit

An evidence-based review of KratosForge's code, done to make it demonstrably enterprise-ready rather than look it. Reviewed at `main` **1248ccd** (2026-10-17, "Merge pull request #120").

| Document | What it holds |
| --- | --- |
| This page | Scorecard, executive assessment, implementation results |
| [Findings register](findings.md) | Every confirmed issue with file and line, evidence and fix |
| [Hardening backlog](backlog.md) | PR-sized work items HD-001 to HD-057 in priority and dependency order |
| [Readiness checklist](readiness-checklist.md) | Ten release gates with acceptance criteria and today's evidence |
| [Engine call limits](engine-call-limits.md) | Which engine calls can hold the app up, what bounds each, and which still need a design decision (PB-072) |

## How the review was done

- Every major component was read at the commit above: the Tauri shell and updater, the 27 UI scripts, the Python engine's RPC surface, notebook kernels, the local API for AI tools, every OneLake write path, the Fabric client, the platform services, CI and release workflows.
- Claims were checked by running code, not by reading docs. The engine suite ran in full (1,345 passed, 2 skipped, 87% line coverage on Linux with Python 3.13; CI uses 3.12 on Windows and Linux). The UI suite ran (45 passed). The Rust watchdog tests were run by hand (5 passed).
- Defects were reproduced with scripts against local Delta tables (delta-rs 1.6.6, Polars 2.0.0), temporary folders and mocked HTTP. Each finding says whether it was reproduced or read from the code.
- Nothing was run against a Fabric tenant. No finding claims live Fabric behavior.
- **Re-checked at `main` 9715e28** (2026-10-17 22:54Z, after #118, #120 to #122, #124, #125, #127 to #130 and #132 to #134 merged). Every finding still applies at that commit. SQL-003 (#134) gave the SQL page paged, cancellable execution with a memory limit, which covers the desktop half of DI-5 and SH-11; the external-tool SQL path (EN-4) and entity previews are unchanged.

## A. Scorecard

Scores are 0 to 10. The preliminary external scores are shown for comparison; they were treated as hypotheses.

| Area | Weight | Preliminary | Evidence-based | Evidence |
| --- | --- | --- | --- | --- |
| Architecture | 10% | 8.0 | **7.0** | Clean three-part split (webview, Rust shell, Python engine over stdio) with a method allowlist and a local-first design. Held back by one shared mutable "attached lakehouse" global that lets a run be re-pointed (EP-1), a single serial RPC pipe with no limit on long calls (SH-11), and three overlapping write paths with different guards (DI-3, EP-9) |
| Code organization | 5% | 7.5 | **6.5** | Modules are mostly cohesive and named well. `Service` is 18 mixins with 335 public methods and 348 `type: ignore` (AR-2); `app.js` is 2,521 lines sharing one `state`; seven HTTP clients with separate retry logic (AR-3) |
| Local execution design | 10% | 8.0 | **7.0** | DuckDB with external access locked for AI-tool SQL, memory preflight on reads, kernels in their own process, batched loads in one commit. But SQL timeouts don't interrupt (EN-4), previews materialize whole tables (DI-5), Stop leaves child processes (EN-8), and local `saveAsTable` accepts schema changes Fabric refuses (DI-8) |
| Security | 20% | 6.0 | **6.0** | Strong: no exploitable UI injection found, a carefully guarded loopback API, hashed tokens, single-use approvals, SHA-pinned CI, SBOMs, provenance, dependency scanning. Weak: no CSP with the Tauri API on the page (SH-1), unsigned releases publish silently (SH-2), plaintext token cache readable by AI-tool code (EN-2, EN-10), run-notebook approval not bound to code (EN-1), policy fails open (EP-4) |
| Reliability | 20% | 6.5 | **5.0** | Delta optimistic concurrency and key assignment hold up under concurrent writers (verified). But three High write-path defects: duplicate rows after a post-commit failure reported as success (DI-1), committed data reported as failed (DI-2), recursive deletes of managed tables (DI-3); restore brings back post-backup data (EP-2); local leases can have two holders (EN-6) |
| Testing | 15% | 6.0 | **6.5** | 1,345 engine tests, 87% coverage, clean fakes for clocks, sessions and sleeps, many concurrency tests. But Rust tests and browser tests never run in CI (SH-6, UI-8), no post-commit failure-injection tests, 53 to 59% coverage in OneLake write and delete modules (TS-1), no live or T-SQL tests (TS-2) |
| Enterprise readiness | 15% | 5.0 | **4.0** | Policies, audit, backup, support bundles, telemetry opt-in and a guarded live harness all exist. But nothing is signed, nothing is live-verified, there is no rollback or build pinning, Stable can install a Preview engine (SH-4), and restore, policy and audit each have a confirmed defect (gates 4 to 9 in the [checklist](readiness-checklist.md)) |
| Documentation | 5% | 9.0 | **7.0** | Broad and well organized, with honest "untested" labels. But four security statements are false at this commit (no network listener; AI tools never get a token; approval shows the code; redaction missing), and the readiness page lists merged work as open (DOC-1) |
| **Overall (weighted)** | | 7.0 | **5.9** | |

Weighted overall: 0.10×7.0 + 0.05×6.5 + 0.10×7.0 + 0.20×6.0 + 0.20×5.0 + 0.15×6.5 + 0.15×4.0 + 0.05×7.0 = 5.85. Security and reliability carry the most weight because the product writes to shared enterprise data.

## B. Executive assessment

**The strongest decisions.** The engine is a child process on pipes with a method allowlist, so the attack surface is small and the UI can't be reached from the network. Overwrites commit against the version they read, which turns concurrent-writer races into retries instead of lost data, and business-key assignment held with six concurrent writers. The local API for AI tools was built carefully: loopback only, DNS-rebinding and browser-request checks, hashed tokens, approvals bound to exact arguments, and no write-to-OneLake capability at all. CI is pinned by SHA, scans dependencies, produces SBOMs and provenance, and keeps signing secrets away from pull requests. The UI's single `h()` builder makes injection hard by construction.

**The biggest risks.**

1. **Silent wrong data in OneLake.** A retry after a failure that happens after the table commit writes the rows again and reports success (DI-1). Quarantine and the Delta profile check have the same ordering problem (DI-4, DI-2). For a product whose purpose is writing to shared lakehouses, this is the top risk.
2. **Destructive notebook helpers.** `notebookutils.fs.rm` deletes OneLake folders recursively even without `recurse=True`, including managed `Tables/`, and ignores the guard that external-tool runs rely on (DI-3). A notebook that is safe in Fabric deletes data when run locally.
3. **Cross-environment writes.** A running Dev cell can be re-pointed at a Prod lakehouse by a refused second start or an AI tool's run (EP-1).
4. **Distribution trust.** Builds aren't signed, the release job publishes unsigned without failing, and the client checks integrity but not authenticity (SH-2). A Stable PC can install a Preview engine after one network error (SH-4).
5. **Tokens on disk.** The token cache is plaintext and any code run through an AI tool can read it, while the docs say tools never receive a token (EN-2, EN-10).
6. **Recovery that doesn't recover.** Restore can bring back post-backup data (EP-2), and there's no way to roll back a bad build (gate 6).

**Enterprise readiness today.** KratosForge is a capable, well-tested local development tool, and the right building blocks for enterprise use exist. It is not ready for an enterprise pilot: five High data and approval defects need fixing (HD-001 to HD-006, all small and unblocked), signing and a live harness run need Chris (gates 3 and 4), and the release path needs rollback and authenticity checks. None of this needs a rewrite. The preliminary 7.0 overall was optimistic mainly on reliability and documentation; the 9.0 target is reachable through the P0 and P1 backlog plus live verification, not through more features.

**The ten highest-impact issues**, and whether existing work covers them:

| # | Finding | Covered by existing work? |
| --- | --- | --- |
| 1 | DI-1 duplicate rows after a post-commit failure | No |
| 2 | DI-3 `fs.rm` recursive delete of managed tables | No |
| 3 | EP-1 running cell re-pointed to another lakehouse | No |
| 4 | DI-2 committed data reported as failed | No |
| 5 | EP-2 restore brings back post-backup data | No (FD-126 built the feature; the defect is new) |
| 6 | EN-1 run-notebook approval not bound to the code | No |
| 7 | SH-2 unsigned, unauthenticated releases | Partly: signing steps built (PR #28), blocked on the Trusted Signing account |
| 8 | SH-1 no CSP, Tauri API on the page | Planned as FD-102b, not started |
| 9 | EP-4 policy fails open | No |
| 10 | EN-2, EN-10 tokens readable on disk and by AI-tool code | Partly: DPAPI cache proposed as FD-131b, awaiting decision D-C2 |

## F. Implementation results

Status at `main` 4b72e92 (2026-10-18 09:28Z). All eleven code fixes below were merged on Chris's go on 2026-10-18, in priority order, after a full engine and UI test run on all of them merged together (that run found one test in #180 that assumed no retry; it was fixed before #180 merged). Each PR was built on its own branch from `main` with a test that fails on `main` and passes with the fix, and each was opened only after the full engine suite passed locally. Every PR had all required checks green on its last commit before merging; the `review` (Codex) check is excluded because it fails on every PR until the OpenAI quota is topped up. Nothing here was run against a live Fabric tenant.

| PR | Backlog | Finding | What changed | State |
| --- | --- | --- | --- | --- |
| #137 | HD-009 | EN-7, DOC-1 | Audit documents; security, permissions and release-readiness pages corrected | **Merged** 2026-10-18 |
| #138 | HD-001 | DI-1, DI-4 | No table write after the commit; quarantine written after the commit; a retry finds the committed run in the table's commit metadata instead of writing again | **Merged** 2026-10-18 |
| #152 | HD-002 | DI-2 | Delta profile checked before writing, so committed data is never reported as failed | **Merged** 2026-10-18 |
| #154 | HD-003 | DI-3 | `notebookutils.fs` deletes need `recurse=True` like Fabric, refuse managed `Tables/`, and honour the external-run write guard | **Merged** 2026-10-18 |
| #162 | HD-004 | EP-1 | A running cell keeps its lakehouse; a second start or an external-tool run can't re-point it, and waits at most 10 s instead of freezing the engine | **Merged** 2026-10-18 |
| #155 | HD-005 | EP-2 | Restore removes stale SQLite WAL files, refuses databases in use and verifies each file's SHA-256 | **Merged** 2026-10-18 |
| #156 | HD-006 | EN-1 | Run-notebook approvals show the cells and are bound to their exact content; notebooks over 20,000 characters are refused | **Merged** 2026-10-18 |
| #157 | HD-007 | EN-5 | Log fields are redacted before JSON encoding | **Merged** 2026-10-18 |
| #158 | HD-008 | SH-3, SH-4 | The shell requires a checksum and falls back to another channel only on a 404, never on a network error. The Rust tests for this don't run in CI yet (HD-011) | **Merged** 2026-10-18 |
| #159 | HD-016 | EN-6, AR-1 | Leases are atomic across processes and a lost lease stops the write | **Merged** 2026-10-18 |
| #179 | QA-006 | (quality review) | Fabric 429 and 503 responses are retried, honouring Retry-After (GET only for 5xx); one retry rule shared by capacity, workloads and integrations | **Merged** 2026-10-18 |
| #180 | QA-003, QA-004, QA-007, QA-010 | (quality review) | Vacuum, Optimize, semantic model builds and lakehouse reads stop with the reason when a lookup fails instead of guessing; replacing a model asks first; X-ray marks a finding fixed only when its Fabric job completes | **Merged** 2026-10-18 |
| #165 | HD-010 | EP-4 | Policy "last known good" (Chris's decision). Built by the Enterprise readiness thread | Open, owned there |

**Not started, and why.**

- **Need Chris.** These can't be built or verified from here:
  - HD-013 token cache encryption (decision D-C2).
  - Code signing (Trusted Signing account, gate 3).
  - A live tenant run of the harness (gate 4).
- **Next in this thread, one PR at a time:**
  - HD-011: `cargo test` in CI. This is a workflow change, so Chris reviews it.
  - The `updater.py` checksum half of HD-008.
  - HD-012, HD-014 and HD-015.
- **Owned by other threads:**
  - HD-017 and QA-012: Enterprise readiness.
  - HD-035, HD-037 and HD-040: Prove.
- **Waiting on other PRs:**
  - UX-018 dangerous-action confirmations wait on #171.
  - UX-021 Preview tag on Create workspace waits on #169.

## G. Revised rating

**Overall: 5.9 → 6.5** (re-scored 2026-10-18 after the eleven fixes merged). A score moves only for fixes merged with CI green; nothing is credited for live behaviour, because nothing has been run on a live tenant.

| Area | Weight | Audit | Now | Why it moved, and what holds it back |
| --- | --- | --- | --- | --- |
| Architecture | 10% | 7.0 | **7.5** | EP-1 fixed (#162): a running cell keeps its lakehouse. `Service` size (AR-2) and the serial RPC pipe (SH-11) unchanged |
| Code organization | 5% | 6.5 | 6.5 | One retry rule now shared (#179); the rest unchanged |
| Local execution design | 10% | 7.0 | 7.0 | EN-4, EN-8, DI-5 and DI-8 still open |
| Security | 20% | 6.0 | **6.5** | EN-1 (#156), EN-5 (#157), SH-3 and SH-4 (#158) fixed. No CSP (SH-1), unsigned builds (SH-2), tokens readable by AI-tool code (EN-2, EN-10) and policy fail-open (EP-4, #165 open) remain |
| Reliability | 20% | 5.0 | **6.5** | DI-1 to DI-4 (#138, #152, #154), EP-2 (#155), EN-6 (#159) fixed; failed lookups stop instead of guessing (#180); Fabric 429/503 retried (#179). Not verified on a live tenant |
| Testing | 15% | 6.5 | **7.0** | Failure-injection tests for post-commit, lookup and retry failures. Rust and browser tests still don't run in CI (SH-6, UI-8) |
| Enterprise readiness | 15% | 4.0 | **4.5** | Restore and update-channel defects fixed. Still no signing, live harness run, rollback or build pinning; policy and audit defects open |
| Documentation | 5% | 7.0 | **8.0** | #137 corrected the false security statements and the stale readiness page |
| **Overall (weighted)** | | 5.9 | **6.5** | 0.10×7.5 + 0.05×6.5 + 0.10×7.0 + 0.20×6.5 + 0.20×6.5 + 0.15×7.0 + 0.15×4.5 + 0.05×8.0 = 6.50 |

6.5 is not ready for a 5,000-person rollout. Reliability and enterprise readiness can't pass 8 without the live harness run and signed releases (gates 3 and 4), and both need Chris. Next re-score after the next merge round, counting only what merged with CI green.
