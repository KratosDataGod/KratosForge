# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-26 23:42 UTC, main 73c4ae4.** Covers the PRs merged from #135 to #791.

## Summary

- 44 PRs merged since the morning check, #742 to #791 plus #725 and #726. They bring the Semantic
  model tab's Direct Lake check and data dictionary, semantic model rules SM001 to SM007 and a
  notebook Best practices panel, the warehouse Monitoring tab (WH-19), PC schedules for pipelines
  (P6), VS Code phase 2a (run cells from a paired window), the Databricks SQL warehouse engine,
  following a data product, asking another team for data and Who's affected on incidents, action-first
  Home and notebooks (AF-2, AF-4), help on request, and IT hosting the engine with a pinned checksum.
- Inventory: 142 features. 119 are Implemented, 12 Partially Implemented, 4 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 127 Partially Verified and 12 Unverified. None has been verified on a Fabric tenant.
- Backlog: 101 items (5 P0, 31 P1, 52 P2, 13 P3). 48 are Proposed, 11 In progress, 37 In review and 5 Accepted
  (PB-108, PB-018, PB-044, PB-054 and PB-065, each with documented limitations). PB-112 moved to In review (#753).

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Slow-line uploads fixed (#786) |
| SQL and warehouse | Build full warehouse development experience | PB-106 | WH-19 merged; WH-16 #755 draft; tenant run next |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | P6 PC schedules merged; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | Direct Lake check, dictionary, SM rules merged |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-108, PB-065 | Follow, Ask for data, Who's affected merged; #744, #763, #765, #793, #794 drafts |
| Developer experience | UX oversight; action-first; VS Code | PB-102, PB-113, PB-109 | AF-2, AF-4, phase 2a merged; #795 phase 2b draft |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Engine checksum pinned (#768) |
| Release | Release management | | Working |

## Open pull requests

At 23:45 UTC there were 17 open PRs. Ready for review: #796 (docs audit 4), #792 (platform repository
landing zones and templates) and #782 (Databricks Connect). Drafts: #795 (VS Code phase 2b), #794 and
#793 (landing sources, domain groups), #789 (stage one block), #765 and #763 (mesh owner coverage, access
reviews), #755 (WH-16), #744 (HD-030 policy), #701 (data contracts), #560 (BPA) and #449 (animation
style guide). Fork PRs #288, #289 and #290 were reviewed FAIL and are recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: a tenant run of the warehouse and Data Factory writes, governance
steps 8 and 12, Direct Lake on zstd, and the privacy and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
