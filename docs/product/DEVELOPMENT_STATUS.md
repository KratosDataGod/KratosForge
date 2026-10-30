# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-29 23:42 UTC, main 02118fa4.** Covers the PRs merged from #135 to #1146.

## Summary

- 59 PRs merged since the morning check's commit (188d5a93). The first recorded live run on a Fabric
  tenant happened: on 2026-10-29 the KUDP comparison signed in, listed workspaces and lakehouses, and
  ran 10 Spark SQL queries in a Fabric Spark session against DuckDB on this PC. Eight matched. One
  difference is real (decimal division) and one was a local bug, fixed by #1144
  ([platforms.md](../platforms.md#live-runs)).
- New programs, now tracked in the backlog:
  - Learn (PB-119, LX-01 to LX-08, Preview).
  - Platform support levels and one engine contract (PB-120, KUDP).
  - Power BI embedding (PB-121, PBE). Its design is proposed; the host frame and engine calls are built.
- Also merged:
  - The notebook engine picker (Preview, #1096).
  - MERGE, UPDATE and DELETE on this PC (Preview, PB-015).
  - Fabric Spark sessions through Livy (PB-116).
  - The Databricks conversion loss report (PB-118).
  - Running several notebooks at once from Home (#1131).
  - Three new notebook rules (#1116).
- PB-116 and PB-118 moved to In review. PB-015 stays In progress: its Prove run on PC and Fabric is
  still to do.
- 25 items are In review and wait on the product owner (acceptance batch 2 covers 23 of them; PB-116
  and PB-118 are new).
- Inventory: 143 features. 121 are Implemented, 13 Partially Implemented, 3 Implemented but Not Exposed, 2 Missing and 4 Planned.
  3 are Verified, 129 Partially Verified and 11 Unverified. Fabric sign-in, listing and Spark SQL now have one recorded live run.
- Backlog: 109 items (5 P0, 32 P1, 55 P2, 17 P3). 18 are Proposed, 26 In progress, 25 In review and 40 Accepted.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-094, PB-068, PB-015 | Kernel measured (#895); key lookups merged (#913); #933 open |
| Fabric, SQL and warehouse | Build full warehouse development experience | PB-002, PB-043, PB-106 | Smoke-test harness merged (#884, #919); the live run waits on a yes |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | #799 draft; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-057, PB-053, PB-083 | Block policies stop promote (GOV-003, #924); endorsed filter (#889) |
| Developer experience | UX oversight; action-first; VS Code | PB-078, PB-077, PB-070, PB-063 | Audit fixes (#926, #951), tab rows (#988), High Contrast (#990), Browse buttons (#987) |
| Testing and security | Codebase audit; engineering quality | PB-074, PB-072, PB-051, PB-101 | Long calls bounded (#898); mypy and ruff in CI (#1011, #1032); HD-050 slices 3 and 4 |
| Databricks and Snowflake | Fabric and Databricks unified development | PB-116, PB-117, PB-118 | Livy (#891) and the loss report (#1053) merged; engine picker (#1096); Fabric verified to level 2 |
| Release | Release management | | Working |

## Open pull requests

See GitHub for the live list. Every queued story with an open PR is In progress in the backlog.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: acceptance of the 25 In review items and the PB-049 and PB-072 criteria changes, a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events can reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
