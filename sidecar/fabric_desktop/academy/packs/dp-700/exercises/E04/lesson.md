# OneLake settings: external app access, file explorer, shortcut cache

Analysts can't open OneLake in the file explorer app, and an S3 shortcut is read so often that egress costs are climbing. The settings were changed in a hurry and don't add up.

## What you'll do

Set the tenant settings the OneLake file explorer needs and a workspace's shortcut cache, and predict what the cache keeps.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your settings let the file explorer work and cache the S3 shortcut for a week, and your expectations match how the cache behaves, as the OneLake pages describe it, checked against a model on this PC.

## What it doesn't prove

- That any setting changes in Fabric: nothing is set here.
- Who may change tenant settings, and security groups that a tenant setting can be limited to.
- Other workspace OneLake settings.
