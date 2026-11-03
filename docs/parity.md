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
| Workspaces and items | Missing | Missing | Verified | · | · |
| Lakehouse tables and files | Missing | Missing | Built | Built | · |
| Warehouse and SQL analytics endpoint | Missing | Missing | Built | Partial | · |
| Notebooks and Spark | Missing | Missing | Verified | Partial | Partial |
| Data Factory pipelines | Missing | Missing | · | Partial | · |
| Dataflows Gen2 | Missing | Missing | · | · | · |
| Medallion task flows | Missing | Missing | · | · | · |
| Semantic models, TMDL and Direct Lake | Missing | Missing | · | Partial | · |
| Git integration and deployment | Missing | Missing | Built | · | · |
| Monitoring and capacity | Missing | Missing | Built | · | · |
| Security and governance | Missing | Missing | · | · | · |

Notes:

- **Workspaces and items, Cloud API integration:** Listing workspaces and lakehouses was recorded; other item types were not. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Lakehouse tables and files, Cloud API integration:** Tables weren't listed in the recorded live run.
- **Lakehouse tables and files, Local execution:** Notebooks on this PC read and write OneLake; used live, not recorded.
- **Warehouse and SQL analytics endpoint, Local execution:** T-SQL is translated to DuckDB; documented gaps fail loudly (docs/sql-compatibility.md).
- **Notebooks and Spark, Cloud API integration:** Spark SQL in one Livy session. Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Notebooks and Spark, Local execution:** PySpark and Spark SQL are simulated on DuckDB; known differences are listed in docs/audits/ENGINE_COMPATIBILITY.md.
- **Notebooks and Spark, Verified runtime equivalence:** 10 Spark SQL queries: 9 match in the 2026-11-03 re-run; decimal division still differs (Spark keeps 6 places). Live: KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs).
- **Data Factory pipelines, Local execution:** Pipelines run on this PC; expected outcomes come from documentation, not a recorded tenant run.
- **Semantic models, TMDL and Direct Lake, Local execution:** TMDL is read offline and the Direct Lake check runs on this PC; models aren't served here.
- **Git integration and deployment, Cloud API integration:** Publishing hasn't been recorded live.

### Azure Databricks

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in and workspace discovery | Missing | Missing | Built | · | · |
| SQL warehouses and the SQL editor | Missing | Missing | Built | Partial | · |
| Notebooks, Spark and dbutils | Missing | Missing | Built | Partial | · |
| Unity Catalog | Missing | Missing | · | · | · |
| Jobs and Lakeflow pipelines | Missing | Missing | · | · | · |
| Compute | Missing | Missing | · | · | · |
| Asset bundles and deployment | Missing | Missing | · | · | · |
| Cost and DBU | Missing | Missing | · | · | · |

Notes:

- **Sign-in and workspace discovery, Cloud API integration:** Against fakes of the CLI and APIs; no live run yet.
- **SQL warehouses and the SQL editor, Cloud API integration:** Against fakes of the SQL Statement API; no live run yet.
- **SQL warehouses and the SQL editor, Local execution:** Databricks SQL is translated to DuckDB; functions that differ are listed in docs/engines.md.
- **Notebooks, Spark and dbutils, Cloud API integration:** Databricks Connect (runtime 16 and later), behind an authorization; against fakes, no live run yet.
- **Notebooks, Spark and dbutils, Local execution:** dbutils and PySpark are simulated on this PC; Databricks Runtime equivalence isn't claimed.

### Snowflake

| Area | Visual familiarity | Workflow compatibility | Cloud API integration | Local execution | Verified runtime equivalence |
| --- | --- | --- | --- | --- | --- |
| Sign-in, roles and warehouses | Missing | Missing | Missing | · | · |
| SQL worksheets | Missing | Missing | Missing | Partial | · |
| Databases, schemas and object browser | Missing | Missing | · | · | · |
| Snowpark | Missing | Missing | · | Missing | · |
| Stages and data loading | Missing | Missing | · | · | · |
| Streams, tasks and dynamic tables | Missing | Missing | · | · | · |
| Query history, profiling and credits | Missing | Missing | · | · | · |

Notes:

- **SQL worksheets, Local execution:** Validation and translation only: Snowflake SQL runs on DuckDB with a label per statement. Not a Snowflake runtime.
<!-- END GENERATED: parity -->

Areas marked · are the parity backlog: each gets assessed against the platform's current
documentation before it is built, and a new status lands with its tests.
