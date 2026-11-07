# OneLake security roles for folders and tables

SalesLH holds sales tables, HR tables and exported files. Analysts and partner auditors have ReadAll on it, and right now that shows them everything, HR salaries included. You're turning on OneLake security roles so each group sees only its part.

## What you'll do

Design OneLake security roles on a lakehouse so each group reads only its folders, and decide what to do with the DefaultReader role.

## How it runs here

This lab is a model, not Fabric. settings.json holds what the team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## Before you start

- LM-24

## What this run proves

- Your roles give each group Read on just its folders, remove the role that gives every ReadAll user all the data, and leave the Contributors out, as the OneLake security model describes it, checked against a model on this PC.

## What it doesn't prove

- That the roles work in Fabric: nothing is set on a lakehouse here.
- Row- and column-level security inside a role, and how they combine across roles.
- Shortcuts, whose access depends on passthrough or delegated identity.
