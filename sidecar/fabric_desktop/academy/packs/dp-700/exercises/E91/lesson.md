# Alerts: pipeline notifications, refresh failure emails and Activator rules

The data team's alerts are a mess. On-call engineers miss copy failures, the run summary only appears on good nights, refresh failures go to nobody, store managers get a message for every low reading, and the error alert checks too often and only sends email. Fix each setup so it does what its need says.

## What you'll do

For five alerting needs, choose the tool that fits and set it up: Outlook and Teams activities on the right pipeline condition, a semantic model's refresh failure email, and Activator alerts from a Power BI visual and a KQL queryset.

## How it runs here

This lab is a model, not Fabric. decisions.json lists the five needs, the tools you can use and the fields each tool takes. Change each need's setups so they meet it. Check my work reads the file as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your setups pick the right tool for each of the five needs, with the condition, schedule and actions each needs, as the Outlook and Teams activity, scheduled refresh and Activator pages describe them, checked against a model on this PC.

## What it doesn't prove

- That any alert fires or any message is sent. Nothing runs here.
- Connections and sign-in for the Outlook and Teams activities, and the tenant setting that shows Set alert in Power BI.
- Alerts on Fabric job events in the Real-Time hub, which this exercise leaves out.
- The exact layout and option names of each editor.
