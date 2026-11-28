# Mutation testing, batch 416

One module not scored before, run with `--kinds all`:
- The Academy's settings exercises: reading the learner's settings document, grading it against rules, and the outline the app shows (`academy/configlab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. A container restart stopped the first "before" run; it was run again from the start, and only the second run is counted. The module has 85 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_configlab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/configlab.py` | test_academy_configlab (+ test_academy_configlab_mutation_b) | 85 of 85 (79 + 6) | 75 / 10 | **88.2%** | 71/79 → 79/79 | 4/6 → 6/6 | 85 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/configlab.py**:
  - A document of exactly 1 MB is read, and one byte more is refused in words.
  - A check with an empty rule list needs rules.
  - A settings exercise can't run another kind of check, and says so.
  - An answer that can't be read is named by the exercise's starter, or settings.json when there is none.
  - The outline shows 200 settings and says when there are more. A long value is cut to 200 characters and keeps its own letters.
  - → `test_academy_configlab_mutation_b.py`.

### Left

None.
