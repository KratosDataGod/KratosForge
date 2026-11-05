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
| 4. Enterprise Services | Metadata, governance, business keys, shared data assets, data quality, lineage, central optimization | Finish governance follow-ups | Metadata-driven data mesh, Workload management |
| 5. Developer Experience | UI and UX, debugging, testing, docs, project management, diagnostics | UX and simplicity oversight | VS Code integration, Action-first experience, Run continuous product management (backlog, inventory, docs audits) |
| 6. Platform Engineering | Architecture, security, tests, CI/CD, dependencies, code review, releases | Codebase audit and hardening | Release management, Engineering quality review, Bug fix and security QC, Enterprise readiness |

## Work queues

[workstreams.json](workstreams.json) is the machine-readable state: for each workstream its active story, the next two ready stories, more backlog, dependencies, blockers, the last completed work and the next action. A story is ready only when nothing it needs is waiting on Chris. A queue slot names a backlog story (`"story": "PB-031"`), or, for a story that has no PB id yet, its issue and title. `python docs/product/operations/tools/queues.py all` renders the table below and checks that each slot names a real story and that no story sits in two queues; the Docs workflow fails on those errors. A workstream short of an active or two ready stories is reported, not failed: the weekly report lists it and the lead or the orchestrator refills it.

Leads top up their own ready stories when they drop below two: no-go, no-mockup, no-spend work only, as an issue in the same format and labels, checked against main first. Work that needs a mockup, Chris's go or capacity spend goes to the orchestrator to batch for Chris. Leads keep each story's `status:` label current (`active` when started, `review` when its PR is open), so the weekly report's counts are right.

**Open issues.** Since 2026-11-02 fewer than five issues stay open, and only for work someone is doing now. A queued story's issue stays closed until its lead starts it: the lead reopens it, or links the PR to it. Its place in the queue is kept here and in [workstreams.json](workstreams.json).

