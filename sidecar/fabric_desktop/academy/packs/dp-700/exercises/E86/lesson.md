# OneLake settings for a workspace: outside apps, SAS tokens, cache and diagnostics

The Claims workspace needs OneLake opened up to Azure Databricks and a partner app, but security wants the file explorer app blocked. A busy cloud shortcut is costing egress, and the auditors want a record of who reads what.

## What you'll do

Set the OneLake tenant and workspace settings for outside apps, the file explorer, SAS tokens, the shortcut cache and diagnostic events, and predict how each one behaves.

## How it runs here

This lab is a model, not Fabric. onelake.json holds the needs, the tenant settings, the workspace's OneLake settings and your expectations. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your tenant settings, the OneLake tab of the workspace settings, the SAS token setting and your expectations follow the OneLake file explorer, security, shortcut caching, diagnostics and tenant settings pages, checked against a model on this PC.

## What it doesn't prove

- That any setting changes in Fabric. Nothing is set here.
- Whether caching covers ADLS Gen2 or OneLake shortcuts. The shortcuts page lists the types it supports and doesn't name these two.
- Immutability of diagnostic events, the end user identifiers setting and outbound access protection.
- Security groups that a tenant setting can be limited to.
