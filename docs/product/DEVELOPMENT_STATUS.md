# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-30 23:42 UTC, main 0c96c9ce.** Covers the PRs merged from #135 to #1191.

## Summary

- 23 PRs merged since the last check's commit (02118fa4), besides the product data PRs.
- Acceptance batch 2 (2026-10-30, #1163): the product owner accepted 19 items, 10 of them with
  documented limitations, and approved the changed criteria for PB-031, PB-049 and PB-072.
- Merged:
  - Learn guidance levels, with Professional the default (GD-01, #1161), and Try the fix and Compare
    beside SQL findings (GD-02, #1162). Also Explain and Lesson buttons (#1160) and the Microsoft
    Fabric path (#1159). All Preview.
  - The Getting started panel on Home (#1147).
  - Column completion in notebook `%%sql` cells, offline too (PB-027, #901).
  - Prove takes Spark notebooks with MERGE, UPDATE and DELETE (PB-015, #1157).
  - The AI client checklist run with Claude Code, Codex CLI and curl (PB-096, #1185).
  - The remaining allow_ policy switches fail closed (PB-061, #1150). The engine's method lists are
    checked against each other (PB-098, #1152). HD-050 slices 6 and 7 (#1153, #1156).
  - Databricks SQL whole results and functions (#1180, #1183), and retries for remote Spark and
    Databricks calls (#1191).
- PB-027 and PB-096 moved to In review. PB-061 is In progress: the keys FD-105 lists are still to
  decide. PB-015 stays In progress for its live Prove run.
- Inventory: 143 features. 121 are Implemented, 13 Partially Implemented, 3 Implemented but Not Exposed, 2 Missing and 4 Planned.
  3 are Verified, 129 Partially Verified and 11 Unverified. Fabric sign-in, listing and Spark SQL now have one recorded live run.
- Backlog: 109 items (5 P0, 32 P1, 55 P2, 17 P3). 15 are Proposed, 25 In progress, 10 In review and 59 Accepted.

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
two release gates. Also waiting: acceptance of the 10 In review items, a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events can reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