**Work in progress.** Review and CI, not building, are the bottleneck. A workstream keeps at most four PRs waiting for a quality or security verdict (a PR with a PASS that only waits to merge doesn't count); past that, it works review findings before opening another. Review fixes go up as one push per PR, stacks are rebased only when their base merges, and a PR is stacked on another only when both change the same code.

<!-- BEGIN GENERATED: queues -->
| Workstream | Lead thread | Active | Ready next | Blockers | Next action |
| --- | --- | --- | --- | --- | --- |
| Local Execution | Local data engine and OneLake publishing | [EA-029] CI runs the start-up, notebook-start and SQL benchmark and flags a regression ([#1664](https://github.com/KratosDataGod/KratosForge/issues/1664)) | [EA-001 follow-up] Every append path has a test that a value-changing append stops ([#1665](https://github.com/KratosDataGod/KratosForge/issues/1665)) | PB-015 live Prove run waits on Chris's yes (state kept in the backlog; its issue is closed until approved) | #1664 then #1665; file the next engine hardening stories. |
| Microsoft Fabric | Build full warehouse development experience | [T-SQL] Every characterized T-SQL gap either runs or is refused by name ([#1666](https://github.com/KratosDataGod/KratosForge/issues/1666)) | [EA-028] Hand-written retry loops in Fabric paths move to retrying.py ([#1667](https://github.com/KratosDataGod/KratosForge/issues/1667)) | PB-002, PB-003, PB-049 and PB-052 live runs wait on Chris's yes (issues closed until approved)<br>#799 staging cleaner held | #1666 by function family; #1667 one module per PR. |
| Databricks and Snowflake | Fabric and Databricks unified development | [DBX-COVERAGE] A generated Spark function coverage list says, for every built-in, runs, refused or differs ([#1668](https://github.com/KratosDataGod/KratosForge/issues/1668)) | [EA-013 follow-up] spark.read.json and spark.read.parquet honour Spark's options or refuse them ([#1669](https://github.com/KratosDataGod/KratosForge/issues/1669)) | Databricks live runs need a workspace and Chris's yes<br>Snowflake sign-in and engine wait on Chris's D4 decision | #1668 and #1669 in parallel. |
| Enterprise Services | Finish governance follow-ups | none |  | MSI or MSIX packaging is a new capability: decision card with Chris (MSI recommended)<br>Email notices for requests: decision card with Chris on how to send without an app registration (Power Automate flow recommended)<br>#1679 Save support bundle: passed review, waits on Chris's Windows click test | Shortcut follow-ups in review (#1719 CSV export, #1724 approvals in Dev, Test and Prod); then email notices and MSI once Chris picks. |
| Developer Experience | UX and simplicity oversight | Keyboard-only pass over every page as an automated browser check ([#1672](https://github.com/KratosDataGod/KratosForge/issues/1672)) | Axe audit: a guard test fails when a page or dialog has no accessibility case ([#1673](https://github.com/KratosDataGod/KratosForge/issues/1673)) | EA-030 Narrator pass needs a Windows PC<br>#1136 Embed Studio screen held for Chris's live report test | #1672 then #1673; land the Learn PRs. |
| Platform Engineering | Codebase audit and hardening | [EA-028] SQLite stores open through one helper that handles a corrupt file ([#1674](https://github.com/KratosDataGod/KratosForge/issues/1674)) | [EA-027] Ruff BLE rules on (second slice), with each reviewed exception listed ([#1675](https://github.com/KratosDataGod/KratosForge/issues/1675)) | PB-001 / EA-009 code signing waits on Microsoft identity validation (Chris)<br>#744 HD-030 waits on Chris's fail-closed decision | #1674 one store per PR; #1675. |
<!-- END GENERATED: queues -->

### Side programs

A side program is a separate thread's work that Chris approved under one workstream, such as Learn or Power BI Embed Studio. It has its own cap on PRs waiting for review, so it neither waits behind the workstream's four nor takes their slots. Its stories are issues labelled with the workstream and a status. They are listed under `side_programs` in [workstreams.json](workstreams.json), and the Docs workflow checks that each one names a real workstream, a cap of 1 to 4, and stories with an issue, a title and a status. More stories at review than the cap is reported, not failed, like a short queue. The orchestrator adds a program when Chris gives it a go and updates its statuses at the weekly review.

<!-- BEGIN GENERATED: side-programs -->
| Side program | Workstream | Thread | Review cap | Stories |
| --- | --- | --- | --- | --- |
<!-- END GENERATED: side-programs -->

## One backlog

- **Stories live in GitHub Issues**, labelled `story`, a priority (`P0` to `P4`), a workstream (`ws:local-exec`, `ws:fabric`, `ws:dbx-snow`, `ws:enterprise`, `ws:devex`, `ws:platform`), a pillar and a status (`status:active`, `status:ready`, `status:blocked`, `status:review`). `queues.py body <story>` writes the issue body: workstream, platform, business value, requirements, acceptance criteria, dependencies, priority, agent, tests required and definition of done.
- **Scoring and inventory stay in the repo**: [PRODUCT_BACKLOG.json](../PRODUCT_BACKLOG.json) and [FEATURE_INVENTORY.json](../FEATURE_INVENTORY.json), maintained by the product check. A story's issue names its PB id; a new story gets a PB id at the next product check.
- **Never in issues:** security findings and anything that could help an attacker. The repository is public. Those stay private with Bug fix and security QC until fixed.
- PRs say `Fixes #<issue>` so merging closes the story.

## Weekly review (Mondays)

1. **About 15:17 UTC, automatic:** the [Weekly review workflow](https://github.com/KratosDataGod/KratosForge/actions/workflows/weekly-review.yml) runs `queues.py report` and opens an issue labelled `weekly-review`: PRs merged and opened, main CI, open PRs, story issues by status, the queue table, the queue check and the token note.
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
