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

The check only checks and formats T-SQL; it never runs it. A local data project runs T-SQL on this PC: see [Local T-SQL changes](#local-t-sql-changes) and the sections after it.

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

## Queries this PC refuses to read

Spark SQL that runs on this PC is read first, and a query that can't be read is refused with a message that
says why, and nothing runs. Besides SQL with a mistake in it, that is:

- A query nested too deeply: brackets, operators or calls inside one another a few dozen levels deep, or
  more than 12 calls named like a type (`string(`, `int(`, `struct(`, `decimal(`...) inside one another, which
  would take the reader minutes. Split it into smaller parts, such as a WITH clause or a view.
- A call missing an argument it needs, such as `datediff('2024-02-01')`, which Spark refuses too.
- A number Spark SQL can't read, such as `1e10.5BD`.

## ANSI mode on this PC

Spark SQL and the DataFrame API on this PC run with ANSI mode off (`spark.sql.ansi.enabled = false`). That is how Fabric runs them. Runtime 1.3 (Spark 3.5) keeps Spark's default, and Runtime 2.0 (Spark 4.1) is set to false by Microsoft, although Spark 4 turns it on. Databricks SQL on the DuckDB engine runs with ANSI mode on, as a Databricks SQL warehouse (`ANSI_MODE`) and Databricks Runtime 17.0 and later do. Each case below is checked against Spark 3.5's ANSI compliance page and Databricks' `ANSI_MODE` page in `sidecar/tests/test_spark_ansi.py`. None has been compared with a Fabric or Databricks run, so treat them as UNVERIFIED there.

| Case | Spark SQL here (ANSI off, as Fabric) | Databricks SQL here (ANSI on) |
| --- | --- | --- |
| A `CAST` of text that isn't a number or a date, such as `CAST('abc' AS INT)` or `CAST('2024-02-30' AS DATE)` | NULL | Stops (`[CAST_INVALID_INPUT]` for a number) |
| A `CAST` of a number past the type's range | A whole number keeps its low bits, as Java's cast does: `CAST(2147483648 AS INT)` is -2147483648. A double past the range becomes the range's nearest end: `CAST(1e10 AS INT)` is 2147483647, and NaN is 0. Text past the range is NULL | Stops |
| A division by zero (`/`, `div`, `%`) | NULL | Stops with `[DIVIDE_BY_ZERO]` |
| `try_cast`, `try_divide` | NULL | NULL |
| `try_divide` of decimals | A decimal, typed as `/` is: `decimal(23,13)` for two `decimal(10,2)` | The same |
| A decimal `sum` or `avg` past its result type, `decimal(p + 10, s)` | NULL | Stops with `[ARITHMETIC_OVERFLOW] Overflow in sum of decimals` |

`spark.conf.set("spark.sql.ansi.enabled", "true")` is kept for when the notebook runs in Fabric, and prints a note saying this PC still answers with ANSI mode off.

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `abs(-2147483648)` or `-(-2147483648)` on an int, with ANSI off | Wraps around: -2147483648 | 2147483648, a `bigint` |
| A notebook in Databricks Runtime 17.0 or later, which runs Spark SQL with ANSI mode on | A failed `CAST` or a division by zero stops | Gives NULL, as with ANSI mode off. The first use of `dbutils` in a session prints a note saying so, since which runtime a notebook is meant for isn't known here. Run it as Databricks SQL on the DuckDB engine, or on Databricks |
| `pmod(x, 0)` in Databricks SQL | Stops with `[DIVIDE_BY_ZERO]` | NULL |
| A decimal `sum` or `avg` whose running total passes about 1.7 × 10^38 | NULL with ANSI off, `[ARITHMETIC_OVERFLOW]` with ANSI on | Stops with DuckDB's "Overflow in HUGEINT addition" |
| On DuckDB 2.0 (not yet the version this PC installs): a decimal `sum` or `avg` of a `decimal(38, s)` column past its type | NULL with ANSI off, `[ARITHMETIC_OVERFLOW]` with ANSI on | Stops with DuckDB's "Overflow in SUM of DECIMAL" |
| On DuckDB 2.0: `pmod(x, 0)` in Databricks SQL | Stops with `[DIVIDE_BY_ZERO]` | Stops with DuckDB's own division-by-zero error |

## Decimal results on this PC (LOC-005)

Spark SQL run on this PC, in a notebook or on the DuckDB engine, and Databricks SQL on the DuckDB engine, give Spark's decimal type and value for division, multiplication, addition, subtraction, `%`, `pmod`, `div`, `avg`, `sum`, `round`, `bround`, `ceil` and `floor`. These follow Spark's own rules: DecimalPrecision with `spark.sql.decimalOperations.allowPrecisionLoss` on, the default. A division by zero gives NULL, as Spark does with ANSI mode off; Databricks SQL stops on it, as with ANSI mode on. For example, `CAST(1 AS DECIMAL(10,2)) / 3` is `decimal(14,6)` 0.333333 here, as in Fabric Spark. A local data project's Spark SQL doesn't use these rules yet: it keeps DuckDB's types, so the same division gives a double there. These results are checked against Spark's published rules in `sidecar/tests/test_spark_decimal_rules.py`. They haven't been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

These cases can still differ, and LOC-005 notes them:

| Case | Spark | This PC |
| --- | --- | --- |
| A sum, difference or product past 38 digits, such as two `DECIMAL(38,18)` columns multiplied | Rounds the scale down, to no fewer than 6 places, for example `decimal(38,6)`; NULL when the result doesn't fit | DuckDB's own type, for example `DECIMAL(38,36)`; the query fails when the result doesn't fit |
| A division whose dividend, scaled, needs more than 38 digits | The exact quotient | Worked out through DOUBLE and cast to Spark's type, so the last digits can differ |
| `div` past 38 digits | The exact quotient | Worked out through DOUBLE |
| `div` whose quotient is past the BIGINT range | Wraps around (ANSI off) | NULL |
| `spark.sql.ansi.enabled` set to true in a notebook | A division by zero is an error | NULL |
| On the DuckDB engine, a statement that reads a table made earlier in the same cell | Spark's decimal types | DuckDB's types: the table isn't there yet when the cell is read. Make the table in its own cell |

## Rounding a double on this PC

Spark SQL run on this PC, and the DataFrame API's `round` and `bround`, round a `double` or `float` as Spark does: the value's shortest decimal text is rounded half up (`round`) or half even (`bround`) and turned back into a `double` (a `float` stays a `float`). For example, `round(0.285D, 2)` is 0.29 and `round(1.005D, 2)` is 1.01, as in Spark, where rounding the binary value gives 0.28 and 1.0. NaN and infinity are kept. These are checked against Apache Spark 3.5.3's own answers in `sidecar/tests/test_spark_double_rounding.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Case | Spark | This PC |
| --- | --- | --- |
| More than 20 places, or fewer than -20, such as `round(x, 25)` | Rounds | Refused by name: cast the value to a decimal first |
| A number of places that isn't written in the query, such as `round(x, n)` | Rounds the decimal text | Rounds the binary value, as DuckDB does |
| A statement whose types can't be worked out | Rounds the decimal text | Rounds the binary value, as DuckDB does |

## Text compared with a number on this PC

Spark SQL run on this PC, and the DataFrame API's filters and columns, compare text with a number as Spark does with ANSI mode off: the text is cast to the number's type, as Spark's `CAST` reads it. So `'1.5' = 1` is true, and `0 = ''`, `'abc' = 1` and `'1e2' = 100` are NULL, where DuckDB rounded `'1.5'` to 2, read `'1e2'` as 100 and stopped on `''`. Text and a decimal are compared as doubles, so `'1.55' = 1.6` is false. `IN` with text and numbers compares them all as text, so `'1.0' IN (1, 2)` is false. `BETWEEN` and `CASE x WHEN ...` compare each pair the same way. `CAST('.5' AS INT)` is 0, as in Spark. These are checked against Apache Spark 3.5.3's own answers in `sidecar/tests/test_spark_text_comparisons.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Case | Spark | This PC |
| --- | --- | --- |
| A statement whose types can't be worked out | Casts the text to the number's type | DuckDB's own comparison, which can round the text or stop on it |
| `IN` with a subquery | Casts to the wider type | DuckDB's own comparison |
| `CASE x WHEN ...` whose values mix text and numbers, or numbers of two types | Casts each pair | DuckDB's own comparison |

## Whole-number arithmetic on this PC

Spark SQL run on this PC, in a notebook or on the DuckDB engine, computes `+`, `-` and `*` of two whole numbers as Spark does with ANSI mode off: in the wider of the two types (`tinyint`, `smallint`, `int` or `bigint`; a whole-number literal is an `int` when it fits, else a `bigint`), wrapping around past the type's range as Java does. For example, `2147483647 + 1` is `-2147483648` and `CAST(100 AS TINYINT) + CAST(100 AS TINYINT)` is `-56`, where DuckDB stops with "Overflow in addition". The cases are checked against Apache Spark 3.5.3's own answers in `sidecar/tests/test_spark_integer_values.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric. Databricks SQL on the DuckDB engine, which runs with ANSI mode on, still stops on an overflow, as Databricks does.

These cases can still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `abs` or unary `-` of the smallest value of a type | Wraps around (the same value) | See "ANSI mode on this PC" above |
| An operand whose type can't be worked out (the statement's types aren't known) | Wraps around | Stops with DuckDB's overflow error |
| The DataFrame API, such as `F.col("id") + F.lit(9223372036854775807)` | Wraps around | Stops with DuckDB's overflow error |
| Arithmetic in a filter (`WHERE`, `HAVING`, `QUALIFY` or a join's `ON`), such as `WHERE id + 1 = 5` | Wraps around | Stops with DuckDB's overflow error, or, when DuckDB rewrites the filter as `id = 4`, compares without wrapping. This keeps the filter fast, because DuckDB can still skip row groups by a column's smallest and largest values |

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
| `date + INTERVAL 1 MONTH`, `date - INTERVAL 2 DAY` | A `date` for an interval of years, months, weeks or days, at the month's last day (`DATE'2024-01-31' + INTERVAL 1 MONTH` is `2024-02-29`). An interval with hours, minutes or seconds gives a `timestamp` |
| `unix_seconds`, `unix_millis` | Rounded down, as Spark does: `unix_seconds(TIMESTAMP'2024-05-17 13:45:12.9')` is `1715953512` |
| `hour`, `minute`, `second` of text | Text that is a time of day only, such as `'13:45:12'` or `'T09:08'`, is read as that time |
| `from_utc_timestamp`, `to_utc_timestamp` | A zone written as an offset, as Java reads one: `GMT+2`, `UTC+05:30`, `-08:00`, `Z` |
| `try_to_date` | Refused by name, and nothing runs. Spark 3.5 (Fabric Runtime 1.3) doesn't have it, so Fabric stops on it too. Use `to_date`, which gives NULL for text that isn't a date. Databricks SQL on this PC still runs it |

Patterns that Spark refuses are refused here by name, and nothing runs: the week-based letters (`Y`, `w`, `W`, `u`, `e`, `c`), `F`, optional sections (`[ ]`), letters that aren't pattern letters, and the runs Spark stops on with "Too many pattern letters": five or more of `G`, `M`, `L`, `E`, `Q` or `q`, and seven or more `y`. These are refused too, because this PC can't give Spark's text for them:

- Zone names (`z`, such as `EST`), from `z` to `zzz` when the session's time zone isn't UTC, and `zzzz` always. Use `VV` for the zone's id or `XXX` for its offset. In UTC, `z` to `zzz` give `UTC`, as in Spark.
- When reading text, `G`, `Q`, `k`, `K`, `O` and `z`.

These cases can still differ. LOC-003 warns wherever a pattern is used:

| Case | Spark | This PC |
| --- | --- | --- |
| Text with more fraction digits than the pattern's `S` letters, such as `.123` with `ss.SS` | NULL | Read, up to 6 digits |
| Text with one digit where the pattern has two, such as `2024-2-9` with `yyyy-MM-dd` | Not read: see the next row | Read |
| A DATE column plus an interval of whole days or months, where the column's type isn't known before the query runs (a column of a subquery's or a DataFrame's expression) | `date` | `timestamp` at midnight. Cast it: `CAST(d + INTERVAL 1 MONTH AS DATE)` |
| `from_utc_timestamp` and `to_utc_timestamp` with the zone from a column, written as an offset such as `GMT+2` | Read as that offset | Stops (`Unknown TimeZone`), or for `UTC+05:30` moves by 5 hours only. A zone id such as `Europe/Paris` gives Spark's answer |
| `hour`, `minute`, `second` of a time of day with a zone, such as `'13:45:12Z'` | That time in the session's zone | NULL |
| `make_ym_interval` | A year-month interval | Refused by name, and nothing runs, because this PC has no year-month interval type |
| Text the pattern doesn't read the Spark 3 way, with `spark.sql.legacy.timeParserPolicy` at its default `EXCEPTION` | Stops with `SparkUpgradeException` when Spark 2's parser would have read it | NULL, as with the policy `CORRECTED`. The setting isn't read here |

A cast of text that is only a year or a year and month, such as `CAST('2024-02' AS DATE)` or `to_timestamp('2024')`, also still differs. Spark reads it as the month's or the year's first day; here it is NULL. No rule warns about it yet.

## The session time zone on this PC (WR-120)

Spark SQL run on this PC reads and shows timestamps in `spark.sql.session.timeZone` as Spark does. Each case is checked against Apache Spark 3.5.3's own answers, in UTC, America/Chicago, Asia/Kolkata, Europe/London and two offsets, in `sidecar/tests/test_spark_timezone_values.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Case | On this PC, as in Spark |
| --- | --- |
| The session time zone | `spark.conf.set("spark.sql.session.timeZone", ...)` takes a region (`America/Chicago`), `UTC`, or an offset of whole hours (`-08:00`, `GMT-08:00`, `UTC+3`). `current_timezone()` gives the id as it was set `SET TIME ZONE 'America/Chicago'` and `SET spark.sql.session.timeZone = ...` in `spark.sql` set it the same way, and `RESET spark.sql.session.timeZone` puts it back to UTC. In a call with several statements, the ones after it use the new zone. |
| `timestamp` and `timestamp_ntz` | A `timestamp` is an instant, shown in the session's zone; a `timestamp_ntz` is a wall clock with no zone. A cast between them, or from a date or text to a `timestamp`, reads the wall clock in the session's zone. `unix_timestamp` of a `timestamp_ntz` reads it as UTC |
| The hour clocks skip | A wall clock that doesn't exist, such as 02:30 on the day America/Chicago springs forward, moves forward by the gap (03:30) |
| The hour clocks repeat | A wall clock that happens twice, such as 01:30 on the day America/Chicago falls back, is the earlier of the two (01:30 CDT), as Java reads it. This holds for casts, `make_timestamp`, `to_timestamp`, `unix_timestamp` and `to_utc_timestamp`; `date_trunc` keeps the offset of the time it truncates |
| An interval of hours added to a `timestamp` | Split into whole days and the rest, as Spark does: `INTERVAL 24 HOURS` adds a calendar day in the session's zone, 23 hours of real time on the day clocks spring forward |

Refused by name, and nothing runs:

| Case | Why |
| --- | --- |
| A session time zone, or a `from_utc_timestamp` or `to_utc_timestamp` zone, written as an offset with minutes, such as `+05:30` | The engine on this PC has a zone for an offset of whole hours only. Use the region's own zone, such as `Asia/Kolkata` |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| A `timestamp` from `collect()` | A `datetime` with no zone, in UTC (Fabric's driver runs in UTC) | A `datetime` with the session's zone, which is the same instant |
| A bare `RESET` in `spark.sql` | Puts every setting back to its default | Puts back only the session time zone |

## Window functions on this PC

Spark SQL and the DataFrame API run on this PC give Spark's answers for window functions, with ANSI mode off as Fabric runs it. Each case below is checked against Spark 3.5's documented behaviour in `sidecar/tests/test_spark_windows.py`. None has been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

| What | On this PC, as in Spark |
| --- | --- |
| NULLs in a window's `ORDER BY` | First in ascending order and last in descending order, unless the window says `NULLS FIRST` or `NULLS LAST` |
| `row_number`, `rank`, `dense_rank`, `ntile` | An `int`. Ties share a rank: `rank` skips after a tie (1, 1, 3), `dense_rank` doesn't (1, 1, 2). `ntile` gives the extra rows to the first groups: 7 rows in 3 groups are 3, 2 and 2 |
| `percent_rank`, `cume_dist` | A `double`; ties share a value |
| `lag`, `lead` | NULL past the edge of the partition, or the default given: `lag(x, 1, 0)`. A NULL value in range stays NULL; the default is only for rows past the edge |
| Aggregates `OVER (ORDER BY ...)` | The default frame is `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, so rows that tie share a running total. Without `ORDER BY` the frame is the whole partition |
| Column names | Spark's, with the frame written out: `row_number() OVER (PARTITION BY p ORDER BY v ASC NULLS FIRST ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` |
| A ranking function, `lag` or `lead` with no `ORDER BY` in its window | Stops, as in Spark: nothing runs |
| `lag` or `lead` given a frame, or a ranking function given a frame other than its own | Stops, as in Spark: nothing runs |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| Rows that tie in a window's `ORDER BY`, for `row_number`, `lag`, `lead`, `first` and `last` | Any order | Any order, which can differ from Spark's. Add a column that tells them apart when the order matters |
| A window given by name in a `WINDOW` clause | Checked as above | Not checked here: a missing `ORDER BY` or a frame isn't refused |
| A DataFrame window function Spark stops on | Stops at the `select` | Stops when `.over(...)` is called, before the `select` |
| A DataFrame window with `rangeBetween` and numbers, such as `rangeBetween(-1, 0)` | Named after its frame as Spark writes it for the column's type | Named `... RANGE BETWEEN 1 PRECEDING AND CURRENT ROW`. Give it an `alias` |
| `ntile(0)`, or `ntile` of a column | Stops with Spark's message | Stops with DuckDB's message |

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

## Arrays, maps and structs on this PC

Spark SQL run on this PC gives Spark's values and types for its array, map and struct functions and its generators, with ANSI mode off as Fabric runs it. Each case below is checked against Spark 3.5's documented behaviour in `sidecar/tests/test_spark_complex.py`. None has been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `size`, `cardinality` | An `int`; -1 for NULL (Spark's `spark.sql.legacy.sizeOfNull`, on with ANSI off); a map's number of keys |
| `element_at`, `[ ]` | NULL for an index past either end or a key the map doesn't have. `element_at(array, 0)` stops, as in Spark. `array[0]` is the first element |
| `array_contains` | NULL when the value isn't found and the array holds a NULL, or when either is NULL |
| `array_position` | A `bigint`; 0 when the value isn't found |
| `flatten` | NULL when one of the inner arrays is NULL |
| `array_sort`, `sort_array` | `array_sort` puts NULLs last; `sort_array` puts them first ascending and last descending |
| `sequence` | Counts down when stop is below start; whole numbers give `array<int>` |
| `reverse`, `concat` | Work on arrays as well as text |
| `map_concat`, `map` | A key that repeats stops the query, as with Spark's default `spark.sql.mapKeyDedupPolicy` (EXCEPTION) |
| `explode`, `explode_outer`, `inline` | One row per element; a map gives `key` and `value` columns. `explode` of an empty or NULL array gives no rows, `explode_outer` one row of NULLs |
| `LATERAL VIEW explode(...)`, `LATERAL VIEW OUTER` | One row per element or map entry, joined to its row. `OUTER` keeps a row whose array or map is empty or NULL, with NULLs. `stack`, `json_tuple` and `inline` after `LATERAL VIEW` give one column per name, as in Spark |
| Struct fields | `s.a`, `named_struct`, `struct(1 AS a)`; written to a Delta table as Spark's `struct<a:int,b:string>`, with arrays and maps keeping their element types |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `posexplode` and `LATERAL VIEW posexplode` | `pos` is an `int` | `pos` is a `bigint` |
| A map that only comes from a computed expression, such as `size(map_filter(m, ...))` or `explode(map_from_arrays(...))`, in a view or subquery the types can't be read through | Counted or exploded as a map | Can stop with a DuckDB error naming the function. A map column, or a map written in the query, works |
| `array(1, 'a')` and other arrays that mix types | The elements are cast to a common type (`array<string>`) | Stops with a conversion error. Cast the elements to one type first |
| The DataFrame API's `F.size`, `F.cardinality`, `F.reverse` and `F.sequence` used in `agg`, `groupBy` or `orderBy`, and `F.explode` or `F.posexplode` of a map used anywhere but `select` | As in Spark SQL | They're read as arrays here: a map stops with a DuckDB error, and a count down gives `[]`. In `select`, `withColumn`, `withColumns` and `filter` they give Spark's answer. Work the column out in a `select` first |
| Databricks SQL on the DuckDB engine | As in Spark SQL | The fixes that don't need column types apply; `size`, `explode` and `reverse` of a map or array column keep DuckDB's forms |
| `explode` of a map that holds `rand()` or `shuffle()`, in a select list | The map is read once | Its keys and values are read separately, so they can come from different reads. Use `LATERAL VIEW explode`, which reads it once |

## JSON functions on this PC (WR-118)

Spark SQL run on this PC gives Spark's answers for its JSON functions, with ANSI mode off as Fabric runs it. 260 queries were run in Apache Spark 3.5.3 and are checked against this PC in `sidecar/tests/test_spark_json_values.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `get_json_object` | Spark's path rules: `$.a`, `$['a']`, `$.a[0]`, and one `[*]`, which gives the one value it matches or a JSON array of all of them. A path Spark doesn't read, such as `a`, `$.` or `$["a"]`, gives NULL. Text with more after its first object or array is read up to that value, and text that isn't JSON gives NULL |
| `json_tuple` | A repeated key gives its last value. A NULL argument must be typed, as in Spark: `CAST(NULL AS STRING)` |
| `from_json` | Each field is read by its type; a value that doesn't fit gives NULL for that field, and an array or map with one such element is NULL as a whole. Text that isn't JSON, or isn't the schema's kind (an array for a struct schema), gives a struct of NULLs; an object on its own read with an `ARRAY<STRUCT<...>>` schema is an array of that one struct. A map keeps the last value of a key its object repeats. The `_corrupt_record` field, or the one `columnNameOfCorruptRecord` names, holds the whole text of a record that didn't read. `mode` takes `PERMISSIVE` (the default; an unknown mode is read as it) and `FAILFAST`, which stops on such a record; `DROPMALFORMED` stops, as in Spark. A date that doesn't exist, such as `2024-02-30`, stops with Spark's `INCONSISTENT_BEHAVIOR_CROSS_VERSION` error. `dateFormat` and `timestampFormat` are read; `MAP<STRING, ...>` schemas are read; `typeof` gives `struct<a:int>` |
| `to_json` | Written from the value's type: a struct leaves out NULL fields (unless `ignoreNullFields` is `false`), arrays and maps keep NULLs as `null`, a decimal is written as Java writes it, keeping its scale (`1.50`) and in E form when it has more than 6 decimal places and its value is below 0.000001 (`1.2E-7`, `0E-8`), a double is written as Java writes it (`1.0E10`), `NaN` and `Infinity` are quoted, dates are `yyyy-MM-dd`, timestamps `yyyy-MM-dd'T'HH:mm:ss.SSSXXX` in the session's time zone (`dateFormat`, `timestampFormat` and `timestampNTZFormat` change them), binary is base64, and a date map key is its days since 1970-01-01 |
| `schema_of_json` | Spark's inferred schema; a whole number past `bigint` is a `DECIMAL` of its digits (`DECIMAL(20,0)` for `99999999999999999999`), or a `DOUBLE` past 38 digits. `NaN` and `Infinity` are `DOUBLE`. `prefersDecimal` and `primitivesAsString` are read; with `prefersDecimal`, a number such as `1e5`, whose exponent leaves it no decimal places, stops, as in Spark |
| `FROM range(...)` | Its column is `id`, a `bigint`, as in Spark |

Refused by name, and nothing runs:

| Case | Why |
| --- | --- |
| The `from_json` and `schema_of_json` options `allowComments`, `allowUnquotedFieldNames` and `allowNumericLeadingZeros` set to `true`, and other options that change how text is read | This PC reads only standard JSON. Leave the option out, or run the cell in Fabric |
| The `to_json` option `pretty` | This PC writes JSON on one line only |
| `to_json` of a value whose type can't be worked out here (such as `struct(*)` or a `to_json` inside another), and of a map whose keys aren't text, whole numbers, booleans, decimals or dates | Spark writes these in its own way |
| `get_json_object` with more than one `[*]`, or `.*`, or a `[*]` that matches one string on its own or a null | Spark writes these in its own way |
| `from_json` text that its `dateFormat` or `timestampFormat` doesn't match (text with no digit gives NULL, as in Spark, when every field of the pattern is a number) | Spark gives NULL or stops, depending on how its older parser reads the text |
| `from_json` of a JSON object that repeats a key of its schema | Spark keeps the last value; this PC can't |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `from_json` text in single quotes, such as `{'a':1}` | Read (`allowSingleQuotes` is on by default) | NULL |
| Databricks SQL on the DuckDB engine | As in Spark SQL | `from_json` and `schema_of_json` options are refused, and `to_json` is DuckDB's own |

## Array and string functions at their edges

Spark SQL run on this PC gives Spark's answers for these array and string functions at their edges, with ANSI mode off as Fabric runs it. The queries in `sidecar/tools/spark_value_cases/arrays_strings.txt` were run in Apache Spark 3.5.3 and are checked against this PC in `sidecar/tests/test_spark_arrays_strings_values.py`.

| Function | On this PC, as in Spark |
| --- | --- |
| `array_join` | NULL elements are skipped, or replaced by the third argument; a NULL delimiter or replacement gives NULL, and an array with nothing left gives `''` |
| `array_size`, `size` | `array_size` of NULL is NULL; `size` of NULL is -1 |
| `array_insert` | A position past the end pads with NULLs; a negative one counts from the end (-1 is after the last element) and pads at the front; 0 is an error |
| `locate`, `instr`, `position` | An empty search text is found at 1 from any start of 1 or more |
| `substring_index` | A delimiter that overlaps itself is counted at each place it starts (`substring_index('aaa', 'aa', -1)` is `''`) |
| `concat_ws` | With only a separator it is `''` (NULL for a NULL separator) |
| `format_number` | A whole number or a decimal keeps every digit; a decimal rounds half-even and keeps a `-` that rounds to 0 (`-0`) |
| `contains` | A bare NULL argument gives NULL |
| `upper`, `lower`, `initcap` | Java's full case mapping: `upper('ß')` is `SS`, `lower('İ')` is `i̇`, a final capital sigma is `ς`, and `initcap` gives a letter's title case |
| `array_distinct` | `0.0` and `-0.0` are kept apart |

Refused by name, and nothing runs:

| Case | Why |
| --- | --- |
| `split` with a pattern written in the query that can match empty text, such as `'|'` or `'x*'` | Java and this PC split around an empty match differently |
| `format_number` with a pattern, such as `'#,###.00'` | Java's DecimalFormat patterns aren't written here. Give the number of decimal places |
| `format_number` with more than 18 decimal places | A decimal here keeps at most 38 digits in all |
| `array_insert` that would pad the array with more than 10,000,000 NULLs | Spark pads up to its array limit, but that many would use up this PC's memory |
| `format_string` and `printf` with `%s` of an array, map or struct | Spark writes them as text its own way (`[1,2]`) |

## Hashing and encoding on this PC (WR-119)

Spark SQL run on this PC gives Spark's answers for its hashing and encoding functions, with ANSI mode off as Fabric runs it. 84 queries were run in Apache Spark 3.5.3 and are checked against this PC in `sidecar/tests/test_spark_hashing_values.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `hash`, `xxhash64` | Spark's Murmur3 and xxHash64 numbers with seed 42, for each type: whole numbers, doubles, decimals, booleans, text, binary, dates, timestamps, arrays, and several arguments. NULL keeps the seed. A map stops, as in Spark |
| `md5`, `sha1`, `sha`, `crc32` | Of text's UTF-8 bytes, or of binary |
| `sha2` | 0 and 256 bits are SHA-256; 224, 384 and 512 are those SHA-2 digests; any other length gives NULL |
| `base64` | Text's UTF-8 bytes or binary; a line break (CR LF) after every 76 characters, as Spark 3.5 writes it |
| `unbase64` | Characters outside the base64 alphabet are skipped and padding is optional. Padding that doesn't end a group of four, base64 after the padding, or one character left over at the end stops, as in Spark |
| `hex`, `unhex` | `hex` of a double or decimal cuts it to a whole number first. `unhex` puts a `0` before an odd number of digits and gives NULL for anything that isn't hex digits |
| `conv` | Spaces around the number are trimmed first |
| `CAST(x AS BINARY)` | A whole number's big-endian bytes: 1 for a `tinyint`, 2 for a `smallint`, 4 for an `int` and 8 for a `bigint` |
| `CAST(x AS STRING)` of binary | Its UTF-8 text |
| `-9223372036854775808L`, `-2147483648` | Read as one literal of its type, as Spark reads them |

Refused by name, and nothing runs:

| Case | Why |
| --- | --- |
| `hash` or `xxhash64` of a struct, or of an array of structs or arrays | This PC can't yet hash a struct's fields in Spark's order. Hash its fields instead: `hash(s.a, s.b)` gives the same number as `hash(s)` |
| A cast of binary to string where the bytes aren't UTF-8 | Spark reads them with replacement characters. Use `hex()` or `base64()` instead |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| Databricks SQL on the DuckDB engine | `base64` and `unbase64` as in Spark SQL | `base64` writes one line, and `unbase64` reads only strict base64 |

## Math functions on this PC (WR-116)

Spark SQL run on this PC gives Spark's answers for its math functions at the edges where DuckDB stops with an error or answers as C does, with ANSI mode off as Fabric runs it. 28 queries were run in Apache Spark 3.5.3 and are checked against this PC in `sidecar/tests/test_spark_math_values.py`, on DuckDB 1.5 and 2.0. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `log`, `ln`, `log10`, `log2` | NULL for 0 or a negative number, and for a base of 0 or a negative base. A base of 1 gives `inf` (`log(1, 8)`) |
| `pow`, `power` | As Java's `Math.pow` does, NaN for a NaN exponent, and for 1 or -1 to an infinite power |
| `hypot` | `inf` when either side is infinite, even beside NaN. Very large or very small sides don't overflow (`hypot(1e200, 1e200)`) |
| `factorial` | A `bigint`, and NULL outside 0 to 20. A `bigint` or decimal argument is cast to an `int` first, so `factorial(3.7)` is 6 |
| `acos`, `asin`, `cot` | NaN outside -1 to 1 for `acos` and `asin`, and `cot(0)` is `inf` |
| `atan2` | -0.0 counts as 0.0, so `atan2(0.0D, -0.0D)` is 0.0 |
| `try_add`, `try_subtract`, `try_multiply` | `-2147483648` is an `int`, so `try_subtract(-2147483648, 1)` is NULL |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `abs(-2147483648)` and `round(2147483647, -1)` | Wraps around (ANSI off) | Widens the type, or stops on DuckDB 2.0 |

To get the same answer from both, cast the argument to `bigint` first.

## Session, reflection and error functions on this PC (WR-122)

Spark SQL run on this PC answers as Spark does for `raise_error`, or refuses the function by name, with ANSI mode off as Fabric runs it. 42 queries were run in Apache Spark 3.5.3 and are checked against this PC in `sidecar/tests/test_spark_functions_values.py`. None has been compared with a Fabric run, so treat them as UNVERIFIED in Fabric.

| Function | On this PC, as in Spark |
| --- | --- |
| `raise_error` | Stops the query only when a row reaches it, so a `CASE`, `if` or `coalesce` that doesn't reach it gives its other value. A NULL message stops it too. `typeof(raise_error(...))` is `void` |

Refused by name, and nothing runs:

| Case | Why |
| --- | --- |
| `aes_encrypt`, `aes_decrypt`, `try_aes_encrypt`, `try_aes_decrypt` | AES isn't available in the engine on this PC |
| `java_method`, `reflect` | They call a Java method by reflection, and this PC runs no Java |
| `input_file_name`, `input_file_block_start`, `input_file_block_length` | This PC doesn't track the file each row was read from. Spark gives `''`, `-1` and `-1` for a row that wasn't read from a file |
| `sql_keywords()` | This PC doesn't have Spark's list of SQL keywords and which of them are reserved |
| `current_user()`, `user()`, `session_user()` | In Fabric they name the identity running the notebook. DuckDB would name itself |
| `version()` | In Fabric it gives Fabric's Spark build and its git revision. DuckDB would give its own version |

These cases still differ:

| Case | Spark | This PC |
| --- | --- | --- |
| `raise_error`'s error | `[USER_RAISED_EXCEPTION]` with the message | DuckDB's `Invalid Input Error:` with the message |
| `user` written without brackets | The identity running the notebook, when no column is named `user` | `duckdb` |

## Local T-SQL changes

When a project runs T-SQL on this PC, `MERGE`, `UPDATE`, `DELETE` and `INSERT` on a database table give a warehouse's rows, or stop by name. Each case below is checked against T-SQL's reference.

| What | Here, as in a warehouse |
| --- | --- |
| `MERGE` with `WHEN MATCHED`, `WHEN NOT MATCHED [BY TARGET]` and `WHEN NOT MATCHED BY SOURCE` | The same rows. The result is the number of rows inserted, updated and deleted, as `@@ROWCOUNT` would be |
| A `MERGE` in which more than one source row would update or delete the same row | Refused with T-SQL's words, "The MERGE statement attempted to UPDATE or DELETE the same row more than once", and nothing changes. A source row that a `WHEN MATCHED ... AND` condition leaves out doesn't count, and source rows that are inserted can repeat. See [Local T-SQL MERGE](#local-t-sql-merge) |
| `UPDATE t SET ... FROM dbo.a AS t JOIN dbo.b AS s ON ...` and `DELETE t FROM dbo.a AS t JOIN ...` | The table named in `FROM` is the one changed, and each inner join is a filter on it, so only the rows that join change |
| `UPDATE dbo.a SET ... FROM dbo.a JOIN dbo.b ON ...` | The `dbo.a` in `FROM` is the table being changed, not a second copy of it, so rows that don't join stay as they are |
| `INSERT`, `UPDATE`, `DELETE` | The result is the number of rows changed |

What can still differ:

- An `UPDATE ... FROM` in which a row of the table joins more than one row is refused. T-SQL takes the value from any one of them, and here that choice isn't made for you. Keep one row per key in `FROM`, or use a `MERGE`.
- `OUTPUT` and `@@ROWCOUNT` are refused by name. Each statement's row count is its result instead.
- An `UPDATE` or `DELETE` whose `FROM` has an outer join (`LEFT`, `RIGHT`, `FULL`), `JOIN ... USING`, or the changed table after another one, is refused by name.
- On one of the project's Delta tables, `MERGE`, `UPDATE` and `DELETE` run through delta-rs, which has its own rules: see [Local data](local-data.md).

## Local T-SQL MERGE

When a project runs a T-SQL `MERGE` on this PC, on a database table or on one of the project's Delta tables, it gives a Fabric Warehouse's rows, or stops by name before anything changes. Each case below is checked against Microsoft's MERGE (Transact-SQL) page, whose syntax block for Fabric Data Warehouse is the one followed here.

| What | Here, as in a warehouse |
| --- | --- |
| A table row that more than one source row would update or delete | Refused with SQL Server's error 8672, word for word: "The MERGE statement attempted to UPDATE or DELETE the same row more than once. This happens when a target row matches more than one source row. A MERGE statement cannot UPDATE/DELETE the same row of the target table multiple times. Refine the ON clause to ensure a target row matches at most one source row, or use the GROUP BY clause to group the source rows." The same on a Delta table, an unconditional `WHEN MATCHED THEN DELETE` included |
| A source row that a `WHEN MATCHED ... AND` condition leaves out, or one that is only inserted | Doesn't count, so the `MERGE` runs and each of the repeated source rows is inserted |
| One source row that matches several table rows | Each of those rows is updated or deleted once |
| Two `WHEN MATCHED` clauses, or two `WHEN NOT MATCHED BY SOURCE` | The first clause whose condition holds takes the row, and the second takes only what the first leaves. The clauses can be written in any order |
| A third `WHEN MATCHED` or `WHEN NOT MATCHED BY SOURCE`, a second `WHEN NOT MATCHED [BY TARGET]`, a first of two clauses with no `AND` condition, or two clauses that both `UPDATE` or both `DELETE` | Refused by name, with the rule it breaks |
| `WHEN NOT MATCHED BY SOURCE` that reads a source column, or `WHEN NOT MATCHED [BY TARGET]` that reads a table column (`s.x` or `t.x`) | Refused by name, because there is no such row to read |
| The same column without `s.` or `t.` | Placed by the table's columns and the source's. A column both have is refused with SQL Server's error 209, "Ambiguous column name 'x'.", in any `WHEN` clause. A column only the table has, in `WHEN NOT MATCHED [BY TARGET]`, and a column only the source has, in `WHEN NOT MATCHED BY SOURCE`, are refused by name, because there is no such row to read. The source's columns are a table's, a CTE's or a subquery's, with `SELECT *` and `x.*` read from the tables they name, or the alias's own list, as in `USING (VALUES (2, 'Q')) AS s(k, w)`. A source whose columns are known only when it runs, such as a temporary table (`#src`), a table the script creates or a table-valued function (`STRING_SPLIT`, `GENERATE_SERIES`), is placed the same way by the columns it has when the `MERGE` runs, before anything is written |
| `UPDATE SET t.v = ...` with the table's alias | Runs as `SET v = ...`. A column set twice in one clause, or named twice in an `INSERT`, is refused by name (SQL Server's error 264) |
| `INSERT VALUES (...)` with no column list | A value for each of the table's columns, in order. A different number of values is refused by name |
| `INSERT DEFAULT VALUES` | Refused by name, because a warehouse's `MERGE` doesn't take it |
| A `MERGE` without a semicolon after it | Refused by name, as SQL Server's error 10713 does |

What can still differ:

- On one of the project's Delta tables, `WHEN NOT MATCHED BY SOURCE` with no condition changes every row the source doesn't have, so it runs only with confirm (`local_change_delta`). Add a condition, such as `AND t.id < 1000`, to run it in a script.
- In Spark SQL or DuckDB SQL a Delta table keeps Delta's own rule for repeated source rows, which counts every match of `ON` and allows an unconditional `DELETE`.
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
| `DATEADD` | Month and year steps end on the last day of a shorter month: `DATEADD(month, 1, '2024-01-31')` is `2024-02-29 00:00:00`, `DATEADD(year, 1, '2024-02-29')` is `2025-02-28 00:00:00`. Text is read as a `datetime`, so the answer is one, as in T-SQL. A minute or hour added to a `date` is an error, as in T-SQL |
| `DATEDIFF` | Boundaries crossed, not whole spans: `DATEDIFF(year, '2023-12-31', '2024-01-01')` is `1`, `DATEDIFF(week, Saturday, Sunday)` is `1`. The answer is an `int`, and a count past an int's range stops with T-SQL's "The datediff function resulted in an overflow" (`DATEDIFF(millisecond, ...)` over more than 24 days) |
| `DATEDIFF_BIG` | The same counts as a `bigint`, with no overflow |
| `EOMONTH` | The month's last day, with or without months to add: `EOMONTH('2024-01-31', 1)` is `2024-02-29` |
| `DATEFROMPARTS` | A date, an error for a day that doesn't exist (`2023, 2, 29`), `NULL` for a `NULL` part |
| `FORMAT` | .NET's date formats in `en-US`, `en-GB` or `de-DE`, on a `date` or a date-and-time column. See [Local T-SQL formatting](#local-t-sql-formatting) |
| `CONVERT` to text, styles 101, 103, 112, 120, 121, 126 | `02/29/2024`, `29/02/2024`, `20240229`, `2024-02-29 13:04:05`, `2024-02-29 13:04:05.123000`, `2024-02-29T13:04:05.123000`. A `date` gives only its date in 120, 121 and 126, and 126 leaves off a fraction of zero |
| `CAST` of a `datetime2(n)` or `time(n)` to text | Its `n` digits of a second, zeros too: a `datetime2(6)` is `2024-02-29 13:04:05.000000`, as a Fabric Warehouse gave it in live run 11 |
| `CONVERT` and `TRY_CONVERT` from text, the same styles | The date the style reads (`'29/02/2024'` with 103), to a `date`, `datetime` or `datetime2`. A date that doesn't exist is an error with `CONVERT` and `NULL` with `TRY_CONVERT`. Styles 120, 121 and 126 read ISO text with or without its time |

What can still differ:

- A date-and-time here is a warehouse's `datetime2(6)`: six digits of fraction (`.123000`), which is also the most a Fabric Warehouse column keeps. T-SQL's default `datetime2` has seven.
- Any other `CONVERT` date style runs as translated, but its values haven't been checked. The run's `translation.notes` says so ("CONVERT style 104 runs as a translation that hasn't been checked ...").
- `CAST(a_datetime AS VARCHAR)` with no style, and `nanosecond` in `DATEADD`, `DATEDIFF` and `DATETRUNC`: see [Local data](local-data.md#t-sql).

## Local T-SQL formatting

When a project runs T-SQL on this PC, `FORMAT`, `PARSE` and `TRY_PARSE` give the answer .NET gives, as a warehouse's do. Where .NET's own versions answer differently, or the case isn't carried yet, the query stops and names it. It never gives another answer.

| What | Here, as in a warehouse |
| --- | --- |
| Cultures | `en-US` (a warehouse's default, also when none is named), `en-GB` and `de-DE`, written in the query in any case (`'DE-de'`). Any other culture is refused by name |
| `FORMAT` of a number: `N`, `F`, `D` | `FORMAT(1234.5, 'N')` is `1,234.50`, and `1.234,50` in `de-DE`. Rounded half away from zero |
| `FORMAT` of a number: `X` | Hexadecimal for an integer: `FORMAT(255, 'X4')` is `00FF`, and a negative `int` is its 32 bits, so `FORMAT(-1, 'X')` is `FFFFFFFF` |
| `FORMAT` of a number: `C` | `en-US` only, at zero or above: `FORMAT(1234.5, 'C')` is `$1,234.50` |
| `FORMAT` of a number: custom | `0`, `#`, `.`, `,` between digits, `%`, text in quotes or after `\`, and a second section for numbers below zero, written without their `-`: `'#,##0.00'`, `'0.0%'` (`25.0%`), `'000-00-0000'` (`123-45-6789`), `'$#,##0.00;($#,##0.00)'` (`($5.50)`). The culture's separators replace `,` and `.` |
| `FORMAT` of a date | .NET's custom date formats, where `/` is the culture's date separator (`dd/MM/yyyy` is `05.03.2024` in `de-DE`) and `dddd` and `MMMM` are its day and month names (`Dienstag`, `März`). The standard formats `d`, `g`, `G`, `t` and `T` in each culture, `s`, `u`, `o` and `R` in all, and every other one in `en-US` |
| `PARSE`, `TRY_PARSE` | Text read as a number (`int`, `bigint`, `smallint`, `tinyint`, `decimal`, `float`, `real`) in the culture: a sign, digits, the culture's separator between thousands and its decimal separator, with spaces around. `PARSE('1.234,5' AS DECIMAL(10, 2) USING 'de-DE')` is `1234.50`. Text with no digit is T-SQL's "Error converting string value" with `PARSE` and `NULL` with `TRY_PARSE`, as is a number too big for the type with `TRY_PARSE` |
| `STR` | Right-aligned text of the length asked (10), rounded, with fewer decimals when they don't fit and asterisks when the whole number doesn't: `STR(1234.5)` is `'      1235'` |

Refused, each by name:

- `FORMAT`'s `P` (percent), `C` below zero or outside `en-US`, a culture's `D`, `f`, `F`, `M` and `Y` outside `en-US`, and its abbreviated names (`ddd`, `MMM`) and AM/PM (`tt`) outside `en-US`: .NET Framework and .NET (Core) write them differently, for instance `12.34 %` or `12.34%`, `($5.00)` or `-$5.00`. A custom format such as `'0.00%'` gives one answer.
- `FORMAT` of a negative number that the format rounds to zero (`FORMAT(-0.001, '0.00')`, `FORMAT(-0.001, 'N2')`, `'F2'` too): one .NET writes `-0.00`, the other `0.00`. Round the value first.
- `FORMAT`'s `E`, `G` and `R` number formats, a precision of more than two digits, a custom format with a third section, scientific notation, `‰`, more than one `%`, `,` right before the point (which divides by 1,000), text among the digits with `#` or `,`, and `FORMAT` of a `float`, `money` or `time`.
- `PARSE` and `TRY_PARSE` of text .NET may read another way: a currency sign, parentheses, an exponent, a sign at the end, `Infinity`, decimals for an integer type, or separators away from groups of three. Text with no digit at all gives T-SQL's answer.
- `PARSE` and `TRY_PARSE` to a date or time (use `CONVERT` with a style), and to `money`, which isn't carried (use `DECIMAL(19, 4)`). `CONVERT`'s `money` styles are refused with `money`.

The static check and **Format SQL** can't read `PARSE`'s `AS ... USING` yet: they mark the query unverified and leave it as written. The live check (`fabric-desktop smoke-test`, flow 17) asks a test warehouse for the answers .NET's versions disagree on, so they can be carried once known.

## Local T-SQL text

When a project runs T-SQL on this PC, text compares as it does with a Fabric Warehouse's default collation, `Latin1_General_100_BIN2_UTF8`: by code point, so case counts and `'B'` sorts before `'a'`, and with ANSI padding, so trailing spaces don't count. These are checked against T-SQL's reference:

| What | Here, as in a warehouse |
| --- | --- |
| `=`, `<>`, `<`, `IN`, `JOIN ... ON` | `'a' = 'a  '` is true and `'a' = 'A'` is false. Only spaces are taken off: `'a' = 'a' + NCHAR(160)` (a no-break space) is false |
| `LIKE` | Case counts. The text's trailing spaces don't count, any number of them, but the pattern's do: `'abc  ' LIKE 'abc'`, `'abc  ' LIKE 'abc_'` and `'abc  ' LIKE 'abc__'` are true, and `'abc' LIKE 'abc '` is false |
| `CHARINDEX` with a `NULL` start | `NULL` |
| `ORDER BY`, `STRING_AGG(...) WITHIN GROUP (ORDER BY ...)` | Code point order (`A`, `B`, `a`, `b`). `STRING_AGG` skips `NULL`s, and is `NULL` over no rows |
| `LEN`, `DATALENGTH` | `LEN('ab  ')` is `2`, and `DATALENGTH('ab  ')` is `4`. `DATALENGTH` counts UTF-8 bytes (`'é'` is `2`) and two per character for `N'...'` |
| `CHARINDEX`, `PATINDEX` | Case counts, and the answer is `0` when the text isn't found. A `CHARINDEX` start of 0 or below searches from the start, and an empty search text gives `0` |
| `CONCAT`, `CONCAT_WS`, `+` | `CONCAT` skips `NULL`, and `+` gives `NULL`. A number written with a point keeps its digits (`CONCAT('a', 1.50)` is `a1.50`), and a `bit` is `1` or `0` |
| `N'...'` text | The same as `'...'` in comparisons, `LIKE` and `+`: `WHERE name = N'abc'` finds `'abc  '`, and `N'a' + N'b'` is `ab`. Only `DATALENGTH` counts it as nvarchar, two bytes a character, and only for an `N'...'` written directly inside it: `DATALENGTH(N'ab')` is `4`, but `DATALENGTH(N'a' + N'b')` is `2` here, where T-SQL gives `4` |
| `TRIM`, `LTRIM`, `RTRIM`, `REPLACE` | Spaces only, or the characters you give. `REPLACE` matches case and leaves the text alone for an empty search |
| `COLLATE` | A binary collation (`..._BIN2`, `..._BIN`) runs as written, since it's how text compares here already |
| `REGEXP_LIKE`, `REGEXP_REPLACE`, `REGEXP_COUNT`, `REGEXP_INSTR` flags | `m` lets `^` and `$` match at each line and `s` lets `.` match a line break, also together (`'sm'`). Of `c` and `i` the last one counts, and `c` is taken by `REGEXP_COUNT` and `REGEXP_INSTR` too. `REGEXP_LIKE('a' + CHAR(10) + 'b', '^b$', 'm')` is true |
| `REGEXP_COUNT`, `REGEXP_INSTR` | An `int`, so `REGEXP_COUNT(s, 'b') / 2` divides as whole numbers |

What still differs:

- `GROUP BY`, `DISTINCT`, `UNION`, `INTERSECT` and `EXCEPT` keep `'a'` and `'a  '` apart, where T-SQL counts them as one value. A query that groups text says so in `translation.notes`. Group on `RTRIM(...)` to get T-SQL's groups. (`COUNT(DISTINCT ...)` counts them as one, as T-SQL does, and the table below shows it.)
- Any other collation (`COLLATE Latin1_General_100_CI_AS_KS_WS_SC_UTF8`, `SQL_Latin1_General_CP1_CI_AS`) is refused by name. Compare `UPPER(...)` on both sides to ignore case.

### Local T-SQL collation

A Fabric Warehouse's default collation is `Latin1_General_100_BIN2_UTF8`, case-sensitive ([Microsoft: collation in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/collation)). A `BIN2` collation sorts and compares by Unicode code point, so case and accents count and `'B'`, `'Z'` sort before `'a'`, and `'z'` before `'é'` ([Microsoft: collation and Unicode support](https://learn.microsoft.com/sql/relational-databases/collations/collation-and-unicode-support)). On top of that, T-SQL compares text with ANSI padding: text that differs only in trailing spaces is equal. The local engine runs every T-SQL query this way. Each row below is checked in `sidecar/tests/test_tsql_collation.py` (WR-246).

| What | Here, as in a warehouse |
| --- | --- |
| `ORDER BY`, `MIN`, `MAX`, `<`, `>` | Code point order, `NULL` first ascending and last descending: `NULL`, `''`, `A`, `B`, `Z`, `a`, `e`, `z`, `é`. `MIN`/`MAX` of `'a'` and `'a '` may show either, as in T-SQL |
| `ORDER BY s, n` | `'a'` and `'a '` are equal in `s`, so `n` orders them. This holds for an output column named by its alias or number (`ORDER BY 1, 2`) |
| `BETWEEN`, `IN (SELECT ...)`, `= ANY (SELECT ...)` | Trailing spaces don't count: `'a ' BETWEEN 'a' AND 'a'` is true, and `'a '` is `IN (SELECT 'a')` |
| `NULLIF`, `CASE s WHEN 'a' THEN ...` | `NULLIF('a ', 'a')` is `NULL`, and `CASE 'a ' WHEN 'a'` takes that branch. `NULLIF` otherwise shows its first value as it is |
| `JOIN ... ON a.s = b.s`, `ON a.s = b.s + ''` | `'a'` joins `'a '` |
| `COUNT(DISTINCT s)` | `'a'`, `'a '` and `'a  '` count once |
| `RANK`, `ROW_NUMBER`, `SUM(...) OVER` | A window's `PARTITION BY` and `ORDER BY` treat `'a'` and `'a '` as one value: they rank as ties, share a partition, and are peers in the default `RANGE` frame |
| `LIKE`, `UPPER`, `LOWER` | `LIKE` is case-sensitive (`'a%'` doesn't find `'A'`). `UPPER('é')` is `É` |
| `COLLATE Latin1_General_100_BIN2_UTF8`, `COLLATE database_default` | Run as written, because that's how text compares here already |
| `COLLATE Latin1_General_100_BIN2`, `..._BIN` | Run as `Latin1_General_100_BIN2_UTF8`, with a note. They agree on ASCII, but a warehouse first converts other text to that collation's code page |
| `VARCHAR(n) COLLATE Latin1_General_100_BIN2_UTF8` on a column | Runs. Any other column collation, including the case-insensitive `Latin1_General_100_CI_AS_KS_WS_SC_UTF8` a warehouse can be made with, is refused by name |
| `JOIN ... USING` | Refused, because it isn't T-SQL and a warehouse refuses it. Use `JOIN ... ON` |

What still differs, and how it shows:

- A warehouse made case-insensitive (`Latin1_General_100_CI_AS_KS_WS_SC_UTF8`, set for the workspace or the warehouse when it's created) compares `'a' = 'A'` as true. The local engine always compares as the default, case-sensitive collation. The static check's SEM-006 warns about a collation name it reads.
- `ORDER BY` text then another key after a `UNION`, `INTERSECT` or `EXCEPT` keeps `'a'` before `'a  '` instead of letting the next key order them, with a note. Put the set operation in a `FROM` and order by `RTRIM(...)`.
- Text compares here without its trailing spaces, where T-SQL pads the shorter side with spaces. The two differ only for a character below a space (a tab or another control character) after the shorter text. For example, T-SQL sorts `'a' + CHAR(9)` before `'a'`, and here it sorts after.
- Where the types in a comparison can't be told (a table the local engine doesn't know), the comparison is done as DuckDB does it, with a note.
- `PATINDEX` needs its pattern written out as text, and is refused otherwise.

T-SQL's reference doesn't give the answers for `LIKE` with a wildcard on text with trailing spaces, or `CHARINDEX` with a `NULL` start. A Fabric Warehouse gave the ones in the table above in live run 11 (2026-11-17), in the warehouse live check, flow 17 of the [smoke test](product/smoke-test/README.md#flow-17-the-warehouse-live-check).

## Regular expressions and text on this PC (LOC-002)

Spark reads a regular expression the Java way (`java.util.regex`), and DuckDB reads it with RE2. On this PC a pattern written in the query, or given as a Python string to the DataFrame API's `F.regexp_replace`, `F.regexp_extract`, `F.split` and `Column.rlike`, is translated for RE2 before it runs, in `rlike`, `regexp`, `regexp_like`, `regexp_extract`, `regexp_extract_all`, `regexp_replace`, `split`, `regexp_count`, `regexp_instr` and `regexp_substr`, in Spark SQL and Databricks SQL. Each case below is checked against Spark 3.5's documented behaviour in `sidecar/tests/test_spark_strings.py`. None has been compared with a Fabric run since the change, so treat them as UNVERIFIED in Fabric.

| What | On this PC, as in Spark |
| --- | --- |
| Named groups `(?<name>...)` and `${name}` in a replacement | Read as Java reads them |
| POSIX classes `\p{Alpha}`, `\p{Digit}`, `\p{Punct}` and the others, and `\P{...}` | ASCII only, as in Java |
| `\s`, `\h`, `\v`, `\R`, `\uXXXX`, `\0nn`, `\e`, `\cX`, `\Q...\E` | Java's characters. `\s` takes the vertical tab, as Java's does. Two `\u` halves of a UTF-16 pair, `\uD83D\uDE00`, are one character, and a backslash before a character outside ASCII (`\é`) is that character |
| `regexp_replace`'s replacement | Java's: `$1` and `${name}` are groups, and `\` takes the next character as it is (`\$` is a dollar sign). A `$` that refers to no group stops, as in Spark |
| `regexp_replace` with a position | The first position is 1. Position 0 or less stops, as in Spark |
| `regexp_extract`, `regexp_extract_all` | Group 1 unless you give one. A group index the pattern doesn't have, or below 0, stops by name. A NULL index gives NULL. A group that took no part in a match is `''`, in `regexp_extract_all`'s array too: `regexp_extract_all('abab', '(a)\|(b)', 2)` is `['', 'b', '', 'b']` |
| `regexp_count` | An `int`. An empty pattern counts each position, as Java does: `regexp_count('abc', '')` is 4 |
| `regexp_instr` | An `int`: where the first match starts, from 1, or 0. An empty match counts (`regexp_instr('xab', 'a*')` is 1, and `regexp_instr('abc', '')` is 1). The third argument is a group number that Spark only checks, not a start: `regexp_instr('bcx', '(b)(c)', 2)` is 1. A character past U+FFFF counts 2, as in a Java string: `regexp_instr('😀a', 'a')` is 3 |
| `regexp_substr` | NULL when nothing matches, and for an empty match: `regexp_substr('xab', 'a*')` is NULL |
| `split` | Every match splits, and empty parts at the end are kept: `split('a,b,,', ',')` has 4 parts |
| Any of these with a NULL | NULL |

These are refused by name, and nothing runs, because RE2 has no reading for them:

- Lookaheads and lookbehinds (`(?=`, `(?!`, `(?<=`, `(?<!`), atomic groups (`(?>`), backreferences (`\1`, `\k<name>`) and possessive quantifiers (`*+`, `++`, `?+`).
- `\Z`. Use `\z` for the very end.
- The flags `x`, `d` and `U` (`(?x)`). RE2's `U` means something else.
- A class inside a class, such as `[a[b]]` or `[[:alpha:]]`, and class intersections (`&&`). Write the characters in one class, or use `\p{Alpha}`.
- A group past 9 in `regexp_extract` or a replacement.
- A `{` that isn't a repeat such as `{2}` or `{2,5}` (`a{`, `{"id"`). Spark stops on it too ("Illegal repetition"). Write `\{`.
- Half of a UTF-16 pair on its own (`\uD83D`). Write the whole character, such as `\x{1F600}`.
- `regexp_replace` with a pattern that can match empty text right after another match, such as `a*`, `[^,]*` or `\s*$`, and a replacement that isn't `''`: Java replaces that empty match too (`regexp_replace('aaa', 'a*', 'X')` is `XX`), and RE2 skips it. A pattern that only matches empty text (`^`, `$`, `\b`) or starts with `^` runs, and so does any pattern with `''` as the replacement. The DataFrame API's `F.regexp_replace` is checked the same way.
- With DuckDB 1.x, `regexp_count` and `regexp_extract_all` with a pattern that can match empty text at `^`, `$`, `\b` or `\B` (`regexp_count(s, '\\b')`, `regexp_count(s, '(?m)^')`), unless it starts with `^`: DuckDB 1.x counts some of those empty matches twice. With DuckDB 2.0 they run, and count as Java does.
- `\p{In...}` blocks, `\p{java...}` classes and binary properties such as `\p{IsAlphabetic}`. Use a category such as `\p{L}` or a script such as `\p{IsLatin}`.

String functions that give Spark's answer on this PC, where DuckDB's own differs:

| Function | On this PC, as in Spark |
| --- | --- |
| `length`, `char_length`, `octet_length`, `bit_length`, `instr`, `locate`, `position`, `levenshtein` | An `int` |
| `split_part` | An empty delimiter doesn't split the text: part 1 or -1 is the whole text. Index 0 stops, as in Spark |
| `left`, `right` | A length of 0 or less gives `''` |
| `lpad`, `rpad` with an empty pad | The text, cut to the length |
| `locate(substr, str, pos)` | 0 for a position before 1, or a NULL position |
| `chr`, `char` | `''` below 0, and the character for n % 256 from 256 on: `chr(321)` is `A` |
| `LIKE`, `ILIKE` | `\` escapes the next `_` or `%`, as Spark's default escape does |
| `concat` with a NULL | NULL, as a `string` |
| `concat_ws` with an array, such as `array(...)`, `split(...)` or `collect_list(...)` | The array's elements joined, NULLs left out |

These cases can still differ. LOC-002 warns wherever a pattern is used:

| Case | Spark | This PC |
| --- | --- | --- |
| A pattern taken from the data, not written in the query | Java's syntax | RE2's: not translated, and a lookaround stops with DuckDB's message |
| `$` without the `m` flag, on text that ends in a line break | Matches just before the final line break | Matches only at the very end |
| `.` and `\r`, `\u0085`, `\u2028`, `\u2029` | Doesn't match them | Matches them. Both leave out `\n` |
| `(?m)` with `^` and `$` next to `\r`, `\u0085`, `\u2028`, `\u2029`, or `^` after a final line break | Each is a line end, and `^` doesn't match at the very end | Only `\n` ends a line, and `^` matches after a final `\n` |
| `(?i)` with letters outside ASCII | Without `(?u)`, only ASCII letters ignore case | Every letter ignores case |
| `\b` next to a letter outside ASCII | Java counts it as a word character | RE2 doesn't |
| `split` with a pattern that can match no text, taken from the data or given to the DataFrame API's `F.split` | The array ends with an empty part | No empty part at the end. One written in the query is refused (see above) |
| `regexp_replace` with a replacement taken from the data that has a `$` or `\` | Read as groups and escapes | Refused by name |
| `concat_ws` with an array column (not an array expression in SQL), or any array in the DataFrame API's `F.concat_ws` | The elements joined | The array written as text, such as `[a, b]` |
| A pattern in the DataFrame API given as a column, such as `F.regexp_like(c, F.lit('...'))` | Java's syntax | RE2's: only a pattern given as a Python string is translated |
| The DataFrame API's `F.split_part` with an empty delimiter, and `F.concat` with a NULL | As in the table above | `''`, and an `int` NULL. The DataFrame API's other string functions weren't compared. Use the SQL form, `F.expr(...)`, for Spark's |
| The DataFrame API's `F.upper` of `'ß'` | `SS` | `ẞ`. Use `F.expr("upper(...)")` for Spark's |

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

## Local T-SQL CTEs

When a project runs T-SQL on this PC, common table expressions (`WITH ... AS`) run as in a Fabric Warehouse, and what a warehouse doesn't run is refused by name before anything runs. This was checked against Microsoft Learn's T-SQL surface area page for Fabric Data Warehouse, as it read on 2026-11-18.

| What | Here, as in a warehouse |
| --- | --- |
| Several CTEs in one `WITH` | Each sees the CTEs before it. A later CTE's name, used before it, is a table's name, as in T-SQL |
| A column list, `WITH a (p, q) AS (...)` | Names the columns |
| A CTE before `SELECT`, `INSERT`, `UPDATE ... FROM` and `DELETE ... WHERE` | Runs, and changes the table the statement names |
| A `WITH` inside a CTE (a nested CTE) | Runs. A warehouse runs it as a preview feature, so the result's notes say so |
| Two CTEs of one name | Stops with SQL Server's "Duplicate common table expression name 'a' was specified." |

Refused by name:

- A recursive CTE, one whose query names itself. A warehouse doesn't run recursive queries. It stops before it runs, so a CTE with no end can't run forever, and nothing changes. Unroll the levels you need with joins, or compute the hierarchy in a notebook.
- `OPTION (MAXRECURSION n)`, which only limits a recursive CTE.
- `WITH RECURSIVE`, which isn't T-SQL.
- `UPDATE` or `DELETE` through a CTE (`WITH a AS (...) UPDATE a SET ...`). The local engine changes tables only, and Microsoft's page doesn't say whether a warehouse does. Name the table, and put the CTE's filter in `WHERE`.

## Local T-SQL hints, labels and statistics

When a project runs T-SQL on this PC, a hint, label or statistics statement either changes no rows and is left out of the run, with a note in the result that says so, or is refused by name. None is dropped without a note. This was checked against Microsoft Learn's Fabric Data Warehouse pages for the T-SQL surface area, query labels, statistics and transactions, and the Fabric section of Hints (Transact-SQL), as they read on 2026-11-18.

| What | Here |
| --- | --- |
| `OPTION (LABEL = '...')` | Left out, with a note. A warehouse shows the label in query insights; it changes no rows |
| `OPTION (...)` with `HASH GROUP`, `ORDER GROUP`, `MERGE UNION`, `HASH UNION`, `CONCAT UNION`, `FORCE ORDER`, `FORCE SINGLE NODE PLAN`, `FORCE DISTRIBUTED PLAN`, `DISABLE_RESULT_SET_CACHE`, or `USE HINT` with the four `ASSUME_..._FILTER_ESTIMATES` names | Left out, with a note. These are the hints Microsoft lists for a warehouse, and each changes how it plans, not the rows |
| A `LOOP`, `HASH` or `MERGE` join hint in `FROM`, such as `INNER HASH JOIN` | Left out, with a note |
| A locking table hint: `NOLOCK`, `READUNCOMMITTED`, `HOLDLOCK`, `TABLOCK`, `ROWLOCK`, `READPAST` and the others, also written `t (NOLOCK)` | Left out, with a note. A warehouse always runs snapshot isolation, and reads here are snapshot reads too, so the hint changes no rows. Microsoft's pages don't say whether a warehouse takes it |
| `CREATE STATISTICS s ON t (c)`, `UPDATE STATISTICS t [(s)]`, `DROP STATISTICS t.s`, with `WITH FULLSCAN` or `SAMPLE n PERCENT` | Changes nothing, with a note. It checks that the table (and for `CREATE`, the column) exist, so a name that isn't there is an error as in a warehouse. `DROP` doesn't check the statistic itself |

Refused by name:

- Any other query hint, such as `MAXDOP`, `RECOMPILE`, `LOOP JOIN`, `FAST n` or another `USE HINT` name, since Microsoft doesn't list it for a warehouse. A `REMOTE` join hint in `FROM` is refused for the same reason. `MAXRECURSION` is refused with recursive CTEs.
- Index hints (`INDEX(...)`, `FORCESEEK`, `FORCESCAN`, `NOEXPAND`), because a warehouse has no indexes to name.
- A statistic on more than one column, which a warehouse refuses too, filtered statistics (`WHERE`), and statistics options other than `FULLSCAN` and `SAMPLE`.

## Local T-SQL writes at the same time

A Fabric Warehouse runs every transaction with snapshot isolation. When two transactions change the same table, the first to commit wins, and the other is rolled back with error 24556 ("Snapshot isolation transaction aborted due to update conflict"). An `INSERT` adds new files and rarely conflicts. This was checked against Microsoft Learn's Fabric Data Warehouse page for transactions, as it read on 2026-11-18.

On this PC:

| What | Here |
| --- | --- |
| Two sessions in the app (two SQL tabs, a tab and a pipeline step) changing one table at once | They take turns: a project runs one statement at a time, so every change lands and none is lost. A read sees a change whole or not at all |
| Another writer (a notebook, another tool) commits to a project Delta table between this statement's read and its commit, with an `UPDATE`, `DELETE` or `MERGE` | This statement stops with "Nothing was committed to …, because another writer changed it after this statement read it" (a Fabric Warehouse reports this as error 24556). The table then shows the other writer's change, and running it again applies it on top |
| Another writer's `INSERT` beside this statement's `UPDATE` | Both commit, as in a warehouse |
| Another writer commits to a project Delta table (adds or deletes rows, adds a column, replaces it, runs `VACUUM`), makes one in `Tables` or deletes one | The next statement reads the table as that writer left it, without a refresh, as a warehouse's next snapshot would |
| Another writer changes a project Delta table's columns, properties or constraints while this statement's `UPDATE`, `DELETE` or `MERGE` runs | Stopped as above, with "Nothing was committed to …" (error 24556), and the table keeps the other writer's change |
| Another writer deletes a project Delta table's folder while this statement changes it | Stops with "… was dropped by another writer while this statement ran", nothing of it kept, and the table is gone from the list. A warehouse then says `Invalid object name` |
| Two sessions in the app: one adding, dropping or replacing a table or its columns, the other reading or changing it | They take turns, so every statement sees the table whole, before or after the change. A read after a `DROP TABLE` says the table doesn't exist |
| `ALTER TABLE … ADD`, `DROP` or `ALTER COLUMN` on a project Delta table | Refused by name. Write the table again with the columns you want: `CREATE OR REPLACE TABLE … USING DELTA AS SELECT … FROM …`, which keeps every version before it |
| `TRUNCATE TABLE` on a project Delta table | Refused by name. `DELETE FROM` the table removes every row the same way, as a new version |

What can still differ:

- A warehouse finds conflicts by table, so two changes to different rows of one table can conflict there. Here delta-rs finds them by the files each change read, so two changes to different files of one table can both commit.
- `BEGIN TRAN`, `COMMIT`, `ROLLBACK` and `SET TRANSACTION` are refused here (see Local T-SQL scripts), so a conflict here is always about one statement.
- A commit delta-rs refuses for another reason, such as a change to a table with `delta.appendOnly`, isn't called a conflict. It stops with delta-rs's own reason and says nothing was committed.

## Local T-SQL errors

When a T-SQL statement on this PC names a column or table that isn't there, the error uses SQL Server's own words, as a Fabric Warehouse does:

| A warehouse says | When |
| --- | --- |
| `Invalid column name 'x'.` (error 207) | A column that isn't in the tables the statement reads, in `SELECT`, `WHERE`, `UPDATE ... SET` or an `INSERT` column list |
| `Invalid object name 'dbo.x'.` (error 208) | A table or view that isn't there, with its schema as the statement wrote it, and a `#temp` table after it's dropped |
| `Ambiguous column name 'x'.` (error 209) | A column two of the tables the statement reads both have |

Any other error from the local engine keeps its own words, without a "Did you mean" that names one of DuckDB's system tables (such as `pg_catalog.pg_settings`), which a warehouse project never has. `DROP TABLE` of a table that isn't there, and a column prefixed by a table alias that isn't in `FROM` (`x.id`), keep the local engine's words too, because a warehouse answers those with other errors (3701 and 4104) whose text the local engine can't give in full.

Where SQL Server's number is certain, the error shows it and its severity as SQL Server prints them, such as `Msg 207, Level 16: Invalid column name 'x'.` That covers 207, 208 and 209 above, `Msg 239` for two CTEs of one name, `Msg 8134` for `Divide by zero error encountered.` (`/` and `%`, for integers, decimals and floats), and the arithmetic and conversion errors below. In a script, the error also names the statement and what ran before it: `Statement 2 of 3 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. Statement 1 ran, and what it changed is kept; the last statement didn't run.` No number is guessed for any other error. SQL Server's state and line aren't shown, and the statement number takes the line's place.

Arithmetic and conversion errors (WR-247), with SQL Server's words as Microsoft Learn's T-SQL pages and SQL Server's message list give them:

| A warehouse says | When |
| --- | --- |
| `Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int.` (or `bigint`, `numeric`) | `+`, `-`, `*`, `ABS` or `-x` past an `int` or `bigint`, or a decimal past 38 digits: `SELECT 2147483647 + 1`, `ABS(CAST(-2147483648 AS INT))`. `SUM` of an `int`, `smallint` or `tinyint` is an `int`, and of a `bigint` a `bigint`, so a total past it stops too (it came back as a wider number before). A column's value cast to a smaller integer type names that type (`smallint`, `tinyint`) |
| `Msg 232, Level 16: Arithmetic overflow error for type int, value = 2147483648.000000.` | `POWER` of an integer past an `int`: `POWER(2, 31)` |
| `Msg 245, Level 16: Conversion failed when converting the varchar value 'x' to data type int.` | Text that isn't a whole number, to `int`, `bigint`, `smallint` or `tinyint`, in a `CAST`, `CONVERT`, `+` or a comparison: `CAST('1.5' AS INT)`. `N'...'` text is named `nvarchar` |
| `Msg 248, Level 16: The conversion of the varchar value '3000000000' overflowed an int column.` | A whole number in text too big for an `int` |
| `Msg 8114, Level 16: Error converting data type varchar to numeric.` (or `float`, `real`) | Text that isn't a number, to a decimal or a float: `CAST('abc' AS DECIMAL(5,2))`, and `''` to a decimal |
| `Msg 241, Level 16: Conversion failed when converting date and/or time from character string.` | Text that isn't a date or time, to `date`, `time`, `datetime2` or `datetime`, or a date that isn't there (`'2024-02-30'`) to `date` or `datetime2`. For `datetime` and `smalldatetime` that's `Msg 242` (`The conversion of a varchar data type to a datetime data type resulted in an out-of-range value.`) |

A whole number written in a query that's too big for an `int` is a `decimal` of its digits, as in T-SQL, so `9223372036854775807 + 1` is `9223372036854775808` and `ABS(-2147483648)` is `2147483648`, where the local engine overflowed a `bigint`. A result column that is only such a number is a `decimal` too, not a `bigint`, so a query that reads it as an integer may need a `CAST`. When an error shows a value, it shows the value's first 1,000 characters.

These keep the local engine's words, with no number, because SQL Server's words depend on what the local engine's message doesn't say:

- A number written in the query cast to a smaller type, such as `CAST(300 AS TINYINT)` or `CAST(2147483648 AS INT)`: SQL Server says error 220 or 8115, by the constant's type.
- A cast to a smaller decimal, such as `CAST(123.45 AS DECIMAL(4,2))` and `CAST('99999' AS DECIMAL(5,2))`: SQL Server's 8115 names the source type (`converting numeric to data type numeric`).
- Whole-number text too big for a `bigint`, `smallint` or `tinyint` (SQL Server's 244 or 8114), and an expression (not a column) cast to a smaller integer type.
- `+`, `-` or `*` past a `smallint` or `tinyint`, and an `int`'s smallest value divided by `-1` (`Overflow in division of -2147483648 / -1` here; the local engine says the same for `%`).

One word can differ: `N'...'` text that isn't a number, to a decimal or a float, shows `Msg 8114` naming `varchar`, where SQL Server says `nvarchar`, because the local engine's message doesn't say which it was.

`@@ERROR`, `ERROR_NUMBER()`, `ERROR_MESSAGE()`, `ERROR_SEVERITY()`, `ERROR_STATE()`, `ERROR_LINE()` and `ERROR_PROCEDURE()` read an error after it happened, in `TRY...CATCH` or the next statement. The local engine doesn't run `TRY...CATCH` yet, so each is refused by name. Run the batch in a Fabric Warehouse to handle the error in T-SQL.

## Local T-SQL wide tables and long values

When a project runs T-SQL on this PC, wide tables and long values stay exact, or stop by name where a Fabric Warehouse differs. Nothing is cut without saying so. This was checked against Microsoft Learn's Fabric Data Warehouse pages for tables and data types, and the REPLICATE page, as they read on 2026-11-18.

| What | Here |
| --- | --- |
| A table of up to 1,024 columns | Made, and every value comes back |
| `CREATE TABLE`, `SELECT ... INTO` or `ALTER TABLE ... ADD` past 1,024 columns, a warehouse table's most | Stops with SQL Server's words, such as "CREATE TABLE failed because column 'c1024' in table 'w' exceeds the maximum of 1024 columns.", and nothing is made. `SELECT *` is counted as it reads |
| A query result wider than 1,024 columns | Runs. The limit is a table's |
| A `VARCHAR(MAX)` value of a megabyte or more | Comes back whole, and `LEN` and `DATALENGTH` count all of it |
| `REPLICATE` of text that isn't `VARCHAR(MAX)` or `NVARCHAR(MAX)`, past 8,000 bytes (4,000 characters of `N''` text) | Stops at the row where a warehouse would cut the result, with the way to keep it whole: cast the input to `VARCHAR(MAX)`. A `VARCHAR(MAX)` column without a cast stops too, because the local engine doesn't keep a column's `(MAX)`, and the cast changes nothing in a warehouse |
| A query that nests string calls dozens deep, or nests anything past what the translator can read | Stops by name and says the query is nested too deeply, so move the inner part into a CTE, a subquery or a variable. A `REPLICATE` inside another one is checked at each level. |
| An Excel export of more than 16,384 columns, a sheet's most | Refused before anything is written, with CSV or Parquet named instead. A text cell past Excel's 32,767 characters is cut there with a note, as before |

What can still differ:

- A warehouse stores at most 16 MB in a `VARCHAR(MAX)` or `VARBINARY(MAX)` value. The local engine keeps a longer one.
- `ALTER TABLE ... ADD` of several columns in one statement is refused by name here, because the local engine adds one at a time. Add each in an `ALTER TABLE` of its own.

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

## Local T-SQL data types

T-SQL run on this PC takes the data types Microsoft's "Data types in Fabric Data Warehouse" page lists, as it read on 2026-11-17, and refuses the rest by name with the type that page says to use instead.

| Type | In a table or view | In a query (`CAST`, `CONVERT`) |
| --- | --- | --- |
| `bit`, `smallint`, `int`, `bigint`, `decimal`/`numeric`, `float`, `real`, `date`, `char`, `varchar`, `varbinary`, `uniqueidentifier` | Yes | Yes |
| `datetime2(n)`, `time(n)` | Only with n up to 6: `datetime2` with no precision is `datetime2(7)` and is refused, naming `datetime2(6)` | Yes, kept to the microsecond (a 7th digit is cut) |
| `datetime` | Refused: use `datetime2(6)` | Yes, rounded as T-SQL rounds it, to 1/300 of a second (`.002` is `.003`, `.005` is `.007`, `23:59:59.999` is the next day). Before 1753 or after 9999-12-31 23:59:59.997 is T-SQL's out-of-range error (`NULL` with `TRY_CAST` and `TRY_CONVERT`), and text with more than three digits of a second is T-SQL's conversion error. `CONVERT` with style 121 or 126 writes it to the millisecond, as T-SQL does |
| `smalldatetime` | Refused: use `datetime2(0)` | Yes, rounded to the minute (29.998 seconds rounds down, 29.999 up), from 1900-01-01 to 2079-06-06 23:59 |
| `nchar`, `nvarchar`, `text`, `ntext` | Refused: use `char`, `varchar` or `varchar(max)` | Yes, as text |
| `image` | Refused: use `varbinary(max)` | Yes, as `varbinary` |
| `tinyint` | Refused: use `smallint` | Yes, 0 to 255 |
| `money`, `smallmoney` | Refused: use `decimal(19, 4)` or `decimal(10, 4)` | Refused by name (their conversion styles aren't carried) |
| `datetimeoffset`, `xml`, `geography`, `geometry`, `json`, `sql_variant`, `rowversion` (`timestamp`), `hierarchyid`, `vector`, CLR types | Refused, with Microsoft's alternative | Refused by name: the local engine has no form of them. Run the query in a Fabric Warehouse |
| `sysname` | As `nvarchar(128)`: refused | Yes, as `varchar(128)` |

"In a table or view" covers `CREATE TABLE`, `ALTER TABLE ... ADD` and `ALTER COLUMN`, `CREATE TABLE ... AS SELECT` and `SELECT ... INTO` with a column cast to the type, and `CREATE VIEW` with a column cast to it.

A `#temp` table takes `nchar`, `nvarchar`, `text`, `ntext`, `image` and `tinyint`, with a note in `translation.notes`: Microsoft's temp table page lists only the warehouse's table types for #temp tables, so a warehouse may refuse them there. A `datetime` or `smalldatetime` column in a #temp table is refused, because a stored value isn't rounded to T-SQL's steps here.

Sizes T-SQL doesn't take stop with T-SQL's message: `varchar(8001)` is "The size (8001) given to the type 'varchar' exceeds the maximum allowed for any data type (8000)." (4000 for `nchar` and `nvarchar`), and `datetime2(8)` is "Specified scale 8 is invalid." A `decimal` with more than 38 digits, or a scale larger than its precision, is refused with a message of the local engine's own.

**Through a Delta table.** Each supported type keeps its exact value when a T-SQL table is written as a local Delta table (`CREATE TABLE ... USING DELTA AS SELECT` in Spark SQL or DuckDB SQL) and read back in T-SQL: a decimal's precision and scale, every digit of a `float` and `real`, `datetime2(6)` to the microsecond from year 1 to 9999, text and bytes in full, and a `uniqueidentifier`. A Delta table keeps its timestamps in UTC, and T-SQL reads them as `datetime2`, the UTC clock time without a zone, as a warehouse's SQL analytics endpoint reads a Delta `TIMESTAMP`. Delta has no time-of-day type, so writing a `time` column to Delta is refused by name; cast it to `varchar` or join it to a date.

What can still differ:

- `CHAR(n)` isn't padded with spaces (see [Local T-SQL tables](#local-t-sql-tables)).
- `GETDATE()` and `CURRENT_TIMESTAMP` give the time to the microsecond here. In T-SQL they're `datetime`, in 1/300-second steps.
- A Delta table's own length limits aren't kept: a `varchar(10)` column written to Delta is text of any length there, as in a Spark table.

## Local T-SQL decimals

When a project runs T-SQL on this PC, arithmetic with a decimal side gets the precision and scale a Fabric Warehouse gives it, by T-SQL's rules ("Precision, scale, and length" on Microsoft Learn). DuckDB's own rules differ: it widens decimals its own way, keeps every digit of scale, and divides decimals as doubles.

| Case | Here, as in a warehouse | What can still differ |
| --- | --- | --- |
| `+`, `-`, `*` of decimals, or a decimal and an integer | T-SQL's type, and its value rounded to that scale. Past 38 digits the scale gives way as T-SQL's rules say (`decimal(38,10) * decimal(38,10)` is `decimal(38,6)`) | A value too big to work out exactly here (more whole digits than DuckDB leaves room for) is worked out in floating point, and the run's notes say so: about 15 significant digits match |
| `/` with a decimal side | T-SQL's type, and the quotient worked out exactly and cut at that scale (`-10.25 / 3` is `-3.416666`, and `-10.25 / CAST(3 AS INT)` is `-3.4166666666666`). An inner division is cut at its own scale first, as in T-SQL. Dividing by zero stops with "Divide by zero error encountered" | A dividend or divisor whose digits don't fit in 38 once moved is divided in floating point, and the notes say so. Also when a side calls `RAND` or `NEWID`, which can't be read twice |
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
| Dividing by zero | `/` and `%` stop with "Msg 8134, Level 16: Divide by zero error encountered.", for integers, decimals and floats. `NULL / 0` is `NULL` |
| A result past `int` or `bigint` | An error, as in T-SQL, in its words: `Msg 8115, Level 16: Arithmetic overflow error converting expression to data type int.` `SUM` of an integer keeps an `int` (or a `bigint`), and a total past it stops the same way (see Local T-SQL errors) |
| Text meets a number | The number's type wins: `'1' + 1` is `2` and `'' + 1` is `1`, while `'1.5' + 1` and `'x' + 1` stop with `Msg 245`, "Conversion failed when converting the varchar value 'x' to data type int." Two texts are concatenated (`'1' + '1'` is `'11'`). In a comparison with an integer or a float, the text is converted as a `CAST` would: `0 = ''` is true, and `2 = '1.5'` is a conversion error |
| `NULL` in `+` and `CONCAT` | `+` with a `NULL` side is `NULL`, for text and numbers (`'a' + NULL`). `CONCAT` takes a `NULL` as `''` (`CONCAT('a', NULL)` is `'a'`, and `CONCAT(NULL, NULL)` is `''`), and `CONCAT_WS` skips it (`CONCAT_WS('-', 'a', NULL, 'b')` is `'a-b'`) |
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

## Local T-SQL procedures, functions and system procedures

When a project runs T-SQL on this PC, procedures, functions and system procedures either run as a Fabric Warehouse documents them or are refused by their own name. None is skipped, so a script never looks as if it made, ran or dropped one. This was checked against Microsoft Learn's T-SQL surface area page for Fabric Data Warehouse and its `sp_rename` reference, as they read on 2026-11-17.

| What | Here |
| --- | --- |
| `EXEC sp_rename 'dbo.old', 'new'` (`@objtype` left out or `'OBJECT'`) | Renames the table, as a warehouse does. Named parameters (`@objname`, `@newname`, `@objtype`) and `sp_rename` with no `EXEC` as a batch's first statement work too |
| `EXEC sp_rename 'dbo.t.old', 'new', 'COLUMN'` | Renames the column. The T-SQL surface area page names `sp_rename` for renaming a warehouse column, though the `sp_rename` reference lists only `'OBJECT'` for a warehouse |
| `INFORMATION_SCHEMA.TABLES`, `COLUMNS`, `VIEWS`, `SCHEMATA` | The project's own tables, views, columns and schemas: not the local engine's system views, and not #temp tables, which T-SQL keeps in tempdb. Their columns are as DuckDB describes them, with a note (see [Local T-SQL names, schemas and views](#local-t-sql-names-schemas-and-views)) |

Refused by name:

- `CREATE`, `ALTER`, `CREATE OR ALTER` and `DROP` of a `PROCEDURE` or `FUNCTION` (scalar, inline table-valued or multi-statement), `EXEC` of a procedure, and a call to a user-defined function (`dbo.f(1)`, `FROM dbo.tvf(1)`, `CROSS APPLY dbo.tvf(o.id)`). A procedure or function takes parameters, which are variables, and runs with control flow, so procedures and functions wait on the local engine's support for variables and control flow, which isn't built yet. Run them in a Fabric Warehouse, or run a procedure's statements one at a time with values in place of its parameters.
- Dynamic SQL: `sp_executesql`, `EXEC ('...')` and `EXEC (@sql)`. The local engine never runs text as SQL.
- Any other system procedure, such as `sp_help`, `sp_helptext`, `sp_columns`, `sp_tables`, `sp_spaceused` or `xp_cmdshell`. INFORMATION_SCHEMA lists the tables, columns and views.
- `CREATE TRIGGER`: a warehouse has no triggers either.
- `ALTER TABLE ... RENAME`, which isn't T-SQL (use `sp_rename`).
- `sp_rename` of another object type (`'INDEX'`, `'DATABASE'`), with a new name that has a period in it (T-SQL would put the period in the name: `'dbo.t2'` names a table `[dbo.t2]`), or of a #temp table. A name a warehouse doesn't take (empty, with `/` or `\`, or ending with a period) stops too.
- Renaming one of the project's Delta tables (its folder keeps its name; copy it with `CREATE TABLE new_name USING DELTA AS SELECT ...`), a table read from a registered folder, or a view (drop it and create it with the new name, as Microsoft recommends for views).
- Renaming a table with a `VARCHAR(n)`, `CHAR(n)`, `VARBINARY(n)` or `BINARY(n)` column, or renaming such a column. The check that holds the column to its length names the table and column in its "would be truncated" message, and would keep the old name. Create the table with the new name and `INSERT ... SELECT` the rows.
- An INFORMATION_SCHEMA view the local engine doesn't have, such as `ROUTINES`, `PARAMETERS` or `VIEW_TABLE_USAGE`. `sys` catalog views are refused as before.

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
| Several statements, or `GO` on a line of its own | Run in order. `GO` splits the script into batches. A `GO` line inside a string, a quoted or bracketed name, or a comment is refused, with its line number |
| A statement that fails part way, or is stopped | Changes no rows. An `INSERT`, `UPDATE`, `DELETE` or `SELECT ... INTO` that fails part way through its rows, or is stopped in the middle, leaves the table as it was, as a warehouse rolls back a statement that fails |
| An error part way through | The script stops at that statement. The error names it and says what ran: "Statement 2 of 3 (SELECT) failed: Msg 8134, Level 16: Divide by zero error encountered. Statement 1 ran, and what it changed is kept; the last statement didn't run." |
| `UPDATE ... SET v = v + 'x'` | The `SET` values and the `WHERE` of an `UPDATE` or `DELETE` follow T-SQL's types, as a `SELECT` does: two texts are joined, and text compares without its trailing spaces (`WHERE v = 'b'` finds `'b  '`) |

Refused by name: variables (`DECLARE`, `@x`, `@@ROWCOUNT`), `IF`, `WHILE`, `BEGIN ... END`, `TRY ... CATCH`, `RETURN`, `BREAK`, `CONTINUE`, `GOTO` and labels, `THROW`, `RAISERROR`, `PRINT`, `WAITFOR`, `CHECKPOINT`, cursors (`DECLARE ... CURSOR`, `OPEN`, `FETCH`, `CLOSE`, `DEALLOCATE`), transactions (`BEGIN TRAN`, `COMMIT`, `ROLLBACK`, `SAVE TRANSACTION`, `SET TRANSACTION`), `EXEC` and `GO n` (run the batch n times). A script with variables and control flow runs in a Fabric Warehouse.

What can still differ:

- In a warehouse, some run-time errors (dividing by zero, a value that doesn't convert) end only their own statement, and the batch goes on to the next one. Here the script stops at the first error, and the error says which statements didn't run.
- Inside a transaction, a warehouse's error ends the whole transaction: in live run 11 a divide by zero in a batch inside `BEGIN TRAN` rolled back a table the transaction had made.
- A script of only session options (`SET NOCOUNT ON`) has nothing to run here, and says so.

## Time travel in local T-SQL

In a Fabric Warehouse, `OPTION (FOR TIMESTAMP AS OF '2026-11-01T09:30:00')` at the end of a `SELECT` reads every table as it was at that time, in UTC, as Microsoft's time travel docs describe it. A warehouse keeps 30 days of history by default, and its retention can be set from 1 to 120 days. Here each table is read from its Delta history at that time.

| What | In a Fabric Warehouse | Here |
| --- | --- | --- |
| A `SELECT` with the hint, its joins, CTEs, subqueries and `UNION`s | One time for every table | One time for every table, each read from its Delta history |
| The time | `yyyy-MM-ddTHH:mm:ss[.fff]`, in UTC. Anything else is an error: "An error occurred during timestamp conversion. Please provide a timestamp in the format yyyy-MM-ddTHH:mm:ss[.fff]." | The same check, with the same words |
| A variable in the hint | Not allowed | Refused by name |
| A `#temp` table in the query | Read as it is now | Read as it is now |
| A table with no history here (a database table, a file, a view) | Not applicable | Refused: "dbo.plain isn't a Delta table ..., so it has no versions." It's never read as it is now |
| A time before the table's first version | Microsoft's docs don't say | Refused, naming the table's first version |
| A time later than now | Microsoft's docs don't say | Refused, rather than read as the tables are now |
| A time more than 120 days ago | Outside any warehouse's retention | Refused |
| A time 30 to 120 days ago | Answered only if the warehouse's retention was raised | Runs, with a note that a warehouse with the default retention doesn't answer |

Refused by name: the hint on `INSERT ... SELECT`, `SELECT ... INTO` or `CREATE TABLE AS` (Microsoft's pages disagree on whether a warehouse runs these), the hint beside another hint (`OPTION (MAXDOP 1, FOR TIMESTAMP AS OF ...)`), a batch with more than one statement beside a `SELECT` with the hint (put `GO` before and after it), and the hint twice in one batch. Other `OPTION (...)` hints are left out or refused as [Local T-SQL hints, labels and statistics](#local-t-sql-hints-labels-and-statistics) says.

On a warehouse, from the SQL page, a `SELECT` ending in the hint is sent as written, and the warehouse answers for its own retention.

## Table clones and warehouse snapshots

In a Fabric Warehouse, `CREATE TABLE dbo.clone AS CLONE OF dbo.source [AT 'YYYY-MM-DDThh:mm:ss']` makes a zero-copy clone: only the table's metadata is copied, the clone shares the source's Parquet files as they were at that time (in UTC), and after that a change to either one isn't seen in the other. The clone keeps the source's primary and unique keys, its object-level security, row-level security and dynamic data masking. A warehouse can't clone across warehouses or workspaces, from its SQL analytics endpoint, or to a time older than its retention (30 days by default, at most 120). A warehouse snapshot is a read-only Fabric item showing a warehouse at a point in time; it is made in Fabric, and `ALTER DATABASE [snapshot] SET TIMESTAMP = ...` moves it. These come from Microsoft's [CREATE TABLE AS CLONE OF](https://learn.microsoft.com/sql/t-sql/statements/create-table-as-clone-of-transact-sql?view=fabric), [Clone table](https://learn.microsoft.com/fabric/data-warehouse/clone-table) and [Warehouse snapshots](https://learn.microsoft.com/fabric/data-warehouse/warehouse-snapshot) pages.

| What | In a Fabric Warehouse | On a warehouse, from the SQL page | Here |
| --- | --- | --- | --- |
| `CREATE TABLE ... AS CLONE OF`, with or without `AT` | A zero-copy clone | Runs as a change; the confirm names the clone, its source and its UTC time | Refused by name: a clone here would be a full copy with a history of its own, never the zero-copy clone a warehouse makes. The message names the copy that does run (`CREATE TABLE dbo.c AS SELECT * FROM dbo.t`) and, with `AT`, the read of the source as it was (`SELECT ... OPTION (FOR TIMESTAMP AS OF ...)`) |
| A clone or source named with its database | Only within the same warehouse | Refused before anything is sent | Refused, as above |
| An `AT` time not written `'YYYY-MM-DDThh:mm:ss[.fff]'` | An error | Refused before anything is sent, naming the format | Refused, as above |
| An `AT` time later than now, or more than 120 days ago | Nothing to clone from | Refused before anything is sent | Refused, as above |
| An `AT` time inside 120 days but older than the warehouse's retention | An error | Sent: the warehouse refuses it | Refused, as above |
| `ALTER DATABASE ... SET TIMESTAMP` (a warehouse snapshot) | Moves the snapshot | Refused: use **Capture new state** on the snapshot in Fabric | Refused by name: the local engine has no warehouse snapshots |
| Querying a warehouse snapshot | Read-only, through the snapshot's own connection | The Warehouse page doesn't open snapshots yet | Not applicable |

## Loading data with T-SQL

Fabric Warehouse loads files with `COPY INTO`, `BULK INSERT` and `OPENROWSET(BULK ...)`. Each reads files from a URL in Azure Storage (Blob or Data Lake Storage Gen2) or OneLake, as Microsoft's T-SQL reference for Fabric describes them. Neither the local engine nor a run from the SQL page loads them, and each is refused by its name, so a script never looks as if it loaded something.

| Statement | In a Fabric Warehouse | Here |
| --- | --- | --- |
| `COPY INTO t FROM '<url>' WITH (...)` | Loads CSV, Parquet or JSONL files into a table. Options include `FILE_TYPE`, `FIRSTROW`, `FIELDTERMINATOR`, `ROWTERMINATOR`, `FIELDQUOTE`, `ENCODING`, `DATEFORMAT`, `COMPRESSION`, `CREDENTIAL`, `MATCH_COLUMN_COUNT` and `IDENTITY_INSERT`. A column list maps fields by number (CSV) or JSON path (JSONL), with a default for each column. `MAXERRORS` (0 by default) lets up to that many rows be rejected while the rest load, and `ERRORFILE` writes the rejected rows to storage | Refused by name |
| `BULK INSERT t FROM '<url>' WITH (...)` | Loads the same file types as `COPY INTO`, with SQL Server's options: `FORMAT`, `FIRSTROW`, `FIELDTERMINATOR` (a tab by default), `MAXERRORS` (10 by default), `ERRORFILE` and others. `DATAFILETYPE = 'native'` and `KEEPIDENTITY` aren't supported, and `TABLOCK`, `BATCHSIZE` and some others are accepted and ignored | Refused by name |
| `SELECT ... FROM OPENROWSET(BULK '<url>', ...) AS r` | Reads CSV, Parquet or JSONL files as rows, with `FORMAT`, `HEADER_ROW`, `FIRSTROW`, `LASTROW`, `FIELDTERMINATOR`, `MAXERRORS` (10 by default) and others, a `WITH (column type ...)` list, and `r.filepath()` and `r.filename()` for the file each row came from | Refused by name |

- **On this PC.** The local engine reads no URLs, so all three are refused with "... isn't mapped yet. It loads files from cloud storage ...", whatever their file type, options, error-row settings or column list. The whole script is refused before anything runs, so nothing is loaded, not even part of a file, and the statements before it don't run either. To load a file here, put it in one of the project's folders, where it's a table such as `dbo.orders`, and `INSERT INTO ... SELECT` from that table.
- **On a warehouse, from the SQL page.** A read and a change both refuse `COPY INTO`, `BULK INSERT` and `OPENROWSET` before anything is sent: "COPY INTO loads files into the warehouse, which isn't run from the SQL page. Use the Load files with COPY INTO template and run it in Fabric's query editor.", or "OPENROWSET reads outside the warehouse".
- **Rejected rows.** In a warehouse, a load with `MAXERRORS` above 0 can succeed with some rows left out. Since neither engine here runs a load, no load here leaves rows out.
- A column, table or alias named `bulk`, `copy` or `openrowset` in brackets, and the words in strings and comments, aren't refused.

## How it's built and tested

- `sidecar/fabric_desktop/sqlcompat/`: `profiles.py` describes each engine (statement categories, unsupported types and their alternatives, identifiers, transactions, DDL and DML, semantic differences, confidence and sources); `rules.py` holds the rules, each naming its engines, severity, confidence and source; `analyzer.py` splits the SQL into statements (at `;`, and at T-SQL's `GO` on a line of its own, with an optional count or comment), reads each with sqlglot and the engine's tokenizer, applies the rules and builds the report. Rules work on tokens first, so they still find problems, at exact positions, in statements sqlglot can't read.
- The engine methods are `sql_analyze` and `sql_compat_export`. `Service.sql_compat_profiles` lists the rules in-process; the app doesn't call it.
- `sidecar/tests/fixtures/sql_compat_v1.jsonl` holds 197 cases: the SQL, the engine, the expected outcome, the exact set of rules expected and where the first finding is. 36 of them are false-positive guards, SQL that must not be flagged (Spark array indexes, `ISNULL(a, b)` in T-SQL, `UPDATE ... SET x = NULL`, keywords inside strings and comments, triggers in a SQL database). `sidecar/tests/test_sql_compat.py` runs them.
- Changing a rule means moving `RULE_VERSION` on, reviewing the fixtures and updating this page.
- The portability report is `sql_portability.py`, tested by `sidecar/tests/test_sql_portability.py`, which also grades every Snowflake probe statement.

## Differential tests (generated queries)

Besides the hand-picked cases above, three tests generate queries from a fixed seed and compare each answer here with the target's:

- **Spark SQL** (`sidecar/tests/test_spark_differential.py` and `_more.py`): about 1,580 random scalar expressions over the functions this page lists (arithmetic, `div`, `pmod`, casts, text, dates, `LIKE`, three-valued logic), with NULLs mixed in. Each was run in Apache Spark 3.5.3 (local mode, UTC, ANSI mode off, as Fabric) by `sidecar/tools/capture_spark_differential.py`, and its answer is kept in `sidecar/tests/fixtures/spark_3_5_3_differential.json`. CI has no Spark, so the capture is run by hand and the fixture is checked in.
- **T-SQL** (`sidecar/tests/test_tsql_rules_differential.py`): about 1,700 inputs for `SUBSTRING`, `LEFT`, `RIGHT`, `CHARINDEX`, `REPLICATE`, `SPACE`, `ASCII`, `UNICODE`, `CHAR`, `LEN`, integer `/` and `%`, `ROUND`, `CAST` to `int`, `=` and `<` with trailing spaces, `LIKE` with `[a-c]` ranges, `DATEDIFF`, `DATEADD`, `EOMONTH`, `DATEPART`, `CONCAT`, `CONCAT_WS`, `+`, and `REGEXP_LIKE`, `REGEXP_REPLACE`, `REGEXP_COUNT` and `REGEXP_INSTR` with their `c`, `i`, `m` and `s` flags on text with line breaks, each with `'...'` and with `N'...'` text. The expected answer is a short model of the rule in T-SQL's reference. No warehouse was used. `SPACE` and `REPLICATE` of a negative count are `NULL` here, as SQL Server answers them (Microsoft's `SPACE` page says only "null string").
- **Snowflake** (`sidecar/tests/test_snowflake_rules_differential.py`): about 1,700 inputs for `SUBSTR`, `LEFT`, `RIGHT`, `REPEAT`, `CONCAT`, `||`, `CONCAT_WS`, `MOD`, `DIV0`, `DIV0NULL`, `ROUND` (both modes), `DATEDIFF`, `DAYOFWEEK`, `DAYOFWEEKISO`, `DATE_TRUNC('week')`, `WEEKISO`, `LAST_DAY`, `POSITION`, `CHARINDEX`, `SPLIT_PART`, `LPAD`, `RPAD`, `ORDER BY`'s NULL order, and `REGEXP_LIKE`, `REGEXP_SUBSTR`, `REGEXP_SUBSTR_ALL`, `REGEXP_COUNT` and `REGEXP_REPLACE` with the `c`, `i`, `m` and `s` parameters and with an empty pattern, modeled on Snowflake's SQL reference with the default session parameters. No Snowflake account was used.

A different answer, or an error where the target answers, fails these tests unless it is named in the test's `KNOWN` list. An entry that no case needs any more fails too, so each list is what still differs. Today they are:

| Engine | What differs here | How |
| --- | --- | --- |
| Spark SQL | `date_add` by about 2^31 days (past DuckDB's timestamp range) | This stops here with an error, where Spark gives a date millions of years away |
| Spark SQL | `split_part(s, d, 0)` | Refused by name before the query runs; Spark stops only when the call is reached |

Fixed since the first run, each with a test named after its case in `sidecar/tests/test_spark_differential_fixes.py` and `test_spark_string_edges.py`: `substring` with a negative length, `overlay` before the first character, `locate` on NULL text, `levenshtein` on text outside ASCII (characters are counted, as in Spark, and a third argument is Spark's threshold), `bit_count` of a negative `int` (64 bits), `pmod(x, 0.0D)` inside another `pmod`, `a div b` of decimals in the SQL editor, `isnan(NULL)` (false), `to_date` and `CAST(... AS DATE)` of text (Spark's reading: a year of 4 to 7 digits, so `'24-02-29'` is NULL), `date_format` past year 9999 (a leading `+`), `ceil` and `floor` of a double or a whole number (a `bigint`, as in Spark, so `CAST(ceil(1.0E3D) AS STRING)` is `1000`), and `shiftleft`/`shiftright` (Java's rules: the count mod 32 for an `int`, mod 64 for a `bigint`). A shift whose value isn't an `int` literal is typed `bigint` here when the value's type is only known as the query runs. Later fixes: `initcap`, `soundex` of a cast, `trunc` and `make_date` inside another call, `sqrt` below 0 (NaN), `sign` of NaN and of -0.0 (themselves, as in Spark), and `div` by 0 (NULL), tested in `test_spark_error_edges.py` and `test_spark_make_date_div_zero.py`.
