# Platform experience parity

How close KratosForge comes to each platform, area by area, at five levels that are judged
separately. A familiar screen is not workflow parity, and a run that works on this PC is not proof of
the same answers in the cloud. The tables below are generated from `fabric_desktop/parity.py`, and a
test keeps them equal to the code, so this page can't claim more than the repository shows.

## Levels

| Level | Means |
| --- | --- |
| Visual familiarity | The app looks familiar to that platform's developers: a [skin](skins.md) targets it. |
| Workflow compatibility | Navigation and steps follow that platform's: a skin arranges the pages that way. |
| Cloud API integration | KratosForge talks to the platform's own service for this area. |
| Local execution | The area's work runs on this PC. The note says whether natively, with documented differences, or as validation only. |
| Verified runtime equivalence | Results on this PC were compared with the platform's own, in a recorded run. |

| Status | Means |
| --- | --- |
| · | Not assessed yet. It counts neither as missing nor as done. |
| Missing | Checked: KratosForge doesn't do it. |
| Partial | Does some of it. The note says what's missing or different. |
| Built | Does it, with tests in this repository. Cloud tests run against fakes of the platform's API. |
| Verified | Built, and a recorded run against the real platform backs it ([Live runs](platforms.md#live-runs)). |

Every status above Missing names the test files that back it, and the test checks they exist.
Visual and workflow stay Missing until a built-in skin targets the platform. Equivalence is only ever
Partial or Verified, from a recorded comparison. For integration depth per platform (sign-in to
enterprise ready), see [Platform support levels](platforms.md); this page doesn't repeat it.

## Where each platform stands

<!-- BEGIN GENERATED: parity -->
### Microsoft Fabric

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in and connections | Partial | Missing | Verified | · | · |
| Workspaces and items | Partial | Partial | Verified | · | · |
| Lakehouse tables and files | Partial | Partial | Built | Built | · |
| Warehouse and SQL analytics endpoint | Partial | Missing | Built | Partial | · |
| Notebooks and Spark | Partial | Missing | Verified | Partial | Partial |
| Data Factory pipelines | Partial | Missing | · | Partial | · |
| Dataflows Gen2 | Partial | Missing | · | · | · |
| Medallion task flows | Partial | Missing | · | · | · |
| Semantic models, TMDL and Direct Lake | Partial | Missing | · | Partial | · |
| Git integration and deployment | Partial | Missing | Built | · | · |
| Monitoring and capacity | Partial | Partial | Built | · | · |
| Security and governance | Partial | Missing | · | · | · |

Notes:

- **Every area, Visual familiarity:** The Fabric-familiar skin (Preview) gives a teal accent and Fabric's page order on the rail (Home, Create, Workspaces, OneLake and Monitor first); pages keep KratosForge's own layout.
- **Sign-in and connections, Cloud API integration:** Signing in with the developer's own identity was recorded. No app registration or service principal is used. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Workspaces and items, Workflow compatibility:** The skin's rail has Workspaces third, after Home and Create, as in Fabric; inside it, the page is KratosForge's own.
- **Workspaces and items, Cloud API integration:** Listing workspaces and lakehouses was recorded; other item types were not. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Lakehouse tables and files, Workflow compatibility:** The skin's rail has OneLake fourth, after Workspaces, as in Fabric; inside it, the page is KratosForge's own.
- **Lakehouse tables and files, Cloud API integration:** Tables weren't listed in the recorded live run.
- **Lakehouse tables and files, Local execution:** Notebooks on this PC read and write OneLake; used live, not recorded.
- **Warehouse and SQL analytics endpoint, Local execution:** T-SQL is translated to DuckDB; documented gaps fail loudly (docs/sql-compatibility.md).
- **Notebooks and Spark, Cloud API integration:** Spark SQL in one Livy session. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Notebooks and Spark, Local execution:** PySpark and Spark SQL are simulated on DuckDB; known differences are listed in docs/audits/ENGINE_COMPATIBILITY.md.
- **Notebooks and Spark, Verified runtime equivalence:** 10 Spark SQL queries: 9 match in the 2026-11-03 re-run. Decimal division differed (Spark keeps 6 places) because that comparison runs the translated SQL on DuckDB directly. spark.sql on this PC now gives Spark's decimal types (docs/sql-compatibility.md), but the comparison hasn't been re-run live since. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Data Factory pipelines, Local execution:** Pipelines run on this PC; expected outcomes come from documentation, not a recorded tenant run.
- **Semantic models, TMDL and Direct Lake, Local execution:** TMDL is read offline and the Direct Lake check runs on this PC; models aren't served here.
- **Git integration and deployment, Cloud API integration:** Publishing hasn't been recorded live.
- **Monitoring and capacity, Workflow compatibility:** The skin's rail has Monitor fifth, after OneLake, as in Fabric; inside it, the page is KratosForge's own.

### Azure Databricks

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in and workspace discovery | Partial | Missing | Built | · | · |
| SQL warehouses and the SQL editor | Partial | Missing | Built | Partial | · |
| Notebooks, Spark and dbutils | Partial | Missing | Built | Partial | · |
| Unity Catalog | Partial | Missing | · | Partial | · |
| Jobs and Lakeflow pipelines | Partial | Partial | Partial | · | · |
| Compute | Partial | Missing | · | · | · |
| Asset bundles and deployment | Partial | Missing | Partial | · | · |
| Cost and DBU | Partial | Missing | Partial | · | · |

Notes:

- **Every area, Visual familiarity:** The Databricks-familiar skin (Preview) gives a blue accent and Databricks' page names on the rail; pages keep KratosForge's own layout.
- **Sign-in and workspace discovery, Cloud API integration:** Against fakes of the CLI and APIs; no live run yet.
- **SQL warehouses and the SQL editor, Cloud API integration:** Against fakes of the SQL Statement API; no live run yet.
- **SQL warehouses and the SQL editor, Local execution:** Databricks SQL is translated to DuckDB; docs/databricks-functions.md says for every built-in whether it runs, differs, is refused or fails, checked in CI.
- **Notebooks, Spark and dbutils, Cloud API integration:** Databricks Connect (runtime 16 and later), behind an authorization, and a notebook folder synced with a workspace folder (Preview); against fakes, no live run yet.
- **Notebooks, Spark and dbutils, Local execution:** dbutils and PySpark are simulated on this PC; Databricks Runtime equivalence isn't claimed. Each dbutils call and % magic: docs/databricks-notebook-helpers.md.
- **Unity Catalog, Local execution:** No Unity Catalog on this PC: a notebook's tables are the attached lakehouse's. catalog.schema.table, USE CATALOG, USE SCHEMA and backticked names read or write the table they name there, or are refused by name (another catalog or schema); none resolves to another table. spark.catalog's calls answer about the lakehouse and temp views, or are refused by name.
- **Jobs and Lakeflow pipelines, Workflow compatibility:** The skin names Monitor "Jobs and pipelines" and puts it where Databricks does; inside it, runs are read, not created or started.
- **Jobs and Lakeflow pipelines, Cloud API integration:** Monitor > Databricks (Preview) reads job runs and Lakeflow updates with their tasks and errors; jobs aren't created or started here. Against fakes, no live run yet.
- **Asset bundles and deployment, Cloud API integration:** Source control > Bundles (Preview) validates, plans and deploys through the Databricks CLI; bundle run and destroy aren't offered. Against a fake CLI, no live run yet.
- **Cost and DBU, Cloud API integration:** Optimize > Cost (Preview) reads DBUs from the billing system tables after a confirm; no budgets or alerts. Against fakes, no live run yet.

### Snowflake

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in, roles and warehouses | Partial | Missing | Partial | · | · |
| SQL worksheets | Partial | Missing | Partial | Partial | · |
| Databases, schemas and object browser | Partial | Missing | · | · | · |
| Snowpark | Partial | Missing | · | Missing | · |
| Stages and data loading | Partial | Missing | · | · | · |
| Streams, tasks and dynamic tables | Partial | Missing | · | Partial | · |
| Query history, profiling and credits | Partial | Missing | · | · | · |

Notes:

- **Every area, Visual familiarity:** The Snowflake-familiar skin (Preview) gives a blue accent and Snowsight's page names on the rail; pages keep KratosForge's own layout.
- **Sign-in, roles and warehouses, Cloud API integration:** Key-pair and programmatic access token sign-in, and a connection check of role, database, schema and warehouse through the SQL API (Preview), without resuming a warehouse. Browser sign-in and Snowflake CLI connections aren't supported yet. Against fakes, no live run yet.
- **SQL worksheets, Cloud API integration:** The Snowflake SQL warehouse engine (Preview) runs read-only statements (SELECT, WITH, SHOW, DESCRIBE, EXPLAIN) through the SQL API after a confirm, and refuses anything else before it is sent. Not in the notebook's engine picker yet. Against fakes, no live run yet.
- **SQL worksheets, Local execution:** Validation and translation only: Snowflake SQL runs on DuckDB with a label per statement. Not a Snowflake runtime. docs/snowflake-functions.md says for every function whether it runs, differs, is refused or fails.
- **Streams, tasks and dynamic tables, Local execution:** Validation only: CREATE TASK, CREATE STREAM and CREATE DYNAMIC TABLE statements are checked on this PC against Snowflake's documented syntax and rules, with task graphs across statements (Preview, no screen yet). Nothing runs or refreshes here, and nothing is deployed to Snowflake.
<!-- END GENERATED: parity -->

Areas marked · are the parity backlog: each gets assessed against the platform's current
documentation before it is built, and a new status lands with its tests.

## What's next, and who owns it

The three-platform parity program ([assessment](product/platform-parity-assessment.md),
[backlog](product/platform-parity-backlog.md)) gives each area a feature family, a priority, the
backlog items that move it and the workstream that owns it. **Runs** says where the area's work runs
in KratosForge today, never more than the cells above show. A plan says what's next, not what's done,
and it never raises a status. This table is generated from `PLANS` in the same module.

<!-- BEGIN GENERATED: parity-plan -->
| Platform | Area | Family | Priority | Runs | Next items | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| Microsoft Fabric | Sign-in and connections | A Authentication and connectivity | P0 | Cloud only | PAR-003 | Platform parity |
| Microsoft Fabric | Workspaces and items | D Data storage and catalog | P1 | Cloud only | FAB-001 | Platform parity |
| Microsoft Fabric | Lakehouse tables and files | D Data storage and catalog | P1 | Local and cloud | FAB-001 | Local engine |
| Microsoft Fabric | Warehouse and SQL analytics endpoint | B SQL development | P1 | Local and cloud | FAB-003 | Warehouse |
| Microsoft Fabric | Notebooks and Spark | C Notebooks and distributed compute | P0 | Local and cloud | PAR-002, FAB-001 | Platform parity |
| Microsoft Fabric | Data Factory pipelines | E Orchestration and transformation | P1 | Simulated or validated on this PC | FAB-002 | unassigned |
| Microsoft Fabric | Dataflows Gen2 | E Orchestration and transformation | P2 | Not built yet | FAB-002 | unassigned |
| Microsoft Fabric | Medallion task flows | E Orchestration and transformation | P3 | Not built yet | · | unassigned |
| Microsoft Fabric | Semantic models, TMDL and Direct Lake | H Semantic engineering | P2 | Local only | FAB-004 | Semantic engineering (OSSIE) |
| Microsoft Fabric | Git integration and deployment | F Source control and deployment | P1 | Cloud only | FAB-007 | unassigned |
| Microsoft Fabric | Monitoring and capacity | G Monitoring, cost and optimization | P1 | Cloud only | FAB-006 | Optimization engine |
| Microsoft Fabric | Security and governance | I Enterprise shared services | P2 | Not built yet | FAB-005 | Governance |
| Azure Databricks | Sign-in and workspace discovery | A Authentication and connectivity | P0 | Cloud only | DBX-001, PAR-003 | Databricks and Snowflake parity |
| Azure Databricks | SQL warehouses and the SQL editor | B SQL development | P0 | Local and cloud | DBX-001, PAR-002 | Databricks and Snowflake parity |
| Azure Databricks | Notebooks, Spark and dbutils | C Notebooks and distributed compute | P1 | Local and cloud | DBX-001 | Databricks and Snowflake parity |
| Azure Databricks | Unity Catalog | D Data storage and catalog | P1 | Simulated or validated on this PC | DBX-002 | Platform parity |
| Azure Databricks | Jobs and Lakeflow pipelines | E Orchestration and transformation | P1 | Cloud only | DBX-003, DBX-004 | Platform parity |
| Azure Databricks | Compute | C Notebooks and distributed compute | P2 | Not built yet | DBX-005 | Platform parity |
| Azure Databricks | Asset bundles and deployment | F Source control and deployment | P1 | Cloud only | DBX-001 | Databricks and Snowflake parity |
| Azure Databricks | Cost and DBU | G Monitoring, cost and optimization | P1 | Cloud only | DBX-006 | Optimization engine |
| Snowflake | Sign-in, roles and warehouses | A Authentication and connectivity | P0 | Cloud only | SF-001, SF-002, PAR-003 | Platform parity |
| Snowflake | SQL worksheets | B SQL development | P0 | Local and cloud | SF-003, PAR-002 | Platform parity |
| Snowflake | Databases, schemas and object browser | D Data storage and catalog | P1 | Not built yet | SF-004 | Platform parity |
| Snowflake | Snowpark | C Notebooks and distributed compute | P2 | Not built yet | SF-007 | unassigned |
| Snowflake | Stages and data loading | D Data storage and catalog | P2 | Not built yet | SF-009 | unassigned |
| Snowflake | Streams, tasks and dynamic tables | E Orchestration and transformation | P2 | Simulated or validated on this PC | SF-008 | unassigned |
| Snowflake | Query history, profiling and credits | G Monitoring, cost and optimization | P1 | Not built yet | SF-005, SF-006 | Platform parity |
<!-- END GENERATED: parity-plan -->
