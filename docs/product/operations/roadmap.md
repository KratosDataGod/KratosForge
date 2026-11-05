# 12-week roadmap

Rolling plan from week 1 (starting Monday 2026-11-02) to week 12 (ending 2027-01-24). The weekly review moves it on by a week, records what changed and why, and keeps it in line with the [work queues](README.md#work-queues). Items with a PB id are in [the backlog](../PRODUCT_BACKLOG.md). Anything marked *needs Chris* waits for his mockup approval, his go on a new feature, or his yes on capacity spend.

Why this order: as of 2026-11-02 about 650 PRs merged into main since 2026-10-26. The enterprise engineering audit ([scorecard](../../audits/ENTERPRISE_READINESS.md)) scores KratosForge 54 of 100 and says it is not enterprise-ready: builds are unsigned, nothing has run against a live tenant, and data integrity, enterprise deployment and performance score lowest. Most audit findings (EA-001 to EA-029) are already fixed. A Fortune 100 rollout needs proof and reliability before breadth, so weeks 1 to 4 close the audit's remaining gaps and turn built work into verified work. Since 2026-11-02, open issues track only work someone can do today; blocked items keep their state in the backlog.

## Weeks 1 to 4: stabilize and verify (2026-11-02 to 2026-11-29)

| Workstream | Goal | Stories |
| --- | --- | --- |
| All | Release-critical | EA-009: signing (waits on Microsoft's identity check, *needs Chris*) and one live run of the FD-101 harness on the non-production workspace (*needs Chris*: capacity yes) |
| Local Execution | Correct numbers, measured speed | #1664 benchmark in CI (EA-029); #1665 a test on every append path (EA-001); PB-015's live Prove run (*needs Chris*) |
| Microsoft Fabric | Warehouse parity without surprises | #1666 every characterized T-SQL gap runs or is refused; #1667 retry loops move to retrying.py (EA-028); PB-002 ten-flow tenant smoke test (*needs Chris*) |
| Databricks and Snowflake | Parity a user can read | #1668 generated Spark function coverage list; #1669 JSON and Parquet reader options (EA-013 pattern); Databricks live run (*needs Chris*: workspace) |
| Enterprise Services | Deployment an IT team can trust | #1670 policy changes outside IT's channel are recorded; #1671 one IT deployment checklist; MSI or MSIX package (*needs Chris*: new capability) |
| Developer Experience | Accessibility that doesn't regress | #1672 keyboard-only check on every page (automatable half of EA-030); #1673 axe guard test; Learn content PB-126 (PRs open) |
| Platform Engineering | Shared plumbing, stricter lint | #1674 SQLite stores share one corruption-safe helper (EA-028); #1675 ruff BLE (EA-027); mypy ratchet PRs |

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
