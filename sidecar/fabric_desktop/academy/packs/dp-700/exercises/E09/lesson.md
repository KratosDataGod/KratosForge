# Deployment pipeline: stages, deployment rules, and what doesn't deploy

The sales team builds in a Development workspace and wants Test and Production behind it. The first draft of the pipeline has two stages, a rule where rules can't go, and a guess at how to repoint the lakehouse. The team also expects the deploy to bring the data with it.

## What you'll do

Set up a three-stage deployment pipeline with the deployment rules that point a notebook and a semantic model at each stage's own data, and say what a deploy doesn't copy.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your stages, deployment rules and answers about what a deploy copies match how deployment pipelines work, as the deployment pipeline pages describe them, checked against a model on this PC.

## What it doesn't prove

- That a pipeline is created or deploys anything: nothing runs here.
- Autobinding, deploying backwards, and deploying only some items.
- Rules for item types other than notebooks and semantic models.
