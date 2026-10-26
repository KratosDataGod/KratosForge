# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-26, main bb084c6.** Covers the PRs merged from #135 to #741.

## Summary

- 63 PRs merged from #676 to #741. They bring warehouse phase 3 (changes after a preview and confirm, the
  Project tab in Fabric's Git layout, compare, deploy and estimated plans, WH-09 to WH-13), Data Factory
  copy jobs and dataflows tabs with Run in Fabric and Refresh in Fabric (DF-6, DF-7), VS Code phase 1
  (kratosforge:// links and repository setup), the Ossie card, the action framework (AF-1), and fixes for
  PB-018, PB-044, HD-030, QA-011 and SH-9.
- Inventory: 142 features. 119 are Implemented, 12 Partially Implemented, 4 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 127 Partially Verified and 12 Unverified. None has been verified on a Fabric tenant.
- Backlog: 101 items (5 P0, 31 P1, 52 P2, 13 P3). 49 are Proposed, 11 In progress, 36 In review and 5 Accepted
  (PB-108, PB-018, PB-044, PB-054 and PB-065, each with documented limitations). Merged work waits on the [implementation review](IMPLEMENTATION_REVIEW_PROCESS.md).

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Review ready |
| SQL and warehouse | Build full warehouse development experience | PB-106 | Phase 3 (WH-09 to WH-13) merged; tenant run next |
| Data Factory | Build full Data Factory experience | PB-107 | DF-1, 2, 5, 6, 7 merged; DF-8 deploy needs a go |
| Pipelines | Local Fabric pipeline execution | PB-037 | Review ready; P5 needs a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | Ossie card merged; #725, #726 open |
| Governance | Enterprise governance foundation | PB-108, PB-065 | PB-108 accepted; HD-030 policy #744 open |
| Developer experience | UX oversight; action-first; animations; VS Code | PB-102, PB-113, PB-109 | AF-1 and VS Code phase 1 merged |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Working |
| Release | Release management | | Working |

## Open pull requests

At 11:50 UTC there were 17 open PRs. Follow-ups and fixes: #742, #743, #745, #746 and #747. New slices
of approved plans: #725 and #726 (semantic model tab), #744 (HD-030 policy), #748 (Direct Lake check),
#749 (mesh requests) and #750 (shared keys database). Drafts waiting on the product owner: #560 (BPA),
#449 (animation style guide) and #701 (versioned data contracts). Fork PRs #288, #289 and #290 were
reviewed FAIL and are recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: a tenant run of the warehouse and Data Factory writes, three VS Code
decisions, governance steps 8 and 12, Direct Lake on zstd, and the privacy and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
