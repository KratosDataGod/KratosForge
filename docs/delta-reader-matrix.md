# Delta reader matrix

Which readers can read a Delta table that uses a feature outside KratosForge's Fabric-safe Delta profile (`sidecar/fabric_desktop/delta_profile.py`). The profile allows reader version 1 and writer version 2 at most, with no deletion vectors, no column mapping and no V2 checkpoints. KratosForge refuses to write to a table outside it.

Every cell comes from a public page, linked below. "Unknown" means no page we found says either way. It is not a guess.

Sources were read on 2026-11-16. Microsoft's Delta Lake interoperability page was last updated on 2026-10-07.

## The matrix

| Feature | Fabric SQL analytics endpoint | Power BI Direct Lake | Fabric Spark | DuckDB (delta extension) | Databricks | Snowflake (Iceberg tables from Delta files) |
| --- | --- | --- | --- | --- | --- | --- |
| Deletion vectors | Reads [1] | Reads [1] | Runtimes 1.2, 1.3 and 2.0 read them; 1.1 doesn't [1] | Reads [5] | Reads [7] | Reads, since 2026-03-02 [10] |
| Column mapping by name | Reads (in preview) [1][2] | Reads [1] | Reads [1] | Reads [5] | Reads [7] | Unknown |
| Column mapping by ID | Doesn't read [1][2] | Reads [1] | Reads [1] | Reads [5] | Reads [7] | Unknown |
| V2 checkpoints | Unknown in Microsoft's docs; users report it refused [3][4] | Unknown | Unknown | Unknown | Reads, Databricks Runtime 13.3 LTS and above [8] | Unknown |
| Reader version above 1 | Depends on the feature (rows above) | Depends on the feature | Depends on the feature | Depends on the feature | Table features from Databricks Runtime 12.2 LTS [7] | Reads minReaderVersion 3 [9] |
| Writer version above 2 | Unknown | Unknown | Unknown | Unknown | Depends on the feature; each has a minimum runtime [7] | Unknown |

## Other Fabric readers

Microsoft's page [1] lists more Fabric items than the six above. These ones can't read some of the profile's features:

| Fabric item | Deletion vectors | Column mapping by name | Column mapping by ID |
| --- | --- | --- | --- |
| Python notebooks | Doesn't read | Doesn't read | Doesn't read |
| Pipelines | Doesn't read | Doesn't read | Doesn't read |
| Eventstreams | Doesn't read | Doesn't read | Doesn't read |
| Lakehouse explorer and preview | Reads | Reads | Doesn't read |
| Dataflows Gen2 | Reads | Reads | Doesn't read |
| KQL databases | Reads | Reads | Doesn't read |

## What this means for the profile

The profile is stricter than Microsoft's page requires for the SQL analytics endpoint and Direct Lake. Both read deletion vectors and column mapping by name [1]. Other Fabric items can't read them: Python notebooks, pipelines and eventstreams. Column mapping by ID also breaks the SQL analytics endpoint. The profile stays as it is. Allowing a feature per table is separate work, and it waits for the product decision.

When KratosForge refuses a write, the message names the feature and the readers on this page that can't read it.

## Reading these tables in KratosForge

KratosForge reads tables with deletion vectors or column mapping correctly, whoever wrote them. Fabric Spark writes both: deleted rows stay in the data files and a deletion vector marks them, and with column mapping the data files name each column `col-<id>`. Fabric turns column mapping on for a column name with a space.

- The pyarrow reader in delta-rs refuses deletion vectors. On a column-mapped table that another writer made, it reads every value as NULL. KratosForge reads these tables through delta-rs's DataFusion reader instead (`sidecar/fabric_desktop/delta_read.py`). This covers previews, notebooks, Spark on this PC, SQL over the lakehouse, pipeline Lookup and Get Metadata, proofs and environment compare. A table with neither feature is read as before. The DataFusion reader reads such a table whole into memory before a query or a notebook works on it, even for a `SELECT ... LIMIT 10`. So a table whose data files hold more than 2 GB is refused, and the refusal gives the reason. Previews and pipeline Lookup read only the first rows, so they have no limit. A local data project reads the table in place, without the limit.
- A local data project reads such a table through its own view. The view reads each column by its physical name and gives it the table's name. It leaves out each deleted row by its row number in its file. Two limits apply. A struct column under column mapping is refused, because a local read names only top-level columns. A table with more than 50,000 runs of deleted rows is also refused, and compacting it in Fabric (OPTIMIZE) folds the vectors in.
- A stream on this PC refuses both features. It reads new files by path, so it would bring deleted rows back or miss renamed columns. Run the stream in Fabric.
- Row counts from the log's file statistics (previews, environment compare) leave out rows that a deletion vector removed.

