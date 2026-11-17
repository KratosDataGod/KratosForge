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

Findings are underlined in the editor, marked in the gutter and listed in the **Compatibility** panel beside it. A chip beside **Format SQL** shows the outcome (**No known problems**, **2 warnings**, **1 error**, **Not verified** when some of the SQL couldn't be checked, **Not checked** when the check didn't run); only **No known problems** is green. The panel opens by itself when the check finds an error or a warning or couldn't verify part of the SQL, and the chip opens or closes it at any time. Closed by hand, it stays closed until the findings change. Hover an underline, or pick a finding in the panel, to see:

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

## Decimal results on this PC (LOC-005)

Spark SQL run on this PC gives Spark's decimal type and value for division, multiplication, addition, subtraction, `%`, `pmod`, `div`, `avg`, `sum`, `round`, `bround`, `ceil` and `floor`. These follow Spark's own rules: DecimalPrecision with `spark.sql.decimalOperations.allowPrecisionLoss` on, the default. A division by zero gives NULL, as Spark does with ANSI mode off. For example, `CAST(1 AS DECIMAL(10,2)) / 3` is `decimal(14,6)` 0.333333 here, as in Fabric Spark. These results are checked against Spark's published rules in `sidecar/tests/test_spark_decimal_rules.py`. They haven't been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

These cases can still differ, and LOC-005 notes them:

| Case | Spark | This PC |
| --- | --- | --- |
| A sum, difference or product past 38 digits, such as two `DECIMAL(38,18)` columns multiplied | Rounds the scale down, to no fewer than 6 places, for example `decimal(38,6)`; NULL when the result doesn't fit | DuckDB's own type, for example `DECIMAL(38,36)`; the query fails when the result doesn't fit |
| A division whose dividend, scaled, needs more than 38 digits | The exact quotient | Worked out through DOUBLE and cast to Spark's type, so the last digits can differ |
| `div` past 38 digits | The exact quotient | Worked out through DOUBLE |
| `div` whose quotient is past the BIGINT range | Wraps around (ANSI off) | NULL |
| ANSI mode on | A division by zero is an error | NULL |

## Dates and times on this PC (LOC-003)

Spark SQL run on this PC gives Spark's values and types for its date and time functions, with ANSI mode off as Fabric runs it. Each case below is checked against Spark 3.5's documented behaviour in `sidecar/tests/test_spark_datetime.py`. None has been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `date_format`, `from_unixtime` with a pattern | Every pattern letter Spark takes is written as Java writes it, in the session's time zone: fractions (`SSS`), quarters (`Q` to `QQQQ`), `k` and `K` hours, offsets (`X`, `x`, `Z`, `O`), the zone's id (`VV`), and quoted text (`'T'`). Text that isn't a timestamp gives NULL |
| `to_timestamp`, `to_date`, `unix_timestamp`, `to_unix_timestamp` with a pattern | Text is read with Spark's pattern, in the session's time zone unless it has an offset (`XXX` reads `Z` and `+05:30`). `yy` reads 00 to 99 as 2000 to 2099. A pattern without a year reads the date as 1970-01-01. Text that doesn't match gives NULL |
| `to_timestamp`, `to_timestamp_ltz`, `try_to_timestamp`, `make_timestamp`, `timestamp_seconds`, `from_utc_timestamp`, `to_utc_timestamp`, `date_trunc` | Spark's `timestamp`, not `timestamp_ntz`, so the result can be written to a Delta table. `to_timestamp_ntz` and `make_timestamp_ntz` give `timestamp_ntz` |
| `year`, `month`, `day`, `dayofyear`, `weekofyear`, `dayofweek`, `weekday`, `quarter`, `hour`, `minute`, `second`, `datediff`, `unix_date` | An `int`, as in Spark |
| `date_add`, `date_sub`, `datediff`, `add_months`, `last_day`, `months_between` | Spark's dates at month ends and the leap day (`add_months(DATE'2019-02-28', 1)` is `2019-03-28`), and NULL for text that isn't a date |
| `trunc`, `date_trunc` | NULL for a unit the function doesn't take. `trunc` takes `YEAR`, `YYYY`, `YY`, `QUARTER`, `MONTH`, `MM`, `MON` and `WEEK`; `date_trunc` also takes `DAY`, `DD`, `HOUR`, `MINUTE`, `SECOND`, `MILLISECOND` and `MICROSECOND` |
| `next_day` | Takes `MO`, `MON` or `MONDAY` and so on for each day, in any case. Any other text gives NULL |
| `make_date`, `make_timestamp` | NULL for a day that doesn't exist, such as `make_date(2023, 2, 29)` |

Patterns that Spark refuses are refused here by name, and nothing runs: the week-based letters (`Y`, `w`, `W`, `u`, `e`, `c`), `F`, optional sections (`[ ]`) and letters that aren't pattern letters. These are refused too, because this PC can't give Spark's text for them:

