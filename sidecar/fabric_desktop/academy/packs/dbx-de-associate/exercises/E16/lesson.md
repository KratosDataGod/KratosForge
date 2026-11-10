# Choose the ingestion method for each requirement

A retailer's platform team gets six ingestion requests in one week: a SaaS CRM, a stream of JSON files, an operational SQL Server database, a SQL-only team, a source with no connector, and a stream whose folder listing has become expensive.

## What you'll do

Pick the ingestion method for a requirement from volume, frequency, data types and governance needs.

## Notes

- Start with the most managed layer, Lakeflow Connect's managed connectors, and drop down when it doesn't support your source.
- Auto Loader (cloudFiles) processes new files in cloud storage as they arrive, exactly once; file notification mode avoids listing the folder.
- Database connectors use change data capture. For SQL users, CREATE STREAMING TABLE is the recommended alternative to COPY INTO.

## What this run proves

- You can pick the right component, method or trigger for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can set it up in Databricks: nothing is created or run here.
