# Lakehouse, warehouse, eventhouse or SQL database: choose the store

Northgate is moving five workloads into Fabric: machine telemetry, an order-entry app, finance reporting, a data science project and a set of read-only SQL users.

## What you'll do

Choose the Fabric data store for five workloads by their data, language and transaction needs, and say why.

## Notes

- Streaming events and fine-grained interactive analytics: an eventhouse (KQL, T-SQL).
- Operational OLTP work: a SQL database in Fabric.
- T-SQL, star schemas and multi-table transactions: a warehouse.
- Spark, machine learning, and unstructured or semi-structured data: a lakehouse.
- A lakehouse's SQL analytics endpoint reads with T-SQL and allows views and table-valued functions, but no DML.

## What this run proves

- You can pick the right tool or store for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can build it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
