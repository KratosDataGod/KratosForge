# Engine compatibility

This page records how local execution behaves compared with Spark in Microsoft Fabric and Databricks. Each row is something that was actually run. The user-facing pages are [Engines](../engines.md), [Spark on this PC](../notebook-spark-on-this-pc.md), [SQL compatibility](../sql-compatibility.md) and [Platforms](../platforms.md). This page doesn't repeat their feature lists. It records what the audit measured, and where those pages and the code disagree.

**How it was run:**
- The real notebook kernel (`notebook.Kernel`), on local Delta tables, with the same fixture as `tests/test_spark_writer.py`.
- DuckDB 1.5.6 (the pinned version), on Linux, at `main` `b75dcf5`.

**Where the expected values come from:**
- Spark's expected values are its documented defaults (Spark 3.5, ANSI mode off, as in Fabric Runtime 1.3). They were not run against a Fabric or Databricks cluster (**UNVERIFIED** live).
- Snowflake isn't compared: KratosForge doesn't run Snowflake SQL locally.

## Matrix

As found at the baseline (`b75dcf5`, 2026-11-02). Rows marked FIXED were re-checked on `main` at `1ca71e3` on 2026-11-05, where the audit's repro test for each one passes.

**Status:** OK matches. FIXED was a DEFECT and is fixed on `main`, with a test. DEFECT gives a different result with no warning. GAP is unsupported and says so. RISK is a credible failure that wasn't reproduced.

| Behaviour | On this PC | Spark / Fabric expected | Status | Backlog |
| --- | --- | --- | --- | --- |
| `7/2`, `-7 div 2`, `-7 % 2`, `pmod(-7, 2)` | 3.5, -3, -1, 1 | same | OK | |
| `x / 0` in SQL | null | null | OK | |
| `x / 0` in the DataFrame API | `inf` | null | FIXED (#1601) | EA-012 |
| `CAST(3.7 AS INT)`, `CAST(-3.7 AS INT)` | 4, -4 (confirmed) | 3, -3 | FIXED (#1579) | EA-003 |
| `NaN = NaN`, `NULL = NULL` | true, null | true, null | OK | |
| Null and NaN sort order (asc / desc) | nulls first, NaN last / NaN first, nulls last | same | OK | |
| `sum(int)` result type, written to Delta | `HUGEINT`, `Decimal128(38,0)` (confirmed) | `bigint` | FIXED (#1646, #1647) | EA-004 |
| `decimal(10,2) / decimal(10,2)` | `double`, written as `Float64` | decimal | FIXED (#1645) | EA-004 |
| Bad string to date or int, SQL | null | null (ANSI off) | OK | |
| Bad string to date, DataFrame `cast` and `to_date` | error | null (ANSI off) | FIXED (#1607) | EA-015 |
| `CAST(timestamp AS BIGINT)`, `CAST(bigint AS TIMESTAMP)` | null | epoch seconds, timestamp | FIXED (#1609) | EA-014 |
| `spark.sql.ansi.enabled = true` | kept, no effect | errors on bad casts | GAP (documented) | |
| Timestamp literal under the session time zone | correct instant | same | OK | |
| Timestamp cast to string | has an offset (`-05`) | no offset | DEFECT (P3) | |
| `TIMESTAMP_NTZ` written to Delta | refused with a reason | supported | GAP (explicit) | |
| Column name case | case-insensitive | same (default) | OK | |
| `spark.sql.caseSensitive = true` | ignored | honoured | GAP (documented as Fabric-only) | |
| `hash('a')` | unsigned 64-bit DuckDB hash | 32-bit Murmur3 | FIXED: refused by name (#1625) | EA-017 |
| CSV read with `inferSchema` left out | types inferred | all strings | FIXED (#1626) | EA-013 |
| CSV `nullValue`, `mode` options | dropped | honoured | FIXED: honoured or refused (#1626) | EA-013 |
| Writer `replaceWhere`, `partitionOverwriteMode`, unknown options | refused, table untouched | honoured | GAP (explicit, safe) | |
| `df.write.parquet/csv` to a path on this PC, by mode | append replaced the file (confirmed) | honoured | FIXED (#1572) | EA-002 |
| Table write: append or overwrite with a different column kind | refused | refused | OK | |
| `lakehouse.write_table` append with a narrower or different kind | value converted silently (confirmed) | refused | FIXED (#1576, #1600) | EA-001 |
| `cache()` and `persist()` with nondeterministic columns | recomputed, so values change | materialised once | RISK (documented as no-op) | |
| Cancel: `spark.sql` / `duckdb.sql` / own `duckdb.connect()` / Polars | stops / stops / runs to the end / runs to the end | stops | FIXED for `duckdb.connect()` (#1624). Polars still runs to the end, and engines.md says so | EA-016 |

## Where the docs and the code disagreed at the baseline

Items 1 and 4 are fixed (#1624, #1572). Items 2 and 3 no longer apply: the behaviour they describe was fixed (#1579, #1645, #1646).

1. [Engines](../engines.md) says cancel is "Native" for Python on this PC. Cancel doesn't reach DuckDB connections the notebook opens itself, or Polars (EA-016).
2. [Platforms](../platforms.md) says local decimal division keeps more places than Spark. It returns `double`, and that is written to Delta as `Float64` (EA-004).
3. No page mentions that float-to-integer casts round (EA-003), or that `sum` widens to `HUGEINT` (EA-004).
4. Before the first remediation PR, the docs said options that aren't honoured are refused. That was true for table writes only; file writes ignored the save mode (EA-002).

Each fix updates the user-facing page in the same PR, so these entries should disappear from this list.

## Principles for closing gaps

- **Refuse before you guess.** A behaviour that would give different values, or a different schema, from Fabric either translates to Spark's semantics or stops with a message that says what to do. The writer's refused-options list is the model to follow.
- **No parity claims without a row here.** A behaviour enters [Spark on this PC](../notebook-spark-on-this-pc.md) as supported only with a test that checks the result, not just that the call runs.
- **Live confirmation.** The rows above should be re-checked against a Fabric notebook on the test workspace when a live run is approved. The T-SQL contract job ([live testing](../live-testing.md)) is the existing mechanism for the SQL side.
