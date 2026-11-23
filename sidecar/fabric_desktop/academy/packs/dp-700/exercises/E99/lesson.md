# Broken: an eventstream misses a CDC snapshot and reads with another app's consumer group

In test, the OrdersStream eventstream lost the initial snapshot of dbo.orders. The Azure SQL Database CDC source sent it at publish, but the Eventhouse table got only later changes. The same stream reads the payments event hub, and the archive team asked why a second reader joined their consumer group. The production copy has the same settings and is published tomorrow.

## What you'll do

Find why an eventstream's Eventhouse destination missed the events sent when the stream started, and why its Event Hubs source shares a consumer group, then fix the production copy before it's published.

## Symptom

OrdersTable has the changes made after publish but none of the snapshot rows, and the archive team sees a second reader in their consumer group.

## How it runs here

This lab is a model, not an eventstream. settings.json holds what the stream needs, its sources, its operator, its two Eventhouse destinations and the consumer groups on the payments event hub. The settings use the names from the eventstream editor, such as Activate ingestion and the start options Now, When streaming was last stopped and Custom time. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## Two things to know

- When an eventstream is published, ingestion can start before routing is ready. Events that arrive in that gap might not reach a destination.
- Every reader of an event hub reads with a consumer group. $Default is just the group each event hub starts with, and it can already belong to another app.

## What this run proves

- Your production copy adds OrdersTable without activating it, starts it from the runbook's custom time, keeps it after the filter in event processing mode, and reads payments with a new orders-stream consumer group while the other apps keep theirs, checked against a model on this PC.

## What it doesn't prove

- That the eventstream publishes or routes a single event. Nothing is sent to Fabric here.
- How long the eventstream keeps events, and so how far back a custom time can reach. The model takes the runbook's time as given.
- Creating the consumer group in Azure Event Hubs. The model lists it, and the Event Hubs admins create it.

## Read more on Learn

- [Add an Eventhouse destination to an eventstream](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/add-destination-kql-database)
- [Pause and resume data streams](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/pause-resume-data-streams)
- [Add an Azure Event Hubs source](https://learn.microsoft.com/fabric/real-time-intelligence/event-streams/add-source-azure-event-hubs)
- [Event Hubs features, including consumer groups](https://learn.microsoft.com/azure/event-hubs/event-hubs-features)
