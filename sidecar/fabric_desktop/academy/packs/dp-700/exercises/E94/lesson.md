# Mirror an Azure SQL Database: prerequisites, which tables, what replicates and how to query it

Reports at a retailer read straight from the production Azure SQL Database and slow it down. The team wants the sales tables mirrored into Fabric and queried there, with HR data kept out. The first draft of the plan mirrors everything, expects every column to arrive, and assumes the copy can be fixed and secured like the source.

## What you'll do

Plan the mirroring of an Azure SQL Database. Set the server identity, the creator's role and the database user's permissions, choose the tables to mirror, say which tables and columns can't come across, and decide where fixes, joins and row security happen.

## What to know

Mirroring keeps a copy of a database's tables in OneLake, as Delta tables, and keeps it in sync as the source changes. You choose the whole database or individual tables. Fabric makes a mirrored database with a SQL analytics endpoint you query with T-SQL.

Before you start, these must be in place.

- The Azure SQL logical server has a managed identity turned on, and it's the server's primary identity.
- The person who creates the mirrored database has the Member or Admin role in the workspace. A Contributor doesn't have the Reshare permission that step needs.
- The database user for the connection has SELECT, ALTER ANY EXTERNAL MIRROR, VIEW DATABASE PERFORMANCE STATE and VIEW DATABASE SECURITY STATE.

When you choose tables, Mirror all data also mirrors every table created after mirroring starts. Turn it off to pick tables yourself.

Some things never come across.

- Some tables can't be mirrored, such as temporal history tables and ledger history tables.
- Some columns are left out of a table that is mirrored, such as computed columns and columns of type image, text, ntext, xml, rowversion, sql_variant, geometry or geography.

Using the copy works differently from using the source.

- The mirrored data is read-only. T-SQL can query it but not change it, so fix data in the source and let mirroring carry the change.
- A cross-database query joins a mirrored database with warehouses and lakehouses in one T-SQL query, with no copy step.
- Granular security set up in the source, such as row-level security, has to be set up again in Fabric.

Sources on Microsoft Learn:

- [Mirroring in Fabric](https://learn.microsoft.com/en-us/fabric/mirroring/overview)
- [Mirroring Azure SQL Database](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database)
- [Tutorial: configure mirroring from Azure SQL Database](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database-tutorial)
- [Limitations of mirroring from Azure SQL Database](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database-limitations)

## How it runs here

This lab is a model, not Fabric. settings.json describes the source database and holds the plan you change. Check my work reads it as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your plan turns on the server's managed identity, has a Member create the mirrored database, gives the database user the four permissions mirroring needs, and mirrors only the three sales tables with Mirror all data off, as the mirroring tutorial on Learn describes them, checked against a model on this PC.
- Your plan names the temporal history table as one that can't be mirrored, leaves out the computed, ntext and rowversion columns, fixes data in the source, joins through a cross-database query and sets row security up again in Fabric, as the mirroring overview and limitations pages describe them, checked against a model on this PC.

## What it doesn't prove

- That anything replicates. Nothing connects to a database here, and no mirrored database is made.
- A user-assigned managed identity, which can also be the server's primary identity. This model only has the system-assigned one.
- Network setup for a server that isn't publicly reachable, such as a data gateway.
- The exact T-SQL of the cross-database query, or how fast changes arrive.
