# Apache Airflow runtime settings: starter or custom pool, node size, autoscale and a private package

The operations team runs Apache Airflow jobs in two workspaces. The development workspace costs money all night, the production one waits several minutes before a run starts, and their own package was dropped in the wrong folder.

## What you'll do

Choose the default pool for a development and a production workspace, size and scale a custom pool, add a private package as a requirement, and predict how each pool behaves.

## How it runs here

This lab is a model, not Fabric. airflow.json holds the needs for two workspaces, the runtime settings you choose and your expectations. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your settings location, default pools, custom pool size and autoscale, private package requirement and expectations follow the Apache Airflow job workspace settings, compute, private package and concepts pages, checked against a model on this PC.

## What it doesn't prove

- That any pool is created or any DAG runs in Fabric. Nothing runs here.
- The starter pool's node size. The workspace settings page says Large and the compute page says small nodes, so it isn't graded.
- Which capacity size each pool needs, and the cost of each node.
- How a job's own runtime settings interact with the workspace default. The workspace settings page doesn't say.
- Public packages from PyPI, Git sync and the role needed to change these settings.
