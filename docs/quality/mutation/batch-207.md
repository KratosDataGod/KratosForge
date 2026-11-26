# Mutation testing, batch 207

A boundary re-score of three modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- Saving JSON state files and the copies set aside when one is corrupt (`jsonfile.py`, a boundary re-score of batch 3).
- DuckDB result rows as JSON values (`duckdb_rows.py`, a boundary re-score of batch 5).
- The local data folder's format mark (`data_format.py`, a boundary re-score of batch 5).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. No test needed `--deselect`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `jsonfile.py` | test_json_files, test_file_replace, test_state_writes, test_meta_atomic_save, test_jsonfile_mutation, test_jsonfile_set_aside_same_tick (+ test_jsonfile_boundary) | 3 of 3 | 1 / 2 | **33.3%** | 2 / 1 | **66.7%** | 1 equivalent |
| `duckdb_rows.py` | test_duckdb_rows, test_duckdb_rows_edges, test_duckdb_try_cast, test_duckdb_rows_mutation | 3 of 3 | 3 / 0 | **100%** | no change needed | **100%** | none |
| `data_format.py` | test_data_format, test_data_format_mutation (+ test_data_format_boundary) | 3 of 3 | 2 / 1 | **66.7%** | 3 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **jsonfile.py**: a set-aside copy stamped exactly a day ahead still counts in the ordering, so the next copy is named after it. → `test_jsonfile_boundary.py`.
- **data_format.py**: a format mark of 0 isn't a format; 1 is. → `test_data_format_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `jsonfile.py` | 138 | `when > newest` to `>=` in naming a set-aside copy | Equivalent: a stamp equal to the newest so far leaves the newest the same. |

