# Mutation testing, batch 130

One engine module not scored before, run with `--kinds all`, so the score counts the boundary mutants together with the earlier kinds:
- The approval that lets remote compute start: a confirmation in the app or the profile's execution policy, its expiry, and the check before a session opens (`engines/authorization.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has 39 mutants, so all of them ran. No `-k` filter or `--deselect` was needed.

The first run used test_engines, test_engines_service and test_engine_switch_failures. Each survivor was run again with test_databricks_sql, test_dbconnect and test_fabric_spark added, and the "before" figures are from that run. No mutant's run timed out.

Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/authorization.py` | test_engines, test_engines_service, test_engine_switch_failures, test_databricks_sql, test_dbconnect, test_fabric_spark (+ test_engine_authorization_mutation) | 39 of 39 (38 + 1) | 35 / 4 | **89.7%** | 35/38 → 38/38 | 0/1 → 1/1 | 39 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/authorization.py**:
  - An approval from the profile's policy lasts the confirmation time from now, as a confirmation in the app does.
  - An approval is good just before its expiry and has run out at exactly its expiry.
  - The check hands back the approval it checked.
  - An approval can't be changed once given.
  - → `test_engine_authorization_mutation.py`.

### Left

None.

