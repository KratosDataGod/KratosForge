# Mutation testing, batch 382: spark_coverage.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is lane A's first mutation run of this module. It has one boundary mutant (`value > 0` for an infinite float, line 82).
- The Spark function coverage list (`spark_coverage.py`): how a collected value is made comparable (`canonical`, `rows_value`), how an example's status is decided (`check`: runs, differs, refused, fails, not checked), the list itself (`coverage`) and the page it writes (`markdown`, docs/spark-functions.md).

It has 85 mutants, and this run ran all 85 of 85, uncapped.

The run was at `main` 9c7201ae45 (2026-11-24), merged into the branch before the run, with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and the re-run. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 6, 11 and 22) were re-run by hand without `-x`: each failed real tests (1, 1 and 3). No mutant timed out.

The set is test_spark_coverage, test_spark_unchecked_functions, and test_spark_integer_values, test_spark_functions_values, test_spark_date_values, test_spark_math_values and test_spark_json_values. It runs in about 28 seconds; test_spark_coverage's run of every function's example takes most of that, and it is the main test of this module, so it stays. Eight tests were deselected because they read `docs/`, which the tool's copy of `sidecar/` doesn't have, so they fail on the unmutated module there: the three docs tests of test_spark_coverage (`test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list`) and the Spark 3.5.3 value check of each value file (`check` in tests/spark_values.py reads docs/sql-compatibility.md). Those value checks are what compares `canonical`'s output with Spark's, which is why the score before is low: in the copy, little else checks it. Left out for time: test_spark_hashing_values (100 s) and test_spark_arrays_strings_values (31 s), whose value checks read `docs/` too, and test_spark_timezone_values (10 s).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_coverage.py` | test_spark_coverage (3 deselected), test_spark_unchecked_functions, test_spark_integer_values, test_spark_functions_values, test_spark_date_values, test_spark_math_values, test_spark_json_values (1 deselected each); after: + test_spark_coverage_mutation | 85 of 85 (84 + 1), uncapped | 37 / 48 | **43.5%** | 37 / 84 (44.0%) → 83 / 84 (98.8%) | 0 / 1 → 0 / 1 | 83 / 2 | **97.6%** | 2 equivalent |

The 48 survivors were re-run with the new file added (`--only`): 46 are now killed, 2 are left. The new tests in `tests/test_spark_coverage_mutation_a.py`, which read no file outside `sidecar/`, check:
- each kind of value `canonical` turns into plain JSON: None, booleans, text and whole numbers as they are; NaN and both infinities; floats to 12 significant digits; decimals by their digits; timestamps in UTC; dates, times, intervals; bytes, bytearrays and memoryviews as hex; maps sorted; rows as structs, a struct inside a struct staying a struct; lists and tuples; anything else by its type name;
- `rows_value` sorts rows, and sorts the lists inside for an order-free function (`collect_set`);
- a refusal is this PC's own message, never a DuckDB error;
- `check` with a fake Spark: no example, a probe, a match, a different value (both sides cut to 80 characters), a varying function or one without a value, a refusal or a failure with its first line (cut to 160), a session function's note, and an uncompared example that stops as in Spark;
- `coverage` lists each function with its group (or "other") and its example (or its probe), checked on the notebook's session;
- the page's counts, the groups in order with their titles, the rows of each group under it, and `|` and line breaks escaped;
- importing the module runs nothing and writes no page, and the page it writes is the repository's docs/spark-functions.md beside `sidecar/`. Mutants 0 and 24 negated or flipped the `if __name__ == "__main__"` guard, which ran the whole list and wrote the page on every import; every test still passed.

No bugs were found.

### Left

- Mutant 77 (line 82): `value > 1` in place of `value > 0`, and mutant 84 (boundary, same line): `value >= 0`. The line is reached only for an infinite float, which is above 1 or below 0. Equivalent.

Lane B scored the same module at the same time, in batch 365 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 365's page each report their own run. Neither is a re-score of the other.
