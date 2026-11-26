# Mutation testing, batch 162

A boundary re-score of batch 7: notebookutils on this PC (`notebookutils_local.py`), whose four boundary survivors batch 160 scored and left, are now pinned. Only the boundary mutants were run (`--kinds boundary`), so the score below counts those alone; the module's point score from batch 7 stands as it was.

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`), with the test files batch 160 ran, given as paths relative to `sidecar/`. The module has 5 boundary mutants, so all of them ran, and the "after" run re-checked the four survivors with `--only 0,1,2,4`. No test needed `--deselect`, and the tests first passed once on the unmutated copy of `sidecar/`. The after run also gave `test_notebookutils_load_table.py`, which the new test takes its fixtures from and which wasn't in batch 7's set.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notebookutils_local.py` | test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_local_edges, test_notebookutils_parity, test_dbutils, test_keys, test_paging_complete, test_contract_onelake, test_notebookutils_local_mutation (+ test_notebookutils_load_table, test_notebookutils_local_boundary) | 5 of 5 | 1 / 4 | **20.0%** | 5 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebookutils_local.py**: `fs.head` with 0 bytes reads nothing and asks the store for nothing; a Key Vault reply of exactly 400 is an error; a token that expires this second isn't valid; a load error names up to exactly five files before "and more". → `test_notebookutils_local_boundary.py`.

### Left

None.

