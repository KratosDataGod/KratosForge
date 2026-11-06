# Design an eventstream: filter, aggregate and route to two destinations

Freezer telemetry flows into one eventstream. Today every event, test rigs included, lands raw in the Lakehouse, the Eventhouse gets averages per region, and the partner's custom endpoint hangs off an operator.

## What you'll do

Model one eventstream: filter its events, shape them for a Lakehouse table, group them for an Eventhouse table with the right ingestion mode, and hand the partner a derived stream.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your filter, field list, grouping, ingestion mode and derived stream meet the four needs, as the eventstream pages on Learn describe operators and destinations, checked against a model on this PC.

## What it doesn't prove

- That any event flows: nothing is read, filtered or written.
- Group by's time window settings, which this model leaves out.
- The exact names of conditions and functions in the eventstream editor.
