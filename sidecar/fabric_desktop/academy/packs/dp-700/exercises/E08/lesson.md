# A warehouse as a database project: add a table and a view, and catch a breaking change

The Sales warehouse is kept as a database project: one file per object under dbo/Tables and dbo/Views. You're adding stores so sales can be reported by region. The change set you're about to ship also carries a teammate's edit that drops DimProduct.Category, which vw_SalesByCategory still reads and reports depend on.

## What you'll do

Add a table and a view to a warehouse's database project, one .sql file per object, check that every object still builds, and keep a teammate's change from breaking an existing view on deploy.

## How it runs here

Your answer is a change set: file sections for new or changed objects, and delete lines for removed ones. Check my work applies it to the project, builds every table and view in DuckDB on this PC, compares the result with what the warehouse has, and runs the view on a few sample rows.

## Before you start

- LM-23

## What this run proves

- Your change set puts each object in its own file in the project's folders, every table and view in the project builds, a deploy would only add objects, and vw_SalesByRegion returns the right sales per region, checked on this PC.

## What it doesn't prove

- A build with the SQL project tools or a deploy to a Fabric Warehouse: the T-SQL is read and built in DuckDB, which isn't the Warehouse engine, and stored procedures are read but not run.
- Deployment pipelines and Git integration in Fabric, which move the project between workspaces.
