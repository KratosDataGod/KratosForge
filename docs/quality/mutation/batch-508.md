# Mutation testing, batch 508

A re-score of one module, run with `--kinds all`:
- The session and DataFrame calls this PC adds to DuckDB's Spark API, and the coverage table behind `docs/notebook-spark-on-this-pc.md`: `spark.conf` (time zone, ANSI mode), randomSplit, crosstab, colRegex, toJSON, sampleBy and the others (`spark_api.py`). It was last scored in batch 10 at 37.3% before and 95.3% after (point mutants only), and its boundary mutants in batch 159 at 12.5% before and 87.5% after (`--kinds boundary`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Tests that read `docs/` were left out with `--deselect`, as in batch 159: test_spark_api's `test_the_coverage_page_matches_the_api`, test_spark_api_coverage's `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list` and `test_the_readme_counts_match_spark_api_coverage`, and test_spark_coverage's `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`. The module has 270 mutants, so a sample of 150 was run (145 point + 5 boundary).

The test set was the files of batches 10 and 159, with the module's test files added on `main` since: test_spark_api, test_spark_api_coverage, test_spark_api_parity, test_spark_coverage, test_spark_local, test_spark_stat_methods, test_spark_views_text, test_spark_writer, test_spark_refusals, test_spark_api_mutation, test_spark_api_boundary, test_spark_api_coverage_mutation and test_spark_api_fails_cleared.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_api.py` | the 13 test files above (+ test_spark_api_mutation_b) | 150 of 270 (145 + 5) | 138 / 12 | **92.0%** | 134/145 → 139/145 | 4/5 → 4/5 | 143 / 7 | **95.3%** | 6 equivalent, 1 dead code |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_api.py**:
  - Unsetting another key keeps the session's time zone; only unsetting the time zone itself goes back to UTC.
  - The ANSI note is printed when `spark.sql.ansi.enabled` is set to true (in any case, with spaces), and not for false.
  - A negative randomSplit weight is refused by name even when the weights add up to more than 0.
  - A colRegex name without backquotes is that column, not a regex.
  - sampleBy with no fraction for any value (`{}`, or only `None`) is an empty frame with the same columns.
  - → `test_spark_api_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_api.py` | 208 | `_duckdb_expr`'s return dropped | Dead code, as batch 10 said (line 194 then): nothing in the engine calls it. |
| `spark_api.py` | 278 | `toLocalIterator(prefetchPartitions=True)` default | Equivalent, as batch 10 said (line 264 then): it is never read. |
| `spark_api.py` | 425 | `toJSON(use_unicode=False)` default | Equivalent: it is never read. |
| `spark_api.py` | 316 | the unseeded `randomSplit` salt from `time.time() * 1001` | Equivalent, as batch 10 said (line 302 then): any salt is a valid random split. |
| `spark_api.py` | 320 | `len(weights) - 1` to `+ 1`, so the last part's upper bound is computed instead of 1.0 | Equivalent, as batch 10 said (line 306 then): a row's point is at most 0.999999, and the weights' sum is 1.0 within float error. |
| `spark_api.py` | 338 | crosstab reads 10002 values, not 10001 | Equivalent, as batch 10 said (line 324 then): one past the limit is already enough to refuse. |
| `spark_api.py` | 405 | `value > 0` to `>=` for an infinite float in toJSON | Equivalent, as batch 159 said (line 399 then): it is only reached for an infinity, never 0. |
