# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-27 11:42 UTC, main 6159e50.** Covers the PRs merged from #135 to #809.

## Summary

- 26 PRs merged overnight, including three long-held drafts: BPA in Fabric (#560), versioned data
  contracts (#701) and the animation style guide (#449). Data mesh became its own page with Home,
  Domains, Products and Requests (#801 to #806), with access reviews, owner coverage and Entra ID
  groups per role. Also: Check now covers SQL scripts and scheduled notebooks (PB-111), a T-SQL
  Best practices chip, the date table mark, VS Code phase 2b (KratosForge in VS Code's kernel picker),
  Databricks Connect, landing copy job requests, platform-repo landing zones and staging one block in
  Source control.
- Inventory: 142 features. 119 are Implemented, 12 Partially Implemented, 4 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 127 Partially Verified and 12 Unverified. None has been verified on a Fabric tenant.
- Backlog: 101 items (5 P0, 31 P1, 52 P2, 13 P3). 46 are Proposed, 11 In progress, 39 In review and 5 Accepted.
  PB-111 (#798) and PB-082 (#701) moved to In review.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Review ready |
| SQL and warehouse | Build full warehouse development experience | PB-106 | WH-19 merged; WH-16 #755 draft; tenant run next |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | DF-8 and P5 need a go; #799 staging cleanup draft |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA engine merged; live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-082, PB-111, PB-112 | Data mesh page rebuilt; #744 draft |
| Developer experience | UX oversight; action-first; VS Code | PB-102, PB-113, PB-109 | VS Code phase 2b merged |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Working |
| Release | Release management | | Working |

## Open pull requests

At 11:45 UTC there were 7 open PRs: #811 (VS Code extension test fix), drafts #799 (Dataflow staging
lakehouse cleanup), #755 (WH-16) and #744 (HD-030 policy), and the fork PRs #288, #289 and #290,
reviewed FAIL and recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: a tenant run of the warehouse and Data Factory writes and the BPA run,
governance steps 8 and 12, Direct Lake on zstd, and the privacy and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
