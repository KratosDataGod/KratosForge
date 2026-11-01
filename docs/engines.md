# Engines and connection profiles (Preview)

KratosForge is growing from "this PC or Fabric" into one place to develop for Microsoft Fabric,
Azure Databricks and Snowflake, alone or together. This page describes the two foundations that work is built on, what
they can do today, and what is planned. With Preview features shown, a notebook picks its engine
from the [engine picker](#engine-picker-preview); connection profiles have no screen of their own
yet, and a Databricks connection comes from a saved profile or the Databricks CLI.

- An **engine** is what runs a notebook cell: Python on this PC, DuckDB, SQLite and Polars, and,
  as Preview engines tested against fakes, a Databricks SQL warehouse, Databricks Connect on a
  cluster and a Fabric Spark session.
- A **connection profile** says which platforms a piece of work uses, where, how it signs in,
  which engine it runs on by default, and whether it may start remote compute.

## What each engine can do

Every engine states each capability at one of five levels, with the evidence behind it. KratosForge
refuses to register an engine that claims more than it has an adapter and tests for.

| Level | Meaning |
| --- | --- |
| Native | The engine does it itself, as the platform does. |
| Partial | It does it, with gaps the note names. |
| Simulated | A stand-in runs on this PC. PySpark here is DuckDB's PySpark API, not Spark. |
| Translated | The code is rewritten for another engine first. Spark SQL and Snowflake SQL on DuckDB are translated by sqlglot. |
| Unsupported | Not available. Anything not listed is unsupported. |

### Compatibility matrix

"Tested" means automated tests in this repository, on Linux and Windows CI, with no cloud. Nothing
here has run against a Fabric tenant or a Databricks workspace yet: the Databricks SQL warehouse,
Databricks Connect and Fabric Spark engines are tested against fakes of the platforms' documented
APIs and clients, and stay Preview until they have.

| Engine | Where it runs | Languages | SQL | Cancel | Plan | Variables | Whole result | Billed | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Python (this PC) | This PC, its own process | Python native; PySpark simulated | (use a SQL engine) | Native | No | Native | No | Nothing | Tested |
| DuckDB (this PC) | This PC | SQL | DuckDB native; Spark SQL, Databricks SQL and Snowflake SQL translated | Native | Native | No | Native | Nothing | Tested |
| SQLite (this PC) | This PC | SQL | SQLite native | Native | Native | No | Native | Nothing | Tested |
| Polars SQL (this PC) | This PC | SQL | Polars SQL partial | **No** (the query finishes) | Native | No | No | Nothing | Tested |
| Apache Spark (this PC) | This PC | none yet | none yet | | | | Nothing | Planned |
| Fabric Spark (remote) | A Spark session on your Fabric capacity | PySpark, Python, Spark SQL, Scala native | Spark SQL native | Native | No (df.explain) | No | Partial (SQL cells) | Fabric capacity | Preview, tested against fakes |
| Databricks Connect (remote) | Your Databricks cluster (Python on this PC) | PySpark native; Python partial | (use spark.sql) | Partial (Spark jobs) | No (df.explain) | No | No | DBUs | Preview, tested against fakes |
| Databricks SQL warehouse (remote) | Your Databricks SQL warehouse | SQL | Databricks SQL native | Native | Partial (EXPLAIN on the warehouse) | No | Partial (up to 25 MB) | DBUs | Preview, tested against fakes |
| Snowflake SQL (remote) | Your Snowflake warehouse | none yet | none yet | | | | Snowflake credits | Planned, Preview |
| Snowpark (this PC, simulated) | This PC, its own process | none yet | none yet | | | | Nothing | Planned |
| Snowpark Connect (remote) | Your Snowflake warehouse | none yet | none yet | | | | Snowflake credits | Planned, Preview |

Engines on this PC bill nothing themselves. **Reading or writing OneLake from this PC still uses
Fabric capacity**, so "runs on this PC" never means "uses no capacity".

**Whole result** means the engine can hand back every row of a result, not just the preview, so
two engines' answers can be compared (below).

A session's database file (an existing DuckDB or SQLite file) is opened read-only. Each session is
its own in-memory database or Python process: two notebooks never see each other's tables or
variables, and closing a session frees everything it held.

A SQL session never reaches past its own database. DuckDB refuses COPY, EXPORT, ATTACH, INSTALL and
LOAD before anything in the cell runs (the same statements the SQL query page refuses), and its
connection can't read or write files on this PC, load extensions or reach the network at all.
SQLite refuses ATTACH and DETACH, so a cell can't create or open another database file. Polars
reads the files a cell names with read_parquet or read_csv, and never writes one.

The Python engine uses the notebook's own session (the same process **Stop** ends), so it needs
**Run notebooks in their own process** turned on in Settings. With it off every notebook shares one
kernel, and a notebook sharing another notebook's session shares its process too; in both cases the
engine says what to change instead of running.

## Snowflake SQL on this PC (Preview)

The DuckDB engine runs Snowflake SQL translated, so Snowflake queries can be tried on this PC without
a Snowflake account or a running warehouse. No screen can pick Snowflake SQL yet: this is the engine
underneath, and the Snowflake screens come with the warehouse work (SF-02). **DuckDB is not Snowflake**: nothing here has
been compared with a Snowflake account, and "it ran" never means "Snowflake gives the same answer".

Every statement in the cell gets a label, and the label is the cell's first output, before the
result:

- **Translated**: no known difference applies. It matches Snowflake's documented behaviour as far
  as KratosForge knows.
- **Runs, with a difference**: it runs, and the note says what differs and what to do about it.
- **Runs only in Snowflake**: streams, tasks, dynamic tables, Snowflake Scripting, time travel,
  clones, stages and the rest below. The note says why.

A cell runs whole or not at all: if any statement runs only in Snowflake, nothing in the cell runs.
A cell that can't be read as Snowflake SQL at all shows only the error, with no label.
The same session rules apply as for any DuckDB cell (no files, extensions or network). **Plan**
shows DuckDB's plan for the translated SQL, not Snowflake's.

The rules and differences come from a probe of 29 common Snowflake statements on the pinned sqlglot
and DuckDB versions, and from 136 function checks against Snowflake's documented values (below);
each is a test.

<!-- Generated from snowflake_sql.docs_table(); a test keeps this table equal to the code. -->
Rules 2026.10.6.

| Id | What | Applies to |
| --- | --- | --- |
| SFX-001 | Rewritten | LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each (INDEX, VALUE, KEY, PATH; OUTER as a left join). SEQ, THIS, RECURSIVE and MODE aren't rewritten. |
| SFX-002 | Rewritten | TO_VARCHAR and TO_CHAR with a date or time format written in the query become strftime. Elements with no equivalent (quarter, week) aren't rewritten. |
| SFX-003 | Runs only in Snowflake | TO_VARCHAR and TO_CHAR with a number format. |
| SFX-004 | Rewritten | OBJECT_CONSTRUCT drops a pair whose value is SQL NULL and keeps JSON null, as Snowflake does. OBJECT_CONSTRUCT(*) isn't rewritten. |
| SFX-005 | Rewritten | A VALUES list in FROM gets Snowflake's column names COLUMN1, COLUMN2..., unless its alias names the columns. |
| SFX-006 | Rewritten | SOUNDEX becomes American Soundex in DuckDB SQL (the first letter and three digits). Text that starts with a non-letter comes back as it is, as in Spark; Snowflake doesn't document that case. |
| SFX-007 | Rewritten | RATIO_TO_REPORT(x) OVER (PARTITION BY ...) becomes x divided by the partition's sum, null when x is null or the sum is 0. An ORDER BY in its window is dropped, as it doesn't change the result. x is worked out twice, which matters only when it isn't deterministic (RANDOM(), for example). |
| SFX-008 | Rewritten | HAVERSINE becomes the great-circle distance in kilometres on a sphere of radius 6,371 km. |
| SFX-009 | Rewritten | TYPEOF gives Snowflake's type names: NULL_VALUE, BOOLEAN, VARCHAR, ARRAY and OBJECT for JSON; INTEGER, DECIMAL or DOUBLE for a JSON number by how PARSE_JSON reads it (an exponent makes it DOUBLE; otherwise INTEGER or DECIMAL while it fits NUMBER); INTEGER, DECIMAL, DOUBLE, VARCHAR, BOOLEAN, DATE, TIME, TIMESTAMP_NTZ, BINARY and ARRAY for other values; SQL NULL for SQL NULL. A whole number is always INTEGER here, where Snowflake notes it may say DECIMAL. Where the name can't be known, SFD-007 applies. The argument is worked out more than once, which matters only when it isn't deterministic. |
| SFX-000 | Runs only in Snowflake | Anything else sqlglot can't write for DuckDB. |
| SFD-001 | Runs, with a difference: Unquoted names keep their case | Snowflake stores an unquoted name in upper case (MyCol becomes MYCOL); DuckDB keeps it as typed. |
| SFD-002 | Runs, with a difference: TIMESTAMP_TZ loses its offset | DuckDB stores TIMESTAMP_TZ as TIMESTAMPTZ, an instant, so the offset Snowflake keeps with each value is lost. |
| SFD-003 | Runs, with a difference: Division returns DOUBLE | DuckDB returns DOUBLE for /, where Snowflake returns NUMBER with its own scale rules. Dividing by zero gives infinity or NULL here, where Snowflake stops with an error. |
| SFD-004 | Runs, with a difference: Local time zone is this PC's | TIMESTAMP_LTZ and CURRENT_TIMESTAMP use this PC's time zone here, not Snowflake's TIMEZONE parameter. |
| SFD-005 | Runs, with a difference: FLATTEN rows and keys | FLATTEN runs as DuckDB's json_each: rows come back in no set order, KEY is the position for array elements (Snowflake gives NULL) and PATH is written differently. |
| SFD-006 | Runs, with a difference: Date arithmetic on a DATE returns a TIMESTAMP | DATEADD and TIMESTAMPADD by a date unit (day and longer), and DATE_TRUNC, of a value whose type isn't known here keep a DATE as a DATE in Snowflake; here the result is a TIMESTAMP at midnight. The date is the same. Raised when the value may be a DATE (a column's type isn't known here); a value that is plainly a DATE (a cast, TO_DATE, CURRENT_DATE) is cast back to DATE instead, and a TIMESTAMP cast or function raises nothing. |
| SFD-007 | Runs, with a difference: TYPEOF can't always name Snowflake's type | TYPEOF gives Snowflake's type names here (SFX-009), with two exceptions. A JSON number with a fraction or an exponent that wasn't parsed in the same call (a column, a path such as v:a) comes back as 'DECIMAL or DOUBLE': Snowflake tells them apart by how the number was written (1.912e2 is DOUBLE, 191.2 is DECIMAL) and DuckDB doesn't keep that. A type with no single Snowflake name keeps DuckDB's name (TIMESTAMP WITH TIME ZONE, which is TIMESTAMP_TZ or TIMESTAMP_LTZ in Snowflake; INTERVAL, UUID, STRUCT, MAP). Raised when the value's type isn't known here: a column, a path or a computed value. |
| SFN-001 | Runs only in Snowflake | Streams. |
| SFN-002 | Runs only in Snowflake | Tasks. |
| SFN-003 | Runs only in Snowflake | Dynamic tables (their SELECT can be tried on its own). |
| SFN-004 | Runs only in Snowflake | Snowflake Scripting, procedures, user-defined functions and CALL. |
| SFN-005 | Runs only in Snowflake | EXECUTE IMMEDIATE. |
| SFN-006 | Runs only in Snowflake | Time travel: AT and BEFORE. |
| SFN-007 | Runs only in Snowflake | UNDROP and zero-copy CLONE. |
| SFN-008 | Runs only in Snowflake | Stages: @stage references, COPY INTO, PUT, GET, LIST, REMOVE. |
| SFN-009 | Runs only in Snowflake | USE, SHOW and DESCRIBE. |
| SFN-010 | Runs only in Snowflake | GRANT, REVOKE, and ALTER of Snowflake settings (policies, tags, suspend and resume). |
| SFN-011 | Runs only in Snowflake | Snowflake-only objects: stages, pipes, warehouses, roles, users, shares, integrations, file formats, sequences, policies, tags. |
| SFN-012 | Runs only in Snowflake | Cortex AI functions. |
| SFN-013 | Runs only in Snowflake | TIMESTAMP_TZ text with an offset after a space; DuckDB reads it as a time zone name. |
| SFN-099 | Runs only in Snowflake | Any other Snowflake command sqlglot can't read as a statement. |

### Snowflake functions checked value by value

Each check runs a Snowflake function through the same rules and DuckDB and compares the answer
with the value Snowflake's SQL reference documents for it. A function that answers differently is
always labelled as a difference when it runs, never as translated.

<!-- Generated from snowflake_sql_parity.table_markdown(); a test keeps this table equal to the code. -->
Of 136 checks on commonly used Snowflake functions, against the values Snowflake's SQL reference documents (no Snowflake account was used), these don't answer the same way on this PC.

| Function | Group | On this PC | What to know |
| --- | --- | --- | --- |
| `/ (by zero)` | Numeric | Different result | Snowflake stops with a division-by-zero error; here it gives infinity (SFD-003). Use DIV0. |
| `TYPEOF (path to an exponent)` | Semi-structured | Different result | A JSON number with a fraction or exponent read from a path or a column is 'DECIMAL or DOUBLE' here: DuckDB doesn't keep how it was written (SFD-007). TYPEOF(PARSE_JSON(...)) is exact. |

These 134 checks answer as Snowflake documents: `IFF`, `NVL`, `NVL2 (NULL)`, `NVL2 (value)`, `IFNULL`, `NULLIF`, `ZEROIFNULL`, `NULLIFZERO`, `DIV0`, `DIV0NULL`, `DECODE`, `DECODE (NULL matches NULL)`, `EQUAL_NULL`, `COALESCE`, `GREATEST`, `GREATEST (any NULL)`, `LEAST (any NULL)`, `CONCAT (any NULL)`, `|| (NULL)`, `CONCAT_WS (any NULL)`, `SPLIT_PART`, `SPLIT_PART (negative part)`, `SPLIT_PART (part 0)`, `SPLIT`, `STRTOK`, `REGEXP_SUBSTR`, `REGEXP_COUNT`, `REGEXP_REPLACE (every match)`, `REGEXP_LIKE (whole string)`, `RLIKE (whole string)`, `LEFT`, `RIGHT`, `LENGTH`, `LEN`, `UPPER`, `INITCAP`, `TRIM`, `LTRIM (characters)`, `LPAD`, `REPLACE`, `REVERSE`, `REPEAT`, `SPACE`, `INSERT`, `CONTAINS`, `STARTSWITH`, `ENDSWITH`, `POSITION`, `CHARINDEX`, `SUBSTR`, `TRANSLATE`, `ASCII`, `CHR`, `EDITDISTANCE`, `SOUNDEX`, `SOUNDEX (same code for similar names)`, `SOUNDEX (adjacent same codes)`, `SOUNDEX (first letter's code)`, `SOUNDEX (H between same codes)`, `SOUNDEX (padded with zeros)`, `MD5`, `SHA2 (256 by default)`, `ABS`, `CEIL`, `FLOOR`, `ROUND (half away from zero)`, `ROUND (negative half)`, `TRUNC`, `MOD (negative dividend)`, `% (modulo)`, `SQUARE`, `SQRT`, `POWER`, `SIGN`, `BITAND`, `TO_NUMBER (scale 0)`, `TRY_TO_NUMBER`, `TRY_CAST`, `TO_VARCHAR`, `TO_BOOLEAN`, `TO_DATE`, `TO_DATE (format)`, `TO_TIME`, `DATE_FROM_PARTS`, `DATE_TRUNC (DATE)`, `DATEADD (DATE, month end)`, `ADD_MONTHS (month end)`, `LAST_DAY`, `DATEDIFF (day)`, `DATEDIFF (month boundary)`, `TIMESTAMPDIFF (hour)`, `TIMEADD`, `TIME_SLICE`, `DAYOFWEEK (Sunday is 0)`, `YEAR`, `HOUR`, `DATE_PART`, `MONTHNAME`, `DAYNAME`, `PARSE_JSON path`, `ARRAY_SIZE`, `ARRAY_CONTAINS`, `ARRAY_TO_STRING`, `ARRAY_SLICE`, `ARRAY_CAT`, `OBJECT_KEYS`, `IS_NULL_VALUE`, `TYPEOF`, `TYPEOF (JSON null)`, `TYPEOF (SQL NULL)`, `TYPEOF (BOOLEAN)`, `TYPEOF (INTEGER)`, `TYPEOF (DECIMAL)`, `TYPEOF (DOUBLE)`, `TYPEOF (VARCHAR)`, `TYPEOF (ARRAY)`, `TYPEOF (OBJECT)`, `TYPEOF (VARCHAR cast to VARIANT)`, `TYPEOF (TIMESTAMP cast to VARIANT)`, `LISTAGG`, `ARRAY_AGG`, `COUNT_IF`, `MEDIAN`, `BOOLOR_AGG`, `ANY_VALUE`, `APPROX_COUNT_DISTINCT`, `STDDEV_SAMP`, `RATIO_TO_REPORT`, `RATIO_TO_REPORT (docs example)`, `RATIO_TO_REPORT (null value)`, `RATIO_TO_REPORT (sum is 0)`, `RATIO_TO_REPORT (by partition)`, `HAVERSINE`, `HAVERSINE (docs example)`.

## Databricks SQL warehouse (Preview)

SQL cells can run on a Databricks SQL warehouse in your workspace, through Databricks' documented
Statement Execution API. A cell may hold several statements; they run in order and the last one's
result is shown, with the warehouse's exact row count. **Cancel** stops the statement on the
warehouse. **Catalog** lists Unity Catalog catalogs, schemas and tables without starting the
warehouse. **Plan** runs EXPLAIN on the warehouse.

- **It bills DBUs.** A statement sent to a stopped warehouse starts it, and a running warehouse
  bills Databricks units until its own auto-stop. KratosForge never stops your warehouse and never
  sends a cell there without your confirmation or your profile's policy.
- **Sign-in** comes from where you already keep it: a Databricks CLI profile (signed in with
  `databricks auth login`, or holding a service principal or personal access token), browser
  sign-in through the Databricks CLI, or a service principal whose secret is an `env:` or `credman:`
  reference. The token stays in memory for the session and is never written anywhere.
- **Cells can change data.** Once the session is open, a cell's CREATE, INSERT, MERGE or DROP runs
  on the warehouse with no further confirm, as in a Databricks notebook. The confirm is for starting
  the session, not for each statement.
- **Try it on this PC first.** The DuckDB engine runs Databricks SQL translated by sqlglot, so you can
  write and test a warehouse query against local tables without starting the warehouse. Unity
  Catalog three-part names need that catalog and schema in the local session, and Delta-only
  commands don't run there.
- **Busy or briefly unavailable:** checking on a statement, and cancelling one, is tried up to four
  times through throttling (HTTP 429), gateway errors (502, 503, 504) and a dropped connection,
  waiting as Databricks' `Retry-After` says (at most 30 seconds in all). A statement is sent again
  only after a 429, because the warehouse turned it away unread; after any other failure it is never
  resent, so it can't run twice. An error after the last try says how many times it was tried.
- **If the connection stays down mid-cell:** the statement is cancelled on the warehouse, so it doesn't
  keep the warehouse busy with no one watching. If the cancel can't get through either, the error
  says the statement may still be running; stop it in Databricks' query history. The two-hour limit
  and a **Cancel** pressed while the statement is being sent report the same way: "stopped" only
  when the warehouse accepted the cancel.
- **Limits:** a statement still running after two hours is cancelled. Results come back inline, so the warehouse refuses one over 25 MB (add a LIMIT), and
  only the first result chunk is previewed. Billed DBUs are in Databricks' system billing tables,
  per warehouse and hour, so the engine reports measured seconds only.
- **Whole results, to compare engines.** When a comparison asks for the whole result, the cell's last
  statement runs with a row limit one above the rows asked for, and every result chunk is read in
  order. Values are typed from the warehouse's schema: numbers, decimals, booleans, strings, dates
  and timestamps (read as UTC, kept without a zone, as the Fabric Spark engine keeps them). Arrays,
  maps, structs, binary and intervals come back as text, and the output names those columns. More
  rows than asked for is an error, never a cut table. The rows are copied onto this PC, so a
  comparison asks for them only after its own confirm.

### Databricks SQL functions on this PC

The DuckDB engine's own translation (sqlglot, then DuckDB) runs each check against the value
Databricks gives: its documented example, or one worked by hand where the reference gives none. A
test runs them again, so the table changes when an upgrade fixes or breaks one. A function not listed
here or below hasn't been checked.

Translated Spark SQL and Databricks SQL run with the session time zone set to UTC, the default for Spark sessions in Fabric and Databricks, so a timestamp reads the same here as there whatever this PC's time zone. DuckDB SQL cells keep the session's own zone.

<!-- Generated from databricks_sql_parity.table_markdown(); a test keeps this table equal to the code. -->

Of 208 checks on commonly used Databricks SQL built-in functions, these are the functions the DuckDB engine doesn't answer the same way.

| Function | Group | On this PC | What to know |
| --- | --- | --- | --- |
| `percentile_approx` | Aggregate | Different result | Approximate on both, but the estimates differ. |
| `aggregate (finish)` | Array and map | Not translated | The finish lambda (fourth argument) isn't translated, so nothing runs. Without it, aggregate and reduce run. |
| `aggregate (struct accumulator)` | Array and map | Not translated | The finish lambda (fourth argument) isn't translated, so nothing runs. Without it, aggregate and reduce run. |
| `reduce (finish)` | Array and map | Not translated | The finish lambda (fourth argument) isn't translated, so nothing runs. Without it, aggregate and reduce run. |
| `array_sort (comparator)` | Array and map | Not translated | A comparator isn't translated, so nothing runs. array_sort without one runs. |
| `array_sort (descending comparator)` | Array and map | Not translated | A comparator isn't translated, so nothing runs. array_sort without one runs. |
| `zip_with` | Array and map | Doesn't run | DuckDB has no zip_with. |
| `zip_with (strings)` | Array and map | Doesn't run | DuckDB has no zip_with. |
| `map_filter` | Array and map | Doesn't run | DuckDB has no map_filter. |
| `transform_keys` | Array and map | Doesn't run | DuckDB has no transform_keys. |
| `transform_keys (key and value)` | Array and map | Doesn't run | DuckDB has no transform_keys. |
| `transform_values` | Array and map | Doesn't run | DuckDB has no transform_values. |
| `transform_values (key and value)` | Array and map | Doesn't run | DuckDB has no transform_values. |
| `str_to_map` | Array and map | Doesn't run | DuckDB has no str_to_map. |
| `str_to_map (no value)` | Array and map | Doesn't run | DuckDB has no str_to_map. |
| `get_json_object (every element)` | JSON | Not translated | Refused here: a [*] path would give a list, not the JSON text '[1,2]'. Read the values with from_json, or use a path to one element. |
| `from_json` | JSON | Doesn't run | DuckDB's from_json takes a JSON structure, not a DDL schema, so DuckDB refuses it. |
| `from_json (name case)` | JSON | Doesn't run | DuckDB's from_json takes a JSON structure, not a DDL schema, so DuckDB refuses it. |
| `json_tuple` | JSON | Doesn't run | DuckDB has no json_tuple. Use get_json_object for each field. |
| `json_tuple (in the select list)` | JSON | Doesn't run | DuckDB has no json_tuple. Use get_json_object for each field. |
| `schema_of_json` | JSON | Doesn't run | DuckDB has no schema_of_json. |

These 187 checks answer the same as on Databricks: `concat`, `concat_ws`, `upper`, `lower`, `length`, `substring`, `substr`, `trim`, `ltrim`, `rtrim`, `lpad`, `rpad`, `replace`, `instr`, `locate`, `position`, `split`, `split_part`, `regexp_replace`, `regexp_extract`, `regexp_like`, `initcap`, `reverse`, `repeat`, `left`, `right`, `startswith`, `endswith`, `contains`, `md5`, `sha2`, `base64`, `ascii`, `chr`, `translate`, `format_number`, `levenshtein`, `soundex`, `abs`, `round`, `round (negative half)`, `bround`, `ceil`, `floor`, `mod (%)`, `pmod`, `div`, `power`, `sqrt`, `ln`, `log10`, `log`, `greatest`, `least`, `sign`, `try_divide`, `hex`, `bin`, `conv`, `factorial`, `date_add`, `date_sub`, `datediff`, `add_months`, `last_day`, `year`, `month`, `dayofmonth`, `dayofweek`, `weekofyear`, `quarter`, `date_format`, `to_date`, `to_timestamp`, `date_trunc`, `trunc`, `make_date`, `next_day`, `months_between`, `array`, `size`, `array_contains`, `element_at`, `array_distinct`, `array_sort`, `sort_array`, `array_join`, `array_max`, `array_min`, `flatten`, `slice`, `sequence`, `array_position`, `arrays_overlap`, `array_union`, `array_intersect`, `array_except`, `transform`, `filter`, `aggregate`, `exists`, `explode`, `map_keys`, `map_values`, `coalesce`, `nvl`, `ifnull`, `nullif`, `nvl2`, `if`, `iff`, `decode`, `get_json_object`, `to_json`, `json_array_length`, `count_if`, `collect_list`, `collect_set`, `array_agg`, `max_by`, `min_by`, `bool_and`, `bool_or`, `median`, `percentile`, `approx_count_distinct`, `stddev`, `variance`, `any_value`, `sort_array (descending)`, `arrays_overlap (only NULL could match)`, `array_union (NULLs)`, `pmod (negative divisor)`, `pmod (both negative)`, `dayofweek (Sunday)`, `array_intersect (order and NULL)`, `array_except (order and NULL)`, `array_distinct (order and NULL)`, `bround (one argument, odd)`, `bround (negative places)`, `conv (negative to a signed base)`, `conv (negative to an unsigned base)`, `conv (stops at an invalid digit)`, `conv (base out of range)`, `soundex (H and W keep a run)`, `soundex (not a letter first)`, `format_number (no decimals)`, `format_number (half-even)`, `exists (NULL and no match)`, `transform (with index)`, `filter (with index)`, `filter (drop NULLs)`, `exists (looking for NULL)`, `forall`, `forall (all match)`, `forall (NULL and a miss)`, `forall (NULL and no miss)`, `reduce`, `map_from_arrays`, `map_concat`, `map_from_entries`, `map_entries`, `element_at (map)`, `element_at (map, missing key)`, `element_at (negative index)`, `try_element_at`, `map_contains_key`, `map_contains_key (missing key)`, `get_json_object (nested)`, `get_json_object (array index)`, `get_json_object (bracket name)`, `get_json_object (no match)`, `to_json (array of structs)`, `to_json (map)`, `lag`, `lag (default)`, `lag (offset 2)`, `lead`, `lead (default)`, `nth_value`, `nth_value (too few rows)`, `ntile`, `percent_rank`, `cume_dist`, `rank`, `dense_rank`, `row_number`.

## Databricks workspace discovery (Preview)

To find a SQL warehouse or a cluster without copying IDs from the Databricks UI, ask the workspace
what you can use. Discovery is a command, with no screen in the app yet:

```
fabric-desktop databricks-discover --profile dev
fabric-desktop databricks-discover --host https://adb-1234567890123456.7.azuredatabricks.net --json
```

It lists who you're signed in as, the SQL warehouses (state, size, auto-stop), the all-purpose
clusters (job clusters are left out), the Unity Catalog metastore and its catalogs, and then a
warehouse and a cluster to try first.

- **Nothing starts.** Every request is a read of a documented Databricks REST API, so discovery never
  starts a warehouse or a cluster and bills no DBUs. It needs no confirm for that reason.
- **Databricks Connect, before you try it.** Each cluster says whether Databricks Connect can use it
  and, if not, why: its runtime line (16.4 or 17.3 in this version) or an access mode without Unity
  Catalog (Connect needs Dedicated or Standard). Installing the client is a separate step.
- **No auto-stop is called out.** A warehouse or cluster that never stops on its own bills DBUs until
  someone stops it, so it's flagged and never suggested.
- **Each part stands alone.** A part you can't read (a 403 on clusters, say) says why, and the rest
  still come back. The command exits 0 when every part was read, 1 when one couldn't be, and 2 when
  it couldn't sign in. Use `--no-catalogs` to leave the catalogs out.

It's tested against fakes of those APIs, not yet against a live workspace.

## Databricks Connect (Preview)

PySpark cells can run their `spark` work on a Databricks cluster through Databricks Connect, while
the rest of the cell's Python runs on this PC.

- **Its own environment.** Databricks Connect brings its own PySpark, so it never loads inside
  KratosForge. You install it once per Databricks Runtime line (for example 16.4); KratosForge
  downloads it with pip into a folder of its own and runs each session as a separate process there.
  Opening a session never installs anything.
- **Pinned packages only.** Each supported runtime line (16.4 and 17.3 in this version) has a lock
  file in the app naming every package, its exact version and its hash. pip installs only those
  files, as prebuilt wheels, and refuses any whose hash differs, so a changed or look-alike package
  never installs. Other runtime lines say they aren't supported in this version. Your organization
  can point the download at its own package mirror (`dbconnect_index_url`) or turn installs off
  (`allow_dbconnect_install`) in the [policy file](azure-setup.md).
- **Checked first.** Before anything starts, KratosForge reads the cluster's runtime and checks it
  against the installed client and this PC's Python. KratosForge's Python is 3.12, so clusters on
  Databricks Runtime 16 and later work. 13.3 to 15.4 need Python 3.10 or 3.11, and the check says so.
- **It bills DBUs.** Connecting to a terminated cluster starts it, and a running cluster bills
  Databricks units until it auto-terminates. A session opens only after your confirmation for that
  cluster, or your profile's policy. Closing the session ends the process on this PC, not the cluster.
- **Sign-in** goes to the process as the Databricks SDK's own settings: your CLI profile's name, or
  the workspace address with the CLI's sign-in, or a service principal whose secret is read from its
  reference. Databricks variables already set on this PC are left out. A sign-in through the CLI
  needs the Databricks CLI on PATH; without it the session doesn't start. A service principal's
  secret is removed from the process once the session has signed in, but cells still run with that
  principal's rights in the workspace, so give it only the access the notebook needs.
- **Limits:** Spark Connect has no RDDs or SparkContext. Results show the first rows only, because
  counting them all would run another Spark job. Cancel stops the session's Spark jobs, not plain
  Python. Serverless compute isn't offered yet.

## Fabric Spark (Preview)

Cells can run in an interactive Spark session in your Fabric workspace, on a lakehouse you pick,
through Fabric's Livy API with your own Fabric sign-in. PySpark, Python, Spark SQL and Scala cells
run in the same session, as in a Fabric notebook. A cell is sent again only when Fabric turns it
away as busy (HTTP 429) before accepting it, so it can't run twice; checking on a cell, cancelling it
and closing the session are tried up to four times through throttling, gateway errors and a dropped
connection, within 30 seconds of waiting. **Cancel** cancels the running statement in Fabric, and closing the session ends it.

- **It uses Fabric capacity** for as long as the session is open, including while it sits idle,
  until you close it or Fabric ends it. A session opens only after your confirmation for that
  lakehouse, or your profile's policy, and never when your organization has turned off Fabric runs
  from KratosForge.
- **Limits:** output arrives when the cell finishes; notebookutils and %magics are Fabric notebook
  features a Livy session doesn't fully have; capacity use is in the Fabric Capacity Metrics app, not
  per cell. A cell still running after two hours is cancelled. If the connection stays down mid-cell,
  the cell is cancelled in Fabric. If that cancel, the two-hour limit's cancel, or a **Cancel**
  pressed while the cell is being sent can't get through, the error says the cell may still be
  running, so stop it from the Monitor hub or close the session.

## Switching engines

A notebook has one engine at a time. Switching to another engine starts a new session: cells and
outputs stay, but variables and tables in the old session are not carried over. KratosForge asks
first, with two choices: close the old session, or keep it in the background so switching back
finds it (local engines only; a remote session is never left running).

If a cell can't run on the chosen engine, KratosForge says why and stops. **It never moves the cell
to another engine on its own**, and above all never to a remote one.

## Comparing two engines' answers (engine only, no screen yet)

`engines/compare.py` runs one query on two open sessions, for example DuckDB and SQLite on this PC,
and compares the two results with the same rules, levels and limits as Prove (`verify.py`): key
columns, numeric tolerances, and only counts and a checksum above 5,000,000 rows. Each side can have
its own SQL, so a query and its translation can be checked against each other.

- Only engines with **Whole result** take part. Any other engine is refused before anything runs,
  with the reason.
- The whole result is never cut short. More rows than asked for (1,000,000 by default), or more than
  256 MB, makes that side an error, and nothing is compared.
- If the first engine fails, the second doesn't run.
- A remote engine's result would be copied onto this PC. That never happens on its own: the caller
  asks you first, and approving the session itself doesn't count. Fabric Spark is the only remote
  engine that returns whole results so far, for SQL cells: Spark sends the rows as JSON with its
  schema, and this PC types them back. Timestamps read as the session's time zone shows them.
  Nested types and binary come back as text, and the cell says which columns.
- Both results stay in memory and are gone when the comparison is returned. Nothing is written.

## Remote compute needs your say-so

A remote engine can start compute that is billed. It opens only when:

- you confirm it for that engine and that workspace, cluster or warehouse (the confirmation lasts
  an hour), or
- the connection profile's execution policy allows that engine without asking.

A profile can also forbid remote compute altogether.

## Engine picker (Preview)

With Preview features shown (Settings), **Engine** replaces **Runs on** in the notebook ribbon,
beside **Run all**, which stays the main button. Nothing else in the ribbon moves. With Preview
hidden, Runs on works as before.

- **Badge.** Beside the engine, *This PC* or *Remote* says where cells run; a remote engine also
  says how it bills ("bills DBUs" for Databricks, "uses Fabric capacity" for Fabric).
- **Connection.** For a Databricks engine, **Connection** picks the saved profile connection or
  Databricks CLI profile to use. Only the host and warehouse or cluster id reach the screen; tokens
  stay with the sidecar. The connection's default catalog and schema show beside it, as a label.
- **Engine menu.** Python (this PC) first, then the other engines on this PC (DuckDB, SQLite,
  Polars), then **Remote**: Run in Fabric (the whole notebook as a Fabric job, as before), Fabric
  Spark, Databricks SQL warehouse and Databricks Connect, each marked Preview. Engines that are
  planned but not built are listed under **More** as information only; they can't be chosen.
  Hover ⓘ for what each engine runs and how it differs from the platform.
- **Remote confirm.** Choosing a remote engine opens a confirm that names the engine, the
  warehouse, cluster or lakehouse, the connection, how it bills, and what the 60-minute grant
  allows. It runs a risk check first: the policy, whether the warehouse or cluster is running and
  its auto-stop, cells that write data, and cells the engine can't run. The check is advice only
  and never blocks; when it can't read something it says "Couldn't run" and why. A profile whose
  policy already allows the engine skips the confirm. Nothing remote starts without one or the
  other, and KratosForge never falls back to remote compute on its own.
- **Switching.** Leaving an engine with a session lists what is lost (its variables) and kept
  (cells and outputs). **Switch and close session** is red; **Keep both** keeps a local session in
  the background. Going back to Python from another engine also closes that engine's session.
- **Cells.** The status line under a cell names the engine that ran it. A cell the engine can't
  run is flagged before anything runs, with the reason and **Run this cell on Python**, which runs
  that one cell in the Python kernel on this PC; the notebook stays on its engine.

The choice lasts while the app is open; it isn't saved in the notebook file yet. Engine sessions
close when the app quits or the notebook switches back to Python, not when its tab closes. Remote
engines have run against fakes only, not a live Fabric tenant or Databricks workspace.

## Usage figures

Every run records what it used, labeled as one of: **measured** on this PC, **billed** by the
platform (with where the bill came from), or **estimated** (with its assumptions). Fabric capacity
unit seconds, Databricks DBUs and Snowflake credits are different units, priced differently:
KratosForge never adds them together or converts one into the other. Comparing money needs your pricing, in Optimize.

## Connection profiles

A profile needs only a name and a kind: **This PC only**, **Microsoft Fabric**, **Azure
Databricks**, **Snowflake**, or **Two or more platforms** (hybrid). Everything else has a sensible
default:

| Part | Default | What it's for |
| --- | --- | --- |
| Connections | The app's own Fabric sign-in | One per platform: Fabric workspace; Databricks workspace address, catalog and schema; Snowflake account, user, role, warehouse, database and schema |
| Default engine | DuckDB on this PC | What a new notebook runs on |
| Environments | Dev, Test, Staging, Prod, all empty | Which workspace, catalog or schema each environment uses, per connection |
| Deploy targets | none | Where each environment deploys, per connection (a Fabric workspace, a Databricks bundle target) |
| Execution policy | Ask before remote compute | Ask, allow listed engines, or never; an optional daily remote-minutes budget |
| Data access | Ask before copying between platforms | Data never moves between Fabric and Databricks on its own |
| Validation | Not checked | The last check of the profile |

The app's current behaviour is the built-in profile **This PC and Fabric**.

### Sign-in: references, never secrets

A profile never holds a password, token or key. It holds a *reference* to one:

| Platform | Ways to sign in |
| --- | --- |
| Fabric | The app's own sign-in (default); a service principal, managed identity or workload identity for automation |
| Databricks | A profile from the Databricks CLI's config file; browser sign-in (OAuth); a service principal (OAuth machine-to-machine) |
| Snowflake (planned) | Browser sign-in through Snowflake's built-in local application (default); a key pair (the private key file's path, and its passphrase as a reference); a programmatic access token (as a reference); a connection from the Snowflake CLI |

