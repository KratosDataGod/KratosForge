# 12-week roadmap

Rolling plan from week 1 (starting Monday 2026-11-02) to week 12 (ending 2027-01-24). The weekly review moves it on by a week, records what changed and why, and keeps it in line with the [work queues](README.md#work-queues). Items with a PB id are in [the backlog](../PRODUCT_BACKLOG.md). Anything marked *needs Chris* waits for his mockup approval, his go on a new feature, or his yes on capacity spend.

Why this order: as of 2026-11-02 about 650 PRs merged into main since 2026-10-26. The enterprise engineering audit ([scorecard](../../audits/ENTERPRISE_READINESS.md)) scored KratosForge 54 of 100 (64 at the 2026-11-05 re-score) and says it is not enterprise-ready: builds are unsigned, nothing has run against a live tenant, and data integrity, enterprise deployment and performance score lowest. Most audit findings (EA-001 to EA-029) are already fixed. A Fortune 100 rollout needs proof and reliability before breadth, so weeks 1 to 4 close the audit's remaining gaps and turn built work into verified work. Since 2026-11-02, open issues track only work someone can do today; blocked items keep their state in the backlog.

## Weeks 1 to 4: stabilize and verify (2026-11-02 to 2026-11-29)

| Workstream | Goal | Stories |
| --- | --- | --- |
| All | Release-critical | EA-009: signing (waits on Microsoft's identity check, *needs Chris*) and one live run of the FD-101 harness on the non-production workspace (*needs Chris*: capacity yes) |
| Local Execution | Correct numbers, measured speed | Done in week 1: #1861 append guard, benchmark job (#1874, advisory), #2281 reproducible engine zip. Now: WR-101 metadata appends retry the first-write race; WR-102 benchmark regression gate; WR-103 clean stop on memory or disk limits; PB-015 live Prove run and the 8-hour soak (*needs Chris*) |
| Microsoft Fabric | Warehouse parity without surprises | Done in week 1: T-SQL gaps refused by name (#1686, #1711, #1869). Now: WR-201 429 Retry-After and long-running limits everywhere (carries #1667); WR-202 request id and next step on Fabric errors; Warehouse run 8 and PB-002 tenant smoke test (*needs Chris*: typed yes) |
| Databricks and Snowflake | Parity a user can read | Done in week 1: Spark DataFrame coverage list (#1668), Databricks SQL Fails at 0 (#2276, #2278). Now: WR-301 reader options (carries #1669); WR-302 Snowflake SQL coverage list; Databricks live run (*needs Chris*: workspace) |
| Enterprise Services | Deployment an IT team can trust | Done in week 1: #1685 policy changes recorded, #1688 IT checklist, #2262 data retention, #2280 TLS inspection, #2285 expired sign-ins, #2294 tokens at rest. Now: WR-401 personal-data inventory; WR-402 runbook for agent stops; WR-403 verifiable audit exports; #2258 Group Policy and #2305 audit time limit (*needs Chris*: go); MSI or MSIX (*needs Chris*) |
| Developer Experience | Accessibility that doesn't regress | Done in week 1: keyboard pass (#1690), axe on every dialog (#1886 to #1890), 200% zoom and contrast (#2286). Now: WR-501 real-world paths in VS Code; WR-502 docs audit; WR-503 errors name a next step; LX Home, Ranks, header art and skins drafts (*needs Chris*: reaction) |
| Platform Engineering | Shared plumbing, stricter lint | Done in week 1: mypy strict with an empty baseline, pinned runner images (#2268), lock-hash and SBOM guard (#2256), .vsix supply chain (#2303, #2307). Now: WR-601 dependency vulnerability scanning; WR-602 VDI and shared-PC fixes; WR-603 report counts cancelled runs apart; ruff BLE second slice stays in backlog |

Exit: signing done or dated, the FD-101 live run done or dated with Chris; audit score re-measured; no P0 or P1 audit finding open without an owner.

## Weeks 5 to 8: expand the three platforms (2026-11-30 to 2026-12-27)

| Workstream | Goal | Candidate stories |
| --- | --- | --- |
| Databricks and Snowflake | Databricks verified live; Snowflake first slice | Databricks Connect and SQL warehouse on a live workspace (*needs Chris*: workspace and spend); Asset Bundles validate and deploy through the Databricks CLI; first Snowflake slice from its ADRs, likely SQL validation and local execution through a Snowflake SQL dialect (*needs Chris*: go) |
| Local Execution | Pick the right engine for each workload | Engine recommendation across local Python, local SQL, Fabric and Databricks, built on the PB-017 advisor; PB-073 process kernel by default with limits; PB-067 bounded-memory runs; real local Spark detection |
| Microsoft Fabric | Deployment and Git parity | PB-040 Fabric workspace Git integration; PB-041 deployment preview and item deploy; PB-049 pipeline mailbox on a tenant (*needs Chris*); PB-048 schedule editing (*needs Chris*: mockup) |
| Platform Engineering | Release confidence | PB-063 assistive technology audit; PB-061 missing policy keys; release notes from merged PRs |
| Developer Experience | Engine picker in the notebook | Engine picker UI from the approved mockup (*needs Chris*: mockup review) |

## Weeks 9 to 12: developer experience, enterprise services, optimization (2026-12-28 to 2027-01-24)

| Workstream | Goal | Candidate stories |
| --- | --- | --- |
| Developer Experience | Debug and test like a desktop IDE | Debugger maturity (PB-026); PB-085 stale-cell marking; PB-034 notebook wizard from a table; PB-027 column completion |
| Local Execution and Enterprise Services | Defensible savings | Measured vs estimated CU rolled up across installations in the central Optimize lakehouse, with the method documented (PB-089, PB-050) |
| Enterprise Services | Data quality and lineage | PB-060 sensitive data classification beside Fabric sensitivity labels; PB-019 late-arriving rows; PB-031 schema evolution |
| Databricks and Snowflake | Snowflake parity grows | Snowpark local testing, dbt projects, deployment, per the Snowflake plan |
| Platform Engineering | Scale | Multiple workers (workloads design), PB-016 reproducible environments |

## Changes

| Week of | Change | Why |
| --- | --- | --- |
| 2026-10-28 | First version | Orchestrator brief; built from the backlog, inventory and open PRs on main d0747a6 |
| 2026-10-28 (evening) | Queues refreshed after the first day's work; a limit of four PRs waiting for review per workstream | Review and CI became the bottleneck: about 20 drafts waited for review and CI queued over 100 runs |
| 2026-11-02 | Weeks 1 to 4 rebuilt around the enterprise audit's remaining gaps; 12 new story issues #1664 to #1675 | The audit (54/100, not enterprise-ready) replaced the old week-1 plan, most of whose stories had merged; Chris's rule that open issues track only work someone can do today |
| 2026-11-09 | Week 2: the weeks 1 to 4 table shows what week 1 finished; queues refilled with 18 hardening stories, WR-101 to WR-603, kept in workstreams.json (a lead opens the issue when it starts one) | Most of last week's 12 stories merged (689 PRs merged in the week); a Fortune 100 rollout needs hardening first, and new features still wait on Chris's go |
