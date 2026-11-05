# Row-level security: a predicate that shows each region its rows

The sales table is about to be shared with the regional teams. A mapping table says who may see which region; a manager's '*' means every region. Sign-in returns names as people typed them, so 'Ana@Example.com' is the same person as 'ana@example.com'. Someone who isn't in the mapping must see nothing, not everything.

## What you'll do

Write the security predicate that lets each person see only the sales rows of the regions they're mapped to, with an all-regions mapping for managers and nothing for anyone unmapped.

## How it runs here

In a Fabric Warehouse you'd write this predicate as an inline table-valued function and add it to the table with a security policy, and USER_NAME() would supply the signed-in name. Here a DuckDB macro stands in for the function, and the signins table stands in for USER_NAME(), so you can see what four different people would get from one query.

## Before you start

- LM-24

## What this run proves

- For each of four sign-ins, the rows the predicate lets through are exactly the rows that person may see, on this PC with DuckDB.
- The sales, user_regions and signins tables are as they were: the answer filters rows, it doesn't change the data or the mapping.

## What it doesn't prove

- Row-level security in a Fabric Warehouse or the SQL analytics endpoint. There the predicate is an inline table-valued function that a security policy applies to every query, and USER_NAME() supplies the signed-in name; here a DuckDB macro stands in for the function and a table of sign-ins stands in for USER_NAME().
- That the rule is enforced for every query path: this lab checks what the predicate returns, not a security policy that applies it.
- Column-level, object-level or OneLake folder security, which the same objective also covers.
