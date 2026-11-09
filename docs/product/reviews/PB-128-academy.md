# Implementation review: PB-128 KratosForge Academy: certification preparation (FEAT-DEV-04)

| Field | Value |
| --- | --- |
| Backlog item | PB-128 KratosForge Academy: certification preparation (FEAT-DEV-04; ACD-001..020, DBA-001..016) |
| Pull requests | The Academy PRs listed in the item, all merged; main at 2bf8ea1b |
| Package | none |
| Reviewer | Product management thread, which didn't write these PRs |
| Date | 2026-11-09 |
| Environment | Linux container, with the engine from source in a Python 3.12 venv. No Fabric tenant, no Databricks workspace and no Windows app were used |

## Outcome

**Accepted with Documented Limitations** for the DP-700 track (recommended). The Databricks Data Engineer Associate track doesn't meet criterion 1 yet, so it should move to an item of its own.

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Every objective in a certification's registry has exercises or a recorded gap | Met for DP-700. Not met for Databricks | DP-700 (`packs/dp-700/certification.json`): 48 of 49 objectives name a built exercise or capstone, and 3.2.5 (eventstream errors) has gap G-02, with E54 waiting until Microsoft documents eventstream schema-change behavior. Databricks (`packs/dbx-de-associate`): 6 of 33 objectives have a built exercise and 20 rely on recorded gaps (G-01 to G-10). The other 7 (1.1, 2.5, 2.6, 2.7, 4.4, 5.2 and 5.4) name planned exercises that aren't built and have no gap |
| 2 | An exercise is graded on what the learner made, read as untrusted input, in a limited process | Met | Graders read the tables and files the learner wrote (lab.py, pylab.py, pyspark_lab.py, gitlab.py, sqlprojlab.py). Learner code runs in the sandbox with time and memory limits for every exercise type (test_academy_limits.py, #2170). On Windows it runs in an AppContainer by default, and the network is refused even on loopback (test_academy_appcontainer.py). Security read at /mnt/project-files/academy/grading-sandbox-security-read.md; docs/academy/grading-sandbox.md |
| 3 | Practice exams, diagnostics and drills run on one exam engine with original questions | Met | One engine (academy/exam.py, academy_exam_service.py) runs practice, diagnostic and drill sessions. The DP-700 bank of 498 questions was re-checked against Microsoft Learn (#2219). Distractors are as specific as the answer (#2229), and retakes draw unseen questions first (#2200) |
| 4 | Nothing in Academy writes to Fabric or spends capacity | Met | The academy package imports no Fabric, OneLake or HTTP client. The learner bootstrap refuses sockets, HTTP, `urllib` and process creation (_bootstrap.py). Objectives that need Fabric stay incomplete with local work alone (`test_an_objective_that_needs_fabric_stays_incomplete_with_local_work`) |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Full suites pass | Yes, for Academy | tests/test_academy_*.py: 779 passed, 41 skipped (Windows-only and machine-wide grant tests) |
| Docs match the code | Yes | docs/academy-guide.md, docs/academy/dp-700-coverage.md, grading-sandbox.md and assessments.md |
| No local-equals-Fabric claim | Yes | Objectives that need Fabric say why and stay incomplete. Readiness counts only verified evidence |

## Findings

| # | Severity | Finding | Where | Status |
| ---: | --- | --- | --- | --- |
| 1 | should fix | Every objective in both registries still has `status: planned`, including the 48 DP-700 objectives with built exercises. The registry summary counts by this field, so it reports 49 planned | packs/*/certification.json, registry.py summary | Open, for the Academy thread |
| 2 | note | 7 Databricks objectives have neither a built exercise nor a gap | packs/dbx-de-associate/certification.json | Moves with the Databricks track |

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| DP-700 objective 3.2.5 (eventstream errors) has no exercise yet | docs/academy/dp-700-coverage.md, gap G-02 | PB-128 follow-up E54 (ACD-014) |
| Objectives that need Fabric can only be completed with Fabric runs, which Academy doesn't start | docs/academy/fabric-gaps.md | none: by design |
| The Databricks track isn't complete | docs/academy/databricks/coverage.md | A new item for the Databricks track, if the product owner agrees |

## Inventory and backlog changes

- PB-128: In progress → In review. Evidence links this review.
- No feature row status changed.
