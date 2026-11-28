# A default domain, a subdomain and who assigns workspaces

Logistics is getting its own domain. Dana owns it and wants to move fast. Eli wants to run the Fleet subdomain. Workspaces should land in Logistics without anyone sorting them by hand, but one of them already belongs to Finance.

## What you'll do

Set up a Logistics domain with a subdomain, its admins and contributors, a default domain for a security group and an assignment by capacity, and predict which domain each workspace ends up in.

## How it runs here

This lab is a model, not Fabric. domains.json holds the people, the workspaces, the needs and the settings you choose. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your roles, contributors, default domain, capacity assignment and predicted workspace domains follow the domain roles, default domain rules and assignment methods on the domains page, checked against a model on this PC.

## What it doesn't prove

- That any domain is created or any workspace assigned in Fabric. Nothing is set here.
- The tenant setting that lets Fabric and domain admins override existing workspace assignments. It's in preview, so this exercise keeps Fleet-Ops off the LOG-F64 capacity.
- Domain settings that override tenant settings, such as certification and a default sensitivity label.
- Whether a subdomain can be a default domain. The domains page doesn't say.