- Zone names (`z`, such as `EST`). Use `VV` for the zone's id or `XXX` for its offset.
- When reading text, `G`, `Q`, `k`, `K`, `O` and `z`.

These cases can still differ. LOC-003 warns wherever a pattern is used:

| Case | Spark | This PC |
| --- | --- | --- |
| Text with more fraction digits than the pattern's `S` letters, such as `.123` with `ss.SS` | NULL | Read, up to 6 digits |
| Text with one digit where the pattern has two, such as `2024-2-9` with `yyyy-MM-dd` | Not read: see the next row | Read |
| Text the pattern doesn't read the Spark 3 way, with `spark.sql.legacy.timeParserPolicy` at its default `EXCEPTION` | Stops with `SparkUpgradeException` when Spark 2's parser would have read it | NULL, as with the policy `CORRECTED`. The setting isn't read here |

A cast of text that is only a year or a year and month, such as `CAST('2024-02' AS DATE)` or `to_timestamp('2024')`, also still differs. Spark reads it as the month's or the year's first day; here it is NULL. No rule warns about it yet.

## Aggregates on this PC

Spark SQL and the DataFrame API run on this PC give Spark's values and types for its aggregates, with ANSI mode off as Fabric runs it. Each case below is checked against Spark 3.5's documented behaviour in `sidecar/tests/test_spark_aggregates.py`. None has been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `collect_list`, `collect_set`, `array_agg` | NULLs are left out, and no value at all (no rows, or only NULLs) gives `[]`, not NULL, as an aggregate, with `FILTER` and over a window. The order of the values isn't fixed (LOC-006) |
| `first`, `last` | NULLs count unless `ignoreNulls` is true (`first(x, true)`, `first(x) IGNORE NULLS`, `F.first(x, ignorenulls=True)`) |
| `count(a, b)`, `count(DISTINCT a, b)` | Count the rows where none of the columns is NULL |
| `percentile`, `median`, `percentile_cont` | Interpolated, and a `double` (an array of doubles for an array of percentages), whatever the column's type. `percentile_disc` and `percentile_approx` give a value from the column, in its type |
| `stddev`, `stddev_samp`, `variance`, `var_samp` | NULL for a group of one row; `stddev_pop` and `var_pop` give 0.0 |
| `sum` of an `int` or `bigint`, `count_if`, `regr_count` | A `bigint`, in the schema and in `typeof`. A table written from them has a `bigint` column |
| `grouping` | A `tinyint`; `grouping_id` is a `bigint` |
| `GROUP BY a, b WITH ROLLUP`, `WITH CUBE` | Run as `ROLLUP(a, b)` and `CUBE(a, b)`. A NULL in the data and a subtotal row both show NULL; `grouping(a)` is 1 only for the subtotal |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `approx_count_distinct` | Spark's HyperLogLog++ estimate, within the `rsd` given (0.05 by default) | DuckDB's own estimate. `rsd` is taken but doesn't change it, and the estimate can be off by one even for a few distinct values. Use `count(DISTINCT x)` when the count must be exact |
| `percentile_approx`, `approx_percentile` | Spark's estimate, within its `accuracy` | DuckDB's exact pick from the column's values. `accuracy` doesn't change it, so on many values the result can differ from Spark's slightly |
| `collect_list` and `collect_set` order | The order the rows arrive in, which can change between runs | DuckDB's order, which can also change. Sort with `array_sort` before comparing |
| `GROUP BY a, b GROUPING SETS ((a, b), a)` and `grouping__id` | Hive's form runs | Refused, and nothing runs. Write `GROUP BY GROUPING SETS ((a, b), (a))` and `grouping_id()` |
| A `sum` past a `bigint`'s range | Wraps around | Kept as a 128-bit number; writing it to a table stops with a message |
| `histogram_numeric` | Spark's streaming bins | Refused by name |

## Local T-SQL changes

When a project runs T-SQL on this PC, `MERGE`, `UPDATE`, `DELETE` and `INSERT` on a database table give a warehouse's rows, or stop by name. Each case below is checked against T-SQL's reference.

| What | Here, as in a warehouse |
| --- | --- |
| `MERGE` with `WHEN MATCHED`, `WHEN NOT MATCHED [BY TARGET]` and `WHEN NOT MATCHED BY SOURCE` | The same rows. The result is the number of rows inserted, updated and deleted, as `@@ROWCOUNT` would be |
| A `MERGE` in which more than one source row would update or delete the same row | Refused with T-SQL's words, "The MERGE statement attempted to UPDATE or DELETE the same row more than once", and nothing changes. A source row that a `WHEN MATCHED ... AND` condition leaves out doesn't count, and source rows that are inserted can repeat |
| `UPDATE t SET ... FROM dbo.a AS t JOIN dbo.b AS s ON ...` and `DELETE t FROM dbo.a AS t JOIN ...` | The table named in `FROM` is the one changed, and each inner join is a filter on it, so only the rows that join change |
| `UPDATE dbo.a SET ... FROM dbo.a JOIN dbo.b ON ...` | The `dbo.a` in `FROM` is the table being changed, not a second copy of it, so rows that don't join stay as they are |
| `INSERT`, `UPDATE`, `DELETE` | The result is the number of rows changed |

