---
site_group: Internal engineering
---

# Databricks path: delivery plan

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

> **Dated assessment.** The baseline and date recorded below apply to this assessment, not the current release. See [Development status](../../product/DEVELOPMENT_STATUS.md) for subsequent work and [GitHub Releases](https://github.com/KratosDataGod/KratosForge/releases) for shipped builds. This website review has not re-certified the assessment.

## Threads

Existing threads take the work their area already owns. One new thread is proposed, for Databricks
exercise content, once the format and the first exercise have landed (the DP-700 content thread is
busy with its own batches).

| Directive role | Thread | Stories |
| --- | --- | --- |
| Academy/Training (Databricks path lead) | Build Databricks Associate Academy path (this plan) | DBA-001, 002, 003, 006, 013 |
| Academy/Training (shared Academy) | Build KratosForge Academy for DP-700 | DBA-014, 016; reviews DBA-001's registry changes |
| Databricks Platform | Lead Databricks and Snowflake parity | DBA-009 (bundles #1747), co-owns DBA-002 and DBA-013 |
| Grading | Build Academy grading sandbox | DBA-011; reviews the DBA-003 runner |
| Assessments | Build Academy exam engine | DBA-012 |
| Content | Proposed: Author Databricks Academy exercises (until then, this thread) | DBA-004, 005, 015 and exercise batches |
| Data Integration and Orchestration | Data Factory | DBA-007 |
| Governance | Enterprise governance | DBA-008 |
| Notebook Experience | Spark functions locally | Reviews the DBA-003 shim and DBA-005 streaming reuse |
| Data Engineering | Local data engine | Reviews DBA-004 dialect use |
| DevOps | Fabric workspace Git awareness | Reviews DBA-009 Git exercises |
| Performance | Optimize central lakehouse | DBA-010 |
| UX | UX | Mockup for the catalog's second path and Databricks exercise views |
| Security | Bug fix and security QC | Reads DBA-002, 003, 008, 011, 013 |
| Quality, Release | Second quality reviewer, Release management | Every PR |
| Master Product Manager | Product check | Twice-daily check covers the path |

## Dependency graph

```
DP-700 stack (in review):  #1776 registry ─ #1778 format ─ #1779 readiness ─ #1782 workspace
                           #1777 validators ─ PR-F sandbox                    #1781 exam engine
                                    │              │                                │
DBA-001 pack + platform-aware registry (on #1782) ─┼────────────────────────────────┼─ DBA-012 question bank
        │                                          │                                │
        ├─ DBA-011 idempotent (on #1777) ──────────┤
        │                                          │
        └─ DBA-003 pyspark_lab + E19  ◄────────────┘  = the vertical slice
                │
                ├─ DBA-004 dbsql_lab ─ content batches (domain 3, then 2)
                ├─ DBA-005 file arrival, Auto Loader, COPY INTO
                ├─ DBA-006 declarative pipelines
                ├─ DBA-007 jobs ─ DBA-010 runs and stages data
                ├─ DBA-008 Unity Catalog
                └─ DBA-009 bundles and Git (#1747)
DBA-002 adapter ─ DBA-013 preflight, ledger, cleanup ─ DATABRICKS VERIFIED versions (+DV)
DBA-014 shared competencies, DBA-015 capstones, DBA-016 change monitoring: Phases 4 and 5
Screens: wait for the Academy mockup go (v7) and a Databricks catalog mockup
```

## First PRs

The vertical slice: Academy, the Databricks Data Engineer Associate path, objective **3.1**, exercise
**E19 Clean bronze orders into a silver Delta table with PySpark**, run on this PC, graded, and 3.1
gaining LOCAL VERIFIED evidence in readiness.

| PR | Contents | Base | Review |
| --- | --- | --- | --- |
| DBA-A | This plan in `docs/academy/databricks/` | main | Second quality, RM |
| DBA-B | DBA-001: platform-aware registry and classes, DP-700 data migrated, `packs/dbx-de-associate/` with all 33 objectives and gap records; no UI | #1782 | Academy lead, Second quality, RM |
| DBA-C | DBA-011 part 1: `idempotent` validator | #1777 | Sandbox thread, Security, Second quality, RM |
| DBA-D | DBA-003: `pyspark_lab` adapter on the sandbox, the Delta shim, E19 live with reference and planted wrong answers, evidence row into readiness | DBA-B, DBA-C, PR-F | Security, Second quality, RM |
| DBA-E | First content batch: E20, E21, E23, E24, E26, E31, E33 (domain 3) | DBA-D | Second quality, RM |

Stacked PRs are retargeted to `main` when their base squash-merges, and say which PR merges first.
At most two of this path's PRs at review at once.
