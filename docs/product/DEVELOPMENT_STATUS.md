# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-31 23:42 UTC, main b3c5b223.** Covers the PRs merged from #135 to #1357.

## Summary

- Evening check, 2026-10-31: 99 PRs merged after f3750b9d (#1205 to #1357), the full-speed day.
  Most are hardening and tests. The engine is ready for DuckDB 2.0 (#1341's parts), OneLake reads,
  writes, shortcut checks and leases wait out throttling, governance requests say what stays in
  Fabric after a decline or withdrawal, and ten more screens have real-browser tests. Also: the
  compute check follows %run and notebook.run and reports Unknown (PB-014), support bundle slice 1
  (PB-062), Learn GD-03, GD-06 and GD-10, and Databricks, Spark and Snowflake SQL that answers as
  those platforms do. PB-014 and PB-062 are now In progress.
- Morning check, 2026-10-31: three more PRs merged. Snowflake functions are checked value by
  value against Snowflake's docs (PB-117, #1202), a time limit or early Cancel says truthfully
  whether a remote statement stopped (#1201), and every platform role write leaves an audit row
  (PB-052, #1200). PB-117 moved to In review in the queue refresh (#1196).
- Evening check, 2026-10-30: 23 PRs merged after 02118fa4, besides the product data PRs.
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
- Backlog: 109 items (5 P0, 32 P1, 55 P2, 17 P3). 13 are Proposed, 27 In progress, 10 In review and 59 Accepted.

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