What can still differ:

- An `UPDATE ... FROM` in which a row of the table joins more than one row is refused. T-SQL takes the value from any one of them, and here that choice isn't made for you. Keep one row per key in `FROM`, or use a `MERGE`.
- `OUTPUT` and `@@ROWCOUNT` are refused by name. Each statement's row count is its result instead.
- An `UPDATE` or `DELETE` whose `FROM` has an outer join (`LEFT`, `RIGHT`, `FULL`), `JOIN ... USING`, or the changed table after another one, is refused by name.
- On one of the project's Delta tables, `MERGE`, `UPDATE` and `DELETE` run through delta-rs, which has its own rules: see [Local data](local-data.md).
## Local T-SQL ordering and window functions

When a project runs T-SQL on this PC, NULLs, ties and window functions give a warehouse's answers. Each case below is checked against T-SQL's reference.

| What | Here, as in a warehouse |
| --- | --- |
| `NULL` in an ordering | The smallest value: first in ascending order, last in descending order. This holds in `ORDER BY`, `TOP` (with or without `WITH TIES`), `OFFSET ... FETCH`, a window's `ORDER BY`, `STRING_AGG ... WITHIN GROUP` and `GROUP BY ... ORDER BY` |
| `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `NTILE` | Ties share a rank: `RANK` skips after a tie (1, 1, 3), `DENSE_RANK` doesn't (1, 1, 2). `PARTITION BY` puts NULLs in one partition |
| `CUME_DIST`, `PERCENT_RANK` | Ties share a value, as in T-SQL |
| `LAG`, `LEAD` | `NULL` past the edge of the partition, or the default you give: `LAG(x, 1, 0)` |
| `SUM`, `COUNT`, `MAX` and the other aggregates `OVER (ORDER BY ...)` | The default frame is `RANGE`, so rows that tie share a running total. Write `ROWS UNBOUNDED PRECEDING` for one total per row |
| `PERCENTILE_CONT` | A `float`, with `NULL`s left out, for a decimal column too: 0.25 of `1.25`, `2.50` and `4.00` is `1.875` |
| `PERCENTILE_CONT` and `PERCENTILE_DISC` with `ORDER BY ... DESC` | p counts from the largest value: 0.25 of 1 to 5 descending is `4`. `PERCENTILE_DISC` keeps its value's type |

What can still differ:

- `PERCENTILE_DISC` with `ORDER BY ... DESC` on a date or text column is refused by name. Run it in a Fabric Warehouse.
- Rows that tie in an `ORDER BY` can come back in any order, in a warehouse as here. Add a column that tells them apart when the order matters.

## Local T-SQL dates and times

When a project runs T-SQL on this PC, these date and time functions are checked against the answers in T-SQL's reference, at their edges: month-end, the leap day, and the week's start (Sunday, Fabric's `DATEFIRST 7`).

| Function | Here, as in a warehouse |
| --- | --- |
| `DATEADD` | Month and year steps end on the last day of a shorter month: `DATEADD(month, 1, '2024-01-31')` is `2024-02-29`, `DATEADD(year, 1, '2024-02-29')` is `2025-02-28`. A minute or hour added to a `date` is an error, as in T-SQL |
| `DATEDIFF` | Boundaries crossed, not whole spans: `DATEDIFF(year, '2023-12-31', '2024-01-01')` is `1`, `DATEDIFF(week, Saturday, Sunday)` is `1`. The answer is an `int`, and a count past an int's range stops with T-SQL's "The datediff function resulted in an overflow" (`DATEDIFF(millisecond, ...)` over more than 24 days) |
| `DATEDIFF_BIG` | The same counts as a `bigint`, with no overflow |
| `EOMONTH` | The month's last day, with or without months to add: `EOMONTH('2024-01-31', 1)` is `2024-02-29` |
| `DATEFROMPARTS` | A date, an error for a day that doesn't exist (`2023, 2, 29`), `NULL` for a `NULL` part |
| `FORMAT` | .NET's date formats in `en-US`, on a `date` or a date-and-time column |
| `CONVERT` to text, styles 101, 103, 112, 120, 121, 126 | `02/29/2024`, `29/02/2024`, `20240229`, `2024-02-29 13:04:05`, `2024-02-29 13:04:05.123000`, `2024-02-29T13:04:05.123000`. A `date` gives only its date in 120, 121 and 126, and 126 leaves off a fraction of zero |
| `CONVERT` and `TRY_CONVERT` from text, the same styles | The date the style reads (`'29/02/2024'` with 103), to a `date`, `datetime` or `datetime2`. A date that doesn't exist is an error with `CONVERT` and `NULL` with `TRY_CONVERT`. Styles 120, 121 and 126 read ISO text with or without its time |

What can still differ:

- A date-and-time here is a warehouse's `datetime2(6)`: six digits of fraction (`.123000`), which is also the most a Fabric Warehouse column keeps. T-SQL's default `datetime2` has seven.
- Any other `CONVERT` date style runs as translated, but its values haven't been checked. The run's `translation.notes` says so ("CONVERT style 104 runs as a translation that hasn't been checked ...").
- `CAST(a_datetime AS VARCHAR)` with no style, and `nanosecond` in `DATEADD`, `DATEDIFF` and `DATETRUNC`: see [Local data](local-data.md#t-sql).

## Local T-SQL text

When a project runs T-SQL on this PC, text compares as it does with a Fabric Warehouse's default collation, `Latin1_General_100_BIN2_UTF8`: by code point, so case counts and `'B'` sorts before `'a'`, and with ANSI padding, so trailing spaces don't count. These are checked against T-SQL's reference:

| What | Here, as in a warehouse |
| --- | --- |
| `=`, `<>`, `<`, `IN`, `JOIN ... ON` | `'a' = 'a  '` is true and `'a' = 'A'` is false. Only spaces are taken off: `'a' = 'a' + NCHAR(160)` (a no-break space) is false |
| `LIKE` | Case counts. The text's trailing spaces don't count, but the pattern's do: `'abc  ' LIKE 'abc'` is true, and `'abc' LIKE 'abc '` is false |
| `ORDER BY`, `STRING_AGG(...) WITHIN GROUP (ORDER BY ...)` | Code point order (`A`, `B`, `a`, `b`). `STRING_AGG` skips `NULL`s, and is `NULL` over no rows |
| `LEN`, `DATALENGTH` | `LEN('ab  ')` is `2`, and `DATALENGTH('ab  ')` is `4`. `DATALENGTH` counts UTF-8 bytes (`'é'` is `2`) and two per character for `N'...'` |
| `CHARINDEX`, `PATINDEX` | Case counts, and the answer is `0` when the text isn't found. A `CHARINDEX` start of 0 or below searches from the start, and an empty search text gives `0` |
| `CONCAT`, `CONCAT_WS`, `+` | `CONCAT` skips `NULL`, and `+` gives `NULL`. A number written with a point keeps its digits (`CONCAT('a', 1.50)` is `a1.50`), and a `bit` is `1` or `0` |
| `TRIM`, `LTRIM`, `RTRIM`, `REPLACE` | Spaces only, or the characters you give. `REPLACE` matches case and leaves the text alone for an empty search |
| `COLLATE` | A binary collation (`..._BIN2`, `..._BIN`) runs as written, since it's how text compares here already |

What still differs:

- `GROUP BY`, `DISTINCT`, `UNION`, `INTERSECT` and `EXCEPT` keep `'a'` and `'a  '` apart, where T-SQL counts them as one value. A query that groups text says so in `translation.notes`. Group on `RTRIM(...)` to get T-SQL's groups.
- Any other collation (`COLLATE Latin1_General_100_CI_AS_KS_WS_SC_UTF8`, `SQL_Latin1_General_CP1_CI_AS`) is refused by name. Compare `UPPER(...)` on both sides to ignore case.
- `PATINDEX` needs its pattern written out as text, and is refused otherwise.

These aren't checked against a warehouse yet, since T-SQL's reference doesn't say. The warehouse live check, flow 17 of the [smoke test](product/smoke-test/README.md#flow-17-the-warehouse-live-check), runs them, and each section's other cases, against a test warehouse:

- `LIKE` with a wildcard where the text has trailing spaces. Here a match counts if the text matches as written, or with its trailing spaces removed. So `'abc  ' LIKE 'abc_'` is false, and `'abc  ' LIKE 'abc__'` is true.
- `CHARINDEX` with a `NULL` start. Here the answer is `NULL`.

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
| LOC-003 | Date pattern reads some text differently on this PC | warning | Spark | medium | [source](https://spark.apache.org/docs/3.5.1/sql-ref-datetime-pattern.html) |
| LOC-004 | *name* gives a different value each run | information | Spark, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-005 | Decimal results can differ | information | Spark | medium | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-006 | *name* has no fixed order | warning | Spark | medium | [source](https://spark.apache.org/docs/3.5.1/sql-ref-functions-builtin.html) |
| LOC-007 | Each run starts fresh | information | Spark | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-008 | DuckDB SQL runs only on this PC | information | DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |
| LOC-009 | *name* reads files on this PC | information | Spark, DuckDB | high | [source](https://github.com/KratosDataGod/KratosForge/blob/main/docs/sql-compatibility.md) |

## Where else will it run? (engine only, no screen yet)

The check above asks "will this run on the engine I picked?". The portability report
(`sidecar/fabric_desktop/sql_portability.py`) asks the other question: SQL written for one platform,
graded for each of the others (DuckDB on this PC, Spark SQL in a Fabric lakehouse, Fabric Warehouse,
Databricks SQL, Snowflake SQL and PostgreSQL), statement by statement:

| Grade | Means |
| --- | --- |
| Compatible | It reads the same there as written, and no known problem applies. |
| Translated | It runs there once rewritten. The report shows the rewrite; your SQL isn't changed. |
| Unverified | Part of it can't be checked statically (a function sqlglot doesn't know, or a statement it can only keep as text). Test it there. |
| Partial | It runs there, but a known difference or risk applies. The reasons say which. |
| Unsupported | It uses something that platform doesn't have. |

A platform's grade is its worst statement's. sqlglot writes the SQL for each platform and refuses what
it can't express, instead of passing it through. The three Fabric targets then get this page's rules,
applied to the rewritten SQL, and Snowflake SQL on DuckDB gets the Snowflake labels from
[Engines](engines.md). Databricks, Snowflake and PostgreSQL have no rules of their own here yet, so for
them only the rewrite is checked, and the report says so. Anything only Snowflake has (streams, tasks,
stages, time travel and the rest) is unsupported everywhere else.

QUALIFY becomes a filter in an outer query on Spark SQL, Fabric Warehouse and PostgreSQL. When the
query also has ORDER BY, LIMIT or OFFSET, those move to the outer query too, so they still apply after
the filter, as written. When that can't be done safely, the grade is unverified and the reason says
why: a selected column with no name, or two with the same name; SELECT * sorted by an expression; or a
window that names a table's column with the same name as an output alias. With SELECT * the rewrite
also returns its helper column `_w`, so the grade is partial.
Fabric Warehouse has QUALIFY too, so there the rewrite is one way to run it, not the only one.

Like the check above, it never runs the SQL or connects anywhere, and "compatible" is not proof that
two platforms give the same answer. Comparing answers needs both runs.

## Local T-SQL tables

When a project runs T-SQL on this PC, tables are made and emptied as in a Fabric Warehouse, and what a warehouse doesn't have is refused by name. This was checked against Microsoft Learn's Fabric Data Warehouse pages for tables, temporary tables, IDENTITY and the T-SQL surface area, as they read on 2026-11-17.

| What | Here, as in a warehouse |
| --- | --- |
| `VARCHAR(n)`, `CHAR(n)`, `VARBINARY(n)`, `BINARY(n)` in `CREATE TABLE` | At most n bytes, as UTF-8 text: `'ééé'` doesn't fit in `VARCHAR(5)`. A longer value stops the `INSERT` or `UPDATE` with T-SQL's "String or binary data would be truncated in table 'dbo.t', column 'v'.", and nothing changes. Trailing blanks past the length don't count, as in T-SQL. `VARCHAR(MAX)` has no limit |
| `NOT NULL` | Kept: a `NULL` stops the `INSERT` |
| `#temp` tables | Last until the project is closed (the session), and are gone when it's opened again. `WITH (DISTRIBUTION = ROUND_ROBIN)`, which a warehouse takes only on a #temp table, is accepted and changes nothing here |
| `CREATE TABLE ... AS SELECT`, `SELECT ... INTO` | A table with the select's columns and types: `1` is an `int`, `1.5` a `decimal(2,1)`, a cast keeps its type |
| `DROP TABLE IF EXISTS`, `TRUNCATE TABLE` | As in a warehouse |

