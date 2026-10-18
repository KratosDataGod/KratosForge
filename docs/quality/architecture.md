# Architecture and standards in practice

What the code at `main` **3bee0db** actually does, the conventions it follows, and where those conventions contradict each other. This is the reference the [PR quality gate](pr-review.md) checks new work against: a PR follows the standards below, or says why not. For how the pieces are meant to fit, see [architecture.md](../architecture.md); this page records what was found.

## The application

| Part | Technology | Size | Where |
| --- | --- | --- | --- |
| Shell | Rust, Tauri 2, two WebView2 views | `main.rs` 1,029 lines, `watchdog.rs`, `updater.py` | `app/src-tauri/` |
| App UI | Plain HTML and JavaScript, no build step, all 29 scripts share one global scope; CodeMirror 6 vendored | 14,400 lines of JavaScript; `app.js` 2,640 | `app/ui/` |
| Engine | Python 3.11+, DuckDB, Polars, delta-rs, sqlglot, MSAL | 157 modules, 41,000 lines | `sidecar/fabric_desktop/` |
| Headless agent | The same engine, run by `cli.py` as a service or container | | `agent.py`, `deploy/` |
| CI and release | GitHub Actions: `ci.yml` (sidecar tests on Windows and Linux, UI tests, dependency audit, SBOMs, agent image, engine zip, shell build, release), `promote.yml`, `docs.yml`, `codex-review.yml` | | `.github/` |

### How a call flows

1. A page calls `rpc(method, params)` (`app.js:4`), which is `invoke("rpc", ...)`.
2. The shell's `rpc` command (`main.rs:298-324`) writes one JSON line to the engine's stdin and holds the pipe's mutex until the reply, so the engine sees one call at a time (SH-11).
3. `server.handle` (`server.py:93-104`) checks the name against `METHODS` (344 names), calls `getattr(service, method)(**params)`, and writes an audit line for anything not in `audit.READ_ONLY`.
4. `Service` (`service.py:55`) is 18 mixins, one per area (`meta_service.py`, `prove_service.py`, ...), 347 public methods.
5. Mixins call domain modules (`meta/`, `workloads/`, `sqlcompat/`, `localdata/`, `orchestration/`, `git/`, ...), which call `fabric_api.FabricClient` (REST) and `onelake.FileStore` / `storage_options` (DFS and delta-rs).

Long work leaves the RPC thread: notebook kernels and SQL runs are child processes (`kernel_proc.py`, `sql_exec.py`), Git network operations and background runs are threads (31 `threading.Thread(` sites), and the agent uses a SQLite job queue (`jobqueue.py`).

### Major subsystems and owners

| Subsystem | Code | Owning thread |
| --- | --- | --- |
| Notebooks, kernels, Spark and notebookutils shims, Prove, side by side, receipts | `notebook.py`, `kernel_*.py`, `spark_local.py`, `notebookutils_local.py`, `prove_service.py`, `proof*.py`, `side_by_side*.py`, `receipts.py`, `scorecard.py` | Prove this notebook program |
| SQL page and execution | `sql*.py`, `sqlcompat/` | SQL development (resolved; owners for SQL-004+ pending) |
| Metadata framework and environments | `meta/`, `meta_service.py`, `cloud_service.py`, `dataops_service.py`, `fabric_compute.py` | Roadmap (keys, releases, lineage), with hardening for unowned paths |
| Platform, governance, data mesh | `central/` | Enterprise governance; Data mesh |
| Monitor operations, agents, incidents, workloads | `workloads/`, `workload_service.py`, `incident_service.py`, `agent*.py`, `mailbox.py` | Workload management |
| Local data projects | `localdata/` | Local data engine |
| Pipelines | `orchestration/`, `pipeline_service.py` | Local pipeline execution |
| Cost and capacity | `costs.py`, `capacity.py`, `measurement.py` | FinOps |
| Source control | `git/`, `git_service.py` | Git-native development |
| MCP and local API | `integrations/` | Bring your own AI |
| Security, install, policy, audit, backup, telemetry | `auth.py`, `credentials.py`, `app_settings.py`, `audit.py`, `backup.py`, `telemetry.py`, shell | Enterprise readiness |
| X-ray, shortcuts, access, schedules | `xray*.py`, `shortcut*.py`, `access.py`, `schedule_service.py`, `estate_service.py` | No single owner; hardening thread takes fixes |

