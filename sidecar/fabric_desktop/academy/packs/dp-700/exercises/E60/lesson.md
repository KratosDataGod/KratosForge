# Optimize a pipeline: parallel ForEach, fewer sequential steps

A nightly pipeline loads 30 tables one at a time, then builds two dimensions one after the other, then the fact table. It takes over two and a half hours, and it has to finish in under 45 minutes.

## What you'll do

Make a slow pipeline faster without breaking it: run the ForEach in parallel within the source's limit, and drop the dependencies that make independent steps wait.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the pipeline needs and the definition you change. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your ForEach settings and dependencies run the loads 8 at a time, build both dimensions side by side, and keep FactSales and the email behind what they need, as the ForEach and activity dependency pages describe them, checked against a model on this PC.

## What it doesn't prove

- That the pipeline runs or gets faster: nothing runs here.
- That the source system really allows 8 connections, or how many iterations Fabric runs at once.
- The rest of a real pipeline-content.json, which this cut-down model leaves out.
