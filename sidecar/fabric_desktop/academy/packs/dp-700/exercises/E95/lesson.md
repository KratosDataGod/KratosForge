# Broken: an eventstream Union drops one source's fields and a window overlaps

Two loading docks send cold-chain readings into one eventstream. The north dock has worked for months. Since the south dock's sensors went live, its rows land with no temperature and no sensor id, the dashboard shows no south sensors, and every sensor has several overlapping figures where the team expected one per 5 minutes.

## What you'll do

Repair an eventstream that merges two sources. Line up one source's field names and types with Manage fields before the Union, group with a tumbling window, and pick the Eventhouse ingestion mode that allows operators.

## Symptom

South rows in dock_readings have no TempC and no SensorId. sensor_temps shows no south sensors. Each sensor has several overlapping 5-minute figures.

## What to know

These operators matter here.

- **Union** merges two or more streams into one. It keeps the fields that have the same name and the same data type in every input. Fields that don't match are dropped from the output.
- **Manage fields** adds, removes or renames fields, or changes their data type. Put it on a source before a Union to make that source's fields match the other's.
- **Group by** aggregates events within a time window, grouped on one or more fields. A tumbling window cuts time into fixed blocks that don't overlap, so each event counts once. A sliding window overlaps, so one event can count in several results.

An Eventhouse destination can follow an operator only with event processing before ingestion. Direct ingestion lands the events without any processing.

Sources on Microsoft Learn:

- [Process event data with the event processor editor](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/process-events-using-event-processor-editor)
- [Route data streams based on content](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/route-events-based-on-content)
- [Eventstream transformations (training)](https://learn.microsoft.com/en-us/training/modules/explore-event-streams-microsoft-fabric/4-route-event-data-to-destinations)
- [Add an Eventhouse destination](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-destination-kql-database)

## How it runs here

This lab is a model, not Fabric. settings.json holds what the team needs, both sources' fields and types, and the eventstream you repair. Check my work reads it as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your eventstream renames and retypes the south dock's fields to the north dock's before the Union, feeds both docks to the Lakehouse, groups each sensor in 5-minute tumbling windows and sends that to the Eventhouse with event processing before ingestion, as the eventstream pages on Learn describe the operators and destinations, checked against a model on this PC.

## What it doesn't prove

- That any event flows. Nothing is read, merged, grouped or written.
- The exact names of data types and window settings in the eventstream editor. This model uses plain names for them.
- Window types other than tumbling and sliding, which this model leaves out.
