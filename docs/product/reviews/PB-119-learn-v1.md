# Implementation review: PB-119, Learn v1

| Field | Value |
| --- | --- |
| Backlog item | PB-119 Learn v1: lessons, labs and finding actions (FEAT-DEV-04) |
| Pull request | LX-01 to LX-09 and the Microsoft Fabric path: #1063, #1076, #1083, #1137 to #1140, #1158, #1159, #1160; lab-limit tests #1552. All merged; main at 6dabe70c |
| Package | none |
| Reviewer | Product management thread. It didn't write these PRs. The Learn thread checked the criteria on 2026-11-01. |
| Date | 2026-11-02 |
| Environment | Linux container, engine from source in a Python 3.12 venv, Playwright Chromium. No tenant, no Windows |

## Outcome

**Accept** (recommended).

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | The Learn page (Preview) lists paths, lessons and labs, and every rule id a lesson names resolves in the catalog (test_learn_content) | Met | sidecar/tests/test_learn_content.py passes; learn.test.cjs passes in Chromium |
| 2 | A lab runs on this PC with generated data, with no file or network access, and a run or check stops at 30 seconds with memory held at 1 GB (test_learn_labs) | Met | test_learn_labs.py passes, including `test_a_lab_answer_cant_reach_files_or_change_the_lab`, `test_a_run_and_a_check_are_stopped_at_the_time_limit` and `test_the_limits_are_30_seconds_and_1gb_and_an_answer_cant_raise_the_memory` (#1552) |
| 3 | Beside notebook and SQL findings: Explain, Show example, Lesson; Dismiss (this line, or the rule everywhere), except for a rule a policy names (finding-actions.test.cjs) | Met | finding-actions.test.cjs passes against the real engine: Expert, Professional and Guided; Not on this line; Never show; a rule a policy names can't be dismissed and ignores earlier dismissals; nothing is added with previews hidden |
| 4 | Learn evidence shows in Profile and comes only from things the person did (test_profile_learn) | Met | test_profile_learn.py passes |
| 5 | docs/learn.md and docs/learn/writing-lessons.md match the code | Met | learn.md states the 30-second and 1 GB limits, the three levels, the Dismiss scopes and the policy rule as the tests show them. writing-lessons.md's `rules` field is what test_learn_content resolves |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Yes | Guided Development (PB-122) and Learn phase 2 (PB-123) are split out (#1551) |
| New tests fail without the change, pass with it | Not re-run without the change | |
| Full suites pass (pytest, UI, cargo) | Partly | Engine tests named "learn": 155 passed. test_learn_content, test_learn_labs and test_profile_learn: 80 passed. finding-actions.test.cjs and learn.test.cjs: 19 passed, 3 skipped (axe-core isn't installed here). ui.test.cjs: 315 passed |
| Reachable where docs say; Preview behavior right; packaged into the engine | Yes | Learn is Preview; with previews hidden, finding actions add nothing (finding-actions.test.cjs) |
| OneLake data safety | Yes | Labs use generated data with no file or network access |
| Authentication and distribution | Not affected | |
| No unproven local-equals-Fabric claim; Preview tags kept where untested | Yes | |
| Docs match the code; linked; strict docs build passes | Yes | zensical --clean is clean |
| Accessibility of new controls | Partly | The axe check in finding-actions.test.cjs was skipped here; CI runs it |

## Findings

None blocking.
