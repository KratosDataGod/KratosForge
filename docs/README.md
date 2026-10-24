# KratosForge documentation

KratosForge runs Microsoft Fabric data engineering work on your own PC (notebooks, Spark SQL, and a metadata-driven medallion framework) and writes the results straight into OneLake. Start with the guide for what you're doing.

Some files, folders and settings keep a legacy name so nothing breaks: [which ones and why](kratosforge.md).

## Start here

| I want to | Read |
| --- | --- |
| Install the app on my PC | [Install](install.md) |
| Back up my PC's notebooks and settings | [Back up and restore](backup.md) |
| Roll it out to many PCs | [Enterprise install](enterprise-install.md) |
| Set it up in Azure and Fabric for my organization | [Configure Azure and Fabric](azure-setup.md), then [Permissions](permissions.md) |
| Learn what every page does | [User guide](user-guide.md) |
| Write and run notebooks and SQL on my PC | [Notebooks and SQL](notebooks.md) |
| Know which engine runs my code, and set up Fabric, Databricks or both | [Engines and connection profiles](engines.md) (Preview) |
| Query a folder of data files with SQL on my PC | [Local data projects](local-data.md) |
| Compare a run on my PC with the same run in Fabric, or benchmark both | [Side by side](side-by-side.md) |
| Know where a CU or cost figure comes from and how far to trust it | [Capacity and cost evidence](finops/cost-evidence.md) |
| Share optimization results across every installation in my organization | [Optimize](optimize/README.md) |
| Build Bronze, Silver and Gold from config | [Metadata framework guide](metadata-guide.md) |
| Set it up for several teams | [Working across teams](teams.md), [The platform](platform.md), [Data mesh](data-mesh.md), [Governance policies](governance.md) and the [governance guide](governance-guide.md) |
| Watch and manage every OneLake shortcut: health, access, changes, requests | [Shortcut command center](shortcuts.md) |
| Check a Fabric pipeline definition on my PC | [Pipeline definitions](orchestration/pipeline-definitions.md) |
| See which pipeline activities need Fabric capacity | [Pipeline activity compatibility](orchestration/activity-registry.md) |
| Test a pipeline's control flow on my PC | [Running pipelines on your PC](orchestration/local-runs.md) |
| Fix an error | [Troubleshooting](troubleshooting.md) |
| Review the app for security approval | [Security evidence pack](evidence-pack.md) |
| Prove the app works with my tenant before a pilot | [Live testing](live-testing.md) |
| Suggest a feature or vote on one | [Submitting ideas](ideas.md) |

## All documents

