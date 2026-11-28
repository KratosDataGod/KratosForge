# Mutation testing, batch 412

One module not scored before, run with `--kinds all`:
- The Academy's practice exams: question banks, the rules a pack's questions must follow, and an exam session from start to review (`academy/exam.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 364 mutants, so 150 were sampled. A container restart stopped the first "before" run at 123 of 150; it was run again from the start, and only the second run is counted. `main` has changed this module since (#3239); the new tests pass against today's version.

The test set was test_academy_exam, test_academy_exam_service and test_academy_exam_service_mutation_b.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/exam.py` | test_academy_exam, test_academy_exam_service, test_academy_exam_service_mutation_b (+ test_academy_exam_mutation_b) | 150 of 364 (144 + 6) | 130 / 20 | **86.7%** | 127/144 → 143/144 | 3/6 → 6/6 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/exam.py**:
  - A good pack name with no folder, and a folder with a bad name, are both refused. A bank loads with and without `cases.json`.
  - An option with no text is named. A version that isn't a whole number from 1 is named, and version 1 is fine.
  - An also_accept order that repeats the answer is named. The limits on options and answers of a multiple-answer question are tested at both edges.
  - A case with a bad id, or one that isn't an object, is named. An objective with exactly the fewest live questions is enough, and one fewer is named.
  - heartbeat, answer and finish return the session. Checking a question no longer in the bank is refused. A review needs a finished session with a result.
  - Questions are numbered from 1. All answers right gives 100%. The clock rounds to 3 places. The seed of an unseeded exam is from 0 to 2**31.
  - → `test_academy_exam_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/exam.py` | 297 | rank of `right` 2 to 3 | Equivalent: the rank is only used to sort, and 1 < 3 sorts the same as 1 < 2. |

Lane A scored the same module at the same time, in batch 392 (#3308). The two runs used different tests and neither saw the other's, so this page and batch 392's page each report their own run. Neither is a re-score of the other.