### Authentication

One delegated path and one unattended path, behind one interface:

- **Desktop:** `auth.Authenticator` (`auth.py:92`), an MSAL public client using Microsoft's Fabric CLI app id and WAM on Windows, with no app registration (standing decision). With Microsoft's app it asks for `<resource>/.default`; with an organization's own app, the narrow scopes listed in `auth.py:29-49`. Tokens are cached on the PC (`default_cache_path`).
- **Agent:** `credentials.py` (service principal or managed identity) answers the same calls (`fabric_token`, `storage_token`, `_token(scopes)`, `silent_token`).
- **Fabric view:** a separate WebView2 profile with Windows single sign-on (`main.rs`, `mod signin`).

The interface is consistent; its use isn't (QA-014): the private `_token(scopes)` is the call everyone needs, and clients are built at 43 sites.

### Fabric integration

| Surface | Module | Retry and errors |
| --- | --- | --- |
| Fabric REST (`api.fabric.microsoft.com`) | `fabric_api.FabricClient`, 41 methods; also `central/client.py` with its own `API` constant | 429 with `Retry-After`; no 5xx retry; `FabricError(code, status)` |
| OneLake DFS | `onelake.FileStore`; `leases.py` builds its own headers | none in `FileStore` |
| Delta tables | delta-rs via `storage_options`, opened directly in five mixins | Delta optimistic concurrency, `retrying.py` |
| Power BI and Graph | raw `requests` in `estate_service.py:355,389,393` | none |
| Fabric portal | the shell's Fabric view | n/a |

### Tests

`sidecar/tests/` (76 files, 1,405 tests, 87% coverage, hand-written fakes), `app/tests/ui.test.cjs` (51 tests that extract functions by regex and run them in `vm`, or match source text), `app/tests/browser/` (Playwright against the real UI with the shell stubbed; not in CI), Rust unit tests (not in CI), `livetest.py` (a guarded live-tenant harness, run by hand only, never run so far).

## Standards the code follows

These hold almost everywhere and new work should keep them.

| Area | Standard | Where it's defined |
| --- | --- | --- |
| Process boundary | Every UI action is one named RPC; the engine never listens on a network port except the opt-in local API | `server.py`, `integrations/http_server.py` |
| Allowlist | A method is callable only if it's in `server.METHODS` | `server.py:20` |
| Audit | Every call that isn't read-only gets one audit line, with content fields removed | `audit.py`, `server._audit` |
| Engine layout | One `*_service.py` mixin per area holds the RPC methods; logic lives in plain modules or a package | `service.py:55` |
| RPC names | `snake_case`, area prefix (`meta_`, `git_`, `platform_`, `local_`, `sql_`) | `server.py` |
| UI pages | Register once with `registerFeature`; at most 8 rail items; Preview tag from `PREVIEW_FEATURES` | `features.js`, `docs/ui-guidelines.md` |
| UI building | One element builder `h()` and one `esc()`; three `innerHTML` sinks, all escaped | `app.js` |
| UI errors | `attempt()` shows a toast with `friendlyError()`; metadata pages use `metaCall()` | `app.js:72`, `activity.js:43`, `meta.js:56` |
| Writes to OneLake | Stage locally, check the Fabric-safe Delta profile, write with delta-rs, record in run history | `lakehouse.write_table`, `publish.py` |
| Dependencies | Pinned in `constraints.txt`, moved only by their own PR | `CONTRIBUTING.md` |
| Docs | User docs in `docs/`, linked from both READMEs; permissions in `docs/permissions.md` | `CONTRIBUTING.md` |
| Words | "Fabric Desktop by KratosBI" in text; plain-language errors | memory, `CONTRIBUTING.md` |

