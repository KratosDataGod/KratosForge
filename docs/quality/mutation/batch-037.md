# Mutation testing, batch 37: boundary mutants, older modules

Batch 36 taught `sidecar/tools/mutate.py` to move a comparison's boundary (`<` to `<=`, `>` to `>=`) and re-ran eight recent modules. This batch runs the boundary mutants on eight older modules from batches 1 to 12, chosen for their limits, deadlines, size caps and date edges, and with test sets that run in seconds. It also adds `--kinds` and `--only` to the tool's usage message, which still left them out.

The run was at `main` a9e1bd2e (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Each module was run with the test set its own batch lists, apart from `meta/mesh.py`. Batch 12's set for it takes 82 seconds a run, so this run leaves out its five slowest files (test_platform, test_platform_service_paths, test_schema_evolution, test_git_scopes, test_meta_atomic_save) and keeps the other eight.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results (boundary mutants only)

| Module | Test files run (its batch) | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `retrying.py` | batch 1's set | 5 | 1 / 4 | **20.0%** | 5 / 0 | **100%** | none |
| `leases.py` | batch 4's set | 13 | 9 / 4 | **69.2%** | 13 / 0 | **100%** | none |
| `onelake.py` | batch 4's set | 11 | 11 / 0 | **100%** | no change needed | **100%** | none |
| `maintenance.py` | batch 2's set | 8 | 6 / 2 | **75.0%** | 8 / 0 | **100%** | none |
| `localdata/discovery.py` | batch 4's set | 9 | 6 / 3 | **66.7%** | 8 / 1 | **88.9%** | 1 equivalent |
| `widgets.py` | batch 11's set | 20 | 12 / 8 | **60.0%** | 19 / 1 | **95.0%** | 1 equivalent |
| `tmdl.py` | batch 12's set | 19 | 14 / 5 | **73.7%** | 18 / 1 | **94.7%** | 1 equivalent |
| `meta/mesh.py` | batch 12's set, less its five slowest files | 12 | 9 / 3 | **75.0%** | 12 / 0 | **100%** | none |
| **All eight** | | 97 | 68 / 29 | **70.1%** | 94 / 3 | **96.9%** | 3 equivalent |

Every boundary mutant that isn't equivalent is now killed. The new tests are in `sidecar/tests/test_boundary_mutation_2.py`, added to each module's set for the "after" runs. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **retrying.py**:
  - A poll still unfinished at exactly its deadline looks once more.
  - A wait that would end exactly at the deadline isn't started, and one ending just before it is.
  - A remote wait is slept in naps with no empty nap at the end.
  - A dropped connection is tried again when the time left under the cap exactly fits the wait.
- **leases.py**:
  - A lease at exactly its expiry isn't taken over yet.
  - A lease left unrenewed for exactly its length counts as lost.
  - The file lock gives up at exactly its deadline, on Windows and elsewhere. Both are run with a stand-in lock call and clock, so each runs on every OS.
- **maintenance.py**: a file exactly at the age limit isn't older yet, and a commit made exactly when a stopped run started counts as after it.
- **localdata/discovery.py**: a file named only by its suffix (`.csv`) isn't a data file, and exactly as many files as the limit isn't reported as over it.
- **widgets.py**:
  - A slider whose min equals its max is allowed.
  - The registry keeps exactly `MAX_WIDGETS` widgets.
  - Printing exactly the stream cap isn't truncated.
  - A tab title one past the end is empty.
  - An index one past the options is refused by name, in both a dropdown and a multi-select.
  - The app selecting one past the options is ignored.
  - `interact` on 0 gives a slider from 0 to 1.
- **tmdl.py**:
  - TMDL of exactly the size limit is read.
  - A model writing exactly the size limit is written.
  - A model with exactly the table limit is written.
  - A fenced expression that's never closed runs to the end of the file.
- **meta/mesh.py**:
  - Two days in a row are listed, not written as a range.
  - A link to a product that stops today blocks the promote.
  - A product deprecated today has given its readers notice.
- → `test_boundary_mutation_2.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `localdata/discovery.py` | 173 | `formats <= {...}` → `formats < {...}` | Equivalent: `formats` holds exactly one format there, so being a subset of the three and being a proper subset are the same. |
| `widgets.py` | 797 | the `min` choice's `v > 0` → `v >= 0` | Equivalent: it differs only at 0, where `-v` and `3 * v` are both 0. |
| `tmdl.py` | 110 | `end < 0` → `end <= 0` | Equivalent: the search starts after the opening quote, so `find` returns -1 or at least 1, never 0. |

### Not in this batch

The other modules from batches 1 to 35, which later batches can re-run with `--kinds boundary`.

