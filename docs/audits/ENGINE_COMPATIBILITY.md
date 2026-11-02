# Engine compatibility

This page records how local execution behaves compared with Spark in Microsoft Fabric and Databricks. Each row is something that was actually run. The user-facing pages are [Engines](../engines.md), [Spark on this PC](../notebook-spark-on-this-pc.md), [SQL compatibility](../sql-compatibility.md) and [Platforms](../platforms.md). This page doesn't repeat their feature lists. It records what the audit measured, and where those pages and the code disagree.

**How it was run:**
- The real notebook kernel (`notebook.Kernel`), on local Delta tables, with the same fixture as `tests/test_spark_writer.py`.
- DuckDB 1.5.6 (the pinned version), on Linux, at `main` `b75dcf5`.

**Where the expected values come from:**
- Spark's expected values are its documented defaults (Spark 3.5, ANSI mode off, as in Fabric Runtime 1.3). They were not run against a Fabric or Databricks cluster (**UNVERIFIED** live).
- Snowflake isn't compared: KratosForge doesn't run Snowflake SQL locally.

## Matrix

**Status:** OK matches. DEFECT gives a different result with no warning. GAP is unsupported and says so. RISK is a credible failure that wasn't reproduced.

| Behaviour | On this PC | Spark / Fabric expected | Status | Backlog |
| --- | --- | --- | --- | --- |
| `7/2`, `-7 div 2`, `-7 % 2`, `pmod(-7, 2)` | 3.5, -3, -1, 1 | same | OK | |
| `x / 0` in SQL | null | null | OK | |
| `x / 0` in the DataFrame API | `inf` | null | DEFECT | EA-012 |
| `CAST(3.7 AS INT)`, `CAST(-3.7 AS INT)` | 4, -4 (confirmed) | 3, -3 | DEFECT | EA-003 |
| `NaN = NaN`, `NULL = NULL` | true, null | true, null | OK | |
| Null and NaN sort order (asc / desc) | nulls first, NaN last / NaN first, nulls last | same | OK | |
| `sum(int)` result type, written to Delta | `HUGEINT`, `Decimal128(38,0)` (confirmed) | `bigint` | DEFECT | EA-004 |
| `decimal(10,2) / decimal(10,2)` | `double`, written as `Float64` | decimal | DEFECT | EA-004 |
| Bad string to date or int, SQL | null | null (ANSI off) | OK | |
| Bad string to date, DataFrame `cast` and `to_date` | error | null (ANSI off) | DEFECT | EA-015 |
| `CAST(timestamp AS BIGINT)`, `CAST(bigint AS TIMESTAMP)` | null | epoch seconds, timestamp | DEFECT | EA-014 |
| `spark.sql.ansi.enabled = true` | kept, no effect | errors on bad casts | GAP (documented) | |
| Timestamp literal under the session time zone | correct instant | same | OK | |
| Timestamp cast to string | has an offset (`-05`) | no offset | DEFECT (P3) | |
| `TIMESTAMP_NTZ` written to Delta | refused with a reason | supported | GAP (explicit) | |
| Column name case | case-insensitive | same (default) | OK | |
| `spark.sql.caseSensitive = true` | ignored | honoured | GAP (documented as Fabric-only) | |
| `hash('a')` | unsigned 64-bit DuckDB hash | 32-bit Murmur3 | DEFECT | EA-017 |
| CSV read with `inferSchema` left out | types inferred | all strings | DEFECT | EA-013 |
| CSV `nullValue`, `mode` options | dropped | honoured | DEFECT | EA-013 |
| Writer `replaceWhere`, `partitionOverwriteMode`, unknown options | refused, table untouched | honoured | GAP (explicit, safe) | |
| `df.write.parquet/csv` to a path on this PC, by mode | append replaced the file (confirmed) | honoured | DEFECT, fix in review | EA-002 |
| Table write: append or overwrite with a different column kind | refused | refused | OK | |
| `lakehouse.write_table` append with a narrower or different kind | value converted silently (confirmed) | refused | DEFECT | EA-001 |
| `cache()` and `persist()` with nondeterministic columns | recomputed, so values change | materialised once | RISK (documented as no-op) | |
| Cancel: `spark.sql` / `duckdb.sql` / own `duckdb.connect()` / Polars | stops / stops / runs to the end / runs to the end | stops | RISK | EA-016 |

## Where the docs and the code disagree

1. [Engines](../engines.md) says cancel is "Native" for Python on this PC. Cancel doesn't reach DuckDB connections the notebook opens itself, or Polars (EA-016).
2. [Platforms](../platforms.md) says local decimal division keeps more places than Spark. It returns `double`, and that is written to Delta as `Float64` (EA-004).
3. No page mentions that float-to-integer casts round (EA-003), or that `sum` widens to `HUGEINT` (EA-004).
4. Before the first remediation PR, the docs said options that aren't honoured are refused. That was true for table writes only; file writes ignored the save mode (EA-002).

Each fix updates the user-facing page in the same PR, so these entries should disappear from this list.

## Principles for closing gaps

- **Refuse before you guess.** A behaviour that would give different values, or a different schema, from Fabric either translates to Spark's semantics or stops with a message that says what to do. The writer's refused-options list is the model to follow.
- **No parity claims without a row here.** A behaviour enters [Spark on this PC](../notebook-spark-on-this-pc.md) as supported only with a test that checks the result, not just that the call runs.
- **Live confirmation.** The rows above should be re-checked against a Fabric notebook on the test workspace when a live run is approved. The T-SQL contract job ([live testing](../live-testing.md)) is the existing mechanism for the SQL side.
