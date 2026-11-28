# A four-stage deployment pipeline: workspaces, rules per item type and pairing

The Claims team adds a UAT stage between Test and Production. The first draft has three stages, two workspaces that can't be assigned, and rules that either can't exist or change the kind of source. Omar also expects to deploy to UAT.

## What you'll do

Create a four-stage deployment pipeline, assign a workspace that can go in each stage, add the rules each item type supports, and predict how rules, pairing and deploys behave.

## How it runs here

This lab is a model, not Fabric. pipeline.json holds the needs, the items, the workspaces, the people and the settings you choose. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your stages, workspace assignments, deployment rules, rule types per item and expectations follow the deployment pipeline pages on stages, assigning workspaces, rules, pairing and what a deploy copies, checked against a model on this PC.

## What it doesn't prove

- That a pipeline is created or deploys anything in Fabric. Nothing runs here.
- Whether stages can be added or renamed after the pipeline is created. The get started page says both that they can't and that they can, so it isn't graded.
- Rules for semantic models, paginated reports and Dataflow Gen1. Deploying these Power BI items is in preview.
- Deploying backwards, deploying only some items, and the new deployment pipeline interface, which is in preview.
