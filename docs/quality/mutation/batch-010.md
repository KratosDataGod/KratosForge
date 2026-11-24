# Mutation testing, batch 10

The rest of the local PySpark layer that batch 8 left, where no open pull request is changing the module:
- createDataFrame's schema inference (`spark_infer.py`).
- `Column.withField` and `dropFields` (`spark_struct_fields.py`).
- Storage levels, `sameSemantics` / `semanticHash` and `Observation` (`spark_df_extras.py`).
- The session and DataFrame calls this PC adds, and the coverage table behind `docs/notebook-spark-on-this-pc.md` (`spark_api.py`).

`spark_sql_names.py` was left out because an open pull request changes it.

The run was at `main` 523163854 (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 9 (sample cap 150, seed 7), on DuckDB 1.5.6. `spark_infer.py` and `spark_api.py` have more points than the cap, so 150 of each were run; the other two were run whole. Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them, so that a test which can't run there doesn't count every mutant as killed.

Tests left out of every run, because they read pages under `docs/`, which the mutator's copy of `sidecar/` doesn't have:
- `test_spark_api.py::test_the_coverage_page_matches_the_api`.
- `test_spark_api_coverage.py`: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.
- `test_spark_coverage.py`: the same three tests.

Without them, nothing checked the coverage table itself. `test_spark_api_mutation.py` now checks it without reading `docs/`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_infer.py` | test_spark_infer_edges, test_spark_infer_schema (+ test_spark_infer_mutation) | 150 of 217 | 123 / 27 | **82.0%** | 147 / 3 | **98.0%** | 3 equivalent |
| `spark_struct_fields.py` | test_spark_struct_fields, test_spark_struct_fields_edges (+ test_spark_struct_fields_mutation) | 129 of 129 | 111 / 18 | **86.0%** | 118 / 11 | **91.5%** | 10 equivalent, 1 killed on DuckDB 2.0 only |
| `spark_df_extras.py` | test_spark_df_extras, test_spark_df_extras_edges (+ test_spark_df_extras_mutation) | 122 of 122 | 118 / 4 | **96.7%** | 120 / 2 | **98.4%** | 1 equivalent, 1 left |
| `spark_api.py` | test_spark_api, test_spark_api_coverage, test_spark_api_parity, test_spark_coverage, test_spark_local (+ existing test_spark_stat_methods, test_spark_views_text, test_spark_writer, test_spark_refusals; + test_spark_api_mutation) | 150 of 258 | 56 / 94 | **37.3%** | 143 / 7 | **95.3%** | 6 equivalent, 1 dead code |

How the survivors were killed:
- `spark_api.py`: 57 of the 94 by existing files that the first set left out (`test_spark_stat_methods` for crosstab, colRegex, toJSON and sampleBy, and `test_spark_views_text` for temp views), and 30 by the new tests.
- The other three modules: all by the new tests.

All four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **spark_infer.py**:
  - Every inferred field, array element and map value marked nullable, also after two structs merge.
  - bigint's edges: `2**63 - 1` and `-2**63` infer as long; one past either goes to DuckDB's own inference, also after an earlier row of the same shape.
  - A `datetime` going to DuckDB's own inference rather than to `date`.
  - Rows skipped as "the same shape" only when they infer the same: lists or maps of different element types, and a map whose values have no cheap shape.
  - A map with only NULL values refused as `CANNOT_DETERMINE_TYPE`.
  - Spark's decimal(38,18) applied inside arrays and maps (NaN as NULL, 18 places), and flat tuple rows passed on as they are.
  - The `LENGTH_SHOULD_BE_THE_SAME` error naming the right rows.
  - → `test_spark_infer_mutation.py`.
- **spark_struct_fields.py**:
  - `withField` names and values for a dotted path (`col("s.c")`), a field name with a double quote, a NULL value, an aliased struct, a nested edit, two edits in a row, an edit whose value is itself an edit, and `withColumn`.
  - → `test_spark_struct_fields_mutation.py`.
- **spark_df_extras.py**:
  - `semanticHash` a signed 32-bit int for plans on both sides of 2**31.
  - `install()` run twice keeping one `cache` wrapper.
  - → `test_spark_df_extras_mutation.py`.
- **spark_api.py**:
  - The coverage table: the state and reason of methods in each state, the counts in each heading, and each label.
  - `_not_implemented` for a short stub that raises DuckDB's ContributionsAcceptedError, and not for a long body, a body that only names the error, a builtin, or nothing.
  - `sparkContext.defaultParallelism` (the PC's cores, or 1) and `startTime` in milliseconds.
  - `tableExists(name, "global_temp")` in any case, and not under another database.
  - The refusals: crosstab past 10000 column values, a colRegex Python can't read, `toJSON().first()` on no rows, and sampleBy fractions that aren't a dict.
  - toJSON's NaN and infinities, and a timestamp with a zone written in UTC.
  - → `test_spark_api_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `spark_infer.py` | 173 | `_fit`'s last `return value` dropped | `_has_struct` is true only for the four types handled above it, so it can't be reached. |
| `spark_infer.py` | 141 | `_SCALE` from `Decimal(0)` | `quantize` uses only the exponent, which is the same. |
| `spark_infer.py` | 258 | `strict=True` → `False` in `arrow_table` | Rows of different lengths still fail the second, strict `zip` with the schema's fields. `infer` checks lengths first anyway. |
| `spark_struct_fields.py` | 31, 36, 42, 48, 53 | `frozen=True` → `False` on the spec dataclasses | Nothing changes or hashes a spec after it is made. |
| `spark_struct_fields.py` | 107 | `and` → `or` before the dotted-path rewrite | In every case tried (`col("s.c")`, `df.s`, `col("s")["c"]`, an alias), the name came out the same either way. |
| `spark_struct_fields.py` | 174 (×2) | `is_struct`'s `False` → `True` or `None` | Only reached when the frame can't read the column; the select then fails with DuckDB's same binder error. |
| `spark_struct_fields.py` | 194 | `self.sql(inner)` dropped | `_spec` returns the edit itself whenever a column has one, so a `_Col` never holds one. |
| `spark_struct_fields.py` | 198 | `or` → `and` before casting a constant | It casts a constant to the type it already has. |
| `spark_df_extras.py` | 112 | `unpersist(blocking=True)` default | `blocking` is never read: there is nothing to wait for. |
| `spark_api.py` | 264 | `toLocalIterator(prefetchPartitions=True)` default | It is never read. |
| `spark_api.py` | 302 (×2) | the unseeded `randomSplit` salt from a different clock reading | Any salt is a valid random split. |
| `spark_api.py` | 306 (×2) | the last part's upper bound computed instead of 1.0 | A row's point is at most 0.999999, and the weights' sum is 1.0 within float error, so no row is lost. |
| `spark_api.py` | 324 | crosstab reads one more value than 10001 | One past the limit is already enough to refuse. |

Left without a test:
- `spark_struct_fields.py` line 108, `m.group(1)` → `m.group(0)`: only DuckDB 2.0 prints `col("s.c")` as `"s"."c"`, which reaches that line. `test_spark_struct_fields_mutation.py` kills it there (checked by hand), but the mutation runs use DuckDB 1.5.6.
- `spark_df_extras.py` line 187, `Observation.get` with `asDict(recursive=True)`: it changes only a struct metric, from a Row to a dict. No Spark 3.5.3 output for that was at hand to test against.
- `spark_api.py` line 194, `_duckdb_expr`: nothing in the engine calls it, so dropping its result changes nothing. It can be removed in a separate change.

### Real bugs found

None.

### Not in this batch

- `spark_sql_names.py`, which an open pull request changes.