A service principal's secret is named as `env:NAME` (an environment variable) or `credman:TARGET`
(Windows Credential Manager). KratosForge refuses to save or import a profile with anything that
looks like a token, key or password in it, and it never repeats the value in its error.

In a hybrid profile, each platform keeps its own sign-in, workspaces and deploy targets, and no two
platforms can share a secret.

A Snowflake connection names the account by its identifier (`myorg-myaccount`, or a legacy locator
such as `xy12345.east-us-2.azure`), never by a URL; the address is always
`https://<account>.snowflakecomputing.com`. On Windows a private key file must be a full path on a
drive (`C:\keys\snowflake.p8`): a network share path is refused, so reading the key never reaches
another computer. Nothing signs in to Snowflake yet; the sign-in methods are recorded so a profile
made now keeps working when they arrive.

Profiles that use only Fabric and Databricks are still saved in the format older versions of
KratosForge read, so a profile shared with someone who hasn't updated still opens for them. A
profile with Snowflake, or any other mix of platforms, needs this version or later.

### Databricks CLI profiles

If you already use the Databricks CLI, KratosForge reads the profile names and workspace addresses
in `~/.databrickscfg` (or `DATABRICKS_CONFIG_FILE`) so you can pick one. It never reads out tokens or
client secrets. A profile that keeps a personal access token in that file gets a warning, because
`databricks auth login` keeps tokens out of plain text. Only Azure Databricks is supported for now.

