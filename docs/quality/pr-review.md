# PR quality gate

How every pull request to Fabric Desktop by KratosBI gets an engineering quality decision. It sits beside the hardening release gates (`docs/hardening/readiness-checklist.md`, PR #137) and the usability gates (`docs/ux/release-review.md`, PR #166) and doesn't repeat them: those ask "is it safe" and "is it usable"; this asks "does this code deserve to exist in this form". Release management decides merges and promotes; this review recommends, and never merges or deploys.

## Scoring

Each category is scored 0 to 10 from evidence in the diff, the tests and the [standards](architecture.md#standards-the-code-follows).

| Category | Weight | A 9 looks like | A 5 looks like |
| --- | --- | --- | --- |
| Functional correctness | 20% | Does what it says on every path, including failures; nothing reports success after a failure | Happy path works; some failure path guesses, or reports success |
| Architectural consistency | 15% | Uses the existing helper for each concern (client factory, `fsutil`, `sqlite_util`, `retrying`, `redact`, `timeutil`, `confirmDialog`, `attempt`) | Adds a new copy of something the repo already has |
| Code quality and maintainability | 15% | Small, named for what it does, no dead code, no broad silent `except` | Long functions, silent handlers, unexplained `type: ignore` |
| UX consistency and simplicity | 15% | Uses shared components, fits the navigation budget, no new configuration | Bespoke dialog or tab bar, a new setting that could be automatic |
| Testing and verification | 15% | A failing-first test for every behavior change, failure paths tested, test names match what they prove | Tests only assert what a fake was told to return |
| Security | 10% | Hardening gates hold; no new scope without `docs/permissions.md` | Unreviewed token, permission or file handling |
| Performance and efficiency | 5% | No new polling or repeated remote calls; local work stays local | New loop or repeated lookup with no limit |
| Documentation | 5% | The doc section exists, matches behavior and says what is local, compatible, simulated, remote or unsupported | Feature names repeated without behavior |

**Decision:** 9.0 and up PASS; 8.0 to 8.9 CONDITIONAL PASS (named follow-up, verified by Release management before the stated point); below 8.0 FAIL.

## Automatic failures

Any one of these is FAIL whatever the score, and can't be conditional:

1. A critical security vulnerability.
2. A known data-loss defect, including an overwrite or delete wider than the user asked for.
3. Functionality presented as working that isn't: an ignored argument, a hard-coded "measured" value, a success message after a failure.
4. A claim of test success that wasn't run.
5. A broken critical workflow.
6. A material architecture change without agreement (a new process, framework, store format or pattern for a concern the repo already handles).
7. Missing authorization control on a write.
8. A known high-severity regression.

## Investigation triggers

These start a closer look; they aren't failures on their own.

| In the diff | Check |
| --- | --- |
| `except Exception` | Does it tell "not set up" from "failed"? Does a failure reach the user? (QA-013) |
| `write_text(json.dumps(...))`, `sqlite3.connect`, `datetime.now`, `re.compile` of secret patterns | Use the shared helper (QA-011, QA-022, QA-024, QA-016) |
| `FabricClient(`, `FileStore(`, `._token(`, `requests.` | Use `Service.fabric` / `files` / `token` (QA-014) |
| A new name in `server.METHODS` | Is it called from the UI or tagged? Is `audit.READ_ONLY` right? (QA-015, QA-017) |
| A new `*_service.py` mixin | Could it be a module called by an existing mixin? |
| `showDialog` with a "…?" title, a `tab` button, a `pill-s` span, `setTimeout` loop | Use the shared UI helper (QA-019) |
| "no capacity", "identical to Fabric", "measured" | Is it verified on this execution path? (QA-008) |
| A test with `monkeypatch.setattr(svc, ...)` or a new `FakeFabric` | Does the test prove the code, or the fake? (QA-018) |
| A Fabric-writing button | Is it under a Preview tag? (QA-020) |

## Report format

One block per PR, pasted into the release record by Release management.

```
PR: #<number> <title>
Quality decision: PASS | CONDITIONAL PASS | FAIL
Score: <overall> (correctness x, architecture x, code x, UX x, tests x, security x, performance x, docs x)
Automatic failures: <none, or which and why>
Findings: <QA-id or new id, severity, file:line, one line each>
AI slop: <duplication, needless complexity, incomplete or simulated behavior, or none>
Required remediation: <what must change, or the condition for CONDITIONAL>
Tests: executed and passed | executed and failed | not executed | not verifiable without Fabric
Release decision: <merge now | merge after X | hold>
```

Every review says which tests were actually run in this review. "Static review only" when none were.

## Reviews

PR reviews start after this baseline merges and are added below, newest first.

### Merge set of 2026-10-18 (Release management's chain for 17:00Z)

Reviewed against `main` 52ff52d. Each PR was reviewed on its own commits only: chained PRs against the PR before them, and the cancel-race fix that appears in #169, #148 and #139 once, under #169. For every PR, the PR's own test files and `node --test app/tests/ui.test.cjs` were run on its head (Python 3.13, Linux). Every FAIL below was reproduced with a script on the PR head, and re-run independently. Nothing was run against Fabric.

| PR | Title | Decision | Score | Blocking finding or condition |
| --- | --- | --- | --- | --- |
| #210 | Retry the Windows Delta log race in local key tables (added to the set) | PASS | 9.1 | None. The race check and bounded retry live once in `retrying.py`; only repeat-safe `LocalStore` calls use them |
| #170 | Schedule dialog opens on Fabric when the environment runs on Fabric | PASS | 9.3 | None |
| #169 | Preview tags for promote, sync keys, variable library, replay | CONDITIONAL PASS; condition closed by #206 (PASS 9.2 at 8cf1d5d) | 8.2 | With Preview off, `previewMarked` (`features.js:90`) shows the four untested writes untagged, while Settings (`settings.js:246`) and `user-guide.md` say the switch hides them. Align the behavior or the text before the next Stable promote |
| #171 | Command bar, help links; `confirmDialog` danger mode | CONDITIONAL PASS | 8.9 | No caller passes `{danger}` yet; about 20 destructive confirmations are still primary. Describe it as "confirmDialog gains a danger mode" and track the call sites under UX-018 |
| #173 | Sign-in errors say what happened (UX-003) | CONDITIONAL PASS; condition closed by #209 (PASS 9.1 at 279f13c) | 8.7 | `attempt()` (`app.js:79`) is silent for any error with empty friendly text, not only a cancel (QA-013). Mark the cancel explicitly and test it |
| #142 | Prove: save failure replay bundles | CONDITIONAL PASS; fix at 4cfdb3a **FAIL**, then PASS (9.0) at 859b79d | 8.35 | Unticking IDs leaves workspace and lakehouse IDs in `abfss` URIs inside error text (`replay.py:115`) while the dialog says the bundle is safe to share. The fix at 4cfdb3a scrubs the JSON text rather than each string: a double-quoted OneLake address makes saving raise `JSONDecodeError`, and an address at the end of a line deletes the code after it. 859b79d scrubs each decoded string instead |
| #148 | Prove: open and replay failure bundles | **FAIL**, then PASS (9.0) at 2ff9b5c; held at 4cfdb3a with #142, PASS (9.0) at 859b79d | 7.35 | Bundle table names aren't validated (`prove_service.py:371-372`): `../../../../../../Tables/dbo.evil` with "Use the bundle's sample" writes a Delta table outside the replay sandbox. Also no cap on `bundle.json`'s uncompressed size |
| #139 | Local data: write Delta tables from SQL (FD-LDE-005) | **FAIL**, then PASS (9.1) at 36f076c | 7.75 | `_as_write` (`localdata/engine.py:600`) treats any `WITH` statement as a SELECT, so `write_delta(..., "WITH c AS (SELECT 1) DELETE FROM plain", dry_run=True)` deletes every row. A dry run must not mutate (AGENTS.md) |
| #150 | ORCH-002 activity compatibility registry | CONDITIONAL PASS, then PASS (9.0) at c7f443e | 8.6 | Control flow, Wait, Web and Webhook are marked `fabric_capacity="not_required"`, and a pipeline that only runs in Fabric reports "Needs Fabric capacity: None" (QA-008) |
| #153 | FINOPS-003 import measured CU | **FAIL**, then PASS (9.0) at 5dee656 | 7.1 | Rows are matched by time overlap ±300 s (`usage/reconcile.py:98`): an adjacent scheduled run of the same notebook is added, giving 10,200 "measured" CU-s for a 1,200 CU-s run. The docs say a row matches only when it can't belong to anything else |
| #167 | FINOPS-003b Cost Management import | **FAIL**, then PASS (9.0) at 04ca09a | 7.3 | Two overlapping month-to-date exports double count (`cost_management.py:113-116`, `usage/__init__.py:122`): 212 CU h / $38.16 against a true 192 / $34.56 |
| #174 | FINOPS-004 part 1 risk check | CONDITIONAL PASS, then PASS (9.0) at e052730 | 8.0 | "Blocked" doesn't stop the run and `require_acknowledgement` has no effect yet; say "reports only" in the docs, MCP description and payload, and hide the setting until it works. Waits for #153 and #167 |
| #194 | Remember a passed write test (#191) | **FAIL**, then PASS (9.0) at 7b1a552 | 6.7 | The remembered pass (`settings.js` `rememberWrite`) has no tenant, lakehouse or expiry, hides Retest, and isn't cleared by a refused test, so Home says "Ready to work" without checking (UX-014). The head also carries 14 other unmerged commits |
| #195 | Monitor: Schedules and Cost get the whole page (#190) | CONDITIONAL PASS | 8.2 | Reduce the head to its own two commits (it carries the #194 stack). Follow-up: the "None" environment tooltip is wrong for Fabric runs |
| #197 | Product records: unit-testing direction and incident management | PASS | 9.2 | None (docs only; `product_data.py` reports consistent) |
| #198 | Release readiness: what's missing for an enterprise-approved app | CONDITIONAL PASS, then PASS (9.1) at ca5db54 | 8.75 | Stable releases get no SBOMs (`promote.yml` uploads three files); #165, #181 and #182 are listed as open but merged; the open High security findings SH-1, SH-2 and EN-1 are left out |

Totals: 2 PASS, 8 CONDITIONAL PASS, 5 FAIL at first review. Follow-ups: #206 makes `previewMarked` always tag and names the always-shown steps in Settings and the user guide; #209 makes `attempt()` silent only for an explicitly cancelled sign-in, with a test that runs it on an empty error. For both, `node --test app/tests/ui.test.cjs` passed on the head (55 and 59 tests). All FAILs went to Release management through the coordinator, with the owner and fix.

What the set says about the baseline: the five FAILs are the patterns the [findings](findings.md) describe, appearing in new code. Unvalidated names building paths (QA-025), a heuristic labelled as measured (QA-008), success shown without checking (UX-014), and dry-run classification by text prefix. No PR reintroduced a silent overwrite, and every PR's new tests failed on its parent's code, which is the evidence the gate asks for.

### Outside the merge set

| PR | Title | Decision | Score | Blocking finding or condition |
| --- | --- | --- | --- | --- |
| #221 | X-ray toast wording (QA-008); Optimize drops its nested Delta log retry | PASS at 5e6a09c | 9.2 | None. `rows`, `create` and `insert_new` now retry once, in LocalStore; a test pins 6 tries. Note: `add_columns` still calls the wrapped `columns_of`, so its scan can retry 6 x 6. Merges cleanly with #219 |
| #219 | Monitor drops Cost; old links open Optimize > Cost and savings | PASS at 13bc7a1, carried to cf2479d and 495d0f5 (9.4) | 9.2 | None. Removes the "what running on this PC saved" overclaim (QA-008). cf2479d fixes the five stale Monitor > Cost doc lines and two QA-008 overclaims (`xray-fixes.md:89`, `azure-setup.md:114`) |
| #199 | Agents guide beside Monitor > Operations > Agents | PASS at cf08228 | 9.2 | None. Every guide link resolves to an `agent.md` heading (tested), and the text matches `agent.md` |
| #234 | A broken policy.json with no saved copy still lets people sign in | CONDITIONAL at b753cb9 | 8.6 | (1) The message in Settings > Help and about still says "no sign-in" (`app_settings.py:181`), and (2) `docs/permissions.md:125` still says a broken file "stops every sign-in". Both now state a security boundary that no longer exists, so IT would believe other tenants' accounts are kept out when they aren't. Fix both and assert the message in a test. The change itself is what Chris chose on the policy card ("Closed, allow sign-in", 2026-10-18): only `allowed_tenants` is exempt (`_restricted`, `app_settings.py:219`), every other restriction still fails closed, a last-good copy still applies in full, and a wrongly typed `allowed_tenants` still lets no account in. Tests run: policy, settings and auth 28/28 |
| #233 | Incidents stage 1b: every incident change copied to the central lakehouse (`fdinc_`) | PASS at 41b3b82, carried to 0316b9d (docs say nothing starts a copy until stage 2; ruff clean; `dt.UTC` fine on requires-python >=3.11; 140/140 re-run) | 9.1 | None. Setting up the tables goes through `central_family(FAMILY, approve)`: without approve it returns `needs_confirm` and writes nothing (tested); read-only access returns `refused`; `allow_optimize_lakehouse` off returns `off`. Copies are insert-only (`setup.write` → `insert_new`) with content-derived record ids, so a re-copy from another PC adds nothing; the live `incidents.json` and its conditional writes are untouched. `incidents_history` is read-only and in READ_ONLY; the sync is not. Writes stay in this family's tables. Known limits: nothing calls the sync yet (screens are stage 2), so the doc line "copied when the app copies them" should say so; `redact.secrets` misses a bare `token=…` (pre-existing in the shared redactor; JWTs, bearer, `access_token=`, `password=` are removed), and the PR's own test uses that input without asserting it; ruff reports 7 new auto-fixable style findings (not run in CI). Tests run: incident, optimize, audit 130/130 |
| #232 | Monitor > Workloads and Agents get "How this works" panels | PASS at 713f554 | 9.2 | None. UI only, no engine change. Every claim checked against code: results in `workloads.sqlite` (workload_service.py:44); Stale after an hour (`RUNS_STALE_AFTER=3600`, :28); "Not observed" is `model.HEALTH_LABELS["unknown"]`; the scan covers pinned, environment and notebook-publish workspaces only (`_schedule_workspaces(False, …)`, schedule_service.py:162), not the whole tenant; the list writes nothing. Agents panel says "changes: Nothing" and keeps the not-run-against-a-live-tenant note. UI tests 87/87, monitor-how browser 3/3 |
| #231 | X-ray Optimize asks first, with the risk check (QA-032) | PASS at 8044a7c, carried to 762da0b (main merged; ui.test.cjs conflict keeps both tests, checked with remerge-diff; X-ray panel and user guide now say all three writing buttons ask first; risk-check.md names the unrecorded go-ahead. Re-run: UI 88/88, browser optimize 5/5, risk 6/6, confirm-dialog 4/4, engine 33/33) | 9.2 | None. Closes QA-032 once merged. The confirm names the table, Fabric, capacity, V-Order and the vacuum history kept; Blocked disables Optimize; `xray_optimize_check` is read-only and in READ_ONLY; `allow_fabric_runs` is still enforced in the engine (`run_item_job`). Known limit, documented: going ahead past a warning isn't recorded |
| #230 | "How this works" on Estate X-ray and Workspaces > Fabric portal | PASS at dec8cf3 | 9.2 | None. Both panels match the code: Optimize starts at once, Vacuum previews then confirms, Adopt confirms and writes only local entity files; the portal sign-in is DPAPI-encrypted in `fabric-view-signin.bin`. The no-confirm Optimize it describes is already on main: QA-032 |
| #229 | Ready to work gets its "How this works" panel | PASS at 726e909 | 9.3 | None. The panel's write claim matches `readiness.write_test`: one file at `Files/_fabric_desktop_check/write-test.txt`, read back, folder deleted in `finally`, a workspace whose name matches prod/prd/production refused, only from the dialog's confirm button; the 7-day memory is `WRITE_PASS_DAYS` keyed to user and tenant and cleared on sign-out |
| #202 | UX-009: one list of source systems in the platform (schema 5) | PASS at 08d2dd2, carried to 1aecf1c | 9.1 | None. In Fabric the schema-5 change reaches the platform only through Finish or repair, which the admin starts from its dialog; Move sources asks first, and adopt_sources reports clashing codes and ids instead of changing them. Note: `list_sources` calls `ensure_source_ids`, so a read runs ALTER and UPDATE when the columns are missing (a local platform file, or a UDF already repaired); idempotent, but reads shouldn't write |
| #228 | Central lakehouse: table families (FabricDesktop_Central) | CONDITIONAL PASS at 50fce68, then PASS (9.2) at a267a70 | 8.8 | `central_family()` calls `setup.initialize(..., approve=True)` for whoever calls it, and that first upgrades Optimize's own tables when they aren't READY. `optimize_connect` changes the schema only with `initialize` (the person confirmed) (`optimize_service.py:93`). Take approval from the caller, or refuse when not READY. Note: every family's `default_config` goes to `fdopt_config` as `default:<key>`, so two families with the same key collide silently (insert-only skips the second); prefix the key with the family. Isolation itself holds: prefixes end in `_` and contain no `_`, so none is a prefix of another |
| #227 | Release chain of #219 #222 #203 #201 #223 #224 #225 #226 | PASS at 9a9bc9c | n/a | None. Every graded head is an ancestor; the conflict resolutions (`git show --remerge-diff`) are the ui.test.cjs union, both READ_ONLY lines and both mixins |
| #226 | Red delete confirms everywhere (finishes UX-018) | PASS at d5ca981 | 9.3 | None. The confirms still without danger only forget, stop applying or undo; none deletes data |
| #225 | Platform Overview and the "How this works" panel | CONDITIONAL PASS at 1d43af0, then PASS (9.2) at 99b0e1e | 8.9 | Overview's panel says "What it changes: Nothing", but the same tab has Approve, Decline and Withdraw on requests, and approving a shortcut request creates the shortcut in OneLake (`shortcut_service.py:385`). The panel's own rule in `ui-guidelines.md` says it must name such writes. Reword before merging |
| #224 | Home reuses its page for five minutes (#192) | CONDITIONAL PASS at 483a815, then PASS (9.2) at ae65f9a | 8.9 | `homeKey()` leaves out the metadata project, so after creating or opening a project in Data mesh, Home keeps showing the old project's environments, or Get started, for up to five minutes. Add `state.meta.project?.path` to the key |
| #223 | Engine messages name screens that exist (#193) | PASS at f315cd3 | 9.3 | None. Text only, with a test that checks every Data mesh and Platform path the engine names |
| #201 | Incidents stage 1: numbers, priorities, SLAs, reported incidents (engine only) | PASS at f877d8c | 9.2 | None. Writes stay conditional; `incident_report` and `incident_set_priority` are left out of READ_ONLY; older agents pass `evaluated` as a set without `reported`, so they never auto-resolve a reported incident. Note: an open incident acknowledged late shows `missed` but isn't counted in the board's at_risk or breached counts |
| #222 | Cost exports: the export reaching the later day wins; risk-check Data mesh sentence | PASS at cd78378 | 9.3 | None. Its regression test fails on main (232 vs 288 CU h) and passes here. My #153 note on `TOLERANCE_S` was wrong: `covered()` uses it (`reconcile.py:70`) |
| #203 | Prove templates gallery, 3 to 12 templates | CONDITIONAL PASS at 3c27809, then PASS (9.2) at e4e6022 | 8.9 | Load a folder writes the folder name raw into a Python string (`wizard.py`, the `assert paths` line): `Files/a"b` produces a notebook cell that does not parse, and a crafted name puts its own code in the notebook. Use `{folder!r}` as the other inserts do. Note: the quality template's range check raises on a text value (`cast(pl.Float64)` is strict) instead of rejecting the row |
| #218 | Run Fabric pipelines on this PC (ORCH-003, draft) | PASS at fdcd5cf, carried to b339a74 (9.3, both notes fixed) and 030c16d (main merge; conflicts with #227 on the Service mixin line only) | 9.1 | None. Notes, fixed in b339a74: expression functions have no size cap, so `@range(0, 500000000)` holds gigabytes in the shared engine before it fails (`evaluate.py:471`; cap near Fabric's 100,000 ForEach items); `finish()` waits 5 s and failed once on a cold first run here (`test_pipeline_runs.py:485`), worth 30 s for Windows CI |
| #185 | FinOps: risk check in the run dialogs (FINOPS-004 part 2) | CONDITIONAL PASS, then PASS (9.0) at e0ca2c8 | 8.5 | The risk panel and dialogs carry no Preview tag (`risk` isn't in `PREVIEW_FEATURES`) although `risk-check.md` calls it Preview and it reads capacity from Fabric. Tag it before merging |
| #146 | Workload operations: queue, shared schedules, takeover, Run as, Monitor tabs (folded stack, with #208) | PASS | 9.0 | None. Takeover and shared schedules are off by default, Preview, and decided by If-Match / If-None-Match writes |
| #216 | Runs > Env says why a Fabric job has no environment | PASS | 9.1 | None |
| #200 | Optimize page in the rail; Fabric portal becomes a Workspaces tab | CONDITIONAL PASS, then PASS (9.1) at e891403 | 8.9 | "What running on this PC saved" (`user-guide.md:275`) and "never shown as saved unless measured" (`optimize.js:99`) describe the avoided figure, which is a Scenario (`costs.py:76`). Reword before merging (QA-008) |
| #196 | Settings note when sign-in isn't Microsoft's own client | PASS | 9.2 | None |
| #215 | Keep webhook signatures out of freshness and agent alert errors (QA-031) | PASS | 9.2 | None. Closes QA-031 |
| #214 | Optimize uses the shared Windows Delta log retry (follow-up to #210) | PASS | 9.2 | None. Its own copy is removed; three wrapped calls now retry at two levels, still bounded |
| #213 | Keep punctuation after a scrubbed OneLake address (follow-up to #142) | PASS | 9.2 | None |
| #212 | Danger confirm for metadata deletes and quarantine discard (UX-018) | PASS | 9.1 | None. UX-018 stays open for the other call sites |
| #205 | Platform notices to Teams (engine) | CONDITIONAL PASS, then PASS (9.0) at 521bfef | 8.4 | The error text from an unreachable Teams held the webhook's `sig=` secret, which was returned and stored in the outbox. Fixed with `notify.failure`, which redacts it, for incident notices too. Still open on `main` (QA-031): `dataops_service.py:312` (freshness alerts) and `agent.py:286` (agent log) |
