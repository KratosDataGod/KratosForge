# Mutation testing, batch 215

One module, run uncapped: every mutant, not a sample. Batch 3 first scored it on 40 point mutants (77.5% before, 95.0% after), and batch 207 re-scored its 3 boundary mutants (33.3% before, 66.7% after).
- Saving JSON state files and the copies set aside when one is corrupt (`jsonfile.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 83 of 83 mutants ran (80 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is batches 3 and 207's files with three more that write through the module: test_file_locks, test_startup_after_power_loss and test_settings_files_fuzz. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `jsonfile.py` | test_json_files, test_file_replace, test_state_writes, test_meta_atomic_save, test_jsonfile_mutation, test_jsonfile_set_aside_same_tick, test_jsonfile_boundary, test_file_locks, test_startup_after_power_loss, test_settings_files_fuzz (+ test_jsonfile_uncapped) | 83 of 83 (80 + 3) | 78 / 5 | **94.0%** | 76/80 → 80/80 | 2/3 → 2/3 | 82 / 1 | **98.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **jsonfile.py**:
  - A text or bytes write whose temporary file is already gone when the swap fails raises the swap's own error, not a missing-file error from the clean-up.
  - `unlink` waits out a busy file 0.05 s, 0.1 s, 0.2 s, as `replace` does.
  - → `test_jsonfile_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `jsonfile.py` | 151 | `when > newest` to `>=` in naming a set-aside copy | Equivalent, as in batch 207: a stamp equal to the newest so far leaves the newest the same. |
