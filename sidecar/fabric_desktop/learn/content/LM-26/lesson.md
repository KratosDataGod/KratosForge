# Three-part names: Unity Catalog and Fabric

Databricks Unity Catalog names every table with three parts: `catalog.schema.table`, such as
`prod.sales.orders`. A name with fewer parts is filled in from the session's current catalog and
schema, which differ between a notebook, a job and a SQL warehouse.

## The same idea in Fabric

| Unity Catalog | Fabric |
| --- | --- |
| Metastore | Tenant (OneLake) |
| Catalog | Workspace, then lakehouse or warehouse |
| Schema | Schema in a schema-enabled lakehouse, or a Warehouse schema |
| Table | Delta table |

A Fabric Spark query can name `workspace.lakehouse.schema.table`; Warehouse T-SQL uses
`warehouse.schema.table` across items in a workspace.

## Always name the schema

A table named without its schema finds whichever table the session reaches first. When a stray
copy exists, as an old `orders` in the default schema often does, the query reads the wrong data
without an error. Rule SQ005 flags table names without a schema.

## Try it

Total the real orders table.
