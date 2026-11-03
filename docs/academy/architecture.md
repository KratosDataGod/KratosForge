# Academy: data and content architecture

Academy is a layer over Learn. Learn already has content folders, a lab runner, checks, evidence
and ranks; Academy adds certifications and objectives above them, more kinds of execution beside
the DuckDB lab, an exam engine, and readiness computed from evidence.

## Entities

```
Certification (dp-700, version 2026-10-30)
 └─ Domain (1 Implement and manage …, weight 30–35%)
     └─ Skill area (1.1 Configure Microsoft Fabric workspace settings)
         └─ Objective = competency (1.1.1 Configure Spark workspace settings)
              ├─ Modules and lessons (existing LM-nn, new ones)
              ├─ Exercises (E01 …; each maps to one or more objectives)
              ├─ Questions (assessment items; each maps to one objective)
              ├─ Gap records (G-01 …: what can't be practised here yet, and why)
              └─ Evidence (rows in the learner's profile, by objective and certification version)
```

The study guide calls the bullets "skills measured"; Academy calls each bullet an **objective**
and treats it as the competency the learner must show. A "required competency" in the registry is
the plain statement of what the learner can do once the objective is met.

## Content packs

Content is data, versioned and testable on its own, and kept apart from application code:

```
sidecar/fabric_desktop/academy/
  registry.py            reads and checks packs (no execution)
  packs/
    dp-700/
      pack.json          id, title, vendor, version, source URL, reviewed date, schema
      certification.json domains → skill areas → objectives, weights, competencies, docs, gaps, status
      exercises/E37/     exercise.json, lesson.md, starter files, datasets (names only)
      grading/E37/       reference solution and hidden checks (never copied to the learner's workspace)
      questions/1.1.1.json  original questions for one objective
      crosswalk/         objective maps between certification versions (ACD-020)
```

Existing `learn/content/` modules stay where they are; an exercise can point at one
(`"uses": "LAB-04/rebuild"`) instead of copying it. CI runs `registry.problems()` the way it runs
`content.problems()`, so a pack with a broken reference, an uncovered objective without a gap
record, or an exercise without a reference solution never ships.

A pack for another certification (DP-600, a Databricks or Snowflake exam) is a new folder under
`packs/` with its own registry and exercises. It reuses the same execution adapters; nothing in
the Academy code names DP-700.

## Exercise format

Schema `kratosforge/academy-exercise@1`.

| Field | Meaning |
| --- | --- |
| `id`, `version`, `status` | `E37`, a whole number, draft/live/retired; live needs `reviewed` |
| `objectives` | Registry objective ids it gives evidence for (`2.2.9`) |
| `title`, `objective_text` | What the learner will be able to do |
| `scenario` | The business situation, two to four sentences |
| `prerequisites` | Exercise or lesson ids |
| `minutes` | Estimated completion time |
| `mode` | `guided`, `challenge` or `troubleshoot` (primary mode) |
| `class` | `local_verified`, `simulated`, `fabric_verified` or `knowledge_only` |
| `verifies` / `does_not_verify` | Plain sentences shown to the learner: what this run proves, and what still needs Fabric |
| `engine_label` | The engine that ran the work, as the learner sees it ("DuckDB on this PC") |
| `adapter` | Execution adapter: `duckdb_lab`, `python_lab`, `delta_lab`, `pipeline_lab`, `config_model`, `git_lab`, `sqlproj_lab`, `quiz`, `fabric` |
| `starter` | Files copied into the learner's workspace |
| `datasets` | Generated datasets with seed and size |
| `expected` | What the learner must produce: tables, files, a result, a settings document |
| `validation` | Validators to run (below), each optionally with its own `pass` and `fail` message |
| `hints` | Progressive, from a nudge to nearly the answer |
| `explanation` | The correct approach and why, shown after a pass or on request after a fail |
| `remediation` | Lesson ids and Microsoft Learn links |
| `reset` | What Reset restores; `cleanup` for anything outside the workspace (Fabric only) |
| `fault` | Troubleshoot exercises only: `summary`, `planted` (what was broken) and `symptom` (what the learner sees) |
| `uses` | A Learn lab exercise to build on (`LAB-04/rebuild`); its data, starter and reference solution are used |
| `parity` | For LOCAL VERIFIED work, the evidence link for any local-to-Fabric claim |

## Verification classes

| Class | Means | Counts for a Fabric-specific competency |
| --- | --- | --- |
| LOCAL VERIFIED | The learner's artifact ran on this PC and validation passed. Proves a portable concept (SQL logic, Delta behavior, a pipeline's control flow). | Only for objectives the registry marks portable (`fabric_required: false`) |
| SIMULATED | The learner's work was checked against a model of the service (settings, permissions, topology, recorded run histories). Nothing ran in Fabric. | No |
| FABRIC VERIFIED | The learner's artifact ran in their own Fabric workspace and the service's actual behavior was checked. | Yes |
| KNOWLEDGE ONLY | Questions or a design answer; no artifact ran. | No |

Every result screen shows the class, the `verifies` and `does_not_verify` sentences, and for
LOCAL VERIFIED work which engine ran it ("DuckDB on this PC", never "Spark").

## Execution adapters and grading

An adapter starts an exercise workspace, runs the learner's work, and hands outputs to validators.
Validators run in the engine process, not in the learner's process, and read the grading material
the learner's process never gets.

