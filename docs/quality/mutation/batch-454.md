# Mutation testing, batch 454

One module not scored before, run with `--kinds all`:
- The Academy's Git lab: a learner's Git session read line by line, replayed against an exercise's own repository built on this PC, and checked with the exercise's rules on branches, files at a ref, commits and conflict markers (`academy/gitlab.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 426 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_gitlab, test_regex_anchors and test_academy_limits.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. They need `git`, as test_academy_gitlab does, and are skipped without it.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/gitlab.py` | test_academy_gitlab, test_regex_anchors, test_academy_limits (+ test_academy_gitlab_mutation_b) | 150 of 426 (141 + 9) | 128 / 22 | **85.3%** | 125/141 → 140/141 | 3/9 → 9/9 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/gitlab.py**:
  - A bare `git` line needs a subcommand, and the refusal says so. A lone `-` is an argument, not flags.
  - A session of exactly the byte cap is read, and one byte more isn't.
  - A path of 12 parts or 240 characters is inside the limits, and one more part or character isn't.
  - Spec problems count commits from 1. A failed build step quotes 400 characters of Git's output.
  - A session stops at 90 seconds. A write over a folder says it's a folder.
  - Run shows 200 rows and says "more" only past them.
  - A branch that is there passes. A file at a ref over the size cap reads as missing. A JSON rule on a file that isn't JSON fails with the rule's reason.
  - An ancestor rule names the ref that isn't there, on either side.
  - `count_max` allows exactly the maximum and names it when it's passed.
  - Conflict markers are looked for in the first 2,000 files only.
  - → `test_academy_gitlab_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/gitlab.py` | 553 | `int(count.strip() or 0)` to `or 1` in the commits-since count | Equivalent: the base and the ref were both found just before, and `git rev-list --count` then always prints a number, so the fallback is never used. |
