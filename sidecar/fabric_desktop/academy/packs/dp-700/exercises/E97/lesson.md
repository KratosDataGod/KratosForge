# Broken: a Dataflow Gen2 shows date errors and its refresh stops on an empty date

The Shipments dataflow loads files from a new carrier, Northwind UK, into silver_shipments. The preview shows Error in many ship_date and delivered_on cells, and last night's refresh failed with error E104100, which says it can't insert null data into a non-nullable column. The other queries in the same dataflow read US files and work fine.

## What you'll do

Read a Dataflow Gen2 query's type conversions and its data destination, find why dates turn into errors and why the refresh fails on empty values, and fix the query and the destination without losing rows.

## Symptom

The preview shows Error in ship_date and delivered_on for dates like 22/10/2026, and the refresh fails with error E104100, which says it can't insert null data into a non-nullable column.

## How it runs here

This lab is a model, not a dataflow. settings.json holds what the load needs, a few sample rows, the query's applied steps in order, the project's regional settings and the data destination. In Changed column type, each column has a type, whether it may be empty, and a locale. A locale of null means the project's regional settings. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## Two things to know

- Power Query uses a locale to turn text into numbers and dates. In Power Query Online the default comes from the project's regional settings, and Change type has a Using locale option for one column.
- A destination column that doesn't allow null makes the refresh fail when a row has no value for it.

## What this run proves

- Your query reads both date columns with the English (United Kingdom) locale while the project keeps English (United States), lets delivered_on be empty in the query and in the destination, and adds no step that drops or hides rows, checked against a model on this PC.

## What it doesn't prove

- That the dataflow publishes or refreshes in Fabric. Nothing runs here, and no Power Query (M) is evaluated.
- The rebuild itself. Learn's fix for a column that's wrongly non-nullable deletes the destination table, removes the destination, changes the type and adds the destination again. The model only checks the result.
- What happens to cells with errors when they reach a destination. The data destinations page doesn't say, so the model only fixes them at the step that makes them.

## Read more on Learn

- [Data types in Power Query, including locale](https://learn.microsoft.com/power-query/data-types)
- [Dealing with errors in Power Query](https://learn.microsoft.com/power-query/dealing-with-errors)
- [Dataflow Gen2 data destinations and managed settings](https://learn.microsoft.com/fabric/data-factory/dataflow-gen2-data-destinations-and-managed-settings)
