# Mutation testing, batch 358

One module not scored before, run with `--kinds all`:
- The capacity and FinOps controls from imported Capacity Metrics exports: the usage record, failed work's share of CU, refreshes per day and a paid F capacity's use (`excellence/catalog_capacity.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 179 mutants, so 150 were sampled.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_capacity.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_capacity_mutation) | 150 of 179 (139 + 11) | 105 / 45 | **70.0%** | 104/139 → 137/139 | 1/11 → 11/11 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_capacity.py**:
  - A CU value must be a number of 0 or more (0 counts; a bool or text doesn't).
  - Operations skip a row without CU and round to three places. The refresh days keep the top 20, name their workspace and item, tie by name, and count a day over only past 24 refreshes.
  - An items period must end after it starts and have some CU. The usage record lists each import it used, in id order.
  - CU-hours divide by 3,600. Failed work with a little CU is worked out, with none it is not evaluated, and exactly 10% fails. The share is rounded to four places.
  - Refreshes without operations are not evaluated; too many refreshes names five days and the hourly saving; exactly one day of operations is enough.
  - Compute-page periods that touch, in either order, both count. F0 isn't a paid size, F1 is. A paused capacity is named. Exactly seven days are enough, short periods are named in tenths of days, exactly 10% use passes, the benefit is the unused capacity, and the detail is rounded.
  - → `test_excellence_catalog_capacity_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/catalog_capacity.py` | 188 | `round(failed, 3)` to 4 places | Equivalent: `failed` is a sum of values already rounded to three places. |
| `excellence/catalog_capacity.py` | 282 | `round(used, 3)` to 4 places | Equivalent: `used` is a sum of items' CU already rounded to three places. |
