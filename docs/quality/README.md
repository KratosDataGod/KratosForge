# Engineering quality baseline

An independent review of whether Fabric Desktop by KratosBI is built like one coherent product: no duplicated services, no functionality that only looks real, no errors that disappear, no tests that prove only their own fakes. Reviewed at `main` **3bee0db** (2026-10-18, "Merge pull request #136"); line numbers refer to that commit.

This review builds on two earlier ones and doesn't repeat them:

- The [enterprise hardening audit](https://github.com/KratosDataGod/fabric-desktop/pull/137) (`docs/hardening/`, findings DI-, EP-, EN-, SH-, AR-, TS-, backlog HD-001 to HD-057) covers security, OneLake write integrity, distribution and reliability.
- The [UX and simplicity audit](https://github.com/KratosDataGod/fabric-desktop/pull/166) (`docs/ux/`, backlog UX-001 to UX-020) covers workflows, navigation, configuration and documentation coverage.

Where a finding here overlaps theirs, it cites their ID and adds only what is new.

| Document | What it holds |
| --- | --- |
| This page | Scorecard, release decision, top 20 concerns, test evidence, technical debt |
| [Architecture and standards](architecture.md) | How the app is actually built, the conventions the code follows, and where they contradict each other |
| [Findings register](findings.md) | Every finding QA-001 to QA-030 with file and line, evidence, impact, fix and how to verify it |
| [Remediation plan](remediation.md) | PR-QA-01 to PR-QA-14: independently executable pull-request specifications |
| [PR quality gate](pr-review.md) | How every pull request is scored, the automatic failures, and the report format given to Release management |

## How the review was done

- The engine (157 Python modules, 41,000 lines), the 29 UI scripts (14,400 lines) and the shell were read at the commit above. Four areas were searched exhaustively: duplication and competing patterns, fake or incomplete functionality and hidden failures, dead code and UI consistency, and test quality and layering.
- Counts come from `git grep`, the Python syntax tree and coverage data, not from estimates. Dead-code candidates came from `vulture` and were each checked against the UI, tests, docs, the RPC allowlist and the shell.
- Defects marked **Reproduced** were shown with a script against the real engine code (fakes only at the network edge). Scripts are in each finding.
- Every test suite that can run on Linux was run. Nothing was run against a Fabric tenant, so no finding claims live Fabric behavior.

## Test evidence

| Suite | Result | Notes |
| --- | --- | --- |
| Engine (`sidecar/`, `python -m pytest`) | **Ran: 1,405 passed, 2 skipped, 0 failed** in 503 s | Python 3.13.16 on Linux; CI uses 3.12. Skips: DuckDB's TPC-H extension can't install offline. 87% line coverage (25,597 statements). 369 `ResourceWarning`s |
| UI (`node --test app/tests/ui.test.cjs`) | **Ran: 51 passed, 0 failed** | 39 tests execute extracted functions; 12 only match source text (QA-018) |
| Browser tests (`app/tests/browser/`) | Not run | Not in CI either (UI-8 / HD-052) |
| Rust tests (`cargo test`) | Not run | Not run in this assessment. CI's `shell` job runs them on Windows since HD-011 (SH-6) |
| Live tenant (`fabric-desktop live-test`) | Not run | Never run anywhere automatically (TS-2 / HD-054) |
| Defect reproductions | **Ran: 3 reproduced** | QA-001, QA-006, QA-016 |

What is **not verified at all**: any Fabric REST call or OneLake write against a real service; 22 of the 41 `FabricClient` methods, which never execute even against a fake (QA-018); the Windows-only sign-in broker; the shipped engine build.

## Scorecard

Scores are 0 to 10, weighted as the quality brief sets out.

| Category | Weight | Score | Evidence |
| --- | --- | --- | --- |
| Functional correctness | 20% | **5.5** | The suite is green and broad, but three defects lose or overwrite data while reporting success: Spark writer options are dropped so `replaceWhere` replaces the whole table (QA-001, reproduced), a failed notebook save shows "Saved" and closing discards the edits (QA-002), VACUUM falls back to 7 days of history on any error (QA-003). A semantic model can be overwritten without a prompt (QA-004) |
| Architectural consistency | 15% | **5.5** | The three-process split (webview, Rust shell, Python engine over stdio with an allowlist) is sound and used everywhere. Inside the engine, cross-cutting concerns are re-solved per feature: 9 atomic-write copies and ~16 non-atomic writes of shared files (QA-011), 6 SQLite idioms (QA-022), 5 disagreeing "is this transient" rules (QA-006), 23 `FabricClient` construction sites (QA-014), 3 hand-kept method lists that 16 of 36 open PRs edit (QA-015) |
| Code quality and maintainability | 15% | **6.5** | Genuinely clean in places AI-built code usually isn't: no TODO/FIXME, no commented-out code, no unused JS function or CSS class, no unimported module, every Preview flag read. Held back by `Service` (18 mixins, 347 public methods, 385 `type: ignore`, QA-014) and 237 broad `except Exception` handlers, 98 of them silent (QA-013) |
| UX consistency and simplicity | 15% | **5.5** | The UX audit scored simplicity 64/100. Component level: destructive confirmations use the primary button (17 calls), 13 confirm-dialog copies, the Domains screen built twice, 11 hand-built tab bars with ARIA on one, 10 status-pill helpers, 4 error-handling styles with 48 silent catches (QA-019) |
| Testing and verification | 15% | **6.0** | 1,405 engine tests, good hand-written HTTP fakes, 87% coverage. But nothing has ever run against Fabric, 22 Fabric client methods (8 of them writes) never execute even against a fake, OneLake upload and delete never run, `FakeFabric` is re-defined 7 times, Rust and browser tests aren't in CI (QA-018) |
| Security | 10% | **6.0** | Accepts the hardening audit's 6.0. Adds: governance checks fail open when the platform can't be reached (QA-005), and the shared redactor lets a Git personal access token in a URL through (QA-016, reproduced) |
| Performance and efficiency | 5% | **6.5** | Local SQL is streamed, capped and cancellable. But a new `requests.Session` per client construction (23 sites), about 15 independent UI poll loops from 300 ms to 10 min (QA-019), and one serial RPC pipe (SH-11) |
| Documentation | 5% | **6.5** | Broad and mostly honest about Preview. But "no Fabric capacity is used" and a "measured" 0 CU-s contradict the project's own cost evidence page, which says OneLake reads and writes from this PC use capacity (QA-008); the README offers local data projects that have no screen (QA-017) |
| **Weighted overall** | | **5.9** | 0.20×5.5 + 0.15×5.5 + 0.15×6.5 + 0.15×5.5 + 0.15×6.0 + 0.10×6.0 + 0.05×6.5 + 0.05×6.5 = 5.88 |

## Release decision

**FAIL** for main at 3bee0db, on two counts that no score can override:

1. **Known data-loss defect** (QA-001): `df.write.mode("overwrite").option("replaceWhere", ...).saveAsTable(...)`, a common Fabric pattern, replaces the entire OneLake table instead of one slice, and prints a successful write.
2. **Success reported after failure** (QA-002): a failed notebook save shows "Saved", and closing or switching notebooks then drops the unsaved cells.

Both are small to fix (PR-QA-01, PR-QA-02). With them fixed and the hardening audit's P0 items merged, the score would still sit below 8.0 until the High findings QA-003 to QA-007 are closed, so the next release would be a **CONDITIONAL PASS** at best.

## Top 20 engineering quality concerns

| # | ID | Sev | Concern | Evidence | Owner |
| --- | --- | --- | --- | --- | --- |
| 1 | QA-001 | Critical | Spark writer `option()`s are stored and dropped; `replaceWhere` and dynamic partition overwrite replace the whole table | Reproduced; `spark_local.py:54-61,79-86` | Prove (notebook compatibility) |
| 2 | QA-002 | High | Failed notebook save shows "Saved"; close and switch discard edits | `app.js:1923-1932,2430,262` | Prove (notebooks) |
| 3 | QA-003 | High | VACUUM drops the layer's Keep history days on any error | `xray_service.py:185-191,199-203` | Hardening (X-ray) |
| 4 | QA-004 | High | Create model overwrites an existing model when the lookup fails; failed table reads are shown as "not written yet" | `cloud_service.py:469-474`, `fabric_api.py:254-263` | Hardening (metadata Fabric hand-off) |
| 5 | QA-005 | High | Shortcut rules and access review treat a failed call as "nothing to enforce" / "no readers" | `shortcut_service.py:255-258`, `estate_service.py:456-459` | Governance |
| 6 | QA-006 | High | Fabric 503 and 429 aren't retryable to the agent; five classifiers disagree | Reproduced; `retrying.py:20-23`, `fabric_api.py:33-37` | Hardening (HD-051) |
| 7 | QA-007 | Medium | A failed lakehouse lookup guesses "schemas on", so a write lands at `Tables/dbo/x` in a flat lakehouse | `service.py:584-592` | Hardening |
| 8 | QA-008 | Medium | "0 CU-s, measured" and "no Fabric capacity used" contradict OneLake transaction billing | `scorecard.py:83-84`, `docs/finops/cost-evidence.md:23` | FinOps with Prove |
| 9 | QA-009 | Medium | `notebookutils.notebook.runMultiple` ignores args, dependencies and timeouts | `notebookutils_local.py:247-249` | Prove |
| 10 | QA-010 | Medium | X-ray marks a finding fixed when the job is submitted, and hides it forever | `xray_service.py:217,247,319-320` | Hardening (X-ray) |
| 11 | QA-011 | Medium | `settings.json` and `schedules.json` are rewritten non-atomically; a torn file reads as `{}` and the next save drops every other key | `meta_service.py:59-66`, `local_schedules.py:194-200` | Hardening |
| 12 | QA-012 | Medium | Two audit-chain writers; the app's keeps the chain head in memory across processes | `audit.py:105,137-144` vs `integrations/audit.py:128` | Enterprise readiness (extends HD-017) |
| 13 | QA-013 | Medium | 237 broad exception handlers, 98 silent; "not configured" and "failed" are the same branch | AST count; e.g. `schedule_service.py:213-233` | Every owner, starting with PR-QA-07 |
| 14 | QA-014 | Medium | `Service` grew to 347 public methods and 385 `type: ignore`; 23 `FabricClient` and 20 `FileStore` construction sites; private `auth._token` used 12 times; raw `requests` for a permission write | `service.py:55`, `estate_service.py:355` | Hardening (extends HD-050/051) |
| 15 | QA-015 | Medium | Three hand-kept method lists (`server.METHODS`, `audit.READ_ONLY`, UI tests) make every feature PR conflict | 16 of 36 open PRs edit `audit.py`, 15 edit `server.py` | Release management with hardening |
| 16 | QA-016 | Medium | Four redaction functions; the shared one passes `https://user:token@host` through | Reproduced; `redact.py:21-43` vs `git/secrets.py:142-153` | Enterprise readiness |
| 17 | QA-017 | Medium | 17 callable RPC methods have no screen, including `delete_notebook` and `platform_revoke`; 15 local-data methods are documented as usable | `server.py:20-90`, `README.md:40` | Each owner; Local data engine |
| 18 | QA-018 | Medium | Tests prove fakes: 22 of 41 Fabric client methods never run, OneLake upload/delete never run, 7 copies of `FakeFabric`, 12 UI tests match text | Coverage data; `test_*.py` | Hardening (extends TS-1/TS-2) |
| 19 | QA-019 | Medium | UI components re-implemented per page: confirm, tabs, pills, grids, polling, error handling | `meta.js:411-415` and 13 copies | UX oversight (extends UX-011/018/020) |
| 20 | QA-020 | Medium | Untested Fabric-writing actions (Promote, Publish, Create workspace, Sync keys, variable library, replay) carry no Preview tag | `features.js:37-63` | UX oversight with owners |

Ten more findings (QA-021 to QA-030) are in the [register](findings.md).

## Critical release blockers

| Blocker | Why it blocks | Fix |
| --- | --- | --- |
| QA-001 | Known data loss in OneLake from ordinary notebook code | PR-QA-01 |
| QA-002 | Success reported after a failure, then edits lost | PR-QA-02 |
| Hardening P0 items (HD-001 to HD-006) | Already tracked by the hardening audit's gates | PRs #138, #152, #154 to #156, #162 |

## Architectural consolidation opportunities

Each is a small shared helper replacing copies, not a rewrite. Details in [architecture.md](architecture.md#consolidation-plan).

1. One `fsutil` with atomic JSON write and a corrupt-file-aside read (replaces ~25 write sites, QA-011).
2. One transient-error rule in `retrying.py`, imported by the other four (QA-006).
3. One `sqlite_util.connect()` and `tx()` (QA-022).
4. One audit chain writer used by both audit logs (QA-012).
5. One redactor that includes the Git secret patterns (QA-016).
6. One public `Credential.token(scopes)` and a client factory on `Service`, instead of 23 constructions and `_token` (QA-014).
7. Method metadata declared once, next to the method, generating `METHODS` and `READ_ONLY` (QA-015).
8. One `BackgroundRuns` helper for Prove and side by side (QA-023).
9. UI: `confirmDialog({danger})`, `tabs()`, `statusPill()`, `resultGrid()`, `poll()` in shared code (QA-019).

## UX consistency concerns

The UX audit owns workflows and navigation. At component level this review found: destructive confirmations styled as the primary action; 13 confirm helpers with two body-text classes; the Domains table built twice with different buttons and empty states; 11 tab bars and 13 segmented controls with ARIA on one tab bar and six buttons; three menu implementations, the shared one with no keyboard support; five result grids that show NULL three different ways; seven date and duration formatters ("1m 5s" and "1 min 5 s"); 208 hard-coded colors beside 16 tokens and two amber "warning" pills. All are in QA-019 and PR-QA-11 to PR-QA-13.

## Technical debt assessment

**Low where AI-built code is usually worst.** No placeholder screens, no simulated progress, no mock responses in shipped code, no "coming soon" stubs, no unused functions in the UI, and every RPC name the UI calls exists. Fakes are confined to tests. That deserves saying plainly.

**High where features were built in parallel.** The debt is repetition of infrastructure, and it is growing with each feature thread: every new area adds its own JSON store, its own `_now()`, its own background-run loop, its own confirm dialog, and three more lines in three shared lists. The copies have already drifted in ways that matter (an atomic write in Prove that side by side lacks, `synchronous=full` in one SQLite store and not its twin, a retry rule that reads the wrong attribute). The biggest single risk pattern is the silent fallback: 98 handlers turn any failure into "nothing configured", which is how QA-003 to QA-007 happen.

**Trend.** Between the hardening audit (1248ccd) and this one, `Service` grew from 335 to 347 public methods and `type: ignore` from 348 to 385. Nine open PRs add new services or mixins. Without PR-QA-05 to PR-QA-09 the pattern costs keep rising per feature.

## What happens next

- Findings go to their owners through the coordinator; this thread doesn't change code in another thread's area.
- Backlog IDs QA-001 to QA-030 go to the product management thread.
- Every PR gets a PASS / CONDITIONAL PASS / FAIL decision in the [PR quality gate](pr-review.md) format, sent to Release management.
- The baseline is re-scored after each merge round, with a row added below.

| Date | Commit | Overall | Decision | Note |
| --- | --- | --- | --- | --- |
| 2026-10-18 | 3bee0db | 5.9 | FAIL | Baseline; QA-001 and QA-002 block |
