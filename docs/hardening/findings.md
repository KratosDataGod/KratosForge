# Hardening findings register

Confirmed issues from the enterprise hardening audit of Fabric Desktop by KratosBI. Reviewed at `main` **1248ccd** (2026-10-17, "Merge pull request #120"); line numbers refer to that commit. Re-checked at **9715e28** after the SQL-003 stack merged: every finding still applies (notes on DI-5 and SH-11 below).

How to read this register:

- **Evidence** says how the issue was confirmed. "Reproduced" means a script ran against local Delta tables, a temporary folder or mocked HTTP and showed the failure. "Code" means it was read from the code but not run. Nothing here was run against a Fabric tenant.
- **Backlog** is the work item in the [hardening backlog](backlog.md) that fixes it.
- **Existing work** names an open PR or another program's plan when one already covers part of it.

Severity follows the brief:

| Severity | Meaning |
| --- | --- |
| Critical | Data loss, credential exposure or unauthorized access that is likely in normal use |
| High | The same harm under conditions that occur in real use (concurrency, retries, a crafted file), or an integrity control that doesn't hold |
| Medium | A real weakness with limited reach, or a defense-in-depth layer that is missing |
| Low | Robustness, hygiene or a narrow edge case |

No Critical issue was confirmed. Twelve are High.

## Summary