Refused by name:

- `IDENTITY`. A warehouse takes only `BIGINT IDENTITY`, with no seed or increment, and numbers rows uniquely but not always in order. The local engine doesn't number rows that way yet, so give the column its values in the `INSERT`. `SET IDENTITY_INSERT` and `DBCC` are refused too.
- `##global` temporary tables, which a warehouse doesn't have either.
- `WITH (DISTRIBUTION = ...)` on a table that isn't a #temp table.
- `ALTER TABLE ... ADD` of a `VARCHAR(n)`, `CHAR(n)`, `VARBINARY(n)` or `BINARY(n)` column, since the local engine can't hold an added column to its length. Declare it in `CREATE TABLE`. Adding a column of another type, or `VARCHAR(MAX)`, works.

What can still differ:

- A table made by `CREATE TABLE ... AS SELECT` or `SELECT ... INTO` keeps no text length and no `NOT NULL`: every column accepts `NULL` and text of any length. In T-SQL, a column copied from a `NOT NULL` column stays `NOT NULL`.
- `CHAR(n)` isn't padded with spaces to n characters. `LEN` gives the same answer either way, but `DATALENGTH` of a stored `CHAR` value doesn't.
- Trailing blanks past a text column's length are kept here. A warehouse drops them, so `DATALENGTH` differs.
- A #temp table is shared by every query in the open project. In a warehouse it belongs to one connection.

