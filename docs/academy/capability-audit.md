# Academy: capability audit and reuse

Audited on main at `da71946` (2026-11-03). Each row names the code that exists today, what it already
does, and what Academy needs from it. "Gap" means nothing in the repository does it yet.

## What exists

### Learn (the base Academy builds on)

| Area | Where | What it does today |
| --- | --- | --- |
| Content format | `sidecar/fabric_desktop/learn/content.py` | `kratosforge/learn-module@1`: module or lab folders (`LM-nn`, `LAB-nn`) with `module.json`, `lesson.md`, setup, starter and solution files; paths in `paths.json`. `problems()` fails CI on broken content (`tests/test_learn_content.py`). No raw HTML in lessons; only files inside the module's folder. |
| Catalog | `learn/content/` | 48 modules and labs (LM-01..LM-35, LAB-01..LAB-12), 107 exercises, 7 paths. All run on DuckDB on this PC. |
| Lab runner | `learn/labs.py` | One DuckDB file per lab under `<data>/learn/<id>/`, opened with `enable_external_access = false`, a 1 GB memory limit and locked settings. Learner SQL runs in a transaction that is always rolled back; 30 second time limit (DuckDB interrupt). Start, save, run, check, hint (progressive), reset. |
| Checks | `learn/checks.py` | `result_matches`, `table_shape`, `integrity`, `no_findings` (best-practice rules), `faster_than` (measured on this PC), `defects_found`. `guard()` refuses BEGIN/COMMIT/ROLLBACK in an answer. Each check explains why it passed or failed. |
| Datasets | `learn/datasets.py` | Deterministic, seeded `shop` dataset in S/M/L sizes with six planted defects and a fingerprint. |
| Hints, guides, patterns | `learn/guides.py`, `learn/patterns.py`, `learn/rule_guides.json` | Progressive hints; rule guides (importance, principle, impact, how to check); decision patterns. |
| Evidence and profile | `progress.py`, `progress_service.py` | Evidence rows per verified accomplishment (`lab.pass`, `module.complete`, `validate.pass`, `fix.checked`), versioned rules (`RULES_VERSION = 3`), skill coverage by dimension, never a score. One file per account. |
| Ranks | `ranks.py` | 13 evidence-based ranks (forge, wolf pack, Greek myth). Ladder versioned so a new lesson never takes a rank away. No rank is, or resembles, a god. |
| RPC | `learn_service.py` | `learn_catalog`, `learn_module`, `learn_lab_start/save/run/check/hint/reset`, rule guides, patterns, fixes, dismissals. |
| UI | `app/ui/learn.js`, Learn in the bottom rail | Tabs Home, Paths, Labs, Best practices. Lesson reader, lab panel with a SQL editor, Run, Check, Hint, Reset. Browser tests in `app/tests/browser/learn.test.cjs`. |
| Guidance levels | `app_settings.py` `learn_mode*` | Expert, Professional, Guided (preference only). |

### Execution engines Academy can use

