# Mutation testing, batch 383: databricks_coverage.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is lane A's first mutation run of this module. Its comparisons are all `==`, `!=` or `in`, so it has no boundary mutants.
- The Databricks SQL function coverage list (`databricks_coverage.py`): values as DuckDB's types give them (`canonical`, `run`), the connection a Databricks SQL cell gets (`connect`), the status of each function (`check`, the listed checks of `DATABRICKS_CHECKS`, `probe` with NULL arguments), the list (`coverage`) and the page it writes (`markdown`, docs/databricks-functions.md).

It has 141 mutants, and this run ran all 141 of 141, uncapped.

The run was at `main` 9c7201ae45 (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 58, 80 and 81) were re-run by hand without `-x`: each failed real tests (3, 1 and 1). No mutant timed out.

The set is test_spark_try_to_date, test_databricks_coverage_cov2 and test_databricks_coverage. It runs in about 30 seconds; test_databricks_coverage's run of every function's example takes most of that, and it is the main test of this module, so it stays. Its three docs tests (`test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list`) were deselected: they read `docs/`, which the tool's copy of `sidecar/` doesn't have. test_parity was left out: it checks the skins and their docs pages, not this module.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_coverage.py` | test_spark_try_to_date, test_databricks_coverage_cov2, test_databricks_coverage (3 deselected); after: + test_databricks_coverage_mutation | 141 of 141 (141 + 0), uncapped | 83 / 58 | **58.9%** | 83 / 141 (58.9%) → 115 / 141 (81.6%) | none | 115 / 26 | **81.6%** | 26 equivalent |

The 58 survivors were re-run with the new file added (`--only`): 32 are now killed, 26 are left, all argued equivalent below; every mutant that changes what this PC reports is killed. A first re-run had mutant 7 surviving: the new test for it (`collect_set` over VALUES) depended on the order DuckDB happened to collect in. That test was replaced by one that doesn't, and the whole re-run was run again; only that second re-run is reported. The new tests in `tests/test_databricks_coverage_mutation_a.py`, which read no file outside `sidecar/`, check:
- a STRUCT keeps its fields in order, a MAP is sorted, NULLs and nested lists stay as they are, and only an example naming an order-free function has its lists sorted;
- the connection runs in UTC with the Databricks functions registered;
- `check`: Databricks' own value is the one checked, a varying function without a listed check isn't checked, an error is refused or fails with its first line (cut to 160), and a differs note cuts both values to 80 characters;
- the listed checks: a value compared sorted, with its note; a pattern that must match every value, wholly; an error that must stop with its text and not be a refusal; and their notes cut to 80 characters; `getdate` and `monotonically_increasing_id` give the values Databricks documents;
- `probe`: up to four NULL arguments and never five, a refusal by name (cut to 160), and a failure naming the first error but not a missing argument;
- `coverage`: Spark's functions, then Databricks' own, each with its group, example and note;
- the page's counts, its groups (Databricks' own last, with their titles), each group's rows, and `|` and line breaks escaped;
- importing the module runs nothing and writes no page, and the page is the repository's docs/databricks-functions.md beside `sidecar/` (mutants 0 and 25 negated or flipped the `__main__` guard, so every import ran the whole list and wrote the page).

No bugs were found.

### Left

All 26 change reference data in `DATABRICKS_CHECKS` or a check that can't fail, and none changes what this PC reports:
- Mutants 90, 91, 92, 125 and 126 (lines 127, 128, 149 and 151): the documented values of `spark_partition_id`, `sql_keywords`, `input_file_block_length` and `input_file_block_start`. This PC refuses all four by name, so their values are never compared. They would matter only once one of them runs here.
- Mutants 93 to 112 (lines 153 and 154): the 20 numbers of `uniform(10, 20, 0)`'s documented series. A seed gives another series here, so it differs either way, and its note takes the place of the values on the page. They would matter only if this PC's series became Databricks'.
- Mutant 134 (line 75): `zip(..., strict=False)` in place of `strict=True`. A relation's types always number its columns, so the zip never has a row and its types of different lengths. Equivalent.

Lane B scored the same module at the same time, in batch 366 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 366's page each report their own run. Neither is a re-score of the other.
