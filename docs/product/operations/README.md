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

[workstreams.json](workstreams.json) is the machine-readable state: for each workstream its active story, the next two ready stories, more backlog, dependencies, blockers, the last completed work and the next action. A story is ready only when nothing it needs is waiting on Chris. A queue slot names a backlog story (`"story": "PB-031"`), or, for a story that has no PB id yet, its issue and title. `python docs/product/operations/tools/queues.py all` renders the table below and checks that each slot names a real story and that no story sits in two queues; the Docs workflow fails on those errors. A workstream short of an active or two ready stories is reported, not failed: the weekly report lists it and the lead or the orchestrator refills it.

Leads top up their own ready stories when they drop below two: no-go, no-mockup, no-spend work only, as an issue in the same format and labels, checked against main first. Work that needs a mockup, Chris's go or capacity spend goes to the orchestrator to batch for Chris. Leads keep each story's `status:` label current (`active` when started, `review` when its PR is open), so the weekly report's counts are right.

**Work in progress.** Review and CI, not building, are the bottleneck. A workstream keeps at most four PRs waiting for a quality or security verdict (a PR with a PASS that only waits to merge doesn't count); past that, it works review findings before opening another. Review fixes go up as one push per PR, stacks are rebased only when their base merges, and a PR is stacked on another only when both change the same code.

<!-- BEGIN GENERATED: queues -->
| Workstream | Lead thread | Active | Ready next | Blockers | Next action |
| --- | --- | --- | --- | --- | --- |
| Local Execution | Local data engine and OneLake publishing | PB-015 Write semantics for MERGE, UPDATE and DELETE on this PC ([#848](https://github.com/KratosDataGod/KratosForge/issues/848)) | Lookup and Get Metadata stop at their policy timeout and on Cancel ([#1368](https://github.com/KratosDataGod/KratosForge/issues/1368))<br>An Until timeout and an Execute Pipeline's policy timeout stop its child pipeline ([#1370](https://github.com/KratosDataGod/KratosForge/issues/1370)) | PB-015 live Prove run waits on Chris's yes (card in the orchestrator thread) | Keep PB-015 moving; build #1368 and #1370 in parallel, and file the next hardening stories. |
| Microsoft Fabric | Build full warehouse development experience | Every Fabric fake refuses what the live runs showed the real API refuses ([#1178](https://github.com/KratosDataGod/KratosForge/issues/1178)) | Platform setup: Fabric refuses to create UDF_Platform ("Functions is null or empty") ([#1165](https://github.com/KratosDataGod/KratosForge/issues/1165))<br>Key Vault secret reads in local notebooks (getSecret, dbutils secrets list) say what's wrong in one line ([#1366](https://github.com/KratosDataGod/KratosForge/issues/1366))<br>The agent's mailbox listing errors are one line ([#1367](https://github.com/KratosDataGod/KratosForge/issues/1367)) | PB-002, PB-003, PB-052 and PB-049 live runs wait on Chris's yes for Trial capacity in kf_tenant_test; harnesses are built against fakes (#884, #919, #931)<br>#799 staging cleaner held<br>#869 PB-048 part 3 (#873, schedules while signed out) waits on Chris: password or S4U, plus a Windows check<br>PB-049 (#920), PB-052 (#909) and PB-003 (#887): only the live runs remain, each needs Chris's yes<br>#1166 live 401/403 check and Run B of #849 wait on Chris's card | Land #1166 and #1246; file the next Fabric hardening stories. |
| Databricks and Snowflake | Fabric and Databricks unified development | [DBX-FIDELITY-2] zip_with, map_filter, transform_keys, transform_values and aggregate's finish run on this PC ([#1371](https://github.com/KratosDataGod/KratosForge/issues/1371)) | [FS-HARDEN-2] Fabric Spark SQL previews never pad, cut or merge columns ([#1369](https://github.com/KratosDataGod/KratosForge/issues/1369))<br>[SF-FIDELITY-5] Snowflake TYPEOF gives Snowflake's VARIANT type names on this PC ([#1372](https://github.com/KratosDataGod/KratosForge/issues/1372)) | Snowflake SF-01 sign-in and SF-07 engine wait on Chris's D4 decision<br>Databricks live runs need a workspace and Chris's yes | #1371, #1369 and #1372 in parallel. |
| Enterprise Services | Enterprise governance foundation | none |  | PB-052 tenant verification needs Chris's yes<br>Workload pipelines mockup waits on Chris<br>PB-062 support bundle slice 1 merged (#1177); the Settings button (#1172) waits on the Save support bundle mockup card | Continue the audit-trail pass (access reviews, role changes, support bundle) and file real gaps as stories. |
| Developer Experience | UX and simplicity oversight | Accessibility audit covers Preview screens and common dialogs ([#1284](https://github.com/KratosDataGod/KratosForge/issues/1284)) | Entity compare, last release and roll back tested in a real browser ([#1373](https://github.com/KratosDataGod/KratosForge/issues/1373))<br>Schema drift, quarantine replay and freshness promises tested in a real browser ([#1374](https://github.com/KratosDataGod/KratosForge/issues/1374))<br>Notebook Clean view, Query profile and the warehouse Plan view tested in a real browser ([#1375](https://github.com/KratosDataGod/KratosForge/issues/1375)) | none | #1373, #1374 and #1375 in parallel; #1284's last screen after #1184. |
| Platform Engineering | Codebase audit and hardening | Engine: DuckDB 2.0 readiness, so the engine suite passes on 2.0 before the pin moves ([#1341](https://github.com/KratosDataGod/KratosForge/issues/1341)) |  | PB-001 code signing waits on Microsoft identity validation<br>#744 HD-030 waits on Chris's fail-closed decision<br>Rest of PB-072 needs a Stop design mockup<br>PB-061 (#960) waits on Chris's policy-keys card; HD-057 card with Chris | #1341 by cause group; file other platform hardening. |
<!-- END GENERATED: queues -->

### Side programs

A side program is a separate thread's work that Chris approved under one workstream, such as Learn or Power BI Embed Studio. It has its own cap on PRs waiting for review, so it neither waits behind the workstream's four nor takes their slots. Its stories are issues labelled with the workstream and a status. They are listed under `side_programs` in [workstreams.json](workstreams.json), and the Docs workflow checks that each one names a real workstream, a cap of 1 to 4, and stories with an issue, a title and a status. More stories at review than the cap is reported, not failed, like a short queue. The orchestrator adds a program when Chris gives it a go and updates its statuses at the weekly review.

<!-- BEGIN GENERATED: side-programs -->
| Side program | Workstream | Thread | Review cap | Stories |
| --- | --- | --- | --- | --- |
| Power BI Embed Studio | Developer Experience | Build Power BI embedded development experience | 2 | active: PBE-03 Power BI embed host frame: pbiembed scheme, its own policy, main-window pop-up rule ([#1127](https://github.com/KratosDataGod/KratosForge/issues/1127))<br>ready: PBE-05 Mock Power BI frame for browser tests ([#1129](https://github.com/KratosDataGod/KratosForge/issues/1129))<br>blocked: PBE-06 Embed Studio screen: open a Power BI report (Preview) ([#1130](https://github.com/KratosDataGod/KratosForge/issues/1130)) (Waits on the Embed Studio mockup) |
<!-- END GENERATED: side-programs -->

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
