# Mutation testing, batch 355

One module not scored before, run with `--kinds all`:
- Mastery levels: how each row of verified evidence counts for a skill, each skill's level, and each class's talents, their states and the class's mastery (`mastery/levels.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 244 mutants, so 150 were sampled. `main` has changed this module since this run (#2955); the new tests pass against today's version too.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/levels.py` | test_mastery (+ test_mastery_levels_mutation) | 150 of 244 (143 + 7) | 95 / 55 | **63.3%** | 93/143 → 139/143 | 2/7 → 7/7 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/levels.py**:
  - Each kind of row counts with exactly its flags: a finished lesson is knowledge and nothing more; a capstone lab is advanced practice; a passed lab is measured by a `faster_than` check; a knowledge-only exercise, a practice-exam objective, a notebook passing Validate, a checked fix and a Prove match each have their own category, source and flags.
  - An Academy exercise finds its pack from its id when the row doesn't name it.
  - One hint still counts as independent and two don't.
  - Only an independent challenge verified on this PC is advanced, and a platform-verified exercise is marked so.
  - A practice-exam objective counts at two answers, both right.
  - A validate row counts only for a skill that takes it, and a prove match only for a skill that takes that.
  - Adept takes two sources exactly a week apart (six days isn't enough). Expert takes engineering work, not measured practice. Master takes 30 days from the first real practice to the last.
  - The rules version is 1.
  - Ten open talents at Adept, two in each specialization, make the class Adept, and every specialization needs two, even one with no talents. Nine don't. A talent at level 4 is mastered and an open one banks nothing; Journeyman counts from level 3. A talent at Initiate is in progress and one not started is available.
  - → `test_mastery_levels_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/levels.py` | 54 | `GRANDMASTER_DAYS` 180 to 181 | Equivalent: nothing reads it yet; no row carries a reviewer, so no skill reaches Grandmaster. |
| `mastery/levels.py` | 100 | `hints_used or 0` to `or 1` | Equivalent: no hints count as at most one hint either way. |
| `mastery/levels.py` | 161 | the spread for fewer than two sources, `-1` to `0` | Equivalent: both are below the 7 days Adept needs. |
| `mastery/levels.py` | 215 | the class levels counted from 0 instead of 1 | Equivalent: level 0 can only set mastery to 0, which it already is. |
