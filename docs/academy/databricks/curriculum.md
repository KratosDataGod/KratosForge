---
site_group: Internal engineering
---

# Databricks Data Engineer Associate: curriculum inventory

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

64 original exercises (12 of them troubleshooting labs), 4 supplementary exercises and 3 capstones,
covering all 33 objectives in [coverage](coverage.md). Exercise ids are unique within the pack
(`dbx-de-associate/E19`); the DP-700 pack has its own E01 to E64.

**This is an inventory, not finished content.** An exercise is done only with every field below, an
independently runnable reference solution, at least two planted wrong answers its validation fails,
and a CI test that runs them. Until then its status is planned.

**Modes.** G guided, C independent challenge, T troubleshooting lab (a broken notebook, table, job,
permission, configuration or ingestion to diagnose, explain, fix and prove fixed).

**Classes.** LV LOCAL VERIFIED, SIM SIMULATED, KO KNOWLEDGE ONLY, and **+DV** where a DATABRICKS
VERIFIED version is planned (DBA-013).

**Engines.** `pyspark` the learner's PySpark in the sandbox on DuckDB's PySpark API with Delta
output (DBA-003); `dbsql` Databricks SQL on DuckDB through sqlglot (DBA-004); `delta` delta-rs;
`stream` the local `availableNow` streaming runner; `copyinto`, `autoloader` the file-arrival model
(DBA-005); `ldp` the declarative pipeline model (DBA-006); `jobs` the Jobs model and failure
simulator (DBA-007); `uc` the Unity Catalog model (DBA-008); `bundle` the offline bundle resolver
(DBA-009); `sparkmetrics` recorded Spark stage metrics, `logs` recorded cluster events and logs
(DBA-010); `config` a document checked by rules; `git` a local repository; `python` plain Python in
the sandbox; `duckdb` the DuckDB lab; `quiz` questions only.

**Datasets** (deterministic, seeded, each in a small and a large size): `shop` (exists in Learn),
`landing` (new: order files arriving in batches with a schema change and a malformed file),
`clicks` (new: nested JSON events), `runs` (new: recorded job run history with a regression),
`stages` (new: recorded Spark stage metrics with skew and spill), `org` (new: users, groups,
service principals, catalogs, tables with sensitivity tags).

## Fields every exercise has