Reading such a table doesn't make it writable. Writes still follow the profile above.

The tests use tables that Apache Spark 3.5.5 with Delta Lake 3.3.2 wrote (`sidecar/tests/fixtures/delta_spark`, made by `sidecar/tools/make_spark_delta_fixtures.py`). Each test checks the rows Spark read back.

## Notes on the sources

- **V2 checkpoints and Fabric.** Microsoft's docs don't mention them. A user reports the SQL analytics endpoint error "The Delta table uses the 'v2Checkpoint' feature, which is not supported" [3]. An idea on the Fabric forum asks for support because the SQL endpoint fails on it [4]. Both are user reports, not documentation.
- **DuckDB.** The delta extension is built on Delta Kernel [5][6]. Its one column mapping limit (no nested or partition columns) is about writing such tables, not reading them [5]. Its documentation doesn't mention V2 checkpoints.
- **Snowflake.** These cells are for Iceberg tables created from Delta files. Snowflake's Delta external tables (`TABLE_FORMAT = DELTA`) are to be deprecated, and their page says nothing about these features [11]. Snowflake lists row tracking and change data files as unsupported [9].
- **Writer version.** The Delta protocol says a writer-only upgrade doesn't affect read-only applications unless the reader version also changes [12]. That is the protocol's rule, not a statement about any one product, so those cells stay unknown.
- **Fabric Spark runtimes.** Runtime 2.0 is the generally available default, and Runtime 1.3 is marked end of support announced [13].

## Sources

1. Microsoft, [Delta Lake table format interoperability](https://learn.microsoft.com/fabric/fundamentals/delta-lake-interoperability)
2. Microsoft, [What is the SQL analytics endpoint for a lakehouse?](https://learn.microsoft.com/fabric/data-engineering/lakehouse-sql-analytics-endpoint), Limitations: "Delta column mapping by name is supported, but Delta column mapping by ID is not supported."
3. Fabric community, [The Delta table uses the 'v2Checkpoint' feature, which is not supported](https://community.fabric.microsoft.com/t5/Data-Engineering/The-Delta-table-uses-the-v2Checkpoint-feature-which-is-not/m-p/4857595)
4. Fabric ideas, [Support v2 checkpoint policy for delta table shortcuts](https://community.fabric.microsoft.com/t5/Fabric-Ideas/Support-v2-checkpoint-policy-for-delta-table-shortcuts/idi-p/4886674)
5. DuckDB, [Delta extension](https://duckdb.org/docs/stable/core_extensions/delta)
6. DuckDB, [duckdb-delta on GitHub](https://github.com/duckdb/duckdb-delta)
7. Databricks, [Delta Lake feature compatibility and protocols](https://docs.databricks.com/aws/en/delta/feature-compatibility)
8. Databricks, [V2 checkpoints](https://docs.databricks.com/aws/en/tables/features/checkpoint-v2)
9. Snowflake, [CREATE ICEBERG TABLE (Delta files in object storage)](https://docs.snowflake.com/en/sql-reference/sql/create-iceberg-table-delta)
10. Snowflake, [Release note, Mar 02, 2026: Query Delta-based Apache Iceberg tables with deletion vectors](https://docs.snowflake.com/en/release-notes/2026/other/2026-03-02-iceberg-delta-deletion-vectors)
11. Snowflake, [CREATE EXTERNAL TABLE](https://docs.snowflake.com/en/sql-reference/sql/create-external-table)
12. Delta Lake, [How does Delta Lake manage feature compatibility?](https://docs.delta.io/versioning/)
13. Microsoft, [Apache Spark runtimes in Fabric](https://learn.microsoft.com/fabric/data-engineering/runtime)
