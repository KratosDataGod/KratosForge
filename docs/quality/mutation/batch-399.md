# Mutation testing, batch 399

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's Databricks SQL exercises: the learner's SQL translated to DuckDB by the engine's own Databricks path, then checked, including the tables an answer leaves after one run and after two (`academy/dbsql_lab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 87 mutants, and the run ran all 87 of 87 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_dbsql_lab and test_academy_limits, about 30 seconds together on the loaded machine; they are the only test files that use the module. Every check runs on an in-memory DuckDB in this process.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. No mutant's run came near the 120 second limit (the longest took 38 seconds), and none timed out. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 8 and 7).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/dbsql_lab.py` | test_academy_dbsql_lab, test_academy_limits (+ test_academy_dbsql_lab_mutation) | 87 of 87 (85 + 2), uncapped | 71 / 16 | **81.6%** | 71/85 → 85/85 | 0/2 → 2/2 | 87 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/dbsql_lab.py**:
  - An answer of exactly 100,000 characters is read, and one more is refused with the limit named.
  - SQL nested too deeply to read is refused in words. SQL it can't parse calls no functions.
  - A table is compared after one run, or after two for an idempotent check, each with its own words.
  - An answer that fails says whether it failed to run or failed the second time, with the first line of DuckDB's error.
  - Wrong columns are named (a table with none says "none"), and the number of rows that differ is counted.
  - A table is read one row past the row limit and compared at exactly the limit; the limit is made small in the test.
  - → `test_academy_dbsql_lab_mutation.py`.

### Left

None.
