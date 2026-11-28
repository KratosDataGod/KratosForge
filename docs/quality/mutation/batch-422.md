# Mutation testing, batch 422

One module not scored before, run with `--kinds all`:
- Pipeline exercises: the learner's pipeline run on the local runner with control-flow activities and the exercise's mocks only, and runMultiple DAGs checked as data (`academy/pipelab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_exercises was left out of the test set because each of its runs takes about 875 seconds; that can only lower the "before" score. The module has 115 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_pipelab and test_academy_limits.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests run only the test's own pipelines on the local runner, with network connections refused.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pipelab.py` | test_academy_pipelab, test_academy_limits (+ test_academy_pipelab_mutation_b) | 115 of 115 (110 + 5) | 87 / 28 | **75.7%** | 87/110 → 110/110 | 0/5 → 5/5 | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pipelab.py**:
  - A pipeline of exactly 200 activities runs. A DAG of exactly 500 activities is ordered, and 501 is refused.
  - A definition that can't be read, and a DAG that isn't valid, are each cut to 300 characters.
  - Five blocked activities are named, and a sixth adds "and more".
  - A child pipeline with activities the exercise can't run is refused. A child with only control flow runs.
  - A run that can't start names its first three problems.
  - The run uses at most four activities at once and takes no time for waits.
  - Extra notebooks are fine unless the check is exact. Missing and extra notebooks are each named, five at most.
  - Concurrency must be from 1 to the cap.
  - check_problems reads a pipeline_outcome's fields only for that kind.
  - An unknown check kind fails in words, and so does a DAG check without its file.
  - Child pipelines are read only from the exercise's folder.
  - → `test_academy_pipelab_mutation_b.py`.

### Left

None.
