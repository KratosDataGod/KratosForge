# Development operations

How the KratosForge development organization runs: six workstreams, one backlog in GitHub Issues, work queues, a weekly review, quality gates and how capacity is reported. Set up on 2026-10-28 from Chris's orchestrator brief. The [12-week roadmap](roadmap.md) is updated at every weekly review.

## The loop

Discover, prioritize, assign, build, test, review, merge, release, repeat. The orchestrator thread ("Orchestrate KratosForge product and development") owns the queues and the weekly review. Leads own delivery in their workstream. Release management is the only thread that merges.

## Six workstreams

Each workstream has one lead thread, which owns its queue and hands stories to the member threads. Shared files get one owner; when two threads need the same component, the lead decides who changes it.

| Workstream | Scope | Lead thread | Member threads |
| --- | --- | --- | --- |
| 1. Local Execution | DuckDB, Polars, Python, Spark API, Delta Lake, engines, hardware use, performance | Local data engine and OneLake publishing | Local TPC-H run slowdown (performance and cost evidence) |
| 2. Microsoft Fabric | Notebooks, pipelines, lakehouses, warehouses, semantic models, Direct Lake, OneLake, deployment | Build full warehouse development experience | Data Factory, Local Fabric pipeline execution, Git-native development, Best Practice Analyzer, TMDL and OSI conversion, Staging cleaner, Pin sync |
| 3. Databricks and Snowflake | Platform adapters, notebook compatibility, SQL, Snowpark, Spark, deployment | Fabric and Databricks unified development | Snowflake local-first development (research first) |
| 4. Enterprise Services | Metadata, governance, business keys, shared data assets, data quality, lineage, central optimization | Enterprise governance foundation | Metadata-driven data mesh, Workload management |
| 5. Developer Experience | UI and UX, debugging, testing, docs, project management, diagnostics | UX and simplicity oversight | VS Code integration, Action-first experience, Run continuous product management (backlog, inventory, docs audits) |
| 6. Platform Engineering | Architecture, security, tests, CI/CD, dependencies, code review, releases | Codebase audit and hardening | Release management, Engineering quality review, Bug fix and security QC, Enterprise readiness |

## Work queues

[workstreams.json](workstreams.json) is the machine-readable state: for each workstream its active story, the next two ready stories, more backlog, dependencies, blockers, the last completed work and the next action. A story is ready only when nothing it needs is waiting on Chris. `python docs/product/operations/tools/queues.py all` renders the table below and checks that every workstream has an active story and two ready ones, that each names a real story and that no story sits in two queues. The Docs workflow runs the check on every pull request.

