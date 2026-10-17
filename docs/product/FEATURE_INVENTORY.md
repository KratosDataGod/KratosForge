# Feature inventory

This is the registry of what Fabric Desktop by KratosBI actually does, how sure we are, and what
should happen next for each capability. It is the starting point for every product decision in
[the product backlog](PRODUCT_BACKLOG.md) and [the prioritized roadmap](PRIORITIZED_ROADMAP.md).

- **Source of truth:** [FEATURE_INVENTORY.json](FEATURE_INVENTORY.json), validated by
  [schema/feature-inventory.schema.json](schema/feature-inventory.schema.json).
- **This page:** the tables between `BEGIN GENERATED` and `END GENERATED` markers are written by
  `python docs/product/tools/product_data.py render`. Edit the JSON, never those tables.
- **Assessed against:** main at commit `b7b04cc` on 2026-10-17. PR #94 (Ready to work checklist)
  merged after that commit; it is listed as Planned here and waits for the implementation review.

## How this inventory was taken

| What was done | Result |
| --- | --- |
| Read the engine (`sidecar/fabric_desktop`), the UI (`app/ui`), the shell (`app/src-tauri`) and the workflows | Every feature row names the files that implement it |
| Ran the engine test suite with `pytest` on Linux, Python 3.13 | 656 passed, 2 skipped (TPC-H extension, one POSIX-only test) |
| Ran the UI tests with `node --test app/tests/ui.test.cjs` | 23 passed |
| Tried `cargo test` in `app/src-tauri` | Could not build on Linux (`gdk-3.0` missing). CI never runs `cargo test` either, so the Rust tests in `watchdog.rs` and `main.rs` are unverified |
| Drove the engine over its stdin protocol and the `fabric-desktop` CLI with local data only | Settings, notebooks, cell runs, Spark shim, jobs preview, metadata init/run/tests/promote check, CLI help, agent check and support bundle behaved as described in each row's evidence |
| Compared each page in `docs/` with the code | Mismatches are listed as limitations and collected in backlog items PB-010 and PB-077 |

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
| Implemented | 2 | 67 | 4 | 73 |
| Partially Implemented | 1 | 9 | 1 | 11 |
| Implemented but Not Exposed | 0 | 4 | 0 | 4 |
| Implemented but Unreliable | 0 | 1 | 0 | 1 |
| Missing | 0 | 0 | 8 | 8 |
| Planned | 0 | 0 | 11 | 11 |
| **Total** | **3** | **81** | **24** | **108** |
<!-- END GENERATED: summary -->

## What the inventory shows

1. **Breadth is real, live proof is not.** Most features are Implemented and backed by passing
   tests, but those tests fake Fabric. The live test harness (FEAT-OPS-04) has never run, so every
   Fabric-facing row stays Partially Verified until the tenant smoke test (PB-002).