| Adapter | Built on | Learner code runs in |
| --- | --- | --- |
| `duckdb_lab` | `learn/labs.py` | Engine process; DuckDB with external access off, memory limit, locked settings, rolled-back transaction, 30 s interrupt |
| `python_lab` | `kernel_proc.py` | Child process with time and memory limits, process group or Job Object; lab mode: no sign-in, no lakehouse, working folder is the exercise workspace, scrubbed environment |
| `delta_lab` | `python_lab` + delta-rs | As `python_lab`; tables live under the workspace |
| `pipeline_lab` | `orchestration/runner.py` | Engine process; only local adapters and the exercise's mocks; no Copy, no Fabric calls |
| `config_model` | rule checks like `governance_checks.py` | No code runs; a JSON or YAML settings document is checked |
| `git_lab`, `sqlproj_lab` | `git_service.py`, `warehouse_project_service.py` | Git and file checks in the workspace |
| `quiz` | exam engine (ACD-013) | No code |
| `fabric` | `prove_service.py` confirm and risk check, `fabric_api.py` | Fabric, in the learner's own workspace, with their own identity (ACD-014) |

| Validator | Checks |
| --- | --- |
| `result_matches` | A query's rows match the reference (existing) |
| `schema_matches` | Columns, types, nullability, keys (extends `table_shape`) |
| `data_quality` | Rules: unique, not null, ranges, referential integrity (extends `integrity`) |
| `delta_table` | Version count, operation history, partitioning, file count and size bounds, schema |
| `notebook_ran` | The notebook ran end to end in the lab kernel; named outputs exist |
| `pipeline_outcome` | Run status, per-activity status, outputs and parameters |
| `config_matches` | Settings document meets the rules (least privilege, required settings) |
| `artifact_present` | Required files or items exist with the right shape |
| `faster_than` | Measured on this PC only, with the reference answer checked first (existing) |
| `fabric_check` | A read-only check of the learner's Fabric item (ACD-014) |

Rules: deterministic (seeded data, ordered comparisons where order matters, numeric tolerance only
where declared), repeatable (same answer, same verdict), explainable (each validator says why).

### Isolation and grading material

- The learner owns the PC; Academy doesn't pretend to hide files from them. Grading material is
  kept out of the learner's **workspace and process** so a run can't read it by accident or game
  the check, and so content stays honest: reference solutions are under `grading/`, never copied
  to the workspace, and never sent to the UI before the learner asks for the explanation.
- Learner code never runs with the person's Fabric tokens, a lakehouse attached, or the app's
  data folder as its working folder.
- `python_lab` adds an audit hook that refuses network connections and file writes outside the
  workspace. That is defense in depth, not a security boundary; whether Academy also needs an OS
  sandbox (a Windows AppContainer or restricted token) is the first question for the Security read
  on ACD-005.
- Fabric exercises use the learner's own identity and workspace. No shared credentials or grading
  secrets exist.

## Evidence, progress and versions

Academy writes evidence rows to the existing profile (`progress.py`, rules v4):

| Row kind | Written when | Carries |
| --- | --- | --- |
| `academy.exercise` | All validators pass | certification id and version, objective ids, exercise id and version, class, mode, hints used, attempt number |
| `academy.troubleshoot` | A broken environment is repaired | as above |
| `academy.assessment` | An exam or diagnostic is finished | per-objective correct and total, item ids, timing |
| `academy.fabric` | A FABRIC VERIFIED check passes | as `academy.exercise`, plus the workspace and item ids (no tokens, no data) |

Today `academy.exercise` rows are written by `progress.from_academy()` and read by
`academy/readiness.py`; they are evidence only and earn no XP until a later ladder version says so.
Rows are never edited. A new certification version adds a crosswalk (old objective → new
objective). Readiness for the new version reads old rows through the crosswalk and shows them as
"earned under 2026-10-30"; the old version's results stay as they were.

## Readiness

Computed by objective, every time it's shown, from evidence rows:

| Metric | From |
| --- | --- |
| Knowledge | Assessment items for the objective, recent ones weighted more |
| Guided completion | Guided exercises passed |
| Independent practice | Challenge exercises passed without the explanation, fewest hints |
| Troubleshooting | Broken environments repaired |
| Fabric-verified experience | FABRIC VERIFIED passes (required where the objective needs Fabric) |
| Retention | Passes repeated after 7 or more days, and recent results |

The dashboard shows strengths, weak objectives, the next recommended exercise, and remaining
objectives, by domain with the published weight ranges. The headline is labelled "KratosForge
readiness estimate"; the help says it is not Microsoft's score and not a promise of passing.
Diagnostics: a short assessment per domain; an objective with strong knowledge **and** practical
evidence can be marked "shown", which skips its guided exercises (never its Fabric requirement).

## Certification change detection (ACD-020)

A scheduled GitHub workflow fetches the study guide, extracts the skills-measured list, and
compares it with the registry's source snapshot. On a change it opens a draft PR that updates the
snapshot and lists objectives added, removed and reworded, with a draft crosswalk. People review
and merge it; the app never changes its curriculum by itself.

## Screens (mockup first)

Learn is at the bottom of the left rail and the main rail is full, so Academy takes Learn's place
rather than a new rail item. Proposed tabs: Home, Certifications, Paths, Practice, Assessments,
Readiness, Portfolio, Best practices. The Exercise Workspace opens the existing SQL editor,
notebook or pipeline canvas with a collapsible exercise panel (Scenario, Requirements, Resources,
Hints, Validation, Explanation, Progress; actions Start, Run, Validate, Hint, Reset, Compare,
Submit, Next). All screens use the design tokens from #1759, follow the common page layout, and
support light and dark themes, keyboard use and reduced motion. The mockup decides this; nothing
is built before Chris reacts to it.
