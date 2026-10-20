# Quality findings register

Every finding from the [engineering quality baseline](README.md), reviewed at `main` **3bee0db** (2026-10-18). Line numbers refer to that commit.

- **Evidence** is **Reproduced** (a script ran the real engine code and showed it), **Code** (read and traced, not run) or **Count** (from `git grep`, the syntax tree or coverage data).
- **Overlaps** names a finding from the [hardening audit](https://github.com/KratosDataGod/fabric-desktop/pull/137) (DI-, EP-, EN-, SH-, AR-, TS-, UI- ids; backlog HD-) or the [UX audit](https://github.com/KratosDataGod/fabric-desktop/pull/166) (UX-) and says what this one adds.
- **Open PR** names a pull request open at review time that already fixes part of it.
- **Fix** points at a [remediation spec](remediation.md).
- Nothing here was run against a Fabric tenant.

Severity:

| Severity | Meaning |
| --- | --- |
| Critical | Data loss or a false success that is likely in normal use |
| High | The same harm under conditions that occur in real use (an outage, throttling, a failed call), or a control that doesn't hold |
| Medium | A real weakness with limited reach, or a pattern that is causing defects elsewhere |
| Low | Hygiene, drift or a narrow edge case |

## Summary

| ID | Sev | Kind | Finding | Fix |
| --- | --- | --- | --- | --- |
| QA-001 | Critical | Fake functionality | Spark writer options are stored and ignored; `replaceWhere` overwrites the whole table | PR-QA-01 |
| QA-002 | High | Hidden failure | A failed notebook save shows "Saved"; close and switch then drop the edits | PR-QA-02 |
| QA-003 | High | Hidden failure | VACUUM falls back to Fabric's 7 days when the project or table setting can't be read | PR-QA-03 |
| QA-004 | High | Hidden failure | Create model can overwrite an existing semantic model and silently leave tables out | PR-QA-03 |
| QA-005 | High | Fail open | Shortcut rules and the access review treat a failed call as "nothing to enforce" and "no readers" | PR-QA-04; fix #247 (PASS), closed once merged |
| QA-006 | High | Duplication | A Fabric 503 or 429 isn't retryable to the agent; five transient-error rules disagree | PR-QA-05 |
| QA-007 | Medium | Hidden failure | A failed lakehouse lookup guesses "schemas on", sending writes to the wrong folder | PR-QA-03 |
| QA-008 | Medium | Overclaim | "0 CU-s, measured" and "no Fabric capacity used" for runs that read and write OneLake | PR-QA-10 |
| QA-009 | Medium | Fake functionality | `notebookutils.notebook.runMultiple` ignores args, dependencies and timeouts | PR-QA-01 |
| QA-010 | Medium | Overclaim | X-ray marks a finding fixed when the job is submitted and hides it for good | PR-QA-03 |
| QA-011 | Medium | Duplication | 9 atomic-write copies; settings and schedules are rewritten non-atomically and read a torn file as empty | PR-QA-06 |
| QA-012 | Medium | Duplication | Two audit-chain writers; the app's caches the chain head across processes | PR-QA-06 |
| QA-013 | Medium | Error handling | 237 broad exception handlers, 98 silent; "not set up" and "the call failed" share one branch | PR-QA-07; partly fixed by #263 (HD-055: CI blocks do-nothing handlers, 56 now log); 51 handlers that only set a default remain (101 on main by this definition) |
| QA-014 | Medium | Architecture | `Service` is 347 public methods and 385 `type: ignore`; clients built at 43 sites; private `_token` is the real API | PR-QA-08 |
| QA-015 | Medium | Architecture | Three hand-kept method lists make every feature PR touch the same files | PR-QA-09 |
| QA-016 | Medium | Duplication | Four redactors; the shared one passes a token embedded in a URL | PR-QA-06 |
| QA-017 | Medium | Disconnected | 17 callable RPC methods with no screen, some destructive; local data is documented as usable with no screen | PR-QA-09 |
| QA-018 | Medium | Weak tests | 22 of 41 Fabric client methods and every OneLake upload and delete never run, even against a fake | PR-QA-14 |
| QA-019 | Medium | UI duplication | Confirm, tabs, pills, grids, polling and error handling re-built per page | PR-QA-11 to PR-QA-13 |
| QA-020 | Medium | Overclaim | Untested Fabric-writing actions have no Preview tag; rail items never show one | PR-QA-10 |
| QA-021 | Medium | Duplication | Teams alerts go two ways; the agent's job-failure alerts skip its retrying outbox | PR-QA-07 |
| QA-022 | Medium | Duplication | Six SQLite connection idioms; twins drifted on durability; one leaks connections | PR-QA-06 |
| QA-023 | Low | Duplication | Prove and side by side are near-clone background-run implementations | PR-QA-08 |
| QA-024 | Low | Duplication | 13 date and time helpers with different rules for naive times and errors | PR-QA-06 |
| QA-025 | Low | Duplication | Table URIs rebuilt by hand in four places, skipping name validation | PR-QA-08 |
| QA-026 | Low | Fake functionality | `fs.exists` says False on 401/403, `mkdirs` does nothing, `read.schema()` is ignored | PR-QA-01 |
| QA-027 | Low | Hidden failure | Monitor > Schedules, readiness and run lists show partial results as complete | PR-QA-07 |
| QA-028 | Low | Configuration | Windows-only dependencies aren't pinned in `constraints.txt` | PR-QA-06 |
| QA-029 | Low | Dead code | A dead redaction fallback, unused symbols and production code only tests call | PR-QA-09 |
| QA-030 | Low | Architecture | Domain modules import the service layer; six cycles through function-level imports | PR-QA-08 |
| QA-032 | Medium | Unconfirmed write | Estate X-ray's Optimize starts a Fabric compaction job on click, with no confirm or risk check | Owner: Optimize; fix #231 (PASS) |
| QA-033 | Medium | Unsafe retry | `meta/engine.py` `_after_commit` (main :367-381) repeats run_log, dq_results, schema_history, quarantine and watermark appends on any exception, so an append that committed and then raised (lost response, timeout after commit) writes the rows twice. Retry only errors that prove nothing was written (e.g. CommitFailedError), or make the append idempotent by run id | Owner: Hardening (found grading #256; pre-existing) |

## Details

### QA-001 Spark writer options are stored and ignored (Critical)

**Evidence: Reproduced.** `LakehouseWriter.option()` and `options()` save into `self._options` (`sidecar/fabric_desktop/spark_local.py:54-61`). The only option ever read is `header`, for CSV (`:104`, `:111`). `saveAsTable` (`:79-86`) calls `lakehouse.write_table(_arrow(self._df), name, mode=how)`, and `write_table` (`lakehouse.py:232`) has no way to receive a predicate. `partitionBy` (`:63-65`) prints a note; the options print nothing. `insertInto = saveAsTable` (`:88`) gives `insertInto` a default of "error if exists" where Spark appends.

Reproduction, with only `lakehouse.write_table` replaced by a recorder:

```python
spark = spark_local.LocalSparkSession()
df = spark.createDataFrame([("2026-10-01", 1)], ["date", "amount"])
w = df.write
w.mode("overwrite").option("replaceWhere", "date = '2026-10-01'").saveAsTable("sales")
# write_table received: [{'table': 'sales', 'mode': 'overwrite', 'rows': 1, 'extra_kwargs': {}}]
# options left unused on the writer: {'replaceWhere': "date = '2026-10-01'"}
```

**Impact.** In Fabric this replaces one day. Here it replaces the whole `sales` table in OneLake with one day's rows, and prints a successful write result. `overwriteSchema`, `mergeSchema` and `partitionOverwriteMode=dynamic` are dropped the same way. `docs/notebooks.md:69` says `saveAsTable` works. Delta time travel can recover the data only until the next VACUUM.

**Overlaps.** DI-8 / HD-040 (local `saveAsTable` overwrite replaces the schema) is a different path through the same writer.

**Fix.** PR-QA-01: refuse any writer option outside an allow-list, with a message that names it; implement `replaceWhere` with delta-rs's predicate overwrite, or refuse it; make `insertInto` append. **Verify:** a test per option that today passes silently and must raise or behave as in Fabric.

### QA-002 A failed notebook save shows "Saved", then edits are dropped (High)

**Evidence: Code.** `saveNotebook()` (`app/ui/app.js:1923-1932`) runs the RPC through `attempt()`, which shows an error toast and returns `undefined`; `saveNotebook` returns nothing either way. Ctrl+S runs `saveNotebook().then(() => toast("Saved"))` (`:2430`); there is one toast element (`:57-69`), so "Saved" replaces "Could not save". Closing the notebook tab runs `await saveNotebook(); state.notebook = null;` (`:262`, commented "closing never loses edits") even when the save failed. Opening or creating another notebook (`:1895`, `:1903`) replaces the unsaved one the same way. The Copy buttons at `activity.js:155`, `dataops.js:150` and `meta.js:402` also toast success after a failure.

**Impact.** A full disk, a locked file or an engine restart loses notebook work while the app says it was saved.

**Fix.** PR-QA-02. **Verify:** a UI test where `save_notebook` rejects: no "Saved" toast, the notebook stays open and dirty.

### QA-003 VACUUM drops the promised history on any error (High)

**Evidence: Code.** `_xray_keep_days` (`sidecar/fabric_desktop/xray_service.py:178-193`) wraps the project load and layer lookup in `except Exception: pass  # no metadata project` (`:185-191`). `_xray_table_days` (`:199-203`) returns `None` on any error, including auth failures and throttling. `xray_vacuum` (`:239-245`) then submits a VACUUM with Fabric's 7-day default.

**Impact.** A corrupt `project.toml`, a TOML typo or a token refresh failure turns "keep 30 days because the layer promises Restore for 30 days" into 7 days, irreversibly. Restore then can't go back as far as the layer says.

**Overlaps.** The launch review fixed Fabric-run vacuum to use layer retention; this is the X-ray path, and the fallback undoes it.

**Fix.** PR-QA-03: only "no metadata project" (a specific exception) means nothing more to keep; any other failure stops the vacuum with the reason. **Verify:** tests with a broken project file and a failing table read: the vacuum is refused.

### QA-004 Create model can overwrite an existing model (High)

**Evidence: Code.** The plan (`sidecar/fabric_desktop/cloud_service.py:469-474`) sets `existing = None` when `list_semantic_models` fails, so the UI (`app/ui/meta.js:1121-1122`) offers **Create model**. `save_semantic_model` (`fabric_api.py:254-263`) then looks the name up again and, if it exists, calls `updateDefinition`, replacing the definition. A failed `table_schema` read (`cloud_service.py:500-508`) becomes `fields = None`, and the UI reports that table as "Not written yet, so left out" (`meta.js:1114`) inside a passing check. No confirmation precedes the overwrite.

**Impact.** Measures, relationships and tables added in Fabric are replaced, and tables whose schema read failed disappear from the model, all after a screen that said "create".

**Fix.** PR-QA-03: a failed lookup fails the plan; "not found" and "read failed" are different outcomes; an existing model gets a confirmation that names it. **Verify:** tests for each failure with a fake client.

### QA-005 Governance checks fail open on errors (High)

**Evidence: Code.**
- `shortcut_preflight` (`sidecar/fabric_desktop/shortcut_service.py:255-258`) returns `{"blocked": [], "warnings": []}` on any exception, commented "no platform: nothing to enforce". `_sc_guard` (`:262-268`) is the enforcement point for creating a shortcut (`:305`) and for requests (`estate_service.py:245`). The UI does the same (`app/ui/shortcuts.js:580-581`).
- The access review (`estate_service.py:456-459`) turns any error from `list_data_access_roles` into `roles = None`, after which `access.py:108-128` lists only workspace Admins, Members and Contributors. Readers through data-access roles vanish with no sign the list is incomplete. `shortcut_service.py:156-159,165-168` do the same for shortcut readers.

**Impact.** A configured platform that times out, throttles or returns 401 enforces no shortcut rules, and "Who can read this" under-reports. Both are controls an auditor would rely on.

**Overlaps.** EP-4 / HD-010 is the same fail-open shape for `policy.json`; open PR #165 fixes that file (including the integration keys) but not these.

**Fix.** PR-QA-04. **Verify:** tests where the platform call raises: shortcut creation is refused with the reason; the access result carries `partial: true` and the UI says so.

### QA-006 Fabric 503 and 429 aren't retryable to the agent (High)

**Evidence: Reproduced.** `retrying.is_retryable` (`sidecar/fabric_desktop/retrying.py:12-23`) reads `status_code`, or `code` when it is an int. `FabricError` keeps the HTTP status in `.status` and a string `.code` (`fabric_api.py:33-37`).

```python
is_retryable(FabricError("Service unavailable", code="ServiceUnavailable", status=503))  # False
is_retryable(FabricError("Too many requests", code="TooManyRequests", status=429))      # False
```

The agent's requeue (`agent.py:212-215`) and the metadata engine (`meta/engine.py:334-335`) use this rule, so a passing Fabric outage fails a scheduled job instead of retrying it. Four more rules disagree: `capacity.py:80` reads `.status` correctly; `integrations/core.py:117` reads `exc.response.status_code`, which `FabricError` doesn't have; `workloads/classify.py:21,25` treats 502 to 504 as transient but not 500; `workloads/model.py:110-127` matches substrings such as `" 429"`. `fabric_api.py:72-78` retries 429 itself but never a 5xx.

**Overlaps.** AR-3 / HD-051 (seven HTTP call sites with separate retry handling). This adds the concrete defect and the classifier list.

**Fix.** PR-QA-05. **Verify:** the reproduction above becomes a test that expects True; each classifier imports one `TRANSIENT_STATUS`.

### QA-007 A failed lakehouse lookup sends writes to the wrong folder (Medium)

**Evidence: Code.** `_schemas_enabled` (`sidecar/fabric_desktop/service.py:584-592`) returns `True` on any exception, without caching. For a lakehouse without schemas, `lakehouse.py:105-121` then turns `sales` into `Tables/dbo/sales` instead of `Tables/sales`. Publishing (`cloud_service.py:156-158`), proofs (`prove_service.py:371`) and side by side (`side_by_side_service.py:202`) use the same answer.

**Impact.** One network error mid-session makes a write land in a folder Fabric shows as "Unidentified", and later reads may resolve to a different path.

**Fix.** PR-QA-03: raise on failure, with the reason; keep the cached answer when there is one. **Verify:** a test with a failing `get_lakehouse` and no cache: the write is refused.

### QA-008 "Measured 0 CU-s" and "no Fabric capacity used" (Medium)

**Evidence: Code.** `scorecard.py:83-84` hard-codes `"capacity_cu_seconds": 0, "kind": "measured"`, shown as "0 CU-s" in `app/ui/prove.js:528`. Nothing measures it. The same claim is in `app/ui/app.js:1717,2025`, `notebook.js:135`, `sidebyside.js:219`, `README.md:3,143,187`, `docs/architecture.md:5`, `docs/notebooks.md:3,173` and `docs/prove/how-proofs-work.md:58`. The project's own `docs/finops/cost-evidence.md:23` and `app/ui/estate.js:332` say OneLake reads and writes made from this PC use capacity too.

**Impact.** The product's central promise is stated more strongly than the code can support, in the place (a "measured" badge) users trust most. The brief's rule is explicit: no zero-CU claim without verifying the execution path.

**Open PR.** #174 fixes FinOps's own strings; the other strings went to Prove, Local data engine, Governance and Hardening, and README and top-level docs to UX oversight.

**Fix.** PR-QA-10: "No Fabric compute (Spark, notebooks, SQL endpoint). OneLake reads and writes still count against the workspace's capacity" in the UI and docs; the PC cost becomes kind `not available` with that text. **Verify:** a test that no figure labelled `measured` is a constant.

### QA-009 `runMultiple` ignores the DAG (Medium)

**Evidence: Code.** `notebookutils_local.py:247-249` takes only each activity's `path`, ignores `args` (children run with default parameters), `dependencies` (list order, not dependency order) and `timeoutPerCellInSeconds`, and writes `"exception": None` for every activity. `docs/notebooks.md:78` lists `runMultiple` as supported.

**Fix.** PR-QA-01: pass `args`, order by `dependencies`, or refuse a DAG that uses them. **Verify:** tests with a two-activity DAG with a dependency and args.

### QA-010 X-ray marks a finding fixed when the job is only submitted (Medium)

**Evidence: Code.** `xray_service.py:217` and `:247` call `_xray_mark_fixed` right after submitting the Fabric job. The id (`:319-320`) carries no time or version, so every later scan filters the finding out (`:175`, `app/ui/xray.js:94`), even if the job failed on a paused capacity or the problem came back.

**Fix.** PR-QA-03: record "submitted" with the job id; mark fixed when the job completes; let a later scan show it again. **Verify:** a test where the job fails and the next scan still shows the finding.

### QA-011 Shared JSON files are rewritten non-atomically (Medium)

**Evidence: Count and code.** About 25 JSON write sites; about 9 are atomic, using three temp-file schemes (fixed `.tmp`, uuid, `mkstemp`): `capacity.py:213-217`, `telemetry.py:102-106`, `git_service.py:61-65`, `prove_service.py:199-206`, `integrations/config.py:47-49`, `integrations/executions.py:197-198`, `notebook.py:907-909,930-932`, `localdata/projects.py:168-175`, `leases.py:82,118-120`. The files that matter most write in place: `settings.json` (`meta_service.py:58-66`, `runtime.py:61-64`, written by about eight mixins), schedules (`local_schedules.py:192-200`), `app_settings.py:253-255`, favorites and pins (`service.py:347,374`). Their readers return `{}` on `ValueError`. The twin of Prove's atomic save, `side_by_side_service._sbs_save` (`:99-106`), writes in place.

**Impact.** A crash or a second process mid-write leaves a torn `settings.json`; it reads as `{}`, and the next `_save_settings(x=...)` saves only `x`, dropping the metadata project, platform, cost settings and Teams webhook. A torn schedules file drops every schedule and orphans their Task Scheduler tasks.

**Fix.** PR-QA-06. **Verify:** a test that a corrupt file is moved aside (not overwritten) and every listed site uses the helper.

### QA-012 Two audit-chain writers (Medium)

**Evidence: Code.** `AuditLog` (`sidecar/fabric_desktop/audit.py:100-110,135-146`) caches the chain head in `self._prev` under a `threading.Lock`. The desktop (`service.py:79`), Task Scheduler runs (`cli.py:279-283`) and the agent (`agent.py:69`) all write the same folder. `integrations/audit.py:128` writes inside a store transaction, "one writer at a time across every process". The two copies also disagree on which fields are content (`audit.py:67` lists nine names the integration copy lacks).

**Overlaps.** EP-3 / HD-017 found the fork. This adds that a correct implementation already exists in the repo, so the fix is to share it.

**Fix.** PR-QA-06: one chain writer that takes a cross-process lock and re-reads the head under it. **Verify:** the HD-017 two-process test passes using the shared writer.

### QA-013 Silent exception handlers (Medium)

**Evidence: Count.** Over the syntax tree: 237 `except Exception`/`BaseException` handlers, 98 silent (no re-raise, log or use of the exception). No bare `except:`. Most per module: `estate_service.py` 17 (13 silent), `meta_service.py` 15 (6), `shortcut_service.py` 13 (8), `workload_service.py` 12 (6), `cloud_service.py` 10 (7), `schedule_service.py` 10 (6), `xray_service.py` 9 (7), `service.py` 8 (5). `server.py` and `agent.py` report every failure.

The harmful pattern is one branch for two meanings: `_project()`, `_catalog()`, role lookups and `silent_token` can't tell "not set up" from "the call failed". QA-003 to QA-007, QA-021 and QA-027 are all this pattern.

**Fix.** PR-QA-07: a `NotConfigured` exception raised by the "not set up" helpers; handlers catch that, and anything else reaches the user. A test that counts silent handlers and fails if the number rises. **Verify:** the counter test, then each module's handlers reduced by its owner.

### QA-014 `Service` and client construction (Medium)

**Evidence: Count.** `Service` (`service.py:55`) combines 18 mixins with **347** public methods and **385** `type: ignore` (351 of them `attr-defined`, mixins reaching for `self.auth` and `self.fabric`). `FabricClient(` is constructed at 23 sites in 11 files, each with a new `requests.Session`; `FileStore(` at 20 sites in 13 files. The private `auth._token(...)` is called 12 times from 5 files. `service.py:102,107` and `runtime.py:49,53` define the same client factories. `estate_service.py:355` adds a workspace user (a permission write) with raw `requests`, and `:389,:393` call Graph the same way, with no 429 handling and no `FabricError`.

**Overlaps.** AR-2 / HD-050 (335 methods, 348 ignores at 1248ccd) and AR-3 / HD-051. Both numbers have grown since.

**Fix.** PR-QA-08: a public `Credential.token(scopes)`, one `self.fabric` and `self.files` factory used by every mixin, and the estate calls through `FabricClient`. Not a rewrite of the mixin layout. **Verify:** `_token(` and `FabricClient(` counts outside `service.py`/`cli.py` fall to zero; `type: ignore` falls.

### QA-015 Three hand-kept method lists (Medium)

**Evidence: Count.** A new RPC method is added in its mixin, in `server.METHODS` (`server.py:20-90`, 344 names), and in `audit.READ_ONLY` (`audit.py:42-66`) when it only reads; `server.py:79` already lists `git_path_info`, `git_notebook_info` and `audit_log` twice. Of the 36 open PRs at review time, 16 edit `audit.py`, 15 edit `server.py`, 15 edit `features.js`, 15 edit `styles.css` and 18 edit `app/tests/ui.test.cjs`.

**Impact.** Release management's notes record this as the merge-conflict hot spot (RM-004). A method added to `METHODS` but not `READ_ONLY` is audited as a change; the reverse hides a change from the audit.

**Fix.** PR-QA-09: declare each method's exposure once, beside it (`@rpc(reads=True)`), and generate both sets. **Verify:** a test that the generated sets equal today's lists.

### QA-016 Four redactors; the shared one misses URL credentials (Medium)

**Evidence: Reproduced.** `redact.secrets` (`sidecar/fabric_desktop/redact.py:21-43`) is used by the server, audit, logs and support bundles. `git/secrets.py:142-153` covers URL user info, GitHub, Slack, AWS and Entra secrets and Teams webhooks; `redact.secrets` covers none of them:

```python
secrets("git push failed: https://chris:ghp_abc...789@github.com/x/y.git")  # unchanged
secrets("webhook https://contoso.webhook.office.com/webhookb2/abc/IncomingWebhook/def/ghi")  # unchanged
```

`integrations/core.py:68-84` keeps a fallback copy "until PR #84 merges" (it has). `livetest.scrub` (`livetest.py:42,73-75`) is weaker again.

**Overlaps.** EN-5 / HD-007 (agent log redaction on JSON text; open PR #157) is about where redaction runs, not what it matches.

**Fix.** PR-QA-06: `redact.secrets` includes the Git patterns and URL user info; delete the fallback; `livetest.scrub` calls it. **Verify:** the two strings above in `app/tests/redact-cases.json`.

### QA-017 RPC methods with no screen (Medium)

**Evidence: Count.** Every method name the UI passes to `rpc`, `metaCall`, `platformCall` or `pollSidecar` exists (211 names). The reverse doesn't hold: 17 allowlisted methods have no caller in `app/ui`, including `delete_notebook` (`service.py:636`), `platform_revoke` (`central/service.py:358`), `pipeline_import` (`pipeline_service.py:70`), `export_sql_csv` (`service.py:568`, superseded by `sql_export`), `integrations_approvals` (superseded by `integrations_pending`), `meta_preview`, `meta_preflight`, `meta_diff`, `notebook_diff`, `git_scan`, `git_ops`, `workload_actions`. The 15 `local_*` methods (`localdata_service.py:25-79`, `server.py:88-90`) have no screen; `docs/local-data.md:5` says so, but `README.md:40` and `docs/README.md:15` present local data as something to do. `cancel_fabric_run` has no button in Activity.

**Impact.** Destructive engine surface with no screen and no Preview gate, reachable by any RPC client; documentation that offers a task the app can't do.

**Overlaps.** UX-004 (screens for local data projects).

**Fix.** PR-QA-09 tags each as API-only or removes it; PR-QA-10 marks local data "engine only, no screen yet" in the README task tables. **Verify:** a test that every `METHODS` entry is either called by the UI or tagged.

### QA-018 Tests that prove only their fakes (Medium)

**Evidence: Count (coverage).** All 41 `FabricClient` methods (`fabric_api.py:109-381`) run only against fakes; **22 never execute at all**, including the writes `add_role_assignment`, `update_role_assignment`, `delete_role_assignment`, `put_data_access_roles`, `delete_schedule`, `cancel_job`, `save_item` and `refresh_sql_endpoint`. Every test swaps `onelake.FileStore` for a folder fake, so `FileStore.upload`, `delete`, `read_bytes` and `download` never ran (`onelake.py` 53%, `publish.py` 58%). `FakeFabric` is re-defined in 7 test files (`test_cloud.py:172`, `test_notebook_publish.py:16`, `test_prove.py:83`, `test_schedules.py:116`, `test_side_by_side.py:189`, `test_workloads.py:206`, `test_xray.py:33`). 30 `monkeypatch.setattr(svc, ...)` calls replace the Service's own methods before testing adapters. Two names claim more than they test: `test_fabric_compute.py:160` ("uploads once, starts the runner and reads results", all on folder fakes) and `test_agent.py:312` ("OneLake leases use conditional requests", on `FakeDfs`). 12 of 51 UI tests only match source text (`ui.test.cjs:18,97,198,209,214,221,238,253,268,514,529,538`); `:209-212` passes if the text `const TESTED_TOOLS = new Set();` exists.

The fakes themselves are good: `test_fabric_api.py:22-33` checks method, URL and body and drives the 202 and 429 paths. The gap is reach.

**Overlaps.** TS-1 / HD-053 and TS-2 / HD-054.

**Fix.** PR-QA-14: one shared `FakeFabric` in `conftest.py` that records request shape; a request-shape test for each of the 22 methods against `FakeSession`; `FileStore` tests against a fake DFS; rename the two tests; extend `livetest.py` to the write paths. **Verify:** `fabric_api.py` and `onelake.py` coverage above 90%.

### QA-019 UI components re-implemented per page (Medium)

**Evidence: Count.**
- Confirmation: `confirmDialog` (`app/ui/meta.js:411-415`, 30 calls in 11 files) always renders OK as `tool primary`, including for 17 destructive actions (Delete branch `git.js:532`, Discard changes `git.js:433`, Delete role `estate.js:512`, Revoke `integrations.js:184`, Delete entity or key table `meta.js:353,863,981`, Remove admin `platform.js:417`). Only `prove.js:598` uses `tool danger`. Four more single-purpose confirm helpers (`incidents.js:168`, `sidebyside.js:112`, `workloads.js:409,446`) and eight ad hoc ones.
- The Domains table is built twice (`mesh.js:63` and `platform.js:331`) with different button styles (`mesh.js:134`, `platform.js:339`) and empty states.
- 11 hand-built tab bars; only `notebook.js:60` sets `role="tab"` and `aria-selected`. 13 segmented controls, 6 `aria-pressed` in all. `showMenu` (`app.js:172`) has no menu role or Escape.
- 10 status-pill helpers and 7 status-to-class maps; `.pill-s.wrn` (`styles.css:357`) and `.pill-s.warn` (`styles.css:937`) are two ambers.
- 5 result grids showing NULL as `NULL` or `null`; 7 date and duration formatters; about 15 independent poll loops (only 5 calls use `pollSidecar`).
- RPC errors: `attempt` (166 calls), `metaCall` (63), `platformCall` (a one-line alias, 36), and 48 silent `.catch(() => null|{}|[]|false)`.
- 208 hex colors outside `:root`, 82 distinct, several equal to existing tokens.

**Overlaps.** UX-011 (one error panel), UX-012 (keyboard), UX-018 (one confirmation), UX-020 (shared loading and empty states). This adds the counts and the specific helpers. The confirmation part is tracked as UX-018 and built by the UX thread.

**Fix.** PR-QA-11 (danger confirm), PR-QA-12 (tabs, segmented, menu), PR-QA-13 (pills, grids, dates, polling). **Verify:** a UI test that every destructive confirm uses the danger style.

### QA-020 Preview tags missing on untested Fabric writes (Medium)

**Evidence: Code.** `PREVIEW_FEATURES` (`app/ui/features.js:37-63`) has no entry for Publish (`notebook.js:84`), Promote (`meta.js:665`), Create workspace (`meta.js:765`, `mesh.js:195`), Sync to LH_Keys (`meta.js:816`), Publish variable library (`estate.js:157`), Quarantine replay (`dataops.js:296`) or Run all in Fabric (`meta.js:119`; the tag is only on the compute picker at `:686`). The live test (`docs/live-testing.md`) covers lakehouses, files and Delta tables only. `railItem` (`features.js:23-32`) never shows the tag for a rail page registered with `preview: true`.

**Overlaps.** UX-019 (one generated Preview list). Tracked as UX-021 since 2026-10-18; tags owned by Prove, the roadmap thread and Hardening.

**Fix.** PR-QA-10. **Verify:** a test that every UI call to a method that writes to Fabric sits under a Preview tag until a live run records it.

### QA-021 Two Teams alert paths (Medium)

**Evidence: Code.** `workloads/notify.send` with `Outbox` queues and retries (used by `incident_service.py:137` and `agent.py:152-154`). `post_teams` (`dataops_service.py:344-347`) is fire and forget, used by `agent.py:283` (job didn't finish), `dataops_service.py:310` and `schedule_service.py:150-158`, which swallows failures with `pass  # Monitor still shows it`.

**Fix.** PR-QA-07: route both through the outbox; record an alert failure on the run row. **Verify:** a test where the webhook fails once and the alert is delivered on retry.

### QA-022 Six SQLite idioms (Medium)

**Evidence: Code.** `jobqueue.py:63-83` and `integrations/store.py:77-96` are twins (thread-local connection, WAL, `begin immediate`); only `jobqueue.py:69` sets `synchronous=full`. `workloads/store.py:55-63` uses a deferred transaction and a process-local lock with no WAL. `workloads/notify.py:113-114` uses `with sqlite3.connect(...)`, which commits but never closes, at five call sites. `central/client.py:57` and `backup.py` have their own.

**Fix.** PR-QA-06: `sqlite_util.connect(path, durable=)` and `tx()`. **Verify:** the six modules use it; a `ResourceWarning` filter turned into an error in the notify tests.

**Status.** Leak fixed: `workloads/notify.py` `Outbox` and `workloads/platform_notify.py` `Ledger` now close every connection (`notify.transaction`: commit, rollback on error, then close; `tests/test_sqlite_connections_closed.py`). The other sites don't leak per call: `jobqueue.py:63-71` and `integrations/store.py:77-84` (with `integrations/audit.py`) keep one connection per thread by design; `workloads/store.py:55-63` and `backup.py:68-74,227-242` close in `finally` (counted over 20-40 operations each). Unifying the idioms and the durability drift (only `jobqueue` sets `synchronous=full`) remain open.

### QA-023 Prove and side by side are clones (Low)

**Evidence: Code.** `prove_service.py:187-212,295-323,641-647` and `side_by_side_service.py:87-108,158-187,333-340`: the same lazy state dict, 200-line JSONL history, copy retry loop, status, list and cancel. One corrupt history line makes `json.loads` raise and breaks the whole list (`prove_service.py:197`, `side_by_side_service.py:97`). 31 `threading.Thread(` sites in 23 files; four pre-fetch tokens by hand to keep MSAL on one thread (`estate_service.py:216`, `workload_service.py:174`, `xray_service.py:110`, `schedule_service.py:198`).

**Fix.** PR-QA-08: one `BackgroundRuns` helper used by both. **Verify:** both test files pass unchanged.

### QA-024 Thirteen date and time helpers (Low)

**Evidence: Code.** Six `_now()` (`schedule_service.py:25`, `prove_service.py:39`, `side_by_side_service.py:26`, `cloud_service.py:36`, `git/ops.py:32`, `central/core.py:73`) and seven parsers (`costs.py:44`, `measurement.py:73`, `workloads/model.py:76`, `prove_service.py:43`, `side_by_side_service.py:30`, `meta/freshness.py:63`, `workloads/agents.py:27`). `agents._when` returns `None` for a naive time where the others assume UTC; `freshness._parse` doesn't catch `ValueError`. Run ids mix local time (`meta/engine.py:309`, `cloud_service.py:394`) and UTC (`jobqueue.py:106`).

**Fix.** PR-QA-06: `workloads/model`'s helpers become `timeutil`. **Verify:** tests for naive, aware, epoch and malformed input.

### QA-025 Table URIs rebuilt by hand (Low)

**Evidence: Code.** `onelake.TableRef.uri` (`onelake.py:37-45`) validates names. `side_by_side_service.OneLakeTables._uri` (`:46-49`) rebuilds it without validation and turns a dotless name with schemas on into `Tables//name`. `prove_service.py:214` and `fabric_run.py:39` build roots by hand; `fabric_run` hard-codes the host instead of `DFS_HOST`.

**Fix.** PR-QA-08.

### QA-026 Notebook file helpers that pretend (Low)

**Evidence: Code.** `fs.exists` (`notebookutils_local.py:117-126`) returns False on any `HTTPError`, including 401 and 403, which skips the overwrite checks in `put` and `mv` (`:131-134`, `:168-171`). `mkdirs` on OneLake (`:175-180`) does nothing and returns True. `spark.read.schema(...)` (`spark_local.py:136-137`) is ignored, so types are inferred (leading zeros, decimals and dates can differ from Fabric).

**Open PR.** #154 (HD-003) rewrites `fs` on OneLake and changes `exists`; re-check after it merges.

**Fix.** PR-QA-01.

### QA-027 Partial results shown as complete (Low)

**Evidence: Code.** Monitor > Schedules (`schedule_service.py:213-233`) turns a `list_items`, `list_schedules` or last-run error into `[]` with no `partial` flag, so a 403 looks like "nothing scheduled". `readiness.py:58-63` marks capacity OK when capacities can't be read. `cloud_service.py:98-101` and `dataops_service.py:269-272` turn a corrupt project file into "no runs" and "nothing late". The catalog already does this right (`service.py:284-297`, `skipped`).

**Overlaps.** UX-014 (Home and the bell); open PR #168 fixes Home.

**Fix.** PR-QA-07.

### QA-028 Unpinned Windows dependencies (Low)

**Evidence: Code.** `pyproject.toml:19` (`pymsalruntime>=0.20,<0.21`) and `:26` (`tzdata`, unbounded) are Windows-only and absent from `constraints.txt`, which the engine build installs with (`ci.yml:120`) and which promises pinned versions (`constraints.txt:1-4`, CONTRIBUTING.md).

**Fix.** PR-QA-06.

### QA-029 Dead code (Low)

**Evidence: Count, each checked by hand.** The codebase is otherwise clean: no TODO or FIXME, no commented-out code, no unimported module, no unused UI function or CSS class, every Preview flag read, no unused crate or direct dependency. Dead: the redaction fallback in `integrations/core.py:68-84`; `git/secrets.py:155` `redact_all`; `livetest.py:45` `class Refused` (never raised); constants `central/core.py:993` `SHORTCUT_CHANGES`, `meta/keys.py:44` `SOURCE_FIELDS`, `meta/project.py:43,49` `REQUIRED_CHECKS` and `CUSTOM_RULES`, `sql_exec.py:52` `EXPORT_BATCH_ROWS`, `workloads/model.py:12` `CAPABILITIES`; `sql_exec.py:135` `self._order` (never read). Called only by tests: `integrations/registry.py:223` `on_change` (the MCP server now polls `registry.version`, `mcp_server.py:89-115`), `registry.py:94` `resource_uri`, `local_schedules.py:171` `run_now`, `localdata/manager.py:64` `close_all` (nothing closes local engines on shutdown), `meta/store.py:104` `scan_table_uri`.

**Fix.** PR-QA-09 removes them (and calls `close_all` on shutdown).

### QA-030 Layering (Low)

**Evidence: Code.** Domain modules import the service layer: `agent.py:30,36` (`dataops_service.post_teams`, `runtime`), `sql_exec.py:554` (`service._json_value`), `integrations/http_server` and `mcp_server` (`service`). No module-level cycles, but six through function-level imports: `app_settings`, `auth` and `telemetry`; `lakehouse` and `sql`; four `integrations` modules; three `meta` modules; `sqlcompat.analyzer` and `rules`; `service` and `sql_exec`. 110 imports sit inside functions.

**Fix.** PR-QA-08 moves `post_teams` and `_json_value` down to domain modules. **Verify:** an import-graph test that `*_service` modules are imported only by `service.py`, `server.py`, `cli.py` and the integrations host.

### QA-032 X-ray Optimize starts a Fabric job without asking (Medium)

`xray.js:162` calls `xray_optimize` as soon as **Optimize** is pressed. That starts a Fabric table maintenance job (`fabric_api.run_table_maintenance`), which rewrites the table's files in OneLake and uses capacity. The table's rows and history are unchanged, so no data is lost, but it is a OneLake write and a capacity spend with no confirmation and no FinOps risk check, unlike Vacuum (preview, then confirm) and the run dialogs. AGENTS.md asks for confirmation before OneLake writes. Fix: a plain (not danger) confirm that names the table, says it runs in Fabric and uses capacity, and goes through the risk check like other Fabric runs. Found grading #230, whose panel describes the behavior correctly.

