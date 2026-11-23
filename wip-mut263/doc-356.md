# Mutation testing, batch 356

One module not scored before, run with `--kinds all`:
- The mastery class catalog: reading and checking the skills registry and the class files, the talent graphs' order and reach, and refusing names that are or resemble a god (`mastery/catalog.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 125 mutants, so all were run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/catalog.py` | test_mastery_catalog_cov3, test_mastery (+ test_mastery_catalog_mutation) | 125 of 125 (118 + 7) | 116 / 9 | **92.8%** | 110/118 → 117/118 | 6/7 → 7/7 | 124 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/catalog.py**:
  - The error counts the problems past five from the sixth, and says nothing extra at exactly five.
  - A class version of 0 is refused, and a specialization id used twice is refused.
  - A class title or an achievement name that names a god is refused.
  - `known_ids()` gives all four kinds and counts draft Learn modules.
  - `load()` checks hooks by default, refuses a hook that names nothing, and returns a clean catalog.
  - → `test_mastery_catalog_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/catalog.py` | 254 | `lru_cache(maxsize=4)` to 5 | Equivalent: only how many catalogs stay cached changes, not what `load()` returns. |
