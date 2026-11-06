# Column- and object-level security: GRANTs that keep salary out of reach

The HR warehouse has an Employees table and a SalaryHistory table. Managers, analysts and a payroll app each read it through the SQL analytics endpoint. None of them has a role in the workspace, so what they read is exactly what you grant.

## What you'll do

Write the GRANT SELECT plan for three roles on a warehouse, using column-level security to hide pay columns and no grant at all to keep a whole table out of reach.

## How it runs here

This lab is a model, not a workspace. settings.json holds what each person needs and the setting you choose for them. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your grants give each role the tables and columns it needs and no more, with Salary, BankAccount and SalaryHistory out of the analysts' reach, checked against a model on this PC.

## What it doesn't prove

- That the statements run in a Fabric Warehouse: no T-SQL runs here. In Fabric the analysts' grant is GRANT SELECT ON dbo.Employees(EmployeeID, FullName, Department, Email, HireDate) TO [Analysts].
- DENY, and how DENY and GRANT combine.
- Workspace Admins, Members and Contributors, whose roles reach all data whatever the grants say.
