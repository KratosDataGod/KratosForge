# Shortcuts: a table shortcut, a folder shortcut, and a chain to avoid

SalesLH needs CRM customers, a partner's CSV exports and last year's archived orders without copying any of them. Someone created three shortcuts, and none of them works as intended: two tables don't appear, and the partner folder fails to open.

## What you'll do

Fix three shortcuts in a lakehouse: where each goes, what it points at, what it's called and how it signs in, so tables show up as tables and files as files.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## Before you start

- LM-24

## What this run proves

- Your shortcuts sit where OneLake allows them, use names a table can have, point straight at their sources and use the right sign-in, as the shortcuts page describes it, checked against a model on this PC.

## What it doesn't prove

- That the shortcuts resolve in Fabric: nothing is created here.
- Schema shortcuts in schema-enabled lakehouses, shortcut caching, and other sources such as S3 or Dataverse.
- Whether the callers have permission on the target lakehouses.