| Id | Sev | Area | Finding | Backlog |
| --- | --- | --- | --- | --- |
| DI-1 | High | OneLake writes | A failure after the table commit re-runs the write, so incremental rows are appended twice and the run reports ok | HD-001 |
| DI-2 | High | OneLake writes | The Fabric-safe Delta profile check runs after the commit, so committed data is reported as a failed write | HD-002 |
| DI-3 | High | OneLake writes | `notebookutils.fs.rm` deletes OneLake folders recursively, including `Tables/`, and bypasses the write guard | HD-003 |
| EP-1 | High | Engine | The attached lakehouse is one global, so a refused second cell start or an external run re-points a running cell (Dev to Prod) | HD-004 |
| EP-2 | High | Recovery | Restore leaves the old SQLite `-wal` file, so data written after the backup comes back | HD-005 |
| EN-1 | High | AI integration | An external tool's run-notebook approval shows no code and isn't bound to the file, which is read after approval | HD-006 |
| SH-1 | High | Shell | No Content Security Policy, and the Tauri API is a page global | HD-020 |
| SH-2 | High | Distribution | Updates check integrity, not authenticity. Releases publish unsigned without failing when signing isn't set up | HD-021 (blocked) |
| DI-4 | Medium | OneLake writes | Quarantine rows are written before the table commit, so a failed write and its retry quarantine the same rows twice | HD-001 |
| EN-5 | Medium | Logging | Agent log redaction runs on JSON text, so quoted secrets leak and lines become invalid JSON | HD-007 |
| SH-3 | Medium | Distribution | A `version.json` without a checksum is still accepted for the exe and the engine | HD-008 |
| SH-4 | Medium | Distribution | The engine download falls back from Stable to Preview on any network error | HD-008 |
| EP-4 | Medium | Policy | A malformed or wrongly typed `policy.json` fails open (Fabric runs allowed, any tenant) | HD-010 |
| SH-6 | Medium | CI | Rust unit tests (update integrity, policy, watchdog) never run in CI | HD-011 |
| EN-2 | Medium | Tokens | External-tool Python runs inherit the full environment and can read the plaintext token cache; docs say tools never get a token | HD-012, HD-013 |
| EN-10 | Medium | Tokens | The MSAL cache is plaintext on every OS; the POSIX temp file is briefly world-readable | HD-013 (awaits D-C2) |
| EN-3 | Medium | AI integration | The per-tool workspace limit only applies when the call names a `workspace_id` | HD-014 |
| EN-4 | Medium | AI integration | An external SQL timeout doesn't stop the query and keeps a lock that blocks every later run | HD-015 |
| EN-6 | Medium | Leases | Local write leases can be taken by more than one process at once | HD-016 |
| AR-1 | Medium | Leases | A lease that stops renewing isn't marked lost, and loss is only checked after the commit | HD-016 |
| EP-3 | Medium | Audit | The audit hash chain forks when the app and a scheduled run write at once; tail deletion goes unnoticed | HD-017 |
| EP-5 | Medium | Governance | The two-person promote rule compares OS login names and can be switched off in an unversioned file | HD-030 |
| EP-6 | Medium | Governance | "Block" governance policies are stored but enforced nowhere | HD-031 (GOV-002) |
| EP-7 | Medium | Business keys | T-SQL key import sends 3,000 parameters per statement; SQL Server allows 2,100 | HD-032 |
| EN-7 | Medium | Docs | `docs/security.md` says the engine has no network listener; it runs one on 127.0.0.1:47615 | HD-009 |
| SH-5 | Medium | Distribution | Any different build installs (downgrades included), and `exe.old` is deleted before the new build proves it starts | HD-022 |
| SH-7 | Medium | Distribution | Promote to stable promotes whatever Preview is at that moment, with no hash re-check | HD-023 |
| SH-8 | Medium | CI | `codex-review.yml` (runs on `pull_request_target`) and `docs.yml` use tag-pinned actions; docs PR builds get `pages: write` | HD-024 |
| SH-9 | Medium | Agent install | The Windows agent installer and `install-sidecar` don't use the pinned constraints | HD-025 |
| DI-5 | Medium | Memory | Entity previews and SQL results load the whole table or result before slicing | HD-033 |
| DI-6 | Medium | OneLake writes | Long writes use one bearer token for their whole duration (inferred) | HD-034 |
| UI-2 | Medium | UI | Prove and side-by-side status polling never stops on errors | HD-035 |
| EP-8 | Low | Environments | Nothing stops Dev and Prod naming the same workspace | HD-036 |
| EP-9 | Low | Publishing | `run_job` bypasses the write guard and overwrites with `schema_mode="overwrite"` | HD-036 |
| EN-8 | Low | Notebooks | Stopping a kernel leaves the processes its cells started | HD-037 |
| EN-11 | Low | Engine | `export_sql_csv` builds a path from an unsanitized lakehouse name; `receipt_save` overwrites | HD-038 |
| DI-7 | Low | Fabric API | `Retry-After` in HTTP-date form crashes the client; 5xx and connection errors aren't retried on reads | HD-039 |
| DI-8 | Low | Notebooks | Local `saveAsTable` overwrite silently replaces the schema where Fabric would refuse | HD-040 |
| SH-10 | Low | Shell | A release build with no engine falls back to a bare `python` command | HD-026 |
| SH-11 | Low | Shell | RPC replies aren't matched to request ids; the pipe runs one call at a time with no limit for long calls | HD-041 |
| SH-12 | Low | Shell | The Fabric view filters new windows but not navigation, and turns SmartScreen off | HD-027 |
| UI-1 | Low | UI | (part of SH-1) Notebook HTML output frames can make network requests | HD-020 |
| UI-4 | Low | UI | Closing a text prompt by its overlay or X never settles its promise | HD-042 |
| UI-5 | Low | UI tests | The name-clash test is a column-0 regex; one test is duplicated | HD-043 |
| UI-6 | Low | Accessibility | No `aria-current` on the rail; the command palette isn't modal | HD-044 |
| AR-2 | Medium | Maintainability | `Service` combines 18 mixins with 335 public methods (332 callable over RPC) and 348 `type: ignore` | HD-050 |
| AR-3 | Medium | Maintainability | Seven HTTP call sites with separate retry and error handling | HD-051 |
| UI-8 | Medium | Maintainability | `app.js` is 2,521 lines; UI tests check 13 of 27 scripts; browser tests aren't in CI | HD-052 |
| TS-1 | Medium | Testing | Low coverage where data is written: `onelake.py` 53%, `publish.py` 58%, `notebookutils_local.py` 59% | HD-053 |
| TS-2 | Medium | Testing | No live-tenant or T-SQL dialect test runs anywhere automatically | HD-054 |
| DOC-1 | Medium | Docs | `release-readiness.md` lists gates as "open PR" that merged hours ago; three security claims in docs are false | HD-009 |

