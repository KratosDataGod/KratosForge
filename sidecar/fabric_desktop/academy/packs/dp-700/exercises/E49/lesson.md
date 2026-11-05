# Broken: a ForEach loads one wrong table instead of three

The schedule passes the tables to load as text, 'customers, orders,stores', typed by hand with uneven spaces. The run succeeds, but it copied one table called 'customers, orders,stores' and nothing else. Make the pipeline load the three tables the text names.

## What you'll do

Find why a ForEach runs once over a whole string, convert a text parameter into the list it stands for, and keep the pipeline driven by the parameter.

## Symptom

The run succeeds but returns loaded = 'bronze.customers, orders,stores'.

## How it runs here

Your answer is the pipeline's JSON (`pipeline.json`), the same shape Fabric shows in the pipeline's JSON view. It runs on the app's local pipeline runner with control-flow activities only. Copy and Lookup activities are stand-ins that return fixed results, so you can practise expressions, dependencies and loops without moving data or using capacity.

## Before you start

- E19

## What this run proves

- Your repaired pipeline JSON ran on the local runner on this PC with the schedule's text parameter and looped over the three table names, trimmed.

## What it doesn't prove

- Copying data: the Copy activity here is a stand-in with a fixed result, so no data moves and no connection is tested.
- How a Fabric schedule or trigger passes parameter types; check your trigger's parameter settings in the workspace.
