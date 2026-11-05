# Where each capability runs

KratosForge keeps four choices apart: how the app looks (its skin), how its pages are arranged, which
platform you work with, and which engine runs your code. This page is about the last one. For each thing a
developer does, it lists every engine that can do it and how, in one of five classifications:

| Classification | Meaning |
| --- | --- |
| Native local execution | Runs on this PC the way the platform runs it. |
| Compatible local execution, with documented differences | Runs on this PC, rewritten for another engine or partly supported. The differences are listed below the table. |
| Local simulation or validation | A stand-in runs on this PC. Good for writing and checking code, not a runtime of the platform. |
| Remote cloud execution | Runs in the platform's cloud and is billed there. KratosForge always asks before it starts one. |
| Unsupported | Not available on that engine. Unsupported pairs are left out of the table. |

The skin you pick never changes any of this: the Fabric-familiar skin can run SQL on DuckDB on this PC,
and the KratosForge skin can run a cell on Fabric Spark. "Where" is the line every run control shows, so
you always know where code will run and what pays for it.

Nothing here is claimed by this page itself. Each row comes from the engine's own description
(`sidecar/fabric_desktop/engines/capabilities.py`), which refuses a claim without tests behind it. Engines
that aren't built yet (Spark on this PC, Snowflake SQL and Snowpark) don't appear. Remote engines are
Preview until a live run is recorded ([Platforms](platforms.md)). How close each platform's experience is,
level by level, is in [Platform experience parity](parity.md).

<!-- BEGIN GENERATED: capabilities -->
| Capability | Engine | How it runs | Where |
| --- | --- | --- | --- |
| Run SQL on this PC | DuckDB (this PC) | Native local execution | This PC · no cloud cost |
| Run Spark SQL | DuckDB (this PC) | Compatible local execution, with documented differences | This PC · no cloud cost |
| Run Spark SQL | Fabric Spark (remote) | Remote cloud execution (Preview) | Fabric · Fabric capacity |
| Run Spark SQL | Databricks SQL warehouse (remote) | Remote cloud execution (Preview) | Databricks · Databricks DBUs |
| Run Databricks SQL | DuckDB (this PC) | Compatible local execution, with documented differences | This PC · no cloud cost |
| Run Databricks SQL | Databricks SQL warehouse (remote) | Remote cloud execution (Preview) | Databricks · Databricks DBUs |
| Run Snowflake SQL | DuckDB (this PC) | Compatible local execution, with documented differences | This PC · no cloud cost |
| Run a Python cell | Python (this PC) | Native local execution | This PC · no cloud cost |
| Run a Python cell | Fabric Spark (remote) | Remote cloud execution (Preview) | Fabric · Fabric capacity |
| Run a Python cell | Databricks Connect (remote) | Remote cloud execution (Preview) | Databricks · Databricks DBUs |
| Run a PySpark cell | Python (this PC) | Local simulation or validation | This PC · no cloud cost |
| Run a PySpark cell | Fabric Spark (remote) | Remote cloud execution (Preview) | Fabric · Fabric capacity |
| Run a PySpark cell | Databricks Connect (remote) | Remote cloud execution (Preview) | Databricks · Databricks DBUs |
| Run a Scala cell | Fabric Spark (remote) | Remote cloud execution (Preview) | Fabric · Fabric capacity |

Known differences:

- **Run Spark SQL, DuckDB (this PC):** Spark SQL is rewritten for DuckDB by sqlglot. Most queries match; Spark-only functions and Delta commands (OPTIMIZE, VACUUM) don't.
- **Run Databricks SQL, DuckDB (this PC):** Databricks SQL is rewritten for DuckDB by sqlglot, so warehouse queries can be written and tried on this PC without starting a warehouse. Most queries match; Unity Catalog three-part names need the catalog and schema in the session, and Delta-only commands don't run.
- **Run Snowflake SQL, DuckDB (this PC):** Snowflake SQL is rewritten for DuckDB by sqlglot with KratosForge's rules; each statement is labelled translated, runs with a known difference, or runs only in Snowflake, and a cell runs only when every statement can. Results are not checked against a Snowflake account.
- **Run a Python cell, Databricks Connect (remote):** Plain Python runs on this PC, in Databricks Connect's own environment; only spark work goes to the cluster.
- **Run a PySpark cell, Python (this PC):** spark is DuckDB's PySpark-compatible API on this PC, not Apache Spark. Common DataFrame and spark.sql code runs; RDDs, UDF registration and Spark configuration don't.
<!-- END GENERATED: capabilities -->
