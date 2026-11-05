# Port a Databricks notebook to Fabric

Most Spark code runs in Fabric unchanged: both run Apache Spark on Delta tables. What breaks is
the edges: table names, a few Databricks-only functions and utilities, and statements a Fabric
Warehouse doesn't have.

## What to change

| Databricks | Fabric |
| --- | --- |
| `spark_catalog.default.orders`, `catalog.schema.table` | the lakehouse's own name: `silver.orders` in a schema-enabled lakehouse |
| `dbutils.fs`, `dbutils.secrets` | `notebookutils.fs`, `notebookutils.credentials` (Key Vault) |
| `nvl(x, 0)` | `coalesce(x, 0)`: standard, and it runs everywhere |
| `INSERT OVERWRITE TABLE t SELECT ...` | Spark: works as is. Warehouse T-SQL: `DELETE` then `INSERT`, or `TRUNCATE TABLE` |
| Databricks SQL warehouse | Fabric Warehouse or the lakehouse's SQL analytics endpoint |

Prefer standard SQL when you port, so the code runs on both. The lab runs in DuckDB, which has
neither Databricks' nor T-SQL's extras, so standard SQL is the only kind that passes.

## The lab

The notebook sums each customer's net amount and overwrites a table with it. Port the query, then
the write.
