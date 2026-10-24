# SQL compatibility check

Fabric has several SQL engines, and they don't take the same SQL. The SQL query page checks what you write against the engine you pick, as you type, and lists what that engine doesn't support or may run differently. It is in preview.

The check is **static**: KratosForge reads the SQL; it doesn't run it, doesn't connect to Fabric and uses no capacity. It never changes your SQL. Each finding says what to change, and you decide.

No findings means **no known problem**, not proof that the query runs, or gives the same answer, in Fabric. A query that runs on this PC with DuckDB says nothing about how a Fabric Warehouse runs it.

## Pick the engine

The dialect list next to **Run** is also the engine the SQL is checked against:

| Engine | What the check looks for |
| --- | --- |
| **Spark SQL (Lakehouse)** | T-SQL that isn't Spark SQL (TOP, `[names]`, `@variables`, GO, T-SQL functions), and where running on this PC with DuckDB can differ from Fabric Spark: regular expressions, date patterns, unordered aggregates, decimals, writes that only happen in Fabric |
| **Fabric Warehouse (T-SQL)** | Spark SQL that isn't T-SQL (LIMIT, backticks, `::` casts, the `STRING` and `BOOLEAN` types), what Microsoft documents as unsupported in Fabric Data Warehouse: data types in tables, ALTER TABLE limits, keys that must be NOT ENFORCED, triggers, sequences, recursive queries, FOR XML, transaction limits, and differences such as case-sensitive text and whole-number division |
| **Fabric SQL analytics endpoint (T-SQL)** | As for Warehouse, and that lakehouse tables are read-only through the endpoint: no table DDL, no INSERT, UPDATE, DELETE or MERGE |
| **Fabric SQL database (T-SQL)** | What the SQL database limitations page lists: EXECUTE AS, BACKUP, change data capture, SQL Agent, Service Broker, in-memory and ledger tables, logins, distributed transactions, column names, cross-database and linked-server names |
| **DuckDB (this PC)** | T-SQL syntax, and that DuckDB SQL runs only on this PC |

T-SQL is checked and formatted here but never run on this PC.

## Findings

Findings are underlined in the editor, marked in the gutter and listed in the **Compatibility** panel beside it. Hover an underline, or pick a finding in the panel, to see:

- the **severity** and **rule ID** (for example `SYN-001`);
- what's wrong and **why**, in the engine's terms;
- **what to do** instead;
- the **target** engine, **line and column**;
- the **confidence** (high, medium or low) and a **Source** link to the Microsoft or Apache page the rule comes from.

| Severity | Means |
| --- | --- |
| **Error** | Microsoft documents it as unsupported on that engine, or it isn't that engine's SQL. Change it before running it there. |
| **Warning** | It may run but behave differently than you expect, it's in preview, or it changes or deletes data (DROP, TRUNCATE, DELETE or UPDATE without WHERE). A warning doesn't mean the query is wrong. |
| **Information** | A documented difference worth knowing, such as snapshot isolation or the UTC clock. |
| **Unverified** | The check couldn't decide: the statement couldn't be read, a function isn't one it knows, or the documentation doesn't say. Test it in Fabric. |

The panel shows the engine, the rule set's version, the number of statements, the count of each severity and an outcome: **Known problems**, **Review the warnings**, **Not verified**, or **No known problems found by static checks**. There is no percentage score: a count of rules passed says nothing about whether a query works.

When you run Spark SQL or DuckDB on this PC and the check found differences from Fabric, a note under the results says so. It never stops the run.

Queries over 100,000 characters are checked when you press **Check now** in the panel, so typing never waits on the engine; scripts over 500,000 characters aren't checked. The panel lists the first 1,000 findings; the counts include all of them.

## Export

**Export JSON** saves the report to your Downloads folder as `<query name> compatibility <engine> <date time>.json`. It never replaces a file: a name that's taken gets a number. The file holds the SQL that was checked and its SHA-256 hash, the engine, the rule set version, every finding with its fields, the counts and the outcome (`schema: sql-compat-report@1`). It holds the SQL, so treat it like the query itself.

## What it doesn't do

- Run the SQL, sign in, call Fabric or use capacity.
- Rewrite your SQL.
- Know your tables' column types, your warehouse's collation or your Fabric runtime version; findings that depend on them are warnings or unverified.
- Check every construct. Statements the engine treats as commands (GRANT, DBCC, SHOW, OPTIMIZE and similar) are checked only by the rules that read their words.
- Put SQL text or findings in the security audit log, or put string literals in findings: a password in the SQL never shows in a message.