2. **Four capabilities are built but unreachable:** the TOML job runner, column lineage, reliability
   objectives and agent status records. A text search of `app/ui` at `b7b04cc` found no caller for
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
| [FEAT-NB-04](#feat-nb-04) | Spark DataFrame API on this PC | Partially Implemented | Partially Verified | Notebook cells | Publish a supported-API matrix generated from tests (PB-012); fix insertInto default mode. |
| [FEAT-NB-05](#feat-nb-05) | notebookutils and mssparkutils on this PC | Partially Implemented | Partially Verified | Notebook cells | Add session, lakehouse and connections stubs that explain themselves; stop compute_check flagging variableLibrary as Fabric-only (PB-013). |
| [FEAT-NB-07](#feat-nb-07) | Where a notebook can run (compute check) | Implemented but Unreliable | Partially Verified | Notebook ribbon > Runs on | Fix the variableLibrary false positive and move to the AST analyzer planned as FD-111 (PB-013, PB-014). |
| [FEAT-PRV-04](#feat-prv-04) | Environment profile | Partially Implemented | Partially Verified | Prove > Packages only (Preview) | Reproducible environments wait on the package-source decision (FD-109). |
| [FEAT-META-02](#feat-meta-02) | Metadata runs on this PC | Implemented | Partially Verified | Data mesh > Entities > Run | Keep; add late-arriving rows and source deletes (FD-117). |
| [FEAT-META-14](#feat-meta-14) | Environments: Tables in and Runs on | Implemented | Partially Verified | Data mesh > Deploy > Environments | Verify the runner notebook on a tenant (PB-002). |

### SQL, Python, and Notebook Development

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-11](#feat-fab-11) | Fabric Warehouse and SQL database development | Planned | Unverified | None | Owned by the SQL development sprint: SQL-004 native connectivity, SQL-005 object explorer, SQL-006 local simulation. |
| [FEAT-NB-01](#feat-nb-01) | Notebooks on this PC | Implemented | Partially Verified | Create > Notebook, notebook page | Add UI tests for the editor; outputs are never saved, say so in docs. |
| [FEAT-NB-06](#feat-nb-06) | Run other notebooks and magics | Implemented | Partially Verified | Notebook cells | Handle !pip and %%time explicitly. |
| [FEAT-SQL-01](#feat-sql-01) | SQL query page | Implemented | Partially Verified | Workspaces > lakehouse > SQL query | Owned by the SQL development sprint: SQL-001 editor, SQL-002 dialect analyzer, SQL-003 managed execution. |
| [FEAT-SQL-02](#feat-sql-02) | %%sql cells | Partially Implemented | Partially Verified | Notebook cells | Make a MERGE/UPDATE/DELETE cell end with a visible 'skipped on this PC' status instead of ok (PB-013). |
| [FEAT-EXT-04](#feat-ext-04) | Debugger for notebooks and pipelines | Missing | Unverified | None | Prove program Stage 5 after the process kernel is default. |

### Data Engineering and Transformation

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-05](#feat-one-05) | Fabric-safe Delta writes to OneLake | Implemented | Partially Verified | Every write path | Keep; verify MERGE on OneLake in the live test. |
| [FEAT-NB-11](#feat-nb-11) | TOML job runner | Implemented but Not Exposed | Partially Verified | CLI only (fabric-desktop preview\|run) | Decide: expose jobs in the app or mark them a CLI/agent feature and document so (PB-079). |
| [FEAT-META-01](#feat-meta-01) | Metadata project, layers and entities | Implemented | Partially Verified | Data mesh > Design | Keep. |
| [FEAT-META-04](#feat-meta-04) | Custom code: hooks, steps and Python checks | Implemented | Partially Verified | Entity TOML | Keep. |

### Pipelines and Orchestration

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-08](#feat-fab-08) | Fabric schedules | Implemented | Partially Verified | Notebook ribbon, Monitor > Schedules (Preview) | Allow editing a schedule's time; use the quiet token path in all_schedules. |
| [FEAT-FAB-10](#feat-fab-10) | Data pipelines, Dataflows and Copy jobs authoring | Missing | Unverified | None (listed, opened in Fabric, run-now from Monitor > Schedules) | Decide scope with decision D2 (who creates landing Copy jobs) before any build (PB-030). |
| [FEAT-MESH-03](#feat-mesh-03) | Landing zone and source registry | Partially Implemented | Partially Verified | Platform > Landing zone (Preview) | Guided ingestion after decision D2 (FD-116). |
| [FEAT-ORC-01](#feat-orc-01) | PC schedules | Partially Implemented | Partially Verified | Notebook ribbon, Data mesh, Monitor > Schedules (Preview) | Run frozen-cell rules the same as the UI; agent schedules cover Linux. |
| [FEAT-ORC-02](#feat-orc-02) | Headless agent | Implemented | Partially Verified | CLI and agent.toml (no UI) | Verify on a tenant; add queue pruning and requeue of dead jobs (PB-044). |
| [FEAT-ORC-04](#feat-orc-04) | Fabric pipeline mailbox | Implemented | Partially Verified | Fabric pipeline notebook + agent (Preview) | Run once on a tenant; record the writer's identity. |
| [FEAT-ORC-06](#feat-orc-06) | Multiple workers | Missing | Unverified | None | Workloads program (FD-124, roadmap FD-12). |

### Fabric Integration and Deployment

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-FAB-01](#feat-fab-01) | Fabric REST client | Implemented | Partially Verified | Used by most pages | Run the live test harness on a non-production workspace (PB-002). |
| [FEAT-FAB-02](#feat-fab-02) | Workspaces page and item listing | Implemented | Partially Verified | Workspaces | Keep; offer actions beyond Open for non-lakehouse items when their workstreams add them. |
| [FEAT-FAB-03](#feat-fab-03) | Create lakehouses | Implemented | Partially Verified | Create, Home, workspace page, command bar | Keep. |
| [FEAT-FAB-05](#feat-fab-05) | Embedded Fabric portal and Monitoring hub | Implemented | Unverified | Fabric, Monitor | Restrict in-view navigation to Fabric hosts and add cargo test to CI (PB-071). |
| [FEAT-FAB-06](#feat-fab-06) | Publish notebooks to Fabric | Implemented | Partially Verified | Notebook ribbon > Publish to OneLake | Match the Fabric item by its recorded id, not display name, so a rename doesn't duplicate (PB-018). |
| [FEAT-FAB-07](#feat-fab-07) | Run in Fabric | Implemented | Partially Verified | Notebook ribbon (Runs on: Fabric) (Preview) | Offer Cancel in Monitor > Runs and the tray (PB-043) and include in the tenant smoke test. |
| [FEAT-FAB-09](#feat-fab-09) | Fabric Environment items and Spark settings | Missing | Unverified | None | Plan with the Prove program's environment profiles (FD-109) once the package-source decision is made (PB-016). |
| [FEAT-FAB-12](#feat-fab-12) | Fabric workspace Git integration | Planned | Unverified | None | Owned by the Git-native program (Phase 2). |
| [FEAT-FAB-13](#feat-fab-13) | Deployment pipelines and item-definition deploy | Planned | Unverified | None | Owned by the Git-native program (Phase 3, FD-120); reuse metadata promote gates. |
| [FEAT-ONE-01](#feat-one-01) | OneLake catalog | Implemented | Partially Verified | OneLake > Catalog | Unify environment-word lists between Python and JS (catalog-groups.js vs shortcut_center.py). |
| [FEAT-META-08](#feat-meta-08) | Compare, meaningful diffs and promote impact | Implemented | Partially Verified | Entity > Compare, Deploy > promote impact | Batch the compare for large tables. |
| [FEAT-META-09](#feat-meta-09) | Promote Dev to Test to Prod | Implemented | Partially Verified | Data mesh > Deploy > Environments | Release rollback (scale plan 5.3) and decision D1 (require a pushed commit for Test/Prod). |
| [FEAT-META-10](#feat-meta-10) | Release records | Implemented | Partially Verified | Promote card notes | Merge PR #113 (release records read Git through the Git layer); add a release list view. |
| [FEAT-META-13](#feat-meta-13) | Direct Lake semantic model | Implemented | Partially Verified | Data mesh > Deploy > Semantic model (Preview) | Include in the tenant smoke test. |

### Enterprise Shared Services

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-03](#feat-one-03) | OneLake shortcut command center | Implemented | Partially Verified | OneLake > Shortcuts (Preview) | Scheduled health check with a Teams alert (documented as not built). |
| [FEAT-META-05](#feat-meta-05) | Surrogate keys | Implemented | Partially Verified | Data mesh > Surrogate keys | Stop reading whole key tables on every keyed run (FD-115). |
| [FEAT-META-15](#feat-meta-15) | Variable library and source systems | Implemented | Partially Verified | Data mesh > Source systems | Keep. |
| [FEAT-MESH-01](#feat-mesh-01) | Central platform | Implemented | Partially Verified | Platform (Preview) | Verify setup, the UDF executing_user and T-SQL on a tenant (platform.md lists these unverified). |
| [FEAT-MESH-02](#feat-mesh-02) | Central business keys | Implemented | Partially Verified | Data mesh > Surrogate keys > Move keys, Platform > Business keys (Preview) | Run on Fabric SQL database before anyone moves real keys. |
| [FEAT-MESH-04](#feat-mesh-04) | Data mesh: domains, products, links, glossary | Implemented | Partially Verified | Data mesh > Domains, OneLake > Data products, Home (Preview) | Try on a tenant (data-mesh.md: not yet). |

### Data Governance and Quality

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-ONE-02](#feat-one-02) | Endorsed items scope | Missing | Unverified | OneLake > Catalog (greyed out) | Fix the reason text and troubleshooting.md, which say Fabric's catalog is embedded on the same page; it isn't. |
| [FEAT-ONE-04](#feat-one-04) | OneLake access review and data access roles | Implemented | Partially Verified | OneLake > Access (Preview) | Include in the tenant smoke test. |
| [FEAT-PRV-02](#feat-prv-02) | Output verification rules | Implemented | Partially Verified | Prove panel (Preview) | Reuse for SQL and metadata compare (one comparison engine). |
| [FEAT-META-03](#feat-meta-03) | Checks, quarantine and replay | Implemented | Partially Verified | Entity page > Quarantine | Keep. |
| [FEAT-META-07](#feat-meta-07) | Schema drift review | Implemented | Partially Verified | Entity page > Drift | Cover landing and link sources (inference: not read). |
| [FEAT-META-16](#feat-meta-16) | Profiling and rule suggestions | Implemented | Partially Verified | Entity > Profile | Keep. |
| [FEAT-META-17](#feat-meta-17) | Column lineage | Implemented but Not Exposed | Partially Verified | None (entity-level Reads/Feeds card only) | Build the lineage view after a mockup (scale plan 8.x). |
| [FEAT-GOV-01](#feat-gov-01) | Freshness promises and Teams alerts | Implemented | Partially Verified | Entity > Freshness, Home | Merge with incidents so a late table posts once (agent.md notes duplicates). |
| [FEAT-GOV-02](#feat-gov-02) | Project tests gate promote | Implemented | Partially Verified | Data mesh > Quality > Tests (Preview) | Fold into the Git program's validation framework (FD-113). |
| [FEAT-GOV-03](#feat-gov-03) | Governance exceptions | Partially Implemented | Partially Verified | Project file only | Request and approval flow with history (FD-129), built by the governance foundation thread on its policy registry. |
| [FEAT-GOV-05](#feat-gov-05) | Enterprise governance policy registry and offline validation | Planned | Unverified | None | Owned by the Enterprise governance foundation thread (GOV-001 registry first). |
| [FEAT-GOV-06](#feat-gov-06) | Sensitive data classification | Missing | Unverified | None | Owned by the governance foundation thread; complement Purview, don't duplicate it. |
| [FEAT-GOV-07](#feat-gov-07) | Reliability objectives | Implemented but Not Exposed | Partially Verified | None (agent incidents only) | Show in the workload page with incidents (PB-047). |
| [FEAT-MESH-05](#feat-mesh-05) | Breaking-change check for data products | Implemented | Partially Verified | Promote card | Consumer declarations and deprecation (scale plan 8.2). |

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
| [FEAT-ADM-04](#feat-adm-04) | Ready to work checklist | Planned | Unverified | None | PR #94 merged after this assessment's commit (main 4079a41); re-inventory and run the implementation review (PB-086). |
| [FEAT-REL-02](#feat-rel-02) | Documentation site | Implemented | Unverified | GitHub Pages | Keep; add docs/product to the nav (this PR). |
| [FEAT-GIT-01](#feat-git-01) | Source control | Implemented | Partially Verified | Source control (bottom of the rail) (Preview) | Owned by the Git-native program. |

### Enterprise Administration and FinOps

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-08](#feat-shl-08) | Settings and the organization policy file | Implemented | Partially Verified | Settings (locked values show 'Set by your organization') | Add the documented-but-missing keys (allowed_hosts, local_retention_days, telemetry, three-value allowed_compute) through the enterprise program (PB-061). |
| [FEAT-SHL-10](#feat-shl-10) | Report a problem with redaction | Implemented | Partially Verified | Account menu, Settings > Help, command bar | Fix the user guide, which says it adds the tenant id (it redacts it). |
| [FEAT-SHL-11](#feat-shl-11) | Support bundle | Partially Implemented | Verified | CLI only (fabric-desktop support-bundle) | Add 'Save support bundle' with a contents preview to Settings, as FD-138's acceptance asks (PB-062). |
| [FEAT-PRV-05](#feat-prv-05) | Scorecard and run measurement | Implemented | Partially Verified | Prove > Full scorecard (Preview) | Measure the kernel child process when it is on (FD-119). |
| [FEAT-META-18](#feat-meta-18) | Estate X-ray and adopt | Implemented | Partially Verified | Home > Estate X-ray (Preview) | Keep. |
| [FEAT-ORC-05](#feat-orc-05) | Agent status records | Implemented but Not Exposed | Partially Verified | None (read only by incident evaluation) | Agent dashboard (FD-122); fix agent.md, which says workload views read these. |
| [FEAT-OPS-01](#feat-ops-01) | Workload inventory and workload page | Implemented | Partially Verified | Monitor > Workloads, Home > Your workloads (Preview) | Shared ownership (stage 2B); an 'on agents' view. |
| [FEAT-OPS-02](#feat-ops-02) | Incidents | Partially Implemented | Partially Verified | Agent only; no incident screens in the app | Incident screens after a mockup (stage 2.3B) (PB-047). |
| [FEAT-OPS-03](#feat-ops-03) | Monitor: runs from every place | Implemented | Partially Verified | Monitor > Runs | Show worker, host and job for agent runs. |
| [FEAT-ADM-01](#feat-adm-01) | Cost estimates | Implemented | Partially Verified | Monitor > Cost (Preview) | Calibrate against the Capacity Metrics app and label every number estimated (PB-050). |
| [FEAT-ADM-06](#feat-adm-06) | Opt-in telemetry | Missing | Unverified | None | Waiting on decision D-C1 (recommendation: none until a pilot asks). |

### Extensibility, APIs, CLI, and MCP

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-EXT-01](#feat-ext-01) | Command-line interface | Implemented | Verified | Terminal | Add backup/restore (PR #91) and an mcp command (PR-AI-002). |
| [FEAT-EXT-02](#feat-ext-02) | MCP server and local API for external AI tools | Planned | Unverified | None | Merge the AI integration stack in order; run docs/product/ai-integration-client-checklist.md per client. |
| [FEAT-EXT-03](#feat-ext-03) | Extensions | Planned | Unverified | None | Owned by the Git-native program (FD-132..135). |

### Security and Enterprise Readiness

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-02](#feat-shl-02) | RPC method allowlist and error redaction | Implemented | Verified | Engine boundary (no UI) | Keep; generate METHODS from the capability registry once PR-AI-003 merges so the two lists can't drift. |
| [FEAT-SHL-07](#feat-shl-07) | Accessibility basics | Implemented | Partially Verified | Whole app | Run an assistive-technology audit on Windows (Narrator) and record results (FD-139 follow-up). |
| [FEAT-SHL-09](#feat-shl-09) | Self-update, channels and engine install | Implemented | Partially Verified | Startup check, Settings > Updates | Code signing (PB-001) and per-build release assets for real rollback (PB-064). |
| [FEAT-AUTH-01](#feat-auth-01) | Sign-in with no app registration | Implemented | Partially Verified | Sign-in callout, account menu | Include in the tenant smoke test (PB-002). |
| [FEAT-AUTH-02](#feat-auth-02) | Own app registration and tenant allow-list | Implemented | Partially Verified | Settings > Account | Keep. |
| [FEAT-AUTH-03](#feat-auth-03) | Token cache | Partially Implemented | Partially Verified | None (background) | Encrypt with DPAPI via msal-extensions once Chris decides D-C2 (PB-005). |
| [FEAT-AUTH-04](#feat-auth-04) | Unattended credentials for the agent | Implemented | Partially Verified | agent.toml (no UI) | Verify a service principal on a tenant with the live test's --agent-config (PB-002). |
| [FEAT-PRV-03](#feat-prv-03) | Run receipts | Implemented | Partially Verified | Prove > Receipt (Preview) | Feed receipts into the audit trail (FD-125, PR #89). |
| [FEAT-GOV-04](#feat-gov-04) | Who can promote and separation of duties | Implemented | Partially Verified | Promote card | Keep; compare signed-in identities rather than PC login names. |
| [FEAT-ADM-02](#feat-adm-02) | Audit trail | Planned | Unverified | None | PR #89 merged after this assessment's commit; run the implementation review and re-inventory (FD-125). |
| [FEAT-ADM-05](#feat-adm-05) | Local data retention | Planned | Unverified | None | PR #95 merged after this assessment's commit; run the implementation review and re-inventory (FD-131b). |
| [FEAT-SEC-01](#feat-sec-01) | Code signing | Planned | Unverified | None | Chris creates the Azure Trusted Signing account (D-C3) (PB-001). |
| [FEAT-SEC-02](#feat-sec-02) | Content Security Policy | Missing | Unverified | None | FD-102b: move notebook HTML output to another origin, then set a CSP (PB-006). |
| [FEAT-SEC-03](#feat-sec-03) | Supply chain: pinned packages, audit, SBOM, provenance | Implemented | Unverified | Releases | Fix docs/security.md, which still says there is no scanning, SBOM or provenance; pin docs.yml and codex-review.yml actions by SHA. |

### Reliability, Testing, and Performance

| Id | Feature | Status | Verification | Where people reach it | Next action |
| --- | --- | --- | --- | --- | --- |
| [FEAT-SHL-01](#feat-shl-01) | Desktop shell and local engine | Implemented | Partially Verified | Whole app | Run the Windows smoke test of ten core flows on the maintainer's tenant (PB-002). |
| [FEAT-SHL-03](#feat-shl-03) | Watchdog time limits on engine calls | Partially Implemented | Unverified | Every page (implicit) | Run cargo test in CI (PB-071) and move long calls (sign-in, Fabric calls) off the single pipe (PB-072). |
| [FEAT-NB-03](#feat-nb-03) | Notebooks in their own process | Implemented | Partially Verified | Settings > Preview features (off by default) (Preview) | Make it the default after a Windows trial; add per-cell time and memory limits (Prove FD-110). |
| [FEAT-PRV-01](#feat-prv-01) | Prove this notebook | Implemented | Partially Verified | Notebook ribbon > Prove (Preview) | Run one proof on the tenant (PB-002); add the column heatmap from plan Stage 3. |
| [FEAT-PRV-06](#feat-prv-06) | Side by side and the TPC-H benchmark | Implemented | Partially Verified | Notebook ribbon > Side by side (Preview) | Retire in favor of Prove for correctness; keep the benchmark. PR #114 open for its write check time limit. |
| [FEAT-META-06](#feat-meta-06) | Large tables: preflight, batches, merge writes and file manifest | Implemented | Partially Verified | Automatic in runs; batch_by in entity TOML | Build the run dialog (mockup first) and vacuum orphan files after a stopped batch (FD-114). |
| [FEAT-META-11](#feat-meta-11) | Table history and restore | Implemented | Partially Verified | Entity > History (Preview) | Keep; downstream rebuild guidance in the dialog. |
| [FEAT-META-12](#feat-meta-12) | Table health, optimize and retention | Implemented | Partially Verified | Data mesh > Quality > Table health (Preview) | Keep. |
| [FEAT-ORC-03](#feat-orc-03) | OneLake write leases | Implemented | Partially Verified | None (agent) | Prove OneLake honors If-None-Match/If-Match in the live test (PB-003). |
| [FEAT-OPS-04](#feat-ops-04) | Live test harness | Implemented | Partially Verified | CLI only | Chris runs it on a non-production workspace (PB-002); extend to leases and MERGE (PB-003). |
| [FEAT-ADM-03](#feat-adm-03) | Backup and restore of local state | Planned | Unverified | None | PR #91 merged after this assessment's commit; run the implementation review and re-inventory (FD-126). |
| [FEAT-REL-01](#feat-rel-01) | Continuous release channels | Implemented | Unverified | Settings > Updates | Per-build release assets for pinning and rollback (PB-064). |
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
- **Pull requests and issues:** [#83](https://github.com/KratosDataGod/fabric-desktop/pull/83)
- **Owning workstream:** Prove (FD-110)
- **Backlog:** [PB-071](PRODUCT_BACKLOG.md#pb-071), [PB-072](PRODUCT_BACKLOG.md#pb-072)
- **Recommended next action:** Run cargo test in CI (PB-071) and move long calls (sign-in, Fabric calls) off the single pipe (PB-072).

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
- **Backlog:** [PB-063](PRODUCT_BACKLOG.md#pb-063), [PB-070](PRODUCT_BACKLOG.md#pb-070)
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
- **Backlog:** [PB-047](PRODUCT_BACKLOG.md#pb-047)
- **Recommended next action:** Feed incidents and agent status into the bell once incident screens exist (PB-047).

### FEAT-SHL-07

**Accessibility basics.** Skip link, labelled icon buttons, modal focus trap, focus-visible and reduced-motion styles.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Whole app. Accessibility: Static checks only; no screen-reader or contrast audit recorded
- **Evidence:** test: app/tests/ui.test.cjs every icon-only button has a name (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs the page has a skip link and labelled controls; test: app/tests/ui.test.cjs dialogs are modal for keyboard users and focus shows
- **Source:** `app/ui/index.html`, `app/ui/app.js`, `app/ui/styles.css`
- **Tests:** `app/tests/ui.test.cjs`
- **Pull requests and issues:** [#92](https://github.com/KratosDataGod/fabric-desktop/pull/92)
- **Owning workstream:** Enterprise readiness (FD-139)
- **Backlog:** [PB-063](PRODUCT_BACKLOG.md#pb-063)
- **Recommended next action:** Run an assistive-technology audit on Windows (Narrator) and record results (FD-139 follow-up).

### FEAT-SHL-08

**Settings and the organization policy file.** Settings resolve policy > environment > user > default; policy.json can lock client id, channel, preview, kernel process, support URL, platform, auto update, Fabric runs and allowed tenants.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Settings (locked values show 'Set by your organization')
- **Evidence:** test: sidecar/tests/test_policy.py::test_policy_problems_are_reported_not_applied (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_policy.py::test_fabric_runs_can_be_turned_off; test: sidecar/tests/test_app_settings.py::test_policy_locks_a_setting; smoke: app_settings RPC returned each key with its source
- **Source:** `sidecar/fabric_desktop/app_settings.py`, `app/ui/settings.js`
- **Tests:** `sidecar/tests/test_policy.py`, `sidecar/tests/test_app_settings.py`
- **Docs:** [docs/azure-setup.md](../azure-setup.md), [docs/enterprise-install.md](../enterprise-install.md)
- **Known limitations:** The headless agent doesn't read the policy file. `platform` key not in azure-setup.md's key table.
- **Pull requests and issues:** [#90](https://github.com/KratosDataGod/fabric-desktop/pull/90)
- **Owning workstream:** Enterprise readiness (FD-105)
- **Backlog:** [PB-061](PRODUCT_BACKLOG.md#pb-061)
- **Recommended next action:** Add the documented-but-missing keys (allowed_hosts, local_retention_days, telemetry, three-value allowed_compute) through the enterprise program (PB-061).

### FEAT-SHL-09

**Self-update, channels and engine install.** Stable and Preview channels from GitHub Releases; the exe and engine zip are checked against version.json SHA-256 and their build commit before use.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Startup check, Settings > Updates
- **Evidence:** test: sidecar/tests/test_updater.py::test_download_app_checks_the_checksum (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_updater.py::test_download_app_refuses_when_the_checksum_cant_be_read; test: sidecar/tests/test_updater.py::test_no_stable_release_yet_follows_preview; absent: main.rs integrity_tests not run in CI or here
- **Source:** `app/src-tauri/src/updater.py`, `app/src-tauri/src/main.rs`
- **Tests:** `sidecar/tests/test_updater.py`
- **Docs:** [docs/install.md](../install.md), [docs/enterprise-install.md](../enterprise-install.md), [docs/security.md](../security.md)
- **Known limitations:** Checksums come from the same release, so they don't stop someone who can publish releases. Only the newest build per channel exists, so no pinning or rollback. A release without engine_sha256 is still accepted (main.rs check_engine_zip). Updating from Program Files fails.
- **Pull requests and issues:** [#51](https://github.com/KratosDataGod/fabric-desktop/pull/51), [#82](https://github.com/KratosDataGod/fabric-desktop/pull/82)
- **Owning workstream:** Enterprise readiness (FD-102, FD-104)
- **Backlog:** [PB-001](PRODUCT_BACKLOG.md#pb-001), [PB-064](PRODUCT_BACKLOG.md#pb-064), [PB-071](PRODUCT_BACKLOG.md#pb-071), [PB-100](PRODUCT_BACKLOG.md#pb-100)
- **Recommended next action:** Code signing (PB-001) and per-build release assets for real rollback (PB-064).

### FEAT-SHL-10

**Report a problem with redaction.** Copies build, Python, page and recent errors with secrets, emails, user paths and GUIDs removed, to the policy's support URL or a GitHub issue.

- **Pillar and category:** Platform foundation; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Account menu, Settings > Help, command bar
- **Evidence:** test: sidecar/tests/test_redact.py::test_shared_cases (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: app/tests/ui.test.cjs Report a problem takes out secrets
- **Source:** `app/ui/settings.js`, `sidecar/fabric_desktop/redact.py`, `app/tests/redact-cases.json`
- **Tests:** `sidecar/tests/test_redact.py`, `app/tests/ui.test.cjs`
- **Docs:** [docs/user-guide.md](../user-guide.md)
- **Pull requests and issues:** [#84](https://github.com/KratosDataGod/fabric-desktop/pull/84)
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
- **Pull requests and issues:** [#42](https://github.com/KratosDataGod/fabric-desktop/pull/42), [#111](https://github.com/KratosDataGod/fabric-desktop/pull/111), issue [#96](https://github.com/KratosDataGod/fabric-desktop/issues/96)
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

**Token cache.** MSAL cache in the user profile, written atomically; a damaged cache is ignored.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None (background)
- **Evidence:** test: sidecar/tests/test_local.py::test_token_cache_survives_damage_and_is_swapped_in_whole (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/auth.py`
- **Tests:** `sidecar/tests/test_local.py`
- **Docs:** [docs/security.md](../security.md)
- **Known limitations:** Plain file on Windows (not encrypted). No lock between the app and scheduled runs (inference).
- **Owning workstream:** Enterprise readiness (FD-131b)
- **Backlog:** [PB-005](PRODUCT_BACKLOG.md#pb-005)
- **Recommended next action:** Encrypt with DPAPI via msal-extensions once Chris decides D-C2 (PB-005).

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
- **Pull requests and issues:** [#34](https://github.com/KratosDataGod/fabric-desktop/pull/34)
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
- **Pull requests and issues:** [#109](https://github.com/KratosDataGod/fabric-desktop/pull/109)
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
- **Pull requests and issues:** [#43](https://github.com/KratosDataGod/fabric-desktop/pull/43)
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
- **Known limitations:** Items matched by display name (fabric_api.py save_notebook). The setup cell pip-installs sqlglot in Fabric. notebooks.md says %%sql notebooks publish as Spark; code converts them (inference).
- **Pull requests and issues:** [#31](https://github.com/KratosDataGod/fabric-desktop/pull/31)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-018](PRODUCT_BACKLOG.md#pb-018), [PB-077](PRODUCT_BACKLOG.md#pb-077)
- **Recommended next action:** Match the Fabric item by its recorded id, not display name, so a rename doesn't duplicate (PB-018).

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
- **Owning workstream:** Staged roadmap (stage 4)
- **Backlog:** [PB-030](PRODUCT_BACKLOG.md#pb-030), [PB-035](PRODUCT_BACKLOG.md#pb-035)
- **Recommended next action:** Decide scope with decision D2 (who creates landing Copy jobs) before any build (PB-030).

### FEAT-FAB-11

**Fabric Warehouse and SQL database development.** Connect to a Warehouse, SQL analytics endpoint or Fabric SQL database and run T-SQL, browse objects, check dialect compatibility.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no TDS/ODBC client in sidecar/constraints.txt; doc: SQL development sprint thread (SQL-001..006), 2026-10-17
- **Owning workstream:** SQL development sprint
- **Backlog:** [PB-020](PRODUCT_BACKLOG.md#pb-020), [PB-022](PRODUCT_BACKLOG.md#pb-022), [PB-024](PRODUCT_BACKLOG.md#pb-024), [PB-025](PRODUCT_BACKLOG.md#pb-025)
- **Recommended next action:** Owned by the SQL development sprint: SQL-004 native connectivity, SQL-005 object explorer, SQL-006 local simulation.

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
- **Pull requests and issues:** [#43](https://github.com/KratosDataGod/fabric-desktop/pull/43)
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
- **Recommended next action:** Fix the reason text and troubleshooting.md, which say Fabric's catalog is embedded on the same page; it isn't.

### FEAT-ONE-03

**OneLake shortcut command center.** Every shortcut with health checks, readers, rules (no Dev to Prod), change history with undo, repoint and requests.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** OneLake > Shortcuts (Preview)
- **Evidence:** test: sidecar/tests/test_shortcut_center.py::test_repoint_puts_the_old_shortcut_back_when_the_new_one_fails (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_shortcut_center.py::test_rules_stop_dev_pointing_at_prod_and_flag_it
- **Source:** `sidecar/fabric_desktop/shortcut_service.py`, `sidecar/fabric_desktop/shortcut_center.py`, `app/ui/shortcuts.js`
- **Tests:** `sidecar/tests/test_shortcut_center.py`
- **Docs:** [docs/shortcuts.md](../shortcuts.md)
- **Known limitations:** No scheduled check or alert. Approved request creates the shortcut in one lakehouse only. OneLake error codes unseen on a tenant.
- **Pull requests and issues:** [#53](https://github.com/KratosDataGod/fabric-desktop/pull/53)
- **Backlog:** [PB-053](PRODUCT_BACKLOG.md#pb-053)
- **Recommended next action:** Scheduled health check with a Teams alert (documented as not built).

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

**Fabric-safe Delta writes to OneLake.** Every write is staged locally, checked against the Fabric Delta profile (reader 1, writer 2, no deletion vectors or column mapping) and committed atomically with a version check.

- **Pillar and category:** Local development; Data Engineering and Transformation
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Every write path
- **Evidence:** test: sidecar/tests/test_meta.py::test_00b_tables_follow_the_fabric_delta_profile (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_local.py::test_profile_rejects_v2_checkpoints; test: sidecar/tests/test_agent.py::test_an_overwrite_from_a_stale_read_is_refused; doc: docs/README.md (writing tables to OneLake used on the maintainer's tenant)
- **Source:** `sidecar/fabric_desktop/publish.py`, `sidecar/fabric_desktop/delta_profile.py`, `sidecar/fabric_desktop/meta/store.py`
- **Tests:** `sidecar/tests/test_meta.py`, `sidecar/tests/test_local.py`
- **Docs:** [docs/architecture.md](../architecture.md)
- **Known limitations:** Writes Delta features Fabric reads; newer Delta features are refused rather than written.
- **Pull requests and issues:** [#51](https://github.com/KratosDataGod/fabric-desktop/pull/51), [#54](https://github.com/KratosDataGod/fabric-desktop/pull/54)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002), [PB-003](PRODUCT_BACKLOG.md#pb-003), [PB-099](PRODUCT_BACKLOG.md#pb-099)
- **Recommended next action:** Keep; verify MERGE on OneLake in the live test.

### FEAT-NB-01

**Notebooks on this PC.** Create, open, save (ipynb, stable cell ids, other tools' metadata kept), rename and delete notebooks; ribbon, command mode, parameters, freeze, outline, find and replace.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Create > Notebook, notebook page
- **Evidence:** test: sidecar/tests/test_notebook.py::test_ipynb_round_trip_keeps_cells_and_lakehouse (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_saving_keeps_metadata_other_tools_wrote; smoke: new_notebook and list_notebooks over the engine pipe
- **Source:** `sidecar/fabric_desktop/notebook.py`, `app/ui/notebook.js`
- **Tests:** `sidecar/tests/test_notebook.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Outputs are not saved with the notebook. No notebook-editor UI tests.
- **Pull requests and issues:** [#30](https://github.com/KratosDataGod/fabric-desktop/pull/30)
- **Backlog:** [PB-070](PRODUCT_BACKLOG.md#pb-070)
- **Recommended next action:** Add UI tests for the editor; outputs are never saved, say so in docs.

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
- **Evidence:** test: sidecar/tests/test_kernel_process.py::test_cancel_keeps_variables_and_stop_ends_only_the_process (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_kernel_process.py::test_a_crash_ends_the_cell_with_a_reason_and_the_next_cell_gets_a_fresh_kernel
- **Source:** `sidecar/fabric_desktop/kernel_proc.py`, `sidecar/fabric_desktop/kernel_host.py`
- **Tests:** `sidecar/tests/test_kernel_process.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Off by default. No memory or CPU limits.
- **Pull requests and issues:** [#67](https://github.com/KratosDataGod/fabric-desktop/pull/67)
- **Owning workstream:** Prove (FD-110)
- **Backlog:** [PB-073](PRODUCT_BACKLOG.md#pb-073)
- **Recommended next action:** Make it the default after a Windows trial; add per-cell time and memory limits (Prove FD-110).

### FEAT-NB-04

**Spark DataFrame API on this PC.** `spark` is DuckDB's Spark-compatible API wired to the lakehouse: read table/csv/json/parquet/text/delta, spark.sql with CTAS and INSERT, saveAsTable, createDataFrame.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_spark_reads_and_writes_lakehouse_tables (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook_parity.py::test_pyspark_runs_locally_but_scala_rdds_and_merge_stay_in_fabric; smoke: SparkSession.builder.getOrCreate().createDataFrame(...).filter(...).count() ran and returned 1
- **Source:** `sidecar/fabric_desktop/spark_local.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** MERGE INTO, UPDATE and DELETE raise. partitionBy and reader schema() are ignored. insertInto defaults to errorifexists, unlike Spark's append (inference). No RDD, UDF, streaming; Scala/R refused.
- **Backlog:** [PB-012](PRODUCT_BACKLOG.md#pb-012), [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-015](PRODUCT_BACKLOG.md#pb-015)
- **Recommended next action:** Publish a supported-API matrix generated from tests (PB-012); fix insertInto default mode.

### FEAT-NB-05

**notebookutils and mssparkutils on this PC.** fs (ls, head, put, append, cp, mv, rm, mkdirs, exists), credentials.getToken/getSecret, notebook.run/exit/runMultiple, runtime.context, variableLibrary for VL_Sources.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_notebookutils_fs_runtime_and_exit (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook_parity.py::test_notebookutils_get_token_uses_the_sign_in; smoke: notebookutils was present in a fresh kernel namespace
- **Source:** `sidecar/fabric_desktop/notebookutils_local.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** session, lakehouse, warehouse, udf, connections are absent (plain AttributeError, contrary to the module docstring). mount/unmount raise. runMultiple is sequential. notebook.run timeout ignored.
- **Backlog:** [PB-012](PRODUCT_BACKLOG.md#pb-012), [PB-013](PRODUCT_BACKLOG.md#pb-013)
- **Recommended next action:** Add session, lakehouse and connections stubs that explain themselves; stop compute_check flagging variableLibrary as Fabric-only (PB-013).

### FEAT-NB-06

**Run other notebooks and magics.** %run and notebookutils.notebook.run with parameters; %%sql, %%html, %%configure (no-op), %pip skipped with a note, %time.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook cells
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_run_another_notebook_with_parameters_and_percent_run (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/notebook.py`
- **Tests:** `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** %pip is skipped, so a missing package fails later. !pip and %%time would be syntax errors (inference). %%spark/%%sparkr/%%csharp refused.
- **Backlog:** [PB-028](PRODUCT_BACKLOG.md#pb-028)
- **Recommended next action:** Handle !pip and %%time explicitly.

### FEAT-NB-07

**Where a notebook can run (compute check).** A saved This PC / Fabric choice per notebook and a static check naming the cells that block each side.

- **Pillar and category:** Local development; Local Development and Execution
- **Status:** Implemented but Unreliable. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Runs on
- **Evidence:** test: sidecar/tests/test_notebook.py::test_open_and_save_say_where_the_notebook_can_run (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: sidecar/fabric_desktop/fabric_run.py _FABRIC_ONLY lists notebookutils.variableLibrary, which notebookutils_local implements
- **Source:** `sidecar/fabric_desktop/fabric_run.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_notebook.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Regex-based; flags variableLibrary notebooks as Fabric-only although they run on the PC. No 'unknown' outcome.
- **Pull requests and issues:** [#29](https://github.com/KratosDataGod/fabric-desktop/pull/29)
- **Owning workstream:** Prove (FD-111)
- **Backlog:** [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-014](PRODUCT_BACKLOG.md#pb-014), [PB-017](PRODUCT_BACKLOG.md#pb-017)
- **Recommended next action:** Fix the variableLibrary false positive and move to the AST analyzer planned as FD-111 (PB-013, PB-014).

### FEAT-NB-08

**Autocomplete and variables pane.** Completion from the live kernel plus table names; variables pane hides secret-looking values.

- **Pillar and category:** Local development; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook editor, View > Variables
- **Evidence:** test: sidecar/tests/test_notebook_parity.py::test_variables_hide_secrets_and_completion_lists_attributes (sidecar suite passed in this assessment (656 passed, 2 skipped)); smoke: kernel_variables listed df; kernel_complete returned items only for names in the namespace
- **Source:** `sidecar/fabric_desktop/notebook.py`, `app/ui/notebook.js`
- **Tests:** `sidecar/tests/test_notebook_parity.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Completes only what the kernel has run. No completion when Runs on is Fabric.
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
- **Pull requests and issues:** [#68](https://github.com/KratosDataGod/fabric-desktop/pull/68)
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
- **Pull requests and issues:** [#65](https://github.com/KratosDataGod/fabric-desktop/pull/65)
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
- **Evidence:** test: sidecar/tests/test_notebook.py::test_sql_runs_spark_sql_over_lakehouse_tables (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_notebook.py::test_sql_result_saves_as_a_table_and_exports_csv
- **Source:** `sidecar/fabric_desktop/sql.py`, `sidecar/fabric_desktop/service.py`, `app/ui/app.js`
- **Tests:** `sidecar/tests/test_notebook.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Plain textarea, no autocomplete or formatting. Read-only. Download CSV re-runs the query. No cancel.
- **Owning workstream:** SQL development sprint
- **Backlog:** [PB-021](PRODUCT_BACKLOG.md#pb-021), [PB-022](PRODUCT_BACKLOG.md#pb-022), [PB-023](PRODUCT_BACKLOG.md#pb-023), [PB-099](PRODUCT_BACKLOG.md#pb-099)
- **Recommended next action:** Owned by the SQL development sprint: SQL-001 editor, SQL-002 dialect analyzer, SQL-003 managed execution.

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
- **Pull requests and issues:** [#59](https://github.com/KratosDataGod/fabric-desktop/pull/59), [#62](https://github.com/KratosDataGod/fabric-desktop/pull/62), [#65](https://github.com/KratosDataGod/fabric-desktop/pull/65)
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

**Side by side and the TPC-H benchmark.** Runs a notebook on the PC and in Fabric against the real lakehouse after confirming the tables it overwrites; compares checksums, speed and cost; TPC-H data and query notebooks.

- **Pillar and category:** Local development; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon > Side by side (Preview)
- **Evidence:** test: sidecar/tests/test_side_by_side.py::test_side_by_side_runs_only_with_the_tables_it_overwrites_confirmed (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_side_by_side.py::test_benchmark_notebooks_run_end_to_end
- **Source:** `sidecar/fabric_desktop/side_by_side.py`, `sidecar/fabric_desktop/side_by_side_service.py`, `sidecar/fabric_desktop/benchmark.py`, `app/ui/sidebyside.js`
- **Tests:** `sidecar/tests/test_side_by_side.py`
- **Docs:** [docs/side-by-side.md](../side-by-side.md)
- **Known limitations:** Writes the real tables twice (confirmed first). Files/ not compared.
- **Pull requests and issues:** [#44](https://github.com/KratosDataGod/fabric-desktop/pull/44), [#81](https://github.com/KratosDataGod/fabric-desktop/pull/81), [#114](https://github.com/KratosDataGod/fabric-desktop/pull/114)
- **Owning workstream:** Prove (FD-112)
- **Backlog:** [PB-076](PRODUCT_BACKLOG.md#pb-076)
- **Recommended next action:** Retire in favor of Prove for correctness; keep the benchmark. PR #114 open for its write check time limit.

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
- **Pull requests and issues:** [#56](https://github.com/KratosDataGod/fabric-desktop/pull/56), [#61](https://github.com/KratosDataGod/fabric-desktop/pull/61)
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
- **Pull requests and issues:** [#23](https://github.com/KratosDataGod/fabric-desktop/pull/23), [#52](https://github.com/KratosDataGod/fabric-desktop/pull/52)
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
- **Pull requests and issues:** [#56](https://github.com/KratosDataGod/fabric-desktop/pull/56), [#69](https://github.com/KratosDataGod/fabric-desktop/pull/69), [#70](https://github.com/KratosDataGod/fabric-desktop/pull/70), [#87](https://github.com/KratosDataGod/fabric-desktop/pull/87)
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
- **Pull requests and issues:** [#75](https://github.com/KratosDataGod/fabric-desktop/pull/75)
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
- **Pull requests and issues:** [#73](https://github.com/KratosDataGod/fabric-desktop/pull/73), [#113](https://github.com/KratosDataGod/fabric-desktop/pull/113)
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
- **Pull requests and issues:** [#25](https://github.com/KratosDataGod/fabric-desktop/pull/25), [#32](https://github.com/KratosDataGod/fabric-desktop/pull/32)
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
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None (entity-level Reads/Feeds card only)
- **Evidence:** test: sidecar/tests/test_lineage.py::test_every_column_says_where_it_comes_from (sidecar suite passed in this assessment (656 passed, 2 skipped)); code: sidecar/fabric_desktop/meta_service.py meta_lineage has no caller in app/ui
- **Source:** `sidecar/fabric_desktop/meta/lineage.py`
- **Tests:** `sidecar/tests/test_lineage.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** Python-added columns are opaque.
- **Pull requests and issues:** [#76](https://github.com/KratosDataGod/fabric-desktop/pull/76)
- **Owning workstream:** Staged roadmap (FD-128)
- **Backlog:** [PB-081](PRODUCT_BACKLOG.md#pb-081)
- **Recommended next action:** Build the lineage view after a mockup (scale plan 8.x).

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
- **Pull requests and issues:** [#41](https://github.com/KratosDataGod/fabric-desktop/pull/41)
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

**Governance exceptions.** exceptions.toml with reason, approver and expiry that lets a gate pass until it expires.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Project file only
- **Evidence:** test: sidecar/tests/test_meta.py::test_05e_exceptions_expire (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/meta/project.py`
- **Tests:** `sidecar/tests/test_meta.py`
- **Docs:** [docs/metadata-guide.md](../metadata-guide.md)
- **Known limitations:** No request or approval flow, no history, no expiry view.
- **Owning workstream:** Enterprise governance foundation (FD-129)
- **Backlog:** [PB-059](PRODUCT_BACKLOG.md#pb-059)
- **Recommended next action:** Request and approval flow with history (FD-129), built by the governance foundation thread on its policy registry.

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
- **Pull requests and issues:** [#93](https://github.com/KratosDataGod/fabric-desktop/pull/93)
- **Owning workstream:** Enterprise readiness (FD-130)
- **Backlog:** [PB-065](PRODUCT_BACKLOG.md#pb-065)
- **Recommended next action:** Keep; compare signed-in identities rather than PC login names.

### FEAT-GOV-05

**Enterprise governance policy registry and offline validation.** Central versioned policies (naming, schema, quality, classification, ownership, contracts, deployment readiness) inherited enterprise to entity and validated offline.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: Enterprise governance foundation thread (GOV-001..005), 2026-10-17
- **Owning workstream:** Enterprise governance foundation
- **Backlog:** [PB-056](PRODUCT_BACKLOG.md#pb-056), [PB-057](PRODUCT_BACKLOG.md#pb-057)
- **Recommended next action:** Owned by the Enterprise governance foundation thread (GOV-001 registry first).

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
- **Pull requests and issues:** [#48](https://github.com/KratosDataGod/fabric-desktop/pull/48), [#50](https://github.com/KratosDataGod/fabric-desktop/pull/50)
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
- **Known limitations:** Moving back to LH_Keys refused once new keys are issued.
- **Backlog:** [PB-052](PRODUCT_BACKLOG.md#pb-052)
- **Recommended next action:** Run on Fabric SQL database before anyone moves real keys.

### FEAT-MESH-03

**Landing zone and source registry.** One landing zone with registered sources, expected arrival and late-file flags, and who reads each.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Platform > Landing zone (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_teams_build_bronze_from_the_one_landing_zone (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/central/service.py`, `app/ui/platform.js`
- **Tests:** `sidecar/tests/test_platform.py`
- **Docs:** [docs/platform.md](../platform.md)
- **Known limitations:** Nothing in the app writes into LH_Landing. No extractors or connectors.
- **Owning workstream:** Staged roadmap (FD-116)
- **Backlog:** [PB-030](PRODUCT_BACKLOG.md#pb-030)
- **Recommended next action:** Guided ingestion after decision D2 (FD-116).

### FEAT-MESH-04

**Data mesh: domains, products, links, glossary.** Domains imported from Fabric, data products with ports, links between teams' products, glossary, About text and a mesh map on Home.

- **Pillar and category:** Enterprise services; Enterprise Shared Services
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Data mesh > Domains, OneLake > Data products, Home (Preview)
- **Evidence:** test: sidecar/tests/test_platform.py::test_an_entity_reads_another_teams_table_through_a_link (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_platform.py::test_mesh_map_lists_domains_projects_products_and_links
- **Source:** `sidecar/fabric_desktop/meta/mesh.py`, `sidecar/fabric_desktop/central/service.py`, `app/ui/mesh.js`, `app/ui/meshmap.js`
- **Tests:** `sidecar/tests/test_platform.py`
- **Docs:** [docs/data-mesh.md](../data-mesh.md)
- **Known limitations:** Fabric domain import needs Tenant.Read.All and its shape is unverified.
- **Pull requests and issues:** [#48](https://github.com/KratosDataGod/fabric-desktop/pull/48), [#50](https://github.com/KratosDataGod/fabric-desktop/pull/50)
- **Backlog:** [PB-052](PRODUCT_BACKLOG.md#pb-052)
- **Recommended next action:** Try on a tenant (data-mesh.md: not yet).

### FEAT-MESH-05

**Breaking-change check for data products.** Promote stops when a change drops or narrows a column another project reads, unless the product version is raised.

- **Pillar and category:** Enterprise services; Data Governance and Quality
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Promote card
- **Evidence:** test: sidecar/tests/test_breaking_changes.py::test_dropping_a_column_another_project_reads_blocks_the_promote (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/meta/mesh.py`, `sidecar/fabric_desktop/meta_service.py`
- **Tests:** `sidecar/tests/test_breaking_changes.py`
- **Docs:** [docs/data-mesh.md](../data-mesh.md)
- **Pull requests and issues:** [#71](https://github.com/KratosDataGod/fabric-desktop/pull/71)
- **Owning workstream:** Staged roadmap (FD-127)
- **Backlog:** [PB-082](PRODUCT_BACKLOG.md#pb-082)
- **Recommended next action:** Consumer declarations and deprecation (scale plan 8.2).

### FEAT-ORC-01

**PC schedules.** Notebook and metadata schedules on this PC through Windows Task Scheduler, with a lock so a run still going isn't started twice.

- **Pillar and category:** Local development; Pipelines and Orchestration
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Notebook ribbon, Data mesh, Monitor > Schedules (Preview)
- **Evidence:** test: sidecar/tests/test_schedules.py::test_task_definition_runs_the_sidecar_quietly (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_schedules.py::test_a_run_still_going_is_not_started_twice
- **Source:** `sidecar/fabric_desktop/local_schedules.py`, `sidecar/fabric_desktop/schedule_service.py`, `app/ui/schedules.js`
- **Tests:** `sidecar/tests/test_schedules.py`
- **Docs:** [docs/notebooks.md](../notebooks.md)
- **Known limitations:** Windows only; saved but never run elsewhere. Runs only while the user is signed in. Scheduled notebook runs execute frozen cells, UI runs skip them.
- **Backlog:** [PB-013](PRODUCT_BACKLOG.md#pb-013), [PB-048](PRODUCT_BACKLOG.md#pb-048)
- **Recommended next action:** Run frozen-cell rules the same as the UI; agent schedules cover Linux.

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
- **Pull requests and issues:** [#34](https://github.com/KratosDataGod/fabric-desktop/pull/34)
- **Backlog:** [PB-044](PRODUCT_BACKLOG.md#pb-044)
- **Recommended next action:** Verify on a tenant; add queue pruning and requeue of dead jobs (PB-044).

### FEAT-ORC-03

**OneLake write leases.** Per-table lease files in LH_Meta with conditional create, renew and delete so two writers don't collide.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** None (agent)
- **Evidence:** test: sidecar/tests/test_agent.py::test_leases_block_a_second_writer_until_released_or_expired (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_agent.py::test_onelake_leases_use_conditional_requests
- **Source:** `sidecar/fabric_desktop/leases.py`
- **Tests:** `sidecar/tests/test_agent.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** Whether OneLake honors conditional requests is unverified. The desktop app and Fabric runs take no leases. Expiry uses the holder's clock.
- **Backlog:** [PB-003](PRODUCT_BACKLOG.md#pb-003)
- **Recommended next action:** Prove OneLake honors If-None-Match/If-Match in the live test (PB-003).

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
- **Pull requests and issues:** [#64](https://github.com/KratosDataGod/fabric-desktop/pull/64)
- **Backlog:** [PB-049](PRODUCT_BACKLOG.md#pb-049), [PB-077](PRODUCT_BACKLOG.md#pb-077)
- **Recommended next action:** Run once on a tenant; record the writer's identity.

### FEAT-ORC-05

**Agent status records.** Each agent publishes agents/<name>.json with state, counts, running jobs and recent jobs; stale after 3 status periods.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented but Not Exposed. **Verification:** Partially Verified.
- **Where people reach it:** None (read only by incident evaluation)
- **Evidence:** test: sidecar/tests/test_agent_status.py::test_the_agent_publishes_what_it_is_doing (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/agent_status.py`, `sidecar/fabric_desktop/workloads/agents.py`
- **Tests:** `sidecar/tests/test_agent_status.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** No UI reads them.
- **Pull requests and issues:** [#66](https://github.com/KratosDataGod/fabric-desktop/pull/66)
- **Owning workstream:** Workloads (FD-122)
- **Backlog:** [PB-077](PRODUCT_BACKLOG.md#pb-077), [PB-091](PRODUCT_BACKLOG.md#pb-091)
- **Recommended next action:** Agent dashboard (FD-122); fix agent.md, which says workload views read these.

### FEAT-ORC-06

**Multiple workers.** Several agents sharing one queue with claims and a worker registry.

- **Pillar and category:** Enterprise services; Pipelines and Orchestration
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: jobqueue.py is per machine; docs/agent.md says one queue per agent
- **Owning workstream:** Workloads (FD-124)
- **Backlog:** [PB-045](PRODUCT_BACKLOG.md#pb-045)
- **Recommended next action:** Workloads program (FD-124, roadmap FD-12).

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
- **Pull requests and issues:** [#63](https://github.com/KratosDataGod/fabric-desktop/pull/63), [#72](https://github.com/KratosDataGod/fabric-desktop/pull/72)
- **Owning workstream:** Workloads
- **Backlog:** [PB-092](PRODUCT_BACKLOG.md#pb-092)
- **Recommended next action:** Shared ownership (stage 2B); an 'on agents' view.

### FEAT-OPS-02

**Incidents.** Incident engine shared by app and agents: run failed, late, objective, agent offline; acknowledge, assign, resolve, escalate; Teams routing with digest, quiet hours and outbox.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Partially Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Agent only; no incident screens in the app
- **Evidence:** test: sidecar/tests/test_incidents.py::test_the_app_and_the_agent_share_one_incident_list (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_incidents.py::test_quiet_hours_hold_messages_until_they_end_except_critical_and_escalations
- **Source:** `sidecar/fabric_desktop/workloads/incidents.py`, `sidecar/fabric_desktop/workloads/notify.py`, `sidecar/fabric_desktop/incident_service.py`
- **Tests:** `sidecar/tests/test_incidents.py`
- **Docs:** [docs/agent.md](../agent.md)
- **Known limitations:** Six incident RPCs have no UI. Metadata tables only. workloads/plan.md describes a Delta table; code uses a JSON document.
- **Pull requests and issues:** [#74](https://github.com/KratosDataGod/fabric-desktop/pull/74), [#79](https://github.com/KratosDataGod/fabric-desktop/pull/79)
- **Owning workstream:** Workloads (FD-123)
- **Backlog:** [PB-047](PRODUCT_BACKLOG.md#pb-047), [PB-077](PRODUCT_BACKLOG.md#pb-077), [PB-084](PRODUCT_BACKLOG.md#pb-084)
- **Recommended next action:** Incident screens after a mockup (stage 2.3B) (PB-047).

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
- **Pull requests and issues:** [#58](https://github.com/KratosDataGod/fabric-desktop/pull/58)
- **Recommended next action:** Show worker, host and job for agent runs.

### FEAT-OPS-04

**Live test harness.** fabric-desktop live-test: guarded checks of sign-in, the Fabric API, OneLake files and Delta on a workspace marked for tests, with a ledger and cleanup.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** CLI only
- **Evidence:** test: sidecar/tests/test_livetest.py::test_refuses_a_workspace_not_marked_for_tests_or_that_looks_like_production (sidecar suite passed in this assessment (656 passed, 2 skipped)); test: sidecar/tests/test_livetest.py::test_a_failure_midway_still_cleans_up_and_keeps_tokens_out; smoke: fabric-desktop live-test --help listed --dry-run, --cleanup and --agent-config
- **Source:** `sidecar/fabric_desktop/livetest.py`, `sidecar/fabric_desktop/cli.py`
- **Tests:** `sidecar/tests/test_livetest.py`
- **Docs:** [docs/live-testing.md](../live-testing.md)
- **Known limitations:** Never run. Doesn't test leases, concurrent writers, mailbox, Run in Fabric.
- **Pull requests and issues:** [#80](https://github.com/KratosDataGod/fabric-desktop/pull/80)
- **Owning workstream:** Enterprise readiness (FD-101)
- **Backlog:** [PB-002](PRODUCT_BACKLOG.md#pb-002)
- **Recommended next action:** Chris runs it on a non-production workspace (PB-002); extend to leases and MERGE (PB-003).

### FEAT-ADM-01

**Cost estimates.** CU-seconds per Fabric run at a configurable price, what PC runs saved, capacity share by SKU.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Implemented. **Verification:** Partially Verified.
- **Where people reach it:** Monitor > Cost (Preview)
- **Evidence:** test: sidecar/tests/test_estate.py::test_cost_estimates_fabric_runs_and_what_local_runs_saved (sidecar suite passed in this assessment (656 passed, 2 skipped))
- **Source:** `sidecar/fabric_desktop/costs.py`, `sidecar/fabric_desktop/estate_service.py`, `app/ui/estate.js`
- **Tests:** `sidecar/tests/test_estate.py`
- **Known limitations:** Estimates only (default $0.18/CU-hour, Python 1 CU, Spark 8 CU). Agent runs counted as local with no measured cost.
- **Backlog:** [PB-050](PRODUCT_BACKLOG.md#pb-050)
- **Recommended next action:** Calibrate against the Capacity Metrics app and label every number estimated (PB-050).

### FEAT-ADM-02

**Audit trail.** One hash-chained line per call that runs or changes something.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no audit module on main; doc: PR #89 open
- **Pull requests and issues:** [#89](https://github.com/KratosDataGod/fabric-desktop/pull/89)
- **Owning workstream:** Enterprise readiness (FD-125)
- **Backlog:** [PB-007](PRODUCT_BACKLOG.md#pb-007)
- **Recommended next action:** PR #89 merged after this assessment's commit; run the implementation review and re-inventory (FD-125).

### FEAT-ADM-03

**Backup and restore of local state.** Back up and restore settings, schedules, queue, workloads and receipts without sign-in tokens.

- **Pillar and category:** Enterprise services; Reliability, Testing, and Performance
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no backup command on main; doc: PR #91 open
- **Pull requests and issues:** [#91](https://github.com/KratosDataGod/fabric-desktop/pull/91)
- **Owning workstream:** Enterprise readiness (FD-126)
- **Backlog:** [PB-066](PRODUCT_BACKLOG.md#pb-066)
- **Recommended next action:** PR #91 merged after this assessment's commit; run the implementation review and re-inventory (FD-126).

### FEAT-ADM-04

**Ready to work checklist.** First-run checklist of sign-in, capacity, workspace access and OneLake reach with fixes.

- **Pillar and category:** Enterprise services; Developer Experience and Productivity
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: PR #94 open (mockup accepted)
- **Pull requests and issues:** [#94](https://github.com/KratosDataGod/fabric-desktop/pull/94)
- **Owning workstream:** Enterprise readiness (FD-136)
- **Backlog:** [PB-086](PRODUCT_BACKLOG.md#pb-086)
- **Recommended next action:** PR #94 merged after this assessment's commit (main 4079a41); re-inventory and run the implementation review (PB-086).

### FEAT-ADM-05

**Local data retention.** Delete local copies of lakehouse data after a policy period.

- **Pillar and category:** Enterprise services; Security and Enterprise Readiness
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** doc: PR #95 open
- **Pull requests and issues:** [#95](https://github.com/KratosDataGod/fabric-desktop/pull/95)
- **Owning workstream:** Enterprise readiness (FD-131b)
- **Backlog:** [PB-009](PRODUCT_BACKLOG.md#pb-009)
- **Recommended next action:** PR #95 merged after this assessment's commit; run the implementation review and re-inventory (FD-131b).

### FEAT-ADM-06

**Opt-in telemetry.** Consent-based usage telemetry with a policy switch.

- **Pillar and category:** Enterprise services; Enterprise Administration and FinOps
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no telemetry code
- **Owning workstream:** Enterprise readiness (FD-137)
- **Backlog:** [PB-093](PRODUCT_BACKLOG.md#pb-093)
- **Recommended next action:** Waiting on decision D-C1 (recommendation: none until a pilot asks).

### FEAT-SEC-01

**Code signing.** Signed exe and engine DLLs so Smart App Control and WDAC allow the engine.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** code: .github/workflows/ci.yml signs only when Trusted Signing secrets exist; doc: PR #28 blocked on the Trusted Signing account
- **Source:** `.github/workflows/ci.yml`
- **Pull requests and issues:** [#28](https://github.com/KratosDataGod/fabric-desktop/pull/28)
- **Owning workstream:** Enterprise readiness (FD-103, FD-104)
- **Backlog:** [PB-001](PRODUCT_BACKLOG.md#pb-001)
- **Recommended next action:** Chris creates the Azure Trusted Signing account (D-C3) (PB-001).

### FEAT-SEC-02

**Content Security Policy.** A CSP as a second layer behind escaping in the main webview.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** code: app/src-tauri/tauri.conf.json csp: null, withGlobalTauri: true
- **Source:** `app/src-tauri/tauri.conf.json`
- **Owning workstream:** Enterprise readiness (FD-102b)
- **Backlog:** [PB-006](PRODUCT_BACKLOG.md#pb-006)
- **Recommended next action:** FD-102b: move notebook HTML output to another origin, then set a CSP (PB-006).

### FEAT-SEC-03

**Supply chain: pinned packages, audit, SBOM, provenance.** Pinned Python packages, pip-audit and cargo-audit, CycloneDX SBOMs for app and engine, build provenance attestation, SHA-pinned actions in CI.

- **Pillar and category:** Platform foundation; Security and Enterprise Readiness
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** Releases
- **Evidence:** code: .github/workflows/ci.yml audit, sbom-app, engine, release jobs; absent: CI not run in this assessment
- **Source:** `.github/workflows/ci.yml`, `sidecar/constraints.txt`
- **Docs:** [docs/security.md](../security.md)
- **Known limitations:** docs.yml and codex-review.yml use tag-pinned actions; zensical install unpinned. release doesn't wait for audit.
- **Pull requests and issues:** [#86](https://github.com/KratosDataGod/fabric-desktop/pull/86), [#88](https://github.com/KratosDataGod/fabric-desktop/pull/88)
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
- **Pull requests and issues:** [#111](https://github.com/KratosDataGod/fabric-desktop/pull/111)
- **Backlog:** [PB-064](PRODUCT_BACKLOG.md#pb-064), [PB-071](PRODUCT_BACKLOG.md#pb-071)
- **Recommended next action:** Per-build release assets for pinning and rollback (PB-064).

### FEAT-REL-02

**Documentation site.** Zensical site from docs/ with strict link checking, published to GitHub Pages.

- **Pillar and category:** Platform foundation; Developer Experience and Productivity
- **Status:** Implemented. **Verification:** Unverified.
- **Where people reach it:** GitHub Pages
- **Evidence:** code: .github/workflows/docs.yml; code: zensical.toml strict = true
- **Source:** `zensical.toml`, `.github/workflows/docs.yml`
- **Docs:** [docs/README.md](../README.md)
- **Pull requests and issues:** [#40](https://github.com/KratosDataGod/fabric-desktop/pull/40)
- **Recommended next action:** Keep; add docs/product to the nav (this PR).

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
- **Pull requests and issues:** [#85](https://github.com/KratosDataGod/fabric-desktop/pull/85)
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
- **Status:** Planned. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no integrations package on main; doc: PRs #99-#108, #110, #112 open (PR-AI-001..012); #98 plan merged
- **Docs:** [docs/architecture/ai-integration.md](../architecture/ai-integration.md)
- **Pull requests and issues:** [#98](https://github.com/KratosDataGod/fabric-desktop/pull/98), [#99](https://github.com/KratosDataGod/fabric-desktop/pull/99), [#100](https://github.com/KratosDataGod/fabric-desktop/pull/100), [#101](https://github.com/KratosDataGod/fabric-desktop/pull/101), [#102](https://github.com/KratosDataGod/fabric-desktop/pull/102), [#103](https://github.com/KratosDataGod/fabric-desktop/pull/103), [#104](https://github.com/KratosDataGod/fabric-desktop/pull/104), [#105](https://github.com/KratosDataGod/fabric-desktop/pull/105), [#106](https://github.com/KratosDataGod/fabric-desktop/pull/106), [#107](https://github.com/KratosDataGod/fabric-desktop/pull/107), [#108](https://github.com/KratosDataGod/fabric-desktop/pull/108), [#110](https://github.com/KratosDataGod/fabric-desktop/pull/110), [#112](https://github.com/KratosDataGod/fabric-desktop/pull/112)
- **Owning workstream:** Bring your own AI integration
- **Backlog:** [PB-095](PRODUCT_BACKLOG.md#pb-095), [PB-096](PRODUCT_BACKLOG.md#pb-096)
- **Recommended next action:** Merge the AI integration stack in order; run docs/product/ai-integration-client-checklist.md per client.

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

**Debugger for notebooks and pipelines.** Breakpoints, stepping and variable watch in an isolated kernel.

- **Pillar and category:** Local development; SQL, Python, and Notebook Development
- **Status:** Missing. **Verification:** Unverified.
- **Where people reach it:** None
- **Evidence:** absent: no debugger code; prove/plan.md Stage 5 backlog #44
- **Owning workstream:** Prove
- **Backlog:** [PB-026](PRODUCT_BACKLOG.md#pb-026)
- **Recommended next action:** Prove program Stage 5 after the process kernel is default.
<!-- END GENERATED: details -->

## Keeping this inventory current

Follow [the product management process](PRODUCT_MANAGEMENT_PROCESS.md): after each merged PR the
implementation review updates the affected rows, adds evidence, and runs
`python docs/product/tools/product_data.py all`, which must end with "Product data is consistent."
Re-take the whole inventory at the start of each development cycle and record the new commit in
`assessed`.
