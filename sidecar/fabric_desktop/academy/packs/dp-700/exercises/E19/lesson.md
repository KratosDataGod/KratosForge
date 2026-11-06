# Parameterised pipeline: ForEach over tables with dynamic expressions

The nightly load copies a fixed pair of tables, and every new table means editing the pipeline. The platform team wants one pipeline that loads whatever list of tables its schedule passes in, names each Bronze table bronze. followed by the table name, and returns the list it loaded so the next step can check it.

## What you'll do

Drive a pipeline from a parameter: loop over the tables it names, build each target name with an expression, and return what was loaded.

## How it runs here

Your answer is the pipeline's JSON (`pipeline.json`), the same shape Fabric shows in the pipeline's JSON view. It runs on the app's local pipeline runner with control-flow activities only. Copy and Lookup activities are stand-ins that return fixed results, so you can practise expressions, dependencies and loops without moving data or using capacity.

## What this run proves

- Your pipeline JSON ran on the local runner on this PC with the run's own table list: the loop, the expressions and the return value behaved as required.

## What it doesn't prove

- Copying data: the Copy activity here is a stand-in with a fixed result, so no data moves and no connection is tested.
- Running in a Fabric workspace, its schedule or trigger, or parallel ForEach behaviour under load.
