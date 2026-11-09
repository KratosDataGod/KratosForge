# Semantic model refresh: read the history, spot a Direct Lake fallback

Five Direct Lake semantic models sit on one F64 capacity. Finance says their numbers stopped moving, and the Stock report got slow last week. You have each model's refresh history, the tables it reads, and a log of which mode each report query really ran in. Find the stale model, the models that fell back, and why.

## What you'll do

From a refresh history and a query log, report each semantic model's latest refresh and its error, count the queries that fell back from Direct Lake to DirectQuery, and name the cause.

## How it runs here

A Direct Lake refresh is framing: a metadata operation that points the model at the latest version of each Delta table, so it takes seconds, not the hours an import refresh can take. When it fails, the model keeps answering from the last version it framed.

A Direct Lake on SQL endpoints model can also fall back: a query it can't answer in Direct Lake goes to the SQL analytics endpoint in DirectQuery. DirectLakeBehavior decides what happens: Automatic (the default) falls back, DirectLakeOnly returns an error instead, and DirectQueryOnly always uses DirectQuery. Queries over SQL views fall back, and a single table over a capacity guardrail sends the whole model to DirectQuery. In a real model, EVALUATE TABLETRAITS() shows the fallback reason per table.

This lab gives you the same facts as four made-up tables: models, refresh_history, model_tables and query_log.

## What this run proves

- Your query finds each model's latest refresh by its start time, counts fallback only for models set to fall back, and traces it to a table over an F64 guardrail or to a SQL view, on simulated data with DuckDB on this PC.

## What it doesn't prove

- The refresh history page, the DAX query view, or EVALUATE TABLETRAITS() in a real model. The history and query log here are made up and shaped like what they show.
- Fallback causes this data doesn't include: row-level or object-level security defined at the SQL analytics endpoint, and the guardrails of capacities other than F64.
- Fixing the causes: compacting the oversized table, or replacing the view with a table.
