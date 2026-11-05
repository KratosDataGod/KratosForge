# Type 2 history: close the old row, add the new one

Finance reports revenue by the city a customer lived in when they ordered, so city changes must keep history. Marketing only ever wants the latest email address, on every row. Tonight's batch has a move, an email change, a customer with no change and a new customer.

## What you'll do

Apply a batch of customer changes to a dimension that keeps history for city (type 2) and overwrites email (type 1).

## Before you start

- LAB-05

## What this run proves

- Your statements ran on this PC with DuckDB and left dim_customer with the expected history.
- Every customer has exactly one current row and every surrogate key is unique.

## What it doesn't prove

- That the statements run in a Fabric Warehouse or Spark SQL unchanged: UPDATE ... FROM is written differently in Spark SQL, where MERGE is the usual way.
- Behaviour under concurrent loads, which needs the target's own transaction rules.
