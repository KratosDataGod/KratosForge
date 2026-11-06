# DP-700 curriculum inventory

64 original exercises (12 of them broken environments) and 3 capstone projects, covering all 49
objectives in [DP-700 coverage](dp-700-coverage.md). Every exercise runs on synthetic, seeded data
the app generates on this PC, so the same seed always gives the same data and the same answer.

**This is an inventory, not finished content.** An exercise is done only when it has a lesson, a
starter, an independently runnable reference solution, validation rules, and a CI test that runs
the solution and the validation. Status today: planned, unless the *Reuse* column names Learn
content it is built from. Built as drafts in the `dp-700` pack, with CI running each reference
solution and planted wrong answers: one file per built exercise in `sidecar/tests/academy_pins/dp-700`, which a
test keeps equal to the pack.

**Modes.** G Guided (explains, demonstrates, then a guided task with progressive hints).
C Challenge (a realistic problem, limited guidance, the learner's artifacts are graded).
T Troubleshoot (a deliberately broken environment to diagnose and repair). Every exercise can also
be opened in the other modes where it makes sense; the column is its primary mode.

**Classes.** LV LOCAL VERIFIED, SIM SIMULATED, KO KNOWLEDGE ONLY, plus **+FV** where a FABRIC
VERIFIED version is planned (ACD-014). See [verification classes](architecture.md#verification-classes).

**Engines.** `duckdb` lab runner, `pyspark` local PySpark API in a lab kernel, `delta` delta-rs,
`pipeline` local pipeline runner, `config` a settings model checked by rules, `git` a local repo,
`sqlproj` a database project, `quiz` questions only.

**Datasets.** `shop` (exists: customers, orders, items, six planted defects), `telemetry` (new:
device events with late and duplicate events), `hr` (new: people, departments, sensitivity
columns), `runs` (new: recorded pipeline, notebook, refresh and capacity histories).

## 1. Implement and manage an analytics solution

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E01 | Right-size Spark for three teams: starter pool, custom pool, autoscale and high concurrency | 1.1.1 | G | SIM +FV | config | 20 | |
| E02 | An environment for a notebook: libraries, Spark properties and the default environment | 1.1.1 | C | SIM +FV | config | 20 | |
| E03 | Domains for a data mesh: assign workspaces, delegate settings | 1.1.2 | G | SIM | config | 15 | Data mesh docs |
| E04 | OneLake settings: external app access, file explorer, shortcut cache | 1.1.3 | G | SIM +FV | config | 15 | LM-07 |
| E05 | Data workflow (Apache Airflow job) settings and when to use them | 1.1.4 | G | KO | quiz | 10 | |
| E06 | Put a workspace under Git: commit, branch, resolve a conflict in an item definition | 1.2.1 | G | LV +FV | git | 25 | Source control |
| E07 | Feature workspaces: branch out, sync, and update from Git | 1.2.1 | C | SIM +FV | git | 20 | |
| E08 | A warehouse as a database project: add a table and view, catch a breaking change | 1.2.2 | C | LV +FV | sqlproj | 25 | LM-23 |
| E09 | Deployment pipeline: stages, deployment rules, and what doesn't deploy | 1.2.3 | G | SIM +FV | config | 20 | |
| E10 | Least privilege workspace roles for six people | 1.3.1 | G | SIM +FV | config | 15 | |
| E11 | Share a lakehouse: Read, ReadData, ReadAll and the SQL endpoint | 1.3.2 | C | SIM +FV | config | 15 | LM-24 |
| E12 | Row-level security: a predicate that shows each region its rows | 1.3.3 | G | LV +FV | duckdb | 25 | |
| E13 | Column- and object-level security: GRANT and DENY that hide salary | 1.3.3 | C | SIM +FV | config | 20 | |
| E14 | OneLake data access roles for folders and tables | 1.3.3 | C | SIM +FV | config | 20 | LM-24 |
| E15 | Dynamic data masking: pick the mask for each column, who sees what | 1.3.4 | G | SIM +FV | config | 15 | |
| E16 | Sensitivity labels and endorsement: promote, certify, label inheritance | 1.3.5, 1.3.6 | G | KO +FV | quiz | 15 | Governance |
| E17 | Pipeline or notebook: eight scenarios, choose and justify | 1.4.1, 2.2.2 | G | KO | quiz | 15 | LM-08 |
| E18 | Schedule a pipeline and trigger it when a file lands | 1.4.2 | C | SIM +FV | config | 20 | |
| E19 | Parameterised pipeline: ForEach over tables with dynamic expressions | 1.4.3 | C | LV | pipeline | 30 | Orchestration |
| E20 | Notebook orchestration with runMultiple, parameters and exit values | 1.4.3 | C | LV | pyspark | 25 | notebookutils |

## 2. Ingest and transform data

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E21 | Full or incremental: load orders with a watermark that misses nothing | 2.1.1 | G | LV | duckdb | 20 | LM-12 |
| E22 | Incremental MERGE into a Delta table | 2.1.1 | C | LV +FV | delta | 25 | LM-11, LM-21 |
| E23 | Prepare a customer dimension: surrogate keys and an Unknown member | 2.1.2 | G | LV | duckdb | 20 | LAB-03 |
| E24 | Type 2 history: close the old row, add the new one | 2.1.2 | C | LV | duckdb | 25 | LAB-05 |
| E25 | Load a fact when its customer arrives late (inferred members) | 2.1.2, 2.2.9 | C | LV | duckdb | 25 | |
| E26 | Streaming load pattern: availableNow with a checkpoint into Bronze | 2.1.3, 2.3.3 | G | LV +FV | pyspark | 25 | |
| E27 | Lakehouse, warehouse, eventhouse or SQL database: choose the store | 2.2.1 | G | KO | quiz | 15 | LM-06, LM-23 |
| E28 | Dataflow Gen2, notebook or T-SQL: choose the transformation tool | 2.2.2 | G | KO | quiz | 15 | |
| E29 | Shortcuts: a table shortcut, a folder shortcut, and a chain to avoid | 2.2.3 | C | SIM +FV | config | 20 | LM-24 |
| E30 | Mirroring: what replicates, how fast, and how to query it | 2.2.4 | G | KO +FV | quiz | 15 | |
| E31 | Copy activity design: staging, schema mapping and fault tolerance | 2.2.5 | C | SIM +FV | pipeline | 25 | Copy jobs |
| E32 | PySpark: clean, cast and join raw orders | 2.2.6 | G | LV +FV | pyspark | 25 | LM-08 |
| E33 | Spark SQL: the same transformation in SQL | 2.2.6 | C | LV +FV | duckdb | 20 | |
| E34 | KQL: summarize, extend and project event data | 2.2.6, 2.3.4 | G | KO +FV | quiz | 20 | |
| E35 | Denormalize a star into a wide reporting table | 2.2.7 | C | LV | duckdb | 20 | LAB-07 |
| E36 | Group and aggregate with ROLLUP and GROUPING SETS | 2.2.8 | G | LV | duckdb | 20 | LM-04 |
| E37 | Deduplicate a retried load: one row per key, the latest version | 2.2.9 | G | LV | duckdb | 20 | LAB-04 |
| E38 | Missing data: defaults, quarantine and the Unknown member | 2.2.9 | C | LV | duckdb | 20 | LM-13 |
| E39 | Late-arriving facts: a reprocessing window that keeps totals right | 2.2.9 | C | LV | duckdb | 25 | |
| E40 | Eventstream, Spark structured streaming or KQL: choose the engine | 2.3.1 | G | KO | quiz | 15 | |
| E41 | Design an eventstream: filter, aggregate and route to two destinations | 2.3.2 | C | SIM +FV | config | 25 | |
| E42 | Structured streaming with foreachBatch and MERGE | 2.3.3 | C | LV +FV | pyspark | 30 | |
| E43 | Tumbling, hopping and session windows over device events | 2.3.5 | G | LV +FV | duckdb | 25 | LM-15 |

## 3. Monitor and optimize an analytics solution

| Id | Exercise | Objectives | Mode | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E44 | Monitoring hub: find the failed ingestion and the run that got slower | 3.1.1, 3.1.2 | G | SIM +FV | duckdb (`runs`) | 20 | |
| E45 | Semantic model refresh: read the history, spot a Direct Lake fallback | 3.1.3 | C | SIM +FV | duckdb (`runs`) | 20 | LM-09 |
| E46 | Alerts: an Activator rule and a pipeline failure alert | 3.1.4 | C | SIM +FV | config | 20 | |
| E47 | Capacity Metrics: find the throttled hour and what caused it | 3.1.2 | C | SIM | duckdb (`runs`) | 20 | FinOps |
| E60 | Optimize a pipeline: parallel ForEach, fewer sequential steps | 3.3.2 | C | SIM +FV | pipeline | 25 | |
| E61 | Optimize a warehouse: loads, data types, statistics and result set caching | 3.3.3 | G | KO +FV | quiz | 15 | |
| E62 | Optimize an eventhouse: caching, retention, materialized views and query shape | 3.3.4 | G | KO +FV | quiz | 15 | |
| E63 | File layout for Spark: partitioning and file pruning on a Delta table | 3.3.5 | C | LV +FV | delta | 25 | LM-27 |
| E64 | Read a query plan and fix the expensive step | 3.3.6 | G | LV | duckdb | 20 | LM-18 |

## Broken environments (Troubleshoot)

Each starts from a working build with a planted fault. The learner finds it, fixes it, and the
validation checks both the fix and that nothing else changed.

| Id | Broken environment | Objectives | Class | Engine | Minutes | Reuse |
| --- | --- | --- | --- | --- | --- | --- |
| E48 | A pipeline fails on a bad dynamic expression and a wrong dependency condition | 3.2.1 | LV | pipeline | 25 | |
| E49 | A ForEach loads the wrong tables: a parameter type mismatch | 3.2.1 | LV | pipeline | 25 | |
| E50 | A Dataflow Gen2 loads nothing: a destination type mismatch and a broken step | 3.2.2 | SIM | config | 20 | Dataflow reader |
| E51 | A notebook append fails: schema drift on a Delta table | 3.2.3 | LV | pyspark | 25 | LM-22 |
| E52 | A notebook runs out of memory: collect() on a large table and a skewed join | 3.2.3, 3.3.5 | LV | pyspark | 25 | |
| E53 | An eventhouse loads empty rows: an ingestion mapping that doesn't match | 3.2.4 | SIM | config | 20 | |
| E54 | An eventstream loses events: a schema change at the source | 3.2.5 | SIM | config | 20 | |
| E55 | T-SQL errors: read the messages, fix conversion, divide-by-zero and MERGE faults | 3.2.6 | SIM | quiz + sqlproj | 25 | LAB-09 |
| E56 | A slow lakehouse table: thousands of small files and no statistics | 3.3.1 | LV +FV | delta | 25 | LM-25 |
| E57 | An incremental load that misses late rows | 2.1.1, 2.2.9 | LV | duckdb | 20 | LM-12 |
| E58 | A retried pipeline doubled a table | 2.2.9, 3.2.1 | LV | duckdb | 20 | LAB-04 |
| E59 | A query that got slow: a filter that can't skip data | 3.3.6 | LV | duckdb | 20 | LAB-02 |

## Capstone projects

| Id | Capstone | Objectives | Class | Hours |
| --- | --- | --- | --- | --- |
| C1 | **Retail lakehouse.** Bronze to Silver to Gold for the `shop` data: a parameterised pipeline, incremental loads with MERGE, a type 2 customer dimension, quality checks, a Gold star, OPTIMIZE, and a run that's safe to repeat. | 1.4.3, 2.1.1, 2.1.2, 2.2.5–2.2.9, 3.2.1, 3.3.1, 3.3.6 | LV +FV | 3 |
| C2 | **Real-time telemetry.** Device events with late and duplicate arrivals: windowed aggregates with structured streaming locally, then the eventstream, eventhouse and KQL design, and an alert. | 2.1.3, 2.3.1–2.3.5, 3.1.4, 3.2.5 | LV + SIM +FV | 3 |
| C3 | **Governed warehouse release.** A warehouse database project in Git, deployed through stages with rules, row-level security and masking for three audiences, monitoring and a fault to repair. | 1.2.1–1.2.3, 1.3.1–1.3.4, 3.1.1, 3.2.6, 3.3.3 | SIM +FV | 3 |

## Totals

| | Count |
| --- | --- |
| Exercises | 64 (52 Guided or Challenge, 12 Troubleshoot) |
| LOCAL VERIFIED | 30 |
| SIMULATED | 24 |
| KNOWLEDGE ONLY | 10 |
| With a planned FABRIC VERIFIED version | 34 |
| Capstones | 3 |
| Built from existing Learn lessons or labs | 26 |

Exam questions are separate from exercises (ACD-013): at least four original questions per
objective (196 or more), in the five types the directive names.
