# Workload operations: program plan and backlog

How Fabric Desktop by KratosBI becomes the place where a data engineer manages the workloads they build and run on their PC, on their own servers and in Microsoft Fabric: what they own, what runs where and as whom, what needs attention, what changed, what it affects, and what they can safely do next. Chris asked for it on 2026-10-16 with a 100-capability backlog. This page is the source of truth for that program; [continuation.md](continuation.md) is the handoff note every PR updates.

Assessed against `main` at `af11938` (sidecar 0.19.0, PR #53 merged; 322 tests pass, 2 skipped, on Linux). Nothing here has run against a live Fabric tenant unless it says so.

**Scope.** The management experience around workloads: discovery, organization, ownership, environments, execution placement, operations, governance, releases, reliability, resources, collaboration and value. Notebook editing, cell execution, transformation functions and pipeline authoring are out of scope; this program calls the execution paths that already exist.

**Sister programs.** Two other programs started the same day. Where they own a capability, items here are marked *Dep* and consume their records rather than building a second one.

| Program | Plan | Owns (as far as this program is concerned) |
| --- | --- | --- |
| Scale and operations (roadmap) | [PR #57](https://github.com/KratosDataGod/fabric-desktop/pull/57), `docs/scale-and-operations-plan.md` | Large-table execution, ingestion, Git and release records that tie promotions to immutable revisions, the Fabric pipeline job contract, agent-side status and queue records (`agents/<name>.json`), run correlation fields (`ran_on`, `job_id`, [PR #58](https://github.com/KratosDataGod/fabric-desktop/pull/58)), column lineage and versioned product contracts. |
| Prove this notebook | [PR #55](https://github.com/KratosDataGod/fabric-desktop/pull/55), `docs/prove/plan.md` | Notebook execution, environments and profiles, local-vs-Fabric comparisons, compatibility analysis, isolated kernels, run receipts (`receipts.py`), the per-run planner, placement recommendation and the cost model with run scorecards. |

Always-on monitoring and incident handling (the roadmap's capability 9) moved to this program on 2026-10-16: items 41 to 50 here. Concurrent writes and leases belong to the review thread ([PR #54](https://github.com/KratosDataGod/fabric-desktop/pull/54)). Platform requests and audit ([platform.md](../platform.md)) and the shortcut command center ([shortcuts.md](../shortcuts.md)) are extended, never duplicated.

## 1. What exists today

Paths are under `sidecar/fabric_desktop/` unless they start with `app/` or `docs/`.

**Things that can be managed.** There is no notion of a workload yet. What exists are separate kinds of runnable definitions, each with its own pages:

- *Metadata entities* (`meta/project.py`): one TOML file per table in a metadata project, with a declared `owner` that validation requires, run on this PC or in Fabric per environment (`environments.toml`: workspace, `target` local/onelake, `compute` local/fabric). Run history in `meta.run_log` in each environment's LH_Meta (`meta/store.py`), with `worker`, `host`, `attempt`, `job_id`. Deployments (promotions) in `meta.deployments` (env, version, source env, time). Freshness promises per entity (`meta/freshness.py`). Only one project is open at a time (`meta_service.py` `_project`, from `settings.json`).
- *Notebooks* (`notebook.py` `NotebookStore`): `.ipynb` files on this PC with attached lakehouse, per-notebook `Runs on` and publish workspace in notebook metadata. Publishing finds the Fabric item **by display name** every time (`cloud_service.py` `_publish`, `_notebook_item`); the item id is not kept.
- *Fabric items* (`fabric_api.py`): listed per workspace in the OneLake catalog (`service.py` `catalog`) and, for Notebook, DataPipeline and SparkJobDefinition, scanned for schedules and their last run (`schedule_service.py` `all_schedules`, scope "the workspaces this app works in" from `_schedule_workspaces`).
- *Schedules on this PC* (`local_schedules.py`): Windows Task Scheduler tasks for a notebook or a metadata environment, with their own run log (`schedule_runs.jsonl`) and lock files.
- *The headless agent* (`agent.py`, `jobqueue.py`, `agent_config.py`): a SQLite queue on the server, `agent.toml` with name, environments, workspace allowlist, identity type and schedules. The desktop app cannot see agents today.

**Where people look.** Home (`app/ui/app.js` `renderHome`) already answers "what happened since I was last here": failed latest runs per entity and environment, Fabric jobs that failed in the last day, late tables, running now, coming up, environment tiles, mesh map, Estate X-ray. Monitor (`renderMonitor`) merges four run sources (metadata runs from every readable environment, Fabric jobs started from this app, scheduled runs on this PC, notebook table writes) beside Fabric's embedded Monitoring hub, plus Schedules and Cost panels. The command bar (Ctrl+K, `features.js`) finds pages, actions, notebooks, workspaces and entities.

**Ownership, organization, governance.** Entities declare `owner`; projects declare domain, subdomain, team and contacts (`project.toml`, `meta/mesh.py`); data products declare an owner; the platform catalog (`central/core.py`, SQLite on this PC or SQL_Platform through UDF_Platform) holds domains, projects, products, ports, links, enrichment (classification, steward), glossary, admins, requests (key table, source, access, shortcut) with decide/withdraw/grants, an audit table, and shortcut inventory with first/last seen and change history. Catalog solution grouping and Dev/Test/Prod pills exist in the OneLake catalog (`app/ui/catalog-groups.js`). Exceptions with expiry exist in metadata projects (`Project.active_exception`).

**Operations.** Freshness alerts to one Teams webhook, deduplicated per PC in `freshness_alerts.json`, evaluated only while the app is open (`dataops_service.py`, `app/ui/dataops.js`). The agent posts its own job failures. Promote gates (ran and passed on current logic, tests, products), promote impact, compare before promote, table history and restore, schema drift review, quarantine and replay. Cost estimates from Fabric job durations and a configured price (`costs.py`, `estate_service.py` `cost_report`). Estate X-ray finds unhealthy tables and offers fixes.

**Identity.** The desktop signs in with the Fabric CLI public client and WAM (`auth.py`); Fabric runs started from the app run as the signed-in person; Fabric schedules run as their owner; the agent runs as a service principal, managed identity or a cached user (`agent.toml` `[auth]`).

**What is missing for this program**, in one line each: no workload record joining a definition to where it is deployed and how it is doing; no stable identity across local and Fabric representations; no ownership for notebooks and Fabric items; no view that says "mine" or "unowned"; no per-workload page with history, changes, impact and the actions that are actually supported; observations that come from a cache are not labelled as such anywhere except "Sign in to see its runs"; no incident record; health is only evaluated while the app is open.

## 2. Architecture decisions

1. **A workload is a reference, not a copy.** A workload record names its definition by its native identity (a metadata project and entity, a notebook file, a Fabric item id) and never stores the definition. Kinds in the first release: `meta_entity`, `notebook`, `fabric_item`. Agent jobs are executions of metadata entities, not workloads of their own.
2. **Stable ids from native identity.** `meta:<project>:<entity>`, `notebook:<name>`, `fabric:<workspace id>:<item id>`. A notebook deployed to Fabric is linked to its item by evidence, in this order: the item id this app recorded when it published or ran it (`recorded`), a link the engineer made by hand (`manual`), and only then the same display name in its publish workspace (`name match`, shown as such). A Fabric item linked to a notebook is shown inside that notebook's workload, not twice.
3. **Definition, deployment, execution and observation are separate.** A workload has one definition and zero or more deployments (environment, execution location, workspace or host, native ids, schedules, deployed release). Executions are runs from the native run stores, normalized to `ok | failed | running | needs_decision | skipped | cancelled | unknown` with the native status kept beside it. Observations are what the app last collected, each with its source, collection time and state.
4. **Observation states are explicit.** Every observed value carries `state`: `live` (collected in this request), `cached` (from an earlier collection, with its age), `stale` (cached and older than the source's freshness limit), `unknown` (never collected), `offline`, `unauthorized`, `signed_out`, `not_set_up`, `partial` (some sources answered) or `error` (with the message). A collection failure is never shown as a workload failure.
5. **Management records are local first, shared later.** Ownership assignments, criticality, tags, saved views and the action log live in `workloads.sqlite` beside the app's other files, labelled "on this PC". Declared ownership from Git (entity `owner`, project team and contacts, product owner) is shown as declared and wins over a local assignment for the same field. When a platform exists, shared assignments move to the platform catalog (stage 2B), migrated from the local file. Ownership is never inferred from who ran something last.
6. **Capabilities per adapter, per deployment.** Each workload kind declares what it supports: `discoverable`, `observable`, `runnable`, `schedulable`, `cancelable`, `deployable`, `read_only`. Actions are offered only where the capability holds for that deployment right now (signed in, environment set up), and each action says its environment, identity, target and consequence before it runs.
7. **Actions go through existing services.** Run an entity uses `meta_run`; run a Fabric item uses `run_fabric_item_now`; run a PC schedule uses `run_pc_schedule_now`; cancel uses `cancel_fabric_run`. No new execution path. An action is recorded as `submitted`, then `succeeded`/`failed` only when the run store says so.
8. **Scoped collection.** Fabric is read only for the workspaces this app works in (pins, environment workspaces, notebook publish workspaces; the same set Schedules already uses), on explicit refresh or when a page needs it, with results cached and timestamped. No tenant-wide polling.
9. **Modular extension, no new service.** A `workloads/` package in the engine, RPCs in a `WorkloadMixin`, UI in `app/ui/workloads.js` inside Monitor and Home. Always-on evaluation (stage 2) runs inside the existing headless agent, not a new backend.
10. **No AI in this program's first stages.** Health findings are rule-based and cite their evidence. If summaries are added later, they are labelled as interpretations, link to evidence, and need explicit context controls.

## 3. Record contracts

Each record type this program introduces, with the rules from the brief.

| | Workload | Deployment | Observation (runs, health inputs) | Assignment (owner, criticality, tags) | Action record | Incident (stage 2) |
| --- | --- | --- | --- | --- | --- | --- |
| **Authority** | The native definition (entity TOML in Git, `.ipynb` on this PC, Fabric item) | `environments.toml`, notebook metadata, Fabric item, `agent.toml` | The native run store: `meta.run_log`, Fabric job instances, `schedule_runs.jsonl`, agent queue | Declared: Git files. Assigned: `workloads.sqlite` (this PC), later the platform catalog | `workloads.sqlite` action log; outcome from the run store | `incidents/incidents.json` in the environment's LH_Meta control area (shared by app and agents; W2 revised) |
| **Identity** | Stable id from native identity (decision 2) | Workload id + environment + location | Native run id, with source | Workload id + field | Generated id; workload id, action, target | Dedup key: workload, environment, kind |
| **Collection** | Read on request; Fabric items on refresh, scoped | Same | Existing reads (`meta_runs_all`, `fabric_runs`, `pc_schedule_runs`, schedule scan) | Local read | Written by the action; outcome reconciled on read | Agent timer and app, same rules |
| **Freshness** | Fabric listing: collection time, stale after 24 h | Same | Per source: collection time; stale after 1 h for runs | Always current (local) | Current | Last evaluated time; heartbeat |
| **Permissions** | Fabric: Workspace.Read.All (existing); local files | Same | Existing scopes; nothing new | None (local file) | Whatever the invoked service needs | Storage access to LH_Meta (existing) |
| **Offline** | Local kinds listed; Fabric from cache, marked cached/stale | Same | Cached rows marked; "offline" state | Works | Actions needing Fabric are disabled with the reason | App shows cached incidents; agent queues alerts |
| **Failure / partial** | Per-workspace errors kept; listing marked `partial` | Same | Per-source state; never turned into a workload failure | n/a | Failure recorded with the service's message | Missed evaluation is its own incident |
| **Retention / privacy** | No data copied; names and ids only | Same | Up to the native store; cache keeps last collection only | Kept until changed; history of changes kept | 1 year or 10,000 rows, whichever is first | Open incidents and the last 200 resolved |
| **Actions** | Assign, tag, classify, link, open | Run, schedule, cancel where supported | n/a | Change (with who/when) | n/a | Acknowledge, assign, resolve |
| **Audit** | n/a | n/a | n/a | Every change keeps who, when, before, after | Is the audit | State changes with who/when |
| **Compatibility** | New, versioned (`schema_version` in the file); old app versions ignore it | Reads existing files unchanged | Reads existing stores unchanged | New file; migration to platform catalog in 2B | New | New file, contract 1; `freshness_alerts.json` not imported (late incidents are derived again) |

## 4. Stages

Effort: S (a day or less), M (a few days), L (a week or more). Every stage is Preview until tried on a tenant.

### Stage 0: this plan

### Stage 1: workload operations workspace (first release)

**Outcome.** An engineer opens Monitor > Workloads and sees every metadata entity, notebook and Fabric notebook, pipeline and Spark job definition in the workspaces they work in, grouped by project, owner or environment, with where each is deployed and runs, whether it is healthy, and how fresh that information is. A failed or late workload opens a detail page with its runs, what changed, who is affected, and the recovery actions the underlying service actually supports. Home shows "Your workloads" first. Every action is recorded with its outcome.

Items: 1, 2, 7, 10, 41, 42 (core); 3, 5, 6, 11, 12, 15, 16, 26, 31, 33, 35, 39, 49, 51, 71, 76, 81, 82, 95 (first increments).

**PR 1A: engine (`workloads/` package, RPCs, tests).** Open as #63. Effort M, risk low. Inventory adapters for metadata entities (open project), notebooks, Fabric items (scoped workspace scan in the background, cached), PC schedules and Fabric runs as deployments and executions; identity and reconciliation; observations with states; health and readiness rules with evidence; local management store (assignments, action log, built-in views); paged, searchable listing; recording the Fabric item id on publish.

- Acceptance: a fresh install with no project and no sign-in lists local notebooks with `observable: unknown` and no errors; the sample project's entities appear with declared owners, deployments per environment, latest run per environment and freshness; a failed run makes the workload `failing` with the run as evidence, and a telemetry failure makes it `unknown` with the reason, never `failing`; a notebook published by the app links to its Fabric item by recorded id after the item is renamed in Fabric; a name-only match is labelled; a Fabric listing that fails for one workspace returns the others with `partial`; cached Fabric observations older than their limit show `stale`; local assignments never override a declared owner; actions are recorded `submitted` and become `succeeded`/`failed` from the run store; 10,000 workloads list, filter and page in under 300 ms (measured).
- Validation: unit tests on local stores; mocked Fabric client for scans, 401/403, throttling (429 with Retry-After) and partial failures; a scale test. No live claim.

**PR 1B: UI (Monitor > Workloads, workload page, Home, command bar).** Effort M, risk low. Mockup first; draft until Chris reacts.

- Acceptance: keyboard reachable, every status has a text label, the inventory pages at 100 rows, every indicator links to a drill-down or action, actions show environment, identity, target and consequence before running, unsupported actions are absent rather than disabled-without-reason, Preview tag, docs in the user guide.
- Validation: `node --test app/tests`, a headless render of the pages against a stub RPC in the existing UI test harness, screenshots in the PR.

**Live verification procedure (for Chris, when convenient):** sign in on a test tenant; open a metadata project with Dev local and Test in a workspace; publish one notebook; open Monitor > Workloads and check the three kinds appear with the right locations; rename the published notebook in Fabric and refresh (still linked, "recorded"); fail a Test entity run (point its source at a missing file) and check Home and the workload page show it with the run; use Retry and check the action record ends `failed` or `succeeded` to match the run.

### Stage 2: always-on health and incidents (was roadmap capability 9)

Items 43, 44, 45, 46, 47 and the missing-heartbeat part of 38. Effort M+M, risk medium.

- **2.1 Incidents and evaluation on the agent.** The health rules from stage 1 (failed latest run, freshness promise missed, objective breached) run on the headless agent on a timer and in the app; incidents are rows in `meta.incidents` in the environment's LH_Meta (key, workload, environment, kind, opened, last seen, count, state, owner, acknowledged by, resolved), written with the conditional, version-checked commit from PR #54, so two evaluators never open the same incident twice. One Teams message on open, one on recovery, a digest above a threshold; alerts that cannot be sent queue in the agent's SQLite and go out marked delayed. Open entries in `freshness_alerts.json` are imported once.
- **2.2 Reliability objectives.** Per workload: freshness (existing promise), success rate over N runs, completion by a time; criticality sets defaults. Evaluation rules documented and tested.
- **2.3 Lifecycle, routing and escalation.** Acknowledge, assign, mitigate, resolve from the workload page and Monitor; routing by owner and criticality to webhooks configured per team; quiet periods; escalation after a set time unacknowledged. A missing agent heartbeat (roadmap's agent status records) opens an incident raised by the app or another agent. Built in two PRs: 2.3A engine (agent-offline incidents, likely failure category, `[[incidents.route]]` routes, quiet hours, escalation once across evaluators) and 2.3B screens (Monitor > Incidents, incident detail, incidents on the workload page, the app's freshness alerts moved onto incidents; mockup boards 5 and 6). This stage covers FD-123 (incident management) from the enterprise program.
- Acceptance: dedup across two evaluators racing; recovery closes; storm digest; network loss queues; telemetry outage opens a "can't evaluate" incident, not a workload incident. Validation: unit and mocked; live procedure for OneLake commits.

### Stage 2B: shared ownership

Items 11 (shared), 15 (departed contacts), 17. Effort M, risk medium (UDF_Platform needs new functions deployed). Assignments and criticality move to a `catalog.workload` table in the platform catalog (SQLite locally, SQL_Platform in Fabric), with history; the local file migrates on first use. Ownership transfer reuses Platform requests (new kind `ownership`), so history and decisions stay in one place. Departed contacts: the existing Graph `User.ReadBasic.All` lookup flags owners who no longer resolve.

### Stage 3: organization and discovery

Items 3, 4, 5, 8, 9, 88, 91. Several registered metadata projects (read-only inventory of projects that are not open), solutions that group workloads, environments, repositories and products; team portfolio views by domain, team and criticality; saved views with custom filters; discovery reconciliation (first seen, last seen, gone, moved, duplicate, inaccessible) reusing the shortcut center's first/last-seen pattern, never deleting a management record silently; bulk assignment with a preview and per-item results; declared business services; onboarding that walks a single engineer from "open a project" to "everything has an owner".

### Stage 4: environments and configuration

Items 21 to 30 and 78, 93, 94. Topology (Dev, Test, Prod, local sandboxes, agents, workspaces, designations, platform); configuration comparison across environments separating expected (workspace ids) from suspicious differences; readiness checks (identity, permissions, referenced items, runtime prerequisites); binding view (workspace, lakehouse, designation, link per environment); declared-vs-deployed drift (project version vs `meta.deployments`, runner bundle hash vs project); configuration provenance (organization policy, user setting, project file, local override), extending Settings' "Set by your organization"; secret-reference health (environment variable names resolvable on the agent, never values); certificate expiry for agent certificates; structured connection diagnostics; redacted support bundles extending Report a problem. Cloning plans and retirement are later (see backlog).

### Stage 5: execution locations

Items 31, 34, 36, 37, 38, 39 (agent and credential recovery). Depends on the roadmap's agent status records (`agents/<name>.json`) and pipeline job contract. Execution location directory (this PC, each agent with version, capabilities, environments, identity type, last heartbeat; Fabric workspaces and capacities); read-only queue view across locations that says which scheduler owns each item; overlap warnings for the same entity scheduled on the PC, an agent and in Fabric; recovery guidance after a PC shutdown, a lost agent, an expired credential or an interrupted Fabric run.

### Stage 6: dependencies and operational depth

Items 14, 19, 20, 46, 48, 49, 50, 81 to 87, 89, 96. Dependency graph labelling declared and inferred edges (entities, products, links, sources, environments, agents); consumer impact for a failure, delay, retirement or release; upstream readiness; product operational scorecards; shared dependency risk; maintenance windows with dependency-aware impact; cross-workload operational timeline; recovery runbooks (guided procedures using existing operations); post-incident review packages; change subscriptions; comments and decisions on workloads, releases and incidents; "since your last visit" summaries. Uses the roadmap's column lineage and product contracts where they exist.

### Stage 7: releases

Items 13, 18, 51 to 58, 60, 90. Built on the roadmap's release records (immutable revisions, stage 5.x there) and Prove's receipts: release inventory per environment and location, readiness that explains existing gates, promotion plans, identity-backed approvals reusing Platform requests with optional separation of duties, release comparison, change impact before release, deployment evidence, rollback feasibility, a release and maintenance calendar, handoff packages and documentation completeness, and a team service catalog over data products.

### Stage 8: resources, placement and value

Items 32, 33, 40, 61 to 70, 79. Reuses Prove's planner, placement recommendation, cost model and scorecards: observable PC and agent resources; Fabric capacity relationships (and their visibility limits); workload resource history from receipts; advisory capacity-aware scheduling; budgets labelled advisory unless enforceable; team allocation without double counting; an evidence-backed optimization queue (repeated failures, poorly placed workloads, X-ray findings); value reports that never claim automatic cash savings; execution policy profiles and external execution boundaries shown at placement time.

### Stage 9: governance

Items 71 to 80. Effective identity per operation and destination; access posture from existing Access tabs and reviews; the request lifecycle extended with evidence, expiry and fulfillment links; policy assessment with compliant / noncompliant / unknown / unenforceable results; policy inheritance; the platform audit extended to workload actions; sensitive-data posture from declared classifications; credential lifecycle awareness; governance exceptions extending project exceptions with owners and re-evaluation.

### Stage 10: command center and reports

Items 92, 97, 98, 99, 100. The organization bootstrap path, shareable reports with provenance and scope, an accessibility pass across every management page, cross-device continuity limited to shared records, and a drillable command center that links the parts above without repeating them.

### Delivery sequence

1. Stage 0 plan; stage 1 PR 1A (engine) and the mockup in parallel; PR 1B after Chris reacts to the mockup.
2. Stage 2.1 and 2.2 (incidents and objectives), because always-on health is the first thing a team needs once workloads are visible, and it was handed to this program.
3. Stage 2B (shared ownership) and stage 3 (organization).
4. Stage 4 (environments), then stage 2.3 and stage 5 once the roadmap's agent records exist.
5. Stages 6, 7 (after the roadmap's release records and Prove's receipts land), 8, 9, 10.

## 5. Decisions

Defaults are being built; Chris can change them.

- **W1. Where shared ownership lives once a platform exists.** Default: the platform catalog (`catalog.workload`), so it sits beside domains, products and requests and every team sees the same owner. Needs a UDF_Platform redeploy with new functions, which the platform has no update path for yet; stage 2B adds one. Alternative: a file in LH_Meta per project (no UDF change, but per project and harder to query across teams).
- **W2. Where incidents live.** Default: `meta.incidents` in each environment's LH_Meta, because the agent already has storage access there and needs no UDF. Alternative: the platform catalog, which would centralize them but makes the agent depend on UDF_Platform.
  *Revised 2026-10-16 while building 2.1:* still per environment in LH_Meta, but as one JSON document (`incidents/incidents.json` in the control area) changed only with etag-conditional writes, instead of a Delta table. A small document with If-Match makes "open once" exact across any number of evaluators without a merge step, and sits beside the agent status records and the pipeline mailbox the agent already writes.

## 6. Backlog

Items in stages 1 and 2 carry full acceptance criteria now. Later items carry their current state, problem and direction; each gets measurable acceptance criteria in this file, in the PR that starts its stage, before implementation.

Status: **Adequate** (already good enough), **Enhance** (present, needs work), **Missing**, **Blocked** (access or no supported API), **Not doing** (with the reason). *Dep* names the program whose records an item consumes. Validation: U unit tests, M mocked service tests, UI UI tests or screenshots, L live tenant check. Needs: permissions or services beyond what the app already asks for ("none new" when nothing). PR: link once opened.

### A. Unified workload inventory and organization

**1. Unified workload inventory** · Enhance · Stage 1
- Today: runs are merged in Monitor (`app.js` `renderMonitor`), schedules in Monitor > Schedules (`schedule_service.py` `all_schedules`), Fabric items in the OneLake catalog (`service.py` `catalog`), entities on Data mesh; nothing lists *workloads*.
- Problem: an engineer can't see everything they run, everywhere, in one place.
- Enhancement: `workloads_list` over three adapters (entities, notebooks, Fabric items in scoped workspaces), searchable and paged, keeping each workload's native id and location; Monitor > Workloads.
- Depends: none. Acceptance: the three kinds listed with native ids and locations; search by name, owner, project, environment, location; paging; 10,000 rows under 300 ms. Validation: U, M, UI. Needs: none new (Workspace.Read.All, Item.Read.All already used). PR: —

**2. Workload identity reconciliation** · Missing · Stage 1
- Today: publish, run and schedule look the Fabric notebook up by display name every time (`cloud_service.py` `_notebook_item`).
- Problem: a rename in Fabric or a same-named item in another workspace silently breaks or mislinks.
- Enhancement: record the item id when the app publishes or runs a notebook; link by recorded id, then manual link, then name match (labelled); manual link and unlink.
- Depends: none. Acceptance: link survives a rename in Fabric; name-only matches labelled; two same-named notebooks in two workspaces don't merge. Validation: U, M. Needs: none new. PR: —

**3. Project and solution organization** · Enhance · Stage 1 (group by project), Stage 3 (solutions)
- Today: catalog solution grouping and environment pills (`app/ui/catalog-groups.js`); one open metadata project with project.toml identity.
- Problem: workloads aren't grouped with their environments, repositories, products and owners.
- Enhancement: group by project in the inventory now; later a solution record joining projects, repositories, products and owners.
- Depends: stage 3 needs several registered projects. Acceptance: grouping shows counts and health per group. Validation: U, UI. Needs: none new. PR: —

**4. Team portfolio views** · Enhance · Stage 3
- Today: project.toml domain/subdomain/team, mesh map on Home.
- Problem: a platform lead can't see workloads by team, domain, criticality or responsibility.
- Enhancement: portfolio grouping and counts from declared team/domain and assignments.
- Depends: 11, 16. Acceptance: grouping never infers a team from who ran something. Validation: U, UI. Needs: none new. PR: —

**5. Saved operational views** · Missing · Stage 1 (built-in), Stage 3 (custom)
- Today: Monitor filters reset per session.
- Problem: engineers rebuild the same filters every time.
- Enhancement: built-in views (Needs attention, Mine, Unowned, On this PC, In Fabric, Production) now; custom saved views later, stored on this PC.
- Depends: 1. Acceptance: views apply server-side filters; custom views survive restarts. Validation: U, UI. Needs: none. PR: —

**6. Management search** · Enhance · Stage 1 (workloads), Stage 6 (incidents, releases)
- Today: Ctrl+K finds pages, actions, notebooks, workspaces, entities (`features.js` `paletteItems`).
- Problem: can't search by owner, environment or state.
- Enhancement: inventory search across name, kind, owner, project, environment, location, tags; workloads in Ctrl+K; results only include what the person's sign-in could read (Fabric listing is already per-user).
- Depends: 1. Acceptance: owner and environment terms match; no result from a workspace the listing could not read. Validation: U, UI. Needs: none. PR: —

**7. Workload detail page** · Missing · Stage 1
- Today: entity editor (Data mesh), notebook editor, run detail dialog; no management page.
- Problem: purpose, ownership, deployments, health, runs, dependencies and actions are spread over five pages.
- Enhancement: one page per workload: summary and capabilities, ownership (declared and assigned), deployments, health and readiness with evidence, recent runs with native links, what changed, upstream and downstream, consumers, supported actions, action log.
- Depends: 1, 2. Acceptance: every section says its source and freshness; unsupported actions are absent. Validation: U, UI. Needs: none new. PR: —

**8. Discovery reconciliation** · Missing · Stage 3
- Today: the shortcut center keeps first/last seen for shortcuts (`central/core.py` `record_shortcuts`), nothing for workloads.
- Problem: removed, moved, duplicated or newly inaccessible items vanish without trace.
- Enhancement: first seen, last seen, gone (after two scans), moved (same id, new workspace), inaccessible (403) states; management records kept.
- Depends: 1. Acceptance: no assignment is deleted when an item disappears; inaccessible ≠ gone. Validation: U, M. Needs: none. PR: —

**9. Bulk organization tools** · Missing · Stage 3
- Problem: assigning owners to fifty workloads one by one.
- Enhancement: select, preview the change per item, apply, per-item result (applied, skipped because declared, failed).
- Depends: 11. Acceptance: partial success reported exactly. Validation: U, UI. Needs: none. PR: —

**10. Inventory freshness** · Enhance · Stage 1
- Today: `meta_runs_all` reports `unavailable` per environment; the schedule scan has `scanned`.
- Problem: cached information looks live.
- Enhancement: every observation carries source, collected time and state (decision 4); the inventory header shows each source's state.
- Depends: 1. Acceptance: offline, signed-out, 403, partial and stale each render differently and never as a failure. Validation: U, M, UI. Needs: none. PR: —

### B. Ownership, responsibility and collaboration

**11. Explicit ownership** · Enhance · Stage 1 (local and declared), Stage 2B (shared)
- Today: entity `owner` is required (`meta/project.py` validate); project team and contacts; product owner. Notebooks and Fabric items have none.
- Problem: no accountable team, technical owner, business or support contact for most workloads.
- Enhancement: four roles (team, technical owner, business contact, support contact); declared from Git shown as declared; local assignments for the rest; shared in 2B.
- Depends: decision W1 for 2B. Acceptance: declared beats assigned; changes keep who and when. Validation: U. Needs: none (2B: UDF_Platform redeploy). PR: —

**12. Personal engineering workspace** · Enhance · Stage 1, Stage 6
- Today: Home lists failures and late tables for everyone in the open project.
- Problem: not personal: no "mine", no assigned incidents or decisions.
- Enhancement: Home's first section is "Your workloads": failing, late and needs-decision workloads you own (declared or assigned to your sign-in), then the rest; incidents and decisions in stage 2 and 6.
- Depends: 11. Acceptance: "mine" uses ownership only. Validation: U, UI. Needs: none. PR: —

**13. Team handoff packages** · Missing · Stage 7
- Enhancement: runbooks, dependencies, access prerequisites, releases, known issues, support terms exported as one reviewable package. Depends: 48, 51, 90. Validation: U. Needs: none. PR: —

**14. Operational discussions** · Missing · Stage 6
- Enhancement: comments and decisions on workloads, releases and incidents with identity and time, in the shared store. Depends: 2B, 45. Validation: U. Needs: none. PR: —

**15. Responsibility gaps** · Missing · Stage 1 (unowned), Stage 2B (departed)
- Today: validation flags an entity without an owner; an owner of "me" (the template default) passes.
- Enhancement: "Unowned" view and finding, placeholder owners flagged; departed contacts via Graph lookup in 2B; unsupported workloads (kinds without observe/run) listed.
- Depends: 11. Acceptance: template placeholder flagged. Validation: U. Needs: 2B uses User.ReadBasic.All (already used by Access). PR: —

**16. Criticality classification** · Missing · Stage 1 (field), Stage 2 (expectations)
- Enhancement: critical / high / standard / low per workload; drives defaults for objectives and alert routing in stage 2. Validation: U. Needs: none. PR: —

**17. Ownership change workflow** · Missing · Stage 2B
- Enhancement: a transfer request (Platform requests, new kind) accepted by the new owner; history kept. Depends: 2B. Validation: U. Needs: none. PR: —

**18. Team service catalog** · Enhance · Stage 7
- Today: OneLake > Data products lists published products.
- Enhancement: support terms, onboarding requirements and owners per product or service. Depends: 11. Validation: U, UI. PR: —

**19. Change subscriptions** · Missing · Stage 6
- Enhancement: follow a workload, release or incident; notified in-app and through the stage 2 routes. Depends: 45, 47. Validation: U. PR: —

**20. Maintenance coordination** · Missing · Stage 6
- Enhancement: maintenance windows with affected workloads, contacts and impact; incidents in a window are marked expected. Depends: 81. Validation: U. PR: —

### C. Environment and configuration management

**21. Environment topology** · Enhance · Stage 4
- Today: environment cards (workspace, Tables in, Runs on, designations); Platform per environment. Enhancement: one map of environments, sandboxes, agents, workspaces and their relationships. Depends: 31. Validation: U, UI. Needs: none. PR: —

**22. Configuration comparison** · Enhance · Stage 4
- Today: compare before promote compares data; promote impact lists changed entities. Enhancement: compare effective configuration across environments, expected differences (ids) separated from suspicious ones. Validation: U. PR: —

**23. Environment readiness checks** · Enhance · Stage 4
- Today: promote gates; compute_check per notebook. Enhancement: identity, permissions, referenced items, runtime prerequisites and known validation gaps per environment, with "unknown" where not checkable. Validation: U, M, L. PR: —

**24. Environment onboarding** · Adequate
- Today: environment cards assign or create workspaces through the dialog; Platform setup and join. Nothing to add beyond linking it from stage 3's onboarding.

**25. Binding management** · Enhance · Stage 4 · *Dep Prove (notebook bindings)*
- Today: environment workspaces, designations, link `reads`/`location` per environment. Enhancement: one read view of bindings per environment; changes through existing dialogs only. Validation: U, UI. PR: —

**26. Drift detection** · Enhance · Stage 1 (release drift), Stage 4
- Today: schema drift review compares data with declarations. Enhancement: stage 1 shows when an environment runs an older project version than the project (from `meta.deployments`); stage 4 adds declared vs deployed vs observed config. Validation: U. PR: —

**27. Environment cloning plans** · Missing · Stage 4 (late)
- Enhancement: a reviewable plan (workspaces, lakehouses, bindings) with secrets excluded and no production data. Low priority until teams ask: promote already creates lakehouses per environment. PR: —

**28. Secret-reference health** · Enhance · Stage 4
- Today: agent.toml and source systems refuse secret values (environment variable names only). Enhancement: check each reference resolves for the intended identity on the agent (`agent check`), without reading values into the app. Validation: U. PR: —

**29. Configuration provenance** · Enhance · Stage 4
- Today: Settings shows "Set by your organization" for policy values. Enhancement: per effective setting: organization policy, user setting, project file, environment file, local override. Validation: U. PR: —

**30. Environment retirement** · Missing · Stage 4 (late)
- Enhancement: dependency list and a reversible plan; never deletes shared resources itself. PR: —

### D. Cross-location execution management

**31. Execution location directory** · Missing · Stage 1 (this PC and Fabric workspaces), Stage 5 (agents) · *Dep roadmap (agent records)*
- Enhancement: locations from deployments now; agents from `agents/<name>.json` later. Acceptance: an agent with no recent heartbeat shows "last seen", not "online". Validation: U, UI. PR: —

**32. Placement recommendations** · Missing · Stage 8 · *Dep Prove (placement)*
- Enhancement: show Prove's recommendation with its evidence on the workload page. PR: —

**33. Execution preflight** · Missing · Stage 1 (action confirmation), Stage 8 · *Dep Prove (planner)*
- Enhancement: every management action shows environment, identity, target, consequence first; the full preflight reuses Prove's planner. Validation: U, UI. PR: —

**34. Unified queue view** · Missing · Stage 5 · *Dep roadmap (agent queue records)*
- Enhancement: queued and running work per location, labelled with which scheduler owns it. PR: —

**35. Supported lifecycle controls** · Enhance · Stage 1
- Today: run, cancel, enable/disable and run-now exist on separate pages. Enhancement: offered on the workload page only where the adapter supports them for that deployment. Acceptance: no pause/resume where none exists. Validation: U, UI. PR: —

**36. Concurrency coordination** · Enhance · Stage 5 · *Dep review thread (PR #54)*
- Today: schedule locks, agent leases, version-checked commits. Enhancement: warn when one entity is scheduled on two locations with overlapping windows. Validation: U. PR: —

**37. Agent enrollment and inventory** · Enhance · Stage 5 · *Dep roadmap*
- Today: `agent.toml`, `fabric-desktop agent status` on the server. Enhancement: agents' identity, capabilities, version, environments and owner in the app. Validation: U, UI, L. PR: —

**38. Agent health and heartbeat** · Missing · Stage 2.3, Stage 5 · *Dep roadmap (heartbeat records)*
- Enhancement: missing heartbeat opens an incident; version mismatch and degraded capability shown. Validation: U. PR: #79 (agent-offline incident; version and capability in stage 5)

**39. Execution recovery guidance** · Enhance · Stage 1 (failed runs), Stage 5
- Today: the agent recovers jobs whose worker died (`jobqueue.recover`); PC schedules skip when locked. Enhancement: guided retry for failed runs now; PC shutdown, agent loss, expired credentials and interrupted Fabric runs in stage 5. Validation: U. PR: —

**40. Execution policy profiles** · Enhance · Stage 8
- Today: agent environments and workspace allowlist; IT policy for the platform. Enhancement: allowed destinations and identities per environment, labelled enforced (agent allowlist) or advisory. PR: —

### E. Operations, reliability and incidents

**41. Actionable operations Home** · Enhance · Stage 1
- Today: Home attention lists failed runs, late tables and Fabric failures. Enhancement: ordered by ownership and severity, from workload health, with a next action each; blocked releases and unhealthy agents as their stages land. Validation: U, UI. PR: —

**42. Unified run history** · Enhance · Stage 1 · *Dep roadmap (PR #58 `ran_on`)*
- Today: Monitor merges four sources but normalizes only the pill. Enhancement: per-workload runs normalized with native status, native link and source kept; uses `ran_on`/`job_id` from PR #58 when present. Validation: U. PR: —

**43. Reliability objectives** · Enhance · Stage 2.2
- Today: freshness promises. Enhancement: success rate and completion-time objectives with documented evaluation. Validation: U. PR: #74 (success rate; completion time is freshness `by`)

**44. Always-on health evaluation** · Missing · Stage 2.1
- Enhancement: the agent evaluates on a timer; the app evaluates when open; same rules. Validation: U, L. Needs: none new. PR: #74 (agent timer, opt-in; app on request)

**45. Incident lifecycle** · Missing · Stage 2.1, 2.3
- Enhancement: detection, acknowledgement, ownership, mitigation, recovery, resolution in the environment's incident log. Depends: W2. Validation: U, M. PR: #74 (engine and RPCs), #79 (escalation, likely category), 2.3B (screens)

**46. Alert correlation** · Missing · Stage 2.3, Stage 6
- Enhancement: group symptoms by shared upstream, environment or location, saying "likely" and listing evidence. Validation: U. PR: —

**47. Alert routing and escalation** · Enhance · Stage 2.3
- Today: one Teams webhook per PC, send-once. Enhancement: per-team routes, criticality, dedup across evaluators, quiet periods, escalation. Validation: U. PR: #74 (dedup, digest, delayed delivery), #79 (routes, quiet hours, escalation)

**48. Recovery runbooks** · Missing · Stage 6
- Enhancement: guided steps that call existing operations with confirmations; stage 1's retry is the first built-in one. PR: —

**49. Operational timeline** · Missing · Stage 1 (per workload), Stage 6 (across)
- Today: run_log, deployments, schema_history, restores, platform audit, shortcut changes are separate. Enhancement: one chronological list per workload now. Validation: U. PR: —

**50. Post-incident review** · Missing · Stage 6
- Enhancement: a reviewable evidence package (impact, timeline, actions, follow-ups) generated from incidents and runs. PR: —

### F. Release and deployment management

**51. Release inventory** · Enhance · Stage 1 (deployed version per environment), Stage 7 · *Dep roadmap (immutable revisions)*
- Today: `meta.deployments` per environment. Enhancement: show it per workload now; immutable revisions per location in stage 7. Validation: U. PR: —

**52. Deployment readiness** · Enhance · Stage 7
- Today: promote gates explain failures. Enhancement: one assessment combining gates, tests, compatibility, permissions and open risks. PR: —

**53. Promotion planning** · Enhance · Stage 7 · Today: promote impact. PR: —

**54. Approval workflows** · Missing · Stage 7 · *Dep roadmap (D1)*
- Enhancement: identity-backed approvals through Platform requests, separation of duties where configured. PR: —

**55. Release comparison** · Missing · Stage 7 · *Dep roadmap 5.2*. PR: —

**56. Change impact assessment** · Enhance · Stage 7 · Today: promote impact, `project.impact`, catalog consumers. PR: —

**57. Deployment evidence** · Enhance · Stage 7 · *Dep roadmap 5.1, Prove receipts*. Today: deployments lack who and commit. PR: —

**58. Rollback planning** · Enhance · Stage 7 · *Dep roadmap 5.3*. Today: per-table restore. PR: —

**59. Progressive rollout management** · Not doing (now)
- Fabric Desktop by KratosBI deploys a whole project per environment, and Test before Prod is already the staged path. Pilot groups need several production destinations for one project, which the deployment model does not have. Revisit when multi-worker agents (roadmap 7.3) exist.

**60. Release calendar** · Missing · Stage 7 (with maintenance windows, 20). PR: —

### G. Resource, capacity and financial management

**61. Resource inventory** · Missing · Stage 8
- Enhancement: this PC's cores, memory and disk; agents' from their records; Fabric capacities the sign-in can read (`list_capacities`, may be refused), with visibility limits stated. Needs: Capacity.Read.All (already optional). PR: —

**62. Resource pressure** · Blocked in part · Stage 8
- PC and agent pressure can be sampled. Fabric capacity utilization has no public per-capacity metrics API for non-admins (the Capacity Metrics app is a Power BI model); shown as unavailable rather than estimated. PR: —

**63. Workload resource history** · Missing · Stage 8 · *Dep Prove (receipts with CPU and memory)*. PR: —

**64. Capacity-aware planning** · Blocked in part · Stage 8 · Advisory from run history only, for the reason in 62. PR: —

**65. Resource budgets and quotas** · Missing · Stage 8 · Advisory warnings; hard enforcement only where the agent can refuse (concurrency). PR: —

**66. Cost model management** · Enhance · *Dep Prove (cost model)* · Today: `costs.py` price per CU-hour and currency in Monitor > Cost. This program reads it; no second model. PR: —

**67. Measured-versus-estimated cost** · Enhance · *Dep Prove* · Labels come from Prove's cost model. PR: —

**68. Team allocation reports** · Missing · Stage 8 · Attribution by ownership (11) over Prove's costs, each run counted once. PR: —

**69. Optimization opportunity queue** · Enhance · Stage 8 · Today: Estate X-ray findings. Adds repeated failures, idle schedules and placement evidence. PR: —

**70. Value reporting** · Missing · Stage 8 · *Dep Prove (scorecards)* · Portfolio roll-up; never claims cash savings. PR: —

### H. Governance, access and security

**71. Effective identity visibility** · Enhance · Stage 1 (per deployment), Stage 9
- Today: signed-in user in the account menu; agent auth type in agent.toml. Enhancement: each deployment says which identity runs it (you on this PC; you for runs started here; the schedule's owner for Fabric schedules; the agent's configured identity). Validation: U. PR: —

**72. Access posture** · Enhance · Stage 9 · Today: OneLake Access tab, workspace access review, shortcut readers. PR: —

**73. Access request lifecycle** · Enhance · Stage 9 · Today: Platform requests with decide, withdraw, grants, revoke. Adds evidence, expiry and fulfillment links. PR: —

**74. Policy assessment** · Enhance · Stage 9 · Today: project validation rules and shortcut rules. PR: —

**75. Policy inheritance** · Missing · Stage 9. PR: —

**76. Operational audit trail** · Enhance · Stage 1 (local action log), Stage 2B (platform audit)
- Today: `catalog.audit`, shortcut changes, restores. Enhancement: every action from a workload page recorded with actor, target, environment, identity, before/after, outcome. Validation: U. PR: —

**77. Sensitive-data handling posture** · Enhance · Stage 9 · Today: classification on entities and enrichment. PR: —

**78. Credential lifecycle awareness** · Enhance · Stage 4 · Today: sign-in state; agent certificate path. Adds certificate expiry and failed refresh, never storing a copy. PR: —

**79. External execution boundaries** · Missing · Stage 8 · Where data and credentials go for this PC, an agent and Fabric, shown when choosing. PR: —

**80. Governance exceptions** · Enhance · Stage 9 · Today: project exceptions with entity, rule and expiry. Adds owner, approval and re-evaluation. PR: —

### I. Dependencies, data products and operational impact

**81. Workload dependency graph** · Enhance · Stage 1 (per workload), Stage 6 · *Dep roadmap (column lineage)*
- Today: `project.impact`, run order, links, mesh map. Enhancement: upstream and downstream per workload now, declared vs inferred labels. Validation: U. PR: —

**82. Consumer impact** · Enhance · Stage 1 (known consumers), Stage 6
- Today: catalog consumers per product (`central/core.py` `list_products`). Enhancement: the workload page lists products its entity feeds and their known consumers. Validation: U. PR: —

**83. Dependency readiness** · Missing · Stage 6. PR: —

**84. Product operational scorecards** · Missing · Stage 6. PR: —

**85. Version compatibility tracking** · Missing · *Dep roadmap 8.2*. PR: —

**86. Deprecation management** · Enhance · Stage 6 · *Dep roadmap 8.2* · Today: product status deprecated and date. PR: —

**87. Shared dependency risk** · Missing · Stage 6. PR: —

**88. Business service mapping** · Missing · Stage 3 · Declared only; no invented lineage. PR: —

**89. Dependency-aware maintenance** · Missing · Stage 6. PR: —

**90. Operational documentation completeness** · Missing · Stage 7 · Today: custom code needs owner and test. PR: —

### J. Adoption, diagnostics and management experience

**91. Guided workspace onboarding** · Enhance · Stage 3 · Today: Home's get-started steps. PR: —

**92. Organization bootstrap** · Enhance · Stage 10 · Today: Platform setup and join, IT policy file. PR: —

**93. Connection diagnostics** · Enhance · Stage 4 · Today: component check, sign-in errors through `friendlyError`, `agent check`. PR: —

**94. Support evidence bundles** · Enhance · Stage 4 · Today: Report a problem. Adds selectable, redacted operational context. PR: —

**95. Management command palette** · Enhance · Stage 1 · Today: Ctrl+K. Adds workloads and their supported actions with environment shown. Validation: UI. PR: —

**96. Personalized attention summaries** · Enhance · Stage 6 · Today: Home's "since I was last here" summary line. PR: —

**97. Shareable management reports** · Missing · Stage 10. PR: —

**98. Accessible operational navigation** · Enhance · every stage, audit in Stage 10 · Text labels on every status, keyboard paths, paging for large inventories. PR: —

**99. Cross-device continuity** · Missing · Stage 10 · Shared state goes through the platform catalog (2B); syncing personal preferences would need a per-user store the product doesn't have and isn't worth a new backend. PR: —

**100. Engineering command center** · Missing · Stage 10 · Emerges from stages 1 to 9; a drillable overview, not a new dashboard of copies. PR: —

## Change log

| Date | Change |
| --- | --- |
| 2026-10-16 | Plan written from `main` at `af11938`; ownership with the roadmap and Prove programs recorded; monitoring and incidents moved here. |
| 2026-10-16 | Stage 2.1/2.2 built: W2 revised to a conditional-write JSON document per environment; agent evaluation opt-in; `freshness_alerts.json` import dropped. |
| 2026-10-17 | #72 and #74 merged. Stage 2.3 split into 2.3A (engine) and 2.3B (screens, after Chris reacts to mockup boards 5 and 6). FD-122/123/124 and governance views assigned to this program by the enterprise program. |
| 2026-10-17 | #79 (2.3A) merged. 2.3B built after Chris's go: Monitor > Incidents, incident page, incidents on the workload page; agents publish their incident settings (no webhooks) in their status records. Moving the app's own freshness alerts onto incidents is split out as 2.3C. |
