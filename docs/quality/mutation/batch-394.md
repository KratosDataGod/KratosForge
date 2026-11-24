# Mutation testing, batch 394

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Academy quiz exercises: questions about a scenario, shown without their answers and scored with the exam engine's rule, never saying which answered question is wrong (`academy/quizlab.py`, ACD-013).

The run was at `main` a73ebb71a (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 96 of 96 mutants ran (91 point + 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed). Nothing here runs the exercise sandbox: a quiz runs no code.

The set is test_academy_quizlab and test_academy_exam, about 18 s a session. test_academy_exercises also goes through the module, but was left out as too slow for a run per mutant (over 120 s, and it reaches the network). The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/quizlab.py` | test_academy_quizlab, test_academy_exam (+ test_academy_quizlab_mutation) | 96 of 96 (91 + 5) | 83 / 13 | **86.5%** | 81/91 → 91/91 | 2/5 → 5/5 | 96 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/quizlab.py**:
  - The limits at their values: answers of exactly 64 KB are read and one byte more refused; 20 questions are fine and 21 are one problem; a min_correct of 1 is fine.
  - The self test's wrong answers: an option outside the answer for a choice question, and the answer reversed for an order question.
  - `changed` says False, not just nothing, when no checked answer moved.
  - `load` gives no questions for a document that is a list, or whose questions aren't a list.
  - Problems: a quiz.json that isn't JSON can't be read (and is the one problem), a question that isn't an object is numbered from 1, and an exercise naming no objective is checked as objective 1.1.
  - → `test_academy_quizlab_mutation.py`.
