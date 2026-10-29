# 12-week roadmap

Rolling plan from week 1 (starting Monday 2026-11-02) to week 12 (ending 2027-01-24). The weekly review moves it on by a week, records what changed and why, and keeps it in line with the [work queues](README.md#work-queues). Items with a PB id are in [the backlog](../PRODUCT_BACKLOG.md). Anything marked *needs Chris* waits for his mockup approval, his go on a new feature, or his yes on capacity spend.

Why this order: as of 2026-10-28 about 370 PRs merged into main in the week from 2026-10-21, main CI is green, and 3 of 142 features are verified end to end. A Fortune 100 rollout needs proof and reliability before breadth, so weeks 1 to 4 turn built work into verified work.

## Weeks 1 to 4: stabilize and verify (2026-11-02 to 2026-11-29)

| Workstream | Goal | Stories |
| --- | --- | --- |
| Microsoft Fabric | Live evidence for the core flows | PB-002 ten-flow tenant smoke test in a throwaway workspace on Trial capacity (*needs Chris*: capacity yes); PB-003 OneLake leases and MERGE writes live; PB-043 cancel a Fabric run where people watch it; PB-013 last parity defect |
| Platform Engineering | Nothing freezes, nothing ships unaudited | PB-074 release waits for the audit job; PB-072 every long engine call has a limit and never blocks a quick one; PB-051 REST client retries and token refresh; PB-001 signing (waits on Microsoft); PB-010 security docs match the code |
| Local Execution | Correct numbers and the most common write pattern on the PC | PB-094 kernel-process measurements; PB-068 key lookups without whole-table reads; PB-015 MERGE, UPDATE and DELETE semantics (after PB-012) |
| Enterprise Services | Policies checked without Fabric | PB-057 offline policy validation; PB-083 endorsed-items wording; PB-053 scheduled shortcut health check; PB-052 central platform on a tenant (*needs Chris*) |
| Developer Experience | Docs and Preview tags tell the truth; editor regressions get caught | PB-078 Preview ids for every Preview feature; PB-077 docs match the code; PB-070 notebook editor UI tests |
| Databricks and Snowflake | Architecture before code | Fabric Spark (Livy) Preview engine draft; Snowflake audit, ADRs and parity matrix (research only); Databricks conversion loss report |
| All | Acceptance | One acceptance batch for the 39 items In review, with evidence prepared by quality |

Exit: PB-002 run with defects filed; no P0 or P1 reliability item open without an owner; every In review item accepted, rejected or given a dated fix.

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
| Enterprise Services | Data quality and lineage | PB-060 sensitive data classification beside Purview; PB-019 late-arriving rows; PB-031 schema evolution |
| Databricks and Snowflake | Snowflake parity grows | Snowpark local testing, dbt projects, deployment, per the Snowflake plan |
| Platform Engineering | Scale | Multiple workers (workloads design), PB-016 reproducible environments |

## Changes

| Week of | Change | Why |
| --- | --- | --- |
| 2026-10-28 | First version | Orchestrator brief; built from the backlog, inventory and open PRs on main d0747a6 |
| 2026-10-28 (evening) | Queues refreshed after the first day's work; a limit of four PRs waiting for review per workstream | Review and CI became the bottleneck: about 20 drafts waited for review and CI queued over 100 runs |
