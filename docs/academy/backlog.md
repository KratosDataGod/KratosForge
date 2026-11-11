---
site_group: Internal engineering
---

# Academy backlog: ACD-001 to ACD-020

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

Priorities: P0 must land for the first learner workflow, P1 phase work, P2 later. Owners are threads
in the KratosForge project ([ownership](delivery-plan.md#ownership)). Every story's definition of
done also includes the directive's list: unit and integration tests, execution tests, a security and
isolation check where code runs, accessibility checks for UI, docs, evidence on the PR, Second
quality's review and Release management's merge. Content claims are checked against current
Microsoft documentation.

## Phase 1: Foundation

### ACD-001 Certification objective registry (P0)
- **What:** `academy/registry.py` and `packs/dp-700/certification.json`: certification id and version,
  the 3 domains, 9 skill areas and 49 objectives verbatim, weight ranges, competencies, linked lessons,
  exercises and questions, validation requirements, docs links, coverage status, gap records,
  last-reviewed date.
- **Depends on:** none.
- **Acceptance:** every published objective appears once; any objective with no exercise has a gap
  record; ids are stable; `problems()` is empty; the source URL and snapshot hash are recorded.
- **Tests:** registry loads; objective count 49; every domain weight is a published range; a missing
  objective or missing gap record fails `problems()`.
- **Owner:** this thread.

### ACD-002 Academy exercise and pack format (P0)
- **What:** schema `kratosforge/academy-exercise@1` and `pack.json` ([fields](architecture.md#exercise-format)); `grading/` kept apart from the workspace; exercises may reuse a Learn lab exercise.
- **Depends on:** ACD-001.
- **Acceptance:** an exercise without a reference solution, validation, class, `verifies` and `does_not_verify`, reset instructions or objective ids fails CI; draft vs live like Learn.
- **Tests:** schema checks on good and broken fixtures; path containment; no raw HTML.
- **Owner:** this thread.

### ACD-003 Verification classes and honesty labels (P0)
- **What:** the four classes, per-objective `fabric_required`, and the rule that SIMULATED and KNOWLEDGE ONLY never complete a Fabric-specific objective. Engine names on results.
- **Depends on:** ACD-001, ACD-002.
- **Acceptance:** a LOCAL VERIFIED result never says Spark, Warehouse or Fabric SQL unless the registry links parity evidence; Fabric-required objectives show incomplete until FABRIC VERIFIED.
- **Tests:** label text tests; completion logic tests per class.
- **Owner:** this thread.

### ACD-004 Validation framework v1 (P0)
- **What:** validators `schema_matches`, `data_quality`, `delta_table`, `config_matches`, `artifact_present`, `pipeline_outcome`, `notebook_ran` beside the existing Learn checks; each deterministic and explained.
- **Depends on:** ACD-002.
- **Acceptance:** each validator passes the reference solution and fails at least two planted wrong answers with a message that names the problem.
- **Tests:** per-validator unit tests; reference-solution runs in CI for every live exercise.
- **Owner:** grading sandbox thread. **Security read:** yes (runs learner code paths).

### ACD-005 Learner execution sandbox (P0)
- **What:** exercise workspaces under `<data>/academy/<pack>/<exercise>/`; `python_lab` kernel mode (no tokens, no lakehouse, scrubbed environment, workspace working folder, network and out-of-workspace writes refused by an audit hook, time and memory limits); grading material never in the workspace.
- **Depends on:** ACD-002.
- **Acceptance:** a learner script can't read `grading/` through the workspace, can't open a socket, can't write outside the workspace, and is stopped at its time and memory limit; Reset restores the start state.
- **Tests:** escape attempts as tests (path traversal, symlink, socket, env tokens, fork bomb with Job Object); Windows CI.
- **Owner:** grading sandbox thread. **Security read:** required, including whether an OS sandbox is needed.

### ACD-006 Evidence and progress by objective (P0)
- **What:** `academy.*` evidence rows (rules v4) with certification version, objective ids, class, mode, hints, attempt; never rewritten; readiness metrics computed per objective.
- **Depends on:** ACD-001, ACD-003.
- **Acceptance:** passing E37 writes one row against 2.2.9 with class LOCAL VERIFIED; repeating it adds a retention point, not a duplicate; excluding a row removes it everywhere.
- **Tests:** `progress` and `readiness` unit tests; version change keeps old rows.
- **Owner:** this thread.

### ACD-007 Academy Home, Certification Catalog and DP-700 Learning Path (P1)
- **What:** the first Academy screens inside the Learn area: next useful action, the catalog (DP-700 live, others "coming later"), the path by domain and objective with status and class badges.
- **Depends on:** mockup approved by Chris, ACD-001, ACD-006.
- **Acceptance:** keyboard reachable; axe clean; light and dark; reduced motion; design tokens only; Preview label.
- **Tests:** browser tests, axe, screenshot evidence.
- **Owner:** this thread with UX's mockup review.

### ACD-008 Exercise Workspace (P1)
- **What:** collapsible exercise panel over the existing SQL editor (then notebook and pipeline canvas): Scenario, Requirements, Resources, Hints, Validation, Explanation, Progress; Start, Run, Validate, Hint, Reset, Compare, Submit, Next; progress saved and restored after a restart.
- **Depends on:** mockup, ACD-004, ACD-005, ACD-006.
- **Acceptance:** a learner completes E37 end to end with the keyboard; closing the app mid-exercise and reopening restores the answer and panel state.
- **Tests:** browser tests including restart; axe.
- **Owner:** this thread.

## Phase 2: Practical depth

### ACD-009 DP-700 exercise catalog (P1)
- **What:** the 64 exercises in [the curriculum](curriculum.md), in batches by domain, each with a reference solution and CI test.
- **Depends on:** ACD-002, ACD-004 (and ACD-005 for Python exercises).
- **Acceptance:** each batch's exercises pass `problems()`, the reference solution passes validation, and planted wrong answers fail.
- **Owner:** content thread. Batches go straight to Second quality and RM.

### ACD-010 Broken environments (P1)
- **What:** the 12 Troubleshoot exercises (E48–E59): a working build plus a fault manifest; validation checks the fix and that nothing else changed.
- **Depends on:** ACD-004, ACD-005, ACD-009 tooling.
- **Owner:** content thread.

### ACD-011 Python, PySpark and Delta exercise execution (P1)
- **What:** `python_lab` and `delta_lab` adapters, `notebook_ran` and `delta_table` validators, notebook starter assets.
- **Depends on:** ACD-005.
- **Owner:** grading sandbox thread, with the Notebook thread's review. **Security read:** yes.

### ACD-012 Pipeline and orchestration exercises (P1)
- **What:** `pipeline_lab` adapter over the local runner with mocks only; `pipeline_outcome` validator; runMultiple exercises.
- **Depends on:** ACD-004.
- **Owner:** grading sandbox thread, with Orchestration's review.

### ACD-013 Exam engine and question bank (P1)
- **What:** question format (multiple choice, multiple response, ordering, scenario/case study); original DP-700 bank, at least four per objective; timed exams, review flags, review screen, keyboard and screen reader support. No claim to reproduce Microsoft's exam interface; no real exam questions.
- **Depends on:** ACD-001, ACD-006; mockup for the screen.
- **Acceptance:** every question cites the Microsoft Learn page it is based on; scoring is per objective; the timer pauses on app close and resumes honestly (time shown as elapsed).
- **Owner:** exam engine thread.

### ACD-015 Readiness dashboard and diagnostics (P1)
- **What:** dashboard by domain and objective with the six metrics, strengths, weaknesses, next exercise, remaining objectives; diagnostic assessments that can mark objectives "shown".
- **Depends on:** ACD-006, ACD-013; mockup.
- **Acceptance:** never labelled as an official score; the help says so.
- **Owner:** this thread.

### ACD-020 Certification change detection (P1)
- **What:** a scheduled workflow that compares the study guide with the registry snapshot and opens a draft PR with the diff and a draft crosswalk; crosswalk format; results never rewritten.
- **Depends on:** ACD-001, ACD-006.
- **Acceptance:** a changed fixture page produces the expected diff; an unchanged page does nothing; a fetch failure reports and doesn't fail other CI.
- **Owner:** this thread.

## Phase 3: Advanced learning

### ACD-014 Fabric-verified exercises (P2)
- **What:** optional connection to the learner's workspace with their own sign-in; preflight (role, capacity state, required features); setup that tags every item it creates; teardown that deletes only tagged items it created; cost and CU estimate only where a credible method exists.
- **Depends on:** ACD-002, ACD-003, ACD-006.
- **Acceptance:** never needs tenant admin; never deletes an untagged or pre-existing item; refuses a Prod-named capacity unless the learner types its name.
- **Owner:** Fabric verification thread (later). **Security read:** required. Runs against Chris's tenant need his typed "yes, run it".

### ACD-016 Real-Time Intelligence exercises (P2)
- **What:** `telemetry` dataset, windowing exercises, eventstream topology model, KQL question set; option to embed Microsoft's Kusto emulator for local KQL (needs Chris's go: a local install).
- **Depends on:** ACD-009, ACD-013.
- **Owner:** content thread.

### ACD-019 Capstones and Project Portfolio (P2)
- **What:** C1–C3 as multi-stage projects; a portfolio screen listing finished projects with their evidence and class; export as a report the learner can share.
- **Depends on:** ACD-008, ACD-009, ACD-011, ACD-012; mockup.
- **Owner:** content thread (projects), this thread (screen).

## Phase 4: Scale and extensibility

### ACD-017 More certifications through packs and adapters (P2)
- **What:** a second pack (DP-600 or a Databricks or Snowflake path) built only from content and adapters, proving no DP-700 assumptions in code.
- **Depends on:** Phase 2.

### ACD-018 Instructor dashboard (P2)
- **What:** a later-phase view for a team lead: cohort progress from evidence people choose to share (reuses `learn/team.py` sharing); no surveillance by default.
- **Depends on:** ACD-015; mockup; Chris's go.

## Dependencies at a glance

| Story | Needs |
| --- | --- |
| ACD-001 | — |
| ACD-002 | 001 |
| ACD-003 | 001, 002 |
| ACD-004 | 002 |
| ACD-005 | 002 |
| ACD-006 | 001, 003 |
| ACD-007 | 001, 006, mockup |
| ACD-008 | 004, 005, 006, mockup |
| ACD-009 | 002, 004 (005 for Python) |
| ACD-010 | 004, 005, 009 |
| ACD-011 | 005 |
| ACD-012 | 004 |
| ACD-013 | 001, 006, mockup |
| ACD-014 | 002, 003, 006 |
| ACD-015 | 006, 013, mockup |
| ACD-016 | 009, 013 |
| ACD-017 | Phase 2 |
| ACD-018 | 015, mockup |
| ACD-019 | 008, 009, 011, 012, mockup |
| ACD-020 | 001, 006 |