<!-- BEGIN GENERATED: queues -->
| Workstream | Lead thread | Active | Ready next | Blockers | Next action |
| --- | --- | --- | --- | --- | --- |
| Local Execution | Local data engine and OneLake publishing | PB-094 Measure the process kernel, not the engine ([#846](https://github.com/KratosDataGod/KratosForge/issues/846)) | PB-068 Key lookups without reading whole key tables ([#847](https://github.com/KratosDataGod/KratosForge/issues/847))<br>PB-015 Write semantics for MERGE, UPDATE and DELETE on this PC ([#848](https://github.com/KratosDataGod/KratosForge/issues/848)) | none | Measure CPU and memory from the kernel process, not the engine (PB-094). |
| Microsoft Fabric | Build full warehouse development experience | PB-002 Tenant smoke test of ten core flows ([#849](https://github.com/KratosDataGod/KratosForge/issues/849)) | PB-043 Cancel a Fabric run from the app ([#850](https://github.com/KratosDataGod/KratosForge/issues/850))<br>PB-049 Run the pipeline mailbox once on a tenant ([#920](https://github.com/KratosDataGod/KratosForge/issues/920)) | PB-002 live run waits on Chris's yes for Trial capacity; the plan and harness are prepared first<br>#755 WH-16 draft waits on Chris<br>#799 staging cleaner held | Prepare the ten-flow tenant smoke test (PB-002) so it runs the moment Chris says yes. |
| Databricks and Snowflake | Fabric and Databricks unified development | PB-116 Fabric Spark interactive sessions (Livy) as a Preview engine ([#852](https://github.com/KratosDataGod/KratosForge/issues/852)) | PB-117 Snowflake pillar: audit, ADRs and parity matrix ([#853](https://github.com/KratosDataGod/KratosForge/issues/853))<br>PB-118 Databricks notebook conversion says what it could not carry over ([#854](https://github.com/KratosDataGod/KratosForge/issues/854)) | Engine picker mockup waits on Chris | Open the Fabric Spark (Livy) draft PR from the saved patch, restarted from main. |
| Enterprise Services | Enterprise governance foundation | PB-057 Offline policy validation ([#855](https://github.com/KratosDataGod/KratosForge/issues/855)) | PB-083 Endorsed items scope in the catalog ([#856](https://github.com/KratosDataGod/KratosForge/issues/856))<br>PB-053 Scheduled shortcut health check with alerts ([#857](https://github.com/KratosDataGod/KratosForge/issues/857)) | PB-052 tenant verification needs Chris's yes<br>Workload pipelines mockup waits on Chris | Offline policy validation (PB-057), engine and CLI first, no new screen. |
| Developer Experience | UX and simplicity oversight | PB-078 Preview tags match the docs ([#858](https://github.com/KratosDataGod/KratosForge/issues/858)) | PB-077 User and agent docs match the code ([#859](https://github.com/KratosDataGod/KratosForge/issues/859))<br>PB-070 UI tests for the notebook editor and command bar ([#860](https://github.com/KratosDataGod/KratosForge/issues/860)) | none | Every feature the docs call Preview gets a Preview id, with a UI test (PB-078). |
| Platform Engineering | Codebase audit and hardening | PB-074 Release pipeline hardening ([#861](https://github.com/KratosDataGod/KratosForge/issues/861)) | PB-072 Long engine calls stop blocking the app ([#862](https://github.com/KratosDataGod/KratosForge/issues/862))<br>PB-051 Fabric REST client resilience ([#863](https://github.com/KratosDataGod/KratosForge/issues/863)) | PB-001 code signing waits on Microsoft identity validation<br>#744 HD-030 waits on Chris's fail-closed decision | Make the release job wait for the audit job (PB-074 remainder). |
<!-- END GENERATED: queues -->

## One backlog

- **Stories live in GitHub Issues**, labelled `story`, a priority (`P0` to `P4`), a workstream (`ws:local-exec`, `ws:fabric`, `ws:dbx-snow`, `ws:enterprise`, `ws:devex`, `ws:platform`), a pillar and a status (`status:active`, `status:ready`, `status:blocked`, `status:review`). `queues.py body <story>` writes the issue body: workstream, platform, business value, requirements, acceptance criteria, dependencies, priority, agent, tests required and definition of done.
- **Scoring and inventory stay in the repo**: [PRODUCT_BACKLOG.json](../PRODUCT_BACKLOG.json) and [FEATURE_INVENTORY.json](../FEATURE_INVENTORY.json), maintained by the product check. A story's issue names its PB id; a new story gets a PB id at the next product check.
- **Never in issues:** security findings and anything that could help an attacker. The repository is public. Those stay private with Bug fix and security QC until fixed.
- PRs say `Fixes #<issue>` so merging closes the story.

## Weekly review (Mondays)

1. **16:00 UTC, automatic:** the [Weekly review workflow](https://github.com/KratosDataGod/KratosForge/actions/workflows/weekly-review.yml) runs `queues.py report` and opens an issue labelled `weekly-review`: PRs merged and opened, main CI, open PRs, story issues by status, the queue table, the queue check and the token note.
2. **17:00 UTC, scheduled Claude task:** the orchestrator thread reads that issue and does the review: audit merged work, reconcile feature status against the code, find defects and debt, look for Fabric, Databricks and Snowflake opportunities, reprioritize, write new stories, refill every queue, update the [roadmap](roadmap.md) and post the next seven days of assignments. It comments the recommendations and the decisions for Chris on the issue and messages each lead.

The twice-daily product check (11:42 and 23:42 UTC) keeps reconciling the inventory between reviews. Docs are audited every 50 merged PRs.

## Handoffs and quality gates

Every finished story ends with a handoff note on its PR: summary, files changed, tests run with results, known limits, remaining issues and follow-ups. A story is done only when:

- CI passes on the PR head (engine tests, UI tests, browser tests, cargo test, audit).
- Its tests fail without the change and cover the acceptance criteria.
- Engineering quality review gives a PASS on the exact head; a push after a PASS needs a new check.
- Bug fix and security QC reads it when it touches inbound surfaces, generated scripts, destructive actions, credentials, or process and ctypes code. Workload changes get Workload management's sign-off.
- Docs and the feature inventory are updated.
- Release management merges it. Merged moves the backlog item to In review; only Chris accepts.

New user-facing features need a mockup and Chris's typed go. Anything that spends Fabric capacity or cloud money needs his typed yes. Untested features are tagged Preview.

## Capacity and token use

Claude threads and GitHub can't read the Claude subscription's allocation, use or reset time, so no utilization percentage is reported. The weekly report shows proxies instead: story issues by status, workstreams short of ready work, PRs merged and main CI. When usage caps are tight, Release management and quality re-confirms go first, then small stories that can finish before the reset. A thread that stops mid-story leaves its branch, PR and next step on the story's issue so any thread can resume.