| Capability | Where | Runs on this PC | Notes for Academy |
| --- | --- | --- | --- |
| DuckDB SQL | `sql.py`, `sql_exec.py` | Yes | The lab engine. Not the Warehouse engine. |
| Spark SQL | `sql_dialects.py`, sqlglot | Yes, translated to DuckDB | A translation, not Spark. |
| T-SQL (Warehouse, SQL endpoint, SQL database) | `sql_dialects.py` | **No.** Edited and formatted only | `localdata/tsql.py` translates a read-only subset for pipelines. T-SQL behavior is never claimed locally. |
| PySpark API | `spark_local.py` | Yes, DuckDB's PySpark-compatible API | `import pyspark` resolves to it. Not Spark: no executors, partitions or AQE. |
| Structured streaming | `spark_stream.py` | Yes, `availableNow` runs with checkpoints | Continuous triggers run only in Fabric. |
| Delta Lake | `localdata/delta.py`, `delta_dml.py`, `maintenance.py` | Yes, delta-rs | MERGE/UPDATE/DELETE, OPTIMIZE and VACUUM. V-Order is Fabric only. |
| Python notebooks | `kernel_proc.py`, `kernel_host.py` | Yes, child process | Time and memory limits; process group (Linux/macOS) or Job Object (Windows) so Stop ends everything. |
| notebookutils | `notebookutils_local.py` | Yes | `notebook.run`, `runMultiple` DAG, `exit`. |
| Pipelines | `orchestration/runner.py`, `expressions.py`, `validate.py` | Yes, for activities with a local adapter or a mock | Copy and cloud-only activities refuse or are mocked; dependency conditions follow Data Factory. |
| Dataflow Gen2 | `orchestration/mashup.py` | Reads definitions only | No local M engine. |
| Database projects | `warehouse_project_service.py` | Yes (files) | The `.sqlproj` layout Fabric's Git integration uses. |
| Git | `git_service.py`, `fabric_git.py` | Yes | Local repo and Fabric item folders. |
| Polars, pandas | engines | Yes | Lazy pipelines (LM-19). |
| KQL, Eventstream, Eventhouse, Activator | `warehouse_monitoring*.py`, `artifacts.py` | **No local engine** | Read-only API use for monitoring only. Gap. |
| Semantic models, Direct Lake | `model_directlake*.py`, `model_bpa*.py`, `tmdl.py` | Checks on this PC | Refresh itself is Fabric only. |
| Capacity and costs | `capacity.py`, `costs.py`, `risk.py` | Reads metrics; risk check | Estimates labelled as estimates. |
| Governance | `governance_checks.py`, `sql_policy.py`, `access.py` | Yes (policy checks) | Sensitivity labels and endorsement read through the API. |
| Prove (Fabric run) | `proof*.py`, `prove_service.py` | Runs in Fabric with a confirm and risk check | The existing path for FABRIC VERIFIED runs. |

### Things that don't exist yet

- A certification, domain or objective model, and a study guide registry.
- Multiple-choice, multiple-response, ordering or scenario questions; timed assessments; review flags.
- Evidence tied to an exam objective or a certification version.
- Readiness by objective, diagnostics, skip by demonstrated evidence.
- Exercise metadata Academy needs: scenario, verification class, estimated time, expected outputs,
  explanation, remediation references, reset and cleanup instructions.
- Python, pipeline and configuration exercises in the lab runner (it runs DuckDB SQL only).
- A local KQL engine or Eventstream simulator.
- Fabric exercise setup and teardown with resource ownership markers.
- Grading material kept apart from the learner's workspace (today solutions sit beside starters).

## Reuse matrix

| Academy need | Reuse | Extend | New |
| --- | --- | --- | --- |
| Content packs | `content.py` loader, `problems()` CI gate, no-HTML rule, file-name rule | Add Academy fields to the exercise format (schema `kratosforge/academy-exercise@1`), keep `learn-module@1` readable | Pack manifest, certification registry |
| Objective registry | — | — | `academy/registry.py`, `packs/dp-700/certification.json` |
| SQL exercises | `labs.py`, `checks.py`, `datasets.py` | Grading material outside the learner's folder; more datasets | — |
| Python and PySpark exercises | `kernel_proc.py` limits, `spark_local.py` | A lab mode for the kernel: no sign-in tokens, no lakehouse, workspace-only files, network off | Output validators (`delta_table`, `files_present`) |
| Pipeline exercises | `orchestration/runner.py` with mocks, `validate.py` | An Academy runner that only allows local adapters and mocks | `pipeline_outcome` validator |
| Configuration exercises | `governance_checks.py` style rule checks | — | `config_matches` validator over JSON settings models |
| Streaming | `spark_stream.py` (availableNow) | Event file generator for windowing | KQL: see [gaps](fabric-gaps.md) |
| Hints | `labs.hint` | Hint use recorded per attempt | — |
| Evidence | `progress.py` evidence rows, `RULES_VERSION` | New row kinds with certification id and version, objective ids, class and mode (rules v4) | — |
| Readiness | `progress.coverage` | — | `academy/readiness.py` (metrics by objective) |
| Exams | — | — | Question bank format, exam engine, keyboard-first runner |
| Ranks | `ranks.py` | Academy evidence can count toward ranks in a later ladder version | — |
| Screens | `learn.js`, page layout, design tokens (#1759) | Learn becomes the Academy area (mockup decides) | Academy screens after the mockup |
| Exercise workspace | SQL editor, notebook editor, data viewer, pipeline canvas | A collapsible exercise panel beside them | — |
| Fabric verification | `prove_service.py` confirm and risk check, `fabric_api.py` | Preflight for permissions and capacity | Setup and teardown with an ownership tag |
| Change detection | Weekly review workflow pattern (`weekly-review.yml`) | — | Study guide check workflow |
