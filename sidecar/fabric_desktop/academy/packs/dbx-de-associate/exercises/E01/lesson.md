# Map the platform: control plane, compute, Delta Lake and Unity Catalog

A security reviewer at a bank asks a new data engineer where Databricks runs each part of the platform, what format the team's tables are in, and who owns the files when a governed table is dropped.

## What you'll do

Explain where the control plane and compute run, what makes a table Delta, and how Unity Catalog names and manages tables.

## Notes

- The control plane is in the Databricks account. Classic compute runs in your cloud account; serverless compute runs in the Databricks account, in the workspace's region.
- Tables on Databricks are Delta Lake tables unless you say otherwise: Parquet files plus a transaction log, and each write is a new version.
- Unity Catalog names objects catalog.schema.object. A managed table's files are Unity Catalog's to manage; an external table's are not.

## What this run proves

- You can pick the right component, method or trigger for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Databricks: nothing is set up or run here. Reading a real table's log and history is exercise E02.
