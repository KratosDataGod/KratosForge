# Schedule a pipeline and trigger it when a file lands

Load orders runs a full reload every night and should also pick up partner files as they land during the day. The current schedule runs at the wrong hour, never ends, and sends a parameter the pipeline ignores. There's no file trigger yet.

## What you'll do

Set a pipeline's nightly schedule and a storage event trigger that starts it for each new file, passing the right parameters to each.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your schedule and trigger start the pipeline at the right times with the right parameters, as the pipeline runs page describes them, checked against a model on this PC.

## What it doesn't prove

- That the schedule or trigger fires in Fabric: nothing is scheduled here.
- The exact names Fabric shows for time zones, event types and filters; the model uses its own fields.
- Interval-based schedules (preview) and OneLake event triggers.