## Local T-SQL decimals

When a project runs T-SQL on this PC, arithmetic with a decimal side gets the precision and scale a Fabric Warehouse gives it, by T-SQL's rules ("Precision, scale, and length" on Microsoft Learn). DuckDB's own rules differ: it widens decimals its own way, keeps every digit of scale, and divides decimals as doubles.

| Case | Here, as in a warehouse | What can still differ |
| --- | --- | --- |
| `+`, `-`, `*` of decimals, or a decimal and an integer | T-SQL's type, and its value rounded to that scale. Past 38 digits the scale gives way as T-SQL's rules say (`decimal(38,10) * decimal(38,10)` is `decimal(38,6)`) | A value too big to work out exactly here (more whole digits than DuckDB leaves room for) is worked out in floating point, and the run's notes say so: about 15 significant digits match |
| `/` with a decimal side | T-SQL's type, and the quotient worked out exactly and cut at that scale (`-10.25 / 3` is `-3.4166666666666`). An inner division is cut at its own scale first, as in T-SQL. Dividing by zero stops with "Divide by zero error encountered" | A dividend or divisor whose digits don't fit in 38 once moved is divided in floating point, and the notes say so. Also when a side calls `RAND` or `NEWID`, which can't be read twice |
| `SUM` of a decimal | `decimal(38, s)` | Nothing |
| `AVG` of a decimal, also with `OVER (...)` | `decimal(38, max(s, 6))` | The average is worked out in floating point, then given that type, and the notes say so |
| Numbers written in the query | `1.5` is `decimal(2,1)`, and a whole number meets a decimal as `decimal(its digits, 0)`, so `1.0 / 3` is `0.333333` | Nothing |
| A result too big for its type | Stops, as in a warehouse ("Could not cast value ... to DECIMAL(38,6)" here, "Arithmetic overflow error" there) | The message's words |

