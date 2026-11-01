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
| Know which engine runs my code, and set up Fabric, Databricks, Snowflake or a mix | [Engines and connection profiles](engines.md) (Preview) |
| Query a folder of data files with SQL on my PC | [Local data projects](local-data.md) |
| Compare a run on my PC with the same run in Fabric, or benchmark both | [Side by side](side-by-side.md) |
| Know where a CU or cost figure comes from and how far to trust it | [Capacity and cost evidence](finops/cost-evidence.md) |
| Share optimization results across every installation in my organization | [Optimize](optimize/README.md) |
| Build Bronze, Silver and Gold from config | [Metadata framework guide](metadata-guide.md) |
| Check a semantic model against Best Practice Analyzer rules | [Semantic model best practices](semantic-model-best-practices.md) |
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
| [enterprise-install.md](enterprise-install.md) | Admins | Deploying with Intune or Configuration Manager, managed updates, hosting the engine on your own server, rollback, failed updates, network access |
| [azure-setup.md](azure-setup.md) | Admins | Fabric tenant settings, sign-in policies, the optional policy file, workspaces and capacity, access, optional extras, a check that it works |
| [permissions.md](permissions.md) | Admins | How sign-in works with no app registration; what each feature uses; an optional own app registration and its permissions, in alphabetical order; what the agent needs |
| [telemetry.md](telemetry.md) | Everyone, admins | Opt-in usage data: every field sent, what is never sent, where it goes, the policy keys |
| [end-user-terms.md](end-user-terms.md) | Admins, legal | Draft end-user terms (not in effect), including the conditions for shipping Microsoft's ODBC driver |
| [evidence-pack.md](evidence-pack.md) | Security reviewers, IT | Everything a security review needs in one place: data flows, documents, build and release evidence, and what is still open |
| [security.md](security.md) | Admins, security reviewers | The threat model: what is protected, trust boundaries, how updates are checked, and what is still open |
| [hardening/README.md](hardening/README.md) | Security reviewers, platform owners | The enterprise hardening audit: scorecard, [findings](hardening/findings.md), [backlog](hardening/backlog.md), [release gates](hardening/readiness-checklist.md) and [engine call limits](hardening/engine-call-limits.md) |
| [user-guide.md](user-guide.md) | Everyone | Every page: Home, Create, Workspaces, OneLake (catalog, shortcuts, access), Data mesh, Fabric, Monitor, Settings, the command bar and the bell |
| [notebooks.md](notebooks.md) | Engineers, analysts | Notebooks on your PC, Fabric code that works here, publishing, Run in Fabric, schedules, SQL query |
| [notebook-spark-on-this-pc.md](notebook-spark-on-this-pc.md) | Engineers | Which PySpark methods and Spark settings work on this PC |
| [sql-compatibility.md](sql-compatibility.md) | Engineers, analysts | Checking SQL against Fabric Warehouse, SQL analytics endpoint, SQL database and Spark before running it; the rules and their sources |
| [semantic-model-rules.md](semantic-model-rules.md) | Engineers, BI developers | Best Practice Analyzer rules for the semantic model Update model writes, checked on this PC (Preview) |
| [sql-best-practices.md](sql-best-practices.md) | Engineers, analysts | Best-practice checks on Warehouse and SQL endpoint T-SQL, read on this PC without running it (Preview) |
| [warehouse.md](warehouse.md) | Engineers, analysts | Warehouses (Preview): the Warehouse page, T-SQL on a Fabric warehouse or SQL analytics endpoint, changes after a preview and confirm, the warehouse project in Git with compare and deploy, Monitoring from Fabric's workspace monitoring with views built in Fabric, what runs in Fabric, the plan |
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
| [vscode.md](vscode.md) | Engineers | VS Code and KratosForge on the same notebooks: Open in VS Code, editing in both, `kratosforge://` links back, repository setup, Copilot (Preview) |
| [home-notebooks.md](home-notebooks.md) | Engineers, analysts | Home's Recent notebooks: each notebook's workspace, lakehouse and last run, running several at once, and items pinned to Home (Preview) |
| [learn.md](learn.md) | Everyone | Learn (Preview): lessons, paths and practice labs that run on this PC, the best-practice rules explained, and guidance modes; [writing a lesson](learn/writing-lessons.md) for authors |
| [notebook-fabric-step.md](notebook-fabric-step.md) | Engineers | Run the last cell on Fabric Spark after the others run on this PC, to V-Order tables for Direct Lake (Preview) |
| [notebook-best-practices.md](notebook-best-practices.md) | Engineers | Best-practice checks on a notebook's code, read on this PC without running it (Preview) |
| [notebook-folders.md](notebook-folders.md) | Engineers, analysts | Notebook folders: group notebooks into folders, move a notebook into one, rename a folder, delete an empty one; publishing from a folder (Preview) |
| [validate-notebook.md](validate-notebook.md) | Engineers, analysts | Validate a notebook: code checks, a clean run from the top with sandboxed writes, and Clean up (Preview) |
| [notebook-import-export.md](notebook-import-export.md) | Engineers, analysts | Fabric and Databricks notebook formats: importing their source and .dbc archives, and exporting as .ipynb, Fabric or Databricks source or HTML (Preview) |
| [notebook-versions.md](notebook-versions.md) | Engineers, analysts | Notebook version history: versions, checkpoints, Compare and Restore |
| [forge-animations.md](forge-animations.md) | Engineers, analysts, IT | Motion and effects: intensity levels, every effect, performance, and the motion engine for developers |
| [profile.md](profile.md) | Engineers, analysts | Profile: a private record of verified work, levels and achievements, and a professional report (Preview) |
| [notebook-editor/select-and-run-parts.md](notebook-editor/select-and-run-parts.md) | Engineers, analysts | Select several cells and act on them; run selected cells or selected text |
| [notebook-editor/organise-cells.md](notebook-editor/organise-cells.md) | Engineers, analysts | Collapse heading sections, cell titles and tags, duplicate and drag cells |
| [platforms.md](platforms.md) | Engineers | What KratosForge supports on Fabric, Databricks, Snowflake and this PC, at which level, and the evidence behind each level |
| [engines.md](engines.md) | Engineers | Execution engines and what each can do, remote compute approval, the Databricks SQL warehouse engine, Databricks SQL and Snowflake SQL tried on DuckDB, usage figures, connection profiles for Fabric, Databricks, Snowflake or a mix (Preview) |
| [notebook-comments.md](notebook-comments.md) | Engineers, analysts | Comments on notebook cells: threads, replies, resolve, and where they are kept |
| [notebook-editor/find-and-replace.md](notebook-editor/find-and-replace.md) | Engineers, analysts | Find and replace across every cell of a notebook, with Replace all and undo |
| [notebook-editor/load-data-items.md](notebook-editor/load-data-items.md) | Engineers, analysts | Drag tables and files into cells; Load with Polars, Spark, SQL or pandas |
| [notebook-editor/format-code.md](notebook-editor/format-code.md) | Engineers, analysts | Format a cell or the whole notebook: Python with ruff, SQL cells as Format SQL does |
| [notebook-editor/intellisense.md](notebook-editor/intellisense.md) | Engineers, analysts | Hover docs, signature help and syntax errors in Python cells |
| [notebook-editor/variables.md](notebook-editor/variables.md) | Engineers, analysts | The Variables pane: filter, sort, memory, open data frames and containers |
| [prove/how-proofs-work.md](prove/how-proofs-work.md) | Engineers, analysts | Proving a notebook: pinned inputs, sandboxed outputs, how results are verified, the scorecard and receipts (Preview) |
| [metadata-guide.md](metadata-guide.md) | Engineers | The metadata framework step by step, from a new project to Prod, and a reference for every setting |
| [semantic-model-best-practices.md](semantic-model-best-practices.md) | Engineers, BI developers | Best Practice Analyzer through semantic-link-labs in Fabric (Preview): what a check runs, writes and costs |
| [platform.md](platform.md) | Platform owners | The central platform: one key system, one landing zone, the catalog; platform admins, requests, the Platform page; creating, joining, moving keys |
| [health-thresholds.md](health-thresholds.md) | Engineers, platform owners | The thresholds KratosForge flags tables, warehouses and capacity use at, compared with Microsoft's Fabric skills, and why each value is kept |
| [platform-audit.md](platform-audit.md) | Auditors, platform owners | The platform's audit trail: what each request, decision and access change records, and how to pull one request's history |
| [governance.md](governance.md) | Platform owners, team leads | Governance policies (Preview): the policy registry, drafts and versions, enterprise to entity scopes, mandatory policies, governance.toml, who can change what |
| [governance-guide.md](governance-guide.md) | Platform owners, team leads | Governance guide (Preview): draft to result, worked examples (naming, classification, ownership), what can be enforced and by whom |
| [data-mesh.md](data-mesh.md) | Team leads, engineers | Domains, workspace designations, data products, links between teams, asking another team for data, following a product, catalog text |
| [direct-lake-check.md](direct-lake-check.md) | Engineers, team leads | The Direct Lake check (Preview): Direct Lake limits per capacity, schema drift and relationship keys, checked on this PC |
| [data-dictionary.md](data-dictionary.md) | Engineers, data stewards | The data dictionary (Preview): descriptions for a layer's model, exported to Excel and imported back, then written into the model |
| [semantic-model-ossie.md](semantic-model-ossie.md) | Engineers, team leads | Semantic models to and from Apache Ossie (Preview): export to a `.ossie.yaml` file, import with a preview of every change, what a round trip keeps |
| [shortcuts.md](shortcuts.md) | Engineers, platform owners | The shortcut command center: health checks, who can read a shortcut, shortcut rules, change history with undo, repointing, requests |
| [source-control.md](source-control.md) | Engineers, analysts | Source control (Preview): clone, diffs, branches, commit, pull and push, the secret check, what it doesn't do yet |
| [orchestration/pipeline-definitions.md](orchestration/pipeline-definitions.md) | Engineers | Reading, checking and converting Fabric pipeline definitions on your PC (Preview) |
| [orchestration/activity-registry.md](orchestration/activity-registry.md) | Engineers | Which pipeline activities run on your PC, are only checked, or need Fabric capacity (Preview) |
| [orchestration/local-runs.md](orchestration/local-runs.md) | Engineers | Running a pipeline's control flow on your PC with mocks; it starts no Fabric job, and notebook steps read and write OneLake, which uses capacity (Preview) |
| [teams.md](teams.md) | Team leads, platform owners | Roles, Git workflow, environments and workspaces, shared keys, feedback at scale |
| [troubleshooting.md](troubleshooting.md) | Everyone | Each error message, what it means and what to do |
| [incidents.md](incidents.md) | Team leads, platform owners, IT | Incidents (Preview): numbers, priorities, SLAs, reported incidents, Who's affected, where incidents are kept, your service desk |
| [release-management/README.md](release-management/README.md) | Maintainers, platform owners | How builds reach Preview and Stable, who approves each step, the integration cycle for parallel PRs, promoting, per-build releases, rollback and pinning, versioning ([status](release-management/release-status.md), [history](release-management/release-history.md)) |
| [live-testing.md](live-testing.md) | Admins, maintainers | The live test: checking sign-in, the Fabric API, OneLake and Delta against a workspace set aside for tests, with cleanup and a report; and the Databricks live test on a warehouse and cluster tagged for tests |
| [product/smoke-test/README.md](product/smoke-test/README.md) | Maintainers | The tenant smoke test: ten core flows on a workspace set aside for tests, part automated (`fabric-desktop smoke-test`) and part by hand, and how to record the results |
| [ideas.md](ideas.md) | Everyone | Posting ideas, voting, and how ideas are prioritized and shipped |
| [xray-fixes.md](xray-fixes.md) | Engineers | Each Estate X-ray finding and how to fix it |
| [agent.md](agent.md) | Platform owners | The headless agent on Windows or Linux servers |
| [architecture.md](architecture.md) | Contributors | How the app, the engine and OneLake fit together |
| [ai/README.md](ai/README.md) | Everyone | Connecting your own AI tool (Claude, Codex, Copilot, Cursor, any MCP client): setup for each, [workflows](ai/workflows.md), [instructions to paste](ai/agent-instructions.md), [use with Microsoft's Fabric skills](ai/microsoft-fabric-skills.md) |
| [architecture/ai-integration.md](architecture/ai-integration.md) | Contributors, admins | External AI tools through MCP and a local API, with no AI in the app: architecture, permissions, security ([backlog](product/ai-integration-backlog.md), [decisions](product/ai-integration-decisions.md), [security](product/ai-integration-security.md), [compatibility](product/ai-integration-compatibility.md), [AI tool test checklist](product/ai-integration-client-checklist.md)) |
| [architecture/power-bi-embedding.md](architecture/power-bi-embedding.md) | Contributors | Proposed, not built: Embed Studio for Power BI embedded analytics, the isolated embed host, tokens and what each content type supports |
| [ui-guidelines.md](ui-guidelines.md) | Contributors | Where new features go in the UI |
| [design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md](design/KRATOSFORGE_VISUAL_STYLE_GUIDE.md) | Contributors, designers, agents | The visual identity, Forged Heroism: the canonical storyboard, palette, the wolf, the forge language, animation standards, consistency checks, image generation ([assets](design/assets.md), [wolf sheet](design/wolf-character-sheet.md), [event catalog](design/animation-events.md), [examples](design/examples.md)) |
| [animation-style-guide.md](animation-style-guide.md) | Contributors, designers | The four animation tiers (Ember, Spark, Forge, Titan), brand fire and winter colours, motifs, timing and reduced-motion rules |
| [ux/README.md](ux/README.md) | Contributors, product and release managers | UX and simplicity audit: scorecard, configuration, navigation, workflows, docs coverage, backlog (UX-nnn), usability release gates |
| [ux/action-first.md](ux/action-first.md) | Contributors, product and release managers | Action-first standard, review checklist, measured baseline and backlog (AF-n); the shared action framework |
| [ux/accessibility-audit.md](ux/accessibility-audit.md) | Contributors, UX reviewers | The automated accessibility audit: WCAG 2.1 A and AA, keyboard and focus results for every page in light and dark, and what is still to judge by hand |
| [enterprise-roadmap.md](enterprise-roadmap.md) | Platform owners | Milestones for running at enterprise scale |
| [local-data-engine-plan.md](local-data-engine-plan.md) | Contributors, platform owners | The local data engine program FD-LDE-001 to 020: audit, implementation matrix, owners, PR plan |
| [scale-and-operations-plan.md](scale-and-operations-plan.md) | Platform owners | Staged plan for large tables, ingestion, releases, pipelines, lineage and monitoring |
| [quality/README.md](quality/README.md) | Contributors, release managers | Engineering quality baseline: scorecard, [findings](quality/findings.md) (QA-nnn), [architecture standards](quality/architecture.md), [remediation plan](quality/remediation.md), [PR quality gate](quality/pr-review.md) |
| [git-integration-plan.md](git-integration-plan.md) | Contributors | The Git integration program: assessment, Phase 1 (Git foundation, done), Fabric workspace Git, deployment and extensions |
| [prove/plan.md](prove/plan.md) | Contributors | The Prove this notebook program: what exists, the stages, and all 100 backlog items ([continuation note](prove/continuation.md)) |
| [orchestration/plan.md](orchestration/plan.md) | Contributors | Fabric pipelines on this PC, ORCH-001 to ORCH-010: definitions, compatibility registry, local execution ([continuation note](orchestration/continuation.md)) |
| [orchestration/data-factory.md](orchestration/data-factory.md) | Contributors | Data Factory beyond pipelines: Fabric pipeline runs in Monitor, copy jobs, Dataflow Gen2 and connections, DF-1 to DF-10 (Preview) |
| [workloads/plan.md](workloads/plan.md) | Contributors, platform owners | The workload operations program: managing workloads on PCs, agents and Fabric; stages and all 100 backlog items ([continuation note](workloads/continuation.md)) |
| [optimize/plan.md](optimize/plan.md) | Contributors, platform owners | The Optimize program (PR-OPT-001 to 009): audit, risks, data model, PRs in order |
| [finops/plan.md](finops/plan.md) | Contributors, platform owners | The administration and FinOps program (FINOPS-001 to 005): audit, overlaps, PRs |
| [product/enterprise-gap-analysis.md](product/enterprise-gap-analysis.md) | Platform owners, contributors | The enterprise product program: what exists for each capability FD-101 to FD-140, what's missing and who owns it ([backlog](product/enterprise-backlog.md), [decisions](product/implementation-decisions.md)) |
| [product/release-readiness.md](product/release-readiness.md) | Platform owners | What must be true before an enterprise pilot, the capability matrix and the pilot checklist |
| [product/PRODUCT_MANAGEMENT_PROCESS.md](product/PRODUCT_MANAGEMENT_PROCESS.md) | Product owner, contributors | How work is chosen, ranked, built and accepted: the [feature inventory](product/FEATURE_INVENTORY.md), [backlog](product/PRODUCT_BACKLOG.md), [roadmap](product/PRIORITIZED_ROADMAP.md), [PR-ready specs](product/pr-ready/README.md), [implementation review](product/IMPLEMENTATION_REVIEW_PROCESS.md), [competitive matrix](product/COMPETITIVE_CAPABILITY_MATRIX.md) and [development status](product/DEVELOPMENT_STATUS.md) |
| [product/operations/README.md](product/operations/README.md) | Product owner, contributors | How development runs: six workstreams and their lead threads, story issues in GitHub, [work queues](product/operations/workstreams.json), the Monday weekly review, quality gates and the [12-week roadmap](product/operations/roadmap.md) |
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
| Notebook engine picker | Notebook ribbon: Engine beside Run all, which replaces Runs on ([engines.md](engines.md#engine-picker-preview)) |
| Schedules | Notebook ribbon, Data mesh, Monitor (on this PC and in Fabric) |
| Side by side | Notebook ribbon (this PC vs Fabric, and the TPC-H benchmark) |
| Prove this notebook | Notebook ribbon (isolated PC vs Fabric runs, verification, receipts) |
| Validate notebook | Notebook ribbon |
| Notebook runs | Notebook ribbon > Schedule > Recent runs, or the Run tab ([notebooks.md](notebooks.md#recent-runs-preview)) |
| Profile | Account menu (private record of verified work, levels, achievements and the professional report) |
| Edit Fabric notebooks on this PC | Workspaces, an item's preview: Edit on this PC ([notebook-files.md](notebook-files.md)) |
| Run several notebooks from Home | Home > Recent notebooks: tick notebooks, then Run ([home-notebooks.md](home-notebooks.md#run-several-notebooks-preview)) |
| Items pinned to Home | Home > Pinned; the pin on Home's notebook rows and on Workspaces item rows ([home-notebooks.md](home-notebooks.md#pinned-items-preview)) |
| Run the last cell on Fabric | Notebook cell ⋯ menu > Run on Fabric (Spark) ([notebook-fabric-step.md](notebook-fabric-step.md)) |
| Pipelines | Create > Pipeline ([user-guide.md](user-guide.md#pipelines-preview)) |
| Pop-out windows | Monitor > Pop out on From KratosForge and In Fabric ([user-guide.md](user-guide.md#pop-out-windows-preview)) |
| Pipeline runs in Monitor | Monitor > Runs, pipeline runs on this PC and in Fabric with their activities ([user-guide.md](user-guide.md#pipeline-runs-in-monitor-preview)) |
| Cancel a Fabric run | Monitor > Runs and the bell, Cancel on a running Fabric job this app started ([user-guide.md](user-guide.md#cancel-a-fabric-run-preview)) |
| Publishing notebooks from folders | All notebooks > Move to folder; Publish names the item without its folder ([notebook-folders.md](notebook-folders.md)) |
| New notebook wizard | Create > Notebook from a template |
| Notebook import and export formats | Home > Import from Fabric or Databricks; a notebook's Home tab > Share > Export ([notebook-import-export.md](notebook-import-export.md)) |
| Cell dependencies | Notebook ribbon, View and Run tabs (with Run stale cells) |
| Notebook best practices | Notebook ribbon, View tab > Best practices ([notebook-best-practices.md](notebook-best-practices.md)) |
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
| Semantic models and Apache Ossie | Data mesh > Deploy > Semantic model ([semantic-model-ossie.md](semantic-model-ossie.md)) |
| Direct Lake check | Data mesh > Deploy > Semantic model ([direct-lake-check.md](direct-lake-check.md)) |
| Data dictionary | Data mesh > Deploy > Semantic model ([data-dictionary.md](data-dictionary.md)) |
| Tests | Data mesh > Quality |
| Estate X-ray | Home |
| Optimize | Optimize |
| Item preview | Workspaces |
| Data mesh | Data mesh > Domains, Data mesh > Products, Home, entity page (domains from Fabric, products, links, glossary, About, the map on Home, data products for everyone, following a product) |
| Platform | Platform, Settings, Data mesh > Surrogate keys (admins, landing zone, business keys, requests) |
| Workspace, domain and exception requests | Data mesh > Requests > New request: a new workspace, access to a workspace, a domain, an exception to a policy, data from another team; Data mesh > Products > Ask for data ([platform.md](platform.md#requests)) |
| Endorsement and business terms | Data mesh > Products, open a product: its endorsement from Fabric's admin tenant scan, and business terms linked to Purview ([data-mesh.md](data-mesh.md#endorsement-and-business-terms)) |
| Best practices: checks and templates | Platform > Governance > Templates and Check this project; Data mesh > Products, open a product: its checklist ([governance.md](governance.md#best-practices-checks-and-templates-preview)) |
| Source control | Source control |
| Workloads | Monitor > KratosForge > Operations > Workloads, Home (inventory, health, ownership, the workload page, Your workloads) |
| Incidents | Monitor > KratosForge > Operations > Incidents (numbers, priorities, SLAs, reported incidents, your service desk, Who's affected; [incidents.md](incidents.md)) |
| Agents | Monitor > KratosForge > Operations > Agents |
| Queue | Monitor > KratosForge > Operations > Queue |
| Run as | Monitor > KratosForge > Operations > Run as |
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
| Warehouse queries | Workspaces > a Warehouse or SQL analytics endpoint row > SQL query (the Warehouse page), and its Monitoring tab ([warehouse.md](warehouse.md#monitoring-preview)) |
| Messages and Chart on the SQL page | SQL query > Messages, Chart above the result ([warehouse.md](warehouse.md#results-messages-and-chart)) |
| SQL best practices | SQL query or Warehouse page, Best practices chip beside Compatibility, for T-SQL ([sql-best-practices.md](sql-best-practices.md)) |
| New build banner | Below the top bar while KratosForge is open; Settings > Updates ([user-guide.md](user-guide.md#a-new-build-while-the-app-is-open-preview)) |
| Quarantine replay | Entity page, quarantined rows (always shown) |
| Risk check before a run | Before a run in Workloads, Notebook > Run in Fabric and Data mesh ([risk-check.md](finops/risk-check.md); always shown) |
| VS Code pairing | Settings > Integrations > VS Code ([vscode.md](vscode.md); always shown, so a pairing that is on can always be turned off) |
| Learn | Learn at the bottom of the left rail: lessons, practice labs on this PC and the best-practice rules ([learn.md](learn.md)) |

Two notebook settings are also in Preview: **Run notebooks in their own process**, and the **notebook cell time and memory limits** that need it (both in Settings > Preview features).

**Local data projects** (Preview) run on your PC and don't call Fabric. Their tests use real files, DuckDB and Delta tables on every build; there are no screens for them yet ([local-data.md](local-data.md)).

**Source control** (Preview) is Git on your PC and doesn't call Fabric. Its tests run real Git against local repositories on every build; it hasn't yet been tried with Git Credential Manager and SSH on many PCs and servers ([source-control.md](source-control.md)).

**Pipeline definitions** (Preview) are read and checked on your PC and don't call Fabric. Their tests use hand-built definitions in Microsoft's documented formats; reading real exports from a tenant hasn't been tried yet ([pipeline-definitions.md](orchestration/pipeline-definitions.md)). The **activity compatibility registry** (Preview) is tested the same way; no entry has been compared against Fabric ([activity-registry.md](orchestration/activity-registry.md)). **Local pipeline runs** (Preview) run control flow and child pipelines on your PC with mocks for data activities; they follow Data Factory's documentation and haven't been compared with Fabric runs ([local-runs.md](orchestration/local-runs.md)).

The headless agent hasn't run against a live tenant either ([agent.md](agent.md#verify-on-your-tenant)). Promote to Test and Prod creates lakehouses and writes through the same calls as the features above; try it on a non-production workspace first.

When a Preview feature has been tried on a tenant, its tag is removed. If you try one, say how it went in the [Ideas forum](ideas.md) or a bug report.
