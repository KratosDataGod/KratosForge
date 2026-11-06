# Pipeline or notebook: choose the tool for each scenario

Harbourline's data team has five jobs to build this quarter, from a petabyte migration to a stream that must keep running. Each needs the right tool.

## What you'll do

Choose between a pipeline, a Copy job, a notebook and a Spark job definition for five loading and orchestration scenarios, and say why.

## Notes

- The pipeline copy activity is the no-code, low-code way to move large volumes into lakehouses and warehouses, with light transformation.
- A Copy job covers ingestion, incremental copy and replication with no code.
- Spark notebooks are the code option for complex transformation.
- notebookutils.notebook.runMultiple runs notebooks together, with dependencies between them.
- A stream that must run all the time belongs in a Spark job definition with a retry policy, not a notebook.

## What this run proves

- You can pick the right tool or store for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can build it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
