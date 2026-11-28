# Mutation testing, batch 365

One module not scored before, run with `--kinds all`:
- Checking that the PySpark examples the docs list still run locally as they do on Spark: values made canonical for comparison, the order-free results, the refusals, the notes for examples that are refused, fail or differ, and the coverage page made from them (`spark_coverage.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because several runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Seven tests that read `docs/` were deselected, because the run copies only `sidecar/`: in test_spark_coverage, `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`; in test_spark_api_coverage, the same three and `test_the_readme_counts_match_spark_api_coverage`; and the same three in test_databricks_coverage. The module has 85 mutants, so all were run. The container restarted during the batch; runs cut short were resumed with `--only` on the indexes not yet run, which keeps the same numbering.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which has not changed it since ea988e99.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_coverage.py` | test_spark_coverage, test_spark_api_coverage, test_databricks_coverage, docs tests deselected (+ test_spark_coverage_mutation) | 85 of 85 (84 + 1) | 49 / 36 | **57.6%** | 49/84 → 83/84 | 0/1 → 0/1 | 83 / 2 | **97.6%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_coverage.py**:
  - Canonical values: NaN and the two infinities, a time zone instant as UTC, maps, nested structs and a complex number.
  - Order-free results are sorted; each refusal case; a check with no example, a probe, a refusal or failure whose first line is cut to 160 characters, the session note, a Spark error, a varying result, and a difference note cut to 80.
  - The coverage run, the markdown counts and group headings, the exact lines of one section, the page's path, and an import that prints nothing.
  - → `test_spark_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_coverage.py` | 82 | `value > 0` constant changed (0 → 1) | Equivalent: the value is already known to be an infinity, so any finite threshold gives the same sign. |
| `spark_coverage.py` | 82 | `value > 0` boundary (`>=`) | Equivalent: same reason; zero is not an infinity. |

Lane A scored the same module at the same time, in batch 382 (#3309). The two runs used different tests and neither saw the other's, so this page and batch 382's page each report their own run. Neither is a re-score of the other.
