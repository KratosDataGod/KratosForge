# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-27 23:42 UTC, main 2225e28.** Covers the PRs merged from #135 to #826.

## Summary

- 15 PRs merged since the morning check (#811 to #826). Warehouses open on the Warehouse page and the
  Fabric portal goes full screen (#823); Data mesh, Platform, Optimize and Monitor share one page layout
  (#825); only a product's owners re-publish it (#817); updates follow the channel when Preview moves on
  (#821); and side by side explains the upload floor (#819, #822). A first warehouse tenant run on
  2026-10-27 led to #808 and #809.
- Implementation reviews for PB-005, 006, 008, 024, 025, 059, 082, 111 and 112 are in
  docs/product/reviews (#815). The product owner's decision is pending, so their states are unchanged;
  the inventory text the reviews named stale (FEAT-AUTH-03, FAB-05, FAB-11, GOV-03, GOV-09, MESH-05,
  SEC-02 and PB-024's evidence) is corrected.
- Inventory: 142 features. 119 are Implemented, 12 Partially Implemented, 4 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 128 Partially Verified and 11 Unverified. None has been verified on a Fabric tenant end to end.
- Backlog: 101 items (5 P0, 31 P1, 52 P2, 13 P3). 46 are Proposed, 11 In progress, 39 In review and 5 Accepted.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | #829 cores draft |
| SQL and warehouse | Build full warehouse development experience | PB-106, PB-024, PB-025 | First tenant run done; WH-16 #755 draft |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | #799 draft; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-059, PB-082, PB-111 | Reviews wait on the product owner; #828 draft |
| Developer experience | UX oversight; action-first; VS Code | PB-102, PB-113, PB-109 | #827 open |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | BQ-013 fix pending |
| Release | Release management | | Working |

## Open pull requests

At 23:45 UTC there were 9 open PRs: #827 (shared navigation for Platform, Optimize and Monitor), drafts
#829 (cores on this PC), #828 (product groups), #799 (Dataflow staging cleanup), #755 (WH-16) and #744
(HD-030 policy), and the fork PRs #288, #289 and #290, reviewed FAIL and recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: the decision on nine implementation reviews (#815), a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