| Document | For | What it covers |
| --- | --- | --- |
| [install.md](install.md) | Everyone | Requirements, download, first start, Windows blocking the engine, updates and channels, where files live, uninstalling |
| [backup.md](backup.md) | Everyone | Backing up and restoring notebooks, settings, schedules and history on a PC |
| [enterprise-install.md](enterprise-install.md) | Admins | Deploying with Intune or Configuration Manager, managed updates, rollback, failed updates, network access |
| [azure-setup.md](azure-setup.md) | Admins | Fabric tenant settings, sign-in policies, the optional policy file, workspaces and capacity, access, optional extras, a check that it works |
| [permissions.md](permissions.md) | Admins | How sign-in works with no app registration; what each feature uses; an optional own app registration and its permissions, in alphabetical order; what the agent needs |
| [telemetry.md](telemetry.md) | Everyone, admins | Opt-in usage data: every field sent, what is never sent, where it goes, the policy keys |
| [end-user-terms.md](end-user-terms.md) | Admins, legal | Draft end-user terms (not in effect), including the conditions for shipping Microsoft's ODBC driver |
| [evidence-pack.md](evidence-pack.md) | Security reviewers, IT | Everything a security review needs in one place: data flows, documents, build and release evidence, and what is still open |
| [security.md](security.md) | Admins, security reviewers | The threat model: what is protected, trust boundaries, how updates are checked, and what is still open |
| [hardening/README.md](hardening/README.md) | Security reviewers, platform owners | The enterprise hardening audit: scorecard, [findings](hardening/findings.md), [backlog](hardening/backlog.md) and [release gates](hardening/readiness-checklist.md) |
| [user-guide.md](user-guide.md) | Everyone | Every page: Home, Create, Workspaces, OneLake (catalog, shortcuts, access), Data mesh, Fabric, Monitor, Settings, the command bar and the bell |
| [notebooks.md](notebooks.md) | Engineers, analysts | Notebooks on your PC, Fabric code that works here, publishing, Run in Fabric, schedules, SQL query |
| [notebook-spark-on-this-pc.md](notebook-spark-on-this-pc.md) | Engineers | Which PySpark methods and Spark settings work on this PC |
| [sql-compatibility.md](sql-compatibility.md) | Engineers, analysts | Checking SQL against Fabric Warehouse, SQL analytics endpoint, SQL database and Spark before running it; the rules and their sources |
| [warehouse.md](warehouse.md) | Engineers, analysts | Warehouses (Preview): the Warehouse page, read-only T-SQL on a Fabric warehouse or SQL analytics endpoint, what runs in Fabric, the plan |
| [sql-execution.md](sql-execution.md) | Engineers, analysts, IT | How SQL page queries run on this PC: Stop, time and memory limits, paging, CSV/Parquet export, what a query may change, audit |
| [local-data.md](local-data.md) | Engineers, analysts | Local data projects (Preview): folders of files as SQL tables on your PC, naming rules, limits, where it is kept |
| [side-by-side.md](side-by-side.md) | Engineers, analysts | Running a notebook on this PC and in Fabric side by side, how results are checked, and the TPC-H benchmark |
| [finops/capacity.md](finops/capacity.md) | Engineers, platform owners | The capacity each environment is on, whether it is running, and what to do when it can't be read |
| [finops/risk-check.md](finops/risk-check.md) | Engineers, FinOps, IT | What the risk check before a run looks at, its results, settings and acknowledgements |
| [finops/placement-advice.md](finops/placement-advice.md) | Engineers, FinOps | Where to run a notebook, on this PC or in Fabric, and the evidence the advice uses |
| [finops/usage-import.md](finops/usage-import.md) | Engineers, capacity admins, FinOps | Import a Capacity Metrics app export as measured CU and compare it with this app's estimates; import an Azure Cost Management export for what each capacity was billed |
| [optimize/README.md](optimize/README.md) | Engineers, platform owners, admins | The central lakehouse (Preview): setting it up, permissions, what each installation writes, who can see what, the policy key |
| [finops/cost-evidence.md](finops/cost-evidence.md) | Engineers, platform owners | Optimize > Cost and savings figures: measured, estimated, scenario and unknown, what is counted, units, and the measurement schema |
| [notebook-resources.md](notebook-resources.md) | Engineers, analysts | Notebook resources: files beside a notebook, read in cells as `builtin/<file>`, and where they go |
| [notebook-magics.md](notebook-magics.md) | Engineers, analysts | Notebook magics and cell languages: SQL results in `_sqldf`, `%sql` inside Python, what runs on this PC |
| [notebook-files.md](notebook-files.md) | Engineers, analysts | All notebooks and search inside them, open a notebook file, save a copy, delete and restore, and edit a Fabric notebook on this PC (Preview) |
| [notebook-fabric-step.md](notebook-fabric-step.md) | Engineers | Run the last cell on Fabric Spark after the others run on this PC, to V-Order tables for Direct Lake (Preview) |
| [notebook-folders.md](notebook-folders.md) | Engineers, analysts | Notebook folders: group notebooks into folders, move a notebook into one, rename a folder, delete an empty one; publishing from a folder (Preview) |
| [validate-notebook.md](validate-notebook.md) | Engineers, analysts | Validate a notebook: code checks, a clean run from the top with sandboxed writes, and Clean up (Preview) |
| [notebook-import-export.md](notebook-import-export.md) | Engineers, analysts | Fabric and Databricks notebook formats: importing their source and .dbc archives, and exporting as .ipynb, Fabric or Databricks source or HTML (Preview) |
| [notebook-versions.md](notebook-versions.md) | Engineers, analysts | Notebook version history: versions, checkpoints, Compare and Restore |
| [forge-animations.md](forge-animations.md) | Engineers, analysts, IT | Motion and effects: intensity levels, every effect, performance, and the motion engine for developers |
| [profile.md](profile.md) | Engineers, analysts | Profile: a private record of verified work, levels and achievements, and a professional report (Preview) |
| [notebook-editor/select-and-run-parts.md](notebook-editor/select-and-run-parts.md) | Engineers, analysts | Select several cells and act on them; run selected cells or selected text |
| [notebook-editor/organise-cells.md](notebook-editor/organise-cells.md) | Engineers, analysts | Collapse heading sections, cell titles and tags, duplicate and drag cells |
| [engines.md](engines.md) | Engineers | Execution engines and what each can do, remote compute approval, usage figures, connection profiles for Fabric, Databricks or both (Preview) |
| [notebook-comments.md](notebook-comments.md) | Engineers, analysts | Comments on notebook cells: threads, replies, resolve, and where they are kept |
| [notebook-editor/find-and-replace.md](notebook-editor/find-and-replace.md) | Engineers, analysts | Find and replace across every cell of a notebook, with Replace all and undo |
| [notebook-editor/load-data-items.md](notebook-editor/load-data-items.md) | Engineers, analysts | Drag tables and files into cells; Load with Polars, Spark, SQL or pandas |
| [notebook-editor/format-code.md](notebook-editor/format-code.md) | Engineers, analysts | Format a cell or the whole notebook: Python with ruff, SQL cells as Format SQL does |
| [notebook-editor/intellisense.md](notebook-editor/intellisense.md) | Engineers, analysts | Hover docs, signature help and syntax errors in Python cells |
| [notebook-editor/variables.md](notebook-editor/variables.md) | Engineers, analysts | The Variables pane: filter, sort, memory, open data frames and containers |
| [prove/how-proofs-work.md](prove/how-proofs-work.md) | Engineers, analysts | Proving a notebook: pinned inputs, sandboxed outputs, how results are verified, the scorecard and receipts (Preview) |
| [metadata-guide.md](metadata-guide.md) | Engineers | The metadata framework step by step, from a new project to Prod, and a reference for every setting |
| [platform.md](platform.md) | Platform owners | The central platform: one key system, one landing zone, the catalog; platform admins, requests, the Platform page; creating, joining, moving keys |
| [governance.md](governance.md) | Platform owners, team leads | Governance policies (Preview): the policy registry, drafts and versions, enterprise to entity scopes, mandatory policies, governance.toml, who can change what |
| [governance-guide.md](governance-guide.md) | Platform owners, team leads | Governance guide (Preview): draft to result, worked examples (naming, classification, ownership), what can be enforced and by whom |
| [data-mesh.md](data-mesh.md) | Team leads, engineers | Domains, workspace designations, data products, links between teams, catalog text |
| [semantic-model-ossie.md](semantic-model-ossie.md) | Engineers, team leads | Semantic models to and from Apache Ossie (Preview): export to a `.ossie.yaml` file, import with a preview of every change, what a round trip keeps |
| [shortcuts.md](shortcuts.md) | Engineers, platform owners | The shortcut command center: health checks, who can read a shortcut, shortcut rules, change history with undo, repointing, requests |
| [source-control.md](source-control.md) | Engineers, analysts | Source control (Preview): clone, diffs, branches, commit, pull and push, the secret check, what it doesn't do yet |
| [orchestration/pipeline-definitions.md](orchestration/pipeline-definitions.md) | Engineers | Reading, checking and converting Fabric pipeline definitions on your PC (Preview) |
| [orchestration/activity-registry.md](orchestration/activity-registry.md) | Engineers | Which pipeline activities run on your PC, are only checked, or need Fabric capacity (Preview) |
| [orchestration/local-runs.md](orchestration/local-runs.md) | Engineers | Running a pipeline's control flow on your PC with mocks; it starts no Fabric job, and notebook steps read and write OneLake, which uses capacity (Preview) |
| [teams.md](teams.md) | Team leads, platform owners | Roles, Git workflow, environments and workspaces, shared keys, feedback at scale |
| [troubleshooting.md](troubleshooting.md) | Everyone | Each error message, what it means and what to do |
| [release-management/README.md](release-management/README.md) | Maintainers, platform owners | How builds reach Preview and Stable, who approves each step, the integration cycle for parallel PRs, promoting, per-build releases, rollback and pinning, versioning ([status](release-management/release-status.md), [history](release-management/release-history.md)) |
| [live-testing.md](live-testing.md) | Admins, maintainers | The live test: checking sign-in, the Fabric API, OneLake and Delta against a workspace set aside for tests, with cleanup and a report |
| [ideas.md](ideas.md) | Everyone | Posting ideas, voting, and how ideas are prioritized and shipped |
| [xray-fixes.md](xray-fixes.md) | Engineers | Each Estate X-ray finding and how to fix it |
| [agent.md](agent.md) | Platform owners | The headless agent on Windows or Linux servers |
| [architecture.md](architecture.md) | Contributors | How the app, the engine and OneLake fit together |
| [ai/README.md](ai/README.md) | Everyone | Connecting your own AI tool (Claude, Codex, Copilot, Cursor, any MCP client): setup for each, [workflows](ai/workflows.md), [instructions to paste](ai/agent-instructions.md) |
| [architecture/ai-integration.md](architecture/ai-integration.md) | Contributors, admins | External AI tools through MCP and a local API, with no AI in the app: architecture, permissions, security ([backlog](product/ai-integration-backlog.md), [decisions](product/ai-integration-decisions.md), [security](product/ai-integration-security.md), [compatibility](product/ai-integration-compatibility.md), [AI tool test checklist](product/ai-integration-client-checklist.md)) |
| [ui-guidelines.md](ui-guidelines.md) | Contributors | Where new features go in the UI |
| [ux/README.md](ux/README.md) | Contributors, product and release managers | UX and simplicity audit: scorecard, configuration, navigation, workflows, docs coverage, backlog (UX-nnn), usability release gates |
| [enterprise-roadmap.md](enterprise-roadmap.md) | Platform owners | Milestones for running at enterprise scale |
| [local-data-engine-plan.md](local-data-engine-plan.md) | Contributors, platform owners | The local data engine program FD-LDE-001 to 020: audit, implementation matrix, owners, PR plan |
| [scale-and-operations-plan.md](scale-and-operations-plan.md) | Platform owners | Staged plan for large tables, ingestion, releases, pipelines, lineage and monitoring |
| [quality/README.md](quality/README.md) | Contributors, release managers | Engineering quality baseline: scorecard, [findings](quality/findings.md) (QA-nnn), [architecture standards](quality/architecture.md), [remediation plan](quality/remediation.md), [PR quality gate](quality/pr-review.md) |
| [git-integration-plan.md](git-integration-plan.md) | Contributors | The Git integration program: assessment, Phase 1 (Git foundation, done), Fabric workspace Git, deployment and extensions |
| [prove/plan.md](prove/plan.md) | Contributors | The Prove this notebook program: what exists, the stages, and all 100 backlog items ([continuation note](prove/continuation.md)) |
| [orchestration/plan.md](orchestration/plan.md) | Contributors | Fabric pipelines on this PC, ORCH-001 to ORCH-010: definitions, compatibility registry, local execution ([continuation note](orchestration/continuation.md)) |
| [workloads/plan.md](workloads/plan.md) | Contributors, platform owners | The workload operations program: managing workloads on PCs, agents and Fabric; stages and all 100 backlog items ([continuation note](workloads/continuation.md)) |
| [optimize/plan.md](optimize/plan.md) | Contributors, platform owners | The Optimize program (PR-OPT-001 to 009): audit, risks, data model, PRs in order |
| [finops/plan.md](finops/plan.md) | Contributors, platform owners | The administration and FinOps program (FINOPS-001 to 005): audit, overlaps, PRs |
| [product/enterprise-gap-analysis.md](product/enterprise-gap-analysis.md) | Platform owners, contributors | The enterprise product program: what exists for each capability FD-101 to FD-140, what's missing and who owns it ([backlog](product/enterprise-backlog.md), [decisions](product/implementation-decisions.md)) |
| [product/release-readiness.md](product/release-readiness.md) | Platform owners | What must be true before an enterprise pilot, the capability matrix and the pilot checklist |
| [product/PRODUCT_MANAGEMENT_PROCESS.md](product/PRODUCT_MANAGEMENT_PROCESS.md) | Product owner, contributors | How work is chosen, ranked, built and accepted: the [feature inventory](product/FEATURE_INVENTORY.md), [backlog](product/PRODUCT_BACKLOG.md), [roadmap](product/PRIORITIZED_ROADMAP.md), [PR-ready specs](product/pr-ready/README.md), [implementation review](product/IMPLEMENTATION_REVIEW_PROCESS.md) and [competitive matrix](product/COMPETITIVE_CAPABILITY_MATRIX.md) |
| [why-kratosforge.md](why-kratosforge.md) | Everyone | Why it exists: the Power BI model for data engineering |
| [CONTRIBUTING.md](https://github.com/KratosDataGod/KratosForge/blob/main/CONTRIBUTING.md) | Contributors | Setting up, rules for changes, pull requests |

## What is tested and what isn't

KratosForge is new, and it's honest about what has been proven.

**Tested on every build**, by automated tests on Windows and Linux, against local Delta tables:

- Notebooks, the `lakehouse` module, `spark` and `notebookutils` on the PC
- Spark SQL translation and the SQL query engine
- The metadata framework: every load mode, checks and quarantine, surrogate keys, the four gates, compare, schema drift, replay, freshness, profiling and tests
- Startup, updates and settings
- The headless agent's queue, retries and leases

**Not yet tried on a live tenant:** sign-in through Microsoft's Fabric sign-in with no app registration, and Windows sign-in. Until it is, the maintainer's tenant results below are with an own app registration.

**Used on the maintainer's own Fabric tenant (with an own app registration):** sign-in and admin consent, listing workspaces and items, the OneLake catalog, the embedded Fabric pages (Fabric and the Monitoring hub) and their saved sign-in, and writing tables to OneLake from the PC. That is one tenant; yours may differ in settings and permissions.

**Preview features.** Every feature tagged **Preview** in the app is in this table, which a test keeps the same as the app's own list. Most are built to Fabric's documented APIs but not yet tried on a live tenant. **Settings > Preview features** hides them, except the ones marked "always shown": those are core steps, so they stay visible with their Preview tag.

| Preview feature | Where |
| --- | --- |
| Breakpoints, variable history and watches | Notebook line numbers (breakpoints, F5/F10/F11 stepping), Variables pane, View tab (with hidden-state warnings) |
| Run in Fabric | Notebook ribbon, Data mesh > Environments (notebooks and environments) |
| Schedules | Notebook ribbon, Data mesh, Monitor (on this PC and in Fabric) |
| Side by side | Notebook ribbon (this PC vs Fabric, and the TPC-H benchmark) |
| Prove this notebook | Notebook ribbon (isolated PC vs Fabric runs, verification, receipts) |
| Validate notebook | Notebook ribbon |
| Notebook runs | Notebook ribbon > Recent runs ([notebooks.md](notebooks.md#recent-runs-preview)) |
| Profile | Account menu (private record of verified work, levels, achievements and the professional report) |
| Edit Fabric notebooks on this PC | Workspaces, an item's preview: Edit on this PC ([notebook-files.md](notebook-files.md)) |
| Run the last cell on Fabric | Notebook cell ⋯ menu > Run on Fabric (Spark) ([notebook-fabric-step.md](notebook-fabric-step.md)) |
| Pipelines | Create > Pipeline ([user-guide.md](user-guide.md#pipelines-preview)) |
| Pop-out windows | Monitor > Pop out on From KratosForge and In Fabric ([user-guide.md](user-guide.md#pop-out-windows-preview)) |
| Pipeline runs in Monitor | Monitor > Runs, pipeline runs on this PC with their activities ([user-guide.md](user-guide.md#pipeline-runs-in-monitor-preview)) |
| Publishing notebooks from folders | All notebooks > Move to folder; Publish names the item without its folder ([notebook-folders.md](notebook-folders.md)) |
| New notebook wizard | Create > Notebook from a template |
| Notebook import and export formats | Home > Import from Fabric or Databricks; a notebook's Home tab > Export ([notebook-import-export.md](notebook-import-export.md)) |
| Cell dependencies | Notebook ribbon, View and Run tabs (with Run stale cells) |
| Parameters and widgets bar | Notebook, above the cells (parameter cell values and Databricks widgets) |
| Failure replay bundles | Under a cell that failed on this PC; Create > Open replay bundle |
| Notebook sessions | Notebook status bar > Sessions (a session per notebook, several running at once; limits in Settings > Preview features) |
| Notebook environments | Notebook status bar > Environment (extra Python packages per notebook) |
| Cost | Monitor |
| Capacity status | Optimize > Cost and savings, Data mesh > Environments |
| Measured usage import | Optimize > Cost and savings |
| OneLake shortcuts | OneLake > Shortcuts |
| Shortcut fixes | OneLake > Shortcuts > Health and a shortcut's panel (find a lakehouse that moved, repoint many at once, accept a crossed environment; [shortcuts.md](shortcuts.md#fixing-shortcuts)) |
| OneLake access | OneLake > Access |
| Table history and restore | Entity page |
| Table health | Data mesh > Quality (with Optimize) |
| Semantic model | Data mesh > Deploy |
| Tests | Data mesh > Quality |
| Estate X-ray | Home |
| Optimize | Optimize |
| Item preview | Workspaces |
| Data mesh | Data mesh > Domains, Platform > Domains, OneLake > Data products, Home, entity page (domains from Fabric, products, links, glossary, About, the map on Home, data products for everyone) |
| Platform | Platform, Settings, Data mesh > Surrogate keys (admins, landing zone, business keys, requests) |
| Workspace, domain and exception requests | Platform > Requests > New request: a new workspace, access to a workspace, a domain, an exception to a policy ([platform.md](platform.md#requests)) |
| Endorsement and business terms | OneLake > Data products, open a product: its endorsement from Fabric's admin tenant scan, and business terms linked to Purview ([data-mesh.md](data-mesh.md#endorsement-and-business-terms)) |
| Source control | Source control |
| Workloads | Monitor > Operations > Workloads, Home (inventory, health, ownership, the workload page, Your workloads) |
| Incidents | Monitor > Operations > Incidents (numbers, priorities, SLAs, reported incidents, your service desk; [incidents.md](incidents.md)) |
| Agents | Monitor > Operations > Agents |
| Queue | Monitor > Operations > Queue |
| Run as | Monitor > Operations > Run as |
| External AI tools | Settings > Integrations |
| Release records | Data mesh > Deploy, each environment's Last release card |
| Chart builder | Notebook > a table output > Chart ([notebooks.md](notebooks.md)) |
| Several charts per result | Notebook > a table output > + Chart ([notebooks.md](notebooks.md)) |
| Data Wrangler | Notebook > a table output > Clean ([notebooks.md](notebooks.md)) |
| Query profile | Notebook > a spark or %%sql table output > Query profile ([notebooks.md](notebooks.md)) |
| Shared notebook sessions | Notebook status bar > Sessions, Share this notebook's session ([notebooks.md](notebooks.md)) |
| Notebook widgets | Notebook > a cell that shows ipywidgets controls ([notebooks.md](notebooks.md)) |
| Altair charts in notebooks | Notebook > a cell that shows an Altair chart ([notebooks.md](notebooks.md)) |
| Result downloads | Notebook > a table output > Download ([notebooks.md](notebooks.md)) |
| Column lineage | Entity page, Columns |
| Roll back a release | Data mesh > Deploy, Roll back... on a Test or Prod Last release card |
| Promote | Data mesh > Deploy (always shown) |
| Sync keys | Data mesh > Surrogate keys, Sync to LH_Keys (always shown) |
| Variable library | Data mesh > Source systems (always shown) |
| Warehouse queries | Workspaces > a Warehouse or SQL analytics endpoint row > SQL query (the Warehouse page) |
| Messages and Chart on the SQL page | SQL query > Messages, Chart above the result ([warehouse.md](warehouse.md#results-messages-and-chart)) |
| Quarantine replay | Entity page, quarantined rows (always shown) |
| Risk check before a run | Before a run in Workloads, Notebook > Run in Fabric and Data mesh ([risk-check.md](finops/risk-check.md); always shown) |

Two notebook settings are also in Preview: **Run notebooks in their own process**, and the **notebook cell time and memory limits** that need it (both in Settings > Preview features).

**Local data projects** (Preview) run on your PC and don't call Fabric. Their tests use real files, DuckDB and Delta tables on every build; there are no screens for them yet ([local-data.md](local-data.md)).

**Source control** (Preview) is Git on your PC and doesn't call Fabric. Its tests run real Git against local repositories on every build; it hasn't yet been tried with Git Credential Manager and SSH on many PCs and servers ([source-control.md](source-control.md)).

**Pipeline definitions** (Preview) are read and checked on your PC and don't call Fabric. Their tests use hand-built definitions in Microsoft's documented formats; reading real exports from a tenant hasn't been tried yet ([pipeline-definitions.md](orchestration/pipeline-definitions.md)). The **activity compatibility registry** (Preview) is tested the same way; no entry has been compared against Fabric ([activity-registry.md](orchestration/activity-registry.md)). **Local pipeline runs** (Preview) run control flow and child pipelines on your PC with mocks for data activities; they follow Data Factory's documentation and haven't been compared with Fabric runs ([local-runs.md](orchestration/local-runs.md)).

The headless agent hasn't run against a live tenant either ([agent.md](agent.md#verify-on-your-tenant)). Promote to Test and Prod creates lakehouses and writes through the same calls as the features above; try it on a non-production workspace first.

When a Preview feature has been tried on a tenant, its tag is removed. If you try one, say how it went in the [Ideas forum](ideas.md) or a bug report.
