# Execution plan: week of 2026-11-16

This plan runs from Saturday 2026-11-14 to Saturday 2026-11-21. Chris's ask (2026-11-13) was to use a full week of capacity by Wednesday, use his capacity reset, and then do another full week of development by Saturday. Work should be prioritized, bundled where that helps, and kept inside every agent review gate, with as few asks to him as possible. This page covers the order of work, who does it, how long Git takes at each step, and the few points where Chris is needed. It sits under the [12-week roadmap](roadmap.md) and the [work queues](README.md#work-queues), and doesn't replace them.

## What we can and can't see

We can't see Chris's usage meter, so this page never states a usage percentage as a measured fact. We plan by **how much work runs at once**: build threads, PRs in CI, and reviews. Last week's merge counts on main are measured. The busiest days were 142 and 157 merges (2026-11-06 and 2026-11-07), and the slowest were 37 and 38 (2026-11-10 and 2026-11-12). The slow days came from three things, not from slow CI:

- the no-go hardening queue ran dry;
- work was waiting on typed goes and mockups;
- runner time was lost to the GitHub outage on 2026-11-11.

To get back to the busy-day rate, the work has to be ready and approved before the threads run out. That is what Phase 0 below is for.

**The one number we ask Chris for:** at the Tuesday 22:00 UTC and Wednesday 09:00 UTC card roundups, his meter reading as he sees it. If it is behind the target, we add threads. If it is ahead, we keep going, because the reset is meant to be used.

## Priorities

