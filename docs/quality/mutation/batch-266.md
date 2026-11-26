# Mutation testing, batch 266

One module that had no score before, run with `--kinds all`:
- Delta change data feed reads on this PC: the options' bounds, Spark's own errors for a bad range, refusing column mapping and time travel, and Spark's types for the change columns (`delta_cdf.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 74 mutants, so all were run. No test was left out, and no mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `delta_cdf.py` | test_delta_change_feed (+ test_delta_cdf_mutation) | 74 of 74 (72 + 2) | 72 / 2 | **97.3%** | 70/72 → 72/72 | 2/2 → 2/2 | 74 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **delta_cdf.py**:
  - A delta-rs error that isn't a bad version or a table without change data is still said, with the range and delta-rs' own words.
  - Only `_commit_timestamp` becomes a UTC microsecond timestamp. The table's own timestamp columns keep their type.
  - → `test_delta_cdf_mutation.py`.

### Left

None.
