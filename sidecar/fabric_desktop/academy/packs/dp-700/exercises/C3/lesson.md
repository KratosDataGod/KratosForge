# Capstone: a governed warehouse release from Git to Production

The Sales warehouse is going to Production for four groups who need very different things. Engineers build and debug it, regional managers may only see their own region, Finance needs everything for invoicing, and Support must only look customers up. Write the release plan, and make sure no setting quietly gives someone more than they need.

## What you'll do

Plan one release of a Sales warehouse end to end: which workspace is in Git, the deployment pipeline's stages and rules, the least workspace roles and sharing permissions for four groups, and the row filter, column grants and masking that give each group exactly what it may see.

## What each group needs

- Engineers develop in Sales-Dev, which is kept in Git on the main branch in the warehouse folder. Changes reach Sales-Test and Sales-Prod only through the deployment pipeline.
- In each stage, Sales model reads that stage's own SalesWH.
- In Sales-Prod, only the people who build and deploy get a workspace role; everyone else gets the warehouse shared with them, with the least permissions that work.
- Engineers build and deploy in Sales-Prod and debug with every row, unmasked. They never manage who has access.
- RegionalManagers read dbo.Sales with T-SQL and reports, each seeing only their own region's rows, and must never see a customer's full email or phone.
- Finance reads every region's sales and the full customer contact details for invoicing, with T-SQL. Finance changes nothing.
- Support looks up a customer's CustomerID, Name and Region with T-SQL, and nothing else: no sales, no contact details, no credit limit.
- Nobody reads the warehouse's files with Spark.

## How it runs here

release.json is a model of the release's settings. Nothing runs and nothing is set in Fabric: the plan is checked against rules that follow how Fabric applies Git, deployment rules, roles, sharing and warehouse security.

## Before you start

- E06
- E09
- E10
- E11
- E12
- E13
- E15

## What this run proves

- Your release plan's Git connections, deployment stages and rules, workspace roles, sharing permissions, row-level security, column grants and masking meet every group's needs, checked against a model of how Fabric applies them, on this PC.

## What it doesn't prove

- Nothing is set in Fabric: no workspace is connected to Git, no pipeline deploys, no T-SQL runs. The Fabric verified version does that in a test workspace.
- The T-SQL itself: the security function and policy, the GRANTs and the MASKED WITH clauses. E12, E13 and E15 grade those.
- The database project, the deploy's safety and monitoring: E08, E44 and E55 cover them.
