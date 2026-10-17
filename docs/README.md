# Fabric Desktop by KratosBI documentation

Fabric Desktop by KratosBI runs Microsoft Fabric data engineering work on your own PC (notebooks, Spark SQL, and a metadata-driven medallion framework) and writes the results straight into OneLake. Start with the guide for what you're doing.

## Start here

| I want to | Read |
| --- | --- |
| Install the app on my PC | [Install](install.md) |
| Back up my PC's notebooks and settings | [Back up and restore](backup.md) |
| Roll it out to many PCs | [Enterprise install](enterprise-install.md) |
| Set it up in Azure and Fabric for my organization | [Configure Azure and Fabric](azure-setup.md), then [Permissions](permissions.md) |
| Learn what every page does | [User guide](user-guide.md) |
| Write and run notebooks and SQL on my PC | [Notebooks and SQL](notebooks.md) |
| Compare a run on my PC with the same run in Fabric, or benchmark both | [Side by side](side-by-side.md) |
| Know where a CU or cost figure comes from and how far to trust it | [Capacity and cost evidence](finops/cost-evidence.md) |
| Build Bronze, Silver and Gold from config | [Metadata framework guide](metadata-guide.md) |
| Set it up for several teams | [Working across teams](teams.md), [The platform](platform.md), [Data mesh](data-mesh.md) and [Governance policies](governance.md) |
| Watch and manage every OneLake shortcut: health, access, changes, requests | [Shortcut command center](shortcuts.md) |
| Fix an error | [Troubleshooting](troubleshooting.md) |
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
| [security.md](security.md) | Admins, security reviewers | The threat model: what is protected, trust boundaries, how updates are checked, and what is still open |
| [user-guide.md](user-guide.md) | Everyone | Every page: Home, Create, Workspaces, OneLake (catalog, shortcuts, access), Data mesh, Fabric, Monitor, Settings, the command bar and the bell |
| [notebooks.md](notebooks.md) | Engineers, analysts | Notebooks on your PC, Fabric code that works here, publishing, Run in Fabric, schedules, SQL query |
| [side-by-side.md](side-by-side.md) | Engineers, analysts | Running a notebook on this PC and in Fabric side by side, how results are checked, and the TPC-H benchmark |
| [finops/cost-evidence.md](finops/cost-evidence.md) | Engineers, platform owners | Monitor > Cost figures: measured, estimated, scenario and unknown, what is counted, units, and the measurement schema |
| [prove/how-proofs-work.md](prove/how-proofs-work.md) | Engineers, analysts | Proving a notebook: pinned inputs, sandboxed outputs, how results are verified, the scorecard and receipts (Preview) |
| [metadata-guide.md](metadata-guide.md) | Engineers | The metadata framework step by step, from a new project to Prod, and a reference for every setting |
| [platform.md](platform.md) | Platform owners | The central platform: one key system, one landing zone, the catalog; platform admins, requests, the Platform page; creating, joining, moving keys |
| [governance.md](governance.md) | Platform owners, team leads | Governance policies (Preview): the policy registry, drafts and versions, enterprise to entity scopes, mandatory policies, governance.toml, who can change what |
| [data-mesh.md](data-mesh.md) | Team leads, engineers | Domains, workspace designations, data products, links between teams, catalog text |
| [shortcuts.md](shortcuts.md) | Engineers, platform owners | The shortcut command center: health checks, who can read a shortcut, shortcut rules, change history with undo, repointing, requests |
| [source-control.md](source-control.md) | Engineers, analysts | Source control (Preview): clone, diffs, branches, commit, pull and push, the secret check, what it doesn't do yet |
| [teams.md](teams.md) | Team leads, platform owners | Roles, Git workflow, environments and workspaces, shared keys, feedback at scale |
| [troubleshooting.md](troubleshooting.md) | Everyone | Each error message, what it means and what to do |
| [live-testing.md](live-testing.md) | Admins, maintainers | The live test: checking sign-in, the Fabric API, OneLake and Delta against a workspace set aside for tests, with cleanup and a report |
| [ideas.md](ideas.md) | Everyone | Posting ideas, voting, and how ideas are prioritized and shipped |
| [xray-fixes.md](xray-fixes.md) | Engineers | Each Estate X-ray finding and how to fix it |
| [agent.md](agent.md) | Platform owners | The headless agent on Windows or Linux servers |
| [architecture.md](architecture.md) | Contributors | How the app, the engine and OneLake fit together |
| [ai/README.md](ai/README.md) | Everyone | Connecting your own AI tool (Claude, Codex, Copilot, Cursor, any MCP client): setup for each, [workflows](ai/workflows.md), [instructions to paste](ai/agent-instructions.md) |
| [architecture/ai-integration.md](architecture/ai-integration.md) | Contributors, admins | External AI tools through MCP and a local API, with no AI in the app: architecture, permissions, security ([backlog](product/ai-integration-backlog.md), [decisions](product/ai-integration-decisions.md), [security](product/ai-integration-security.md), [compatibility](product/ai-integration-compatibility.md), [AI tool test checklist](product/ai-integration-client-checklist.md)) |
| [ui-guidelines.md](ui-guidelines.md) | Contributors | Where new features go in the UI |
| [enterprise-roadmap.md](enterprise-roadmap.md) | Platform owners | Milestones for running at enterprise scale |
| [scale-and-operations-plan.md](scale-and-operations-plan.md) | Platform owners | Staged plan for large tables, ingestion, releases, pipelines, lineage and monitoring |
| [git-integration-plan.md](git-integration-plan.md) | Contributors | The Git integration program: assessment, Phase 1 (Git foundation, done), Fabric workspace Git, deployment and extensions |
| [prove/plan.md](prove/plan.md) | Contributors | The Prove this notebook program: what exists, the stages, and all 100 backlog items ([continuation note](prove/continuation.md)) |
| [workloads/plan.md](workloads/plan.md) | Contributors, platform owners | The workload operations program: managing workloads on PCs, agents and Fabric; stages and all 100 backlog items ([continuation note](workloads/continuation.md)) |
| [finops/plan.md](finops/plan.md) | Contributors, platform owners | The administration and FinOps program (FINOPS-001 to 005): audit, overlaps, PRs |
| [product/enterprise-gap-analysis.md](product/enterprise-gap-analysis.md) | Platform owners, contributors | The enterprise product program: what exists for each capability FD-101 to FD-140, what's missing and who owns it ([backlog](product/enterprise-backlog.md), [decisions](product/implementation-decisions.md)) |
| [product/release-readiness.md](product/release-readiness.md) | Platform owners | What must be true before an enterprise pilot, the capability matrix and the pilot checklist |
| [product/PRODUCT_MANAGEMENT_PROCESS.md](product/PRODUCT_MANAGEMENT_PROCESS.md) | Product owner, contributors | How work is chosen, ranked, built and accepted: the [feature inventory](product/FEATURE_INVENTORY.md), [backlog](product/PRODUCT_BACKLOG.md), [roadmap](product/PRIORITIZED_ROADMAP.md), [PR-ready specs](product/pr-ready/README.md), [implementation review](product/IMPLEMENTATION_REVIEW_PROCESS.md) and [competitive matrix](product/COMPETITIVE_CAPABILITY_MATRIX.md) |
| [why-fabric-desktop.md](why-fabric-desktop.md) | Everyone | Why it exists: the Power BI model for data engineering |
| [CONTRIBUTING.md](https://github.com/KratosDataGod/fabric-desktop/blob/main/CONTRIBUTING.md) | Contributors | Setting up, rules for changes, pull requests |

## What is tested and what isn't

Fabric Desktop by KratosBI is new, and it's honest about what has been proven.

**Tested on every build**, by automated tests on Windows and Linux, against local Delta tables:

- Notebooks, the `lakehouse` module, `spark` and `notebookutils` on the PC
- Spark SQL translation and the SQL query engine
- The metadata framework: every load mode, checks and quarantine, surrogate keys, the four gates, compare, schema drift, replay, freshness, profiling and tests
- Startup, updates and settings
- The headless agent's queue, retries and leases

**Not yet tried on a live tenant:** sign-in through Microsoft's Fabric sign-in with no app registration, and Windows sign-in (version 0.15.0). Until it is, the maintainer's tenant results below are with an own app registration.

**Used on the maintainer's own Fabric tenant (with an own app registration):** sign-in and admin consent, listing workspaces and items, the OneLake catalog, the embedded Fabric pages (Fabric and the Monitoring hub) and their saved sign-in, and writing tables to OneLake from the PC. That is one tenant; yours may differ in settings and permissions.

**Built to Fabric's documented APIs but not yet tried on a live tenant.** These are tagged **Preview** in the app and can be hidden in **Settings > Preview features**:

| Preview feature | Where |
| --- | --- |
| Run in Fabric (notebooks and environments) | Notebook ribbon, Data mesh > Environments |
| Schedules (on this PC and in Fabric) | Notebook ribbon, Data mesh, Monitor |
| Side by side (this PC vs Fabric) and the TPC-H benchmark | Notebook ribbon |
| Prove this notebook (isolated PC vs Fabric runs, verification, receipts) | Notebook ribbon |
| New notebook from a template | Create |
| Run notebooks in their own process | Settings > Preview features |
| Cell dependencies and Run stale cells | Notebook ribbon (View and Run tabs) |
| Variable history, watches and hidden-state warnings | Notebook Variables pane (View tab) |
| Notebook cell time and memory limits | Settings > Preview features (with Run notebooks in their own process on) |
| Cost | Monitor |
| OneLake shortcuts | OneLake |
| OneLake access | OneLake |
| Table history and restore | Entity page |
| Table health and Optimize | Data mesh > Quality |
| Semantic model | Data mesh > Deploy |
| Tests | Data mesh > Quality |
| Estate X-ray | Home |
| Item preview | Workspaces |
| Platform (admins, landing zone, business keys, requests) | Platform, Settings, Data mesh > Surrogate keys |
| Data products for everyone | OneLake > Data products |
| Data mesh (domains from Fabric, products, links, glossary, About, the map on Home) | Data mesh > Domains, Platform > Domains, Home, entity page |
| Workloads (inventory, health, ownership, the workload page, Your workloads) | Monitor > Workloads, Home |

**Source control** (Preview) is Git on your PC and doesn't call Fabric. Its tests run real Git against local repositories on every build; it hasn't yet been tried with Git Credential Manager and SSH on many PCs and servers ([source-control.md](source-control.md)).

The headless agent hasn't run against a live tenant either ([agent.md](agent.md#verify-on-your-tenant)). Promote to Test and Prod creates lakehouses and writes through the same calls as the features above; try it on a non-production workspace first.

When a Preview feature has been tried on a tenant, its tag is removed. If you try one, say how it went in the [Ideas forum](ideas.md) or a bug report.
