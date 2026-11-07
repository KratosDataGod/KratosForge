# Alerts: Activator rules for warm and silent freezers

Freezer telemetry streams into Fabric. Store staff are flooded with emails, one per warm reading, about whole stores rather than freezers, at the wrong threshold, and nobody notices a freezer that stops sending.

## What you'll do

Model an Activator item: pick the object ID, write a stateful rule that alerts once and starts a pipeline, and a heartbeat rule for devices that go quiet.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your object and rules watch each freezer, alert once when it turns warm, start the incident pipeline and catch silence, as the Activator overview describes objects, rules and actions, checked against a model on this PC.

## What it doesn't prove

- That Activator fires anything: no events flow and nothing is sent.
- The exact names and options of Activator's conditions in the editor.
- Alerts set up from a pipeline's own failure notifications.
