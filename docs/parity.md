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
- **Workspaces and items, Workflow compatibility:** The skin's rail has Workspaces third, after Home and Create, as in Fabric; inside it, the page is KratosForge's own.
- **Workspaces and items, Cloud API integration:** Listing workspaces and lakehouses was recorded; other item types were not. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Lakehouse tables and files, Workflow compatibility:** The skin's rail has OneLake fourth, after Workspaces, as in Fabric; inside it, the page is KratosForge's own.
- **Lakehouse tables and files, Cloud API integration:** Tables weren't listed in the recorded live run.
- **Lakehouse tables and files, Local execution:** Notebooks on this PC read and write OneLake; used live, not recorded.
- **Warehouse and SQL analytics endpoint, Local execution:** T-SQL is translated to DuckDB; documented gaps fail loudly (docs/sql-compatibility.md).
- **Notebooks and Spark, Cloud API integration:** Spark SQL in one Livy session. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Notebooks and Spark, Local execution:** PySpark and Spark SQL are simulated on DuckDB; known differences are listed in docs/audits/ENGINE_COMPATIBILITY.md.
- **Notebooks and Spark, Verified runtime equivalence:** 10 Spark SQL queries: 9 match in the 2026-11-03 re-run; decimal division still differs (Spark keeps 6 places). Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
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
| Unity Catalog | Partial | Missing | · | · | · |
| Jobs and Lakeflow pipelines | Partial | Partial | Partial | · | · |
| Compute | Partial | Missing | · | · | · |
| Asset bundles and deployment | Partial | Missing | Partial | · | · |
| Cost and DBU | Partial | Missing | Partial | · | · |

Notes:

- **Every area, Visual familiarity:** The Databricks-familiar skin (Preview) gives a blue accent and Databricks' page names on the rail; pages keep KratosForge's own layout.
- **Sign-in and workspace discovery, Cloud API integration:** Against fakes of the CLI and APIs; no live run yet.
- **SQL warehouses and the SQL editor, Cloud API integration:** Against fakes of the SQL Statement API; no live run yet.
- **SQL warehouses and the SQL editor, Local execution:** Databricks SQL is translated to DuckDB; functions that differ are listed in docs/engines.md.
- **Notebooks, Spark and dbutils, Cloud API integration:** Databricks Connect (runtime 16 and later), behind an authorization, and a notebook folder synced with a workspace folder (Preview); against fakes, no live run yet.
- **Notebooks, Spark and dbutils, Local execution:** dbutils and PySpark are simulated on this PC; Databricks Runtime equivalence isn't claimed. Each dbutils call and % magic: docs/databricks-notebook-helpers.md.
- **Jobs and Lakeflow pipelines, Workflow compatibility:** The skin names Monitor "Jobs and pipelines" and puts it where Databricks does; inside it, runs are read, not created or started.
- **Jobs and Lakeflow pipelines, Cloud API integration:** Monitor > Databricks (Preview) reads job runs and Lakeflow updates with their tasks and errors; jobs aren't created or started here. Against fakes, no live run yet.
- **Asset bundles and deployment, Cloud API integration:** Source control > Bundles (Preview) validates, plans and deploys through the Databricks CLI; bundle run and destroy aren't offered. Against a fake CLI, no live run yet.
- **Cost and DBU, Cloud API integration:** Optimize > Cost (Preview) reads DBUs from the billing system tables after a confirm; no budgets or alerts. Against fakes, no live run yet.

### Snowflake

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in, roles and warehouses | Partial | Missing | Missing | · | · |
| SQL worksheets | Partial | Missing | Missing | Partial | · |
| Databases, schemas and object browser | Partial | Missing | · | · | · |
| Snowpark | Partial | Missing | · | Missing | · |
| Stages and data loading | Partial | Missing | · | · | · |
| Streams, tasks and dynamic tables | Partial | Missing | · | · | · |
| Query history, profiling and credits | Partial | Missing | · | · | · |

Notes:

- **Every area, Visual familiarity:** The Snowflake-familiar skin (Preview) gives a blue accent and Snowsight's page names on the rail; pages keep KratosForge's own layout.
- **SQL worksheets, Local execution:** Validation and translation only: Snowflake SQL runs on DuckDB with a label per statement. Not a Snowflake runtime.
<!-- END GENERATED: parity -->

Areas marked · are the parity backlog: each gets assessed against the platform's current
documentation before it is built, and a new status lands with its tests.
