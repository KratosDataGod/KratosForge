---
site_group: Internal engineering
---

# Implementation review: PB-073 Process kernel with limits (FEAT-NB-03)

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

| Field | Value |
| --- | --- |
| Backlog item | PB-073 Process kernel by default with limits (FD-110) |
| Pull request | #115 (cell time and memory limits), #996 (the memory limit reads the interpreter's own pid), #1084 (the memory limit counts the processes a cell starts), all merged; main at 47416473 |
| Package | none |
| Reviewer | Product management thread, which didn't write these PRs |
| Date | 2026-11-11 |
| Environment | Linux container, engine from source in a Python 3.12 venv. The Windows trial is the one recorded on issue #896 (2026-10-28, Chris's PC); no new Windows run and no tenant |

## Outcome

**Accepted with Documented Limitations** (recommended). The limits half of the item is done. A notebook
running in its own process has a cell time limit and a notebook memory limit, set in Settings or by
policy, and both are tested. The default flip is not done. The process kernel stays an opt-in Preview
setting, because the default waits on a clean Windows trial rerun (Python 3.12, with the venv fix from
#996) and Chris's go. Issue #896 was closed on 2026-11-02 for that reason. If accepted, the flip moves to
its own item, PB-136.

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

The item's acceptance says "As defined in FD-110". FD-110 (docs/product/enterprise-gap-analysis.md)
names the gap as "no memory or CPU limits, no per-cell timeout"; issue #896 added the trial and the
default flip.

| # | Criterion | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | A cell past its time limit is interrupted and keeps variables; one that ignores the interrupt has its process ended | Met | `test_kernel_process.py::test_a_cell_past_the_time_limit_is_interrupted_and_keeps_variables`, `::test_a_cell_that_ignores_the_interrupt_has_its_process_ended` |
| 2 | A notebook over its memory limit has its process ended, counting the processes its cells start and the real interpreter | Met | `::test_a_notebook_over_the_memory_limit_has_its_process_ended`, `::test_the_memory_limit_counts_the_processes_a_cell_starts` (#1084), `::test_the_memory_limit_reads_the_interpreter_the_process_reported` (#996) |
| 3 | The limits come from Settings or policy, and a proof stopped by a limit says so | Met | `::test_the_limits_come_from_settings_or_policy`, `::test_a_proof_stopped_by_a_limit_says_so` |
| 4 | Windows trial recorded per area | Met, once | Issue #896, 2026-10-28: kernel, %pip, environments, magics, Prove, schedules and sessions passed on Windows 11; the venv memory-limit bug it found was fixed in #996. Not covered: Python 3.12, the 30 s grace kill on Windows |
| 5 | The process kernel is the default, after a clean trial, in its own PR | Not met | Not started. #896 closed not planned on 2026-11-02, waiting on Chris's go for the trial rerun |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Partly | Limits shipped; the default flip did not |
| Full suites pass | Yes, for this area | `test_kernel_process.py` and `test_kernel_process_edges.py`: 53 passed here. The full suites run in main CI |
| Reachable where docs say; Preview behavior right | Yes | Settings > Preview features: **Run notebooks in their own process** and the cell time and memory limits that need it (docs/README.md, docs/notebooks.md) |
| OneLake data safety | Yes | Nothing new writes to OneLake |
| Authentication | Not applicable | |
| No unproven local-equals-platform claim | Yes | No platform claim |
| Docs match the code | Yes | docs/notebooks.md describes the time limit's 30 s grace, the memory check every second, and what counts toward it |
| Accessibility | Not applicable | No new controls in this review |

## Findings

| # | Severity | Finding | Where | Status |
| ---: | --- | --- | --- | --- |
| 1 | Note | An idle kernel uses about 0.8 GB on Windows, so a memory limit under 2 GB leaves little room | docs/notebooks.md says so | Documented |

None blocking.

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| Notebooks share one in-engine kernel unless **Run notebooks in their own process** is on, and the limits need it | docs/README.md, docs/notebooks.md, docs/engines.md | PB-136 (default flip after a clean Windows trial) |

## Inventory and backlog changes

- PB-073: In progress → In review. Evidence links this review.
- PB-136 added (Proposed): process kernel by default, after a clean Windows trial rerun and Chris's go.
- No feature row status changed.
