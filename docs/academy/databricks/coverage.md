# Databricks Data Engineer Associate: objective coverage

Every objective in the current exam guide, word for word, with what Academy plans for it. The same
data is the pack's `certification.json` (DBA-001), which CI checks: no objective may be left out,
and every objective that needs Databricks has a gap record saying what this PC can't show.

## Which guide

| | |
| --- | --- |
| Certification page | https://www.databricks.com/learn/certification/data-engineer-associate (read 2026-11-03; it links the August 2026 guide) |
| Current guide | *Databricks Certified Data Engineer Associate Exam Guide*, "Exam Published May 4, 2026 / Exam Guide Updated August 6, 2026" |
| Guide in the brief | The May 4, 2026 guide. Same 7 sections, same weights, same 33 objectives; the August update rewords 1.1, 1.2, 3.5, 5.1, 5.2 and 5.3 (product names) and changes typography only elsewhere. The May wording is kept in the registry beside each changed objective. |
| Exam | 45 scored multiple-choice questions, up to 10 unscored, 90 minutes. Code is in SQL when possible, otherwise Python. |
| Registry version | `2026-08-06` |

Objectives from guides before May 4, 2026 (Delta Sharing, Lakehouse Federation, audit log storage,
Databricks Connect as an objective) are **not** in this matrix. Where the brief asks for them they are
supplementary exercises X01 to X04, labelled so, and they don't count toward coverage.