`money` and `smallmoney` are refused rather than given decimal rules (use `DECIMAL(19, 4)` and `DECIMAL(10, 4)`). An integer divided by an integer stays an integer, cut toward zero. Dividing by zero is an error for integers and floats too (see below).

## Local T-SQL arithmetic and conversion

When a project runs T-SQL on this PC, these are checked against T-SQL's reference:

| What | Here, as in a warehouse |
| --- | --- |
| `/` and `%` on integers | Cut toward zero, and the remainder takes the dividend's sign: `-7 / 2` is `-3`, and `-7 % 2` is `-1` |
| Dividing by zero | `/` and `%` stop with "Divide by zero error encountered.", for integers, decimals and floats. `NULL / 0` is `NULL` |
| A result past `int` or `bigint` | An error, as in T-SQL. The message is DuckDB's ("Overflow in addition of INT32"), not T-SQL's "Arithmetic overflow error" |
| Text meets a number | The number's type wins: `'1' + 1` is `2` and `'' + 1` is `1`, while `'1.5' + 1` and `'x' + 1` stop with "Conversion failed". Two texts are concatenated (`'1' + '1'` is `'11'`) |
| `ISNULL(a, b)`, `COALESCE(a, b)` | `ISNULL` takes `a`'s type, so `b` is converted to it: `ISNULL(CAST(NULL AS VARCHAR(2)), 'abcd')` is `'ab'`, and `ISNULL(int_column, 2.7)` is `2`. `COALESCE` takes the type that ranks highest: `2.7` and `'abcd'` |
| `CAST`, `TRY_CAST` | A value that doesn't convert stops `CAST` and is `NULL` with `TRY_CAST`. A decimal or float to an integer is cut toward zero, and a decimal to a narrower decimal rounds |
| `ROUND` | Keeps its value's type: `ROUND(150.75, 0)` is `151.00`, `ROUND(748.58, -1)` is `750.00`, and `ROUND(150.75, 0, 1)` cuts to `150.00`. A result that no longer fits the type stops with "Arithmetic overflow error" (`ROUND(748.58, -3)`, `ROUND(999.5, 0)`) |

## Local T-SQL JSON

When a project runs T-SQL on this PC, the JSON functions give a Fabric Warehouse's answers, as T-SQL's reference gives them (its pages for these functions apply to Warehouse in Microsoft Fabric):

