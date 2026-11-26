# Mutation testing, batch 354

Two modules not scored before, run with `--kinds all`:
- What a semantic model's definition shows, as names and counts only (`excellence/inspect_models.py`).
- What a notebook's code or a pipeline's activities show, as rule ids, places, counts and activity names only (`excellence/inspect_items.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Both modules have fewer than 150 mutants (63 and 49), so all were run. `main` has changed `inspect_items.py` since this run (#3162); the new tests pass against today's version too.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/inspect_models.py` | test_excellence_models (+ test_excellence_inspect_mutation) | 63 of 63 (61 + 2) | 52 / 11 | **82.5%** | 52/61 → 57/61 | 0/2 → 2/2 | 59 / 4 | **93.7%** | 4 equivalent |
| `excellence/inspect_items.py` | test_excellence_artifacts (+ test_excellence_inspect_mutation) | 49 of 49 (48 + 1) | 38 / 11 | **77.6%** | 37/48 → 47/48 | 1/1 → 1/1 | 48 / 1 | **98.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/inspect_models.py**:
  - A definition without a model is read and says so.
  - Names are kept to 50, and each name to 256 characters: one of exactly 256 is kept whole.
  - A rule keeps 50 of its objects and counts them all.
  - A relationship missing either table is left out, not an error.
- **excellence/inspect_items.py**:
  - A workload whose definition wasn't read says so, and a notebook or pipeline without its cells or activities says which.
  - A definition that can't be inspected is an error naming the kind, not a crash.
  - A rule keeps 20 places and counts them all; activity names are kept to 50 and counted in full.
  - A fixed workspace inside a list of objects is found.
- → `test_excellence_inspect_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/inspect_models.py` | 56 | a table's `dimension` set to `False` | Equivalent: only SM006 reads it, and SM006 is turned off here. |
| `excellence/inspect_models.py` | 68 (3 mutants) | the relationship's `active` flag changed | Equivalent: SM001, SM002 and SM004, the rules run here, don't read it. |
| `excellence/inspect_items.py` | 73 | `return False` to `return None` | Equivalent: the result is only used as a condition. |
