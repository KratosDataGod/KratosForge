# Data workflow (Apache Airflow job) settings and when to use them

Two teams want orchestration in the same workspace: one writes Airflow DAGs in Python, the other doesn't write code. Operations wants idle environments to stop using compute.

## What you'll do

Decide when an Apache Airflow job fits better than a pipeline, and which of its settings handle versions, packages, idle time and DAG sources.

## Notes

- Apache Airflow jobs run Python DAGs as a managed service; pipelines are the no-code option.
- The Airflow version can't be changed on an existing job: create a new one.
- Features include Git sync, private package requirements, pause and resume time to live, autoscale and high availability.

## What this run proves

- You can pick the right feature, setting or operator for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
