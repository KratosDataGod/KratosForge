# Hardening backlog

PR-sized work items from the enterprise hardening audit of KratosForge, in dependency order inside each priority. Each item fixes findings in the [findings register](findings.md). Reviewed at `main` **1248ccd**.

Priority:

- **P0** Critical correctness and security: data loss, credential exposure, unauthorized access.
- **P1** Enterprise readiness blockers: deployment, verification, recovery, adoption.
- **P2** Maintainability and developer experience.
- **P3** Optional architecture work, not needed for the next Stable.

**Status** says whether the item is fixed on `main`, with the PR that fixed it, as checked on 2026-10-25 at `main` **164587d**. "Partly fixed" items say what remains in their row or section.

Complexity is S (under a day, one module), M (a few modules, new tests), L (cross-cutting or needs a manual Windows check).

**Owner** is the program that owns the code. "Hardening" means this program builds it; other owners were told through the project's coordinator. **Waits on** names what must land first. "Open stack" meant PRs #134, #125, #127, #133, #124 and #132, which change `service.py`, `server.py`, `app_settings.py`, `audit.py`, `app.js`, `updater.py`, `ci.yml` and `promote.yml`. They merged at 9715e28, so items that waited only on them are unblocked.

## P0

| Id | Status | Fixes | Owner | Waits on | Cx |
| --- | --- | --- | --- | --- | --- |
| HD-001 | Fixed (#138) | DI-1, DI-4: no rewrite after the commit; quarantine after the commit | Hardening (agreed with staged roadmap) | none | M |
| HD-002 | Fixed (#152) | DI-2: check the Delta profile before writing | Hardening (agreed with staged roadmap) | none | S |
| HD-003 | Fixed (#154) | DI-3: guard `notebookutils.fs` mutations on OneLake | Hardening (agreed with Prove) | none | S |
| HD-004 | Fixed (#162) | EP-1: a running cell keeps its lakehouse | Hardening | open stack (`service.py`) | M |
| HD-005 | Fixed (#155) | EP-2: restore removes stale SQLite WAL and verifies | Hardening | none | S |
| HD-006 | Fixed (#156) | EN-1: external run-notebook approval shows and binds the code | Hardening | none | M |
| HD-007 | Fixed (#157) | EN-5: redact log fields before JSON encoding | Hardening | none | S |
| HD-008 | Fixed (#158) | SH-3, SH-4: require checksums; Stable never falls back to Preview on a network error | Hardening | none for `main.rs`; `updater.py` part after the open stack | S |
| HD-009 | Fixed (#137) | EN-7, DOC-1: correct false security claims in docs | Hardening | none (docs-only PR) | S |

### HD-001 No rewrite after the commit (DI-1, DI-4)

- **Files:** `sidecar/fabric_desktop/meta/engine.py`; tests in `sidecar/tests/test_meta_engine.py` or a new `test_write_failures.py`.
- **Approach:** `_write` retries only when the table commit itself raised `CommitFailedError`. Watermark, run log, quality and schema-history writes after a successful commit get their own small retry; if they still fail, the run is reported as committed with a warning that names what wasn't recorded, and the table is never written again. Before any retry of the table write, check Delta history for a commit carrying this run id (`fabric_desktop_run_id`, already in commit metadata) and treat it as done. Quarantine rows are held in memory and written after the commit.
- **Acceptance:** a failure injected after the commit never duplicates rows, for append, upsert and rewrite paths, with and without `merge_writes`; the result says the data was committed; an agent retry after such a failure doesn't duplicate rows; a failed table write leaves quarantine unchanged.
- **Tests:** failure injection on `meta_append("watermarks")`, `meta_append("run_log")` and the quality writes; a commit-race retry still works (existing tests).
- **Impact:** removes the one confirmed path to silent duplicate rows in OneLake.

### HD-002 Check the Delta profile before writing (DI-2)

- **Files:** `meta/store.py`; `publish.py` and `lakehouse.py` publish path use the same helper.
- **Approach:** open the target (if it exists) and run `delta_profile.check_table` before `write_deltalake`/MERGE; keep the post-commit check, but only to report an upgrade the write caused, as a warning.
- **Acceptance:** append, upsert and `write_stream` to a table outside the Fabric-safe profile refuse with the table version unchanged; a valid table writes as before.
- **Tests:** writer-v4 table for each path; existing profile tests stay green.

### HD-003 Guard `notebookutils.fs` on OneLake (DI-3)

- **Files:** `notebookutils_local.py`; `onelake.py` if `delete` needs a non-recursive mode.
- **Approach:** `rm` without `recurse=True` refuses a non-empty OneLake folder, as Fabric does; `rm`, `mv`, `cp` and `put` refuse `Tables/` paths (managed tables change only through Delta); every OneLake mutation calls `lakehouse.write_guard`; `mv` checks the copy before deleting.
- **Acceptance:** each refusal raises with a clear message and sends no DELETE; a guarded run (proof sandbox or external tool refusing writes) refuses every filesystem mutation; `rm(file)` and `rm(folder, True)` under `Files/` still work.
- **Tests:** mocked `FileStore` recording calls.

### HD-004 A running cell keeps its lakehouse (EP-1)

- **Files:** `service.py`, `lakehouse.py`, `integrations/executions.py`.
- **Approach:** in `start_cell`, check whether a cell is running before attaching, under one lock shared with external runs. Then bind the target per run (a `contextvars.ContextVar` set by the run's thread) so the global is only the default.
- **Acceptance:** a refused start leaves the running cell's target unchanged; an external `run_cells` during a desktop cell doesn't change it.
- **Tests:** the reproduction from the audit as a regression test.

### HD-005 Restore removes stale WAL and verifies (EP-2)

- **Files:** `backup.py`; `sidecar/tests/test_backup.py`.
- **Approach:** on apply, for each restored SQLite file, move its `-wal`, `-shm` and `-journal` into the safety folder before replacing it; refuse to restore a database another process has open (an exclusive open attempt); verify each written file's SHA-256 against the manifest after writing.
- **Acceptance:** restoring over a database with a stale WAL gives exactly the backed-up rows; the safety copy includes the WAL; a held database is refused with a message saying to close the app.
- **Tests:** stale-WAL reproduction; open-connection refusal; safety contents.

### HD-006 Run-notebook approval shows and binds the code (EN-1)

- **Files:** `integrations/services.py`, `integrations/permissions.py`, `integrations/registry.py`, `docs/product/ai-integration-security.md`.
- **Approach:** the run-notebook capability loads the notebook before authorization, passes its cells as approval content, and includes a SHA-256 of the cells in the approval's argument hash. At run time the notebook is loaded again and refused if the hash differs. `%run` of other notebooks inside an approved run is refused.
- **Acceptance:** the approval request shows every cell; editing the file after approval returns "the notebook changed since it was approved"; `%run` is refused with a message.
- **Tests:** summary content, changed-file refusal, `%run` refusal.

### HD-007 Redact log fields before encoding (EN-5)

- **Files:** `logs.py`; `integrations/core.py` (`redact_result`).
- **Approach:** walk the record and redact each string value before `json.dumps`; blank values whose key names a secret (`password`, `secret`, `token`, `key`, `connection_string`).
- **Acceptance:** every case in `app/tests/redact-cases.json` is redacted through `JsonFormatter`; every output line is valid JSON; `redact_result({"password": "x"})` hides the value.
- **Tests:** the shared redaction cases run through the formatter; JSON validity.

### HD-008 Update fallbacks (SH-3, SH-4)

- **Files:** `app/src-tauri/src/main.rs` (engine); `app/src-tauri/src/updater.py` (exe, after the open stack).
- **Approach:** a release without `engine_sha256` is refused; the engine falls back from `stable` to `latest` only when Stable returns HTTP 404 (curl `-w %{http_code}`), never on a timeout or 5xx. Then the same for `exe_sha256` in the updater.
- **Acceptance:** Rust tests: missing checksum refused; a non-404 failure on Stable fails the install. Python test: missing `exe_sha256` refused.
- **Tests:** update `a_release_from_before_checksums_still_names_its_build` and `test_download_app_checks_the_build` to expect refusal. (These Rust tests only run in CI after HD-011.)

### HD-009 Correct the security docs (EN-7, DOC-1)

- **Files:** `docs/security.md`, `docs/product/release-readiness.md`, `docs/permissions.md`, `docs/product/ai-integration-security.md`.
- **Approach:** describe the loopback HTTP server and its guards; mark redaction, scanning, SBOMs, provenance and SHA pinning as in place; say plainly that external-tool Python can reach the user's tokens until HD-012/HD-013; mark the G3 to G8 gates as merged and point the readiness verdict at the [readiness checklist](readiness-checklist.md).
- **Acceptance:** no statement in those files contradicts the code at the reviewed commit.

## P1

| Id | Status | Fixes | Owner | Waits on | Cx |
| --- | --- | --- | --- | --- | --- |
| HD-010 | Fixed (#165) | EP-4: policy fails closed, one loader | Hardening | open stack (`app_settings.py`) | M |
| HD-011 | Fixed (#244) | SH-6: `cargo test` in CI | Hardening | open stack (`ci.yml`); workflow change needs Chris's review | S |
| HD-012 | Fixed (#249) | EN-2: execution kernels get an allowlisted environment (done in #249, plus #453 and #465: `kernel_proc.external_environment` keeps only `EXTERNAL_ENV_ALLOW`, strips proxy passwords and sets `PYTHONPATH` itself) | Hardening | none | S |
| HD-013 | Fixed (#593); Windows check pending | EN-10: encrypt the token cache with DPAPI on Windows | Hardening | Done (D-C2 approved 2026-10-23); Windows check pending | M |
| HD-014 | Fixed (#260) | EN-3: workspace limits on every capability | Hardening | HD-006 | M |
| HD-015 | Fixed (#253) | EN-4: external SQL timeout interrupts and releases the lock (done in #253: tables are registered under the attach lock and the query runs after it is released; a timeout or cancel interrupts DuckDB until it stops; the SQL page's memory share and threads apply) | Hardening | none | S |
| HD-016 | Fixed (#159, #624) | EN-6, AR-1: leases atomic across processes; lost lease stops the write (done: local takeover is one step under an OS file lock; `leases.check_held()` runs before each leased write starts in `publish.py` and `meta/store.py`, including quarantine and key writes inside an entity's lease, and stops a write whose lease was taken over or not renewed for two thirds of its length; a lease lost while the write uploads its files is still reported after it) | Hardening (agent) | none | M |
| HD-017 | Fixed (#182, #623) | EP-3: audit chain safe across processes; head checkpoint (done: `audit_chain.append` re-reads the last hash under the folder's lock file; each write records the newest hash and line count in `head.json`, and `verify()` reports a trail that ends before it, also after later writes) | Hardening | open stack (`audit.py`) | S |
| HD-020 | Fixed (#250) | SH-1, UI-1: Content Security Policy; notebook output on its own origin | Hardening | a manual Windows check before merge | L |
| HD-021 | Open: blocked on signing | SH-2: fail unsigned releases; verify signatures in CI and on the client | Hardening | **Chris: Trusted Signing account (D-C3)** | M |
| HD-022 | Fixed (#258) | SH-5: no downgrades; keep the previous exe until the new one starts. Must still let a Stable PC follow a deliberate rollback (an earlier build promoted to Stable, HD-028) | Hardening | open stack (`app.js`) | M |
| HD-023 | Partly fixed (#241) | SH-7: promote a named commit, re-check hashes, protected environment. Named commit and hash re-check come with HD-028's `build` input; the protected environment and copying the SBOMs remain | Hardening | #132 (`promote.yml`) | S |
| HD-024 | Fixed (#674); workflow review pending | SH-8: pin actions in `codex-review.yml` and `docs.yml`; scope permissions | Hardening | Done; workflow change needs maintainer review | S |
| HD-025 | Partly fixed (#679) | SH-9: agent installers use constraints; ProgramData ACL | Hardening | Constraints done; the ACL needs a Windows host to verify | S |
| HD-026 | Fixed (#251) | SH-10: no bare `python` fallback in release builds (done in #251: `choose_python` returns a bare name only in debug builds, and a release build needs an absolute `FABRIC_DESKTOP_PYTHON`) | Hardening | none | S |
| HD-027 | Fixed (#259) | SH-12: navigation filter for the Fabric view | Hardening | manual Windows check | S |
| HD-028 | Partly fixed (#241) | Per-build release assets, so a build can be pinned and rolled back (known gap in release-readiness.md). **Partly done:** CI keeps each published build as a `build-<sha>` release (newest 20, plus every Stable build); Promote to stable takes a `build` input, so Stable rolls back by promoting an earlier build. With `"auto_update": false` the app downloads its engine only from its own build's `build-*` release (#418, `update_guard::engine_tags`), so a pinned exe gets the matching engine. Remaining: rollback hasn't been run end to end | Hardening | Chris's review (workflow); a first rollback drill on `main` | M |
| HD-029 | Open: waits on Chris | Live verification (gate G1): run the existing harness on a designated non-production workspace | Chris | **Chris: workspace and OK** | S |
| HD-030 | Partly fixed (Entra ids) | EP-5: separation of duties by Entra id; switch from policy or versioned files. **Done:** release records carry the promoter's Entra object id and the check treats a matching id or a matching PC login name as the same person, with a note when only login names could be compared. Remaining: take the switch from platform policy or versioned files | Staged roadmap | open stack (`meta_service.py`) | M |
| HD-031 | Fixed (#147) | EP-6: label "Block" as not enforced until GOV-002 | Governance | none | S |
| HD-032 | Fixed (#141) | EP-7: T-SQL key import chunking; T-SQL contract test | Data mesh | none | S |
| HD-033 | Fixed (#245) | DI-5: bounded previews | Staged roadmap (FD-114) | SQL-003 #134 covers desktop SQL | S |
| HD-034 | Fixed (#243) | DI-6: fresh token before long writes (done: storage tokens have at least 30 minutes left) | Local data engine | none | S |
| HD-035 | Fixed (#306) | UI-2: bounded status polling | Prove | none | S |
| HD-058 | Fixed (#272) | EN-12: followed URLs keep the token on Fabric, OneLake and Power BI hosts (done) | Hardening | none | S |

Acceptance for each P1 item is the fix and tests named in its finding. The ones that change what admins or users see:

- **HD-010:** a `policy.json` with a syntax error denies Fabric runs and other tenants and shows the error in Settings; a wrongly typed restrictive key is treated as the most restrictive value; `integrations*` and `receipt_redactions` aren't reported as unknown.
- **HD-012:** inside an external-tool run, `os.environ` has no variable from the parent outside an allowlist (`PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, locale); `docs/permissions.md` says tools' code runs as the user and can reach their sign-in until HD-013.
- **HD-013:** the cache file is DPAPI-encrypted on Windows; an existing plaintext cache is encrypted in place on first start; sign-in survives the update without a prompt. Migration documented in `docs/security.md`.
- **HD-020:** CSP set in `tauri.conf.json`; notebook HTML output, charts, the Fabric view and sign-in work on Windows (checked by hand and recorded in the PR); a UI test fails if `csp` is null or allows `unsafe-eval`.
- **HD-021:** a push to `main` without signing configured fails the release unless an explicit unsigned-Preview variable is set; the client refuses an exe or engine whose signature doesn't name the expected publisher.
- **HD-027:** checked by hand on Windows and recorded in the PR: sign-in inside the Fabric view; open a catalog item; sign out; a link off the allowed hosts opens in the default browser; a sign-in pop-up opened from a sign-in host can't navigate off the list (its links open in the browser) and still reports back to the view; on a federated tenant with `fabric_view_sign_in_hosts` set, sign-in finishes in the view, the identity provider's cookies survive a restart (or the provider asks again, which is acceptable but noted), and Seamless SSO, the device step and `login.windows.net` don't leave the view.
- **HD-029:** the FD-101 harness's `summary.md` recorded in `release-readiness.md` with date, build and workspace type.

## P2

| Id | Status | Fixes | Owner | Cx |
| --- | --- | --- | --- | --- |
| HD-036 | Fixed (#248) | EP-8, EP-9: refuse one workspace in two environments; guard `run_job` | Staged roadmap | S |
| HD-037 | Fixed (#239) | EN-8: Stop ends the kernel's whole process tree | Prove | M |
| HD-038 | Fixed (#299, #304) | EN-11: sanitize the CSV export name; receipts never overwrite (done: `service.file_stem` cleans the name; CSV exports and receipts open with mode `"x"`) | Hardening | S |
| HD-039 | Fixed (#256) | DI-7: `Retry-After` parsing and cap; retry 5xx on reads (done: `retrying.retry_after` reads seconds or an HTTP date, capped at 60 s; `retrying.send_with_retry` retries 5xx and dropped connections on reads only, used by the Fabric client and OneLake, lease, mailbox, Graph and Key Vault reads) | Hardening | S |
| HD-040 | Fixed (#238) | DI-8: overwrite refuses a schema change without `overwriteSchema` | Prove | S |
| HD-041 | Fixed (#264) | SH-11: reply ids checked; long calls off the shared pipe (done: replies matched by id; quick calls in their own lane) | Hardening | M |
| HD-042 | Fixed (#302) | UI-4: every dialog close settles its prompt (done: `showDialog`'s `onClose` settles `askText`, `pickWorkspace` and `confirmDialog` on overlay, X and Escape) | Hardening | S |
| HD-043 | Fixed (#298) | UI-5: parsed name-clash test; remove duplicate test (done: V8 finds each script's top-level names; duplicate test removed) | Hardening | S |
| HD-044 | Fixed (#300) | UI-6: `aria-current`, modal palette (done: the rail sets `aria-current="page"`; the command bar is a modal dialog that keeps and returns focus and doesn't open over another dialog) | Hardening | S |
| HD-051 | Fixed (#275) | AR-3: one HTTP session with a shared retry policy (done: `http_session.session()` shared by the Fabric, OneLake, Power BI and platform clients) | Hardening | M |
| HD-052 | Fixed (#242, #618, #619) | UI-8: browser tests in CI; ban new raw HTML sinks (done: the `ui-browser` CI job runs `app/tests/browser`; `ui.test.cjs` fails on a raw HTML sink or a URL attribute built from data outside a reviewed list, and image output shows only picture types) | Hardening | S |
| HD-053 | Fixed (#266) | TS-1: contract tests for OneLake writes and deletes and the Fabric client's 429/5xx/LRO paths; refuse `..` and empty OneLake file paths | Hardening | M |
| HD-054 | Fixed (#267, #271); live job waits on a test workspace | TS-2: T-SQL contract harness; scheduled live job once a test workspace exists | Hardening | M |

## P3

| Id | Status | What | Cx |
| --- | --- | --- | --- |
| HD-050 | Partly fixed (#305) | AR-2: replace the 18-mixin `Service` with small injected services; narrow `server.METHODS` to what the UI calls. First slice: METHODS guard test with listed exceptions (no behaviour change; `sidecar/tests/test_rpc_surface.py`). | L |
| HD-055 | Fixed (#263) | AR-4: lint rule against new silent `except Exception`; clear the 53 existing ones | M |
| HD-056 | Partly fixed (#274) | Split `app.js` along `registerFeature` boundaries | L |
| HD-057 | Open | Registry (ADMX) policy alongside `policy.json` | M |

## Not duplicated

Already covered elsewhere, so not in this backlog:

- Code signing in CI: built (`ci.yml` signing steps), waiting on the Trusted Signing account. HD-021 only adds the enforcement around it.
- Kernel isolation, cell time and memory limits: Prove program (#83, #114, #115 merged).
- SQL cancellation and paged results for the desktop SQL page: SQL-003 (#134, open).
- Local data engine cancel race: the local data engine thread has an unpushed fix.
- Telemetry, audit log, support bundle, retention, backup command: merged in the enterprise readiness program (#84 to #95, #121). This backlog only fixes the defects found in them.
