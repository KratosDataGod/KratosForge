# A schedule or a storage event trigger for six pipelines

Harbor Retail runs six pipelines, and each one was started by hand until now. Some need fixed times, one has to run more often than a daily schedule allows, and three should start the moment a file lands or is deleted, in Azure Blob Storage or in OneLake.

## What you'll do

Decide whether each of six pipelines starts on a schedule or on a storage event, then set the frequency, days, times and end date, or the event source, event types, folder and parameters.

## How it runs here

This lab is a model, not Fabric. pipelines.json holds what each pipeline needs and the schedule or trigger you choose for it. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your start for each pipeline matches the schedule recurrence limits of the job scheduler page and the schedule and storage event trigger rules of the pipeline runs page, with the Azure Blob Storage and OneLake event types from the Real-Time hub pages, checked against a model on this PC.

## What it doesn't prove

- That any schedule or trigger fires in Fabric. Nothing is scheduled here.
- The exact labels Fabric's schedule and trigger panes show. The model uses its own field names.
- Interval-based schedules and run conditions, which are in preview and left out.
- The permissions and the eventstream and Activator items a storage event trigger creates.
