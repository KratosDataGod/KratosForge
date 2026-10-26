# SQL best practices (Preview)

KratosForge checks T-SQL for a Fabric Warehouse, SQL analytics endpoint or SQL database against
best-practice rules on this PC. It reads the SQL with the same reader as the
[SQL compatibility check](sql-compatibility.md). **Nothing runs**, nothing is sent to Fabric, and it
uses no capacity.

The compatibility check says what an engine supports. These rules say what is likely to be slow,
fragile or hard to maintain, even when it runs. The two lists are kept apart, so best-practice
findings never change a compatibility result. A DELETE or UPDATE without WHERE isn't repeated here,
because compatibility rule DES-003 already reports it.

The engine is in place. The screen (a Best practices panel on the SQL page) comes next, once the
mockup is approved.

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

## Settings

A governance policy can name these rules in **Checked by**, like `SQ001, SQ005` ([governance rules](governance.md#the-rules)). Each rule can be turned off or given another severity, which is how governance templates tune them:
`{"SQ005": {"enabled": false}, "SQ002": {"severity": "Error"}}`.

## Engine methods

| Method | What it does | Changes |
| --- | --- | --- |
| `sql_practices(query, target?, settings?)` | Findings for T-SQL on `warehouse` (default), `endpoint` or `sqldb`, worst first, with statement, line and column | Nothing |
| `sql_practice_rules()` | The rules above, for a template to list | Nothing |