## Details

### DI-1 High: a failure after the table commit writes the rows again

- **Where:** `sidecar/fabric_desktop/meta/engine.py:954-964` (`_write`), `:1112-1131` (`_advance_watermark`), `:337` (`run_log` append outside the `try`); `agent.py:231-243` (automatic retry).
- **What:** `_write` retries all of `_write_once` when a `CommitFailedError` is raised. `_write_once` commits the table first, then appends the watermark to the meta store. If that append fails, the table write runs again. On keyless incremental loads (append, or the rewrite path that concatenates existing and new rows), the rows go in twice and the run reports `ok`. Any other error after the commit marks the run failed with the watermark not advanced; the agent retries `ConnectionError` and `CommitFailedError` jobs on its own, and the retry appends again.
- **Why it matters:** silent duplicate rows in OneLake, reported as success. The trigger is ordinary: two first writes to a meta table that doesn't exist yet raise `CommitFailedError` (4 of 60 in a concurrency run).
- **Evidence:** reproduced. With the watermark append failing once, both `merge_writes=True` and `False` gave `status=ok`, table ids `[0, 1, 2, 10, 10, 11, 11, 12, 12]`.
- **Fix:** retry only the table commit. Treat watermark, run log, quality and schema-history writes as follow-up writes with their own retry, and never as a reason to rewrite the table. Make appends idempotent with the run id already written to commit metadata (`fabric_desktop_run_id`): before a retry, look for a commit carrying this run id.
- **Tests:** inject a failure into `meta_append("watermarks")` and `meta_append("run_log")` after the commit, for keyless append and keyed upsert, with and without `merge_writes`. Assert no duplicates and a truthful status. An agent retry after a post-commit failure must not duplicate rows.
- **Existing work:** none. The staged roadmap program owns incremental loads (FD-117).

### DI-2 High: the Delta profile check runs after the commit

- **Where:** `meta/store.py:206-208`, `:232-234`; `publish.py:52-58`.
- **What:** `delta_profile.check_table` runs after `write_deltalake` has committed. If the target has a protocol or feature outside the Fabric-safe profile (for example, Fabric Spark turned on change data feed), the data is already in OneLake, but `ProfileViolation` (a `ValueError`) is raised. The run is marked failed, the watermark doesn't move, and every rerun appends again.
- **Evidence:** reproduced. Append to a writer-version-4 table: `ProfileViolation ... writer version 4 > 2`, and the table moved from version 0 to 1 with both rows.
- **Fix:** open the table and check its profile before writing; keep the post-commit check only for upgrades the write itself caused, and report those as committed with a warning, never as a failure.
- **Tests:** an append, upsert, `write_stream` and `publish` to a writer-v4 table refuse with the version unchanged.

### DI-3 High: `notebookutils.fs.rm` deletes any OneLake path recursively

- **Where:** `notebookutils_local.py:153-166`; `onelake.py:167-172`.
- **What:** for OneLake paths, `rm` ignores `recurse` and always deletes with `recursive=true`. It accepts `Tables/...` (managed Delta tables) and doesn't consult `lakehouse.write_guard`, which proof sandboxes and external-tool "refuse all writes" runs (`integrations/executions.py:146-155`) rely on. In Fabric, `rm(folder)` without `recurse=True` fails on a non-empty folder, so a notebook that is safe in Fabric deletes data locally. `mv` copies then deletes, and doesn't confirm the copy landed.
- **Evidence:** reproduced with mocked HTTP and the guard installed: `rm("Files/landing")` sent `DELETE ... recursive=true`; `rm("Tables/dbo/fact_sales")` was sent while the guard refused writes.
- **Fix:** honor `recurse=False` (refuse non-empty folders), refuse `Tables/` for `rm`, `mv`, `cp` and `put`, run every OneLake mutation through `write_guard`, and make `mv` check the copy before deleting.
- **Tests:** each of those refusals, plus a guarded run refusing all filesystem mutations.

