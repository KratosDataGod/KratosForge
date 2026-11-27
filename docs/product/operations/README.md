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
| Local Execution | Local data engine and OneLake publishing | WR-140 Spark DataFrame API: value-check the 24 Not checked rows against Spark 3.5, or refuse them by name (no issue yet) | WR-141 Spark SQL: fix the two wrong answers pinned by strict xfails (sha2 with a decimal bit length, a shift of a shift typed bigint) (no issue yet)<br>WR-142 Spark DataFrame join on column names puts the join columns first, as Spark does (no issue yet) | PB-015 live Prove run waits on Chris's yes (state kept in the backlog; its issue is closed until approved)<br>DuckDB for OneLake writes: route decision card with Chris (DuckDB 2 opt-in recommended); the DuckDB 2 pin move and any local test of the extension wait on it<br>Delta compatibility profile thread: the default-profile decision is with Chris | WR-140, then WR-141 and WR-142. #3190 (pandas_udf numbers) is in review. WR-123 to WR-129 aren't queued: their titles weren't recorded where this review could check them; the orchestrator re-adds them. The DuckDB OneLake route waits on Chris. |
| Microsoft Fabric | Build full warehouse development experience | WR-238 Local T-SQL: a refused statement in a script is named by its place (no issue yet) | WR-241 Local T-SQL: DATETIMEOFFSET and AT TIME ZONE match a Fabric Warehouse or are refused by name (no issue yet)<br>WR-247 Local T-SQL arithmetic errors carry SQL Server's error numbers (no issue yet) | PB-002, PB-003, PB-049 and PB-052 live runs wait on Chris's yes (issues closed until approved)<br>WR-218's live check and Run 11 part 2 wait on Chris's yes (kf_tenant_test only)<br>The T-SQL variables and control-flow interpreter is a new capability: waits on Chris's go<br>#799 staging cleaner held | WR-238 (in review, #3184), then WR-241 and WR-247. WR-240 was dropped (duplicate of #2921). WR-255 and WR-259 are in review (#3188) in the SQL translator thread. WR-237's status isn't confirmed; check docs/quality/mutation-testing.md before re-queueing it. |
| Databricks and Snowflake | Fabric and Databricks unified development | WR-316 Unity Catalog names in Databricks SQL cells on this PC (no issue yet) | WR-318 Snowflake: value-check the 16 Not checked functions against Snowflake's reference, or refuse them by name (no issue yet)<br>WR-319 Databricks SQL: the 10 functions that Differ match Databricks or are refused by name (no issue yet) | Databricks live runs need a workspace and Chris's yes<br>Snowflake live runs need an account and Chris's yes | WR-316 (in review, #3163), then WR-318 and WR-319. Snowflake SF-001 to SF-005 run in the three-platform parity thread. |
| Enterprise Services | Finish governance follow-ups | WR-410 Audit fail-closed coverage: every write that must be audited first is listed and tested (no issue yet) | WR-411 Audit trail: Windows Event Log anchor failures say what happened and never block work silently (no issue yet)<br>WR-412 Privacy: a deletion request leaves a proof of what was deleted, without the data (no issue yet) | MSI or MSIX packaging is a new capability: decision card with Chris (MSI recommended)<br>Email notices for requests: decision card with Chris on how to send without an app registration (Power Automate flow recommended)<br>Save support bundle (#1679) is merged; Chris's Windows click test isn't recorded | WR-410, then WR-411 and WR-412; WR-413 and WR-414 after. |
| Developer Experience | UX and simplicity oversight | WR-130 Notebook kernel lifecycle matches Fabric: start, restart, interrupt and idle stop (no issue yet) | WR-133 Notebook display() and Spark output on this PC look as in Fabric (no issue yet)<br>WR-132 Notebook .ipynb files round-trip without losing cells, outputs or metadata (no issue yet) | EA-030 Narrator pass needs a Windows PC<br>Profile Ranks (#2245) and Home restyle (#2253) drafts wait on Chris's reaction<br>Embed Studio (#1136) is merged as Preview; Chris's live report test isn't recorded | WR-130 in Notebook parity, then WR-133 and WR-132 (UX reviews copy). Docs audits continue every 50 merges. |
| Platform Engineering | Codebase audit and hardening | WR-612 Every strict xfail on main names the story that will fix it (no issue yet) | WR-615 Tests bound by wall-clock time: count work instead where the time isn't the point (no issue yet)<br>WR-616 Story ids stay unique: a ledger of WR ranges per thread, checked by queues.py (no issue yet) | PB-001 / EA-009 code signing waits on Microsoft identity validation (Chris)<br>#744 HD-030 waits on Chris's fail-closed decision | WR-612, then WR-615 and WR-616. Mutation and coverage lanes keep their ledger ranges; the codebase audit adds the next hardening story. |
<!-- END GENERATED: queues -->

### Side programs

A side program is a separate thread's work that Chris approved under one workstream, such as Learn or Power BI Embed Studio. It has its own cap on PRs waiting for review, so it neither waits behind the workstream's four nor takes their slots. Its stories are issues labelled with the workstream and a status. They are listed under `side_programs` in [workstreams.json](workstreams.json), and the Docs workflow checks that each one names a real workstream, a cap of 1 to 4, and stories with an issue, a title and a status. More stories at review than the cap is reported, not failed, like a short queue. The orchestrator adds a program when Chris gives it a go and updates its statuses at the weekly review.

<!-- BEGIN GENERATED: side-programs -->
| Side program | Workstream | Thread | Review cap | Stories |
| --- | --- | --- | --- | --- |
| Semantic engineering (Ossie, Semantic Views, TMDL) | Microsoft Fabric | TMDL and OSI conversion | 2 | ready: OSSIE-007a One TMDL emitter for the Direct Lake model ([#3278](https://github.com/KratosDataGod/KratosForge/issues/3278))<br>ready: OSSIE-012a Semantic fixture corpus per version profile ([#3279](https://github.com/KratosDataGod/KratosForge/issues/3279))<br>blocked: OSSIE-002a New and open Ossie files in the project ([#3280](https://github.com/KratosDataGod/KratosForge/issues/3280))<br>blocked: OSSIE-006d Semantic parity L3, live DAX side ([#3281](https://github.com/KratosDataGod/KratosForge/issues/3281)) |
| Central control plane | Enterprise Services | Central control plane | 2 | review: CP-015 Service registry and inventory engine ([#3282](https://github.com/KratosDataGod/KratosForge/issues/3282)) (PR #3214)<br>blocked: CP-012 First live Create of a KratosForge Environment ([#3283](https://github.com/KratosDataGod/KratosForge/issues/3283))<br>blocked: CP-011 Environment cost and inventory panels on the Environment screen ([#3284](https://github.com/KratosDataGod/KratosForge/issues/3284)) |
| KratosForge Academy DP-700 | Developer Experience | KratosForge Academy | 2 | review: ACD-013 Practice exam question types with partial credit ([#3286](https://github.com/KratosDataGod/KratosForge/issues/3286)) (PR #3239)<br>ready: E54 Eventstream loses events after a source schema change ([#3287](https://github.com/KratosDataGod/KratosForge/issues/3287))<br>blocked: ACD-014 Fabric-verified exercises ([#3288](https://github.com/KratosDataGod/KratosForge/issues/3288))<br>blocked: ACD-016 Real-Time Intelligence exercises with local KQL ([#3289](https://github.com/KratosDataGod/KratosForge/issues/3289)) |
| Fabric Excellence Assessment | Enterprise Services | Fabric Excellence Assessment | 2 | review: FEA-303 Lakehouse and Warehouse excellence controls ([#3290](https://github.com/KratosDataGod/KratosForge/issues/3290)) (PR #3275)<br>review: FEA-404 Accepted risk and exceptions ([#3291](https://github.com/KratosDataGod/KratosForge/issues/3291)) (PR #3264)<br>ready: FEA-106 Fabric API capability matrix for the assessment ([#3292](https://github.com/KratosDataGod/KratosForge/issues/3292))<br>blocked: FEA-205 Live assessment check in the test workspace ([#3293](https://github.com/KratosDataGod/KratosForge/issues/3293)) |
| Enterprise Savings Intelligence | Enterprise Services | Enterprise Savings Intelligence | 2 | ready: ESI-301 Optimization records with lifecycle and rollback ([#3294](https://github.com/KratosDataGod/KratosForge/issues/3294))<br>ready: ESI-402 Capacity headroom from imported timepoints ([#3295](https://github.com/KratosDataGod/KratosForge/issues/3295))<br>blocked: ESI-502 Cost and savings screens mockup ([#3296](https://github.com/KratosDataGod/KratosForge/issues/3296))<br>blocked: ESI-108 Live check of two installations writing at once ([#3297](https://github.com/KratosDataGod/KratosForge/issues/3297)) |
| Three-platform parity | Databricks and Snowflake | Three-platform parity | 2 | review: SF-005 Snowflake object listing and query history ([#3298](https://github.com/KratosDataGod/KratosForge/issues/3298)) (PR #3215)<br>review: PAR-002 More parity cases, including cases that expect an error ([#3299](https://github.com/KratosDataGod/KratosForge/issues/3299)) (PR #3245)<br>ready: PAR-003 Unified connection check across the three platforms ([#3300](https://github.com/KratosDataGod/KratosForge/issues/3300))<br>ready: SF-009 Snowflake stages and data loading ([#3301](https://github.com/KratosDataGod/KratosForge/issues/3301)) |
<!-- END GENERATED: side-programs -->

### Story ids

WR story ids come from a ledger, [story-ids.json](story-ids.json), so two lanes never give two stories the same id. Each lane owns blocks of ids and takes its next id from them; a smaller block inside a larger one belongs to its own lane.

| Lane | Workstream | Ids |
| --- | --- | --- |
| Local engine | Local Execution | WR-100 to WR-199, apart from Notebook parity's block; next WR-147 (WR-146 is the decimal branch), and WR-123 to WR-129 stay free |
| Notebook parity | Developer Experience | WR-130 to WR-139; next WR-138 (WR-135 to WR-137 are used by #3368) |
| Warehouse | Microsoft Fabric | WR-200 to WR-269 (used up) and WR-700 to WR-749; next WR-708 |
| SQL translator | Microsoft Fabric | WR-270 to WR-299 |
| Dbx parity | Databricks and Snowflake | WR-300 to WR-399; next WR-322 |
| Governance | Enterprise Services | WR-400 to WR-499; next WR-419 |
| DevEx | Developer Experience | WR-500 to WR-599; next WR-508 (WR-507 is the extension channel fuzz story) |
| Platform | Platform Engineering | WR-600 to WR-699; next WR-618 |

`queues.py check` fails when a WR id in [workstreams.json](workstreams.json) is in no block, when a queued story's id is outside the blocks of its workstream's lanes, or when one id names two stories (two `new_stories` entries, or a slot or side-program title that starts with an id another story already has). Each error names the id and both places. Past collisions keep their ids and are recorded under `exceptions` with a note: WR-250 to WR-259 (Warehouse and the SQL translator) and WR-410 (the capacity hard stop and Governance's audit story). A lane's `next`, where set, must be in its own blocks. A new lane, or a lane that runs out of ids, gets a block in the ledger before its first story.

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
