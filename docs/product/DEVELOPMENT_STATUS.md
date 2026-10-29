# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-29 11:42 UTC, main 188d5a93.** Covers the PRs merged from #135 to #1050.

## Summary

- 82 PRs merged since the evening check's commit (b19b7456), 63 of them numbered #973 to #1050. The tenant smoke-test harness (#884) and its
  central platform flows (#919) are in, and so are the OneLake lease and MERGE live-test checks (#892)
  and the pipeline mailbox's run check (#931). None of these has run on a tenant yet. Also merged:
  - Snowflake as a Preview engine (#993, #995).
  - Block policies stop promote (GOV-003, #924).
  - Accessibility fixes from the audit (#926, #951, #988, #990).
  - Paged reads that can't loop forever (#944), and Fabric's own error text (#939).
  - A mypy and ruff check in CI (#1011, #1032).
  - The Apache-2.0 license (#973) and a new README (#998).
- 3 more items moved to In review: PB-031, PB-072 and PB-083. PB-067's file clean-up merged (#950), but its
  run dialog isn't built, so it stays In progress. PB-031 can't widen a type
  without a rewrite (delta-rs and the Fabric-safe profile), so its first criterion is Chris's call.
- 23 items are In review and wait on the product owner. Two of them have changed acceptance criteria
  (PB-049, PB-072), which also need him.
- Inventory: 142 features. 119 are Implemented, 13 Partially Implemented, 4 Implemented but Not Exposed, 2 Missing and 4 Planned.
  3 are Verified, 128 Partially Verified and 11 Unverified. None has been verified on a Fabric tenant end to end.
- Backlog: 106 items (5 P0, 31 P1, 54 P2, 16 P3). 19 are Proposed, 24 In progress, 23 In review and 40 Accepted.

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
| Databricks and Snowflake | Fabric and Databricks unified development | PB-116, PB-117, PB-118 | Livy draft #891; Snowflake engine and SQL merged as Preview (#993, #995) |
| Release | Release management | | Working |

## Open pull requests

See GitHub for the live list. Every queued story with an open PR is In progress in the backlog.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: acceptance of the 23 In review items and the PB-049 and PB-072 criteria changes, a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events can reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
