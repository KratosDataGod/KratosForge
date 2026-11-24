# Share a warehouse and a semantic model: ReadData, ReadAll and Build

Other teams want the Sales warehouse and the Sales semantic model. An analyst queries with T-SQL, a data scientist reads the files with Spark, an auditor may see only two views, a report author builds on the model and a manager only opens reports. A new engineer has to load tables. Each one should get only what their work needs.

## What you'll do

Share a warehouse and a semantic model with five people from other teams, choosing Read, ReadData, ReadAll or Build for each, and give the one person who writes data the right access.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each person needs and the access you choose for them. Check my work reads it as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your share for each person has the item permissions their work needs and no more, as the warehouse sharing page and the semantic model permissions page describe Read, ReadData, ReadAll, Build and Write, and the person who writes data gets a workspace role instead of a share, checked against a model on this PC.

## What it doesn't prove

- That anything is shared in Fabric. Nothing is set on a warehouse or a model here.
- The T-SQL GRANT that gives the auditor her two views, and row-level security on the model.
- Licenses and capacity, which a report author also needs before they can use Build.
- The Subscribe to events box, which this exercise leaves out.
