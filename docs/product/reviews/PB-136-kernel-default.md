# Implementation review: PB-136 Process kernel by default after a clean Windows trial (FEAT-NB-03)

| Field | Value |
| --- | --- |
| Backlog item | PB-136 Process kernel by default after a clean Windows trial (FD-110, from PB-073) |
| Pull request | #2601, merged; main at af7d786b |
| Package | none |
| Reviewer | Product management thread, which didn't write this PR |
| Date | 2026-11-13 |
| Environment | Trial: Chris's PC over Remote Control, Windows 11 Pro 10.0.26200, the installed app's embedded Python 3.12.10, origin/main e9e8e3e8, no tenant, after Chris's "go on PB-136" (2026-11-13 13:04 UTC), by the Local engine thread. This review: Linux container, engine from source in a Python 3.12 venv |

## Outcome

**Accepted with Documented Limitations** (recommended). The Windows trial on Python 3.12 passed in every area
with no product defects, the default flipped in its own PR, and a test shows existing notebook cells give the same
status, output and errors under the new default as inside the engine. The limits are on coverage: a per-user 3.12
venv and the two fixes #2601 made were not run on that PC, and the trial results are in a project file, not yet on
issue #896.

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Trial results recorded per area on Windows with Python 3.12, defects filed | Met, with a limit | Kernel 25 passed, 2 skipped; %pip 6; environments 21; magics 53, 3 skipped; Prove 71 passed; schedules 173, 25 skipped; sessions 31. The 8 failures (1 kernel, 7 Prove) were test setup only: the fake Fabric run sets PYTHONPATH, which the embedded Python ignores; the same tests from a venv on main gave 79 passed. Measurements, 5 runs each: cold start median 1.135 s, Stop 0.021 s with nothing left behind, a 2 s limit ends at about 2.8 s with variables kept, the 30 s grace kill at about 33 s, the memory limit at under 1 s. No product defects; two test follow-ups are recorded in the trial file and aren't filed as issues yet |
| 2 | The default flips only after a clean trial, in its own PR | Met | #2601 sets `kernel_process` on by default after the 11-13 trial; a user's or a policy's choice still wins |
| 3 | Existing notebooks run unchanged under the new default (test) | Met | `test_kernel_default.py` runs the same cells (variables, print, DuckDB, Polars, a function, an error) under the default and in the engine and asserts the same status, output and errors; with `test_kernel_process.py`, 32 passed here |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Yes | The default flip, with the in-engine kernel kept as a setting |
| Full suites pass | Yes, for this area | 32 passed here; the full suites run in main CI, on Linux and Windows |
| Reachable where docs say; Preview behavior right | Yes | Settings keeps **Run notebooks in their own process**, now on by default |
| OneLake data safety | Yes | Nothing new writes. #2601 also stops own-process cells asking Fabric about schemas before every cell |
| Authentication | Yes | A signed-out or offline person's cells no longer wait on a sign-in for the schema question |
| No unproven local-equals-platform claim | Yes | No platform claim |
| Docs match the code | Yes | #2601 updated the notebook and engine docs |
| Accessibility | Not applicable | No new controls |

## Findings

| # | Severity | Finding | Where | Status |
| ---: | --- | --- | --- | --- |
| 1 | Note | The new default exposed two bugs, fixed in #2601 with tests: the Variables detail, hover and result viewer read the open notebook's kernel instead of their own, and own-process cells asked Fabric about schemas before every cell | #2601 | Fixed |
| 2 | Note | test_prove's fake Fabric run should put its path on `sys.path` so it also runs on the embedded Python | trial file | Not filed |
| 3 | Note | One stray "Traceback" line appeared on stderr during measurements, with no body captured; every measurement met its expectation | trial file | Unverified |

None blocking.

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| An idle notebook process uses about 0.8 GB on Windows, so each open notebook costs that much memory; a memory limit under 2 GB leaves little room | docs/notebooks.md | none |
| The trial didn't cover a per-user Python 3.12 venv, the two #2601 fixes on that PC, or a live tenant | this review | none |

## Inventory and backlog changes

- PB-136: In progress → In review. Evidence links this review.
- No feature row status changed.
