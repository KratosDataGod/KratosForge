# Broken: an eventhouse loads empty rows after the devices changed their events

The DeviceReadings table in an eventhouse filled up nicely until this morning. Since the devices moved to firmware 2.x, new rows arrive with DeviceId, ReadingTime and TemperatureC empty. Nobody changed the table or the mapping. The devices changed what they send.

## What you'll do

Compare the events arriving today with the table's JSON ingestion mapping, find which paths and types no longer fit, and fix the mapping so every column is filled again.

## Symptom

New rows arrive, but DeviceId, ReadingTime, TemperatureC and Firmware are empty.

## How it runs here

This lab is a model, not an eventhouse. settings.json holds the table's columns, an event from last week, one from today, and the JSON ingestion mapping in the shape Kusto uses: each entry names a column and the Path to read it from, with an optional Transform. Check my work reads the mapping as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your mapping reads each column from a field today's events carry, turns ts into a datetime with the right Unix-time transform, and leaves the table as it was, checked against a model on this PC.

## What it doesn't prove

- That an eventhouse accepts the mapping or ingests a single row: nothing is sent to Fabric, and no KQL runs here.
- Devices still on the old firmware: here every device moved to 2.x overnight. A mixed fleet needs a mapping or an update policy that reads both shapes.
- Where ingestion problems are reported in Fabric, and the exact behaviour of a path that's missing from an event.