## Where the standards contradict each other

| Concern | Competing patterns | Finding |
| --- | --- | --- |
| Persisting a JSON file | ~9 atomic writes with three temp-name schemes; ~16 in-place writes, including `settings.json` and schedules | QA-011 |
| Settings | Two files side by side, `app-settings.json` (`app_settings.py`) and `settings.json` (`meta_service._settings`, written by ~8 mixins), plus `policy.json` read by two loaders, `agent.toml`, `integrations` config, `engine.json`, `project.toml` | QA-011; PR #165 merges the policy loaders |
| SQLite | Six connection idioms with different durability and locking | QA-022 |
| Transient errors | Five classifiers with different status sets and attribute names | QA-006 |
| Audit trail | Two chain writers, one safe across processes | QA-012 |
| Redaction | Four functions with different patterns | QA-016 |
| Fabric clients | 23 `FabricClient(` and 20 `FileStore(` constructions; raw `requests` in three places | QA-014 |
| Background runs | Thread plus JSONL history re-written per feature (Prove, side by side), job queue, child processes | QA-023 |
| Teams alerts | Outbox with retry, and fire-and-forget | QA-021 |
| Time | 13 helpers; local and UTC run ids | QA-024 |
| Method metadata | `METHODS`, `READ_ONLY` and the UI's own lists kept by hand | QA-015 |
| "Not configured" vs "failed" | One `except Exception` for both, 98 times | QA-013 |
| UI confirmation, tabs, pills, grids, polling, errors | Re-built per page | QA-019 |
| UI errors | `attempt`, `metaCall`, `platformCall`, silent `.catch` | QA-019 |

## Consolidation plan

The rule for each step: extend the implementation that already works; don't add a fourth pattern. Each is one PR in the [remediation plan](remediation.md), and none changes behavior a user relies on except to remove a defect.

| Step | Keep | Retire | PR |
| --- | --- | --- | --- |
| 1 | A new `fsutil.write_json_atomic` / `read_json` (mkstemp + `os.replace`, corrupt file moved aside) | the 25 hand-written JSON writes | PR-QA-06 |
| 2 | `integrations/store.py`'s connection idiom, with `jobqueue`'s `synchronous=full` option | four other idioms | PR-QA-06 |
| 3 | `integrations/audit.py`'s locked chain writer | `AuditLog`'s in-memory head | PR-QA-06 |
| 4 | `redact.secrets`, extended with `git/secrets.PATTERNS` | the fallback copy and `livetest.scrub` | PR-QA-06 |
| 5 | `retrying.TRANSIENT_STATUS` and `is_retryable`, reading `FabricError.status` | four classifiers | PR-QA-05 |
| 6 | `workloads/model` time helpers, moved to `timeutil` | 12 copies | PR-QA-06 |
| 7 | `Service.fabric` / `Service.files` factories and a public `token(scopes)` | 43 constructions, `_token`, raw `requests` | PR-QA-08 |
| 8 | A `BackgroundRuns` helper from `prove_service` | the side-by-side copy | PR-QA-08 |
| 9 | Method metadata declared beside each method | hand-kept `METHODS` and `READ_ONLY` | PR-QA-09 |
| 10 | `workloads/notify` outbox | `post_teams` fire and forget | PR-QA-07 |
| 11 | `confirmDialog` (moved to `app.js`, with `danger`), `attempt`, `metaCall` | 12 confirm copies, `platformCall`, silent catches | PR-QA-11 |
| 12 | One `tabs()`, `segmented()`, accessible `showMenu` | 24 hand-built controls | PR-QA-12 |
| 13 | One `statusPill`, `resultGrid` with `cellText`, `activity.js` time helpers, `pollSidecar` | 10 pill helpers, 5 grids, 4 formatters, ~10 loops | PR-QA-13 |

What not to do: split `Service` into separate processes, introduce a dependency-injection framework, or build a UI framework. The mixin layout is acceptable once the mixins stop re-implementing infrastructure.
