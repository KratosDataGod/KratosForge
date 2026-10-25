# Feature inventory

This is the registry of what KratosForge actually does, how sure we are, and what
should happen next for each capability. It is the starting point for every product decision in
[the product backlog](PRODUCT_BACKLOG.md) and [the prioritized roadmap](PRIORITIZED_ROADMAP.md).

- **Source of truth:** [FEATURE_INVENTORY.json](FEATURE_INVENTORY.json), validated by
  [schema/feature-inventory.schema.json](schema/feature-inventory.schema.json).
- **This page:** the tables between `BEGIN GENERATED` and `END GENERATED` markers are written by
  `python docs/product/tools/product_data.py render`. Edit the JSON, never those tables.
- **Assessed against:** main at commit `b7b04cc` on 2026-10-17, then refreshed against `9715e28`
  the same day for the PRs merged in between (#89 to #134). The refresh re-read only the features
  those PRs touched and added four new rows. Refreshed again against `be3e45b` on 2026-10-22 for
  the PRs merged from #135 to #559: 25 rows were added for features those PRs shipped, and the
  rows whose status they changed were re-read. Rows neither refresh touched still describe `b7b04cc`.
  Merged is not accepted: these rows wait for the implementation review.

## How this inventory was taken

| What was done | Result |
| --- | --- |
| Read the engine (`sidecar/fabric_desktop`), the UI (`app/ui`), the shell (`app/src-tauri`) and the workflows | Every feature row names the files that implement it |
| Ran the engine test suite with `pytest` on Linux, Python 3.13 | 656 passed, 2 skipped (TPC-H extension, one POSIX-only test) |
| Ran the UI tests with `node --test app/tests/ui.test.cjs` | 23 passed |
| Tried `cargo test` in `app/src-tauri` | Could not build on Linux (`gdk-3.0` missing). CI never runs `cargo test` either, so the Rust tests in `watchdog.rs` and `main.rs` are unverified |
| Drove the engine over its stdin protocol and the `fabric-desktop` CLI with local data only | Settings, notebooks, cell runs, Spark shim, jobs preview, metadata init/run/tests/promote check, CLI help, agent check and support bundle behaved as described in each row's evidence |
| Compared each page in `docs/` with the code | Mismatches are listed as limitations and collected in backlog items PB-010 and PB-077 |

| Refresh at `9715e28`: re-read the code of every PR merged since `b7b04cc`, reran the engine and UI tests | Engine tests: 1405 passed, 2 skipped. UI tests: 51 passed. Browser tests in `app/tests/browser` not run (Playwright not installed; CI does not run them either) |
| Refresh at `be3e45b` (2026-10-22): read the PRs merged from #135 to #559, added rows for what they shipped and re-read rows they changed; ran the engine, UI and browser tests | Engine tests: 3926 passed, 74 skipped (Linux, Python 3.13). `ui.test.cjs` passed. Browser tests: 285 passed (Playwright, Chromium). No tenant, no Windows app |

**Not done:** no Fabric tenant was used, the Windows app was not started, and CI was not run.
Nothing in this inventory claims that local execution is equivalent to Fabric execution; that claim
needs a passing Prove run for the notebook in question.

## Status and verification

Status says what exists. Verification says how sure we are, and is tracked separately so a feature
can be Implemented and still Unverified.

| Status | Meaning |
| --- | --- |
| Implemented | Works as described through its intended surface |
| Partially Implemented | Some of the described capability exists |
| Implemented but Not Exposed | Code exists, but its intended user can't reach it (no screen, no command) |
| Implemented but Unreliable | Exposed, but a known defect makes results untrustworthy |
| Documented Only | Described in docs with no code |
| Missing | Neither docs nor code |
| Planned | An approved plan or open PR exists, but nothing is on main |
| Deprecated | Kept for compatibility and being replaced |

| Verification | Meaning |
| --- | --- |
| Verified | The behavior a user receives was observed working in this assessment, end to end, on its target environment |
| Partially Verified | Automated tests exercise the logic (usually with Fabric faked), or part of the flow was observed |
| Unverified | Code reading only, or someone else's report |

Evidence kinds: `test` (a test that ran and passed here), `smoke` (observed by driving the engine or
CLI here), `code` (read in the source), `doc` (stated in docs), `report` (someone else's
observation, such as the maintainer's tenant), `absent` (looked for and not found).

Because the Windows app and Fabric were out of reach, only features whose user-facing surface is the
engine boundary or the CLI could reach Verified. That is why Verified is small; it is not a judgement
on quality.

<!-- BEGIN GENERATED: summary -->
| Status | Verified | Partially Verified | Unverified | Total |
| --- | ---: | ---: | ---: | ---: |
| Implemented | 2 | 109 | 4 | 115 |
| Partially Implemented | 1 | 9 | 1 | 11 |
| Implemented but Not Exposed | 0 | 6 | 0 | 6 |
| Missing | 0 | 0 | 4 | 4 |
| Planned | 0 | 0 | 4 | 4 |
| **Total** | **3** | **124** | **13** | **140** |
<!-- END GENERATED: summary -->

## What the inventory shows

1. **Breadth is real, live proof is not.** Most features are Implemented and backed by passing
   tests, but those tests fake Fabric. The live test harness (FEAT-OPS-04) has never run, so every
   Fabric-facing row stays Partially Verified until the tenant smoke test (PB-002).
2. **Some capabilities are built but unreachable.** At `b7b04cc` these were the TOML job runner,
   column lineage, reliability objectives and agent status records; since then column lineage
   (#124), agent status (#133) and the incident methods (#125) gained screens, and local data
   projects (FEAT-LDE-01) arrived with no screen yet. A text search of `app/ui` at `b7b04cc` found no caller for
   30 of the 275 methods in the engine's allowlist, including `meta_preflight`, `meta_lineage`,
   the six incident methods, `platform_revoke`, `receipts_list` and
   `run_job`. Some may be called through computed names, so treat 30 as an upper bound.
3. **One feature gives wrong answers:** the compute check flags notebooks that use
   `notebookutils.variableLibrary` as Fabric-only, although they run on the PC (FEAT-NB-07).
   Related parity defects are listed under FEAT-NB-04, FEAT-NB-05, FEAT-SQL-02 and FEAT-ORC-01.
4. **The Rust shell is untested in CI.** The watchdog, embedded sign-in and update integrity checks
   have tests that never run (FEAT-SHL-03, FEAT-FAB-05, FEAT-SHL-09).
5. **Docs and code disagree in places**, mostly docs lagging behind code (security.md says there is
   no redaction or SBOM; agent.md says workload views read agent status) and a few where docs promise
   more than code does (Report a problem, endorsed items, Preview labels).
6. **Specialist programs own most open work.** Planned rows point at the Git-native, enterprise
   readiness, AI integration, SQL, governance and FinOps workstreams rather than restating them.

## Feature index

<!-- BEGIN GENERATED: index -->
### Local Development and Execution

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-NB-02](#feat-nb-02) | Run cells on this PC | Implemented | Partially Verified | Notebook page | Keep. |
| [FEAT-NB-04](#feat-nb-04) | Spark DataFrame API on this PC | Partially Implemented | Partially Verified | Notebook cells | Close the gaps the supported-API table in notebook-spark-on-this-pc.md lists; MERGE, UPDATE and DELETE still run in Fabric only (PB-015). |
| [FEAT-NB-05](#feat-nb-05) | notebookutils and mssparkutils on this PC | Partially Implemented | Partially Verified | Notebook cells | fs.mkdirs on a file raises and fs.fastcp copies folders by default, as in Fabric; run the Preview rows on a tenant. |
| [FEAT-NB-07](#feat-nb-07) | Where a notebook can run (compute check) | Implemented | Partially Verified | Notebook ribbon > Runs on | AST analyzer with an Unknown outcome (PB-014). |
| [FEAT-PRV-04](#feat-prv-04) | Environment profile | Partially Implemented | Partially Verified | Prove > Packages only (Preview) | Reproducible environments wait on the package-source decision (FD-109). |
| [FEAT-META-02](#feat-meta-02) | Metadata runs on this PC | Implemented | Partially Verified | Data mesh > Entities > Run | Keep; add late-arriving rows and source deletes (FD-117). |
| [FEAT-META-14](#feat-meta-14) | Environments: Tables in and Runs on | Implemented | Partially Verified | Data mesh > Deploy > Environments | Verify the runner notebook on a tenant (PB-002). |
| [FEAT-LDE-01](#feat-lde-01) | Local data projects | Implemented but Not Exposed | Partially Verified | None yet (engine calls only) (Preview) | local_query, local_explain, Delta writes and transformation steps are checked like a managed local run; screens still wait on the mockup. |
| [FEAT-NB-21](#feat-nb-21) | Notebook environments | Implemented | Partially Verified | Notebook status bar > Environment (Preview) | Implementation review of PR #421, #539; run on a tenant before the Preview tag comes off. |
| [FEAT-NB-22](#feat-nb-22) | Notebook sessions | Implemented | Partially Verified | Notebook status bar > Sessions (Preview) | Implementation review of PR #561; run on a tenant before the Preview tag comes off. |
| [FEAT-NB-26](#feat-nb-26) | Databricks notebooks on this PC | Implemented | Partially Verified | Notebook cells | Implementation review of PR #371. |

### SQL, Python, and Notebook Development

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-11](#feat-fab-11) | Fabric Warehouse development | Partially Implemented | Partially Verified | Workspaces > warehouse > Warehouse page (Preview) | WH-06 saved queries and WH-07 history, then phase 3 build and deploy after Chris's go. |
| [FEAT-NB-01](#feat-nb-01) | Notebooks on this PC | Implemented | Partially Verified | Create > Notebook, notebook page | The Parameters bar now applies to Fabric and side by side runs (#635), which closes Chris's 2026-10-23 'later' ask. |
| [FEAT-NB-06](#feat-nb-06) | Run other notebooks and magics | Implemented | Partially Verified | Notebook cells | Implementation review of the magics PRs. |
| [FEAT-SQL-01](#feat-sql-01) | SQL query page | Implemented | Partially Verified | Workspaces > lakehouse > SQL query | SQL editor rows are tracked by the warehouse program now (WH-08 Messages and Chart merged). |
| [FEAT-SQL-02](#feat-sql-02) | %%sql cells | Partially Implemented | Partially Verified | Notebook cells | Make a MERGE/UPDATE/DELETE cell end with a visible 'skipped on this PC' status instead of ok (PB-013). |
| [FEAT-EXT-04](#feat-ext-04) | Debugger for notebooks | Partially Implemented | Partially Verified | Notebook line numbers, Variables pane, View tab (Preview) | Implementation review of PR #387; decide whether pipeline runs need step-through. |
| [FEAT-SQL-03](#feat-sql-03) | Fabric SQL dialect compatibility analyzer | Implemented | Partially Verified | SQL page | Compare its verdicts with a Fabric Warehouse run on a tenant (HD-054) |
| [FEAT-NB-12](#feat-nb-12) | Notebook import and export formats | Implemented | Partially Verified | Home > Import; a notebook's Home tab > Export (Preview) | Implementation review of PR #350; run on a tenant before the Preview tag comes off. |
| [FEAT-NB-13](#feat-nb-13) | Notebook folders | Implemented | Partially Verified | All notebooks | Implementation review of PR #444. |
| [FEAT-NB-14](#feat-nb-14) | Notebook version history | Implemented | Partially Verified | Notebook ribbon > Versions | Implementation review of PR #346, #461. |
| [FEAT-NB-15](#feat-nb-15) | Notebook comments | Implemented | Partially Verified | Notebook cells and the Comments panel | Implementation review of PR #388. |
| [FEAT-NB-16](#feat-nb-16) | Notebook resources | Implemented | Partially Verified | Notebook > Resources | Implementation review of PR #376. |
| [FEAT-NB-17](#feat-nb-17) | Notebook files and editing Fabric notebooks on this PC | Implemented | Partially Verified | All notebooks; Workspaces item preview > Edit on this PC (Preview) | Implementation review of PR #320; run on a tenant before the Preview tag comes off. |
| [FEAT-NB-25](#feat-nb-25) | Rich notebook outputs and charts | Implemented | Partially Verified | Notebook cell output (Preview) | Implementation review of PR #385, #386, #427, #430, #448, #472, #479, #490, #546, #552; run on a tenant before the Preview tag comes off. |

### Data Engineering and Transformation

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-05](#feat-one-05) | Fabric-safe Delta writes to OneLake | Implemented | Partially Verified | Every write path | #612 SF10 streaming in quality per memory; Direct Lake on zstd is Chris's call (Snappy fallback); verify MERGE on OneLake in the live test (PB-003). |
| [FEAT-NB-11](#feat-nb-11) | TOML job runner | Implemented but Not Exposed | Partially Verified | CLI only (fabric-desktop preview\|run) | Decide: expose jobs in the app or mark them a CLI/agent feature and document so (PB-079). |
| [FEAT-META-01](#feat-meta-01) | Metadata project, layers and entities | Implemented | Partially Verified | Data mesh > Design | Keep. |
| [FEAT-META-04](#feat-meta-04) | Custom code: hooks, steps and Python checks | Implemented | Partially Verified | Entity TOML | Keep. |

### Pipelines and Orchestration

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-08](#feat-fab-08) | Fabric schedules | Implemented | Partially Verified | Notebook ribbon, Monitor > Schedules (Preview) | Allow editing a schedule's time; use the quiet token path in all_schedules. |
| [FEAT-FAB-10](#feat-fab-10) | Data pipelines, Dataflows and Copy jobs authoring | Missing | Unverified | None (listed, opened in Fabric, run-now from Monitor > Schedules) | Data Factory plan DF-2..DF-10 (Chris's go 2026-10-24 for the program; UI waits on the mockup). Local pipeline runs are FEAT-ORC-08. |
| [FEAT-MESH-03](#feat-mesh-03) | Landing zone and source registry | Partially Implemented | Partially Verified | Platform > Landing zone (Preview) | Guided ingestion still waits on D2 (FD-116); Data Factory copy jobs (DF-6) may answer it. |
| [FEAT-ORC-01](#feat-orc-01) | PC schedules | Implemented | Partially Verified | Notebook ribbon, Data mesh, Monitor > Schedules (Preview) | Agent schedules cover Linux; Windows Task Scheduler runs need a Windows check. |
| [FEAT-ORC-02](#feat-orc-02) | Headless agent | Implemented | Partially Verified | CLI and agent.toml (no UI) | Verify on a tenant; add queue pruning and requeue of dead jobs (PB-044). |
| [FEAT-ORC-04](#feat-orc-04) | Fabric pipeline mailbox | Implemented | Partially Verified | Fabric pipeline notebook + agent (Preview) | Run once on a tenant; record the writer's identity. |
| [FEAT-ORC-06](#feat-orc-06) | Multiple workers | Partially Implemented | Partially Verified | agent.toml settings; no screen (Preview) | Run the mailbox on a tenant (PB-049), then build the app labels (multi-worker.md step 4). |
| [FEAT-ORC-07](#feat-orc-07) | Fabric pipeline definitions on this PC | Implemented | Partially Verified | fabric-desktop pipeline (CLI) and the external-tool capabilities; no screen | ORCH-002 activity registry, after Chris approves |
| [FEAT-ORC-08](#feat-orc-08) | Running pipelines on this PC | Implemented | Partially Verified | Create > Pipeline, Pipelines page, Monitor > Runs (Preview) | P5 Copy data (pipeline thread) after Chris's go; implementation review of #563, #568, #578, #579. |
| [FEAT-ORC-09](#feat-orc-09) | Pipeline activity compatibility registry | Implemented | Partially Verified | fabric-desktop pipeline (CLI); no screen | Registry v7 on main; review with ORC-08. |
| [FEAT-ORC-10](#feat-orc-10) | Fabric pipeline runs and activity runs | Implemented but Not Exposed | Partially Verified | None yet: Monitor > Runs rows wait on the Data Factory mockup (Preview) | DF-1 screen in Monitor > Runs once Chris reacts to the Data Factory mockup. |

### Fabric Integration and Deployment

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-01](#feat-fab-01) | Fabric REST client | Implemented | Partially Verified | Used by most pages | Run the live test harness on a non-production workspace (PB-002). |
| [FEAT-FAB-02](#feat-fab-02) | Workspaces page and item listing | Implemented | Partially Verified | Workspaces | Keep; offer actions beyond Open for non-lakehouse items when their workstreams add them. |
| [FEAT-FAB-03](#feat-fab-03) | Create lakehouses | Implemented | Partially Verified | Create, Home, workspace page, command bar | Keep. |
| [FEAT-FAB-05](#feat-fab-05) | Embedded Fabric portal and Monitoring hub | Implemented | Unverified | Fabric, Monitor | Restrict in-view navigation to Fabric hosts and add cargo test to CI (PB-071). |
| [FEAT-FAB-06](#feat-fab-06) | Publish notebooks to Fabric | Implemented | Partially Verified | Notebook ribbon > Publish to OneLake | Include publish after a rename in the tenant smoke test. |
| [FEAT-FAB-07](#feat-fab-07) | Run in Fabric | Implemented | Partially Verified | Notebook ribbon (Runs on: Fabric) (Preview) | Offer Cancel in Monitor > Runs and the tray (PB-043) and include in the tenant smoke test. |
| [FEAT-FAB-09](#feat-fab-09) | Fabric Environment items and Spark settings | Missing | Unverified | None | Plan with the Prove program's environment profiles (FD-109) once the package-source decision is made (PB-016). |
| [FEAT-FAB-12](#feat-fab-12) | Fabric workspace Git integration | Planned | Unverified | None | Owned by the Git-native program (Phase 2). |
| [FEAT-FAB-13](#feat-fab-13) | Deployment pipelines and item-definition deploy | Planned | Unverified | None | Owned by the Git-native program (Phase 3, FD-120); reuse metadata promote gates. |
| [FEAT-ONE-01](#feat-one-01) | OneLake catalog | Implemented | Partially Verified | OneLake > Catalog | Unify environment-word lists between Python and JS (catalog-groups.js vs shortcut_center.py). |
| [FEAT-META-08](#feat-meta-08) | Compare, meaningful diffs and promote impact | Implemented | Partially Verified | Entity > Compare, Deploy > promote impact | Batch the compare for large tables. |
| [FEAT-META-09](#feat-meta-09) | Promote Dev to Test to Prod | Implemented | Partially Verified | Data mesh > Deploy > Environments | Release rollback (scale plan 5.3) and decision D1 (require a pushed commit for Test/Prod). |
| [FEAT-META-10](#feat-meta-10) | Release records | Implemented | Partially Verified | Promote card notes | Merge PR #113 (release records read Git through the Git layer); add a release list view. |
| [FEAT-META-13](#feat-meta-13) | Direct Lake semantic model | Implemented | Partially Verified | Data mesh > Deploy > Semantic model (Preview) | Include in the tenant smoke test. |
| [FEAT-NB-23](#feat-nb-23) | Run the last cell on Fabric | Implemented | Partially Verified | Notebook cell ⋯ menu > Run on Fabric (Spark) (Preview) | Fabric step reads V-Order from file footers, Rewrite all asks for V-Order and its cost, risk check reads the attached workspace; run on a tenant before Preview comes off. |
| [FEAT-META-19](#feat-meta-19) | Semantic models to and from Apache Ossie | Implemented but Not Exposed | Partially Verified | None yet: Deploy > Semantic model card waits on the mockup (Preview) | UI card after Chris reacts to the mockup and decides whether Import may create new models. |

### Enterprise Shared Services

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-03](#feat-one-03) | OneLake shortcut command center | Implemented | Partially Verified | OneLake > Shortcuts (Preview) | Scheduled health check with a Teams alert (PB-053). |
| [FEAT-META-05](#feat-meta-05) | Surrogate keys | Implemented | Partially Verified | Data mesh > Surrogate keys | Stop reading whole key tables on every keyed run (FD-115). |
| [FEAT-META-15](#feat-meta-15) | Variable library and source systems | Implemented | Partially Verified | Data mesh > Source systems | Keep. |
| [FEAT-MESH-01](#feat-mesh-01) | Central platform | Implemented | Partially Verified | Platform (Preview) | Verify setup, the UDF executing_user and T-SQL on a tenant (platform.md lists these unverified). |
| [FEAT-MESH-02](#feat-mesh-02) | Central business keys | Implemented | Partially Verified | Data mesh > Surrogate keys > Move keys, Platform > Business keys (Preview) | Staged roadmap moves keys to one shared keys database in a dedicated workspace, keeping existing key numbers (item 25, decided 2026-10-20) |
| [FEAT-MESH-04](#feat-mesh-04) | Data mesh: domains, products, links, glossary | Implemented | Partially Verified | Data mesh > Domains, OneLake > Data products, Home (Preview) | Purview and endorsement live checks wait on Chris's tenant run; implementation review of #591, #627, #642. |

### Data Governance and Quality

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-02](#feat-one-02) | Endorsed items scope | Missing | Unverified | OneLake > Catalog (greyed out) | Endorsement now shows on data products (#627, FEAT-MESH-04); a catalog filter is still missing (PB-083). |
| [FEAT-ONE-04](#feat-one-04) | OneLake access review and data access roles | Implemented | Partially Verified | OneLake > Access (Preview) | Include in the tenant smoke test. |
| [FEAT-PRV-02](#feat-prv-02) | Output verification rules | Implemented | Partially Verified | Prove panel (Preview) | Reuse for SQL and metadata compare (one comparison engine). |
| [FEAT-META-03](#feat-meta-03) | Checks, quarantine and replay | Implemented | Partially Verified | Entity page > Quarantine | Keep. |
| [FEAT-META-07](#feat-meta-07) | Schema drift review | Implemented | Partially Verified | Entity page > Drift | Cover landing and link sources (inference: not read). |
| [FEAT-META-16](#feat-meta-16) | Profiling and rule suggestions | Implemented | Partially Verified | Entity > Profile | Keep. |
| [FEAT-META-17](#feat-meta-17) | Column lineage | Implemented | Partially Verified | Entity page > Lineage > Columns (Preview) | Implementation review of PR #124. |
| [FEAT-GOV-01](#feat-gov-01) | Freshness promises and Teams alerts | Implemented | Partially Verified | Entity > Freshness, Home | Merge with incidents so a late table posts once (agent.md notes duplicates). |
| [FEAT-GOV-02](#feat-gov-02) | Project tests gate promote | Implemented | Partially Verified | Data mesh > Quality > Tests (Preview) | Fold into the Git program's validation framework (FD-113). |
| [FEAT-GOV-03](#feat-gov-03) | Governance exceptions | Implemented | Partially Verified | Metadata project, Platform > Governance > Requests (Preview) | Implementation review of #603 and #605 (PB-059). |
| [FEAT-GOV-05](#feat-gov-05) | Enterprise governance policy registry and offline validation | Partially Implemented | Partially Verified | None | GOV-002 offline validation, after Chris approves |
| [FEAT-GOV-06](#feat-gov-06) | Sensitive data classification | Missing | Unverified | None | Owned by the governance foundation thread; complement Purview, don't duplicate it. |
| [FEAT-GOV-07](#feat-gov-07) | Reliability objectives | Implemented but Not Exposed | Partially Verified | None (agent incidents only) | Show in the workload page with incidents (PB-047). |
| [FEAT-MESH-05](#feat-mesh-05) | Breaking-change check for data products | Implemented | Partially Verified | Promote card | Consumer declarations and deprecation (scale plan 8.2). |
| [FEAT-GOV-08](#feat-gov-08) | Governance requests | Implemented | Partially Verified | Platform > Governance > Requests (Preview) | Governance steps 8 and 12 wait on Chris's two read-only PC checks. |

### Developer Experience and Productivity

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-04](#feat-shl-04) | Left rail, pages and command bar | Implemented | Partially Verified | Rail, Ctrl+K | Keep; add keyboard tests for the command bar. |
| [FEAT-SHL-05](#feat-shl-05) | Preview tags and hiding Preview features | Implemented | Partially Verified | Settings > Preview features | Add Preview ids for batch_by, release records, who-can-promote, objectives and column lineage, which docs call Preview but code doesn't tag (PB-078). |
| [FEAT-SHL-06](#feat-shl-06) | Plain-language errors and activity tray | Implemented | Partially Verified | Top bar bell | Feed incidents and agent status into the bell once incident screens exist (PB-047). |
| [FEAT-FAB-04](#feat-fab-04) | Item preview panel | Implemented | Partially Verified | Workspaces > item (Preview) | Clean up downloaded preview files (inference: never removed). |
| [FEAT-NB-08](#feat-nb-08) | Autocomplete and variables pane | Implemented | Partially Verified | Notebook editor, View > Variables | Column completion for attached tables (Prove backlog #12). |
| [FEAT-NB-09](#feat-nb-09) | Cell dependencies and stale cells | Implemented | Partially Verified | Notebook View and Run tabs (Preview) | Add UI to declare depends_on (engine reads it, no UI sets it); mark stale on a new Delta version. |
| [FEAT-NB-10](#feat-nb-10) | New notebook wizard | Implemented | Partially Verified | Create > Notebook from a template (Preview) | Add the entry point from a lakehouse table (plan Stage 3). |
| [FEAT-ADM-04](#feat-adm-04) | Ready to work checklist | Implemented | Partially Verified | None | Implementation review of PR #94 |
| [FEAT-REL-02](#feat-rel-02) | Documentation site | Implemented | Unverified | GitHub Pages | Docs audit 3 due at about #662 (cadence every 50 merged PRs). |
| [FEAT-GIT-01](#feat-git-01) | Source control | Implemented | Partially Verified | Source control (bottom of the rail) (Preview) | Owned by the Git-native program. |
| [FEAT-NB-18](#feat-nb-18) | Notebook cell editing | Implemented | Partially Verified | Notebook editor | Implementation review of PR #338, #347, #383, #390, #473. |
| [FEAT-NB-19](#feat-nb-19) | Notebook IntelliSense | Implemented | Partially Verified | Notebook code cells | Implementation review of PR #354. |
| [FEAT-NB-20](#feat-nb-20) | Format code in notebooks | Implemented | Partially Verified | Notebook Edit tab > Format cell, Format notebook | Implementation review of PR #470, #486, #491. |
| [FEAT-DEV-01](#feat-dev-01) | Profile | Implemented | Partially Verified | Account menu > Profile (Preview) | Proof Forged from Prove, Journey and Achievements tabs (KF-DX-002); implementation review of #598. |
| [FEAT-DEV-02](#feat-dev-02) | Motion and effects | Implemented | Partially Verified | Settings > Motion and effects | Draft #449 style guide waits on Chris; keep effects subordinate to work (brief section 8). |
| [FEAT-DEV-03](#feat-dev-03) | Pop-out windows | Implemented | Partially Verified | Monitor > Pop out (Preview) | Implementation review of PR #557, #558; run on a tenant before the Preview tag comes off. |

### Enterprise Administration and FinOps

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-08](#feat-shl-08) | Settings and the organization policy file | Implemented | Partially Verified | Settings (locked values show 'Set by your organization') | Missing keys through the enterprise program (PB-061); #569 closed the ten audit bugs #411 to #420. |
| [FEAT-SHL-10](#feat-shl-10) | Report a problem with redaction | Implemented | Partially Verified | Account menu, Settings > Help, command bar | Fix the user guide, which says it adds the tenant id (it redacts it). |
| [FEAT-SHL-11](#feat-shl-11) | Support bundle | Partially Implemented | Verified | CLI only (fabric-desktop support-bundle) | Add 'Save support bundle' with a contents preview to Settings, as FD-138's acceptance asks (PB-062). |
| [FEAT-PRV-05](#feat-prv-05) | Scorecard and run measurement | Implemented | Partially Verified | Prove > Full scorecard (Preview) | Measure the kernel child process when it is on (FD-119). |
| [FEAT-META-18](#feat-meta-18) | Estate X-ray and adopt | Implemented | Partially Verified | Home > Estate X-ray (Preview) | Keep. |
| [FEAT-ORC-05](#feat-orc-05) | Agent status records | Implemented | Partially Verified | Monitor > Operations > Agents (Preview) | Implementation review of PR #133. |
| [FEAT-OPS-01](#feat-ops-01) | Workload inventory and workload page | Implemented | Partially Verified | Monitor > Workloads, Home > Your workloads (Preview) | Shared ownership (stage 2B); an 'on agents' view. |
| [FEAT-OPS-02](#feat-ops-02) | Incidents | Implemented | Partially Verified | Monitor > Incidents (Chris used it in unit testing, 2026-10-18) | Simplified incident management (PB-105) builds on these incidents; implementation review of PRs #125 and #127 |
| [FEAT-OPS-03](#feat-ops-03) | Monitor: runs from every place | Implemented | Partially Verified | Monitor > Runs | Fabric pipeline runs (FEAT-ORC-10) join this list after the Data Factory mockup. |
| [FEAT-ADM-01](#feat-adm-01) | Cost estimates | Implemented | Partially Verified | Optimize > Cost and savings (Preview) | Calibrate against the Capacity Metrics app and label every number (PB-050) |
| [FEAT-ADM-06](#feat-adm-06) | Opt-in telemetry | Implemented | Partially Verified | None (Preview) | Implementation review of PR #121; nothing is sent until someone opts in and a connection string exists |
| [FEAT-ADM-07](#feat-adm-07) | Capacity of each environment | Implemented | Partially Verified | Optimize > Cost and savings; Data mesh > Environments (Preview) | Check against a live tenant's capacities (PB-002) |
| [FEAT-ADM-08](#feat-adm-08) | Risk check before a run | Implemented | Partially Verified | Run in Fabric and Data mesh deploy confirms; Settings (Preview) | Run on a tenant before the Preview tag comes off. |
| [FEAT-ADM-09](#feat-adm-09) | Where to run a notebook (placement advice) | Implemented | Partially Verified | MCP and local API tool fabric_desktop_placement_advice (read only); no screen yet (Preview) | Implementation review of PR #276; run on a tenant before the Preview tag comes off. |
| [FEAT-ADM-10](#feat-adm-10) | Measured usage import | Implemented | Partially Verified | Optimize > Cost and savings (Preview) | Implementation review of PR #153; run on a tenant before the Preview tag comes off. |
| [FEAT-ADM-11](#feat-adm-11) | Optimize and the central lakehouse | Implemented | Partially Verified | Optimize (Preview) | Settings history can include capacity overrides for administrators (#613); not yet set up on a tenant. |

### Extensibility, APIs, CLI, and MCP

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-EXT-01](#feat-ext-01) | Command-line interface | Implemented | Verified | Terminal | Add backup/restore (PR #91) and an mcp command (PR-AI-002). |
| [FEAT-EXT-02](#feat-ext-02) | MCP server and local API for external AI tools | Implemented | Partially Verified | None | Run the client checklist per named AI tool (PB-096); HD-006 and HD-014 harden approvals and workspace limits |
| [FEAT-EXT-03](#feat-ext-03) | Extensions | Planned | Unverified | None | Owned by the Git-native program (FD-132..135). |
| [FEAT-EXT-05](#feat-ext-05) | Engines and connection profiles | Implemented but Not Exposed | Partially Verified | None: the engine underneath; no screen yet (Preview) | Screens after a mockup; never install databricks-connect into the engine (ADR-004). |

### Security and Enterprise Readiness

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-02](#feat-shl-02) | RPC method allowlist and error redaction | Implemented | Verified | Engine boundary (no UI) | Keep; generate METHODS from the capability registry once PR-AI-003 merges so the two lists can't drift. |
| [FEAT-SHL-07](#feat-shl-07) | Accessibility basics | Implemented | Partially Verified | Whole app | Run an assistive-technology audit on Windows (Narrator) and record results (FD-139 follow-up). |
| [FEAT-SHL-09](#feat-shl-09) | Self-update, channels and engine install | Implemented | Partially Verified | Startup check, Settings > Updates | Code signing (PB-001); Rust tests in CI (PB-071). |
| [FEAT-AUTH-01](#feat-auth-01) | Sign-in with no app registration | Implemented | Partially Verified | Sign-in callout, account menu | Include in the tenant smoke test (PB-002). |
| [FEAT-AUTH-02](#feat-auth-02) | Own app registration and tenant allow-list | Implemented | Partially Verified | Settings > Account | Keep. |
| [FEAT-AUTH-03](#feat-auth-03) | Token cache | Implemented | Partially Verified | None (background) | Chris restarts and signs in on Windows on Stable 3633b4a to confirm (PB-005 review). |
| [FEAT-AUTH-04](#feat-auth-04) | Unattended credentials for the agent | Implemented | Partially Verified | agent.toml (no UI) | Verify a service principal on a tenant with the live test's --agent-config (PB-002). |
| [FEAT-PRV-03](#feat-prv-03) | Run receipts | Implemented | Partially Verified | Prove > Receipt (Preview) | Feed receipts into the audit trail (FD-125, PR #89). |
| [FEAT-GOV-04](#feat-gov-04) | Who can promote and separation of duties | Implemented | Partially Verified | Promote card | Keep; compare signed-in identities rather than PC login names. |
| [FEAT-ADM-02](#feat-adm-02) | Audit trail | Implemented | Partially Verified | None | HD-017: the chain notices lines deleted from the end, and the user guide says how to clear a reported gap. |
| [FEAT-ADM-05](#feat-adm-05) | Local data retention | Implemented | Partially Verified | None | Implementation review of PR #95 |
| [FEAT-SEC-01](#feat-sec-01) | Code signing | Planned | Unverified | None | Chris creates the Azure Trusted Signing account (D-C3) (PB-001). |
| [FEAT-SEC-02](#feat-sec-02) | Content Security Policy | Implemented | Partially Verified | None | HD-052 checks URL sinks in the UI and shows only picture types as images; check under WebView2 on Windows. |
| [FEAT-SEC-03](#feat-sec-03) | Supply chain: pinned packages, audit, SBOM, provenance | Implemented | Unverified | Releases | Fix docs/security.md, which still says there is no scanning, SBOM or provenance; pin docs.yml and codex-review.yml actions by SHA. |

### Reliability, Testing, and Performance

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-01](#feat-shl-01) | Desktop shell and local engine | Implemented | Partially Verified | Whole app | Run the Windows smoke test of ten core flows on the maintainer's tenant (PB-002). |
| [FEAT-SHL-03](#feat-shl-03) | Watchdog time limits on engine calls | Partially Implemented | Unverified | Every page (implicit) | Move long calls (sign-in, Fabric calls) off the single pipe (PB-072); cargo test runs in CI since #244. |
| [FEAT-NB-03](#feat-nb-03) | Notebooks in their own process | Implemented | Partially Verified | Settings > Preview features (off by default) (Preview) | Make it the default after a Windows trial; add per-cell time and memory limits (Prove FD-110). |
| [FEAT-PRV-01](#feat-prv-01) | Prove this notebook | Implemented | Partially Verified | Notebook ribbon > Prove (Preview) | Run one proof on the tenant (PB-002); add the column heatmap from plan Stage 3. |
| [FEAT-PRV-06](#feat-prv-06) | Side by side and the TPC-H benchmark | Implemented | Partially Verified | Notebook ribbon > Side by side (Preview) | Chris's SF1 local run is 65 s vs 101 s before (1.9x); keep the benchmark and retire correctness checks into Prove (PB-076). |
| [FEAT-META-06](#feat-meta-06) | Large tables: preflight, batches, merge writes and file manifest | Implemented | Partially Verified | Automatic in runs; batch_by in entity TOML | Build the run dialog (mockup first) and vacuum orphan files after a stopped batch (FD-114). |
| [FEAT-META-11](#feat-meta-11) | Table history and restore | Implemented | Partially Verified | Entity > History (Preview) | Keep; downstream rebuild guidance in the dialog. |
| [FEAT-META-12](#feat-meta-12) | Table health, optimize and retention | Implemented | Partially Verified | Data mesh > Quality > Table health (Preview) | Keep. |
| [FEAT-ORC-03](#feat-orc-03) | OneLake write leases | Implemented | Partially Verified | None (agent) | HD-016: a writer checks its leases right before the commit; OneLake conditional requests still need the live test (PB-003). |
| [FEAT-OPS-04](#feat-ops-04) | Live test harness | Implemented | Partially Verified | CLI only | Chris runs it on a non-production workspace (PB-002); it now runs the notebook in Fabric only after it published as written. |
| [FEAT-ADM-03](#feat-adm-03) | Backup and restore of local state | Implemented | Partially Verified | None | Implementation review of PR #91; HD-005 (restore removes the stale WAL and verifies) is a P0 fix |
| [FEAT-REL-01](#feat-rel-01) | Continuous release channels | Implemented | Unverified | Settings > Updates | Per-build release assets for pinning and rollback (PB-064). |
| [FEAT-NB-24](#feat-nb-24) | Validate a notebook | Implemented | Partially Verified | Notebook ribbon > Validate (Preview) | Implementation review of PR #282; run on a tenant before the Preview tag comes off. |
<!-- END GENERATED: index -->

## Feature details

<!-- BEGIN GENERATED: details -->
### FEAT-SHL-01

**Desktop shell and local engine.** Tauri 2 exe hosting the plain-JS UI and a Python engine spoken to over JSON lines on stdin/stdout, with no network listener.

- **Pillar and category:** Platform foundation; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Whole app
- **Evidence:** test: sidecar/tests/test_local.py::test_server_reports_errors_without_crashing (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_server_keeps_prints_out_of_the_protocol_and_speaks_utf8; smoke: python -m fabric_desktop.server driven over stdin in this assessment (environment, notebooks, cells, jobs, metadata calls answered; an unknown method was refused); absent: Windows app not run: this assessment ran on Linux; app/src-tauri cargo test could not build (gdk-3.0 missing)
- **Source:** `app/src-tauri/src/main.rs`, `sidecar/fabric_desktop/server.py`, `sidecar/fabric_desktop/service.py`
- **Tests:** `sidecar/tests/test_local.py`, `sidecar/tests/test_notebook.py`
- **Docs:** [docs/architecture.md](../architecture.md)
- **Known limitations:** All RPC calls are serialized behind one mutex (main.rs rpc); calls outside the watchdog's quick list have no time limit, so a long Fabric call blocks every page. Windows only for the desktop app; the engine and agent also run on Linux.
- **Owning workstream:** Master product (foundation)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-072](PRODUCT_BACKLOG.md#pb-072)
- **Recommended next action:** Run the Windows smoke test of ten core flows on the maintainer's tenant (PB-002).

### FEAT-SHL-02

**RPC method allowlist and error redaction.** server.py only dispatches methods named in METHODS and passes every error through redact.secrets before it reaches the UI.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Verified.
- **Where people reach it:** Engine boundary (no UI)
- **Evidence:** test: sidecar/tests/test_redact.py::test_errors_sent_to_the_ui_carry_no_tokens (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: no_such_method call returned "unknown method 'no_such_method'"
- **Source:** `sidecar/fabric_desktop/server.py`, `sidecar/fabric_desktop/redact.py`
- **Tests:** `sidecar/tests/test_redact.py`
- **Docs:** [docs/security.md](../security.md)
- **Owning workstream:** Enterprise readiness
- **Backlog:** [PB-010](PRODUCT_BACKLOG.md#pb-010), [PB-098](PRODUCT_BACKLOG.md#pb-098)
- **Recommended next action:** Keep; generate METHODS from the capability registry once PR-AI-003 merges so the two lists can't drift.

### FEAT-SHL-03

**Watchdog time limits on engine calls.** Quick engine calls get a 60-second limit; a call that doesn't answer ends the engine instead of freezing the app.

- **Pillar and category:** Platform foundation; Reliability, Testing, and Performance
- **Status:** Partially Implemented. **Verification:** Unverified.
- **Where people reach it:** Every page (implicit)
- **Evidence:** code: app/src-tauri/src/watchdog.rs QUICK list and read_reply; absent: Rust tests in watchdog.rs are never run by CI (.github/workflows/ci.yml has cargo build, no cargo test) and could not build here
- **Source:** `app/src-tauri/src/watchdog.rs`, `app/src-tauri/src/main.rs`
- **Known limitations:** Only about 22 methods are on the quick list; sign-in, Fabric calls and runs have no limit.
- **Pull requests and issues:** [#83](https://github.com/KratosDataGod/KratosForge/pull/83), [#114](https://github.com/KratosDataGod/KratosForge/pull/114), [#115](https://github.com/KratosDataGod/KratosForge/pull/115)
- **Owning workstream:** Prove (FD-110)
- **Backlog:** [PB-071](PRODUCT_BACKLOG.md#pb-071), [PB-072](PRODUCT_BACKLOG.md#pb-072)
- **Recommended next action:** Move long calls (sign-in, Fabric calls) off the single pipe (PB-072); cargo test runs in CI since #244.

### FEAT-SHL-04

**Left rail, pages and command bar.** Pages and commands registered in features.js; an 8-item rail plus a bottom group; Ctrl+K command bar that finds pages, actions, notebooks, workspaces and entities.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Rail, Ctrl+K
- **Evidence:** test: app/tests/ui.test.cjs every UI script parses (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: app/ui/features.js registerFeature, paletteItems, openPalette
- **Source:** `app/ui/features.js`, `app/ui/app.js`
- **Tests:** `app/tests/ui.test.cjs`
- **Docs:** [docs/ui-guidelines.md](../ui-guidelines.md), [docs/user-guide.md](../user-guide.md)
- **Known limitations:** The rail is full (8 of 8); new features must live inside existing pages. Command bar listbox has no aria-activedescendant (inference).
- **Backlog:** [PB-063](PRODUCT_BACKLOG.md#pb-063), [PB-070](PRODUCT_BACKLOG.md#pb-070), [PB-102](PRODUCT_BACKLOG.md#pb-102)
- **Recommended next action:** Keep; add keyboard tests for the command bar.

### FEAT-SHL-05

**Preview tags and hiding Preview features.** Features not tried on a live tenant carry a Preview tag (19 ids) and can be hidden in Settings or by policy.

- **Pillar and category:** Platform foundation; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings > Preview features
- **Evidence:** test: app/tests/ui.test.cjs Workloads is a Preview feature (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: app/ui/features.js PREVIEW_FEATURES
- **Source:** `app/ui/features.js`, `app/ui/settings.js`
- **Tests:** `app/tests/ui.test.cjs`
- **Docs:** [docs/README.md](../README.md)
- **Known limitations:** Hiding only hides UI; engine methods stay callable. Several docs-Preview features have no PREVIEW_FEATURES id.
- **Backlog:** [PB-078](PRODUCT_BACKLOG.md#pb-078)
- **Recommended next action:** Add Preview ids for batch_by, release records, who-can-promote, objectives and column lineage, which docs call Preview but code doesn't tag (PB-078).

### FEAT-SHL-06

**Plain-language errors and activity tray.** friendlyError maps sign-in, HTTP and capacity errors to plain words; the bell and tray list this app's runs and problems.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Top bar bell
- **Evidence:** test: app/tests/ui.test.cjs an engine ended for not answering says so (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: app/ui/activity.js friendlyError, renderBell, trackFabricJobs
- **Source:** `app/ui/activity.js`
- **Tests:** `app/tests/ui.test.cjs`
- **Known limitations:** Tray lives in this webview's localStorage only (80 items). Not fed by incidents or agent status.
- **Pull requests and issues:** [#122](https://github.com/KratosDataGod/KratosForge/pull/122)
- **Backlog:** [PB-047](PRODUCT_BACKLOG.md#pb-047), [PB-102](PRODUCT_BACKLOG.md#pb-102)
- **Recommended next action:** Feed incidents and agent status into the bell once incident screens exist (PB-047).

### FEAT-SHL-07

**Accessibility basics.** Skip link, labelled icon buttons, modal focus trap, focus-visible and reduced-motion styles.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Whole app. Accessibility: Static checks only; no screen-reader or contrast audit recorded
- **Evidence:** test: app/tests/ui.test.cjs every icon-only button has a name (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs the page has a skip link and labelled controls; test: app/tests/ui.test.cjs dialogs are modal for keyboard users and focus shows
- **Source:** `app/ui/index.html`, `app/ui/app.js`, `app/ui/styles.css`
- **Tests:** `app/tests/ui.test.cjs`
- **Pull requests and issues:** [#92](https://github.com/KratosDataGod/KratosForge/pull/92)
- **Owning workstream:** Enterprise readiness (FD-139)
- **Backlog:** [PB-063](PRODUCT_BACKLOG.md#pb-063)
- **Recommended next action:** Run an assistive-technology audit on Windows (Narrator) and record results (FD-139 follow-up).

### FEAT-SHL-08

**Settings and the organization policy file.** Settings resolve policy > environment > user > default; policy.json can lock client id, channel, preview, kernel process, support URL, platform, auto update, Fabric runs and allowed tenants.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings (locked values show 'Set by your organization')
- **Evidence:** test: sidecar/tests/test_policy.py::test_policy_problems_are_reported_not_applied (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_policy.py::test_fabric_runs_can_be_turned_off; test: sidecar/tests/test_app_settings.py::test_policy_locks_a_setting; smoke: app_settings RPC returned each key with its source
- **Source:** `sidecar/fabric_desktop/app_settings.py`, `app/ui/settings.js`, `app/src-tauri/src/policy_file.rs`
- **Tests:** `sidecar/tests/test_policy.py`, `sidecar/tests/test_app_settings.py`
- **Docs:** [docs/azure-setup.md](../azure-setup.md), [docs/enterprise-install.md](../enterprise-install.md)
- **Known limitations:** The headless agent doesn't read the policy file. `platform` key not in azure-setup.md's key table.
- **Pull requests and issues:** [#90](https://github.com/KratosDataGod/KratosForge/pull/90), [#569](https://github.com/KratosDataGod/KratosForge/pull/569)
- **Owning workstream:** Enterprise readiness (FD-105)
- **Backlog:** [PB-061](PRODUCT_BACKLOG.md#pb-061)
- **Recommended next action:** Missing keys through the enterprise program (PB-061); #569 closed the ten audit bugs #411 to #420.

### FEAT-SHL-09

**Self-update, channels and engine install.** Stable and Preview channels from GitHub Releases, checked against version.json SHA-256 and their build commit; Check for updates in the profile menu updates on demand or offers the download by hand; the program file is kratosforge.exe.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Startup check, Settings > Updates
- **Evidence:** test: sidecar/tests/test_updater.py::test_download_app_checks_the_checksum (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_updater.py::test_download_app_refuses_when_the_checksum_cant_be_read; test: sidecar/tests/test_updater.py::test_no_stable_release_yet_follows_preview; test: sidecar/tests/test_whats_new.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_updater.py (passed in the 2026-10-17 refresh run at 9715e28); code: app/ui/settings.js showWhatsNew; test: sidecar/tests/test_updater.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/update-check.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `app/src-tauri/src/updater.py`, `app/src-tauri/src/main.rs`, `app/src-tauri/src/update_guard.rs`, `app/src-tauri/src/exe_name.rs`
- **Tests:** `sidecar/tests/test_updater.py`, `sidecar/tests/test_whats_new.py`, `app/tests/browser/update-check.test.cjs`
- **Docs:** [docs/install.md](../install.md), [docs/enterprise-install.md](../enterprise-install.md), [docs/security.md](../security.md)
- **Known limitations:** Checksums come from the same release, so they don't stop someone who can publish releases. Unsigned until Trusted Signing exists (PB-001). Rust update guard (OneDrive lock wait, engine folder kept while running) has no Linux test run; CI builds it on Windows.
- **Pull requests and issues:** [#51](https://github.com/KratosDataGod/KratosForge/pull/51), [#82](https://github.com/KratosDataGod/KratosForge/pull/82), [#132](https://github.com/KratosDataGod/KratosForge/pull/132), [#565](https://github.com/KratosDataGod/KratosForge/pull/565), [#566](https://github.com/KratosDataGod/KratosForge/pull/566), [#567](https://github.com/KratosDataGod/KratosForge/pull/567), [#569](https://github.com/KratosDataGod/KratosForge/pull/569), [#594](https://github.com/KratosDataGod/KratosForge/pull/594), [#602](https://github.com/KratosDataGod/KratosForge/pull/602), [#607](https://github.com/KratosDataGod/KratosForge/pull/607)
- **Owning workstream:** Fuller What's new dialog (PB-100) and Enterprise readiness
- **Backlog:** [PB-001](PRODUCT_BACKLOG.md#pb-001), [PB-064](PRODUCT_BACKLOG.md#pb-064), [PB-071](PRODUCT_BACKLOG.md#pb-071), [PB-100](PRODUCT_BACKLOG.md#pb-100), [PB-101](PRODUCT_BACKLOG.md#pb-101)
- **Recommended next action:** Code signing (PB-001); Rust tests in CI (PB-071).

### FEAT-SHL-10

**Report a problem with redaction.** Copies build, Python, page and recent errors with secrets, emails, user paths and GUIDs removed, to the policy's support URL or a GitHub issue.

- **Pillar and category:** Platform foundation; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Account menu, Settings > Help, command bar
- **Evidence:** test: sidecar/tests/test_redact.py::test_shared_cases (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs Report a problem takes out secrets
- **Source:** `app/ui/settings.js`, `sidecar/fabric_desktop/redact.py`, `app/tests/redact-cases.json`
- **Tests:** `sidecar/tests/test_redact.py`, `app/tests/ui.test.cjs`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Pull requests and issues:** [#84](https://github.com/KratosDataGod/KratosForge/pull/84)
- **Owning workstream:** Enterprise readiness (FD-131a, FD-138)
- **Backlog:** [PB-077](PRODUCT_BACKLOG.md#pb-077)
- **Recommended next action:** Fix the user guide, which says it adds the tenant id (it redacts it).

### FEAT-SHL-11

**Support bundle.** A zip of versions, settings sources and the last 2,000 log lines with personal details taken out.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Partially Implemented. **Verification:** Verified.
- **Where people reach it:** CLI only (fabric-desktop support-bundle)
- **Evidence:** test: sidecar/tests/test_redact.py::test_support_bundle_holds_versions_and_logs_with_personal_details_taken_out (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: fabric-desktop support-bundle --out <dir> wrote about.json and CONTENTS.txt
- **Source:** `sidecar/fabric_desktop/support_bundle.py`, `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_redact.py`
- **Known limitations:** No UI entry and no RPC. Non-Windows data folder differs from app_settings' folder.
- **Owning workstream:** Enterprise readiness (FD-138)
- **Backlog:** [PB-062](PRODUCT_BACKLOG.md#pb-062)
- **Recommended next action:** Add 'Save support bundle' with a contents preview to Settings, as FD-138's acceptance asks (PB-062).

### FEAT-AUTH-01

**Sign-in with no app registration.** MSAL public client using Microsoft's Fabric CLI app id, with Windows WAM single sign-on; tokens for Fabric and Storage.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Sign-in callout, account menu
- **Evidence:** test: sidecar/tests/test_local.py::test_microsofts_sign_in_asks_for_default_scopes_and_an_own_app_for_narrow_ones (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_local.py::test_signed_in_tenant_comes_from_the_msal_account; doc: docs/README.md (Not yet tried on a live tenant with Microsoft's app; the maintainer's tenant results used an own app registration)
- **Source:** `sidecar/fabric_desktop/auth.py`, `sidecar/fabric_desktop/service.py`
- **Tests:** `sidecar/tests/test_local.py`
- **Docs:** [docs/permissions.md](../permissions.md), [docs/troubleshooting.md](../troubleshooting.md)
- **Known limitations:** Depends on Microsoft keeping the Fabric CLI public client usable by third parties (review item 8). Personal accounts can't sign in (/organizations).
- **Pull requests and issues:** [#42](https://github.com/KratosDataGod/KratosForge/pull/42), [#111](https://github.com/KratosDataGod/KratosForge/pull/111), issue [#96](https://github.com/KratosDataGod/KratosForge/issues/96)
- **Owning workstream:** Enterprise readiness
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002)
- **Recommended next action:** Include in the tenant smoke test (PB-002).

### FEAT-AUTH-02

**Own app registration and tenant allow-list.** Optional own client id (policy, environment or Settings) with an admin-consent link; a policy list of allowed tenants signs out others.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings > Account
- **Evidence:** test: sidecar/tests/test_app_settings.py::test_client_id_order (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_policy.py::test_other_tenants_are_signed_out
- **Source:** `sidecar/fabric_desktop/app_settings.py`, `sidecar/fabric_desktop/auth.py`, `app/ui/settings.js`
- **Tests:** `sidecar/tests/test_app_settings.py`, `sidecar/tests/test_policy.py`
- **Docs:** [docs/permissions.md](../permissions.md), [docs/azure-setup.md](../azure-setup.md)
- **Known limitations:** Tenant is checked only after a token is issued.
- **Recommended next action:** Keep.

### FEAT-AUTH-03

**Token cache.** MSAL cache in the user profile, written atomically and encrypted with Windows DPAPI for the signed-in Windows user; a damaged cache is ignored.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None (background)
- **Evidence:** test: sidecar/tests/test_local.py::test_token_cache_survives_damage_and_is_swapped_in_whole (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_token_cache_dpapi.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/auth.py`
- **Tests:** `sidecar/tests/test_local.py`, `sidecar/tests/test_token_cache_dpapi.py`
- **Docs:** [docs/security.md](../security.md)
- **Known limitations:** DPAPI is checked by tests with the Windows call faked; not yet observed on Chris's PC after the Stable promotion (3633b4a).
- **Pull requests and issues:** [#593](https://github.com/KratosDataGod/KratosForge/pull/593)
- **Owning workstream:** Enterprise readiness (FD-131b)
- **Backlog:** [PB-005](PRODUCT_BACKLOG.md#pb-005)
- **Recommended next action:** Chris restarts and signs in on Windows on Stable 3633b4a to confirm (PB-005 review).

### FEAT-AUTH-04

**Unattended credentials for the agent.** Service principal (certificate or secret from an environment variable), managed identity, or a cached user for the headless agent.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** agent.toml (no UI)
- **Evidence:** test: sidecar/tests/test_agent.py::test_service_principal_asks_for_each_resource_default (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_agent.py::test_managed_identity_asks_for_the_resource; test: sidecar/tests/test_agent.py::test_service_principal_loads_a_pem_certificate
- **Source:** `sidecar/fabric_desktop/credentials.py`, `sidecar/fabric_desktop/runtime.py`
- **Tests:** `sidecar/tests/test_agent.py`
- **Docs:** [docs/agent.md](../agent.md), [docs/permissions.md](../permissions.md)
- **Known limitations:** No workload identity federation. Live service principal access to OneLake unverified (roadmap FD-1).
- **Pull requests and issues:** [#34](https://github.com/KratosDataGod/KratosForge/pull/34)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002)
- **Recommended next action:** Verify a service principal on a tenant with the live test's --agent-config (PB-002).

### FEAT-FAB-01

**Fabric REST client.** Workspaces, capacities, lakehouses, items, definitions, notebooks, jobs, schedules, table maintenance, semantic models, SQL endpoint refresh, domains, role assignments, data access roles, shortcuts, connections and variable libraries; 429 retry and long-running operation polling.

- **Pillar and category:** Platform foundation; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Used by most pages
- **Evidence:** test: sidecar/tests/test_fabric_api.py::test_create_lakehouse_enables_schemas_and_follows_lro (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_fabric_api.py::test_create_workspace_puts_it_on_a_capacity_and_explains_a_taken_name; doc: docs/README.md (Listing workspaces and items used on the maintainer's tenant)
- **Source:** `sidecar/fabric_desktop/fabric_api.py`
- **Tests:** `sidecar/tests/test_fabric_api.py`, `sidecar/tests/test_cloud.py`
- **Docs:** [docs/permissions.md](../permissions.md)
- **Known limitations:** No retry on 5xx or 401. Long-running operations give up after 300 s. POWERBI_ADMIN constant unused.
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-051](PRODUCT_BACKLOG.md#pb-051)
- **Recommended next action:** Run the live test harness on a non-production workspace (PB-002).

### FEAT-FAB-02

**Workspaces page and item listing.** Lists workspaces with local favorites and pins, a workspace's lakehouses and items, and a lakehouse explorer tree.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Workspaces
- **Evidence:** test: sidecar/tests/test_local.py::test_workspace_marks_keep_favorites_and_pin_order (sidecar suite passed in this assessment (656 passed, 2 skipped)); doc: docs/README.md (used on the maintainer's tenant)
- **Source:** `sidecar/fabric_desktop/service.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_local.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Workspace page lists lakehouses, not all items. Favorites and pins are local only. No 'New workspace' outside metadata and platform flows.
- **Recommended next action:** Keep; offer actions beyond Open for non-lakehouse items when their workstreams add them.

### FEAT-FAB-03

**Create lakehouses.** Creates schema-enabled lakehouses with Fabric's name rule checked first (letters, numbers, underscores).

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Create, Home, workspace page, command bar
- **Evidence:** test: sidecar/tests/test_fabric_api.py::test_create_lakehouse_refuses_a_name_fabric_would_reject_before_calling_fabric (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs a lakehouse name Fabric would refuse is caught
- **Source:** `sidecar/fabric_desktop/fabric_api.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_fabric_api.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Always schema-enabled; no delete in the UI.
- **Pull requests and issues:** [#109](https://github.com/KratosDataGod/KratosForge/pull/109)
- **Recommended next action:** Keep.

### FEAT-FAB-04

**Item preview panel.** Previews tables (100 rows plus count from the Delta log), files and notebook cells; other item types embed Fabric's own page.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Workspaces > item (Preview)
- **Evidence:** test: sidecar/tests/test_preview.py::test_table_preview_rows_types_and_count (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_preview.py::test_service_guards_files
- **Source:** `sidecar/fabric_desktop/preview.py`, `sidecar/fabric_desktop/preview_service.py`, `app/ui/item-preview.js`
- **Tests:** `sidecar/tests/test_preview.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Files download whole into the app data folder and are not cleaned up (inference). Notebook preview shows no outputs.
- **Pull requests and issues:** [#43](https://github.com/KratosDataGod/KratosForge/pull/43)
- **Backlog:** [PB-087](PRODUCT_BACKLOG.md#pb-087)
- **Recommended next action:** Clean up downloaded preview files (inference: never removed).

### FEAT-FAB-05

**Embedded Fabric portal and Monitoring hub.** A second WebView2 showing app.fabric.microsoft.com (Fabric page) and the Monitoring hub (Monitor), with sign-in cookies saved under DPAPI.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** Fabric, Monitor
- **Evidence:** code: app/src-tauri/src/main.rs fabric_view_show, mod signin, dpapi; report: docs/README.md: embedded Fabric pages and saved sign-in used on the maintainer's tenant; absent: Rust signin tests never run by CI
- **Source:** `app/src-tauri/src/main.rs`, `app/ui/app.js`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Host allow-list covers any *.microsoft.com and *.live.com. Windows only.
- **Backlog:** [PB-008](PRODUCT_BACKLOG.md#pb-008), [PB-071](PRODUCT_BACKLOG.md#pb-071)
- **Recommended next action:** Restrict in-view navigation to Fabric hosts and add cargo test to CI (PB-071).

### FEAT-FAB-06

**Publish notebooks to Fabric.** Creates or updates a Fabric notebook; notebooks using the lakehouse module get a setup cell that makes them run in a Fabric Python notebook.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Publish to OneLake
- **Evidence:** test: sidecar/tests/test_notebook_publish.py::test_publish_takes_the_app_payload_and_defaults_to_the_lakehouse_workspace (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_cloud.py::test_notebook_using_the_local_module_gets_a_setup_cell_and_a_python_kernel
- **Source:** `sidecar/fabric_desktop/fabric_run.py`, `sidecar/fabric_desktop/cloud_service.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_notebook_publish.py`, `sidecar/tests/test_cloud.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** After the first publish, the item is matched by its recorded id (PB-018); not yet checked against a live tenant. The setup cell pip-installs sqlglot in Fabric. notebooks.md says %%sql notebooks publish as Spark; code converts them (inference).
- **Pull requests and issues:** [#31](https://github.com/KratosDataGod/KratosForge/pull/31)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-018](PRODUCT_BACKLOG.md#pb-018), [PB-077](PRODUCT_BACKLOG.md#pb-077)
- **Recommended next action:** Include publish after a rename in the tenant smoke test.

### FEAT-FAB-07

**Run in Fabric.** Publishes and runs a notebook with Fabric's job scheduler and tracks it in Monitor and the tray; IT can turn it off by policy.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon (Runs on: Fabric) (Preview)
- **Evidence:** test: sidecar/tests/test_cloud.py::test_run_item_job_returns_the_job_id_from_location (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_policy.py::test_fabric_runs_can_be_turned_off
- **Source:** `sidecar/fabric_desktop/cloud_service.py`, `sidecar/fabric_desktop/fabric_api.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_cloud.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Cancel is reachable only as a workload action in Monitor > Workloads, not from Monitor > Runs or the tray. UI requires an attached lakehouse.
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-043](PRODUCT_BACKLOG.md#pb-043)
- **Recommended next action:** Offer Cancel in Monitor > Runs and the tray (PB-043) and include in the tenant smoke test.

### FEAT-FAB-08

**Fabric schedules.** Create, list, pause and delete schedules for notebooks; pause, delete and run-now for pipelines and Spark job definitions in workspaces you work in.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon, Monitor > Schedules (Preview)
- **Evidence:** test: sidecar/tests/test_cloud.py::test_schedule_configuration (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_schedules.py::test_one_list_has_this_pc_and_fabric
- **Source:** `sidecar/fabric_desktop/schedule_service.py`, `sidecar/fabric_desktop/fabric_run.py`, `app/ui/schedules.js`
- **Tests:** `sidecar/tests/test_schedules.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** No edit of a schedule's time. Remove has no confirmation. 12 hard-coded time zones. all_schedules may prompt a sign-in (inference).
- **Backlog:** [PB-048](PRODUCT_BACKLOG.md#pb-048)
- **Recommended next action:** Allow editing a schedule's time; use the quiet token path in all_schedules.

### FEAT-FAB-09

**Fabric Environment items and Spark settings.** Attach a notebook to a Fabric Environment item, read its libraries and Spark settings, or create one.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no Environment item calls in sidecar/fabric_desktop/fabric_api.py
- **Owning workstream:** Prove (FD-109)
- **Backlog:** [PB-016](PRODUCT_BACKLOG.md#pb-016)
- **Recommended next action:** Plan with the Prove program's environment profiles (FD-109) once the package-source decision is made (PB-016).

### FEAT-FAB-10

**Data pipelines, Dataflows and Copy jobs authoring.** Create or edit Fabric Data pipelines, Dataflow Gen2 or Copy jobs.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None (listed, opened in Fabric, run-now from Monitor > Schedules)
- **Evidence:** absent: fabric_api.py has no pipeline, dataflow or copy job create/update
- **Owning workstream:** Build full Data Factory experience (DF-1..10)
- **Backlog:** [PB-030](PRODUCT_BACKLOG.md#pb-030), [PB-035](PRODUCT_BACKLOG.md#pb-035)
- **Recommended next action:** Data Factory plan DF-2..DF-10 (Chris's go 2026-10-24 for the program; UI waits on the mockup). Local pipeline runs are FEAT-ORC-08.

### FEAT-FAB-11

**Fabric Warehouse development.** A Warehouse page opened from Workspaces: read-only T-SQL on a Fabric warehouse or SQL analytics endpoint through Microsoft's mssql-python driver, an object explorer with Script as, Messages and a quick Chart, and SQL templates (built-in and the user's own .sql files, shareable through Git). Preview.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Workspaces > warehouse > Warehouse page (Preview)
- **Evidence:** doc: SQL development sprint thread (SQL-001..006), 2026-10-17; test: sidecar/tests/test_warehouse_queries.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_warehouse_driver.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_sql_templates.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/warehouse-page.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/sql-templates.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/warehouse.py`, `sidecar/fabric_desktop/sql_worker.py`, `sidecar/fabric_desktop/sql_templates_service.py`, `app/ui/warehouse.js`, `app/ui/query.js`, `app/ui/sqltemplates.js`, `app/ui/sqlchart.js`
- **Tests:** `sidecar/tests/test_warehouse_queries.py`, `sidecar/tests/test_warehouse_driver.py`, `sidecar/tests/test_sql_templates.py`, `app/tests/browser/warehouse-page.test.cjs`, `app/tests/browser/sql-templates.test.cjs`
- **Docs:** [docs/warehouse.md](../warehouse.md), [docs/sql-execution.md](../sql-execution.md)
- **Known limitations:** Read-only: writes wait on Chris's decision D1. Not yet run against a live warehouse (decision D3). One result set per run: the engine refuses a second statement. No build or deploy (WH-09..14), no Fabric SQL database, no Snowflake.
- **Pull requests and issues:** [#580](https://github.com/KratosDataGod/KratosForge/pull/580), [#581](https://github.com/KratosDataGod/KratosForge/pull/581), [#636](https://github.com/KratosDataGod/KratosForge/pull/636), [#637](https://github.com/KratosDataGod/KratosForge/pull/637), [#638](https://github.com/KratosDataGod/KratosForge/pull/638), [#639](https://github.com/KratosDataGod/KratosForge/pull/639), [#641](https://github.com/KratosDataGod/KratosForge/pull/641), [#645](https://github.com/KratosDataGod/KratosForge/pull/645)
- **Owning workstream:** Build full warehouse development experience (WH-01..21)
- **Backlog:** [PB-020](PRODUCT_BACKLOG.md#pb-020), [PB-022](PRODUCT_BACKLOG.md#pb-022), [PB-024](PRODUCT_BACKLOG.md#pb-024), [PB-025](PRODUCT_BACKLOG.md#pb-025)
- **Recommended next action:** WH-06 saved queries and WH-07 history, then phase 3 build and deploy after Chris's go.

### FEAT-FAB-12

**Fabric workspace Git integration.** Show and drive a workspace's Git connection, status, commit and update from Git.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: docs/git-integration-plan.md (Phase 2, waiting on Chris's go)
- **Owning workstream:** Git-native development
- **Backlog:** [PB-040](PRODUCT_BACKLOG.md#pb-040)
- **Recommended next action:** Owned by the Git-native program (Phase 2).

### FEAT-FAB-13

**Deployment pipelines and item-definition deploy.** Deploy items between workspaces through Fabric deployment pipelines or item definition APIs with preview and validation.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: docs/git-integration-plan.md (Phase 3)
- **Owning workstream:** Git-native development
- **Backlog:** [PB-041](PRODUCT_BACKLOG.md#pb-041)
- **Recommended next action:** Owned by the Git-native program (Phase 3, FD-120); reuse metadata promote gates.

### FEAT-ONE-01

**OneLake catalog.** Lakehouses across workspaces in one list, table search, group by environment and solution, local favorites.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** OneLake > Catalog
- **Evidence:** test: sidecar/tests/test_local.py::test_catalog_lists_lakehouses_across_workspaces_and_keeps_favorites (sidecar suite passed in this assessment (656 passed, 2 skipped)); doc: docs/README.md (used on the maintainer's tenant)
- **Source:** `sidecar/fabric_desktop/service.py`, `app/ui/app.js`, `app/ui/catalog-groups.js`
- **Tests:** `sidecar/tests/test_local.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Table names cached once per session; errors swallowed.
- **Pull requests and issues:** [#43](https://github.com/KratosDataGod/KratosForge/pull/43)
- **Recommended next action:** Unify environment-word lists between Python and JS (catalog-groups.js vs shortcut_center.py).

### FEAT-ONE-02

**Endorsed items scope.** Filter the catalog to endorsed (promoted, certified) items.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** OneLake > Catalog (greyed out)
- **Evidence:** code: sidecar/fabric_desktop/service.py endorsements always returns available:false
- **Source:** `sidecar/fabric_desktop/service.py`
- **Docs:** [docs/troubleshooting.md](../troubleshooting.md)
- **Backlog:** [PB-083](PRODUCT_BACKLOG.md#pb-083)
- **Recommended next action:** Endorsement now shows on data products (#627, FEAT-MESH-04); a catalog filter is still missing (PB-083).

### FEAT-ONE-03

**OneLake shortcut command center.** Every shortcut with health checks, readers, rules (no Dev to Prod), change history with undo, repoint (one or many), finding a moved lakehouse, accepting a crossed environment, and requests.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** OneLake > Shortcuts (Preview)
- **Evidence:** test: sidecar/tests/test_shortcut_center.py::test_repoint_puts_the_old_shortcut_back_when_the_new_one_fails (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_shortcut_center.py::test_rules_stop_dev_pointing_at_prod_and_flag_it; test: sidecar/tests/test_shortcut_fixes.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/shortcut-fixes.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/shortcut_service.py`, `sidecar/fabric_desktop/shortcut_center.py`, `app/ui/shortcuts.js`
- **Tests:** `sidecar/tests/test_shortcut_center.py`, `sidecar/tests/test_shortcut_fixes.py`, `app/tests/browser/shortcut-fixes.test.cjs`
- **Docs:** [docs/shortcuts.md](../shortcuts.md)
- **Known limitations:** No scheduled check or alert. Approved request creates the shortcut in one lakehouse only. OneLake error codes unseen on a tenant.
- **Pull requests and issues:** [#53](https://github.com/KratosDataGod/KratosForge/pull/53), [#595](https://github.com/KratosDataGod/KratosForge/pull/595)
- **Backlog:** [PB-053](PRODUCT_BACKLOG.md#pb-053)
- **Recommended next action:** Scheduled health check with a Teams alert (PB-053).

### FEAT-ONE-04

**OneLake access review and data access roles.** Who can read a lakehouse, workspace roles and OneLake data access roles, with grant by email.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** OneLake > Access (Preview)
- **Evidence:** test: sidecar/tests/test_estate.py::test_access_review_flags_what_needs_a_second_look (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_estate.py::test_data_access_roles_and_who_can_read
- **Source:** `sidecar/fabric_desktop/access.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`
- **Tests:** `sidecar/tests/test_estate.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Viewers can't list roles.
- **Recommended next action:** Include in the tenant smoke test.

### FEAT-ONE-05

**Fabric-safe Delta writes to OneLake.** Every write is staged locally, checked against the Fabric Delta profile and committed atomically with a version check. Notebook tables can be written on this PC and uploaded in the background (zstd, unchanged rewrites skipped); the log loads once per write; nothing from a write stays on disk; Fabric runs stream batch by batch.

- **Pillar and category:** Local development; Data Engineering and Transformation
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Every write path
- **Evidence:** test: sidecar/tests/test_meta.py::test_00b_tables_follow_the_fabric_delta_profile (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_local.py::test_profile_rejects_v2_checkpoints; test: sidecar/tests/test_agent.py::test_an_overwrite_from_a_stale_read_is_refused; doc: docs/README.md (writing tables to OneLake used on the maintainer's tenant); test: sidecar/tests/test_onelake_writes.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_delta_profile_writes.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/onelake-writes.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/publish.py`, `sidecar/fabric_desktop/delta_profile.py`, `sidecar/fabric_desktop/meta/store.py`, `sidecar/fabric_desktop/write_queue.py`
- **Tests:** `sidecar/tests/test_meta.py`, `sidecar/tests/test_local.py`, `sidecar/tests/test_onelake_writes.py`, `sidecar/tests/test_delta_profile_writes.py`, `app/tests/browser/onelake-writes.test.cjs`
- **Docs:** [docs/architecture.md](../architecture.md)
- **Known limitations:** Writes Delta features Fabric reads; newer Delta features are refused rather than written.
- **Pull requests and issues:** [#51](https://github.com/KratosDataGod/KratosForge/pull/51), [#54](https://github.com/KratosDataGod/KratosForge/pull/54), [#588](https://github.com/KratosDataGod/KratosForge/pull/588), [#609](https://github.com/KratosDataGod/KratosForge/pull/609), [#610](https://github.com/KratosDataGod/KratosForge/pull/610), [#612](https://github.com/KratosDataGod/KratosForge/pull/612), [#622](https://github.com/KratosDataGod/KratosForge/pull/622), [#625](https://github.com/KratosDataGod/KratosForge/pull/625)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-003](PRODUCT_BACKLOG.md#pb-003), [PB-099](PRODUCT_BACKLOG.md#pb-099), [PB-101](PRODUCT_BACKLOG.md#pb-101)
- **Recommended next action:** #612 SF10 streaming in quality per memory; Direct Lake on zstd is Chris's call (Snappy fallback); verify MERGE on OneLake in the live test (PB-003).

### FEAT-NB-01

**Notebooks on this PC.** Create, open, save (ipynb, stable cell ids, other tools' metadata kept), rename and delete notebooks; ribbon, command mode, parameters, freeze, outline, find and replace.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Create > Notebook, notebook page
- **Evidence:** test: sidecar/tests/test_notebook.py::test_ipynb_round_trip_keeps_cells_and_lakehouse (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_saving_keeps_metadata_other_tools_wrote; smoke: new_notebook and list_notebooks over the engine pipe; test: sidecar/tests/test_notebook_publish.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/notebook.py`, `app/ui/notebook.js`
- **Tests:** `sidecar/tests/test_notebook.py`, `sidecar/tests/test_notebook_publish.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Outputs are not saved with the notebook. No notebook-editor UI tests.
- **Pull requests and issues:** [#30](https://github.com/KratosDataGod/KratosForge/pull/30), [#635](https://github.com/KratosDataGod/KratosForge/pull/635)
- **Backlog:** [PB-070](PRODUCT_BACKLOG.md#pb-070)
- **Recommended next action:** The Parameters bar now applies to Fabric and side by side runs (#635), which closes Chris's 2026-10-23 'later' ask.

### FEAT-NB-02

**Run cells on this PC.** One shared Python namespace, streamed output, cancel that keeps variables, tables, charts, HTML in sandboxed frames.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook page
- **Evidence:** test: sidecar/tests/test_notebook.py::test_kernel_keeps_state_and_shows_last_expression (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook_parity.py::test_output_streams_while_a_cell_runs_and_cancel_keeps_variables; smoke: run_cell ran DuckDB and Polars code, reported a ZeroDivisionError and kept the kernel alive
- **Source:** `sidecar/fabric_desktop/notebook.py`, `app/ui/notebook.js`
- **Tests:** `sidecar/tests/test_notebook.py`, `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** input() unsupported. One cell at a time. Stop in the in-engine kernel restarts the whole engine.
- **Recommended next action:** Keep.

### FEAT-NB-03

**Notebooks in their own process.** Optional child-process kernel: Stop or a crash ends only that process.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings > Preview features (off by default) (Preview)
- **Evidence:** test: sidecar/tests/test_kernel_process.py::test_cancel_keeps_variables_and_stop_ends_only_the_process (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_kernel_process.py::test_a_crash_ends_the_cell_with_a_reason_and_the_next_cell_gets_a_fresh_kernel; test: sidecar/tests/test_kernel_process.py (passed in the 2026-10-17 refresh run at 9715e28)
- **Source:** `sidecar/fabric_desktop/kernel_proc.py`, `sidecar/fabric_desktop/kernel_host.py`
- **Tests:** `sidecar/tests/test_kernel_process.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Off by default. No memory or CPU limits. Cell time and memory limits are Preview settings (PR #115).
- **Pull requests and issues:** [#67](https://github.com/KratosDataGod/KratosForge/pull/67), [#115](https://github.com/KratosDataGod/KratosForge/pull/115)
- **Owning workstream:** Prove (FD-110)
- **Backlog:** [PB-073](PRODUCT_BACKLOG.md#pb-073)
- **Recommended next action:** Make it the default after a Windows trial; add per-cell time and memory limits (Prove FD-110).

### FEAT-NB-04

**Spark DataFrame API on this PC.** `spark` is DuckDB's Spark-compatible API wired to the lakehouse, with KratosForge's additions written as the Spark SQL they stand for: windows, aggregates, pivot, time travel and history, structured streaming as availableNow (Preview), and Spark's argument checks. A supported-API table is published.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_spark_local.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_spark_compat.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_spark_writer.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_spark_stream.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_time_travel.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_parity.py::test_spark_reads_and_writes_lakehouse_tables (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook_parity.py::test_pyspark_runs_locally_but_scala_rdds_and_merge_stay_in_fabric; smoke: SparkSession.builder.getOrCreate().createDataFrame(...).filter(...).count() ran and returned 1; test: sidecar/tests/test_spark_compat.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/spark_local.py`, `sidecar/fabric_desktop/spark_compat.py`, `sidecar/fabric_desktop/spark_stream.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`, `sidecar/tests/test_spark_local.py`, `sidecar/tests/test_spark_compat.py`, `sidecar/tests/test_spark_writer.py`, `sidecar/tests/test_spark_stream.py`, `sidecar/tests/test_time_travel.py`
- **Docs:** [docs/notebooks.md](../notebooks.md), [docs/notebook-spark-on-this-pc.md](../notebook-spark-on-this-pc.md)
- **Known limitations:** MERGE INTO, UPDATE and DELETE raise. partitionBy and reader schema() are ignored. insertInto defaults to errorifexists, unlike Spark's append (inference). No RDD, UDF, streaming; Scala/R refused.
- **Pull requests and issues:** [#177](https://github.com/KratosDataGod/KratosForge/pull/177), [#393](https://github.com/KratosDataGod/KratosForge/pull/393), [#409](https://github.com/KratosDataGod/KratosForge/pull/409), [#422](https://github.com/KratosDataGod/KratosForge/pull/422), [#488](https://github.com/KratosDataGod/KratosForge/pull/488), [#497](https://github.com/KratosDataGod/KratosForge/pull/497), [#520](https://github.com/KratosDataGod/KratosForge/pull/520), [#534](https://github.com/KratosDataGod/KratosForge/pull/534), [#551](https://github.com/KratosDataGod/KratosForge/pull/551), [#592](https://github.com/KratosDataGod/KratosForge/pull/592), [#596](https://github.com/KratosDataGod/KratosForge/pull/596)
- **Backlog:** [PB-012](PRODUCT_BACKLOG.md#pb-012), [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-015](PRODUCT_BACKLOG.md#pb-015), [PB-103](PRODUCT_BACKLOG.md#pb-103)
- **Recommended next action:** Close the gaps the supported-API table in notebook-spark-on-this-pc.md lists; MERGE, UPDATE and DELETE still run in Fabric only (PB-015).

### FEAT-NB-05

**notebookutils and mssparkutils on this PC.** fs (ls, head, put, append, cp, mv, rm, mkdirs, exists), credentials (getToken, getSecret, isValidToken), notebook (run, exit, runMultiple, validateDAG; Fabric items read-only, Preview), runtime.context, session, lakehouse (read-only, Preview), udf.getFunctions (read-only, Preview), nbResPath and variableLibrary.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebookutils_fs.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebookutils_helpers.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_parity.py::test_notebookutils_fs_runtime_and_exit (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook_parity.py::test_notebookutils_get_token_uses_the_sign_in; smoke: notebookutils was present in a fresh kernel namespace; test: sidecar/tests/test_notebookutils_fs.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/notebookutils_local.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`, `sidecar/tests/test_notebookutils_fs.py`, `sidecar/tests/test_notebookutils_helpers.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** mount/unmount, warehouse and connections run in Fabric. runMultiple runs one notebook at a time. Changes to Fabric items (create, update, delete) run in Fabric.
- **Pull requests and issues:** [#446](https://github.com/KratosDataGod/KratosForge/pull/446), [#477](https://github.com/KratosDataGod/KratosForge/pull/477), [#481](https://github.com/KratosDataGod/KratosForge/pull/481), [#483](https://github.com/KratosDataGod/KratosForge/pull/483), [#541](https://github.com/KratosDataGod/KratosForge/pull/541), [#545](https://github.com/KratosDataGod/KratosForge/pull/545), [#547](https://github.com/KratosDataGod/KratosForge/pull/547), [#585](https://github.com/KratosDataGod/KratosForge/pull/585), [#633](https://github.com/KratosDataGod/KratosForge/pull/633)
- **Backlog:** [PB-012](PRODUCT_BACKLOG.md#pb-012), [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-103](PRODUCT_BACKLOG.md#pb-103)
- **Recommended next action:** fs.mkdirs on a file raises and fs.fastcp copies folders by default, as in Fabric; run the Preview rows on a tenant.

### FEAT-NB-06

**Run other notebooks and magics.** %run and notebookutils.notebook.run with parameters; %%sql with _sqldf, %sql inside Python, %pip into the notebook's environment, %env, %timeit, %who, %%writefile, %autoreload, %history and In/Out, and Databricks' %md/%sql/%python cells.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebook_magics.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_magics_windows.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_autoreload.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_history.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_notebook_parity.py::test_run_another_notebook_with_parameters_and_percent_run (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/notebook.py`, `sidecar/fabric_desktop/magics.py`, `sidecar/fabric_desktop/pip_magic.py`, `sidecar/fabric_desktop/autoreload.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`, `sidecar/tests/test_notebook_magics.py`, `sidecar/tests/test_notebook_magics_windows.py`, `sidecar/tests/test_notebook_autoreload.py`, `sidecar/tests/test_notebook_history.py`
- **Docs:** [docs/notebooks.md](../notebooks.md), [docs/notebook-magics.md](../notebook-magics.md)
- **Known limitations:** %%spark, %%sparkr and %%csharp run in Fabric.
- **Pull requests and issues:** [#352](https://github.com/KratosDataGod/KratosForge/pull/352), [#440](https://github.com/KratosDataGod/KratosForge/pull/440), [#467](https://github.com/KratosDataGod/KratosForge/pull/467), [#478](https://github.com/KratosDataGod/KratosForge/pull/478), [#513](https://github.com/KratosDataGod/KratosForge/pull/513), [#514](https://github.com/KratosDataGod/KratosForge/pull/514), [#515](https://github.com/KratosDataGod/KratosForge/pull/515), [#516](https://github.com/KratosDataGod/KratosForge/pull/516), [#517](https://github.com/KratosDataGod/KratosForge/pull/517), [#518](https://github.com/KratosDataGod/KratosForge/pull/518), [#519](https://github.com/KratosDataGod/KratosForge/pull/519)
- **Backlog:** [PB-028](PRODUCT_BACKLOG.md#pb-028), [PB-103](PRODUCT_BACKLOG.md#pb-103)
- **Recommended next action:** Implementation review of the magics PRs.

### FEAT-NB-07

**Where a notebook can run (compute check).** A saved This PC / Fabric choice per notebook and a static check naming the cells that block each side.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Runs on
- **Evidence:** test: sidecar/tests/test_fabric_compute.py (passed in the 2026-10-22 refresh run at be3e45b); code: sidecar/fabric_desktop/fabric_run.py _FABRIC_ONLY no longer lists variableLibrary; test: sidecar/tests/test_notebook.py::test_open_and_save_say_where_the_notebook_can_run (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: sidecar/fabric_desktop/fabric_run.py _FABRIC_ONLY lists notebookutils.variableLibrary, which notebookutils_local implements
- **Source:** `sidecar/fabric_desktop/fabric_run.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_notebook.py`, `sidecar/tests/test_fabric_compute.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Regex-based; flags variableLibrary notebooks as Fabric-only although they run on the PC. No 'unknown' outcome.
- **Pull requests and issues:** [#29](https://github.com/KratosDataGod/KratosForge/pull/29), [#410](https://github.com/KratosDataGod/KratosForge/pull/410)
- **Owning workstream:** Prove (FD-111)
- **Backlog:** [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-014](PRODUCT_BACKLOG.md#pb-014), [PB-017](PRODUCT_BACKLOG.md#pb-017)
- **Recommended next action:** AST analyzer with an Unknown outcome (PB-014).

### FEAT-NB-08

**Autocomplete and variables pane.** Completion from the live kernel plus table names; variables pane hides secret-looking values.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook editor, View > Variables
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_variables_hide_secrets_and_completion_lists_attributes (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: kernel_variables listed df; kernel_complete returned items only for names in the namespace; test: sidecar/tests/test_debugging.py (passed in the 2026-10-17 refresh run at 9715e28)
- **Source:** `sidecar/fabric_desktop/notebook.py`, `app/ui/notebook.js`, `app/ui/debugging.js`, `sidecar/fabric_desktop/kernel_host.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`, `sidecar/tests/test_debugging.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Completes only what the kernel has run. No completion when Runs on is Fabric. Variable history, watches and hidden-state warnings are Preview (PR #129).
- **Pull requests and issues:** [#129](https://github.com/KratosDataGod/KratosForge/pull/129)
- **Backlog:** [PB-027](PRODUCT_BACKLOG.md#pb-027)
- **Recommended next action:** Column completion for attached tables (Prove backlog #12).

### FEAT-NB-09

**Cell dependencies and stale cells.** Static analysis of what each cell defines, reads and writes; stale cells marked; Run stale cells asks before side effects.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook View and Run tabs (Preview)
- **Evidence:** test: sidecar/tests/test_depgraph.py::test_tables_written_by_one_cell_and_read_by_another (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs stale cells
- **Source:** `sidecar/fabric_desktop/depgraph.py`, `app/ui/deps.js`
- **Tests:** `sidecar/tests/test_depgraph.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Declared dependencies have no UI. External table changes don't mark cells stale.
- **Pull requests and issues:** [#68](https://github.com/KratosDataGod/KratosForge/pull/68)
- **Owning workstream:** Prove
- **Backlog:** [PB-085](PRODUCT_BACKLOG.md#pb-085)
- **Recommended next action:** Add UI to declare depends_on (engine reads it, no UI sets it); mark stale on a new Delta version.

### FEAT-NB-10

**New notebook wizard.** Three templates (clean, join and aggregate, load a file) that write runnable lakehouse-module code and a proof contract.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Create > Notebook from a template (Preview)
- **Evidence:** test: sidecar/tests/test_wizard.py::test_every_template_writes_code_that_parses_and_uses_the_lakehouse_module (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_wizard.py::test_a_wizard_notebook_proves_the_same_with_its_contract; smoke: wizard_templates listed clean, join_aggregate, load_file
- **Source:** `sidecar/fabric_desktop/wizard.py`, `app/ui/wizard.js`
- **Tests:** `sidecar/tests/test_wizard.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Three fixed templates. No launch from a table.
- **Pull requests and issues:** [#65](https://github.com/KratosDataGod/KratosForge/pull/65)
- **Owning workstream:** Prove
- **Backlog:** [PB-034](PRODUCT_BACKLOG.md#pb-034)
- **Recommended next action:** Add the entry point from a lakehouse table (plan Stage 3).

### FEAT-NB-11

**TOML job runner.** A job file runs DuckDB SQL or a Polars transform locally, checks the result and publishes Delta to OneLake.

- **Pillar and category:** Local development; Data Engineering and Transformation
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** CLI only (fabric-desktop preview|run)
- **Evidence:** test: sidecar/tests/test_local.py::test_duckdb_job_runs_locally (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_local.py::test_polars_job_runs_locally; smoke: preview_job on examples/sales_by_region.toml returned 3 rows; CLI lists preview/run
- **Source:** `sidecar/fabric_desktop/jobs.py`, `sidecar/fabric_desktop/publish.py`, `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_local.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** No UI; Monitor labels its history as 'Notebook write' (inference). No managed-table write guard (inference).
- **Backlog:** [PB-079](PRODUCT_BACKLOG.md#pb-079), [PB-099](PRODUCT_BACKLOG.md#pb-099)
- **Recommended next action:** Decide: expose jobs in the app or mark them a CLI/agent feature and document so (PB-079).

### FEAT-SQL-01

**SQL query page.** Spark SQL per lakehouse translated by sqlglot to DuckDB over OneLake Delta; first 1,000 rows, Copy, Download CSV, Save as table, Open in notebook.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Workspaces > lakehouse > SQL query
- **Evidence:** test: sidecar/tests/test_notebook.py::test_sql_runs_spark_sql_over_lakehouse_tables (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_sql_result_saves_as_a_table_and_exports_csv; test: sidecar/tests/test_sql_editor.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_sql_exec.py (passed in the 2026-10-17 refresh run at 9715e28); test: app/tests/browser/sql-editor.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/sql.py`, `sidecar/fabric_desktop/service.py`, `app/ui/app.js`, `app/ui/sqleditor.js`, `sidecar/fabric_desktop/sql_exec.py`, `sidecar/fabric_desktop/sql_worker.py`, `sidecar/fabric_desktop/sql_format.py`
- **Tests:** `sidecar/tests/test_notebook.py`, `sidecar/tests/test_sql_editor.py`, `sidecar/tests/test_sql_exec.py`, `app/tests/browser/sql-editor.test.cjs`
- **Docs:** [docs/notebooks.md](../notebooks.md), [docs/sql-execution.md](../sql-execution.md)
- **Known limitations:** Plain textarea, no autocomplete or formatting. Read-only. Download CSV re-runs the query. No cancel. Editor (SQL-001) and managed execution with cancel and streaming (SQL-003) verified by engine tests only; the browser tests were not run.
- **Pull requests and issues:** [#118](https://github.com/KratosDataGod/KratosForge/pull/118), [#134](https://github.com/KratosDataGod/KratosForge/pull/134), [#639](https://github.com/KratosDataGod/KratosForge/pull/639)
- **Owning workstream:** SQL development sprint
- **Backlog:** [PB-021](PRODUCT_BACKLOG.md#pb-021), [PB-022](PRODUCT_BACKLOG.md#pb-022), [PB-023](PRODUCT_BACKLOG.md#pb-023), [PB-099](PRODUCT_BACKLOG.md#pb-099)
- **Recommended next action:** SQL editor rows are tracked by the warehouse program now (WH-08 Messages and Chart merged).

### FEAT-SQL-02

**%%sql cells.** Spark SQL cells over the attached lakehouse through the local Spark session.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_spark_session_builder_and_temp_views_and_sql_cells (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_sql_uses_utc_whatever_this_pc_time_zone_is
- **Source:** `sidecar/fabric_desktop/notebook.py`, `sidecar/fabric_desktop/sql.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** MERGE, UPDATE, DELETE print a note and report status ok, so Run all continues.
- **Backlog:** [PB-012](PRODUCT_BACKLOG.md#pb-012), [PB-013](PRODUCT_BACKLOG.md#pb-013)
- **Recommended next action:** Make a MERGE/UPDATE/DELETE cell end with a visible 'skipped on this PC' status instead of ok (PB-013).

### FEAT-PRV-01

**Prove this notebook.** Pins input versions, runs the notebook on the PC and in Fabric into sandboxes, verifies outputs per table and records a verdict.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Prove (Preview)
- **Evidence:** test: sidecar/tests/test_prove.py::test_a_proof_runs_both_sides_from_pinned_inputs_without_touching_shared_tables (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_prove.py::test_a_write_to_an_input_during_the_proof_makes_it_inconclusive
- **Source:** `sidecar/fabric_desktop/prove_service.py`, `sidecar/fabric_desktop/proof.py`, `sidecar/fabric_desktop/proof_runtime.py`, `app/ui/prove.js`
- **Tests:** `sidecar/tests/test_prove.py`
- **Docs:** [docs/prove/how-proofs-work.md](../prove/how-proofs-work.md), [docs/prove/plan.md](../prove/plan.md)
- **Known limitations:** Spark notebooks blocked. One proof at a time. TZ fixed only where time.tzset exists (not Windows).
- **Pull requests and issues:** [#59](https://github.com/KratosDataGod/KratosForge/pull/59), [#62](https://github.com/KratosDataGod/KratosForge/pull/62), [#65](https://github.com/KratosDataGod/KratosForge/pull/65)
- **Owning workstream:** Prove
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002)
- **Recommended next action:** Run one proof on the tenant (PB-002); add the column heatmap from plan Stage 3.

### FEAT-PRV-02

**Output verification rules.** Keyed or whole-row comparison with tolerances, ignored columns and a fingerprint level above a row limit.

- **Pillar and category:** Local development; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Prove panel (Preview)
- **Evidence:** test: sidecar/tests/test_prove.py::test_tiny_floating_point_drift_passes_the_default_tolerance (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_prove.py::test_verify_whole_rows_counts_duplicates_and_reports_levels
- **Source:** `sidecar/fabric_desktop/verify.py`
- **Tests:** `sidecar/tests/test_prove.py`
- **Docs:** [docs/prove/how-proofs-work.md](../prove/how-proofs-work.md)
- **Owning workstream:** Prove
- **Recommended next action:** Reuse for SQL and metadata compare (one comparison engine).

### FEAT-PRV-03

**Run receipts.** Tamper-evident receipts (SHA-256 id) of what ran where, exported to Markdown or HTML with chosen redactions.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Prove > Receipt (Preview)
- **Evidence:** test: sidecar/tests/test_receipts_env.py::test_store_writes_once_and_detects_tampering (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_receipts_env.py::test_export_redacts_what_was_chosen_and_keeps_the_id
- **Source:** `sidecar/fabric_desktop/receipts.py`, `sidecar/fabric_desktop/prove_service.py`
- **Tests:** `sidecar/tests/test_receipts_env.py`
- **Docs:** [docs/prove/how-proofs-work.md](../prove/how-proofs-work.md)
- **Known limitations:** No receipt list UI (receipts_list has no caller).
- **Owning workstream:** Prove
- **Backlog:** [PB-007](PRODUCT_BACKLOG.md#pb-007)
- **Recommended next action:** Feed receipts into the audit trail (FD-125, PR #89).

### FEAT-PRV-04

**Environment profile.** Imports found per cell and classified for this PC and Fabric; observed Fabric versions remembered.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Prove > Packages only (Preview)
- **Evidence:** test: sidecar/tests/test_receipts_env.py::test_check_classifies_each_module_for_both_places (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/envprofile.py`
- **Tests:** `sidecar/tests/test_receipts_env.py`
- **Docs:** [docs/prove/how-proofs-work.md](../prove/how-proofs-work.md)
- **Known limitations:** Nothing installs packages. Fabric runtime list is documented, not queried.
- **Owning workstream:** Prove (FD-109)
- **Backlog:** [PB-016](PRODUCT_BACKLOG.md#pb-016)
- **Recommended next action:** Reproducible environments wait on the package-source decision (FD-109).

### FEAT-PRV-05

**Scorecard and run measurement.** Correctness, time, resources and cost labelled measured, estimated or not available.

- **Pillar and category:** Local development; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Prove > Full scorecard (Preview)
- **Evidence:** test: app/tests/ui.test.cjs a scorecard never shows an estimate or a missing number as measured (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: sidecar/fabric_desktop/measure.py RunMeter
- **Source:** `sidecar/fabric_desktop/scorecard.py`, `sidecar/fabric_desktop/measure.py`
- **Tests:** `app/tests/ui.test.cjs`
- **Docs:** [docs/prove/how-proofs-work.md](../prove/how-proofs-work.md)
- **Known limitations:** CPU/memory come from the engine process, not the child kernel (inference). Fabric CPU/memory not available.
- **Owning workstream:** Prove (FD-119)
- **Backlog:** [PB-050](PRODUCT_BACKLOG.md#pb-050), [PB-094](PRODUCT_BACKLOG.md#pb-094)
- **Recommended next action:** Measure the kernel child process when it is on (FD-119).

### FEAT-PRV-06

**Side by side and the TPC-H benchmark.** Runs a notebook on the PC and in Fabric against the real lakehouse after confirming the tables it overwrites; compares checksums, speed and cost, splits This PC's time into processing and OneLake upload wait, and says plainly when a table is missing. TPC-H data generates on every core and is always deleted afterwards.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Side by side (Preview)
- **Evidence:** test: sidecar/tests/test_side_by_side.py::test_side_by_side_runs_only_with_the_tables_it_overwrites_confirmed (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_side_by_side.py::test_benchmark_notebooks_run_end_to_end; test: sidecar/tests/test_side_by_side.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/side_by_side.py`, `sidecar/fabric_desktop/side_by_side_service.py`, `sidecar/fabric_desktop/benchmark.py`, `app/ui/sidebyside.js`
- **Tests:** `sidecar/tests/test_side_by_side.py`
- **Docs:** [docs/side-by-side.md](../side-by-side.md)
- **Known limitations:** Writes the real tables twice (confirmed first). Files/ not compared.
- **Pull requests and issues:** [#44](https://github.com/KratosDataGod/KratosForge/pull/44), [#81](https://github.com/KratosDataGod/KratosForge/pull/81), [#114](https://github.com/KratosDataGod/KratosForge/pull/114), [#564](https://github.com/KratosDataGod/KratosForge/pull/564), [#622](https://github.com/KratosDataGod/KratosForge/pull/622), [#625](https://github.com/KratosDataGod/KratosForge/pull/625), [#634](https://github.com/KratosDataGod/KratosForge/pull/634), [#635](https://github.com/KratosDataGod/KratosForge/pull/635)
- **Owning workstream:** Prove (FD-112)
- **Backlog:** [PB-076](PRODUCT_BACKLOG.md#pb-076)
- **Recommended next action:** Chris's SF1 local run is 65 s vs 101 s before (1.9x); keep the benchmark and retire correctness checks into Prove (PB-076).

### FEAT-META-01

**Metadata project, layers and entities.** TOML project of 2-10 medallion layers (one lakehouse each) and entities from templates, edited in the app with validation and save gates.

- **Pillar and category:** Local development; Data Engineering and Transformation
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Design
- **Evidence:** test: sidecar/tests/test_meta.py::test_06_no_reads_from_a_later_layer (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meta.py::test_templates_meet_the_standards; smoke: meta_init created a 3-layer sample project; meta_project returned its layers
- **Source:** `sidecar/fabric_desktop/meta/project.py`, `sidecar/fabric_desktop/meta/templates.py`, `sidecar/fabric_desktop/meta_service.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_meta.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Deleting an entity leaves its table.
- **Recommended next action:** Keep.

### FEAT-META-02

**Metadata runs on this PC.** Runs entities in dependency order with full, incremental, SCD1 and SCD2 loads; Dev defaults to local Delta in the project's .local folder.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Entities > Run
- **Evidence:** test: sidecar/tests/test_meta.py::test_04_type1_dimension_becomes_type2 (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_large_tables.py::test_an_update_merges_into_the_table_and_rewrites_only_the_files_it_touches; smoke: meta_run env=dev ran every sample entity locally with status ok in under a second
- **Source:** `sidecar/fabric_desktop/meta/engine.py`, `sidecar/fabric_desktop/meta/store.py`
- **Tests:** `sidecar/tests/test_meta.py`, `sidecar/tests/test_large_tables.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Watermark filter is strictly >, so late rows are dropped. Type 2 source repeating a key fails (plan).
- **Pull requests and issues:** [#56](https://github.com/KratosDataGod/KratosForge/pull/56), [#61](https://github.com/KratosDataGod/KratosForge/pull/61)
- **Backlog:** [PB-019](PRODUCT_BACKLOG.md#pb-019)
- **Recommended next action:** Keep; add late-arriving rows and source deletes (FD-117).

### FEAT-META-03

**Checks, quarantine and replay.** not_null, unique, exists_in, accepted_values, range, expr and Python checks; stop, quarantine or warn; fix and replay quarantined rows.

- **Pillar and category:** Local development; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity page > Quarantine
- **Evidence:** test: sidecar/tests/test_dataops.py::test_fix_and_replay_a_quarantined_row (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meta.py::test_03b_a_stop_check_writes_nothing
- **Source:** `sidecar/fabric_desktop/meta/engine.py`, `sidecar/fabric_desktop/dataops_service.py`, `app/ui/dataops.js`
- **Tests:** `sidecar/tests/test_dataops.py`, `sidecar/tests/test_meta.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** exists_in with a missing reference table marks every row bad.
- **Recommended next action:** Keep.

### FEAT-META-04

**Custom code: hooks, steps and Python checks.** after_clean and before_write hooks, custom steps and Python checks that read only declared tables and can't write.

- **Pillar and category:** Local development; Data Engineering and Transformation
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity TOML
- **Evidence:** test: sidecar/tests/test_meta.py::test_05b_hook_guardrails (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meta.py::test_05d_custom_step_output_is_still_checked
- **Source:** `sidecar/fabric_desktop/meta/engine.py`, `sidecar/fabric_desktop/meta/project.py`
- **Tests:** `sidecar/tests/test_meta.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Recommended next action:** Keep.

### FEAT-META-05

**Surrogate keys.** One key table per business key in LH_Keys, merged with retry; one business key never gets two keys.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Surrogate keys
- **Evidence:** test: sidecar/tests/test_keys.py::test_runs_at_once_never_give_two_business_keys_one_key (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_keys.py::test_merge_keys_never_gives_a_business_key_two_keys
- **Source:** `sidecar/fabric_desktop/meta/keys.py`, `sidecar/fabric_desktop/meta_service.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_keys.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** read_keys reads the whole key table.
- **Pull requests and issues:** [#23](https://github.com/KratosDataGod/KratosForge/pull/23), [#52](https://github.com/KratosDataGod/KratosForge/pull/52)
- **Owning workstream:** Staged roadmap (FD-115)
- **Backlog:** [PB-068](PRODUCT_BACKLOG.md#pb-068)
- **Recommended next action:** Stop reading whole key tables on every keyed run (FD-115).

### FEAT-META-06

**Large tables: preflight, batches, merge writes and file manifest.** Memory estimate from the Delta log stops runs that won't fit; batch_by streams a full load into one commit; MERGE writes rewrite only touched files; landing files are read once.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Automatic in runs; batch_by in entity TOML
- **Evidence:** test: sidecar/tests/test_preflight.py::test_a_run_shows_the_estimate_and_stops_when_it_cannot_fit (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_batches.py::test_a_batched_full_load_writes_the_same_rows_in_one_commit; test: sidecar/tests/test_landing_manifest.py::test_a_run_reads_only_files_it_has_not_loaded; smoke: meta_runs step detail showed 'about 5 rows ... 0.1 MB in memory' estimates
- **Source:** `sidecar/fabric_desktop/meta/preflight.py`, `sidecar/fabric_desktop/meta/batches.py`, `sidecar/fabric_desktop/meta/engine.py`
- **Tests:** `sidecar/tests/test_preflight.py`, `sidecar/tests/test_batches.py`, `sidecar/tests/test_landing_manifest.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md), [docs/scale-and-operations-plan.md](../scale-and-operations-plan.md)
- **Known limitations:** meta_preflight has no UI caller. Only Delta sources are estimated. No physical partitioning. Batching excludes SCD modes.
- **Pull requests and issues:** [#56](https://github.com/KratosDataGod/KratosForge/pull/56), [#69](https://github.com/KratosDataGod/KratosForge/pull/69), [#70](https://github.com/KratosDataGod/KratosForge/pull/70), [#87](https://github.com/KratosDataGod/KratosForge/pull/87)
- **Owning workstream:** Staged roadmap (FD-114)
- **Backlog:** [PB-067](PRODUCT_BACKLOG.md#pb-067), [PB-090](PRODUCT_BACKLOG.md#pb-090)
- **Recommended next action:** Build the run dialog (mockup first) and vacuum orphan files after a stopped batch (FD-114).

### FEAT-META-07

**Schema drift review.** Detects renamed, new and retyped columns against the contract with evolve, ignore_new or strict per layer, and settles them through the save gate.

- **Pillar and category:** Local development; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity page > Drift
- **Evidence:** test: sidecar/tests/test_dataops.py::test_drift_review_shows_renamed_new_and_retyped_columns_and_settles_them (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/meta/drift.py`, `sidecar/fabric_desktop/dataops_service.py`, `app/ui/dataops.js`
- **Tests:** `sidecar/tests/test_dataops.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** landing and link sources not checked (inference).
- **Backlog:** [PB-031](PRODUCT_BACKLOG.md#pb-031)
- **Recommended next action:** Cover landing and link sources (inference: not read).

### FEAT-META-08

**Compare, meaningful diffs and promote impact.** Compare a table between environments or versions; TOML and notebook diffs by meaning; promote impact lists changed entities.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity > Compare, Deploy > promote impact
- **Evidence:** test: sidecar/tests/test_dataops.py::test_compare_dev_with_test_shows_what_a_logic_change_does (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meaningful_diffs.py::test_comments_order_and_spacing_are_not_changes
- **Source:** `sidecar/fabric_desktop/meta/compare.py`, `sidecar/fabric_desktop/meta/diff.py`, `sidecar/fabric_desktop/dataops_service.py`, `app/ui/dataops.js`
- **Tests:** `sidecar/tests/test_dataops.py`, `sidecar/tests/test_meaningful_diffs.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Compare reads whole tables. meta_diff against Git head has no UI caller.
- **Pull requests and issues:** [#75](https://github.com/KratosDataGod/KratosForge/pull/75)
- **Backlog:** [PB-069](PRODUCT_BACKLOG.md#pb-069)
- **Recommended next action:** Batch the compare for large tables.

### FEAT-META-09

**Promote Dev to Test to Prod.** Gated promote of configuration: every entity ran and passed on current logic, tests pass, keys workspace set; creates lakehouses, syncs keys, publishes VL_Sources and products.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Deploy > Environments
- **Evidence:** test: sidecar/tests/test_meta.py::test_08_promote_dev_to_test_to_prod (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meta.py::test_08b_a_failed_run_blocks_promote; smoke: meta_promote_check dev->test returned a plain error (no Keys workspace) and a Git note
- **Source:** `sidecar/fabric_desktop/meta/promote.py`, `sidecar/fabric_desktop/meta_service.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_meta.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Copies configuration only, never data. No release-level rollback.
- **Owning workstream:** Staged roadmap (FD-121)
- **Backlog:** [PB-042](PRODUCT_BACKLOG.md#pb-042)
- **Recommended next action:** Release rollback (scale plan 5.3) and decision D1 (require a pushed commit for Test/Prod).

### FEAT-META-10

**Release records.** Each promote records environment, version, commit, branch, who, app and engine version in meta.deployments.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Promote card notes
- **Evidence:** test: sidecar/tests/test_release.py::test_a_promote_from_a_pushed_commit_records_it_and_the_record_checks_out (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_release.py::test_uncommitted_files_are_a_note_unless_the_target_requires_a_commit
- **Source:** `sidecar/fabric_desktop/meta/release.py`, `sidecar/fabric_desktop/meta_service.py`
- **Tests:** `sidecar/tests/test_release.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** No list of releases in the UI. Docs say Preview; no Preview id.
- **Pull requests and issues:** [#73](https://github.com/KratosDataGod/KratosForge/pull/73), [#113](https://github.com/KratosDataGod/KratosForge/pull/113), [#124](https://github.com/KratosDataGod/KratosForge/pull/124)
- **Owning workstream:** Staged roadmap
- **Backlog:** [PB-042](PRODUCT_BACKLOG.md#pb-042)
- **Recommended next action:** Merge PR #113 (release records read Git through the Git layer); add a release list view.

### FEAT-META-11

**Table history and restore.** Lists a table's Delta versions and restores one, moving the watermark back and logging the restore.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity > History (Preview)
- **Evidence:** test: sidecar/tests/test_estate.py::test_restore_a_bad_load_and_move_the_watermark_back (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_estate.py::test_versions_vacuumed_away_cannot_be_restored
- **Source:** `sidecar/fabric_desktop/meta/restore.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`
- **Tests:** `sidecar/tests/test_estate.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Per table; downstream tables not changed.
- **Recommended next action:** Keep; downstream rebuild guidance in the dialog.

### FEAT-META-12

**Table health, optimize and retention.** Small-file and retention advice; Fabric table maintenance (V-Order, VACUUM) for OneLake tables and delta-rs compaction on the PC.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Quality > Table health (Preview)
- **Evidence:** test: sidecar/tests/test_cloud.py::test_health_advises_compaction_and_compact_local_fixes_it (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_estate.py::test_layer_retention_is_saved_checked_and_used_by_maintenance
- **Source:** `sidecar/fabric_desktop/maintenance.py`, `sidecar/fabric_desktop/cloud_service.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_cloud.py`, `sidecar/tests/test_estate.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Local compaction has no V-Order.
- **Recommended next action:** Keep.

### FEAT-META-13

**Direct Lake semantic model.** Builds a Direct Lake semantic model (TMDL) over the Gold layer after refreshing the SQL endpoint.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Deploy > Semantic model (Preview)
- **Evidence:** test: sidecar/tests/test_cloud.py::test_save_semantic_model_updates_an_existing_one (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_cloud.py::test_onelake_run_queues_maintenance_and_builds_the_model
- **Source:** `sidecar/fabric_desktop/semantic.py`, `sidecar/fabric_desktop/cloud_service.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_cloud.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Refused for Dev on the PC. Only facts get a row-count measure. Arrays and structs skipped.
- **Recommended next action:** Include in the tenant smoke test.

### FEAT-META-14

**Environments: Tables in and Runs on.** Each environment has its own workspace, keeps tables on the PC or in OneLake, and runs on this PC or in Fabric through the runner notebook.

- **Pillar and category:** Enterprise services; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Deploy > Environments
- **Evidence:** test: sidecar/tests/test_fabric_compute.py::test_run_in_fabric_uploads_once_starts_the_runner_and_reads_results (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_fabric_compute.py::test_bundle_has_the_engine_and_the_project_but_no_local_data
- **Source:** `sidecar/fabric_desktop/meta_service.py`, `sidecar/fabric_desktop/cloud_service.py`, `sidecar/fabric_desktop/fabric_compute.py`, `app/ui/meta.js`
- **Tests:** `sidecar/tests/test_fabric_compute.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Runs on Fabric is Preview. Bundle re-uploaded whole. Runner pip-installs deltalake/polars in Fabric when older.
- **Pull requests and issues:** [#25](https://github.com/KratosDataGod/KratosForge/pull/25), [#32](https://github.com/KratosDataGod/KratosForge/pull/32)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-017](PRODUCT_BACKLOG.md#pb-017)
- **Recommended next action:** Verify the runner notebook on a tenant (PB-002).

### FEAT-META-15

**Variable library and source systems.** Source systems with per-environment connection fields (no secrets) published as the VL_Sources variable library.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Source systems
- **Evidence:** test: sidecar/tests/test_estate.py::test_variable_library_definition_has_a_value_set_per_environment (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_estate.py::test_publish_variable_library_and_read_it_in_a_notebook
- **Source:** `sidecar/fabric_desktop/meta/varlib.py`, `sidecar/fabric_desktop/estate_service.py`, `sidecar/fabric_desktop/meta/keys.py`
- **Tests:** `sidecar/tests/test_estate.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Promote publishes VL_Sources best-effort.
- **Recommended next action:** Keep.

### FEAT-META-16

**Profiling and rule suggestions.** Profiles a table locally and suggests clean and check blocks that the engine runs.

- **Pillar and category:** Local development; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity > Profile
- **Evidence:** test: sidecar/tests/test_cloud.py::test_profile_suggests_rules_the_engine_runs (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_cloud.py::test_profile_preview_and_add_rules_round_trip
- **Source:** `sidecar/fabric_desktop/profiling.py`, `sidecar/fabric_desktop/cloud_service.py`
- **Tests:** `sidecar/tests/test_cloud.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Reads every row locally.
- **Recommended next action:** Keep.

### FEAT-META-17

**Column lineage.** Where each column comes from (declared, inferred or opaque) upstream and downstream.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity page > Lineage > Columns (Preview)
- **Evidence:** test: sidecar/tests/test_lineage.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_lineage.py::test_every_column_says_where_it_comes_from (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: sidecar/fabric_desktop/meta_service.py meta_lineage has no caller in app/ui
- **Source:** `sidecar/fabric_desktop/meta/lineage.py`, `app/ui/dataops.js`
- **Tests:** `sidecar/tests/test_lineage.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Python-added columns are opaque.
- **Pull requests and issues:** [#76](https://github.com/KratosDataGod/KratosForge/pull/76), [#124](https://github.com/KratosDataGod/KratosForge/pull/124)
- **Owning workstream:** Staged roadmap (FD-128)
- **Backlog:** [PB-081](PRODUCT_BACKLOG.md#pb-081)
- **Recommended next action:** Implementation review of PR #124.

### FEAT-META-18

**Estate X-ray and adopt.** Scans lakehouses for small files, unidentified and stale tables, vacuum and access findings; optimize, vacuum with preview, adopt existing tables as entities.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Home > Estate X-ray (Preview)
- **Evidence:** test: sidecar/tests/test_xray.py::test_scan_finds_what_needs_attention (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_xray.py::test_vacuum_preview_counts_what_it_would_delete; test: sidecar/tests/test_xray.py::test_adopt_writes_entities_and_skips_what_exists
- **Source:** `sidecar/fabric_desktop/xray.py`, `sidecar/fabric_desktop/xray_service.py`, `app/ui/xray.js`
- **Tests:** `sidecar/tests/test_xray.py`
- **Docs:** [docs/xray-fixes.md](../xray-fixes.md)
- **Known limitations:** Stops at 500 tables. Paused capacities skipped.
- **Pull requests and issues:** [#41](https://github.com/KratosDataGod/KratosForge/pull/41)
- **Recommended next action:** Keep.

### FEAT-GOV-01

**Freshness promises and Teams alerts.** Per-entity freshness promise with late alerts sent once to a Teams webhook.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Entity > Freshness, Home
- **Evidence:** test: sidecar/tests/test_dataops.py::test_late_tables_are_posted_to_teams_once (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_dataops.py::test_freshness_within_hours
- **Source:** `sidecar/fabric_desktop/meta/freshness.py`, `sidecar/fabric_desktop/dataops_service.py`, `app/ui/dataops.js`
- **Tests:** `sidecar/tests/test_dataops.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Checked only while the app is open (agent covers unattended). May post twice alongside incidents.
- **Backlog:** [PB-084](PRODUCT_BACKLOG.md#pb-084)
- **Recommended next action:** Merge with incidents so a late table posts once (agent.md notes duplicates).

### FEAT-GOV-02

**Project tests gate promote.** pytest-style project tests discovered, run and required to pass before promote.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Quality > Tests (Preview)
- **Evidence:** test: sidecar/tests/test_estate.py::test_failing_project_test_blocks_promote (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: meta_promote_check reported 1 test passed in the sample project
- **Source:** `sidecar/fabric_desktop/meta/testing.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`
- **Tests:** `sidecar/tests/test_estate.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Only the tmp_path fixture.
- **Owning workstream:** Git-native (FD-113)
- **Recommended next action:** Fold into the Git program's validation framework (FD-113).

### FEAT-GOV-03

**Governance exceptions.** exceptions.toml with reason, approver and expiry that lets a gate pass until it expires; approved exceptions are shown and warned about before they end, and a policy exception can be requested and approved on the Governance tab (Preview).

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Metadata project, Platform > Governance > Requests (Preview)
- **Evidence:** test: sidecar/tests/test_meta.py::test_05e_exceptions_expire (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_meta.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_requests_core_four.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/meta/project.py`
- **Tests:** `sidecar/tests/test_meta.py`, `sidecar/tests/test_requests_core_four.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Pull requests and issues:** [#603](https://github.com/KratosDataGod/KratosForge/pull/603), [#605](https://github.com/KratosDataGod/KratosForge/pull/605)
- **Owning workstream:** Enterprise governance foundation (FD-129)
- **Backlog:** [PB-059](PRODUCT_BACKLOG.md#pb-059)
- **Recommended next action:** Implementation review of #603 and #605 (PB-059).

### FEAT-GOV-04

**Who can promote and separation of duties.** Checks Fabric roles in every target workspace and that the person who promoted to Test doesn't promote to Prod.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Promote card
- **Evidence:** test: sidecar/tests/test_duties.py::test_the_person_who_promoted_to_test_cant_promote_to_prod (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_duties.py::test_promote_checks_roles_in_every_target_workspace
- **Source:** `sidecar/fabric_desktop/duties.py`, `sidecar/fabric_desktop/meta_service.py`
- **Tests:** `sidecar/tests/test_duties.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Not a security boundary. Group roles become notes. separate_promoter compares PC login names.
- **Pull requests and issues:** [#93](https://github.com/KratosDataGod/KratosForge/pull/93)
- **Owning workstream:** Enterprise readiness (FD-130)
- **Backlog:** [PB-065](PRODUCT_BACKLOG.md#pb-065)
- **Recommended next action:** Keep; compare signed-in identities rather than PC login names.

### FEAT-GOV-05

**Enterprise governance policy registry and offline validation.** Central versioned policies (naming, schema, quality, classification, ownership, contracts, deployment readiness) inherited enterprise to entity and validated offline.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** doc: Enterprise governance foundation thread (GOV-001..005), 2026-10-17; test: sidecar/tests/test_governance.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/central/policy.py; code: sidecar/fabric_desktop/central/governance_service.py
- **Source:** `sidecar/fabric_desktop/central/policy.py`, `sidecar/fabric_desktop/central/governance_service.py`, `app/ui/governance.js`
- **Tests:** `sidecar/tests/test_governance.py`
- **Docs:** [docs/governance.md](../governance.md)
- **Known limitations:** GOV-001 policy registry only; offline validation (GOV-002) and enforcement are not built; HD-031 labels Block as not enforced.
- **Pull requests and issues:** [#119](https://github.com/KratosDataGod/KratosForge/pull/119)
- **Owning workstream:** Enterprise governance foundation
- **Backlog:** [PB-056](PRODUCT_BACKLOG.md#pb-056), [PB-057](PRODUCT_BACKLOG.md#pb-057)
- **Recommended next action:** GOV-002 offline validation, after Chris approves

### FEAT-GOV-06

**Sensitive data classification.** Classify columns and carry labels into products and Purview.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: udf.py set_classification exists with no caller in the repo
- **Source:** `sidecar/fabric_desktop/central/udf.py`
- **Owning workstream:** Enterprise governance foundation
- **Backlog:** [PB-060](PRODUCT_BACKLOG.md#pb-060)
- **Recommended next action:** Owned by the governance foundation thread; complement Purview, don't duplicate it.

### FEAT-GOV-07

**Reliability objectives.** Per-entity success-rate objectives evaluated into incidents.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None (agent incidents only)
- **Evidence:** test: sidecar/tests/test_incidents.py::test_success_rate_against_the_objective (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/meta/objectives.py`, `sidecar/fabric_desktop/workloads/evaluate.py`
- **Tests:** `sidecar/tests/test_incidents.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Owning workstream:** Workloads
- **Backlog:** [PB-047](PRODUCT_BACKLOG.md#pb-047)
- **Recommended next action:** Show in the workload page with incidents (PB-047).

### FEAT-MESH-01

**Central platform.** Per-environment Platform workspace with LH_Meta, LH_Landing, SQL_Platform (Fabric SQL database) and a User Data Functions app; admins, requests, grants and audit.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Platform (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_only_platform_admins_create_domains_key_tables_and_sources (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_platform.py::test_requests_for_key_tables_sources_and_access
- **Source:** `sidecar/fabric_desktop/central/core.py`, `sidecar/fabric_desktop/central/service.py`, `sidecar/fabric_desktop/central/setup.py`, `sidecar/fabric_desktop/central/udf.py`, `app/ui/platform.js`
- **Tests:** `sidecar/tests/test_platform.py`
- **Docs:** [docs/platform.md](../platform.md)
- **Known limitations:** T-SQL tested on SQLite only. Whether UDF context exposes executing_user is unverified. setup()/join() have no tests. platform_revoke has no UI.
- **Pull requests and issues:** [#48](https://github.com/KratosDataGod/KratosForge/pull/48), [#50](https://github.com/KratosDataGod/KratosForge/pull/50)
- **Backlog:** [PB-052](PRODUCT_BACKLOG.md#pb-052), [PB-054](PRODUCT_BACKLOG.md#pb-054)
- **Recommended next action:** Verify setup, the UDF executing_user and T-SQL on a tenant (platform.md lists these unverified).

### FEAT-MESH-02

**Central business keys.** Keys issued in batches by SQL_Platform UDFs with numbers kept when moved from LH_Keys.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Surrogate keys > Move keys, Platform > Business keys (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_keys_are_issued_in_batches (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_platform.py::test_keys_move_to_the_platform_with_their_numbers
- **Source:** `sidecar/fabric_desktop/central/core.py`, `sidecar/fabric_desktop/central/client.py`, `sidecar/fabric_desktop/central/migrate.py`
- **Tests:** `sidecar/tests/test_platform.py`
- **Docs:** [docs/platform.md](../platform.md)
- **Known limitations:** Moving back to LH_Keys refused once new keys are issued. Direction change (Chris's unit testing, 2026-10-18, item 25): keys live in one Fabric SQL database in a dedicated workspace, shared by all environments (Chris chose one for all on 2026-10-20, reversing the 2026-10-16 decision to keep keys per environment); today each environment has its own SQL_Platform.
- **Backlog:** [PB-052](PRODUCT_BACKLOG.md#pb-052)
- **Recommended next action:** Staged roadmap moves keys to one shared keys database in a dedicated workspace, keeping existing key numbers (item 25, decided 2026-10-20)

### FEAT-MESH-03

**Landing zone and source registry.** One central landing workspace for Dev, Test and Prod with one lakehouse per zone, optional domain zones and Prod-only secure zones, registered sources and late-file flags (Preview).

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Platform > Landing zone (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_teams_build_bronze_from_the_one_landing_zone (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_landing_zones.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/landing-zones.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/central/service.py`, `app/ui/platform.js`
- **Tests:** `sidecar/tests/test_platform.py`, `sidecar/tests/test_landing_zones.py`, `app/tests/browser/landing-zones.test.cjs`
- **Docs:** [docs/platform.md](../platform.md)
- **Known limitations:** Nothing in the app writes into landing: no extractors or connectors. Not yet set up on a tenant.
- **Pull requests and issues:** [#597](https://github.com/KratosDataGod/KratosForge/pull/597)
- **Owning workstream:** Staged roadmap (FD-116)
- **Backlog:** [PB-030](PRODUCT_BACKLOG.md#pb-030)
- **Recommended next action:** Guided ingestion still waits on D2 (FD-116); Data Factory copy jobs (DF-6) may answer it.

### FEAT-MESH-04

**Data mesh: domains, products, links, glossary.** Domains imported from and created in Fabric, data products with ports, endorsement badges and Purview term links, last scan and mixed endorsement on the product page, links between teams' products, glossary, About text and a mesh map on Home.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Domains, OneLake > Data products, Home (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_an_entity_reads_another_teams_table_through_a_link (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_platform.py::test_mesh_map_lists_domains_projects_products_and_links; test: sidecar/tests/test_domain_in_fabric.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_trust_badges.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/domain-in-fabric.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/trust-badges.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/meta/mesh.py`, `sidecar/fabric_desktop/central/service.py`, `app/ui/mesh.js`, `app/ui/meshmap.js`, `sidecar/fabric_desktop/central/trust_service.py`
- **Tests:** `sidecar/tests/test_platform.py`, `sidecar/tests/test_domain_in_fabric.py`, `sidecar/tests/test_trust_badges.py`, `app/tests/browser/domain-in-fabric.test.cjs`, `app/tests/browser/trust-badges.test.cjs`
- **Docs:** [docs/data-mesh.md](../data-mesh.md)
- **Known limitations:** Fabric domain import needs Tenant.Read.All and its shape is unverified. Direction change (Chris's unit testing, 2026-10-18, item 27): the glossary should come from Purview, Collibra or another catalog instead of KratosForge's own; the built-in glossary becomes a fallback or is retired.
- **Pull requests and issues:** [#48](https://github.com/KratosDataGod/KratosForge/pull/48), [#50](https://github.com/KratosDataGod/KratosForge/pull/50), [#591](https://github.com/KratosDataGod/KratosForge/pull/591), [#627](https://github.com/KratosDataGod/KratosForge/pull/627), [#642](https://github.com/KratosDataGod/KratosForge/pull/642)
- **Backlog:** [PB-052](PRODUCT_BACKLOG.md#pb-052)
- **Recommended next action:** Purview and endorsement live checks wait on Chris's tenant run; implementation review of #591, #627, #642.

### FEAT-MESH-05

**Breaking-change check for data products.** Promote stops when a change drops or narrows a column another project reads, unless the product version is raised.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Promote card
- **Evidence:** test: sidecar/tests/test_breaking_changes.py::test_dropping_a_column_another_project_reads_blocks_the_promote (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/meta/mesh.py`, `sidecar/fabric_desktop/meta_service.py`
- **Tests:** `sidecar/tests/test_breaking_changes.py`
- **Docs:** [docs/data-mesh.md](../data-mesh.md)
- **Pull requests and issues:** [#71](https://github.com/KratosDataGod/KratosForge/pull/71)
- **Owning workstream:** Staged roadmap (FD-127)
- **Backlog:** [PB-082](PRODUCT_BACKLOG.md#pb-082)
- **Recommended next action:** Consumer declarations and deprecation (scale plan 8.2).

### FEAT-ORC-01

**PC schedules.** Notebook and metadata schedules on this PC through Windows Task Scheduler, with a lock so a run still going isn't started twice.

- **Pillar and category:** Local development; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon, Data mesh, Monitor > Schedules (Preview)
- **Evidence:** test: sidecar/tests/test_schedules.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_schedules.py::test_task_definition_runs_the_sidecar_quietly (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_schedules.py::test_a_run_still_going_is_not_started_twice
- **Source:** `sidecar/fabric_desktop/local_schedules.py`, `sidecar/fabric_desktop/schedule_service.py`, `app/ui/schedules.js`
- **Tests:** `sidecar/tests/test_schedules.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Backlog:** [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-048](PRODUCT_BACKLOG.md#pb-048)
- **Recommended next action:** Agent schedules cover Linux; Windows Task Scheduler runs need a Windows check.

### FEAT-ORC-02

**Headless agent.** fabric-desktop agent run/check/status/enqueue on Windows or Linux: SQLite queue with retries, dead letter, recovery, schedules and Teams alerts.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** CLI and agent.toml (no UI)
- **Evidence:** test: sidecar/tests/test_agent.py::test_queue_retries_with_backoff_then_gives_up (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_agent.py::test_a_job_from_a_crashed_agent_runs_again_after_restart; smoke: fabric-desktop agent check without a config said where to put agent.toml
- **Source:** `sidecar/fabric_desktop/agent.py`, `sidecar/fabric_desktop/jobqueue.py`, `sidecar/fabric_desktop/agent_config.py`, `sidecar/fabric_desktop/runtime.py`, `deploy/agent.example.toml`
- **Tests:** `sidecar/tests/test_agent.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** Never run against a live tenant. Runs metadata environments only, not notebooks or jobs. Queue never pruned (inference). No command to revive a dead job.
- **Pull requests and issues:** [#34](https://github.com/KratosDataGod/KratosForge/pull/34)
- **Backlog:** [PB-044](PRODUCT_BACKLOG.md#pb-044)
- **Recommended next action:** Verify on a tenant; add queue pruning and requeue of dead jobs (PB-044).

### FEAT-ORC-03

**OneLake write leases.** Per-table lease files in LH_Meta with conditional create, renew and delete so two writers don't collide.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None (agent)
- **Evidence:** test: sidecar/tests/test_agent.py::test_leases_block_a_second_writer_until_released_or_expired (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_agent.py::test_onelake_leases_use_conditional_requests; test: sidecar/tests/test_lease_before_commit.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/leases.py`
- **Tests:** `sidecar/tests/test_agent.py`, `sidecar/tests/test_lease_before_commit.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** Whether OneLake honors conditional requests is unverified. The desktop app and Fabric runs take no leases. Expiry uses the holder's clock.
- **Pull requests and issues:** [#624](https://github.com/KratosDataGod/KratosForge/pull/624), [#626](https://github.com/KratosDataGod/KratosForge/pull/626)
- **Backlog:** [PB-003](PRODUCT_BACKLOG.md#pb-003)
- **Recommended next action:** HD-016: a writer checks its leases right before the commit; OneLake conditional requests still need the live test (PB-003).

### FEAT-ORC-04

**Fabric pipeline mailbox.** A Fabric pipeline drops a request in LH_Meta; an agent claims it once, runs it, and writes status; cancel and expiry supported.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Fabric pipeline notebook + agent (Preview)
- **Evidence:** test: sidecar/tests/test_mailbox.py::test_a_pipeline_request_runs_and_reports_back (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_mailbox.py::test_the_same_request_runs_once_however_often_it_is_sent_or_read
- **Source:** `sidecar/fabric_desktop/mailbox.py`, `examples/pipeline/run_on_agent.ipynb`
- **Tests:** `sidecar/tests/test_mailbox.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** Not run live. submitted_by is free text. enterprise-roadmap.md still calls it unbuilt.
- **Pull requests and issues:** [#64](https://github.com/KratosDataGod/KratosForge/pull/64)
- **Backlog:** [PB-049](PRODUCT_BACKLOG.md#pb-049), [PB-077](PRODUCT_BACKLOG.md#pb-077)
- **Recommended next action:** Run once on a tenant; record the writer's identity.

### FEAT-ORC-05

**Agent status records.** Each agent publishes agents/<name>.json with state, counts, running jobs and recent jobs; stale after 3 status periods.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Operations > Agents (Preview)
- **Evidence:** test: sidecar/tests/test_agent_status.py::test_the_agent_publishes_what_it_is_doing (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_agent_fleet.py (passed in the 2026-10-17 refresh run at 9715e28); code: app/ui/agents.js
- **Source:** `sidecar/fabric_desktop/agent_status.py`, `sidecar/fabric_desktop/workloads/agents.py`, `app/ui/agents.js`
- **Tests:** `sidecar/tests/test_agent_status.py`, `sidecar/tests/test_agent_fleet.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Pull requests and issues:** [#66](https://github.com/KratosDataGod/KratosForge/pull/66), [#133](https://github.com/KratosDataGod/KratosForge/pull/133)
- **Owning workstream:** Workloads (FD-122)
- **Backlog:** [PB-077](PRODUCT_BACKLOG.md#pb-077), [PB-091](PRODUCT_BACKLOG.md#pb-091)
- **Recommended next action:** Implementation review of PR #133.

### FEAT-ORC-06

**Multiple workers.** Agents share schedules through the mailbox (shared = true) and can take a request only with a worker free (claim = when_free) or take over a stopped agent's request (take_over = true). All off by default.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** agent.toml settings; no screen (Preview)
- **Evidence:** test: sidecar/tests/test_shared_schedules.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_agent_takeover.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_agent_fleet.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/shared_schedules.py`, `sidecar/fabric_desktop/agent.py`, `sidecar/fabric_desktop/agent_config.py`, `sidecar/fabric_desktop/mailbox.py`
- **Tests:** `sidecar/tests/test_shared_schedules.py`, `sidecar/tests/test_agent_takeover.py`, `sidecar/tests/test_agent_fleet.py`
- **Docs:** [docs/agent.md](../agent.md), [docs/workloads/multi-worker.md](../workloads/multi-worker.md)
- **Known limitations:** Conditional requests on OneLake are unproven on a tenant (FD-2). Step 4 of the design (app labels) is not built.
- **Pull requests and issues:** [#146](https://github.com/KratosDataGod/KratosForge/pull/146), [#149](https://github.com/KratosDataGod/KratosForge/pull/149)
- **Owning workstream:** Workloads (FD-124)
- **Backlog:** [PB-045](PRODUCT_BACKLOG.md#pb-045)
- **Recommended next action:** Run the mailbox on a tenant (PB-049), then build the app labels (multi-worker.md step 4).

### FEAT-OPS-01

**Workload inventory and workload page.** One list of metadata entities, notebooks and Fabric notebooks, pipelines and Spark jobs with health, owners, links and actions with preflight.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Workloads, Home > Your workloads (Preview)
- **Evidence:** test: sidecar/tests/test_workloads.py::test_a_source_that_cant_be_read_is_unknown_never_failing (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_workloads.py::test_ten_thousand_workloads_filter_and_page_quickly
- **Source:** `sidecar/fabric_desktop/workload_service.py`, `sidecar/fabric_desktop/workloads/inventory.py`, `sidecar/fabric_desktop/workloads/store.py`, `app/ui/workloads.js`
- **Tests:** `sidecar/tests/test_workloads.py`
- **Docs:** [docs/workloads/plan.md](../workloads/plan.md)
- **Known limitations:** Ownership is per PC. No agent location or queue view.
- **Pull requests and issues:** [#63](https://github.com/KratosDataGod/KratosForge/pull/63), [#72](https://github.com/KratosDataGod/KratosForge/pull/72)
- **Owning workstream:** Workloads
- **Backlog:** [PB-092](PRODUCT_BACKLOG.md#pb-092)
- **Recommended next action:** Shared ownership (stage 2B); an 'on agents' view.

### FEAT-OPS-02

**Incidents.** Incident engine shared by app and agents: run failed, late, objective, agent offline; acknowledge, assign, resolve, escalate; Teams routing with digest, quiet hours and outbox. Numbers per environment, priority P1-P4, SLA targets from policy (incident_sla), a timeline and reported incidents (engine only, no screens yet).

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Incidents (Chris used it in unit testing, 2026-10-18)
- **Evidence:** test: sidecar/tests/test_incidents.py::test_the_app_and_the_agent_share_one_incident_list (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_incidents.py::test_quiet_hours_hold_messages_until_they_end_except_critical_and_escalations; test: sidecar/tests/test_incidents.py (passed in the 2026-10-17 refresh run at 9715e28); code: app/ui/incidents.js; test: sidecar/tests/test_incident_desk.py (19 tests, Fabric faked; incidents stage 1)
- **Source:** `sidecar/fabric_desktop/workloads/incidents.py`, `sidecar/fabric_desktop/workloads/notify.py`, `sidecar/fabric_desktop/incident_service.py`, `app/ui/incidents.js`, `sidecar/fabric_desktop/workloads/sla.py`, `sidecar/fabric_desktop/incident_desk.py`
- **Tests:** `sidecar/tests/test_incidents.py`, `sidecar/tests/test_incident_desk.py`
- **Docs:** [docs/agent.md](../agent.md), [docs/incidents.md](../incidents.md)
- **Known limitations:** Metadata tables only. workloads/plan.md describes a Delta table; code uses a JSON document. Direction (Chris's unit testing, 2026-10-18, item 33): a simplified incident management capability (detect, triage, hand off) is now its own workstream. Numbers, priority and SLAs are in the engine only; the list doesn't show them yet.
- **Pull requests and issues:** [#74](https://github.com/KratosDataGod/KratosForge/pull/74), [#79](https://github.com/KratosDataGod/KratosForge/pull/79), [#125](https://github.com/KratosDataGod/KratosForge/pull/125), [#127](https://github.com/KratosDataGod/KratosForge/pull/127)
- **Owning workstream:** Workloads (FD-123)
- **Backlog:** [PB-047](PRODUCT_BACKLOG.md#pb-047), [PB-077](PRODUCT_BACKLOG.md#pb-077), [PB-084](PRODUCT_BACKLOG.md#pb-084), [PB-105](PRODUCT_BACKLOG.md#pb-105)
- **Recommended next action:** Simplified incident management (PB-105) builds on these incidents; implementation review of PRs #125 and #127

### FEAT-OPS-03

**Monitor: runs from every place.** One list of metadata runs, Fabric jobs started here, PC schedule runs and notebook writes, plus the Fabric Monitoring hub.

- **Pillar and category:** Local development; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Runs
- **Evidence:** test: sidecar/tests/test_cloud.py::test_monitor_never_signs_in_to_refresh (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_fabric_compute.py::test_runs_from_every_environment
- **Source:** `sidecar/fabric_desktop/cloud_service.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_cloud.py`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Only jobs started from this app are tracked. fabric_runs.jsonl keeps 500 entries.
- **Pull requests and issues:** [#58](https://github.com/KratosDataGod/KratosForge/pull/58), [#579](https://github.com/KratosDataGod/KratosForge/pull/579)
- **Recommended next action:** Fabric pipeline runs (FEAT-ORC-10) join this list after the Data Factory mockup.

### FEAT-OPS-04

**Live test harness.** fabric-desktop live-test: guarded checks of sign-in, the Fabric API, OneLake files and Delta on a workspace marked for tests, with a ledger and cleanup.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** CLI only
- **Evidence:** test: sidecar/tests/test_livetest.py::test_refuses_a_workspace_not_marked_for_tests_or_that_looks_like_production (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_livetest.py::test_a_failure_midway_still_cleans_up_and_keeps_tokens_out; smoke: fabric-desktop live-test --help listed --dry-run, --cleanup and --agent-config; test: sidecar/tests/test_livetest.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/livetest.py`, `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_livetest.py`
- **Docs:** [docs/live-testing.md](../live-testing.md)
- **Known limitations:** Never run. Doesn't test leases, concurrent writers, mailbox, Run in Fabric.
- **Pull requests and issues:** [#80](https://github.com/KratosDataGod/KratosForge/pull/80), [#630](https://github.com/KratosDataGod/KratosForge/pull/630)
- **Owning workstream:** Enterprise readiness (FD-101)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002)
- **Recommended next action:** Chris runs it on a non-production workspace (PB-002); it now runs the notebook in Fabric only after it published as written.

### FEAT-ADM-01

**Cost estimates.** CU-seconds per Fabric run at a configurable price, what PC runs saved, capacity share by SKU.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Optimize > Cost and savings (Preview)
- **Evidence:** test: sidecar/tests/test_estate.py::test_cost_estimates_fabric_runs_and_what_local_runs_saved (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_measurement.py (passed in the 2026-10-17 refresh run at 9715e28)
- **Source:** `sidecar/fabric_desktop/costs.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`, `sidecar/fabric_desktop/measurement.py`
- **Tests:** `sidecar/tests/test_estate.py`, `sidecar/tests/test_measurement.py`
- **Docs:** [docs/finops/cost-evidence.md](../finops/cost-evidence.md)
- **Known limitations:** Estimates only (default $0.18/CU-hour, Python 1 CU, Spark 8 CU). Agent runs counted as local with no measured cost. Direction change (Chris's unit testing, 2026-10-18, item 36, decided 10:10Z): Monitor > Cost moves to the Optimize page's Cost and savings tab, without Fabric's Monitoring hub underneath.
- **Pull requests and issues:** [#117](https://github.com/KratosDataGod/KratosForge/pull/117)
- **Owning workstream:** Administration and FinOps sprint (FINOPS-001)
- **Backlog:** [PB-050](PRODUCT_BACKLOG.md#pb-050), [PB-104](PRODUCT_BACKLOG.md#pb-104)
- **Recommended next action:** Calibrate against the Capacity Metrics app and label every number (PB-050)

### FEAT-ADM-02

**Audit trail.** One hash-chained line per call that runs or changes something.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: sidecar/tests/test_audit.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/audit.py; test: sidecar/tests/test_audit_chain.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/audit.py`
- **Tests:** `sidecar/tests/test_audit.py`, `sidecar/tests/test_audit_chain.py`
- **Pull requests and issues:** [#89](https://github.com/KratosDataGod/KratosForge/pull/89), [#623](https://github.com/KratosDataGod/KratosForge/pull/623), [#629](https://github.com/KratosDataGod/KratosForge/pull/629)
- **Owning workstream:** Enterprise readiness (FD-125)
- **Backlog:** [PB-007](PRODUCT_BACKLOG.md#pb-007)
- **Recommended next action:** HD-017: the chain notices lines deleted from the end, and the user guide says how to clear a reported gap.

### FEAT-ADM-03

**Backup and restore of local state.** Back up and restore settings, schedules, queue, workloads and receipts without sign-in tokens.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: sidecar/tests/test_backup.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/backup.py
- **Source:** `sidecar/fabric_desktop/backup.py`
- **Tests:** `sidecar/tests/test_backup.py`
- **Docs:** [docs/backup.md](../backup.md)
- **Pull requests and issues:** [#91](https://github.com/KratosDataGod/KratosForge/pull/91)
- **Owning workstream:** Enterprise readiness (FD-126)
- **Backlog:** [PB-066](PRODUCT_BACKLOG.md#pb-066)
- **Recommended next action:** Implementation review of PR #91; HD-005 (restore removes the stale WAL and verifies) is a P0 fix

### FEAT-ADM-04

**Ready to work checklist.** First-run checklist of sign-in, capacity, workspace access and OneLake reach with fixes.

- **Pillar and category:** Enterprise services; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: sidecar/tests/test_readiness.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/readiness.py
- **Source:** `sidecar/fabric_desktop/readiness.py`
- **Tests:** `sidecar/tests/test_readiness.py`
- **Pull requests and issues:** [#94](https://github.com/KratosDataGod/KratosForge/pull/94)
- **Owning workstream:** Enterprise readiness (FD-136)
- **Backlog:** [PB-086](PRODUCT_BACKLOG.md#pb-086)
- **Recommended next action:** Implementation review of PR #94

### FEAT-ADM-05

**Local data retention.** Delete local copies of lakehouse data after a policy period.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: sidecar/tests/test_retention.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/retention.py
- **Source:** `sidecar/fabric_desktop/retention.py`
- **Tests:** `sidecar/tests/test_retention.py`
- **Pull requests and issues:** [#95](https://github.com/KratosDataGod/KratosForge/pull/95)
- **Owning workstream:** Enterprise readiness (FD-131b)
- **Backlog:** [PB-009](PRODUCT_BACKLOG.md#pb-009)
- **Recommended next action:** Implementation review of PR #95

### FEAT-ADM-06

**Opt-in telemetry.** Consent-based usage telemetry with a policy switch.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None (Preview)
- **Evidence:** test: sidecar/tests/test_telemetry.py (passed in the 2026-10-17 refresh run at 9715e28); doc: docs/telemetry.md (off until turned on in Settings > Usage data)
- **Source:** `sidecar/fabric_desktop/telemetry.py`, `app/ui/settings.js`
- **Tests:** `sidecar/tests/test_telemetry.py`
- **Docs:** [docs/telemetry.md](../telemetry.md)
- **Pull requests and issues:** [#121](https://github.com/KratosDataGod/KratosForge/pull/121)
- **Owning workstream:** Enterprise readiness (FD-137)
- **Backlog:** [PB-093](PRODUCT_BACKLOG.md#pb-093)
- **Recommended next action:** Implementation review of PR #121; nothing is sent until someone opts in and a connection string exists

### FEAT-SEC-01

**Code signing.** Signed exe and engine DLLs so Smart App Control and WDAC allow the engine.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** code: .github/workflows/ci.yml signs only when Trusted Signing secrets exist; doc: PR #28 blocked on the Trusted Signing account
- **Source:** `.github/workflows/ci.yml`
- **Pull requests and issues:** [#28](https://github.com/KratosDataGod/KratosForge/pull/28)
- **Owning workstream:** Enterprise readiness (FD-103, FD-104)
- **Backlog:** [PB-001](PRODUCT_BACKLOG.md#pb-001)
- **Recommended next action:** Chris creates the Azure Trusted Signing account (D-C3) (PB-001).

### FEAT-SEC-02

**Content Security Policy.** A Content Security Policy in the main webview, behind escaping; notebook HTML output runs on its own origin (nbout:) so it can't reach the app.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: app/tests/browser/csp.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); code: app/src-tauri/tauri.conf.json security.csp (default-src 'none', script-src 'self', frame-src nbout:)
- **Source:** `app/src-tauri/tauri.conf.json`
- **Tests:** `app/tests/browser/csp.test.cjs`
- **Docs:** [docs/security.md](../security.md)
- **Pull requests and issues:** [#250](https://github.com/KratosDataGod/KratosForge/pull/250), [#445](https://github.com/KratosDataGod/KratosForge/pull/445), [#618](https://github.com/KratosDataGod/KratosForge/pull/618), [#619](https://github.com/KratosDataGod/KratosForge/pull/619)
- **Owning workstream:** Codebase audit and hardening (HD-020)
- **Backlog:** [PB-006](PRODUCT_BACKLOG.md#pb-006)
- **Recommended next action:** HD-052 checks URL sinks in the UI and shows only picture types as images; check under WebView2 on Windows.

### FEAT-SEC-03

**Supply chain: pinned packages, audit, SBOM, provenance.** Pinned Python packages, pip-audit and cargo-audit, CycloneDX SBOMs for app and engine, build provenance attestation, SHA-pinned actions in CI.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** Releases
- **Evidence:** code: .github/workflows/ci.yml audit, sbom-app, engine, release jobs; absent: CI not run in this assessment
- **Source:** `.github/workflows/ci.yml`, `sidecar/constraints.txt`
- **Docs:** [docs/security.md](../security.md)
- **Known limitations:** docs.yml and codex-review.yml use tag-pinned actions; zensical install unpinned. release doesn't wait for audit.
- **Pull requests and issues:** [#86](https://github.com/KratosDataGod/KratosForge/pull/86), [#88](https://github.com/KratosDataGod/KratosForge/pull/88)
- **Owning workstream:** Enterprise readiness (FD-103)
- **Backlog:** [PB-010](PRODUCT_BACKLOG.md#pb-010), [PB-074](PRODUCT_BACKLOG.md#pb-074)
- **Recommended next action:** Fix docs/security.md, which still says there is no scanning, SBOM or provenance; pin docs.yml and codex-review.yml actions by SHA.

### FEAT-REL-01

**Continuous release channels.** Every green main build replaces the Preview release; Promote to stable copies it to Stable.

- **Pillar and category:** Platform foundation; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** Settings > Updates
- **Evidence:** code: .github/workflows/ci.yml release job; code: .github/workflows/promote.yml
- **Source:** `.github/workflows/ci.yml`, `.github/workflows/promote.yml`
- **Docs:** [docs/install.md](../install.md)
- **Known limitations:** Only the newest build per channel. Promote can't choose a build; SBOMs not copied. cargo test never runs.
- **Pull requests and issues:** [#111](https://github.com/KratosDataGod/KratosForge/pull/111)
- **Backlog:** [PB-064](PRODUCT_BACKLOG.md#pb-064), [PB-071](PRODUCT_BACKLOG.md#pb-071)
- **Recommended next action:** Per-build release assets for pinning and rollback (PB-064).

### FEAT-REL-02

**Documentation site.** Zensical site from docs/ with strict link checking, published to GitHub Pages.

- **Pillar and category:** Platform foundation; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** GitHub Pages
- **Evidence:** code: .github/workflows/docs.yml; code: zensical.toml strict = true
- **Source:** `zensical.toml`, `.github/workflows/docs.yml`
- **Docs:** [docs/README.md](../README.md), [docs/end-user-terms.md](../end-user-terms.md)
- **Pull requests and issues:** [#40](https://github.com/KratosDataGod/KratosForge/pull/40), [#562](https://github.com/KratosDataGod/KratosForge/pull/562), [#590](https://github.com/KratosDataGod/KratosForge/pull/590), [#617](https://github.com/KratosDataGod/KratosForge/pull/617)
- **Recommended next action:** Docs audit 3 due at about #662 (cadence every 50 merged PRs).

### FEAT-GIT-01

**Source control.** Git on the PC: clone, status, diffs (notebook-aware), branches, commit with a secret scan, pull and push in the background.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Source control (bottom of the rail) (Preview)
- **Evidence:** test: sidecar/tests/test_git.py::test_notebook_diffs_compare_code_and_leave_outputs_out (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/git_service.py`, `sidecar/fabric_desktop/git/repo.py`, `sidecar/fabric_desktop/git/secrets.py`, `app/ui/git.js`
- **Tests:** `sidecar/tests/test_git.py`
- **Docs:** [docs/source-control.md](../source-control.md)
- **Known limitations:** Not tried with Git Credential Manager and SSH on many PCs. No line staging, rebase, tags.
- **Pull requests and issues:** [#85](https://github.com/KratosDataGod/KratosForge/pull/85)
- **Owning workstream:** Git-native development
- **Recommended next action:** Owned by the Git-native program.

### FEAT-EXT-01

**Command-line interface.** fabric-desktop serve, sign-in, preview, run, history, run-schedule, agent, live-test, support-bundle.

- **Pillar and category:** Platform foundation; Extensibility, APIs, CLI, and MCP
- **Status:** Implemented. **Verification:** Verified.
- **Where people reach it:** Terminal
- **Evidence:** test: sidecar/tests/test_agent.py::test_cli_check_enqueue_and_status (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: fabric-desktop --help listed all nine commands
- **Source:** `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_agent.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Recommended next action:** Add backup/restore (PR #91) and an mcp command (PR-AI-002).

### FEAT-EXT-02

**MCP server and local API for external AI tools.** Read-only MCP over stdio, a capability registry, permissions and approvals, REST and MCP over HTTP on 127.0.0.1, resources, approved execution, Settings > Integrations, audit, conformance tests.

- **Pillar and category:** Enterprise services; Extensibility, APIs, CLI, and MCP
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None
- **Evidence:** test: sidecar/tests/test_integrations_mcp.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_integrations_http.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_integrations_conformance.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_integrations_permissions.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_integrations_executions.py (passed in the 2026-10-17 refresh run at 9715e28); test: sidecar/tests/test_integrations_audit.py (passed in the 2026-10-17 refresh run at 9715e28); absent: No named AI tool has been run against it (docs/product/ai-integration-client-checklist.md, PB-096)
- **Source:** `sidecar/fabric_desktop/integrations/mcp_server.py`, `sidecar/fabric_desktop/integrations/http_server.py`, `sidecar/fabric_desktop/integrations/registry.py`, `sidecar/fabric_desktop/integrations/permissions.py`, `sidecar/fabric_desktop/integrations/executions.py`, `app/ui/integrations.js`
- **Tests:** `sidecar/tests/test_integrations_conformance.py`
- **Docs:** [docs/architecture/ai-integration.md](../architecture/ai-integration.md), [docs/ai/README.md](../ai/README.md)
- **Pull requests and issues:** [#98](https://github.com/KratosDataGod/KratosForge/pull/98), [#99](https://github.com/KratosDataGod/KratosForge/pull/99), [#100](https://github.com/KratosDataGod/KratosForge/pull/100), [#101](https://github.com/KratosDataGod/KratosForge/pull/101), [#102](https://github.com/KratosDataGod/KratosForge/pull/102), [#103](https://github.com/KratosDataGod/KratosForge/pull/103), [#104](https://github.com/KratosDataGod/KratosForge/pull/104), [#105](https://github.com/KratosDataGod/KratosForge/pull/105), [#106](https://github.com/KratosDataGod/KratosForge/pull/106), [#107](https://github.com/KratosDataGod/KratosForge/pull/107), [#108](https://github.com/KratosDataGod/KratosForge/pull/108), [#110](https://github.com/KratosDataGod/KratosForge/pull/110), [#112](https://github.com/KratosDataGod/KratosForge/pull/112)
- **Owning workstream:** Bring your own AI integration
- **Backlog:** [PB-095](PRODUCT_BACKLOG.md#pb-095), [PB-096](PRODUCT_BACKLOG.md#pb-096)
- **Recommended next action:** Run the client checklist per named AI tool (PB-096); HD-006 and HD-014 harden approvals and workspace limits

### FEAT-EXT-03

**Extensions.** Extension manifest, validators, templates and third-party commands in their own process.

- **Pillar and category:** Enterprise services; Extensibility, APIs, CLI, and MCP
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: docs/git-integration-plan.md (Phase 4)
- **Owning workstream:** Git-native development
- **Backlog:** [PB-097](PRODUCT_BACKLOG.md#pb-097)
- **Recommended next action:** Owned by the Git-native program (FD-132..135).

### FEAT-EXT-04

**Debugger for notebooks.** Breakpoints on notebook line numbers, F5/F10/F11 stepping, variable history and watches in the notebook's own process (Preview). Pipelines have no debugger.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook line numbers, Variables pane, View tab (Preview)
- **Evidence:** test: sidecar/tests/test_debugger.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_debugging.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/debugger.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/debugger.py`, `app/ui/debugging.js`
- **Tests:** `sidecar/tests/test_debugger.py`, `sidecar/tests/test_debugging.py`, `app/tests/browser/debugger.test.cjs`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Notebooks only: pipelines have no breakpoints. Needs Run notebooks in their own process.
- **Pull requests and issues:** [#387](https://github.com/KratosDataGod/KratosForge/pull/387)
- **Owning workstream:** Prove
- **Backlog:** [PB-026](PRODUCT_BACKLOG.md#pb-026)
- **Recommended next action:** Implementation review of PR #387; decide whether pipeline runs need step-through.

### FEAT-SQL-03

**Fabric SQL dialect compatibility analyzer.** Checks a SQL script against Fabric SQL dialect profiles and reports what will and won't run, with an Unknown outcome when it can't tell.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** SQL page
- **Evidence:** test: sidecar/tests/test_sql_compat.py (passed in the 2026-10-17 refresh run at 9715e28); absent: app/tests/browser/*.test.cjs not run: Playwright is not installed here and CI does not run them (HD-052); code: sidecar/fabric_desktop/sqlcompat/analyzer.py
- **Source:** `sidecar/fabric_desktop/sqlcompat/analyzer.py`, `sidecar/fabric_desktop/sqlcompat/profiles.py`, `sidecar/fabric_desktop/sqlcompat/rules.py`, `app/ui/sqleditor.js`
- **Tests:** `sidecar/tests/test_sql_compat.py`
- **Docs:** [docs/sql-compatibility.md](../sql-compatibility.md)
- **Pull requests and issues:** [#120](https://github.com/KratosDataGod/KratosForge/pull/120)
- **Owning workstream:** SQL development sprint (SQL-002)
- **Recommended next action:** Compare its verdicts with a Fabric Warehouse run on a tenant (HD-054)

### FEAT-ADM-07

**Capacity of each environment.** Read-only list of the Fabric capacity each environment's workspace is on and whether it is running, on Optimize > Cost and savings and each environment card.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Optimize > Cost and savings; Data mesh > Environments (Preview)
- **Evidence:** test: sidecar/tests/test_capacity.py (passed in the 2026-10-17 refresh run at 9715e28); doc: docs/finops/capacity.md (running is not the same as having room)
- **Source:** `sidecar/fabric_desktop/capacity.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`
- **Tests:** `sidecar/tests/test_capacity.py`
- **Docs:** [docs/finops/capacity.md](../finops/capacity.md)
- **Pull requests and issues:** [#123](https://github.com/KratosDataGod/KratosForge/pull/123)
- **Owning workstream:** Administration and FinOps sprint (FINOPS-002)
- **Recommended next action:** Check against a live tenant's capacities (PB-002)

### FEAT-LDE-01

**Local data projects.** A persistent DuckDB project on the PC over registered folders of CSV, JSON and Parquet files, with memory, thread and time limits, spill to disk and cancel.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None yet (engine calls only) (Preview)
- **Evidence:** test: sidecar/tests/test_local_data.py (passed in the 2026-10-17 refresh run at 9715e28); doc: docs/local-data.md (the screens come after the mockup is agreed); test: sidecar/tests/test_local_data.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_local_transform.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_sql_exec.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/localdata/engine.py`, `sidecar/fabric_desktop/localdata/discovery.py`, `sidecar/fabric_desktop/localdata/projects.py`, `sidecar/fabric_desktop/localdata_service.py`
- **Tests:** `sidecar/tests/test_local_data.py`, `sidecar/tests/test_local_transform.py`, `sidecar/tests/test_sql_exec.py`
- **Docs:** [docs/local-data.md](../local-data.md), [docs/local-data-engine-plan.md](../local-data-engine-plan.md)
- **Pull requests and issues:** [#126](https://github.com/KratosDataGod/KratosForge/pull/126), [#620](https://github.com/KratosDataGod/KratosForge/pull/620), [#628](https://github.com/KratosDataGod/KratosForge/pull/628), [#631](https://github.com/KratosDataGod/KratosForge/pull/631)
- **Owning workstream:** Local data engine and OneLake publishing (FD-LDE-001, FD-LDE-002)
- **Recommended next action:** local_query, local_explain, Delta writes and transformation steps are checked like a managed local run; screens still wait on the mockup.

### FEAT-ORC-07

**Fabric pipeline definitions on this PC.** Reads, checks and exports Fabric Data pipeline definitions (pipeline-content.json, getDefinition results or Git folders) without running anything.

- **Pillar and category:** Local development; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** fabric-desktop pipeline (CLI) and the external-tool capabilities; no screen
- **Evidence:** test: sidecar/tests/test_pipeline_definitions.py (passed in the 2026-10-17 refresh run at 9715e28); code: sidecar/fabric_desktop/cli.py pipeline command
- **Source:** `sidecar/fabric_desktop/orchestration/definition.py`, `sidecar/fabric_desktop/orchestration/expressions.py`, `sidecar/fabric_desktop/orchestration/validate.py`, `sidecar/fabric_desktop/pipeline_service.py`, `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_pipeline_definitions.py`
- **Docs:** [docs/orchestration/pipeline-definitions.md](../orchestration/pipeline-definitions.md)
- **Pull requests and issues:** [#130](https://github.com/KratosDataGod/KratosForge/pull/130)
- **Owning workstream:** Local Fabric pipeline execution (ORCH-001)
- **Recommended next action:** ORCH-002 activity registry, after Chris approves

### FEAT-NB-12

**Notebook import and export formats.** Import Fabric and Databricks notebook source and .dbc archives; export as .ipynb, Fabric source or Databricks source.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Home > Import; a notebook's Home tab > Export (Preview)
- **Evidence:** test: sidecar/tests/test_notebook_formats.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-formats.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/notebook_formats.py`, `sidecar/fabric_desktop/notebook_formats_service.py`, `app/ui/notebook-formats.js`
- **Tests:** `sidecar/tests/test_notebook_formats.py`, `app/tests/browser/notebook-formats.test.cjs`
- **Docs:** [docs/notebook-import-export.md](../notebook-import-export.md)
- **Pull requests and issues:** [#350](https://github.com/KratosDataGod/KratosForge/pull/350)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #350; run on a tenant before the Preview tag comes off.

### FEAT-NB-13

**Notebook folders.** Group notebooks into folders, move notebooks between them, rename and delete empty folders; publish names the item without its folder (Preview).

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** All notebooks
- **Evidence:** test: sidecar/tests/test_notebook_folders.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-folders.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/notebook_folders.py`, `sidecar/fabric_desktop/notebook_folders_service.py`
- **Tests:** `sidecar/tests/test_notebook_folders.py`, `app/tests/browser/notebook-folders.test.cjs`
- **Docs:** [docs/notebook-folders.md](../notebook-folders.md)
- **Pull requests and issues:** [#444](https://github.com/KratosDataGod/KratosForge/pull/444)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #444.

### FEAT-NB-14

**Notebook version history.** A version per save and named checkpoints, with Compare and Restore.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Versions
- **Evidence:** test: sidecar/tests/test_notebook_versions.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-versions.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/notebook_versions.py`, `sidecar/fabric_desktop/notebook_versions_service.py`, `app/ui/notebook-versions.js`
- **Tests:** `sidecar/tests/test_notebook_versions.py`, `app/tests/browser/notebook-versions.test.cjs`
- **Docs:** [docs/notebook-versions.md](../notebook-versions.md)
- **Pull requests and issues:** [#346](https://github.com/KratosDataGod/KratosForge/pull/346), [#461](https://github.com/KratosDataGod/KratosForge/pull/461), [#650](https://github.com/KratosDataGod/KratosForge/pull/650)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #346, #461.

### FEAT-NB-15

**Notebook comments.** Comment threads on a cell or lines of its code, with replies, resolve and a Comments panel, kept in the notebook file.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells and the Comments panel
- **Evidence:** test: sidecar/tests/test_notebook_comments.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-comments.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/comments_service.py`, `app/ui/notebook-comments.js`
- **Tests:** `sidecar/tests/test_notebook_comments.py`, `app/tests/browser/notebook-comments.test.cjs`
- **Docs:** [docs/notebook-comments.md](../notebook-comments.md)
- **Pull requests and issues:** [#388](https://github.com/KratosDataGod/KratosForge/pull/388)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #388.

### FEAT-NB-16

**Notebook resources.** Files kept beside a notebook and read as builtin/<file>, like Fabric's built-in resources.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook > Resources
- **Evidence:** test: sidecar/tests/test_notebook_resources.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-resources.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/notebook_resources.py`, `sidecar/fabric_desktop/notebook_resources_service.py`, `app/ui/nbresources.js`
- **Tests:** `sidecar/tests/test_notebook_resources.py`, `app/tests/browser/notebook-resources.test.cjs`
- **Docs:** [docs/notebook-resources.md](../notebook-resources.md)
- **Pull requests and issues:** [#376](https://github.com/KratosDataGod/KratosForge/pull/376)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #376.

### FEAT-NB-17

**Notebook files and editing Fabric notebooks on this PC.** Search inside notebooks, open a notebook file, save a copy, delete and restore, and edit a Fabric notebook item on this PC (Preview).

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** All notebooks; Workspaces item preview > Edit on this PC (Preview)
- **Evidence:** test: sidecar/tests/test_notebook_files.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-files.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-disk-change.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: sidecar/tests/test_notebook_versions.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/notebook_files.py`, `sidecar/fabric_desktop/notebook_files_service.py`, `app/ui/notebook-files.js`
- **Tests:** `sidecar/tests/test_notebook_files.py`, `app/tests/browser/notebook-files.test.cjs`, `app/tests/browser/notebook-disk-change.test.cjs`, `sidecar/tests/test_notebook_versions.py`
- **Docs:** [docs/notebook-files.md](../notebook-files.md)
- **Known limitations:** Edits made in another program while the notebook is open are kept: the app never saves over them (#650); VS Code integration builds on this.
- **Pull requests and issues:** [#320](https://github.com/KratosDataGod/KratosForge/pull/320), [#650](https://github.com/KratosDataGod/KratosForge/pull/650)
- **Owning workstream:** Notebook development program
- **Recommended next action:** Implementation review of PR #320; run on a tenant before the Preview tag comes off.

### FEAT-NB-18

**Notebook cell editing.** Select several cells to run, copy, merge, freeze or delete; run selected text; collapse heading sections, cell titles and tags; find and replace across cells; drag tables and files into cells.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook editor
- **Evidence:** test: app/tests/browser/notebook-select.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-cells.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-find.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-drag.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-folded.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `app/ui/nbselect.js`, `app/ui/nbcells.js`, `app/ui/nbfind.js`, `app/ui/nbdrag.js`
- **Tests:** `app/tests/browser/notebook-select.test.cjs`, `app/tests/browser/notebook-cells.test.cjs`, `app/tests/browser/notebook-find.test.cjs`, `app/tests/browser/notebook-drag.test.cjs`, `app/tests/browser/notebook-folded.test.cjs`
- **Docs:** [docs/notebook-editor/select-and-run-parts.md](../notebook-editor/select-and-run-parts.md), [docs/notebook-editor/organise-cells.md](../notebook-editor/organise-cells.md), [docs/notebook-editor/find-and-replace.md](../notebook-editor/find-and-replace.md), [docs/notebook-editor/load-data-items.md](../notebook-editor/load-data-items.md)
- **Pull requests and issues:** [#338](https://github.com/KratosDataGod/KratosForge/pull/338), [#347](https://github.com/KratosDataGod/KratosForge/pull/347), [#383](https://github.com/KratosDataGod/KratosForge/pull/383), [#390](https://github.com/KratosDataGod/KratosForge/pull/390), [#473](https://github.com/KratosDataGod/KratosForge/pull/473)
- **Owning workstream:** Notebook editor
- **Recommended next action:** Implementation review of PR #338, #347, #383, #390, #473.

### FEAT-NB-19

**Notebook IntelliSense.** Hover docs, signature help, docs in autocomplete and syntax errors as you type, from the notebook's own kernel.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook code cells
- **Evidence:** test: sidecar/tests/test_intellisense.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-intellisense.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/intellisense.py`, `app/ui/nbintel.js`
- **Tests:** `sidecar/tests/test_intellisense.py`, `app/tests/browser/notebook-intellisense.test.cjs`
- **Docs:** [docs/notebook-editor/intellisense.md](../notebook-editor/intellisense.md)
- **Pull requests and issues:** [#354](https://github.com/KratosDataGod/KratosForge/pull/354)
- **Owning workstream:** Notebook editor
- **Recommended next action:** Implementation review of PR #354.

### FEAT-NB-20

**Format code in notebooks.** Format a cell or the whole notebook: Python in Black's style with ruff, %%sql cells as Format SQL lays them out.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook Edit tab > Format cell, Format notebook
- **Evidence:** test: sidecar/tests/test_code_format.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/notebook-format.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/code_format.py`, `app/ui/nbformat.js`
- **Tests:** `sidecar/tests/test_code_format.py`, `app/tests/browser/notebook-format.test.cjs`
- **Docs:** [docs/notebook-editor/format-code.md](../notebook-editor/format-code.md)
- **Pull requests and issues:** [#470](https://github.com/KratosDataGod/KratosForge/pull/470), [#486](https://github.com/KratosDataGod/KratosForge/pull/486), [#491](https://github.com/KratosDataGod/KratosForge/pull/491)
- **Owning workstream:** Notebook editor
- **Recommended next action:** Implementation review of PR #470, #486, #491.

### FEAT-NB-21

**Notebook environments.** Extra Python packages per notebook, installed with pip after a confirm; the app's own packages always win; Spark packages are refused.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook status bar > Environment (Preview)
- **Evidence:** test: sidecar/tests/test_environments.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_environment_isolation.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/environments.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/environments.py`, `sidecar/fabric_desktop/environment_service.py`, `app/ui/nbenv.js`
- **Tests:** `sidecar/tests/test_environments.py`, `sidecar/tests/test_environment_isolation.py`, `app/tests/browser/environments.test.cjs`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Not published with the notebook: in Fabric, attach a Fabric Environment with the same packages.
- **Pull requests and issues:** [#421](https://github.com/KratosDataGod/KratosForge/pull/421), [#539](https://github.com/KratosDataGod/KratosForge/pull/539)
- **Owning workstream:** Notebook runtime
- **Recommended next action:** Implementation review of PR #421, #539; run on a tenant before the Preview tag comes off.

### FEAT-NB-22

**Notebook sessions.** A session per notebook, several running at once, with limits in Settings; notebooks can share one session like Fabric's high-concurrency mode (Preview).

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook status bar > Sessions (Preview)
- **Evidence:** test: sidecar/tests/test_kernel_sessions.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/sessions.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_shared_sessions.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/kernel_sessions.py`, `app/ui/sessions.js`
- **Tests:** `sidecar/tests/test_kernel_sessions.py`, `app/tests/browser/sessions.test.cjs`, `sidecar/tests/test_shared_sessions.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Pull requests and issues:** [#379](https://github.com/KratosDataGod/KratosForge/pull/379), [#464](https://github.com/KratosDataGod/KratosForge/pull/464), [#490](https://github.com/KratosDataGod/KratosForge/pull/490), [#561](https://github.com/KratosDataGod/KratosForge/pull/561)
- **Owning workstream:** Notebook runtime
- **Recommended next action:** Implementation review of PR #561; run on a tenant before the Preview tag comes off.

### FEAT-NB-23

**Run the last cell on Fabric.** Run every cell on this PC, then the last cell on Fabric Spark, so tables are V-Ordered for Direct Lake.

- **Pillar and category:** Local development; Fabric Integration and Deployment
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cell ⋯ menu > Run on Fabric (Spark) (Preview)
- **Evidence:** test: sidecar/tests/test_fabric_step.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/fabric-step.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_fabric_step.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/fabric-step.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/fabric_step.py`, `app/ui/fabricstep.js`
- **Tests:** `sidecar/tests/test_fabric_step.py`, `app/tests/browser/fabric-step.test.cjs`
- **Docs:** [docs/notebook-fabric-step.md](../notebook-fabric-step.md)
- **Known limitations:** Not yet run on a tenant: the Fabric half is tested against a faked Fabric.
- **Pull requests and issues:** [#550](https://github.com/KratosDataGod/KratosForge/pull/550), [#570](https://github.com/KratosDataGod/KratosForge/pull/570), [#577](https://github.com/KratosDataGod/KratosForge/pull/577), [#584](https://github.com/KratosDataGod/KratosForge/pull/584)
- **Owning workstream:** Notebook runtime
- **Recommended next action:** Fabric step reads V-Order from file footers, Rewrite all asks for V-Order and its cost, risk check reads the attached workspace; run on a tenant before Preview comes off.

### FEAT-NB-24

**Validate a notebook.** Code checks, a clean run from the top with sandboxed writes, and Clean up for imports, spacing and suggestions.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Validate (Preview)
- **Evidence:** test: sidecar/tests/test_validate.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/validate.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/validate.py`, `sidecar/fabric_desktop/validate_service.py`, `app/ui/validate.js`
- **Tests:** `sidecar/tests/test_validate.py`, `app/tests/browser/validate.test.cjs`
- **Docs:** [docs/validate-notebook.md](../validate-notebook.md)
- **Pull requests and issues:** [#282](https://github.com/KratosDataGod/KratosForge/pull/282)
- **Owning workstream:** Prove
- **Recommended next action:** Implementation review of PR #282; run on a tenant before the Preview tag comes off.

### FEAT-NB-25

**Rich notebook outputs and charts.** displayHTML and IPython display (with display handles), matplotlib, plotly, bokeh, seaborn and Altair in the cell, several charts per result, the chart builder, result downloads, Data Wrangler and ipywidgets, with per-cell output caps.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cell output (Preview)
- **Evidence:** test: sidecar/tests/test_rich_outputs.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_chart_libraries.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_display_handles.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_ipywidgets.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_wrangler.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_outputs_hardening.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_output_cap.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/altair.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/ipywidgets.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/several-charts.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/result-viewer.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/plotly.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/ipython_display.py`, `sidecar/fabric_desktop/chart_output.py`, `sidecar/fabric_desktop/widgets.py`, `sidecar/fabric_desktop/wrangler.py`, `app/ui/charts.js`, `app/ui/ipywidgets.js`, `app/ui/wrangler.js`
- **Tests:** `sidecar/tests/test_rich_outputs.py`, `sidecar/tests/test_chart_libraries.py`, `sidecar/tests/test_display_handles.py`, `sidecar/tests/test_ipywidgets.py`, `sidecar/tests/test_wrangler.py`, `sidecar/tests/test_outputs_hardening.py`, `sidecar/tests/test_output_cap.py`, `app/tests/browser/altair.test.cjs`, `app/tests/browser/ipywidgets.test.cjs`, `app/tests/browser/several-charts.test.cjs`, `app/tests/browser/result-viewer.test.cjs`, `app/tests/browser/plotly.test.cjs`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Altair, ipywidgets, Data Wrangler, the chart builder, result downloads and several charts per result carry Preview tags.
- **Pull requests and issues:** [#385](https://github.com/KratosDataGod/KratosForge/pull/385), [#386](https://github.com/KratosDataGod/KratosForge/pull/386), [#427](https://github.com/KratosDataGod/KratosForge/pull/427), [#430](https://github.com/KratosDataGod/KratosForge/pull/430), [#448](https://github.com/KratosDataGod/KratosForge/pull/448), [#472](https://github.com/KratosDataGod/KratosForge/pull/472), [#479](https://github.com/KratosDataGod/KratosForge/pull/479), [#490](https://github.com/KratosDataGod/KratosForge/pull/490), [#546](https://github.com/KratosDataGod/KratosForge/pull/546), [#552](https://github.com/KratosDataGod/KratosForge/pull/552), [#632](https://github.com/KratosDataGod/KratosForge/pull/632)
- **Owning workstream:** Notebook outputs
- **Recommended next action:** Implementation review of PR #385, #386, #427, #430, #448, #472, #479, #490, #546, #552; run on a tenant before the Preview tag comes off.

### FEAT-NB-26

**Databricks notebooks on this PC.** dbutils (fs, notebook, widgets, secrets from Azure Key Vault, jobs.taskValues, library, data) so Databricks notebooks run here where the same thing exists.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_dbutils.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/dbutils_local.py`
- **Tests:** `sidecar/tests/test_dbutils.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** dbfs:/, /dbfs/ and /Volumes/ paths can't be reached from this PC. Databricks-backed secret scopes can't be read.
- **Pull requests and issues:** [#371](https://github.com/KratosDataGod/KratosForge/pull/371)
- **Owning workstream:** Fabric and Databricks unified development
- **Recommended next action:** Implementation review of PR #371.

### FEAT-ORC-08

**Running pipelines on this PC.** Runs a Fabric pipeline's control flow on the PC (dependencies, conditions, loops, variables, failure handling, child pipelines); Lookup and Get Metadata read a lakehouse and Script runs through the SQL runner; Create > Pipeline, the Pipelines page, a Run dialog and run view, and runs in Monitor > Runs (Preview). Starts no Fabric job.

- **Pillar and category:** Local development; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Create > Pipeline, Pipelines page, Monitor > Runs (Preview)
- **Evidence:** test: sidecar/tests/test_pipeline_runs.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_pipeline_notebooks.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_pipeline_runs.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/pipelines-page.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/pipeline-runs.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/orchestration/runner.py`, `sidecar/fabric_desktop/orchestration/runs.py`, `sidecar/fabric_desktop/pipeline_notebooks.py`, `app/ui/pipelines.js`, `app/ui/pipeline-runs.js`, `sidecar/fabric_desktop/pipeline_service.py`
- **Tests:** `sidecar/tests/test_pipeline_runs.py`, `sidecar/tests/test_pipeline_notebooks.py`, `app/tests/browser/pipelines-page.test.cjs`, `app/tests/browser/pipeline-runs.test.cjs`
- **Docs:** [docs/orchestration/local-runs.md](../orchestration/local-runs.md)
- **Known limitations:** Not yet compared with a run in Fabric (verified_against_fabric: false). Copy data runs only in Fabric until P5. Lookup, Get Metadata, Script and notebook steps read or write OneLake, which uses capacity.
- **Pull requests and issues:** [#218](https://github.com/KratosDataGod/KratosForge/pull/218), [#319](https://github.com/KratosDataGod/KratosForge/pull/319), [#559](https://github.com/KratosDataGod/KratosForge/pull/559), [#563](https://github.com/KratosDataGod/KratosForge/pull/563), [#568](https://github.com/KratosDataGod/KratosForge/pull/568), [#578](https://github.com/KratosDataGod/KratosForge/pull/578), [#579](https://github.com/KratosDataGod/KratosForge/pull/579), [#606](https://github.com/KratosDataGod/KratosForge/pull/606)
- **Owning workstream:** Local Fabric pipeline execution (ORCH-004)
- **Backlog:** [PB-037](PRODUCT_BACKLOG.md#pb-037)
- **Recommended next action:** P5 Copy data (pipeline thread) after Chris's go; implementation review of #563, #568, #578, #579.

### FEAT-ORC-09

**Pipeline activity compatibility registry.** A versioned registry of Data Factory activity types saying which run on this PC, are only checked, or run only in Fabric.

- **Pillar and category:** Local development; Pipelines and Orchestration
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** fabric-desktop pipeline (CLI); no screen
- **Evidence:** test: sidecar/tests/test_activity_registry.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/orchestration/registry.py`
- **Tests:** `sidecar/tests/test_activity_registry.py`
- **Docs:** [docs/orchestration/activity-registry.md](../orchestration/activity-registry.md)
- **Pull requests and issues:** [#150](https://github.com/KratosDataGod/KratosForge/pull/150), [#563](https://github.com/KratosDataGod/KratosForge/pull/563), [#568](https://github.com/KratosDataGod/KratosForge/pull/568)
- **Owning workstream:** Local Fabric pipeline execution (ORCH-002)
- **Backlog:** [PB-036](PRODUCT_BACKLOG.md#pb-036)
- **Recommended next action:** Registry v7 on main; review with ORC-08.

### FEAT-ADM-08

**Risk check before a run.** Pass, warning, blocked or unknown before a capacity-spending run, with evidence for each finding; a check that can't run asks with its reason instead of going ahead silently. Advice only, never blocks.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Run in Fabric and Data mesh deploy confirms; Settings (Preview)
- **Evidence:** test: sidecar/tests/test_risk.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/risk.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_risk.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/risk.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/risk.py`, `sidecar/fabric_desktop/risk_service.py`, `app/ui/risk.js`
- **Tests:** `sidecar/tests/test_risk.py`, `app/tests/browser/risk.test.cjs`
- **Docs:** [docs/finops/risk-check.md](../finops/risk-check.md)
- **Pull requests and issues:** [#167](https://github.com/KratosDataGod/KratosForge/pull/167), [#459](https://github.com/KratosDataGod/KratosForge/pull/459), [#462](https://github.com/KratosDataGod/KratosForge/pull/462), [#584](https://github.com/KratosDataGod/KratosForge/pull/584), [#589](https://github.com/KratosDataGod/KratosForge/pull/589)
- **Owning workstream:** Administration and FinOps (FINOPS-005)
- **Backlog:** [PB-090](PRODUCT_BACKLOG.md#pb-090)
- **Recommended next action:** Run on a tenant before the Preview tag comes off.

### FEAT-ADM-09

**Where to run a notebook (placement advice).** Advice on running a notebook on this PC or in Fabric, with the evidence behind it.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** MCP and local API tool fabric_desktop_placement_advice (read only); no screen yet (Preview)
- **Evidence:** test: sidecar/tests/test_advisor.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/advisor.py`
- **Tests:** `sidecar/tests/test_advisor.py`
- **Docs:** [docs/finops/placement-advice.md](../finops/placement-advice.md)
- **Pull requests and issues:** [#276](https://github.com/KratosDataGod/KratosForge/pull/276)
- **Owning workstream:** Administration and FinOps (FINOPS-005)
- **Backlog:** [PB-017](PRODUCT_BACKLOG.md#pb-017)
- **Recommended next action:** Implementation review of PR #276; run on a tenant before the Preview tag comes off.

### FEAT-ADM-10

**Measured usage import.** Measured CU from a Capacity Metrics app export, matched to this app's runs beside their estimates, with billed CU hours.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Optimize > Cost and savings (Preview)
- **Evidence:** test: sidecar/tests/test_usage_import.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/usage_service.py`, `sidecar/fabric_desktop/usage`
- **Tests:** `sidecar/tests/test_usage_import.py`
- **Docs:** [docs/finops/usage-import.md](../finops/usage-import.md)
- **Pull requests and issues:** [#153](https://github.com/KratosDataGod/KratosForge/pull/153)
- **Owning workstream:** Administration and FinOps (FINOPS-003)
- **Backlog:** [PB-089](PRODUCT_BACKLOG.md#pb-089)
- **Recommended next action:** Implementation review of PR #153; run on a tenant before the Preview tag comes off.

### FEAT-ADM-11

**Optimize and the central lakehouse.** One central lakehouse (FabricDesktop_Central) for every installation: opportunities, local run records, access and incident caches, with roles changed only through central_restrict.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Optimize (Preview)
- **Evidence:** test: sidecar/tests/test_optimize_service.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_optimize_setup.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_optimize_roles.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_optimize_shared.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_optimize_local_runs.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/optimize.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/settings-history.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/optimize`, `sidecar/fabric_desktop/optimize_service.py`, `app/ui/optimize.js`
- **Tests:** `sidecar/tests/test_optimize_service.py`, `sidecar/tests/test_optimize_setup.py`, `sidecar/tests/test_optimize_roles.py`, `sidecar/tests/test_optimize_shared.py`, `sidecar/tests/test_optimize_local_runs.py`, `app/tests/browser/optimize.test.cjs`, `app/tests/browser/settings-history.test.cjs`
- **Docs:** [docs/optimize/README.md](../optimize/README.md)
- **Known limitations:** Not a security boundary: anyone with access to the lakehouse can read it. Not yet set up on a tenant.
- **Pull requests and issues:** [#186](https://github.com/KratosDataGod/KratosForge/pull/186), [#228](https://github.com/KratosDataGod/KratosForge/pull/228), [#262](https://github.com/KratosDataGod/KratosForge/pull/262), [#349](https://github.com/KratosDataGod/KratosForge/pull/349), [#613](https://github.com/KratosDataGod/KratosForge/pull/613)
- **Owning workstream:** Optimize central lakehouse (PB-104)
- **Backlog:** [PB-104](PRODUCT_BACKLOG.md#pb-104)
- **Recommended next action:** Settings history can include capacity overrides for administrators (#613); not yet set up on a tenant.

### FEAT-DEV-01

**Profile.** A private record of verified work, levels and achievements, and a review-ready professional report.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Account menu > Profile (Preview)
- **Evidence:** test: sidecar/tests/test_profile.py (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/profile.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/profile.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `app/ui/profile.js`
- **Tests:** `sidecar/tests/test_profile.py`, `app/tests/browser/profile.test.cjs`
- **Docs:** [docs/profile.md](../profile.md)
- **Pull requests and issues:** [#327](https://github.com/KratosDataGod/KratosForge/pull/327), [#598](https://github.com/KratosDataGod/KratosForge/pull/598)
- **Owning workstream:** Developer profile
- **Recommended next action:** Proof Forged from Prove, Journey and Achievements tabs (KF-DX-002); implementation review of #598.

### FEAT-DEV-02

**Motion and effects.** Four visual intensity levels (0 to 3) for the forge, wolf and ember effects on notebooks, SQL, Fabric runs, pipelines, data mesh runs, saves and deploys, with panel transitions; reduced motion and load drop honored; Achievement Reveal off by default.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings > Motion and effects
- **Evidence:** test: app/tests/browser/forge-animations.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/motion-effects.test.cjs (passed in the 2026-10-22 refresh run at be3e45b); test: app/tests/browser/forge-animations.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/cooling-spark.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/pipeline-flow.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `app/ui/motion.js`
- **Tests:** `app/tests/browser/forge-animations.test.cjs`, `app/tests/browser/motion-effects.test.cjs`
- **Docs:** [docs/forge-animations.md](../forge-animations.md)
- **Pull requests and issues:** [#325](https://github.com/KratosDataGod/KratosForge/pull/325), [#372](https://github.com/KratosDataGod/KratosForge/pull/372), [#571](https://github.com/KratosDataGod/KratosForge/pull/571), [#572](https://github.com/KratosDataGod/KratosForge/pull/572), [#573](https://github.com/KratosDataGod/KratosForge/pull/573), [#574](https://github.com/KratosDataGod/KratosForge/pull/574), [#575](https://github.com/KratosDataGod/KratosForge/pull/575), [#576](https://github.com/KratosDataGod/KratosForge/pull/576), [#582](https://github.com/KratosDataGod/KratosForge/pull/582), [#583](https://github.com/KratosDataGod/KratosForge/pull/583), [#600](https://github.com/KratosDataGod/KratosForge/pull/600), [#614](https://github.com/KratosDataGod/KratosForge/pull/614), [#644](https://github.com/KratosDataGod/KratosForge/pull/644), [#646](https://github.com/KratosDataGod/KratosForge/pull/646), [#647](https://github.com/KratosDataGod/KratosForge/pull/647)
- **Owning workstream:** Forge and wolf animations
- **Recommended next action:** Draft #449 style guide waits on Chris; keep effects subordinate to work (brief section 8).

### FEAT-DEV-03

**Pop-out windows.** Monitor's sections in windows of their own.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Pop out (Preview)
- **Evidence:** test: app/tests/browser/pop-out.test.cjs (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `app/ui/popout.js`
- **Tests:** `app/tests/browser/pop-out.test.cjs`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Known limitations:** Needs a check in the Windows shell: the browser test runs in Chromium.
- **Pull requests and issues:** [#557](https://github.com/KratosDataGod/KratosForge/pull/557), [#558](https://github.com/KratosDataGod/KratosForge/pull/558)
- **Owning workstream:** UX and simplicity oversight
- **Recommended next action:** Implementation review of PR #557, #558; run on a tenant before the Preview tag comes off.

### FEAT-EXT-05

**Engines and connection profiles.** Execution engines with honest capability levels, remote compute approval, and connection profiles for Fabric, Databricks or both.

- **Pillar and category:** Local development; Extensibility, APIs, CLI, and MCP
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None: the engine underneath; no screen yet (Preview)
- **Evidence:** test: sidecar/tests/test_engines.py (passed in the 2026-10-22 refresh run at be3e45b); test: sidecar/tests/test_connprofiles.py (passed in the 2026-10-22 refresh run at be3e45b)
- **Source:** `sidecar/fabric_desktop/engines`, `sidecar/fabric_desktop/connprofiles.py`
- **Tests:** `sidecar/tests/test_engines.py`, `sidecar/tests/test_connprofiles.py`
- **Docs:** [docs/engines.md](../engines.md)
- **Pull requests and issues:** [#380](https://github.com/KratosDataGod/KratosForge/pull/380)
- **Owning workstream:** Fabric and Databricks unified development
- **Recommended next action:** Screens after a mockup; never install databricks-connect into the engine (ADR-004).

### FEAT-ORC-10

**Fabric pipeline runs and activity runs.** Reads the Fabric pipeline runs of a workspace and each run's activity runs (queryactivityruns), in a background scan, for Monitor. Engine only (Data Factory DF-1).

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None yet: Monitor > Runs rows wait on the Data Factory mockup (Preview)
- **Evidence:** test: sidecar/tests/test_fabric_pipeline_runs.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/fabric_pipeline_runs_service.py`, `sidecar/fabric_desktop/fabric_api.py`
- **Tests:** `sidecar/tests/test_fabric_pipeline_runs.py`
- **Docs:** [docs/orchestration/data-factory.md](../orchestration/data-factory.md)
- **Known limitations:** Fabric API faked in tests; not run on a tenant.
- **Pull requests and issues:** [#640](https://github.com/KratosDataGod/KratosForge/pull/640)
- **Owning workstream:** Build full Data Factory experience (DF-1..10)
- **Recommended next action:** DF-1 screen in Monitor > Runs once Chris reacts to the Data Factory mockup.

### FEAT-GOV-08

**Governance requests.** Request a workspace, workspace access, a domain or a policy exception; approving creates it in Fabric (a domain request creates the domain), with notifications. Preview.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Platform > Governance > Requests (Preview)
- **Evidence:** test: sidecar/tests/test_requests_core_four.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs)); test: app/tests/browser/requests-core-four.test.cjs (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/central/requests_service.py`, `sidecar/fabric_desktop/central/governance_service.py`, `app/ui/governance.js`
- **Tests:** `sidecar/tests/test_requests_core_four.py`, `app/tests/browser/requests-core-four.test.cjs`
- **Docs:** [docs/governance.md](../governance.md), [docs/governance-guide.md](../governance-guide.md), [docs/platform.md](../platform.md)
- **Known limitations:** Fabric API faked in tests; not run on a tenant.
- **Pull requests and issues:** [#605](https://github.com/KratosDataGod/KratosForge/pull/605), [#642](https://github.com/KratosDataGod/KratosForge/pull/642)
- **Owning workstream:** Enterprise governance foundation
- **Recommended next action:** Governance steps 8 and 12 wait on Chris's two read-only PC checks.

### FEAT-META-19

**Semantic models to and from Apache Ossie.** Exports a semantic model (TMSL from Fabric getDefinition) to an Apache Ossie YAML file and previews or imports one back, with Microsoft's vendored Apache-2.0 converter. Engine only.

- **Pillar and category:** Enterprise services; Fabric Integration and Deployment
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None yet: Deploy > Semantic model card waits on the mockup (Preview)
- **Evidence:** test: sidecar/tests/test_model_ossie.py (CI run 2235 on main at 524e6a3 passed (engine, UI, browser and Rust jobs))
- **Source:** `sidecar/fabric_desktop/model_ossie.py`, `sidecar/fabric_desktop/model_ossie_service.py`, `sidecar/fabric_desktop/_vendor/ossie_microsoft`
- **Tests:** `sidecar/tests/test_model_ossie.py`
- **Docs:** [docs/semantic-model-ossie.md](../semantic-model-ossie.md)
- **Known limitations:** Ossie spec 0.2 is a draft and still changes. TMDL only through Fabric's TMSL bridge.
- **Pull requests and issues:** [#599](https://github.com/KratosDataGod/KratosForge/pull/599)
- **Owning workstream:** TMDL and OSI semantic model conversion
- **Recommended next action:** UI card after Chris reacts to the mockup and decides whether Import may create new models.
<!-- END GENERATED: details -->

## Keeping this inventory current

Follow [the product management process](PRODUCT_MANAGEMENT_PROCESS.md): after each merged PR the
implementation review updates the affected rows, adds evidence, and runs
`python docs/product/tools/product_data.py all`, which must end with "Product data is consistent."
Re-take the whole inventory at the start of each development cycle and record the new commit in
`assessed`.
