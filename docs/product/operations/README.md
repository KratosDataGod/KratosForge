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
| Local Execution | Local data engine and OneLake publishing | PB-015 Write semantics for MERGE, UPDATE and DELETE on this PC ([#848](https://github.com/KratosDataGod/KratosForge/issues/848)) | Notebook table writes give plain messages and Fabric-safe types for float16, time, uint64 and all-NULL columns ([#953](https://github.com/KratosDataGod/KratosForge/issues/953))<br>Metadata loads give plain messages for TIME, INTERVAL and float16 columns ([#954](https://github.com/KratosDataGod/KratosForge/issues/954)) | #933 MERGE on this PC waits on Chris's go | UPDATE and DELETE on this PC (PB-015 second PR) once #933 settles; #953 and #954 are built locally. |
| Microsoft Fabric | Build full warehouse development experience | Hardening: a warehouse deploy change runs only if its object is still as the preview saw it ([#947](https://github.com/KratosDataGod/KratosForge/issues/947)) | PB-043 Cancel a Fabric run from the app ([#850](https://github.com/KratosDataGod/KratosForge/issues/850))<br>PB-049 Run the pipeline mailbox once on a tenant ([#920](https://github.com/KratosDataGod/KratosForge/issues/920)) | PB-002, PB-003, PB-052 and PB-049 live runs wait on Chris's yes for Trial capacity in kf_tenant_test; harnesses are built against fakes (#884, #919, #931)<br>#755 WH-16 draft waits on Chris<br>#799 staging cleaner held | Work review findings first (eleven drafts at review), then #947; no further stacked drafts until the base PRs merge. |
| Databricks and Snowflake | Fabric and Databricks unified development | Databricks refusals say what to check for the API that refused (KF-DBX-ERRORS) ([#1102](https://github.com/KratosDataGod/KratosForge/issues/1102)) | Databricks SQL function parity table for DuckDB on this PC (KF-DBX-SQLPARITY) ([#1103](https://github.com/KratosDataGod/KratosForge/issues/1103)) | Databricks live runs need a workspace and Chris's yes | Per-API refusal hints (#1102), then the SQL parity table (#1103). The live Databricks run waits on Chris's yes; a discovery RPC waits until the picker UI needs it. |
| Enterprise Services | Enterprise governance foundation | PB-031 Schema evolution without full rewrites ([#904](https://github.com/KratosDataGod/KratosForge/issues/904)) | Platform client: a non-JSON or 5xx answer from UDF_Platform gives a clear, typed error ([#935](https://github.com/KratosDataGod/KratosForge/issues/935))<br>Test: every UDF_Platform function takes the same parameters as the platform operation it calls ([#936](https://github.com/KratosDataGod/KratosForge/issues/936)) | PB-052 tenant verification needs Chris's yes<br>Workload pipelines mockup waits on Chris | Open #904 when CI clears, with the behaviour change at the top of the PR. |
| Developer Experience | UX and simplicity oversight | KratosForge stays usable in Windows High Contrast ([#955](https://github.com/KratosDataGod/KratosForge/issues/955)) | AI integration architecture page matches what shipped ([#948](https://github.com/KratosDataGod/KratosForge/issues/948)) | none | #955 locally; #948 is built and opens when CI clears. |
| Platform Engineering | Codebase audit and hardening | PB-061 Missing policy keys ([#960](https://github.com/KratosDataGod/KratosForge/issues/960)) | Test: the network host list stays in step with the code (PB-010 follow-up, after #917) ([#959](https://github.com/KratosDataGod/KratosForge/issues/959)) | PB-001 code signing waits on Microsoft identity validation<br>#744 HD-030 waits on Chris's fail-closed decision<br>Rest of PB-072 needs a Stop design mockup | Hosts-table guard test (#959, #917 has merged); then the next ready story. |
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