| What | Here, as in a warehouse |
| --- | --- |
| `ISJSON(x)` | `1` for an object or array, `0` for anything else (`ISJSON('1')` is `0`), and `NULL` for `NULL`. With `VALUE`, `ARRAY`, `OBJECT` or `SCALAR` it tests for that kind: `ISJSON('"text"', SCALAR)` is `1` |
| `JSON_VALUE(x, path)` | A scalar's text. In lax mode, the default, a path that isn't there, an object or array, or text longer than 4000 characters gives `NULL`. In `strict` mode each is an error. A JSON `null` gives `NULL` either way |
| `JSON_QUERY(x, path)` | An object or array, and the whole text with no path. In lax mode a scalar or a path that isn't there gives `NULL`; in `strict` mode each is an error |
| `JSON_OBJECT('a': x, ...)` | `{"a":...}`, keeping a `NULL` value as `null`, T-SQL's default |
| `JSON_ARRAY(x, ...)` | `[...]`, leaving `NULL`s out, T-SQL's default: `JSON_ARRAY('a', 1, NULL)` is `["a",1]` |
| One of these inside another | Nested as JSON, not as quoted text: `JSON_ARRAY(1, JSON_OBJECT('k': 'v'))` is `[1,{"k":"v"}]` |
| JSON text that isn't JSON | `JSON_VALUE` and `JSON_QUERY` stop with an error, in lax mode too. The message is the local engine's ("Malformed JSON"), not T-SQL's |

