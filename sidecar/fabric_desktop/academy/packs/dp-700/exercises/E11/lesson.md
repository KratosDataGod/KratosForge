# Share a lakehouse: Read, Read all with the SQL analytics endpoint, Read all with Spark

SalesLH is in demand. A Power BI developer, a data scientist and an auditor from other teams want to read it, and a new engineer needs to load data into it. None of the readers should see the rest of the workspace.

## What you'll do

Choose between sharing a lakehouse (with or without its two Read all boxes) and a workspace role, so four people each get the least access that works.

## How it runs here

This lab is a model, not a workspace. settings.json holds what each person needs and the setting you choose for them. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## Before you start

- LM-24

## What this run proves

- Your choices give each person the share boxes or workspace role their work needs, and no more, as the Fabric sharing and role pages describe them, checked against a model on this PC.

## What it doesn't prove

- That the share works in Fabric: nothing is shared here.
- The DBA's SQL grants for Lena, or OneLake security roles, which decide what a Read-only user sees.
- Other share options, such as subscribing to OneLake events or running Spark.
