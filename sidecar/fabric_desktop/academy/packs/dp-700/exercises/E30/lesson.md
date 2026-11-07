# Mirroring: what replicates, how fast, and how to query it

Contoso wants its Azure SQL Database and an in-house order system in Fabric for analytics, with little delay and no copy pipelines to maintain.

## What you'll do

Explain what mirroring creates, how fast it publishes changes, what it costs and which kind fits a custom application.

## Notes

- Mirroring replicates continuously into OneLake as Delta and adds a SQL analytics endpoint.
- Changes can publish as fast as every 15 seconds.
- Replication compute is free; each CU brings a free terabyte of mirroring storage.
- Open mirroring lets your own application write change data; metadata mirroring syncs only catalog metadata.

## What this run proves

- You can pick the right feature, setting or operator for each scenario, with the reason and source shown afterwards.

## What it doesn't prove

- That you can do it in Fabric: nothing is set up or run here. Doing it on a real item is FABRIC VERIFIED work.
