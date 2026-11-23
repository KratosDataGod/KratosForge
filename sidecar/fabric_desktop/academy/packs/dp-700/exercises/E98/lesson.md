# Broken: an eventhouse update policy stops filling its table after a function edit

In an eventhouse, an update policy on Readings turns each batch that lands in RawTelemetry into typed rows. On Monday someone edited the ExpandTelemetry function it runs. Since then RawTelemetry keeps growing, but Readings gets no new rows, and the dashboards that read it have gone flat.

## What you'll do

Compare an update policy's function output with its target table, find the column order and type that no longer match, fix the function, and make the policy transactional so the target table doesn't lose rows.

## Symptom

RawTelemetry keeps growing, but Readings has had no new rows since Monday's edit to ExpandTelemetry.

## How it runs here

This lab is a model, not an eventhouse. settings.json holds the two tables, a sample event, the function's output column by column, and the update policy with the property names Kusto uses. Each output entry names a column, the payload field it reads, the KQL conversion that makes it, and the type it returns. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## Two things to know

- An update policy's query output and its target table must match in column types and order.
- IsTransactional decides what a failed policy does to the source table. With false, the source still gets the rows. With true, neither table gets them and the ingestion fails.

## What this run proves

- Your function returns the four columns in the target table's order with its types, converts the temperature with toreal or todouble, leaves the tables as they were, and makes the update policy transactional, checked against a model on this PC.

## What it doesn't prove

- That an eventhouse accepts the function or the policy. Nothing is sent to Fabric, and no KQL runs here.
- Backfilling the rows Readings missed since Monday. They're still in RawTelemetry, but this exercise doesn't load them again.
- Where the failures show up. Learn names the .show ingestion failures command for update policy failures, and this model doesn't run it.

## Read more on Learn

- [Update policy overview](https://learn.microsoft.com/kusto/management/update-policy?view=microsoft-fabric)
- [toreal()](https://learn.microsoft.com/kusto/query/toreal-function?view=microsoft-fabric)
