# Mutation testing, batch 112

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Formatting notebook cells: what ruff is given, ruff's errors as the cell's reason, `%%sql` cells and cells without ruff (`code_format.py`).
- Undoing a bad load: which Delta versions can still be restored after VACUUM, and the restore itself (`meta/restore.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. In the "after" run of `meta/restore.py` the new test file was given first, so a mutant it kills stops before the slower estate tests. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `code_format.py` | test_code_format (+ test_code_format_mutation) | 61 of 61 (61 + 0) | 51 / 10 | **83.6%** | 51/61 → 59/61 | none | 59 / 2 | **96.7%** | 2 equivalent |
| `meta/restore.py` | test_estate, test_duties (+ test_meta_restore_mutation) | 59 of 59 (56 + 3) | 36 / 23 | **61.0%** | 35/56 → 56/56 | 1/3 → 1/3 | 57 / 2 | **96.6%** | 1 equivalent, 1 not killed |

No bugs were found. One edge is noted under "Left" for `meta/restore.py`.

### What the survivors were, and the tests that now kill them

- **code_format.py**:
  - ruff gets the cell as a one-cell notebook (format 4.5), with its own arguments and a 20 s limit.
  - A parse error names its line and reason; another ruff error is given in ruff's words, also one that says "parse" without a line.
  - A formatter that hangs changes nothing.
  - An `%%sql` cell with nothing under its magic is left alone.
  - A `%%pyspark` cell without ruff says ruff isn't installed.
  - → `test_code_format_mutation.py`.
- **meta/restore.py**:
  - A commit's time is milliseconds since 1970 in UTC, and its rows come from the commit's metrics.
  - 50 versions are listed, and 1,000 commits are read to find the VACUUMs.
  - Without a VACUUM, every earlier version can be restored.
  - A VACUUM reaches back by its own retention, or by the layer's when it has none, exact to the millisecond and the hour. The latest VACUUM's horizon counts.
  - Maintenance commits are marked, and only a version that can't be restored says why.
  - A restore counts the files it restored and removed, and 0 when the counts are missing.
  - Restoring to a version whose files VACUUM removed fails and changes nothing.
  - → `test_meta_restore_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `code_format.py` | 44 | `text=True` to `False` for ruff's run | Equivalent: the run also gives `encoding="utf-8"`, which opens the pipes as text either way. |
| `code_format.py` | 97 | `max_workers=4` to `5` | Equivalent: `pool.map` gives the results in the cells' order whatever the number of workers. |
| `meta/restore.py` | 61 | `cut > horizon` to `>=` | Equivalent: when two VACUUMs give the same cut, either one is the same horizon. |
| `meta/restore.py` | 76 | `superseded > horizon` to `>=` | Not killed: it differs only for a version superseded at exactly the horizon's millisecond. The comment on line 60 says files removed before the horizon are gone, so at that millisecond the mutant may be the better answer. The engine is cautious there (it says such a version can't be restored), and no test was written to fix either answer. |

