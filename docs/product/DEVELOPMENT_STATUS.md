# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Product check of 2026-10-28 11:42 UTC, main cdbb967.** Covers the PRs merged from #135 to #838.

## Summary

- 10 PRs merged since the evening check (#827, #829 and #831 to #838). Platform, Optimize and Monitor
  share Data mesh's layout (#827, #831); side by side can hold the PC's run to Fabric's cores (#829);
  projects have owners and members, and joining a domain is a request (#834); a consumer's widened
  accepts range now reaches the catalog (#836, PB-082 finding 2); the warehouse explorer is a keyboard
  tree (#835, PB-025) and the 2026-10-27 warehouse tenant run is written up (#833); and policy
  exceptions warn 14 days before they end and can be ended early (#838, PB-059).
- The nine implementation reviews in docs/product/reviews (#815) still wait on the product owner, so
  their states are unchanged. Fixes since then cover PB-025 findings 1 and 2, PB-059 findings 2 and 3,
  and PB-082 finding 2.
- Inventory: 142 features. 119 are Implemented, 12 Partially Implemented, 4 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 128 Partially Verified and 11 Unverified. None has been verified on a Fabric tenant end to end.
- Backlog: 101 items (5 P0, 31 P1, 52 P2, 13 P3). 46 are Proposed, 11 In progress, 39 In review and 5 Accepted.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Cores on this PC merged (#829) |
| SQL and warehouse | Build full warehouse development experience | PB-106, PB-024, PB-025 | Tenant run written up; WH-16 #755 draft |
| Data Factory and pipelines | Data Factory; Local Fabric pipeline execution | PB-107, PB-037 | #799 draft; DF-8 and P5 need a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-110, PB-112 | BPA live run next |
| Governance and mesh | Enterprise governance foundation; data mesh | PB-059, PB-082, PB-111 | Reviews wait on the product owner; #828 draft |
| Developer experience | UX oversight; action-first; VS Code | PB-102, PB-113, PB-109 | Shared page layout merged (#827) |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Working |
| Release | Release management | | Working |

## Open pull requests

At 11:45 UTC there were 7 open PRs: drafts #828 (product groups), #799 (Dataflow staging cleanup),
#755 (WH-16) and #744 (HD-030 policy), and the fork PRs #288, #289 and #290, reviewed FAIL and
recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: the decision on nine implementation reviews (#815), a tenant run of
the Data Factory writes and the BPA run, governance steps 8 and 12, Direct Lake on zstd, and the privacy
and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A product
check twice a day (11:42 and 23:42 UTC) refreshes this data and proposes assignments. Pull request events reach the thread that opened the PR. Quality review gates every PR,
and only Release management merges.
