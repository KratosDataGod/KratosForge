# Development status

The state of development at the end of the last product management cycle. It's replaced each
cycle, and the history is in Git. The counts come from `python docs/product/tools/product_data.py summary`.

**Daily check of 2026-10-25, main 5ae85e5.** Covers the PRs merged from #135 to #675.

## Summary

- 32 PRs merged from #643 to #675 overnight. They bring best-practice checks and templates, WH-06 saved queries and WH-07 history, Data Factory DF-1 in Monitor plus DF-2, DF-5 and the DF-7 engine, Open in VS Code, and QA-002, HD-012, HD-015, HD-026 and SH-8.
- Inventory: 142 features. 117 are Implemented, 13 Partially Implemented, 5 Implemented but Not Exposed, 3 Missing and 4 Planned.
  3 are Verified, 127 Partially Verified and 12 Unverified. None has been verified on a Fabric tenant.
- Backlog: 98 items (5 P0, 31 P1, 49 P2, 13 P3). 51 are Proposed, 10 In progress, 37 In review and none Accepted:
  merged work waits on the [implementation review](IMPLEMENTATION_REVIEW_PROCESS.md).

## Workstreams

| Workstream | Owner thread | Backlog | State |
| --- | --- | --- | --- |
| Local execution and compute | Local data engine and OneLake publishing | PB-099 | Review ready |
| SQL and warehouse | Build full warehouse development experience | PB-106 | Phases 1-2 done; phase 3 needs a go |
| Data Factory | Build full Data Factory experience | PB-107 | DF-1, 2, 5 on main; DF-6, DF-7 screen next |
| Pipelines | Local Fabric pipeline execution | PB-037 | Review ready; P5 needs a go |
| Semantic models | Best Practice Analyzer; TMDL and OSI conversion | PB-108, PB-110 | Wait on the product owner |
| Governance | Enterprise governance foundation | PB-108, PB-059 | Best-practice checks merged; In review |
| Developer experience | UX oversight; animations; VS Code | PB-102, PB-109 | Open in VS Code merged; phase 2 waits on the product owner |
| Testing and security | Codebase audit; engineering quality | PB-101, PB-103 | Re-checks dispatched |
| Release | Release management | | Working |

## Open pull requests

At 11:45 UTC there were 24 open PRs, #676 to #700, after Chris's call to get every thread working again. They are fixes and hardening (PB-018 #685, PB-044 #689, QA-011 #680, SH-9 #679), the next slices of approved plans (DF-7 Dataflows tab #700, Ossie card #681, KF-DE-001 #692), and motion and Preview UI. #677 is this product data update. #560 (BPA) and #449 (animation style guide) wait on the product owner. Fork PRs #288, #289 and #290 were reviewed FAIL and are recommended to close.

## Blocked on the product owner

Trusted Signing (PB-001) and a non-production workspace for the live harness (PB-002, PB-003) are the
two release gates. Also waiting: a sign-in check on Stable for DPAPI (PB-005), the Data Factory and
Ossie mockups, warehouse decisions D1 and D3, three VS Code decisions, governance steps 8 and 12,
Direct Lake on zstd, and the privacy and support details.

## Orchestration

Development runs when the product owner or a thread wakes the project coordinator. A daily product
check (11:42 UTC) refreshes this data and proposes assignments. Pull request events don't reach the threads. Quality review gates every PR,
and only Release management merges.
