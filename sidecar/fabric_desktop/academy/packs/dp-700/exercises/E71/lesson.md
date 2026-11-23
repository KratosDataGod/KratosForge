# Dynamic data masking: work out what each person's SELECT returns

Support wants to know what five people will really see before the Customers table goes live. Ana is a call-center agent, Ben handles card disputes, Dev runs the callback team, Chloe builds the warehouse and Eli, a workspace Member, builds the reports on it. The masks are already on the columns and the UNMASK grants are made. Before anyone queries the table, write down, row by row, what each of them gets back, so a wrong grant or a wrong mask shows up now and not in a customer's complaint.

## What you'll do

Given the masks on a warehouse table, the users' workspace roles and their column-level UNMASK grants, produce exactly what each user's SELECT returns: the output of email(), partial() and default(), and who sees real values in which column.

## How it runs here

In a Fabric Warehouse, dynamic data masking is set per column with ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...'). It changes what a query returns, not what's stored. A user sees a masked column's real value only with UNMASK on it or with CONTROL on the database, and workspace Admins, Members and Contributors have CONTROL by design.

The four functions:

- default(): XXXX for text (fewer X if the column is shorter than 4 characters), 0 for numbers, 1900-01-01 00:00:00.0000000 for date and time types.
- email(): the first letter, then the constant XXX@XXXX.com, so aXXX@XXXX.com.
- partial(prefix, "padding", suffix): the first prefix characters, the padding string as written, the last suffix characters. partial(1, "XXXXXXX", 0) turns 555.123.1234 into 5XXXXXXX.
- random(start, end): a random number in the range, for numeric columns. Nothing here uses it.

No T-SQL runs on this PC, so the lab gives you the table and its settings as four tables: customers, column_masks, users and unmask_grants. You write the query that returns what each user would get back.

## What this run proves

- Your query gives every user's view of every customer: real values only in the columns a user holds UNMASK on or for a workspace role with CONTROL, and otherwise the documented output of email(), partial() and default() for each column's type, on a model of the table with DuckDB on this PC.

## What it doesn't prove

- That the masks and grants work in a Fabric Warehouse: no T-SQL runs here. In Fabric the masks are ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...') and each grant is a GRANT UNMASK statement.
- random(), whose output changes on every query, and values too short for a partial() mask, where Learn says only that part of the prefix or suffix isn't exposed.
- How a masked NULL is shown: no column here holds one.
- That masking keeps a value secret: a user who can query a masked column can still filter on it and infer it. E15 asks what protects such a column.
