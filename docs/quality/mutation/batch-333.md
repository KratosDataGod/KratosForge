# Mutation testing, batch 333

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- What one run on a PC still cost, as separate measurements: its transfer figures, the syncs that carried it, and its share of each sync (`savings/overhead.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 140 mutants, and the run ran all 140 of 140 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `savings/overhead.py`: test_savings_overhead. It takes about a second, so none was left out. No `-k` filter or `--deselect` was needed. Since the run, `main` (3424d8f33) has not changed the module, and the new tests pass there.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (20, 1 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/overhead.py` | test_savings_overhead (+ test_savings_overhead_mutation) | 140 of 140 (139 + 1), uncapped | 130 / 10 | **92.9%** | 129/139 → 139/139 | 1/1 → 1/1 | 140 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/overhead.py**:
  - A transfer figure keeps the run's workspace and capacity, and an empty one is None.
  - A sync keeps each figure's method, assumptions and period, also when the period comes as a dict.
  - A rows share keeps the sync's method and assumptions, and the commit that isn't split is still `optimize_sync_commits`.
  - A run without an execution id can't share a sync.
  - A run synced once keeps that sync's figures as they are, not a total of one.
  - → `test_savings_overhead_mutation.py`.

### Left

None.
