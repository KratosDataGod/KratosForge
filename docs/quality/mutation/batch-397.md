# Mutation testing, batch 397

One module, scored for the first time in mutation lane A. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's pipeline and orchestration exercises: the learner's pipeline run on the local runner with control flow and the exercise's mocks only, and runMultiple DAGs checked as data (`academy/pipelab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 115 mutants, and the run ran all 115 of 115 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_pipelab and test_academy_limits, about 18 seconds together. test_academy_exercises also reaches the module, but it takes about 9 minutes and uses it in three places, so it was left out; the score counts only the files given. Pipelines run in this process on the local runner, with no sandbox process to stub.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 2 and 1).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pipelab.py` | test_academy_pipelab, test_academy_limits (+ test_academy_pipelab_mutation) | 115 of 115 (110 + 5), uncapped | 87 / 28 | **75.7%** | 87/110 → 110/110 | 0/5 → 5/5 | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pipelab.py**:
  - A pipeline may hold 200 activities and a DAG 500; one more is refused with the limit named.
  - Activities it can't run are named five at most, then "and more". Missing and extra notebooks are named five at most, and extra ones are fine unless the check is exact.
  - A run that can't start names its first three reasons.
  - A definition it can't read, and a DAG that isn't valid, say why in 300 characters.
  - A DAG's concurrency must be from 1 to the exercise's cap.
  - A child pipeline is held to the same rule, and one that passes runs.
  - A pipeline runs four activities at a time, with waits that take no time and a 20 second limit.
  - A check it doesn't know fails in words. Grading without the exercise's folder reads no child pipelines.
  - Only a pipeline_outcome check has its status words checked.
  - → `test_academy_pipelab_mutation.py`.

### Left

None.

Lane B scored the same module at the same time, in batch 422 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 422's page each report their own run. Neither is a re-score of the other.
