# Mutation testing, batch 410

One module not scored before, run with `--kinds all`:
- Academy assessments: practice exams, domain diagnostics and objective drills built from a pack's questions, with an honest clock driven by heartbeats and a result scored once at Finish (`academy_exam_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 99 mutants, so all were run. `main` hasn't changed this module since.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy_exam_service.py` | test_academy_exam_service (+ test_academy_exam_service_mutation_b) | 99 of 99 (98 + 1) | 79 / 20 | **79.8%** | 79/98 → 98/98 | 0/1 → 1/1 | 99 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy_exam_service.py**:
  - A practice exam for a pack with no published exam takes 50 questions in 100 minutes, and one with a published exam takes no more questions than the bank holds.
  - A diagnostic asks two questions per objective.
  - A domain or objectives that the certification doesn't have are refused in words, and so is an empty list.
  - A drill is titled by its objectives up to three, and by a count from four.
  - Answering returns the session's view, and flagging flags unless told not to.
  - A heartbeat after Finish says the exam is finished and not running.
  - A result breaks the score down by domain and by objective.
  - The home screen counts the questions per objective, and an objective with none shows 0.
  - → `test_academy_exam_service_mutation_b.py`.
