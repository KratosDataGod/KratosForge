# Delta reader matrix

Which readers can read a Delta table that uses a newer Delta feature, and what that means for the Delta profiles KratosForge writes to (`sidecar/fabric_desktop/delta_profile.py`). The same cells are in the code as `delta_profile.MATRIX`, and a test checks the two agree.

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

## The profiles

KratosForge writes to a table only when the table is inside the profile and every one of the table's readers can read it. Otherwise the write is refused before anything is committed, and the refusal says why.

| Profile | Allows | Readers that can't read it |
| --- | --- | --- |
| Modern (the default) | Reader version 3 at most, and writer version 2 or 7, with deletion vectors and column mapping by name. Writer versions 3 to 5 turn on features without listing them, so a table at one of them is written only when its settings and schema show none of those features in use other than column mapping by name. That means no check constraint (a `delta.constraints.` setting), no change data feed (`delta.enableChangeDataFeed` true) and no generated column (a `delta.generationExpression` on any column, nested ones included). That lets older column-mapped tables, which Spark and Databricks made as reader 2 and writer 5, be written. The refusal names the feature in use. Writer version 6 (identity columns) is refused | Fabric Python notebooks, pipelines and eventstreams |
| Conservative ("Fabric-safe") | Reader version 1 and writer version 2 at most, no table features | None in the matrix |

Both profiles refuse column mapping by ID, because Fabric's SQL analytics endpoint can't read it. Both also refuse V2 checkpoints, which users report the endpoint refusing, and any other table feature.

- **The default.** The modern profile has been the default since 2026-11-17. It is the setting `delta_profile.DEFAULT_PROFILE`. The SQL analytics endpoint and Direct Lake both read deletion vectors and column mapping by name [1][2], so the conservative profile was stricter than they need.
- **Readers.** A write checks the table's features against its readers. Unless a write names others, the readers are the SQL analytics endpoint and Direct Lake (`delta_profile.DEFAULT_READERS`). Suppose a table uses a feature that one of its readers can't read, or that no source says the reader can read (an "Unknown" cell). Then the write is refused, and the refusal names the reader, the feature and the source.
- **How to choose.**
  - Keep the modern profile when the table is read through the SQL analytics endpoint, Direct Lake, Fabric Spark, Databricks or DuckDB. It lets KratosForge write to tables that Fabric Spark made with deletion vectors, and to tables with a column name that needs column mapping.
  - Use the conservative profile when a Fabric Python notebook, a pipeline or an eventstream reads the table, or when you don't know who reads it.
- **What KratosForge itself writes.** A table KratosForge creates stays at reader 1 and writer 2: delta-rs never writes deletion vectors (a DELETE rewrites files), and no feature is turned on for you. On a table that has these features, appends, overwrites, merges, updates and deletes keep the features and the rows right. We checked this with Spark reading the tables back.
- **Column mapping limits.** delta-rs can't change a column-mapped table's columns. A write that adds or leaves out a column is refused before it starts. Change the columns in Fabric Spark first. Compacting a column-mapped table on this PC is also refused by delta-rs. Fabric's table maintenance compacts it instead.

## Each table's own choice (Preview)

In a metadata project, each table can choose the Delta features a run may leave on it when it publishes the table. Choose it in Data mesh > Deploy > Environments and promote, under **Delta table features at publish**, in each table's **Delta features** list. The choice is kept in the table's entity file, so it reaches Test and Prod with the next promote.

- **Default: deletion vectors and column mapping (modern)** (no setting): the modern profile, with deletion vectors and column mapping by name.
- **Every reader: no deletion vectors or column mapping** (`delta_features = "every_reader"`): the conservative profile. Every reader in the matrix reads it.
- **Allow deletion vectors only** (`delta_features = "deletion_vectors"`): reader version 3 and writer version 7 at most, with deletion vectors and no column mapping.
- **Allow column mapping by name only** (`delta_features = "column_mapping"`): reader version 3 and writer version 7 at most, with column mapping by name and no deletion vectors.

A table's readers are the SQL analytics endpoint and Direct Lake unless its file names others, like `delta_readers = ["sql_endpoint", "pipelines"]`. The keys are the reader names in `delta_profile.READERS`.

When a choice allows a feature that one of the table's readers can't read, or that no source says it reads, KratosForge saves it only after you type the table's full name, such as `gold.fact_sales`, to allow it. The dialog lists each reader, feature and source, as a refused write does. The readers you allowed are kept in the file as `delta_accepted`, and a write doesn't check them again. A file changed outside the app with such a choice and no `delta_accepted` fails the save gate until you choose again in the app. Removing from `delta_readers` a reader that the choice would break also needs the typed name. A reader that is out of `delta_readers` is no longer checked, so a later choice that breaks it doesn't ask. `delta_accepted` is part of the file, so a hand edit or a change that comes in through Git can allow a reader without the typed confirm. Review changes to it as you would any other change to the project.

Every write to the table is checked with its choice before anything is committed. A table that already uses a feature the choice doesn't allow is refused, and its version, Delta log and files stay as they were. A value KratosForge doesn't know is refused when you save, and the save gate names it. A file from before this setting has none, so it keeps the default.

## Reading these tables in KratosForge

KratosForge reads tables with deletion vectors or column mapping correctly, whoever wrote them. Fabric Spark writes both: deleted rows stay in the data files and a deletion vector marks them, and with column mapping the data files name each column `col-<id>`. Fabric turns column mapping on for a column name with a space.

- The pyarrow reader in delta-rs refuses deletion vectors. On a column-mapped table that another writer made, it reads every value as NULL. KratosForge reads these tables through delta-rs's DataFusion reader instead (`sidecar/fabric_desktop/delta_read.py`). This covers previews, notebooks, Spark on this PC, SQL over the lakehouse, pipeline Lookup and Get Metadata, proofs and environment compare. A table with neither feature is read as before. The DataFusion reader reads such a table whole into memory before a query or a notebook works on it, even for a `SELECT ... LIMIT 10`. So a table whose data files hold more than 2 GB is refused, and the refusal gives the reason. The files are compressed, so the read can take several times that in memory. Previews and pipeline Lookup read only the first rows, so they have no limit. A local data project reads the table in place, without the limit.
- A local data project reads such a table through its own view. The view reads each column by its physical name and gives it the table's name. It leaves out each deleted row by its row number in its file. Two limits apply. A struct column under column mapping is refused, because a local read names only top-level columns. A table with more than 50,000 runs of deleted rows is also refused, and compacting it in Fabric (OPTIMIZE) folds the vectors in.
- A stream on this PC refuses both features. It reads new files by path, so it would bring deleted rows back or miss renamed columns. Run the stream in Fabric.
- Row counts from the log's file statistics (previews, environment compare) leave out rows that a deletion vector removed.

Reading a table doesn't make it writable. Writes follow the profiles above.

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