**Columns.** *Needs Databricks*: only DATABRICKS VERIFIED evidence completes it. *Best here*: the
strongest class this PC can give (LV LOCAL VERIFIED, SIM SIMULATED, KO KNOWLEDGE ONLY). *Qs*: planned
practice questions. Gap records are [below](#gap-records).


## 1. Databricks Intelligence Platform (6%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | Understand the core components of the Databricks Platform, such as its architecture, Delta Lake, and Unity Catalog. | no | LV | E01, E02 |  | `delta_table`, `assessment` |  | 5 |
| 1.2 | Understand Databricks Platform's compute services, including their characteristics, limitations, and cost models, and select the most suitable option for each workload use case. | yes | SIM | E03, E04 |  | `config_matches`, `assessment`, `databricks_check` | G-09 | 7 |

## 2. Data Ingestion and Loading (21%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.1 | Enable and detail data ingestion patterns, including batch, streaming, and incremental loading, and import data from sources such as local files, Lakeflow Connect standard connectors, and Lakeflow Connect managed connectors. | no | LV | E05, E06, E07, E18, E26 | C1 | `result_matches`, `delta_table`, `idempotent`, `data_quality` |  | 9 |
| 2.2 | Use the COPY INTO command to incrementally load files from cloud object storage (ADLS/S3/GCS) into Unity Catalog-governed tables. | yes | SIM | E08, E09 |  | `delta_table`, `idempotent`, `databricks_check` | G-01 | 6 |
| 2.3 | Use Auto Loader with schema enforcement and schema evolution in batch modes (for example, directory listing or file notification) to land data into Unity Catalog-governed tables. | yes | SIM | E10, E11, E12 | C1 | `delta_table`, `schema_matches`, `idempotent`, `databricks_check` | G-01 | 8 |
| 2.4 | Configure Lakeflow Connect to reliably ingest data from diverse enterprise sources into Unity Catalog-governed tables. | yes | SIM | E13 |  | `config_matches`, `databricks_check` | G-08 | 5 |
| 2.5 | Use JDBC/ODBC or REST clients in notebooks to land data into cloud storage or directly into Unity Catalog-governed tables, usually orchestrated and scheduled with Lakeflow Jobs. | no | LV | E14, E15 |  | `result_matches`, `artifact_present`, `idempotent` |  | 5 |
| 2.6 | Prioritize between Auto Loader, Lakeflow Connect (standard and managed connectors), partner connectors, and other ingestion methods based on technical requirements such as data volume, ingestion frequency, data types, and governance needs with Unity Catalog. | no | KO | E16 |  | `assessment` |  | 6 |
| 2.7 | Ingest semi-structured and unstructured data (for example, JSON and nested data) via Lakeflow Connect and other managed connectors into Unity Catalog-governed Delta tables. | no | LV | E17 |  | `schema_matches`, `result_matches` |  | 5 |

## 3. Data Transformation and Modeling (22%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3.1 | Implement data cleaning by reading bronze tables with PySpark/SQL, cleaning nulls, standardizing data types, and writing to new silver tables. | no | LV | E19, E20, E26, E33 | C1 | `schema_matches`, `data_quality`, `result_matches`, `delta_table`, `idempotent` |  | 7 |
| 3.2 | Combine DataFrames with operations such as Inner join, left join, broadcast join, multiple keys, cross join, union, and union all. | no | LV | E21, E22 |  | `result_matches`, `plan_shows`, `assessment` |  | 7 |
| 3.3 | Manipulate columns, rows, and table structures by adding, dropping, splitting, renaming column names, applying filters, and exploding arrays. | no | LV | E23 |  | `schema_matches`, `result_matches` |  | 5 |
| 3.4 | Perform data deduplication operations and aggregate operations on DataFrames, such as count, approximate count distinct, and mean, summary. | no | LV | E24, E25, E32 | C1 | `result_matches`, `data_quality` |  | 7 |
| 3.5 | Understand the basic tuning parameters (spark.sql.shuffle.partitions, spark.default.parallelism, spark.executor/driver.memory, spark.sql.autoBroadcastJoinThreshold) and re-measure the performance. | yes | SIM | E22, E27 |  | `plan_shows`, `assessment`, `databricks_check` | G-05 | 6 |
| 3.6 | Understand the difference between, and how to build, Gold layer objects such as materialized views, views, streaming tables, and tables for BI and analytics teams in Unity Catalog. | yes | SIM | E28, E29 | C1 | `config_matches`, `delta_table`, `assessment`, `databricks_check` | G-04, G-02 | 6 |
| 3.7 | Apply data quality checks and validation rules to ensure reliable Silver and Gold datasets. | no | LV | E29, E30, E31 | C1 | `data_quality`, `pipeline_outcome`, `result_matches` |  | 7 |

## 4. Working with Lakeflow Jobs (16%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4.1 | Implement control flows (retries and conditional tasks such as branching and looping) using Lakeflow Jobs for pipeline orchestration | yes | SIM | E36, E37, E38, E42 | C2 | `job_graph`, `pipeline_outcome`, `databricks_check` | G-03 | 9 |
| 4.2 | Configure common tasks (notebook, SQL query, dashboard, and pipeline tasks) and their dependencies using Lakeflow Jobs and its DAG-based task graph | yes | SIM | E34, E35, E42, E43 | C2 | `job_graph`, `pipeline_outcome`, `databricks_check` | G-03 | 9 |
| 4.3 | Implement job schedules using Lakeflow Jobs with an understanding of trigger types (scheduled, file arrival, and table update) | yes | SIM | E39, E40 | C2 | `config_matches`, `databricks_check` | G-03 | 7 |
| 4.4 | Choose between time-based and data-driven triggers based on data availability and pipeline dependencies. | no | KO | E41 |  | `assessment` |  | 5 |

## 5. Implementing CI/CD (10%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5.1 | Manage your code development workflow within the Databricks workspace UI, including creating and switching between branches in Databricks Git folders (formerly Databricks Repos), committing and pushing changes, and creating pull requests using Databricks Git integration. | yes | LV | E44, E45 |  | `artifact_present`, `databricks_check` | G-07 | 5 |
| 5.2 | Understand environment-specific configuration using Declarative Automation Bundle (formerly Databricks Asset Bundles) variables and overrides while promoting the same codebase across dev, test, and prod targets. | no | SIM | E46, E50 | C2 | `bundle_resolves`, `config_matches` |  | 5 |
| 5.3 | Deploy Declarative Automation Bundles (formerly Databricks Asset Bundles) to package, configure, and promote Lakeflow Jobs, Lakeflow Declarative Pipelines, and other workspace assets across dev, test, and prod environments. | yes | SIM | E47 | C2 | `bundle_resolves`, `databricks_check` | G-06 | 5 |
| 5.4 | Understand the Databricks CLI to validate, deploy, and manage Declarative Automation Bundles (formerly Databricks Asset Bundles) and other workspace assets in automated CI/CD workflows. | no | SIM | E48, E49 | C3 | `config_matches`, `result_matches` |  | 5 |

## 6. Troubleshooting, Monitoring, and Optimization (10%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6.1 | Identify trends in job performance using the Lakeflow Jobs run history view to compare current execution times against historical baselines. | yes | LV | E51 | C2 | `result_matches`, `databricks_check` | G-03 | 5 |
| 6.2 | Use the Lakeflow Jobs UI to monitor pipeline health by interpreting job statuses, viewing DAG-based task graphs to spot upstream blockers, and tracking pipeline run times and failure rates. | yes | SIM | E52 | C2 | `assessment`, `databricks_check` | G-03 | 5 |
| 6.3 | Identify common performance bottlenecks such as data skew, shuffling, and disk spilling by interpreting stage-level metrics in the Spark UI. | yes | SIM | E53, E54 |  | `plan_shows`, `assessment`, `databricks_check` | G-05 | 5 |
| 6.4 | Understand the features of Liquid Clustering and predictive optimization. | yes | SIM | E55, E56 |  | `delta_table`, `assessment`, `databricks_check` | G-10 | 5 |
| 6.5 | Diagnose cluster startup failures, library conflicts, and out-of-memory issues. | yes | SIM | E57, E58 |  | `assessment`, `databricks_check` | G-09 | 5 |

## 7. Governance and Security (15%)

| Id | Objective | Needs Databricks | Best here | Exercises | Capstones | Validation | Gaps | Qs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7.1 | Differentiate between managed and external tables in Unity Catalog and perform basic operations (create, modify, delete, and convert between managed and external tables) on them. | yes | SIM | E59 | C3 | `grants`, `assessment`, `databricks_check` | G-02 | 7 |
| 7.2 | Configure access controls using the UI and SQL by applying GRANT, REVOKE, and DENY privileges to principals (users, groups, and service principals) at appropriate levels of the security hierarchy. | yes | SIM | E60, E61, E62 | C3 | `grants`, `assessment`, `databricks_check` | G-02 | 9 |
| 7.3 | Understand column-level masking and row-level security to restrict data visibility based on user groups. | yes | SIM | E63 | C3 | `grants`, `result_matches`, `databricks_check` | G-02 | 7 |
| 7.4 | Understand Unity Catalog ABAC policies to centrally control row-level filtering and column masking for sensitive data. | yes | SIM | E64 | C3 | `grants`, `assessment`, `databricks_check` | G-02 | 7 |

## Totals

33 objectives; every one has at least one exercise. 20 need Databricks for completion;
13 can be completed on this PC. 206 practice questions planned (at least 5 per objective, more
where the section weighs more), enough for three different 45-question practice exams.

## Content notes

- **7.2.** The guide names DENY. Unity Catalog's documented privilege statements are GRANT and REVOKE; DENY exists only as a Beta ABAC DENY policy, and only for MANAGE ACCESS CONTROL (read 2026-11-03). Exercises teach that difference.
- **7.1.** SET MANAGED / UNSET MANAGED need Databricks Runtime 17.3 LTS or above or serverless; rollback is possible for 14 days (read 2026-11-03).

Documentation links for each objective are in `certification.json`; every one was fetched on
2026-11-03 and its page covers the objective.

## Gap records

| Id | Gap | Why | Closes with |
| --- | --- | --- | --- |
| G-01 | No COPY INTO or Auto Loader on this PC | KratosForge models file tracking, schema inference and evolution itself; it is not Databricks' implementation, so results are SIMULATED. | DATABRICKS VERIFIED runs of E08-E12 in the learner's workspace (DBA-013). |
| G-02 | No Unity Catalog on this PC | Privileges, inheritance, ownership, managed storage, row filters, column masks and ABAC are evaluated by KratosForge's model of the documented rules. | DATABRICKS VERIFIED runs of E59-E64 with tagged, Academy-created objects (DBA-013). |
| G-03 | No Lakeflow Jobs scheduler or run history on this PC | Job definitions are validated and run by a local model; triggers, run history and the Jobs UI exist only in a workspace. | DATABRICKS VERIFIED job runs (E34, E42) on serverless job compute after the learner's go. |
| G-04 | No Lakeflow Declarative Pipelines runtime on this PC | Materialized views, streaming tables, refresh semantics and the pipeline event log are modelled, not run. | DATABRICKS VERIFIED pipeline update (E29) in the learner's workspace. |
| G-05 | No Apache Spark on this PC | The notebook's spark is DuckDB's PySpark API, so physical plans, partitions, broadcast and Spark UI metrics are recorded Spark runs, not live ones. | Optional local Apache Spark (decision pending) for LOCAL VERIFIED; Databricks runs for DATABRICKS VERIFIED. |
| G-06 | Bundle deploys need a workspace | Variable and target resolution is checked offline; a deploy needs the Databricks CLI signed in to a workspace. | DATABRICKS VERIFIED deploy of E47 to a dev target. |
| G-07 | Git folders are a workspace feature | Branch, commit, push and pull request steps run in a local repository; the Git folder UI is not shown. | DATABRICKS VERIFIED Git folder check (read-only API) for E44. |
| G-08 | Lakeflow Connect is a managed service | Connector configurations are checked as documents; ingestion gateways and managed connectors only run in Databricks. | DATABRICKS VERIFIED read of a learner's ingestion pipeline definition. |
| G-09 | Compute, cluster events and libraries are workspace features | Compute choice, startup failures and library conflicts are taught from recorded events and logs. | DATABRICKS VERIFIED read-only check of the learner's compute policy and cluster events. |
| G-10 | Liquid clustering and predictive optimization aren't in delta-rs | OPTIMIZE and VACUUM run locally; CLUSTER BY and predictive optimization are explained and modelled. | DATABRICKS VERIFIED run of E55 on a Unity Catalog managed table. |
