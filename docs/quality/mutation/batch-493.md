# Mutation testing, batch 493

One module not scored before, run with `--kinds all`:
- Telling a network share from a folder on this PC in a Windows path, in any of its forms, keeping the drive letter a person typed for a mapped drive, and the words for a save that failed because its path is past Windows' 260 characters (`win_paths.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 37 mutants, so all were run.

The test set was test_win_paths_mutation, test_long_paths_named and test_network_paths.

Every mutant was killed, so no new tests were needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `win_paths.py` | test_win_paths_mutation, test_long_paths_named and test_network_paths | 37 of 37 (36 + 1) | 37 / 0 | **100.0%** | 36/36 → 36/36 | 1/1 → 1/1 | 37 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **win_paths.py**: there were none; no new tests needed.

### Left

None.
