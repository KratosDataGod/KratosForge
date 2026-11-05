# DP-700 objective coverage

**Source:** [Microsoft's DP-700 study guide](https://learn.microsoft.com/en-us/credentials/certifications/resources/study-guides/dp-700),
read 2026-11-03. The page says "Last updated on 2026-10-30" and shows one skills list with no
upcoming version and no change log.

**Version.** The directive targets the objectives effective 19 October 2026. The page gives no
effective date for its list, so that date is **UNVERIFIED**: the registry records this list as
version `2026-10-30` (the page's own update date) and the check in ACD-020 compares against it.

**Weights.** Each domain is published as 30–35%. 49 objectives in 9 skill areas. Microsoft notes
that the bullets illustrate the skills and related topics may also be tested, and that most
questions cover generally available features.

**Columns.** *Exercises* are from the [curriculum](curriculum.md). *Best local* is the strongest
class an exercise can reach on this PC: **LV** LOCAL VERIFIED, **SIM** SIMULATED, **KO**
KNOWLEDGE ONLY. *Fabric* says whether the competency stays incomplete until a FABRIC VERIFIED
exercise passes. *Lessons* are existing Learn modules to reuse. Every objective also gets at least
four original exam questions (ACD-013).

## 1. Implement and manage an analytics solution (30–35%)

| Id | Objective | Exercises | Best local | Fabric | Lessons |
| --- | --- | --- | --- | --- | --- |
| 1.1.1 | Configure Spark workspace settings | E01, E02 | SIM | Yes | |
| 1.1.2 | Configure domain workspace settings | E03 | SIM | Yes | |
| 1.1.3 | Configure OneLake workspace settings | E04 | SIM | Yes | LM-07 |
| 1.1.4 | Configure data workflow workspace settings | E05 | KO | Yes | |
| 1.2.1 | Configure version control | E06, E07, C3 | LV | Yes | |
| 1.2.2 | Implement database projects | E08, C3 | LV | Yes | LM-23 |
| 1.2.3 | Create and configure deployment pipelines | E09, C3 | SIM | Yes | |
| 1.3.1 | Implement workspace-level access controls | E10, C3 | SIM | Yes | |
| 1.3.2 | Implement item-level access controls | E11, C3 | SIM | Yes | LM-24 |
| 1.3.3 | Implement row-level, column-level, object-level, and file-level access controls | E12, E13, E14, C3 | LV (row filter logic only) | Yes | LM-24 |
| 1.3.4 | Implement dynamic data masking | E15, C3 | SIM | Yes | |
| 1.3.5 | Apply sensitivity labels to items | E16 | KO | Yes | |
| 1.3.6 | Endorse items | E16 | KO | Yes | |
| 1.4.1 | Choose between a pipeline and a notebook | E17 | KO | No | LM-08 |
| 1.4.2 | Design and implement schedules and event-based triggers | E18 | SIM | Yes | |
| 1.4.3 | Implement orchestration patterns with notebooks and pipelines, including parameters and dynamic expressions | E19, E20, C1 | LV | No | |

## 2. Ingest and transform data (30–35%)

| Id | Objective | Exercises | Best local | Fabric | Lessons |
| --- | --- | --- | --- | --- | --- |
| 2.1.1 | Design and implement full and incremental data loads | E21, E22, E57, C1 | LV | No | LM-12, LM-21 |
| 2.1.2 | Prepare data for loading into a dimensional model | E23, E24, E25, C1 | LV | No | LAB-03, LAB-05 |
| 2.1.3 | Design and implement a loading pattern for streaming data | E26, C2 | LV | Yes | |
| 2.2.1 | Choose an appropriate data store | E27 | KO | No | LM-06, LM-23 |
| 2.2.2 | Choose between dataflows, notebooks, and T-SQL for data transformation | E17, E28 | KO | No | |
| 2.2.3 | Create and manage shortcuts to data | E29 | SIM | Yes | LM-24 |
| 2.2.4 | Implement mirroring | E30 | KO | Yes | |
| 2.2.5 | Ingest data by using pipelines | E31, C1 | SIM | Yes | |
| 2.2.6 | Transform data by using PySpark, SQL, and KQL | E32, E33, E34, C1 | LV (PySpark API, SQL); KO (KQL) | Yes (KQL) | LM-08, LM-19 |
| 2.2.7 | Denormalize data | E35, C1 | LV | No | LAB-07 |
| 2.2.8 | Group and aggregate data | E36, C1 | LV | No | LM-04 |
| 2.2.9 | Handle duplicate, missing, and late-arriving data | E25, E37, E38, E39, E57, E58, C1 | LV | No | LM-13, LM-17, LAB-04, LAB-06 |
| 2.3.1 | Choose an appropriate streaming engine | E40, C2 | KO | No | |
| 2.3.2 | Process data by using eventstreams | E41, C2 | SIM | Yes | |
| 2.3.3 | Process data by using Spark structured streaming | E26, E42, C2 | LV (availableNow only) | Yes | |
| 2.3.4 | Process data by using KQL | E34, C2 | KO | Yes | |
| 2.3.5 | Create windowing functions | E43, C2 | LV (SQL windows); KO (KQL, Eventstream) | Yes | LM-15 |

## 3. Monitor and optimize an analytics solution (30–35%)

| Id | Objective | Exercises | Best local | Fabric | Lessons |
| --- | --- | --- | --- | --- | --- |
| 3.1.1 | Monitor data ingestion | E44, C3 | SIM | Yes | |
| 3.1.2 | Monitor data transformation | E44, E47 | SIM | Yes | |
| 3.1.3 | Monitor semantic model refresh | E45 | SIM | Yes | LM-09 |
| 3.1.4 | Configure alerts | E46, C2 | SIM | Yes | |
| 3.2.1 | Identify and resolve pipeline errors | E48, E49, E58, C1 | LV (local runner) | No | |
| 3.2.2 | Identify and resolve dataflow errors | E50 | SIM | Yes | |
| 3.2.3 | Identify and resolve notebook errors | E51, E52 | LV | No | |
| 3.2.4 | Identify and resolve eventhouse errors | E53 | SIM | Yes | |
| 3.2.5 | Identify and resolve eventstream errors | E54, C2 | SIM | Yes | |
| 3.2.6 | Identify and resolve T-SQL errors | E55, C3 | SIM | Yes | LAB-09 |
| 3.3.1 | Optimize a lakehouse table | E56, C1 | LV (OPTIMIZE, Z-order, VACUUM); KO (V-Order) | Yes | LM-25 |
| 3.3.2 | Optimize a pipeline | E60 | SIM | Yes | |
| 3.3.3 | Optimize a data warehouse | E61, C3 | KO | Yes | |
| 3.3.4 | Optimize eventstreams and eventhouses | E62 | KO | Yes | |
| 3.3.5 | Optimize Spark performance | E52, E63 | LV (file layout and pruning on Delta); KO (Spark settings) | Yes | LM-27 |
| 3.3.6 | Optimize query performance | E59, E64, C1 | LV | No | LM-18, LAB-02 |

## Totals

| | Objectives |
| --- | --- |
| Covered by at least one exercise | 49 of 49 |
| Best local class LOCAL VERIFIED (all or part) | 18 |
| Best local class SIMULATED | 20 |
| KNOWLEDGE ONLY on this PC | 11 |
| Stay incomplete until FABRIC VERIFIED | 36 |

## Coverage gap records

No objective is left out. Every objective that stays incomplete until FABRIC VERIFIED has at least one
gap record, and only those do. These objectives are covered, but not to the standard of practice the directive
asks for until the gap closes. Each one is a record in the registry (`gaps[]`), shown to learners
on the objective.

| Gap | Objectives | Why | Closes with |
| --- | --- | --- | --- |
| G-01 No local KQL engine | 2.2.6 (KQL), 2.3.4, 2.3.5 (KQL), 3.2.4, 3.3.4 | Nothing in the repository runs KQL | Fabric Eventhouse exercises (ACD-014), or embedding Microsoft's Kusto emulator if Chris approves installing it (ACD-016) |
| G-02 No Eventstream runtime | 2.3.2, 3.2.5, 3.3.4 | Eventstream is a Fabric service | Topology checks locally (SIMULATED), real runs in Fabric (ACD-014) |
| G-03 T-SQL never runs here | 1.2.2 (deploy), 1.3.3 (security policy), 1.3.4, 3.2.6, 3.3.3 | DuckDB is not the Warehouse engine (`sql_dialects.py`) | Fabric Warehouse exercises (ACD-014) |
| G-04 Workspace, tenant and domain settings, roles and endorsement | 1.1.1–1.1.4, 1.3.1, 1.3.2, 1.3.6 | Settings and permissions live in Fabric; some need admin roles | SIMULATED settings models locally, read-only checks in the learner's own workspace |
| G-05 Sensitivity labels | 1.3.5 | Needs sensitivity labels published in the tenant | FABRIC VERIFIED only where the learner's tenant has labels; otherwise KO |
| G-06 Mirroring | 2.2.4 | Needs a source database and capacity | KO locally; Fabric exercise with a free-tier source is a cost decision for Chris |
| G-07 Data workflows (Apache Airflow jobs) | 1.1.4 | No local Airflow job runtime in KratosForge | KO; Fabric exercise later |
| G-08 Spark engine and continuous streaming | 1.1.1, 2.1.3, 2.3.3, 3.3.5 | The local PySpark API is DuckDB: no executors, shuffles or AQE; streams run only as availableNow here | File layout lessons locally; Spark tuning verified in Fabric |
| G-09 Monitoring hub, alerts, refresh | 3.1.1–3.1.4 | Service telemetry | Recorded run histories (SIMULATED), live read-only checks later |
| G-10 Item behavior only the service shows | 1.2.1, 1.2.3, 1.4.2, 2.2.3, 2.2.5, 3.3.1, 3.3.2 | Workspace Git sync, deployment, triggers, shortcuts, copy throughput and V-Order happen in Fabric; locally the learner works with the files and the logic | Fabric exercises (ACD-014) |
| G-11 No local Power Query (M) engine | 3.2.2 | `orchestration/mashup.py` reads dataflow definitions but nothing evaluates M | SIMULATED fault diagnosis locally; Fabric exercise (ACD-014) |
