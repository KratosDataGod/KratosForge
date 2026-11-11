---
site_group: Internal engineering
---

# Databricks path: capability audit, reuse and gaps

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

Read on 2026-11-03 from `main` (d4b1530) and the open Academy stack: #1776 registry, #1778 exercise
format and classes, #1779 evidence and readiness, #1782 learner workspace and engine methods, #1777
validators, #1781 exam engine, plus the grading sandbox work in progress (PR-F, not yet opened).
"Exists" means the code is on `main` or in one of those PRs; each row names the module.

## What exists

### Academy (DP-700 work, all still in review)

| Capability | Where | State |
| --- | --- | --- |
| Certification registry: packs, published objectives verbatim, weights, snapshot hash, gap records, `problems()` | `academy/registry.py` (#1776) | In review. Fabric-specific in three places: doc hosts are `learn.microsoft.com` only, the platform flag is `fabric_required`, the platform check is `fabric_check` |
| Exercise format, modes (guided, challenge, troubleshoot), classes, `verifies`/`does_not_verify`, self test | `academy/exercises.py` (#1778) | In review. Class list and the "local result can't claim a Fabric engine" rule are Fabric-only |
| Evidence rows and readiness: six measures per objective, version crosswalk | `academy/readiness.py`, `progress.py` (#1779) | In review. Measure `fabric_verified` named for Fabric |
| Learner workspace, `academy_*` engine methods, reset | `academy/lab.py`, `academy_service.py` (#1782) | In review, Security CLEAR. DuckDB exercises only |
| Validators: schema, data quality, config, artifact, Delta log, pipeline outcome, unchanged | `academy/validators.py`, `academy/workspace.py` (#1777) | In review, Security CLEAR |
| Learner Python process (Job Object, import guard, no tokens) | `academy/sandbox.py` (PR-F, patch in project files) | Not opened; waits for #1777 |
| Exam engine: question format, forms, timing, case studies | `academy/exam.py` (#1781) | In review |
| Screens | Mockup v7 (UX PASS), Assessment Center mockup | Waiting on Chris's reaction |

### Databricks support in KratosForge

| Capability | Where | What it gives Academy |
| --- | --- | --- |
| Local `spark` (DuckDB PySpark API), `saveAsTable`, `DeltaTable`, `MERGE` builder | `spark_local.py`, `spark_api.py`, `spark_compat.py` | PySpark DataFrame exercises graded on table contents. Not Apache Spark |
| Structured streaming, `availableNow` with a checkpoint, file and Delta sources | `spark_stream.py` | Incremental and streaming-concept exercises; the base for an Auto Loader model |
| `MERGE INTO`, `UPDATE`, `DELETE` on Delta (delta-rs) | `delta_dml.py` | MERGE and upsert exercises |
| Databricks SQL dialect on DuckDB (sqlglot), function parity table | `engines/local_sql.py`, `engines/databricks_sql_parity.py` | Databricks SQL exercises, with each function's parity known |
| `dbutils` (fs, widgets, notebook, secrets, jobs.taskValues) on this PC | `dbutils_local.py` | Notebook and job-parameter exercises run unchanged |
| Databricks sign-in from a connection profile (CLI profile, OAuth U2M/M2M), tokens in memory only | `engines/databricks_auth.py`, `connprofiles.py` | The adapter's credentials; no new credential store |
| Read-only workspace discovery (who, warehouses, clusters, Unity Catalog) | `engines/databricks_discovery.py` | Capability detection with no DBU spend |
| Databricks SQL warehouse engine, Databricks Connect engine (own process, locked runtimes) | `engines/databricks_sql.py`, `engines/dbconnect.py`, `engines/runtimes.py` | Remote execution for DATABRICKS VERIFIED |
| Remote run confirmation and usage | `engines/authorization.py`, `engines/usage.py` | Confirm before cloud spend |
| Live-test harness with a test tag and dry run | `dbx_livetest.py` | Pattern for tagging and safe cleanup |
| Platform manifest and parity levels | `platforms.py`, #1772 | Where parity claims come from |
| Bundles validate, plan, deploy (Preview) | #1747 (open) | Bundle exercises when it lands |
| Jobs and Lakeflow update monitoring (Preview) | #1741 (draft) | Job-run exercises in Databricks |
| Git and source control | `git/`, `git_service.py` | Git folder workflow exercises (local repo) |
| Learn: lessons, labs, checks, ranks | `learn/`, `ranks.py` | Lessons, the `shop` dataset, `result_matches` and friends |

### Things that don't exist yet

- **Apache Spark on this PC.** `engines/registry.py` lists `local-spark` as planned ("whether it
  should is an open decision"). Without it no exercise can be LOCAL VERIFIED for Spark's own
  behaviour (plans, partitions, broadcast, the Spark UI).
- **COPY INTO and Auto Loader** (`cloudFiles`) on this PC.
- **Lakeflow Spark Declarative Pipelines** (`dp`/`dlt` decorators, expectations, materialized views,
  streaming tables) on this PC.
- **Lakeflow Jobs** model: tasks, `depends_on`, `run_if`, condition and for-each tasks, retries,
  triggers, repair runs. The orchestration runner models Fabric pipelines, not Jobs.
- **Unity Catalog** permissions model: securable hierarchy, privilege inheritance, ownership, row
  filters, column masks, ABAC policies.
- **A Databricks Academy adapter** that ties discovery, sign-in, remote engines, tagging and cleanup
  to exercises.

## Reuse and gaps

| Directive need | Reuse | Gap (story) |
| --- | --- | --- |
| Versioned competency registry | `registry.py` as is, plus a `platform` per pack | Platform-named flag, check, class and doc hosts (DBA-001) |
| Exercise format, modes, classes | `exercises.py` | DATABRICKS VERIFIED class and `databricks` adapter (DBA-001) |
| Learner profile, evidence, readiness | `progress.py`, `readiness.py` | Rename the platform measure to `platform_verified` (DBA-001, agreed with the Academy lead) |
| Grading engine | `validators.py`, `learn/checks.py` | `idempotent`, `job_graph`, `grants`, `bundle_resolves`, `plan_shows` (DBA-011) |
| Learner code isolation | `sandbox.py` (PR-F) | A `pyspark_lab` adapter that runs in it (DBA-003) |
| PySpark and Delta exercises | `spark_local.py`, `delta_dml.py`, deltalake | Spark shim that works inside the sandbox, which blocks `fabric_desktop` imports (DBA-003) |
| Databricks SQL exercises | Databricks dialect on DuckDB, parity table | Exercises refuse functions the parity table marks `differs` (DBA-004) |
| Auto Loader and COPY INTO | `spark_stream.py` checkpoint logic | File-arrival simulator, `cloudFiles` and `COPY INTO` models (DBA-005) |
| Declarative pipelines | Delta writes, `validators.data_quality` | Local pipeline model (DBA-006) |
| Jobs | `dbutils_local.py` task values, run-record shape from `orchestration/runs.py` | Jobs model and failure simulator (DBA-007) |
| Unity Catalog | none | Permission model (DBA-008) |
| CI/CD | `git_service.py`, #1747 bundles | Offline bundle resolver for exercises (DBA-009) |
| Optimization | Delta maintenance (delta-rs optimize, vacuum), recorded metrics | Recorded Spark stage metrics dataset (DBA-010) |
| Assessment bank | `exam.py` (45 questions, 90 minutes already configurable per form) | Databricks question bank (DBA-012) |
| Remote verification | discovery, auth, SQL warehouse, Databricks Connect, authorization, live-test tags | Academy adapter, ledger, cleanup (DBA-002, DBA-013) |
| Cross-platform credit | registry `crosswalk` (version to version) | Shared-competency map across packs (DBA-014) |
| Change monitoring | ACD-020 study-guide check | A second source type: Databricks exam guide PDF (DBA-016) |
| Screens | Academy mockup v7 | Catalog shows two paths; nothing Databricks-specific until a mockup (DBA-001 UI follow-up) |