### Snowflake CLI connections

If you already use the Snowflake CLI, KratosForge reads its connection names, accounts, users,
roles, warehouses, databases and schemas so you can pick one. It looks where the CLI does:
`SNOWFLAKE_HOME` if it's set (if that folder doesn't exist it finds nothing, as the CLI does), else
`~/.snowflake`, else `%USERPROFILE%\AppData\Local\snowflake`, and `connections.toml` there wins
over the connections in `config.toml`. It never reads out passwords, tokens, private keys or
passphrases. A connection that keeps one in the file gets a warning, and a file it can't read says
only that, never which line was wrong. A connection that signs in with browser single sign-on is
listed but can't be used yet.

### Sharing a profile

Export writes a profile without anything tied to your PC: secret references, the Databricks CLI
profile name, the Snowflake CLI connection name, the private key file's path and the last check are
removed. Whoever imports it adds their own sign-in; the
imported profile says what's missing.

### Switching profiles

Switching closes every open engine session (they may hold data read with the old sign-in). Cells
and outputs stay. A notebook with unsaved changes must be saved or discarded first.

## Adding an engine

Every engine with an adapter must pass the shared contract checks in
`sidecar/tests/engine_conformance.py`, run by `tests/test_engine_conformance.py`. The checks read the
engine's own descriptor and hold the adapter to it: claimed capabilities work, unsupported ones
raise `Unsupported` with a reason, a cell in a language or dialect the engine marks unsupported never
comes back as run, a failing cell is an error output that never repeats a secret, a closed session
refuses cells, two sessions share nothing, a whole result comes back only from an engine that
claims it and only when asked, and a remote engine contacts nothing before it has an
authorization. Remote engines run against the same fakes as their own tests, so the checks never
reach a workspace.

A new adapter adds a harness there: how to open a session (against a fake for a remote engine), a
cell that runs, a cell that fails, and cells it refuses. The run fails until every engine with an
adapter has one.

## Not yet

- No profile setup screen: connection profiles are read, not edited, by the engine picker.
- Snowflake: the three engines above are listed so the plan is visible, but none runs a cell yet.
  Snowflake SQL and Snowpark Connect will run on your Snowflake warehouse and use credits, so they
  will ask first, as every remote engine does. Snowpark on this PC will be simulated by Snowflake's
  local testing framework, which can't run `session.sql` and implements only some functions.
- Databricks SQL warehouses sign in through the Databricks CLI or a service principal and run SQL
  through the Statement Execution API, tested against fakes only. Workspace discovery and Databricks
  Connect stay Preview until tested against a live workspace.
- Databricks Connect will run in its own Python environment: it ships its own PySpark, which would
  otherwise change what `spark` means in every local notebook. Its version is checked before
  anything starts (Databricks Runtime 16.4 or later, or serverless, for this app's Python 3.12).
