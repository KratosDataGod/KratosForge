# Five streaming flows: eventstream, Spark Structured Streaming or KQL

Larkspur Payments has five streaming flows. One team wants every flow in PySpark, another wants everything in an eventstream, and the production Spark stream stopped for a day after the last patching window.

## What you'll do

Record the streaming engine for five flows, and for the Spark streams, how they run in production and how often they write.

## How it runs here

This lab is a model, not Fabric. decisions.json holds each scenario and the decision you record for it. Check my work reads it as data; Run lists every value so you can see what the checks read.

## What this run proves

- Your engine for each flow matches the eventstream, eventhouse and lakehouse streaming pages, and your production Spark stream runs as a Spark job definition with a retry policy and the small-files stream sets a trigger interval, checked against a model on this PC.

## What it doesn't prove

- That any stream runs: no events flow here.
- The retry policy's count and interval, or the exact trigger interval that gives well-sized files.
- The eventstream SQL operator and Spark Notebook destination, which are in preview.
