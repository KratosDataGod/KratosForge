---
site_group: Internal engineering
---

# Implementation review: PB-126 Learn curriculum content batches by path (FEAT-DEV-04)

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

| Field | Value |
| --- | --- |
| Backlog item | PB-126 Learn curriculum content batches by path (FEAT-DEV-04) |
| Pull request | #1658, #1659, #1660, #1677, #1684 and #1687, merged; main at 9367abf7 |
| Package | none |
| Reviewer | Product management thread. It didn't write these PRs. |
| Date | 2026-11-02 |
| Environment | Linux container, engine from source in a Python 3.12 venv, Playwright Chromium. No tenant, no Windows |

## Outcome

**Accept** (recommended).

Recommended by: Product management thread. Decided by (product owner): Chris, 'Accept all four as recommended' on the decision card on 2026-11-03 (project message cmsg_018T4o2pQdpbLBNhHpgVNNGpUAPuMuwCvujJpe76xnHm5f).

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Every lesson names rule ids that resolve in the catalog (test_learn_content) | Met | test_learn_content.py passes on all lessons LM-13 to LM-35 |
| 2 | Every lab runs on this PC with generated data inside the lab limits (test_learn_labs) | Met | test_learn_labs.py passes on LAB-05 to LAB-12, with the 30-second and 1 GB limit tests from #1552 |
| 3 | docs/learn.md lists the paths and lessons as shipped | Met | docs/learn.md names the lessons and labs up to LM-35 and LAB-12 |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Full suites pass | Partly | test_learn_content, test_learn_labs and test_profile_learn: 167 passed. learn.test.cjs passes |
| OneLake data safety | Yes | Labs use generated data on this PC |

## Findings

None blocking.
