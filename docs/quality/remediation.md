# Remediation plan

Fourteen pull requests that fix the [findings](findings.md), in priority order. Each one is independent unless **Depends on** says otherwise, small enough to review in one sitting, and owned by the thread that owns the code. Specs say what to change and how to prove it; the owner decides how.

Rules for every PR in this plan:

- Fix the finding; don't widen it. Anything else found on the way goes back to this register.
- Extend the implementation that already works rather than adding another one ([consolidation plan](architecture.md#consolidation-plan)).
- Every behavior change gets a test that fails before the change.
- No Fabric tenant is used. Where a fix can only be fully proven live, the PR says so and the item stays Preview.

| PR | Title | Fixes | Priority | Owner | Depends on |
| --- | --- | --- | --- | --- | --- |
| PR-QA-01 | Refuse Spark and notebookutils calls the shim can't honor | QA-001, QA-009, QA-026 | P0 | Prove | (#154 for QA-026) |
| PR-QA-02 | A failed notebook save never reports "Saved" or drops edits | QA-002 | P0 | Prove | |
| PR-QA-03 | Destructive Fabric actions stop when a lookup fails | QA-003, QA-004, QA-007, QA-010 | P1 | Hardening | |
| PR-QA-04 | Shortcut rules and access review fail closed | QA-005 | P1 | Governance | |
| PR-QA-05 | One rule for transient errors | QA-006 | P1 | Hardening | |
| PR-QA-06 | Shared file, SQLite, audit, redaction and time helpers | QA-011, QA-012, QA-016, QA-022, QA-024, QA-028 | P1 | Hardening, with Enterprise readiness for audit and redaction | |
| PR-QA-07 | Tell "not set up" from "the call failed" | QA-013, QA-021, QA-027 | P1 | Hardening, then each owner | PR-QA-06 |
| PR-QA-08 | One way to get a Fabric client and run background work | QA-014, QA-023, QA-025, QA-030 | P2 | Hardening | PR-QA-05 |
| PR-QA-09 | Declare each RPC method once; remove dead surface | QA-015, QA-017, QA-029 | P2 | Hardening with Release management | |
| PR-QA-10 | Say what runs where, and tag what's untested | QA-008, QA-017 (docs), QA-020 (UX-021) | P1 | Capacity wording: FinOps (own files, in #174), Prove, Local data engine, Governance and Hardening for theirs, UX oversight for README and top-level docs. Preview tags (UX-021): Prove, roadmap, Hardening | |
| PR-QA-11 | One confirmation dialog, red for destructive actions | QA-019 (now UX-018) | P1 | UX oversight, as UX-018; this thread reviews it | |
| PR-QA-12 | Accessible tabs, segmented controls and menus | QA-019 | P2 | UX oversight with Enterprise readiness (a11y) | |
| PR-QA-13 | Shared pills, result grid, time formats and polling | QA-019 | P2 | UX oversight | PR-QA-11 |
| PR-QA-14 | Tests that reach the Fabric client and OneLake I/O | QA-018 | P1 | Hardening | |

---

## PR-QA-01 Refuse Spark and notebookutils calls the shim can't honor

**Problem.** `df.write.option(...)` values are stored and dropped, so `replaceWhere` replaces the whole OneLake table (QA-001, reproduced). `runMultiple` ignores args and dependencies (QA-009). `fs.exists` says False on 401/403, `mkdirs` does nothing, `read.schema()` is ignored (QA-026).

**Existing implementation.** `sidecar/fabric_desktop/spark_local.py` (`LakehouseWriter` `:38-110`, reader `:118-140`); `notebookutils_local.py:117-180,245-249`; `lakehouse.write_table` (`lakehouse.py:232`); `docs/notebooks.md:60-80`.

**Required changes.**
1. An allow-list of writer options. Today only CSV `header` is honored. Any other option raises `NotImplementedError` naming the option and what to do instead.
2. `replaceWhere`: either implement it through delta-rs's predicate overwrite (`write_deltalake(..., predicate=...)`, after the profile check) or refuse it with the error above. `partitionOverwriteMode=dynamic` is refused.
3. `insertInto` appends (Spark's default), and refuses `overwrite=True` with a clear message if not supported.
4. `read.schema(...)` applies the schema (cast after read) or raises; it is never silently ignored.
5. `runMultiple` passes each activity's `args`, runs in dependency order, and records each activity's exception; a DAG using features not supported (timeouts, concurrency) raises.
6. After #154 merges: `fs.exists` returns False only on 404; `mkdirs` on OneLake creates the folder or says why it can't.

**Exclusions.** Partitioned writes (still a printed note); new Spark APIs; the Fabric-run path.

**Acceptance criteria.** The QA-001 reproduction raises (or writes only the matching slice) instead of replacing the table. No writer method accepts an argument it then ignores without saying so.

**Required tests.** One per refused option; `replaceWhere` against a local Delta table (if implemented) proving other rows survive; `insertInto` appends; `runMultiple` with a dependency and args; `exists` with 403.

**Documentation.** `docs/notebooks.md`: list the supported writer options and what is refused; `runMultiple` support.

**Definition of done.** Tests pass on CI; the notebooks guide matches the code; QA-001, QA-009, QA-026 marked fixed in the register with the PR number.

## PR-QA-02 A failed notebook save never reports "Saved" or drops edits

**Problem.** QA-002.

**Existing implementation.** `app/ui/app.js` `saveNotebook` (`:1923-1932`), Ctrl+S (`:2430`), tab close (`:262`), open and new (`:1895`, `:1903`); Copy handlers in `activity.js:155`, `dataops.js:150`, `meta.js:402`.

**Required changes.** `saveNotebook` returns true or false. Ctrl+S shows "Saved" only on true. Close, open and new stop when it returns false and keep the notebook open and dirty; the error toast stays. Copy handlers toast only after the copy promise resolves.

**Exclusions.** Autosave timing; conflict detection with files changed on disk.

**Acceptance criteria.** With `save_notebook` failing, no path shows "Saved", and no path replaces or closes the dirty notebook.

**Required tests.** `ui.test.cjs` (or a browser test) running `saveNotebook` with a rejecting `rpc`: returns false, no "Saved" toast, `state.notebook` unchanged after a close attempt.

**Documentation.** None beyond the PR description.

**Definition of done.** Tests pass; QA-002 marked fixed.

## PR-QA-03 Destructive Fabric actions stop when a lookup fails

**Problem.** VACUUM retention (QA-003), semantic model overwrite (QA-004), wrong table folder (QA-007), X-ray "fixed" on submit (QA-010): each turns a failed read into a guess and then does something irreversible.

**Existing implementation.** `xray_service.py:178-203,217,239-247,319-320`; `cloud_service.py:466-508`; `fabric_api.save_semantic_model` (`:252-264`); `service._schemas_enabled` (`:584-592`); `app/ui/meta.js:1110-1125`, `app/ui/xray.js:94`.

**Required changes.**
1. `_xray_keep_days`: only "no metadata project" (a specific exception) adds nothing; any other error refuses the vacuum with the reason. `_xray_table_days` failing refuses too.
2. Semantic model plan: a failed `list_semantic_models` fails the plan; a failed `table_schema` is reported as "couldn't read", not "not written yet", and blocks the write; when the model exists, the UI asks for confirmation naming the model and saying its definition will be replaced.
3. `_schemas_enabled`: raise on failure unless a cached answer exists.
4. X-ray: record "submitted" with the job id; mark fixed only when the job completes; findings marked fixed reappear if a later scan finds them.

**Exclusions.** Changing retention rules; new X-ray checks; semantic model content.

**Acceptance criteria.** None of the four paths performs a write after a failed read.

**Required tests.** Fakes raising on each read; the vacuum, model save and table write are refused with a message; a failed job leaves the finding visible.

**Documentation.** `docs/xray-fixes.md` and the semantic model section of `docs/metadata-guide.md`: what happens when Fabric can't be read.

**Definition of done.** Tests pass; four findings marked fixed.

## PR-QA-04 Shortcut rules and access review fail closed

**Problem.** QA-005.

**Existing implementation.** `shortcut_service.py:150-170,250-268,305`; `estate_service.py:245,450-470`; `access.py:100-130`; `app/ui/shortcuts.js:575-585`.

**Required changes.** `shortcut_preflight` returns "nothing to enforce" only when no platform is configured; a failed platform call blocks creation and requests with the reason, and the UI shows it. The access review returns `partial: true` with the failed source when data-access roles can't be read (only a specific "OneLake security not enabled" answer means no roles), and the UI says the list is incomplete. Shortcut readers do the same.

**Exclusions.** New rule types; platform availability work.

**Acceptance criteria.** With the platform call failing, no shortcut is created and the reason is shown; an access review missing a source says so.

**Required tests.** Engine tests for each failure; a UI test that the preflight error isn't swallowed.

**Documentation.** `docs/shortcuts.md` and `docs/governance.md`: what happens when the platform can't be reached.

**Definition of done.** Tests pass; QA-005 marked fixed; Governance confirms the behavior matches GOV policy intent.

## PR-QA-05 One rule for transient errors

**Problem.** QA-006: `FabricError` 503 and 429 aren't retryable; five classifiers disagree.

**Existing implementation.** `retrying.py:12-23`; `fabric_api.py:33-37,72-78`; `capacity.py:80`; `integrations/core.py:117`; `workloads/classify.py:21,25`; `workloads/model.py:110-127`; callers `agent.py:212-215`, `meta/engine.py:334-335`.

**Required changes.** `is_retryable` reads `FabricError.status` (or `FabricError` gains `status_code`). `TRANSIENT_STATUS` = {408, 429, 500, 502, 503, 504} is defined once in `retrying.py` and imported by the other four. `FabricClient` retries idempotent reads on 5xx with the same bounded backoff it uses for 429, honoring `Retry-After` (with DI-7 / HD-039 if it isn't merged first).

**Exclusions.** Retrying writes; changing agent requeue limits.

**Acceptance criteria.** The QA-006 reproduction returns True; every classifier agrees on every status in a shared table test.

**Required tests.** Table test across all classifiers; agent requeue on a `FabricError(status=503)`.

**Documentation.** None user-facing.

**Definition of done.** Tests pass; HD-051 notes this as its first slice.

## PR-QA-06 Shared file, SQLite, audit, redaction and time helpers

**Problem.** QA-011, QA-012, QA-016, QA-022, QA-024, QA-028: the same infrastructure written many times, with the copies drifting.

**Existing implementation.** See each finding's evidence.

**Required changes.**
1. `fsutil.write_json_atomic(path, data)` (mkstemp in the same folder, flush, `os.replace`) and `read_json(path, default)` that moves a corrupt file to `<name>.corrupt-<time>` and logs it. Adopt at every site listed in QA-011, `settings.json` and schedules first.
2. `sqlite_util.connect(path, durable=False)` and `tx()` from `integrations/store.py`'s idiom; adopt in the six modules of QA-022; fix `notify.py`'s unclosed connections.
3. One chain writer from `integrations/audit.py` used by `AuditLog`, taking the cross-process lock and re-reading the head (closes HD-017's fork).
4. `redact.secrets` gains URL user info and `git/secrets.PATTERNS`; delete the fallback in `integrations/core.py:68-84`; `livetest.scrub` calls `redact.secrets`.
5. `timeutil` (from `workloads/model.py`): `now`, `iso`, `parse` (naive means UTC; malformed returns None); adopt at the 13 sites; run ids in UTC.
6. Pin `pymsalruntime` and `tzdata` in `constraints.txt`.

This PR may be split into one per numbered item; each is independent.

**Exclusions.** Moving settings between files; changing any file format.

**Acceptance criteria.** `git grep` finds no JSON `write_text` of a shared state file outside `fsutil`; no `sqlite3.connect` outside `sqlite_util`; one chain writer; one redactor.

**Required tests.** Torn-file test (corrupt `settings.json` is moved aside and the next save keeps other keys from the last good copy if present, or starts clean and says so); two-process audit test; the two QA-016 strings in `app/tests/redact-cases.json`; `timeutil` edge cases.

**Documentation.** `docs/security.md` redaction section; `CONTRIBUTING.md`: "use `fsutil`, `sqlite_util`, `timeutil`, `redact`".

**Definition of done.** Tests pass on Windows and Linux; findings marked fixed.

## PR-QA-07 Tell "not set up" from "the call failed"

**Problem.** QA-013, QA-021, QA-027: 98 silent handlers; alerts lost; partial lists shown as complete.

**Existing implementation.** `_project()`, `_catalog()`, `silent_token` and their callers; `schedule_service.py:150-158,213-233`; `dataops_service.py:344-347`; `agent.py:283`; `readiness.py:58-63`; `cloud_service.py:98-101`; `dataops_service.py:269-272`.

**Required changes.** A `NotConfigured(LookupError)` raised by the "not set up" helpers. Handlers in this PR catch `NotConfigured` for the empty case and let other errors reach the user, or return `partial: true` with `skipped` like the catalog (`service.py:284-297`). Teams alerts go through `workloads/notify` with the outbox; a failed alert is recorded on the run row. A test that counts silent broad handlers per module and fails if any module's count rises; owners lower theirs over time.

**Exclusions.** Rewriting every handler in one PR: this PR fixes the listed sites and adds the counter.

**Acceptance criteria.** Schedules, readiness, run lists and freshness show when they couldn't check; no alert failure is silent.

**Required tests.** Each listed site with a failing fake; the counter test.

**Documentation.** `CONTRIBUTING.md`: the rule for `except Exception`.

**Definition of done.** Tests pass; the counter's baseline is committed (98) and only goes down.

## PR-QA-08 One way to get a Fabric client and run background work

**Problem.** QA-014, QA-023, QA-025, QA-030.

**Existing implementation.** 23 `FabricClient(` and 20 `FileStore(` sites; `auth._token` callers; `service.py:102,107`, `runtime.py:49,53`; `estate_service.py:355,389,393`; Prove and side-by-side run loops; `side_by_side_service.py:46-49`, `prove_service.py:214`, `fabric_run.py:39`; `agent.py:30,36`, `sql_exec.py:554`.

**Required changes.** `Credential.token(scopes)` public (keep `_token` as an alias for one release). `Service.fabric`, `Service.fabric_execute`, `Service.files` as the only constructors outside `cli.py` and the agent; `runtime.py` reuses them. `FabricClient.request(url)` for the Power BI and Graph calls. A `BackgroundRuns(file, name)` helper used by Prove and side by side, tolerating a corrupt history line. Every table URI from `TableRef`; `fabric_run` uses `DFS_HOST`. `post_teams` and `_json_value` move to domain modules.

**Exclusions.** Splitting `Service`, removing mixins, changing RPC names.

**Acceptance criteria.** The counts in QA-014 fall: `_token(` outside auth and credentials is zero, `FabricClient(` outside the factories is zero; `type: ignore` falls.

**Required tests.** Existing suites pass unchanged; an import-graph test (QA-030).

**Documentation.** `docs/architecture.md` module table.

**Definition of done.** Tests pass; counts recorded in the register.

## PR-QA-09 Declare each RPC method once; remove dead surface

**Problem.** QA-015, QA-017, QA-029.

**Existing implementation.** `server.METHODS` (`server.py:20-90`), `audit.READ_ONLY` (`audit.py:42-66`), the methods in QA-017, the symbols in QA-029.

**Required changes.** An `@rpc(reads=False, ui=True)` decorator (or a single table in one module) that each method uses; `METHODS` and `READ_ONLY` are generated from it. Methods with no screen are tagged `ui=False` with a reason (API, agent, test) or removed; `export_sql_csv` and `integrations_approvals` are removed if nothing external uses them. Local-data methods stay but are tagged. Dead symbols in QA-029 removed; `localdata.close_all` called on shutdown.

**Exclusions.** Changing what any method does.

**Acceptance criteria.** Adding an RPC method touches one file; generated sets equal today's lists except deliberate removals.

**Required tests.** Generated-vs-previous set test; every `ui=True` method is called somewhere in `app/ui`.

**Documentation.** `CONTRIBUTING.md` "Adding an engine method".

**Definition of done.** Release management confirms it removes the `server.py` and `audit.py` conflict hot spot.

## PR-QA-10 Say what runs where, and tag what's untested

**Problem.** QA-008 (capacity claims), QA-017 (local data offered with no screen), QA-020 (missing Preview tags).

**Existing implementation.** `scorecard.py:83-84`; strings listed in QA-008; `features.js:23-63`; the buttons in QA-020; `README.md:40`, `docs/README.md:15`.

**Required changes.** One sentence used everywhere: "Runs on this PC. No Fabric compute is used; reading and writing OneLake still counts against the workspace's capacity." The PC cost in Prove becomes kind `not available` with that text. Preview entries for Publish, Promote, Create workspace, Sync keys, variable library, replay and Run all in Fabric; `railItem` shows the tag for `preview: true` pages. README task rows for local data say "engine only, no screen yet".

**Exclusions.** Measuring OneLake transactions (FinOps-003 territory).

**Acceptance criteria.** `git grep -i "no fabric capacity"` returns nothing; no constant is labelled `measured`.

**Required tests.** A UI test that every listed action is under a Preview tag; an engine test that `scorecard` has no constant measured value.

**Documentation.** README, `docs/architecture.md`, `docs/notebooks.md`, `docs/prove/how-proofs-work.md`, `docs/user-guide.md`.

**Definition of done.** FinOps confirms the wording against `docs/finops/cost-evidence.md`.

## PR-QA-11 One confirmation dialog, red for destructive actions

**Problem.** QA-019: 17 destructive confirmations styled as the primary action; 13 confirm copies.

**Existing implementation.** `confirmDialog` (`meta.js:411-415`); copies at `incidents.js:168`, `sidebyside.js:112`, `workloads.js:409,446` and the eight ad hoc dialogs listed in QA-019; `prove.js:598`.

**Required changes.** Move `confirmDialog` to `app.js` with `{danger, confirmLabel}`; danger renders `tool danger` and focuses Cancel. Route the copies through it. One body-text class.

**Exclusions.** Changing which actions confirm (UX-018 decides that).

**Acceptance criteria.** Every destructive action uses `danger`.

**Required tests.** A UI test listing destructive actions and checking their dialog.

**Documentation.** `docs/ui-guidelines.md`: shared helpers section.

**Definition of done.** UX review PASS.

## PR-QA-12 Accessible tabs, segmented controls and menus

**Problem.** QA-019: 11 tab bars and 13 segmented controls hand-built, ARIA on one; `showMenu` without roles or Escape.

**Existing implementation.** Sites listed in QA-019; `notebook.js:60` is the accessible one.

**Required changes.** `tabs(items, current, onPick)` and `segmented(...)` helpers in shared code with roles, `aria-selected`/`aria-pressed` and arrow keys; `showMenu` gets `role="menu"`, `menuitem`, Escape and arrow keys. Adopt at every site.

**Exclusions.** Visual redesign.

**Acceptance criteria.** Every tab bar and segment uses the helpers.

**Required tests.** Browser test: keyboard through a tab bar and a menu.

**Documentation.** `docs/ui-guidelines.md`.

**Definition of done.** UX and accessibility review PASS; closes part of UX-012.

## PR-QA-13 Shared pills, result grid, time formats and polling

**Problem.** QA-019: 10 pill helpers, two ambers, 5 grids, 7 formatters, ~15 poll loops.

**Required changes.** `statusPill(tone, label)` with one tone list and `.warn` removed; `resultGrid` using `cellText` (NULL shown one way); `activity.js` time helpers only; `poll(fn, {every, until})` with backoff, stop on error count and pause when hidden, replacing the loops (includes UI-2 / HD-035 if not yet merged). Success and warning color tokens; existing hex values that equal tokens replaced.

**Exclusions.** Dark mode; layout changes.

**Acceptance criteria.** Counts in QA-019 for pills, grids, formatters and loops fall to one each.

**Required tests.** Unit tests for each helper.

**Definition of done.** UX review PASS.

## PR-QA-14 Tests that reach the Fabric client and OneLake I/O

**Problem.** QA-018.

**Existing implementation.** `test_fabric_api.py` `FakeSession`; seven `FakeFabric` copies; folder fakes for `FileStore`; `livetest.py`.

**Required changes.** One `FakeFabric` in `conftest.py`, recording requests, used by the seven files. A request-shape test (method, URL, body, LRO handling) for each of the 22 methods never executed. `FileStore` tests against a fake DFS session (upload, read, download, delete, 404 and 403). Rename `test_fabric_compute.py:160` and `test_agent.py:312` to say they use fakes. Replace the 12 source-text UI tests with behavior tests where the code can run. Extend `livetest.py` to schedules, jobs, shortcuts and semantic models (run by hand; not in CI).

**Exclusions.** Running anything against a tenant in CI.

**Acceptance criteria.** `fabric_api.py` and `onelake.py` coverage at least 90%; no `FakeFabric` outside `conftest.py`.

**Documentation.** `docs/live-testing.md`: what the harness now covers.

**Definition of done.** Coverage numbers recorded; TS-1 / HD-053 updated.