Refused by name: `OPENJSON`, with or without `WITH`, `FOR JSON PATH` and `FOR JSON AUTO`, `NULL ON NULL` and `ABSENT ON NULL` (leave the clause out to get T-SQL's default), `JSON_ARRAYAGG`, `JSON_OBJECTAGG`, `JSON_MODIFY` and `JSON_PATH_EXISTS`. A path that isn't written out as text is refused too.

What can still differ, each with a note in the run's `translation.notes`:

- `JSON_VALUE` gives a number in the local engine's form: `1.50` is `1.5`, and `1e2` is `100.0`. T-SQL gives it as written.
- `JSON_OBJECT` and `JSON_ARRAY` write a decimal in its shortest form (`1.50` is `1.5`). T-SQL keeps its scale.
- `ISJSON` counts JSON with a trailing comma, or `NaN`, as valid. T-SQL counts only standard JSON.
- `JSON_QUERY` gives an object or array without the spaces between its parts (`[1,2]` for `[1, 2]`). Whether a warehouse keeps them hasn't been checked.

## Local T-SQL names, schemas and views

When a project runs T-SQL on this PC, names resolve as in a Fabric Warehouse, and a name that can't resolve here is refused by name, never answered from another table.

| What | Here, as in a warehouse |
| --- | --- |
| `t`, `dbo.t`, `[dbo].[t]`, `"dbo"."t"` | The project's table. A name without a schema means `dbo`: `SELECT * FROM t` reads `dbo.t`, and if there's no `dbo.t` but another schema has a `t`, it stops with "Invalid object name 't'" and names that schema, where it used to read the other table |
| `CREATE SCHEMA`, `DROP SCHEMA` | As in a warehouse. A schema with tables in it isn't dropped |
| `CREATE VIEW`, `CREATE OR ALTER VIEW`, `ALTER VIEW`, `DROP VIEW` | As in a warehouse. `ALTER VIEW` changes a view that's there, and stops with "Invalid object name" otherwise. A name without a schema in a view's query is bound to `dbo` when the view is made, as T-SQL binds it |
| A view whose table was dropped | Stops with an error when it's read |
| `INFORMATION_SCHEMA.TABLES`, `.COLUMNS` | List the project's tables and columns, with a note: `TABLE_CATALOG` names the local database, not the warehouse, and some columns differ |

Refused by name:

- Three-part names (`otherwarehouse.dbo.t`, `mylakehouse.dbo.t`): on this PC a project is one database, so another warehouse or a lakehouse's SQL analytics endpoint can't be named. Write `dbo.t` for the project's own table.
- Four-part names (`server.db.dbo.t`), which a warehouse doesn't take either: it has no linked servers.
- `sys` catalog views (`sys.objects`, `sys.tables`). Use `INFORMATION_SCHEMA`.
- `CREATE SCHEMA ... AUTHORIZATION`, since the local engine has no users to own a schema, and `CREATE VIEW ... WITH SCHEMABINDING`.

What can still differ:

- A view made with `SELECT *` picks up columns added to its table later. In a warehouse its columns stay as they were until the view is altered.
- `ALTER VIEW` on a view made earlier in the same script stops with "Invalid object name": the check reads the views that were there when the script started.
- A name with a schema that a warehouse doesn't have, such as DuckDB's `main`, still runs here.

## Local T-SQL security and session settings

The local engine has no users, roles or permissions. Statements that set them are refused by name, never skipped, so a script that ran here never looks as if it applied permissions. Apply them in the Fabric Warehouse.

Refused by name, with "... isn't applied on this PC":

- `GRANT`, `DENY` and `REVOKE`, `GRANT UNMASK` included
- `EXECUTE AS` and `REVERT`
- Row-level security: `CREATE`, `ALTER` and `DROP SECURITY POLICY`
- Dynamic data masking: `MASKED WITH (FUNCTION = ...)` in `CREATE TABLE`, and `ADD MASKED` and `DROP MASKED` in `ALTER TABLE`
- `CREATE`, `ALTER` and `DROP USER`, `ROLE` and `LOGIN`, `ALTER ROLE ... ADD MEMBER`, and `ALTER AUTHORIZATION`

`SET` options run as a Fabric Warehouse's defaults:

| Option | Runs here | Refused by name |
| --- | --- | --- |
| `NOCOUNT`, `XACT_ABORT` | `ON` and `OFF`. A script stops at its first error either way (see Local T-SQL scripts) | |
| `ANSI_NULLS`, `QUOTED_IDENTIFIER`, `ANSI_WARNINGS`, `ANSI_PADDING`, `ARITHABORT`, `CONCAT_NULL_YIELDS_NULL` | `ON` | `OFF` |
| `NUMERIC_ROUNDABORT` | `OFF` | `ON` |
| `DATEFORMAT` | `mdy` | any other |
| `LANGUAGE` | `us_english`, `English` | any other |
| `DATEFIRST` | `7` | any other |

Any other option (`ROWCOUNT`, `STATISTICS IO`, `STATISTICS TIME`, `SHOWPLAN_XML`, `NOEXEC`, `FMTONLY`, `LOCK_TIMEOUT`, `IMPLICIT_TRANSACTIONS`, `CONTEXT_INFO` and the rest) is refused by name. `SET TRANSACTION` is refused with the transactions.

## Local T-SQL scripts

When a project runs a T-SQL script on this PC, it runs as a batch does in a Fabric Warehouse that nothing wraps in a transaction, and what the local engine can't run is refused by name before anything runs.

| What | Here, as in a warehouse |
| --- | --- |
| Several statements, or `GO` on a line of its own | Run in order. `GO` splits the script into batches |
| An error part way through | The script stops at that statement. The error names it and says what ran: "Statement 2 of 3 (SELECT) failed: Divide by zero error encountered. Statement 1 ran, and what it changed is kept; the last statement didn't run." |
| `UPDATE ... SET v = v + 'x'` | The `SET` values and the `WHERE` of an `UPDATE` or `DELETE` follow T-SQL's types, as a `SELECT` does: two texts are joined, and text compares without its trailing spaces (`WHERE v = 'b'` finds `'b  '`) |

Refused by name: variables (`DECLARE`, `@x`, `@@ROWCOUNT`), `IF`, `WHILE`, `BEGIN ... END`, `TRY ... CATCH`, `RETURN`, `BREAK`, `CONTINUE`, `GOTO` and labels, `THROW`, `RAISERROR`, `PRINT`, `WAITFOR`, `CHECKPOINT`, cursors (`DECLARE ... CURSOR`, `OPEN`, `FETCH`, `CLOSE`, `DEALLOCATE`), transactions (`BEGIN TRAN`, `COMMIT`, `ROLLBACK`, `SAVE TRANSACTION`, `SET TRANSACTION`), `EXEC` and `GO n` (run the batch n times). A script with variables and control flow runs in a Fabric Warehouse.

What can still differ:

- In a warehouse, some run-time errors (dividing by zero, a value that doesn't convert) end only their own statement, and the batch goes on to the next one. Here the script stops at the first error, and the error says which statements didn't run.
- A script of only session options (`SET NOCOUNT ON`) has nothing to run here, and says so.

## How it's built and tested

- `sidecar/fabric_desktop/sqlcompat/`: `profiles.py` describes each engine (statement categories, unsupported types and their alternatives, identifiers, transactions, DDL and DML, semantic differences, confidence and sources); `rules.py` holds the rules, each naming its engines, severity, confidence and source; `analyzer.py` splits the SQL into statements (at `;`, and at T-SQL's `GO` on a line of its own, with an optional count or comment), reads each with sqlglot and the engine's tokenizer, applies the rules and builds the report. Rules work on tokens first, so they still find problems, at exact positions, in statements sqlglot can't read.
- The engine methods are `sql_analyze` and `sql_compat_export`. `Service.sql_compat_profiles` lists the rules in-process; the app doesn't call it.
- `sidecar/tests/fixtures/sql_compat_v1.jsonl` holds 197 cases: the SQL, the engine, the expected outcome, the exact set of rules expected and where the first finding is. 36 of them are false-positive guards, SQL that must not be flagged (Spark array indexes, `ISNULL(a, b)` in T-SQL, `UPDATE ... SET x = NULL`, keywords inside strings and comments, triggers in a SQL database). `sidecar/tests/test_sql_compat.py` runs them.
- Changing a rule means moving `RULE_VERSION` on, reviewing the fixtures and updating this page.
- The portability report is `sql_portability.py`, tested by `sidecar/tests/test_sql_portability.py`, which also grades every Snowflake probe statement.
