# A new column and a reporting schema in a warehouse database project

The Sales warehouse is kept as a database project, with one file per object. Sales now record the channel they came through, Online or Store. FactSales already holds rows from before the channel was tracked, and those rows have no channel. Finance wants a report of sales by channel in a new reporting schema, kept apart from the dbo tables. Reports still read dbo.vw_SalesByCategory, so it stays where it is.

## What you'll do

Change an existing table in a warehouse's database project so a deploy can add the column in place, and add a schema with a view in its own files.

## What the change set must hold

- FactSales with a new Channel column, VARCHAR(10), that the deploy can add to the existing table.
- A reporting schema in its own file.
- reporting.vw_SalesByChannel with Channel, Sales and Orders, where a sale with no channel counts as Unknown.
- dbo.vw_SalesByCategory left where it is.

## How it runs here

Your answer is a change set, with a file section for each new or changed object. Check my work applies it to the project, builds every table and view in DuckDB on this PC, compares the result with what the warehouse has, and runs the new view on a few sample rows.

## Before you start

- E08

## What this run proves

- Your change set puts each object in its own file in the project's folders, every table and view builds, a deploy would add the column without rebuilding FactSales and drop nothing, and vw_SalesByChannel returns the right sales per channel, checked on this PC.

## What it doesn't prove

- A build with the SQL project tools or a deploy to a Fabric Warehouse. The T-SQL is read and built in DuckDB, which isn't the Warehouse engine.
- The exact plan a DacFx deploy makes. This lab's own compare decides what counts as adding a column in place.
- Where Fabric's Git integration writes a schema's file. Security/reporting.sql is where this lab keeps the reporting schema, and Learn doesn't show it in text.
- Moving the rows or the reports. Old rows keep an empty Channel, and no report is pointed at the new view.