## Rules

Rule set **2026.10.3**, from Microsoft Learn and the Apache Spark 3.5 reference as they read on 2026-10-17. Each Microsoft page's "last updated" date is kept in `sidecar/fabric_desktop/sqlcompat/profiles.py`. DDL-007 (ALTER TABLE) is an error except ALTER COLUMN, which is a warning because it is in preview. 2026.10.3 stops reading T-SQL's `GRANT`, `DENY` or `REVOKE ... ON SCHEMA::name` (and the other securable classes after `ON`) as a Spark `::` cast, and `UPDATE STATISTICS` (with no `SET`) as an UPDATE without WHERE.

| Rule | Finding | Severity | Engines | Confidence | Source |
| --- | --- | --- | --- | --- | --- |
| SYN-001 | LIMIT isn't T-SQL | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/sql/t-sql/queries/top-transact-sql) |
| SYN-002 | TOP is T-SQL, not the target | error | Spark, DuckDB | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref.html) |
| SYN-003 | [Square brackets] are T-SQL names | error | Spark, DuckDB | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref.html) |
| SYN-004 | @variables are T-SQL | error | Spark | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref.html) |
| SYN-005 | GO isn't SQL | error | Spark, DuckDB | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref.html) |
| SYN-006 | `Backticks` aren't T-SQL | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| SYN-007 | Not checked: couldn't read this statement | unverified | Spark, Warehouse, Endpoint, SQL database, DuckDB | medium | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| SYN-008 | "Double quotes" make a name, not text | warning | Warehouse, Endpoint, SQL database, DuckDB | medium | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| SYN-009 | *construct* is Spark SQL, not T-SQL | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| SYN-010 | *construct* isn't in SQL database T-SQL | error | SQL database | medium | [source](https://learn.microsoft.com/fabric/database/sql/limitations) |
| FN-001 | Not checked: function *name* | unverified | Spark, Warehouse, Endpoint, SQL database, DuckDB | low | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| FN-002 | ISNULL takes one argument in Spark SQL | error | Spark | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref-functions-builtin.html) |
| FN-003 | *name* is a T-SQL function | error | Spark | high | [source](https://spark.apache.org/docs/3.5.1/sql-ref-functions-builtin.html) |
| FN-004 | *name* isn't a T-SQL function | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| FN-005 | *name* is in preview | Warehouse: warning; Endpoint: warning | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| FN-006 | *name* isn't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| TYP-001 | *name* can't be used in a Warehouse table | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/data-types) |
| TYP-002 | The new table would get a *name* column | warning | Warehouse | medium | [source](https://learn.microsoft.com/fabric/data-warehouse/data-types) |
| TYP-003 | *name*(n) keeps more than 6 digits of seconds | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/data-types) |
| TYP-004 | uniqueidentifier is stored as binary | information | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/data-types) |
| TYP-005 | *name*(max) holds at most 16 MB | information | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/data-types) |
| DDL-001 | *name* aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-002 | Computed columns aren't supported | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-003 | Unique indexes aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-004 | *name* must be *form* | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-005 | ## global temp tables aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/temp-tables) |
| DDL-006 | #temp table without distribution | information | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/temp-tables) |
| DDL-007 | *name* | Warehouse: error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DDL-008 | Name '*name*' isn't allowed | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-009 | More than 1,024 columns | error | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-010 | Multi-column statistics aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DDL-011 | CREATE USER isn't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DDL-012 | Not checked: *name* | unverified | Warehouse | low | [source](https://learn.microsoft.com/fabric/data-warehouse/tables) |
| DDL-013 | External tables aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DDL-014 | Column name '*name*' isn't allowed | error | SQL database | high | [source](https://learn.microsoft.com/fabric/database/sql/limitations) |
| DDL-015 | Logins aren't supported | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/database/sql/limitations) |
| EP-001 | The SQL analytics endpoint can't change tables | error | Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DB-001 | *name* isn't supported | error | SQL database | high | [source](https://learn.microsoft.com/fabric/database/sql/limitations) |
| DML-001 | *name* isn't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DML-002 | Recursive CTE | Warehouse: error; Endpoint: error; Spark: unverified | Warehouse, Endpoint, Spark | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DML-003 | Nested CTEs are in preview | warning | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DML-004 | System and user tables in one query | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DML-005 | Not checked: *name* | unverified | Warehouse, Endpoint | low | [source](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area) |
| DML-006 | MERGE and write conflicts | information | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/transactions) |
| DML-007 | Three-part name | Warehouse: information; Endpoint: information; SQL database: warning | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/data-warehouse/query-warehouse) |
| DML-008 | Four-part name | Warehouse: unverified; Endpoint: unverified; SQL database: error | Warehouse, Endpoint, SQL database | medium | [source](https://learn.microsoft.com/fabric/data-warehouse/query-warehouse) |
| TRX-001 | Isolation level can't be changed | warning | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/transactions) |
| TRX-002 | *name* aren't supported | error | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/transactions) |
| TRX-003 | Distributed transactions aren't supported | error | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/fabric/data-warehouse/transactions) |
| TRX-004 | Snapshot isolation and conflicts | information | Warehouse | high | [source](https://learn.microsoft.com/fabric/data-warehouse/transactions) |
| SEM-001 | = NULL is never true | warning | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://learn.microsoft.com/sql/t-sql/language-elements/null-and-unknown-transact-sql) |
| SEM-002 | Text compares case-sensitively | information | Warehouse, Endpoint | high | [source](https://learn.microsoft.com/fabric/data-warehouse/collation) |
| SEM-003 | Whole-number division drops the fraction | warning | Warehouse, Endpoint, SQL database | high | [source](https://learn.microsoft.com/sql/t-sql/language-elements/divide-transact-sql) |
| SEM-004 | NOT IN with a subquery | information | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://learn.microsoft.com/sql/t-sql/language-elements/in-transact-sql) |
| SEM-005 | The clock is UTC | information | Warehouse, Endpoint, SQL database | medium | [source](https://learn.microsoft.com/fabric/database/sql/limitations) |
| SEM-006 | Collation *name* | warning | Warehouse | medium | [source](https://learn.microsoft.com/fabric/data-warehouse/collation) |
| DES-001 | DROP *name* deletes it | warning | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| DES-002 | TRUNCATE deletes every row | warning | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| DES-003 | *name* without WHERE changes every row | warning | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| DES-004 | *name* replaces the table | warning | Spark, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| DES-005 | DROP COLUMN deletes the column's data | warning | Spark, Warehouse, Endpoint, SQL database, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-001 | Changes data in Fabric, not here | warning | Spark | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-002 | Regular expressions differ on this PC | warning | Spark | medium | [source](https://duckdb.org/docs/stable/sql/functions/regular_expressions) |
| LOC-003 | Date pattern is translated on this PC | warning | Spark | medium | [source](https://spark.apache.org/docs/3.5.1/sql-ref-datetime-pattern.html) |
| LOC-004 | *name* gives a different value each run | information | Spark, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-005 | Decimal results can differ | information | Spark | medium | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-006 | *name* has no fixed order | warning | Spark | medium | [source](https://spark.apache.org/docs/3.5.1/sql-ref-functions-builtin.html) |
| LOC-007 | Each run starts fresh | information | Spark | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-008 | DuckDB SQL runs only on this PC | information | DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-009 | *name* reads files on this PC | information | Spark, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |

## How it's built and tested

- `sidecar/fabric_desktop/sqlcompat/`: `profiles.py` describes each engine (statement categories, unsupported types and their alternatives, identifiers, transactions, DDL and DML, semantic differences, confidence and sources); `rules.py` holds the rules, each naming its engines, severity, confidence and source; `analyzer.py` splits the SQL into statements (at `;` and T-SQL's `GO`), reads each with sqlglot and the engine's tokenizer, applies the rules and builds the report. Rules work on tokens first, so they still find problems, at exact positions, in statements sqlglot can't read.
- The engine methods are `sql_analyze`, `sql_compat_profiles` and `sql_compat_export`.
- `sidecar/tests/fixtures/sql_compat_v1.jsonl` holds 197 cases: the SQL, the engine, the expected outcome, the exact set of rules expected and where the first finding is. 36 of them are false-positive guards, SQL that must not be flagged (Spark array indexes, `ISNULL(a, b)` in T-SQL, `UPDATE ... SET x = NULL`, keywords inside strings and comments, triggers in a SQL database). `sidecar/tests/test_sql_compat.py` runs them.
- Changing a rule means moving `RULE_VERSION` on, reviewing the fixtures and updating this page.