Work is chosen in this order. Within each tier, the order is usefulness × reach ÷ effort, as in the editor parity plan (#2579).

1. **Keep main green and secure.** A red main, a Security read or a data-integrity question always comes first, as it does today.
2. **Approved work that is ready to build.** These need no new go.
    - Editor parity wave 1, the items marked *No* for a mockup: Chris's go for the program was given 2026-11-13.
    - Fortune 100 hardening: mutation batches, failure-path tests, Windows paths.
    - Academy exercises.
3. **Work that is approved once Chris gives one bundled go.** This is the list under [The bundled go](#the-bundled-go). He answers once and does not approve each item.
4. **Work that waits on Chris for something only he can do.** This covers signing, the Run 10 tenant run, the soak log and the Chris-only checks. It is listed so nothing is forgotten, but no thread sits idle waiting for it.

## Phases

| Phase | When (UTC) | Goal | What runs |
| --- | --- | --- | --- |
| 0 | Sat 11-14 to Mon 11-16 17:00 | Have every item ready to build before Monday | The editor parity *No* items start now (items 1, 2, 3, 5, 6, 10, 11, 12, 13 and 17). UX draws **one clickable mockup** covering every parity *Yes* item and the bundled features with a screen. Owners write a one-paragraph scope for each bundled feature, in its issue. Hardening continues |
| A | Mon 11-16 17:00 to Wed 11-18 | First full week of capacity | Chris answers the bundled go and the mockup once, at Monday's 17:00 roundup or the Tuesday 09:00 one. All build threads stay full: 2 PRs in CI each, with the next item queued behind them |
| Reset | Wed 11-18 | Chris uses his capacity reset | No pause in work. The Wednesday 09:00 roundup confirms the reset and the meter reading |
| B | Wed 11-18 to Sat 11-21 | Second full week of development | The larger items: the result grid, multi-statement runs, query parameters, the MCP server, lineage export and observability. Wave 1 finishes and wave 2 starts where Chris's go covers it |
| Close | Sat 11-21 22:00 | Count what is done | The parity score is recomputed from main. The weekly review (Monday 11-23 17:00) records results against this plan |

## Work list and owners

New threads are started by the coordinator, one branch per thread. **Bundle** means one PR carries the items together, because they touch the same files and one review covers them.

| # | Work | Bundle into one PR | Owner (thread) | Phase | Needs |
| --- | --- | --- | --- | --- | --- |
| 1 | SQL export as Excel and JSON; run the statement under the cursor; results pane keys | Parity items 1, 2 and 3 | New: *SQL editor parity* | 0 | Nothing more |
| 2 | Markdown rendering: CommonMark, tables, lists, links, images, math (markdown-it, KaTeX) | Parity item 5 | New: *Notebook parity* | 0 | Security read: untrusted Markdown and links |
| 3 | SQL hover and signature help; plan and profile for local queries | Parity items 6 and 11 | *SQL editor parity* | 0 to A | Nothing more |
| 4 | Go to definition and rename across cells; find inside outputs; export as PDF | Parity items 12, 17 and 10 | *Notebook parity* | 0 to A | Nothing more |
| 5 | Transactions on a warehouse as one guarded script | Parity item 13, after #9 | *Warehouse* | B | Security read; Workload sign-off if workloads change |
| 6 | Finish PySpark parity: the 15 missing DataFrame methods (#2360) | One PR per 5 methods | *Spark local* | A | Bundled go |
| 7 | Finish the notebook debugger: breakpoints and step-through (#2362) | One PR | New: *Debugger* | A | Bundled go; mockup only if a panel changes |
| 8 | Modern Delta profile: V-Order, deletion vectors, column mapping (#2361) | Read first, then write | New: *Delta profile* | A to B | Bundled go; data-integrity tests on every write |
| 9 | Result grid: sort, filter, resize, value viewer, copy as, statistics | Parity item 8 | *SQL editor parity* | B | Mockup |
| 10 | Several statements per run, a result tab for each | Parity item 9 | New: *SQL multi-result* | B | Mockup |
| 11 | Query parameters; open and save .sql files; result history | Parity items 7, 4 and 14 | *SQL multi-result* | B | Mockup |
| 12 | Editor settings (wrap, font size, key rebinding, Vim); table diagram; read-only report; scratch console | Parity items 16, 15, 18 and 19 | *Notebook parity* | B | Mockup |
| 13 | KratosForge MCP server for outside AI tools (#2363) | Read-only tools first | New: *MCP server* | B | Bundled go; Security read (an inbound surface) |
| 14 | Lineage impact and OpenLineage export (#2359) | One PR, then export | New: *Lineage* | B | Bundled go; mockup for the impact view |
| 15 | Data observability: freshness, volume and anomaly alerts (#2358) | Engine first, screen second | *Workload mgmt* | B | Bundled go; mockup for the screen |
| 16 | Hardening without a go: mutation batches, failure paths, Windows paths, flakes | As today | *Coverage*, *Codebase*, *Warehouse*, *Enterprise readiness*, *Workload* | All | Nothing more |
| 17 | Academy exercises and follow-ups | As today | *Academy-2* | All | Mockups as already agreed |

Dark mode (parity N60) stays out of this week unless Chris says otherwise, because it touches the standing light-shell design rule. Wave 2 rows that write to tables or open new connections (edit rows in the grid, connect to Azure SQL Database) also stay out: they need Chris's decision and a Security read first.

## Git: how long each step takes

The CI times below were measured from the last 40 pull request CI runs on 2026-11-12 and 2026-11-13. The review and merge times are this plan's **targets**. Release management and the Quality reviewers confirm them in review of this page.

| Step | Typical | Worst normal case | Basis |
| --- | --- | --- | --- |
| PR CI, from push to green | 30 min | 47 min (p90) | Measured, 31 successful runs. Fastest 14 min |
| Quality review, from green CI to verdict | 45 min | 90 min | Target. Four Quality reviewers. A re-check after a push starts the wait again |
| Security read, when one is needed | 2 h | 4 h | Target. Asked for when the PR opens, so it runs alongside CI |
| Wait for the next merge train | 1 h | 2 h | Target: a train every 2 hours (below) |
| Main CI after a merge | 35 min | 50 min | Measured on main, 2026-11-12 to 2026-11-13 |
| **PR to on main and green** | **about 3 h** | **about 5 h** | Sum of the above, with no Security read |
| PR with a Security read | about 4 h | about 7 h | The Security read replaces the Quality wait on the longest path |

### Merge trains

Release management already merges in batches of 15 or fewer and waits for main to go green. To make waits predictable, this plan suggests fixed train times:

- **Times:** every 2 hours, on the even hour (UTC), from 00:00 to 22:00.
- **Cut-off:** a PR boards a train when it has green CI on its final head, a current Quality PASS (and Security where asked), and base equal to main, 15 minutes before the train.
- **Size:** at most 15 PRs. A fix for a red main leaves alone and at once, as today.
- **Missed train:** the next one. The owner doesn't need to ping anyone.
- **Red main:** trains stop until main is green. Never re-run a red main that may be a real bug; re-run only known flakes, as Release management does today.

With 12 trains a day of up to 15 PRs, the train limit is 180 merges a day, above last week's best day (157). The trains are not the limit.

### CI capacity

The runners saturated on 2026-11-11, with about 42 runs queued. The levels that worked after that are 2 PRs in CI per thread, with about 10 threads building. This plan keeps those levels:

- **Normal:** 2 PRs in CI per build thread; 3 open per thread at most (drafts parked on Chris don't count).
- **Warning sign:** more than 30 queued runs, or a run waiting more than 30 minutes to start. The orchestrator then drops the busiest threads to 1 PR in CI, and lifts the limit again in steps once the queue clears.
- **Pushes:** one push per fix. Run `test_silent_except` and `py_compile` before pushing, so CI time isn't spent on a known failure.

## Review gates (unchanged)

Every PR keeps the gates it has today. Speed comes from preparing work ahead and running reviews alongside each other, not from skipping steps.

- **Quality:** a reviewer verdict on the final head, and a re-check after any push.
- **Security read:** for inbound surfaces, scripts, destructive actions, tokens, TLS, rendering untrusted content and opening links or files.
- **UX:** for any UI copy. A new screen or panel gets a mockup first.
- **Workload mgmt:** sign-off for workloads changes.
- **Merging:** only Release management merges.
- **Preview:** new features ship under the Preview tag until they have been used for real.

To keep reviews from becoming the bottleneck, owners ask for a Security read **when they open the PR**, not after Quality. UX reviews copy from the mockup, so the build PR only needs a check that it matches.

## Asking Chris less

Everything that needs Chris goes on the twice-daily card roundup (09:00 and 22:00 UTC), and nowhere else unless it is urgent. This week that means **four asks in total**.

### The bundled go

New features need Chris's **typed** go; a card tap only counts for merges and decisions. This plan asks for that go **once**, for the whole list below, with a one-paragraph scope each in its issue.

- **The list:** #2360, #2362, #2361, #2363, #2359, #2358, and parity wave 1 (already approved). Each ships under Preview and passes every gate above.
- **Reply wanted:** a single typed reply such as "go on the week plan list", or the list minus any item he wants to leave out.
- **When:** Monday 17:00 roundup.

### The other three asks

1. **One clickable mockup** for every screen this week: parity items 4, 7, 8, 9, 14, 15, 16, 18 and 19, plus the lineage impact view and the observability screen. Asked for by Tuesday 09:00, answered at one roundup.
2. **The meter reading**, at Tuesday 22:00 and Wednesday 09:00, as described above.
3. **The parked drafts**, as one card: which of the 20 drafts waiting on him should move, close or keep waiting.

Things only Chris can do are listed once, on Monday's card, with no deadline. They don't block this plan:

- signing (the Microsoft identity check);
- Run 10's typed "yes, run it";
- the Local engine soak log;
- the Chris-only checks.

## How we track it

- **Every 6 hours, the orchestrator sweep checks:** the CI queue and start waits, main, merges against the train times, idle threads, and the work list above. It refills idle threads the same hour.
- **The coordinator** gives Chris a one-paragraph status at the 09:00 and 22:00 roundups, alongside the cards.
- **Editor parity:** the editor parity page (#2579) moves a row from Gap or Partial to Have only when the code is on main.
- **The weekly review** on Monday 11-23 records what this plan delivered and what slipped, and why.
