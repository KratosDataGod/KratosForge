# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Cycle of 2026-10-25, main 524e6a3.** Covers the PRs merged from #135 to #650.

## Summary

- 86 PRs merged from #561 to #650 in about 40 hours, so roughly 40 a day from 22 to 24 October.
- Inventory: 140 features. 115 are Implemented, 11 Partially Implemented, 6 Implemented but Not Exposed, 4 Missing and 4 Planned.
  3 are Verified, 124 Partially Verified and 13 Unverified. None has been verified on a Fabric tenant.
- Backlog: 98 items (5 P0, 31 P1, 49 P2, 13 P3). 54 are Proposed, 9 In progress, 35 In review and none Accepted:
  merged work waits on the [implementation review](IMPLEMENTATION_REVIEW_PROCESS.md).
- CI run 2235 passed every job on main at 524e6a3.

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Review ready |
| SQL and warehouse | Build full warehouse development experience | PB-106 | WH-06, WH-07 next |
| Data Factory | Build full Data Factory experience | PB-107 | Screens wait on the mockup |
| Pipelines | Local Fabric pipeline execution | PB-037 | Review ready; P5 needs a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-108, PB-110 | Wait on the product owner |
| Governance | Enterprise governance foundation | PB-108, PB-059 | #648, #651 in quality |
| Developer experience | UX oversight; animations; VS Code | PB-102, PB-109 | Wait on the product owner |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Re-checks dispatched |
| Release | Release management | | Working |

## Open pull requests

#643, #648, #649 and #651 (best-practice checks, in quality); #560 (BPA, waits on a cell error from
the product owner); #449 (animation style guide, waits on the product owner); fork PRs #288, #289 and
#290 (reviewed FAIL, recommended to close).

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: a sign-in check on Stable for DPAPI (PB-005), the Data Factory and
Ossie mockups, warehouse decisions D1 and D3, three VS Code decisions, governance steps 8 and 12,
Direct Lake on zstd, and the privacy and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. No recurring
scheduled task runs, and pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