Id; certification and objective ids; difficulty (1 to 3); minutes; business scenario; starting
environment; sample dataset and size; required implementation; expected result; validation rules;
hints (progressive); reference solution (in `grading/`, never in the workspace); troubleshooting
guidance; reset instructions; cleanup instructions (local: remove the workspace folder; Databricks:
remove only objects in the run's ledger); `verifies` and `does_not_verify` sentences; engine label.
These are the DP-700 format's fields (`kratosforge/academy-exercise@1`) plus `difficulty`,
`troubleshooting` and `cleanup`, added for both packs in DBA-001.


## 1. Databricks Intelligence Platform (6%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E01 | Map the platform: control plane, compute, Delta Lake, Unity Catalog | 1.1 | G | KO | quiz | 10 |  |
| E02 | Delta Lake under the hood: read a table's log, history and an earlier version | 1.1 | G | LV | delta | 20 | Delta support |
| E03 | Choose compute for five workloads: serverless, SQL warehouse, job, all-purpose | 1.2 | C | SIM +DV | config | 20 |  |
| E04 | Estimate the cost of a nightly job on three compute choices | 1.2 | C | SIM | config | 15 | Optimize > Cost (DBU) |

## 2. Data Ingestion and Loading (21%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E05 | Incremental batch load with a watermark, rerun-safe | 2.1 | G | LV | pyspark | 20 | DP-700 E21 pattern |
| E06 | Local files to bronze with schema and provenance columns | 2.1 | G | LV | pyspark | 20 |  |
| E07 | Streaming concepts: availableNow, checkpoints and reruns | 2.1 | C | LV | stream | 25 | spark_stream |
| E08 | COPY INTO three batches; a rerun loads nothing twice | 2.2 | G | SIM +DV | copyinto | 20 |  |
| E09 | COPY INTO options: format options, mergeSchema, force | 2.2 | C | SIM +DV | copyinto | 20 |  |
| E10 | Auto Loader with a schema location and arriving files | 2.3 | G | SIM +DV | autoloader | 25 |  |
| E11 | Auto Loader schema evolution: add columns, rescue, fail, none | 2.3 | C | SIM +DV | autoloader | 25 |  |
| E12 | Broken: an Auto Loader stream stops after a new column arrives | 2.3 | T | SIM +DV | autoloader | 25 |  |
| E13 | Configure a Lakeflow Connect managed ingestion into a governed schema | 2.4 | G | SIM +DV | config | 20 |  |
| E14 | A paged REST API to bronze from a notebook (recorded responses) | 2.5 | C | LV | python | 30 |  |
| E15 | A database table to bronze over JDBC-style partitioned reads | 2.5 | C | LV | python | 25 |  |
| E16 | Choose the ingestion method for eight requirements | 2.6 | C | KO | quiz | 15 |  |
| E17 | Nested JSON to Delta: schema, struct fields and explode | 2.7 | G | LV | pyspark | 25 |  |
| E18 | Broken: bronze is missing a batch; reconcile source and target | 2.1 | T | LV | pyspark | 25 |  |

## 3. Data Transformation and Modeling (22%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E19 | Clean bronze orders into a silver Delta table with PySpark | 3.1 | G | LV | pyspark | 25 | shop dataset |
| E20 | The same cleaning in Databricks SQL with CREATE OR REPLACE TABLE | 3.1 | G | LV | dbsql | 20 | shop dataset |
| E21 | Joins: inner, left, multi-key, cross; union vs unionByName | 3.2 | G | LV | pyspark | 25 | LM lessons on joins |
| E22 | Broadcast joins: the hint, the threshold and the plan | 3.2, 3.5 | C | SIM | sparkmetrics | 20 |  |
| E23 | Columns and rows: add, drop, split, rename, filter, explode | 3.3 | G | LV | pyspark | 20 |  |
| E24 | Deduplicate and aggregate: dropDuplicates, latest row, approx counts, summary | 3.4 | G | LV | pyspark | 25 | DP-700 E37 pattern |
| E25 | Window functions: running totals, rank per group, lag | 3.4 | C | LV | dbsql | 20 |  |
| E26 | MERGE an upsert with deletes into silver; rerun changes nothing | 3.1, 2.1 | C | LV | delta | 25 | delta_dml |
| E27 | Tune shuffle partitions, parallelism and the broadcast threshold, then re-measure | 3.5 | C | SIM | sparkmetrics | 25 |  |
| E28 | Gold for BI: view, table, materialized view or streaming table | 3.6 | G | SIM +DV | ldp | 25 |  |
| E29 | A declarative pipeline: bronze, silver and gold datasets | 3.6, 3.7 | G | SIM +DV | ldp | 30 |  |
| E30 | Expectations that warn, drop and fail, and their metrics | 3.7 | C | SIM +DV | ldp | 25 |  |
| E31 | Quality rules and a quarantine table in plain PySpark | 3.7 | C | LV | pyspark | 25 |  |
| E32 | Broken: silver doubles rows after a retried job | 3.4 | T | LV | delta | 25 |  |
| E33 | Broken: a cast quietly turns amounts into NULLs | 3.1 | T | LV | pyspark | 20 |  |

## 4. Working with Lakeflow Jobs (16%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E34 | A first job: notebook, SQL and pipeline tasks with depends_on | 4.2 | G | SIM +DV | jobs | 25 |  |
| E35 | Job parameters and task values between tasks | 4.2 | C | SIM | jobs | 20 | dbutils.jobs.taskValues |
| E36 | Retries and timeouts that match the failure | 4.1 | G | SIM | jobs | 20 |  |
| E37 | Branch with an if/else task and run-if rules | 4.1 | C | SIM | jobs | 25 |  |
| E38 | Loop over regions with a for-each task | 4.1 | C | SIM | jobs | 20 |  |
| E39 | Schedules: cron, time zone and pause | 4.3 | G | SIM +DV | jobs | 15 |  |
| E40 | File arrival and table update triggers | 4.3 | C | SIM | jobs | 20 |  |
| E41 | Time-based or data-driven: choose triggers for five pipelines | 4.4 | C | KO | quiz | 15 |  |
| E42 | Broken: a nightly job fails at task 3; repair only what failed | 4.1, 4.2 | T | SIM +DV | jobs | 25 |  |
| E43 | Broken: a report task reads stale data because a dependency is missing | 4.2 | T | SIM | jobs | 20 |  |

## 5. Implementing CI/CD (10%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E44 | Git folder workflow: branch, commit, push, pull request | 5.1 | G | LV +DV | git | 25 | Source control |
| E45 | Broken: a merge conflict in a notebook source file | 5.1 | T | LV | git | 20 | Source control |
| E46 | Bundle variables, targets and overrides for dev, test and prod | 5.2 | G | SIM | bundle | 25 | Bundles (#1747) |
| E47 | Package a job and a pipeline in a bundle and promote it | 5.3 | C | SIM +DV | bundle | 30 | Bundles (#1747) |
| E48 | A CI workflow that validates, tests and deploys a bundle | 5.4 | C | SIM | config | 25 |  |
| E49 | Unit tests for transformation functions, run before deploy | 5.4 | C | LV | python | 25 |  |
| E50 | Broken: a deploy to prod changed dev resources | 5.2 | T | SIM | bundle | 20 |  |

## 6. Troubleshooting, Monitoring, and Optimization (10%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E51 | Run history: find the regression against the baseline | 6.1 | G | LV | duckdb | 20 | runs dataset |
| E52 | Pipeline health: statuses, blockers and failure rates | 6.2 | C | SIM | jobs | 20 | runs dataset |
| E53 | Read stage metrics: skew, shuffle and spill | 6.3 | G | SIM | sparkmetrics | 25 |  |
| E54 | Broken: one task is 20 times slower; find the skewed key | 6.3 | T | SIM | sparkmetrics | 30 |  |
| E55 | Liquid clustering and predictive optimization | 6.4 | G | SIM +DV | dbsql | 20 |  |
| E56 | Delta maintenance: compaction, vacuum and file counts | 6.4 | C | LV | delta | 20 | Delta support |
| E57 | Broken: a cluster won't start and a library clashes | 6.5 | T | SIM | logs | 20 |  |
| E58 | Broken: the driver runs out of memory on collect | 6.5 | T | SIM | logs | 20 |  |

## 7. Governance and Security (15%)

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E59 | Managed and external tables: create, alter, drop, convert | 7.1 | G | SIM +DV | uc | 25 |  |
| E60 | Privileges down the hierarchy: catalog, schema, table | 7.2 | G | SIM +DV | uc | 25 |  |
| E61 | Broken: an analyst with SELECT still can't read the table | 7.2 | T | SIM | uc | 20 |  |
| E62 | Least privilege for a job's service principal and a group | 7.2 | C | SIM | uc | 20 |  |
| E63 | Row filters and column masks by group | 7.3 | G | SIM +DV | uc | 25 |  |
| E64 | ABAC: governed tags and policies that mask PII across a catalog | 7.4 | C | SIM +DV | uc | 25 |  |

## Supplementary (not in the current guide)

Topics the brief lists that the August 2026 guide doesn't test as objectives. Labelled
supplementary on screen; never counted toward coverage or readiness.

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| X01 | Databricks Connect: run local code on a remote cluster | 1.2 | G | KO +DV | dbconnect | 20 | Databricks Connect engine |
| X02 | Notebook workflow: widgets, %run, dbutils and notebook exit values | 4.2 | G | LV | pyspark | 20 | dbutils_local |
| X03 | Lineage and audit: where they come from and what they show | 7.2 | G | KO | quiz | 10 |  |
| X04 | Delta Sharing and Lakehouse Federation (earlier guide topics) | 7.2 | G | KO | quiz | 10 |  |

## Troubleshooting labs

E12, E18, E32, E33, E42, E43, E45, E50, E54, E57, E58, E61. Each has a `fault` (summary, symptom,
what was planted), and its validation checks the repair *and* that nothing else changed
(`unchanged`), plus a short written cause the learner picks or types (graded as `assessment`).

## Capstones

| Id | Project | Objectives | Stages | Independent validation | Portfolio artifact |
| --- | --- | --- | --- | --- | --- |
| C1 | Retail lakehouse: bronze, silver, gold from arriving order files | 2.1, 2.3, 3.1, 3.4, 3.6, 3.7 | Incremental ingest with schema drift; silver cleaning and MERGE; quality rules with quarantine; gold tables and a view for BI | Hidden seed: the validators rerun the learner's code on a second, unseen data seed; idempotent rerun; reconciliation of counts and sums | Architecture page, lineage diagram, quality report, run evidence |
| C2 | Production data pipeline | 4.1, 4.2, 4.3, 5.2, 5.3, 6.1, 6.2 | Job with parameters, dependencies, retries, a condition task and a for-each; a failure injected and repaired; bundle with dev and prod targets; run-history baseline | Jobs model runs with injected failures the learner hasn't seen; bundle resolution per target | Job graph, runbook, deploy plan, incident note |
| C3 | Governed enterprise data platform | 5.4, 7.1, 7.2, 7.3, 7.4 | Catalog and schema layout, managed and external tables, group-based grants, row filter and column mask, ABAC tags, tests in CI, deploy | Access matrix checked for every principal and table, including denied paths; tests must catch a planted regression | Access matrix, governance decision record, CI log |

Each capstone is SIMULATED or LOCAL VERIFIED per stage on this PC, with a DATABRICKS VERIFIED
version planned (DBA-015).

