# Mutation testing, batch 366

One module not scored before, run with `--kinds all`:
- Checking the Databricks SQL functions the docs list against this PC's engine: the listed examples with their values, patterns, errors and notes, the unordered aggregates, probes with NULL arguments for functions not listed, and the coverage page made from them (`databricks_coverage.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because several runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Three tests in test_databricks_coverage read `docs/` and were deselected, because the run copies only `sidecar/`: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`. The module has 141 mutants, so all were run. The container restarted twice during the batch; the before-run was resumed with `--only` on indexes 136 to 140, which keeps the same numbering.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. `main` has changed this module since ea988e99 (#3163 adds notes and values for `current_catalog`, `current_database` and `current_schema`, then #3266 and #3247); the new tests pass against today's version.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_coverage.py` | test_databricks_coverage (docs tests deselected), test_databricks_coverage_cov2, test_spark_try_to_date (+ test_databricks_coverage_mutation) | 141 of 141 (141 + 0) | 84 / 57 | **59.6%** | 84/141 → 115/141 | none | 115 / 26 | **81.6%** | 26 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_coverage.py**:
  - The listed values of the checks that run here: `cube`, `current_timezone`, `getdate`, `monotonically_increasing_id` and `raise_error`.
  - An unordered aggregate's array is sorted (`collect_set`) and another array is not.
  - A probe tries up to four NULL arguments, and a function needing five fails; refusal and failure notes are cut to 160 characters.
  - A listed check: a matching error, a refusal, a difference with and without a note, a long result cut to 80 characters, a pattern that needs values, and values compared in any order.
  - The example used for a function, the "other" group, the order of the page with the Databricks groups last, one section's exact lines, the page's path, and an import that prints nothing.
  - → `test_databricks_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `databricks_coverage.py` | 75 | `strict=True` in `zip` changed | Equivalent: a row and the relation's types always have the same length. |
| `databricks_coverage.py` | 128, 129 | the listed values of `input_file_block_length` and `input_file_block_start` (2) | Equivalent: both are refused here before a value is compared. |
| `databricks_coverage.py` | 150, 152 | the listed values of `spark_partition_id` and `sql_keywords` (3) | Equivalent: both are refused here. |
| `databricks_coverage.py` | 154, 155 | the listed values of `uniform` (20) | Equivalent: a seed gives another series here, so it differs whatever the listed values are, and its note is what is shown. |

Lane A scored the same module at the same time, in batch 383 (#3309). The two runs used different tests and neither saw the other's, so this page and batch 383's page each report their own run. Neither is a re-score of the other.
