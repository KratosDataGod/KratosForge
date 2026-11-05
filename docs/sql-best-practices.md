# SQL best practices (Preview)

KratosForge checks T-SQL for a Fabric Warehouse, SQL analytics endpoint or SQL database against
best-practice rules on this PC. It reads the SQL with the same reader as the
[SQL compatibility check](sql-compatibility.md). **Nothing runs**, nothing is sent to Fabric, and it
uses no capacity.

The compatibility check says what an engine supports. These rules say what is likely to be slow,
fragile or hard to maintain, even when it runs. The two lists are kept apart, so best-practice
findings never change a compatibility result. A DELETE or UPDATE without WHERE isn't repeated here,
because compatibility rule DES-003 already reports it.

## On the SQL page

When the query's dialect is T-SQL (Fabric Warehouse, SQL analytics endpoint or SQL database), and on
the Warehouse page, a **Best practices** chip sits beside **Compatibility**. Both are checked together
shortly after you stop typing. The chip shows how many findings there are, and is red or amber for the
worst one. It opens the panel beside the editor on its own tab, listing each finding with what to do and
**Line n**, which selects the place in the editor. **About SQ0nn** opens the rule's explanation in
[Each rule explained](#each-rule-explained). Pressing the chip again closes the panel. Under each
finding, **Dismiss** hides it for you, and in Professional or Guided guidance **Explain** and **Show
example** show its section of this page in the panel ([Learn](learn.md#beside-a-finding)).

The governance policies in force for the open project tune the rules, read once each time you open
the page (`governance_practice_settings`). A rule a policy turns off isn't shown, and a policy can change
a rule's severity. With no project open, or when the policies can't be read, every rule is on as built,
and the panel says so. Like the other Preview features, the chip is hidden when previews are hidden.

## Rules

| Id | Severity | Rule | What to do |
| --- | --- | --- | --- |
| SQ001 | Warning | `SELECT *` in a view, procedure or function | Name the columns |
| SQ002 | Warning | Function on a column in `WHERE` (`YEAR(order_date) = 2026`) | Compare the column to a range |
| SQ003 | Warning | `varchar(max)` or `varbinary(max)` column in `CREATE TABLE` | Give the column the length it needs |
| SQ004 | Info | `SELECT ... INTO` a table (not a `#temp` table) | `CREATE TABLE` with explicit types, then `INSERT` |
| SQ005 | Info | Table named without its schema | Write `dbo.sales`, not `sales` |
| SQ006 | Warning | `CROSS JOIN`, or a `JOIN` with no `ON` | Join on the key columns |
| SQ007 | Warning | Cursor or `WHILE` loop | Write one set-based statement |
| SQ008 | Info | `ORDER BY` in a view, or `TOP` without `ORDER BY` | Order in the final query |
| SQ009 | Info | `DISTINCT` with `GROUP BY` | Drop the `DISTINCT` |
| SQ010 | Warning | `INSERT ... SELECT *` | List the columns on both sides |

A plain `SELECT *` to look at a table is fine and isn't reported. CTE names, `#temp` tables,
`@variables` and the alias in `UPDATE alias ... FROM table AS alias` aren't reported as tables
without a schema.

## Each rule explained

Each finding in the panel links to its rule here with **About SQ0nn**: why the rule exists, an
example and, where it applies, when the finding is safe to leave. Each rule also has the principle
behind it, its impact, and how to check the change worked. Under the title are the rule's severity,
its area and how important it is:

- **Critical**: it can leak a secret, lose data or put wrong data in place. Fix it before the code
  is shared or scheduled.
- **Recommended**: sound practice that most code should follow.
- **Educational**: a habit worth knowing, with little risk when it's skipped.
- **Advanced**: a tradeoff that needs judgment, where the exceptions matter.

This only explains the rule. The severity, and any policy your organization sets, decide
what the panel shows and requires.

### SQ001

**`SELECT *` in a view, procedure or function** · Warning · Maintenance · Recommended

Why: the object's columns change whenever the table's do. A report or caller that expects the old
columns breaks, and a view can keep returning the columns it had when it was created.

```sql
-- Before
CREATE VIEW dbo.v_sales AS SELECT * FROM dbo.sales;
-- After
CREATE VIEW dbo.v_sales AS SELECT order_id, order_date, amount FROM dbo.sales;
```

Principle: A view's columns are a promise to its callers. Name them.

Impact: Reliability. Reports and callers break when the table's columns change.

How to check: The view's columns in the explorer are the ones you named, and they stay the same after a column is added to the table.

### SQ002

**Function on a column in `WHERE`** · Warning · Performance · Recommended

Why: the engine skips files and row groups by comparing a column's values with your filter. Wrapped
in a function, the column can't be compared directly, so every row is read.

```sql
-- Before
WHERE YEAR(order_date) = 2026
-- After
WHERE order_date >= '2026-01-01' AND order_date < '2027-01-01'
```

When it's fine: small tables, or a function on the other side of the comparison
(`order_date >= DATEADD(day, -7, GETDATE())`), which isn't reported.

Principle: Compare the column itself with a value or a range. Put any function on the value's side, so the engine can skip the data it doesn't need.

Impact: Performance. Every row is read, whatever the filter keeps.

How to check: Where Try the fix is offered, Compare runs both versions read-only and shows whether the rows match, with each run's measured time.

### SQ003

**`varchar(max)` or `varbinary(max)` column** · Warning · Performance · Recommended

Why: in a Fabric Warehouse, MAX columns are stored and read less efficiently than sized ones, and
tools that read the table can't plan for the length.

```sql
-- Before
CREATE TABLE dbo.customer (name varchar(max));
-- After
CREATE TABLE dbo.customer (name varchar(200));
```

When it's fine: values that really can be longer than 8,000 bytes, like JSON documents.

Principle: Size each column for the values it holds.

Impact: Performance. MAX columns are stored and read less efficiently, and tools can't plan for their length.

How to check: `SELECT MAX(LEN(name)) FROM dbo.customer` fits inside the size you chose.

### SQ004

**`SELECT ... INTO` makes a table with guessed column types** · Info · Maintenance · Educational

Why: the new table takes its types from the query, so a change to the query or the data can change
the table's types. `#temp` tables aren't reported.

```sql
-- Before
SELECT order_id, amount INTO dbo.big_orders FROM dbo.sales WHERE amount > 1000;
-- After
CREATE TABLE dbo.big_orders (order_id int, amount decimal(18,2));
INSERT INTO dbo.big_orders (order_id, amount) SELECT order_id, amount FROM dbo.sales WHERE amount > 1000;
```

Principle: Declare a table's column types. Don't let a query guess them.

Impact: Maintenance. A change to the query or the data can change the table's types.

How to check: The table's columns in the explorer have the types you declared.

### SQ005

**Table named without its schema** · Info · Maintenance · Educational

Why: `sales` means whatever the caller's default schema has, which differs between people and
service accounts. `dbo.sales` always means the same table.

Principle: Name the schema with every table, so the query means the same thing whoever runs it.

Impact: Reliability. Two people, or a person and a service account, can read different tables.

How to check: Every table name has a schema. Where Try the fix is offered, Compare shows both versions return the same rows.

### SQ006

**`CROSS JOIN`, or a `JOIN` with no `ON`** · Warning · Performance · Recommended

Why: every row is matched with every row of the other table. Two tables of 10,000 rows give
100,000,000 rows.

```sql
-- Before
SELECT o.order_id, c.name FROM dbo.orders o JOIN dbo.customer c
-- After
SELECT o.order_id, c.name FROM dbo.orders o JOIN dbo.customer c ON c.customer_id = o.customer_id
```

When it's fine: a deliberate product with a small table, such as a date spine or a list of scenarios.

Principle: Every join says how rows match.

Impact: Performance and correctness. The row count is the product of both tables.

How to check: The join has an `ON`, and the result's row count is what you expect, not the two tables' counts multiplied.

### SQ007

**Cursor or `WHILE` loop** · Warning · Performance · Advanced

Why: a loop handles one row or batch at a time. A distributed engine is fastest when one statement
works on the whole set.

```sql
-- Before: a cursor that updates each order's status
-- After
UPDATE o SET status = 'late'
FROM dbo.orders AS o
WHERE o.due_date < CAST(GETDATE() AS date) AND o.shipped_date IS NULL;
```

When it's fine: loops over a handful of items, like the tables to load.

Principle: Work on the whole set in one statement.

Impact: Performance. Rows are handled one at a time or one batch at a time.

How to check: On a copy of the table, run the loop and the single statement, then check they changed the same rows. Compare the measured times of the two runs.

### SQ008

**`ORDER BY` in a view, or `TOP` without `ORDER BY`** · Info · Reliability · Recommended

Why: a view's `ORDER BY` doesn't order the rows its callers get, and `TOP 10` without `ORDER BY`
returns any ten rows, which can change from one run to the next.

```sql
-- Before
SELECT TOP 10 customer_id, amount FROM dbo.sales;
-- After
SELECT TOP 10 customer_id, amount FROM dbo.sales ORDER BY amount DESC;
```

Principle: Order rows where they are returned, and say which rows you want.

Impact: Reliability. The rows, or their order, can change from one run to the next.

How to check: Running it twice returns the same rows in the same order.

### SQ009

**`DISTINCT` with `GROUP BY`** · Info · Maintenance · Educational

Why: `GROUP BY` already returns one row per group, so `DISTINCT` only adds a step.

Principle: Ask for one row per group once.

Impact: Performance. A small extra step.

How to check: Without `DISTINCT`, the query returns the same rows.

### SQ010

**`INSERT ... SELECT *`** · Warning · Reliability · Critical

Why: columns are matched by position. Adding a column to either table, or changing their order,
puts values in the wrong columns without an error when the types happen to fit.

```sql
-- Before
INSERT INTO dbo.sales_archive SELECT * FROM dbo.sales;
-- After
INSERT INTO dbo.sales_archive (order_id, order_date, amount)
SELECT order_id, order_date, amount FROM dbo.sales;
```

Principle: Match columns by name, never by position.

Impact: Data quality. Values land in the wrong columns without an error.

How to check: The `INSERT` names its columns, and a few rows of the target table have each value in the right column.

## Settings

A governance policy can name these rules in **Checked by**, like `SQ001, SQ005` ([governance rules](governance.md#the-rules)). Each rule can be turned off or given another severity, which is how governance templates tune them:
`{"SQ005": {"enabled": false}, "SQ002": {"severity": "Error"}}`.

## Engine methods

| Method | What it does | Changes |
| --- | --- | --- |
| `sql_practices(query, target?, settings?)` | Findings for T-SQL on `warehouse` (default), `endpoint` or `sqldb`, worst first, with statement, line and column | Nothing |
| `sql_practice_rules()` | The rules above, for a template to list | Nothing |