### EP-1 High: a running cell can be re-pointed to another lakehouse

- **Where:** `service.py:420` (`start_cell`), `:447` (`_attach`); `lakehouse.py:63`, `:86`; `integrations/executions.py:302-303`.
- **What:** the attached lakehouse is a module global. `start_cell` attaches the new notebook's lakehouse before the kernel checks whether a cell is already running, so a refused start still changes the running cell's target. External-tool runs re-attach under their own lock, which the desktop path never takes.
- **Evidence:** reproduced. A cell started against `dev-ws` printed `dev-ws -> prod-ws` after a refused second start against `prod-ws`.
- **Fix:** check busy before attaching, under one lock shared with external runs; then bind each run to its own target (a context variable or a per-run binding) instead of a global.
- **Tests:** a refused start leaves the running target unchanged; an external run can't change a desktop cell's target.
- **Existing work:** none. `service.py` is changed by the open SQL-003 stack (#134, #125, #127, #133, #124, #132), so this waits for it.

### EP-2 High: restore brings back data written after the backup

- **Where:** `backup.py:213-232` (`restore`); backups skip `-wal`/`-shm` at `:53`.
- **What:** restore replaces `*.sqlite` files but leaves the destination's `-wal` and `-shm`. SQLite replays the old write-ahead log onto the restored file. The safety copy misses the WAL too. The agent queue and the integrations store both use WAL.
- **Evidence:** reproduced: the backup held 1 row; after `restore(apply=True)` the database had 201 rows and a table created after the backup. `integrity_check` still said ok.
- **Fix:** on apply, move the destination's `-wal`, `-shm` and `-journal` into the safety folder, refuse while another connection holds the database, and verify each restored file's SHA-256 against the manifest.
- **Tests:** restore over a stale WAL; restore while a connection is open; safety copy includes the WAL.

### EN-1 High: external run-notebook approval isn't bound to the code

- **Where:** `integrations/services.py:290-308`; `integrations/permissions.py:38-50`; `integrations/registry.py:327-335`.
- **What:** `fabric_desktop_run_notebook` takes a notebook name. The approval summary copies content only from `code`, `query`, `cells` and `source` arguments, so the person approves a bare name. The approval hash covers only the arguments, and the notebook is read after approval. An AI tool that can write files can get "run notebook X" approved, then change `X.ipynb`. `docs/product/ai-integration-security.md:56` says the approval screen shows the code in full.
- **Evidence:** code.
- **Fix:** load the cells before asking, show them, include their hash in the approval, and refuse at run time if the file changed. Refuse `%run` of notebooks that weren't part of the approval.
- **Tests:** the summary has every cell's source; changing the file after approval is refused; `%run` of another notebook is refused.

### SH-1 High: no Content Security Policy, Tauri API on the page

- **Where:** `app/src-tauri/tauri.conf.json` (`"csp": null`, `"withGlobalTauri": true`).
- **What:** the UI escapes consistently (the UI review found no exploitable injection: every DOM write goes through `h()` and four raw HTML sinks are escaped). But escaping is the only layer. Any script that lands in the page can call `invoke("rpc")`, which reaches every engine method, including running notebook code with the user's tokens.
- **Fix:** a strict CSP (`script-src 'self'`, `object-src 'none'`, `connect-src ipc: http://ipc.localhost`), the one inline handler moved to a script, notebook HTML output served from its own origin with a `connect-src 'none'` policy, and `withGlobalTauri: false`. This needs a manual check on Windows before it ships (a wrong CSP is a blank window for everyone).
- **Existing work:** FD-102b in the enterprise backlog, not started.

### SH-2 High: updates prove integrity, not authenticity

- **Where:** `app/src-tauri/src/main.rs:403-499`, `:504-529`; `src/updater.py:295-307`; `.github/workflows/ci.yml:134`, `:191`, `:210-273`.
- **What:** checksums come from the same release as the files, so whoever can publish a release controls every PC. Build provenance attestations are produced but nothing verifies them. When `TRUSTED_SIGNING_ACCOUNT` is unset, the signing steps are skipped and the release publishes unsigned builds without failing.
- **Fix:** once signing exists, fail the release job if signing isn't configured (with an explicit unsigned-Preview switch), verify Authenticode signatures in CI before upload, and check signature and publisher on the client before swapping the exe or unpacking the engine.
- **Blocked:** on the Trusted Signing account (decision D-C3, Chris).

### DI-4 Medium: quarantine rows from a failed write

- **Where:** `meta/engine.py:882-950`.
- **What:** rows that fail checks are appended to quarantine before the table write. If the write fails, quarantine holds rows from a run that wrote nothing, and the retry adds them again with new ids.
- **Evidence:** reproduced: run 1 failed after quarantining 1 row, run 2 quarantined it again: 2 quarantine rows for one source row.
- **Fix:** hold quarantine rows in memory (as `_held` does for replay) and write them after the table commit.

### EN-5 Medium: agent log redaction runs on JSON text

- **Where:** `logs.py:36` (`secrets(json.dumps(line))`); `support_bundle.py:96`; `integrations/core.py:87-96`.
- **What:** after `json.dumps`, quotes are escaped, so `password": "x"`-style patterns don't match, and a match can cut a line in the middle of a string.
- **Evidence:** reproduced: `"conn \"password\": \"hunter2\""` passed through unredacted; `client_secret=...\"` produced `[removed]"s3cr3tvalue\"` and an invalid JSON line. `redact_result` leaves `{"password": "x"}` alone because keys aren't examined.
- **Fix:** redact each string value before encoding; blank values whose key names a secret.

### SH-3 and SH-4 Medium: update fallbacks

- **Where:** `src/updater.py:306-307`, `:345`; `main.rs:127` (`None => Ok`); `main.rs:417-445`.
- **What:** a `version.json` without `exe_sha256` or `engine_sha256` is accepted (left over from before CI wrote checksums). Separately, the engine download loops over `stable` then `latest` and moves on after any curl failure on Stable, so one timeout installs the Preview engine on a Stable PC. The exe updater correctly falls back only on 404.
- **Fix:** require both checksums; fall back to Preview only when Stable returns 404.

### EP-4 Medium: policy fails open

- **Where:** `app_settings.py:79-85`, `:166-185`; `integrations/policy.py:30-35`.
- **What:** one syntax error in `policy.json` ignores the whole file, so `allow_fabric_runs` becomes true and `allowed_tenants` becomes any. A wrongly typed key is dropped and falls back to allow. The integrations reader parses the same file with different rules. `policy_problems()` reports valid `integrations*` and `receipt_redactions` keys as unknown. There is no registry (ADMX) policy.
- **Evidence:** reproduced with a temporary ProgramData folder.
- **Fix:** one loader; restrictive keys fail closed when the file or the key is invalid; one list of known keys.
- **Existing work:** `app_settings.py` is changed by the open stack; waits for it.

### SH-6 Medium: Rust tests never run

- **Where:** `.github/workflows/ci.yml:169-208` builds the shell but there is no `cargo test` anywhere.
- **What:** `integrity_tests` (`main.rs:988`), `policy_tests` (`main.rs:49`), the sign-in cookie tests and the watchdog tests exist but don't run, so the update checksum logic can regress silently. The audit ran the watchdog tests by hand (5 pass).
- **Fix:** `cargo test --release --locked` in the Windows shell job.

### EN-2 and EN-10 Medium: tokens reachable from external-tool code

- **Where:** `kernel_proc.py:83-85`; `integrations/executions.py:134-157`; `auth.py:93-127`; `docs/permissions.md:148`.
- **What:** external-tool Python runs start a kernel with the parent's whole environment, as the same user, so code can read the plaintext MSAL cache (refresh tokens for Fabric, Graph and Power BI) and any secret in the environment. `credentials.getToken("storage")` returns a tenant-wide OneLake token. `docs/permissions.md:148` says an AI tool never receives a token. On POSIX the cache's temp file is created with the umask's mode before `chmod`.
- **Evidence:** reproduced: cell code printed the cache's refresh token and an `AGENT_SP_SECRET` from the environment.
- **Fix:** correct the docs now; start execution kernels with an allowlisted environment; encrypt the cache with DPAPI through `msal-extensions` (FD-131b); create the temp file with mode 0600.
- **Awaiting:** the `msal-extensions` dependency decision (D-C2, Chris; recommended yes).

### EN-3 Medium: workspace limit bypass

- **Where:** `integrations/permissions.py:124`.
- **What:** the limit is checked only if the call has a `workspace_id` argument. `verify_proof_table` and `export_receipt` (both return example rows), proofs, receipts, notebooks and the workspace list take none.
- **Evidence:** reproduced: a client limited to one workspace was allowed `verify_proof_table`, `export_receipt` and `list_workspaces`.
- **Fix:** each capability names the workspace it touches; refuse when it can't be resolved for a limited client; filter lists.

### EN-4 Medium: external SQL timeout doesn't stop the query

- **Where:** `integrations/executions.py:247-252`, `:274-280`.
- **What:** the run holds `_attach_lock` for the whole query and sets no cancel hook; the timeout only marks it `timed_out`. No DuckDB `memory_limit`.
- **Evidence:** reproduced: after a 1-second timeout on a 6-second query the lock was still held; the next run waited 10.5 s.
- **Fix:** `on_cancel = con.interrupt`; register views under the lock, run outside it; set `memory_limit` and `threads` before locking the configuration.

### EN-6 and AR-1 Medium: local lease races

- **Where:** `leases.py:113-129`, `:239-268`; `agent.py:193-198`.
- **What:** the local backend's compare-and-replace is guarded by an in-process lock only. Renewal errors are swallowed, so a lease that hasn't renewed past its TTL isn't marked lost, and `LeaseLost` is raised only after the write. Delta's version check still prevents lost updates (verified), so this is the second layer that's weak, not the first.
- **Evidence:** reproduced: 8 processes racing to take one expired lease, 30 trials: 16 trials had more than one holder.
- **Fix:** an OS file lock around the compare-and-swap; mark the lease lost once `now - last_renewal > ttl`; check it right before the commit.

### EP-3 Medium: audit chain across processes

- **Where:** `audit.py:115-140`.
- **What:** each `AuditLog` caches the previous hash in memory and locks per thread. The desktop engine and scheduled runs write the same file, so the chain forks and `--verify` reports tampering that didn't happen. Deleting lines from the end isn't detected. `integrations/audit.py:128` does this correctly.
- **Evidence:** reproduced both.
- **Fix:** re-read the last hash under an OS file lock; record a head-hash checkpoint elsewhere.
- **Existing work:** `audit.py` is changed by the open stack; waits for it.

### Other Medium and Low findings

| Id | Where | Finding and fix |
| --- | --- | --- |
| EP-5 | `meta_service.py:508-530`, `duties.py:46-67`, `meta/project.py:56` | Compare Entra object ids, not `getpass.getuser()`; take `separate_promoter` from platform policy or versioned files; label it advisory |
| EP-6 | `central/policy.py:131-164`, `docs/governance.md:18` | "Block" stops nothing yet. Label it "not enforced yet" in Governance until GOV-002 adds the promote gate (governance thread) |
| EP-7 | `central/core.py:363-367` | Chunk T-SQL key import at 699 rows or use OPENJSON like `issue_keys`; add a T-SQL contract test (inferred from SQL Server's documented 2,100-parameter limit, not run) |
| EN-7, DOC-1 | `docs/security.md`, `docs/product/release-readiness.md`, `docs/permissions.md:148`, `docs/product/ai-integration-security.md:56` | Correct: the engine runs a loopback HTTP server (guarded by Host and Origin checks and a hashed token); redaction exists; scanning, SBOM, provenance and SHA pinning exist; AI-tool code can reach tokens; run-notebook approval doesn't show code; G3 to G8 PRs are merged |
| SH-5 | `app/ui/app.js:2409-2422`, `main.rs:504-529`, `:974-976` | Refuse older builds unless the channel changes; keep `exe.old` until the new build's first health check; restore it on failure |
| SH-7 | `.github/workflows/promote.yml` | Require the commit to promote; `sha256sum -c` against `version.json`; protected environment; copy SBOMs |
| SH-8 | `.github/workflows/codex-review.yml:353-397`, `docs.yml:419-436` | Pin by SHA; move `pages`/`id-token` to the deploy job; pin zensical |
| SH-9 | `deploy/windows/install-agent.ps1:78`, `src/updater.py:268-275` | Install with `-c constraints.txt`; set an explicit ACL on `%ProgramData%\FabricDesktop` (unverified on Windows) |
| DI-5 | `meta_service.py:270-272`, `:405-411`; `service.py:496-526` | Use `scan().head(n)` and a log-based count for entity previews. The desktop SQL page has paged, memory-limited execution since SQL-003 (#134, merged); `export_sql_csv` and external-tool SQL still materialize |
| DI-6 | `onelake.py:48-50`, `meta/store.py:426-427` | Ask for a token with at least 30 minutes left before long writes (inferred, not run against OneLake) |
| UI-2 | `app/ui/prove.js:155-165`, `sidebyside.js:54-58` | Cap attempts, stop on "not found", guard against two loops for one run |
| EP-8 | `meta_service.py:208-234`, `:373-377`; `meta/engine.py:291-299` | Refuse the same workspace in two environments |
| EP-9 | `publish.py:44-81` | Apply `write_guard` and an environment check to `run_job` |
| EN-8 | `kernel_proc.py:152`, `:305` | New process group (POSIX) or Job Object (Windows) so Stop ends the whole tree (reproduced: an orphaned `sleep` survived) |
| EN-11 | `service.py:524`, `prove_service.py:127` | Sanitize the CSV name like `sql_compat_export`; open receipts with mode `"x"` |
| DI-7 | `fabric_api.py:74-78`, `:91-107` | Parse and cap `Retry-After` (seconds or HTTP date); retry 5xx and connection errors on GETs only |
| DI-8 | `spark_local.py:67-86` | Refuse a schema change on overwrite unless `overwriteSchema` is set, as Fabric does |
| SH-10 | `main.rs:161-166` | No bare `python` fallback in release builds |
| SH-11 | `main.rs:267-325` | Check reply ids; reset the engine on a mismatch; move long calls off the shared pipe (SQL-003, merged, moved SQL page queries off it; notebooks already use `start_cell`) |
| SH-12 | `main.rs:540`, `:623-630`, `:876` | Add a navigation filter with the same host check; keep SmartScreen on |
| UI-4 | `app/ui/app.js:135-170` | `showDialog` takes an `onClose` so every way of closing settles the prompt |
| UI-5 | `app/tests/ui.test.cjs:18-32`, `:229-235`, `:296-302` | Parse instead of regex; remove the duplicate test |
| UI-6 | `app/ui/features.js:31`, `:149`; `app/ui/app.js:2298` | `aria-current="page"`; mount the palette as a modal dialog; ignore Ctrl+K while a dialog is open |

### Maintainability and testing

| Id | Evidence | Direction |
| --- | --- | --- |
| AR-2 | `service.py:55`: 18 mixins, 335 public methods, 332 callable over RPC, 348 `type: ignore[attr-defined]` | Small injected services (auth, Fabric client, store) with Protocols; narrow `server.METHODS` to what the UI calls |
| AR-3 | `fabric_api.FabricClient` retries only 429; `onelake`, `central/client`, `leases` don't retry; `requests` called directly in `estate_service.py:355`, `:389`, `telemetry.py:212`, `dataops_service.py:345`, `notebookutils_local.py:226`; 8 hand-written retry loops | One session factory with a retry policy that never blindly retries non-idempotent writes |
| AR-4 | 218 `except Exception`, about 53 that swallow (`leases.py:264`, `xray_service.py:149-236`) | Log or re-raise; ban new silent ones with a lint rule |
| UI-8 | `app.js` 2,521 lines, one shared `state`; tests cover 13 of 27 scripts; no tests for `meta.js`, which starts writes; 24 Playwright tests in `app/tests/browser` not run by CI | Run the browser suite in CI; a test that bans new raw HTML sinks; split `app.js` along `registerFeature` boundaries |
| TS-1 | Line coverage 87% overall, but `onelake.py` 53%, `publish.py` 58%, `notebookutils_local.py` 59%, `server.py` 63%, `fabric_api.py` 72%, `central/setup.py` 20%, `kernel_host.py` 0% (subprocess) | Contract tests with a fake `requests.Session` for OneLake writes and deletes and Fabric 429/5xx/LRO paths |
| TS-2 | CI runs only fakes; `livetest.py` is manual; T-SQL code in UDF_Platform never runs in its dialect | Scheduled live job against a designated non-production workspace once Chris approves one; a T-SQL contract harness |

## What was checked and held up

These were tested or read closely and found sound. They're listed so later reviews don't redo them.

- **UI injection.** Every DOM write goes through `h()` (`app/ui/app.js:29-43`), which creates text nodes; Markdown is escaped before formatting (`:2217-2221`); no `href` or `src` is built from data; no `eval` or `new Function`. Notebook HTML output runs in `sandbox="allow-scripts"` frames with an opaque origin.
- **Local HTTP API.** Binds 127.0.0.1 only, checks `Host` against loopback names (DNS rebinding) and refuses any `Origin` (browser requests), caps bodies at 1 MiB, rate-limits per tool (`integrations/http_server.py:124-160`, `:394`). Tokens are stored as SHA-256 hashes and looked up by hash. Approvals are single use, bound to the argument hash and client, and expire.
- **SQL sandbox for external tools.** One `SELECT`, then `enable_external_access=false` and `lock_configuration` (`sql.py:72-102`); reading local files, globbing, HTTP and extensions were all refused.
- **RPC errors** carry no tracebacks and pass through redaction (`server.py:89-101`); Python's stdout is moved off the reply pipe.
- **Concurrent Delta writes.** Overwrites commit against the version they read (`meta/store.py:189-196`), so a concurrent append makes the overwrite fail instead of losing rows. Key assignment held under 6 concurrent writers (302 rows, no duplicate keys). Batched loads land in one commit or none. `loaded_files` is recorded after the commit.
- **Dry runs.** VACUUM preview uses `dry_run=True` and changed nothing; the live test harness dry run makes no network calls.
- **Every HTTP call has a timeout.**
- **Platform identity.** UDF operations take the caller from the execution context, never from the client; admin checks run server side; policy versions are immutable and hashed.
- **Supply chain in `ci.yml`.** Read-only by default, actions pinned by SHA, signing only on pushes to `main`, PRs never write the Rust cache, release checks main is still at its commit and uploads `version.json` last, SBOMs for shell and engine, provenance attestations, `pip-audit` and `cargo audit`.
- **Shell commands.** `update_app` validates the commit, `open_link` allows https and mailto, `open_doc` uses an allowlist; Windows tools run from System32 by full path; the engine unpacks to staging and is renamed into place.
