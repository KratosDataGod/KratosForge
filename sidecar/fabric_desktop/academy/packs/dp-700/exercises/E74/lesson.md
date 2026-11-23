# Six workloads, four stores: lakehouse, warehouse, eventhouse or SQL database

Brightline Manufacturing is moving six workloads into Fabric. The architect's first draft puts everything that uses T-SQL in a warehouse and everything else in a lakehouse, and the review board sent it back.

## What you'll do

Record the right Fabric data store for six workloads, including the two where T-SQL alone doesn't decide it.

## How it runs here

This lab is a model, not Fabric. decisions.json holds each scenario and the decision you record for it. Check my work reads it as data; Run lists every value so you can see what the checks read.

## What this run proves

- Your store for each workload matches the data store decision guide and the lakehouse or warehouse guide: SQL database for OLTP, warehouse for multi-table transactions and T-SQL writes, eventhouse for streaming events and interactive analytics, lakehouse for Spark and unstructured data, with read-only T-SQL readers left on the lakehouse, checked against a model on this PC.

## What it doesn't prove

- That any store is created or loaded: nothing runs here.
- Costs, capacity sizes or performance numbers for any store.
- Cosmos DB in Fabric, which this exercise leaves out.
