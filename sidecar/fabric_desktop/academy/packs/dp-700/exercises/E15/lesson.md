# Dynamic data masking: pick the mask for each column, and who sees what

Call-center agents query the Customers table in a warehouse to help callers. They need to confirm who is calling without seeing full personal details. Their leads handle disputes and need the real values.

## What you'll do

Choose a dynamic data masking function for each sensitive column, decide who gets UNMASK, and spot the column that masking alone can't protect.

## How it runs here

This lab is a model, not a workspace. settings.json holds what each person needs and the setting you choose for them. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your masks, UNMASK list and salary choice match what each column's need calls for, using the four masking functions a warehouse offers, checked against a model on this PC.

## What it doesn't prove

- That the masks work in a Fabric Warehouse: no T-SQL runs here. In Fabric each mask is an ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...') change.
- The exact masked output for each data type.
- Workspace Admins, Members and Contributors, who see unmasked data by default.
